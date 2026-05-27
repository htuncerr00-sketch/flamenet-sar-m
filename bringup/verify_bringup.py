"""
bringup/verify_bringup.py — First Silicon Bring-Up Verification Harness
=========================================================================
Standalone (pyserial only — no PySide6, no Qt, no firmware simulator).
Connects directly to UART1 telemetry port at 921600 baud and verifies the
full bring-up checklist from CLAUDE.md §11 P0.

Wire protocol expected:
  MAGIC (2B) = 0xAA 0x55
  PAYLOAD (64B), struct '>QHHffffffffffffHH'
  CRC-16/CCITT of payload[0..61] stored at payload[62..63]

Five verification phases:
  P1  First frame received within 5 s of opening the port
  P2  Sensor health flags: INA226_OK (bit 9), IMU_OK (bit 10), THERMAL_OK (bit 11)
  P3  Data plausibility: temperature, current, vibration in expected ranges
  P4  Wire integrity: 0 CRC errors, ≥99% delivery over first 30 s
  P5  Soak: configurable duration (default 10 min); all P2-P4 criteria maintained

Usage:
  python verify_bringup.py --port /dev/ttyUSB1 --soak-minutes 10
  python verify_bringup.py --port COM3 --soak-minutes 1   # quick smoke test
  python verify_bringup.py --port /dev/ttyUSB1 --skip-soak

Outputs:
  • Live terminal progress (colour if TTY)
  • JSON results: bringup_results_YYYYMMDD_HHMMSS.json  (in ./bringup/)
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import struct
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

# ── optional colour ───────────────────────────────────────────────────
_IS_TTY = sys.stdout.isatty()
G  = "\033[1;32m"  if _IS_TTY else ""
R  = "\033[1;31m"  if _IS_TTY else ""
Y  = "\033[1;33m"  if _IS_TTY else ""
B  = "\033[1m"     if _IS_TTY else ""
CY = "\033[1;36m"  if _IS_TTY else ""
Z  = "\033[0m"     if _IS_TTY else ""

# ── wire constants ────────────────────────────────────────────────────
MAGIC          = b"\xaa\x55"
WIRE_LEN       = 66          # 2 magic + 64 payload
PAYLOAD_LEN    = 64
CRC_DATA_LEN   = 62          # CRC covers payload[0..61]
TELEM_FMT      = struct.Struct(">QHHffffffffffffHH")
assert TELEM_FMT.size == PAYLOAD_LEN

# ── flag bits ─────────────────────────────────────────────────────────
F_BOOT_OK        = 1 << 0
F_TENSION_OK     = 1 << 1
F_TEMP_OK        = 1 << 2
F_RPM_OK         = 1 << 3
F_VIBRATION_OK   = 1 << 4
F_HOMED          = 1 << 5
F_RUNNING        = 1 << 6
F_ESTOP          = 1 << 7
F_SAFE_HALT      = 1 << 8
F_INA226_OK      = 1 << 9
F_IMU_OK         = 1 << 10
F_THERMAL_OK     = 1 << 11
F_BROWNOUT       = 1 << 12
F_THERMAL_SHUT   = 1 << 13
F_WATCHDOG_RST   = 1 << 14

# ── pass/fail thresholds ──────────────────────────────────────────────
# Adjust if your board's operating environment differs.
TEMP_K_MIN       = 273.15    # 0 °C
TEMP_K_MAX       = 348.15    # 75 °C  (above ambient, below shutdown 373.15 K)
TEMP_K_SHUTDOWN  = 373.15    # safety threshold — flag if frame reaches this
CURRENT_ABS_MAX  = 45.0      # safety threshold A
CURRENT_ABS_MAX_BENCH = 5.0  # expected bench idle current (will just check sign)
VIB_MAX_G        = 2.0       # ±2 g range of MPU6050
VIB_NOISE_FLOOR  = 0.005     # g — idle noise should be below this after warmup
DELIVERY_TARGET  = 0.99      # 99% frame delivery
SENSOR_UP_TARGET = 0.98      # 98% uptime for each sensor flag
PHASE4_WINDOW_S  = 30.0      # seconds of data to evaluate P4 delivery
FIRST_FRAME_TIMEOUT_S = 10.0 # seconds to wait for the first frame


# ═══════════════════════════════════════════════════════════════════════
# CRC-16/CCITT
# ═══════════════════════════════════════════════════════════════════════

def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# ═══════════════════════════════════════════════════════════════════════
# Telemetry frame
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class TelemetryFrame:
    ts_us:     int
    seq:       int
    flags:     int
    x_mm:      float
    a_deg:     float
    T_N:       float
    rpm:       float
    vib_x:     float
    vib_y:     float
    vib_z:     float
    temp_K:    float
    current_A: float
    alpha:     float
    quality:   float
    spare:     float

    @classmethod
    def unpack(cls, payload: bytes) -> "TelemetryFrame":
        vals = TELEM_FMT.unpack(payload)
        # vals = (ts_us, seq, flags, x_mm, a_deg, T_N, rpm, vib_x, vib_y, vib_z,
        #         temp_K, current_A, alpha, quality, spare, _reserved, crc16)
        return cls(
            ts_us=vals[0], seq=vals[1], flags=vals[2],
            x_mm=vals[3], a_deg=vals[4], T_N=vals[5], rpm=vals[6],
            vib_x=vals[7], vib_y=vals[8], vib_z=vals[9],
            temp_K=vals[10], current_A=vals[11], alpha=vals[12],
            quality=vals[13], spare=vals[14],
        )

    def is_finite(self) -> bool:
        for v in (self.x_mm, self.a_deg, self.T_N, self.rpm,
                  self.vib_x, self.vib_y, self.vib_z,
                  self.temp_K, self.current_A, self.alpha, self.quality):
            if not math.isfinite(v):
                return False
        return True


# ═══════════════════════════════════════════════════════════════════════
# Frame parser — byte-streaming with sync recovery
# ═══════════════════════════════════════════════════════════════════════

class FrameParser:
    """Incremental byte-stream parser.  Feed bytes; receive complete frames."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.n_frames_ok   = 0
        self.n_crc_errors  = 0
        self.n_sync_drops  = 0    # bytes discarded before finding magic

    def feed(self, data: bytes) -> list[TelemetryFrame]:
        """Feed raw bytes; returns list of decoded frames (may be empty)."""
        self._buf.extend(data)
        frames: list[TelemetryFrame] = []

        while True:
            # Scan for magic
            idx = self._buf.find(MAGIC)
            if idx == -1:
                # No magic anywhere; keep last byte in case it's 0xAA
                self.n_sync_drops += max(0, len(self._buf) - 1)
                self._buf = self._buf[-1:]
                break

            if idx > 0:
                self.n_sync_drops += idx
                del self._buf[:idx]

            # Need full frame
            if len(self._buf) < WIRE_LEN:
                break

            frame_bytes = bytes(self._buf[:WIRE_LEN])
            del self._buf[:WIRE_LEN]

            payload = frame_bytes[2:]          # 64 bytes
            crc_expected = crc16_ccitt(payload[:CRC_DATA_LEN])
            crc_actual   = struct.unpack_from(">H", payload, CRC_DATA_LEN)[0]

            if crc_actual != crc_expected:
                self.n_crc_errors += 1
                # Put back all but the magic bytes and re-search
                self._buf[0:0] = frame_bytes[2:]
                continue

            frame = TelemetryFrame.unpack(payload)
            self.n_frames_ok += 1
            frames.append(frame)

        return frames


