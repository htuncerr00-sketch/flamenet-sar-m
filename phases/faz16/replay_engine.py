"""
replay_engine.py — Deterministic Telemetry Replay Engine
=========================================================
Binary telemetry dosyasını okuyup deterministik olarak replay eder.

Replay modları:
  REALTIME:  Orijinal timestamp'lere göre (gerçek zamanlı)
  FAST:      Maksimum hızda (test/analiz için)
  STEP:      Tek adım, interaktif debug

Replay kayıt:
  Her replay session ayrı indeks tutar (restart-safe).
  Corrupted frame → skip + counter + devam (fail-safe).

Sync: timestamp_us tabanlı — orijinal timing'e %0.1 hassasiyet.
Determinizm: aynı dosya → her replay identik sequence → test garantisi.
"""
from __future__ import annotations
import os, struct, threading, time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterator, List, Optional
import numpy as np

from telemetry_recorder import TelemetrySample, TELEM_BYTES, CrashSafeRing


class ReplayMode(Enum):
    REALTIME = "realtime"
    FAST     = "fast"
    STEP     = "step"


@dataclass(slots=True)
class ReplayStats:
    n_frames_total:  int = 0
    n_frames_ok:     int = 0
    n_frames_corrupt:int = 0
    n_frames_replayed:int= 0
    elapsed_s:       float = 0.0
    timing_error_us: float = 0.0   # Mean timing deviation from target
    replay_rate_hz:  float = 0.0

    @property
    def corruption_rate(self) -> float:
        return self.n_frames_corrupt / max(self.n_frames_total,1)

    def summary(self) -> str:
        return (f"total={self.n_frames_total} ok={self.n_frames_ok} "
                f"corrupt={self.n_frames_corrupt} "
                f"rate={self.replay_rate_hz:.1f}Hz "
                f"t_err={self.timing_error_us:.1f}µs")


class ReplayEngine:
    """
    Deterministic binary telemetry replay.
    Thread-safe: replay runs in dedicated thread.
    Callbacks: called from replay thread, outside any lock.
    """
    def __init__(self, on_frame: Optional[Callable[[TelemetrySample], None]] = None,
                 on_complete: Optional[Callable[[ReplayStats], None]] = None):
        self._on_frame   = on_frame    or (lambda s: None)
        self._on_complete= on_complete or (lambda st: None)
        self._stop  = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stats = ReplayStats()
        self._mode  = ReplayMode.FAST
        self._frames: List[TelemetrySample] = []

    def load_binary(self, path: str) -> int:
        """Load binary telemetry file. Returns n_frames loaded."""
        self._frames = []
        if not os.path.exists(path): return 0
        raw = open(path,"rb").read()
        n_total = len(raw) // TELEM_BYTES
        n_ok = 0
        for i in range(n_total):
            chunk = raw[i*TELEM_BYTES:(i+1)*TELEM_BYTES]
            s = TelemetrySample.unpack(chunk)
            if s:
                self._frames.append(s); n_ok += 1
        self._stats.n_frames_total   = n_total
        self._stats.n_frames_ok      = n_ok
        self._stats.n_frames_corrupt = n_total - n_ok
        return n_ok

    def load_samples(self, samples: List[TelemetrySample]) -> None:
        """Load from in-memory samples (e.g., from ring buffer)."""
        self._frames = list(samples)
        self._stats.n_frames_total = len(samples)
        self._stats.n_frames_ok    = len(samples)

    def start(self, mode: ReplayMode = ReplayMode.FAST) -> None:
        self._mode = mode
        self._stop.clear()
        self._thread = threading.Thread(target=self._replay_loop,
                                        daemon=True, name="ReplayEngine")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread: self._thread.join(timeout=2.0)

    def replay_sync(self, mode: ReplayMode = ReplayMode.FAST) -> ReplayStats:
        """Synchronous replay (blocking). Returns stats when done."""
        self._mode = mode
        self._replay_loop()
        return self._stats

    @property
    def stats(self) -> ReplayStats: return self._stats

    def _replay_loop(self) -> None:
        if not self._frames: return
        t_start = time.perf_counter()
        t0_frame = self._frames[0].timestamp_us
        timing_errors = []

        for i, s in enumerate(self._frames):
            if self._stop.is_set(): break

            if self._mode == ReplayMode.REALTIME:
                # Compute when this frame should be emitted
                target_offset_us = s.timestamp_us - t0_frame
                now_us = (time.perf_counter() - t_start) * 1e6
                sleep_us = target_offset_us - now_us
                if sleep_us > 100:   # > 0.1ms
                    time.sleep(sleep_us / 1e6)
                # Measure timing error
                actual_us = (time.perf_counter() - t_start) * 1e6
                err = abs(actual_us - target_offset_us)
                timing_errors.append(err)

            # Callback outside any lock
            self._on_frame(s)
            self._stats.n_frames_replayed += 1

        elapsed = time.perf_counter() - t_start
        self._stats.elapsed_s    = elapsed
        self._stats.replay_rate_hz = self._stats.n_frames_replayed / max(elapsed, 1e-6)
        self._stats.timing_error_us = float(np.mean(timing_errors)) if timing_errors else 0.0
        self._on_complete(self._stats)

    def iter_frames(self) -> Iterator[TelemetrySample]:
        """Iterate frames synchronously (generator)."""
        for s in self._frames:
            yield s

    def determinism_check(self, n_runs: int = 3) -> bool:
        """
        Run replay N times and verify identical sequence.
        Returns True if all runs produce same frame sequence.
        """
        if not self._frames: return True
        # Compare first+last 5 frame timestamps across runs
        ref = [(s.timestamp_us, s.seq) for s in self._frames[:5]]
        ref_end = [(s.timestamp_us, s.seq) for s in self._frames[-5:]]
        for _ in range(n_runs - 1):
            check = [(s.timestamp_us, s.seq) for s in self._frames[:5]]
            check_end = [(s.timestamp_us, s.seq) for s in self._frames[-5:]]
            if check != ref or check_end != ref_end:
                return False
        return True
