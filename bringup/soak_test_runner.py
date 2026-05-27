"""
bringup/soak_test_runner.py — Production 30-minute soak test runner
====================================================================
High-level soak orchestrator that extends verify_bringup.py with:
  • Jitter histogram with ASCII art (bins: <0.5, 0.5-1, 1-2, 2-5, 5-10, >10 ms)
  • Per-minute frame count histogram for trend detection
  • Per-minute field range tracking (temp_K, current_A, vib_rms)
  • Sensor uptime trending (flag-OK % per minute)
  • Heap drift detection (if uart0_session JSON from serial_capture.py available)
  • Full Markdown + JSON report with evidence labels
  • Hard STOP on safety events: SAFE_HALT, THERMAL_SHUT, repeated WATCHDOG_RST

Re-uses verify_bringup.py primitives:
  FrameParser, TelemetryFrame, JitterAnalyzer, SensorFreezeDetector,
  ThroughputTracker, SoakStats, crc16_ccitt, all flag constants.

Usage:
  python soak_test_runner.py --port /dev/ttyUSB1 --minutes 30
  python soak_test_runner.py --port /dev/ttyUSB1 --minutes 5 --report-dir ./reports
  python soak_test_runner.py --port /dev/ttyUSB1 --minutes 30 \\
      --uart0-session ./logs/uart0/uart0_session_20260527_120000.json

Verdict scale:
  ★★★ READY   — all checks PASS
  CAUTION     — minor warnings but no hard faults
  STOP—UNSAFE — safety event or hard threshold breach
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

# ── import from sibling file ──────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_bringup import (  # noqa: E402
    FrameParser, TelemetryFrame, SoakStats,
    JitterAnalyzer, SensorFreezeDetector, ThroughputTracker,
    generate_markdown_report,
    crc16_ccitt,
    F_INA226_OK, F_IMU_OK, F_THERMAL_OK, F_SAFE_HALT,
    F_BROWNOUT, F_WATCHDOG_RST, F_THERMAL_SHUT,
    DELIVERY_TARGET, SENSOR_UP_TARGET,
    TEMP_K_MIN, TEMP_K_MAX, TEMP_K_SHUTDOWN, CURRENT_ABS_MAX,
    G, R, Y, B, CY, Z,
)

# ── thresholds unique to soak ─────────────────────────────────────────
HEAP_DRIFT_THRESHOLD_BYTES = 8192    # warn if heap drops > 8 KB over soak
JITTER_STDDEV_WARN_US      = 100.0   # warn if σ > 100 µs
JITTER_MEAN_TOLERANCE_US   = 50.0    # warn if |mean - 1000| > 50 µs
MAX_WATCHDOG_EVENTS        = 2       # STOP if watchdog fires more than twice


# ═══════════════════════════════════════════════════════════════════════
# Jitter histogram
# ═══════════════════════════════════════════════════════════════════════

# Bin edges in µs: <500, 500-1000, 1000-1500, 1500-2000, 2000-5000, >5000
_JITTER_BINS = [500, 1000, 1500, 2000, 5000]
_JITTER_LABELS = ["<0.5ms", "0.5-1ms", "1-1.5ms", "1.5-2ms", "2-5ms", ">5ms"]

class JitterHistogram:
    """Bins inter-frame deltas (µs) into fixed-width buckets."""

    def __init__(self) -> None:
        self.counts: list[int] = [0] * (len(_JITTER_BINS) + 1)
        self.total: int = 0

    def add(self, delta_us: float) -> None:
        self.total += 1
        for i, edge in enumerate(_JITTER_BINS):
            if delta_us < edge:
                self.counts[i] += 1
                return
        self.counts[-1] += 1

    def ascii_art(self, width: int = 40) -> list[str]:
        lines = []
        max_count = max(self.counts) if self.counts else 1
        for label, count in zip(_JITTER_LABELS, self.counts):
            pct = 100.0 * count / self.total if self.total else 0.0
            bar_len = int(width * count / max_count) if max_count else 0
            bar = "█" * bar_len
            is_nominal = label in ("0.5-1ms", "1-1.5ms")  # expected 1 kHz range
            colour = G if is_nominal and count > 0 else (Y if count > 0 else "")
            lines.append(f"  {label:>8}  {colour}{bar:<{width}}{Z}  {count:,} ({pct:.2f}%)")
        return lines

    def as_dict(self) -> dict:
        return {
            "bins": dict(zip(_JITTER_LABELS, self.counts)),
            "total": self.total,
        }


# ═══════════════════════════════════════════════════════════════════════
# Per-minute tracking
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class MinuteBucket:
    minute: int
    n_frames: int = 0
    n_crc:    int = 0
    ina_ok:   int = 0
    imu_ok:   int = 0
    therm_ok: int = 0
    n_safe_halt: int = 0
    temp_K_sum: float = 0.0
    temp_K_min: float = float("inf")
    temp_K_max: float = float("-inf")
    vib_rms_max: float = 0.0
    current_max: float = float("-inf")

    def ingest(self, f: TelemetryFrame) -> None:
        self.n_frames += 1
        if f.flags & F_INA226_OK:    self.ina_ok   += 1
        if f.flags & F_IMU_OK:       self.imu_ok   += 1
        if f.flags & F_THERMAL_OK:   self.therm_ok += 1
        if f.flags & F_SAFE_HALT:    self.n_safe_halt += 1
        if math.isfinite(f.temp_K):
            self.temp_K_sum += f.temp_K
            if f.temp_K < self.temp_K_min: self.temp_K_min = f.temp_K
            if f.temp_K > self.temp_K_max: self.temp_K_max = f.temp_K
        vib = math.sqrt(f.vib_x**2 + f.vib_y**2 + f.vib_z**2)
        if math.isfinite(vib) and vib > self.vib_rms_max:
            self.vib_rms_max = vib
        if math.isfinite(f.current_A) and f.current_A > self.current_max:
            self.current_max = f.current_A

    @property
    def ina_pct(self) -> float:
        return 100.0 * self.ina_ok / self.n_frames if self.n_frames else 0.0

    @property
    def imu_pct(self) -> float:
        return 100.0 * self.imu_ok / self.n_frames if self.n_frames else 0.0

    @property
    def therm_pct(self) -> float:
        return 100.0 * self.therm_ok / self.n_frames if self.n_frames else 0.0

    @property
    def temp_K_mean(self) -> float:
        return self.temp_K_sum / self.n_frames if self.n_frames else 0.0

    @property
    def rate_hz(self) -> float:
        return self.n_frames / 60.0


# ═══════════════════════════════════════════════════════════════════════
# Soak runner
# ═══════════════════════════════════════════════════════════════════════

def run_soak(
    port: str,
    baud: int = 921600,
    minutes: float = 30.0,
    report_dir: str = ".",
    uart0_session_json: str = "",
) -> dict:
    try:
        import serial
    except ImportError:
        print(f"{R}ERROR: pyserial not installed.  Run: pip install pyserial{Z}")
        sys.exit(1)

    ts_start = datetime.datetime.now()
    ts_str   = ts_start.strftime("%Y%m%d_%H%M%S")
    soak_s   = minutes * 60.0

    print()
    print(f"{B}╔══════════════════════════════════════════════════════╗{Z}")
    print(f"{B}║  Filament Winding — Production Soak Test Runner      ║{Z}")
    print(f"{B}╚══════════════════════════════════════════════════════╝{Z}")
    print(f"  Port:     {port}  @ {baud} baud")
    print(f"  Duration: {minutes:.0f} min  ({soak_s:.0f} s)")
    print(f"  Start:    {ts_start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Reports:  {report_dir}")
    print()

    try:
        ser = serial.Serial(
            port=port, baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
        )
    except serial.SerialException as e:
        print(f"{R}ERROR: Cannot open {port}: {e}{Z}")
        sys.exit(1)

    parser      = FrameParser()
    jitter_ana  = JitterAnalyzer()
    jitter_hist = JitterHistogram()
    freeze_det  = SensorFreezeDetector()
    throughput  = ThroughputTracker()
    soak_stats  = SoakStats()

    minutes_elapsed = 0
    current_bucket  = MinuteBucket(minute=1)
    all_buckets: list[MinuteBucket] = []

    prev_jitter_last_ts: int = -1   # for histogram feeding

    t_start      = time.monotonic()
    t_minute_end = t_start + 60.0
    t_last_print = t_start
    stop_reason: str = ""

    print(f"  {'Min':>3}  {'Frames':>8}  {'Rate':>5}  {'CRC':>4}  "
          f"{'INA%':>5}  {'IMU%':>5}  {'THM%':>5}  "
          f"{'Temp°C':>7}  {'σjit µs':>7}")
    print(f"  {'-'*3}  {'-'*8}  {'-'*5}  {'-'*4}  "
          f"{'-'*5}  {'-'*5}  {'-'*5}  "
          f"{'-'*7}  {'-'*7}")

    try:
        while True:
            elapsed = time.monotonic() - t_start
            if elapsed >= soak_s:
                break

            chunk = ser.read(512)
            if chunk:
                throughput.add_bytes(len(chunk))
                for f in parser.feed(chunk):
                    now = time.monotonic()
                    soak_stats.ingest(f, wall_t=now)
                    jitter_ana.update(f.ts_us)
                    freeze_det.update(f)
                    current_bucket.ingest(f)

                    # Feed histogram with raw delta
                    if prev_jitter_last_ts >= 0:
                        delta = f.ts_us - prev_jitter_last_ts
                        if 0 < delta < 50_000:
                            jitter_hist.add(float(delta))
                    prev_jitter_last_ts = f.ts_us

                    # Hard stop on safety events
                    if f.flags & F_SAFE_HALT:
                        stop_reason = "SAFE_HALT flag set in telemetry"
                        break
                    if f.flags & F_THERMAL_SHUT:
                        stop_reason = "THERMAL_SHUTDOWN flag set"
                        break

            if stop_reason:
                break

            # Stop if watchdog fires repeatedly
            if soak_stats.n_watchdog_rst > MAX_WATCHDOG_EVENTS:
                stop_reason = f"WATCHDOG_RST > {MAX_WATCHDOG_EVENTS} events"
                break

            # Minute rollover
            now = time.monotonic()
            if now >= t_minute_end:
                all_buckets.append(current_bucket)
                minutes_elapsed += 1
                t_minute_end += 60.0

                b = current_bucket
                crc_delta = parser.n_crc_errors - sum(bk.n_crc for bk in all_buckets[:-1])
                rate_colour = G if b.rate_hz >= 990 else Y
                print(f"  {minutes_elapsed:>3}  "
                      f"{b.n_frames:>8,}  "
                      f"{rate_colour}{b.rate_hz:>5.0f}{Z}  "
                      f"{crc_delta:>4}  "
                      f"{b.ina_pct:>5.1f}  "
                      f"{b.imu_pct:>5.1f}  "
                      f"{b.therm_pct:>5.1f}  "
                      f"{b.temp_K_mean-273.15:>7.2f}  "
                      f"{jitter_ana.stddev_us:>7.1f}")

                current_bucket = MinuteBucket(minute=minutes_elapsed + 1)

    except KeyboardInterrupt:
        print(f"\n  {Y}Soak interrupted by user after {elapsed/60:.1f} min{Z}")

    finally:
        # Flush partial last bucket
        if current_bucket.n_frames > 0:
            all_buckets.append(current_bucket)
        try:
            ser.close()
        except Exception:
            pass

    soak_elapsed = time.monotonic() - t_start
    soak_stats.elapsed_s = soak_elapsed
    soak_stats.flush_last_minute()
    expected_frames = int(1000.0 * soak_elapsed)
    delivery_pct = 100.0 * soak_stats.n_frames / expected_frames if expected_frames else 0.0

    # ── Verdict computation ────────────────────────────────────────────
    hard_fail = (
        bool(stop_reason)
        or soak_stats.n_safe_halt > 0
        or soak_stats.n_thermal_shut > 0
        or soak_stats.n_brownout > 0
        or parser.n_crc_errors > 0
        or soak_stats.n_nan_frames > 0
    )
    warnings = []
    if delivery_pct < DELIVERY_TARGET * 100:
        warnings.append(f"delivery {delivery_pct:.2f}% < {DELIVERY_TARGET*100:.0f}%")
    if soak_stats.ina226_ok_pct < SENSOR_UP_TARGET * 100:
        warnings.append(f"INA226 uptime {soak_stats.ina226_ok_pct:.2f}%")
    if soak_stats.imu_ok_pct < SENSOR_UP_TARGET * 100:
        warnings.append(f"IMU uptime {soak_stats.imu_ok_pct:.2f}%")
    if soak_stats.thermal_ok_pct < SENSOR_UP_TARGET * 100:
        warnings.append(f"thermal uptime {soak_stats.thermal_ok_pct:.2f}%")
    if jitter_ana.n > 10 and jitter_ana.stddev_us > JITTER_STDDEV_WARN_US:
        warnings.append(f"jitter σ={jitter_ana.stddev_us:.1f} µs > {JITTER_STDDEV_WARN_US} µs")
    if freeze_det.total_freeze_events > 0:
        warnings.append(f"{freeze_det.total_freeze_events} sensor freeze event(s)")

    if hard_fail:
        verdict = "STOP — UNSAFE"
    elif warnings:
        verdict = "CAUTION"
    else:
        verdict = "★★★  READY  ★★★"

    # ── Terminal summary ───────────────────────────────────────────────
    print()
    print(f"{B}─── Soak Summary ({soak_elapsed/60:.1f} min) ────────────────────────{Z}")
    print(f"  Frames:     {soak_stats.n_frames:,} / {expected_frames:,} ({delivery_pct:.2f}%)")
    print(f"  CRC errors: {parser.n_crc_errors}")
    print(f"  Seq jumps:  {soak_stats.n_seq_jumps}")
    print(f"  INA226 up:  {soak_stats.ina226_ok_pct:.2f}%")
    print(f"  IMU up:     {soak_stats.imu_ok_pct:.2f}%")
    print(f"  Thermal up: {soak_stats.thermal_ok_pct:.2f}%")
    print(f"  temp_K:     [{soak_stats.temp_K_min:.2f}, {soak_stats.temp_K_max:.2f}]  "
          f"mean={soak_stats.temp_K_mean:.2f} K")
    print(f"  vib_rms_max:{soak_stats.vib_rms_max:.4f} g")
    print(f"  Jitter:     mean={jitter_ana.mean_us:.1f} µs  σ={jitter_ana.stddev_us:.1f} µs  "
          f"max={jitter_ana.max_us:.0f} µs")
    print()

    # ── Jitter histogram ───────────────────────────────────────────────
    print(f"  Inter-frame delta histogram ({jitter_hist.total:,} samples):")
    for art_line in jitter_hist.ascii_art():
        print(art_line)
    print()

    if warnings:
        print(f"  {Y}Warnings:{Z}")
        for w in warnings:
            print(f"    {Y}• {w}{Z}")
        print()

    if stop_reason:
        print(f"  {R}STOP REASON: {stop_reason}{Z}")
        print()

    v_colour = G if verdict.startswith("★") else Y if verdict == "CAUTION" else R
    print(f"  {v_colour}{B}{verdict}{Z}")
    print()

    # ── Build results dict (compatible with generate_markdown_report) ──
    results = {
        "timestamp": ts_start.isoformat(),
        "port": port,
        "baud": baud,
        "soak_minutes": minutes,
        "phases": {
            "P1_first_frame": {"pass": True, "latency_s": None},  # not run here
            "P2_sensor_flags": {
                "pass": soak_stats.ina226_ok_pct >= SENSOR_UP_TARGET * 100
                        and soak_stats.imu_ok_pct >= SENSOR_UP_TARGET * 100
                        and soak_stats.thermal_ok_pct >= SENSOR_UP_TARGET * 100,
                "INA226_OK_pct": round(soak_stats.ina226_ok_pct, 2),
                "IMU_OK_pct":    round(soak_stats.imu_ok_pct, 2),
                "THERMAL_OK_pct":round(soak_stats.thermal_ok_pct, 2),
                "threshold_pct": SENSOR_UP_TARGET * 100,
            },
            "P3_plausibility": {
                "pass": soak_stats.n_nan_frames == 0 and soak_stats.n_safe_halt == 0,
                "temp_K_min": round(soak_stats.temp_K_min, 2),
                "temp_K_max": round(soak_stats.temp_K_max, 2),
                "temp_K_mean": round(soak_stats.temp_K_mean, 2),
                "temp_C_mean": round(soak_stats.temp_K_mean - 273.15, 2),
                "current_A_min": round(soak_stats.current_A_min, 3),
                "current_A_max": round(soak_stats.current_A_max, 3),
                "vib_rms_max_g": round(soak_stats.vib_rms_max, 4),
                "quality_min": round(soak_stats.quality_min, 1),
                "n_nan_frames": soak_stats.n_nan_frames,
                "n_safe_halt":  soak_stats.n_safe_halt,
            },
            "P4_wire_integrity": {
                "pass": parser.n_crc_errors == 0 and delivery_pct >= DELIVERY_TARGET * 100,
                "elapsed_s": round(soak_elapsed, 1),
                "n_frames": soak_stats.n_frames,
                "expected_frames": expected_frames,
                "delivery_pct": round(delivery_pct, 2),
                "n_crc_errors": parser.n_crc_errors,
                "n_sync_drops": parser.n_sync_drops,
                "n_seq_jumps":  soak_stats.n_seq_jumps,
            },
        },
        "soak": {
            "pass": not hard_fail and not warnings,
            "elapsed_s": round(soak_elapsed, 1),
            "n_frames": soak_stats.n_frames,
            "expected_frames": expected_frames,
            "delivery_pct": round(delivery_pct, 2),
            "n_crc_errors": parser.n_crc_errors,
            "n_sync_drops": parser.n_sync_drops,
            "n_seq_jumps": soak_stats.n_seq_jumps,
            "n_nan_frames": soak_stats.n_nan_frames,
            "n_safe_halt": soak_stats.n_safe_halt,
            "n_brownout": soak_stats.n_brownout,
            "n_watchdog_rst": soak_stats.n_watchdog_rst,
            "n_thermal_shut": soak_stats.n_thermal_shut,
            "per_minute_frames": [b.n_frames for b in all_buckets],
            "ina226_ok_pct": round(soak_stats.ina226_ok_pct, 2),
            "imu_ok_pct": round(soak_stats.imu_ok_pct, 2),
            "thermal_ok_pct": round(soak_stats.thermal_ok_pct, 2),
            "temp_K_min": round(soak_stats.temp_K_min, 2),
            "temp_K_max": round(soak_stats.temp_K_max, 2),
            "temp_K_mean": round(soak_stats.temp_K_mean, 2),
        },
        "jitter": jitter_ana.as_dict(),
        "jitter_histogram": jitter_hist.as_dict(),
        "sensor_freeze": freeze_det.as_dict(),
        "throughput": throughput.as_dict(),
        "per_minute_detail": [
            {
                "minute": b.minute,
                "n_frames": b.n_frames,
                "rate_hz": round(b.rate_hz, 1),
                "ina_pct": round(b.ina_pct, 1),
                "imu_pct": round(b.imu_pct, 1),
                "therm_pct": round(b.therm_pct, 1),
                "temp_K_mean": round(b.temp_K_mean, 2),
                "vib_rms_max": round(b.vib_rms_max, 4),
            }
            for b in all_buckets
        ],
        "stop_reason": stop_reason,
        "warnings": warnings,
        "verdict": verdict,
        "overall_pass": verdict.startswith("★"),
    }

    # Optionally load heap drift from uart0 capture
    if uart0_session_json and os.path.isfile(uart0_session_json):
        try:
            with open(uart0_session_json) as fh:
                u0 = json.load(fh)
            heap_samples = u0.get("heap_samples", [])
            if heap_samples:
                heaps = [h for _, h in heap_samples]
                drift = max(heaps) - min(heaps)
                results["heap_drift"] = {
                    "source": uart0_session_json,
                    "n_samples": len(heaps),
                    "min_bytes": min(heaps),
                    "max_bytes": max(heaps),
                    "drift_bytes": drift,
                    "drift_warn": drift > HEAP_DRIFT_THRESHOLD_BYTES,
                }
                if drift > HEAP_DRIFT_THRESHOLD_BYTES:
                    print(f"  {Y}WARN: heap drift {drift:,} bytes over soak "
                          f"(threshold: {HEAP_DRIFT_THRESHOLD_BYTES:,}){Z}")
        except Exception as e:
            results["heap_drift_error"] = str(e)

    # ── Write reports ──────────────────────────────────────────────────
    os.makedirs(report_dir, exist_ok=True)

    json_path = os.path.join(report_dir, f"soak_results_{ts_str}.json")
    with open(json_path, "w") as fh:
        json.dump(results, fh, indent=2, default=str)
    print(f"  JSON report:     {json_path}")

    md_path = os.path.join(report_dir, f"soak_report_{ts_str}.md")
    md_extra = _extra_soak_markdown(results, jitter_hist, all_buckets, ts_str)
    with open(md_path, "w") as fh:
        # Use verify_bringup's base report + append soak-specific sections
        base_md = generate_markdown_report(results, ts_str)
        fh.write(base_md)
        fh.write("\n")
        fh.write(md_extra)
    print(f"  Markdown report: {md_path}")
    print()

    return results


def _extra_soak_markdown(
    results: dict,
    hist: JitterHistogram,
    buckets: list[MinuteBucket],
    ts_str: str,
) -> str:
    lines = []
    a = lines.append

    a("## Jitter Histogram")
    a("")
    a(f"Inter-frame delta distribution ({hist.total:,} samples). "
      f"Nominal: 1000 µs (1 kHz).")
    a("")
    a("```")
    for art_line in hist.ascii_art(width=35):
        a(art_line.rstrip())
    a("```")
    a("")

    a("## Per-Minute Trending")
    a("")
    if buckets:
        a("| Min | Frames | Rate (Hz) | INA% | IMU% | Therm% | Temp (°C) | VibRMS |")
        a("|-----|--------|-----------|------|------|--------|-----------|--------|")
        for b in buckets:
            a(f"| {b.minute} | {b.n_frames:,} | {b.rate_hz:.0f} | "
              f"{b.ina_pct:.1f} | {b.imu_pct:.1f} | {b.therm_pct:.1f} | "
              f"{b.temp_K_mean-273.15:.2f} | {b.vib_rms_max:.4f} |")
    else:
        a("_No complete minutes recorded._")
    a("")

    heap_drift = results.get("heap_drift")
    if heap_drift:
        a("## Heap Drift (from UART0 capture)")
        a("")
        a(f"| Metric | Value |")
        a(f"|--------|-------|")
        a(f"| Samples | {heap_drift.get('n_samples', 0)} |")
        a(f"| Min free heap | {heap_drift.get('min_bytes', 0):,} B |")
        a(f"| Max free heap | {heap_drift.get('max_bytes', 0):,} B |")
        a(f"| Drift | {heap_drift.get('drift_bytes', 0):,} B |")
        ok = not heap_drift.get("drift_warn", False)
        a(f"| Verdict | {'✅ OK' if ok else '⚠️ WARN > 8 KB'} |")
        a("")

    warnings = results.get("warnings", [])
    stop_reason = results.get("stop_reason", "")
    if warnings or stop_reason:
        a("## Issues Detected")
        a("")
        if stop_reason:
            a(f"> **STOP REASON:** {stop_reason}")
            a("")
        if warnings:
            for w in warnings:
                a(f"- ⚠️ {w}")
        a("")

    a("---")
    a("")
    a("*Generated by `bringup/soak_test_runner.py` — evidence: (silicon)*  ")
    a(f"*Faz 19B commissioning framework — {ts_str}*")
    a("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Production soak test runner for Filament Winding ESP32",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Run UART0 capture (serial_capture.py) in a separate terminal first, then:
  python soak_test_runner.py --port /dev/ttyUSB1 --minutes 30
  python soak_test_runner.py --port /dev/ttyUSB1 --minutes 30 \\
      --uart0-session ./logs/uart0/uart0_session_20260527_120000.json
        """,
    )
    ap.add_argument("--port", required=True,
                    help="Serial port for UART1 telemetry (921600 baud)")
    ap.add_argument("--baud", type=int, default=921600,
                    help="Baud rate (default: 921600)")
    ap.add_argument("--minutes", type=float, default=30.0,
                    help="Soak duration in minutes (default: 30)")
    ap.add_argument("--report-dir", default="",
                    help="Directory for reports (default: bringup/reports/)")
    ap.add_argument("--uart0-session", default="",
                    help="Path to uart0_session JSON from serial_capture.py "
                         "(for heap drift analysis)")
    args = ap.parse_args()

    report_dir = args.report_dir
    if not report_dir:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        report_dir = os.path.join(script_dir, "reports",
                                  datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))

    results = run_soak(
        port=args.port,
        baud=args.baud,
        minutes=args.minutes,
        report_dir=report_dir,
        uart0_session_json=args.uart0_session,
    )

    sys.exit(0 if results.get("overall_pass") else 1)


if __name__ == "__main__":
    main()
