"""
pattern_score_engine.py — Pattern Üretilebilirlik Puanlama Motoru
=================================================================
PatternCandidate nesnelerini 9 kriter üzerinden puanlayarak
"matematiksel geçerli" ile "üretilebilir ve stabil" ayrımını yapar.

Puanlama Kriterleri:
  1.  Overlap Amount          — geometrik kaplama kalitesi
  2.  Gap Risk                — boşluk tehlikesi (lagging pattern için kritik)
  3.  Manufacturability       — toplam devre sayısı ve karmaşıklık
  4.  Spindle Realism         — spindle rpm makine limitinde mi?
  5.  Carriage Acceleration   — turn-around hızlanma / yavaşlama
  6.  Layer Homogeneity       — katmanlar arası kalınlık düzgünlüğü
  7.  Repeatability           — pattern tekrarlanabilirliği (k sayısı kontrolü)
  8.  Winding Stability       — fiber slip riski (geodezik → her zaman 1.0)
  9.  Process Efficiency      — toplam sarım süresi ve fiber tüketimi

Her kriter: [0.0, 1.0] normalleştirilmiş skor
Ağırlıklı toplam: composite_score ∈ [0.0, 1.0]

Kriter ağırlıkları (mühendislik kararı — ayarlanabilir):
  Overlap:        0.22  → kaplama kalitesi en kritik
  Gap Risk:       0.16  → boşluk yapısal hasar riski yüksek
  Manufacturab.:  0.14  → üretilebilirlik temel gereksinim
  Spindle:        0.12  → makine limiti hard constraint
  Carriage Acc.:  0.10  → reçine akışını etkiler
  Homogeneity:    0.10  → homojen kalınlık → iyi yapısal özellik
  Repeatability:  0.07  → setup kolaylığı
  Stability:      0.05  → geodezik için zaten 1.0 (Faz 3'te önem kazanır)
  Efficiency:     0.04  → üretim süresi (secondary)

Referans: Koussios (2004) Tablo 12.1; Özbek et al. (2020) makine parametreleri
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from geometry import CylindricalMandrel
from motion_planner import MachineConfig
from pattern_solver import PatternCandidate
from winding_math import WindingParameters, ClairautConstants


# ---------------------------------------------------------------------------
# Scoring Weights Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ScoringWeights:
    """
    Kriter ağırlıkları — toplam 1.0 olmalı.

    Mühendislik gerekçesi:
      overlap_amount (0.22): Kaplama kalitesi yapısal bütünlüğün temelidir.
        Aşırı overlap → ek katman kalınlığı, reçine zenginleşmesi, void oluşumu.
      gap_risk (0.16): Boşluklar → matris çatlakları → katastrofik hasar.
        Leading pattern'lar daha güvenli (overlap > gap).
      manufacturability (0.14): Çok fazla devre → uzun süre, başarısızlık riski.
      spindle_realism (0.12): Hard limit ihlali → makine durur → G28 emergency.
      carriage_accel (0.10): Turn-around'da hızlı değişim → reçine dökülmesi.
      layer_homogeneity (0.10): Homojen t(z) → öngörülebilir mekanik özellik.
      repeatability (0.07): Küçük k → kolay program, az hata şansı.
      winding_stability (0.05): Geodezik: daima 1.0. Non-geodezik Faz 3'te önem.
      process_efficiency (0.04): İkincil öncelik — maliyet parametresi.
    """
    overlap_amount:     float = 0.22
    gap_risk:           float = 0.16
    manufacturability:  float = 0.14
    spindle_realism:    float = 0.12
    carriage_accel:     float = 0.10
    layer_homogeneity:  float = 0.10
    repeatability:      float = 0.07
    winding_stability:  float = 0.05
    process_efficiency: float = 0.04

    def __post_init__(self) -> None:
        total = (self.overlap_amount + self.gap_risk + self.manufacturability +
                 self.spindle_realism + self.carriage_accel + self.layer_homogeneity +
                 self.repeatability + self.winding_stability + self.process_efficiency)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"Ağırlık toplamı 1.0 olmalı, hesaplanan: {total:.6f}"
            )


@dataclass(slots=True)
class CriterionScore:
    """Tek kriter puanı — değer + açıklama."""
    name:        str
    score:       float    # [0, 1]
    weight:      float
    weighted:    float    # score × weight
    reason:      str      # Puan gerekçesi
    is_critical: bool     # True → bu kriter başarısız olursa tüm pattern reddedilir

    @property
    def grade(self) -> str:
        """İnsan okunabilir puan sınıfı."""
        if self.score >= 0.90: return "A+"
        if self.score >= 0.80: return "A"
        if self.score >= 0.70: return "B"
        if self.score >= 0.55: return "C"
        if self.score >= 0.35: return "D"
        return "F"


@dataclass(slots=True)
class PatternScore:
    """
    Bir PatternCandidate için tam puanlama sonucu.

    composite_score: Ağırlıklı toplam [0, 1] — ana sıralama kriteri
    is_recommended:  True → endüstriyel üretim için önerilir
    is_feasible:     True → tüm hard constraint'ler sağlanıyor
    criteria:        Her kriterin detaylı skoru
    """
    candidate:       PatternCandidate
    criteria:        Dict[str, CriterionScore]
    composite_score: float
    is_feasible:     bool     # Tüm hard constraint'ler OK mi?
    is_recommended:  bool     # Composite ≥ 0.70 ve is_feasible mi?
    rejection_reason: str     # Neden reddedildi (varsa)

    def summary_line(self) -> str:
        grade = "A+" if self.composite_score >= 0.90 else \
                "A"  if self.composite_score >= 0.80 else \
                "B"  if self.composite_score >= 0.70 else \
                "C"  if self.composite_score >= 0.55 else \
                "D"  if self.composite_score >= 0.35 else "F"
        status = "✓ ÖNERİLİR" if self.is_recommended else \
                 "~ geçerli"  if self.is_feasible else "✗ reddedildi"
        return (
            f"n={self.candidate.n:3d}  p={self.candidate.p:3d}  "
            f"k={self.candidate.k:4d}  "
            f"overlap={self.candidate.overlap_mm:+7.3f}mm  "
            f"score={self.composite_score:.4f} [{grade}]  {status}"
        )

    def detail_report(self) -> str:
        """Tüm kriterlerin detaylı raporu."""
        lines = [
            f"  Pattern: n={self.candidate.n}, p={self.candidate.p}, "
            f"k={self.candidate.k}, type={self.candidate.pattern_type}",
            f"  Composite Score: {self.composite_score:.4f}  |  "
            f"{'ÖNERİLİR' if self.is_recommended else 'KABUL EDİLEBİLİR' if self.is_feasible else 'REDDEDİLDİ'}",
            f"  {'Kriter':<25}  {'Skor':>6}  {'Ağırlık':>8}  {'Katkı':>8}  {'Not':<45}",
            "  " + "-" * 100,
        ]
        for name, cs in self.criteria.items():
            critical_tag = " [KRİTİK]" if cs.is_critical and cs.score < 0.5 else ""
            lines.append(
                f"  {cs.name:<25}  {cs.score:>6.4f}  "
                f"{cs.weight:>7.2%}  {cs.weighted:>8.4f}  "
                f"{cs.grade}  {cs.reason[:43]}{critical_tag}"
            )
        if self.rejection_reason:
            lines.append(f"  ⚠ Red Sebebi: {self.rejection_reason}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Score Engine
# ---------------------------------------------------------------------------

class PatternScoreEngine:
    """
    PatternCandidate nesnelerini çok kriterli olarak puanlar.

    Kullanım:
        engine = PatternScoreEngine(mandrel, winding, constants, machine)
        candidates = solver.solve()
        scores = engine.score_all(candidates)
        best = scores[0]  # En yüksek composite_score
    """

    # Makine limitleri (Koussios 2004, Tablo 12.1 — tipik lathe winder)
    # Özbek et al. (2020): max 8000 mm/min X, 250 rpm spindle
    SPINDLE_MAX_RPM_HARD   = 250.0    # [rpm] — makine mekanik limiti
    SPINDLE_MAX_RPM_IDEAL  = 150.0    # [rpm] — reçine kalitesi için önerilen max
    CARRIAGE_MAX_ACC       = 3500.0   # [mm/s²] — X ekseni max ivme (Koussios: 3.5 m/s²)
    CARRIAGE_IDEAL_ACC     = 500.0    # [mm/s²] — smooth üretim için
    FIBER_SPEED_MAX        = 500.0    # [mm/s]  — max roving hızı
    MAX_CIRCUITS_IDEAL     = 100      # bu değerin üstü "karmaşık" sayılır
    MAX_CIRCUITS_HARD      = 500      # bu değerin üstü pratik değil

    def __init__(
        self,
        mandrel:   CylindricalMandrel,
        winding:   WindingParameters,
        constants: ClairautConstants,
        machine:   MachineConfig,
        weights:   Optional[ScoringWeights] = None,
    ) -> None:
        self.mandrel   = mandrel
        self.winding   = winding
        self.constants = constants
        self.machine   = machine
        self.weights   = weights or ScoringWeights()

        # Ön hesaplamalar
        self._R     = mandrel.radius
        self._L     = mandrel.length
        self._alpha = winding.alpha_rad
        self._b     = winding.bandwidth
        self._S     = winding.fiber_speed
        self._F     = machine.fiber_speed_target * math.cos(self._alpha) * 60.0  # mm/min

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def score(self, candidate: PatternCandidate) -> PatternScore:
        """Tek bir PatternCandidate'i puanla."""
        w  = self.weights
        cs = {}

        # --- 1. Overlap Amount ---
        cs["overlap_amount"] = self._score_overlap(candidate, w.overlap_amount)

        # --- 2. Gap Risk ---
        cs["gap_risk"] = self._score_gap_risk(candidate, w.gap_risk)

        # --- 3. Manufacturability ---
        cs["manufacturability"] = self._score_manufacturability(candidate, w.manufacturability)

        # --- 4. Spindle Realism ---
        cs["spindle_realism"] = self._score_spindle(candidate, w.spindle_realism)

        # --- 5. Carriage Acceleration ---
        cs["carriage_accel"] = self._score_carriage_accel(w.carriage_accel)

        # --- 6. Layer Homogeneity ---
        cs["layer_homogeneity"] = self._score_layer_homogeneity(candidate, w.layer_homogeneity)

        # --- 7. Repeatability ---
        cs["repeatability"] = self._score_repeatability(candidate, w.repeatability)

        # --- 8. Winding Stability ---
        cs["winding_stability"] = self._score_stability(w.winding_stability)

        # --- 9. Process Efficiency ---
        cs["process_efficiency"] = self._score_efficiency(candidate, w.process_efficiency)

        # --- Composite ---
        composite = sum(c.weighted for c in cs.values())
        composite = max(0.0, min(1.0, composite))

        # --- Feasibility ---
        critical_fails = [
            c for c in cs.values()
            if c.is_critical and c.score < 0.30
        ]
        is_feasible = len(critical_fails) == 0
        rejection_reason = ""
        if not is_feasible:
            rejection_reason = "; ".join(
                f"{c.name}: {c.reason}" for c in critical_fails
            )

        is_recommended = is_feasible and composite >= 0.68

        return PatternScore(
            candidate        = candidate,
            criteria         = cs,
            composite_score  = composite,
            is_feasible      = is_feasible,
            is_recommended   = is_recommended,
            rejection_reason = rejection_reason,
        )

    def score_all(
        self,
        candidates: List[PatternCandidate],
    ) -> List[PatternScore]:
        """
        Tüm adayları puanla ve composite_score'a göre sırala.

        Returns:
            PatternScore listesi (en yüksek → en düşük composite_score)
        """
        scores = [self.score(c) for c in candidates]
        scores.sort(key=lambda s: s.composite_score, reverse=True)
        return scores

    def report(self, scores: List[PatternScore], max_show: int = 10) -> str:
        """Tüm puanlama sonuçları raporu."""
        lines = [
            "=" * 80,
            "PATTERN SCORE ENGINE RAPORU",
            "=" * 80,
            f"  {'#':>3}  {'n':>4}  {'p':>4}  {'k':>5}  "
            f"{'overlap':>9}  {'score':>7}  {'durum':>12}",
            "-" * 80,
        ]
        for i, ps in enumerate(scores[:max_show]):
            lines.append(f"  {i+1:>3}  {ps.summary_line()}")

        recommended = [s for s in scores if s.is_recommended]
        feasible    = [s for s in scores if s.is_feasible and not s.is_recommended]
        rejected    = [s for s in scores if not s.is_feasible]

        lines += [
            "-" * 80,
            f"  Önerilen: {len(recommended)} | Kabul edilebilir: {len(feasible)} | Reddedilen: {len(rejected)}",
            "=" * 80,
        ]
        return "\n".join(lines)

    # -----------------------------------------------------------------------
    # Criterion Scorers
    # -----------------------------------------------------------------------

    def _score_overlap(
        self,
        c: PatternCandidate,
        weight: float,
    ) -> CriterionScore:
        """
        Kriter 1: Overlap miktarı.

        Skor = 1 - (|overlap_mm| / (b/2))^0.7
        Tam üst sınır: b/2 (half-bandwidth)

        İdeal: overlap = 0 → skor = 1.0
        b/2 overlap → skor = 0.0
        """
        b      = self._b
        ratio  = c.overlap_ratio   # |overlap| / b

        # Smooth ceza: ratio=0→1.0, ratio=0.5→0.0
        raw_score = max(0.0, 1.0 - (ratio / 0.5) ** 0.7)
        score = min(1.0, raw_score)

        reason = (
            f"|overlap|={abs(c.overlap_mm):.3f}mm = {ratio*100:.1f}% bant. "
            f"{'İdeal' if ratio < 0.05 else 'İyi' if ratio < 0.20 else 'Kabul' if ratio < 0.40 else 'Sınırda'}."
        )
        return CriterionScore(
            name        = "Overlap Amount",
            score       = score,
            weight      = weight,
            weighted    = score * weight,
            reason      = reason,
            is_critical = True,
        )

    def _score_gap_risk(
        self,
        c: PatternCandidate,
        weight: float,
    ) -> CriterionScore:
        """
        Kriter 2: Gap riski.

        Leading pattern (ε > 0): overlap var, gap yok → en güvenli.
        Lagging pattern (ε < 0): son band biraz geri → teorik gap olabilir.
        Exact: hem güvenli hem mükemmel.

        Skor:
          Leading:  0.80 + 0.20·(1 - ratio)   [hafif bonus, en güvenli]
          Exact:    1.00                        [mükemmel]
          Lagging:  0.60 - 0.60·ratio          [artan gap riski]
        """
        ratio = c.overlap_ratio

        if c.pattern_type == "exact":
            score  = 1.00
            reason = "Exact pattern — ne gap ne overlap."
        elif c.pattern_type == "leading":
            score  = min(1.0, 0.80 + 0.20 * (1.0 - ratio * 2))
            reason = f"Leading: hafif overlap, gap riski yok ({ratio*100:.1f}% bant)."
        else:  # lagging
            score  = max(0.0, 0.60 - 0.60 * ratio / 0.5)
            reason = (
                f"Lagging: son band {abs(c.overlap_mm):.2f}mm geride. "
                f"{'Minör' if ratio < 0.15 else 'Orta' if ratio < 0.35 else 'YÜK­SEK'} gap riski."
            )
        return CriterionScore(
            name        = "Gap Risk",
            score       = score,
            weight      = weight,
            weighted    = score * weight,
            reason      = reason,
            is_critical = True,  # Büyük gap → yapısal hasar riski
        )

    def _score_manufacturability(
        self,
        c: PatternCandidate,
        weight: float,
    ) -> CriterionScore:
        """
        Kriter 3: Üretilebilirlik.

        k (toplam devre) sayısı üretim karmaşıklığını belirler.
          k ≤ 30:  Mükemmel (basit, hızlı)
          k ≤ 100: İyi
          k ≤ 300: Kabul edilebilir
          k > 300: Zor (uzun süre, fiber break riski artar)

        Ayrıca: gcd(p, k) = 1 doğrulaması (hard constraint).
        """
        from math import gcd as _gcd
        k = c.k

        if _gcd(c.p, c.k) != 1:
            return CriterionScore(
                name="Manufacturability", score=0.0, weight=weight, weighted=0.0,
                reason=f"gcd(p={c.p}, k={c.k}) = {_gcd(c.p,c.k)} ≠ 1: GEÇERSİZ PATTERN",
                is_critical=True,
            )

        if k <= 30:   score = 1.00
        elif k <= 60: score = 0.90 - 0.10 * (k - 30) / 30
        elif k <= 100: score = 0.80 - 0.15 * (k - 60) / 40
        elif k <= 200: score = 0.65 - 0.25 * (k - 100) / 100
        elif k <= 300: score = 0.40 - 0.20 * (k - 200) / 100
        else:          score = max(0.0, 0.20 - 0.20 * (k - 300) / 200)

        reason = (
            f"k={k} devre. "
            f"{'Kısa' if k <= 30 else 'Orta' if k <= 100 else 'Uzun' if k <= 300 else 'Çok uzun'} "
            f"sarım süresi."
        )
        return CriterionScore(
            name="Manufacturability", score=score, weight=weight,
            weighted=score * weight, reason=reason, is_critical=True,
        )

    def _score_spindle(
        self,
        c: PatternCandidate,
        weight: float,
    ) -> CriterionScore:
        """
        Kriter 4: Spindle (A-ekseni) gerçekçiliği.

        Silindir için spindle hızı sabittir (α sabit).
        rpm = S'·sin(α) / (2π·R)

        Bu RPM'nin makine limitiyle karşılaştırılması:
          rpm < 0.60·rpm_hard:  Mükemmel
          rpm < 0.80·rpm_hard:  İyi
          rpm < 1.00·rpm_hard:  Sınırda (uyarı)
          rpm > 1.00·rpm_hard:  Aşıyor — HARD CONSTRAINT ihlali
        """
        S   = self._S
        R   = self._R
        rpm = S * math.sin(self._alpha) / (2.0 * math.pi * R) * 60.0

        hard_limit  = self.SPINDLE_MAX_RPM_HARD
        ideal_limit = self.SPINDLE_MAX_RPM_IDEAL
        ratio_hard  = rpm / hard_limit

        if ratio_hard > 1.05:   # Hard limit ihlali
            score  = 0.0
            reason = f"rpm={rpm:.2f} > {hard_limit:.0f} (HARD LİMİT AŞILDI)."
            is_crit = True
        elif ratio_hard > 1.0:
            score  = 0.10
            reason = f"rpm={rpm:.2f} ≈ limit ({hard_limit:.0f}). Riskli."
            is_crit = True
        elif rpm <= ideal_limit:
            score  = 1.0 - 0.3 * (rpm / ideal_limit) ** 2
            reason = f"rpm={rpm:.2f} ≤ {ideal_limit:.0f} (ideal). Stabil."
            is_crit = False
        else:
            score  = max(0.0, 0.70 - 0.70 * (rpm - ideal_limit) / (hard_limit - ideal_limit))
            reason = f"rpm={rpm:.2f}: ideal ({ideal_limit:.0f}) ile hard ({hard_limit:.0f}) arası."
            is_crit = False

        return CriterionScore(
            name="Spindle Realism", score=score, weight=weight,
            weighted=score * weight, reason=reason, is_critical=is_crit,
        )

    def _score_carriage_accel(self, weight: float) -> CriterionScore:
        """
        Kriter 5: Carriage ivmelenmesi.

        Turn-around noktasında carriage durmalı ve ters yöne başlamalı.
        İvme = 2·V_x / t_acc

        V_x = S'·cos(α) [mm/s]
        t_acc: makine ivme süresi (0.05s varsayım — ADA268923 Appendix K)

        Koussios (2004) Tablo 12.1: X'' max = ±3500 mm/s² (3.5 m/s²)
        """
        V_x    = self._S * math.cos(self._alpha)   # [mm/s]
        t_acc  = 0.05                               # [s] — tipik CNC ivmelenme süresi
        accel  = 2.0 * V_x / t_acc                  # [mm/s²] — turnaround ivmesi

        ratio_hard  = accel / self.CARRIAGE_MAX_ACC
        ratio_ideal = accel / self.CARRIAGE_IDEAL_ACC

        if ratio_hard > 1.0:
            score  = max(0.0, 0.20 - 0.20 * (ratio_hard - 1.0))
            reason = (
                f"a={accel:.0f} mm/s² > {self.CARRIAGE_MAX_ACC:.0f} (limit). "
                f"Fiber hızı azaltılmalı."
            )
            is_crit = True
        elif ratio_ideal > 1.0:
            score  = max(0.40, 0.80 - 0.40 * (ratio_hard))
            reason = f"a={accel:.0f} mm/s². İdeal ({self.CARRIAGE_IDEAL_ACC:.0f}) üstü ama kabul."
            is_crit = False
        else:
            score  = 1.0 - 0.30 * ratio_ideal
            reason = f"a={accel:.0f} mm/s² ≤ {self.CARRIAGE_IDEAL_ACC:.0f} ideal. Smooth."
            is_crit = False

        return CriterionScore(
            name="Carriage Acceleration", score=score, weight=weight,
            weighted=score * weight, reason=reason, is_critical=is_crit,
        )

    def _score_layer_homogeneity(
        self,
        c: PatternCandidate,
        weight: float,
    ) -> CriterionScore:
        """
        Kriter 6: Layer homojenliği.

        Gerçek kaplama kalınlığı sabiti için her paralelde
        aynı sayıda fiber geçmeli.

        Silindir için homojenlik yaklaşımı:
          b_actual = 2πR·cos(α) / k   (gerçek band genişliği)
          deviation = |b_actual - b| / b

          deviation < 5%:  Mükemmel
          deviation < 15%: İyi
          deviation < 30%: Kabul
          > 30%:           Zayıf (aşırı overlap veya gap)
        """
        R       = self._R
        b       = self._b
        cos_a   = math.cos(self._alpha)
        b_actual = 2.0 * math.pi * R * cos_a / c.k
        deviation = abs(b_actual - b) / b

        if deviation < 0.05:   score = 1.00
        elif deviation < 0.10: score = 0.90 - 2.0 * (deviation - 0.05) / 0.05
        elif deviation < 0.20: score = 0.80 - 0.30 * (deviation - 0.10) / 0.10
        elif deviation < 0.35: score = 0.50 - 0.30 * (deviation - 0.20) / 0.15
        else:                  score = max(0.0, 0.20 - 0.20 * (deviation - 0.35) / 0.15)

        reason = (
            f"b_actual={b_actual:.3f}mm vs b={b:.2f}mm. "
            f"Sapma={deviation*100:.1f}%. "
            f"{'Homojen' if deviation < 0.10 else 'Kabul' if deviation < 0.25 else 'Heterojen'}."
        )
        return CriterionScore(
            name="Layer Homogeneity", score=score, weight=weight,
            weighted=score * weight, reason=reason, is_critical=False,
        )

    def _score_repeatability(
        self,
        c: PatternCandidate,
        weight: float,
    ) -> CriterionScore:
        """
        Kriter 7: Tekrarlanabilirlik.

        p değeri pattern adım düzgünlüğünü gösterir.
        p = 1: polar winding (en basit, en tekrarlanabilir)
        p büyükse: fiber yolu şablonu karmaşık

        Ayrıca: k / p oranı "pattern period"unu gösterir.
        Küçük period → simpler setup.
        """
        k  = c.k
        p  = c.p

        # k başına küçük değerler daha tekrarlanabilir
        period = k / p  # [devre/pattern step]

        if period <= 5:   score = 1.00
        elif period <= 15: score = 0.90 - 0.10 * (period - 5) / 10
        elif period <= 50: score = 0.80 - 0.30 * (period - 15) / 35
        elif period <= 150: score = 0.50 - 0.30 * (period - 50) / 100
        else:              score = max(0.10, 0.20 - 0.10 * (period - 150) / 100)

        reason = (
            f"p={p}, k={k}, period={period:.1f}. "
            f"{'Basit' if period < 15 else 'Orta' if period < 50 else 'Karmaşık'} setup."
        )
        return CriterionScore(
            name="Repeatability", score=score, weight=weight,
            weighted=score * weight, reason=reason, is_critical=False,
        )

    def _score_stability(self, weight: float) -> CriterionScore:
        """
        Kriter 8: Sarım stabilitesi.

        Geodezik sarım için fiber slip olmaz → daima 1.0.
        Non-geodezik Faz 3'te bu kriter λ/μ oranını değerlendirecek.
        """
        return CriterionScore(
            name     = "Winding Stability",
            score    = 1.0,
            weight   = weight,
            weighted = weight,
            reason   = "Geodezik sarım: slip condition daima sağlanıyor (k_g=0).",
            is_critical = False,
        )

    def _score_efficiency(
        self,
        c: PatternCandidate,
        weight: float,
    ) -> CriterionScore:
        """
        Kriter 9: Süreç verimliliği.

        Tahmini sarım süresi (dakika):
          t = k · (2·L) / (S'·cos(α)·60)
            = k · (2·L) / F [min]

        İdeal: t < 10 dakika
        Kabul: t < 30 dakika
        Uzun:  t > 60 dakika
        """
        F_mm_per_min = self._S * math.cos(self._alpha) * 60.0  # [mm/min]
        t_min = c.k * (2.0 * self._L) / F_mm_per_min           # [dakika]

        if t_min < 5:     score = 1.00
        elif t_min < 15:  score = 0.90 - 0.10 * (t_min - 5) / 10
        elif t_min < 30:  score = 0.80 - 0.25 * (t_min - 15) / 15
        elif t_min < 60:  score = 0.55 - 0.35 * (t_min - 30) / 30
        elif t_min < 120: score = 0.20 - 0.15 * (t_min - 60) / 60
        else:             score = max(0.05, 0.05)

        fiber_m = c.k * 2.0 * self._L / math.cos(self._alpha) / 1000.0  # [m]

        reason = (
            f"t≈{t_min:.1f} dak, fiber≈{fiber_m:.1f}m. "
            f"{'Hızlı' if t_min < 15 else 'Orta' if t_min < 30 else 'Yavaş'}."
        )
        return CriterionScore(
            name="Process Efficiency", score=score, weight=weight,
            weighted=score * weight, reason=reason, is_critical=False,
        )
