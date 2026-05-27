"""
digital_twin_correlation.py — Real vs Twin Drift Analysis
==========================================================
Gerçek makine telemetrisi ile digital twin simülasyonu arasındaki
sapmaları ölçer ve raporlar.

RMS drift metrikleri:
  RMS_x(n)      = √(Σ(x_real[i]-x_twin[i])²/n)        [mm]
  RMS_tension   = √(Σ(T_real[i]-T_twin[i])²/n)        [N]
  RMS_phi       = √(Σ(φ_real[i]-φ_twin[i])²/n)        [°]
  phi_lag_mean  = mean(φ_cmd[i]-φ_meas[i])             [°]
  delta_L_therm = α_CTE×L×ΔT                           [mm]
  eps_encoder   = 1/(4×steps_per_mm)                   [mm]
  eps_shrink    = α_cure×ε_chem                         [mm/mm]

Correlation score:
  score = 100 × (1 - RMS_x/RMS_x_max) × (1 - RMS_T/RMS_T_max)
  RMS_x_max = 1.0mm, RMS_T_max = 3.0N (production targets)

Adaptive correction:
  If RMS_x > threshold → update twin parameters:
    τ_A correction, backlash correction, thermal correction
"""
from __future__ import annotations
import math, threading, time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np

from telemetry_recorder import TelemetrySample


# ── Drift Metrics ─────────────────────────────────────────────────

@dataclass(slots=True)
class DriftMetrics:
    n_samples:       int
    rms_x_mm:        float
    rms_tension_N:   float
    rms_phi_deg:     float
    phi_lag_mean_deg:float
    delta_L_therm_mm:float   # Thermal expansion mismatch
    eps_encoder_mm:  float   # Encoder quantization error
    eps_shrink:      float   # Cure shrinkage strain
    correlation_score:float  # [0,100]
    twin_accuracy_pct:float  # 100×(1-RMS_x/0.5mm)

    @property
    def is_accurate(self) -> bool:
        return (self.rms_x_mm < 0.5 and
                self.rms_tension_N < 2.0 and
                self.rms_phi_deg < 2.0)

    def summary(self) -> str:
        return (f"n={self.n_samples}  "
                f"RMS_x={self.rms_x_mm:.4f}mm  "
                f"RMS_T={self.rms_tension_N:.4f}N  "
                f"RMS_φ={self.rms_phi_deg:.4f}°  "
                f"φ_lag={self.phi_lag_mean_deg:.4f}°  "
                f"ΔL_th={self.delta_L_therm_mm:.4f}mm  "
                f"score={self.correlation_score:.2f}/100  "
                f"{'✓ ACCURATE' if self.is_accurate else '✗ DRIFTED'}")


@dataclass(slots=True)
class TwinState:
    """Digital twin state at a given time."""
    t_us:      int
    x_mm:      float
    a_deg:     float
    T_N:       float
    rpm:       float
    alpha:     float
    temp_C:    float


# ── Physics Error Sources ─────────────────────────────────────────

def thermal_expansion_error(alpha_CTE: float, L_mm: float, delta_T_K: float) -> float:
    """ΔL = α_CTE × L × ΔT [mm]"""
    return alpha_CTE * L_mm * delta_T_K

def encoder_quantization_error(steps_per_mm: float) -> float:
    """ε_q = 1/(4×steps/mm) [mm] — 4x quadrature decoding"""
    return 1.0 / (4.0 * max(steps_per_mm, 1.0))

def cure_shrinkage_error(alpha: float, eps_chem: float = 0.003,
                         L_mm: float = 300.0) -> float:
    """Longitudinal shrinkage from cure [mm]"""
    return alpha * eps_chem * L_mm

def spindle_lag_error(tau_A_s: float, omega_dps: float) -> float:
    """Steady-state spindle lag: φ_lag = ω × τ_A [°]"""
    return omega_dps * tau_A_s


# ── Digital Twin Correlator ───────────────────────────────────────

