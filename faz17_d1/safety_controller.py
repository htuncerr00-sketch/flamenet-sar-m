"""
core/safety_controller.py — Realtime Safety Controller (SACROSANCT)
=====================================================================
The safety thread:
  - Highest priority
  - NEVER blocks (put_nowait, no lock-held callbacks)
  - Drains event queue OUTSIDE lock
  - Validates AI advisories before they reach controller

Bounds (hard limits):
  T_MIN_N=3, T_MAX_N=38   (fiber breaks below 3N, damage above 38N)
  RPM_MAX=260             (mechanical limit)
  X_MIN_MM=-5, X_MAX_MM=395   (travel limits)
  VIB_MAX_G=2.0           (severe vibration)
  TEMP_MAX_K=523          (250°C — decomposition)
  DT_DT_MAX_KS=10         (10°C/s thermal runaway)
"""
from __future__ import annotations
import queue, threading, time
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, List, Optional
from ..hardware.esp32_link import TelemetryFrame


class SafetyLevel(IntEnum):
    OK   = 0
    INFO = 1
    WARN = 2
    CRIT = 3
    FATAL= 4


@dataclass(slots=True)
class SafetyEvent:
    level:     SafetyLevel
    code:      str
    msg:       str
    value:     float
    threshold: float
    timestamp: float

    def __str__(self) -> str:
        t = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        return f"[{t}] L{int(self.level)} {self.code}: {self.msg} ({self.value:.3f}/{self.threshold:.3f})"


@dataclass(frozen=True, slots=True)
class SafetyBounds:
    T_MIN_N:      float = 3.0
    T_MAX_N:      float = 38.0
    RPM_MAX:      float = 260.0
    X_MIN_MM:     float = -5.0
    X_MAX_MM:     float = 395.0
    VIB_MAX_G:    float = 2.0
    TEMP_MAX_K:   float = 523.15   # 250°C
    DT_DT_MAX_KS: float = 10.0      # K/s


