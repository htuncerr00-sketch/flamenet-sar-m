"""
transition_manager.py — Dome-Cylinder Geçiş Yöneticisi
========================================================
Dome-cylinder junction'larında üç sorun analiz edilir ve çözülür:

1. CURVATURE DISCONTINUITY (C1 ama C2 değil):
   κ_m cylinder'da 0, dome equator'da ≠ 0.
   Ani κ_m değişimi → feedrate jump → reçine hatası.

   Çözüm: Cosine blending zone ±δ:
     κ_m_blend(z) = κ_m_dome(z)·(1-f(t)) + 0·f(t)
     f(t) = 0.5·(1 - cos(π·t)),  t ∈ [0,1]

2. JUNCTION THICKNESS ACCUMULATION:
   Fiberlerin dome'dan cylindere geçişinde, δφ/δz değişimi
   yerel yoğunluk artışına yol açar.

   Analitik: ρ(z) = k·b/(2π·r(z)·cos(α(z)))
   Junction'da: r ≈ R (sürekli), α ≈ α₀ (sürekli) → ρ sürekli.
   Ama: dome içinde z arttıkça r azalır, α artar → ρ artar.
   Bu gradyan junction bölgesinde konsantre olur.

3. FEEDRATE CONTINUITY:
   κ_n(z) değişimi → curvature-aware v_max(z) değişimi.
   Junction'da ani κ_n → ani feedrate.
   Blended κ_m ile bu düzeltilir.

Blending parametresi δ (transition half-width):
  Minimum δ: δ_min = √(R × b) (band genişliği × dome curvature)
  Önerilen: δ = 3 × b (3 band genişliği)

Referans: Koussios (2004) s.210; ICCM8 Di Vita et al.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import numpy as np
from full_body_mandrel import FullBodyMandrel, BodyRegion

# ── Cosine blend function ──────────────────────────────────────────
def cosine_blend(t: float) -> float:
    """Smooth S-curve: f(0)=0, f(1)=1, f'(0)=f'(1)=0."""
    return 0.5 * (1.0 - math.cos(math.pi * t))

# ── Data Structures ──────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class JunctionMetrics:
    """Tek bir dome-cylinder junction'ının analiz sonuçları."""
    label:              str     # "front" | "rear"
    z_junction:         float   # [mm]
    kappa_m_dome:       float   # Dome equator κ_m [1/mm]
    kappa_m_cylinder:   float   # = 0 always
    curvature_jump:     float   # |Δκ_m| [1/mm]
    required_blend_mm:  float   # önerilen δ [mm]
    thickness_gradient: float   # dρ/dz at junction [1/mm]
    feedrate_ratio:     float   # v_max_cyl / v_max_dome (curvature ratio)
    c1_ok:              bool    # r' continuous?
    c2_ok:              bool    # r'' continuous?

    def report(self) -> str:
        return (
            f"  [{self.label.upper()} JUNCTION] z={self.z_junction:.2f}mm\n"
            f"  κ_m dome={self.kappa_m_dome:.6f}  cyl=0  Δκ_m={self.curvature_jump:.6f}/mm\n"
            f"  Önerilen blend δ={self.required_blend_mm:.2f}mm\n"
            f"  Kalınlık gradyanı dρ/dz={self.thickness_gradient:.5f}/mm\n"
            f"  Feedrate ratio={self.feedrate_ratio:.3f}\n"
            f"  C1:{'✓' if self.c1_ok else '✗'}  C2:{'✗' if not self.c2_ok else '✓'}"
        )


@dataclass(slots=True)
class BlendedCurvature:
    """Blending zone içinde yumuşatılmış eğrilik profili."""
    z_arr:        np.ndarray
    kappa_m_raw:  np.ndarray   # Blending öncesi
    kappa_m_blend:np.ndarray   # Cosine blend sonrası
    kappa_c_arr:  np.ndarray
    blend_weight: np.ndarray   # f(t) ∈ [0,1]

    def kappa_n_blend(self, alpha_arr: np.ndarray) -> np.ndarray:
        ca = np.cos(alpha_arr); sa = np.sin(alpha_arr)
        return self.kappa_m_blend*ca**2 + self.kappa_c_arr*sa**2


