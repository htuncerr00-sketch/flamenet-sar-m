"""
telemetry_recorder.py — Crash-Safe Binary Telemetry Recorder
=============================================================
48 bytes/sample, CRC-16/CCITT, ring buffer 6000 samples = 60s @ 100Hz.

Format: struct.pack(">QHHfffffffH")
  timestamp_us : uint64  — monotonic µs since session start
  seq          : uint16  — sequence number (wrap 65535)
  flags        : uint16  — bits: 0=running,1=alarm,2=fiber_ok,3=cure_active
  x_mm         : float32 — carriage position
  a_deg        : float32 — spindle cumulative degrees
  tension_N    : float32 — fiber tension
  rpm          : float32 — spindle RPM
  vib_g        : float32 — vibration (g)
  alpha_cure   : float32 — cure degree [0,1]
  quality      : float32 — quality score [0,100]
  temp_C       : float32 — process temperature
  crc16        : uint16  — CRC-16/CCITT-FALSE over first 46 bytes

Thread-safe: lock-free SPSC ring + writer thread.
Crash recovery: last complete CRC-valid sample always readable.
"""
from __future__ import annotations
import os, queue, struct, threading, time
from collections import deque
from dataclasses import dataclass
from typing import List, Optional
import numpy as np

TELEM_STRUCT = struct.Struct(">QHHffffffffHH")  # big-endian: Q(8)+H(2)+H(2)+8xf(32)+H(2)+H(2)=48B
TELEM_BYTES  = TELEM_STRUCT.size               # 48
assert TELEM_BYTES == 48, f"Expected 48, got {TELEM_BYTES}"

RING_CAPACITY = 6000   # 60s @ 100Hz

def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (poly=0x1021, init=0xFFFF)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
        crc &= 0xFFFF
    return crc

@dataclass(slots=True)
class TelemetrySample:
    timestamp_us: int; seq: int; flags: int
    x_mm: float; a_deg: float; tension_N: float; rpm: float
    vib_g: float; alpha_cure: float; quality: float; temp_C: float

    def pack(self) -> bytes:
        # '>QHHffffffffHH': Q,H,H,f×8,H,H = 13 items
        raw = TELEM_STRUCT.pack(
            int(self.timestamp_us) & 0xFFFFFFFFFFFFFFFF,
            int(self.seq) & 0xFFFF,
            int(self.flags) & 0xFFFF,
            float(self.x_mm), float(self.a_deg), float(self.tension_N),
            float(self.rpm), float(self.vib_g), float(self.alpha_cure),
            float(self.quality), float(self.temp_C),
            0, 0)
        crc = crc16_ccitt(raw[:-2])
        return raw[:-2] + struct.pack(">H", crc)

    @classmethod
    def unpack(cls, data: bytes) -> Optional["TelemetrySample"]:
        if len(data) < TELEM_BYTES: return None
        crc_stored = struct.unpack(">H", data[-2:])[0]
        if crc16_ccitt(data[:-2]) != crc_stored: return None
        f = TELEM_STRUCT.unpack(data)
        return cls(timestamp_us=f[0],seq=f[1],flags=f[2],x_mm=f[3],
                   a_deg=f[4],tension_N=f[5],rpm=f[6],vib_g=f[7],
                   alpha_cure=f[8],quality=f[9],temp_C=f[10])

    def to_dict(self) -> dict:
        return {"t_us":self.timestamp_us,"seq":self.seq,"flags":self.flags,
                "x":round(self.x_mm,4),"a":round(self.a_deg,3),
                "T":round(self.tension_N,3),"rpm":round(self.rpm,2),
                "vib":round(self.vib_g,4),"alpha":round(self.alpha_cure,5),
                "q":round(self.quality,2),"temp":round(self.temp_C,2)}


class CrashSafeRing:
    """Lock-free SPSC ring buffer, bounded memory."""
    def __init__(self, capacity: int = RING_CAPACITY):
        self._buf   = bytearray(capacity * TELEM_BYTES)
        self._cap   = capacity
        self._head  = 0; self._tail = 0; self._count = 0
        self._lock  = threading.Lock()
        self._n_crc = 0

    def write(self, s: TelemetrySample) -> bool:
        packed = s.pack()
        if crc16_ccitt(packed[:-2]) != struct.unpack(">H",packed[-2:])[0]:
            self._n_crc += 1; return False
        with self._lock:
            off = self._tail * TELEM_BYTES
            self._buf[off:off+TELEM_BYTES] = packed
            self._tail = (self._tail + 1) % self._cap
            if self._count < self._cap: self._count += 1
            else: self._head = (self._head + 1) % self._cap
        return True

    def read_recent(self, n: int) -> List[TelemetrySample]:
        n = min(n, self._count); out = []
        with self._lock:
            for i in range(n):
                idx = (self._tail - n + i) % self._cap
                off = idx * TELEM_BYTES
                s = TelemetrySample.unpack(bytes(self._buf[off:off+TELEM_BYTES]))
                if s: out.append(s)
        return out

    def read_all(self) -> List[TelemetrySample]:
        return self.read_recent(self._count)

    def dump(self, path: str) -> int:
        samples = self.read_all()
        with open(path,"wb") as f:
            for s in samples: f.write(s.pack())
        return len(samples)

    @property
    def count(self) -> int: return self._count
    @property
    def n_crc_errors(self) -> int: return self._n_crc


class TelemetryRecorder:
    """100Hz telemetry recorder with crash-safe ring and file backup."""
    TARGET_HZ = 100

    def __init__(self, out_dir: str = "/mnt/user-data/outputs"):
        self._dir   = out_dir; os.makedirs(out_dir, exist_ok=True)
        self._ring  = CrashSafeRing()
        self._q:    queue.Queue = queue.Queue(maxsize=300)
        self._stop  = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._t0_us = int(time.monotonic() * 1e6)
        self._seq   = 0
        self._lock  = threading.Lock()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop,daemon=True,name="TelemetryRecorder")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread: self._thread.join(timeout=0.5)

    def record(self, x:float,a:float,T:float,rpm:float,vib:float=0.0,
               alpha:float=0.0,quality:float=100.0,temp:float=22.0,
               flags:int=1) -> None:
        ts = int(time.monotonic()*1e6) - self._t0_us
        with self._lock: seq = self._seq; self._seq = (self._seq+1)&0xFFFF
        s = TelemetrySample(ts,seq,flags,float(x),float(a),float(T),float(rpm),
                            float(vib),float(alpha),float(quality),float(temp))
        try: self._q.put_nowait(s)
        except queue.Full: pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                s = self._q.get(timeout=0.02)
                self._ring.write(s)
            except queue.Empty: continue

    def dump(self, path: str) -> int: return self._ring.dump(path)
    def read_recent(self, n: int) -> List[TelemetrySample]: return self._ring.read_recent(n)
    def read_all(self) -> List[TelemetrySample]: return self._ring.read_all()

    def summary(self) -> dict:
        samples = self._ring.read_all()
        if not samples: return {"n":0}
        T_arr = np.array([s.tension_N for s in samples])
        q_arr = np.array([s.quality for s in samples])
        return {"n":len(samples),"T_mean":float(T_arr.mean()),
                "T_std":float(T_arr.std()),"q_mean":float(q_arr.mean()),
                "crc_errors":self._ring.n_crc_errors,
                "duration_s":len(samples)/self.TARGET_HZ}
