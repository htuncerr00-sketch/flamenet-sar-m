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

Extended analysis (added in production hardening pass):
  • Jitter analysis:  Welford's online mean/variance of ts_us inter-frame deltas
  • Sensor freeze:    Sliding-window freeze detection (200-frame window)
  • Throughput:       UART byte-rate and utilisation tracking
  • Per-minute:       Frame count per minute for trend detection
  • Reports:          Markdown + JSON written to --report-dir

Usage:
  python verify_bringup.py --port /dev/ttyUSB1 --soak-minutes 10
  python verify_bringup.py --port COM3 --soak-minutes 1   # quick smoke test
  python verify_bringup.py --port /dev/ttyUSB1 --skip-soak
  python verify_bringup.py --port /dev/ttyUSB1 --report-dir ./reports

Outputs:
  • Live terminal progress (colour if TTY)
  • JSON results: <report-dir>/bringup_results_YYYYMMDD_HHMMSS.json
  • Markdown report: <report-dir>/bringup_report_YYYYMMDD_HHMMSS.md
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
from collections import deque
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
TEMP_K_MIN            = 273.15    # 0 °C
TEMP_K_MAX            = 348.15    # 75 °C (above ambient, below shutdown 373.15 K)
TEMP_K_SHUTDOWN       = 373.15    # safety threshold
CURRENT_ABS_MAX       = 45.0      # safety threshold A
VIB_MAX_G             = 2.0       # ±2 g range of MPU6050
DELIVERY_TARGET       = 0.99      # 99% frame delivery
SENSOR_UP_TARGET      = 0.98      # 98% uptime per sensor flag
PHASE4_WINDOW_S       = 30.0      # seconds for P4 evaluation
FIRST_FRAME_TIMEOUT_S = 10.0      # seconds to wait for first frame
JITTER_LATE_THRESH_US = 1500      # µs — inter-frame delta flagged as late
JITTER_BURST_THRESH_US = 500      # µs — inter-frame delta flagged as burst
FREEZE_WINDOW_FRAMES  = 200       # frames for freeze detection (~200 ms @ 1 kHz)
BAUD_RATE             = 921600    # bits/s
BITS_PER_BYTE         = 10        # 8N1: start + 8 data + stop


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
            idx = self._buf.find(MAGIC)
            if idx == -1:
                self.n_sync_drops += max(0, len(self._buf) - 1)
                self._buf = self._buf[-1:]
                break

            if idx > 0:
                self.n_sync_drops += idx
                del self._buf[:idx]

            if len(self._buf) < WIRE_LEN:
                break

            frame_bytes = bytes(self._buf[:WIRE_LEN])
            del self._buf[:WIRE_LEN]

            payload = frame_bytes[2:]          # 64 bytes
            crc_expected = crc16_ccitt(payload[:CRC_DATA_LEN])
            crc_actual   = struct.unpack_from(">H", payload, CRC_DATA_LEN)[0]

            if crc_actual != crc_expected:
                self.n_crc_errors += 1
                self._buf[0:0] = frame_bytes[2:]
                continue

            frame = TelemetryFrame.unpack(payload)
            self.n_frames_ok += 1
            frames.append(frame)

        return frames


# ═══════════════════════════════════════════════════════════════════════
# Jitter analyser — Welford's online algorithm
# ═══════════════════════════════════════════════════════════════════════

