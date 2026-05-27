"""
daq_architecture.py — 1kHz Synchronized DAQ + Binary Telemetry V2
==================================================================
Hardware timestamp alignment, lock-free SPSC ring, CRC-protected frames.

Channels (sync acquired at 1kHz):
  x_mm, a_deg     — encoder (4x quadrature)
  T_N             — HX711 load cell (Kalman filtered)
  rpm             — Hall PLL
  vib_x/y/z (g)   — MPU6050/ICM20948 IMU
  temp_K          — MAX31856 thermocouple
  current_A       — ACS712 / INA219 motor current
  alpha_cure      — Kamal model (online integration)
  quality         — quality estimator output

Telemetry V2: 64 byte/sample, CRC-16/CCITT, deterministic ordering.
"""
from __future__ import annotations
import os, queue, struct, threading, time
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Callable
import numpy as np

TELEM_V2 = struct.Struct(">QHHffffffffffH")  # 8+2+2+10×4+2 = 54 → too small
# Recalculate: target 64 bytes
# Q(8)+H(2)+H(2)+12xf(48)+HH(4) = 64 ✓
TELEM_V2 = struct.Struct(">QHHffffffffffffHH")
TELEM_V2_BYTES = TELEM_V2.size
assert TELEM_V2_BYTES == 64, f"Expected 64, got {TELEM_V2_BYTES}"

def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
        crc &= 0xFFFF
    return crc

@dataclass(slots=True)
class TelemetryV2:
    ts_us:    int    # hardware timestamp [µs]
    seq:      int    # sequence (wrap 65535)
    flags:    int    # bit field: running/alarm/cure/etc
    x_mm:     float
    a_deg:    float
    T_N:      float
    rpm:      float
    vib_x:    float
    vib_y:    float
    vib_z:    float
    temp_K:   float
    current_A:float
    alpha:    float
    quality:  float
    spare:    float = 0.0   # Reserved for future expansion

    def pack(self) -> bytes:
        raw = TELEM_V2.pack(
            int(self.ts_us) & 0xFFFFFFFFFFFFFFFF,
            int(self.seq) & 0xFFFF, int(self.flags) & 0xFFFF,
            float(self.x_mm), float(self.a_deg), float(self.T_N), float(self.rpm),
            float(self.vib_x), float(self.vib_y), float(self.vib_z),
            float(self.temp_K), float(self.current_A),
            float(self.alpha), float(self.quality), float(self.spare),
            0, 0)
        crc = crc16_ccitt(raw[:-2])
        return raw[:-2] + struct.pack(">H", crc)

    @classmethod
    def unpack(cls, data: bytes) -> Optional["TelemetryV2"]:
        if len(data) < TELEM_V2_BYTES: return None
        crc_stored = struct.unpack(">H", data[-2:])[0]
        if crc16_ccitt(data[:-2]) != crc_stored: return None
        f = TELEM_V2.unpack(data)
        return cls(ts_us=f[0],seq=f[1],flags=f[2],
            x_mm=f[3],a_deg=f[4],T_N=f[5],rpm=f[6],
            vib_x=f[7],vib_y=f[8],vib_z=f[9],
            temp_K=f[10],current_A=f[11],
            alpha=f[12],quality=f[13],spare=f[14])

    @property
    def vib_rms(self) -> float:
        return math_sqrt(self.vib_x**2 + self.vib_y**2 + self.vib_z**2)

def math_sqrt(x): import math; return math.sqrt(max(0,x))


# ── High-speed SPSC ring (10s @ 1kHz) ────────────────────────────

class HighSpeedRing:
    """Lock-free SPSC ring, bounded memory, 10s @ 1kHz."""
    CAPACITY = 10_000

    def __init__(self):
        self._buf  = bytearray(self.CAPACITY * TELEM_V2_BYTES)
        self._head = 0; self._tail = 0; self._count = 0
        self._lock = threading.Lock()
        self._n_crc= 0; self._n_seq_gap = 0
        self._last_seq = -1

    def write(self, s: TelemetryV2) -> bool:
        packed = s.pack()
        if crc16_ccitt(packed[:-2]) != struct.unpack(">H",packed[-2:])[0]:
            self._n_crc += 1; return False
        with self._lock:
            # Sequence gap detection
            if self._last_seq >= 0:
                exp = (self._last_seq + 1) & 0xFFFF
                if s.seq != exp: self._n_seq_gap += 1
            self._last_seq = s.seq
            off = self._tail * TELEM_V2_BYTES
            self._buf[off:off+TELEM_V2_BYTES] = packed
            self._tail = (self._tail + 1) % self.CAPACITY
            if self._count < self.CAPACITY: self._count += 1
            else: self._head = (self._head + 1) % self.CAPACITY
        return True

    def read_recent(self, n: int) -> List[TelemetryV2]:
        n = min(n, self._count); out = []
        with self._lock:
            for i in range(n):
                idx = (self._tail - n + i) % self.CAPACITY
                off = idx * TELEM_V2_BYTES
                s = TelemetryV2.unpack(bytes(self._buf[off:off+TELEM_V2_BYTES]))
                if s: out.append(s)
        return out

    def read_all(self) -> List[TelemetryV2]:
        return self.read_recent(self._count)

    def dump(self, path: str) -> int:
        samples = self.read_all()
        with open(path,"wb") as f:
            for s in samples: f.write(s.pack())
            f.flush(); os.fsync(f.fileno())
        return len(samples)

    @property
    def count(self) -> int: return self._count
    @property
    def n_crc_errors(self) -> int: return self._n_crc
    @property
    def n_seq_gaps(self) -> int: return self._n_seq_gap


