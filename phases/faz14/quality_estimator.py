"""
quality_estimator.py — Winding Quality Estimator + Online Tuner + RL Assistant
===============================================================================
Üç modül:

1. QualityEstimator: Anlık winding kalite skoru [0-100]
   Metrikler: gap%, overlap%, thickness_var, angle_dev, tension_cv, dome_quality

2. OnlineTuner: Bounded Nelder-Mead simplex parametr optimizasyonu
   Parametreler: [feed_mult, tension_target, curvature_factor]
   Convergence: simplex boyutu < tol
   Bounded: her parametre fizik limitlerinde

3. RLAssistant: Tabular Q-learning (discrete action space)
   State: (tension_level, speed_level, quality_level) → 3×3×3 = 27 state
   Action: {increase_feed, hold, decrease_feed, increase_tension, decrease_tension}
   Reward: quality_improvement - cost_of_change
   Conservative: küçük learning rate, bounded exploration
"""
from __future__ import annotations
import math, threading, time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np

# ── Quality Estimator ────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class QualityFrame:
    timestamp:         float
    gap_pct:           float   # % gap coverage deficiency
    overlap_pct:       float   # % overlap (buildup)
    thickness_var:     float   # coefficient of variation
    angle_deviation_deg: float # RMS sarım açısı sapması
    tension_cv:        float   # tension coefficient of variation
    dome_quality:      float   # [0,1]
    composite_score:   float   # [0,100]
    grade:             str     # A+/A/B/C/D

    @property
    def is_acceptable(self) -> bool:
        return self.composite_score >= 70.0


class QualityEstimator:
    """
    Online winding quality scorer.
    Ağırlıklı metrik sistemi — fizik tabanlı.
    """
    WEIGHTS = dict(gap=0.20, overlap=0.20, thickness=0.20,
                   angle=0.15, tension=0.15, dome=0.10)

    def __init__(self, bandwidth_mm: float = 10.0,
                 alpha_0_deg: float = 10.17, T_nom: float = 15.0):
        self.b       = bandwidth_mm
        self.alpha_0 = alpha_0_deg
        self.T_nom   = T_nom
        self._history: deque = deque(maxlen=500)
        self._lock   = threading.Lock()
        self._n      = 0

    def update(self, x_err_mm: float, phi_err_deg: float,
               T_N: float, rho_local: float,
               dome_ok: bool = True) -> QualityFrame:
        """
        Anlık kalite hesaplama.

        x_err_mm:   Carriage pozisyon hatası
        phi_err_deg:Spindle açı hatası → sarım açısı sapması
        T_N:        Fiber gerilmesi
        rho_local:  Yerel kaplama yoğunluğu (kanalama analizi)
        dome_ok:    Dome geçiş kalitesi
        """
        # Gap/overlap from rho
        gap_pct     = max(0.0, 1.0 - rho_local) * 100.0  # %gap
        overlap_pct = max(0.0, rho_local - 1.0) * 100.0  # %overlap

        # Thickness variation (running estimate)
        self._history.append(rho_local)
        if len(self._history) >= 5:
            arr = np.array(self._history)
            thickness_var = float(arr.std() / max(arr.mean(), 1e-6))
        else:
            thickness_var = 0.0

        # Angle deviation from position error
        angle_dev = abs(phi_err_deg)

        # Tension CV (exponential decay estimate)
        with self._lock:
            self._n += 1
        tension_cv = abs(T_N - self.T_nom) / max(self.T_nom, 1.0)

        # Dome quality
        dome_q = 1.0 if dome_ok else 0.6

        # Component scores [0,100]
        def s_gap(g):     return max(0.0, 100.0 - g * 2.0)
        def s_over(o):    return max(0.0, 100.0 - o * 3.0)
        def s_thick(cv):  return max(0.0, 100.0 - cv * 200.0)
        def s_angle(a):   return max(0.0, 100.0 - a * 10.0)
        def s_tension(cv):return max(0.0, 100.0 - cv * 150.0)
        def s_dome(d):    return d * 100.0

        scores = dict(
            gap      = s_gap(gap_pct),
            overlap  = s_over(overlap_pct),
            thickness= s_thick(thickness_var),
            angle    = s_angle(angle_dev),
            tension  = s_tension(tension_cv),
            dome     = s_dome(dome_q),
        )
        composite = sum(scores[k] * self.WEIGHTS[k] for k in self.WEIGHTS)

        if composite >= 90: grade = "A+"
        elif composite >= 80: grade = "A"
        elif composite >= 70: grade = "B"
        elif composite >= 60: grade = "C"
        else: grade = "D"

        return QualityFrame(
            timestamp=time.time(), gap_pct=gap_pct, overlap_pct=overlap_pct,
            thickness_var=thickness_var, angle_deviation_deg=angle_dev,
            tension_cv=tension_cv, dome_quality=dome_q,
            composite_score=composite, grade=grade,
        )


