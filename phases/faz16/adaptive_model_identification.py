"""
adaptive_model_identification.py — Machine Parameter Auto-Identification
=========================================================================
Recursive Bayesian identification of machine parameters from telemetry.

Parameters identified:
  τ_A    : Spindle time constant [s]  — from step responses
  δ_bl   : Backlash [mm]             — from direction reversals
  k_fiber: Fiber stiffness [N/mm]    — from tension/position correlation
  η_visc : Resin viscosity curve     — from process data
  ε_therm: Thermal coefficient       — from T vs position correlation

Method: Recursive Bayesian estimation
  Prior: θ_prior ~ N(μ_prior, σ_prior²)
  Likelihood: θ_meas ~ N(θ_est, σ_meas²)
  Posterior: precision-weighted average

Convergence: |θ_k - θ_{k-1}| < tol for all parameters.
"""
from __future__ import annotations
import math, threading, time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np

from telemetry_recorder import TelemetrySample


@dataclass(slots=True)
class ParameterEstimate:
    """Single parameter Bayesian estimate."""
    name:   str
    value:  float
    sigma:  float          # Uncertainty (1σ)
    n_obs:  int = 0        # Observation count
    converged: bool = False

    def update(self, meas: float, sigma_meas: float) -> None:
        """Bayesian update: precision-weighted fusion."""
        prec_prior = 1.0 / max(self.sigma**2, 1e-12)
        prec_meas  = 1.0 / max(sigma_meas**2, 1e-12)
        self.value = (prec_prior*self.value + prec_meas*meas) / (prec_prior + prec_meas)
        self.sigma = 1.0 / math.sqrt(prec_prior + prec_meas)
        self.n_obs += 1

    def __str__(self) -> str:
        s = "✓" if self.converged else "~"
        return f"{self.name}={self.value:.5f}±{self.sigma:.5f} n={self.n_obs} {s}"


@dataclass(slots=True)
class IdentificationResult:
    """Full identification result after convergence."""
    tau_A_s:        ParameterEstimate
    backlash_mm:    ParameterEstimate
    k_fiber_N_mm:   ParameterEstimate
    thermal_mm_C:   ParameterEstimate
    n_reversals:    int
    n_step_responses:int
    converged_all:  bool
    elapsed_s:      float

    def summary(self) -> str:
        lines = ["  Machine Parameter Identification:"]
        for p in [self.tau_A_s, self.backlash_mm, self.k_fiber_N_mm, self.thermal_mm_C]:
            lines.append(f"    {p}")
        lines.append(f"  Reversals={self.n_reversals}  StepResp={self.n_step_responses}  "
                     f"Converged={'ALL ✓' if self.converged_all else 'partial'}")
        return "\n".join(lines)


