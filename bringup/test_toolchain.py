#!/usr/bin/env python3
"""
bringup/test_toolchain.py — Dry-run validation of the commissioning toolchain
==============================================================================
Tests all toolchain scripts against the mock serial environment without
any real ESP32 hardware.

Tests:
  T01  Happy path (clean frames) → verify_bringup PASS
  T02  CRC corruption (10%)     → P4 FAIL, n_crc_errors > 0
  T03  Desync injection (5%)    → n_sync_drops > 0, P4 may FAIL
  T04  Watchdog flag            → warning in results, no hard FAIL
  T05  Brownout flag            → warning in results, no hard FAIL
  T06  SAFE_HALT flag           → P3 FAIL
  T07  Sensor freeze (thermal)  → freeze_events > 0 in results
  T08  Low delivery (60%)       → P4 FAIL (delivery < 99%)
  T09  No frames at all         → P1 FAIL, STOP-UNSAFE
  T10  INA226 dropout           → P2 FAIL (INA226 uptime < 98%)
  T11  serial_capture.py clean  → OK verdict, boot banner detected
  T12  serial_capture.py crash  → STOP verdict, Guru Meditation detected
  T13  JSON report validity      → all required keys present, valid JSON
  T14  Markdown report validity  → required sections in output

Evidence labels: (host-sim) — all tests run against FrameGenerator + PTY
"""
from __future__ import annotations

import json
import math
import os
import struct
import sys
import threading
import time
import pty
import termios

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mock_frame_generator import (
    FrameGenerator, FaultSchedule, MockBootLog, MockBootLogFaultMode,
)
from verify_bringup import (
    FrameParser, TelemetryFrame, verify_bringup, generate_markdown_report,
    FIRST_FRAME_TIMEOUT_S,
)
from serial_capture import capture, SessionStats


# ── constants ─────────────────────────────────────────────────────────
QUICK_SOAK_S  = 35.0   # 35 s soak for PASS tests (needs > 30 s P4 window + a bit)
SKIP_SOAK_S   = 0.0    # no soak
TELEM_BAUD    = 921600
DEBUG_BAUD    = 115200

# ── colour output ─────────────────────────────────────────────────────
_IS_TTY = sys.stdout.isatty()
G  = "\033[1;32m" if _IS_TTY else ""
R  = "\033[1;31m" if _IS_TTY else ""
Y  = "\033[1;33m" if _IS_TTY else ""
B  = "\033[1m"    if _IS_TTY else ""
Z  = "\033[0m"    if _IS_TTY else ""

_results: list[dict] = []


# ═══════════════════════════════════════════════════════════════════════
# PTY harness
# ═══════════════════════════════════════════════════════════════════════

def _configure_raw(fd: int) -> None:
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0  # iflag: all off
        attrs[1] = 0  # oflag: all off
        attrs[2] |= termios.CS8
        attrs[3] = 0  # lflag: all off (raw)
        attrs[6][termios.VMIN]  = 1
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except Exception:
        pass


def _telem_writer(master_fd: int, gen: FrameGenerator,
                  duration_s: float, stop: threading.Event) -> None:
    t_start = time.monotonic()
    target_t = t_start
    while not stop.is_set():
        now = time.monotonic()
        if now - t_start > duration_s:
            break
        elapsed_us = int((now - t_start) * 1_000_000)
        frame_bytes = gen.next_frame_bytes(elapsed_us)
        if frame_bytes:
            try:
                os.write(master_fd, frame_bytes)
            except OSError:
                break
        target_t += 0.001
        sleep_for = target_t - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
        elif sleep_for < -0.01:
            target_t = time.monotonic()


def _debug_writer(master_fd: int, boot_log: MockBootLog,
                  duration_s: float, stop: threading.Event) -> None:
    t_start = time.monotonic()
    while not stop.is_set():
        elapsed_s = time.monotonic() - t_start
        if elapsed_s > duration_s:
            break
        for line in boot_log.get_lines_at(elapsed_s):
            try:
                os.write(master_fd, line.encode("utf-8", errors="replace"))
            except OSError:
                return
        time.sleep(0.02)


