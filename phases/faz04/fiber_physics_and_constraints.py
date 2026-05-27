"""
fiber_physics_and_constraints.py — Fiber Fiziği ve Süreç Kısıtları
====================================================================
Modüller:
  FiberPhysics          — slip riski, temas stabilitesi
  CurvatureAwareFeedrateAdapter — eğrilik-uyarlamalı hız profili
  ProcessConstraints    — makine limitleri (dome için)
  DomeCoverageAnalyzer  — pol yoğunluğu, kalınlık dağılımı, clustering

FİBER FİZİĞİ — Geodesik vs Non-Geodesik:
  Geodesik: κ_g = 0 → slip riski yok (μ > 0 olduğu sürece)
  Non-geodesik: slip koşulu |κ_g/κ_n| ≤ μ
    λ = κ_g/κ_n = slippage tendency coefficient
    Stability: |λ| ≤ μ (ICCM8 Eq.6)

  Temas kuvveti/uzunluk:
    q_n = T·κ_n   [N/mm]
    q_n > 0: fiber yüzeye basıyor (stabil)
    q_n < 0: bridging (fiber yüzeyden kalkar)

EĞRİLİK-UYARLAMALI HIZ:
  Centripetal ivme: a_c = v²·κ_n
  Limit: a_c ≤ a_centripetal_max
  v_max(s) = √(a_max/κ_n(s))

  Pol yakınında κ_n büyük → v_max küçük → zorunlu yavaşlama

  Spindle RPM limiti (dome'da değişken):
    ω(z) = v·c/r(z)²
    n_rpm(z) = ω·60/(2π) = v·c·60/(2π·r(z)²)
    r→r_pole: rpm → MAX (kritik!)

  Carriage speed limit:
    v_z(z) = v·√(r²-c²)/(r·√G)
    r→R (ekvatör): v_z ≈ v·cos(α)  (silindir formülüne döner)
    r→c (pol): v_z → 0

DOME KAPLAMA ANALİZİ:
  Pol yoğunluğu: ρ(z) = k·b/(2π·r(z)·cos(α(z)))
    ρ=1: tam kaplama
    ρ>1: overlap (pol yakınında kaçınılmaz)
    ρ→∞: pol singularitesi

  Polar boss zorunluluğu:
    r_pole_min = k·b/(2π)  [yaklaşık, α=0 için]
    Pratik: r_pole ≥ 1.22·r₀ (α=54° inflection, AIAA 2005)

Referans: ICCM8 (Di Vita et al.); Koussios (2004); filamentwindingshapeoptimization.pdf
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from dome_mandrel import DomeMandrel
from geodesic_integrator import GeodesicPath, GeodesicPoint
from machine_model import MachinePhysics


# ============================================================================
# 1. FIBER PHYSICS
# ============================================================================

@dataclass(slots=True)
class FiberStabilityResult:
    """Yol boyunca fiber stabilite analizi sonucu."""
    is_geodesic:       bool
    mu_friction:       float       # Sürtünme katsayısı
    max_slip_tendency: float       # max |λ| = |κ_g/κ_n|
    n_slip_points:     int         # λ > μ olan nokta sayısı
    n_bridging_points: int         # κ_n < 0 olan nokta sayısı
    min_contact_force: float       # min q_n = T·κ_n [N/mm]
    overall_stable:    bool
    critical_z_mm:     List[float] # Kritik z konumları

    def report(self)->str:
        return (
            f"  Fiber Stabilite Analizi\n"
            f"  {'Geodezik' if self.is_geodesic else 'Non-Geodezik'}  μ={self.mu_friction}\n"
            f"  Max slip tendency |λ|: {self.max_slip_tendency:.4f}  "
            f"{'< μ ✓' if self.max_slip_tendency<self.mu_friction else '> μ ✗ KAYMA!'}\n"
            f"  Bridging noktaları: {self.n_bridging_points}  "
            f"{'uyarı: köprüleme!' if self.n_bridging_points>0 else 'OK'}\n"
            f"  Min temas kuvveti: {self.min_contact_force:.4f} N/mm\n"
            f"  {'✓ STABİL' if self.overall_stable else '✗ STABIL DEĞİL'}"
        )


class FiberPhysics:
    """
    Fiber yolu stabilite ve temas fiziği.

    Geodezik yollar için λ=0 (Clairaut koşulundan), bu sınıf
    özellikle şunları hesaplar:
      1. κ_n profili → temas kuvveti profili
      2. Bridging risk bölgeleri
      3. Pol yakınında hız adaptasyonu gereksinimi
    """

    def __init__(
        self,
        dome:        DomeMandrel,
        mu_friction: float = 0.2,     # Cam elyaf / mandrel sürtünmesi
        tension_N:   float = 15.0,    # Fiber sarım gerilmesi [N]
    ) -> None:
        self.dome       = dome
        self.mu         = mu_friction
        self.tension    = tension_N

    def analyze_path(
        self,
        path:        GeodesicPath,
        is_geodesic: bool = True,
    ) -> FiberStabilityResult:
        """
        Yol boyunca fiber stabilite analizi.
        """
        if not path.points:
            return FiberStabilityResult(
                is_geodesic=is_geodesic, mu_friction=self.mu,
                max_slip_tendency=0.0, n_slip_points=0,
                n_bridging_points=0, min_contact_force=0.0,
                overall_stable=False, critical_z_mm=[])

        kn_values  = np.array([p.kappa_n for p in path.points])
        q_n_values = self.tension * kn_values

        # Geodezik: λ = 0 her zaman (Clairaut → κ_g=0)
        # Non-geodesic için λ = f(path) — henüz Faz 3'te basit tutuluyor
        max_lambda = 0.0 if is_geodesic else 0.0  # genişletme için yer

        n_bridge   = int(np.sum(kn_values < -1e-9))
        n_slip     = int(np.sum(np.abs(kn_values) > 0) and max_lambda > self.mu)
        min_qn     = float(q_n_values.min()) if len(q_n_values) > 0 else 0.0

        critical_z = [p.z_mm for p in path.points if p.kappa_n < -1e-9]
        overall    = (max_lambda <= self.mu and n_bridge == 0)

        return FiberStabilityResult(
            is_geodesic       = is_geodesic,
            mu_friction       = self.mu,
            max_slip_tendency = max_lambda,
            n_slip_points     = n_slip,
            n_bridging_points = n_bridge,
            min_contact_force = min_qn,
            overall_stable    = overall,
            critical_z_mm     = critical_z,
        )

    def polar_congestion_analysis(
        self,
        c:          float,
        k:          int,
        bandwidth:  float,
        z_arr:      Optional[np.ndarray] = None,
    ) -> dict:
        """
        z boyunca pol yoğunluk profili.

        ρ(z) = k·b / (2π·r(z)·cos(α(z)))

        Returns:
            {"z": arr, "r": arr, "alpha_deg": arr, "rho": arr,
             "congestion_onset_z": float, "polar_boss_min_r": float}
        """
        dome = self.dome
        if z_arr is None:
            z_arr = np.linspace(0.0, dome.height_mm * 0.98, 150)

        z_out=[]; r_out=[]; al_out=[]; rho_out=[]

        for z in z_arr:
            r = dome.r(z)
            if r < c: break
            alpha = dome.alpha_from_clairaut(z, c)
            cos_a = math.cos(alpha)
            if cos_a < 1e-6:
                rho = float("inf")
            else:
                rho = k * bandwidth / (2.0 * math.pi * r * cos_a)
            z_out.append(z); r_out.append(r)
            al_out.append(math.degrees(alpha)); rho_out.append(rho)

        z_arr_out = np.array(z_out); rho_arr = np.array(rho_out)
        r_arr_out = np.array(r_out)

        # Congestion onset: ρ ilk > 1 geçtiği yer
        onset_idx = np.where(rho_arr > 1.0)[0]
        congestion_onset_z = float(z_arr_out[onset_idx[0]]) if len(onset_idx)>0 else float(dome.height_mm)

        # Polar boss minimum yarıçapı: ρ=1 koşulundan
        # k·b = 2π·r_pb·1  → r_pb = k·b/(2π)
        r_pb_min = k * bandwidth / (2.0 * math.pi)

        return {
            "z":                  z_arr_out,
            "r":                  r_arr_out,
            "alpha_deg":          np.array(al_out),
            "rho":                rho_arr,
            "congestion_onset_z": congestion_onset_z,
            "polar_boss_min_r":   r_pb_min,
        }


# ============================================================================
# 2. CURVATURE-AWARE FEEDRATE ADAPTER
# ============================================================================

@dataclass(slots=True)
class FeedrateProfile:
    """Yol boyunca fiber hızı profili."""
    s_arr:     np.ndarray   # arc-length [mm]
    z_arr:     np.ndarray   # eksenel konum [mm]
    v_arr:     np.ndarray   # fiber hızı S'(s) [mm/s]
    v_x_arr:   np.ndarray   # carriage hızı [mm/s]
    rpm_arr:   np.ndarray   # spindle rpm
    kn_arr:    np.ndarray   # normal eğrilik [1/mm]
    v_limit_arr: np.ndarray # curvature limit [mm/s]

    @property
    def v_mean(self)->float: return float(np.mean(self.v_arr))
    @property
    def v_min(self)->float: return float(np.min(self.v_arr))
    @property
    def v_max(self)->float: return float(np.max(self.v_arr))
    @property
    def rpm_max(self)->float: return float(np.max(self.rpm_arr))
    @property
    def v_x_max(self)->float: return float(np.max(self.v_x_arr))

    def constraint_violations(self, v_max_machine:float, rpm_max_machine:float) -> dict:
        return {
            "v_exceed":   int(np.sum(self.v_arr > v_max_machine * 1.02)),
            "rpm_exceed": int(np.sum(self.rpm_arr > rpm_max_machine * 1.02)),
        }

    def summary(self)->str:
        return (
            f"  FeedrateProfile: n={len(self.s_arr)}\n"
            f"  v: min={self.v_min:.1f}  mean={self.v_mean:.1f}  max={self.v_max:.1f} mm/s\n"
            f"  rpm_max={self.rpm_max:.2f}\n"
            f"  v_x_max={self.v_x_max:.2f} mm/s\n"
        )


class CurvatureAwareFeedrateAdapter:
    """
    Dome yolunda eğrilik-uyarlamalı fiber hız profili üretir.

    Algoritma:
      1. Her nokta için κ_n hesapla
      2. v_curvature_limit(s) = √(a_centripetal_max / κ_n(s))
      3. v_rpm_limit(s) = rpm_max · 2π·r(z)² / (c · 60)
      4. v_carriage_limit(s) = v_x_max · r·√G / √(r²-c²)
      5. v(s) = min(v_target, v_curvature, v_rpm, v_carriage)
      6. Smoothing (jerk limiti)
    """

    def __init__(
        self,
        dome:            DomeMandrel,
        machine:         MachinePhysics,
        c_clairaut:      float,
        v_target:        float = 100.0,   # [mm/s] hedef nominal hız
        a_centripetal:   float = 5000.0,  # [mm/s²] curvature limit
        jerk_limit:      float = 1000.0,  # [mm/s³] hız değişim hızı limiti
        polar_slowdown_factor: float = 0.3,  # Pol yakınında min hız oranı
    ) -> None:
        self.dome        = dome
        self.machine     = machine
        self.c           = c_clairaut
        self.v_target    = v_target
        self.a_cent      = a_centripetal
        self.jerk_limit  = jerk_limit
        self.polar_sf    = polar_slowdown_factor

    def compute_profile(self, path: GeodesicPath) -> FeedrateProfile:
        """
        GeodesicPath için tam hız profili hesapla.
        """
        if not path.points:
            empty = np.array([])
            return FeedrateProfile(empty,empty,empty,empty,empty,empty,empty)

        dome    = self.dome
        machine = self.machine
        c       = self.c
        R       = dome.equator_radius_mm

        # Makine limitleri
        v_x_max  = machine.carriage_max_speed_mm_s()
        rpm_max  = machine.spindle_max_speed_rpm()

        n = len(path.points)
        s_arr    = np.array([p.s_mm for p in path.points])
        z_arr    = np.array([p.z_mm for p in path.points])
        kn_arr   = np.array([p.kappa_n for p in path.points])
        r_arr    = np.array([dome.r(z) for z in z_arr])
        G_arr    = np.array([dome.G(z) for z in z_arr])

        # 1. Curvature limit
        v_curv   = np.where(kn_arr > 1e-9,
                            np.sqrt(self.a_cent / np.maximum(kn_arr, 1e-9)),
                            self.v_target)
        v_curv   = np.clip(v_curv, self.v_target*self.polar_sf, self.v_target*3.0)

        # 2. RPM limit: v ≤ rpm_max·2π·r²/(c·60)
        #    n_rpm = v·c·60/(2π·r²) ≤ rpm_max
        r2_arr   = r_arr**2
        v_rpm    = np.where(c > 1e-6,
                            rpm_max * 2.0 * math.pi * r2_arr / (c * 60.0),
                            np.full(n, self.v_target))
        v_rpm    = np.clip(v_rpm, self.v_target*self.polar_sf, float("inf"))

        # 3. Carriage limit: v_z = v·√(r²-c²)/(r·√G) ≤ v_x_max
        r2c2     = np.maximum(0.0, r2_arr - c**2)
        denom    = np.where(r_arr > c+1e-3, r_arr*np.sqrt(G_arr), 1e9)
        coeff    = np.sqrt(r2c2) / denom
        v_carr   = np.where(coeff > 1e-6, v_x_max / coeff, self.v_target*3.0)

        # 4. Minimum
        v_raw    = np.minimum(np.minimum(self.v_target, v_curv),
                              np.minimum(v_rpm, v_carr))
        v_raw    = np.maximum(v_raw, self.v_target * self.polar_sf)

        # 5. Smoothing (jerk limit — first-order)
        v_smooth = v_raw.copy()
        if len(v_smooth) > 1:
            ds_arr = np.diff(s_arr)
            for i in range(1, len(v_smooth)):
                ds = ds_arr[i-1] if i-1 < len(ds_arr) else 1.0
                if ds < 1e-6: continue
                dv_max = self.jerk_limit * ds / v_smooth[i-1] if v_smooth[i-1]>0 else 1.0
                v_smooth[i] = min(v_smooth[i], v_smooth[i-1] + dv_max)

        # 6. Türetilmiş büyüklükler
        v_x_arr  = v_smooth * np.sqrt(r2c2) / denom
        rpm_arr  = np.where(c > 1e-6,
                            v_smooth * c * 60.0 / (2.0 * math.pi * r2_arr),
                            np.zeros(n))

        return FeedrateProfile(
            s_arr       = s_arr,
            z_arr       = z_arr,
            v_arr       = v_smooth,
            v_x_arr     = v_x_arr,
            rpm_arr     = rpm_arr,
            kn_arr      = kn_arr,
            v_limit_arr = v_curv,
        )


# ============================================================================
# 3. PROCESS CONSTRAINTS (DOME)
# ============================================================================

@dataclass(slots=True)
class DomeConstraintReport:
    """Dome winding süreç kısıtları özet raporu."""
    alpha_equator_deg:    float
    c_clairaut_mm:        float
    pole_boss_min_r_mm:   float
    max_rpm_at_pole:      float
    max_rpm_machine:      float
    rpm_ok:               bool
    max_kappa_n:          float
    min_v_curvature_mm_s: float
    bridging_zones:       int
    congestion_onset_z:   float
    overall_feasible:     bool

    def report(self)->str:
        lines=[
            "  DOME SÜREÇ KISIT RAPORU",
            f"  α_ekvatör = {self.alpha_equator_deg:.3f}°",
            f"  c (Clairaut) = {self.c_clairaut_mm:.4f} mm",
            f"  Polar boss min r ≥ {self.pole_boss_min_r_mm:.4f} mm",
            f"  Max κ_n = {self.max_kappa_n:.5f} /mm",
            f"  Min curvature hız = {self.min_v_curvature_mm_s:.2f} mm/s",
            f"  Max spindle @ pol = {self.max_rpm_at_pole:.2f} rpm  [limit: {self.max_rpm_machine:.0f}]",
            f"  RPM: {'✓ OK' if self.rpm_ok else '✗ AŞILIYOR'}",
            f"  Bridging bölgesi: {self.bridging_zones}",
            f"  Congestion başlangıcı: z={self.congestion_onset_z:.1f}mm",
            f"  {'✓ ÜRETİLEBİLİR' if self.overall_feasible else '✗ PROBLEM VAR'}",
        ]
        return "\n".join(lines)


class ProcessConstraints:
    """Dome winding süreç kısıtları değerlendirici."""

    def __init__(
        self,
        dome:      DomeMandrel,
        machine:   MachinePhysics,
        physics:   FiberPhysics,
    ) -> None:
        self.dome    = dome
        self.machine = machine
        self.physics = physics

    def evaluate(
        self,
        path:      GeodesicPath,
        feedrate:  FeedrateProfile,
        k:         int,
        bandwidth: float,
    ) -> DomeConstraintReport:
        """Tam dome kısıt değerlendirmesi."""
        c        = path.c_clairaut
        rpm_lim  = self.machine.spindle_max_speed_rpm()

        # Pol yakınında max rpm
        r_pole   = self.dome.r_stop = c * 1.01 if hasattr(self,'_r_stop') else c*1.01
        r_pole_r = max(c * 1.001, self.dome.polar_radius_mm + 0.01) if self.dome.polar_radius_mm > 0 else c*1.002
        # rpm at pole = v_min · c / (2π · r_pole²) · 60
        v_at_pole = feedrate.v_min if feedrate.v_arr.size>0 else 100.0
        rpm_at_pole = v_at_pole * c * 60.0 / (2.0*math.pi*r_pole_r**2)

        # Pol yoğunluk analizi
        cong = self.physics.polar_congestion_analysis(c, k, bandwidth)

        # Bridging
        stability = self.physics.analyze_path(path)
        n_bridge  = stability.n_bridging_points

        return DomeConstraintReport(
            alpha_equator_deg    = path.alpha_equator_deg,
            c_clairaut_mm        = c,
            pole_boss_min_r_mm   = cong["polar_boss_min_r"],
            max_rpm_at_pole      = rpm_at_pole,
            max_rpm_machine      = rpm_lim,
            rpm_ok               = rpm_at_pole <= rpm_lim * 1.05,
            max_kappa_n          = path.max_kappa_n(),
            min_v_curvature_mm_s = path.min_v_max(),
            bridging_zones       = n_bridge,
            congestion_onset_z   = cong["congestion_onset_z"],
            overall_feasible     = (rpm_at_pole <= rpm_lim*1.05 and n_bridge == 0),
        )


# ============================================================================
# 4. DOME COVERAGE ANALYZER
# ============================================================================

@dataclass(slots=True)
class DomeCoverageResult:
    """Dome kaplama analizi sonucu."""
    z_arr:           np.ndarray
    r_arr:           np.ndarray
    alpha_deg_arr:   np.ndarray
    rho_arr:         np.ndarray    # ρ(z) pol yoğunluğu
    thickness_arr:   np.ndarray    # t(z) [mm]
    global_cv:       float
    axial_cv:        float
    pole_rho_max:    float
    gap_fraction:    float
    homogeneity_score: float

    def summary(self)->str:
        return (
            f"  Dome Kaplama: n={len(self.z_arr)} zon\n"
            f"  Pol yoğunluğu (max): {self.pole_rho_max:.2f}x\n"
            f"  Global CV: {self.global_cv:.4f}\n"
            f"  Eksenel CV: {self.axial_cv:.4f}\n"
            f"  Gap oranı: {self.gap_fraction*100:.1f}%\n"
            f"  Homojenlik: {self.homogeneity_score:.4f}"
        )

    def print_thickness_profile(self, n_bars:int=30)->None:
        """ASCII kalınlık profili."""
        t = self.thickness_arr
        max_t = t.max() if t.max()>0 else 1.0
        print(f"\n  Dome Kalınlık Profili (z: 0→{self.z_arr[-1]:.0f}mm)")
        bar_w=25
        for i in range(min(n_bars, len(self.z_arr))):
            idx=int(i*len(self.z_arr)/n_bars)
            z=self.z_arr[idx]; r=self.r_arr[idx]
            ti=t[idx]; al=self.alpha_deg_arr[idx]; rho=self.rho_arr[idx]
            n=int(ti/max_t*bar_w)
            bar="█"*n+"░"*(bar_w-n)
            print(f"  z={z:5.1f}mm r={r:5.1f}mm │{bar}│{ti:.3f}mm "
                  f"α={al:.1f}° ρ={rho:.2f}{'⚠' if rho>2.0 else ''}")


class DomeCoverageAnalyzer:
    """
    Dome kaplama kalınlığı ve homojenlik analizi.

    Zonal hesaplama (z boyunca):
      Paralel r(z)'de k fiber band:
      t(z) = k·b / (2π·r(z)·cos(α(z))) · t_fiber
      (Her bir hücre üzerinden geçen fiber sayısı × t_fiber)
    """

    def __init__(
        self,
        dome:          DomeMandrel,
        c_clairaut:    float,
        n_circuits:    int,
        bandwidth:     float,
        fiber_thickness: float = 0.25,  # [mm]
    ) -> None:
        self.dome        = dome
        self.c           = c_clairaut
        self.k           = n_circuits
        self.b           = bandwidth
        self.t_fiber     = fiber_thickness

    def analyze(self, n_zones:int=100) -> DomeCoverageResult:
        """Dome boyunca kaplama analizi."""
        dome=self.dome; c=self.c; k=self.k; b=self.b; t=self.t_fiber

        z_arr=np.linspace(0.0, dome.height_mm*0.98, n_zones)
        r_out=[]; al_out=[]; rho_out=[]; thick_out=[]

        for z in z_arr:
            r=dome.r(z)
            if r<c: break
            alpha=dome.alpha_from_clairaut(z,c)
            cos_a=math.cos(alpha)
            if cos_a<1e-6:
                rho=float("inf"); thick_val=float("inf")
            else:
                rho=k*b/(2.0*math.pi*r*cos_a)
                thick_val=rho*t   # [mm]
            r_out.append(r); al_out.append(math.degrees(alpha))
            rho_out.append(min(rho,10.0)); thick_out.append(min(thick_val,10.0))

        z_arr=z_arr[:len(r_out)]
        r_arr=np.array(r_out); al_arr=np.array(al_out)
        rho_arr=np.array(rho_out); thick_arr=np.array(thick_out)

        # Homojenlik
        valid=thick_arr[np.isfinite(thick_arr)&(thick_arr>0)]
        global_cv=float(np.std(valid)/np.mean(valid)) if len(valid)>1 else 0.0
        axial_cv=global_cv  # z boyunca = global (1D)
        gap_frac=float(np.mean(thick_arr==0)) if len(thick_arr)>0 else 0.0
        pole_rho=float(rho_arr.max()) if len(rho_arr)>0 else 0.0
        homog=max(0.0, 1.0-global_cv/2.0)

        return DomeCoverageResult(
            z_arr=z_arr, r_arr=r_arr,
            alpha_deg_arr=al_arr, rho_arr=rho_arr,
            thickness_arr=thick_arr,
            global_cv=global_cv, axial_cv=axial_cv,
            pole_rho_max=pole_rho,
            gap_fraction=gap_frac,
            homogeneity_score=homog,
        )