class DigitalTwinCorrelator:
    """
    Real telemetry vs digital twin correlation engine.
    Thread-safe, non-blocking update().
    """
    BUFFER = 10000   # Bounded memory

    def __init__(self,
                 steps_per_mm:    float = 80.0,
                 mandrel_L_mm:    float = 300.0,
                 alpha_CTE:       float = 11.7e-6,
                 tau_A_s:         float = 6.0,
                 T_ref_C:         float = 20.0):
        self.spm       = steps_per_mm
        self.L         = mandrel_L_mm
        self.cte       = alpha_CTE
        self.tau_A     = tau_A_s
        self.T_ref     = T_ref_C

        self._real_buf: deque = deque(maxlen=self.BUFFER)
        self._twin_buf: deque = deque(maxlen=self.BUFFER)
        self._lock     = threading.Lock()
        self._n        = 0

    def add_pair(self, real: TelemetrySample, twin: TwinState) -> None:
        """Add synchronized real+twin pair. Thread-safe."""
        with self._lock:
            self._real_buf.append(real)
            self._twin_buf.append(twin)
            self._n += 1

    def compute_drift(self) -> DriftMetrics:
        """Compute all drift metrics from buffered data."""
        with self._lock:
            real_list = list(self._real_buf)
            twin_list = list(self._twin_buf)
        n = min(len(real_list), len(twin_list))
        if n < 2:
            return DriftMetrics(0,0,0,0,0,0,0,0,0,0)

        r_arr = np.array([(s.x_mm, s.tension_N, s.a_deg, s.rpm, s.temp_C)
                          for s in real_list[:n]])
        t_arr = np.array([(s.x_mm, s.T_N, s.a_deg, s.rpm, s.temp_C)
                          for s in twin_list[:n]])

        # RMS errors
        rms_x  = float(np.sqrt(np.mean((r_arr[:,0]-t_arr[:,0])**2)))
        rms_T  = float(np.sqrt(np.mean((r_arr[:,1]-t_arr[:,1])**2)))
        rms_phi= float(np.sqrt(np.mean((r_arr[:,2]-t_arr[:,2])**2)))

        # Spindle phase lag (mean offset)
        phi_lag= float(np.mean(t_arr[:,2] - r_arr[:,2]))  # twin_cmd - real_meas

        # Physics error sources
        mean_temp  = float(r_arr[:,4].mean())
        dT         = mean_temp - self.T_ref
        dL_therm   = thermal_expansion_error(self.cte, self.L, dT)
        eps_enc    = encoder_quantization_error(self.spm)
        mean_alpha = float(np.mean([s.alpha_cure for s in real_list[:n]]))
        eps_shrink = cure_shrinkage_error(mean_alpha)
        mean_omega = float(r_arr[:,3].mean()) * 6.0  # RPM → °/s
        phi_lag_ss = spindle_lag_error(self.tau_A, mean_omega)

        # Correlation score [0,100]
        score = max(0.0, min(100.0,
            100.0 * (1.0 - rms_x/1.0) * (1.0 - rms_T/3.0)))
        twin_acc = max(0.0, 100.0 * (1.0 - rms_x/0.5))

        return DriftMetrics(
            n_samples=n, rms_x_mm=rms_x, rms_tension_N=rms_T,
            rms_phi_deg=rms_phi, phi_lag_mean_deg=phi_lag+phi_lag_ss,
            delta_L_therm_mm=dL_therm, eps_encoder_mm=eps_enc,
            eps_shrink=eps_shrink, correlation_score=score,
            twin_accuracy_pct=twin_acc,
        )

    def update_tau_A(self, new_tau: float, alpha_blend: float = 0.2) -> None:
        """Adaptive τ_A update from correlation data."""
        self.tau_A = (1-alpha_blend)*self.tau_A + alpha_blend*new_tau

    def reset(self) -> None:
        with self._lock:
            self._real_buf.clear(); self._twin_buf.clear(); self._n = 0

    @property
    def n_pairs(self) -> int: return self._n
