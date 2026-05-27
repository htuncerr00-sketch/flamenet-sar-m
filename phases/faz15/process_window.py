"""process_window.py — Process Window Validator"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple
import numpy as np

@dataclass(frozen=True, slots=True)
class ProcessWindow:
    T_min_C:      float = 80.0
    T_max_C:      float = 140.0
    alpha_flow_max:float= 0.58   # Max cure where resin still flows
    eta_max_Pa_s: float = 10.0   # Max processable viscosity
    void_max:     float = 0.05   # 5% max void
    P_comp_min_MPa:float= 0.05
    P_comp_max_MPa:float= 2.0

    def check(self, T_C: float, alpha: float, eta_Pa_s: float,
              void_frac: float, P_MPa: float) -> Tuple[bool, str]:
        if T_C < self.T_min_C: return False, f"T={T_C:.1f}<{self.T_min_C}°C"
        if T_C > self.T_max_C: return False, f"T={T_C:.1f}>{self.T_max_C}°C"
        if alpha > self.alpha_flow_max: return False, f"α={alpha:.3f}>{self.alpha_flow_max} (gelated)"
        if eta_Pa_s > self.eta_max_Pa_s: return False, f"η={eta_Pa_s:.1f}>{self.eta_max_Pa_s}Pa·s"
        if void_frac > self.void_max: return False, f"Vv={void_frac*100:.1f}%>{self.void_max*100:.1f}%"
        if P_MPa < self.P_comp_min_MPa: return False, f"P={P_MPa:.3f}<{self.P_comp_min_MPa}MPa"
        return True, "OK"

    def processing_fraction(self, T_arr, alpha_arr, eta_arr) -> float:
        """Fraction of time within process window."""
        n_ok = sum(1 for T,a,e in zip(T_arr,alpha_arr,eta_arr)
                   if self.T_min_C<=T<=self.T_max_C and a<self.alpha_flow_max and e<self.eta_max_Pa_s)
        return n_ok/max(len(T_arr),1)