class AdaptiveModelIdentification:
    """
    Online machine parameter identification from telemetry stream.
    Thread-safe. Non-blocking update().

    Convergence criteria:
      σ_parameter < 0.05 × |value|   (5% relative uncertainty)
      n_obs ≥ 20
    """
    CONVERGENCE_REL = 0.05   # 5% relative sigma
    MIN_OBS         = 20

    def __init__(self):
        # Initialize parameter estimates with priors
        self._tau_A  = ParameterEstimate("tau_A_s",        6.00, 2.00)
        self._bl     = ParameterEstimate("backlash_mm",    0.15, 0.05)
        self._kf     = ParameterEstimate("k_fiber_N_mm",   0.035,0.010)
        self._therm  = ParameterEstimate("therm_mm_per_C", 0.0035,0.001)
        self._lock   = threading.Lock()
        self._n_rev  = 0
        self._n_step = 0
        self._t_start= time.monotonic()
        # Buffers for step response detection
        self._v_buf: deque = deque(maxlen=200)   # velocity
        self._x_buf: deque = deque(maxlen=200)   # position
        self._T_buf: deque = deque(maxlen=200)   # tension
        self._temp_buf:deque=deque(maxlen=100)   # temperature

    def update(self, sample: TelemetrySample, v_mm_s: float = 0.0) -> None:
        """Process one telemetry sample. Non-blocking."""
        with self._lock:
            self._v_buf.append(v_mm_s)
            self._x_buf.append(sample.x_mm)
            self._T_buf.append(sample.tension_N)
            self._temp_buf.append(sample.temp_C)
            self._detect_reversal()
            if len(self._v_buf) >= 20:
                self._identify_fiber_stiffness()
            if len(self._temp_buf) >= 10:
                self._identify_thermal()
            self._check_convergence()

    def _detect_reversal(self) -> None:
        """Detect direction reversal → estimate backlash."""
        v = list(self._v_buf); x = list(self._x_buf)
        if len(v) < 5: return
        # Sign change in velocity
        recent_v = np.array(v[-5:])
        if np.any(recent_v > 0.5) and np.any(recent_v < -0.5):
            # Find reversal point
            for i in range(1, len(v)):
                if v[i-1] > 0.1 and v[i] < -0.1 or v[i-1] < -0.1 and v[i] > 0.1:
                    if i < len(x)-1:
                        bl_meas   = abs(x[i+1] - x[i])
                        bl_meas   = float(np.clip(bl_meas, 0.001, 2.0))
                        self._bl.update(bl_meas, sigma_meas=0.02)
                        self._n_rev += 1
                        break

    def _identify_fiber_stiffness(self) -> None:
        """k_fiber from tension vs position correlation."""
        x_arr = np.array(list(self._x_buf)[-20:])
        T_arr = np.array(list(self._T_buf)[-20:])
        if x_arr.std() < 0.1: return
        # k_fiber ≈ dT/dx
        try:
            slope, _ = np.polyfit(x_arr, T_arr, 1)
            if abs(slope) > 1e-6:
                k_meas = float(np.abs(slope))
                k_meas = float(np.clip(k_meas, 0.001, 1.0))
                self._kf.update(k_meas, sigma_meas=0.005)
        except Exception: pass

    def _identify_thermal(self) -> None:
        """Thermal coefficient from temperature vs position drift."""
        temp_arr = np.array(list(self._temp_buf))
        x_arr    = np.array(list(self._x_buf)[-len(temp_arr):])
        if len(x_arr) < len(temp_arr): return
        delta_T  = temp_arr[-1] - temp_arr[0]
        if abs(delta_T) < 1.0: return
        delta_x  = x_arr[-1] - x_arr[0]
        th_meas  = float(np.clip(abs(delta_x / delta_T), 0.0001, 0.02))
        self._therm.update(th_meas, sigma_meas=0.0005)

    def add_step_response(self, t_arr: np.ndarray,
                          omega_arr: np.ndarray, omega_inf: float) -> Optional[float]:
        """Fit τ_A from step response data."""
        if len(t_arr) < 5 or omega_inf < 1.0: return None
        thresh = omega_inf * 0.632
        idx    = np.argmax(omega_arr >= thresh)
        if idx == 0: return None
        tau_meas = float(t_arr[idx])
        # Polyfit: log(1-ω/ω_inf) = -t/τ
        ratio = np.clip(omega_arr/omega_inf, 0.01, 0.99)
        log_r = -np.log(1.0 - ratio)
        valid = (log_r > 0.05) & (t_arr > 0)
        if valid.sum() < 3: tau_fit = tau_meas
        else:
            slope, _ = np.polyfit(t_arr[valid], log_r[valid], 1)
            tau_fit  = float(np.clip(1.0/max(slope,1e-6), 0.5, 30.0))
        with self._lock:
            self._tau_A.update(tau_fit, sigma_meas=0.5)
            self._n_step += 1
        return tau_fit

    def _check_convergence(self) -> None:
        for p in [self._tau_A, self._bl, self._kf, self._therm]:
            if p.n_obs >= self.MIN_OBS:
                p.converged = (p.sigma < self.CONVERGENCE_REL * abs(p.value))

    def get_result(self) -> IdentificationResult:
        with self._lock:
            return IdentificationResult(
                tau_A_s        = ParameterEstimate(self._tau_A.name,  self._tau_A.value,  self._tau_A.sigma,  self._tau_A.n_obs,  self._tau_A.converged),
                backlash_mm    = ParameterEstimate(self._bl.name,     self._bl.value,     self._bl.sigma,     self._bl.n_obs,     self._bl.converged),
                k_fiber_N_mm   = ParameterEstimate(self._kf.name,     self._kf.value,     self._kf.sigma,     self._kf.n_obs,     self._kf.converged),
                thermal_mm_C   = ParameterEstimate(self._therm.name,  self._therm.value,  self._therm.sigma,  self._therm.n_obs,  self._therm.converged),
                n_reversals    = self._n_rev,
                n_step_responses=self._n_step,
                converged_all  = all(p.converged for p in [self._tau_A,self._bl,self._kf,self._therm]),
                elapsed_s      = time.monotonic()-self._t_start,
            )
