"""
core/fiber_band.py — Fiber Bant Fiziği
========================================
Filament sarma sürecinde fitil (tow) bandının fiziksel davranışını modeller.
Sıkıştırma, bindirme/boşluk, kat birikimi ve bant baskısı dahildir.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class FiberBand:
    """
    Fitil bant özellikleri.

    Sarma açısına, bindirme oranına ve sıkıştırma faktörüne göre etkin
    adım mesafesi, katman kalınlığı ve bant baskısı hesaplanır.
    """
    tow_width_mm: float = 6.0       # Nominal fitil genişliği (mm)
    tow_thickness_mm: float = 0.25  # Nominal fitil kalınlığı (mm)
    compaction_factor: float = 0.85  # Gerilim altında sıkıştırma oranı (0..1)
    overlap_pct: float = 5.0        # Komşu bantlar arası bindirme % (0..50)
    gap_pct: float = 0.0            # Komşu bantlar arası boşluk % (0..20)

    def __post_init__(self) -> None:
        if not (0 < self.tow_width_mm <= 200):
            raise ValueError(f"tow_width_mm out of range: {self.tow_width_mm}")
        if not (0 < self.tow_thickness_mm <= 10):
            raise ValueError(f"tow_thickness_mm out of range: {self.tow_thickness_mm}")
        if not (0.1 <= self.compaction_factor <= 1.0):
            raise ValueError(f"compaction_factor must be in [0.1, 1.0]: {self.compaction_factor}")
        if not (0.0 <= self.overlap_pct <= 90.0):
            raise ValueError(f"overlap_pct must be in [0, 90]: {self.overlap_pct}")
        if not (0.0 <= self.gap_pct <= 50.0):
            raise ValueError(f"gap_pct must be in [0, 50]: {self.gap_pct}")

    # ── Adım ve kaplama ──────────────────────────────────────────────────────

    @property
    def effective_step_mm(self) -> float:
        """
        Komşu bant merkezleri arası net adım mesafesi (mm).

        effective_step = W * (1 - overlap_pct/100 + gap_pct/100)

        Bindirme < 0 olamaz: eğer overlap_pct > 100 ise adım negatif olur
        (fiziksel olarak anlamsız), bu nedenle 0.01 mm'ye kırpılır.
        """
        factor = 1.0 - self.overlap_pct / 100.0 + self.gap_pct / 100.0
        return max(0.01, self.tow_width_mm * factor)

    @property
    def compacted_thickness_mm(self) -> float:
        """Gerilim altında sıkıştırılmış fitil kalınlığı (mm)."""
        return self.tow_thickness_mm * self.compaction_factor

    def layer_thickness_mm(self, n_layers: int) -> float:
        """N kat için toplam birikmeli kalınlık (mm)."""
        if n_layers < 1:
            return 0.0
        return self.compacted_thickness_mm * n_layers

    def circuits_for_coverage(self, circumference_mm: float) -> int:
        """
        Verilen çevreyi tamamen kaplamak için gereken minimum devre sayısı.

        n = ceil(circumference / effective_step)
        """
        step = max(self.effective_step_mm, 0.01)
        return math.ceil(circumference_mm / step)

    # ── Geometrik projeksiyon ────────────────────────────────────────────────

    def bandwidth_at_angle(self, alpha_deg: float) -> float:
        """
        Verilen sarma açısında (mandrel ekseninden) fitil bandının eksenel
        projeksiyonu (mm).

        bandwidth_axial = tow_width / sin(alpha)

        Fizik: Fitil, eksenle alpha açısı yaptığında, bant genişliğinin eksen
        yönündeki iz düşümü tow_width / sin(alpha) değerine eşittir.

        alpha = 0° (aksiyel) → sonsuz (tüm uzunluğu kaplıyor)
        alpha = 90° (çevre) → tow_width (en dar eksenel alan)
        """
        alpha_rad = math.radians(max(abs(alpha_deg), 0.5))
        return self.tow_width_mm / math.sin(alpha_rad)

    def circumferential_step_at_angle(self, alpha_deg: float) -> float:
        """
        Verilen sarma açısında bir devreden diğerine çevresel adım mesafesi (mm).

        Bu değer effective_step_mm ile aynıdır; açıya bağlı değildir çünkü
        bant genişliği zaten çevresel yönde ölçülmüştür.
        """
        return self.effective_step_mm

    # ── Gerilim ve baskı ─────────────────────────────────────────────────────

    def band_pressure_mpa(self, tension_N: float, mandrel_radius_mm: float) -> float:
        """
        Gerilim altında bant-mandrel temas basıncı (MPa).

        Silindirik basınçlı kap analogisinden:
            p = T / (r · w)   [N/mm² = MPa]

        Parametreler
        ----------
        tension_N : Fitil gerilimi (Newton)
        mandrel_radius_mm : Temas noktasındaki mandrel yarıçapı (mm)
        """
        if mandrel_radius_mm <= 0 or self.tow_width_mm <= 0:
            return 0.0
        return tension_N / (mandrel_radius_mm * self.tow_width_mm)

    def compaction_pressure_mpa(self, tension_N: float, mandrel_radius_mm: float,
                                 n_layers: int = 1) -> float:
        """
        N kat birikiminde radyal sıkıştırma basıncı (MPa).

        Her kat önceki katları sıkıştırır; basınç katmanlar arası dağılır.
        Basitleştirilmiş: p_total = n_layers * band_pressure
        """
        return n_layers * self.band_pressure_mpa(tension_N, mandrel_radius_mm)

    # ── Bindirme analizi ─────────────────────────────────────────────────────

    def overlap_thickness_mm(self, n_overlapping: int = 2) -> float:
        """Bindirme bölgesinde birikimli yığın kalınlığı (mm)."""
        return self.compacted_thickness_mm * max(1, n_overlapping)

    def coverage_uniformity_pct(self, actual_circuits: int,
                                 circumference_mm: float) -> float:
        """
        Gerçekleşen devre sayısına göre teorik kaplama tekdüzelik yüzdesi.

        uniformity = min(100, actual_circuits / required_circuits * 100)
        """
        required = self.circuits_for_coverage(circumference_mm)
        if required <= 0:
            return 100.0
        return min(100.0, actual_circuits / required * 100.0)

    # ── Çıktı ────────────────────────────────────────────────────────────────

    def summary(self) -> str:
        """İnsan okunabilir özet."""
        return (
            f"FiberBand: genişlik={self.tow_width_mm:.2f}mm, "
            f"kalınlık={self.tow_thickness_mm:.3f}mm, "
            f"sıkıştırma={self.compaction_factor:.0%}, "
            f"bindirme={self.overlap_pct:.1f}%, "
            f"boşluk={self.gap_pct:.1f}%, "
            f"etkin_adım={self.effective_step_mm:.3f}mm, "
            f"sıkıştırılmış_kalınlık={self.compacted_thickness_mm:.3f}mm"
        )