def _run_with_telem_mock(
    faults: FaultSchedule,
    soak_minutes: float,
    skip_soak: bool,
    duration_s: float,
) -> dict:
    """Run verify_bringup against a mock telemetry stream on a PTY."""
    master_fd, slave_fd = pty.openpty()
    _configure_raw(master_fd)
    slave_path = os.ttyname(slave_fd)

    gen = FrameGenerator(seed=42, faults=faults)
    stop = threading.Event()

    t = threading.Thread(
        target=_telem_writer,
        args=(master_fd, gen, duration_s, stop),
        daemon=True,
    )
    t.start()

    try:
        results = verify_bringup(
            port=slave_path,
            baud=TELEM_BAUD,
            soak_minutes=soak_minutes,
            skip_soak=skip_soak,
        )
    finally:
        stop.set()
        # Close fds after verify_bringup has closed the slave via serial.Serial
        for fd in (master_fd, slave_fd):
            try:
                os.close(fd)
            except OSError:
                pass

    return results


def _run_serial_capture_mock(
    boot_log: MockBootLog,
    duration_s: float,
) -> SessionStats:
    """Run serial_capture against a mock UART0 debug stream on a PTY."""
    import tempfile
    master_fd, slave_fd = pty.openpty()
    _configure_raw(master_fd)
    slave_path = os.ttyname(slave_fd)

    stop = threading.Event()
    t = threading.Thread(
        target=_debug_writer,
        args=(master_fd, boot_log, duration_s, stop),
        daemon=True,
    )
    t.start()

    out_dir = tempfile.mkdtemp(prefix="sc_test_")
    try:
        stats = capture(
            port=slave_path,
            baud=DEBUG_BAUD,
            duration_s=duration_s,
            out_dir=out_dir,
            rotate_mb=10.0,
            quiet=True,
        )
    finally:
        stop.set()
        for fd in (master_fd, slave_fd):
            try:
                os.close(fd)
            except OSError:
                pass

    # Clean up temp log files
    import shutil
    shutil.rmtree(out_dir, ignore_errors=True)

    return stats


# ═══════════════════════════════════════════════════════════════════════
# Test runner
# ═══════════════════════════════════════════════════════════════════════

def _run_test(
    test_id: str,
    name: str,
    fn,
    checks: list[tuple[str, bool, str]],  # (description, expected_bool, evidence_label)
) -> dict:
    """Run a test function and evaluate multiple pass/fail checks."""
    print(f"\n{B}── {test_id}: {name} ──{Z}")
    t0 = time.monotonic()

    try:
        result_data = fn()
        elapsed = time.monotonic() - t0
        error = None
    except Exception as e:
        elapsed = time.monotonic() - t0
        result_data = None
        error = str(e)

    check_results = []
    all_pass = True

    if error:
        print(f"  {R}EXCEPTION: {error}{Z}")
        all_pass = False
        check_results.append({
            "description": "no exception",
            "expected": True,
            "actual": False,
            "pass": False,
            "evidence": "(host-sim)",
        })
    else:
        for desc, expected, evidence in checks:
            try:
                # Evaluate the check against result_data
                actual = _eval_check(desc, result_data)
                ok = (bool(actual) == bool(expected))
            except Exception as ce:
                actual = f"EVAL_ERROR: {ce}"
                ok = False

            colour = G if ok else R
            symbol = "✓" if ok else "✗"
            print(f"  {colour}{symbol}{Z}  {desc}  "
                  f"(expected={expected}, got={actual}) {evidence}")
            if not ok:
                all_pass = False
            check_results.append({
                "description": desc,
                "expected": expected,
                "actual": actual,
                "pass": ok,
                "evidence": evidence,
            })

    status = "PASS" if all_pass else "FAIL"
    colour = G if all_pass else R
    print(f"  {colour}{B}{status}{Z}  ({elapsed:.1f}s)")

    record = {
        "test_id": test_id,
        "name": name,
        "pass": all_pass,
        "elapsed_s": round(elapsed, 1),
        "checks": check_results,
        "error": error,
    }
    _results.append(record)
    return record


