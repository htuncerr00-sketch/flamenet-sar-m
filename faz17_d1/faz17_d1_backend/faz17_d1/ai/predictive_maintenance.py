"""
ai/predictive_maintenance.py — Component RUL Estimator (Advisory)
======================================================================
"""
from __future__ import annotations
import math, threading
from dataclasses import dataclass
from typing import Dict, List
import numpy as np


@dataclass(slots=True)
class ComponentRUL:
    name:    str
    runtime_h: float
    MTTF_h:  float
    damage:  float    # 0..1

    @property
    def RUL_h(self) -> float:
        return max(0.0, self.MTTF_h * (1 - self.damage))

    @property
    def health_pct(self) -> float:
        return max(0.0, 100.0 * (1 - self.damage))


class PredictiveMaintenance:
    """Miner's-rule damage accumulator. Advisory only."""
    def __init__(self):
        self._comps: Dict[str, ComponentRUL] = {
            "X_bearing":   ComponentRUL("X_bearing",   0, 5000, 0),
            "X_motor":     ComponentRUL("X_motor",     0, 20000, 0),
            "A_belt":      ComponentRUL("A_belt",      0, 8000, 0),
            "fiber_guide": ComponentRUL("fiber_guide", 0, 2000, 0),
            "lead_screw":  ComponentRUL("lead_screw",  0, 5000, 0),
        }
        self._lock = threading.Lock()

    def step(self, dt_h: float, vib_rms: float, temp_C: float,
             current_A: float) -> None:
        with self._lock:
            vib_factor = max(0.1, (vib_rms / 0.5) ** 1.5) if vib_rms > 0 else 0.1
            temp_factor = max(1.0, (temp_C / 60.0) ** 1.0)
            load_factor = max(0.5, (current_A / 8.0) ** 0.8)
            for c in self._comps.values():
                c.runtime_h += dt_h
                dD = dt_h / c.MTTF_h * vib_factor * temp_factor * load_factor
                c.damage = float(np.clip(c.damage + dD, 0, 1))

    def system_health_pct(self) -> float:
        with self._lock:
            return float(np.mean([c.health_pct for c in self._comps.values()]))

    def components(self) -> List[ComponentRUL]:
        with self._lock:
            return [ComponentRUL(c.name, c.runtime_h, c.MTTF_h, c.damage)
                    for c in self._comps.values()]

    def min_RUL_h(self) -> float:
        with self._lock:
            return min(c.RUL_h for c in self._comps.values())
