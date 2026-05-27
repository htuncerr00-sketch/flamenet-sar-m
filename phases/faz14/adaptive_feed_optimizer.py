"""
adaptive_feed_optimizer.py — Recursive Least Squares Feed Optimizer
====================================================================
Gerçek winding verisiyle online feed rate optimizasyonu.

Algoritma: RLS (Recursive Least Squares) ile sistem tanıma.
  Model: quality_score = θ₀ + θ₁·v + θ₂·v² + θ₃·T + θ₄·κ
  θ vektörü anlık güncellenir (forgetting factor λ=0.98).
  Optimal v: d(quality)/dv = 0 → v* = -θ₁/(2θ₂) [bounded]

Neden RLS?
  - Deterministik: aynı veri → aynı sonuç
  - Real-time: O(n²) güncellem, n=5 özellik → microsecond
  - Convergence guarantee: λ<1 ile eski veri unutulur
  - No GPU, no framework: pure NumPy

Güvenlik sınırları:
  v_min = 0.30 × v_nominal  (motor stall koruması)
  v_max = 1.20 × v_nominal  (kinematic limit)
  Δv_per_step ≤ 0.05 × v_nominal  (gradual adaptation)
"""
from __future__ import annotations
import math, threading, time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Tuple
import numpy as np

@dataclass(frozen=True, slots=True)
class FeedSuggestion:
    v_suggested_mm_s: float   # Önerilen hız
    v_current_mm_s:   float   # Mevcut hız
    delta_v_mm_s:     float   # Fark
    confidence:       float   # [0,1]
    reason:           str
    q_predicted:      float   # Tahmin edilen kalite skoru
    safe:             bool    # Güvenlik doğrulamasından geçti mi?

class RLSEstimator:
    """
    Recursive Least Squares — 5 feature model.
    Features: [1, v, v², T, κ_n]
    Target:   quality_score ∈ [0,100]
    """
    N_FEAT = 5

    def __init__(self, lambda_forget: float = 0.98, init_p: float = 1000.0):
        self._theta = np.zeros(self.N_FEAT)
        self._P     = np.eye(self.N_FEAT) * init_p
        self._lam   = lambda_forget
        self._n     = 0

    def features(self, v: float, T: float, kappa_n: float) -> np.ndarray:
        return np.array([1.0, v, v*v, T, kappa_n])

    def update(self, v: float, T: float, kappa_n: float, quality: float) -> None:
        phi = self.features(v, T, kappa_n)
        # Innovation
        y_hat = float(phi @ self._theta)
        e     = quality - y_hat
        # Kalman gain
        denom = self._lam + float(phi @ self._P @ phi)
        K     = (self._P @ phi) / max(denom, 1e-12)
        # Update
        self._theta += K * e
        self._P     = (self._P - np.outer(K, phi @ self._P)) / self._lam
        self._n     += 1

    def predict(self, v: float, T: float, kappa_n: float) -> float:
        phi = self.features(v, T, kappa_n)
        return float(phi @ self._theta)

    def optimal_v(self, T: float, kappa_n: float,
                  v_min: float, v_max: float) -> Tuple[float, float]:
        """Optimal v: argmax quality(v) — quadratic in v."""
        th = self._theta
        # quality = θ₀ + θ₁v + θ₂v² + θ₃T + θ₄κ
        # dq/dv = θ₁ + 2θ₂v = 0 → v* = -θ₁/(2θ₂)
        if self._n < 10 or abs(th[2]) < 1e-9:
            return (v_min + v_max) / 2.0, 0.0
        v_star = -th[1] / (2.0 * th[2])
        v_star = float(np.clip(v_star, v_min, v_max))
        q_star = self.predict(v_star, T, kappa_n)
        return v_star, q_star


class AdaptiveFeedOptimizer:
    """
    Production feed rate optimizer — advisory only.
    Runs in low-priority daemon thread.
    """
    UPDATE_HZ    = 10.0   # Optimization cycle rate
    DELTA_V_FRAC = 0.05   # Max step per cycle (5% nominal)

    def __init__(self, v_nominal: float = 100.0,
                 v_min_frac: float = 0.30, v_max_frac: float = 1.20):
        self.v_nom = v_nominal
        self.v_min = v_nominal * v_min_frac
        self.v_max = v_nominal * v_max_frac
        self._rls  = RLSEstimator()
        self._buf: deque = deque(maxlen=200)
        self._last_v   = v_nominal
        self._lock     = threading.Lock()
        self._suggestion: Optional[FeedSuggestion] = None
        self._stop     = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._n_updates = 0

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="FeedOpt")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread: self._thread.join(timeout=0.5)

    def record(self, v: float, T: float, kappa_n: float, quality: float) -> None:
        """Ölçüm ekle (thread-safe, non-blocking)."""
        self._buf.append((float(v), float(T), float(kappa_n), float(quality)))

    def get_suggestion(self) -> Optional[FeedSuggestion]:
        with self._lock: return self._suggestion

    def _loop(self) -> None:
        interval = 1.0 / self.UPDATE_HZ
        while not self._stop.is_set():
            t0 = time.monotonic()
            self._optimize_cycle()
            elapsed = time.monotonic() - t0
            time.sleep(max(0.0, interval - elapsed))

    def _optimize_cycle(self) -> None:
        # Snapshot buffer (non-blocking copy)
        if len(self._buf) < 5:
            return
        samples = list(self._buf)[-20:]

        # RLS update
        for v, T, kn, q in samples[-5:]:
            self._rls.update(v, T, kn, q)
        self._n_updates += 1

        # Current conditions (last sample)
        v_curr, T_curr, kn_curr, q_curr = samples[-1]
        v_star, q_star = self._rls.optimal_v(T_curr, kn_curr, self.v_min, self.v_max)

        # Gradual adaptation: cap delta
        delta_max = self.v_nom * self.DELTA_V_FRAC
        delta_v   = float(np.clip(v_star - v_curr, -delta_max, delta_max))
        v_new     = float(np.clip(v_curr + delta_v, self.v_min, self.v_max))

        confidence = min(1.0, self._n_updates / 50.0)
        safe = (self.v_min <= v_new <= self.v_max and
                abs(delta_v) <= delta_max * 1.01)

        reason = ("rls_optimal" if abs(delta_v) > 0.01
                  else "at_optimum" if confidence > 0.5
                  else "warming_up")

        sugg = FeedSuggestion(v_suggested_mm_s=v_new, v_current_mm_s=v_curr,
            delta_v_mm_s=delta_v, confidence=confidence,
            reason=reason, q_predicted=q_star, safe=safe)
        with self._lock:
            self._suggestion = sugg
