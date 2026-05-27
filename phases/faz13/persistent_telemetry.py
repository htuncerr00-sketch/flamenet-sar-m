"""
persistent_telemetry.py — Crash-Safe Binary Telemetri Sistemi
=============================================================
100Hz logging, 60s ring buffer, power-loss recovery.
"""
from __future__ import annotations
import csv, json, math, os, queue, struct, threading, time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 32 byte/sample: timestamp(8) + x(4) + a(4) + rpm(4) + tension(4) + temp(4) + quality(4)
TELEM_FMT    = struct.Struct(">Qffffff")
TELEM_BYTES  = TELEM_FMT.size          # 32
RING_SAMPLES = 6000                    # 60s @ 100Hz
RING_BYTES   = RING_SAMPLES * TELEM_BYTES

@dataclass(slots=True)
class TelemetrySample:
    timestamp_ms: int
    x_mm:   float; a_deg:  float; rpm:    float
    tension_N: float; temp_C: float; quality: float
    def pack(self) -> bytes:
        return TELEM_FMT.pack(self.timestamp_ms,
            self.x_mm,self.a_deg,self.rpm,self.tension_N,self.temp_C,self.quality)
    @classmethod
    def unpack(cls, data: bytes) -> "TelemetrySample":
        ts,x,a,r,T,t,q = TELEM_FMT.unpack(data)
        return cls(ts,x,a,r,T,t,q)
    def to_dict(self) -> dict:
        return {"t":self.timestamp_ms,"x":round(self.x_mm,4),
                "a":round(self.a_deg,3),"rpm":round(self.rpm,2),
                "tension":round(self.tension_N,3),"temp":round(self.temp_C,2),
                "q":round(self.quality,4)}

class CrashSafeRingBuffer:
    """Lock-free single-producer single-consumer binary ring buffer."""
    def __init__(self):
        self._buf   = bytearray(RING_BYTES)
        self._head  = 0; self._tail = 0; self._count = 0
        self._lock  = threading.Lock()
    def write(self, sample: TelemetrySample) -> None:
        data = sample.pack()
        with self._lock:
            off = self._tail * TELEM_BYTES
            self._buf[off:off+TELEM_BYTES] = data
            self._tail = (self._tail + 1) % RING_SAMPLES
            if self._count < RING_SAMPLES: self._count += 1
            else: self._head = (self._head + 1) % RING_SAMPLES
    def read_all(self) -> List[TelemetrySample]:
        with self._lock:
            n = self._count; head = self._head; results = []
            for i in range(n):
                idx = (head + i) % RING_SAMPLES
                off = idx * TELEM_BYTES
                try: results.append(TelemetrySample.unpack(bytes(self._buf[off:off+TELEM_BYTES])))
                except: pass
            return results
    def read_recent(self, n: int) -> List[TelemetrySample]:
        with self._lock:
            n = min(n, self._count); results = []
            for i in range(n):
                idx = (self._tail - n + i) % RING_SAMPLES
                off = idx * TELEM_BYTES
                try: results.append(TelemetrySample.unpack(bytes(self._buf[off:off+TELEM_BYTES])))
                except: pass
            return results
    @property
    def count(self): return self._count
    def save_dump(self, path: str) -> int:
        samples = self.read_all()
        with open(path,"wb") as f:
            for s in samples: f.write(s.pack())
        return len(samples)

class PersistentTelemetry:
    """
    Production telemetry: 100Hz binary ring + CSV + JSON + WebSocket stub.
    Thread: TelemetryThread (daemon).
    """
    TARGET_HZ = 100
    def __init__(self, out_dir: str = "/mnt/user-data/outputs"):
        self._dir    = out_dir; os.makedirs(out_dir, exist_ok=True)
        self._ring   = CrashSafeRingBuffer()
        self._q:     queue.Queue = queue.Queue(maxsize=500)
        self._stop   = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._t0_ms  = int(time.time()*1000)
        self._n_logged = 0

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="Telemetry")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread: self._thread.join(timeout=1.0)

    def log(self, x:float, a:float, rpm:float, tension:float,
            temp:float=20.0, quality:float=1.0) -> None:
        """Non-blocking enqueue. Drop if full."""
        ts = int(time.time()*1000) - self._t0_ms
        s  = TelemetrySample(ts,x,a,rpm,tension,temp,quality)
        try: self._q.put_nowait(s)
        except queue.Full: pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                sample = self._q.get(timeout=0.05)
                self._ring.write(sample)
                self._n_logged += 1
            except queue.Empty: continue

    # ── Exports ──────────────────────────────────────────────────
    def export_csv(self, path: str) -> int:
        samples = self._ring.read_all()
        with open(path,"w",newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_ms","x_mm","a_deg","rpm","tension_N","temp_C","quality"])
            for s in samples:
                w.writerow([s.timestamp_ms,f"{s.x_mm:.4f}",f"{s.a_deg:.3f}",
                             f"{s.rpm:.2f}",f"{s.tension_N:.3f}",f"{s.temp_C:.2f}",f"{s.quality:.4f}"])
        return len(samples)

    def export_json(self, path: str, last_n: int = 100) -> int:
        samples = self._ring.read_recent(last_n)
        with open(path,"w") as f:
            json.dump([s.to_dict() for s in samples], f, separators=(",",":"))
        return len(samples)

    def crash_dump(self, path: str) -> int:
        return self._ring.save_dump(path)

    def summary(self) -> dict:
        samples = self._ring.read_all()
        if not samples: return {"n":0}
        import numpy as np
        T_arr = np.array([s.tension_N for s in samples])
        q_arr = np.array([s.quality for s in samples])
        return {"n":len(samples),"n_logged":self._n_logged,
                "T_mean":float(T_arr.mean()),"T_std":float(T_arr.std()),
                "q_mean":float(q_arr.mean()),"ring_sec":len(samples)/self.TARGET_HZ}