# ── Online Tuner (Bounded Nelder-Mead) ───────────────────────────

@dataclass(slots=True)
class TunerState:
    params: np.ndarray   # [feed_mult, T_target_offset, curv_factor]
    score:  float
    n_evals: int = 0

class OnlineTuner:
    """
    Bounded Nelder-Mead simplex optimization.
    Parameters: [feed_mult ∈[0.5,1.2], T_offset ∈[-3,+3]N, curv_fac ∈[0.8,1.2]]
    Objective:  maximize quality_score
    Convergence: simplex size < tol = 0.01

    Advisory: approved by SafetyValidator before application.
    """
    # Parameter bounds: [min, max]
    BOUNDS = np.array([[0.50, 1.20],   # feed_mult
                       [-3.0,  3.0],   # T_offset [N]
                       [0.80,  1.20]]) # curvature_factor
    N_PARAMS = 3
    TOL      = 0.01

    def __init__(self):
        self._simplex: Optional[List[TunerState]] = None
        self._best:    Optional[TunerState]       = None
        self._lock     = threading.Lock()
        self._history: List[Tuple] = []   # (params, score)
        self._converged = False
        self._n_iter    = 0

    def _init_simplex(self, x0: np.ndarray) -> List[TunerState]:
        """Initial simplex around x0."""
        simplex = [TunerState(params=x0.copy(), score=-np.inf)]
        for i in range(self.N_PARAMS):
            xi = x0.copy()
            xi[i] += 0.1 * (self.BOUNDS[i,1] - self.BOUNDS[i,0])
            xi     = np.clip(xi, self.BOUNDS[:,0], self.BOUNDS[:,1])
            simplex.append(TunerState(params=xi, score=-np.inf))
        return simplex

    def _clip(self, x: np.ndarray) -> np.ndarray:
        return np.clip(x, self.BOUNDS[:,0], self.BOUNDS[:,1])

    def observe(self, params: np.ndarray, quality: float) -> None:
        """Ölçüm kaydet ve simplex güncelle."""
        with self._lock:
            self._history.append((params.copy(), quality))
            # Update simplex vertex
            if self._simplex is None:
                self._simplex = self._init_simplex(params)
            # Match closest vertex
            dists = [np.linalg.norm(st.params - params) for st in self._simplex]
            idx   = int(np.argmin(dists))
            if quality > self._simplex[idx].score:
                self._simplex[idx].score = quality
                self._simplex[idx].params = params.copy()
                self._simplex[idx].n_evals += 1
            # Update best
            best_st = max(self._simplex, key=lambda s: s.score)
            if self._best is None or best_st.score > self._best.score:
                self._best = TunerState(params=best_st.params.copy(), score=best_st.score)
            self._n_iter += 1

    def suggest(self) -> Optional[np.ndarray]:
        """Next parameter set to try (Nelder-Mead reflection/expansion)."""
        with self._lock:
            if self._simplex is None or len(self._history) < 2:
                # Warm-up: return slightly perturbed nominal
                x0 = np.array([1.0, 0.0, 1.0])
                noise = np.random.default_rng(self._n_iter).normal(0, 0.05, self.N_PARAMS)
                return self._clip(x0 + noise)

            # Sort simplex: best first
            ordered = sorted(self._simplex, key=lambda s: -s.score)
            x_best  = ordered[0].params
            x_worst = ordered[-1].params
            # Centroid (exclude worst)
            x_ctr   = np.mean([s.params for s in ordered[:-1]], axis=0)
            # Reflection
            x_ref   = self._clip(x_ctr + 1.0 * (x_ctr - x_worst))
            # Check simplex size (convergence)
            sizes = [np.linalg.norm(s.params - x_ctr) for s in self._simplex]
            if max(sizes) < self.TOL:
                self._converged = True
                return x_best.copy()
            return x_ref

    @property
    def best_params(self) -> Optional[np.ndarray]:
        with self._lock: return self._best.params.copy() if self._best else None

    @property
    def converged(self) -> bool:
        return self._converged