# ═══════════════════════════════════════════════════════════════════════
# Statistics accumulator
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class SoakStats:
    n_frames:         int = 0
    n_crc_errors:     int = 0
    n_sync_drops:     int = 0
    n_nan_frames:     int = 0
    n_seq_jumps:      int = 0       # non-consecutive sequence numbers

    n_ina226_ok:      int = 0
    n_imu_ok:         int = 0
    n_thermal_ok:     int = 0
    n_safe_halt:      int = 0
    n_brownout:       int = 0
    n_watchdog_rst:   int = 0
    n_thermal_shut:   int = 0

    temp_K_min:       float = float("inf")
    temp_K_max:       float = float("-inf")
    temp_K_sum:       float = 0.0

    current_A_min:    float = float("inf")
    current_A_max:    float = float("-inf")

    vib_rms_max:      float = 0.0
    quality_min:      float = float("inf")

    _last_seq:        int = field(default=-1, repr=False)
    elapsed_s:        float = 0.0

    def ingest(self, f: TelemetryFrame) -> None:
        self.n_frames += 1

        if not f.is_finite():
            self.n_nan_frames += 1

        if self._last_seq >= 0:
            expected = (self._last_seq + 1) & 0xFFFF
            if f.seq != expected:
                self.n_seq_jumps += 1
        self._last_seq = f.seq

        if f.flags & F_INA226_OK:  self.n_ina226_ok  += 1
        if f.flags & F_IMU_OK:     self.n_imu_ok     += 1
        if f.flags & F_THERMAL_OK: self.n_thermal_ok += 1
        if f.flags & F_SAFE_HALT:  self.n_safe_halt  += 1
        if f.flags & F_BROWNOUT:   self.n_brownout   += 1
        if f.flags & F_WATCHDOG_RST: self.n_watchdog_rst += 1
        if f.flags & F_THERMAL_SHUT: self.n_thermal_shut += 1

        if math.isfinite(f.temp_K):
            self.temp_K_min = min(self.temp_K_min, f.temp_K)
            self.temp_K_max = max(self.temp_K_max, f.temp_K)
            self.temp_K_sum += f.temp_K

        if math.isfinite(f.current_A):
            self.current_A_min = min(self.current_A_min, f.current_A)
            self.current_A_max = max(self.current_A_max, f.current_A)

        vib_rms = math.sqrt(f.vib_x**2 + f.vib_y**2 + f.vib_z**2)
        if math.isfinite(vib_rms):
            self.vib_rms_max = max(self.vib_rms_max, vib_rms)

        if math.isfinite(f.quality):
            self.quality_min = min(self.quality_min, f.quality)

    @property
    def temp_K_mean(self) -> float:
        return self.temp_K_sum / self.n_frames if self.n_frames else 0.0

    @property
    def ina226_ok_pct(self) -> float:
        return 100.0 * self.n_ina226_ok / self.n_frames if self.n_frames else 0.0

    @property
    def imu_ok_pct(self) -> float:
        return 100.0 * self.n_imu_ok / self.n_frames if self.n_frames else 0.0

    @property
    def thermal_ok_pct(self) -> float:
        return 100.0 * self.n_thermal_ok / self.n_frames if self.n_frames else 0.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop("_last_seq", None)
        d["temp_K_mean"]      = round(self.temp_K_mean, 3)
        d["ina226_ok_pct"]    = round(self.ina226_ok_pct, 2)
        d["imu_ok_pct"]       = round(self.imu_ok_pct, 2)
        d["thermal_ok_pct"]   = round(self.thermal_ok_pct, 2)
        return d


