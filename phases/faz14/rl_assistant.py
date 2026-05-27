"""
rl_assistant.py — Conservative Tabular Q-learning RL Assistant
==============================================================
Sadece offline advisory — ASLA realtime command üretmez.

State space (27 = 3³):
  tension_level  ∈ {low, normal, high}        → [0,1,2]
  speed_level    ∈ {slow, nominal, fast}       → [0,1,2]
  quality_level  ∈ {poor, acceptable, good}   → [0,1,2]

Action space (5):
  0: hold              → nothing
  1: increase_feed     → suggest +5% feed
  2: decrease_feed     → suggest -5% feed
  3: increase_damping  → suggest lag_comp += 0.05
  4: reduce_accel      → suggest jerk limit tighten

Q-learning:
  Q(s,a) ← Q(s,a) + lr × [r + γ·max Q(s') - Q(s,a)]
  lr=0.05 (slow), γ=0.90, ε=0.05 (mostly exploit)

Reward:
  r = quality_improvement - |action_magnitude| × 0.2 - thermal_penalty

Deterministik: seed sabittir, replay garanti edilir.
Advisory penalty: her RL öneri güvenlik doğrulamasından geçmeli.
"""
from __future__ import annotations
import math, time, threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np


ACTION_NAMES = ["hold", "increase_feed", "decrease_feed",
                "increase_damping", "reduce_accel"]
N_STATES  = 27
N_ACTIONS = 5


@dataclass(frozen=True, slots=True)
class OptimizationSuggestion:
    action:            str      # Action name
    action_magnitude:  float    # Suggested change magnitude
    confidence:        float    # Q-value confidence
    state_code:        int      # Discrete state index
    q_value:           float    # Q(s,a)
    reward_last:       float    # Son adım reward
    episode:           int
    timestamp:         float
    # Advisory sadece (SafetyValidator geçmeden uygulanamaz)
    advisory_only:     bool = True

    @property
    def delta_feed_pct(self) -> float:
        """Feed önerisi [%]. 0 if action is not feed-related."""
        if self.action == "increase_feed":   return +5.0
        if self.action == "decrease_feed":   return -5.0
        return 0.0

    @property
    def delta_lag_comp(self) -> float:
        """Lag compensation change. 0 if not applicable."""
        if self.action == "increase_damping": return +0.05
        return 0.0


class TabularQAgent:
    """Auditable tabular Q-agent."""
    LR       = 0.05
    GAMMA    = 0.90
    EPSILON  = 0.05

    def __init__(self, seed: int = 42):
        self._Q    = np.zeros((N_STATES, N_ACTIONS))
        self._rng  = np.random.default_rng(seed)
        self._seed = seed
        self._prev_s: Optional[int]   = None
        self._prev_a: Optional[int]   = None
        self._n_steps = 0

    def _update(self, s: int, a: int, r: float, s_next: int) -> float:
        td = r + self.GAMMA * self._Q[s_next].max() - self._Q[s, a]
        self._Q[s, a] += self.LR * td
        return td

    def select_action(self, state: int) -> Tuple[int, float]:
        if self._rng.random() < self.EPSILON:
            a = int(self._rng.integers(0, N_ACTIONS))
        else:
            a = int(np.argmax(self._Q[state]))
        # Confidence: normalized advantage
        q = self._Q[state]
        conf = float((q[a] - q.mean()) / (q.std() + 1e-9))
        conf = float(np.clip((conf + 1) / 2, 0, 1))
        return a, conf

    def step(self, state: int, reward: float) -> Tuple[int, float]:
        if self._prev_s is not None and self._prev_a is not None:
            self._update(self._prev_s, self._prev_a, reward, state)
        a, conf = self.select_action(state)
        self._prev_s = state; self._prev_a = a
        self._n_steps += 1
        return a, conf

    def reset_episode(self) -> None:
        self._prev_s = self._prev_a = None

    @property
    def q_norm(self) -> float: return float(np.linalg.norm(self._Q))

    def replay(self, seed: Optional[int] = None) -> "TabularQAgent":
        """Deterministik replay için aynı seed'li yeni agent."""
        return TabularQAgent(seed=seed or self._seed)


