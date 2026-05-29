"""
core/fiber_deposition.py — Fiber Yatırma Simülasyonu
=====================================================
Sarma yolu boyunca gerçek bant yatırmayı simüle eder; yüzeyde lokal
kalınlık haritası, fiber yönelim haritası, bindirme birikimi ve
kaplanmamış bölge tespiti üretir.

Izgara: (z × θ) — eksenel × açısal yüzey hücreleri.

coverage_solver ile fark: bu modül her hücrede SADECE devre sayımı değil,
biriken KALINLIK (her geçişin sıkıştırılmış bant kalınlığı) ve son geçişin
FİBER YÖNELİMİNİ (lokal sarma açısı) de tutar.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .fiber_band import FiberBand
from .geometry_engine import MandrelProfile
from .path_generator import WindingPath, WindingPoint

_BAND_SAMPLES = 9


def compaction_factor(
    layer_index: int,
    tension_N: float = 50.0,
    radius_mm: float = 50.0,
) -> float:
    """
    Katman dizinine göre sıkıştırma çarpanı (0 < factor ≤ 1).

    Fizik (fiber yerleşimi / iç içe geçme modeli):
    - Her yeni katman önceki katmanların vadilerine gömülür (nesting).
    - Net efektif kalınlık artışı layer_index arttıkça azalır (üstel yakınsama).
    - Yüksek gerilim → daha fazla radyal basınç → hafif ek incelme.
    - Büyük yarıçap → eğrilik basıncı düşük → biraz daha az sıkıştırma.
    - factor > 0 garantili → toplam laminat kalınlığı her zaman monotonik artar.

    Model:  f(i) = (1 - N*(1-exp(-i/τ))) × tension_mod × radius_mod
      N=0.12 : maks iç içe geçme fraksiyonu (yaklaşık %12 azalma)
      τ=2.5  : yarılanma derinliği (katman)
    """
    nesting_frac = 0.12
    tau = 2.5
    nesting = nesting_frac * (1.0 - math.exp(-layer_index / tau)) if layer_index > 0 else 0.0

    # Gerilim etkisi: referans 50 N; her 50 N üstünde %0.5 ek incelme
    tension_ref = 50.0
    tension_mod = max(0.85, 1.0 - 0.005 * max(0.0, tension_N - tension_ref) / tension_ref)

    # Yarıçap etkisi: r↑ → eğrilik basıncı↓ → hafif daha az sıkıştırma
    radius_ref = 50.0
    radius_mod = min(1.0, max(0.90, 1.0 - 0.04 * max(0.0, radius_mm - radius_ref) / radius_ref))

    return max(0.70, (1.0 - nesting) * tension_mod * radius_mod)


@dataclass
class UncoveredRegion:
    """Kaplanmamış yüzey bölgesi."""
    z_center_mm: float
    theta_center_deg: float
    area_mm2: float
    cell_count: int


@dataclass
class DepositionMap:
    """
    Fiber yatırma haritası.

    thickness_mm[i,j]    : (z_i, θ_j) hücresindeki biriken kalınlık (mm).
    orientation_deg[i,j] : Son yatırılan bandın lokal sarma açısı (°, eksenden).
    coverage_count[i,j]  : Hücreden geçen devre sayısı.
    """
    z_bins: np.ndarray
    theta_bins: np.ndarray
    thickness_mm: np.ndarray
    orientation_deg: np.ndarray
    coverage_count: np.ndarray
    profile: Optional[MandrelProfile] = None

    @property
    def coverage_pct(self) -> float:
        return float(np.sum(self.coverage_count > 0)) / self.coverage_count.size * 100.0

    @property
    def uncovered_pct(self) -> float:
        return 100.0 - self.coverage_pct

    @property
    def mean_thickness_mm(self) -> float:
        """Kaplanan hücrelerin ortalama kalınlığı."""
        covered = self.thickness_mm[self.coverage_count > 0]
        return float(covered.mean()) if covered.size else 0.0

    @property
    def total_thickness_sum(self) -> float:
        """Tüm hücrelerdeki toplam birikmiş kalınlık (mm).
        Katman eklendikçe monotonik artar — katman karşılaştırmaları için kullan."""
        return float(self.thickness_mm.sum())

    @property
    def max_thickness_mm(self) -> float:
        return float(self.thickness_mm.max())

    @property
    def min_covered_thickness_mm(self) -> float:
        covered = self.thickness_mm[self.coverage_count > 0]
        return float(covered.min()) if covered.size else 0.0

    def thickness_uniformity(self) -> float:
        """
        Kalınlık tekdüzelik indeksi (0..1).
        1 = mükemmel tekdüze, 0 = çok düzensiz.
        """
        covered = self.thickness_mm[self.coverage_count > 0]
        if covered.size < 2 or covered.mean() < 1e-9:
            return 1.0 if covered.size else 0.0
        cv = covered.std() / covered.mean()
        return max(0.0, 1.0 - cv)

    def mean_orientation_deg(self) -> float:
        """Kaplanan hücrelerin ortalama fiber yönelimi (°)."""
        covered = self.orientation_deg[self.coverage_count > 0]
        return float(covered.mean()) if covered.size else 0.0

    def find_uncovered_regions(self, min_area_mm2: float = 1.0) -> List[UncoveredRegion]:
        """Bitişik kaplanmamış hücreleri bölge olarak grupla."""
        regions: List[UncoveredRegion] = []
        Nz, Nth = self.coverage_count.shape
        dz = (self.z_bins[-1] - self.z_bins[0]) / max(Nz - 1, 1)
        dtheta = 2.0 * math.pi / Nth

        empty = self.coverage_count == 0
        visited = np.zeros_like(empty, dtype=bool)

        for i in range(Nz):
            for j in range(Nth):
                if not empty[i, j] or visited[i, j]:
                    continue
                # Flood fill (θ sarmalı)
                stack = [(i, j)]
                cells: List[Tuple[int, int]] = []
                while stack:
                    ri, ci = stack.pop()
                    ci %= Nth
                    if ri < 0 or ri >= Nz or visited[ri, ci] or not empty[ri, ci]:
                        continue
                    visited[ri, ci] = True
                    cells.append((ri, ci))
                    stack.extend([(ri - 1, ci), (ri + 1, ci),
                                  (ri, ci - 1), (ri, ci + 1)])
                if not cells:
                    continue
                if self.profile is not None:
                    r_avg = float(np.mean([self.profile.radius_at(self.z_bins[c[0]])
                                           for c in cells]))
                else:
                    r_avg = 50.0
                area = len(cells) * dz * dtheta * r_avg
                if area >= min_area_mm2:
                    regions.append(UncoveredRegion(
                        z_center_mm=float(np.mean([self.z_bins[c[0]] for c in cells])),
                        theta_center_deg=math.degrees(
                            float(np.mean([self.theta_bins[c[1]] for c in cells]))),
                        area_mm2=area,
                        cell_count=len(cells),
                    ))
        return sorted(regions, key=lambda r: -r.area_mm2)

    def summary(self) -> str:
        return (
            f"DepositionMap {self.thickness_mm.shape}: "
            f"kaplama={self.coverage_pct:.1f}% "
            f"kalınlık_ort={self.mean_thickness_mm:.3f}mm "
            f"maks={self.max_thickness_mm:.3f}mm "
            f"tekdüzelik={self.thickness_uniformity():.3f} "
            f"yönelim_ort={self.mean_orientation_deg():.1f}°"
        )


def _circuit_local_alpha(circuit_pts: List[WindingPoint],
                          profile: MandrelProfile) -> np.ndarray:
    """Bir devrenin her noktasında lokal sarma açısını (derece) hesapla."""
    n = len(circuit_pts)
    z_c = np.array([p.x_mm for p in circuit_pts])
    a_cum = np.radians(np.array([p.a_deg for p in circuit_pts]))
    r_c = np.interp(z_c, profile.z_mm, profile.r_mm)

    dz = np.diff(z_c)
    da = np.abs(np.diff(a_cum))
    r_mid = (r_c[:-1] + r_c[1:]) * 0.5
    ds_circ = r_mid * da
    ds_axial = np.abs(dz)
    alpha_mid = np.degrees(np.arctan2(ds_circ, ds_axial + 1e-12))

    alpha = np.empty(n)
    alpha[0] = alpha_mid[0]
    alpha[-1] = alpha_mid[-1]
    if n > 2:
        alpha[1:-1] = (alpha_mid[:-1] + alpha_mid[1:]) * 0.5
    return alpha


def simulate_deposition(
    paths: List[WindingPath],
    band: FiberBand,
    profile: MandrelProfile,
    n_z: int = 120,
    n_theta: int = 360,
    tension_N: float = 50.0,
) -> DepositionMap:
    """
    Bir veya daha fazla katman yolunu yüzeye yatır.

    Her devre için bant ayak izi boyanır; her geçiş hücreye sıkıştırılmış
    kalınlık ekler ve o hücrenin yönelimini günceller (son geçiş).
    Katman-bağımlı sıkıştırma `compaction_factor()` ile uygulanır.

    Parametreler
    ----------
    paths     : Katman yollarının listesi (tek katman için tek elemanlı liste).
    band      : Fiber bant fiziği.
    profile   : Referans mandrel profili (ızgara ve yarıçap için).
    tension_N : Payout gerilimi (Newton) — sıkıştırma hesabı için.
    """
    z0 = float(profile.z_mm[0])
    z1 = float(profile.z_mm[-1])
    z_bins = np.linspace(z0, z1, n_z)
    th_bins = np.linspace(0.0, 2.0 * math.pi, n_theta)

    thickness = np.zeros((n_z, n_theta), dtype=np.float64)
    orientation = np.zeros((n_z, n_theta), dtype=np.float64)
    coverage = np.zeros((n_z, n_theta), dtype=np.int32)

    t_ply = band.compacted_thickness_mm
    W = band.tow_width_mm
    t_offsets = np.linspace(-W / 2.0, W / 2.0, _BAND_SAMPLES)
    dz_grid = (z1 - z0) / max(n_z - 1, 1)

    for path in paths:
        pts = path.points
        if len(pts) < 2:
            continue

        # Devre başına grupla
        circuits: dict = {}
        for pt in pts:
            circuits.setdefault((pt.layer, pt.circuit), []).append(pt)

        temp = np.zeros((n_z, n_theta), dtype=bool)

        for circuit_key, circuit_pts in circuits.items():
            if len(circuit_pts) < 2:
                continue
            temp[:] = False

            layer_idx = circuit_key[0]  # (layer, circuit) tuple
            r_mid_mm = float(np.interp(
                float(np.mean([p.x_mm for p in circuit_pts])),
                profile.z_mm, profile.r_mm,
            ))
            c_factor = compaction_factor(layer_idx, tension_N, r_mid_mm)
            t_layer = t_ply * c_factor

            z_c = np.array([p.x_mm for p in circuit_pts])
            a_c = np.radians(np.array([p.a_deg for p in circuit_pts])) % (2.0 * math.pi)
            r_c = np.interp(z_c, profile.z_mm, profile.r_mm)
            alpha_c = _circuit_local_alpha(circuit_pts, profile)
            alpha_rad = np.radians(alpha_c)

            sin_a = np.sin(alpha_rad)
            cos_a = np.cos(alpha_rad)

            dz_off = np.outer(sin_a, t_offsets)
            dth_off = np.outer(cos_a / np.maximum(r_c, 1e-3), t_offsets)

            # Yönelim için noktasal eşleme (örnek başına alpha)
            alpha_tiled = np.repeat(alpha_c, _BAND_SAMPLES)

            z_s = (z_c[:, None] + dz_off).ravel()
            th_s = ((a_c[:, None] + dth_off) % (2.0 * math.pi)).ravel()

            iz = np.floor((z_s - z0) / dz_grid + 0.5).astype(int)
            ith = np.floor(th_s / (2.0 * math.pi) * n_theta).astype(int)

            valid = (iz >= 0) & (iz < n_z) & (ith >= 0) & (ith < n_theta)
            iz_v = iz[valid]
            ith_v = ith[valid]
            alpha_v = alpha_tiled[valid]

            # Bu devrenin yönelimini hücrelere yaz (son yazan kazanır)
            orientation[iz_v, ith_v] = alpha_v
            temp[iz_v, ith_v] = True

            # Devre bazlı: her hücreye 1 kez kalınlık ekle (katman sıkıştırması ile)
            thickness += temp.astype(np.float64) * t_layer
            coverage += temp.astype(np.int32)

    return DepositionMap(
        z_bins=z_bins,
        theta_bins=th_bins,
        thickness_mm=thickness,
        orientation_deg=orientation,
        coverage_count=coverage,
        profile=profile,
    )
