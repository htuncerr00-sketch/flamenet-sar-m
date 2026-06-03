"""
core/material_database.py — Fiber, Resin ve Kompozit Malzeme Kataloğu
=======================================================================
Filament sarma uygulamaları için yaygın fiber, reçine ve kompozit
malzeme özelliklerini tanımlar. Birim sistemi: mm, kg, GPa, MPa, USD.

Lineer yoğunluk birimi: tex (g/km)
    Kütle hesabı: m_kg = L_mm × tex_g_km / 1e9

Hacim fraksiyonu Vf:
    ρ_c = Vf·ρ_f + (1-Vf)·ρ_r  (kural karışımı)
    m_r = m_f × (1-Vf)/Vf × ρ_r/ρ_f
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ── Fiber özellikleri ─────────────────────────────────────────────────────────

@dataclass
class FiberSpec:
    """
    Tek bir fiber türünün fiziksel ve maliyet özellikleri.

    tex_g_km : Lineer yoğunluk (g/km) — 12K karbon tow ≈ 800 g/km.
    E_GPa    : Fiber yönünde Young modülü.
    """
    name: str
    density_g_cm3: float        # fiber yoğunluğu
    E_GPa: float                 # elastik modül (fiber yönü)
    tensile_MPa: float           # çekme dayanımı
    cost_usd_per_kg: float       # piyasa fiyatı (USD/kg)
    tex_g_km: float              # tow lineer yoğunluğu (g/km)
    filament_diameter_um: float  # tek filament çapı (µm)

    def mass_per_mm_kg(self) -> float:
        """1 mm fiber uzunluğunun kütlesi (kg)."""
        return self.tex_g_km / 1e9  # g/km → kg/mm


# ── Reçine özellikleri ────────────────────────────────────────────────────────

@dataclass
class ResinSpec:
    """Reçine sistemi fiziksel ve maliyet özellikleri."""
    name: str
    density_g_cm3: float
    cost_usd_per_kg: float


# ── Tow geometrisi ────────────────────────────────────────────────────────────

@dataclass
class TowSpec:
    """
    Fitil (tow/tape) fiziksel geometrisi.

    Sarım sırasındaki ölçüler — kuru fitil, gerilim altında.
    """
    n_filaments: int             # toplam filament sayısı (3K/6K/12K/24K/48K)
    tex_g_km: float              # lineer yoğunluk (g/km)
    tow_width_mm: float          # sarım sırasında bant genişliği (mm)
    tow_thickness_mm: float      # sarım sırasında bant kalınlığı (mm)

    def cross_section_mm2(self) -> float:
        """Kuru fitil kesit alanı (mm²)."""
        return self.tow_width_mm * self.tow_thickness_mm


# ── Kompozit malzeme ──────────────────────────────────────────────────────────

@dataclass
class MaterialSpec:
    """
    Tam fiber + reçine + hacim fraksiyonu malzeme tanımı.

    Tüm kütlesel ve geometrik hesaplarda bu sınıf kullanılır.
    """
    name: str
    fiber: FiberSpec
    resin: ResinSpec
    tow: TowSpec
    fiber_volume_fraction: float = 0.55   # Vf (tipik: 0.50-0.65)

    def __post_init__(self) -> None:
        if not (0.1 <= self.fiber_volume_fraction <= 0.9):
            raise ValueError(f"fiber_volume_fraction ∈ [0.1, 0.9]: {self.fiber_volume_fraction}")

    # ── Kompozit özellikleri ────────────────────────────────────────────────

    @property
    def composite_density_g_cm3(self) -> float:
        """Kural karışımı: ρ_c = Vf·ρ_f + (1−Vf)·ρ_r"""
        Vf = self.fiber_volume_fraction
        return Vf * self.fiber.density_g_cm3 + (1.0 - Vf) * self.resin.density_g_cm3

    @property
    def matrix_volume_fraction(self) -> float:
        return 1.0 - self.fiber_volume_fraction

    @property
    def E_composite_GPa(self) -> float:
        """Fiber yönünde kural karışımı elastik modül."""
        Vf = self.fiber_volume_fraction
        Er = self.resin.density_g_cm3 * 3.5 / 1.2  # rough estimate ~3.5 GPa for epoxy
        return Vf * self.fiber.E_GPa + (1.0 - Vf) * Er

    # ── Kütle hesapları ─────────────────────────────────────────────────────

    def fiber_mass_kg(self, fiber_length_mm: float) -> float:
        """
        Fiber uzunluğundan kuru fiber kütlesi.

        m_kg = L_mm × tex_g_km / 1e9
        (birim analizi: mm × g/km = mm × g/(1e6mm) = g/1e6 → kg/1e9)
        """
        return fiber_length_mm * self.tow.tex_g_km / 1e9

    def resin_mass_kg(self, fiber_mass_kg: float) -> float:
        """
        Fiber kütlesinden ıslak sarma reçine kütlesi.

        m_r = m_f × (1−Vf)/Vf × ρ_r/ρ_f
        """
        Vf = self.fiber_volume_fraction
        ratio = (1.0 - Vf) / Vf * (self.resin.density_g_cm3 / self.fiber.density_g_cm3)
        return fiber_mass_kg * ratio

    def total_composite_mass_kg(self, fiber_length_mm: float) -> float:
        """Fiber + reçine toplam kütlesi."""
        m_f = self.fiber_mass_kg(fiber_length_mm)
        return m_f + self.resin_mass_kg(m_f)

    def summary(self) -> str:
        return (
            f"Malzeme '{self.name}': "
            f"ρ_c={self.composite_density_g_cm3:.2f}g/cm³ | "
            f"E_fiber={self.fiber.E_GPa:.0f}GPa | "
            f"Vf={self.fiber_volume_fraction:.2f} | "
            f"tex={self.tow.tex_g_km:.0f}g/km"
        )


# ── Fiber kataloğu ────────────────────────────────────────────────────────────

def _f_t700s() -> FiberSpec:
    """Toray T700S/12K standart modül karbon fiber."""
    return FiberSpec(
        name="Toray T700S-12K",
        density_g_cm3=1.80,
        E_GPa=230.0,
        tensile_MPa=4900.0,
        cost_usd_per_kg=30.0,
        tex_g_km=800.0,
        filament_diameter_um=7.0,
    )

def _f_t300() -> FiberSpec:
    """Toray T300/3K standart modül (aerospace)."""
    return FiberSpec(
        name="Toray T300-3K",
        density_g_cm3=1.76,
        E_GPa=230.0,
        tensile_MPa=3530.0,
        cost_usd_per_kg=25.0,
        tex_g_km=200.0,
        filament_diameter_um=7.0,
    )

def _f_im7() -> FiberSpec:
    """Hexcel IM7/12K ara modül karbon fiber."""
    return FiberSpec(
        name="Hexcel IM7-12K",
        density_g_cm3=1.77,
        E_GPa=276.0,
        tensile_MPa=5580.0,
        cost_usd_per_kg=80.0,
        tex_g_km=795.0,
        filament_diameter_um=5.2,
    )

def _f_eglass() -> FiberSpec:
    """E-cam fiber (12K)."""
    return FiberSpec(
        name="E-Glass-12K",
        density_g_cm3=2.54,
        E_GPa=72.0,
        tensile_MPa=2500.0,
        cost_usd_per_kg=3.0,
        tex_g_km=1200.0,
        filament_diameter_um=10.0,
    )

def _f_aramid_k49() -> FiberSpec:
    """DuPont Kevlar 49 aramid fiber (3K)."""
    return FiberSpec(
        name="Kevlar-49-3K",
        density_g_cm3=1.44,
        E_GPa=112.0,
        tensile_MPa=3600.0,
        cost_usd_per_kg=22.0,
        tex_g_km=266.0,
        filament_diameter_um=12.0,
    )


# ── Reçine kataloğu ───────────────────────────────────────────────────────────

def _r_standard_epoxy() -> ResinSpec:
    """Standart bisfenol-A epoksi (oda sıcaklığı + ısıl kürleme)."""
    return ResinSpec(name="Standart Epoksi", density_g_cm3=1.25, cost_usd_per_kg=8.0)

def _r_low_viscosity_epoxy() -> ResinSpec:
    """Düşük viskoziteli ıslak sarma epoksi (Araldite tipi)."""
    return ResinSpec(name="Düşük Viskozite Epoksi", density_g_cm3=1.14, cost_usd_per_kg=12.0)

def _r_vinyl_ester() -> ResinSpec:
    """Vinil ester reçine (korozyon dirençli)."""
    return ResinSpec(name="Vinil Ester", density_g_cm3=1.12, cost_usd_per_kg=5.0)


# ── Tow geometri kataloğu ─────────────────────────────────────────────────────

def _tow_carbon_12k_standard() -> TowSpec:
    """12K karbon tow — endüstriyel standart boyutlar."""
    return TowSpec(n_filaments=12_000, tex_g_km=800.0,
                   tow_width_mm=6.0, tow_thickness_mm=0.25)

def _tow_carbon_12k_narrow() -> TowSpec:
    """12K karbon tow — dar format (yüksek hassasiyet)."""
    return TowSpec(n_filaments=12_000, tex_g_km=800.0,
                   tow_width_mm=4.0, tow_thickness_mm=0.20)

def _tow_glass_12k() -> TowSpec:
    """12K cam fiber tow."""
    return TowSpec(n_filaments=12_000, tex_g_km=1200.0,
                   tow_width_mm=8.0, tow_thickness_mm=0.30)

def _tow_aramid_3k() -> TowSpec:
    """3K aramid tow."""
    return TowSpec(n_filaments=3_000, tex_g_km=266.0,
                   tow_width_mm=3.0, tow_thickness_mm=0.15)


# ── Hazır malzeme profilleri ──────────────────────────────────────────────────

def carbon_t700_standard_epoxy() -> MaterialSpec:
    """T700S/12K + standart epoksi — endüstriyel standart."""
    return MaterialSpec(
        name="T700S/Epoksi",
        fiber=_f_t700s(), resin=_r_standard_epoxy(),
        tow=_tow_carbon_12k_standard(),
        fiber_volume_fraction=0.55,
    )

def carbon_t700_narrow_epoxy() -> MaterialSpec:
    """T700S/12K dar tow + standart epoksi — yüksek hassasiyet."""
    return MaterialSpec(
        name="T700S/Epoksi-Dar",
        fiber=_f_t700s(), resin=_r_standard_epoxy(),
        tow=_tow_carbon_12k_narrow(),
        fiber_volume_fraction=0.55,
    )

def carbon_im7_epoxy() -> MaterialSpec:
    """IM7/12K + standart epoksi — yüksek performans."""
    return MaterialSpec(
        name="IM7/Epoksi",
        fiber=_f_im7(), resin=_r_standard_epoxy(),
        tow=_tow_carbon_12k_standard(),
        fiber_volume_fraction=0.58,
    )

def eglass_epoxy() -> MaterialSpec:
    """E-cam/12K + standart epoksi — ekonomik basınçlı kap."""
    return MaterialSpec(
        name="E-Cam/Epoksi",
        fiber=_f_eglass(), resin=_r_standard_epoxy(),
        tow=_tow_glass_12k(),
        fiber_volume_fraction=0.50,
    )

def eglass_vinyl_ester() -> MaterialSpec:
    """E-cam/12K + vinil ester — kimyasal ortam."""
    return MaterialSpec(
        name="E-Cam/Vinil Ester",
        fiber=_f_eglass(), resin=_r_vinyl_ester(),
        tow=_tow_glass_12k(),
        fiber_volume_fraction=0.50,
    )

def aramid_epoxy() -> MaterialSpec:
    """Kevlar-49/3K + epoksi — darbe direnci."""
    return MaterialSpec(
        name="Kevlar-49/Epoksi",
        fiber=_f_aramid_k49(), resin=_r_standard_epoxy(),
        tow=_tow_aramid_3k(),
        fiber_volume_fraction=0.50,
    )


_MATERIAL_CATALOG: Dict[str, "callable"] = {
    "carbon_t700_standard_epoxy": carbon_t700_standard_epoxy,
    "carbon_t700_narrow_epoxy": carbon_t700_narrow_epoxy,
    "carbon_im7_epoxy": carbon_im7_epoxy,
    "eglass_epoxy": eglass_epoxy,
    "eglass_vinyl_ester": eglass_vinyl_ester,
    "aramid_epoxy": aramid_epoxy,
}


def available_materials() -> List[str]:
    """Mevcut malzeme profili adları."""
    return list(_MATERIAL_CATALOG.keys())


def get_material(name: str) -> MaterialSpec:
    """Ada göre hazır malzeme profili döndür."""
    if name not in _MATERIAL_CATALOG:
        raise KeyError(f"Bilinmeyen malzeme: '{name}'. Mevcut: {list(_MATERIAL_CATALOG.keys())}")
    return _MATERIAL_CATALOG[name]()
