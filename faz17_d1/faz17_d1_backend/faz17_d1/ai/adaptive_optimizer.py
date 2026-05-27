"""
ai/adaptive_optimizer.py — RLS-based Feed/Tension Optimizer (Advisory)
========================================================================
Advisory-only. Suggestions go through SafetyValidator before applying.

Cost model:  J(v) = a + b*v + c*v²  (quality cost)
Optimal:    v* = -b / (2c)
RLS update: θ_k+1 = θ_k + K_k × (y_k - φ_kᵀ θ_k)
"""
from __future__ import annotations
import math, threading
from dataclasses import dataclass
from typing import Optional
import numpy as np

V_MIN_MM_S = 30.0
V_MAX_MM_S = 140.0
DELTA_V_MAX = 5.0   # max change per advisory


@dataclass(slots=True)
class FeedSuggestion:
    feed_mm_s:        float
    expected_quality: float
    confidence:       float
    reason:           str
    accepted:         bool = False   # set by SafetyValidator


class AdaptiveFeedOptimizer:
    """
    RLS quadratic cost model. Advisory only.
    """
    def __init__(self, lambda_forget: float = 0.95):
        self._theta = np.array([90.0, -1.0, 0.005])   # [a, b, c]
        self._P = np.eye(3) * 100.0
        self._lambda = lambda_forget
        self._lock = threading.Lock()
        self._n_updates = 0
        self._last_v = 80.0

    def add_observation(self, feed_v: float, quality: float) -> None:
        """Update RLS with (v, quality) observation."""
        phi = np.array([1.0, feed_v, feed_v**2])
        with self._lock:
            K = self._P @ phi / (self._lambda + phi @ self._P @ phi)
            err = quality - phi @ self._theta
            self._theta = self._theta + K * err
            self._P = (self._P - np.outer(K, phi) @ self._P) / self._lambda
            self._n_updates += 1

    def suggest(self, current_v: float) -> FeedSuggestion:
        """Returns suggested feed within DELTA_V_MAX bound."""
        with self._lock:
            a, b, c = self._theta.tolist()
            n = self._n_updates
        if abs(c) < 1e-6 or n < 5:
            return FeedSuggestion(
                feed_mm_s=current_v, expected_quality=0.0,
                confidence=0.0, reason="insufficient data")
        v_star = -b / (2 * c)
        v_star = max(V_MIN_MM_S, min(V_MAX_MM_S, v_star))
        # Limit step size
        v_suggested = current_v + max(-DELTA_V_MAX, min(DELTA_V_MAX, v_star - current_v))
        v_suggested = max(V_MIN_MM_S, min(V_MAX_MM_S, v_suggested))
        expected_q = a + b * v_suggested + c * v_suggested ** 2
        confidence = min(1.0, n / 50.0)
        return FeedSuggestion(
            feed_mm_s=float(v_suggested),
            expected_quality=float(expected_q),
            confidence=float(confidence),
            reason=f"RLS v*={v_star:.1f} step={v_suggested-current_v:+.1f}")

    @property
    def n_updates(self) -> int:
        with self._lock: return self._n_updates
