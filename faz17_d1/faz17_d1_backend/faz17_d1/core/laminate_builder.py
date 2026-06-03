"""
core/laminate_builder.py — Sarma Takviye Tabakaları Oluşturucu
===============================================================
Açı ailelerinden (angle families) katmanlı kompozit bir yapı inşa eder.
Her açı ailesi; sarma stratejisi, kat sayısı ve simetri bilgisi içerir.

Takviye türleri
---------------
    Helisel (helical) : α ∈ [20°, 75°] — eksenel + çevresel taşıma
    Kutupsal (polar)  : α ∈ [0°, 20°]  — eksenel taşıma, kubbe geçişli
    Çevre (hoop)     : α ∈ [75°, 90°] — çevresel taşıma, minimum eksenel

Simetri
-------
Helisel ve kutupsal katlar daima ±α çifti olarak sarılır.
Bir "kat seti" (layer_set) = +α gidiş + -α dönüş = 2 kat.
Çevre sarma tek yönlü; bir "kat seti" = 1 kat.

Kalınlık modeli
---------------
Her katın kalınlığı: t_ply = tow_thickness × compaction_factor(idx, tension, radius)
compaction_factor, fiber yerleşim modeline göre azalan bir faktördür (nesting).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from .fiber_deposition import compaction_factor as _compaction_fn
from .material_database import MaterialSpec


# ── Açı ailesi ────────────────────────────────────────────────────────────────

@dataclass
class AngleFamily:
    """
    Tek bir sarma açı ailesinin parametreleri.

    alpha_deg     : Nominal sarma açısı (mandrel ekseninden, derece).
    n_layer_sets  : Bu açı için kat seti sayısı (bkz. simetri notu).
    strategy      : "helical" | "polar" | "hoop" — sarma stratejisi.
    symmetric     : True → ±α çifti (helisel/kutupsal için); False → tek yön.
    overlap_pct   : Komşu bantlar arası bindirme yüzdesi.
    """
    alpha_deg: float
    n_layer_sets: int = 1
    strategy: str = "helical"     # "helical" | "polar" | "hoop"
    symmetric: bool = True         # ±α çifti mi?
    overlap_pct: float = 5.0

    def __post_init__(self) -> None:
        if not (0.0 <= self.alpha_deg <= 90.0):
            raise ValueError(f"alpha_deg ∈ [0, 90]: {self.alpha_deg}")
        if self.n_layer_sets < 0:
            raise ValueError(f"n_layer_sets >= 0: {self.n_layer_sets}")
        if self.strategy not in ("helical", "polar", "hoop"):
            raise ValueError(f"strategy ∈ {{helical, polar, hoop}}: {self.strategy}")

    @property
    def n_plies(self) -> int:
        """Gerçek kat sayısı (simetri hesaba katılır)."""
        multiplier = 2 if self.symmetric else 1
        return self.n_layer_sets * multiplier

    @property
    def is_dome_traversal(self) -> bool:
        """Bu strateji kubbe geçişi gerektiriyor mu?"""
        return self.strategy in ("helical", "polar")

    def ply_angles(self) -> List[float]:
        """Tüm katların açı listesi (simetri dahil)."""
        if self.symmetric:
            return [a for _ in range(self.n_layer_sets) for a in (self.alpha_deg, -self.alpha_deg)]
        return [self.alpha_deg] * self.n_layer_sets


# ── Katman planı ──────────────────────────────────────────────────────────────

@dataclass
class LayerSchedule:
    """
    Tüm açı ailelerinden oluşan sarma planı.

    families: Sıralı açı aileleri listesi (sıralama sarım dizisini verir).
    """
    families: List[AngleFamily]
    name: str = ""

    @property
    def total_layer_sets(self) -> int:
        return sum(f.n_layer_sets for f in self.families)

    @property
    def total_plies(self) -> int:
        return sum(f.n_plies for f in self.families)

    @property
    def angle_summary(self) -> str:
        parts = []
        for f in self.families:
            sym = "±" if f.symmetric else ""
            parts.append(f"{sym}{f.alpha_deg:.0f}°×{f.n_layer_sets}")
        return " / ".join(parts)

    def is_empty(self) -> bool:
        return self.total_layer_sets == 0


# ── Tek bir kat ───────────────────────────────────────────────────────────────

@dataclass
class LaminatePly:
    """Sarma dizisindeki tek bir katın tanımı."""
    angle_deg: float         # gerçek sarma açısı (+α veya -α)
    thickness_mm: float      # sıkıştırılmış kalınlık
    global_ply_idx: int      # toplam dizi içindeki sıra (0-tabanlı)
    family_idx: int          # hangi açı ailesinden geldiği
    strategy: str            # "helical" | "polar" | "hoop"


# ── Katman yığını ─────────────────────────────────────────────────────────────

@dataclass
class LaminateStack:
    """
    Tüm katlardan oluşan laminat yığını ve özet özellikleri.

    total_thickness_mm    : Tüm katların sıkıştırılmış toplam kalınlığı (mm).
    areal_fiber_mass_g_m2 : Birim alana düşen fiber kütlesi (g/m²).
    """
    plies: List[LaminatePly]
    total_thickness_mm: float
    areal_fiber_mass_g_m2: float   # sadece fiber, reçine dahil değil
    total_composite_mass_g_m2: float  # fiber + reçine

    @property
    def n_plies(self) -> int:
        return len(self.plies)

    def angle_sequence(self) -> List[float]:
        return [p.angle_deg for p in self.plies]

    def thickness_by_family(self, n_families: int) -> List[float]:
        """Her aile için toplam kalınlık."""
        totals = [0.0] * n_families
        for p in self.plies:
            if 0 <= p.family_idx < n_families:
                totals[p.family_idx] += p.thickness_mm
        return totals

    def summary(self) -> str:
        return (
            f"Laminat: {self.n_plies} kat | "
            f"toplam_t={self.total_thickness_mm:.3f}mm | "
            f"fiber={self.areal_fiber_mass_g_m2:.1f}g/m² | "
            f"kompozit={self.total_composite_mass_g_m2:.1f}g/m²"
        )


# ── Ana inşa fonksiyonu ───────────────────────────────────────────────────────

def build_laminate(
    schedule: LayerSchedule,
    material: MaterialSpec,
    tension_N: float = 50.0,
    radius_mm: float = 50.0,
) -> LaminateStack:
    """
    LayerSchedule + malzeme özelliklerinden LaminateStack oluştur.

    Her katın kalınlığı:
        t_ply[i] = tow_thickness × compaction_factor(i, tension, radius)

    Fiber alan kütlesi (birim yüzey başına):
        Her katın yüzey başına fiber kütlesi = fiber_linear_mass × fiber_len_per_area
        Burada: fiber_len_per_area = 1/cos(α) × (1/effective_step) [mm/mm²]
        Ve effective_step = W × (1 - overlap/100)

    Parametreler
    ------------
    schedule   : Sarma planı (açı aileleri).
    material   : Malzeme özellikleri (fiber, reçine, Vf, tow boyutları).
    tension_N  : Fiber gerilimi (N) — sıkıştırma faktörü için.
    radius_mm  : Mandrel yarıçapı (mm) — sıkıştırma faktörü için.
    """
    tow = material.tow
    plies: List[LaminatePly] = []
    global_idx = 0
    total_t = 0.0
    total_fiber_areal = 0.0   # g/m²

    for fam_idx, family in enumerate(schedule.families):
        for _ in range(family.n_layer_sets):
            angles = [family.alpha_deg, -family.alpha_deg] if family.symmetric else [family.alpha_deg]
            for angle in angles:
                cf = _compaction_fn(global_idx, tension_N, radius_mm)
                t_ply = tow.tow_thickness_mm * cf
                plies.append(LaminatePly(
                    angle_deg=angle,
                    thickness_mm=t_ply,
                    global_ply_idx=global_idx,
                    family_idx=fam_idx,
                    strategy=family.strategy,
                ))
                total_t += t_ply

                # Fiber alan kütlesi bu kat için (g/m²):
                # effective_step = W × (1-overlap/100)  [mm]
                # fiber_len_per_mm2_surface = 1/effective_step × 1/cos(α) × 1000 [m/m²?]
                # Hayır, basit: fiber_areal = tex × (1/cos α) / effective_step [g/m²]
                alpha_rad = math.radians(max(abs(angle), 0.5))
                step_mm = tow.tow_width_mm * max(1.0 - family.overlap_pct / 100.0, 0.01)
                # tex [g/km] = tex/1e3 [g/m] → fiber_len_per_m2_surface = (1/step_mm) × 1/cos(α) [m/m²]
                # fiber_areal [g/m²] = tex/1e3 [g/m] × fiber_len_per_m2 [m/m²] × 1e6 [mm²/m²]
                # = tex/1e3 × (1/step_mm × 1/cos(α)) × 1e6
                # = tex × 1e3 / (step_mm × cos(α))
                fiber_areal_g_m2 = tow.tex_g_km * 1e3 / (step_mm * math.cos(alpha_rad))
                total_fiber_areal += fiber_areal_g_m2

                global_idx += 1

    # Reçine kütlesi proportional:
    # total_composite = total_fiber / Vf  (hacim fraksiyonundan)
    # m_r/m_f = (1-Vf)/Vf × ρ_r/ρ_f  → composite/fiber ≈ 1 + (1-Vf)/Vf × ρ_r/ρ_f
    Vf = material.fiber_volume_fraction
    composite_ratio = 1.0 + (1.0 - Vf) / Vf * (material.resin.density_g_cm3 / material.fiber.density_g_cm3)
    total_composite_areal = total_fiber_areal * composite_ratio

    return LaminateStack(
        plies=plies,
        total_thickness_mm=total_t,
        areal_fiber_mass_g_m2=total_fiber_areal,
        total_composite_mass_g_m2=total_composite_areal,
    )


# ── Katman planı oluşturma yardımcıları ───────────────────────────────────────

def make_helical_schedule(alpha_deg: float, n_layer_sets: int,
                          overlap_pct: float = 5.0) -> LayerSchedule:
    """Tek açılı simetrik helisel sarma planı."""
    return LayerSchedule(
        families=[AngleFamily(alpha_deg, n_layer_sets, "helical", True, overlap_pct)],
        name=f"Helisel±{alpha_deg:.0f}°×{n_layer_sets}",
    )

def make_hoop_schedule(n_layer_sets: int, overlap_pct: float = 5.0) -> LayerSchedule:
    """Çevre sarma planı (α≈88°)."""
    return LayerSchedule(
        families=[AngleFamily(88.0, n_layer_sets, "hoop", False, overlap_pct)],
        name=f"Çevre×{n_layer_sets}",
    )

def make_polar_helical_hoop_schedule(
    polar_n: int, helical_alpha: float, helical_n: int, hoop_n: int,
    overlap_pct: float = 5.0,
) -> LayerSchedule:
    """
    Klasik 3-aileli basınçlı kap planı: kutupsal + helisel + çevre.

    Standart dizim: [polar, hoop, helical, hoop, polar] simetrik.
    """
    families: List[AngleFamily] = []
    if polar_n > 0:
        families.append(AngleFamily(10.0, polar_n, "polar", True, overlap_pct))
    if hoop_n > 0:
        families.append(AngleFamily(88.0, hoop_n // 2 if hoop_n > 1 else hoop_n,
                                     "hoop", False, overlap_pct))
    if helical_n > 0:
        families.append(AngleFamily(helical_alpha, helical_n, "helical", True, overlap_pct))
    if hoop_n > 1:
        families.append(AngleFamily(88.0, hoop_n - hoop_n // 2, "hoop", False, overlap_pct))
    if polar_n > 0:
        families.append(AngleFamily(10.0, polar_n, "polar", True, overlap_pct))

    return LayerSchedule(
        families=families,
        name=f"[Kutupsal×{polar_n}|Çevre×{hoop_n}|Helisel±{helical_alpha:.0f}°×{helical_n}]",
    )
