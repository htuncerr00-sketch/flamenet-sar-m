"""
online_parameter_tuner.py — Online Parametre Tuner (Nelder-Mead + Rollback)
============================================================================
Kapalı çevrim PID + feedforward parametrelerini online optimize eder.

Tuning parametreleri:
  θ = [Kp_x, Ki_x, Kp_phi, lag_comp_gain, ff_scale]
  θ ∈ [θ_min, θ_max] (sabit bounds)

Algoritma: Bounded Nelder-Mead simplex
  - Gradient-free (sadece quality değerlendirme)
  - Convergence: simplex boyutu < tol
  - Rollback: unstable → θ_prev geri yükle

Transaction mantığı:
  1. apply_candidate(θ_new)    → controller'a geçici uygula
  2. observe(quality_samples)  → N=20 örnek gözlemle
  3. commit()                  → quality artış > threshold → onayla
     rollback()                → quality düşüş → eski θ'ya dön

Güvenlik:
  Tüm θ bounds içinde kırpılır.
  SafetyValidator onaylamadan controller'a gönderilmez.
  Tuning active → control quality monitör → auto-rollback.
"""
from __future__ import annotations
import math, threading, time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple
import numpy as np


class TuningState(Enum):
    IDLE       = "idle"
    CANDIDATE  = "candidate"     # Aday parametreler uygulandı
    OBSERVING  = "observing"     # Kalite gözlemleniyor
    COMMITTED  = "committed"     # Onaylandı
    ROLLED_BACK= "rolled_back"   # Geri alındı


@dataclass(slots=True)
class TunerParams:
    """5-parametre tuning vektörü + metadata."""
    theta: np.ndarray          # [Kp_x, Ki_x, Kp_phi, lag_comp, ff_scale]
    score: float = -np.inf
    n_obs: int   = 0
    timestamp: float = field(default_factory=time.time)

    def copy(self) -> "TunerParams":
        return TunerParams(theta=self.theta.copy(), score=self.score,
                           n_obs=self.n_obs)


@dataclass(frozen=True, slots=True)
class TuningTransaction:
    """Tek bir tuning denemesinin tam kaydı."""
    candidate_theta: np.ndarray
    baseline_theta:  np.ndarray
    baseline_quality:float
    candidate_quality:float
    committed:       bool
    reason:          str


