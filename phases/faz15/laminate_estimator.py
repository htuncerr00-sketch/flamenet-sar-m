"""
laminate_estimator.py — Laminate Quality Estimator (Online, Real-Time)
========================================================================
Anlık tahmin: kalınlık, Vf, boşluk %, geçiş kalitesi.

Thickness model:
  t_lam(n) = n·t_layer_dry·(1 + Vr/Vf)   [per-layer accumulation]
  t_layer_dry = A_fiber / (b_tow·Vf_nom)

Mandrel thermal expansion:
  ΔD_mandrel = D0·CTE_mandrel·ΔT
  → effective radius: r_eff = r0 + ΔD/2
  → fiber path correction: c_eff = r_eff·sin(α)  (Clairaut)

Fiber slip detection:
  slip = (α_measured - α_commanded) > threshold
  Physical cause: fiber moves off geodesic → coverage error

Gap/overlap from winding pattern:
  gap    = max(0, b_tow·cos(α) - pitch)   [mm/layer]
  overlap= max(0, pitch - b_tow·cos(α))
  Coverage ratio ρ = b_tow·cos(α) / pitch
"""
from __future__ import annotations
import math, threading
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import numpy as np

from resin_physics import (VoidModel, FiberVolumeFraction,
                            compaction_pressure_Pa, tow_width_actual_mm)


# ── Layer Quality Frame ───────────────────────────────────────────

@dataclass(slots=True)
class LayerQuality:
    layer_idx:      int
    t_layer_mm:     float   # Actual layer thickness [mm]
    Vf:             float   # Fiber volume fraction
    Vv:             float   # Void fraction
    gap_mm:         float   # Gap per layer [mm]
    overlap_mm:     float   # Overlap per layer [mm]
    coverage_ratio: float   # ρ = actual/ideal [0,2]
    P_comp_MPa:     float   # Compaction pressure [MPa]
    slip_deg:       float   # Fiber slip angle error [°]
    quality_score:  float   # [0,100]
    grade:          str     # A+/A/B/C/D

    @property
    def void_pct(self) -> float: return self.Vv * 100.0
    @property
    def acceptable(self) -> bool: return self.quality_score >= 70.0


# ── Laminate Accumulator ──────────────────────────────────────────

