"""
alpha_optimizer.py — Sarım Açısı Optimizasyon Motoru
=====================================================
Verilen (R, L, b, machine) için optimal sarım açısı α ve pattern ailesini bulur.

Temel matematiksel fikir:
  K(α)   = 2·L·tan(α)/R          [turn-around açısı, rad]
  f(α)   = K(α)/(2π)             [devre başına devir sayısı]
  n(α)   = 2πR·cos(α)/b          [ideal band sayısı, gerçel]

  Pattern kapanma kalitesi (bir k için):
    ε(α,k) = k·K(α) - round(k·K(α)/2π) · 2π   [rad]
    q(α,k) = |ε(α,k)| / (2π/k)                 [normalize artık açı, 0..0.5]
    q=0: mükemmel kapanma;  q=0.5: en kötü

  Coverage kalitesi:
    cov(α,k) = |k - n(α)| / n(α)               [n'den ne kadar sapıyor]
    cov=0: tam ideal kaplama sayısı

  Birleşik kalite indeksi:
    Q(α,k) = w_q·q(α,k) + w_c·cov(α,k) + w_m·machine_penalty(α)

Algoritma:
  1. α ∈ [α_min, α_max] için N_alpha örnekle
  2. Her α'da k ∈ [k_min(α), k_max(α)] için Q hesapla
  3. Q < Q_threshold olanları kaydet
  4. Makine kısıtları kontrolü
  5. Pattern ailelerine grupla (aynı k → aynı aile)
  6. Pareto front bul: min(overlap) × min(k) × max(machine_score)
  7. Her aile için en iyi α'yı bul

Pattern Ailesi Kavramı:
  Aynı k (toplam devre sayısı) değerine sahip tüm (α, p) çiftleri
  aynı "aile"yi oluşturur. Bir aile belirli bir üretim karmaşıklığını temsil eder.
  Aile içinde α değiştikçe overlap değişir; optimal α en az overlap verendir.

Referans: Koussios (2004) Ch. 8; Bookhart & Fowler (1968) NOCIRC
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from math import gcd
from typing import Dict, List, Optional, Tuple

import numpy as np

from geometry import CylindricalMandrel
from machine_model import MachinePhysics, check_machine_constraints
from pattern_solver import PatternCandidate, PatternSearchConfig
from winding_math import WindingParameters, ClairautCalculator, ClairautConstants


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AlphaPoint:
    """
    Belirli bir α değerindeki winding konfigürasyonu.

    Tüm büyüklükler bu α'nın fiziksel sonuçları.
    """
    alpha_deg:      float
    k:              int       # toplam devre (turn-around sayısı)
    p:              int       # integer devir
    n_ideal:        float     # gerçel band sayısı
    K_rad:          float     # turn-around açısı [rad]
    epsilon_rad:    float     # artık açı [rad]
    overlap_mm:     float     # ekvatoral overlap [mm, signed]
    overlap_ratio:  float     # |overlap| / b
    b_actual_mm:    float     # bu k için gerçek band genişliği
    pattern_type:   str       # "leading" | "lagging" | "exact"
    closure_quality:float     # q ∈ [0, 0.5] — küçük = iyi kapanma
    coverage_quality:float    # cov ∈ [0, ∞) — sıfır = ideal kaplama
    machine_ok:     bool      # makine kısıtları sağlanıyor mu
    rpm:            float     # spindle hızı [rpm]
    v_x_mm_s:       float     # carriage hızı [mm/s]
    fiber_speed:    float     # hedef fiber hızı [mm/s]

    @property
    def alpha_rad(self) -> float:
        return math.radians(self.alpha_deg)

    @property
    def composite_quality(self) -> float:
        """
        Birleşik kalite indeksi [0, 1] — 1 = mükemmel.
        Düşük closure_quality + düşük coverage_quality → yüksek composite.
        """
        # q: 0=mükemmel, 0.5=kötü → skor: 1=mükemmel, 0=kötü
        closure_score  = 1.0 - 2.0 * self.closure_quality
        coverage_score = max(0.0, 1.0 - 2.0 * self.coverage_quality)
        machine_bonus  = 1.0 if self.machine_ok else 0.5
        return 0.5 * closure_score + 0.3 * coverage_score + 0.2 * machine_bonus

    def summary(self) -> str:
        return (
            f"α={self.alpha_deg:6.2f}°  k={self.k:3d}  p={self.p:3d}  "
            f"ε={self.epsilon_rad:+.4f}rad  "
            f"OL={self.overlap_mm:+6.3f}mm({self.overlap_ratio*100:5.1f}%)  "
            f"b_act={self.b_actual_mm:.3f}mm  "
            f"Q={self.composite_quality:.4f}  "
            f"{'OK' if self.machine_ok else 'FAIL'}"
        )


@dataclass(slots=True)
class PatternFamily:
    """
    Aynı k değerine sahip AlphaPoint kümesi — bir "pattern ailesi".

    family_k:   Bu ailenin k değeri
    members:    Tüm (α, p) çiftleri (farklı α'larda aynı k)
    best:       En yüksek composite_quality'e sahip üye
    alpha_range:[α_min, α_max] — bu k'nın geçerli olduğu α aralığı
    """
    family_k:   int
    members:    List[AlphaPoint] = field(default_factory=list)

    @property
    def best(self) -> Optional[AlphaPoint]:
        if not self.members:
            return None
        return max(self.members, key=lambda m: m.composite_quality)

    @property
    def alpha_range(self) -> Tuple[float, float]:
        if not self.members:
            return (0.0, 0.0)
        alphas = [m.alpha_deg for m in self.members]
        return (min(alphas), max(alphas))

    @property
    def overlap_range(self) -> Tuple[float, float]:
        if not self.members:
            return (0.0, 0.0)
        ovls = [m.overlap_mm for m in self.members]
        return (min(ovls), max(ovls))

    def summary(self) -> str:
        b = self.best
        if b is None:
            return f"  k={self.family_k}: boş"
        ar = self.alpha_range
        or_ = self.overlap_range
        return (
            f"  k={self.family_k:3d} | "
            f"α ∈ [{ar[0]:.1f}°, {ar[1]:.1f}°]  "
            f"OL ∈ [{or_[0]:+.2f}, {or_[1]:+.2f}]mm | "
            f"Best: α={b.alpha_deg:.2f}°  OL={b.overlap_mm:+.3f}mm  "
            f"Q={b.composite_quality:.4f}  "
            f"{'✓' if b.machine_ok else '✗'}"
        )


@dataclass(slots=True)
class OptimizationResult:
    """α optimizasyonu tam sonucu."""
    R_mm:           float
    L_mm:           float
    b_mm:           float
    fiber_speed:    float
    alpha_range:    Tuple[float, float]
    n_alpha_steps:  int
    all_points:     List[AlphaPoint]
    families:       Dict[int, PatternFamily]
    pareto_front:   List[AlphaPoint]   # min_k × min_overlap × machine_ok Pareto set
    best_overall:   Optional[AlphaPoint]

    @property
    def n_good_points(self) -> int:
        return len(self.all_points)

    @property
    def n_families(self) -> int:
        return len(self.families)

    def family_report(self) -> str:
        lines = [
            "=" * 72,
            f"PATTERN AİLE ANALİZİ  (R={self.R_mm:.0f}mm, L={self.L_mm:.0f}mm, b={self.b_mm:.0f}mm)",
            "=" * 72,
            f"  α aralığı: {self.alpha_range[0]:.1f}° → {self.alpha_range[1]:.1f}°",
            f"  Toplam geçerli nokta: {self.n_good_points}",
            f"  Aile sayısı: {self.n_families}",
            "-" * 72,
            f"  {'k':>4}  {'α aralığı':>18}  {'OL aralığı':>18}  {'Best α':>8}  "
            f"{'Best OL':>10}  {'Q':>7}  {'Mak.':>5}",
            "-" * 72,
        ]
        for k_val in sorted(self.families.keys()):
            fam = self.families[k_val]
            b   = fam.best
            if b is None:
                continue
            ar  = fam.alpha_range
            or_ = fam.overlap_range
            lines.append(
                f"  {k_val:>4}  "
                f"[{ar[0]:5.1f}° - {ar[1]:5.1f}°]  "
                f"[{or_[0]:+5.2f} - {or_[1]:+5.2f}]mm  "
                f"{b.alpha_deg:>7.2f}°  "
                f"{b.overlap_mm:>+9.3f}mm  "
                f"{b.composite_quality:>7.4f}  "
                f"{'✓' if b.machine_ok else '✗'}"
            )
        if self.best_overall:
            bo = self.best_overall
            lines += [
                "-" * 72,
                f"  ★ EN İYİ: α={bo.alpha_deg:.4f}°  k={bo.k}  p={bo.p}  "
                f"overlap={bo.overlap_mm:+.4f}mm  Q={bo.composite_quality:.4f}",
            ]
        lines.append("=" * 72)
        return "\n".join(lines)

    def pareto_report(self, max_show: int = 10) -> str:
        lines = [
            f"  PARETO ÖN YÜZEYİ ({len(self.pareto_front)} nokta):",
            f"  {'α':>8}  {'k':>4}  {'p':>4}  {'overlap':>10}  {'Q':>7}  {'rpm':>8}  {'v_x':>8}",
            "  " + "-" * 58,
        ]
        for pt in self.pareto_front[:max_show]:
            lines.append(
                f"  {pt.alpha_deg:>7.3f}°  {pt.k:>4}  {pt.p:>4}  "
                f"{pt.overlap_mm:>+9.3f}mm  {pt.composite_quality:>7.4f}  "
                f"{pt.rpm:>7.2f}rpm  {pt.v_x_mm_s:>7.2f}mm/s"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Alpha Optimizer
# ---------------------------------------------------------------------------

class AlphaOptimizer:
    """
    Sarım açısı α optimizasyon motoru.

    Verilen (R, L, b, machine) için:
      1. α ∈ [α_min, α_max] sweep
      2. Her α'da en iyi (k, p) bul
      3. Makine kısıtları uygula
      4. Pattern ailelerine grupla
      5. Pareto front hesapla
      6. En iyi α ve pattern öner

    Kullanım:
        opt = AlphaOptimizer(R=50, L=300, b=10, machine=MachinePhysics.default())
        result = opt.optimize(alpha_min=10, alpha_max=80, n_steps=500)
        print(result.family_report())
        best = result.best_overall
    """

    def __init__(
        self,
        R_mm:         float,
        L_mm:         float,
        b_mm:         float,
        machine:      MachinePhysics,
        fiber_speed:  float = 100.0,
        n_layers:     int   = 1,
    ) -> None:
        self.R           = R_mm
        self.L           = L_mm
        self.b           = b_mm
        self.machine     = machine
        self.fiber_speed = fiber_speed
        self.n_layers    = n_layers

    # -----------------------------------------------------------------------
    # Core calculation
    # -----------------------------------------------------------------------

    def _K(self, alpha_rad: float) -> float:
        """Turn-around açısı K(α) [rad]."""
        return 2.0 * self.L * math.tan(alpha_rad) / self.R

    def _n_ideal(self, alpha_rad: float) -> float:
        """İdeal band sayısı n(α) [gerçel]."""
        return 2.0 * math.pi * self.R * math.cos(alpha_rad) / self.b

    def _b_eff(self, alpha_rad: float) -> float:
        """Efektif band genişliği b_eff(α) [mm]."""
        return self.b / math.cos(alpha_rad)

    def _best_k_for_alpha(
        self,
        alpha_rad:    float,
        K:            float,
        n_ideal:      float,
        k_range_frac: float = 0.45,
        max_k:        int   = 200,
        overlap_limit:float = 0.55,
    ) -> List[AlphaPoint]:
        """
        Belirli bir α için en iyi (k, p) çiftlerini bul.

        Algoritma:
          k ∈ [n_ideal*(1-frac), n_ideal*(1+frac)] ∩ [2, max_k]
          p_float = k·K/(2π)
          p = round, floor, ceil
          → gcd(p,k) = 1 filtresi
          → overlap < overlap_limit filtresi
        """
        alpha_deg  = math.degrees(alpha_rad)
        cos_a      = math.cos(alpha_rad)
        b_eff_val  = self.b / cos_a
        TAU        = 2.0 * math.pi

        k_min = max(2, int(n_ideal * (1.0 - k_range_frac)))
        k_max = min(max_k, int(n_ideal * (1.0 + k_range_frac)))

        # Makine kısıtları
        mc = check_machine_constraints(
            self.machine, alpha_deg, self.fiber_speed,
            self.R, self.L,
        )

        results = []
        for k in range(k_min, k_max + 1):
            p_float = k * K / TAU
            p_candidates = {
                math.floor(p_float),
                math.ceil(p_float),
                round(p_float),
            }

            for p in p_candidates:
                p = int(p)
                if p <= 0:
                    continue
                if gcd(p, k) != 1:
                    continue

                eps      = k * K - TAU * p
                b_act    = TAU * self.R * cos_a / k   # gerçek band genişliği
                ovl      = eps * self.R / cos_a
                ratio    = abs(ovl) / self.b

                if ratio > overlap_limit:
                    continue

                # Kapanma kalitesi: [0, 0.5] — 0=mükemmel
                closure_q = abs(eps) / (TAU / k)
                closure_q = min(0.5, closure_q)

                # Kaplama kalitesi: |k - n_ideal| / n_ideal
                coverage_q = abs(k - n_ideal) / max(n_ideal, 1.0)

                ptype = "exact" if abs(eps) < 1e-4 else ("leading" if eps > 0 else "lagging")

                pt = AlphaPoint(
                    alpha_deg       = alpha_deg,
                    k               = k,
                    p               = p,
                    n_ideal         = n_ideal,
                    K_rad           = K,
                    epsilon_rad     = eps,
                    overlap_mm      = ovl,
                    overlap_ratio   = ratio,
                    b_actual_mm     = b_act,
                    pattern_type    = ptype,
                    closure_quality = closure_q,
                    coverage_quality= coverage_q,
                    machine_ok      = mc.overall_ok,
                    rpm             = mc.rpm,
                    v_x_mm_s        = mc.v_x_mm_s,
                    fiber_speed     = self.fiber_speed,
                )
                results.append(pt)

        return results

    # -----------------------------------------------------------------------
    # Main optimization loop
    # -----------------------------------------------------------------------

    def optimize(
        self,
        alpha_min_deg: float = 8.0,
        alpha_max_deg: float = 80.0,
        n_steps:       int   = 600,
        overlap_limit: float = 0.50,
        max_k:         int   = 200,
    ) -> OptimizationResult:
        """
        Tam α optimizasyon taraması.

        Args:
            alpha_min_deg: Minimum sarım açısı [°]
            alpha_max_deg: Maksimum sarım açısı [°]
            n_steps:       Tarama adım sayısı
            overlap_limit: Maksimum |overlap| / b
            max_k:         Maksimum k değeri

        Returns:
            OptimizationResult
        """
        all_points: List[AlphaPoint] = []
        families:   Dict[int, PatternFamily] = {}

        alpha_arr = np.linspace(
            math.radians(alpha_min_deg),
            math.radians(alpha_max_deg),
            n_steps,
        )

        for alpha_rad in alpha_arr:
            K       = self._K(alpha_rad)
            n_ideal = self._n_ideal(alpha_rad)

            pts = self._best_k_for_alpha(
                alpha_rad    = alpha_rad,
                K            = K,
                n_ideal      = n_ideal,
                overlap_limit= overlap_limit,
                max_k        = max_k,
            )

            for pt in pts:
                all_points.append(pt)
                if pt.k not in families:
                    families[pt.k] = PatternFamily(family_k=pt.k)
                families[pt.k].members.append(pt)

        # Pareto front hesapla
        pareto = self._compute_pareto(all_points)

        # En iyi genel
        best = None
        if all_points:
            feasible = [p for p in all_points if p.machine_ok]
            pool     = feasible if feasible else all_points
            best     = max(pool, key=lambda p: p.composite_quality)

        return OptimizationResult(
            R_mm         = self.R,
            L_mm         = self.L,
            b_mm         = self.b,
            fiber_speed  = self.fiber_speed,
            alpha_range  = (alpha_min_deg, alpha_max_deg),
            n_alpha_steps= n_steps,
            all_points   = all_points,
            families     = families,
            pareto_front = pareto,
            best_overall = best,
        )

    def _compute_pareto(
        self,
        points: List[AlphaPoint],
    ) -> List[AlphaPoint]:
        """
        2D Pareto front hesapla: min(|overlap_ratio|) × min(k).

        Makine kısıtlarını sağlayan noktalar öncelikli.
        Bir nokta başka bir nokta tarafından hem k hem overlap açısından
        domine edilmiyorsa Pareto front üyesidir.
        """
        if not points:
            return []

        # Makine-uyumlu olanları önce dene
        feasible = [p for p in points if p.machine_ok]
        pool     = feasible if len(feasible) > 2 else points

        pareto = []
        for candidate in pool:
            dominated = False
            for other in pool:
                if other is candidate:
                    continue
                # other, candidate'i domine ediyor mu?
                # (her iki kriteri de en az candidate kadar iyi, birinde daha iyi)
                if (other.overlap_ratio <= candidate.overlap_ratio and
                        other.k <= candidate.k and
                        (other.overlap_ratio < candidate.overlap_ratio or
                         other.k < candidate.k)):
                    dominated = True
                    break
            if not dominated:
                pareto.append(candidate)

        # composite_quality'e göre sırala
        pareto.sort(key=lambda p: -p.composite_quality)
        return pareto

    # -----------------------------------------------------------------------
    # Sensitivity Analysis
    # -----------------------------------------------------------------------

    def sensitivity_report(
        self,
        alpha_center_deg: float,
        delta_deg:        float = 2.0,
        n_pts:            int   = 20,
    ) -> str:
        """
        Belirli bir α etrafında hassasiyet analizi raporu.

        α ± delta aralığında overlap ve pattern kalitesinin değişimini gösterir.
        """
        alphas = np.linspace(
            alpha_center_deg - delta_deg,
            alpha_center_deg + delta_deg,
            n_pts,
        )

        lines = [
            f"  Hassasiyet Analizi: α = {alpha_center_deg:.2f}° ± {delta_deg:.1f}°",
            f"  {'α':>8}  {'K(°)':>10}  {'n_ideal':>8}  {'f(α)':>8}  "
            f"{'n/f ratio':>10}",
            "  " + "-" * 52,
        ]
        for a_deg in alphas:
            a_rad   = math.radians(a_deg)
            K_val   = self._K(a_rad)
            n_val   = self._n_ideal(a_rad)
            f_val   = K_val / (2.0 * math.pi)
            ratio   = n_val / f_val if f_val > 0 else 0
            lines.append(
                f"  {a_deg:>7.3f}°  {math.degrees(K_val):>9.4f}°  "
                f"{n_val:>8.3f}  {f_val:>8.5f}  {ratio:>10.4f}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Quick Diagnostic
# ---------------------------------------------------------------------------

def find_sweet_spots(
    R_mm: float, L_mm: float, b_mm: float,
    alpha_min: float = 8.0, alpha_max: float = 80.0,
    n_steps: int = 1000,
) -> List[Tuple[float, int, float]]:
    """
    K/2π'nin "iyi" rasyonel yaklaşımlarına karşılık gelen α sweet spot'larını bul.

    "İyi": küçük k ile |k·K/2π - round(k·K/2π)| < 0.05

    Returns:
        [(alpha_deg, k, closure_quality), ...]
    """
    spots = []
    for a_deg in np.linspace(alpha_min, alpha_max, n_steps):
        a_rad   = math.radians(a_deg)
        K       = 2.0 * L_mm * math.tan(a_rad) / R_mm
        n_ideal = 2.0 * math.pi * R_mm * math.cos(a_rad) / b_mm
        k_min   = max(2, int(n_ideal * 0.6))
        k_max   = int(n_ideal * 1.4)

        for k in range(k_min, k_max + 1):
            p_float = k * K / (2.0 * math.pi)
            p       = round(p_float)
            if p <= 0:
                continue
            if gcd(p, k) != 1:
                continue
            frac    = abs(p_float - p)   # [0, 0.5]
            if frac < 0.04:
                spots.append((a_deg, k, frac))

    # closure_quality'e göre sırala
    spots.sort(key=lambda x: x[2])
    # Benzerleri temizle: k başına en iyi α'yı tut
    seen_k = {}
    for a_deg, k, q in spots:
        if k not in seen_k or q < seen_k[k][1]:
            seen_k[k] = (a_deg, q)

    return [(a_deg, k, q) for k, (a_deg, q) in sorted(seen_k.items(), key=lambda x: x[1][1])]
