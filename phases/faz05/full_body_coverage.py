"""
full_body_coverage.py — Full-Body Kaplama ve Kalınlık Analizi
=============================================================
3D kalınlık dağılımı: t(z, φ) [mm] — gerçek basınçlı kap davranışı.

Kalınlık modeli (analitik zonal model + yol tabanlı):
  Her z pozisyonunda k fiber band geçer (k=circuit sayısı).
  Yerel kaplama oranı:
    ρ(z,φ) = k·b / (2π·r(z)·cos(α(z)))  [boyutsuz]
  Yerel kalınlık:
    t(z) = ρ(z) · t_fiber  [mm]

Bu analiz birleşik bir 3D thickness field üretir:
  ThicknessField: (N_z × N_phi) array
    - t[iz, iphi] = yerel kalınlık [mm]
    - density_map: t / t_nominal
    - hotspot_map: t > 1.5 × t_mean → kalınlık patlaması uyarısı

Kritik junction bölgesi:
  Cylinder kalınlığı: t_cyl = k·b·t_f/(2π·R·cos(α₀))
  Dome equator kalınlığı: aynı formül → süreklİ!
  Dome iç bölge: r azalır → ρ artar → t artar
  Pol bölgesi: ρ → ∞ → polar boss zorunlu

Density equalization hedefi:
  t(z=H_dome/2) ≤ 1.2 × t_cyl  ("20% allowable buildup")
  İhlal → daha büyük c (Clairaut) veya k azaltma gerekli

Junction gradient (thickness ramp):
  dρ/dz at z=z_fe: ρ giderek artarken, junction bölgesinde
  dt/dz ≠ 0 → stress concentration → dome failure location!

Referans: index.pdf ("dome failure most critical"); Koussios (2004) s.127
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np
from full_body_mandrel import FullBodyMandrel, BodyRegion
from transition_manager import TransitionManager
from unified_geodesic_planner import FullBodyPath, FullBodyPoint

# ── Thickness Field ──────────────────────────────────────────────

@dataclass(slots=True)
class ThicknessField:
    """
    Full-body 2D thickness field t(z, φ).

    For the analytical model (uniform φ): t is independent of φ.
    For path-based model: small φ variations exist near junctions.
    """
    z_arr:       np.ndarray    # (N_z,) eksenel grid
    phi_arr:     np.ndarray    # (N_phi,) azimut grid
    t_matrix:    np.ndarray    # (N_z, N_phi) kalınlık [mm]
    region_arr:  List[str]     # (N_z,) region labels
    t_nominal:   float         # Silindir bölgesi nominal kalınlık [mm]
    c_clairaut:  float
    k_circuits:  int

    @property
    def N_z(self): return len(self.z_arr)
    @property
    def N_phi(self): return len(self.phi_arr)

    @property
    def t_axial(self) -> np.ndarray:
        """z boyunca azimut ortalaması [mm]."""
        return self.t_matrix.mean(axis=1)

    @property
    def t_max(self): return float(self.t_matrix.max())
    @property
    def t_mean(self): return float(self.t_matrix[self.t_matrix > 0].mean())
    @property
    def t_min_nonzero(self): return float(self.t_matrix[self.t_matrix > 0].min())

    @property
    def density_map(self) -> np.ndarray:
        """ρ = t / t_nominal."""
        return self.t_matrix / max(self.t_nominal, 1e-9)

    def hotspot_mask(self, threshold: float = 1.5) -> np.ndarray:
        """ρ > threshold olan hücreler."""
        return self.density_map > threshold

    @property
    def global_cv(self) -> float:
        t_flat = self.t_matrix[self.t_matrix > 0].flatten()
        return float(np.std(t_flat)/np.mean(t_flat)) if len(t_flat)>1 else 0.0

    def junction_gradient(self, z_junction: float, dz: float = 2.0) -> float:
        """
        z_junction noktasındaki axial thickness gradient [mm/mm].
        """
        t_ax = self.t_axial
        iz_j = int(np.argmin(np.abs(self.z_arr - z_junction)))
        iz_p = min(iz_j + int(dz/((self.z_arr[-1]-self.z_arr[0])/self.N_z)), self.N_z-1)
        iz_m = max(iz_j - int(dz/((self.z_arr[-1]-self.z_arr[0])/self.N_z)), 0)
        dz_act = self.z_arr[iz_p] - self.z_arr[iz_m]
        return (t_ax[iz_p] - t_ax[iz_m]) / max(dz_act, 1e-6)

    def print_profile(self, n_bars: int = 35, hotspot_thresh: float = 1.5) -> None:
        """ASCII thickness bar chart."""
        t_ax   = self.t_axial
        rho_ax = t_ax / max(self.t_nominal, 1e-9)
        max_t  = max(t_ax.max(), 1e-9)
        bar_w  = 25

        print(f"\n  Full-Body Kalınlık Profili  t_nom={self.t_nominal:.3f}mm")
        print(f"  {'z':>6}  {'r':>5}  {'reg':>5}  {'t [mm]':>8}  {'ρ':>6}  Profil")
        print("  " + "─"*65)
        step = max(1, self.N_z // n_bars)
        for i in range(0, self.N_z, step):
            z   = self.z_arr[i]
            t   = t_ax[i]
            ρ   = rho_ax[i]
            reg = self.region_arr[i][:2]
            n   = int(t/max_t*bar_w)
            bar = "█"*n + "░"*(bar_w-n)
            hot = "⚠" if ρ > hotspot_thresh else " "
            print(f"  {z:>6.1f}  {reg:>5}  {t:>8.4f}  {ρ:>6.2f}  {bar}{hot}")


@dataclass(slots=True)
class CoverageAnalysisResult:
    """Full-body kaplama analizi tam sonucu."""
    thickness_field:     ThicknessField
    junction_gradient_front: float   # dt/dz at front junction
    junction_gradient_rear:  float
    pole_thickness_front:float       # dome'da max t (pol yakını)
    pole_thickness_rear: float
    cylinder_thickness:  float
    buildup_factor_front:float       # max_t_dome / t_cyl
    buildup_factor_rear: float
    n_hotspot_cells:     int
    density_equalized:   bool        # buildup < 1.5?
    cv_global:           float
    cv_axial:            float
    homogeneity_score:   float
    pole_boss_required_r:float
    advisory:            List[str]

    def print_summary(self) -> None:
        tf = self.thickness_field
        print("  ═"*34)
        print("  FULL-BODY KAPLAMA ANALİZİ")
        print("  ─"*34)
        print(f"  Silindir kalınlığı (nominal): {self.cylinder_thickness:.4f} mm")
        print(f"  Front dome buildup: {self.buildup_factor_front:.3f}x  "
              f"({'OK' if self.buildup_factor_front<1.5 else '⚠ YÜKSEK'})")
        print(f"  Rear  dome buildup: {self.buildup_factor_rear:.3f}x  "
              f"({'OK' if self.buildup_factor_rear<1.5 else '⚠ YÜKSEK'})")
        print(f"  Junction gradient (front): {self.junction_gradient_front:+.5f} mm/mm")
        print(f"  Junction gradient (rear):  {self.junction_gradient_rear:+.5f} mm/mm")
        print(f"  Polar boss min r: {self.pole_boss_required_r:.2f} mm")
        print(f"  Hotspot cells: {self.n_hotspot_cells}  "
              f"({'OK' if self.n_hotspot_cells==0 else '⚠'})")
        print(f"  Global CV: {self.cv_global:.4f}  "
              f"Axial CV: {self.cv_axial:.4f}")
        print(f"  Homojenlik: {self.homogeneity_score:.4f}  "
              f"({'İyi' if self.homogeneity_score>0.7 else 'Kabul' if self.homogeneity_score>0.5 else 'Zayıf'})")
        print(f"  Density equalized: {'✓' if self.density_equalized else '✗'}")
        if self.advisory:
            print("  ── ÖNERİLER ──")
            for a in self.advisory: print(f"  • {a}")
        print("  ═"*34)


# ── Full-Body Coverage Analyzer ─────────────────────────────────

class FullBodyCoverageAnalyzer:
    """
    Full-body mandrel kalınlık ve kaplama analizi.

    Analytical model: t(z) = ρ(z) × t_fiber
    Path-based model: fiber geçişlerini sayarak t(z,φ) hesapla

    Kullanım:
        fbca = FullBodyCoverageAnalyzer(body, c=25.0, k=21, b=10.0)
        result = fbca.analyze_analytical()
        result.print_summary()
    """
    def __init__(
        self,
        body:          FullBodyMandrel,
        c_clairaut:    float,
        k_circuits:    int,
        bandwidth:     float,
        fiber_thickness: float = 0.25,
        n_z:           int    = 180,
        n_phi:         int    = 72,
    ) -> None:
        self.body    = body
        self.c       = c_clairaut
        self.k       = k_circuits
        self.b       = bandwidth
        self.t_fiber = fiber_thickness
        self.n_z     = n_z
        self.n_phi   = n_phi

    # ── Analytical thickness field ────────────────────────────────
    def _build_thickness_field(self) -> ThicknessField:
        """Analitik t(z,φ) — φ bağımsız (uniform winding)."""
        body = self.body; c = self.c; k = self.k; b = self.b; tf = self.t_fiber
        R    = body.R
        alpha_eq = math.asin(min(1.0, c/R))
        cos_eq   = math.cos(alpha_eq)
        # Nominal: silindir bölgesi
        t_nom = k * b * tf / (2.0 * math.pi * R * cos_eq)

        zz  = np.linspace(0.0, body.Z_total, self.n_z)
        phi = np.linspace(0.0, 2*math.pi, self.n_phi, endpoint=False)
        t_ax  = np.zeros(self.n_z)
        regs  = []

        for i, z in enumerate(zz):
            r = body.r(z)
            if r <= c + 1e-6:
                t_ax[i] = float("inf"); regs.append("PO")
                continue
            al    = body.alpha_from_clairaut(z, c)
            cos_a = math.cos(al)
            rho   = k*b/(2*math.pi*r*max(cos_a,1e-9))
            t_ax[i] = min(rho*tf, 20.0)
            regs.append(body.region_of(z).name[:2])

        # Replace inf with capped value
        max_finite = t_ax[np.isfinite(t_ax)].max() if np.any(np.isfinite(t_ax)) else 1.0
        t_ax = np.where(np.isfinite(t_ax), t_ax, max_finite*2.5)

        # t_matrix: uniform in phi (analytical)
        t_mat = np.outer(t_ax, np.ones(self.n_phi))

        return ThicknessField(
            z_arr=zz, phi_arr=phi, t_matrix=t_mat,
            region_arr=regs, t_nominal=t_nom,
            c_clairaut=c, k_circuits=k)

    # ── Path-based thickness refinement ─────────────────────────
    def add_path_data(
        self,
        tf_field: ThicknessField,
        paths:    List[FullBodyPath],
    ) -> ThicknessField:
        """
        Yol noktalarından fiber geçişlerini sayarak t(z,φ) rafine et.
        """
        body = self.body; c = self.c; b = self.b; t_f = self.t_fiber
        R    = body.R
        dz   = (body.Z_total) / self.n_z
        dphi = 2*math.pi / self.n_phi
        b_phi_half = (b / (2*R))  # band angular half-width [rad]
        n_phi_cells = max(1, int(2*b_phi_half/dphi))

        count = np.zeros((self.n_z, self.n_phi), dtype=np.int16)

        for path in paths:
            for i in range(len(path.points)-1):
                p0, p1 = path.points[i], path.points[i+1]
                z0, z1 = p0.z_mm, p1.z_mm
                phi0   = p0.phi_rad % (2*math.pi)
                phi1   = p1.phi_rad % (2*math.pi)

                iz0 = max(0, min(self.n_z-1, int(z0/body.Z_total*self.n_z)))
                iz1 = max(0, min(self.n_z-1, int(z1/body.Z_total*self.n_z)))
                iz_lo, iz_hi = min(iz0,iz1), max(iz0,iz1)

                for iz in range(iz_lo, iz_hi+1):
                    t  = (iz - iz_lo)/(max(iz_hi-iz_lo,1))
                    ph = phi0 + t*(phi1-phi0)
                    ip_c = int((ph%(2*math.pi))/dphi)
                    for di in range(-n_phi_cells//2, n_phi_cells//2+2):
                        ip = (ip_c+di)%self.n_phi
                        count[iz, ip] += 1

        # Scale count to thickness
        alpha_eq = math.asin(min(1.0, c/R))
        t_per_pass = self.t_fiber
        t_refined = tf_field.t_matrix.copy()
        nonzero = count > 0
        if nonzero.any():
            t_refined[nonzero] = count[nonzero].astype(float) * t_per_pass

        return ThicknessField(
            z_arr=tf_field.z_arr, phi_arr=tf_field.phi_arr,
            t_matrix=t_refined, region_arr=tf_field.region_arr,
            t_nominal=tf_field.t_nominal,
            c_clairaut=self.c, k_circuits=self.k)

    # ── Main analysis ─────────────────────────────────────────────
    def analyze_analytical(
        self,
        paths: Optional[List[FullBodyPath]] = None,
    ) -> CoverageAnalysisResult:
        """Analytical (+ optional path-based refinement) coverage analysis."""
        body = self.body; c = self.c; k = self.k; b = self.b

        tf = self._build_thickness_field()
        if paths:
            tf = self.add_path_data(tf, paths)

        t_ax = tf.t_axial
        # Region-specific thickness
        t_nom   = tf.t_nominal
        # Cylinder
        iz_cyl  = [i for i,r in enumerate(tf.region_arr) if r.startswith("CY")]
        t_cyl   = float(t_ax[iz_cyl].mean()) if iz_cyl else t_nom
        # Front dome max (polar region)
        iz_fd   = [i for i,r in enumerate(tf.region_arr) if r.startswith("FR") or r=="FD"]
        t_fd_max = float(t_ax[iz_fd].max()) if iz_fd else t_cyl
        # Rear dome max
        iz_rd   = [i for i,r in enumerate(tf.region_arr) if r.startswith("RE") or r=="RD"]
        t_rd_max = float(t_ax[iz_rd].max()) if iz_rd else t_cyl

        buildup_f = t_fd_max / max(t_cyl, 1e-9)
        buildup_r = t_rd_max / max(t_cyl, 1e-9)

        # Junction gradients
        grad_f = tf.junction_gradient(body.z_fe)
        grad_r = tf.junction_gradient(body.z_re)

        # CV
        t_valid = t_ax[np.isfinite(t_ax) & (t_ax > 0)]
        global_cv = float(np.std(t_valid)/np.mean(t_valid)) if len(t_valid)>1 else 0.0
        # Axial CV (only cylinder)
        t_cyl_arr = t_ax[iz_cyl] if iz_cyl else np.array([t_cyl])
        axial_cv  = float(np.std(t_cyl_arr)/np.mean(t_cyl_arr)) if len(t_cyl_arr)>1 else 0.0

        homog = max(0.0, 1.0 - global_cv)

        # Hotspots
        n_hot = int(tf.hotspot_mask(1.5).sum())
        equalized = (buildup_f < 1.5 and buildup_r < 1.5)

        # Polar boss
        r_pb = k * b / (2 * math.pi)

        # Advisory
        advisories = []
        if buildup_f > 2.0:
            advisories.append(f"Front dome buildup {buildup_f:.2f}x > 2. Clairaut c'yi artır.")
        if buildup_r > 2.0:
            advisories.append(f"Rear  dome buildup {buildup_r:.2f}x > 2. k azalt.")
        if abs(grad_f) > 0.05:
            advisories.append(f"Front junction gradient {grad_f:.4f} mm/mm yüksek — stress risk.")
        if axial_cv > 0.1:
            advisories.append(f"Cylinder CV {axial_cv:.3f} > 0.1 — homojenlik sorunu.")

        return CoverageAnalysisResult(
            thickness_field          = tf,
            junction_gradient_front  = grad_f,
            junction_gradient_rear   = grad_r,
            pole_thickness_front     = t_fd_max,
            pole_thickness_rear      = t_rd_max,
            cylinder_thickness       = t_cyl,
            buildup_factor_front     = buildup_f,
            buildup_factor_rear      = buildup_r,
            n_hotspot_cells          = n_hot,
            density_equalized        = equalized,
            cv_global                = global_cv,
            cv_axial                 = axial_cv,
            homogeneity_score        = homog,
            pole_boss_required_r     = r_pb,
            advisory                 = advisories,
        )
