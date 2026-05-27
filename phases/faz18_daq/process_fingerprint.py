"""
process_fingerprint.py — Process Signature Extraction
========================================================
FP vector = [RPM, Tension_mean, Tension_std, Vib_RMS, Current_mean,
             Thermal_grad, Cure_rate, Quality_mean]

Used for:
  - Lot-to-lot consistency (cosine similarity)
  - Operator-independent repeatability
  - Anomaly detection (Mahalanobis distance)
"""
from __future__ import annotations
import math
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import numpy as np

FP_DIM = 8

@dataclass(slots=True)
class ProcessFingerprint:
    rpm_mean:     float = 0.0
    T_mean:       float = 0.0
    T_std:        float = 0.0
    vib_rms:      float = 0.0
    current_mean: float = 0.0
    thermal_grad: float = 0.0
    cure_rate:    float = 0.0
    quality_mean: float = 0.0
    n_samples:    int   = 0
    timestamp:    float = 0.0

    def to_vector(self) -> np.ndarray:
        return np.array([self.rpm_mean, self.T_mean, self.T_std,
                          self.vib_rms, self.current_mean,
                          self.thermal_grad, self.cure_rate, self.quality_mean])

    def similarity(self, other: "ProcessFingerprint") -> float:
        """Cosine similarity [-1, 1]."""
        a = self.to_vector(); b = other.to_vector()
        n_a = np.linalg.norm(a); n_b = np.linalg.norm(b)
        if n_a < 1e-9 or n_b < 1e-9: return 0.0
        return float(np.dot(a, b) / (n_a * n_b))

    def mahalanobis(self, mean: np.ndarray, inv_cov: np.ndarray) -> float:
        """Mahalanobis distance from reference distribution."""
        d = self.to_vector() - mean
        return float(math.sqrt(max(0.0, d @ inv_cov @ d)))


class FingerprintExtractor:
    """Build fingerprint from telemetry samples."""
    def extract(self, samples: list) -> ProcessFingerprint:
        if not samples:
            return ProcessFingerprint()
        rpm_arr  = np.array([s.rpm for s in samples])
        T_arr    = np.array([s.T_N for s in samples])
        vx_arr   = np.array([s.vib_x for s in samples])
        vy_arr   = np.array([s.vib_y for s in samples])
        vz_arr   = np.array([s.vib_z for s in samples])
        cur_arr  = np.array([s.current_A for s in samples])
        tmp_arr  = np.array([s.temp_K for s in samples])
        alp_arr  = np.array([s.alpha for s in samples])
        q_arr    = np.array([s.quality for s in samples])
        # Vibration RMS (3-axis combined)
        vib_rms  = float(np.sqrt(np.mean(vx_arr**2 + vy_arr**2 + vz_arr**2)))
        # Thermal gradient (temporal)
        if len(tmp_arr) > 2:
            grad = float(np.abs(np.diff(tmp_arr)).mean())
        else:
            grad = 0.0
        # Cure rate
        if len(alp_arr) > 2:
            rate = float((alp_arr[-1] - alp_arr[0]) / max(len(alp_arr), 1))
        else:
            rate = 0.0
        import time as _t
        return ProcessFingerprint(
            rpm_mean=float(rpm_arr.mean()),
            T_mean=float(T_arr.mean()),
            T_std=float(T_arr.std()),
            vib_rms=vib_rms,
            current_mean=float(cur_arr.mean()),
            thermal_grad=grad,
            cure_rate=rate,
            quality_mean=float(q_arr.mean()),
            n_samples=len(samples),
            timestamp=_t.time(),
        )


class FingerprintLibrary:
    """Reference fingerprint library for similarity / anomaly."""
    def __init__(self, max_size: int = 100):
        self._lib: List[ProcessFingerprint] = []
        self._max = max_size
        self._mean: Optional[np.ndarray] = None
        self._inv_cov: Optional[np.ndarray] = None

    def add_reference(self, fp: ProcessFingerprint) -> None:
        self._lib.append(fp)
        if len(self._lib) > self._max: self._lib.pop(0)
        if len(self._lib) >= 5: self._update_stats()

    def _update_stats(self) -> None:
        arr = np.array([fp.to_vector() for fp in self._lib])
        self._mean = arr.mean(axis=0)
        cov = np.cov(arr.T) + np.eye(FP_DIM)*1e-6
        try:
            self._inv_cov = np.linalg.inv(cov)
        except np.linalg.LinAlgError:
            self._inv_cov = np.eye(FP_DIM)

    def anomaly_score(self, fp: ProcessFingerprint) -> float:
        """Mahalanobis distance — >3 = anomaly."""
        if self._mean is None or self._inv_cov is None: return 0.0
        return fp.mahalanobis(self._mean, self._inv_cov)

    def best_match(self, fp: ProcessFingerprint) -> Tuple[Optional[int], float]:
        """Find most similar reference; returns (idx, similarity)."""
        if not self._lib: return None, 0.0
        sims = [fp.similarity(ref) for ref in self._lib]
        idx = int(np.argmax(sims))
        return idx, sims[idx]