class SafetyController:
    """
    Realtime safety controller.
    Thread-safe, non-blocking, callbacks dispatched OUTSIDE lock.

    Public API:
      - update_frame(frame)    : ingest one telemetry frame (lock-free)
      - register_callback(cb)  : add event listener (cb called outside lock)
      - validate_advisory(...) : AI advisory bounds check
      - is_halted              : current halt state
      - drain_events()         : pull events for UI display
    """
    POLL_MS = 10

    def __init__(self, bounds: Optional[SafetyBounds] = None):
        self._bounds = bounds or SafetyBounds()
        self._halted = False
        self._reason = ""
        self._lock = threading.Lock()
        self._event_q: queue.Queue = queue.Queue(maxsize=200)
        self._events: deque = deque(maxlen=500)
        self._callbacks: List[Callable[[SafetyEvent], None]] = []
        self._temp_history: deque = deque(maxlen=10)   # last 10 temps for dT/dt
        self._n_checks = 0

    def register_callback(self, cb: Callable[[SafetyEvent], None]) -> None:
        with self._lock:
            self._callbacks.append(cb)

    def update_frame(self, frame: TelemetryFrame) -> None:
        """
        Validate frame against bounds. Non-blocking.
        Fires events via queue (drained outside lock by callbacks).
        """
        if self._halted:
            return
        b = self._bounds
        events_to_fire: List[SafetyEvent] = []
        now = time.time()
        self._n_checks += 1

        # Tension
        if frame.T_N < b.T_MIN_N:
            events_to_fire.append(SafetyEvent(
                SafetyLevel.CRIT, "TENSION_LOW",
                f"Tension below {b.T_MIN_N}N — fiber break?",
                frame.T_N, b.T_MIN_N, now))
        elif frame.T_N > b.T_MAX_N:
            events_to_fire.append(SafetyEvent(
                SafetyLevel.CRIT, "TENSION_HIGH",
                f"Tension above {b.T_MAX_N}N",
                frame.T_N, b.T_MAX_N, now))

        # RPM
        if frame.rpm > b.RPM_MAX:
            events_to_fire.append(SafetyEvent(
                SafetyLevel.FATAL, "RPM_OVER",
                f"RPM {frame.rpm:.1f} > {b.RPM_MAX}",
                frame.rpm, b.RPM_MAX, now))

        # X position
        if frame.x_mm < b.X_MIN_MM or frame.x_mm > b.X_MAX_MM:
            events_to_fire.append(SafetyEvent(
                SafetyLevel.FATAL, "X_OUT_OF_RANGE",
                f"X={frame.x_mm:.2f}mm out of [{b.X_MIN_MM}, {b.X_MAX_MM}]",
                frame.x_mm, b.X_MAX_MM, now))

        # Vibration
        vib_rms = (frame.vib_x**2 + frame.vib_y**2 + frame.vib_z**2) ** 0.5
        if vib_rms > b.VIB_MAX_G:
            events_to_fire.append(SafetyEvent(
                SafetyLevel.CRIT, "VIB_OVER",
                f"Vibration RMS {vib_rms:.2f}g > {b.VIB_MAX_G}",
                vib_rms, b.VIB_MAX_G, now))

        # Temperature
        if frame.temp_K > b.TEMP_MAX_K:
            events_to_fire.append(SafetyEvent(
                SafetyLevel.FATAL, "TEMP_OVER",
                f"Temperature {frame.temp_K-273.15:.1f}°C > {b.TEMP_MAX_K-273.15}°C",
                frame.temp_K, b.TEMP_MAX_K, now))

        # dT/dt thermal runaway
        with self._lock:
            self._temp_history.append((frame.ts_us, frame.temp_K))
            if len(self._temp_history) >= 5:
                t0_us, T0 = self._temp_history[0]
                t1_us, T1 = self._temp_history[-1]
                dt_s = (t1_us - t0_us) / 1e6
                if dt_s > 1e-6:
                    dT_dt = (T1 - T0) / dt_s
                    if abs(dT_dt) > b.DT_DT_MAX_KS:
                        events_to_fire.append(SafetyEvent(
                            SafetyLevel.FATAL, "THERMAL_RUNAWAY",
                            f"dT/dt={dT_dt:.2f}K/s > {b.DT_DT_MAX_KS}",
                            dT_dt, b.DT_DT_MAX_KS, now))

        # Fire events — all OUTSIDE the lock
        for ev in events_to_fire:
            self._fire(ev)

    def _fire(self, ev: SafetyEvent) -> None:
        """Called outside lock. Append to deque + push to queue + callbacks."""
        with self._lock:
            self._events.append(ev)
            callbacks_snapshot = list(self._callbacks)
            # FATAL → set halt
            if ev.level == SafetyLevel.FATAL:
                self._halted = True
                self._reason = ev.code

        try:
            self._event_q.put_nowait(ev)
        except queue.Full:
            pass   # drop, never block

        # Callbacks OUTSIDE lock
        for cb in callbacks_snapshot:
            try:
                cb(ev)
            except Exception:
                pass

    def validate_advisory(self, T_N: float, rpm: float,
                          feed_mm_s: float) -> bool:
        """
        Check if an AI advisory would violate safety bounds.
        Returns True if safe to apply.
        """
        b = self._bounds
        if not (b.T_MIN_N <= T_N <= b.T_MAX_N): return False
        if not (0 <= rpm <= b.RPM_MAX): return False
        if not (0 <= feed_mm_s <= 200.0): return False   # max feed
        return True

    @property
    def is_halted(self) -> bool:
        with self._lock: return self._halted

    @property
    def halt_reason(self) -> str:
        with self._lock: return self._reason

    @property
    def n_checks(self) -> int:
        with self._lock: return self._n_checks

    @property
    def n_events(self) -> int:
        with self._lock: return len(self._events)

    def clear_halt(self) -> None:
        """Operator-acknowledged clear. Should only be called after fault resolved."""
        with self._lock:
            self._halted = False
            self._reason = ""

    def drain_events(self, max_n: int = 100) -> List[SafetyEvent]:
        """Pull events from queue (non-blocking) for UI display."""
        out: List[SafetyEvent] = []
        for _ in range(max_n):
            try:
                out.append(self._event_q.get_nowait())
            except queue.Empty:
                break
        return out

    def get_recent_events(self, n: int = 20) -> List[SafetyEvent]:
        with self._lock:
            return list(self._events)[-n:]
