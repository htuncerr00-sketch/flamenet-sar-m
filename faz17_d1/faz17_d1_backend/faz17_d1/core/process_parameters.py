"""
core/process_parameters.py — Proses Mühendisliği Parametreleri
================================================================
Filament sarma kompoziti için malzeme ve proses parametrelerini modeller:
reçine içeriği, tow sayısı, bant sıkıştırma, kür çekme payı ve hız sınırları.

Malzeme dengesi (ağırlık → hacim)
---------------------------------
W_f = fiber ağırlık oranı = 1 − W_r
V_f = (W_f/ρ_f) / (W_f/ρ_f + W_r/ρ_r)         [fiber hacim oranı]
ρ_c = 1 / (W_f/ρ_f + W_r/ρ_r)                  [kompozit yoğunluğu]
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Tuple


@dataclass
class ProcessParameters:
    """
    Sarma prosesi malzeme ve işlem parametreleri.

    resin_content_pct : Reçine ağırlık yüzdesi (matris) [0..100].
    tow_count         : Bant içindeki tow (fitil) sayısı.
    tow_tex_g_km      : Tek tow lineer yoğunluğu (g/km = g/1000m).
    fiber_density_g_cm3 : Fiber yoğunluğu (karbon ≈ 1.8).
    resin_density_g_cm3 : Reçine yoğunluğu (epoksi ≈ 1.2).
    band_compaction_ratio : Bant sıkıştırma oranı (0..1; 1 = sıkıştırma yok).
    cure_shrinkage_pct : Kür sonrası hacimsel çekme yüzdesi.
    max_winding_speed_mm_s : Proses kaynaklı maksimum sarma hızı.
    """
    resin_content_pct: float = 35.0
    tow_count: int = 4
    tow_tex_g_km: float = 800.0
    fiber_density_g_cm3: float = 1.80
    resin_density_g_cm3: float = 1.20
    band_compaction_ratio: float = 0.85
    cure_shrinkage_pct: float = 3.0
    max_winding_speed_mm_s: float = 120.0

    def __post_init__(self) -> None:
        if not (0.0 <= self.resin_content_pct < 100.0):
            raise ValueError(f"resin_content_pct ∈ [0,100): {self.resin_content_pct}")
        if self.tow_count < 1:
            raise ValueError(f"tow_count >= 1 olmalı: {self.tow_count}")
        if self.tow_tex_g_km <= 0:
            raise ValueError(f"tow_tex_g_km > 0 olmalı: {self.tow_tex_g_km}")
        if not (0.1 <= self.band_compaction_ratio <= 1.0):
            raise ValueError(f"band_compaction_ratio ∈ [0.1,1]: {self.band_compaction_ratio}")

    # ── Ağırlık oranları ─────────────────────────────────────────────────────

    @property
    def fiber_weight_fraction(self) -> float:
        """Fiber ağırlık oranı W_f = 1 − W_r."""
        return 1.0 - self.resin_content_pct / 100.0

    @property
    def resin_weight_fraction(self) -> float:
        return self.resin_content_pct / 100.0

    # ── Hacim oranları ───────────────────────────────────────────────────────

    @property
    def fiber_volume_fraction(self) -> float:
        """V_f = (W_f/ρ_f) / (W_f/ρ_f + W_r/ρ_r)."""
        wf = self.fiber_weight_fraction
        wr = self.resin_weight_fraction
        vf_term = wf / self.fiber_density_g_cm3
        vr_term = wr / self.resin_density_g_cm3
        return vf_term / (vf_term + vr_term)

    @property
    def composite_density_g_cm3(self) -> float:
        """ρ_c = 1 / (W_f/ρ_f + W_r/ρ_r)."""
        wf = self.fiber_weight_fraction
        wr = self.resin_weight_fraction
        return 1.0 / (wf / self.fiber_density_g_cm3 + wr / self.resin_density_g_cm3)

    # ── Lineer yoğunluklar ───────────────────────────────────────────────────

    @property
    def fiber_linear_density_g_mm(self) -> float:
        """
        Bandın fiber lineer yoğunluğu (g/mm).
        tex = g/km = g/1e6 mm  →  tow_count · tex / 1e6
        """
        return self.tow_count * self.tow_tex_g_km / 1.0e6

    @property
    def total_linear_density_g_mm(self) -> float:
        """Reçine dahil toplam bant lineer yoğunluğu (g/mm)."""
        wf = self.fiber_weight_fraction
        if wf <= 0:
            return float('inf')
        return self.fiber_linear_density_g_mm / wf

    # ── Üretim tahminleri ────────────────────────────────────────────────────

    def fiber_mass_g(self, fiber_length_mm: float) -> float:
        """Verilen fiber uzunluğu için fiber kütlesi (g)."""
        return fiber_length_mm * self.fiber_linear_density_g_mm

    def resin_mass_g(self, fiber_length_mm: float) -> float:
        """Reçine kütlesi (g)."""
        return self.total_mass_g(fiber_length_mm) - self.fiber_mass_g(fiber_length_mm)

    def total_mass_g(self, fiber_length_mm: float) -> float:
        """Toplam yatırılan kütle (fiber + reçine) (g)."""
        return fiber_length_mm * self.total_linear_density_g_mm

    def composite_volume_cm3(self, fiber_length_mm: float) -> float:
        """Kür sonrası kompozit hacmi (cm³), çekme payı dahil."""
        raw_vol = self.total_mass_g(fiber_length_mm) / self.composite_density_g_cm3
        return raw_vol * (1.0 - self.cure_shrinkage_pct / 100.0)

    def band_areal_weight_g_m2(self, band_width_mm: float) -> float:
        """
        Bandın fiber alansal ağırlığı (FAW, g/m²).
        FAW = (fiber lineer yoğunluk [g/mm] · 1000 mm/m) / (genişlik [mm] / 1000 mm/m)
        """
        if band_width_mm <= 0:
            return 0.0
        # g/mm → g/m: ×1000 ; genişlik mm → m: /1000
        return (self.fiber_linear_density_g_mm * 1000.0) / (band_width_mm / 1000.0)

    def cured_thickness_mm(self, nominal_thickness_mm: float) -> float:
        """Kür çekme payı uygulanmış nihai ply kalınlığı (mm)."""
        return nominal_thickness_mm * (1.0 - self.cure_shrinkage_pct / 100.0)

    def speed_limit_violation(self, winding_speed_mm_s: float) -> bool:
        """Proses hız sınırı aşıldı mı?"""
        return winding_speed_mm_s > self.max_winding_speed_mm_s * 1.001

    def summary(self) -> str:
        return (
            f"ProcessParameters: reçine={self.resin_content_pct:.0f}%(ağ.) "
            f"V_f={self.fiber_volume_fraction:.1%} "
            f"ρ_c={self.composite_density_g_cm3:.3f}g/cm³ "
            f"tow×{self.tow_count}@{self.tow_tex_g_km:.0f}tex "
            f"lineer={self.total_linear_density_g_mm*1000:.2f}g/m "
            f"çekme={self.cure_shrinkage_pct:.1f}%"
        )
