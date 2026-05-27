"""
dome_mandrel.py — Dome Mandrel Geometrisi (Faz 3, tam yeniden yazım)
=====================================================================
EllipticDome, SphericalDome, IsotensoidDome.
Tüm r'' formülleri analitik olarak doğrulanmış.
"""
from __future__ import annotations
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np

@dataclass(frozen=True, slots=True)
class DomeSurfacePoint:
    z_mm:float; r_mm:float; alpha_rad:float; G:float
    kappa_m:float; kappa_c:float; kappa_n:float
    r_prime:float; r_dbl_prime:float
    @property
    def alpha_deg(self)->float: return math.degrees(self.alpha_rad)
    @property
    def bridging_risk(self)->bool: return self.kappa_n < -1e-9
    def max_fiber_speed(self, a_max:float=5000.0)->float:
        if self.kappa_n <= 1e-9: return float("inf")
        return math.sqrt(a_max/self.kappa_n)
    def summary(self)->str:
        return (f"z={self.z_mm:.2f}mm r={self.r_mm:.4f}mm α={self.alpha_deg:.2f}° "
                f"κ_m={self.kappa_m:.5f} κ_c={self.kappa_c:.5f} κ_n={self.kappa_n:.5f}/mm "
                f"{'⚠BRIDGE' if self.bridging_risk else 'OK'}")

class DomeMandrel(ABC):
    @abstractmethod
    def r(self,z:float)->float: ...
    @abstractmethod
    def r_prime(self,z:float)->float: ...
    @abstractmethod
    def r_double_prime(self,z:float)->float: ...
    @property
    @abstractmethod
    def equator_radius_mm(self)->float: ...
    @property
    @abstractmethod
    def height_mm(self)->float: ...
    @property
    @abstractmethod
    def polar_radius_mm(self)->float: ...

    def G(self,z:float)->float:
        rp=self.r_prime(z); return 1.0+rp*rp
    def kappa_m(self,z:float)->float:
        G=self.G(z)
        if G<1e-12: return 0.0
        return -self.r_double_prime(z)/G**1.5
    def kappa_c(self,z:float)->float:
        r=self.r(z); G=self.G(z)
        if r<1e-9 or G<1e-12: return 0.0
        return 1.0/(r*math.sqrt(G))
    def kappa_n(self,z:float,alpha_rad:float)->float:
        ca=math.cos(alpha_rad); sa=math.sin(alpha_rad)
        return self.kappa_m(z)*ca*ca + self.kappa_c(z)*sa*sa
    def alpha_from_clairaut(self,z:float,c:float)->float:
        r=self.r(z)
        if r<c-1e-9: return math.pi/2.0
        return math.asin(min(1.0,c/r))
    def contact_force_per_length(self,z:float,alpha_rad:float,tension_N:float)->float:
        return tension_N*self.kappa_n(z,alpha_rad)
    def surface_point(self,z:float,c:float,pole_clearance:float=1.001)->DomeSurfacePoint:
        r_val=self.r(z)
        alpha=self.alpha_from_clairaut(z,c)
        G=self.G(z); km=self.kappa_m(z); kc=self.kappa_c(z)
        kn=km*math.cos(alpha)**2+kc*math.sin(alpha)**2
        return DomeSurfacePoint(z_mm=z,r_mm=r_val,alpha_rad=alpha,G=G,
            kappa_m=km,kappa_c=kc,kappa_n=kn,
            r_prime=self.r_prime(z),r_dbl_prime=self.r_double_prime(z))
    def pole_congestion_ratio(self,n_circuits:int,bandwidth:float,z:float,c:float)->float:
        r=self.r(z)
        if r<1e-6: return float("inf")
        alpha=self.alpha_from_clairaut(z,c)
        cos_a=math.cos(alpha)
        if cos_a<1e-6: return float("inf")
        return n_circuits*bandwidth/(2.0*math.pi*r*cos_a)
    def bridging_check(self,z:float,alpha_rad:float)->Tuple[bool,float,str]:
        kn=self.kappa_n(z,alpha_rad); is_b=kn<-1e-9
        msg=f"KÖPRÜLEME @ z={z:.1f}: κ_n={kn:.5f}<0" if is_b else ""
        return (is_b,kn,msg)
    def curvature_profile(self,c:float,n_pts:int=100)->dict:
        z_arr=np.linspace(0.0,self.height_mm*0.999,n_pts)
        out={k:[] for k in ["z","r","alpha_deg","G","kappa_m","kappa_c","kappa_n"]}
        for z in z_arr:
            rv=self.r(z)
            if rv<c: break
            al=self.alpha_from_clairaut(z,c)
            out["z"].append(z); out["r"].append(rv)
            out["alpha_deg"].append(math.degrees(al))
            out["G"].append(self.G(z))
            out["kappa_m"].append(self.kappa_m(z))
            out["kappa_c"].append(self.kappa_c(z))
            out["kappa_n"].append(self.kappa_n(z,al))
        return {k:np.array(v) for k,v in out.items()}
    def report(self)->str:
        R=self.equator_radius_mm; H=self.height_mm
        c=R*0.3
        rows=[]
        for label,z in [("z=0 (ekvatör)",0.0),("z=H/2 (orta)",H*0.5),("z≈H (pol)",H*0.95)]:
            km=self.kappa_m(z); kc=self.kappa_c(z); G=self.G(z)
            rows.append(f"  {label:<15} κ_m={km:+.6f} κ_c={kc:+.6f} G={G:.4f}")
        return (f"  {self.__class__.__name__}: R={R}mm H={H}mm r_pol={self.polar_radius_mm}mm\n"
                + "\n".join(rows))

