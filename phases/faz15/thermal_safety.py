"""
thermal_safety.py — Thermal Runaway Detection + Safe Halt
==========================================================
KRİTİK: Termal kaçma (exotherm kaçması) tespit → SAFE HALT.

Termal kaçma mekanizması:
  Exotherm → T artar → k(T) üstel artar → dα/dt artar → daha fazla exotherm
  Pozitif geri besleme → kontrol dışı sıcaklık artışı

Tespit: dT/dt eşik kontrolü + Avrami kritik dα/dt
  dT/dt > 2°C/s     → WARNING
  dT/dt > 5°C/s     → CRITICAL → feedhold
  dT/dt > 10°C/s    → FATAL → SAFE HALT + alarm
  T > T_max_safe    → FATAL → SAFE HALT

Safe Halt prosedürü:
  1. Heater OFF
  2. Cooling ON (fan / quench)
  3. Motion STOP
  4. Operator alarm
  5. Log event (kurtarma için)

Avrami-Erofeev accelerating period check:
  Eğer dα/dt giderek artıyorsa (d²α/dt² > 0 sustained) → erken uyarı

Thread-safe: safety thread priority yüksek, non-blocking.
"""
from __future__ import annotations
import queue, threading, time
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, List, Optional
import numpy as np


class ThermalSafetyLevel(IntEnum):
    OK       = 0
    WARNING  = 1
    CRITICAL = 2
    FATAL    = 3


@dataclass(slots=True)
class ThermalEvent:
    level:     ThermalSafetyLevel
    code:      str
    T_C:       float
    dT_dt:     float
    alpha:     float
    timestamp: float
    action:    str

    def __str__(self) -> str:
        t = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        return (f"[{t}] L{int(self.level)} {self.code}: "
                f"T={self.T_C:.1f}°C dT/dt={self.dT_dt:.3f}°C/s "
                f"α={self.alpha:.3f} → {self.action}")


@dataclass(frozen=True, slots=True)
class ThermalConfig:
    T_max_C:        float = 250.0    # Decomposition limit
    T_warn_C:       float = 220.0    # Warning threshold
    dT_warn_Cs:     float = 2.0      # dT/dt warning [°C/s]
    dT_crit_Cs:     float = 5.0      # dT/dt critical [°C/s]
    dT_fatal_Cs:    float = 10.0     # dT/dt fatal [°C/s]
    alpha_max:      float = 0.98     # Max cure (beyond → overcure risk)
    hb_timeout_s:   float = 2.0      # Heartbeat timeout
    filter_tau_s:   float = 5.0      # dT/dt EMA filter
    accel_window:   int   = 10       # Avrami accelerating period check


