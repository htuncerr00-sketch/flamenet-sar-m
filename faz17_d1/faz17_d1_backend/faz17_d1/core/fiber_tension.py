"""
core/fiber_tension.py — Fiber Gerilim Modeli
==============================================
Tow gerilimini, sürtünme tutunmasını, kayma eşiğini, gerilim kaynaklı
sıkıştırmayı ve minimum kararlı gerilimi modeller.

Temel fizik
-----------
Temas basıncı         : p = T / (R · w)            [N/mm² = MPa]
Normal çizgi yükü     : N' = T / R                 [N/mm]   (eğri yüzeyde)
Sürtünme tutunması    : F_fric' = μ · T / R        [N/mm]
Kayma oranı           : λ = yanal_yük / sürtünme   (λ > 1 → kayma)
Fiber uzaması         : ε = T / (A · E)            [birimsiz]
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class FiberTensionModel:
    """
    Fiber gerilim ve tutunma modeli.

    tension_N            : Tow gerilimi (Newton).
    friction_coeff       : Fiber-mandrel statik sürtünme katsayısı μ.
    fiber_modulus_GPa    : Fiber elastik modülü (karbon ≈ 230 GPa).
    tow_cross_section_mm2 : Yük taşıyan tow kesit alanı (mm²).
    consolidation_pressure_MPa : Tam sıkıştırma için referans basınç.
    min_contact_pressure_MPa : Fiberin yerinde kalması için min temas basıncı.
    """
    tension_N: float = 50.0
    friction_coeff: float = 0.3
    fiber_modulus_GPa: float = 230.0
    tow_cross_section_mm2: float = 0.45
    consolidation_pressure_MPa: float = 0.5
    min_contact_pressure_MPa: float = 0.03

    def __post_init__(self) -> None:
        if self.tension_N < 0:
            raise ValueError(f"tension_N >= 0 olmalı: {self.tension_N}")
        if not (0.01 <= self.friction_coeff <= 1.5):
            raise ValueError(f"friction_coeff ∈ [0.01,1.5]: {self.friction_coeff}")
        if self.tow_cross_section_mm2 <= 0:
            raise ValueError(f"tow_cross_section_mm2 > 0 olmalı: {self.tow_cross_section_mm2}")

    # ── Basınç ve yük ────────────────────────────────────────────────────────

    def contact_pressure_MPa(self, radius_mm: float, band_width_mm: float) -> float:
        """Bant-mandrel temas basıncı p = T/(R·w) [MPa]."""
        if radius_mm <= 0 or band_width_mm <= 0:
            return 0.0
        return self.tension_N / (radius_mm * band_width_mm)

    def normal_line_load_N_mm(self, radius_mm: float) -> float:
        """Eğri yüzeyde normal çizgi yükü N' = T/R [N/mm]."""
        if radius_mm <= 0:
            return 0.0
        return self.tension_N / radius_mm

    def friction_hold_N_mm(self, radius_mm: float) -> float:
        """Mevcut yanal sürtünme tutunması F' = μ·T/R [N/mm]."""
        return self.friction_coeff * self.normal_line_load_N_mm(radius_mm)

    # ── Kayma eşiği ──────────────────────────────────────────────────────────

    def slip_ratio(self, geodesic_curvature_1_mm: float,
                   radius_mm: float) -> float:
        """
        Kayma oranı λ = T·|k_g| / (μ·T/R) = |k_g|·R / μ.

        k_g : geodezik eğrilik (1/mm). Geodezik yol için k_g = 0 → λ = 0.
        λ > 1 ise fiber yanal olarak kayar.

        Not: Gerilim T pay ve paydadan sadeleşir; kayma gerilimden bağımsızdır
        (klasik sonuç) — bu yüzden geodezik yol kritiktir, gerilim değil.
        """
        if radius_mm <= 0:
            return float('inf')
        normal_curv = math.sin(math.radians(45.0)) ** 2 / radius_mm  # ölçek referansı
        # Doğrudan tanım: λ = |k_g| / (μ · k_n).  Burada k_n ölçeğini kullanırız.
        # Sadeleştirilmiş kararlı form: λ = |k_g| · R / μ
        return abs(geodesic_curvature_1_mm) * radius_mm / self.friction_coeff

    def slip_ratio_from_angle(self, alpha_deg: float) -> float:
        """
        Steady-state kayma oranı tahmini lokal sarma açısından.

        Eğri olmayan (sabit açılı) yol için yaklaşık üst sınır:
        λ ≈ tan(α) / μ.   Yüksek açı → yüksek yanal eğilim.
        """
        alpha_rad = math.radians(max(0.0, min(alpha_deg, 89.9)))
        return math.tan(alpha_rad) / self.friction_coeff

    def is_slip_safe(self, alpha_deg: float) -> bool:
        """Verilen açıda kayma güvenli mi (λ ≤ 1)?"""
        return self.slip_ratio_from_angle(alpha_deg) <= 1.0

    # ── Gerilim kaynaklı sıkıştırma ──────────────────────────────────────────

    def compaction_factor(self, radius_mm: float, band_width_mm: float) -> float:
        """
        Gerilim kaynaklı sıkıştırma faktörü (kalınlık oranı, 0..1).

        Konsolidasyon modeli:
            factor = 1 / (1 + p / p_consol)

        Yüksek temas basıncı → daha fazla sıkıştırma → daha düşük faktör.
        Alt sınır 0.5 (fiziksel olarak fiberler tamamen ezilmez).
        """
        p = self.contact_pressure_MPa(radius_mm, band_width_mm)
        factor = 1.0 / (1.0 + p / max(self.consolidation_pressure_MPa, 1e-6))
        return max(0.5, min(1.0, factor))

    # ── Minimum kararlı gerilim ──────────────────────────────────────────────

    def min_stable_tension_N(self, radius_mm: float, band_width_mm: float) -> float:
        """
        Fiberin yüzeyde kalması için gereken minimum gerilim.

        T_min = p_min · R · w

        Bu gerilimin altında temas basıncı yetersiz; fiber gevşer / kayar.
        """
        return self.min_contact_pressure_MPa * radius_mm * band_width_mm

    def is_tension_stable(self, radius_mm: float, band_width_mm: float) -> bool:
        """Mevcut gerilim minimum kararlı gerilimin üzerinde mi?"""
        return self.tension_N >= self.min_stable_tension_N(radius_mm, band_width_mm)

    # ── Fiber uzaması ────────────────────────────────────────────────────────

    @property
    def fiber_strain(self) -> float:
        """Fiber uzaması ε = T / (A·E) [birimsiz]."""
        E_MPa = self.fiber_modulus_GPa * 1000.0  # GPa → MPa = N/mm²
        return self.tension_N / (self.tow_cross_section_mm2 * E_MPa)

    @property
    def fiber_strain_pct(self) -> float:
        return self.fiber_strain * 100.0

    @property
    def fiber_stress_MPa(self) -> float:
        """Fiber gerilmesi σ = T / A [MPa]."""
        return self.tension_N / self.tow_cross_section_mm2

    def summary(self) -> str:
        return (
            f"FiberTension: T={self.tension_N:.1f}N μ={self.friction_coeff:.2f} "
            f"σ={self.fiber_stress_MPa:.0f}MPa ε={self.fiber_strain_pct:.3f}% "
            f"güvenli_açı≤{self._max_safe_angle_deg():.1f}°"
        )

    def _max_safe_angle_deg(self) -> float:
        """λ = tan(α)/μ = 1 → α_max = atan(μ)."""
        return math.degrees(math.atan(self.friction_coeff))