def _eval_check(desc: str, data) -> object:
    """Evaluate a check expression against result data."""
    if isinstance(data, dict):
        # Shorthand access for nested keys
        if desc == "overall_pass is True":
            return data.get("overall_pass") is True
        if desc == "overall_pass is False":
            return data.get("overall_pass") is False
        if desc == "P1 PASS":
            return data.get("phases", {}).get("P1_first_frame", {}).get("pass") is True
        if desc == "P1 FAIL":
            return data.get("phases", {}).get("P1_first_frame", {}).get("pass") is False
        if desc == "P2 PASS":
            return data.get("phases", {}).get("P2_sensor_flags", {}).get("pass") is True
        if desc == "P2 FAIL":
            return data.get("phases", {}).get("P2_sensor_flags", {}).get("pass") is False
        if desc == "P3 PASS":
            return data.get("phases", {}).get("P3_plausibility", {}).get("pass") is True
        if desc == "P3 FAIL":
            return data.get("phases", {}).get("P3_plausibility", {}).get("pass") is False
        if desc == "P4 PASS":
            return data.get("phases", {}).get("P4_wire_integrity", {}).get("pass") is True
        if desc == "P4 FAIL":
            return data.get("phases", {}).get("P4_wire_integrity", {}).get("pass") is False
        if desc == "n_crc_errors > 0":
            return data.get("phases", {}).get("P4_wire_integrity", {}).get("n_crc_errors", 0) > 0
        if desc == "n_crc_errors == 0":
            return data.get("phases", {}).get("P4_wire_integrity", {}).get("n_crc_errors", 0) == 0
        if desc == "n_sync_drops > 0":
            return data.get("phases", {}).get("P4_wire_integrity", {}).get("n_sync_drops", 0) > 0
        if desc == "safe_halt_count > 0":
            return data.get("phases", {}).get("P3_plausibility", {}).get("n_safe_halt", 0) > 0
        if desc == "freeze_events > 0":
            return data.get("sensor_freeze", {}).get("total_freeze_events", 0) > 0
        if desc == "delivery_pct < 99":
            return data.get("phases", {}).get("P4_wire_integrity", {}).get("delivery_pct", 100) < 99.0
        if desc == "verdict is STOP":
            return "STOP" in str(data.get("verdict", ""))
        if desc == "verdict is READY":
            return "READY" in str(data.get("verdict", ""))
        if desc == "INA226_OK_pct < 98":
            return data.get("phases", {}).get("P2_sensor_flags", {}).get("INA226_OK_pct", 100) < 98.0
        if desc == "first_frame_latency < 5":
            lat = data.get("phases", {}).get("P1_first_frame", {}).get("latency_s")
            return lat is not None and lat < 5.0
        if desc == "n_frames > 1000":
            n = (data.get("phases", {}).get("P4_wire_integrity", {}).get("n_frames", 0))
            return n > 1000
        if desc == "jitter_mean near 1000us":
            mean = data.get("jitter", {}).get("mean_us", 0)
            return abs(mean - 1000.0) < 200.0  # ±200 µs — relaxed for slow CI
        if desc == "json_has_required_keys":
            required = {"timestamp", "port", "baud", "phases", "jitter",
                        "sensor_freeze", "throughput", "verdict", "overall_pass"}
            return required.issubset(set(data.keys()))
        if desc == "markdown_has_required_sections":
            # data is a string in this case
            raise ValueError("use isinstance check")
    if isinstance(data, str):
        if desc == "markdown_has_required_sections":
            required = ["Phase Summary", "Jitter Analysis",
                        "Sensor Freeze", "UART Throughput", "Overall Verdict"]
            return all(s in data for s in required)
    if isinstance(data, SessionStats):
        if desc == "boot_banner_seen":
            return data.boot_banner_seen
        if desc == "telem_task_started":
            return data.telem_task_started
        if desc == "sensor_task_started":
            return data.sensor_task_started
        if desc == "guru_meditation_detected":
            return data.guru_meditation
        if desc == "verdict OK":
            return data.verdict.startswith("OK")
        if desc == "verdict STOP":
            return data.verdict.startswith("STOP")
        if desc == "total_lines > 0":
            return data.total_lines > 0
    raise ValueError(f"Unknown check: {desc!r} for data type {type(data).__name__}")


# ═══════════════════════════════════════════════════════════════════════
# Individual tests
# ═══════════════════════════════════════════════════════════════════════

def test_t01_happy_path():
    return _run_test(
        "T01", "Happy path — clean frames",
        lambda: _run_with_telem_mock(
            FaultSchedule(), soak_minutes=0, skip_soak=True, duration_s=40),
        [
            ("P1 PASS",              True,  "(host-sim)"),
            ("P2 PASS",              True,  "(host-sim)"),
            ("P3 PASS",              True,  "(host-sim)"),
            ("P4 PASS",              True,  "(host-sim)"),
            ("overall_pass is True", True,  "(host-sim)"),
            ("verdict is READY",     True,  "(host-sim)"),
            ("n_crc_errors == 0",    True,  "(host-sim)"),
            ("first_frame_latency < 5", True, "(host-sim)"),
            ("n_frames > 1000",      True,  "(host-sim)"),
            ("jitter_mean near 1000us", True, "(analytical)"),
        ],
    )