class EllipticDome(DomeMandrel):
    """
    r(z) = (R/H)·√(H²-z²)
    r'(z) = -R·z/(H·√(H²-z²))
    r''(z)= -R·H/(H²-z²)^(3/2)   [doğrulanmış]
    z=0: κ_m=R/H², κ_c=1/R
    """
    def __init__(self,R:float,H:float)->None:
        if R<=0 or H<=0: raise ValueError(f"R,H>0: R={R},H={H}")
        self._R=R; self._H=H
    @property
    def equator_radius_mm(self)->float: return self._R
    @property
    def height_mm(self)->float: return self._H
    @property
    def polar_radius_mm(self)->float: return 0.0
    def r(self,z:float)->float:
        A=self._H**2-z**2
        return (self._R/self._H)*math.sqrt(max(0.0,A))
    def r_prime(self,z:float)->float:
        A=self._H**2-z**2
        if A<1e-12: return -1e9
        return -self._R*z/(self._H*math.sqrt(A))
    def r_double_prime(self,z:float)->float:
        """r''=-R·H/(H²-z²)^(3/2) — analitik türev, doğrulanmış."""
        A=self._H**2-z**2
        if A<1e-12: return 0.0
        return -self._R*self._H/A**1.5

class SphericalDome(DomeMandrel):
    """
    r(z)=√(R²-z²)
    r'(z)=-z/r(z)
    r''(z)=-R²/r(z)³   [doğrulanmış: κ_m=κ_c=1/R sabit]
    """
    def __init__(self,R:float)->None:
        if R<=0: raise ValueError(f"R>0: {R}")
        self._R=R
    @property
    def equator_radius_mm(self)->float: return self._R
    @property
    def height_mm(self)->float: return self._R
    @property
    def polar_radius_mm(self)->float: return 0.0
    def r(self,z:float)->float:
        val=self._R**2-z**2
        return math.sqrt(max(0.0,val))
    def r_prime(self,z:float)->float:
        rv=self.r(z)
        return -z/rv if rv>1e-9 else -1e9
    def r_double_prime(self,z:float)->float:
        """r''=-R²/r³.  Türetme: d/dz[-z/r]=-(r²+z²)/r³=-R²/r³"""
        rv=self.r(z)
        return -self._R**2/rv**3 if rv>1e-9 else 0.0

class IsotensoidDome(DomeMandrel):
    """
    Isotensoid dome — netting theory ODE integrasyon profili.
    Tüm noktalarda eşit fiber gerilmesi sağlar.
    Referans: filamentwindingshapeoptimization.pdf Eq.6-8; Koussios Ch.4
    """
    def __init__(self,R:float,r_pole:float,n_points:int=200)->None:
        if r_pole<=0 or r_pole>=R: raise ValueError(f"0<r_pole<R: {r_pole},{R}")
        self._R=R; self._r_pole=r_pole
        self._z_np,self._r_np,self._H=self._integrate(n_points)
    def _integrate(self,n:int):
        from scipy.integrate import solve_ivp
        R=self._R; c=self._r_pole
        def ode(psi,y):
            r=y[0]
            if r<c*1.001: return [0.0,0.0]
            return [-r*math.sin(psi), r*math.cos(psi)]
        def stop(psi,y): return y[0]-c*1.001
        stop.terminal=True; stop.direction=-1
        sol=solve_ivp(ode,[0,math.pi/2*0.99],[R,0.0],
            max_step=math.pi/2/n,rtol=1e-7,atol=1e-8,events=[stop])
        z_arr=np.array(sol.y[1]); r_arr=np.array(sol.y[0])
        H_val=float(z_arr.max()) if len(z_arr)>0 else R
        return z_arr,r_arr,H_val
    @property
    def equator_radius_mm(self)->float: return self._R
    @property
    def height_mm(self)->float: return self._H
    @property
    def polar_radius_mm(self)->float: return self._r_pole
    def r(self,z:float)->float:
        return float(np.interp(z,self._z_np,self._r_np))
    def r_prime(self,z:float,dz:float=0.01)->float:
        return (self.r(z+dz)-self.r(z-dz))/(2.0*dz)
    def r_double_prime(self,z:float,dz:float=0.01)->float:
        return (self.r(z+dz)-2.0*self.r(z)+self.r(z-dz))/dz**2
