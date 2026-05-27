"""
coverage_analyzer.py — Gelişmiş Kaplama Analizi
=================================================
CoverageMap'ten zengin analiz çıktıları üretir:
  - Overlap clustering: bitişik yüksek-yoğunluk bölgelerini grupla
  - Gap detection: sıfır kaplama bölgelerini bul ve nitelendir
  - Axial homogeneity: z boyunca kalınlık dağılımı
  - Circumferential homogeneity: φ boyunca kalınlık dağılımı
  - Winding density map: beklenen vs gerçek kaplama oranı
  - Thickness CV (Coefficient of Variation): zon bazlı homojenlik

Kullanılan teknikler:
  - Binary morphology: ndimage.label ile bağlı bileşen analizi
  - Zone analysis: eksenel bölgelere ayırma (start / mid / end)
  - Histogram analizi: kalınlık dağılımı
  - Coefficient of Variation (CV = σ/μ): her zon için
  - Percentile analysis: P5, P25, P50, P75, P95

Referans:
  Bookhart & Fowler (1968): Coverage/circuit = w/(πr·cos(α))
  Koussios (2004) Eq. 8.2: n·d·b_eff = 2πR
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    from scipy.ndimage import label as ndimage_label, binary_dilation
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

from coverage_map import CoverageMap, CoverageStats


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ClusterInfo:
    """Tek bir kaplama kümesi (connected component) bilgisi."""
    cluster_id:    int
    cluster_type:  str      # "gap" | "overlap" | "single"
    n_cells:       int      # Hücre sayısı
    mean_count:    float    # Ortalama fiber geçiş sayısı
    max_count:     int
    z_range_mm:    Tuple[float, float]    # [z_min, z_max]
    phi_range_deg: Tuple[float, float]   # [phi_min, phi_max]
    area_mm2:      float                 # Tahmini alan

    @property
    def is_critical(self) -> bool:
        """Büyük boşluk → yapısal risk."""
        return (self.cluster_type == "gap" and
                self.area_mm2 > 50.0)  # 50mm² üzeri kritik

    def summary(self) -> str:
        return (
            f"  [{self.cluster_type:>8}] ID={self.cluster_id:3d}  "
            f"cells={self.n_cells:4d}  "
            f"count_avg={self.mean_count:5.2f}  "
            f"z=[{self.z_range_mm[0]:5.1f},{self.z_range_mm[1]:5.1f}]mm  "
            f"φ=[{self.phi_range_deg[0]:5.1f},{self.phi_range_deg[1]:5.1f}]°  "
            f"A={self.area_mm2:.1f}mm²"
            f"{'  ⚠ KRİTİK' if self.is_critical else ''}"
        )


@dataclass(slots=True)
class ZoneStats:
    """Eksenel zon (start/mid/end) istatistikleri."""
    zone_name:     str
    z_range_mm:    Tuple[float, float]
    mean_thickness:float
    std_thickness: float
    cv:            float     # σ/μ
    min_thickness: float
    max_thickness: float
    gap_fraction:  float     # Bu zonda boşluk oranı
    percentiles:   Dict[int, float]  # {5: val, 25: val, ...}

    @property
    def quality_grade(self) -> str:
        if self.cv < 0.05: return "A+"
        if self.cv < 0.10: return "A"
        if self.cv < 0.20: return "B"
        if self.cv < 0.35: return "C"
        if self.cv < 0.55: return "D"
        return "F"

    def summary(self) -> str:
        return (
            f"  {self.zone_name:>10}  z=[{self.z_range_mm[0]:5.1f},{self.z_range_mm[1]:5.1f}]mm  "
            f"μ={self.mean_thickness:.4f}mm  σ={self.std_thickness:.4f}mm  "
            f"CV={self.cv:.4f}  [{self.quality_grade}]  "
            f"gap={self.gap_fraction*100:.1f}%"
        )


@dataclass(slots=True)
class CoverageAnalysisResult:
    """Tam analiz sonucu."""
    basic_stats:         CoverageStats
    gap_clusters:        List[ClusterInfo]
    overlap_clusters:    List[ClusterInfo]
    zone_stats:          List[ZoneStats]
    axial_profile:       np.ndarray    # (N_z,) — z boyunca ortalama kalınlık
    circ_profile:        np.ndarray    # (N_phi,) — φ boyunca ortalama kalınlık
    density_map:         np.ndarray    # (N_z, N_phi) — count / expected_count
    thickness_histogram: Tuple[np.ndarray, np.ndarray]  # (counts, bin_edges)
    global_cv:           float         # Tüm kaplanmış alan için σ/μ
    axial_cv:            float         # z boyunca zonal mean'lerin CV'si
    circumf_cv:          float         # φ boyunca mean'lerin CV'si
    homogeneity_score:   float         # [0,1] — 1=mükemmel
    n_critical_gaps:     int

    def summary_report(self) -> str:
        lines = [
            "=" * 68,
            "GELİŞMİŞ KAPLAMA ANALİZİ",
            "=" * 68,
            "  [TEMEL İSTATİSTİKLER]",
            self.basic_stats.report(),
            "-" * 68,
            "  [HOmojenLİK ANALİZİ]",
            f"  Global CV (σ/μ):    {self.global_cv:.4f}",
            f"  Eksenel CV:         {self.axial_cv:.4f}",
            f"  Çevresel CV:        {self.circumf_cv:.4f}",
            f"  Homojenlik Skoru:   {self.homogeneity_score:.4f}  "
            f"[{'Mükemmel' if self.homogeneity_score > 0.90 else 'İyi' if self.homogeneity_score > 0.75 else 'Kabul' if self.homogeneity_score > 0.55 else 'Zayıf'}]",
            "-" * 68,
            "  [ZON ANALİZİ]",
            f"  {'Zon':>10}  {'z Aralığı':>18}  {'μ (mm)':>8}  {'CV':>7}  {'Not':>5}  Gap%",
        ]
        for zs in self.zone_stats:
            lines.append(zs.summary())

        if self.gap_clusters:
            lines += [
                "-" * 68,
                f"  [BOŞLUK KÜMELERİ — {len(self.gap_clusters)} adet, "
                f"{self.n_critical_gaps} kritik]",
            ]
            for gc in self.gap_clusters[:8]:
                lines.append(gc.summary())
            if len(self.gap_clusters) > 8:
                lines.append(f"  ... ve {len(self.gap_clusters)-8} boşluk daha")

        if self.overlap_clusters:
            lines += [
                "-" * 68,
                f"  [OVERLAP KÜMELERİ — {len(self.overlap_clusters)} adet]",
            ]
            for oc in self.overlap_clusters[:5]:
                lines.append(oc.summary())

        lines.append("=" * 68)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Coverage Analyzer
# ---------------------------------------------------------------------------

class CoverageAnalyzer:
    """
    CoverageMap nesnesinden zengin analiz çıktıları üretir.

    Kullanım:
        analyzer = CoverageAnalyzer(coverage_map, mandrel_length, fiber_thickness)
        result = analyzer.analyze()
        print(result.summary_report())
        analyzer.print_density_map()
    """

    def __init__(
        self,
        cov_map:          CoverageMap,
        fiber_thickness:  float = 0.25,   # [mm]
        expected_layers:  float = 1.0,    # Beklenen katman sayısı
    ) -> None:
        self.cov_map         = cov_map
        self.fiber_thickness = fiber_thickness
        self.expected_layers = expected_layers

        self._N_z  = cov_map.config.n_z
        self._N_phi = cov_map.config.n_phi
        self._L    = cov_map.mandrel.length
        self._R    = cov_map.mandrel.radius

        # Kalınlık matrisi [N_z × N_phi]
        self._thickness = cov_map.count_map.astype(np.float32) * fiber_thickness

    # -----------------------------------------------------------------------
    # Main
    # -----------------------------------------------------------------------

    def analyze(self) -> CoverageAnalysisResult:
        """Tam analizi çalıştır."""
        basic     = self.cov_map.analyze()
        ax_prof   = self._axial_profile()
        circ_prof = self._circumferential_profile()
        dens_map  = self._density_map()
        hist      = self._thickness_histogram()
        zones     = self._zone_analysis(n_zones=3)

        # Homojenlik metrikleri
        t_flat = self._thickness[self.cov_map.count_map > 0].flatten()
        global_cv = float(np.std(t_flat) / np.mean(t_flat)) if len(t_flat) > 1 else 0.0

        # Eksenel CV: z boyunca zonal ortalamaların sapması
        ax_nonzero = ax_prof[ax_prof > 0]
        axial_cv   = (float(np.std(ax_nonzero) / np.mean(ax_nonzero))
                      if len(ax_nonzero) > 1 else 0.0)

        # Çevresel CV
        cp_nonzero = circ_prof[circ_prof > 0]
        circ_cv    = (float(np.std(cp_nonzero) / np.mean(cp_nonzero))
                      if len(cp_nonzero) > 1 else 0.0)

        # Homojenlik skoru [0,1]
        homog = max(0.0, 1.0 - (global_cv + 0.5 * axial_cv + 0.5 * circ_cv) / 3.0)

        # Kümeleme
        gap_clusters     = self._find_clusters("gap")
        overlap_clusters = self._find_clusters("overlap")
        n_critical       = sum(1 for gc in gap_clusters if gc.is_critical)

        return CoverageAnalysisResult(
            basic_stats          = basic,
            gap_clusters         = gap_clusters,
            overlap_clusters     = overlap_clusters,
            zone_stats           = zones,
            axial_profile        = ax_prof,
            circ_profile         = circ_prof,
            density_map          = dens_map,
            thickness_histogram  = hist,
            global_cv            = global_cv,
            axial_cv             = axial_cv,
            circumf_cv           = circ_cv,
            homogeneity_score    = homog,
            n_critical_gaps      = n_critical,
        )

    # -----------------------------------------------------------------------
    # Profiller
    # -----------------------------------------------------------------------

    def _axial_profile(self) -> np.ndarray:
        """Z boyunca ortalama kalınlık profili [N_z,]."""
        return self._thickness.mean(axis=1)

    def _circumferential_profile(self) -> np.ndarray:
        """φ boyunca ortalama kalınlık profili [N_phi,]."""
        return self._thickness.mean(axis=0)

    def _density_map(self) -> np.ndarray:
        """
        Winding density haritası: gerçek / beklenen fiber sayısı.

        Beklenen: expected_layers (tüm alanda uniform kaplama varsayımı)
        """
        expected = max(1.0, float(self.cov_map.count_map.max()) * self.expected_layers)
        dens = self.cov_map.count_map.astype(np.float32) / expected
        return np.clip(dens, 0.0, 2.5)  # 2.5x üstü kırp

    def _thickness_histogram(
        self,
        n_bins: int = 30,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Kalınlık histogramı (sadece kaplanmış hücreler)."""
        t_nonzero = self._thickness[self.cov_map.count_map > 0].flatten()
        if len(t_nonzero) == 0:
            return np.array([0]), np.array([0.0, 0.0])
        return np.histogram(t_nonzero, bins=n_bins)

    # -----------------------------------------------------------------------
    # Zon Analizi
    # -----------------------------------------------------------------------

    def _zone_analysis(self, n_zones: int = 3) -> List[ZoneStats]:
        """
        Eksenel bölgelere (start/mid/end) ayır ve her biri için istatistik üret.

        n_zones=3 → [0, L/3], [L/3, 2L/3], [2L/3, L]
        """
        zone_names = {1: ["Start"], 2: ["Start", "End"],
                      3: ["Start", "Middle", "End"],
                      4: ["Start", "Mid-L", "Mid-R", "End"]}
        names = zone_names.get(n_zones, [f"Z{i}" for i in range(n_zones)])

        L     = self._L
        N_z   = self._N_z
        dz    = L / N_z
        zones = []

        for i in range(n_zones):
            z0     = i * L / n_zones
            z1     = (i + 1) * L / n_zones
            iz0    = int(z0 / L * N_z)
            iz1    = min(N_z, int(z1 / L * N_z) + 1)

            t_zone = self._thickness[iz0:iz1, :]
            c_zone = self.cov_map.count_map[iz0:iz1, :]

            t_flat  = t_zone.flatten()
            t_nz    = t_flat[t_flat > 0]
            total_c = float(c_zone.size)
            gap_f   = float(np.sum(c_zone == 0)) / total_c if total_c > 0 else 0.0

            if len(t_nz) > 1:
                mu   = float(np.mean(t_nz))
                sig  = float(np.std(t_nz))
                cv   = sig / mu if mu > 0 else 0.0
                pcts = {p: float(np.percentile(t_nz, p)) for p in [5, 25, 50, 75, 95]}
                t_min = float(t_nz.min())
                t_max = float(t_nz.max())
            else:
                mu = sig = cv = t_min = t_max = 0.0
                pcts = {p: 0.0 for p in [5, 25, 50, 75, 95]}

            zones.append(ZoneStats(
                zone_name      = names[i] if i < len(names) else f"Z{i}",
                z_range_mm     = (z0, z1),
                mean_thickness = mu,
                std_thickness  = sig,
                cv             = cv,
                min_thickness  = t_min,
                max_thickness  = t_max,
                gap_fraction   = gap_f,
                percentiles    = pcts,
            ))

        return zones

    # -----------------------------------------------------------------------
    # Kümeleme
    # -----------------------------------------------------------------------

    def _find_clusters(self, cluster_type: str) -> List[ClusterInfo]:
        """
        Boşluk veya overlap kümelerini bul.

        cluster_type: "gap" (count=0) veya "overlap" (count>1)

        scipy.ndimage.label ile bağlı bileşen analizi kullanır.
        scipy yoksa basit satır bazlı analiz yapar.
        """
        count = self.cov_map.count_map

        if cluster_type == "gap":
            binary_mask = (count == 0)
        else:  # overlap
            binary_mask = (count > 1)

        if not np.any(binary_mask):
            return []

        # Wrap-around φ bağlantısı için maske genişlet
        extended = np.concatenate([binary_mask, binary_mask, binary_mask], axis=1)

        if HAS_SCIPY:
            labeled, n_features = ndimage_label(extended)
            clusters = self._extract_clusters_scipy(
                labeled, n_features, cluster_type, count, extended)
        else:
            clusters = self._extract_clusters_simple(binary_mask, cluster_type, count)

        return sorted(clusters, key=lambda c: -c.area_mm2)

    def _extract_clusters_scipy(
        self,
        labeled:      np.ndarray,
        n_features:   int,
        cluster_type: str,
        count:        np.ndarray,
        extended:     np.ndarray,
    ) -> List[ClusterInfo]:
        """scipy.ndimage.label sonucundan küme bilgisi çıkar."""
        N_z  = self._N_z
        N_phi = self._N_phi
        dz   = self._L / N_z
        dphi = 360.0 / N_phi
        dA   = dz * (self._R * math.radians(dphi))  # hücre alanı [mm²]

        clusters = []
        for cid in range(1, min(n_features + 1, 100)):  # max 100 küme göster
            mask_cid   = (labeled == cid)
            # Sadece orta dilime (orijinal indeksler) bak
            mask_orig  = mask_cid[:, N_phi : 2 * N_phi]
            if not np.any(mask_orig):
                continue

            z_idx, phi_idx = np.where(mask_orig)
            n_cells     = int(np.sum(mask_orig))
            count_vals  = count[z_idx, phi_idx]

            clusters.append(ClusterInfo(
                cluster_id    = cid,
                cluster_type  = cluster_type,
                n_cells       = n_cells,
                mean_count    = float(np.mean(count_vals)),
                max_count     = int(np.max(count_vals)) if len(count_vals) > 0 else 0,
                z_range_mm    = (float(z_idx.min()) * dz,
                                 float(z_idx.max()) * dz),
                phi_range_deg = (float(phi_idx.min()) * dphi,
                                 float(phi_idx.max()) * dphi),
                area_mm2      = n_cells * dA,
            ))

        return clusters

    def _extract_clusters_simple(
        self,
        mask:         np.ndarray,
        cluster_type: str,
        count:        np.ndarray,
    ) -> List[ClusterInfo]:
        """scipy olmadan basit küme tespiti — yatay şerit bazlı."""
        N_z  = self._N_z
        N_phi = self._N_phi
        dz   = self._L / N_z
        dphi = 360.0 / N_phi
        dA   = dz * (self._R * math.radians(dphi))

        clusters = []
        visited  = np.zeros_like(mask, dtype=bool)
        cid      = 0

        for iz in range(N_z):
            for ip in range(N_phi):
                if mask[iz, ip] and not visited[iz, ip]:
                    # BFS
                    queue   = [(iz, ip)]
                    members = []
                    while queue:
                        r, c = queue.pop()
                        if (r < 0 or r >= N_z or c < 0 or c >= N_phi):
                            continue
                        if visited[r, c] or not mask[r, c]:
                            continue
                        visited[r, c] = True
                        members.append((r, c))
                        queue.extend([(r+1,c),(r-1,c),(r,c+1),(r,c-1)])

                    if not members:
                        continue
                    cid += 1
                    z_idx  = [m[0] for m in members]
                    phi_idx= [m[1] for m in members]
                    c_vals = [count[m[0], m[1]] for m in members]

                    clusters.append(ClusterInfo(
                        cluster_id    = cid,
                        cluster_type  = cluster_type,
                        n_cells       = len(members),
                        mean_count    = float(np.mean(c_vals)),
                        max_count     = int(np.max(c_vals)),
                        z_range_mm    = (min(z_idx)*dz, max(z_idx)*dz),
                        phi_range_deg = (min(phi_idx)*dphi, max(phi_idx)*dphi),
                        area_mm2      = len(members) * dA,
                    ))

        return clusters

    # -----------------------------------------------------------------------
    # Görselleştirme
    # -----------------------------------------------------------------------

    def print_density_map(
        self,
        z_bins:   int = 55,
        phi_bins: int = 22,
    ) -> None:
        """
        ASCII density map: beklenen vs gerçek kaplama yoğunluğu.

        Karakterler: ' '=boşluk, '·'=az, ':'=normal, '+'=fazla, '#'=çok fazla
        """
        dens = self._density_map()
        N_z  = self._N_z
        N_phi = self._N_phi

        z_step  = max(1, N_z // z_bins)
        ph_step = max(1, N_phi // phi_bins)
        sub     = dens[::z_step, ::ph_step]

        chars  = " ·:+*#"
        thresholds = [0.0, 0.2, 0.6, 1.0, 1.5, 2.0]

        print(f"\n  Winding Density Map [{z_bins}z × {phi_bins}φ]")
        print(f"  Renk: ' '=boşluk  '·'=ince  ':'=normal  '+'=fazla  '#'=aşırı")
        print(f"  z: 0{' '*(z_bins-8)}L={self._L:.0f}mm")
        print("  " + "─" * z_bins)

        for phi_i in range(min(phi_bins, sub.shape[0])):
            row = ""
            for zi in range(min(z_bins, sub.shape[1])):
                val = sub[phi_i, zi]
                ch  = " "
                for j, thr in enumerate(thresholds):
                    if val >= thr:
                        ch = chars[min(j, len(chars)-1)]
                row += ch

            phi_deg = phi_i / phi_bins * 360.0
            if any(abs(phi_deg - tgt) < 360.0/phi_bins*1.5 for tgt in [0,90,180,270,360]):
                label = f"  {phi_deg:3.0f}°│{row}"
            else:
                label = f"       │{row}"
            print(label)

        print("  " + "─" * z_bins)

    def print_axial_thickness_bar(self, n_bars: int = 50) -> None:
        """z boyunca kalınlık profili — ASCII bar grafik."""
        prof   = self._axial_profile()
        N_z    = len(prof)
        step   = max(1, N_z // n_bars)
        sub    = prof[::step]
        max_t  = sub.max() if sub.max() > 0 else 1.0

        print(f"\n  Eksenel Kalınlık Profili (z: 0→{self._L:.0f}mm)")
        bar_w = 30
        for i, t in enumerate(sub):
            z_mm = i * step * self._L / N_z
            n    = int(t / max_t * bar_w)
            bar  = "█" * n + "░" * (bar_w - n)
            print(f"  z={z_mm:5.0f}mm │{bar}│ {t:.3f}mm")