@dataclass(slots=True)
class TransitionAnalysisResult:
    """Full-body geçiş analizi tam sonucu."""
    front_junction:  JunctionMetrics
    rear_junction:   JunctionMetrics
    blend_delta_mm:  float       # Kullanılan blend genişliği
    z_blend_front:   Tuple[float,float]  # (z_start, z_end)
    z_blend_rear:    Tuple[float,float]
    thickness_hotspot_z: List[float]  # Yoğunluk pik z konumları
    feedrate_smooth_ok:  bool

    def report(self) -> str:
        return (
            "  TRANSITION ANALİZİ\n"
            f"{self.front_junction.report()}\n"
            f"{self.rear_junction.report()}\n"
            f"  Blend δ={self.blend_delta_mm:.2f}mm  "
            f"Front zone=[{self.z_blend_front[0]:.1f},{self.z_blend_front[1]:.1f}]  "
            f"Rear zone=[{self.z_blend_rear[0]:.1f},{self.z_blend_rear[1]:.1f}]\n"
            f"  Thickness hotspots: {[f'{z:.1f}mm' for z in self.thickness_hotspot_z]}\n"
            f"  Feedrate smooth: {'✓' if self.feedrate_smooth_ok else '⚠ BLEND GEREKLİ'}"
        )


# ── Transition Manager ────────────────────────────────────────────

