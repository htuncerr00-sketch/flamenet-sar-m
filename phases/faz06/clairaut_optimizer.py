"""
clairaut_optimizer.py — Clairaut Sabiti Optimizasyon Motoru
============================================================
c (Clairaut constant) = R·sin(α₀):
  - Trajectory'yi tamamen belirler
  - Density controller: ρ(z) = k·b / (2π·r(z)·cos(α(z)))
  - Buildup controller: buildup = ρ_max / ρ_cyl
  - Coverage regulator: α₀ = arcsin(c/R) → ekvatör sarım açısı

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DOĞRU POL BOSS FİZİĞİ (Faz 4b kritik düzeltme)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

r_stop (integratör) ≠ r_boss (gerçek minimum erişim yarıçapı).

r_boss = max(c × ε_pole, k × b / (2π))

Gerekçe:
  k×b/(2π): k fiber band, her biri b genişliğinde, bir tam çevre
  kaplaması için gereken minimum yarıçap. Bu'nun altında fiber
  fiziksel olarak sığamaz → polar boss bu yarıçapı minimum alır.

  Örnek: k=21, b=10mm → r_boss = 210/(2π) = 33.42mm
         c=25mm < r_boss → fiber r_boss'ta döner, c'ye ulaşamaz
         → buildup hesabı r=r_boss'ta yapılmalı

Buildup oranı (analitik, tüm z için):
  ρ(z)/ρ_cyl = R·cos(α₀) / (r(z)·cos(α(z)))

  Clairaut: sin(α) = c/r → cos(α) = √(1 - c²/r²)

  ρ(z)/ρ_cyl = R·cos(α₀) / (r·√(1 - c²/r²))

  r = r_boss'ta maksimum (en derin erişilebilir nokta):
  buildup_max = R·cos(α₀) / (r_boss·√(1 - (c/r_boss)²))

Optimize edilen metrikler:
  1. buildup_factor  → minimize (target ≤ 1.5)
  2. junction_rho    → minimize (fiberlerin birbirine bindirmesi)
  3. alpha_0_deg     → makine limitleriyle uyumlu
  4. delta_phi       → turn-around açısı (pattern kalitesiyle ilgili)
  5. machine_ok      → spindle rpm, carriage speed limitleri

Referans: Koussios (2004) Eq. 8.2; AIAA 2005 (filamentwindingshapeoptimization)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from full_body_mandrel import FullBodyMandrel


# ── Data Structures ──────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ClairautPoint:
    """
    Tek bir c değerinin tam metrik profili.
    """
    c_mm:            float   # Clairaut sabiti [mm]
    alpha_0_deg:     float   # Ekvatör sarım açısı [°]
    r_boss_mm:       float   # Polar boss yarıçapı [mm]
    alpha_boss_deg:  float   # r=r_boss'ta sarım açısı [°]

    # Yoğunluk metrikleri
    rho_cylinder:    float   # ρ_cyl  (nominal, =1 ideal)
    rho_junction:    float   # ρ at z_junction (ekvator)
    rho_at_boss:     float   # ρ at r=r_boss (dome peak)
    buildup_factor:  float   # rho_at_boss / rho_cylinder

    # Geometrik metrikler
    delta_phi_deg:   float   # Turn-around açısı [°] (dome)
    coverage_fraction:float  # Ekvatörde teorik kaplama oranı

    # Makine metrikleri
    rpm_at_cylinder: float   # Silindir bölgesi RPM
    rpm_at_boss:     float   # Polar boss RPM (max)
    v_x_at_cylinder: float   # Carriage hızı silindir [mm/s]
    machine_ok:      bool    # Tüm makine limitleri OK?

    # Birleşik skor
    composite_score: float   # [0,1] — büyük = iyi

    @property
    def buildup_ok(self) -> bool:
        return self.buildup_factor <= 1.5

    @property
    def grade(self) -> str:
        if self.composite_score >= 0.85: return "A+"
        if self.composite_score >= 0.75: return "A"
        if self.composite_score >= 0.60: return "B"
        if self.composite_score >= 0.45: return "C"
        return "D"

    def one_line(self) -> str:
        return (
            f"c={self.c_mm:5.2f}mm  α₀={self.alpha_0_deg:5.2f}°  "
            f"r_boss={self.r_boss_mm:5.2f}mm  "
            f"buildup={self.buildup_factor:5.3f}x  "
            f"ρ_jct={self.rho_junction:.3f}  "
            f"rpm_boss={self.rpm_at_boss:6.2f}  "
            f"Q={self.composite_score:.4f}[{self.grade}]  "
            f"{'✓' if self.machine_ok else '✗'}  "
            f"{'BU✓' if self.buildup_ok else 'BU✗'}"
        )


@dataclass(slots=True)
class ClairautFamily:
    """
    Belirli bir buildup target ile geçerli c aralığı — bir "aile".

    buildup_threshold: Bu ailenin tanımlayıcı üst buildup sınırı
    members:           Bu sınırı sağlayan ClairautPoint listesi
    best:              En yüksek composite_score'lu üye
    """
    buildup_threshold: float
    members:           List[ClairautPoint] = field(default_factory=list)

    @property
    def best(self) -> Optional[ClairautPoint]:
        if not self.members: return None
        return max(self.members, key=lambda m: m.composite_score)

    @property
    def c_range(self) -> Tuple[float, float]:
        if not self.members: return (0.0, 0.0)
        cc = [m.c_mm for m in self.members]
        return (min(cc), max(cc))

    @property
    def alpha_range(self) -> Tuple[float, float]:
        if not self.members: return (0.0, 0.0)
        aa = [m.alpha_0_deg for m in self.members]
        return (min(aa), max(aa))

    def summary(self) -> str:
        if not self.members:
            return f"  Family (BU≤{self.buildup_threshold}x): boş"
        b = self.best
        cr = self.c_range; ar = self.alpha_range
        return (
            f"  Family (BU≤{self.buildup_threshold}x): "
            f"  {len(self.members)} üye\n"
            f"    c ∈ [{cr[0]:.2f}, {cr[1]:.2f}]mm  "
            f"α ∈ [{ar[0]:.2f}°, {ar[1]:.2f}°]\n"
            f"    Best: c={b.c_mm:.2f}mm  α₀={b.alpha_0_deg:.2f}°  "
            f"buildup={b.buildup_factor:.3f}x  Q={b.composite_score:.4f}[{b.grade}]"
        )


@dataclass(slots=True)
class OptimizationResult:
    """c-optimizasyon tam sonucu."""
    R_mm:         float
    k_circuits:   int
    bandwidth_mm: float
    fiber_speed:  float
    all_points:   List[ClairautPoint]
    families:     Dict[float, ClairautFamily]   # threshold → family
    pareto_front: List[ClairautPoint]
    best_overall: Optional[ClairautPoint]
    # Polar boss sabit (geometri tarafından belirlenir)
    r_boss_geometric: float

    def buildup_table(self, max_rows: int = 25) -> str:
        """Tüm c değerleri tablosu."""
        lines = [
            "═" * 105,
            f"CLAIRAUT OPTİMİZASYON TABLOSU  "
            f"(R={self.R_mm}mm, k={self.k_circuits}, b={self.bandwidth_mm}mm)",
            "─" * 105,
            f"  {'c':>6}  {'α₀':>7}  {'r_boss':>7}  {'α_boss':>7}  "
            f"{'buildup':>8}  {'ρ_jct':>6}  {'rpm_boss':>9}  "
            f"{'Q':>7}  {'BU':>4}  {'mak':>4}",
            "─" * 105,
        ]
        for pt in self.all_points[:max_rows]:
            lines.append(
                f"  {pt.c_mm:>6.2f}  {pt.alpha_0_deg:>6.2f}°  "
                f"{pt.r_boss_mm:>6.2f}mm  {pt.alpha_boss_deg:>6.2f}°  "
                f"{pt.buildup_factor:>8.3f}  {pt.rho_junction:>6.3f}  "
                f"{pt.rpm_at_boss:>9.2f}  "
                f"{pt.composite_score:>7.4f}  "
                f"{'✓' if pt.buildup_ok else '✗':>4}  "
                f"{'✓' if pt.machine_ok else '✗':>4}"
            )
        lines.append("═" * 105)
        return "\n".join(lines)

    def family_report(self) -> str:
        lines = ["  AILE ANALİZİ:"]
        for thr, fam in sorted(self.families.items()):
            lines.append(fam.summary())
        if self.best_overall:
            bo = self.best_overall
            lines += [
                "  ─" * 25,
                f"  ★ EN İYİ: c={bo.c_mm:.3f}mm  α₀={bo.alpha_0_deg:.3f}°  "
                f"buildup={bo.buildup_factor:.3f}x  Q={bo.composite_score:.4f}",
            ]
        return "\n".join(lines)


# ── Clairaut Optimizer ────────────────────────────────────────────

class ClairautOptimizer:
    """
    Optimal Clairaut sabiti c için sweep ve Pareto analizi.

    Fizik modeli:
      - Polar boss yarıçapı: r_boss = max(c·ε, k·b/(2π))
      - Buildup factor: R·cos(α₀) / (r_boss·√(1-(c/r_boss)²))
      - Junction density: k·b / (2π·R·cos(α₀))
      - Makine limitleri: spindle_max_rpm, carriage_max_speed

    Kullanım:
        opt = ClairautOptimizer(body, k=21, bandwidth=10.0, machine=phys)
        result = opt.optimize(n_steps=200)
        print(result.buildup_table())
        print(result.family_report())
    """

    BUILDUP_THRESHOLDS = [1.5, 2.0, 2.5]   # Aile sınırları

    def __init__(
        self,
        body:         FullBodyMandrel,
        k_circuits:   int,
        bandwidth:    float,
        machine,                             # MachinePhysics
        fiber_speed:  float  = 100.0,
        pole_eps:     float  = 1.002,        # r_boss_min = c × pole_eps
    ) -> None:
        self.body        = body
        self.k           = k_circuits
        self.b           = bandwidth
        self.machine     = machine
        self.v_target    = fiber_speed
        self.pole_eps    = pole_eps
        self.R           = body.R

        # Polar boss minimum geometrik yarıçap: k×b/(2π)
        self.r_boss_geo  = k_circuits * bandwidth / (2.0 * math.pi)

    # ── Core metric computation ──────────────────────────────────

    def _r_boss(self, c: float) -> float:
        """
        Fiziğe dayalı polar boss yarıçapı.

        r_boss = max(c·ε, k·b/(2π))
        """
        return max(c * self.pole_eps, self.r_boss_geo)

    def _buildup_factor(self, c: float, r_boss: float) -> float:
        """
        ρ(r_boss) / ρ_cyl — analitik.

        ρ(r)/ρ_cyl = R·cos(α₀) / (r·cos(α(r)))
                   = R·cos(α₀) / (r·√(1 - c²/r²))

        α₀ = arcsin(c/R)
        """
        if c >= self.R:
            return float("inf")
        alpha_0   = math.asin(min(1.0, c / self.R))
        cos_alpha0 = math.cos(alpha_0)

        c_over_r  = c / r_boss
        if c_over_r >= 1.0:
            return float("inf")
        cos_alpha_boss = math.sqrt(max(0.0, 1.0 - c_over_r**2))
        if cos_alpha_boss < 1e-9:
            return float("inf")

        return (self.R * cos_alpha0) / (r_boss * cos_alpha_boss)

    def _junction_rho(self, c: float) -> float:
        """
        Junction (ekvatör) yoğunluğu: ρ_jct = k·b/(2π·R·cos(α₀)).
        """
        if c >= self.R: return float("inf")
        alpha_0   = math.asin(min(1.0, c / self.R))
        cos_alpha0 = math.cos(alpha_0)
        if cos_alpha0 < 1e-9: return float("inf")
        return self.k * self.b / (2.0 * math.pi * self.R * cos_alpha0)

    def _delta_phi(self, c: float) -> float:
        """
        Dome turn-around açısı Δφ [°] — analytical approximation.

        Dome içindeki geodezik için:
          Δφ_dome ≈ 2·∫_{r_boss}^{R} c / (r·√(r²-c²)) · dr/√G
        Için eliptik dome (H, R):
          Δφ ≈ π·(1 - c/R)·(H/R)^0.5  [yaklaşım, Koussios (2004)]
        Silindir: Δφ_cyl = L·tan(α₀)/R = L·c / (R·√(R²-c²))
        """
        if c >= self.R: return 0.0
        alpha_0 = math.asin(min(1.0, c/self.R))
        # Silindir katkısı
        sin_a = math.sin(alpha_0); cos_a = math.cos(alpha_0)
        if cos_a < 1e-6: return 0.0
        dphi_cyl = math.degrees(
            self.body.L_cyl * math.tan(alpha_0) / self.R
        )
        # Dome katkısı (front + rear): yaklaşım
        H = self.body.front.height_mm
        dphi_dome = math.degrees(math.pi * c / self.R *
                    math.sqrt(H / self.R) * 2.0)
        return dphi_cyl + dphi_dome

    def _machine_metrics(self, c: float) -> Tuple[float, float, float, bool]:
        """
        (rpm_cyl, rpm_boss, v_x_cyl, machine_ok)
        """
        R  = self.R
        v  = self.v_target
        mach = self.machine

        if c >= R: return (0.0, 0.0, 0.0, False)
        alpha_0 = math.asin(min(1.0, c/R))
        sin_a = math.sin(alpha_0); cos_a = math.cos(alpha_0)

        # Cylinder metrics
        rpm_cyl = v * sin_a / (2.0 * math.pi * R) * 60.0
        v_x_cyl = v * cos_a

        # Boss metrics
        r_boss   = self._r_boss(c)
        sin_boss = min(1.0, c / r_boss)
        cos_boss = math.sqrt(max(0.0, 1.0 - sin_boss**2))
        rpm_boss = v * sin_boss / (2.0 * math.pi * r_boss) * 60.0

        # Limits
        v_x_lim  = mach.carriage_max_speed_mm_s()
        rpm_lim  = mach.spindle_max_speed_rpm()

        machine_ok = (rpm_cyl <= rpm_lim and
                      rpm_boss <= rpm_lim * 1.05 and
                      v_x_cyl <= v_x_lim)
        return rpm_cyl, rpm_boss, v_x_cyl, machine_ok

    def _score_from_raw(self, bu, rj, mach_ok, dphi, rpm_boss) -> float:
        """Compute composite score from raw values (no dataclass needed)."""
        if bu <= 1.0:    s_bu = 1.00
        elif bu <= 1.5:  s_bu = 1.00 - 0.60*(bu - 1.0)/0.5
        elif bu <= 2.0:  s_bu = 0.40 - 0.35*(bu - 1.5)/0.5
        elif bu <= 3.0:  s_bu = 0.05 - 0.05*(bu - 2.0)
        else:            s_bu = 0.0
        if rj <= 0.8:    s_jct = 0.80
        elif rj <= 1.0:  s_jct = 0.80 + 0.20*(rj - 0.8)/0.2
        elif rj <= 1.3:  s_jct = 1.00 - 0.40*(rj - 1.0)/0.3
        elif rj <= 1.8:  s_jct = 0.60 - 0.50*(rj - 1.3)/0.5
        else:            s_jct = max(0.0, 0.10 - 0.10*(rj - 1.8))
        s_mach = 1.0 if mach_ok else 0.0
        if 300 <= dphi <= 720:   s_dphi = 1.0
        elif 180 <= dphi < 300:  s_dphi = 0.7 + 0.3*(dphi-180)/120
        elif dphi < 180:         s_dphi = max(0.0, dphi/180*0.7)
        elif 720 < dphi <= 1080: s_dphi = max(0.0, 0.7-(dphi-720)/360*0.7)
        else:                    s_dphi = 0.0
        rpm_max = self.machine.spindle_max_speed_rpm()
        r_boss = rpm_boss / max(rpm_max, 1.0)
        if r_boss <= 0.3:    s_rpm = 1.0
        elif r_boss <= 0.6:  s_rpm = 1.0 - 0.5*(r_boss-0.3)/0.3
        elif r_boss <= 1.0:  s_rpm = 0.5 - 0.5*(r_boss-0.6)/0.4
        else:                s_rpm = 0.0
        return (0.35*s_bu + 0.20*s_jct + 0.20*s_mach + 0.15*s_dphi + 0.10*s_rpm)

    def _composite_score(self, pt: ClairautPoint) -> float:
        """
        Ağırlıklı birleşik skor [0,1].

        Ağırlıklar (mühendislik önceliği):
          buildup control: 0.35 — en kritik (dome failure)
          junction density:0.20 — junction congestion
          machine_ok:      0.20 — hard constraint
          delta_phi quality:0.15 — pattern closure
          rpm_boss:        0.10 — polar spindle control
        """
        # 1. Buildup score (hedef ≤ 1.5, ≤ 1.0 mükemmel)
        bu = pt.buildup_factor
        if bu <= 1.0:    s_bu = 1.00
        elif bu <= 1.5:  s_bu = 1.00 - 0.60*(bu - 1.0)/0.5
        elif bu <= 2.0:  s_bu = 0.40 - 0.35*(bu - 1.5)/0.5
        elif bu <= 3.0:  s_bu = 0.05 - 0.05*(bu - 2.0)
        else:            s_bu = 0.0

        # 2. Junction density (hedef ≤ 1.3)
        rj = pt.rho_junction
        if rj <= 0.8:    s_jct = 0.80  # alt kaplama eksik
        elif rj <= 1.0:  s_jct = 0.80 + 0.20*(rj - 0.8)/0.2
        elif rj <= 1.3:  s_jct = 1.00 - 0.40*(rj - 1.0)/0.3
        elif rj <= 1.8:  s_jct = 0.60 - 0.50*(rj - 1.3)/0.5
        else:            s_jct = max(0.0, 0.10 - 0.10*(rj - 1.8))

        # 3. Machine OK
        s_mach = 1.0 if pt.machine_ok else 0.0

        # 4. Delta_phi (K/2π irrasyonelliği burada yok, ama büyüklük kalitesi)
        # Hem çok küçük hem çok büyük Δφ kötü
        dphi = pt.delta_phi_deg
        if 300 <= dphi <= 720:   s_dphi = 1.0
        elif 180 <= dphi < 300:  s_dphi = 0.7 + 0.3*(dphi-180)/120
        elif dphi < 180:         s_dphi = max(0.0, dphi/180*0.7)
        elif 720 < dphi <= 1080: s_dphi = max(0.0, 0.7-(dphi-720)/360*0.7)
        else:                    s_dphi = 0.0

        # 5. RPM boss (hedef < 0.5×limit)
        rpm_max = self.machine.spindle_max_speed_rpm()
        r_boss  = pt.rpm_at_boss / max(rpm_max, 1.0)
        if r_boss <= 0.3:    s_rpm = 1.0
        elif r_boss <= 0.6:  s_rpm = 1.0 - 0.5*(r_boss-0.3)/0.3
        elif r_boss <= 1.0:  s_rpm = 0.5 - 0.5*(r_boss-0.6)/0.4
        else:                s_rpm = 0.0

        return (0.35*s_bu + 0.20*s_jct + 0.20*s_mach +
                0.15*s_dphi + 0.10*s_rpm)

    # ── Main sweep ────────────────────────────────────────────────

    def optimize(
        self,
        c_min_frac: float = 0.15,   # c_min = R × frac
        c_max_frac: float = 0.92,   # c_max = R × frac
        n_steps:    int   = 200,
    ) -> OptimizationResult:
        """
        c ∈ [c_min, c_max] taraması.

        Args:
            c_min_frac: c_min / R — too small → huge buildup, small α₀
            c_max_frac: c_max / R — close to R → α₀ → 90°, unstable
            n_steps:    tarama adımı

        Returns:
            OptimizationResult with all ClairautPoints and families
        """
        R    = self.R
        c_arr = np.linspace(R * c_min_frac, R * c_max_frac, n_steps)

        all_points: List[ClairautPoint] = []

        for c in c_arr:
            r_boss   = self._r_boss(c)
            alpha_0  = math.degrees(math.asin(min(1.0, c/R)))
            sin_boss = min(1.0, c/r_boss)
            alpha_boss = math.degrees(math.asin(sin_boss))
            buildup  = self._buildup_factor(c, r_boss)
            rho_jct  = self._junction_rho(c)
            dphi     = self._delta_phi(c)
            cov_frac = rho_jct  # junction yoğunluğu ≈ kaplama oranı
            rpm_cyl, rpm_boss, v_x, mach_ok = self._machine_metrics(c)

            score = self._score_from_raw(buildup, rho_jct, mach_ok, dphi, rpm_boss)
            pt = ClairautPoint(
                c_mm=float(c), alpha_0_deg=alpha_0, r_boss_mm=r_boss,
                alpha_boss_deg=alpha_boss, rho_cylinder=rho_jct,
                rho_junction=rho_jct, rho_at_boss=buildup*rho_jct,
                buildup_factor=buildup, delta_phi_deg=dphi,
                coverage_fraction=cov_frac, rpm_at_cylinder=rpm_cyl,
                rpm_at_boss=rpm_boss, v_x_at_cylinder=v_x,
                machine_ok=mach_ok, composite_score=score,
            )
            all_points.append(pt)

        # ── Families ──────────────────────────────────────────────
        families = {}
        for thr in self.BUILDUP_THRESHOLDS:
            members = [p for p in all_points if p.buildup_factor <= thr]
            families[thr] = ClairautFamily(buildup_threshold=thr, members=members)

        # ── Pareto front: min(buildup) × min(rho_junction) ────────
        pareto = self._pareto_front(all_points)

        # ── Best overall (feasible preferred) ─────────────────────
        feasible = [p for p in all_points if p.machine_ok and p.buildup_ok]
        pool     = feasible if feasible else all_points
        best     = max(pool, key=lambda p: p.composite_score)

        return OptimizationResult(
            R_mm              = R,
            k_circuits        = self.k,
            bandwidth_mm      = self.b,
            fiber_speed       = self.v_target,
            all_points        = all_points,
            families          = families,
            pareto_front      = pareto,
            best_overall      = best,
            r_boss_geometric  = self.r_boss_geo,
        )

    def _pareto_front(self, pts: List[ClairautPoint]) -> List[ClairautPoint]:
        """
        2D Pareto front: min(buildup_factor) × min(rho_junction).
        Machine-OK points first.
        """
        feasible = [p for p in pts if p.machine_ok]
        pool = feasible if len(feasible) > 3 else pts

        pareto = []
        for cand in pool:
            dominated = False
            for other in pool:
                if other is cand: continue
                if (other.buildup_factor <= cand.buildup_factor and
                        other.rho_junction <= cand.rho_junction and
                        (other.buildup_factor < cand.buildup_factor or
                         other.rho_junction < cand.rho_junction)):
                    dominated = True; break
            if not dominated:
                pareto.append(cand)

        pareto.sort(key=lambda p: -p.composite_score)
        return pareto

    # ── Sensitivity ───────────────────────────────────────────────

    def sensitivity_table(
        self,
        c_center: float,
        dc:       float = 3.0,
        n:        int   = 9,
    ) -> str:
        """c ± dc aralığında hassasiyet tablosu."""
        c_arr = np.linspace(c_center - dc, c_center + dc, n)
        lines = [
            f"  Hassasiyet: c = {c_center:.2f} ± {dc:.1f}mm",
            f"  {'c':>6}  {'α₀':>7}  {'buildup':>8}  {'ρ_jct':>6}  "
            f"{'Δφ':>8}  {'Q':>7}",
            "  " + "─" * 52,
        ]
        for c in c_arr:
            r_boss = self._r_boss(c)
            alpha0 = math.degrees(math.asin(min(1.0, c/self.R)))
            bu     = self._buildup_factor(c, r_boss)
            rj     = self._junction_rho(c)
            dphi   = self._delta_phi(c)
            rpm_c, rpm_b, vx, mok = self._machine_metrics(c)
            pt_tmp = ClairautPoint(
                c_mm=c, alpha_0_deg=alpha0, r_boss_mm=r_boss,
                alpha_boss_deg=0, rho_cylinder=rj, rho_junction=rj,
                rho_at_boss=bu*rj, buildup_factor=bu, delta_phi_deg=dphi,
                coverage_fraction=rj, rpm_at_cylinder=rpm_c,
                rpm_at_boss=rpm_b, v_x_at_cylinder=vx,
                machine_ok=mok, composite_score=0.0)
            q = self._composite_score(pt_tmp)
            lines.append(
                f"  {c:>6.2f}  {alpha0:>6.2f}°  {bu:>8.3f}  {rj:>6.3f}  "
                f"{dphi:>7.1f}°  {q:>7.4f}"
            )
        return "\n".join(lines)

    # ── Density equalization target ───────────────────────────────

    def find_equalization_c(self, target_buildup: float = 1.5) -> Optional[float]:
        """
        Buildup ≤ target_buildup koşulunu sağlayan minimum c.

        Bu c değeri: dome'da ve cylinderde kalınlık oranı tam hedefte.
        """
        R = self.R
        c_arr = np.linspace(R * 0.10, R * 0.95, 1000)
        for c in c_arr:
            r_boss = self._r_boss(c)
            bu     = self._buildup_factor(c, r_boss)
            if bu <= target_buildup:
                return float(c)
        return None
