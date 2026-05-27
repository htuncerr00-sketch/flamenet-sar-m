"""
ai/anomaly_detector.py — CUSUM + Vibration Anomaly (Advisory)
=================================================================
"""
from __future__ import annotations
import math, threading, time
from collections import deque
from dataclasses import dataclass
from typing import List, Optional
import numpy as np

@dataclass(slots=True)
class AnomalyAlert:
    code:      str
    msg:       str
    severity:  int      # 1=info, 2=warn, 3=critical
    value:     float
    threshold: float
    timestamp: float


class AnomalyDetector:
    """CUSUM + threshold-based anomaly detection. Advisory only."""
    def __init__(self, T_target: float = 15.0, T_sigma: float = 0.5,
                 vib_threshold_g: float = 0.5, cusum_h: float = 5.0):
        self._T_target = T_target; self._T_sigma = T_sigma
        self._vib_thresh = vib_threshold_g; self._cusum_h = cusum_h
        self._cusum_pos = 0.0; self._cusum_neg = 0.0
        self._buf: deque = deque(maxlen=200)
        self._lock = threading.Lock()
        self._n_alerts = 0

    def feed(self, T_N: float, vib_x: float, vib_y: float,
             vib_z: float) -> Optional[AnomalyAlert]:
        with self._lock:
            self._buf.append((T_N, vib_x, vib_y, vib_z))
            # CUSUM on tension
            z = (T_N - self._T_target) / max(self._T_sigma, 1e-6)
            self._cusum_pos = max(0.0, self._cusum_pos + z - 0.5)
            self._cusum_neg = max(0.0, self._cusum_neg - z - 0.5)
            if self._cusum_pos > self._cusum_h:
                self._n_alerts += 1
                self._cusum_pos = 0.0
                return AnomalyAlert("TENSION_DRIFT_HIGH",
                    f"Tension drifting high (CUSUM={self._cusum_pos:.2f})",
                    2, T_N, self._T_target + 3*self._T_sigma, time.time())
            if self._cusum_neg > self._cusum_h:
                self._n_alerts += 1
                self._cusum_neg = 0.0
                return AnomalyAlert("TENSION_DRIFT_LOW",
                    f"Tension drifting low (CUSUM={self._cusum_neg:.2f})",
                    2, T_N, self._T_target - 3*self._T_sigma, time.time())
            # Vibration threshold
            vib_rms = math.sqrt(vib_x**2 + vib_y**2 + vib_z**2)
            if vib_rms > self._vib_thresh:
                self._n_alerts += 1
                return AnomalyAlert("VIB_HIGH",
                    f"Vibration RMS={vib_rms:.3f}g > {self._vib_thresh}g",
                    2, vib_rms, self._vib_thresh, time.time())
        return None

    @property
    def n_alerts(self) -> int:
        with self._lock: return self._n_alerts
