"""
production_health_score.py — Composite Production Health [0-100]
==================================================================
Aggregates:
  - Twin correlation (drift)    × 0.20
  - Process capability (Cpk)    × 0.20
  - Equipment health (RUL)      × 0.20
  - Quality score (real-time)   × 0.20
  - Process fingerprint consistency × 0.10
  - Anomaly score (inverted)    × 0.10
"""
from __future__ import annotations
from dataclasses import dataclass
import math

@dataclass(slots=True)
class HealthScoreInputs:
    twin_rms_x_mm:     float = 0.05
    twin_rms_T_N:      float = 0.5
    Cpk_void:          float = 1.33
    Cpk_Vf:            float = 1.33
    equipment_health:  float = 95.0    # 0-100
    quality_mean:      float = 92.0
    fp_similarity:     float = 0.98
    anomaly_score:     float = 0.5    # 0=normal, >3=anomaly

class ProductionHealthScore:
    WEIGHTS = {"twin":0.20,"capability":0.20,"equipment":0.20,
               "quality":0.20,"fingerprint":0.10,"anomaly":0.10}

    def __init__(self):
        pass

    def compute(self, inp: HealthScoreInputs) -> float:
        # Sub-scores [0,100]
        s_twin = max(0.0, 100.0 * (1.0 - inp.twin_rms_x_mm/0.5))
        s_cap  = min(100.0, 100.0 * min(inp.Cpk_void, inp.Cpk_Vf) / 1.33)
        s_eq   = inp.equipment_health
        s_q    = inp.quality_mean
        s_fp   = max(0.0, 100.0 * inp.fp_similarity)
        s_an   = max(0.0, 100.0 * (1.0 - inp.anomaly_score/5.0))

        score = (self.WEIGHTS["twin"]*s_twin +
                 self.WEIGHTS["capability"]*s_cap +
                 self.WEIGHTS["equipment"]*s_eq +
                 self.WEIGHTS["quality"]*s_q +
                 self.WEIGHTS["fingerprint"]*s_fp +
                 self.WEIGHTS["anomaly"]*s_an)
        return max(0.0, min(100.0, score))

    def breakdown(self, inp: HealthScoreInputs) -> dict:
        s_twin = max(0.0, 100.0 * (1.0 - inp.twin_rms_x_mm/0.5))
        s_cap  = min(100.0, 100.0 * min(inp.Cpk_void, inp.Cpk_Vf) / 1.33)
        s_eq   = inp.equipment_health
        s_q    = inp.quality_mean
        s_fp   = max(0.0, 100.0 * inp.fp_similarity)
        s_an   = max(0.0, 100.0 * (1.0 - inp.anomaly_score/5.0))
        return {"twin":round(s_twin,2),"capability":round(s_cap,2),
                "equipment":round(s_eq,2),"quality":round(s_q,2),
                "fingerprint":round(s_fp,2),"anomaly":round(s_an,2)}
