"""
full_body_mandrel.py — Full-Body Mandrel (Cylinder + Dome)
===========================================================
Global coordinate: z_global ∈ [0, Z_total]
  z_global = 0              : front pole
  z_global = H_front        : front equator (dome-cylinder junction)
  z_global = H_front+L_cyl  : rear equator (cylinder-dome junction)
  z_global = Z_total        : rear pole

Region logic:
  FRONT_DOME  : z_global ∈ [0, H_front]
    z_local  = H_front - z_global  (0 at equator, H at pole)
    dr/dz_gl = -front.r_prime(z_local)   [chain rule: dz_local/dz_gl=-1]
    d²r/dz²  = +front.r_dbl_prime(z_local)

  CYLINDER    : z_global ∈ [H_front, H_front+L_cyl]
    r = R (const), r'=0, r''=0, G=1, κ_m=0, κ_c=1/R

  REAR_DOME   : z_global ∈ [H_front+L_cyl, Z_total]
    z_local  = z_global - (H_front+L_cyl)  (0 at equator)
    dr/dz_gl = +rear.r_prime(z_local)
    d²r/dz²  = +rear.r_dbl_prime(z_local)

C1 continuity at junctions (both domes have r_prime(0)=0):
  Front junction: lim(z→H_front⁻) r'_global = -r_dome'(0) = 0  ✓
  Rear  junction: lim(z→H_re⁺)    r'_global = +r_dome'(0) = 0  ✓

C2 discontinuity (handled by TransitionManager):
  κ_m jumps from dome value to 0 (cylinder) at junction.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, Tuple
import numpy as np
from dome_mandrel import DomeMandrel, EllipticDome, SphericalDome

class BodyRegion(Enum):
    FRONT_DOME = auto()
    CYLINDER   = auto()
    REAR_DOME  = auto()

@dataclass(frozen=True)
class RegionPoint:
    z_global: float
    region:   BodyRegion
    z_local:  float    # z within the region
    r:        float    # r(z_global)

class FullBodyMandrel:
    """
    Full-body mandrel: front dome + cylinder + rear dome.
    Acts as a DomeMandrel-compatible surface for the unified planner.
    """
    def __init__(
        self,
        front_dome:    DomeMandrel,
        cylinder_length: float,
        rear_dome:     Optional[DomeMandrel] = None,
    ) -> None:
        self.front = front_dome
        self.L_cyl  = cylinder_length
        self.rear   = rear_dome if rear_dome is not None else front_dome
        # Region boundaries in global z
        self.z_fe   = front_dome.height_mm               # front equator
        self.z_re   = self.z_fe + cylinder_length         # rear equator
        self.Z_total= self.z_re + self.rear.height_mm     # rear pole
        self.R      = front_dome.equator_radius_mm

    # ── Region detection ──────────────────────────────────────────
    def region_of(self, z: float) -> BodyRegion:
        if z <= self.z_fe + 1e-9:
            return BodyRegion.FRONT_DOME
        if z <= self.z_re + 1e-9:
            return BodyRegion.CYLINDER
        return BodyRegion.REAR_DOME

    def region_point(self, z: float) -> RegionPoint:
        reg = self.region_of(z)
        if reg == BodyRegion.FRONT_DOME:
            z_loc = max(0.0, self.z_fe - z)
        elif reg == BodyRegion.CYLINDER:
            z_loc = z - self.z_fe
        else:
            z_loc = z - self.z_re
        return RegionPoint(z, reg, z_loc, self.r(z))

    # ── Geometry ──────────────────────────────────────────────────
    def r(self, z: float) -> float:
        reg = self.region_of(z)
        if reg == BodyRegion.FRONT_DOME:
            return self.front.r(max(0.0, self.z_fe - z))
        elif reg == BodyRegion.CYLINDER:
            return self.R
        else:
            return self.rear.r(max(0.0, z - self.z_re))

    def r_prime(self, z: float) -> float:
        """dr/dz_global — chain rule applied."""
        reg = self.region_of(z)
        if reg == BodyRegion.FRONT_DOME:
            z_loc = max(0.0, self.z_fe - z)
            return -self.front.r_prime(z_loc)   # dz_loc/dz_gl = -1
        elif reg == BodyRegion.CYLINDER:
            return 0.0
        else:
            z_loc = max(0.0, z - self.z_re)
            return self.rear.r_prime(z_loc)     # dz_loc/dz_gl = +1

    def r_double_prime(self, z: float) -> float:
        """d²r/dz_global² — chain rule applied."""
        reg = self.region_of(z)
        if reg == BodyRegion.FRONT_DOME:
            z_loc = max(0.0, self.z_fe - z)
            # d/dz_gl[-r'(z_loc)] = -r''(z_loc)×(-1) = +r''(z_loc)
            return self.front.r_double_prime(z_loc)
        elif reg == BodyRegion.CYLINDER:
            return 0.0
        else:
            z_loc = max(0.0, z - self.z_re)
            return self.rear.r_double_prime(z_loc)

    def G(self, z: float) -> float:
        rp = self.r_prime(z); return 1.0 + rp*rp

    def kappa_m(self, z: float) -> float:
        G = self.G(z)
        return -self.r_double_prime(z)/G**1.5 if G > 1e-12 else 0.0

    def kappa_c(self, z: float) -> float:
        r = self.r(z); G = self.G(z)
        return 1.0/(r*math.sqrt(G)) if r > 1e-9 and G > 1e-12 else 0.0

    def kappa_n(self, z: float, alpha_rad: float) -> float:
        ca = math.cos(alpha_rad); sa = math.sin(alpha_rad)
        return self.kappa_m(z)*ca*ca + self.kappa_c(z)*sa*sa

    def alpha_from_clairaut(self, z: float, c: float) -> float:
        r = self.r(z)
        if r < c - 1e-9: return math.pi/2.0
        return math.asin(min(1.0, c/r))

    # ── Curvature jump at junctions ───────────────────────────────
    def junction_curvature_jump(self) -> Tuple[float, float]:
        """
        Returns (Δκ_m_front, Δκ_m_rear) — curvature jumps at junctions.
        Cylinder: κ_m = 0. Dome at equator: κ_m = R/H² (elliptic).
        """
        delta_front = self.front.kappa_m(0.0) - 0.0
        delta_rear  = self.rear.kappa_m(0.0)  - 0.0
        return delta_front, delta_rear

    # ── Coverage model ────────────────────────────────────────────
    def pole_congestion(self, z: float, c: float, k: int, b: float) -> float:
        r = self.r(z)
        if r < c: return float("inf")
        alpha = self.alpha_from_clairaut(z, c)
        cos_a = math.cos(alpha)
        if cos_a < 1e-6: return float("inf")
        return k*b/(2*math.pi*r*cos_a)

    # ── Geometry profile ─────────────────────────────────────────
    def profile(self, n_pts: int = 200):
        zz = np.linspace(0.0, self.Z_total, n_pts)
        rr = np.array([self.r(z) for z in zz])
        km = np.array([self.kappa_m(z) for z in zz])
        kc = np.array([self.kappa_c(z) for z in zz])
        return {"z": zz, "r": rr, "kappa_m": km, "kappa_c": kc}

    def report(self) -> str:
        dkm_f, dkm_r = self.junction_curvature_jump()
        lines=[
            f"  FullBodyMandrel: R={self.R:.1f}mm  L_cyl={self.L_cyl:.1f}mm",
            f"  Front dome: {self.front.__class__.__name__}  H={self.front.height_mm:.1f}mm",
            f"  Rear  dome: {self.rear.__class__.__name__}   H={self.rear.height_mm:.1f}mm",
            f"  Z_total = {self.Z_total:.2f}mm",
            f"  Front equator @ z={self.z_fe:.2f}mm  Rear equator @ z={self.z_re:.2f}mm",
            f"  κ_m jump (front): {dkm_f:+.6f}/mm  (rear): {dkm_r:+.6f}/mm",
        ]
        return "\n".join(lines)
