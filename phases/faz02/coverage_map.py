"""
coverage_map.py — Silindirik Yüzey Kaplama Haritası
====================================================
Fiber yollarından üretilen 2D kaplama haritası:
  eksen-1: z (eksenel, 0..L)
  eksen-2: φ (azimut, 0..2π)

Her hücre şunları saklar:
  count      : Kaç fiber bandı geçti (kaç kat)
  thickness  : Lokal kalınlık = count · t_fiber [mm]
  density    : Kaplama yoğunluğu [0..1]
  first_layer: İlk kaplayan katman indeksi

Algoritma:
  Her ToolPoint için fiber bandının kapladığı φ aralığını hesapla:
    φ_center = tp.phi_rad % (2π)
    φ_half   = b_eff / (2·r)   [rad]
    φ_range  = [φ_center - φ_half, φ_center + φ_half]

  Hücre çözünürlüğü:
    Δz   = L / N_z     [mm/hücre]
    Δφ   = 2π / N_phi  [rad/hücre]
    → Tipik: N_z=200, N_phi=360

Analiz çıktıları:
  coverage_fraction: Tam kaplanan alan / toplam alan
  max_count:         Maksimum üst üste binen fiber sayısı
  gap_zones:         Hiç kaplama olmayan bölgeler
  overlap_zones:     >1 katman kaplama olan bölgeler
  thickness_map:     [N_z × N_phi] kalınlık haritası
  uniformity_index:  σ(thickness) / μ(thickness) — homojenlik ölçüsü

Referans: Bookhart & Fowler (1968), Appendix C — Coverage per Circuit
  COVERAGE/CIRCUIT = w / (πr·cos(α))
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from geometry import CylindricalMandrel
from winding_math import ToolPoint, WindingParameters, ClairautConstants
from layer_manager import WindingSchedule, WoundCircuit, WindingType


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CoverageConfig:
    """
    Kaplama haritası çözünürlük ve analiz konfigürasyonu.

    n_z:            Eksenel hücre sayısı
    n_phi:          Azimut hücre sayısı
    fiber_thickness: Tek fiber katman kalınlığı [mm]
    """
    n_z:             int   = 200
    n_phi:           int   = 360
    fiber_thickness: float = 0.25    # [mm] — tipik cam elyaf

    def __post_init__(self) -> None:
        if self.n_z < 10:
            raise ValueError(f"n_z={self.n_z} çok küçük (≥10)")
        if self.n_phi < 36:
            raise ValueError(f"n_phi={self.n_phi} çok küçük (≥36)")
        if self.fiber_thickness <= 0:
            raise ValueError(f"fiber_thickness={self.fiber_thickness} pozitif olmalı")


# ---------------------------------------------------------------------------
# Coverage Statistics
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CoverageStats:
    """
    Kaplama analizi istatistikleri.

    coverage_fraction:  Tam kaplanan alan / toplam alan [0..1]
    gap_fraction:       Hiç kaplanmamış alan oranı [0..1]
    uniformity_index:   σ/μ kalınlık — 0=mükemmel, 1=çok heterojen
    max_thickness:      Maksimum lokal kalınlık [mm]
    min_thickness:      Minimum lokal kalınlık (sıfır olmayan) [mm]
    mean_thickness:     Ortalama kalınlık [mm]
    n_gap_cells:        Boşluk hücresi sayısı
    n_overlap_cells:    Üst üste binen hücre sayısı (>1 kat)
    coverage_per_circuit: Bookhart-Fowler formülünden teorik değer
    """
    coverage_fraction:    float
    gap_fraction:         float
    uniformity_index:     float   # Coefficient of Variation σ/μ
    max_thickness:        float
    min_thickness_nonzero:float
    mean_thickness:       float
    n_gap_cells:          int
    n_overlap_cells:      int
    n_total_cells:        int
    coverage_per_circuit: float   # Teorik, Bookhart&Fowler Eq.

    def report(self) -> str:
        return (
            f"  Kaplama Oranı:          {self.coverage_fraction*100:.2f}%\n"
            f"  Boşluk Oranı:           {self.gap_fraction*100:.2f}%\n"
            f"  Homojenlik (σ/μ):       {self.uniformity_index:.4f} "
            f"({'Mükemmel' if self.uniformity_index < 0.05 else 'İyi' if self.uniformity_index < 0.15 else 'Kabul' if self.uniformity_index < 0.30 else 'Zayıf'})\n"
            f"  Min kalınlık (sıfırsız):{self.min_thickness_nonzero:.4f} mm\n"
            f"  Max kalınlık:           {self.max_thickness:.4f} mm\n"
            f"  Ortalama kalınlık:      {self.mean_thickness:.4f} mm\n"
            f"  Boşluk hücresi:         {self.n_gap_cells} / {self.n_total_cells}\n"
            f"  Overlap hücresi:        {self.n_overlap_cells} ({self.n_overlap_cells/self.n_total_cells*100:.1f}%)\n"
            f"  Kaplama/devre (teor):   {self.coverage_per_circuit:.6f}\n"
        )


# ---------------------------------------------------------------------------
# Coverage Map
# ---------------------------------------------------------------------------

class CoverageMap:
    """
    2D silindirik kaplama haritası üreticisi ve analizörü.

    Koordinat sistemi:
      z-eksen:   0 → L    (N_z hücre)
      φ-eksen:   0 → 2π   (N_phi hücre)
      Her hücre: (Δz × Δφ) boyutlu yüzey parçası

    Fiber band modeli:
      Her ToolPoint'te, fiber band'ın kapladığı:
        z aralığı: [z - Δz_half, z + Δz_half]  (nokta tabanlı, Δz_half → 0)
        φ aralığı: [φ - φ_half, φ + φ_half]     φ_half = b_eff / (2r)

      Birleşik eğri boyunca bu hesap her segment için yapılır.

    Üretim:
        cm = CoverageMap(mandrel, winding, constants)
        cm.add_circuit(circuit)          # Tek devre ekle
        cm.add_schedule(schedule)        # Tüm program ekle
        stats = cm.analyze()
        cm.print_ascii(z_bins=40, phi_bins=80)
    """

    def __init__(
        self,
        mandrel:   CylindricalMandrel,
        winding:   WindingParameters,
        constants: ClairautConstants,
        config:    Optional[CoverageConfig] = None,
    ) -> None:
        self.mandrel   = mandrel
        self.winding   = winding
        self.constants = constants
        self.config    = config or CoverageConfig()

        L   = mandrel.length
        N_z = self.config.n_z
        N_p = self.config.n_phi

        # Grid hücre boyutu
        self.dz  = L / N_z           # [mm/hücre]
        self.dph = 2.0 * math.pi / N_p   # [rad/hücre]

        # Ana veri matrisleri
        self.count_map     = np.zeros((N_z, N_p), dtype=np.int16)  # fiber geçiş sayısı
        self.layer_map     = np.full((N_z, N_p), -1, dtype=np.int8)  # ilk katman

        # Fiber band yarı-genişliği [rad]
        R            = mandrel.radius
        cos_a        = math.cos(winding.alpha_rad)
        b_eff        = winding.bandwidth / cos_a
        self._phi_half = b_eff / (2.0 * R)   # [rad]

        # İstatistik sayaçları
        self._total_passes = 0

    # -----------------------------------------------------------------------
    # Map Building
    # -----------------------------------------------------------------------

    def add_circuit(
        self,
        circuit: WoundCircuit,
        layer_idx: int = 0,
    ) -> None:
        """
        Tek bir devreyi kaplama haritasına ekle.

        Ardışık ToolPoint çiftleri arasında z ekseninde lineer interpolasyon
        yaparak z-hücre boşluklarını doldurur.
        """
        if not circuit.toolpath:
            return

        N_z  = self.config.n_z
        N_p  = self.config.n_phi
        L    = self.mandrel.length
        tp   = circuit.toolpath

        def mark_phi_band(iz: int, phi_wrapped: float, layer: int) -> None:
            """Belirli z-hücresinde, phi band aralığını işaretle."""
            n_phi_cells = max(1, int(2 * self._phi_half / self.dph) + 1)
            ip_center   = int((phi_wrapped % (2.0 * math.pi)) / self.dph)
            half_cells  = n_phi_cells // 2
            for di in range(-half_cells, half_cells + 2):
                ip = (ip_center + di) % N_p
                self.count_map[iz, ip] += 1
                if self.layer_map[iz, ip] < 0:
                    self.layer_map[iz, ip] = layer

        for i in range(len(tp) - 1):
            tp0, tp1 = tp[i], tp[i + 1]
            z0, z1   = tp0.x, tp1.x
            phi0_w   = tp0.phi_rad % (2.0 * math.pi)
            phi1_w   = tp1.phi_rad % (2.0 * math.pi)

            iz0 = max(0, min(N_z - 1, int(z0 / L * N_z)))
            iz1 = max(0, min(N_z - 1, int(z1 / L * N_z)))

            iz_lo, iz_hi = min(iz0, iz1), max(iz0, iz1)
            n_steps      = max(1, iz_hi - iz_lo)

            for step in range(n_steps + 1):
                iz = iz_lo + step
                if iz > N_z - 1:
                    break
                t   = step / n_steps if n_steps > 0 else 0.0
                phi = phi0_w + t * (phi1_w - phi0_w)
                mark_phi_band(iz, phi, layer_idx)

        # Son nokta
        tp_last = tp[-1]
        iz_last = max(0, min(N_z - 1, int(tp_last.x / L * N_z)))
        mark_phi_band(iz_last, tp_last.phi_rad % (2.0 * math.pi), layer_idx)

        self._total_passes += 1

    def add_schedule(self, schedule: WindingSchedule) -> None:
        """Tüm winding programını kaplama haritasına ekle."""
        for layer in schedule.layers:
            for circuit in layer.circuits:
                self.add_circuit(circuit, layer_idx=layer.layer_index)

    # -----------------------------------------------------------------------
    # Analysis
    # -----------------------------------------------------------------------

    def analyze(self) -> CoverageStats:
        """
        Kaplama haritasını analiz et ve istatistik üret.

        Returns:
            CoverageStats nesnesi
        """
        t_fiber = self.config.fiber_thickness
        N_z     = self.config.n_z
        N_p     = self.config.n_phi

        # Kalınlık haritası
        thickness = self.count_map.astype(np.float32) * t_fiber

        total_cells   = N_z * N_p
        covered_cells = int(np.sum(self.count_map > 0))
        gap_cells     = total_cells - covered_cells
        overlap_cells = int(np.sum(self.count_map > 1))

        # Kalınlık istatistikleri (sadece kaplanmış bölge)
        if covered_cells > 0:
            nonzero_t     = thickness[self.count_map > 0]
            mean_t        = float(np.mean(nonzero_t))
            std_t         = float(np.std(nonzero_t))
            uniformity    = std_t / mean_t if mean_t > 0 else 0.0
            min_t_nonzero = float(np.min(nonzero_t))
            max_t         = float(np.max(nonzero_t))
        else:
            mean_t = std_t = uniformity = min_t_nonzero = max_t = 0.0

        # Bookhart & Fowler (1968) Appendix C: teorik kaplama/devre
        R     = self.mandrel.radius
        b_eff = self.winding.bandwidth / math.cos(self.winding.alpha_rad)
        coverage_per_circuit = b_eff / (math.pi * R * math.cos(self.winding.alpha_rad))

        return CoverageStats(
            coverage_fraction     = covered_cells / total_cells,
            gap_fraction          = gap_cells / total_cells,
            uniformity_index      = uniformity,
            max_thickness         = max_t,
            min_thickness_nonzero = min_t_nonzero,
            mean_thickness        = mean_t,
            n_gap_cells           = gap_cells,
            n_overlap_cells       = overlap_cells,
            n_total_cells         = total_cells,
            coverage_per_circuit  = coverage_per_circuit,
        )

    def get_thickness_profile(self, phi_idx: int = None) -> np.ndarray:
        """
        Belirli azimut dilimindeki kalınlık profili z boyunca.

        Args:
            phi_idx: Azimut hücre indeksi (None → ortalama)

        Returns:
            (N_z,) array, kalınlık [mm]
        """
        t = self.count_map.astype(np.float32) * self.config.fiber_thickness
        if phi_idx is not None:
            return t[:, phi_idx % self.config.n_phi]
        return t.mean(axis=1)

    def get_circumferential_profile(self, z_idx: int = None) -> np.ndarray:
        """
        Belirli eksenel konumdaki kalınlık profili φ boyunca.

        Args:
            z_idx: Eksenel hücre indeksi (None → ortalama)
        """
        t = self.count_map.astype(np.float32) * self.config.fiber_thickness
        if z_idx is not None:
            return t[z_idx % self.config.n_z, :]
        return t.mean(axis=0)

    # -----------------------------------------------------------------------
    # ASCII Visualization
    # -----------------------------------------------------------------------

    def print_ascii(
        self,
        z_bins:   int = 60,
        phi_bins: int = 30,
        chars:    str = " ·:+*#",
    ) -> None:
        """
        Terminal ASCII kaplama haritası çiz.

        z-eksen: yatay (sütun)
        φ-eksen: dikey (satır)

        Karakterler: ' ' = boşluk, '·' = az kaplama, '#' = yoğun kaplama
        """
        N_z = self.config.n_z
        N_p = self.config.n_phi

        # Downsample
        z_step  = max(1, N_z // z_bins)
        ph_step = max(1, N_p // phi_bins)
        sub     = self.count_map[::z_step, ::ph_step]

        max_count = sub.max() if sub.max() > 0 else 1
        n_chars   = len(chars)

        print(f"\n  CoverageMap [{z_bins}z × {phi_bins}φ] — max={max_count} kat")
        print("  z: 0" + " " * (z_bins - 8) + f"L={self.mandrel.length:.0f}mm")
        print("  " + "─" * z_bins)

        phi_labels = ["0°", "90°", "180°", "270°", "360°"]
        for phi_i in range(phi_bins):
            row = ""
            for zi in range(min(z_bins, sub.shape[1])):
                val   = sub[phi_i, zi] if phi_i < sub.shape[0] and zi < sub.shape[1] else 0
                level = int(val / max_count * (n_chars - 1))
                row  += chars[level]

            # φ etiketi
            phi_frac = phi_i / phi_bins
            if any(abs(phi_frac - f / 4) < 0.05 for f in range(5)):
                phi_label = phi_labels[min(4, int(phi_frac * 4 + 0.5))]
                label = f"  {phi_label:>4}│{row}"
            else:
                label = f"       │{row}"
            print(label)

        print("  " + "─" * z_bins)

    # -----------------------------------------------------------------------
    # Detailed Report
    # -----------------------------------------------------------------------

    def full_report(self) -> str:
        """Tam analiz raporu."""
        stats = self.analyze()
        lines = [
            "=" * 60,
            "COVERAGE MAP ANALİZİ",
            "=" * 60,
            f"  Grid:      {self.config.n_z}z × {self.config.n_phi}φ hücre",
            f"  Δz:        {self.dz:.4f} mm/hücre",
            f"  Δφ:        {math.degrees(self.dph):.4f}°/hücre",
            f"  φ_half:    {math.degrees(self._phi_half):.4f}° (bant yarı-genişliği)",
            f"  Geçen devre sayısı: {self._total_passes}",
            "-" * 60,
            stats.report(),
            "=" * 60,
        ]
        return "\n".join(lines)
