"""
core/coverage_solver.py — 2-Boyutlu Yüzey Kaplama Çözücü
==========================================================
Filament sarma yolunun mandrel yüzeyi üzerindeki kaplama dağılımını
hesaplar: devre başına hücre sayısı, üst üste binme ısı haritası,
boşluk tespiti ve bant genişliği tazminatı.

Yüzey ızgarası: (z × θ) — eksenel konumlar × açısal konumlar.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from .fiber_band import FiberBand
from .geometry_engine import MandrelProfile
from .path_generator import WindingPath, WindingPoint

# Bant genişliği örnek sayısı (performans / doğruluk dengesi)
_BAND_SAMPLES = 9


@dataclass
class GapRegion:
    """Kaplama eksikliği olan bölge."""
    z_center_mm: float
    theta_center_deg: float
    area_mm2: float
    severity: float  # 0..1 (1 = tamamen boş)


@dataclass
class CoverageMap:
    """
    2-Boyutlu yüzey kaplama haritası.

    count[i, j] = (z_bins[i], theta_bins[j]) hücresinden geçen
                  ayrı fitil devresi sayısı.
    """
    z_bins: np.ndarray       # (Nz,) eksenel hücre merkezleri (mm)
    theta_bins: np.ndarray   # (Ntheta,) açısal hücre merkezleri (rad)
    count: np.ndarray        # (Nz, Ntheta) int32, geçen devre sayısı
    profile: Optional[MandrelProfile] = None  # Yüzey alanı hesabı için

    @property
    def coverage_pct(self) -> float:
        """En az bir devre tarafından kaplanan hücre yüzdesi."""
        return float(np.sum(self.count > 0)) / self.count.size * 100.0

    @property
    def gap_pct(self) -> float:
        """Hiçbir devre tarafından kaplanmayan hücre yüzdesi."""
        return 100.0 - self.coverage_pct

    @property
    def overlap_pct(self) -> float:
        """İki veya daha fazla devre tarafından kaplanan hücre yüzdesi."""
        return float(np.sum(self.count >= 2)) / self.count.size * 100.0

    @property
    def max_overlap_count(self) -> int:
        """Tek bir hücredeki maksimum üst üste binme sayısı."""
        return int(self.count.max())

    def uniformity_index(self) -> float:
        """
        Kaplama tekdüzelik indeksi (0..1).

        Kaplanan hücrelerin standart sapması / ortalamaya göre normalize edilmiş.
        1.0 = mükemmel tekdüze, 0.0 = son derece düzensiz.
        """
        covered = self.count[self.count > 0].astype(float)
        if len(covered) < 2:
            return 1.0 if len(covered) == 1 else 0.0
        cv = covered.std() / (covered.mean() + 1e-9)
        return max(0.0, 1.0 - cv)

    def find_gap_regions(self, min_area_mm2: float = 1.0) -> List[GapRegion]:
        """
        Boşluk bölgelerini tespit et.

        Bitişik boşluk hücreleri tek bir bölge olarak gruplandırılır.
        """
        gaps = []
        Nz, Nth = self.count.shape
        dz = (self.z_bins[-1] - self.z_bins[0]) / max(Nz - 1, 1)
        dtheta = 2.0 * math.pi / Nth

        gap_mask = self.count == 0
        visited = np.zeros_like(gap_mask, dtype=bool)

        def _expand(r, c):
            stack = [(r, c)]
            cells = []
            while stack:
                ri, ci = stack.pop()
                if ri < 0 or ri >= Nz or ci < 0 or ci >= Nth:
                    continue
                ci_wrap = ci % Nth
                if visited[ri, ci_wrap] or not gap_mask[ri, ci_wrap]:
                    continue
                visited[ri, ci_wrap] = True
                cells.append((ri, ci_wrap))
                for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    stack.append((ri + dr, ci_wrap + dc))
            return cells

        for i in range(Nz):
            for j in range(Nth):
                if gap_mask[i, j] and not visited[i, j]:
                    cells = _expand(i, j)
                    if not cells:
                        continue
                    r_center = float(np.mean([self.z_bins[c[0]] for c in cells]))
                    th_center = float(np.mean([self.theta_bins[c[1]] for c in cells]))
                    # Hücre alanı = dz * (r * dtheta) ≈ dz * dtheta * r_avg
                    if self.profile is not None:
                        r_avg = float(np.mean([self.profile.radius_at(
                            self.z_bins[c[0]]) for c in cells]))
                    else:
                        r_avg = 50.0  # mm fallback
                    area = len(cells) * dz * dtheta * r_avg
                    if area >= min_area_mm2:
                        gaps.append(GapRegion(
                            z_center_mm=r_center,
                            theta_center_deg=math.degrees(th_center),
                            area_mm2=area,
                            severity=1.0,
                        ))

        return sorted(gaps, key=lambda g: -g.area_mm2)

    def overlap_heatmap(self) -> np.ndarray:
        """
        Normalize edilmiş üst üste binme ısı haritası (0..1).
        0 = boşluk, 0.5 = tam kaplama (1 devre), 1 = maksimum bindirme.
        """
        mx = max(float(self.count.max()), 1.0)
        return self.count.astype(float) / mx

    def summary(self) -> str:
        return (
            f"CoverageMap {self.count.shape}: "
            f"kaplama={self.coverage_pct:.1f}%, "
            f"boşluk={self.gap_pct:.1f}%, "
            f"bindirme={self.overlap_pct:.1f}%, "
            f"maks_bindirme={self.max_overlap_count}x, "
            f"tekdüzelik={self.uniformity_index():.3f}"
        )


def solve_coverage(
    path: WindingPath,
    band: FiberBand,
    profile: MandrelProfile,
    n_z: int = 120,
    n_theta: int = 360,
) -> CoverageMap:
    """
    Fitil bant ayak izlerini boyayarak 2B kaplama haritası oluştur.

    Her devre bağımsız olarak işlenir; devreler birikimli sayım döndürür.
    Bu yöntem, her hücrenin kaç farklı devre tarafından kapsandığını doğru
    şekilde hesaplar (her devre yalnızca 1 kez sayılır).

    Algoritma
    ---------
    1. Yolu devre/kat grubuna göre ayır.
    2. Her devre için yerel winding açısını hesapla (ardışık noktalardan).
    3. Bant genişliğini dik yönde ±W/2 örnekle.
    4. Örnekleri (z, θ) ızgara hücrelerine düş; geçici bool maskesi ile işaretle.
    5. Geçici maskeden ana count matrisine ekle.
    """
    pts = path.points
    if not pts:
        z_bins = np.linspace(float(profile.z_mm[0]), float(profile.z_mm[-1]), n_z)
        th_bins = np.linspace(0.0, 2.0 * math.pi, n_theta)
        return CoverageMap(z_bins, th_bins, np.zeros((n_z, n_theta), dtype=np.int32), profile)

    z0 = float(profile.z_mm[0])
    z1 = float(profile.z_mm[-1])
    z_bins = np.linspace(z0, z1, n_z)
    th_bins = np.linspace(0.0, 2.0 * math.pi, n_theta)
    count = np.zeros((n_z, n_theta), dtype=np.int32)

    W = band.tow_width_mm
    t_offsets = np.linspace(-W / 2.0, W / 2.0, _BAND_SAMPLES)  # (K,) dik ofset

    # Devre başına nokta listesi
    circuits: dict = {}
    for pt in pts:
        key = (pt.layer, pt.circuit)
        if key not in circuits:
            circuits[key] = []
        circuits[key].append(pt)

    temp = np.zeros((n_z, n_theta), dtype=bool)

    for circuit_pts in circuits.values():
        n = len(circuit_pts)
        if n < 2:
            continue
        temp[:] = False

        z_c = np.array([p.x_mm for p in circuit_pts], dtype=np.float64)
        # Kümülatif açıyı 0..2π'ye sarmala
        a_c = np.radians(np.array([p.a_deg for p in circuit_pts])) % (2.0 * math.pi)
        r_c = np.interp(z_c, profile.z_mm, profile.r_mm)

        # Her nokta için lokal sarma açısını hesapla
        # alpha (eksen yönünden): atan2(|dz|, |r·dθ|)
        dz = np.diff(z_c)
        # Kümülatif açı farkı (her zaman pozitif olmalı çünkü a_deg monoton artar)
        raw_da = np.diff(np.radians(np.array([p.a_deg for p in circuit_pts])))
        da = np.abs(raw_da)
        r_mid = (r_c[:-1] + r_c[1:]) * 0.5
        ds_circ = r_mid * da
        ds_axial = np.abs(dz)
        alpha_mid = np.arctan2(ds_axial, ds_circ + 1e-9)  # (N-1,)

        # Uç noktalar için genişlet (ikinci dereceden yaklaşım)
        alpha_c = np.empty(n, dtype=np.float64)
        alpha_c[0] = alpha_mid[0]
        alpha_c[-1] = alpha_mid[-1]
        if n > 2:
            alpha_c[1:-1] = (alpha_mid[:-1] + alpha_mid[1:]) * 0.5

        sin_a = np.sin(alpha_c)   # (N,) — eksenel bileşen
        cos_a = np.cos(alpha_c)   # (N,) — çevresel bileşen

        # Bant genişliği boyunca dik örnekleme:
        # dz_offset = t * sin(alpha_local)     → eksenel kayma
        # dtheta_offset = t * cos(alpha_local) / r_local → açısal kayma
        dz_off = np.outer(sin_a, t_offsets)        # (N, K)
        dth_off = np.outer(cos_a / np.maximum(r_c, 1e-3), t_offsets)  # (N, K)

        z_s = (z_c[:, None] + dz_off).ravel()
        th_s = ((a_c[:, None] + dth_off) % (2.0 * math.pi)).ravel()

        # Izgara indeksleri
        dz_grid = (z1 - z0) / max(n_z - 1, 1)
        iz = np.floor((z_s - z0) / dz_grid + 0.5).astype(int)
        ith = np.floor(th_s / (2.0 * math.pi) * n_theta).astype(int)

        valid = (iz >= 0) & (iz < n_z) & (ith >= 0) & (ith < n_theta)
        iz = iz[valid]
        ith = ith[valid]

        temp[iz, ith] = True
        count += temp.astype(np.int32)

    return CoverageMap(z_bins, th_bins, count, profile)


def bandwidth_compensation(
    path: WindingPath,
    band: FiberBand,
    profile: MandrelProfile,
    target_coverage_pct: float = 98.0,
) -> Tuple[int, float]:
    """
    Hedef kaplama yüzdesine ulaşmak için gereken ek devre sayısını ve
    tavsiye edilen yeni bindirme yüzdesini hesapla.

    Döner: (extra_circuits, recommended_overlap_pct)
    """
    cmap = solve_coverage(path, band, profile)
    current_cov = cmap.coverage_pct

    if current_cov >= target_coverage_pct:
        return 0, band.overlap_pct

    # Eksik kaplama oranından gereken ek devre sayısını tahmin et
    deficit_pct = target_coverage_pct - current_cov
    extra_circuits = math.ceil(
        deficit_pct / 100.0 * path.n_circuits / max(current_cov / 100.0, 0.01)
    )

    # Hedef kaplama için gereken bindirme oranını bul
    r_avg = profile.avg_radius_mm
    circumference = 2.0 * math.pi * r_avg
    total_circuits = path.n_circuits + extra_circuits
    # W * (1 - ov/100) * total_circuits = circumference → ov = 1 - circumference/(W*n)
    if total_circuits > 0 and band.tow_width_mm > 0:
        step_needed = circumference / total_circuits
        ov_needed = max(0.0, (1.0 - step_needed / band.tow_width_mm) * 100.0)
    else:
        ov_needed = band.overlap_pct

    return extra_circuits, min(ov_needed, 50.0)


# ═══════════════════════════════════════════════════════════════════════════════
# GELİŞMİŞ KAPLAMA ANALİZİ (Faz: Endüstriyel Üretim Doğruluğu — Görev 5)
# ═══════════════════════════════════════════════════════════════════════════════
# coverage_solver.py uzantısı: gerçek bant projeksiyon genişliği, lokal kaplama
# yoğunluğu, reçine fazlası / kuru fiber risk bölgeleri, bindirme histogramı ve
# istatistiksel kaplama metrikleri.


def band_projection_width_mm(tow_width_mm: float, alpha_deg: float) -> float:
    """
    Gerçek eksenel bant projeksiyon genişliği: W_axial = W / sin(α).

    Fiber, eksenle α açısı yaptığında bant genişliğinin eksenel iz düşümü
    W/sin(α)'dır. α → 90° (hoop) → W (en dar); α → 0° → ∞ (eksenel).
    """
    sin_a = math.sin(math.radians(max(abs(alpha_deg), 0.5)))
    return tow_width_mm / sin_a


@dataclass
class CoverageStatistics:
    """İstatistiksel kaplama metrikleri."""
    n_cells: int
    coverage_pct: float
    gap_pct: float
    overlap_pct: float
    mean_count: float              # tüm hücrelerde ortalama geçiş sayısı
    mean_covered_count: float      # yalnızca kaplanan hücrelerde
    std_count: float
    max_count: int
    median_count: float
    p95_count: float               # 95. yüzdelik geçiş sayısı
    histogram: dict                # {geçiş_sayısı: hücre_sayısı}
    local_density_map: np.ndarray  # (Nz, Ntheta) normalize lokal yoğunluk [0,1]

    def summary(self) -> str:
        return (
            f"KaplamaİstatistiK: kaplama={self.coverage_pct:.1f}% "
            f"boşluk={self.gap_pct:.1f}% bindirme={self.overlap_pct:.1f}% | "
            f"geçiş: ort={self.mean_covered_count:.2f} std={self.std_count:.2f} "
            f"medyan={self.median_count:.1f} p95={self.p95_count:.1f} maks={self.max_count} | "
            f"histogram={self.histogram}"
        )


def overlap_histogram(cmap: CoverageMap) -> dict:
    """
    Bindirme histogramı: {geçiş_sayısı: o sayıda geçişe sahip hücre adedi}.

    0 = boşluk, 1 = tek geçiş (tam kaplama), 2+ = bindirme.
    """
    counts = cmap.count.ravel()
    vals, freqs = np.unique(counts, return_counts=True)
    return {int(v): int(f) for v, f in zip(vals, freqs)}


def coverage_statistics(cmap: CoverageMap) -> CoverageStatistics:
    """
    Bir kaplama haritasından istatistiksel metrikleri hesapla.

    Lokal yoğunluk haritası: her hücredeki geçiş sayısı, maksimuma normalize.
    """
    counts = cmap.count
    flat = counts.ravel().astype(float)
    covered = flat[flat > 0]

    mx = max(float(counts.max()), 1.0)
    density = counts.astype(float) / mx

    return CoverageStatistics(
        n_cells=int(counts.size),
        coverage_pct=cmap.coverage_pct,
        gap_pct=cmap.gap_pct,
        overlap_pct=cmap.overlap_pct,
        mean_count=float(flat.mean()),
        mean_covered_count=float(covered.mean()) if covered.size else 0.0,
        std_count=float(flat.std()),
        max_count=int(counts.max()),
        median_count=float(np.median(flat)),
        p95_count=float(np.percentile(flat, 95)),
        histogram=overlap_histogram(cmap),
        local_density_map=density,
    )


@dataclass
class CoverageRiskReport:
    """Reçine fazlası ve kuru fiber risk bölgeleri."""
    excess_resin_zones: List[GapRegion]   # aşırı bindirme (count ≥ eşik)
    dry_fiber_zones: List[GapRegion]      # boşluk / yetersiz kaplama
    excess_resin_pct: float               # aşırı bindirmeli hücre yüzdesi
    dry_fiber_pct: float                  # boşluk hücre yüzdesi
    overlap_threshold: int
    is_acceptable: bool
    warnings: List[str]

    def summary(self) -> str:
        status = "KABUL EDİLEBİLİR ✓" if self.is_acceptable else "RİSKLİ ✗"
        return (
            f"KaplamaRiski: {status} | "
            f"reçine_fazlası={self.excess_resin_pct:.1f}% "
            f"({len(self.excess_resin_zones)} bölge, eşik≥{self.overlap_threshold}×) | "
            f"kuru_fiber={self.dry_fiber_pct:.1f}% ({len(self.dry_fiber_zones)} bölge)"
        )


def _group_mask_regions(
    mask: np.ndarray, cmap: CoverageMap, min_area_mm2: float, severity: float,
) -> List[GapRegion]:
    """Boolean maskeden bitişik bölgeleri grupla (θ sarmalı flood fill)."""
    Nz, Nth = mask.shape
    dz = (cmap.z_bins[-1] - cmap.z_bins[0]) / max(Nz - 1, 1)
    dtheta = 2.0 * math.pi / Nth
    visited = np.zeros_like(mask, dtype=bool)
    regions: List[GapRegion] = []

    for i in range(Nz):
        for j in range(Nth):
            if not mask[i, j] or visited[i, j]:
                continue
            stack = [(i, j)]
            cells: List[Tuple[int, int]] = []
            while stack:
                ri, ci = stack.pop()
                ci %= Nth
                if ri < 0 or ri >= Nz or visited[ri, ci] or not mask[ri, ci]:
                    continue
                visited[ri, ci] = True
                cells.append((ri, ci))
                stack.extend([(ri - 1, ci), (ri + 1, ci), (ri, ci - 1), (ri, ci + 1)])
            if not cells:
                continue
            if cmap.profile is not None:
                r_avg = float(np.mean([cmap.profile.radius_at(cmap.z_bins[c[0]])
                                       for c in cells]))
            else:
                r_avg = 50.0
            area = len(cells) * dz * dtheta * r_avg
            if area >= min_area_mm2:
                regions.append(GapRegion(
                    z_center_mm=float(np.mean([cmap.z_bins[c[0]] for c in cells])),
                    theta_center_deg=math.degrees(
                        float(np.mean([cmap.theta_bins[c[1]] for c in cells]))),
                    area_mm2=area,
                    severity=severity,
                ))
    return sorted(regions, key=lambda g: -g.area_mm2)


def analyze_coverage_risk(
    cmap: CoverageMap,
    overlap_threshold: int = 3,
    min_area_mm2: float = 1.0,
    max_excess_pct: float = 10.0,
    max_dry_pct: float = 5.0,
) -> CoverageRiskReport:
    """
    Reçine fazlası (aşırı bindirme) ve kuru fiber (boşluk) risk bölgelerini tespit et.

    Parametreler
    ----------
    overlap_threshold : Bu sayı ve üzeri geçiş = aşırı bindirme (reçine fazlası).
    min_area_mm2      : Raporlanacak minimum bölge alanı.
    max_excess_pct    : Kabul edilebilir maksimum reçine fazlası yüzdesi.
    max_dry_pct       : Kabul edilebilir maksimum kuru fiber yüzdesi.

    Fizik
    -----
    - Aşırı bindirme → fazla reçine birikir → ağırlık artışı, kalınlık şişmesi,
      lokal zayıflık (reçinece zengin bölge).
    - Boşluk → kuru fiber / açık alan → sızıntı yolu, mekanik zayıflık.
    """
    counts = cmap.count
    n_cells = counts.size

    excess_mask = counts >= overlap_threshold
    dry_mask = counts == 0

    excess_pct = float(np.sum(excess_mask)) / n_cells * 100.0
    dry_pct = float(np.sum(dry_mask)) / n_cells * 100.0

    excess_zones = _group_mask_regions(excess_mask, cmap, min_area_mm2, 0.7)
    dry_zones = _group_mask_regions(dry_mask, cmap, min_area_mm2, 1.0)

    warnings: List[str] = []
    if excess_pct > max_excess_pct:
        warnings.append(
            f"Reçine fazlası %{excess_pct:.1f} > %{max_excess_pct:.1f} "
            f"({overlap_threshold}× bindirme eşiği)")
    if dry_pct > max_dry_pct:
        warnings.append(f"Kuru fiber %{dry_pct:.1f} > %{max_dry_pct:.1f} (boşluk)")

    is_acceptable = (excess_pct <= max_excess_pct) and (dry_pct <= max_dry_pct)

    return CoverageRiskReport(
        excess_resin_zones=excess_zones,
        dry_fiber_zones=dry_zones,
        excess_resin_pct=excess_pct,
        dry_fiber_pct=dry_pct,
        overlap_threshold=overlap_threshold,
        is_acceptable=is_acceptable,
        warnings=warnings,
    )
