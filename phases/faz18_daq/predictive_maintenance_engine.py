"""
predictive_maintenance_engine.py — RUL Estimator (Remaining Useful Life)
==========================================================================
Multi-component prognostic model:
  RUL(t) = MTTF - cumulative_damage(t)

Damage model:
  D(t) = ∫₀ᵗ (vib_rms(τ)/V_ref)^a × (T(τ)/T_ref)^b × (load(τ)/L_ref)^c × dτ

Inputs from fingerprint, vibration FFT, thermocouple, current.
"""
from __future__ import annotations
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import numpy as np

@dataclass(slots=True)
class ComponentHealth:
    name:          str
    runtime_h:     float = 0.0
    MTTF_h:        float = 8760.0   # 1 year nominal
    damage:        float = 0.0      # 0=new, 1=failure
    vibration_rms: float = 0.0
    thermal_C:     float = 22.0
    load_pct:      float = 0.0      # 0-100% of rated

    @property
    def RUL_h(self) -> float:
        """Remaining Useful Life [hours]."""
        return max(0.0, self.MTTF_h * (1.0 - self.damage))

    @property
    def health_pct(self) -> float:
        return max(0.0, 100.0 * (1.0 - self.damage))


@dataclass(frozen=True, slots=True)
class MaintenanceAction:
    component:  str
    action:     str     # "inspect","lubricate","replace","calibrate"
    priority:   str     # "immediate","scheduled","preventive"
    due_h:      float   # Hours until action due
    reason:     str


class PredictiveMaintenanceEngine:
    """Production RUL/maintenance predictor."""
    # Acceleration factors per damage mode (Miner's rule)
    VIB_REF = 0.5   # g
    T_REF = 60.0    # °C
    L_REF = 100.0   # % load

    def __init__(self):
        self._components: Dict[str, ComponentHealth] = {
            "X_bearing":       ComponentHealth("X_bearing", MTTF_h=5000),
            "X_motor":         ComponentHealth("X_motor", MTTF_h=20000),
            "A_motor":         ComponentHealth("A_motor", MTTF_h=20000),
            "A_belt":          ComponentHealth("A_belt", MTTF_h=8000),
            "encoder_X":       ComponentHealth("encoder_X", MTTF_h=15000),
            "load_cell":       ComponentHealth("load_cell", MTTF_h=30000),
            "fiber_guide":     ComponentHealth("fiber_guide", MTTF_h=2000),
            "lead_screw":      ComponentHealth("lead_screw", MTTF_h=5000),
        }
        self._history: List[Dict] = []

    def step(self, dt_h: float, vib_rms: float, temp_C: float,
             current_A: float) -> None:
        """Update damage based on operating conditions (1h interval)."""
        load_pct = min(100.0, current_A / 8.0 * 100.0)   # 8A rated
        for comp in self._components.values():
            comp.runtime_h += dt_h
            comp.vibration_rms = vib_rms
            comp.thermal_C = temp_C
            comp.load_pct = load_pct
            # Damage increment (multiplicative model)
            vib_factor = (vib_rms / self.VIB_REF) ** 1.5 if vib_rms > 0 else 0.1
            temp_factor= max(1.0, (temp_C / self.T_REF) ** 1.0)
            load_factor= max(0.5, (load_pct / self.L_REF) ** 0.8)
            dD = dt_h / comp.MTTF_h * vib_factor * temp_factor * load_factor
            comp.damage = float(np.clip(comp.damage + dD, 0.0, 1.0))

    def get_health(self) -> Dict[str, ComponentHealth]:
        return self._components.copy()

    def get_actions(self) -> List[MaintenanceAction]:
        """Generate maintenance actions based on current state."""
        actions = []
        for comp in self._components.values():
            if comp.RUL_h < 24:
                actions.append(MaintenanceAction(comp.name, "replace",
                    "immediate", comp.RUL_h, f"RUL={comp.RUL_h:.1f}h<24h"))
            elif comp.RUL_h < 168:
                actions.append(MaintenanceAction(comp.name, "inspect",
                    "scheduled", comp.RUL_h, f"RUL={comp.RUL_h:.0f}h<1week"))
            elif comp.damage > 0.5 and comp.vibration_rms > 0.3:
                actions.append(MaintenanceAction(comp.name, "lubricate",
                    "preventive", 1000, "High vibration"))
        return actions

    def system_health(self) -> float:
        """Overall system health [0,100]."""
        if not self._components: return 100.0
        return float(np.mean([c.health_pct for c in self._components.values()]))

    def report(self) -> str:
        lines = [f"  Predictive Maintenance Report:"]
        for name, c in sorted(self._components.items()):
            icon = "✓" if c.RUL_h > 1000 else ("⚠" if c.RUL_h > 100 else "✗")
            lines.append(f"    {icon} {name:<18} RUL={c.RUL_h:7.0f}h  "
                         f"health={c.health_pct:5.1f}%  damage={c.damage:.4f}")
        lines.append(f"  System health: {self.system_health():.1f}%")
        return "\n".join(lines)


# ── production_health_score.py ───────────────────────────────────
