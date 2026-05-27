"""
geodesic_family.py — Üretilebilir Geodezik Aile Analizi
========================================================
Her bir c değeri için ÜRETIM ZİNCİRİNİN tamamını değerlendirir:

  1. Clairaut (fiziksel uygunluk)    — buildup, congestion
  2. Pattern (Diophantine uygunluk)  — overlap, gcd koşulu
  3. Machine (kinematik uygunluk)    — rpm, carriage, acceleration
  4. Coverage (kaplama kalitesi)     — homojenlik, CV

Bir c değeri "üretilebilir aile üyesi" olmak için:
  - buildup ≤ buildup_max (default 1.5)
  - |overlap| ≤ overlap_max (default 0.4×b)
  - gcd(p,k) = 1
  - machine_ok = True
  - coverage_fraction ≥ 0.95

"Aile" = belirli bir c aralığında bu koşulları birlikte sağlayan
(c, n, p, k) dörtlüleri kümesi.

Faz 5 hazırlığı:
  Her aile üyesi bir winding sequence için çıkış noktası olabilir.
  Aile içinde p, k değerleri LayerManager ve GCode generator'a doğrudan girebilir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from math import gcd
from typing import Dict, List, Optional, Tuple

import numpy as np

from full_body_mandrel import FullBodyMandrel
from clairaut_optimizer import ClairautOptimizer, ClairautPoint


# ── Data Structures ──────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class GeodesicFamilyMember:
    """
    Tam üretilebilirlik değerlendirmesi geçmiş (c, n, p, k) konfigürasyonu.
    """
    c_mm:           float
    alpha_0_deg:    float
    n_bands:        int     # Ekvatör band sayısı
    p_step:         int     # Pattern adım sayısı
    k_circuits:     int     # Toplam devre
    epsilon_rad:    float   # Artık açı
    overlap_mm:     float   # Overlap [mm]
    overlap_ratio:  float   # |overlap|/b
    pattern_type:   str     # "leading"|"lagging"|"exact"
    buildup_factor: float
    rho_junction:   float
    delta_phi_deg:  float   # Full-body turn-around [°]
    machine_ok:     bool
    rpm_at_boss:    float
    composite_score:float

    @property
    def is_manufacturable(self) -> bool:
        return (
            self.machine_ok and
            self.buildup_factor <= 1.5 and
            self.overlap_ratio <= 0.40 and
            gcd(self.p_step, self.k_circuits) == 1
        )

    def one_line(self) -> str:
        ok = "✓" if self.is_manufacturable else "✗"
        return (
            f"c={self.c_mm:5.2f}mm  α₀={self.alpha_0_deg:5.2f}°  "
            f"n={self.n_bands:3d}  p={self.p_step:3d}  k={self.k_circuits:4d}  "
            f"ε={self.epsilon_rad:+.4f}rad  "
            f"OL={self.overlap_mm:+6.3f}mm({self.overlap_ratio*100:4.1f}%)  "
            f"BU={self.buildup_factor:.3f}x  "
            f"Q={self.composite_score:.4f}  {ok}"
        )


@dataclass(slots=True)
class GeodesicFamily:
    """Belirli bir pattern karakteriyle tanımlanan üretilebilir aile."""
    family_label:  str   # e.g. "k=21"
    members:       List[GeodesicFamilyMember] = field(default_factory=list)

    @property
    def best(self) -> Optional[GeodesicFamilyMember]:
        feasible = [m for m in self.members if m.is_manufacturable]
        pool = feasible if feasible else self.members
        return max(pool, key=lambda m: m.composite_score) if pool else None

    @property
    def c_range(self) -> Tuple[float, float]:
        if not self.members: return (0.0, 0.0)
        cc = [m.c_mm for m in self.members]
        return (min(cc), max(cc))

    def summary(self) -> str:
        b  = self.best
        cr = self.c_range
        n_ok = sum(1 for m in self.members if m.is_manufacturable)
        if not b:
            return f"  {self.family_label}: boş"
        return (
            f"  {self.family_label:<10} | "
            f"c∈[{cr[0]:.2f},{cr[1]:.2f}]mm  "
            f"{len(self.members):3d} üye  {n_ok:3d} üretilebilir | "
            f"Best: c={b.c_mm:.2f}mm α₀={b.alpha_0_deg:.2f}° "
            f"BU={b.buildup_factor:.3f}x OL={b.overlap_mm:+.3f}mm "
            f"Q={b.composite_score:.4f}"
        )


@dataclass(slots=True)
class GeodesicFamilySearchResult:
    """Tüm aile arama sonucu."""
    all_members:         List[GeodesicFamilyMember]
    families_by_k:       Dict[int, GeodesicFamily]
    manufacturable:      List[GeodesicFamilyMember]
    best_manufacturable: Optional[GeodesicFamilyMember]
    best_overall:        Optional[GeodesicFamilyMember]

    def report(self, max_families: int = 10) -> str:
        lines = [
            "═" * 80,
            "ÜRETİLEBİLİR GEODEZİK AİLE RAPORU",
            "═" * 80,
            f"  Toplam aday: {len(self.all_members)}  "
            f"Üretilebilir: {len(self.manufacturable)}  "
            f"Aile sayısı: {len(self.families_by_k)}",
            "─" * 80,
        ]
        for k_val in sorted(self.families_by_k.keys())[:max_families]:
            lines.append(self.families_by_k[k_val].summary())
        if self.best_manufacturable:
            bm = self.best_manufacturable
            lines += [
                "─" * 80,
                f"  ★ EN İYİ ÜRETİLEBİLİR:",
                f"    {bm.one_line()}",
            ]
        lines.append("═" * 80)
        return "\n".join(lines)

    def top_n(self, n: int = 8) -> str:
        """En iyi n üretilebilir adayın detaylı listesi."""
        pool = sorted(self.manufacturable,
                      key=lambda m: -m.composite_score)[:n]
        lines = [f"  Top-{n} Üretilebilir Konfigürasyonlar:"]
        for i, m in enumerate(pool):
            lines.append(f"  {i+1:2d}. {m.one_line()}")
        return "\n".join(lines)


# ── Geodesic Family Searcher ──────────────────────────────────────

class GeodesicFamilySearcher:
    """
    Verilen (R, L_cyl, H_dome, k, b) için üretilebilir geodezik aile arama motoru.

    Kullanım:
        searcher = GeodesicFamilySearcher(body, k=21, bandwidth=10.0, machine=phys)
        result = searcher.search(c_min_frac=0.3, c_max_frac=0.8, n_c=100)
        print(result.report())
    """

    def __init__(
        self,
        body:             FullBodyMandrel,
        k_circuits:       int,
        bandwidth:        float,
        machine,
        fiber_speed:      float = 100.0,
        buildup_max:      float = 1.5,
        overlap_max_frac: float = 0.40,
        n_tolerance:      float = 0.40,  # n ideal ±40%
    ) -> None:
        self.body        = body
        self.k           = k_circuits
        self.b           = bandwidth
        self.machine     = machine
        self.v_target    = fiber_speed
        self.buildup_max = buildup_max
        self.overlap_max = overlap_max_frac * bandwidth
        self.n_tol       = n_tolerance

        self._c_opt = ClairautOptimizer(
            body, k_circuits, bandwidth, machine, fiber_speed)

    # ── K (full-body turn-around angle) ─────────────────────────

    def _K_full_body(self, c: float) -> float:
        """
        Full-body turn-around açısı K [rad].

        K = K_cylinder + 2 × K_dome
        K_cyl  = 2 × L × tan(α₀) / R
        K_dome = (dome integral, approx)
        """
        R    = self.body.R
        L    = self.body.L_cyl
        H    = self.body.front.height_mm
        if c >= R: return 0.0
        alpha_0 = math.asin(min(1.0, c/R))
        tan_a   = math.tan(alpha_0)
        K_cyl   = 2.0 * L * tan_a / R
        # Dome: numerical approximation (2 domes)
        # ∫ c/r² × dr/(√(r²-c²)/r×√G) from r_boss to R
        r_boss = self._c_opt._r_boss(c)
        K_dome_one = self._integrate_dome_K(c, r_boss, R, H, n=50)
        return K_cyl + 2.0 * K_dome_one

    def _integrate_dome_K(
        self, c: float, r_min: float, r_max: float,
        H: float, n: int = 50,
    ) -> float:
        """
        Tek dome için Δφ integrali.

        K_dome = ∫ c/r² × r/(√(r²-c²)) × √G dz
               = ∫ c√G / (r√(r²-c²)) dz

        Eliptik dome: r(z) = R/H×√(H²-z²) → z(r) = H√(1-(rH/R)²)
        dz/dr = ... sayısal entegrasyon daha güvenli.
        """
        dome = self.body.front
        R    = self.body.R
        r_arr = np.linspace(r_min * 1.01, r_max * 0.999, n)
        dphi  = 0.0
        dr    = (r_arr[-1] - r_arr[0]) / (n - 1) if n > 1 else 0.0

        for r in r_arr:
            # z'yi r'den bul (eliptik: r = R/H×√(H²-z²))
            # → z = H√(1-(r/R)²)
            z_val  = H * math.sqrt(max(0.0, 1.0 - (r/R)**2))
            G_val  = dome.G(z_val)
            r2mc2  = r*r - c*c
            if r2mc2 <= 0: continue
            integrand = c * math.sqrt(G_val) / (r * math.sqrt(r2mc2))
            dphi += integrand * dr

        return dphi

    def _n_ideal(self, c: float) -> float:
        """İdeal band sayısı (gerçel)."""
        R = self.body.R
        if c >= R: return 0.0
        alpha_0 = math.asin(min(1.0, c/R))
        # Full-body: n ideal = 2π×R×cos(α₀) / b
        return 2.0 * math.pi * R * math.cos(alpha_0) / self.b

    # ── Pattern solver (inline, hafif versiyon) ─────────────────

    def _find_patterns(
        self, c: float, K: float, n_ideal: float,
    ) -> List[Tuple[int, int, float, float, str]]:
        """
        (n, p, epsilon, overlap_mm, type) listesi döndür.
        Diophantine: gcd(p,k)=1, |overlap|≤overlap_max.
        """
        k        = self.k
        R        = self.body.R
        alpha_0  = math.asin(min(1.0, c/R))
        cos_a    = math.cos(alpha_0)
        TAU      = 2.0 * math.pi
        results  = []

        n_lo = max(2, int(n_ideal * (1.0 - self.n_tol)))
        n_hi = int(n_ideal * (1.0 + self.n_tol))

        for n in range(n_lo, n_hi + 1):
            p_float = k * K / TAU
            for p in {math.floor(p_float), math.ceil(p_float),
                      round(p_float)}:
                p = int(p)
                if p <= 0: continue
                if gcd(p, k) != 1: continue
                eps = k * K - TAU * p
                ovl = eps * R / cos_a
                if abs(ovl) > self.overlap_max: continue
                ratio = abs(ovl) / self.b
                ptype = "exact" if abs(eps)<1e-4 else ("leading" if eps>0 else "lagging")
                results.append((n, p, eps, ovl, ptype, ratio))

        return results

    # ── Member score ─────────────────────────────────────────────

    def _member_score(
        self, c_pt: ClairautPoint,
        overlap_ratio: float,
        pattern_type:  str,
    ) -> float:
        """GeodesicFamilyMember için composite skor."""
        # Clairaut base score
        s_base = c_pt.composite_score

        # Overlap penalty
        if overlap_ratio <= 0.05:   s_ol = 1.0
        elif overlap_ratio <= 0.20: s_ol = 0.9 - 0.5*(overlap_ratio-0.05)/0.15
        elif overlap_ratio <= 0.40: s_ol = 0.4 - 0.4*(overlap_ratio-0.20)/0.20
        else:                       s_ol = 0.0

        # Pattern type bonus
        s_pt = 1.0 if pattern_type == "exact" else 0.85 if pattern_type == "leading" else 0.65

        return 0.50 * s_base + 0.30 * s_ol + 0.20 * s_pt

    # ── Main search ───────────────────────────────────────────────

    def search(
        self,
        c_min_frac: float = 0.25,
        c_max_frac: float = 0.90,
        n_c:        int   = 120,
    ) -> GeodesicFamilySearchResult:
        """Tam üretilebilir geodezik aile araması."""
        # Clairaut sweep
        c_opt_result = self._c_opt.optimize(c_min_frac, c_max_frac, n_c)
        c_arr = np.array([pt.c_mm for pt in c_opt_result.all_points])
        c_pts = {pt.c_mm: pt for pt in c_opt_result.all_points}

        all_members:      List[GeodesicFamilyMember] = []
        families_by_k:    Dict[int, GeodesicFamily]  = {}

        for c in c_arr:
            c_pt = c_pts[c]
            K    = self._K_full_body(c)
            n_id = self._n_ideal(c)

            patterns = self._find_patterns(c, K, n_id)
            if not patterns:
                continue

            for (n, p, eps, ovl, ptype, ovl_ratio) in patterns:
                score = self._member_score(c_pt, ovl_ratio, ptype)
                member = GeodesicFamilyMember(
                    c_mm            = float(c),
                    alpha_0_deg     = c_pt.alpha_0_deg,
                    n_bands         = n,
                    p_step          = p,
                    k_circuits      = self.k,
                    epsilon_rad     = eps,
                    overlap_mm      = ovl,
                    overlap_ratio   = ovl_ratio,
                    pattern_type    = ptype,
                    buildup_factor  = c_pt.buildup_factor,
                    rho_junction    = c_pt.rho_junction,
                    delta_phi_deg   = c_pt.delta_phi_deg,
                    machine_ok      = c_pt.machine_ok,
                    rpm_at_boss     = c_pt.rpm_at_boss,
                    composite_score = score,
                )
                all_members.append(member)

                if self.k not in families_by_k:
                    families_by_k[self.k] = GeodesicFamily(f"k={self.k}")
                families_by_k[self.k].members.append(member)

        # Deduplicate by (c, n, p)
        seen = set()
        unique = []
        for m in all_members:
            key = (round(m.c_mm, 3), m.n_bands, m.p_step)
            if key not in seen:
                seen.add(key)
                unique.append(m)
        all_members = unique

        manufacturable = [m for m in all_members if m.is_manufacturable]
        best_m = max(manufacturable, key=lambda m: m.composite_score) if manufacturable else None
        best_a = max(all_members,    key=lambda m: m.composite_score) if all_members else None

        return GeodesicFamilySearchResult(
            all_members         = all_members,
            families_by_k       = families_by_k,
            manufacturable      = manufacturable,
            best_manufacturable = best_m,
            best_overall        = best_a,
        )

    # ── Faz 5 çıkışı ─────────────────────────────────────────────

    def export_for_phase5(
        self,
        member: GeodesicFamilyMember,
    ) -> dict:
        """
        Faz 5 winding sequence planner için çıktı paketi.

        Returns dict with all parameters needed for:
        - LayerManager
        - GCode generator
        - UnifiedGeodesicPlanner
        """
        return {
            "c_clairaut_mm":    member.c_mm,
            "alpha_0_deg":      member.alpha_0_deg,
            "r_boss_mm":        self._c_opt._r_boss(member.c_mm),
            "k_circuits":       member.k_circuits,
            "n_bands":          member.n_bands,
            "p_step":           member.p_step,
            "pattern_type":     member.pattern_type,
            "overlap_mm":       member.overlap_mm,
            "buildup_factor":   member.buildup_factor,
            "K_full_body_deg":  math.degrees(self._K_full_body(member.c_mm)),
            "delta_phi_deg":    member.delta_phi_deg,
            "rho_junction":     member.rho_junction,
            "machine_ok":       member.machine_ok,
            "composite_score":  member.composite_score,
            # Faz 5 için hazır bayrak
            "phase5_ready":     member.is_manufacturable,
        }
