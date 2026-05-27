"""
pattern_solver.py — Winding Pattern Çözücü
==========================================
Diophantine denklem sistemi çözücü: matematiksel geçerli ve
üretilebilir winding pattern adaylarını bulur.

Teori (Koussios 2004, Bölüm 8):
  Temel büyüklükler:
    K  = Δθ  : Tek devrenin turn-around açısı [rad]
    n  : Ekvatorda toplam fiber band sayısı
    p  : Pattern adım sayısı (her devre n·Δθ/(2π) ≈ p/k aralık atlar)
    d  : İstenen katman sayısı
    k  = n·d : Toplam devre sayısı

  Diophantine koşulları (Koussios Eq. 8.12):
    Leading:  p·k = d·n + 1   → son band ilk bandın üzerine hafif biner
    Lagging:  p·k = d·n - 1   → son band bir öncekinin gerisinde kalır

  Artık açı (residual):
    ε = k·K - 2π·p   [rad]
    ε > 0 → leading;  ε < 0 → lagging

  Ekvatorda overlap:
    overlap_mm = ε·R / cos(α)     [mm, signed]
    |overlap_mm| < b/2 → acceptable (half-bandwidth criterion)

Referans: Koussios (2004) s.127-136, Bookhart & Fowler (1968) NOCIRC
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from math import gcd
from typing import Iterator, List, Optional, Tuple

from geometry import CylindricalMandrel
from winding_math import WindingParameters, ClairautConstants


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PatternCandidate:
    """
    Tek bir geçerli winding pattern adayı.

    n  : Ekvatorda fiber band sayısı (= circumference / b_eff)
    p  : Pattern adım sayısı (devreler arası kayma)
    k  : Toplam devre sayısı = n·d
    d  : Katman sayısı

    epsilon_rad   : Artık açı ε = k·K - 2π·p [rad]
    overlap_mm    : Ekvatorda lineer overlap (+) / gap (-) [mm]
    overlap_ratio : |overlap_mm| / b  (0=mükemmel, 1=tam band overlap)
    diophantine_lhs: p·k değeri (doğrulama için)
    diophantine_rhs: d·n ± 1  (leading: +1, lagging: -1)
    pattern_type  : "leading" | "lagging" | "exact"
    """
    n:              int
    p:              int
    k:              int
    d:              int
    epsilon_rad:    float
    overlap_mm:     float
    overlap_ratio:  float
    pattern_type:   str      # "leading" | "lagging" | "exact"

    # Türetilmiş doğrulama alanları
    diophantine_lhs: int    # p·k
    diophantine_rhs: int    # d·n ± 1

    @property
    def is_valid_diophantine(self) -> bool:
        """Diophantine koşulunu sağlıyor mu?"""
        return (self.diophantine_lhs == self.diophantine_rhs and
                gcd(self.p, self.k) == 1)

    @property
    def is_coprime(self) -> bool:
        """gcd(p, k) = 1 mı?"""
        return gcd(self.p, self.k) == 1

    def summary_line(self) -> str:
        """Tek satır özet."""
        return (
            f"n={self.n:3d}  p={self.p:3d}  k={self.k:4d}  "
            f"overlap={self.overlap_mm:+7.3f}mm  "
            f"({self.overlap_ratio*100:5.1f}%)  "
            f"{self.pattern_type:<8s}  "
            f"Dio: {self.diophantine_lhs}={self.diophantine_rhs}  "
            f"gcd={gcd(self.p,self.k)}"
        )

    def __repr__(self) -> str:
        return (
            f"PatternCandidate(n={self.n}, p={self.p}, k={self.k}, d={self.d}, "
            f"overlap={self.overlap_mm:.3f}mm, type={self.pattern_type!r})"
        )


@dataclass(frozen=True, slots=True)
class PatternSearchConfig:
    """
    Pattern arama parametreleri.

    n_tolerance    : n_ideal etrafında arama aralığı oranı [0.3 = ±30%]
    overlap_limit  : Maksimum kabul edilebilir |overlap_mm| / b [0.5 = yarı bant]
    max_candidates : Döndürülecek maksimum aday sayısı
    exact_tol_rad  : "exact" pattern toleransı [rad]
    """
    n_tolerance:    float = 0.35
    overlap_limit:  float = 0.50
    max_candidates: int   = 20
    exact_tol_rad:  float = 1e-4


# ---------------------------------------------------------------------------
# Pattern Solver
# ---------------------------------------------------------------------------

class PatternSolver:
    """
    Filament winding için Diophantine pattern çözücü.

    Algoritma:
      1. Temel parametreleri hesapla (K, n_ideal, b_eff)
      2. n ∈ [n_min, n_max] için tüm (n, p) çiftlerini tara
      3. Her (n, d) çifti için: k = n·d, p_float = k·K/(2π)
         p ∈ {floor(p_float), ceil(p_float)} için:
           - gcd(p, k) = 1 kontrolü
           - ε hesabı ve overlap kontrolü
           - PatternCandidate oluştur
      4. Overlap miktarına göre sırala
      5. max_candidates kadar döndür

    Üretilebilirlik farkı:
      Matematiksel geçerlilik: gcd(p,k)=1 ve |overlap|<b/2
      Üretilebilirlik:         + spindle rpm limiti + carriage hız limiti
                               → PatternScoreEngine'de değerlendirilir
    """

    def __init__(
        self,
        mandrel:   CylindricalMandrel,
        winding:   WindingParameters,
        constants: ClairautConstants,
        config:    Optional[PatternSearchConfig] = None,
    ) -> None:
        if not isinstance(mandrel, CylindricalMandrel):
            raise NotImplementedError(
                "PatternSolver Faz 2'de yalnızca CylindricalMandrel destekler."
            )
        self.mandrel   = mandrel
        self.winding   = winding
        self.constants = constants
        self.config    = config or PatternSearchConfig()

        # Temel büyüklükler (bir kez hesapla)
        self._K        = self._compute_turn_around_angle()
        self._n_ideal  = self._compute_n_ideal()
        self._b_eff    = constants.bandwidth_eff

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    @property
    def turn_around_angle_rad(self) -> float:
        """K = Δθ [rad]: tek devrenin azimut artışı."""
        return self._K

    @property
    def n_ideal(self) -> float:
        """İdeal band sayısı (gerçel — tam sayı değil)."""
        return self._n_ideal

    @property
    def b_eff_mm(self) -> float:
        """Efektif band genişliği = b/cos(α) [mm]."""
        return self._b_eff

    def solve(self) -> List[PatternCandidate]:
        """
        Tüm geçerli pattern adaylarını bul ve puanlama için hazırla.

        Returns:
            PatternCandidate listesi, |overlap_mm| değerine göre sıralı.
        """
        d      = self.winding.n_layers
        cfg    = self.config
        K      = self._K
        b      = self.winding.bandwidth
        R      = self.mandrel.radius
        cos_a  = math.cos(self.winding.alpha_rad)

        n_min = max(2, int(math.floor(self._n_ideal * (1.0 - cfg.n_tolerance))))
        n_max = int(math.ceil(self._n_ideal * (1.0 + cfg.n_tolerance)))

        candidates: List[PatternCandidate] = []

        for n in range(n_min, n_max + 1):
            k       = n * d
            p_float = k * K / (2.0 * math.pi)  # gerçel p değeri

            for p in self._p_candidates(p_float):
                if p <= 0:
                    continue
                if gcd(p, k) != 1:
                    continue  # Diophantine: gcd koşulu

                # Artık açı ve overlap
                epsilon    = k * K - 2.0 * math.pi * p   # [rad]
                overlap_mm = epsilon * R / cos_a          # [mm, signed]
                ratio      = abs(overlap_mm) / b

                if ratio > cfg.overlap_limit:
                    continue  # Kabul edilemez overlap

                # Pattern tipi
                dio_rhs = d * n
                if abs(epsilon) < cfg.exact_tol_rad:
                    ptype   = "exact"
                    dio_rhs_adj = p * k  # exact → p·k = d·n (nadiren olur)
                elif epsilon > 0:
                    ptype       = "leading"
                    dio_rhs_adj = d * n + 1  # leading: p·k = d·n + 1
                else:
                    ptype       = "lagging"
                    dio_rhs_adj = d * n - 1  # lagging: p·k = d·n - 1

                cand = PatternCandidate(
                    n             = n,
                    p             = p,
                    k             = k,
                    d             = d,
                    epsilon_rad   = epsilon,
                    overlap_mm    = overlap_mm,
                    overlap_ratio = ratio,
                    pattern_type  = ptype,
                    diophantine_lhs = p * k,
                    diophantine_rhs = dio_rhs_adj,
                )
                candidates.append(cand)

        # Tekrar kaldır (aynı (n,p) farklı yoldan gelebilir)
        seen = set()
        unique = []
        for c in candidates:
            key = (c.n, c.p, c.k)
            if key not in seen:
                seen.add(key)
                unique.append(c)

        # |overlap_mm| göre sırala (en az overlap en iyi)
        unique.sort(key=lambda c: abs(c.overlap_mm))
        return unique[: cfg.max_candidates]

    def solve_best(self) -> Optional[PatternCandidate]:
        """En iyi (minimum overlap) pattern adayını döndür."""
        candidates = self.solve()
        return candidates[0] if candidates else None

    def solve_by_type(self, pattern_type: str) -> List[PatternCandidate]:
        """
        Belirli tipteki pattern adaylarını döndür.

        Args:
            pattern_type: "leading" | "lagging" | "exact"
        """
        return [c for c in self.solve() if c.pattern_type == pattern_type]

    # -----------------------------------------------------------------------
    # Report
    # -----------------------------------------------------------------------

    def report(self, max_show: int = 10) -> str:
        """İnsan okunabilir pattern raporu."""
        candidates = self.solve()
        d          = self.winding.n_layers

        lines = [
            "=" * 72,
            "WINDING PATTERN SOLVER RAPORU",
            "=" * 72,
            f"  Mandrel:      R={self.mandrel.radius:.2f} mm, L={self.mandrel.length:.2f} mm",
            f"  Sarım açısı:  α={self.winding.alpha_deg:.4f}°",
            f"  Band:         b={self.winding.bandwidth:.2f} mm → b_eff={self._b_eff:.4f} mm",
            f"  Katman sayısı: d={d}",
            f"  K (Δθ):       {math.degrees(self._K):.6f}° = {self._K:.8f} rad",
            f"  K/(2π):       {self._K/(2*math.pi):.8f} (devre başına devir)",
            f"  n_ideal:      {self._n_ideal:.6f} (gerçel)",
            f"  Arama aralığı: n ∈ [{int(self._n_ideal*(1-self.config.n_tolerance))},"
            f"{int(self._n_ideal*(1+self.config.n_tolerance))}]",
            "-" * 72,
            f"  {'#':>3}  {'n':>4}  {'p':>4}  {'k':>5}  "
            f"{'overlap':>9}  {'ratio':>7}  {'type':<8}  {'Dio LHS=RHS':>14}  {'gcd':>4}",
            "-" * 72,
        ]

        for i, c in enumerate(candidates[:max_show]):
            valid_marker = "✓" if c.is_valid_diophantine else "✗"
            lines.append(
                f"  {i+1:>3}  {c.n:>4}  {c.p:>4}  {c.k:>5}  "
                f"{c.overlap_mm:>+8.3f}mm  {c.overlap_ratio*100:>6.1f}%  "
                f"{c.pattern_type:<8}  "
                f"{c.diophantine_lhs:>6}={c.diophantine_rhs:<6}  "
                f"{gcd(c.p,c.k):>2} {valid_marker}"
            )

        if not candidates:
            lines.append("  [Pattern bulunamadı — α veya b değerini değiştirin]")

        lines.append("=" * 72)
        return "\n".join(lines)

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _compute_turn_around_angle(self) -> float:
        """
        K = Δθ: Silindir için analitik.
        K = 2·L·tan(α) / R   [rad]

        Faz 3'te dome geometry eklenince bu integral olacak.
        """
        return (2.0 * self.mandrel.length *
                math.tan(self.winding.alpha_rad) /
                self.mandrel.radius)

    def _compute_n_ideal(self) -> float:
        """
        İdeal band sayısı (gerçel): n_ideal = 2π·R·cos(α) / (d·b)
        Tam sayı olması gerekmez; Diophantine solver en yakın integer'ları bulur.
        """
        d = self.winding.n_layers
        return (2.0 * math.pi * self.mandrel.radius *
                math.cos(self.winding.alpha_rad) /
                (d * self.winding.bandwidth))

    @staticmethod
    def _p_candidates(p_float: float) -> Iterator[int]:
        """
        Verilen gerçel p değeri için denenecek tam sayı adaylar.
        floor ve ceil dışında ±1 komşuları da dene (pattern aralığı genişletme).
        """
        base = [math.floor(p_float), math.ceil(p_float)]
        candidates = set()
        for b in base:
            for delta in range(-1, 3):  # ±1 komşu
                candidates.add(b + delta)
        yield from sorted(candidates)
