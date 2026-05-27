"""
core/digital_twin.py — Real-time Digital Twin Simulator (100Hz)
==================================================================
First-order lag spindle model:
  ω(t+dt) = ω(t) + (ω_cmd - ω(t)) × dt/τ_A
First-order lag carriage:
  v(t+dt) = v(t) + (v_cmd - v(t)) × dt/τ_X
"""
from __future__ import annotations
import math, threading, time
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional
import numpy as np
from ..hardware.esp32_link import TelemetryFrame


@dataclass(slots=True)
class TwinState:
    x_mm:      float = 0.0
    a_deg:     float = 0.0
    v_x_mm_s:  float = 0.0
    omega_dps: float = 0.0
    T_N:       float = 15.0
    alpha:     float = 0.0
    rpm:       float = 0.0


@dataclass(slots=True)
class TwinParams:
    tau_A_s:     float = 4.5     # spindle time constant
    tau_X_s:     float = 0.3     # carriage time constant
    backlash_mm: float = 0.15
    friction:    float = 0.05


class DigitalTwin:
    """
    100Hz first-order dynamic model.
    Runs in dedicated thread, computes RMS drift vs real.
    """
    STEP_HZ = 100

    def __init__(self, params: Optional[TwinParams] = None):
        self._p = params or TwinParams()
        self._state = TwinState()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Setpoints from controller
        self._v_x_cmd = 0.0
        self._omega_cmd = 0.0
        self._T_cmd = 15.0
        # Correlation tracking
        self._real_buf: deque = deque(maxlen=1000)
        self._twin_buf: deque = deque(maxlen=1000)

    def set_setpoints(self, v_x_mm_s: float, omega_dps: float,
                      T_N: float) -> None:
        with self._lock:
            self._v_x_cmd = float(v_x_mm_s)
            self._omega_cmd = float(omega_dps)
            self._T_cmd = float(T_N)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="DigitalTwin")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _loop(self) -> None:
        dt = 1.0 / self.STEP_HZ
        last_t = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            real_dt = now - last_t
            last_t = now
            with self._lock:
                v_cmd = self._v_x_cmd
                w_cmd = self._omega_cmd
                T_cmd = self._T_cmd
            # First-order updates
            with self._lock:
                self._state.v_x_mm_s += (v_cmd - self._state.v_x_mm_s) * dt / self._p.tau_X_s
                self._state.omega_dps += (w_cmd - self._state.omega_dps) * dt / self._p.tau_A_s
                self._state.x_mm += self._state.v_x_mm_s * dt
                self._state.a_deg += self._state.omega_dps * dt
                self._state.rpm = self._state.omega_dps / 6.0
                self._state.T_N = T_cmd   # tension assumed instantaneous from PID
            time.sleep(max(0, dt - real_dt * 0.1))

    def add_real_sample(self, frame: TelemetryFrame) -> None:
        with self._lock:
            self._real_buf.append((frame.x_mm, frame.T_N, frame.a_deg))
            self._twin_buf.append((self._state.x_mm, self._state.T_N, self._state.a_deg))

    @property
    def state(self) -> TwinState:
        with self._lock:
            return TwinState(
                x_mm=self._state.x_mm, a_deg=self._state.a_deg,
                v_x_mm_s=self._state.v_x_mm_s,
                omega_dps=self._state.omega_dps,
                T_N=self._state.T_N, alpha=self._state.alpha,
                rpm=self._state.rpm)

    def correlation_rms(self) -> dict:
        with self._lock:
            n = min(len(self._real_buf), len(self._twin_buf))
            if n < 2:
                return {"n":0, "rms_x":0.0, "rms_T":0.0, "rms_phi":0.0}
            real = np.array(list(self._real_buf)[:n])
            twin = np.array(list(self._twin_buf)[:n])
        return {
            "n":      n,
            "rms_x":   float(np.sqrt(np.mean((real[:,0]-twin[:,0])**2))),
            "rms_T":   float(np.sqrt(np.mean((real[:,1]-twin[:,1])**2))),
            "rms_phi": float(np.sqrt(np.mean((real[:,2]-twin[:,2])**2))),
        }