# ── RL Assistant (Tabular Q-learning) ────────────────────────────

class RLWindingAssistant:
    """
    Conservative tabular Q-learning.
    State:  (tension_bin, speed_bin, quality_bin) — 3³=27 states
    Action: 0=hold, 1=inc_feed, 2=dec_feed, 3=inc_tension, 4=dec_tension
    Reward: quality_delta - |action_cost|
    Conservative: ε=0.05 (95% exploitation), lr=0.05 (slow learning)
    """
    N_STATES  = 27
    N_ACTIONS = 5
    LR        = 0.05     # Learning rate (small → conservative)
    GAMMA     = 0.90     # Discount
    EPSILON   = 0.05     # Exploration (5% only)
    BINS      = 3        # Per dimension

    ACTION_NAMES = ["hold","inc_feed","dec_feed","inc_tension","dec_tension"]

    def __init__(self, seed: int = 42):
        self._Q     = np.zeros((self.N_STATES, self.N_ACTIONS))
        self._rng   = np.random.default_rng(seed)
        self._prev_state:  Optional[int]   = None
        self._prev_action: Optional[int]   = None
        self._prev_quality:Optional[float] = None
        self._n_steps = 0
        self._lock    = threading.Lock()

    def _discretize(self, T: float, v: float, q: float) -> int:
        """Continuous → discrete state index."""
        # Normalize to [0,2]
        t_bin = min(2, int(np.clip((T - 10.0) / 10.0 * self.BINS, 0, self.BINS-1)))
        v_bin = min(2, int(np.clip((v - 60.0) / 60.0 * self.BINS, 0, self.BINS-1)))
        q_bin = min(2, int(np.clip((q - 0.0)  / 100.0 * self.BINS, 0, self.BINS-1)))
        return t_bin * 9 + v_bin * 3 + q_bin

    def step(self, T: float, v: float, quality: float) -> Tuple[str, float]:
        """
        One RL step. Returns (action_name, confidence).
        Advisory only — no direct actuation.
        """
        with self._lock:
            state = self._discretize(T, v, quality)
            self._n_steps += 1

            # Update Q from previous step
            if (self._prev_state is not None and
                    self._prev_action is not None and
                    self._prev_quality is not None):
                reward  = quality - self._prev_quality - 0.1  # action cost
                td_err  = reward + self.GAMMA * self._Q[state].max() - \
                          self._Q[self._prev_state, self._prev_action]
                self._Q[self._prev_state, self._prev_action] += self.LR * td_err

            # ε-greedy action selection
            if self._rng.random() < self.EPSILON:
                action = int(self._rng.integers(0, self.N_ACTIONS))
            else:
                action = int(np.argmax(self._Q[state]))

            self._prev_state   = state
            self._prev_action  = action
            self._prev_quality = quality

            # Confidence: how dominant is Q(best)?
            q_vals = self._Q[state]
            if q_vals.std() > 1e-6:
                conf = min(1.0, (q_vals.max() - q_vals.mean()) /
                                (q_vals.std() + 1e-6))
            else:
                conf = 0.0

            return self.ACTION_NAMES[action], float(conf)

    @property
    def q_table_norm(self) -> float:
        """Q-table Frobenius norm — learning progress indicator."""
        return float(np.linalg.norm(self._Q))
