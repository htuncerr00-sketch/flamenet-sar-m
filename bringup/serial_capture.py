"""
bringup/serial_capture.py — Continuous UART0 debug log capture
===============================================================
Captures the ESP32 UART0 debug port (115200 baud, text output from ESP_LOG*)
to timestamped files.  Designed to run in a SECOND terminal alongside
verify_bringup.py / soak_test_runner.py which listen on UART1 (921600 baud).

UART mapping:
  UART0  GPIO 1/3   115200 baud   → ESP-IDF debug log (this tool)
  UART1  GPIO 17/16 921600 baud   → binary telemetry (verify_bringup.py)

Features:
  • Millisecond-precision timestamp on every line
  • Colour-coded ESP_LOG* levels (E/W/I/D) on TTY
  • Auto-reconnects within 3 s if the port disappears (firmware reboot)
  • Log rotation: new file every --rotate-mb MB (default 10 MB)
  • Pattern detection and event recording:
      – Boot events:   boot banner, reset reason, UART init, task starts
      – Crashes:       Guru Meditation Error, backtrace, panic
      – Health stats:  heap free, stack HWM from health_monitor lines
      – Resets:        brownout, watchdog, any rst:0x line
  • JSON session summary exported on exit

Usage:
  python serial_capture.py --port /dev/ttyUSB0
  python serial_capture.py --port /dev/ttyUSB0 --out-dir ./logs --duration 1800
  python serial_capture.py --port COM3 --baud 115200 --quiet

Output files (in --out-dir):
  uart0_YYYYMMDD_HHMMSS.log     — timestamped text log
  uart0_session_YYYYMMDD_HHMMSS.json — session event summary
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

# ── colour helpers ────────────────────────────────────────────────────
_IS_TTY = sys.stdout.isatty()
_RED    = "\033[1;31m" if _IS_TTY else ""
_YEL    = "\033[1;33m" if _IS_TTY else ""
_GRN    = "\033[1;32m" if _IS_TTY else ""
_CYN    = "\033[1;36m" if _IS_TTY else ""
_DIM    = "\033[2m"    if _IS_TTY else ""
_RST    = "\033[0m"    if _IS_TTY else ""

# ── log-level colour mapping ──────────────────────────────────────────
_LEVEL_COLOUR = {
    "E": _RED,
    "W": _YEL,
    "I": _GRN,
    "D": _DIM,
    "V": _DIM,
}

# ── patterns ──────────────────────────────────────────────────────────
# Each tuple: (event_type, compiled_pattern)
_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Boot events
    ("boot_banner",      re.compile(r"Filament Winding Telemetry|Faz 19")),
    ("reset_reason",     re.compile(r"Reset reason:\s*(\w+)")),
    ("uart1_init",       re.compile(r"UART1 @ 921600")),
    ("telem_task_start", re.compile(r"telemetry task started")),
    ("sensor_task_start",re.compile(r"sensor I.?C task started")),
    ("health_monitor",   re.compile(r"health_monitor")),
    ("rst_reason_raw",   re.compile(r"rst:0x([0-9a-fA-F]+)")),
    # Crashes
    ("guru_meditation",  re.compile(r"Guru Meditation Error")),
    ("backtrace",        re.compile(r"Backtrace:")),
    ("panic",            re.compile(r"abort\(\)|assert failed|LoadProhibited|StoreProhibited")),
    # Hardware faults
    ("brownout",         re.compile(r"brownout detector|rst:0x.*brownout", re.IGNORECASE)),
    ("watchdog",         re.compile(r"TG0WDT|TWDT|Task watchdog|WDT reset", re.IGNORECASE)),
    ("safe_halt",        re.compile(r"SAFE_HALT|safe halt", re.IGNORECASE)),
    # Sensor health
    ("ina226_fault",     re.compile(r"INA226.*fail|fail.*INA226", re.IGNORECASE)),
    ("mpu6050_fault",    re.compile(r"MPU6050.*fail|fail.*MPU6050|WHO_AM_I", re.IGNORECASE)),
    ("thermal_fault",    re.compile(r"thermal.*fault|NTC.*fail", re.IGNORECASE)),
    # Health monitor lines (heap / stack HWM)
    ("heap_line",        re.compile(r"free heap:\s*(\d+)")),
    ("stack_hwm",        re.compile(r"stack HWM.*telem.*?(\d+)|stack HWM.*sens.*?(\d+)", re.IGNORECASE)),
    # ESP-IDF warnings
    ("esp_error",        re.compile(r"E \(.+?\) ")),
    ("esp_warning",      re.compile(r"W \(.+?\) ")),
]

# Events that are STOP conditions
_STOP_EVENTS = frozenset({"guru_meditation", "panic"})

# Events that are fatal but allow monitoring to continue
_FATAL_EVENTS = frozenset({"brownout", "watchdog"})


# ═══════════════════════════════════════════════════════════════════════
# Session state
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class SessionEvent:
    ts_wall: float
    event_type: str
    line: str
    match_text: str = ""


@dataclass
class SessionStats:
    start_time: str = ""
    end_time: str = ""
    total_lines: int = 0
    total_bytes: int = 0
    reconnects: int = 0
    stop_conditions: list = field(default_factory=list)
    events: list = field(default_factory=list)
    heap_samples: list = field(default_factory=list)   # [(ts, free_bytes)]
    telem_task_started: bool = False
    sensor_task_started: bool = False
    boot_banner_seen: bool = False
    guru_meditation: bool = False
    brownout_count: int = 0
    watchdog_count: int = 0
    verdict: str = "INCOMPLETE"

    def add_event(self, ev: SessionEvent) -> None:
        self.events.append({
            "ts": ev.ts_wall,
            "type": ev.event_type,
            "line": ev.line[:200],
            "match": ev.match_text,
        })
        if ev.event_type == "telem_task_start":
            self.telem_task_started = True
        elif ev.event_type == "sensor_task_start":
            self.sensor_task_started = True
        elif ev.event_type == "boot_banner":
            self.boot_banner_seen = True
        elif ev.event_type == "guru_meditation":
            self.guru_meditation = True
            self.stop_conditions.append("Guru Meditation Error")
        elif ev.event_type in ("brownout",):
            self.brownout_count += 1
        elif ev.event_type in ("watchdog",):
            self.watchdog_count += 1

    def final_verdict(self) -> str:
        if self.guru_meditation:
            return "STOP — UNSAFE: Guru Meditation Error"
        if self.brownout_count >= 3:
            return "STOP — UNSAFE: repeated brownout (power issue)"
        if self.watchdog_count >= 3:
            return "STOP — UNSAFE: repeated watchdog reset"
        if not self.boot_banner_seen:
            return "WARN: boot banner not seen"
        if not self.telem_task_started:
            return "WARN: telemetry task start not confirmed"
        if not self.sensor_task_started:
            return "WARN: sensor task start not confirmed"
        return "OK"


# ═══════════════════════════════════════════════════════════════════════
# Log rotation
# ═══════════════════════════════════════════════════════════════════════

class RotatingLogFile:
    def __init__(self, out_dir: str, base_name: str, max_bytes: int) -> None:
        self._out_dir   = out_dir
        self._base_name = base_name
        self._max_bytes = max_bytes
        self._fh        = None
        self._path      = ""
        self._written   = 0
        self._open_new()

    def _open_new(self) -> None:
        if self._fh:
            self._fh.close()
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._path = os.path.join(self._out_dir, f"{self._base_name}_{ts}.log")
        self._fh = open(self._path, "w", encoding="utf-8", errors="replace")
        self._written = 0

    def write(self, line: str) -> None:
        encoded = line.encode("utf-8", errors="replace")
        self._written += len(encoded)
        self._fh.write(line)
        self._fh.flush()
        if self._written >= self._max_bytes:
            self._open_new()

    @property
    def current_path(self) -> str:
        return self._path

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


# ═══════════════════════════════════════════════════════════════════════
# Main capture loop
# ═══════════════════════════════════════════════════════════════════════

def _colour_line(raw: str) -> str:
    """Apply colour to a raw ESP-IDF log line based on log level."""
    # IDF format: "I (1234) TAG: message" or "E (...) ..."
    m = re.match(r'^([EWIDV]) \(', raw)
    if m:
        level = m.group(1)
        colour = _LEVEL_COLOUR.get(level, "")
        return f"{colour}{raw}{_RST}"
    return raw


def _check_patterns(line: str, t_wall: float) -> list[SessionEvent]:
    events = []
    for ev_type, pat in _PATTERNS:
        m = pat.search(line)
        if m:
            events.append(SessionEvent(
                ts_wall=t_wall,
                event_type=ev_type,
                line=line.rstrip(),
                match_text=m.group(0)[:100],
            ))
            break  # first match wins per line
    return events


def capture(
    port: str,
    baud: int = 115200,
    duration_s: float = 0.0,
    out_dir: str = ".",
    rotate_mb: float = 10.0,
    quiet: bool = False,
) -> SessionStats:
    try:
        import serial
    except ImportError:
        print(f"{_RED}ERROR: pyserial not installed.  Run: pip install pyserial{_RST}")
        sys.exit(1)

    os.makedirs(out_dir, exist_ok=True)
    ts_start_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = RotatingLogFile(out_dir, "uart0", int(rotate_mb * 1024 * 1024))

    stats = SessionStats(
        start_time=datetime.datetime.now().isoformat(),
    )

    if not quiet:
        print()
        print(f"{_CYN}══════════════════════════════════════════════════{_RST}")
        print(f"{_CYN} UART0 Debug Capture — {port} @ {baud}{_RST}")
        print(f"{_CYN}══════════════════════════════════════════════════{_RST}")
        print(f"  Log:     {log_file.current_path}")
        print(f"  Rotate:  every {rotate_mb:.0f} MB")
        if duration_s > 0:
            print(f"  Duration: {duration_s:.0f} s")
        print(f"  Press Ctrl-C to stop.")
        print()

    t_start = time.monotonic()
    leftover = b""
    ser = None
    reconnect_delay = 1.0

    def open_port() -> Optional["serial.Serial"]:  # noqa: F821
        import serial
        try:
            s = serial.Serial(
                port=port, baudrate=baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1,
            )
            if not quiet:
                print(f"  {_GRN}Port opened: {port}{_RST}")
            return s
        except Exception as e:
            if not quiet:
                print(f"  {_YEL}Cannot open {port}: {e} — retrying…{_RST}")
            return None

    try:
        while True:
            if duration_s > 0 and (time.monotonic() - t_start) >= duration_s:
                break

            if ser is None:
                ser = open_port()
                if ser is None:
                    time.sleep(reconnect_delay)
                    continue

            try:
                chunk = ser.read(4096)
            except Exception:
                if not quiet:
                    print(f"  {_YEL}Port disconnected — reconnecting…{_RST}")
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
                stats.reconnects += 1
                time.sleep(reconnect_delay)
                continue

            if not chunk:
                continue

            stats.total_bytes += len(chunk)
            data = leftover + chunk
            lines = data.split(b"\n")
            leftover = lines[-1]  # may be partial

            for raw_bytes in lines[:-1]:
                try:
                    raw = raw_bytes.decode("utf-8", errors="replace")
                except Exception:
                    raw = raw_bytes.decode("latin-1", errors="replace")

                raw = raw.rstrip("\r\n")
                if not raw:
                    continue

                stats.total_lines += 1
                t_wall = time.monotonic() - t_start
                ts_ms = int(t_wall * 1000)

                timestamped = f"[{ts_ms:010d}] {raw}\n"
                log_file.write(timestamped)

                # Pattern matching
                for ev in _check_patterns(raw, t_wall):
                    stats.add_event(ev)

                    # Extract heap free value
                    if ev.event_type == "heap_line":
                        m = re.search(r"free heap:\s*(\d+)", raw, re.IGNORECASE)
                        if m:
                            stats.heap_samples.append((t_wall, int(m.group(1))))

                    if not quiet:
                        level_char = ""
                        m2 = re.match(r'^([EWIDV]) \(', raw)
                        if m2:
                            level_char = m2.group(1)

                        if ev.event_type in _STOP_EVENTS:
                            print(f"{_RED}[{ts_ms:010d}] ⛔ {raw}{_RST}")
                        elif ev.event_type in _FATAL_EVENTS:
                            print(f"{_YEL}[{ts_ms:010d}] ⚠  {raw}{_RST}")
                        elif level_char in ("E", "W"):
                            print(_colour_line(f"[{ts_ms:010d}] {raw}"))
                        else:
                            colour = _LEVEL_COLOUR.get(level_char, "")
                            print(f"{colour}[{ts_ms:010d}] {raw}{_RST}")
                    else:
                        # Quiet mode: only print important events
                        if ev.event_type in _STOP_EVENTS | _FATAL_EVENTS | {
                            "boot_banner", "telem_task_start", "sensor_task_start",
                            "reset_reason", "guru_meditation",
                        }:
                            print(f"[{ts_ms:010d}] [{ev.event_type.upper()}] {raw[:120]}")

    except KeyboardInterrupt:
        if not quiet:
            print()
            print(f"  {_CYN}Capture stopped by user.{_RST}")

    finally:
        if ser:
            try:
                ser.close()
            except Exception:
                pass
        log_file.close()

    stats.end_time = datetime.datetime.now().isoformat()
    stats.verdict = stats.final_verdict()

    # ── Print summary ──────────────────────────────────────────────────
    if not quiet:
        elapsed = time.monotonic() - t_start
        print()
        print(f"  ── Session Summary ──────────────────────────")
        print(f"  Duration:    {elapsed:.1f} s")
        print(f"  Lines:       {stats.total_lines:,}")
        print(f"  Bytes:       {stats.total_bytes:,}")
        print(f"  Reconnects:  {stats.reconnects}")
        print(f"  Events:      {len(stats.events)}")
        print(f"  Boot banner: {'YES' if stats.boot_banner_seen else 'NO'}")
        print(f"  Telem task:  {'YES' if stats.telem_task_started else 'NO'}")
        print(f"  Sensor task: {'YES' if stats.sensor_task_started else 'NO'}")
        if stats.brownout_count:
            print(f"  {_YEL}Brownouts:   {stats.brownout_count}{_RST}")
        if stats.watchdog_count:
            print(f"  {_YEL}Watchdogs:   {stats.watchdog_count}{_RST}")
        if stats.guru_meditation:
            print(f"  {_RED}GURU MEDITATION: YES{_RST}")
        if stats.heap_samples:
            heaps = [h for _, h in stats.heap_samples]
            print(f"  Heap free:   min={min(heaps):,}  max={max(heaps):,}  "
                  f"last={heaps[-1]:,} bytes")
            if max(heaps) - min(heaps) > 4096 and len(heaps) > 5:
                print(f"  {_YEL}WARN: heap range > 4 KB — possible leak{_RST}")
        print()
        c = _GRN if stats.verdict.startswith("OK") else _YEL if stats.verdict.startswith("WARN") else _RED
        print(f"  Verdict: {c}{stats.verdict}{_RST}")
        print()

    return stats


# ═══════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(
        description="UART0 debug log capture for ESP32 bring-up",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Run in a SEPARATE terminal from verify_bringup.py.
  UART0 (/dev/ttyUSB0, 115200) → this tool  [debug text]
  UART1 (/dev/ttyUSB1, 921600) → verify_bringup.py  [telemetry]

Examples:
  python serial_capture.py --port /dev/ttyUSB0
  python serial_capture.py --port /dev/ttyUSB0 --duration 1800 --out-dir ./logs
  python serial_capture.py --port COM3 --quiet
        """,
    )
    ap.add_argument("--port", required=True,
                    help="Serial port for UART0 debug output (usually 115200 baud)")
    ap.add_argument("--baud", type=int, default=115200,
                    help="Baud rate (default: 115200)")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="Capture duration in seconds (0 = run until Ctrl-C)")
    ap.add_argument("--out-dir", default="",
                    help="Output directory (default: bringup/logs/uart0/)")
    ap.add_argument("--rotate-mb", type=float, default=10.0,
                    help="Rotate log file every N MB (default: 10)")
    ap.add_argument("--quiet", action="store_true",
                    help="Only print important events to terminal; still logs all to file")
    args = ap.parse_args()

    out_dir = args.out_dir
    if not out_dir:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        out_dir = os.path.join(script_dir, "logs", "uart0")

    stats = capture(
        port=args.port,
        baud=args.baud,
        duration_s=args.duration,
        out_dir=out_dir,
        rotate_mb=args.rotate_mb,
        quiet=args.quiet,
    )

    # Write JSON summary
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(out_dir, f"uart0_session_{ts}.json")
    os.makedirs(out_dir, exist_ok=True)
    with open(json_path, "w") as fh:
        d = asdict(stats)
        # Truncate event list if huge
        if len(d.get("events", [])) > 500:
            d["events"] = d["events"][:500]
            d["events_truncated"] = True
        json.dump(d, fh, indent=2, default=str)
    print(f"  JSON summary: {json_path}")

    sys.exit(0 if stats.verdict.startswith("OK") else 1)


if __name__ == "__main__":
    main()
