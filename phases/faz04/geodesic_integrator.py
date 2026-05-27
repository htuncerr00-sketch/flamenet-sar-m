"""
geodesic_integrator.py — Geodezik Yol İntegratörü (RK45 / Arc-Length)
======================================================================
ODE sistemi (arc-length s bağımsız değişken):

  dz/ds   = dir · √(r²-c²) / (r·√G)
  dφ/ds   = c / r²

  c = Clairaut sabiti = R·sin(α_ekvatör) = r_pole
  sin(α(z)) = c/r(z)  → α her noktada Clairaut'dan

Turn-around event: r(z) → c·(1+ε), dir tersine döner.
Equator return event: z → z_start azalırken.

Referans: Koussios (2004) Eq.11.48-11.49; Clairaut (1733)
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import numpy as np
from scipy.integrate import solve_ivp
from dome_mandrel import DomeMandrel

POLE_CLR = 1.002   # r_stop = c × bu

@dataclass(slots=True)
class GeodesicPoint:
    s_mm:float; z_mm:float; phi_rad:float; r_mm:float
    alpha_rad:float; kappa_n:float; v_max_mm_s:float
    is_turnaround:bool=False
    @property
    def alpha_deg(self): return math.degrees(self.alpha_rad)
    @property
    def phi_deg(self): return math.degrees(self.phi_rad)

@dataclass(slots=True)
class GeodesicPath:
    c_clairaut:float; phi_start_rad:float
    points:List[GeodesicPoint]=field(default_factory=list)
    z_turnaround:float=0.0; phi_turnaround:float=0.0
    converged:bool=False
    @property
    def n_points(self): return len(self.points)
    @property
    def total_arc_length_mm(self):
        return self.points[-1].s_mm-self.points[0].s_mm if self.points else 0.0
    @property
    def phi_total_rad(self):
        return self.points[-1].phi_rad-self.points[0].phi_rad if self.points else 0.0
    @property
    def phi_total_deg(self): return math.degrees(self.phi_total_rad)
    @property
    def alpha_equator_deg(self):
        return self.points[0].alpha_deg if self.points else 0.0
    def max_kappa_n(self): return max((p.kappa_n for p in self.points),default=0.0)
    def min_v_max(self): return min((p.v_max_mm_s for p in self.points),default=float("inf"))
    def bridging_points(self): return [p for p in self.points if p.kappa_n<-1e-9]
    def summary(self):
        return (f"c={self.c_clairaut:.3f}mm φ₀={math.degrees(self.phi_start_rad):.2f}° "
                f"n={self.n_points} arc={self.total_arc_length_mm:.1f}mm "
                f"Δφ={self.phi_total_deg:.2f}° z_ta={self.z_turnaround:.2f}mm "
                f"v_min={self.min_v_max():.1f}mm/s {'✓' if self.converged else '✗'}")


class GeodesicPathIntegrator:
    """
    RK45 geodezik yol integratörü.

    Kullanım:
        gi = GeodesicPathIntegrator(dome, c=15.0)
        path = gi.integrate_circuit(phi_start=0.0)
        print(path.summary())
    """
    def __init__(self, dome:DomeMandrel, c_clairaut:float,
                 a_centripetal_max:float=5000.0,
                 pole_clearance:float=POLE_CLR,
                 rtol:float=1e-6, atol:float=1e-7):
        self.dome=dome; self.c=c_clairaut
        self.a_max=a_centripetal_max
        self.r_stop=c_clairaut*pole_clearance
        if self.r_stop>dome.equator_radius_mm:
            raise ValueError(f"c={c_clairaut:.3f}≥R={dome.equator_radius_mm:.3f}")
        self.rtol=rtol; self.atol=atol

    # --- ODE ---
    def _dydx(self, s:float, y:list, direction:int)->list:
        z,phi=y; dome=self.dome; c=self.c
        r=dome.r(z); r2=r*r; c2=c*c
        if r2<=c2+1e-10:
            return [0.0, c/max(r2,c2)]
        G=dome.G(z)
        dz=direction*math.sqrt(r2-c2)/(r*math.sqrt(G))
        dphi=c/r2
        return [dz, dphi]

    # --- Events ---
    def _pole_event(self):
        r_stop=self.r_stop; dome=self.dome
        def ev(s,y): return dome.r(y[0])-r_stop
        ev.terminal=True; ev.direction=-1
        return ev

    def _equator_event(self, z_target:float):
        def ev(s,y): return y[0]-z_target
        ev.terminal=True; ev.direction=-1
        return ev

    # --- Forward pass ---
    def _forward(self, phi_start:float, z_start:float, s_max:float=3000.0):
        sol=solve_ivp(
            lambda s,y: self._dydx(s,y,+1),
            (0.0,s_max), [z_start,phi_start],
            method="RK45", events=[self._pole_event()],
            rtol=self.rtol, atol=self.atol)
        reached=len(sol.t_events[0])>0
        return sol.t, sol.y[0], sol.y[1], reached

    # --- Return pass ---
    def _return(self, phi_ta:float, z_ta:float, z_target:float,
                s_offset:float, s_max:float=3000.0):
        sol=solve_ivp(
            lambda s,y: self._dydx(s,y,-1),
            (s_offset,s_offset+s_max), [z_ta,phi_ta],
            method="RK45", events=[self._equator_event(z_target)],
            rtol=self.rtol, atol=self.atol)
        reached=len(sol.t_events[0])>0
        return sol.t, sol.y[0], sol.y[1], reached

    # --- Helper ---
    def _make_pt(self, s:float, z:float, phi:float, is_ta:bool=False)->GeodesicPoint:
        dome=self.dome; c=self.c
        r=dome.r(z)
        alpha=dome.alpha_from_clairaut(z,c)
        kn=dome.kappa_n(z,alpha)
        vmax=math.sqrt(self.a_max/kn) if kn>1e-9 else float("inf")
        return GeodesicPoint(s_mm=s,z_mm=z,phi_rad=phi,r_mm=r,
            alpha_rad=alpha,kappa_n=kn,v_max_mm_s=vmax,is_turnaround=is_ta)

    # --- Full circuit ---
    def integrate_circuit(self, phi_start:float=0.0, z_start:float=0.0,
                          s_max_half:float=3000.0)->GeodesicPath:
        """Ekvatör→pol→ekvatör tam devre."""
        path=GeodesicPath(c_clairaut=self.c, phi_start_rad=phi_start)

        s_f,z_f,phi_f,rp=self._forward(phi_start,z_start,s_max_half)
        if len(s_f)==0: return path

        z_ta=float(z_f[-1]); phi_ta=float(phi_f[-1]); s_ta=float(s_f[-1])
        path.z_turnaround=z_ta; path.phi_turnaround=phi_ta

        # Forward points
        for i,(s,z,phi) in enumerate(zip(s_f,z_f,phi_f)):
            path.points.append(self._make_pt(s,z,phi,is_ta=(i==len(s_f)-1)))

        # Return points
        s_r,z_r,phi_r,re=self._return(phi_ta,z_ta,z_start,s_ta,s_max_half)
        for s,z,phi in zip(s_r,z_r,phi_r):
            path.points.append(self._make_pt(s,z,phi))

        path.converged=rp and re
        return path

    # --- Multi-circuit layer ---
    def integrate_layer(self, n_circuits:int, phi_offsets:Optional[np.ndarray]=None,
                        z_start:float=0.0, s_max_half:float=3000.0)->List[GeodesicPath]:
        """n_circuits devre integre et."""
        paths=[]
        # İlk devre (K hesapla)
        p0=self.integrate_circuit(0.0,z_start,s_max_half)
        if not p0.points: return paths
        paths.append(p0)
        K=p0.phi_total_rad

        if phi_offsets is None:
            phi_offsets=np.arange(1,n_circuits)*K
        for phi_off in phi_offsets[:n_circuits-1]:
            p=self.integrate_circuit(float(phi_off),z_start,s_max_half)
            paths.append(p)
        return paths
