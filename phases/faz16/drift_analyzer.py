"""
drift_analyzer.py — Multi-Metric Drift Analyzer + Long-Run Projection
=======================================================================
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import numpy as np

@dataclass(slots=True)
class DriftReport:
    n_samples:          int
    drift_x_mm_per_h:   float   # Carriage position drift rate
    drift_T_N_per_h:    float   # Tension drift rate
    drift_phi_deg_per_h:float   # Spindle phase drift rate
    drift_quality_per_h:float   # Quality score drift rate
    rms_x_mm:           float
    rms_T_N:            float
    rms_phi_deg:        float
    thermal_x_mm:       float   # Thermal expansion error
    encoder_mm:         float   # Encoder quantization
    shrink_mm:          float   # Cure shrinkage
    stable:             bool    # All drifts within limits

    @property
    def total_error_mm(self) -> float:
        return math.sqrt(self.rms_x_mm**2 + (self.thermal_x_mm/10)**2)

    def project_8h(self) -> dict:
        return {
            "x_drift_8h_mm":   abs(self.drift_x_mm_per_h) * 8.0,
            "T_drift_8h_N":    abs(self.drift_T_N_per_h) * 8.0,
            "phi_drift_8h_deg":abs(self.drift_phi_deg_per_h) * 8.0,
            "total_error_8h_mm": abs(self.drift_x_mm_per_h)*8 + self.thermal_x_mm,
        }


class DriftAnalyzer:
    """Computes drift rates and long-run projections."""
    LIMITS = {"x_mm_per_h":0.5, "T_N_per_h":2.0, "phi_deg_per_h":10.0}

    def analyze(self, real_x: np.ndarray, twin_x: np.ndarray,
                real_T: np.ndarray, twin_T: np.ndarray,
                real_phi: np.ndarray, twin_phi: np.ndarray,
                quality: np.ndarray, dt_s: float = 0.01,
                temp_delta_C: float = 20.0,
                alpha_cure: float = 0.9) -> DriftReport:
        n = min(len(real_x), len(twin_x))
        if n < 2:
            return DriftReport(0,0,0,0,0,0,0,0,0,0,0,False)

        ex = real_x[:n] - twin_x[:n]
        eT = real_T[:n] - twin_T[:n]
        ep = real_phi[:n] - twin_phi[:n]

        # RMS errors
        rms_x  = float(np.sqrt(np.mean(ex**2)))
        rms_T  = float(np.sqrt(np.mean(eT**2)))
        rms_phi= float(np.sqrt(np.mean(ep**2)))

        # Drift rates (linear trend / elapsed time)
        elapsed_h = n * dt_s / 3600.0
        def rate(arr, elapsed): return float(np.polyfit(np.arange(len(arr)),arr,1)[0]) * len(arr) * dt_s / 3600.0 if len(arr)>2 else 0.0

        dx_h  = rate(ex, elapsed_h)
        dT_h  = rate(eT, elapsed_h)
        dp_h  = rate(ep, elapsed_h)
        dq_h  = rate(quality[:n], elapsed_h) if len(quality)>=n else 0.0

        # Physics error sources
        therm = 11.7e-6 * 300.0 * temp_delta_C * 1000  # mm
        enc   = 1.0/(4*80.0)                           # mm
        shrink= alpha_cure * 0.003 * 300.0             # mm

        stable = (abs(dx_h) < self.LIMITS["x_mm_per_h"] and
                  abs(dT_h) < self.LIMITS["T_N_per_h"] and
                  abs(dp_h) < self.LIMITS["phi_deg_per_h"])

        return DriftReport(n_samples=n,
            drift_x_mm_per_h=dx_h, drift_T_N_per_h=dT_h,
            drift_phi_deg_per_h=dp_h, drift_quality_per_h=dq_h,
            rms_x_mm=rms_x, rms_T_N=rms_T, rms_phi_deg=rms_phi,
            thermal_x_mm=therm, encoder_mm=enc, shrink_mm=shrink,
            stable=stable)