def _discretize_state(T_N: float, v_mm_s: float, quality: float) -> int:
    """State → integer index (27 states)."""
    # Tension bins: <12=low, 12-22=normal, >22=high
    t_bin = 0 if T_N < 12.0 else (1 if T_N < 22.0 else 2)
    # Speed bins: <60=slow, 60-110=nominal, >110=fast
    v_bin = 0 if v_mm_s < 60.0 else (1 if v_mm_s < 110.0 else 2)
    # Quality bins: <60=poor, 60-80=acceptable, >80=good
    q_bin = 0 if quality < 60.0 else (1 if quality < 80.0 else 2)
    return t_bin * 9 + v_bin * 3 + q_bin


def _compute_reward(quality_delta: float, action: int,
                    thermal_C: float) -> float:
    """Reward function — penalizes unnecessary actions."""
    r_quality  = quality_delta * 0.5          # Quality gain
    r_action   = -abs(action - 0) * 0.2       # Action cost (hold=0)
    r_thermal  = -max(0.0, (thermal_C - 40.0) / 20.0)  # Thermal penalty
    return r_quality + r_action + r_thermal


class RLAssistant:
    """
    Production RL assistant. Low-priority daemon.
    Advisory only — all suggestions validated externally.
    """
    def __init__(self, seed: int = 42, T_nominal: float = 15.0):
        self._agent  = TabularQAgent(seed=seed)
        self._T_nom  = T_nominal
        self._episode= 0
        self._step   = 0
        self._last_q = 50.0
        self._last_r = 0.0
        self._lock   = threading.Lock()
        self._history: List[OptimizationSuggestion] = []

    def step(self, T_N: float, v_mm_s: float, quality: float,
             thermal_C: float = 22.0) -> OptimizationSuggestion:
        """
        Single RL step → OptimizationSuggestion.
        Thread-safe, non-blocking (<0.5ms).
        """
        with self._lock:
            state  = _discretize_state(T_N, v_mm_s, quality)
            reward = _compute_reward(quality - self._last_q, 0, thermal_C)
            self._last_r = reward

            a, conf = self._agent.step(state, reward)
            self._last_q = quality
            self._step  += 1

            # Action magnitude (how much to change)
            mag = 0.05 if a in (1,2) else (0.05 if a==3 else 0.02)

            sugg = OptimizationSuggestion(
                action           = ACTION_NAMES[a],
                action_magnitude = mag,
                confidence       = conf,
                state_code       = state,
                q_value          = float(self._agent._Q[state, a]),
                reward_last      = reward,
                episode          = self._episode,
                timestamp        = time.time(),
                advisory_only    = True,
            )
            self._history.append(sugg)
            return sugg

    def new_episode(self) -> None:
        with self._lock:
            self._agent.reset_episode()
            self._episode += 1

    def get_history(self, n: int = 50) -> List[OptimizationSuggestion]:
        with self._lock: return list(self._history[-n:])

    def q_table_summary(self) -> dict:
        with self._lock:
            Q = self._agent._Q
            return {
                "q_norm":     round(self._agent.q_norm, 4),
                "best_actions": {f"s{i}": int(np.argmax(Q[i])) for i in range(N_STATES)},
                "n_steps":    self._step,
                "n_episodes": self._episode,
            }

    def deterministic_replay(self, seed: int = 42,
                              n_steps: int = 100) -> List[int]:
        """Aynı seed ile aynı action sequence → determinizm doğrulama."""
        agent = self._agent.replay(seed)
        actions = []
        rng = np.random.default_rng(seed + 1)
        for _ in range(n_steps):
            T = float(rng.normal(15, 1.5))
            v = float(rng.uniform(60, 120))
            q = float(rng.uniform(60, 95))
            s = _discretize_state(T, v, q)
            a, _ = agent.select_action(s)
            actions.append(a)
        return actions