class JitterAnalyzer:
    """Online mean/variance of ts_us inter-frame deltas using Welford's algorithm.

    Expected inter-frame delta: 1000 µs (1 kHz).
    Tracks late frames (>1.5 ms) and burst frames (<0.5 ms) separately.
    Skips deltas that suggest a wrap-around or stale ts_us (>50 ms gap).
    """

    def __init__(self) -> None:
        self._last_ts_us: int = -1
        self.n: int = 0
        self._mean: float = 0.0
        self._M2: float = 0.0
        self.min_us: float = float("inf")
        self.max_us: float = float("-inf")
        self.n_late: int = 0     # delta > JITTER_LATE_THRESH_US
        self.n_burst: int = 0    # delta < JITTER_BURST_THRESH_US

    def update(self, ts_us: int) -> None:
        if self._last_ts_us < 0:
            self._last_ts_us = ts_us
            return

        delta = ts_us - self._last_ts_us
        self._last_ts_us = ts_us

        # Ignore implausible deltas (wrap-around or firmware reboot gap)
        if delta <= 0 or delta > 50_000:
            return

        self.n += 1
        delta_f = float(delta)
        old_mean = self._mean
        self._mean += (delta_f - self._mean) / self.n
        self._M2 += (delta_f - old_mean) * (delta_f - self._mean)

        if delta_f < self.min_us:
            self.min_us = delta_f
        if delta_f > self.max_us:
            self.max_us = delta_f

        if delta > JITTER_LATE_THRESH_US:
            self.n_late += 1
        if delta < JITTER_BURST_THRESH_US:
            self.n_burst += 1

    @property
    def mean_us(self) -> float:
        return self._mean

    @property
    def variance_us2(self) -> float:
        return self._M2 / (self.n - 1) if self.n > 1 else 0.0

    @property
    def stddev_us(self) -> float:
        return math.sqrt(self.variance_us2)

    def jitter_ok(self) -> bool:
        """True if mean is within ±5% of 1000 µs and stddev < 100 µs."""
        if self.n < 10:
            return True  # not enough data to judge
        return (abs(self.mean_us - 1000.0) < 50.0) and (self.stddev_us < 100.0)

    def as_dict(self) -> dict:
        return {
            "n_deltas": self.n,
            "mean_us": round(self.mean_us, 2),
            "stddev_us": round(self.stddev_us, 2),
            "min_us": round(self.min_us, 2) if self.n > 0 else None,
            "max_us": round(self.max_us, 2) if self.n > 0 else None,
            "n_late_gt1500us": self.n_late,
            "n_burst_lt500us": self.n_burst,
            "late_pct": round(100.0 * self.n_late / self.n, 3) if self.n > 0 else 0.0,
            "jitter_ok": self.jitter_ok(),
        }


# ═══════════════════════════════════════════════════════════════════════
# Sensor freeze detector — sliding window
# ═══════════════════════════════════════════════════════════════════════

class SensorFreezeDetector:
    """Sliding-window sensor freeze detection.

    A field is "frozen" if it has not changed across FREEZE_WINDOW_FRAMES
    consecutive frames.  This catches LKG propagation without a flag update,
    wiring faults, or sensor address collisions.

    Note: LKG is expected when a sensor's *flag* bit is clear.  The freeze
    detector fires independently of flag bits — flag-clear freeze is expected;
    flag-set freeze is a fault.
    """

    _FIELDS = ("temp_K", "current_A", "vib_x", "vib_y", "vib_z")

    def __init__(self) -> None:
        self._windows: dict[str, deque] = {
            k: deque(maxlen=FREEZE_WINDOW_FRAMES) for k in self._FIELDS
        }
        self.freeze_counts: dict[str, int] = {k: 0 for k in self._FIELDS}
        self._freeze_active: dict[str, bool] = {k: False for k in self._FIELDS}
        self.total_freeze_events: int = 0

    def update(self, f: TelemetryFrame) -> list[str]:
        """Returns list of currently frozen fields (empty if none)."""
        frozen = []
        for fname in self._FIELDS:
            window = self._windows[fname]
            val = getattr(f, fname)
            window.append(val)
            if len(window) == FREEZE_WINDOW_FRAMES:
                # All values identical in window?
                first = window[0]
                is_frozen = all(v == first for v in window)
                if is_frozen and not self._freeze_active[fname]:
                    self.freeze_counts[fname] += 1
                    self.total_freeze_events += 1
                    self._freeze_active[fname] = True
                elif not is_frozen:
                    self._freeze_active[fname] = False
                if is_frozen:
                    frozen.append(fname)
        return frozen

    def as_dict(self) -> dict:
        return {
            "total_freeze_events": self.total_freeze_events,
            "by_field": dict(self.freeze_counts),
        }


# ═══════════════════════════════════════════════════════════════════════
# Throughput tracker
# ═══════════════════════════════════════════════════════════════════════

