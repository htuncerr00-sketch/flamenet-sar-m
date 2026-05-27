"""
real_vs_twin_fitter.py — Online Twin Parameter Fitting
========================================================
Recursive Bayesian update of twin parameters using real telemetry.
Advisory only — never applied without SafetyValidator.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
import numpy as np

@dataclass(slots=True)
class TwinFitState:
    """Current fitted twin parameters with uncertainty."""
    tau_A_s:       float = 6.0
    tau_A_sigma:   float = 1.0
    backlash_mm:   float = 0.15
    backlash_sigma:float = 0.05
    friction_coef: float = 0.05
    friction_sigma:float = 0.01
    k_fiber_N_mm:  float = 0.04
    k_fiber_sigma: float = 0.01
    n_updates:     int   = 0
    converged:     bool  = False

    def converged_check(self, rel_tol: float = 0.05) -> bool:
        cond = (self.tau_A_sigma  < rel_tol * abs(self.tau_A_s) and
                self.backlash_sigma < rel_tol * abs(self.backlash_mm) and
                self.friction_sigma < rel_tol * abs(self.friction_coef))
        self.converged = cond
        return cond


class RealVsTwinFitter:
    """
    Online Bayesian parameter fitting.
    Each measurement → posterior update (precision-weighted).
    """
    def __init__(self, initial: TwinFitState = None):
        self.state = initial or TwinFitState()
        self._fit_history = []

    def update_tau_A(self, measured_tau: float, meas_sigma: float = 0.5) -> None:
        prec_prior = 1.0 / max(self.state.tau_A_sigma**2, 1e-12)
        prec_meas  = 1.0 / max(meas_sigma**2, 1e-12)
        self.state.tau_A_s = ((prec_prior*self.state.tau_A_s + prec_meas*measured_tau) /
                              (prec_prior + prec_meas))
        self.state.tau_A_sigma = 1.0 / math.sqrt(prec_prior + prec_meas)
        self.state.n_updates += 1
        self.state.converged_check()

    def update_backlash(self, measured_bl: float, meas_sigma: float = 0.02) -> None:
        prec_prior = 1.0 / max(self.state.backlash_sigma**2, 1e-12)
        prec_meas  = 1.0 / max(meas_sigma**2, 1e-12)
        self.state.backlash_mm = ((prec_prior*self.state.backlash_mm + prec_meas*measured_bl) /
                                   (prec_prior + prec_meas))
        self.state.backlash_sigma = 1.0 / math.sqrt(prec_prior + prec_meas)
        self.state.n_updates += 1
        self.state.converged_check()

    def update_friction(self, measured_mu: float, meas_sigma: float = 0.005) -> None:
        prec_prior = 1.0 / max(self.state.friction_sigma**2, 1e-12)
        prec_meas  = 1.0 / max(meas_sigma**2, 1e-12)
        self.state.friction_coef = ((prec_prior*self.state.friction_coef + prec_meas*measured_mu) /
                                     (prec_prior + prec_meas))
        self.state.friction_sigma = 1.0 / math.sqrt(prec_prior + prec_meas)
        self.state.n_updates += 1
        self.state.converged_check()

    def predict_RMS(self, real_x: np.ndarray, twin_x: np.ndarray) -> float:
        return float(np.sqrt(np.mean((real_x - twin_x)**2)))

    def summary(self) -> dict:
        return {
            "tau_A_s":       round(self.state.tau_A_s, 4),
            "tau_A_sigma":   round(self.state.tau_A_sigma, 5),
            "backlash_mm":   round(self.state.backlash_mm, 4),
            "backlash_sigma":round(self.state.backlash_sigma, 5),
            "friction":      round(self.state.friction_coef, 5),
            "friction_sigma":round(self.state.friction_sigma, 6),
            "n_updates":     self.state.n_updates,
            "converged":     self.state.converged,
        }