def test_t02_crc_corruption():
    return _run_test(
        "T02", "CRC corruption (10%)",
        lambda: _run_with_telem_mock(
            FaultSchedule(crc_corrupt_rate=0.10),
            soak_minutes=0, skip_soak=True, duration_s=40),
        [
            ("P1 PASS",              True,  "(host-sim)"),
            ("P4 FAIL",              True,  "(host-sim)"),
            ("n_crc_errors > 0",     True,  "(host-sim)"),
            ("overall_pass is False",True,  "(host-sim)"),
            ("verdict is STOP",      True,  "(host-sim)"),
        ],
    )


def test_t03_desync():
    return _run_test(
        "T03", "Desync injection (10%)",
        lambda: _run_with_telem_mock(
            FaultSchedule(desync_rate=0.10),
            soak_minutes=0, skip_soak=True, duration_s=40),
        [
            ("P1 PASS",              True,  "(host-sim)"),
            ("n_sync_drops > 0",     True,  "(host-sim)"),
        ],
    )


def test_t04_watchdog_flag():
    # Watchdog flag in first frame — should warn but P1 still passes
    return _run_test(
        "T04", "Watchdog RST flag in frames",
        lambda: _run_with_telem_mock(
            FaultSchedule(watchdog_start_us=0, watchdog_dur_us=5_000_000),
            soak_minutes=0, skip_soak=True, duration_s=40),
        [
            ("P1 PASS",              True,  "(host-sim)"),
            ("overall_pass is True", True,  "(host-sim)"),  # watchdog flag is a warning, not hard fail
        ],
    )


def test_t05_brownout_flag():
    return _run_test(
        "T05", "Brownout flag in frames",
        lambda: _run_with_telem_mock(
            FaultSchedule(brownout_start_us=0, brownout_dur_us=5_000_000),
            soak_minutes=0, skip_soak=True, duration_s=40),
        [
            ("P1 PASS",              True,  "(host-sim)"),
            ("overall_pass is True", True,  "(host-sim)"),  # brownout flag is a warning, not hard fail
        ],
    )


def test_t06_safe_halt():
    return _run_test(
        "T06", "SAFE_HALT flag in frames",
        lambda: _run_with_telem_mock(
            FaultSchedule(safe_halt_start_us=0, safe_halt_dur_us=60_000_000),
            soak_minutes=0, skip_soak=True, duration_s=40),
        [
            ("P3 FAIL",              True,  "(host-sim)"),
            ("safe_halt_count > 0",  True,  "(host-sim)"),
            ("overall_pass is False",True,  "(host-sim)"),
            ("verdict is STOP",      True,  "(host-sim)"),
        ],
    )


def test_t07_sensor_freeze():
    return _run_test(
        "T07", "Thermal sensor freeze (200+ identical frames)",
        lambda: _run_with_telem_mock(
            FaultSchedule(thermal_freeze_start_us=0),
            soak_minutes=0, skip_soak=True, duration_s=40),
        [
            ("P1 PASS",              True,  "(host-sim)"),
            ("freeze_events > 0",    True,  "(host-sim)"),
        ],
    )


def test_t08_low_delivery():
    return _run_test(
        "T08", "Low delivery rate (60%)",
        lambda: _run_with_telem_mock(
            FaultSchedule(delivery_fraction=0.60),
            soak_minutes=0, skip_soak=True, duration_s=40),
        [
            ("P1 PASS",              True,  "(host-sim)"),
            ("P4 FAIL",              True,  "(host-sim)"),
            ("delivery_pct < 99",    True,  "(host-sim)"),
            ("overall_pass is False",True,  "(host-sim)"),
            ("verdict is STOP",      True,  "(host-sim)"),
        ],
    )


def test_t09_no_frames():
    # Mock stops frames immediately — P1 should time out and fail
    return _run_test(
        "T09", "No frames at all (timeout)",
        lambda: _run_with_telem_mock(
            FaultSchedule(no_frames_after_us=0),
            soak_minutes=0, skip_soak=True,
            duration_s=FIRST_FRAME_TIMEOUT_S + 5),
        [
            ("P1 FAIL",              True,  "(host-sim)"),
            ("verdict is STOP",      True,  "(host-sim)"),
            ("overall_pass is False",True,  "(host-sim)"),
        ],
    )


def test_t10_ina226_dropout():
    return _run_test(
        "T10", "INA226 dropout (flag clear for entire window)",
        lambda: _run_with_telem_mock(
            FaultSchedule(ina226_drop_start_us=0, ina226_drop_dur_us=60_000_000),
            soak_minutes=0, skip_soak=True, duration_s=40),
        [
            ("P2 FAIL",              True,  "(host-sim)"),
            ("INA226_OK_pct < 98",   True,  "(host-sim)"),
            ("overall_pass is False",True,  "(host-sim)"),
        ],
    )


