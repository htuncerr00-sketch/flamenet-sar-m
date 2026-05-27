"""
unified_geodesic_planner.py — Unified Geodesic Trajectory Planner
==================================================================
Tek sürekli geodezik yol: front pole → equator → cylinder → rear dome → pole → return.

Unified ODE (aynı silindir+dome için):
  dz/ds   = dir · √(r(z)² - c²) / (r(z) · √G(z))
  dφ/ds   = c / r(z)²

Neden aynı ODE çalışır?
  Cylinder: r=R sabit, G=1 → dz/ds = dir·cos(α₀), dφ/ds = sin(α₀)/R
  Dome:     r(z) değişken, G>1 → aynı form, farklı katsayılar
  C1 continuity garantisi: r'=0 junction'da → ODE RHS sürekli

Tam devre (full circuit):
  1. Forward: z_start (front equator) → rear pole (z_turn_rear)
  2. Return:  rear pole → front pole (z_turn_front)
  3. Forward2: front pole → z_start (front equator, tamamlama)

Alternatif (yarım devre):
  Start: front equator → rear equator (cylinder traverse)
  Bu pratik makinede yaygındır: dome başı, silindir, dome sonu.

Event sistemi:
  rear_pole_event:  r(z) → c near rear dome  → dir=-1
  front_pole_event: r(z) → c near front dome → dir=+1, circuit done
  equator_events: region sınırlarını işaretle (terminal değil)

Referans: Guo et al. (2018); Koussios (2004) Ch.11
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional, Tuple
import numpy as np
from scipy.integrate import solve_ivp
from full_body_mandrel import FullBodyMandrel, BodyRegion

POLE_CLR = 1.002   # r_stop = c × bu

# ── Path Point ──────────────────────────────────────────────────

@dataclass(slots=True)
class FullBodyPoint:
    s_mm:       float
    z_mm:       float
    phi_rad:    float
    r_mm:       float
    alpha_rad:  float
    kappa_n:    float
    region:     BodyRegion
    is_turnaround: bool = False
    is_junction:   bool = False
    v_max_mm_s:    float = float("inf")

    @property
    def alpha_deg(self): return math.degrees(self.alpha_rad)
    @property
    def phi_deg(self):   return math.degrees(self.phi_rad)
    @property
    def region_code(self):
        return {"FRONT_DOME":"FD","CYLINDER":"CY","REAR_DOME":"RD"}[self.region.name]


@dataclass(slots=True)
class FullBodyPath:
    """Tam bir full-body geodesic circuit."""
    c_clairaut:     float
    phi_start_rad:  float
    points:         List[FullBodyPoint] = field(default_factory=list)
    n_turnarounds:  int = 0
    converged:      bool = False

    @property
    def n_points(self): return len(self.points)

    @property
    def total_arc_mm(self):
        return self.points[-1].s_mm - self.points[0].s_mm if self.points else 0.0

    @property
    def phi_total_deg(self):
        if not self.points: return 0.0
        return math.degrees(self.points[-1].phi_rad - self.points[0].phi_rad)

    def turnaround_points(self):
        return [p for p in self.points if p.is_turnaround]

    def junction_points(self):
        return [p for p in self.points if p.is_junction]

    def region_counts(self):
        from collections import Counter
        return Counter(p.region.name for p in self.points)

    def alpha_profile(self) -> Tuple[np.ndarray, np.ndarray]:
        """α(z) profili — (z_arr, alpha_arr)."""
        z = np.array([p.z_mm for p in self.points])
        a = np.array([p.alpha_rad for p in self.points])
        return z, a

    def min_v_max(self):
        return min((p.v_max_mm_s for p in self.points), default=float("inf"))

    def bridging_count(self):
        return sum(1 for p in self.points if p.kappa_n < -1e-9)

    def summary(self) -> str:
        rc = self.region_counts()
        return (
            f"FullBodyPath: c={self.c_clairaut:.3f}mm  "
            f"φ₀={math.degrees(self.phi_start_rad):.2f}°  "
            f"n={self.n_points}  arc={self.total_arc_mm:.1f}mm  "
            f"Δφ={self.phi_total_deg:.2f}°  "
            f"turns={self.n_turnarounds}  "
            f"FD:{rc.get('FRONT_DOME',0)} CY:{rc.get('CYLINDER',0)} RD:{rc.get('REAR_DOME',0)}  "
            f"v_min={self.min_v_max():.1f}mm/s  "
            f"{'✓' if self.converged else '✗'}"
        )


# ── Planner ──────────────────────────────────────────────────────

class UnifiedGeodesicPlanner:
    """
    Full-body geodesic path integrator.

    Kullanım:
        planner = UnifiedGeodesicPlanner(body, c=25.0)
        path = planner.plan_circuit(phi_start=0.0)
        print(path.summary())
    """

    def __init__(
        self,
        body:             FullBodyMandrel,
        c_clairaut:       float,
        a_centripetal:    float = 5000.0,
        pole_clearance:   float = POLE_CLR,
        rtol:             float = 1e-6,
        atol:             float = 1e-7,
    ) -> None:
        self.body   = body
        self.c      = c_clairaut
        self.a_max  = a_centripetal
        self.r_stop = c_clairaut * pole_clearance
        self.rtol   = rtol
        self.atol   = atol

        if self.r_stop >= body.R:
            raise ValueError(f"c={c_clairaut:.3f} ≥ R={body.R:.3f}")

    # ── ODE ───────────────────────────────────────────────────────
    def _ode(self, s, y, direction):
        z, phi = y
        body = self.body; c = self.c
        r = body.r(z); r2 = r*r; c2 = c*c
        if r2 <= c2 + 1e-10:
            return [0.0, c/max(r2, c2)]
        G  = body.G(z)
        dz = direction * math.sqrt(r2 - c2) / (r * math.sqrt(G))
        return [dz, c/r2]

    # ── Events ───────────────────────────────────────────────────
    def _make_pole_event(self, near_z_range, is_rear):
        """Pol dönüş olayı: r(z) → r_stop."""
        body = self.body; r_stop = self.r_stop
        z_lo, z_hi = near_z_range

        def ev(s, y):
            z = y[0]
            if not (z_lo <= z <= z_hi): return 1.0
            return body.r(z) - r_stop
        ev.terminal  = True
        ev.direction = -1  # r azalırken
        return ev

    def _make_return_event(self, z_target):
        """Return pass bitiş olayı: z → z_target."""
        def ev(s, y): return y[0] - z_target
        ev.terminal  = True
        ev.direction = -1
        return ev

    def _make_forward_end_event(self, z_target):
        """Forward bitiş: z → z_target (front equator'a dönüş)."""
        def ev(s, y): return y[0] - z_target
        ev.terminal  = True
        ev.direction = +1
        return ev

    # ── Helper: point ────────────────────────────────────────────
    def _make_pt(self, s, z, phi, is_ta=False, is_jct=False):
        body = self.body; c = self.c
        r    = body.r(z)
        reg  = body.region_of(z)
        al   = body.alpha_from_clairaut(z, c)
        kn   = body.kappa_n(z, al)
        vmax = math.sqrt(self.a_max/kn) if kn > 1e-9 else float("inf")
        return FullBodyPoint(s_mm=s, z_mm=z, phi_rad=phi, r_mm=r,
            alpha_rad=al, kappa_n=kn, region=reg,
            is_turnaround=is_ta, is_junction=is_jct, v_max_mm_s=vmax)

    def _is_near_junction(self, z):
        δ = self.body.L_cyl * 0.05 + 2.0
        return (abs(z - self.body.z_fe) < δ or abs(z - self.body.z_re) < δ)

    # ── Forward integration (front equator → rear pole) ──────────
    def _integrate_forward(self, phi0, z0, s_max=5000.0):
        rear_range = (self.body.z_re, self.body.Z_total)
        events = [self._make_pole_event(rear_range, is_rear=True)]
        sol = solve_ivp(
            lambda s, y: self._ode(s, y, +1),
            (0.0, s_max), [z0, phi0],
            method="RK45", events=events,
            rtol=self.rtol, atol=self.atol)
        reached = len(sol.t_events[0]) > 0
        return sol.t, sol.y[0], sol.y[1], reached

    # ── Return integration (rear pole → front pole) ──────────────
    def _integrate_return(self, phi_ta, z_ta, s0, s_max=8000.0):
        front_range = (0.0, self.body.z_fe)
        events = [self._make_pole_event(front_range, is_rear=False)]
        sol = solve_ivp(
            lambda s, y: self._ode(s, y, -1),
            (s0, s0+s_max), [z_ta, phi_ta],
            method="RK45", events=events,
            rtol=self.rtol, atol=self.atol)
        reached = len(sol.t_events[0]) > 0
        return sol.t, sol.y[0], sol.y[1], reached

    # ── Completion (front pole → front equator) ──────────────────
    def _integrate_completion(self, phi_fp, z_fp, s0, z_target, s_max=3000.0):
        events = [self._make_forward_end_event(z_target)]
        sol = solve_ivp(
            lambda s, y: self._ode(s, y, +1),
            (s0, s0+s_max), [z_fp, phi_fp],
            method="RK45", events=events,
            rtol=self.rtol, atol=self.atol)
        reached = len(sol.t_events[0]) > 0
        return sol.t, sol.y[0], sol.y[1], reached

    # ── Full circuit ──────────────────────────────────────────────
    def plan_circuit(
        self,
        phi_start:      float = 0.0,
        z_start:        float = None,   # default: front equator
        s_max_forward:  float = 5000.0,
        s_max_return:   float = 8000.0,
    ) -> FullBodyPath:
        """
        Tam bir full-body geodesic circuit entegre et.

        Front equator → rear pole → front pole → front equator.
        """
        body = self.body
        if z_start is None:
            z_start = body.z_fe  # front equator default

        path = FullBodyPath(c_clairaut=self.c, phi_start_rad=phi_start)

        # ── Phase 1: Forward (equator → rear pole) ────────────────
        s_f, z_f, phi_f, reached_rear = self._integrate_forward(
            phi_start, z_start, s_max_forward)
        if len(s_f) == 0: return path

        z_ta_rear   = float(z_f[-1])
        phi_ta_rear = float(phi_f[-1])
        s_ta_rear   = float(s_f[-1])

        for i, (s, z, phi) in enumerate(zip(s_f, z_f, phi_f)):
            is_ta  = (i == len(s_f)-1)
            is_jct = self._is_near_junction(z)
            path.points.append(self._make_pt(s, z, phi, is_ta=is_ta, is_jct=is_jct))
        if reached_rear:
            path.n_turnarounds += 1

        # ── Phase 2: Return (rear pole → front pole) ──────────────
        s_r, z_r, phi_r, reached_front = self._integrate_return(
            phi_ta_rear, z_ta_rear, s_ta_rear, s_max_return)

        z_ta_front   = float(z_r[-1]) if len(z_r)>0 else z_ta_rear
        phi_ta_front = float(phi_r[-1]) if len(phi_r)>0 else phi_ta_rear
        s_ta_front   = float(s_r[-1]) if len(s_r)>0 else s_ta_rear

        for i, (s, z, phi) in enumerate(zip(s_r, z_r, phi_r)):
            is_ta  = (i == len(s_r)-1) and reached_front
            is_jct = self._is_near_junction(z)
            path.points.append(self._make_pt(s, z, phi, is_ta=is_ta, is_jct=is_jct))
        if reached_front:
            path.n_turnarounds += 1

        # ── Phase 3: Completion (front pole → front equator) ──────
        s_c, z_c, phi_c, completed = self._integrate_completion(
            phi_ta_front, z_ta_front, s_ta_front, z_start)

        for s, z, phi in zip(s_c, z_c, phi_c):
            is_jct = self._is_near_junction(z)
            path.points.append(self._make_pt(s, z, phi, is_jct=is_jct))

        path.converged = reached_rear and reached_front and completed
        return path

    # ── Multi-circuit layer ───────────────────────────────────────
    def plan_layer(
        self,
        n_circuits: int,
        phi_step:   Optional[float] = None,
        z_start:    Optional[float] = None,
    ) -> List[FullBodyPath]:
        """
        n_circuits devre planla.

        phi_step: Devreler arası azimut artışı (default: K — turn-around açısı)
        """
        paths = []
        p0 = self.plan_circuit(phi_start=0.0, z_start=z_start)
        if not p0.points: return paths
        paths.append(p0)

        # Turn-around açısı
        K = p0.phi_total_deg if p0.phi_total_deg > 0 else 360.0
        if phi_step is None:
            phi_step = math.radians(K)

        for i in range(1, n_circuits):
            phi_i = i * phi_step
            p = self.plan_circuit(phi_start=phi_i, z_start=z_start)
            paths.append(p)
        return paths