class LaminateEstimator:
    """
    Per-layer laminate quality estimation.
    Thread-safe, non-blocking, bounded memory.

    Kullanım:
        est = LaminateEstimator(mandrel_R=50.0, ...)
        for each layer:
            result = est.estimate_layer(tension, alpha_cmd, alpha_meas,
                                        T_K, cure_alpha, ...)
    """
    MAX_HISTORY = 100   # Bounded memory: max 100 layers tracked

    # Fiber properties (12K carbon)
    A_FIBER_MM2   = 0.196   # Roving cross-section [mm²] (12K @ 6.9µm)
    E_FIBER_GPA   = 230.0   # Axial stiffness
    CTE_STEEL     = 11.7e-6 # Mandrel CTE [1/°C]
    CTE_COMP      = 2.0e-6  # Composite CTE (axial) [1/°C]

    def __init__(
        self,
        mandrel_R_mm:     float = 50.0,
        alpha_wind_deg:   float = 10.17,
        b_tow_mm:         float = 10.0,
        n_rovings:        int   = 1,
        Vf_nominal:       float = 0.55,
        T_ref_C:          float = 20.0,
    ):
        self.r    = mandrel_R_mm
        self.α    = math.radians(alpha_wind_deg)
        self.b0   = b_tow_mm
        self.n_rov= n_rovings
        self.Vf_nom = Vf_nominal
        self.T_ref_K= T_ref_C + 273.15

        self._void_model = VoidModel()
        self._Vf_model   = FiberVolumeFraction()
        self._history: deque = deque(maxlen=self.MAX_HISTORY)
        self._n_layers   = 0
        self._t_total_mm = 0.0
        self._Vv_running = 0.025   # Running void estimate
        self._lock       = threading.Lock()

    # ── Per-layer estimation ──────────────────────────────────────

    def estimate_layer(
        self,
        tension_N:      float,
        alpha_cmd_deg:  float,
        alpha_meas_deg: float,
        T_K:            float,
        cure_alpha:     float,
        pitch_mm:       float = 14.0,   # Feed per circuit [mm/circuit]
        T_ambient_K:    float = 293.15,
        T_mandrel_K:    float = 293.15,
    ) -> LayerQuality:
        """Estimate quality metrics for one layer pass."""
        # Compaction pressure
        P_Pa = compaction_pressure_Pa(tension_N, self.α, self._r_eff(T_K), self.b0)
        P_MPa= P_Pa / 1e6

        # Actual tow width (spreading under compaction)
        sigma_comp = P_MPa
        b_actual = tow_width_actual_mm(self.b0, sigma_comp, self.E_FIBER_GPA)

        # Gap/overlap from coverage ratio
        projected_w = b_actual * math.cos(self.α)
        gap_mm      = max(0.0, projected_w - pitch_mm)
        overlap_mm  = max(0.0, pitch_mm - projected_w)
        coverage_r  = projected_w / max(pitch_mm, 1e-6)

        # Void fraction (updated)
        visc = 0.5   # Pa·s — simplified (would come from resin model)
        V0   = self._void_model.initial_void(100.0, visc)
        Vv   = self._void_model.void_fraction(P_Pa, T_K, cure_alpha, self._Vv_running)
        self._Vv_running = Vv

        # Fiber volume fraction
        t_layer_dry = self.A_FIBER_MM2 * self.n_rov / (self.b0 * self.Vf_nom)
        Vf_geom = self._Vf_model.Vf_geometric(self.n_rov, b_actual/1000, t_layer_dry/1000)
        Vf      = self._Vf_model.Vf_compacted(Vf_geom, P_Pa)

        # Layer thickness (actual)
        t_layer_mm = t_layer_dry * (1.0 - Vf + Vv)

        # Fiber slip
        slip_deg = abs(alpha_meas_deg - alpha_cmd_deg)

        # Mandrel thermal expansion correction
        r_eff  = self._r_eff(T_K)
        dr_mm  = r_eff - self.r

        # Quality score
        q = self._quality_score(Vf, Vv, coverage_r, slip_deg, P_MPa)
        grade = "A+" if q>=90 else "A" if q>=80 else "B" if q>=70 else "C" if q>=60 else "D"

        result = LayerQuality(
            layer_idx=self._n_layers, t_layer_mm=t_layer_mm,
            Vf=Vf, Vv=Vv, gap_mm=gap_mm, overlap_mm=overlap_mm,
            coverage_ratio=coverage_r, P_comp_MPa=P_MPa,
            slip_deg=slip_deg, quality_score=q, grade=grade,
        )
        with self._lock:
            self._history.append(result)
            self._n_layers += 1
            self._t_total_mm += t_layer_mm
        return result

    # ── Mandrel thermal expansion ─────────────────────────────────

    def _r_eff(self, T_K: float) -> float:
        """Effective mandrel radius with thermal expansion."""
        dT = T_K - self.T_ref_K
        return self.r * (1.0 + self.CTE_STEEL * dT)

    # ── Fiber slip detection ───────────────────────────────────────

    def is_slip(self, alpha_cmd_deg: float, alpha_meas_deg: float,
                tol_deg: float = 1.5) -> bool:
        return abs(alpha_meas_deg - alpha_cmd_deg) > tol_deg

    # ── Quality score ─────────────────────────────────────────────

    def _quality_score(self, Vf: float, Vv: float, cov: float,
                       slip_deg: float, P_MPa: float) -> float:
        s_Vf  = max(0.0, 100.0 - abs(Vf - self.Vf_nom) * 500.0)
        s_Vv  = max(0.0, 100.0 - Vv * 2000.0)  # 5% void → 0 score
        s_cov = max(0.0, 100.0 - abs(cov - 1.0) * 200.0)
        s_slip= max(0.0, 100.0 - slip_deg * 20.0)
        s_P   = 100.0 if 0.1 <= P_MPa <= 5.0 else max(0.0, 70.0 - abs(P_MPa-1.0)*10)
        return (0.30*s_Vf + 0.25*s_Vv + 0.20*s_cov + 0.15*s_slip + 0.10*s_P)

    # ── Summary ───────────────────────────────────────────────────

    def summary(self) -> dict:
        with self._lock:
            hist = list(self._history)
        if not hist: return {"n_layers": 0}
        Vf_arr = np.array([h.Vf  for h in hist])
        Vv_arr = np.array([h.Vv  for h in hist])
        q_arr  = np.array([h.quality_score for h in hist])
        t_arr  = np.array([h.t_layer_mm for h in hist])
        return {
            "n_layers":      self._n_layers,
            "t_total_mm":    round(float(t_arr.sum()), 4),
            "Vf_mean":       round(float(Vf_arr.mean()), 4),
            "Vf_std":        round(float(Vf_arr.std()), 4),
            "Vv_mean":       round(float(Vv_arr.mean()), 5),
            "Vv_max":        round(float(Vv_arr.max()), 5),
            "q_mean":        round(float(q_arr.mean()), 2),
            "q_min":         round(float(q_arr.min()), 2),
            "void_pct_mean": round(float(Vv_arr.mean())*100, 3),
        }

    @property
    def n_layers(self) -> int: return self._n_layers
    @property
    def total_thickness_mm(self) -> float: return self._t_total_mm
