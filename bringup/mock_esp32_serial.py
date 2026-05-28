#!/usr/bin/env python3
"""
bringup/mock_esp32_serial.py — Virtual ESP32 serial device (PTY-based)
=======================================================================
Creates pseudo-terminal (PTY) pairs simulating:
  UART1 (/dev/pts/N)  921600 baud  binary telemetry at 1 kHz
  UART0 (/dev/pts/M)  115200 baud  debug text (boot log + health lines)

Prints slave device paths to stdout on the first two lines:
  UART0=/dev/pts/3
  UART1=/dev/pts/4

Run in one terminal, then connect tools to the printed paths:
  python mock_esp32_serial.py --duration 60 &
  python verify_bringup.py --port /dev/pts/4 --skip-soak
  python serial_capture.py --port /dev/pts/3 --duration 60

Fault injection:
  --crc-corrupt-rate 0.05     5% of frames have flipped CRC
  --desync-rate 0.01          1% of frames have garbage bytes prepended
  --watchdog-at-sec 15        Set F_WATCHDOG_RST for 2 s starting at t=15
  --brownout-at-sec 20        Set F_BROWNOUT for 1 s starting at t=20
  --safe-halt-at-sec 60       Set F_SAFE_HALT starting at t=60
  --ina226-freeze-at-sec 30   Freeze INA226 readings starting at t=30
  --thermal-freeze-at-sec 30  Freeze temp_K reading starting at t=30
  --imu-freeze-at-sec 30      Freeze vib_x reading starting at t=30
  --ina226-drop-at-sec 30     Clear F_INA226_OK for 5 s starting at t=30
  --delivery-fraction 0.7     Only emit 70% of expected 1kHz frames
  --no-frames-after-sec N     Stop emitting telemetry after N seconds
  --jitter-us 100             ±100 µs jitter on ts_us
  --guru-meditation           Emit Guru Meditation in UART0 at t=2 s
  --brownout-log              Emit brownout log line in UART0 at t=2 s
  --watchdog-log              Emit watchdog log line in UART0 at t=2 s
  --duration N                Exit after N seconds (default: run forever)
  --seed N                    RNG seed (default: 42)

Linux only (requires pty module and /dev/pts filesystem).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import threading
import tty
import termios

# ── import from sibling files ─────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mock_frame_generator import (  # noqa: E402
    FrameGenerator, FaultSchedule, MockBootLog, MockBootLogFaultMode,
)


def _configure_master_raw(fd: int) -> None:
    """Set PTY master to raw mode to prevent line-discipline byte mangling."""
    try:
        attrs = termios.tcgetattr(fd)
        # cfmakeraw equivalent: disable all processing
        attrs[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK |
                      termios.ISTRIP | termios.INLCR | termios.IGNCR |
                      termios.ICRNL | termios.IXON)
        attrs[1] &= ~termios.OPOST
        attrs[2] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON |
                      termios.ISIG | termios.IEXTEN)
        attrs[2] |= termios.CS8
        attrs[3] &= ~(termios.CSIZE | termios.PARENB)
        attrs[3] |= termios.CS8
        attrs[6][termios.VMIN]  = 1
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except Exception:
        pass  # On some systems this may not work; keep going


def _telem_loop(
    master_fd: int,
    gen: FrameGenerator,
    duration_s: float,
    stop_event: threading.Event,
) -> None:
    """Emits telemetry frames to UART1 master fd at ~1 kHz."""
    t_start = time.monotonic()
    target_t = t_start

    while not stop_event.is_set():
        now = time.monotonic()
        elapsed_us = int((now - t_start) * 1_000_000)

        if duration_s > 0 and (now - t_start) >= duration_s:
            break

        frame_bytes = gen.next_frame_bytes(elapsed_us)
        if frame_bytes:
            try:
                os.write(master_fd, frame_bytes)
            except OSError:
                break   # slave closed (test finished)

        # Drift-free 1 kHz timing
        target_t += 0.001
        sleep_for = target_t - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
        elif sleep_for < -0.005:
            # Fell behind by > 5 ms — reset target to now
            target_t = time.monotonic()


def _debug_loop(
    master_fd: int,
    boot_log: MockBootLog,
    duration_s: float,
    stop_event: threading.Event,
) -> None:
    """Emits UART0 debug text lines to the debug master fd."""
    t_start = time.monotonic()

    while not stop_event.is_set():
        now = time.monotonic()
        elapsed_s = now - t_start

        if duration_s > 0 and elapsed_s >= duration_s:
            break

        lines = boot_log.get_lines_at(elapsed_s)
        for line in lines:
            try:
                os.write(master_fd, line.encode("utf-8", errors="replace"))
            except OSError:
                return

        time.sleep(0.02)  # 50 Hz poll — fine for text output


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Virtual ESP32 serial device (PTY-based)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Fault injection
    ap.add_argument("--crc-corrupt-rate", type=float, default=0.0)
    ap.add_argument("--desync-rate", type=float, default=0.0)
    ap.add_argument("--watchdog-at-sec", type=float, default=-1.0)
    ap.add_argument("--watchdog-dur-sec", type=float, default=2.0)
    ap.add_argument("--brownout-at-sec", type=float, default=-1.0)
    ap.add_argument("--brownout-dur-sec", type=float, default=1.0)
    ap.add_argument("--safe-halt-at-sec", type=float, default=-1.0)
    ap.add_argument("--safe-halt-dur-sec", type=float, default=10.0)
    ap.add_argument("--ina226-freeze-at-sec", type=float, default=-1.0)
    ap.add_argument("--imu-freeze-at-sec", type=float, default=-1.0)
    ap.add_argument("--thermal-freeze-at-sec", type=float, default=-1.0)
    ap.add_argument("--ina226-drop-at-sec", type=float, default=-1.0)
    ap.add_argument("--ina226-drop-dur-sec", type=float, default=5.0)
    ap.add_argument("--imu-drop-at-sec", type=float, default=-1.0)
    ap.add_argument("--imu-drop-dur-sec", type=float, default=5.0)
    ap.add_argument("--thermal-drop-at-sec", type=float, default=-1.0)
    ap.add_argument("--thermal-drop-dur-sec", type=float, default=5.0)
    ap.add_argument("--jitter-us", type=int, default=0)
    ap.add_argument("--delivery-fraction", type=float, default=1.0)
    ap.add_argument("--no-frames-after-sec", type=float, default=-1.0)
    # UART0 fault modes
    ap.add_argument("--guru-meditation", action="store_true")
    ap.add_argument("--brownout-log", action="store_true")
    ap.add_argument("--watchdog-log", action="store_true")
    # Global
    ap.add_argument("--duration", type=float, default=0.0,
                    help="Run for this many seconds then exit (0=forever)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--quiet", action="store_true",
                    help="Do not print status (only print UART paths)")
    args = ap.parse_args()

    def _sec_to_us(s: float) -> int:
        return int(s * 1_000_000) if s >= 0 else -1

    faults = FaultSchedule(
        crc_corrupt_rate  = args.crc_corrupt_rate,
        desync_rate       = args.desync_rate,
        watchdog_start_us = _sec_to_us(args.watchdog_at_sec),
        watchdog_dur_us   = int(args.watchdog_dur_sec * 1_000_000),
        brownout_start_us = _sec_to_us(args.brownout_at_sec),
        brownout_dur_us   = int(args.brownout_dur_sec * 1_000_000),
        safe_halt_start_us= _sec_to_us(args.safe_halt_at_sec),
        safe_halt_dur_us  = int(args.safe_halt_dur_sec * 1_000_000),
        ina226_freeze_start_us  = _sec_to_us(args.ina226_freeze_at_sec),
        imu_freeze_start_us     = _sec_to_us(args.imu_freeze_at_sec),
        thermal_freeze_start_us = _sec_to_us(args.thermal_freeze_at_sec),
        ina226_drop_start_us  = _sec_to_us(args.ina226_drop_at_sec),
        ina226_drop_dur_us    = int(args.ina226_drop_dur_sec * 1_000_000),
        imu_drop_start_us     = _sec_to_us(args.imu_drop_at_sec),
        imu_drop_dur_us       = int(args.imu_drop_dur_sec * 1_000_000),
        thermal_drop_start_us = _sec_to_us(args.thermal_drop_at_sec),
        thermal_drop_dur_us   = int(args.thermal_drop_dur_sec * 1_000_000),
        jitter_us          = args.jitter_us,
        delivery_fraction  = args.delivery_fraction,
        no_frames_after_us = _sec_to_us(args.no_frames_after_sec),
    )

    # UART0 boot log variant
    if args.guru_meditation:
        boot_log: MockBootLog = MockBootLogFaultMode("guru_meditation")
    elif args.brownout_log:
        boot_log = MockBootLogFaultMode("brownout")
    elif args.watchdog_log:
        boot_log = MockBootLogFaultMode("watchdog")
    else:
        boot_log = MockBootLog()

    gen = FrameGenerator(seed=args.seed, faults=faults)

    # ── Create PTY pairs ──────────────────────────────────────────────
    import pty
    uart1_master, uart1_slave = pty.openpty()
    uart0_master, uart0_slave = pty.openpty()

    _configure_master_raw(uart1_master)
    _configure_master_raw(uart0_master)

    uart1_path = os.ttyname(uart1_slave)
    uart0_path = os.ttyname(uart0_slave)

    # Print paths FIRST — callers read these to know where to connect
    print(f"UART0={uart0_path}", flush=True)
    print(f"UART1={uart1_path}", flush=True)
    if not args.quiet:
        print(f"[mock] UART0 (debug/115200): {uart0_path}", file=sys.stderr, flush=True)
        print(f"[mock] UART1 (telem/921600): {uart1_path}", file=sys.stderr, flush=True)
        if faults.crc_corrupt_rate > 0:
            print(f"[mock] CRC corruption: {faults.crc_corrupt_rate*100:.1f}%", file=sys.stderr)
        if faults.desync_rate > 0:
            print(f"[mock] Desync injection: {faults.desync_rate*100:.1f}%", file=sys.stderr)
        if faults.safe_halt_start_us >= 0:
            print(f"[mock] SAFE_HALT: at t={args.safe_halt_at_sec}s", file=sys.stderr)
        if faults.watchdog_start_us >= 0:
            print(f"[mock] WATCHDOG_RST flag: at t={args.watchdog_at_sec}s", file=sys.stderr)
        if faults.brownout_start_us >= 0:
            print(f"[mock] BROWNOUT flag: at t={args.brownout_at_sec}s", file=sys.stderr)
        if faults.delivery_fraction < 1.0:
            print(f"[mock] Delivery: {faults.delivery_fraction*100:.0f}%", file=sys.stderr)

    stop_event = threading.Event()

    t_telem = threading.Thread(
        target=_telem_loop,
        args=(uart1_master, gen, args.duration, stop_event),
        daemon=True,
    )
    t_debug = threading.Thread(
        target=_debug_loop,
        args=(uart0_master, boot_log, args.duration, stop_event),
        daemon=True,
    )

    t_telem.start()
    t_debug.start()

    try:
        if args.duration > 0:
            time.sleep(args.duration + 1.0)
        else:
            while True:
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        # Close fds — slaves will see EOF
        for fd in (uart1_master, uart1_slave, uart0_master, uart0_slave):
            try:
                os.close(fd)
            except OSError:
                pass

    if not args.quiet:
        print("[mock] Stopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