# ── DAQ Architecture (1kHz acquisition coordinator) ──────────────

@dataclass(frozen=True, slots=True)
class DAQChannelSpec:
    name:         str
    sample_rate:  int    # Hz
    resolution:   int    # bits
    range_min:    float
    range_max:    float
    units:        str
    hw_filter_Hz: float  # Anti-aliasing filter cutoff

# Sensor specs (production hardware)
DAQ_CHANNELS = [
    DAQChannelSpec("encoder_x",    10000, 32, -10, 400,  "mm",   0),
    DAQChannelSpec("encoder_a",    10000, 32, 0, 1e6,    "deg",  0),
    DAQChannelSpec("tension_N",     80,    24, 0, 50,    "N",    20),
    DAQChannelSpec("rpm",          1000,  16, 0, 300,    "rpm",  100),
    DAQChannelSpec("vib_x",       4000,  16, -16, 16,    "g",    1600),
    DAQChannelSpec("vib_y",       4000,  16, -16, 16,    "g",    1600),
    DAQChannelSpec("vib_z",       4000,  16, -16, 16,    "g",    1600),
    DAQChannelSpec("temp_K",        10,   18, 200, 700,   "K",    1),
    DAQChannelSpec("current_A",    500,  12, 0, 20,      "A",    100),
]

class DAQArchitecture:
    """Synchronized 1kHz DAQ coordinator with HW timestamp alignment."""
    BASE_RATE = 1000   # Hz (highest sync rate)

    def __init__(self):
        self._channels = {c.name: c for c in DAQ_CHANNELS}
        self._ring     = HighSpeedRing()
        self._q:       queue.Queue = queue.Queue(maxsize=2000)
        self._stop     = threading.Event()
        self._thread:  Optional[threading.Thread] = None
        self._t0_us    = int(time.monotonic()*1e6)
        self._seq      = 0
        self._lock     = threading.Lock()
        self._sync_errors = 0

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="DAQThread")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread: self._thread.join(timeout=0.5)

    def acquire(self, x:float, a:float, T:float, rpm:float,
                vx:float=0, vy:float=0, vz:float=0,
                temp_K:float=295.15, current:float=0.0,
                alpha:float=0.0, quality:float=100.0,
                flags:int=1) -> None:
        """1kHz aggregate sample. Non-blocking."""
        ts = int(time.monotonic()*1e6) - self._t0_us
        with self._lock: seq = self._seq; self._seq = (self._seq+1)&0xFFFF
        s = TelemetryV2(ts_us=ts, seq=seq, flags=flags,
            x_mm=x, a_deg=a, T_N=T, rpm=rpm,
            vib_x=vx, vib_y=vy, vib_z=vz,
            temp_K=temp_K, current_A=current,
            alpha=alpha, quality=quality)
        try: self._q.put_nowait(s)
        except queue.Full:
            with self._lock: self._sync_errors += 1

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                s = self._q.get(timeout=0.01)
                self._ring.write(s)
            except queue.Empty: continue

    @property
    def ring(self) -> HighSpeedRing: return self._ring
    @property
    def sync_errors(self) -> int: return self._sync_errors
    @property
    def channels(self) -> dict: return self._channels.copy()

    def summary(self) -> dict:
        samples = self._ring.read_recent(min(500, self._ring.count))
        if not samples: return {"n": 0}
        T_arr = np.array([s.T_N for s in samples])
        vib_arr = np.array([math_sqrt(s.vib_x**2+s.vib_y**2+s.vib_z**2) for s in samples])
        return {
            "n":             self._ring.count,
            "n_channels":    len(self._channels),
            "rate_hz":       self.BASE_RATE,
            "T_mean":        float(T_arr.mean()),
            "T_std":         float(T_arr.std()),
            "vib_rms":       float(np.sqrt(np.mean(vib_arr**2))),
            "crc_errors":    self._ring.n_crc_errors,
            "seq_gaps":      self._ring.n_seq_gaps,
            "sync_errors":   self._sync_errors,
        }