class TransitionManager:
    """
    Dome-cylinder junction analizi ve blending.

    Kullanım:
        tm = TransitionManager(body, bandwidth=10.0)
        result = tm.analyze(c=25.0, k=21)
        print(result.report())
        blended = tm.blend_curvature(z_arr, c=25.0)
    """

    def __init__(
        self,
        body:            FullBodyMandrel,
        bandwidth:       float,      # b [mm]
        blend_delta:     Optional[float] = None,  # δ override
        blend_multiplier:float = 3.0,
    ) -> None:
        self.body  = body
        self.b     = bandwidth
        # δ = blend_multiplier × b (default 3b)
        self._delta = blend_delta if blend_delta is not None else blend_multiplier * bandwidth
        self._delta = min(self._delta,
                          body.front.height_mm * 0.4,
                          body.L_cyl * 0.4 if body.L_cyl > 0 else 1e9)

    @property
    def blend_delta(self) -> float:
        return self._delta

    # ── Junction analysis ─────────────────────────────────────────
    def _analyze_junction(
        self,
        label:      str,
        z_junction: float,
        c:          float,
        k:          int,
        dome:       object,
    ) -> JunctionMetrics:
        """Single junction analysis."""
        R = self.body.R
        # κ_m at dome equator (z_local=0)
        km_dome   = dome.kappa_m(0.0)
        kc_dome   = dome.kappa_c(0.0)   # = 1/R
        km_jump   = abs(km_dome)          # cylinder κ_m = 0

        # C1/C2 checks
        c1_ok  = abs(dome.r_prime(0.0)) < 1e-6    # r'(0)=0 for all our domes
        c2_ok  = km_jump < 1e-9

        # Recommended blend width: based on curvature jump magnitude
        # δ_min = √(R × 1/κ_m_dome) if κ_m > 0
        delta_req = math.sqrt(R / max(km_jump, 1e-9)) if km_jump > 1e-9 else self._delta
        delta_rec = max(self._delta, delta_req * 0.3)

        # Thickness gradient at junction: dρ/dz = d/dz[k·b/(2π·r·cos(α))]
        # At z_junction, r=R, α=arcsin(c/R)
        alpha_eq = math.asin(min(1.0, c/R))
        cos_aeq  = math.cos(alpha_eq)
        rho_eq   = k * self.b / (2.0 * math.pi * R * cos_aeq)
        # Gradient: d(rho)/dz at dome side (z_local=0+)
        # dρ/dz ≈ (ρ(Δz) - ρ(0))/Δz using small Δz=0.5mm
        dz_eps   = 0.5
        r_eps    = dome.r(dz_eps)
        al_eps   = math.asin(min(1.0, c/r_eps)) if r_eps > c else math.pi/2
        cos_eps  = math.cos(al_eps)
        rho_eps  = k*self.b/(2*math.pi*r_eps*max(cos_eps,1e-9)) if r_eps > 1e-9 else 0.0
        dρ_dz    = (rho_eps - rho_eq) / dz_eps  # [1/mm]

        # Feedrate ratio: v_cyl / v_dome
        # Curvature-aware limit: v ∝ 1/√κ_n
        kn_cyl  = 0.0 + kc_dome  # κ_m=0, κ_c=1/R, α≈α_eq → κ_n ≈ κ_c·sin²(α_eq)
        kn_dome = dome.kappa_n(0.0, alpha_eq)
        ratio   = math.sqrt(kn_dome / max(kn_cyl, 1e-9)) if kn_cyl > 1e-9 else 1.0

        return JunctionMetrics(
            label            = label,
            z_junction       = z_junction,
            kappa_m_dome     = km_dome,
            kappa_m_cylinder = 0.0,
            curvature_jump   = km_jump,
            required_blend_mm= delta_rec,
            thickness_gradient = dρ_dz,
            feedrate_ratio   = ratio,
            c1_ok            = c1_ok,
            c2_ok            = c2_ok,
        )

    def analyze(self, c: float, k: int) -> TransitionAnalysisResult:
        """Full-body junction analizi."""
        body = self.body
        front_j = self._analyze_junction("front", body.z_fe, c, k, body.front)
        rear_j  = self._analyze_junction("rear",  body.z_re, c, k, body.rear)
        δ = self._delta

        # Blend zones
        z_bf = (body.z_fe - δ, body.z_fe + δ)
        z_br = (body.z_re - δ, body.z_re + δ)

        # Thickness hotspots: where ρ > 1.5
        hotspots = []
        for dz in np.linspace(0.0, min(δ*2, body.front.height_mm*0.5), 20):
            z = body.z_fe - dz
            rho = body.pole_congestion(z, c, k, self.b)
            if rho > 1.5:
                hotspots.append(z)
                break

        # Feedrate smooth: if ratio < 1.15, no blending needed
        smooth_ok = (front_j.feedrate_ratio < 1.15 and rear_j.feedrate_ratio < 1.15)

        return TransitionAnalysisResult(
            front_junction       = front_j,
            rear_junction        = rear_j,
            blend_delta_mm       = δ,
            z_blend_front        = z_bf,
            z_blend_rear         = z_br,
            thickness_hotspot_z  = hotspots,
            feedrate_smooth_ok   = smooth_ok,
        )

    # ── Blended curvature profile ─────────────────────────────────
    def blend_curvature(self, z_arr: np.ndarray, c: float) -> BlendedCurvature:
        """
        z_arr boyunca cosine-blended κ_m profili üret.

        Blend zones:
          Front: z ∈ [z_fe - δ, z_fe + δ]
          Rear:  z ∈ [z_re - δ, z_re + δ]
        """
        body  = self.body
        δ     = self._delta
        km_raw  = np.array([body.kappa_m(z) for z in z_arr])
        kc_arr  = np.array([body.kappa_c(z) for z in z_arr])
        km_b    = km_raw.copy()
        w_arr   = np.zeros(len(z_arr))

        for i, z in enumerate(z_arr):
            # Front junction blend
            t_f = (z - (body.z_fe - δ)) / (2*δ)
            if 0.0 <= t_f <= 1.0:
                f_val = cosine_blend(t_f)
                w_arr[i] = f_val
                # Blend: dome κ_m → 0 (cylinder)
                km_b[i] = km_raw[i] * (1.0 - f_val)

            # Rear junction blend
            t_r = (z - (body.z_re - δ)) / (2*δ)
            if 0.0 <= t_r <= 1.0:
                f_val = cosine_blend(t_r)
                w_arr[i] = f_val
                km_b[i] = km_raw[i] * f_val  # 0 → dome κ_m (entering rear dome)

        return BlendedCurvature(
            z_arr         = z_arr,
            kappa_m_raw   = km_raw,
            kappa_m_blend = km_b,
            kappa_c_arr   = kc_arr,
            blend_weight  = w_arr,
        )

    # ── Junction thickness growth model ──────────────────────────
    def junction_thickness_profile(
        self, c: float, k: int, t_fiber: float,
        n_pts: int = 80,
    ) -> dict:
        """
        Tüm body boyunca analitik kalınlık profili.
        t(z) = ρ(z) × t_fiber

        Returns {"z","rho","t","region"}
        """
        body = self.body
        zz   = np.linspace(0.0, body.Z_total, n_pts)
        rho_arr=[]; t_arr=[]; reg_arr=[]
        for z in zz:
            rho = body.pole_congestion(z, c, k, self.b)
            rho_arr.append(min(rho, 15.0))
            t_arr.append(min(rho, 15.0) * t_fiber)
            reg_arr.append(body.region_of(z).name)
        return {"z": zz, "rho": np.array(rho_arr),
                "t": np.array(t_arr), "region": reg_arr}
