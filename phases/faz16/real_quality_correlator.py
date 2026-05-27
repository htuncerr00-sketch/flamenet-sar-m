"""real_quality_correlator.py — Quality Correlation Engine"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List, Optional
import numpy as np
from telemetry_recorder import TelemetrySample

@dataclass(slots=True)
class QualityCorrelation:
    n_samples:       int
    measured_q_mean: float
    predicted_q_mean:float
    correlation_r:   float
    rmse:            float
    production_score:float
    grade:           str

class RealQualityCorrelator:
    def __init__(self, twin_accuracy_weight:float=0.30,
                 process_stability_weight:float=0.40,
                 quality_score_weight:float=0.30):
        self.w_twin=twin_accuracy_weight; self.w_stab=process_stability_weight
        self.w_qual=quality_score_weight

    def correlate(self, samples:List[TelemetrySample], twin_quality:np.ndarray,
                  twin_accuracy:float, drift_stable:bool) -> QualityCorrelation:
        if not samples:
            return QualityCorrelation(0,0,0,0,0,0,"D")
        n = min(len(samples), len(twin_quality))
        meas_q = np.array([s.quality for s in samples[:n]])
        pred_q = twin_quality[:n]
        corr_r = float(np.corrcoef(meas_q, pred_q)[0,1]) if n>2 else 0.0
        rmse   = float(np.sqrt(np.mean((meas_q-pred_q)**2)))
        T_arr  = np.array([s.tension_N for s in samples[:n]])
        T_cv   = float(T_arr.std()/max(T_arr.mean(),1.0))
        stab   = max(0.0, 1.0-T_cv*3.0-(0 if drift_stable else 0.2))
        score  = float(np.clip((self.w_twin*min(1,twin_accuracy/100)+
                  self.w_stab*stab+self.w_qual*min(1,meas_q.mean()/100))*100,0,100))
        grade  = "A+" if score>=90 else "A" if score>=80 else "B" if score>=70 else "C" if score>=60 else "D"
        return QualityCorrelation(n_samples=n, measured_q_mean=float(meas_q.mean()),
            predicted_q_mean=float(pred_q.mean()), correlation_r=corr_r,
            rmse=rmse, production_score=score, grade=grade)