class ThermalSafetyMonitor:
    """
    Real-time thermal runaway detector.
    Thread-safe, non-blocking, advisory + hard-stop.

    Architecture:
      - Poll: 100ms (10Hz thermal measurement)
      - Callback: outside lock (deadlock-free)
      - Queue: put_nowait (never blocks safety loop)
    """
    POLL_MS = 100

    def __init__(self,
                 config:    ThermalConfig = None,
                 on_event:  Optional[Callable[[ThermalEvent], None]] = None,
                 on_halt:   Optional[Callable[[], None]] = None):
        self._cfg      = config or ThermalConfig()
        self._on_ev    = on_event or (lambda e: None)
        self._on_halt  = on_halt  or (lambda: None)

        # State (atomic updates)
        self._T_C      = 20.0
        self._alpha    = 0.0
        self._halt     = False
        self._hb       = time.monotonic()

        # Filtering
        self._T_hist:  deque = deque(maxlen=50)
        self._dadt_hist:deque= deque(maxlen=self._cfg.accel_window)
        self._dT_dt_ema= 0.0

        # Events
        self._events:  List[ThermalEvent] = []
        self._eq:      queue.Queue = queue.Queue(maxsize=32)
        self._lock     = threading.Lock()

        self._stop     = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._n_polls  = 0

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                         name="ThermalSafety")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread: self._thread.join(timeout=0.3)

    def update(self, T_C: float, alpha: float) -> None:
        """Non-blocking state update. Called from ProcessThread."""
        self._T_C   = float(np.clip(T_C, -50, 600))
        self._alpha = float(np.clip(alpha, 0, 1))
        self._hb    = time.monotonic()

    def clear_halt(self) -> None:
        """Operator clears halt after fault resolution."""
        self._halt = False

    @property
    def is_halted(self) -> bool: return self._halt

    @property
    def n_events(self) -> int:
        with self._lock: return len(self._events)

    def get_events(self, n: int = 10) -> List[ThermalEvent]:
        with self._lock: return list(self._events[-n:])

    # ── Monitor loop ──────────────────────────────────────────────

    def _loop(self) -> None:
        period = self.POLL_MS / 1000.0
        while not self._stop.is_set():
            t0 = time.monotonic()
            self._poll()
            self._drain()
            elapsed = time.monotonic() - t0
            time.sleep(max(0.0, period - elapsed))

    def _poll(self) -> None:
        self._n_polls += 1
        T  = self._T_C; alpha = self._alpha
        cfg= self._cfg

        # dT/dt estimation (EMA of finite difference)
        self._T_hist.append(T)
        if len(self._T_hist) >= 3:
            # FD over last 3 samples (Δt ≈ POLL_MS)
            dt = self.POLL_MS / 1000.0
            dT_dt_raw = (float(self._T_hist[-1]) - float(self._T_hist[-3])) / (2*dt)
            tau = cfg.filter_tau_s
            self._dT_dt_ema += (dT_dt_raw - self._dT_dt_ema) * dt / max(tau, dt)
        dT_dt = self._dT_dt_ema

        # Avrami accelerating check (d²α/dt²)
        # This is a simplified proxy: check if dT/dt is monotonically increasing
        accel_runaway = False
        if len(self._T_hist) >= cfg.accel_window:
            T_arr    = np.array(self._T_hist)
            dT_arr   = np.diff(T_arr)
            accel_runaway = bool(np.all(dT_arr[-5:] > 0) and dT_dt > cfg.dT_warn_Cs)

        # Heartbeat timeout
        if not self._halt:
            dt_hb = time.monotonic() - self._hb
            if dt_hb > cfg.hb_timeout_s:
                self._fire(ThermalSafetyLevel.CRITICAL, "THERMAL_HB_TIMEOUT",
                           T, dT_dt, alpha, "feedhold")

        if self._halt: return  # Halted — no further checks

        # Check hierarchy
        if T >= cfg.T_max_C:
            self._fire_fatal("T_DECOMP", T, dT_dt, alpha)
        elif dT_dt >= cfg.dT_fatal_Cs or (accel_runaway and dT_dt > cfg.dT_crit_Cs):
            self._fire_fatal("THERMAL_RUNAWAY", T, dT_dt, alpha)
        elif dT_dt >= cfg.dT_crit_Cs:
            self._fire(ThermalSafetyLevel.CRITICAL, "dT_CRITICAL",
                       T, dT_dt, alpha, "feedhold+cool")
        elif T >= cfg.T_warn_C:
            self._fire(ThermalSafetyLevel.WARNING, "T_WARN",
                       T, dT_dt, alpha, "reduce_heat")
        elif dT_dt >= cfg.dT_warn_Cs:
            self._fire(ThermalSafetyLevel.WARNING, "dT_WARN",
                       T, dT_dt, alpha, "monitor")
        elif alpha >= cfg.alpha_max:
            self._fire(ThermalSafetyLevel.WARNING, "OVERCURE",
                       T, dT_dt, alpha, "check_process")

    def _fire_fatal(self, code: str, T: float, dT_dt: float, alpha: float) -> None:
        self._halt = True
        self._fire(ThermalSafetyLevel.FATAL, code, T, dT_dt, alpha,
                   "SAFE_HALT+heater_off+cooling_on+motion_stop")
        self._on_halt()   # Called outside lock

    def _fire(self, level: ThermalSafetyLevel, code: str,
              T: float, dT_dt: float, alpha: float, action: str) -> None:
        ev = ThermalEvent(level=level, code=code, T_C=T, dT_dt=dT_dt,
                          alpha=alpha, timestamp=time.time(), action=action)
        with self._lock: self._events.append(ev)
        try: self._eq.put_nowait(ev)
        except queue.Full: pass   # Never block

    def _drain(self) -> None:
        """Callbacks outside lock — deadlock-free."""
        while True:
            try:
                ev = self._eq.get_nowait()
                self._on_ev(ev)
            except queue.Empty: break

    def report(self) -> str:
        evs = self.get_events(5)
        lines = [f"  ThermalSafety: polls={self._n_polls} "
                 f"halt={'YES' if self._halt else 'NO'} "
                 f"T={self._T_C:.1f}°C dT/dt={self._dT_dt_ema:.3f}°C/s"]
        for e in evs: lines.append(f"    {e}")
        return "\n".join(lines)