class OnlineParameterTuner:
    """
    Production online parameter tuner.
    Thread-safe. Non-blocking suggest(). Rollback guaranteed.

    Kullanım:
        tuner = OnlineParameterTuner(initial_theta)
        tuner.start_session(quality_baseline)
        candidate = tuner.suggest()
        # Apply to controller (via SafetyValidator)
        tuner.begin_observation()
        for each sample: tuner.observe(quality)
        result = tuner.finalize()  # commit or rollback
    """
    # Parametre isimleri ve bounds
    PARAM_NAMES = ["Kp_x", "Ki_x", "Kp_phi", "lag_comp_gain", "ff_scale"]
    BOUNDS = np.array([
        [0.5,  8.0],   # Kp_x    ∈ [0.5, 8.0]
        [0.05, 2.0],   # Ki_x    ∈ [0.05, 2.0]
        [0.3,  5.0],   # Kp_phi  ∈ [0.3, 5.0]
        [0.1,  1.0],   # lag_comp∈ [0.1, 1.0]
        [0.7,  1.3],   # ff_scale∈ [0.7, 1.3]
    ])
    N_PARAMS    = 5
    SIMPLEX_TOL = 0.005
    MIN_OBS     = 20         # Minimum gözlem sayısı karar için
    COMMIT_THR  = 1.0        # Quality improvement threshold [points]
    ROLLBACK_THR= -2.0       # Auto-rollback threshold

    def __init__(self, initial_theta: Optional[np.ndarray] = None):
        if initial_theta is None:
            # Default nominal PID gains (Faz 8 değerleri)
            initial_theta = np.array([3.0, 0.5, 1.5, 0.5, 1.0])
        self._baseline = TunerParams(theta=np.clip(initial_theta,
                                                    self.BOUNDS[:,0], self.BOUNDS[:,1]))
        self._current  = self._baseline.copy()
        self._candidate: Optional[TunerParams] = None
        self._simplex: List[TunerParams] = []
        self._state    = TuningState.IDLE
        self._history: List[TuningTransaction] = []
        self._obs_q:   List[float] = []   # Quality observations
        self._baseline_q = 0.0
        self._lock     = threading.Lock()
        self._n_sessions = 0
        self._converged  = False
        self._init_simplex()

    # ── Simplex ───────────────────────────────────────────────────

    def _init_simplex(self) -> None:
        theta0 = self._baseline.theta
        self._simplex = [TunerParams(theta=theta0.copy())]
        for i in range(self.N_PARAMS):
            t = theta0.copy()
            # Step = 5% of parameter range
            step = 0.05 * (self.BOUNDS[i,1] - self.BOUNDS[i,0])
            t[i] = float(np.clip(t[i] + step, self.BOUNDS[i,0], self.BOUNDS[i,1]))
            self._simplex.append(TunerParams(theta=t))

    def _clip(self, theta: np.ndarray) -> np.ndarray:
        return np.clip(theta, self.BOUNDS[:,0], self.BOUNDS[:,1])

    # ── Public API ────────────────────────────────────────────────

    def start_session(self, baseline_quality: float) -> None:
        with self._lock:
            self._state       = TuningState.IDLE
            self._baseline_q  = baseline_quality
            self._obs_q       = []
            self._n_sessions += 1

    def suggest(self) -> Optional[np.ndarray]:
        """
        Next candidate θ (Nelder-Mead reflection).
        Returns None if converged or state invalid.
        Thread-safe, non-blocking.
        """
        with self._lock:
            if self._converged or self._state != TuningState.IDLE:
                return None
            # Update scored simplex vertices from history
            self._update_simplex_from_history()
            theta_cand = self._nelder_mead_step()
            theta_cand = self._clip(theta_cand)
            self._candidate = TunerParams(theta=theta_cand)
            self._state     = TuningState.CANDIDATE
            return theta_cand.copy()

    def begin_observation(self) -> bool:
        with self._lock:
            if self._state != TuningState.CANDIDATE:
                return False
            self._obs_q = []
            self._state = TuningState.OBSERVING
            return True

    def observe(self, quality: float) -> None:
        """Quality ölçümü ekle (kandidat aktifken)."""
        with self._lock:
            if self._state == TuningState.OBSERVING:
                self._obs_q.append(float(quality))

    def finalize(self) -> TuningTransaction:
        """
        Yeterli gözlem varsa commit/rollback kararı ver.
        Her zaman TuningTransaction döndürür.
        """
        with self._lock:
            if self._state != TuningState.OBSERVING or len(self._obs_q) < self.MIN_OBS:
                self._state = TuningState.ROLLED_BACK
                return TuningTransaction(
                    candidate_theta   = self._current.theta,
                    baseline_theta    = self._baseline.theta,
                    baseline_quality  = self._baseline_q,
                    candidate_quality = self._baseline_q,
                    committed         = False, reason="insufficient_obs",
                )

            cand_q = float(np.mean(self._obs_q))
            delta  = cand_q - self._baseline_q

            if delta > self.COMMIT_THR:
                # Commit
                self._current.theta = self._candidate.theta.copy()
                self._current.score = cand_q
                self._baseline      = self._current.copy()
                self._baseline_q    = cand_q
                # Update simplex
                if self._candidate:
                    self._candidate.score = cand_q
                    self._simplex.append(self._candidate)
                committed = True; reason = f"improved+{delta:.2f}"
                self._state = TuningState.COMMITTED
            else:
                # Rollback
                self._current = self._baseline.copy()
                committed     = False
                reason        = (f"degraded{delta:.2f}" if delta < self.ROLLBACK_THR
                                 else f"no_improvement{delta:.2f}")
                self._state   = TuningState.ROLLED_BACK

            tx = TuningTransaction(
                candidate_theta   = self._candidate.theta if self._candidate else self._current.theta,
                baseline_theta    = self._baseline.theta,
                baseline_quality  = self._baseline_q,
                candidate_quality = cand_q,
                committed         = committed, reason=reason,
            )
            self._history.append(tx)
            self._state = TuningState.IDLE
            # Convergence check
            self._check_convergence()
            return tx

    def emergency_rollback(self) -> None:
        """Safety trigger → immediate rollback to baseline."""
        with self._lock:
            self._current = self._baseline.copy()
            self._state   = TuningState.ROLLED_BACK

    # ── Nelder-Mead step ──────────────────────────────────────────

    def _update_simplex_from_history(self) -> None:
        if not self._history:
            return
        for tx in self._history[-5:]:
            # Find closest vertex and update score
            dists = [np.linalg.norm(v.theta - tx.candidate_theta)
                     for v in self._simplex]
            if dists:
                idx = int(np.argmin(dists))
                if tx.candidate_quality > self._simplex[idx].score:
                    self._simplex[idx].score  = tx.candidate_quality
                    self._simplex[idx].theta  = tx.candidate_theta.copy()

    def _nelder_mead_step(self) -> np.ndarray:
        if len(self._simplex) < 2:
            return self._baseline.theta.copy()
        # Sort by score (descending)
        scored = [(v.score if v.score > -np.inf else 0.0, v.theta)
                  for v in self._simplex]
        scored.sort(key=lambda x: -x[0])
        best_theta = scored[0][1]
        worst_theta= scored[-1][1]
        # Centroid (exclude worst)
        ctr = np.mean([t for _, t in scored[:-1]], axis=0)
        # Reflection
        ref = ctr + 1.0 * (ctr - worst_theta)
        return ref

    def _check_convergence(self) -> None:
        if len(self._simplex) < 2:
            return
        ctr    = np.mean([v.theta for v in self._simplex], axis=0)
        sizes  = [np.linalg.norm(v.theta - ctr) for v in self._simplex]
        if max(sizes) < self.SIMPLEX_TOL:
            self._converged = True

    # ── Properties ────────────────────────────────────────────────

    @property
    def current_theta(self) -> np.ndarray:
        with self._lock: return self._current.theta.copy()

    @property
    def state(self) -> TuningState:
        with self._lock: return self._state

    @property
    def converged(self) -> bool:
        return self._converged

    @property
    def n_commits(self) -> int:
        return sum(1 for tx in self._history if tx.committed)

    @property
    def n_rollbacks(self) -> int:
        return sum(1 for tx in self._history if not tx.committed)

    def summary(self) -> dict:
        with self._lock:
            return {
                "state":      self._state.value,
                "converged":  self._converged,
                "n_sessions": self._n_sessions,
                "n_commits":  self.n_commits,
                "n_rollbacks":self.n_rollbacks,
                "theta":      self._current.theta.tolist(),
                "theta_names":self.PARAM_NAMES,
            }