def test_t11_serial_capture_clean():
    return _run_test(
        "T11", "serial_capture.py — clean boot log",
        lambda: _run_serial_capture_mock(MockBootLog(), duration_s=2.0),
        [
            ("boot_banner_seen",     True,  "(host-sim)"),
            ("telem_task_started",   True,  "(host-sim)"),
            ("sensor_task_started",  True,  "(host-sim)"),
            ("total_lines > 0",      True,  "(host-sim)"),
            ("verdict OK",           True,  "(host-sim)"),
        ],
    )


def test_t12_serial_capture_guru():
    return _run_test(
        "T12", "serial_capture.py — Guru Meditation detection",
        lambda: _run_serial_capture_mock(
            MockBootLogFaultMode("guru_meditation"), duration_s=3.0),
        [
            ("guru_meditation_detected", True, "(host-sim)"),
            ("verdict STOP",             True, "(host-sim)"),
        ],
    )


def test_t13_json_validity():
    """Verify that verify_bringup produces valid, complete JSON."""
    def _run():
        results = _run_with_telem_mock(
            FaultSchedule(), soak_minutes=0, skip_soak=True, duration_s=40)
        # Serialize and re-parse to verify JSON validity
        raw_json = json.dumps(results, default=str)
        reparsed = json.loads(raw_json)
        return reparsed

    return _run_test(
        "T13", "JSON report validity",
        _run,
        [
            ("json_has_required_keys", True, "(host-sim)"),
            ("overall_pass is True",   True, "(host-sim)"),
        ],
    )


def test_t14_markdown_validity():
    """Verify markdown report contains required sections."""
    def _run():
        results = _run_with_telem_mock(
            FaultSchedule(), soak_minutes=0, skip_soak=True, duration_s=40)
        md = generate_markdown_report(results, "test_ts")
        return md

    return _run_test(
        "T14", "Markdown report validity",
        _run,
        [
            ("markdown_has_required_sections", True, "(host-sim)"),
        ],
    )


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Toolchain dry-run validation")
    ap.add_argument("--tests", nargs="*",
                    help="Run only these test IDs (e.g. T01 T02); default: all")
    ap.add_argument("--json-out", default="",
                    help="Write full results to this JSON file")
    args = ap.parse_args()

    run_all = not args.tests
    test_filter = set(args.tests or [])

    print()
    print(f"{B}╔══════════════════════════════════════════════════════╗{Z}")
    print(f"{B}║  Commissioning Toolchain Dry-Run Validation          ║{Z}")
    print(f"{B}╚══════════════════════════════════════════════════════╝{Z}")
    print(f"  Evidence label: (host-sim) — PTY + FrameGenerator")
    print(f"  No real ESP32 hardware required.")
    print()

    all_tests = [
        ("T01", test_t01_happy_path),
        ("T02", test_t02_crc_corruption),
        ("T03", test_t03_desync),
        ("T04", test_t04_watchdog_flag),
        ("T05", test_t05_brownout_flag),
        ("T06", test_t06_safe_halt),
        ("T07", test_t07_sensor_freeze),
        ("T08", test_t08_low_delivery),
        ("T09", test_t09_no_frames),
        ("T10", test_t10_ina226_dropout),
        ("T11", test_t11_serial_capture_clean),
        ("T12", test_t12_serial_capture_guru),
        ("T13", test_t13_json_validity),
        ("T14", test_t14_markdown_validity),
    ]

    for tid, fn in all_tests:
        if run_all or tid in test_filter:
            fn()

    # ── Summary ────────────────────────────────────────────────────────
    n_pass = sum(1 for r in _results if r["pass"])
    n_fail = sum(1 for r in _results if not r["pass"])
    n_total = len(_results)

    print()
    print(f"{B}══════════════════════════════════════════════════════{Z}")
    print(f"{B} DRY-RUN VALIDATION SUMMARY{Z}")
    print(f"{B}══════════════════════════════════════════════════════{Z}")

    for r in _results:
        colour = G if r["pass"] else R
        symbol = "PASS" if r["pass"] else "FAIL"
        print(f"  {colour}{symbol}{Z}  {r['test_id']:4s}  {r['name']}")

    print()
    colour = G if n_fail == 0 else R
    print(f"  {colour}{B}{n_pass}/{n_total} tests PASS{Z}"
          + (f"  {R}({n_fail} FAIL){Z}" if n_fail else ""))
    print()

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({
                "n_pass": n_pass,
                "n_fail": n_fail,
                "n_total": n_total,
                "tests": _results,
            }, fh, indent=2, default=str)
        print(f"  Full results: {args.json_out}")
        print()

    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
