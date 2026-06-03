"""
core/cost_estimator.py — Malzeme ve Üretim Maliyet Tahmincisi
==============================================================
Bir sarma planı için malzeme maliyeti, işçilik maliyeti ve toplam
parça maliyetini tahmin eder.

Maliyet bileşenleri
-------------------
1. Fiber maliyeti     : fiber_mass × fiber_cost_per_kg
2. Reçine maliyeti    : resin_mass × resin_cost_per_kg
3. İşçilik maliyeti  : cycle_time × labor_rate
4. Genel gider        : (fiber + reçine + işçilik) × overhead_factor

Fiber kütlesi:
    m_fiber = L_fiber_total × tex / 1e9   [kg; L mm, tex g/km]

Reçine kütlesi (ıslak sarma):
    m_resin = m_fiber × (1−Vf)/Vf × ρ_r/ρ_f

Mandrel yüzey alanı (dönel yüzey):
    A = 2π × ∫ r(z) dz   (Pappus teoremi)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .geometry_engine import MandrelProfile
from .laminate_builder import LayerSchedule
from .material_database import MaterialSpec
from .production_estimator import CycleBreakdown, estimate_cycle_time


# ── Maliyet kırılımı ──────────────────────────────────────────────────────────

@dataclass
class CostBreakdown:
    """Parça başı maliyet kırılımı (USD)."""
    fiber_cost_usd: float
    resin_cost_usd: float
    labor_cost_usd: float
    overhead_cost_usd: float
    total_cost_usd: float

    fiber_mass_kg: float         # kullanılan fiber kütlesi
    resin_mass_kg: float         # kullanılan reçine kütlesi
    composite_mass_kg: float     # toplam parça kütlesi
    surface_area_mm2: float      # mandrel yüzey alanı

    def cost_per_kg_usd(self) -> float:
        """Parça bazında spesifik maliyet (USD/kg parça)."""
        if self.composite_mass_kg < 1e-9:
            return 0.0
        return self.total_cost_usd / self.composite_mass_kg

    def summary(self) -> str:
        return (
            f"Maliyet: {self.total_cost_usd:.2f}USD | "
            f"fiber={self.fiber_cost_usd:.2f} "
            f"reçine={self.resin_cost_usd:.2f} "
            f"işçilik={self.labor_cost_usd:.2f} "
            f"genel_gider={self.overhead_cost_usd:.2f} | "
            f"kütle={self.composite_mass_kg*1000:.0f}g | "
            f"spesifik={self.cost_per_kg_usd():.2f}USD/kg"
        )


# ── Ücret parametreleri ───────────────────────────────────────────────────────

@dataclass
class ProductionRates:
    """
    Üretim birim ücretleri.

    labor_rate_usd_per_hr : İşçilik ücreti (USD/saat).
    overhead_factor       : Genel gider faktörü (direkt maliyetin yüzdesi).
    scrap_factor          : Fire faktörü — fiber ve reçinede israf payı.
    """
    labor_rate_usd_per_hr: float = 60.0   # endüstriyel üretim
    overhead_factor: float = 0.30          # %30 genel gider
    scrap_factor: float = 0.08             # %8 fire


# ── Mandrel yüzey alanı ──────────────────────────────────────────────────────

def _mandrel_surface_area_mm2(profile: MandrelProfile) -> float:
    """
    Dönel yüzey alanı (Pappus teoremi).

    A = 2π × ∫ r(z) ds   (meridyen boyunca)
    ds = sqrt(dz² + dr²)
    """
    z = profile.z_mm
    r = profile.r_mm
    dz = np.diff(z)
    dr = np.diff(r)
    ds = np.sqrt(dz ** 2 + dr ** 2)
    r_mid = (r[:-1] + r[1:]) / 2.0
    return float(2.0 * math.pi * np.sum(r_mid * ds))


# ── Malzeme kütlesi tahmini ───────────────────────────────────────────────────

def _estimate_fiber_mass_kg(
    schedule: LayerSchedule,
    profile: MandrelProfile,
    material: MaterialSpec,
    total_fiber_length_mm: float,
    scrap_factor: float = 0.08,
) -> float:
    """
    Kullanılan toplam fiber kütlesi (artık dahil).

    m_fiber = L_total × tex / 1e9 × (1 + fire_faktörü)
    """
    return material.fiber_mass_kg(total_fiber_length_mm) * (1.0 + scrap_factor)


def _estimate_composite_mass_kg(
    profile: MandrelProfile,
    schedule: LayerSchedule,
    material: MaterialSpec,
) -> float:
    """
    Parça kompozit kütlesi (Pappus + laminat kalınlığı).

    m_composite = A_surface × t_avg × ρ_composite
    (A_surface mm², t_avg mm, ρ g/cm³ → kg)
    """
    from .thickness_predictor import predict_cylinder_thickness
    t_avg_mm = predict_cylinder_thickness(schedule, material,
                                           radius_mm=float(np.max(profile.r_mm)))
    A_mm2 = _mandrel_surface_area_mm2(profile)
    vol_mm3 = A_mm2 * t_avg_mm
    vol_cm3 = vol_mm3 / 1e3
    return vol_cm3 * material.composite_density_g_cm3 / 1e3  # g→kg (/1000 burada yanlış)
    # Düzeltme: g/cm³ × cm³ = g → /1000 → kg
    # vol_cm3 × ρ_g_cm3 = g; /1000 = kg → doğru


# ── Ana maliyet hesabı ─────────────────────────────────────────────────────────

def estimate_cost(
    schedule: LayerSchedule,
    profile: MandrelProfile,
    material: MaterialSpec,
    cycle: CycleBreakdown,
    rates: Optional[ProductionRates] = None,
) -> CostBreakdown:
    """
    Sarma planı için tam maliyet tahmini.

    Parametreler
    ------------
    schedule  : Sarma planı.
    profile   : Mandrel geometrisi.
    material  : Malzeme özellikleri.
    cycle     : Döngü süresi tahmini (estimate_cycle_time çıktısı).
    rates     : Üretim ücretleri (None → varsayılan).
    """
    if rates is None:
        rates = ProductionRates()

    scrap = rates.scrap_factor
    total_fiber_mm = cycle.total_fiber_length_mm

    # ── Fiber maliyeti ────────────────────────────────────────────────────
    fiber_mass_kg = _estimate_fiber_mass_kg(schedule, profile, material,
                                             total_fiber_mm, scrap)
    fiber_cost = fiber_mass_kg * material.fiber.cost_usd_per_kg

    # ── Reçine maliyeti ───────────────────────────────────────────────────
    resin_mass_kg = material.resin_mass_kg(fiber_mass_kg)
    resin_cost = resin_mass_kg * material.resin.cost_usd_per_kg

    # ── İşçilik maliyeti ──────────────────────────────────────────────────
    labor_cost = (cycle.total_time_s / 3600.0) * rates.labor_rate_usd_per_hr

    # ── Genel gider ───────────────────────────────────────────────────────
    direct_cost = fiber_cost + resin_cost + labor_cost
    overhead_cost = direct_cost * rates.overhead_factor

    total_cost = direct_cost + overhead_cost

    # ── Parça kütlesi ─────────────────────────────────────────────────────
    composite_mass = fiber_mass_kg + resin_mass_kg
    A_mm2 = _mandrel_surface_area_mm2(profile)

    return CostBreakdown(
        fiber_cost_usd=fiber_cost,
        resin_cost_usd=resin_cost,
        labor_cost_usd=labor_cost,
        overhead_cost_usd=overhead_cost,
        total_cost_usd=total_cost,
        fiber_mass_kg=fiber_mass_kg,
        resin_mass_kg=resin_mass_kg,
        composite_mass_kg=composite_mass,
        surface_area_mm2=A_mm2,
    )


def estimate_cost_quick(
    schedule: LayerSchedule,
    profile: MandrelProfile,
    material: MaterialSpec,
    nominal_feed_mm_s: float = 80.0,
    spindle_rpm_max: float = 120.0,
    rates: Optional[ProductionRates] = None,
) -> CostBreakdown:
    """
    Tek çağrıyla hızlı maliyet tahmini (döngü süresi otomatik hesaplı).
    """
    cycle = estimate_cycle_time(schedule, profile, material,
                                nominal_feed_mm_s=nominal_feed_mm_s,
                                spindle_rpm_max=spindle_rpm_max)
    return estimate_cost(schedule, profile, material, cycle, rates)