# ═══════════════════════════════════════════════════════════════════════
# Main verifier
# ═══════════════════════════════════════════════════════════════════════

def _pf(ok: bool) -> str:
    return f"{G}PASS{Z}" if ok else f"{R}FAIL{Z}"


def verify_bringup(
    port: str,
    baud: int = 921600,
    soak_minutes: float = 10.0,
    skip_soak: bool = False,
) -> dict:
    try:
        import serial
    except ImportError:
        print(f"{R}ERROR: pyserial not installed.  Run: pip install pyserial{Z}")
        sys.exit(1)

    print()
    print(f"{B}══════════════════════════════════════════════════════{Z}")
    print(f"{B} First Silicon Bring-Up Verification{Z}")
    print(f"{B}══════════════════════════════════════════════════════{Z}")
    print(f"  Port:    {port}  @ {baud} baud")
    print(f"  Soak:    {'SKIP' if skip_soak else f'{soak_minutes:.0f} min'}")
    print(f"  Time:    {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    results: dict = {
        "timestamp": datetime.datetime.now().isoformat(),
        "port": port,
        "baud": baud,
        "phases": {},
        "soak": {},
        "verdict": "UNKNOWN",
    }

    try:
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
        )
    except serial.SerialException as e:
        print(f"{R}ERROR: Cannot open {port}: {e}{Z}")
        sys.exit(1)

    print(f"  Port opened OK: {port}")

    parser = FrameParser()

    # ──────────────────────────────────────────────────────────────────
    # Phase 1: First frame within timeout
    # ──────────────────────────────────────────────────────────────────
    print()
    print(f"{B}── Phase 1: First frame within {FIRST_FRAME_TIMEOUT_S:.0f} s ──{Z}")
    t0 = time.monotonic()
    first_frame: Optional[TelemetryFrame] = None
    first_frame_latency_s: float = -1.0

    while (time.monotonic() - t0) < FIRST_FRAME_TIMEOUT_S:
        chunk = ser.read(256)
        if chunk:
            frames = parser.feed(chunk)
            if frames:
                first_frame = frames[0]
                first_frame_latency_s = time.monotonic() - t0
                break

    p1_ok = first_frame is not None
    results["phases"]["P1_first_frame"] = {
        "pass": p1_ok,
        "latency_s": round(first_frame_latency_s, 3) if p1_ok else None,
        "first_seq": first_frame.seq if p1_ok else None,
        "first_flags_hex": hex(first_frame.flags) if p1_ok else None,
    }

    if p1_ok:
        print(f"  {_pf(True)}  First frame in {first_frame_latency_s:.3f} s  "
              f"(seq={first_frame.seq}, flags=0x{first_frame.flags:04x})")
        print(f"         BOOT_OK={bool(first_frame.flags & F_BOOT_OK)}  "
              f"INA226_OK={bool(first_frame.flags & F_INA226_OK)}  "
              f"IMU_OK={bool(first_frame.flags & F_IMU_OK)}  "
              f"THERMAL_OK={bool(first_frame.flags & F_THERMAL_OK)}")
        if first_frame.flags & F_BROWNOUT:
            print(f"  {Y}WARN  BROWNOUT flag set in first frame{Z}")
        if first_frame.flags & F_WATCHDOG_RST:
            print(f"  {Y}WARN  WATCHDOG_RESET flag set in first frame{Z}")
    else:
        print(f"  {_pf(False)}  No frame within {FIRST_FRAME_TIMEOUT_S:.0f} s")
        print(f"         CRC errors so far: {parser.n_crc_errors}")
        print(f"         Sync drops:        {parser.n_sync_drops}")
        ser.close()
        results["verdict"] = "STOP — UNSAFE"
        return results

    # ──────────────────────────────────────────────────────────────────
    # Phase 2+3+4: 30-second initial window — flags, plausibility, CRC
    # ──────────────────────────────────────────────────────────────────
    print()
    print(f"{B}── Phases 2-4: {PHASE4_WINDOW_S:.0f}-second initial window ──{Z}")
    stats_p4 = SoakStats()
    stats_p4.ingest(first_frame)  # fold in the first frame already captured

    t_p4_start = time.monotonic()
    t_last_print = t_p4_start
    expected_rate_hz = 1000.0

    while (time.monotonic() - t_p4_start) < PHASE4_WINDOW_S:
        chunk = ser.read(512)
        if chunk:
            for f in parser.feed(chunk):
                stats_p4.ingest(f)

        now = time.monotonic()
        if now - t_last_print >= 5.0:
            elapsed = now - t_p4_start
            rate_hz = stats_p4.n_frames / elapsed if elapsed > 0 else 0
            print(f"  t={elapsed:4.0f}s  frames={stats_p4.n_frames:,d}  "
                  f"rate={rate_hz:.0f} Hz  "
                  f"crc={parser.n_crc_errors}  "
                  f"temp={stats_p4.temp_K_mean - 273.15:.1f}°C  "
                  f"INA={stats_p4.ina226_ok_pct:.0f}%  "
                  f"IMU={stats_p4.imu_ok_pct:.0f}%  "
                  f"THERM={stats_p4.thermal_ok_pct:.0f}%")
            t_last_print = now

    p4_elapsed = time.monotonic() - t_p4_start
    stats_p4.elapsed_s = p4_elapsed
    expected_p4 = int(expected_rate_hz * p4_elapsed)
    delivery_p4 = stats_p4.n_frames / expected_p4 if expected_p4 > 0 else 0.0

    # Phase 2: sensor flags
    p2_ina    = stats_p4.ina226_ok_pct / 100.0 >= SENSOR_UP_TARGET
    p2_imu    = stats_p4.imu_ok_pct    / 100.0 >= SENSOR_UP_TARGET
    p2_therm  = stats_p4.thermal_ok_pct/ 100.0 >= SENSOR_UP_TARGET
    p2_ok     = p2_ina and p2_imu and p2_therm

    # Phase 3: plausibility
    p3_temp   = TEMP_K_MIN <= stats_p4.temp_K_mean <= TEMP_K_MAX
    p3_nohalt = stats_p4.n_safe_halt == 0
    p3_nonan  = stats_p4.n_nan_frames == 0
    p3_ok     = p3_temp and p3_nohalt and p3_nonan

    # Phase 4: delivery + CRC
    p4_delivery = delivery_p4 >= DELIVERY_TARGET
    p4_crc      = parser.n_crc_errors == 0
    p4_ok       = p4_delivery and p4_crc

    results["phases"]["P2_sensor_flags"] = {
        "pass": p2_ok,
        "INA226_OK_pct": round(stats_p4.ina226_ok_pct, 2),
        "IMU_OK_pct":    round(stats_p4.imu_ok_pct, 2),
        "THERMAL_OK_pct": round(stats_p4.thermal_ok_pct, 2),
        "threshold_pct": SENSOR_UP_TARGET * 100,
    }
    results["phases"]["P3_plausibility"] = {
        "pass": p3_ok,
        "temp_K_min": round(stats_p4.temp_K_min, 2),
        "temp_K_max": round(stats_p4.temp_K_max, 2),
        "temp_K_mean": round(stats_p4.temp_K_mean, 2),
        "temp_C_mean": round(stats_p4.temp_K_mean - 273.15, 2),
        "current_A_min": round(stats_p4.current_A_min, 3),
        "current_A_max": round(stats_p4.current_A_max, 3),
        "vib_rms_max_g": round(stats_p4.vib_rms_max, 4),
        "quality_min": round(stats_p4.quality_min, 1),
        "n_nan_frames": stats_p4.n_nan_frames,
        "n_safe_halt":  stats_p4.n_safe_halt,
    }
    results["phases"]["P4_wire_integrity"] = {
        "pass": p4_ok,
        "elapsed_s": round(p4_elapsed, 1),
        "n_frames": stats_p4.n_frames,
        "expected_frames": expected_p4,
        "delivery_pct": round(delivery_p4 * 100.0, 2),
        "n_crc_errors": parser.n_crc_errors,
        "n_sync_drops": parser.n_sync_drops,
        "n_seq_jumps":  stats_p4.n_seq_jumps,
    }

    print()
    print(f"  Phase 2 (sensor flags): {_pf(p2_ok)}")
    print(f"    INA226_OK:   {stats_p4.ina226_ok_pct:.1f}%  {'✓' if p2_ina else '✗'}")
    print(f"    IMU_OK:      {stats_p4.imu_ok_pct:.1f}%  {'✓' if p2_imu else '✗'}")
    print(f"    THERMAL_OK:  {stats_p4.thermal_ok_pct:.1f}%  {'✓' if p2_therm else '✗'}")

    print()
    print(f"  Phase 3 (plausibility): {_pf(p3_ok)}")
    print(f"    temp_K:  mean={stats_p4.temp_K_mean:.2f} K ({stats_p4.temp_K_mean-273.15:.1f}°C)  "
          f"range=[{stats_p4.temp_K_min:.2f}, {stats_p4.temp_K_max:.2f}]")
    print(f"    current: [{stats_p4.current_A_min:.3f}, {stats_p4.current_A_max:.3f}] A")
    print(f"    vib_rms_max: {stats_p4.vib_rms_max:.4f} g")
    print(f"    quality_min: {stats_p4.quality_min:.1f}")
    if stats_p4.n_nan_frames > 0:
        print(f"    {R}NaN frames: {stats_p4.n_nan_frames}{Z}")
    if stats_p4.n_safe_halt > 0:
        print(f"    {R}SAFE_HALT frames: {stats_p4.n_safe_halt} — firmware halted!{Z}")

    print()
    print(f"  Phase 4 (wire integrity): {_pf(p4_ok)}")
    print(f"    Frames:   {stats_p4.n_frames:,d} / {expected_p4:,d} expected  "
          f"({delivery_p4*100:.2f}%)")
    print(f"    CRC errors:  {parser.n_crc_errors}")
    print(f"    Sync drops:  {parser.n_sync_drops} bytes")
    print(f"    Seq jumps:   {stats_p4.n_seq_jumps}")

    # ──────────────────────────────────────────────────────────────────
    # Phase 5: Soak
    # ──────────────────────────────────────────────────────────────────
    if skip_soak:
        print()
        print(f"  {Y}Phase 5 (soak): SKIPPED{Z}")
        results["soak"] = {"pass": None, "skipped": True}
    else:
        soak_s = soak_minutes * 60.0
        print()
        print(f"{B}── Phase 5: {soak_minutes:.0f}-minute soak ──{Z}")
        print(f"  Running {soak_s:.0f} s… (Ctrl-C to abort early)")

        stats_soak = SoakStats()
        t_soak_start = time.monotonic()
        t_last_print = t_soak_start
        print_interval = 60.0   # print status every minute

        try:
            while (time.monotonic() - t_soak_start) < soak_s:
                chunk = ser.read(512)
                if chunk:
                    for f in parser.feed(chunk):
                        stats_soak.ingest(f)

                now = time.monotonic()
                if now - t_last_print >= print_interval:
                    elapsed = now - t_soak_start
                    remaining = soak_s - elapsed
                    rate_hz = stats_soak.n_frames / elapsed if elapsed > 0 else 0
                    print(f"  t={elapsed/60:.1f}min  frames={stats_soak.n_frames:,d}  "
                          f"rate={rate_hz:.0f} Hz  "
                          f"crc={parser.n_crc_errors}  "
                          f"temp={stats_soak.temp_K_mean-273.15:.1f}°C  "
                          f"INA={stats_soak.ina226_ok_pct:.0f}%  "
                          f"IMU={stats_soak.imu_ok_pct:.0f}%  "
                          f"THERM={stats_soak.thermal_ok_pct:.0f}%  "
                          f"remaining={remaining/60:.1f}min")
                    t_last_print = now

        except KeyboardInterrupt:
            print(f"\n  {Y}Soak interrupted by user{Z}")

        soak_elapsed = time.monotonic() - t_soak_start
        stats_soak.elapsed_s = soak_elapsed
        expected_soak = int(expected_rate_hz * soak_elapsed)
        delivery_soak = stats_soak.n_frames / expected_soak if expected_soak > 0 else 0.0
        # Use cumulative CRC count from the whole session
        soak_crc = parser.n_crc_errors - results["phases"]["P4_wire_integrity"]["n_crc_errors"]

        p5_delivery = delivery_soak >= DELIVERY_TARGET
        p5_crc      = soak_crc == 0
        p5_ina      = stats_soak.ina226_ok_pct / 100.0 >= SENSOR_UP_TARGET
        p5_imu      = stats_soak.imu_ok_pct    / 100.0 >= SENSOR_UP_TARGET
        p5_therm    = stats_soak.thermal_ok_pct/ 100.0 >= SENSOR_UP_TARGET
        p5_halt     = stats_soak.n_safe_halt == 0
        p5_nonan    = stats_soak.n_nan_frames == 0
        p5_ok       = (p5_delivery and p5_crc and p5_ina and p5_imu
                       and p5_therm and p5_halt and p5_nonan)

        results["soak"] = {
            "pass": p5_ok,
            "elapsed_s": round(soak_elapsed, 1),
            "n_frames": stats_soak.n_frames,
            "expected_frames": expected_soak,
            "delivery_pct": round(delivery_soak * 100.0, 2),
            "n_crc_errors": soak_crc,
            "n_sync_drops": parser.n_sync_drops,
            "n_seq_jumps": stats_soak.n_seq_jumps,
            "n_nan_frames": stats_soak.n_nan_frames,
            "n_safe_halt":  stats_soak.n_safe_halt,
            "n_brownout":   stats_soak.n_brownout,
            "n_watchdog_rst": stats_soak.n_watchdog_rst,
            "n_thermal_shut": stats_soak.n_thermal_shut,
            **stats_soak.as_dict(),
        }

        print()
        print(f"  Phase 5 soak ({soak_elapsed/60:.1f} min): {_pf(p5_ok)}")
        print(f"    Frames:   {stats_soak.n_frames:,d} / {expected_soak:,d}  ({delivery_soak*100:.2f}%)")
        print(f"    CRC errors (soak):   {soak_crc}")
        print(f"    INA226_OK: {stats_soak.ina226_ok_pct:.2f}%  "
              f"IMU_OK: {stats_soak.imu_ok_pct:.2f}%  "
              f"THERMAL_OK: {stats_soak.thermal_ok_pct:.2f}%")
        print(f"    temp_K range: [{stats_soak.temp_K_min:.2f}, {stats_soak.temp_K_max:.2f}]  "
              f"mean={stats_soak.temp_K_mean:.2f} K")
        if stats_soak.n_safe_halt > 0:
            print(f"    {R}SAFE_HALT events: {stats_soak.n_safe_halt}{Z}")
        if stats_soak.n_thermal_shut > 0:
            print(f"    {R}THERMAL_SHUTDOWN events: {stats_soak.n_thermal_shut}{Z}")
        if stats_soak.n_watchdog_rst > 0:
            print(f"    {R}WATCHDOG_RESET events: {stats_soak.n_watchdog_rst}{Z}")

    # ──────────────────────────────────────────────────────────────────
    # Overall verdict
    # ──────────────────────────────────────────────────────────────────
    all_phases_pass = p1_ok and p2_ok and p3_ok and p4_ok
    soak_pass = (results["soak"].get("pass") is True) or skip_soak
    overall_pass = all_phases_pass and soak_pass

    results["verdict"] = "★★★  READY  ★★★" if overall_pass else "STOP — UNSAFE"
    results["overall_pass"] = overall_pass

    print()
    print(f"{B}══════════════════════════════════════════════════════{Z}")
    print(f"{B} VERDICT{Z}")
    print(f"{B}══════════════════════════════════════════════════════{Z}")
    print(f"  P1 first frame:   {_pf(p1_ok)}")
    print(f"  P2 sensor flags:  {_pf(p2_ok)}")
    print(f"  P3 plausibility:  {_pf(p3_ok)}")
    print(f"  P4 wire integrity:{_pf(p4_ok)}")
    if not skip_soak:
        p5_res = results["soak"].get("pass", False)
        print(f"  P5 soak:          {_pf(p5_res)}")
    print()
    if overall_pass:
        print(f"  {G}{B}★★★  READY FOR SILICON SOAK TESTING  ★★★{Z}")
    else:
        print(f"  {R}{B}STOP — UNSAFE — review FAIL items above{Z}")
    print()

    ser.close()
    return results


# ═══════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(
        description="First silicon bring-up verification harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python verify_bringup.py --port /dev/ttyUSB1 --soak-minutes 10
  python verify_bringup.py --port /dev/ttyUSB1 --skip-soak
  python verify_bringup.py --port COM3 --soak-minutes 1
        """,
    )
    ap.add_argument("--port", required=True,
                    help="Serial port for UART1 telemetry (921600 baud)")
    ap.add_argument("--baud", type=int, default=921600,
                    help="Baud rate (default: 921600)")
    ap.add_argument("--soak-minutes", type=float, default=10.0,
                    help="Soak duration in minutes (default: 10)")
    ap.add_argument("--skip-soak", action="store_true",
                    help="Skip Phase 5 soak (for quick smoke tests)")
    args = ap.parse_args()

    results = verify_bringup(
        port=args.port,
        baud=args.baud,
        soak_minutes=args.soak_minutes,
        skip_soak=args.skip_soak,
    )

    # Write JSON
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(out_dir, f"bringup_results_{ts}.json")
    with open(json_path, "w") as fh:
        json.dump(results, fh, indent=2, default=str)
    print(f"  Results written: {json_path}")
    print()

    sys.exit(0 if results.get("overall_pass") else 1)


if __name__ == "__main__":
    main()
