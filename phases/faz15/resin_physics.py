"""
resin_physics.py — Resin Flow, Viscosity, Void & Fiber Volume Fraction
=======================================================================
Fizik modelleri:

1. Viskozite: Castro-Macosko modeli
   η(T,α) = η∞·exp(Ea_η/RT)·(αg/(αg-α))^(a+b·α)
   [αg: gel conversion, a,b: empirical fitting]

2. Void fraction (Lundström, 1993):
   Vv = V_gas / (V_gas + V_resin + V_fiber)
   Büyüme: sıkışmış hava + dissolved gas çökmesi
   Kompaksiyon ile azalır: P_comp artarsa Vv düşer

3. Fiber volume fraction:
   Vf = n_layers · A_f / A_total
   Gerçek: kompaksiyon sonrası ölçüm

4. Resin flow (Darcy):
   u_resin = -(k_perm / η) · ∇P
   k_perm: fiber preform permeability [m²]

5. Kompaksiyon basıncı (Laplace formülü):
   P_comp = T_fiber · cos(α_winding) / (r_mandrel · b_tow)
   [N/m²]

6. Tow spreading (geometrik):
   w_actual = w0 · (1 + k_spread · σ_comp / E_fiber)
   σ_comp: transverse compaction stress

7. Shrinkage:
   ε_shrink = ε_chem · α + ε_therm · ΔT
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np

R_GAS = 8.314

# ── Resin Viscosity ───────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ViscosityParams:
    """Castro-Macosko viscosity model parameters."""
    eta_inf:  float   # [Pa·s] infinite temperature viscosity
    Ea_eta:   float   # [J/mol] flow activation energy
    alpha_g:  float   # gelation conversion
    a:        float   # exponent coefficient
    b:        float   # exponent coefficient
    eta_min:  float = 0.1    # [Pa·s] minimum viscosity (fully melted)
    eta_max:  float = 1e8    # [Pa·s] practical gelation limit

# Epoxy typical parameters
EPOXY_VISCOSITY = ViscosityParams(
    eta_inf=1.2e-4, Ea_eta=58_000.0,
    alpha_g=0.62, a=1.5, b=1.2,
)

class ViscosityModel:
    """Castro-Macosko resin viscosity model."""

    def __init__(self, params: ViscosityParams = None):
        self.p = params or EPOXY_VISCOSITY

    def viscosity(self, T_K: float, alpha: float) -> float:
        """
        Resin viscosity η [Pa·s].
        Returns eta_max when alpha ≥ alpha_g (gelation).
        """
        alpha = float(np.clip(alpha, 0.0, self.p.alpha_g - 1e-4))
        if alpha >= self.p.alpha_g - 0.01:
            return self.p.eta_max

        eta_T = self.p.eta_inf * math.exp(self.p.Ea_eta / (R_GAS * max(T_K, 200.0)))
        exp_v = self.p.a + self.p.b * alpha
        gel_f = (self.p.alpha_g / (self.p.alpha_g - alpha)) ** exp_v
        eta   = eta_T * gel_f
        return float(np.clip(eta, self.p.eta_min, self.p.eta_max))

    def gel_pot(self, T_K: float, alpha: float) -> float:
        """Gelation potential [0,1]: how close to gel point."""
        return float(np.clip(alpha / self.p.alpha_g, 0, 1))

    def processing_window(self, T_K_arr: np.ndarray,
                          alpha_arr: np.ndarray,
                          eta_max_process: float = 10.0) -> np.ndarray:
        """Boolean array: True where processable (η < eta_max_process)."""
        return np.array([
            self.viscosity(T, a) < eta_max_process
            for T, a in zip(T_K_arr, alpha_arr)
        ])


# ── Void Fraction ─────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class VoidModel:
    """
    Void content prediction model (Lundström, 1993).
    Voids: trapped air + dissolved gas nucleation.
    Reduction by compaction pressure.
    """
    V0_void: float = 0.025     # Initial void fraction (2.5% — typical wet winding)
    P_ref:   float = 1.0e5    # Reference pressure [Pa] (1 atm)
    Henry_k: float = 3.5e-8   # Henry constant [m³/(Pa·m³)] gas solubility
    r_void:  float = 5e-5     # Initial void radius [m] (50µm typical)
    gamma:   float = 0.040    # Surface tension [N/m] (epoxy)

    def void_fraction(self, P_comp_Pa: float, T_K: float,
                      alpha: float, V_void_prev: float) -> float:
        """
        Evolving void fraction.
        Compaction: P↑ → voids compress (Boyle's law).
        Cure: α↑ → resin stiffens → voids trapped.
        """
        # Boyle's law compression
        if P_comp_Pa > 1e3:
            V_boyle = V_void_prev * self.P_ref / max(P_comp_Pa, 1e3)
        else:
            V_boyle = V_void_prev

        # Surface tension: critical radius r_c = 2γ/P
        r_c = 2.0 * self.gamma / max(P_comp_Pa, 1e3)

        # Post-gel: voids frozen in place
        if alpha > 0.8:
            freeze = min(1.0, (alpha - 0.8) / 0.2)
            V_void = V_boyle * freeze + V_void_prev * (1 - freeze)
        else:
            V_void = V_boyle * (1 - alpha * 0.3)   # cure reduces void mobility

        return float(np.clip(V_void, 0.0, 0.15))

    def initial_void(self, wet_winding_speed_mm_s: float,
                     resin_viscosity_Pa_s: float) -> float:
        """
        Initial void content from wet winding.
        Higher speed & higher viscosity → more air entrapment.
        """
        air_trap = 0.01 + 0.002 * wet_winding_speed_mm_s / 100.0
        visc_pen  = min(0.015, resin_viscosity_Pa_s * 1e-3)
        return float(np.clip(air_trap + visc_pen, 0.005, 0.10))


# ── Fiber Volume Fraction ─────────────────────────────────────────

class FiberVolumeFraction:
    """
    Fiber volume fraction prediction from geometric and process parameters.

    Vf = (n_layers · A_fiber_per_layer) / A_composite_section

    Compaction effect:
      As P_comp increases, laminate compresses:
      Vf_final = Vf_nominal / (1 - ε_comp)
      ε_comp = σ / E_transverse
    """
    def __init__(self, E_transverse_MPa: float = 8.0,
                 A_fiber_per_roving_mm2: float = 0.196):  # 12K carbon @ 6.9µm
        self.E_T = E_transverse_MPa * 1e6   # [Pa]
        self.A_f = A_fiber_per_roving_mm2 * 1e-6   # [m²]

    def Vf_geometric(self, n_rovings: int, b_tow_m: float,
                     t_layer_m: float) -> float:
        """Geometric (nominal) fiber volume fraction."""
        A_fiber   = n_rovings * self.A_f
        A_section = b_tow_m * t_layer_m
        return float(np.clip(A_fiber / max(A_section, 1e-12), 0.0, 0.80))

    def Vf_compacted(self, Vf_nom: float, P_comp_Pa: float) -> float:
        """Compacted Vf after resin squeezeout under pressure."""
        eps_comp = float(np.clip(P_comp_Pa / self.E_T, 0.0, 0.20))
        return float(np.clip(Vf_nom / max(1.0 - eps_comp, 0.80), 0.0, 0.80))

    def target_check(self, Vf: float,
                     Vf_min: float = 0.50, Vf_max: float = 0.70) -> bool:
        return Vf_min <= Vf <= Vf_max


# ── Compaction Pressure ───────────────────────────────────────────

def compaction_pressure_Pa(T_fiber_N: float, alpha_wind_rad: float,
                           r_mandrel_mm: float, b_tow_mm: float) -> float:
    """
    Laplace pressure from fiber tension → compaction.
    P = T·cos(α) / (r_mandrel·b_tow)   [N/m²]

    Derivation: equilibrium of curved fiber on cylindrical surface.
    """
    r_m = r_mandrel_mm / 1000.0
    b_t = b_tow_mm / 1000.0
    P   = T_fiber_N * math.cos(alpha_wind_rad) / (r_m * b_t)
    return max(0.0, P)


# ── Tow Spreading ─────────────────────────────────────────────────

def tow_width_actual_mm(w0_mm: float, sigma_comp_MPa: float,
                        E_fiber_GPa: float = 230.0,
                        k_spread: float = 0.15) -> float:
    """
    Actual tow width after spreading under compaction.
    w = w0 · (1 + k·σ/E)
    """
    spread = 1.0 + k_spread * sigma_comp_MPa / (E_fiber_GPa * 1000.0)
    return float(np.clip(w0_mm * spread, w0_mm * 0.9, w0_mm * 1.5))


# ── Shrinkage ─────────────────────────────────────────────────────

def shrinkage_strain(alpha: float, delta_T_K: float,
                     eps_chem: float = 0.003,
                     CTE_resin: float = 65e-6) -> float:
    """
    Total linear shrinkage strain.
    ε = α·ε_chem + ΔT·CTE
    """
    return alpha * eps_chem + delta_T_K * CTE_resin


# ── Darcy Flow ────────────────────────────────────────────────────

def darcy_flow_speed_m_s(k_perm_m2: float, viscosity_Pa_s: float,
                          dP_dx_Pa_m: float) -> float:
    """Darcy's law: u = -(k/η)·∇P."""
    return abs(k_perm_m2 * dP_dx_Pa_m / max(viscosity_Pa_s, 1e-6))
