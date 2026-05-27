"""
anomaly_cluster_engine.py — Unsupervised Anomaly Clustering
==============================================================
Uses simple k-medoids-like clustering on fingerprint vectors.
No scikit-learn dependency — bounded memory, deterministic.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple
import numpy as np

@dataclass(frozen=True, slots=True)
class AnomalyCluster:
    cluster_id:   int
    n_members:    int
    centroid:     np.ndarray
    avg_distance: float
    is_outlier:   bool

class AnomalyClusterEngine:
    """Online clustering of process fingerprints."""
    def __init__(self, k: int = 3, threshold: float = 3.0, seed: int = 42):
        self.k = k
        self.threshold = threshold
        self._rng = np.random.default_rng(seed)
        self._data: List[np.ndarray] = []
        self._labels: List[int] = []
        self._centroids: Optional[np.ndarray] = None

    def add(self, vector: np.ndarray) -> int:
        self._data.append(vector.copy())
        if len(self._data) < self.k:
            self._labels.append(-1)
            return -1
        if self._centroids is None:
            # Initial: pick k random points as centroids
            idx = self._rng.choice(len(self._data), self.k, replace=False)
            self._centroids = np.array([self._data[i] for i in idx])
        # Assign to nearest centroid
        d = [np.linalg.norm(vector - c) for c in self._centroids]
        lab = int(np.argmin(d))
        self._labels.append(lab)
        # Update centroid (moving average)
        self._centroids[lab] = 0.95*self._centroids[lab] + 0.05*vector
        return lab

    def is_anomaly(self, vector: np.ndarray) -> bool:
        """True if vector is far from all centroids."""
        if self._centroids is None or len(self._centroids) == 0: return False
        min_d = min(np.linalg.norm(vector - c) for c in self._centroids)
        # Compute typical distance scale
        if len(self._data) > 10:
            sample_d = [np.linalg.norm(self._data[i]-self._centroids[self._labels[i]])
                        for i in range(max(0,len(self._data)-50), len(self._data))
                        if self._labels[i] >= 0]
            if sample_d:
                scale = float(np.std(sample_d)) + 1e-6
                return min_d > self.threshold * scale
        return False

    def cluster_report(self) -> List[AnomalyCluster]:
        if self._centroids is None: return []
        out = []
        for cid in range(self.k):
            members = [i for i,l in enumerate(self._labels) if l == cid]
            if not members: continue
            mem_arr = np.array([self._data[i] for i in members])
            avg_d = float(np.mean([np.linalg.norm(m - self._centroids[cid]) for m in mem_arr]))
            out.append(AnomalyCluster(
                cluster_id=cid, n_members=len(members),
                centroid=self._centroids[cid],
                avg_distance=avg_d, is_outlier=len(members) < 3))
        return out