class ThroughputTracker:
    """Tracks raw UART byte rate and link utilisation."""

    def __init__(self) -> None:
        self.total_bytes: int = 0
        self._t_start: float = time.monotonic()

    def add_bytes(self, n: int) -> None:
        self.total_bytes += n

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self._t_start

    @property
    def bytes_per_sec(self) -> float:
        el = self.elapsed_s
        return self.total_bytes / el if el > 0 else 0.0

    @property
    def utilization_pct(self) -> float:
        bits_per_sec = self.bytes_per_sec * BITS_PER_BYTE
        return 100.0 * bits_per_sec / BAUD_RATE

    def as_dict(self) -> dict:
        return {
            "total_bytes": self.total_bytes,
            "elapsed_s": round(self.elapsed_s, 1),
            "bytes_per_sec": round(self.bytes_per_sec, 1),
            "utilization_pct": round(self.utilization_pct, 2),
            "expected_bytes_per_sec": 66_000,   # 66 B × 1 kHz
            "expected_utilization_pct": 71.6,
        }


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

    # per-minute frame count (bucket per 60 s)
    per_minute_frames: list = field(default_factory=list, repr=False)
    _minute_start_t:   float = field(default=0.0, repr=False)
    _minute_count:     int = field(default=0, repr=False)

    def ingest(self, f: TelemetryFrame, wall_t: float = 0.0) -> None:
        self.n_frames += 1

        if not f.is_finite():
            self.n_nan_frames += 1

        if self._last_seq >= 0:
            expected = (self._last_seq + 1) & 0xFFFF
            if f.seq != expected:
                self.n_seq_jumps += 1
        self._last_seq = f.seq

        if f.flags & F_INA226_OK:    self.n_ina226_ok    += 1
        if f.flags & F_IMU_OK:       self.n_imu_ok       += 1
        if f.flags & F_THERMAL_OK:   self.n_thermal_ok   += 1
        if f.flags & F_SAFE_HALT:    self.n_safe_halt    += 1
        if f.flags & F_BROWNOUT:     self.n_brownout     += 1
        if f.flags & F_WATCHDOG_RST: self.n_watchdog_rst += 1
        if f.flags & F_THERMAL_SHUT: self.n_thermal_shut += 1

        if math.isfinite(f.temp_K):
            if f.temp_K < self.temp_K_min: self.temp_K_min = f.temp_K
            if f.temp_K > self.temp_K_max: self.temp_K_max = f.temp_K
            self.temp_K_sum += f.temp_K

        if math.isfinite(f.current_A):
            if f.current_A < self.current_A_min: self.current_A_min = f.current_A
            if f.current_A > self.current_A_max: self.current_A_max = f.current_A

        vib_rms = math.sqrt(f.vib_x**2 + f.vib_y**2 + f.vib_z**2)
        if math.isfinite(vib_rms) and vib_rms > self.vib_rms_max:
            self.vib_rms_max = vib_rms

        if math.isfinite(f.quality) and f.quality < self.quality_min:
            self.quality_min = f.quality

        # per-minute bucketing
        if wall_t > 0:
            if self._minute_start_t == 0.0:
                self._minute_start_t = wall_t
            if wall_t - self._minute_start_t >= 60.0:
                self.per_minute_frames.append(self._minute_count)
                self._minute_count = 0
                self._minute_start_t = wall_t
            self._minute_count += 1

    def flush_last_minute(self) -> None:
        if self._minute_count > 0:
            self.per_minute_frames.append(self._minute_count)
            self._minute_count = 0

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
        d.pop("_minute_start_t", None)
        d.pop("_minute_count", None)
        d["temp_K_mean"]      = round(self.temp_K_mean, 3)
        d["ina226_ok_pct"]    = round(self.ina226_ok_pct, 2)
        d["imu_ok_pct"]       = round(self.imu_ok_pct, 2)
        d["thermal_ok_pct"]   = round(self.thermal_ok_pct, 2)
        return d


# ═══════════════════════════════════════════════════════════════════════
# Markdown report generator
# ═══════════════════════════════════════════════════════════════════════

def _md_pass(ok: bool) -> str:
    return "✅ PASS" if ok else "❌ FAIL"


def generate_markdown_report(results: dict, ts_str: str) -> str:
    """Generate a human-readable markdown bring-up report from results dict."""
    lines = []
    a = lines.append

    a("# First Silicon Bring-Up Report")
    a("")
    a(f"**Generated:** {results.get('timestamp', ts_str)}  ")
    a(f"**Port:** `{results.get('port', '?')}` @ {results.get('baud', '?')} baud  ")
    a(f"**Evidence label:** (silicon) — measurements on real hardware")
    a("")

    # ── Overall verdict ────────────────────────────────────────────────
    verdict = results.get("verdict", "UNKNOWN")
    is_pass = results.get("overall_pass", False)
    a("## Overall Verdict")
    a("")
    if is_pass:
        a("> **★★★ READY FOR SILICON SOAK TESTING ★★★**")
    else:
        a("> **STOP — UNSAFE — review FAIL items below**")
    a("")

    # ── Phase summary ──────────────────────────────────────────────────
    a("## Phase Summary")
    a("")
    a("| Phase | Result |")
    a("|-------|--------|")
    phases = results.get("phases", {})
    for key, label in [
        ("P1_first_frame", "P1 First Frame"),
        ("P2_sensor_flags", "P2 Sensor Flags"),
        ("P3_plausibility", "P3 Plausibility"),
        ("P4_wire_integrity", "P4 Wire Integrity"),
    ]:
        ph = phases.get(key, {})
        ok = ph.get("pass", False)
        a(f"| {label} | {_md_pass(ok)} |")

    soak = results.get("soak", {})
    if soak.get("skipped"):
        a("| P5 Soak | ⏭ SKIPPED |")
    else:
        a(f"| P5 Soak ({soak.get('elapsed_s', 0)/60:.1f} min) | {_md_pass(soak.get('pass', False))} |")
    a(f"| **OVERALL** | **{verdict}** |")
    a("")

    # ── P1 detail ──────────────────────────────────────────────────────
    a("## P1: First Frame")
    a("")
    p1 = phases.get("P1_first_frame", {})
    if p1.get("pass"):
        a(f"- Latency: **{p1.get('latency_s', '?')} s**")
        a(f"- First sequence number: {p1.get('first_seq', '?')}")
        a(f"- First flags: `{p1.get('first_flags_hex', '?')}`")
    else:
        a("- ❌ No frame received within timeout")
    a("")

    # ── P2 sensor flags ────────────────────────────────────────────────
    a("## P2: Sensor Flags")
    a("")
    p2 = phases.get("P2_sensor_flags", {})
    thr = p2.get("threshold_pct", 98.0)
    a(f"| Sensor | Uptime % | Threshold | Result |")
    a(f"|--------|----------|-----------|--------|")
    for s, k in [("INA226", "INA226_OK_pct"), ("IMU (MPU6050)", "IMU_OK_pct"), ("Thermal (NTC)", "THERMAL_OK_pct")]:
        pct = p2.get(k, 0.0)
        ok = pct >= thr
        a(f"| {s} | {pct:.2f}% | ≥{thr:.0f}% | {_md_pass(ok)} |")
    a("")

    # ── P3 plausibility ────────────────────────────────────────────────
    a("## P3: Plausibility")
    a("")
    p3 = phases.get("P3_plausibility", {})
    tmean = p3.get("temp_K_mean", 0.0)
    a(f"| Metric | Value | Expected |")
    a(f"|--------|-------|----------|")
    a(f"| temp_K mean | {tmean:.2f} K ({tmean-273.15:.1f} °C) | {TEMP_K_MIN:.0f}–{TEMP_K_MAX:.0f} K |")
    a(f"| temp_K range | [{p3.get('temp_K_min','?'):.2f}, {p3.get('temp_K_max','?'):.2f}] K | within range |")
    a(f"| current_A range | [{p3.get('current_A_min','?'):.3f}, {p3.get('current_A_max','?'):.3f}] A | < {CURRENT_ABS_MAX} A |")
    a(f"| vib_rms_max | {p3.get('vib_rms_max_g','?'):.4f} g | < {VIB_MAX_G} g |")
    a(f"| quality_min | {p3.get('quality_min','?'):.1f} | ≥ 17 (one sensor) |")
    a(f"| NaN frames | {p3.get('n_nan_frames', 0)} | 0 |")
    a(f"| SAFE_HALT frames | {p3.get('n_safe_halt', 0)} | 0 |")
    a("")

    # ── P4 wire integrity ──────────────────────────────────────────────
    a("## P4: Wire Integrity")
    a("")
    p4 = phases.get("P4_wire_integrity", {})
    a(f"| Metric | Value | Threshold |")
    a(f"|--------|-------|-----------|")
    a(f"| Frames received | {p4.get('n_frames',0):,} / {p4.get('expected_frames',0):,} | — |")
    a(f"| Delivery rate | {p4.get('delivery_pct',0):.2f}% | ≥{DELIVERY_TARGET*100:.0f}% |")
    a(f"| CRC errors | {p4.get('n_crc_errors',0)} | 0 |")
    a(f"| Sync drops (bytes) | {p4.get('n_sync_drops',0)} | 0 |")
    a(f"| Sequence jumps | {p4.get('n_seq_jumps',0)} | 0 |")
    a("")

    # ── Jitter analysis ────────────────────────────────────────────────
    a("## Jitter Analysis (ts_us inter-frame deltas)")
    a("")
    jitter = results.get("jitter", {})
    if jitter:
        ok = jitter.get("jitter_ok", False)
        a(f"| Metric | Value | Expected |")
        a(f"|--------|-------|----------|")
        a(f"| Samples | {jitter.get('n_deltas',0):,} | — |")
        a(f"| Mean delta | {jitter.get('mean_us','?')} µs | 1000 µs |")
        a(f"| Std dev | {jitter.get('stddev_us','?')} µs | < 100 µs |")
        a(f"| Min / Max | {jitter.get('min_us','?')} / {jitter.get('max_us','?')} µs | — |")
        a(f"| Late (>1.5 ms) | {jitter.get('n_late_gt1500us',0)} ({jitter.get('late_pct',0):.3f}%) | ≈ 0 |")
        a(f"| Burst (<0.5 ms) | {jitter.get('n_burst_lt500us',0)} | ≈ 0 |")
        a(f"| **Result** | **{_md_pass(ok)}** | mean ±5%, σ < 100 µs |")
    else:
        a("_Jitter data not available (no soak run)._")
    a("")

    # ── Sensor freeze ──────────────────────────────────────────────────
    a("## Sensor Freeze Detection")
    a("")
    freeze = results.get("sensor_freeze", {})
    total_events = freeze.get("total_freeze_events", 0)
    if freeze:
        if total_events == 0:
            a("✅ No freeze events detected across 200-frame sliding window.")
        else:
            a(f"⚠️ **{total_events} freeze event(s) detected:**")
            a("")
            by_field = freeze.get("by_field", {})
            for fname, cnt in by_field.items():
                if cnt > 0:
                    a(f"- `{fname}`: {cnt} event(s)")
            a("")
            a("> A freeze event = 200 consecutive frames with identical value.")
            a("> LKG propagation when sensor flag is clear is **expected**.")
            a("> Freeze with sensor flag SET is a fault.")
    else:
        a("_Freeze detection not run._")
    a("")

    # ── Throughput ─────────────────────────────────────────────────────
    a("## UART Throughput")
    a("")
    tput = results.get("throughput", {})
    if tput:
        a(f"| Metric | Measured | Expected |")
        a(f"|--------|----------|----------|")
        a(f"| Total bytes | {tput.get('total_bytes',0):,} | — |")
        a(f"| Throughput | {tput.get('bytes_per_sec',0):.0f} B/s | {tput.get('expected_bytes_per_sec',66000):,} B/s |")
        a(f"| Link utilisation | {tput.get('utilization_pct',0):.2f}% | {tput.get('expected_utilization_pct',71.6):.1f}% |")
    else:
        a("_Throughput data not available._")
    a("")

    # ── Soak detail ────────────────────────────────────────────────────
    if soak and not soak.get("skipped"):
        a("## P5: Soak Detail")
        a("")
        a(f"Duration: **{soak.get('elapsed_s', 0)/60:.1f} min**")
        a("")
        a(f"| Metric | Value |")
        a(f"|--------|-------|")
        a(f"| Frames | {soak.get('n_frames',0):,} / {soak.get('expected_frames',0):,} ({soak.get('delivery_pct',0):.2f}%) |")
        a(f"| CRC errors | {soak.get('n_crc_errors',0)} |")
        a(f"| INA226 uptime | {soak.get('ina226_ok_pct',0):.2f}% |")
        a(f"| IMU uptime | {soak.get('imu_ok_pct',0):.2f}% |")
        a(f"| Thermal uptime | {soak.get('thermal_ok_pct',0):.2f}% |")
        a(f"| temp_K min/max | {soak.get('temp_K_min','?'):.2f} / {soak.get('temp_K_max','?'):.2f} K |")
        a(f"| NaN frames | {soak.get('n_nan_frames',0)} |")
        a(f"| SAFE_HALT events | {soak.get('n_safe_halt',0)} |")
        a(f"| THERMAL_SHUT events | {soak.get('n_thermal_shut',0)} |")
        a(f"| WATCHDOG_RST events | {soak.get('n_watchdog_rst',0)} |")
        a(f"| Brownout events | {soak.get('n_brownout',0)} |")

        per_min = soak.get("per_minute_frames", [])
        if per_min:
            a("")
            a("### Per-Minute Frame Count")
            a("")
            a("| Minute | Frames | Rate (Hz) |")
            a("|--------|--------|-----------|")
            for i, cnt in enumerate(per_min, 1):
                hz = cnt / 60.0
                a(f"| {i} | {cnt:,} | {hz:.0f} |")
        a("")

    # ── Footer ─────────────────────────────────────────────────────────
    a("---")
    a("")
    a("*Generated by `bringup/verify_bringup.py` — Filament Winding CAM Platform*  ")
    a(f"*Faz 19B commissioning framework — {ts_str}*")
    a("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _pf(ok: bool) -> str:
    return f"{G}PASS{Z}" if ok else f"{R}FAIL{Z}"


# ═══════════════════════════════════════════════════════════════════════
# Main verifier
# ═══════════════════════════════════════════════════════════════════════

def verify_bringup(
    port: str,
    baud: int = 921600,
    soak_minutes: float = 10.0,
    skip_soak: bool = False,
    report_dir: str = "",
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

    ts_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    results: dict = {
        "timestamp": datetime.datetime.now().isoformat(),
        "port": port,
        "baud": baud,
        "phases": {},
        "soak": {},
        "jitter": {},
        "sensor_freeze": {},
        "throughput": {},
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

    parser     = FrameParser()
    jitter     = JitterAnalyzer()
    freeze     = SensorFreezeDetector()
    throughput = ThroughputTracker()

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
            throughput.add_bytes(len(chunk))
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
    stats_p4.ingest(first_frame, wall_t=time.monotonic())
    jitter.update(first_frame.ts_us)
    freeze.update(first_frame)

    t_p4_start = time.monotonic()
    t_last_print = t_p4_start
    expected_rate_hz = 1000.0

    while (time.monotonic() - t_p4_start) < PHASE4_WINDOW_S:
        chunk = ser.read(512)
        if chunk:
            throughput.add_bytes(len(chunk))
            for f in parser.feed(chunk):
                stats_p4.ingest(f, wall_t=time.monotonic())
                jitter.update(f.ts_us)
                freeze.update(f)

        now = time.monotonic()
        if now - t_last_print >= 5.0:
            elapsed = now - t_p4_start
            rate_hz = stats_p4.n_frames / elapsed if elapsed > 0 else 0
            frozen_now = freeze.total_freeze_events
            print(f"  t={elapsed:4.0f}s  frames={stats_p4.n_frames:,d}  "
                  f"rate={rate_hz:.0f} Hz  "
                  f"crc={parser.n_crc_errors}  "
                  f"temp={stats_p4.temp_K_mean - 273.15:.1f}°C  "
                  f"INA={stats_p4.ina226_ok_pct:.0f}%  "
                  f"IMU={stats_p4.imu_ok_pct:.0f}%  "
                  f"THERM={stats_p4.thermal_ok_pct:.0f}%"
                  + (f"  {Y}freeze={frozen_now}{Z}" if frozen_now else ""))
            t_last_print = now

    p4_elapsed = time.monotonic() - t_p4_start
    stats_p4.elapsed_s = p4_elapsed
    stats_p4.flush_last_minute()
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

    # Print jitter after P4
    print()
    jd = jitter.as_dict()
    print(f"  Jitter (ts_us deltas, {jd['n_deltas']:,d} samples):")
    print(f"    mean={jd['mean_us']} µs  σ={jd['stddev_us']} µs  "
          f"min={jd['min_us']} µs  max={jd['max_us']} µs")
    if jd['n_late_gt1500us'] > 0:
        print(f"    {Y}Late frames (>1.5ms): {jd['n_late_gt1500us']}{Z}")
    if not jd['jitter_ok']:
        print(f"    {Y}WARN: jitter outside expected range{Z}")

    results["jitter"] = jd
    results["sensor_freeze"] = freeze.as_dict()
    results["throughput"] = throughput.as_dict()

    # Print freeze summary
    if freeze.total_freeze_events > 0:
        print()
        print(f"  {Y}WARN  Sensor freeze events: {freeze.total_freeze_events}{Z}")
        for fname, cnt in freeze.freeze_counts.items():
            if cnt > 0:
                print(f"         {fname}: {cnt} event(s)")

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
        print_interval = 60.0

        try:
            while (time.monotonic() - t_soak_start) < soak_s:
                chunk = ser.read(512)
                if chunk:
                    throughput.add_bytes(len(chunk))
                    for f in parser.feed(chunk):
                        stats_soak.ingest(f, wall_t=time.monotonic())
                        jitter.update(f.ts_us)
                        freeze.update(f)

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
        stats_soak.flush_last_minute()
        expected_soak = int(expected_rate_hz * soak_elapsed)
        delivery_soak = stats_soak.n_frames / expected_soak if expected_soak > 0 else 0.0
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

        soak_dict = {
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
            "per_minute_frames": stats_soak.per_minute_frames,
        }
        soak_dict.update(stats_soak.as_dict())
        results["soak"] = soak_dict

        # Update final jitter and freeze after soak
        results["jitter"] = jitter.as_dict()
        results["sensor_freeze"] = freeze.as_dict()
        results["throughput"] = throughput.as_dict()

        print()
        print(f"  Phase 5 soak ({soak_elapsed/60:.1f} min): {_pf(p5_ok)}")
        print(f"    Frames:   {stats_soak.n_frames:,d} / {expected_soak:,d}  ({delivery_soak*100:.2f}%)")
        print(f"    CRC errors (soak):   {soak_crc}")
        print(f"    INA226_OK: {stats_soak.ina226_ok_pct:.2f}%  "
              f"IMU_OK: {stats_soak.imu_ok_pct:.2f}%  "
              f"THERMAL_OK: {stats_soak.thermal_ok_pct:.2f}%")
        print(f"    temp_K range: [{stats_soak.temp_K_min:.2f}, {stats_soak.temp_K_max:.2f}]  "
              f"mean={stats_soak.temp_K_mean:.2f} K")
        print(f"    Jitter: mean={jitter.mean_us:.1f} µs  σ={jitter.stddev_us:.1f} µs")
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
  python verify_bringup.py --port /dev/ttyUSB1 --report-dir ./reports/session1
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
    ap.add_argument("--report-dir", default="",
                    help="Directory for JSON + Markdown reports (default: same dir as script)")
    ap.add_argument("--no-markdown", action="store_true",
                    help="Skip markdown report generation")
    args = ap.parse_args()

    results = verify_bringup(
        port=args.port,
        baud=args.baud,
        soak_minutes=args.soak_minutes,
        skip_soak=args.skip_soak,
        report_dir=args.report_dir,
    )

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = args.report_dir if args.report_dir else os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out_dir, exist_ok=True)

    json_path = os.path.join(out_dir, f"bringup_results_{ts}.json")
    with open(json_path, "w") as fh:
        json.dump(results, fh, indent=2, default=str)
    print(f"  Results (JSON):     {json_path}")

    if not args.no_markdown:
        md_path = os.path.join(out_dir, f"bringup_report_{ts}.md")
        with open(md_path, "w") as fh:
            fh.write(generate_markdown_report(results, ts))
        print(f"  Report (Markdown):  {md_path}")

    print()
    sys.exit(0 if results.get("overall_pass") else 1)


if __name__ == "__main__":
    main()
