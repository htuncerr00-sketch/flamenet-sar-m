"""
core/stl_intelligence.py — STL Intelligence Layer (S1)
======================================================
STL vertex bulutunu *anlayan* katman: dönme eksenini bulur, robust radius(z)
çıkarır, geometriyi (dome/silindir) bölütler, kutup bölgelerini ve turnaround
adaylarını tespit eder, güven + kalite raporlar.

Tasarım belgeleri:
  - STL_INTELLIGENCE_TASARIM.md   (6 bileşen, A-E)
  - STL_CEKIRDEK_ENTEGRASYON_RAPORU.md
  - MANDREL_MODEL_TASARIM.md

İlkeler:
  - numpy-only (scipy YOK): PCA = numpy.linalg.eigh; rafinasyon = coarse-grid.
  - Çıktı downstream-uyumlu: nihai geometri yine MandrelProfile.
  - Deterministik: aynı vertex + aynı param → bit-aynı sonuç.
  - Sessiz yanlış sonuç YASAK: düşük güven her zaman raporlanır.

6 bileşen:
  1. solve_axis              — dönme ekseni (PCA tohum + simetri maliyeti J)
  2. extract_radius_profile  — robust p95 radius(z)
  3. compute_confidence      — 4 alt-skor + agregat + grade
  4. analyze_quality         — winding uygunluk verdict
  5. segment_regions         — dome / cylinder / taper
  6. detect_turnaround_candidates — α → (z_left, z_right, polar_radius)

Orkestratör: analyze_vertices(V) / analyze_stl(path) -> StlIntelligenceReport
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .geometry_engine import MandrelProfile


# ═══════════════════════════════════════════════════════════════════════════════
# Veri yapıları (MANDREL_MODEL_TASARIM.md §1.1)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AxisResult:
    """Dönme ekseni çözüm sonucu."""
    axis_unit: np.ndarray            # (3,) birim vektör — dönme ekseni
    center: np.ndarray               # (3,) eksen üzerinde nokta (centroid)
    j_min: float                     # simetri maliyeti (küçük = iyi)
    eig_ratios: Tuple[float, float, float]
    degeneracy_flag: bool            # küre/küresel-en-boy uyarısı


@dataclass
class RadiusProfile:
    """Eksenel yarıçap profili (radius(z))."""
    z_mm: np.ndarray
    r_mm: np.ndarray
    n_per_bin: np.ndarray            # her bin'deki vertex sayısı
    pole_clamped: bool               # kutupta r kenetlendi mi


@dataclass
class Segment:
    """Tek bir geometrik bölge."""
    z_start_mm: float
    z_end_mm: float
    kind: str                        # "cylinder" | "dome" | "taper"
    r_mean_mm: float
    dr_dz_mean: float


@dataclass
class RegionSegmentation:
    """Bölge bölütlemesi."""
    spans: List[Segment]
    transitions: List[float]         # etiket değişim z'leri

    def by_kind(self, kind: str) -> List[Segment]:
        return [s for s in self.spans if s.kind == kind]


@dataclass
class PoleRegion:
    """Kutup bölgesi (r→0)."""
    side: str                        # "left" | "right"
    z_start_mm: float
    z_end_mm: float
    min_radius_mm: float


@dataclass
class TurnaroundCandidate:
    """Bir α için turnaround verisi."""
    alpha_deg: float
    z_left_mm: float
    z_right_mm: float
    polar_radius_c_mm: float         # c = r_max·sin(α)
    reachable: bool                  # z_right > z_left


@dataclass
class TurnaroundCandidates:
    by_alpha: Dict[float, TurnaroundCandidate] = field(default_factory=dict)


@dataclass
class ConfidenceReport:
    """Güven skoru raporu (0..1)."""
    axis: float
    symmetry: float
    mesh_density: float
    radial_fit: float
    aggregate: float
    grade: str                       # "YÜKSEK" | "ORTA" | "DÜŞÜK" | "REDDET"


@dataclass
class QualityReport:
    """Winding kalite/uygunluk raporu."""
    aspect_ratio: float
    min_radius_mm: float
    is_single_valued: bool
    asymmetry_mm: float
    degenerate_tri_count: int
    mesh_density: float
    winding_suitable: bool
    reasons: List[str] = field(default_factory=list)


@dataclass
class StlIntelligenceReport:
    """STL Intelligence tam çıktısı."""
    profile: MandrelProfile
    axis: AxisResult
    radius_profile: RadiusProfile
    segments: RegionSegmentation
    pole_regions: List[PoleRegion]
    turnaround_candidates: TurnaroundCandidates
    confidence: ConfidenceReport
    quality: QualityReport
    source_path: Optional[str] = None
    units: str = "mm"

    def is_winding_ready(self, min_confidence: float = 0.65) -> bool:
        """Sarma uygunluğu — tek karar noktası.

        Tüm kriterleri kapsar; backend başka bir yerde ayrıca karar vermez:
        - Güven derecesi ≠ REDDET
        - Tek-değerli profil (torus/çok-değerli değil)
        - Eksen dejenere değil (küre/izotropik kütle)
        - Asimetri düşük (< r_max × 10%)
        - Kutup yarıçapı yeterli (≥ r_max × 2%)
        - Agregat güven ≥ min_confidence
        - En az bir erişilebilir turnaround adayı
        """
        c = self.confidence
        q = self.quality

        if c.grade == "REDDET":
            return False
        if not q.is_single_valued:
            return False
        if self.axis.degeneracy_flag:
            return False
        r_max = float(self.profile.r_mm.max())
        if q.asymmetry_mm > r_max * 0.10:
            return False
        if q.min_radius_mm < r_max * 0.02:
            return False
        if c.aggregate < min_confidence:
            return False
        if not any(tc.reachable
                   for tc in self.turnaround_candidates.by_alpha.values()):
            return False
        return True

    def human_summary(self) -> str:
        c = self.confidence
        q = self.quality
        return (
            f"Eksen güveni %{c.axis*100:.0f}, simetri %{c.symmetry*100:.0f}, "
            f"mesh %{c.mesh_density*100:.0f}. Genel: {c.grade}. "
            f"Winding uygun: {'EVET' if q.winding_suitable else 'HAYIR'}. "
            f"r_max={self.profile.r_mm.max():.1f}mm, "
            f"L={self.profile.z_mm[-1]-self.profile.z_mm[0]:.1f}mm."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Yardımcılar
# ═══════════════════════════════════════════════════════════════════════════════

def _project_axis(X: np.ndarray, axis: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Merkezlenmiş X'i eksen çerçevesine projekte et → (z', rho)."""
    z = X @ axis                                  # (N,) eksen boyu projeksiyon
    perp = X - np.outer(z, axis)                  # (N,3) eksene dik bileşen
    rho = np.sqrt(np.einsum('ij,ij->i', perp, perp))
    return z, rho


def _trimmed_variance(values: np.ndarray, lo: float = 10.0, hi: float = 90.0) -> float:
    """p10–p90 kırpılmış varyans (aykırıya dayanıklı)."""
    if values.size < 3:
        return float(np.var(values)) if values.size else 0.0
    a, b = np.percentile(values, [lo, hi])
    core = values[(values >= a) & (values <= b)]
    return float(np.var(core)) if core.size else 0.0


def _symmetry_cost(X: np.ndarray, axis: np.ndarray, n_bins: int = 80) -> float:
    """
    Simetri maliyeti J(a): her z'-dilimindeki rho varyansının (boyutsuz) toplamı.
    Gerçek dönme ekseni bunu minimize eder (body-of-revolution tanımı).
    """
    z, rho = _project_axis(X, axis)
    z0, z1 = float(z.min()), float(z.max())
    if z1 - z0 < 1e-9:
        return float('inf')
    edges = np.linspace(z0, z1, n_bins + 1)
    idx = np.clip(np.searchsorted(edges, z) - 1, 0, n_bins - 1)
    total = 0.0
    count = 0
    for b in range(n_bins):
        sl = rho[idx == b]
        if sl.size < 3:
            continue
        mean = float(sl.mean())
        if mean < 1e-9:
            continue
        total += _trimmed_variance(sl) / (mean * mean)   # boyutsuz
        count += 1
    return total / count if count else float('inf')


# ═══════════════════════════════════════════════════════════════════════════════
# Bileşen 1 — STL Axis Solver
# ═══════════════════════════════════════════════════════════════════════════════

def solve_axis(vertices: np.ndarray, refine: bool = True) -> AxisResult:
    """
    Dönme eksenini bul: PCA tohum + simetri-maliyeti seçim + grid rafinasyon.

    PCA tek başına yetmez (kısa-şişman gövdede en-büyük-λ yanlıştır); bu yüzden
    3 PCA adayı J(a) ile objektif kıyaslanır, en düşük J seçilir.
    """
    V = np.asarray(vertices, dtype=np.float64)
    C = V.mean(axis=0)
    X = V - C

    # Kovaryans + özdeğer ayrışımı (numpy-only)
    M = (X.T @ X) / max(len(X), 1)
    lam, E = np.linalg.eigh(M)                     # lam artan; E sütunları
    lam = np.maximum(lam, 0.0)

    # 3 aday eksen — en düşük simetri maliyetli olanı seç
    candidates = [E[:, i] for i in range(3)]
    costs = [_symmetry_cost(X, a) for a in candidates]
    best = int(np.argmin(costs))
    axis = candidates[best].copy()
    j_min = costs[best]

    # Rafinasyon: eksen etrafında kaba→ince açısal arama (scipy YOK)
    if refine and np.isfinite(j_min):
        axis, j_min = _refine_axis(X, axis, j_min)

    # İşaret normalizasyonu (deterministik): en büyük bileşen pozitif
    k = int(np.argmax(np.abs(axis)))
    if axis[k] < 0:
        axis = -axis
    axis = axis / (np.linalg.norm(axis) + 1e-12)

    # Dejenerasyon teşhisi — İKİ kriter:
    #   (a) özdeğer dejenerasyonu (izotropik kütle dağılımı)
    #   (b) eksen belirsizliği: en iyi eksen, ikinciden belirgin daha iyi DEĞİL
    #       (küre/küresel gövdede HER eksen eşit iyi → en iyi/ikinci ≈ 1)
    lam_sorted = np.sort(lam)[::-1]                # büyük→küçük
    l3, l2, l1 = lam_sorted[0], lam_sorted[1], lam_sorted[2]
    ratio_21 = (l2 - l1) / (l3 + 1e-12)
    ratio_32 = (l3 - l2) / (l3 + 1e-12)
    eig_degenerate = (ratio_21 < 0.05) and (ratio_32 < 0.05)

    finite_costs = sorted(c for c in costs if np.isfinite(c))
    axis_ambiguous = False
    if len(finite_costs) >= 2 and finite_costs[1] > 1e-12:
        # en iyi / ikinci-iyi → 1'e yakınsa eksen ayırt edilemez
        axis_ambiguous = (finite_costs[0] / finite_costs[1]) > 0.40

    degeneracy = bool(eig_degenerate or axis_ambiguous)
    eig_ratios = (float(l1 / (l3 + 1e-12)),
                  float(l2 / (l3 + 1e-12)),
                  1.0)

    return AxisResult(axis_unit=axis, center=C, j_min=float(j_min),
                      eig_ratios=eig_ratios, degeneracy_flag=bool(degeneracy))


def _refine_axis(X: np.ndarray, axis: np.ndarray, j0: float,
                 coarse_deg: float = 5.0, levels: int = 3) -> Tuple[np.ndarray, float]:
    """Eksen etrafında kaba→ince ızgara inişi (numpy-only, scipy gerektirmez)."""
    # Eksene dik iki taban vektörü
    tmp = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(axis, tmp); u /= (np.linalg.norm(u) + 1e-12)
    w = np.cross(axis, u);   w /= (np.linalg.norm(w) + 1e-12)

    best_axis = axis.copy()
    best_j = j0
    step = math.radians(coarse_deg)
    for _ in range(levels):
        improved = False
        for du in (-step, 0.0, step):
            for dw in (-step, 0.0, step):
                if du == 0.0 and dw == 0.0:
                    continue
                cand = best_axis + du * u + dw * w
                cand /= (np.linalg.norm(cand) + 1e-12)
                jc = _symmetry_cost(X, cand)
                if jc < best_j:
                    best_j = jc
                    best_axis = cand
                    improved = True
        if improved:
            # Yön iyileşti — tabanı güncelle, aynı adımda devam
            u = np.cross(best_axis, tmp); u /= (np.linalg.norm(u) + 1e-12)
            w = np.cross(best_axis, u);   w /= (np.linalg.norm(w) + 1e-12)
        else:
            step *= 0.5                            # daha ince ara
    return best_axis, best_j


# ═══════════════════════════════════════════════════════════════════════════════
# Bileşen 2 — STL Radius(z) Extractor
# ═══════════════════════════════════════════════════════════════════════════════

def extract_radius_profile(vertices: np.ndarray, axis: AxisResult,
                           n_bins: int = 300, percentile: float = 95.0
                           ) -> Tuple[RadiusProfile, bool]:
    """
    Robust radius(z): eksene projekte et, örtüşen bin, bin başına p95(rho),
    moving-median düzleştir, boş doldur, kutup kenetle.

    Döner: (RadiusProfile, is_single_valued)
    """
    V = np.asarray(vertices, dtype=np.float64)
    X = V - axis.center
    z, rho = _project_axis(X, axis.axis_unit)

    z0, z1 = float(z.min()), float(z.max())
    if z1 - z0 < 1e-9:
        raise ValueError("Eksen boyunca uzanım sıfır — profil çıkarılamaz.")

    centers = np.linspace(z0, z1, n_bins)
    spacing = (z1 - z0) / (n_bins - 1)
    bin_half = 1.5 * spacing                       # örtüşen binleme

    r = np.zeros(n_bins, dtype=np.float64)
    n_per_bin = np.zeros(n_bins, dtype=np.int64)
    spread = np.zeros(n_bins, dtype=np.float64)    # p95-p50 (tek-değerlilik için)

    # Sıralayıp searchsorted ile pencere — O(N log N)
    order = np.argsort(z)
    z_s = z[order]; rho_s = rho[order]
    lo_idx = np.searchsorted(z_s, centers - bin_half, side='left')
    hi_idx = np.searchsorted(z_s, centers + bin_half, side='right')
    for i in range(n_bins):
        sl = rho_s[lo_idx[i]:hi_idx[i]]
        n_per_bin[i] = sl.size
        if sl.size:
            p95, p5 = np.percentile(sl, [percentile, 5.0])
            r[i] = p95
            spread[i] = p95 - p5            # dilim içi TAM yayılım (torus testi)

    # Boş bin → komşulardan interpolasyon
    nz = n_per_bin > 0
    if nz.sum() < 2:
        raise ValueError("Yeterli vertex yoğunluğu yok — profil çıkarılamaz.")
    r = np.interp(centers, centers[nz], r[nz])

    # Moving-median düzleştirme (faset gürültüsü)
    r = _moving_median(r, window=5)

    # Kutup kenetleme (r→0 tekilliğini önle)
    r_max = float(r.max())
    clamp = r_max * 0.01
    pole_clamped = bool(np.any(r < clamp))
    r = np.maximum(r, clamp)

    # Tek-değerlilik (torus testi): dilim içi rho yayılımı r'ye göre çok büyükse.
    # Eşik 0.25: torus gibi çok-değerli yüzeyler medyan > 0.25 yayılım gösterir;
    # silindir/kubbe yüzey gürültüsü < 0.05 kalır.
    rel_spread = spread[nz] / np.maximum(r[nz], 1e-9)
    is_single_valued = bool(np.median(rel_spread) < 0.25)

    rp = RadiusProfile(z_mm=centers, r_mm=r, n_per_bin=n_per_bin,
                       pole_clamped=pole_clamped)
    return rp, is_single_valued


def _moving_median(a: np.ndarray, window: int = 5) -> np.ndarray:
    """Basit hareketli medyan (numpy-only)."""
    if window < 3 or a.size < window:
        return a
    half = window // 2
    out = a.copy()
    for i in range(a.size):
        lo = max(0, i - half)
        hi = min(a.size, i + half + 1)
        out[i] = np.median(a[lo:hi])
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# Bileşen 5 — Dome / Cylinder Segmentation
# ═══════════════════════════════════════════════════════════════════════════════

def segment_regions(rp: RadiusProfile, eps_cyl: float = 0.02) -> RegionSegmentation:
    """
    dr/dz ile bölgeleri sınıflandır: |dr/dz|<eps ⇒ cylinder, uçta büyük ⇒ dome,
    ara ⇒ taper. Bitişik aynı-etiket binleri tek span'e grupla.
    """
    z = rp.z_mm; r = rp.r_mm
    drdz = _moving_median(np.gradient(r, z), window=5)

    labels = []
    z_span = z[-1] - z[0]
    for i in range(len(z)):
        slope = abs(drdz[i])
        near_end = (z[i] - z[0] < 0.15 * z_span) or (z[-1] - z[i] < 0.15 * z_span)
        if slope < eps_cyl:
            labels.append("cylinder")
        elif slope >= eps_cyl and near_end:
            labels.append("dome")
        else:
            labels.append("taper")

    # Bitişik etiketleri grupla
    spans: List[Segment] = []
    transitions: List[float] = []
    start = 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[start]:
            seg_r = r[start:i]
            seg_slope = drdz[start:i]
            spans.append(Segment(
                z_start_mm=float(z[start]), z_end_mm=float(z[i - 1]),
                kind=labels[start], r_mean_mm=float(seg_r.mean()),
                dr_dz_mean=float(seg_slope.mean())))
            if i < len(labels):
                transitions.append(float(z[i - 1]))
            start = i
    return RegionSegmentation(spans=spans, transitions=transitions)


def detect_pole_regions(rp: RadiusProfile, seg: RegionSegmentation) -> List[PoleRegion]:
    """
    Profil uçlarındaki dome kapanışı (düşük-yarıçap) bölgelerini işaretle.

    Uç bölgesi (ilk/son %15) içindeki minimum yarıçap r_max'ın belirgin altındaysa
    (kapanan dome), o uç bir kutup bölgesidir. Düzleştirme uç değerleri şişirdiği
    için tek-noktalı kontrol yerine pencere-temelli minimum kullanılır.
    """
    r_max = float(rp.r_mm.max())
    threshold = r_max * 0.55                         # dome kapanış eşiği
    poles: List[PoleRegion] = []
    z = rp.z_mm; r = rp.r_mm
    n = len(r)
    win = max(2, int(n * 0.15))                      # uç pencere genişliği

    # Sol uç: ilk pencerede min yarıçap eşiğin altında mı?
    left_min_i = int(np.argmin(r[:win]))
    if r[left_min_i] < threshold and r[left_min_i] < r[win - 1]:
        # kutup, dome yükselene kadar (r ilk kez threshold'u geçene dek)
        i = left_min_i
        while i < n and r[i] < threshold:
            i += 1
        poles.append(PoleRegion(side="left", z_start_mm=float(z[0]),
                                z_end_mm=float(z[min(i, n - 1)]),
                                min_radius_mm=float(r[:win].min())))
    # Sağ uç
    right_seg = r[n - win:]
    right_min_i = int(np.argmin(right_seg)) + (n - win)
    if r[right_min_i] < threshold and r[right_min_i] < r[n - win]:
        i = right_min_i
        while i >= 0 and r[i] < threshold:
            i -= 1
        poles.append(PoleRegion(side="right",
                                z_start_mm=float(z[max(i, 0)]),
                                z_end_mm=float(z[-1]),
                                min_radius_mm=float(r[n - win:].min())))
    return poles


# ═══════════════════════════════════════════════════════════════════════════════
# Bileşen 6 — Turnaround Candidate Detection
# ═══════════════════════════════════════════════════════════════════════════════

# Varsayılan aday sarma açıları (geriye-uyumlu sabit liste).
# Gelecekte alpha_step_deg ile 5°-85° arasında otomatik üretilebilir.
_TURNAROUND_ALPHA_DEFAULTS: List[float] = [10.0, 20.0, 30.0, 45.0, 55.0, 70.0, 80.0]


def _default_alpha_range(step_deg: float = 5.0) -> List[float]:
    """5°-85° arasında step_deg adımıyla α listesi üret."""
    alphas: List[float] = []
    a = 5.0
    while a <= 85.0 + 1e-9:
        alphas.append(float(a))
        a += float(step_deg)
    return alphas


def detect_turnaround_candidates(
        profile: MandrelProfile,
        alphas_deg: Optional[List[float]] = None,
        *,
        alpha_step_deg: float = 0.0) -> TurnaroundCandidates:
    """
    Aday α'lar için c = r_max·sin(α) ve find_turnaround_z_left/right (mevcut)
    ile sarılabilir bölgeyi hesapla.

    alphas_deg: α listesi. None ise _TURNAROUND_ALPHA_DEFAULTS kullanılır.
                alpha_step_deg > 0 verilirse 5°-85° arasında adım adım
                otomatik oluşturulur (varsayılan listeyi geçersiz kılar).
    """
    from .path_generator import find_turnaround_z_left, find_turnaround_z_right

    if alphas_deg is None:
        alphas_deg = (_default_alpha_range(alpha_step_deg)
                      if alpha_step_deg > 0
                      else list(_TURNAROUND_ALPHA_DEFAULTS))

    r_max = float(profile.r_mm.max())
    out = TurnaroundCandidates()
    for a in alphas_deg:
        c = r_max * math.sin(math.radians(a))
        c = min(c, r_max * 0.995)
        z_left = find_turnaround_z_left(profile, c)
        z_right = find_turnaround_z_right(profile, c)
        out.by_alpha[float(a)] = TurnaroundCandidate(
            alpha_deg=float(a), z_left_mm=float(z_left), z_right_mm=float(z_right),
            polar_radius_c_mm=float(c), reachable=bool(z_right > z_left))
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# Bileşen 4 — Quality Analyzer
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_quality(vertices: np.ndarray, rp: RadiusProfile, axis: AxisResult,
                    is_single_valued: bool) -> QualityReport:
    """Winding uygunluk verdict + nedenler."""
    V = np.asarray(vertices, dtype=np.float64)
    r_max = float(rp.r_mm.max())
    r_min = float(rp.r_mm.min())
    length = float(rp.z_mm[-1] - rp.z_mm[0])
    aspect = length / (2.0 * r_max + 1e-9)

    # Asimetri: dilim içi p95-p50 maksimumu
    X = V - axis.center
    z, rho = _project_axis(X, axis.axis_unit)
    z0, z1 = float(z.min()), float(z.max())
    edges = np.linspace(z0, z1, 51)
    idx = np.clip(np.searchsorted(edges, z) - 1, 0, 49)
    asym = 0.0
    for b in range(50):
        sl = rho[idx == b]
        if sl.size >= 3:
            p95, p50 = np.percentile(sl, [95.0, 50.0])
            asym = max(asym, float(p95 - p50))

    mesh_density = float(np.mean(rp.n_per_bin))
    reasons: List[str] = []

    if not is_single_valued:
        reasons.append("Profil tek-değerli değil (dönel simetrik gövde değil — torus?).")
    if r_min < r_max * 0.02:
        reasons.append(f"Kutup yarıçapı çok küçük ({r_min:.2f} mm) — winding zor.")
    if asym > r_max * 0.10:
        reasons.append(f"Yüksek asimetri ({asym:.2f} mm) — eksen şüpheli / gövde eğri.")
    if mesh_density < 3.0:
        reasons.append("Mesh yoğunluğu düşük — profil gürültülü olabilir.")
    if axis.degeneracy_flag:
        reasons.append("Eksen dejenere (küre/küresel en-boy) — dönme ekseni belirsiz.")
    if len(V) < 100:
        reasons.append("Çok az vertex — STL kalitesi yetersiz.")

    suitable = (is_single_valued and r_min >= r_max * 0.02
                and asym <= r_max * 0.10 and mesh_density >= 3.0
                and not axis.degeneracy_flag)

    return QualityReport(
        aspect_ratio=float(aspect), min_radius_mm=r_min,
        is_single_valued=bool(is_single_valued), asymmetry_mm=float(asym),
        degenerate_tri_count=0, mesh_density=mesh_density,
        winding_suitable=bool(suitable), reasons=reasons)


# ═══════════════════════════════════════════════════════════════════════════════
# Bileşen 3 — Confidence Score
# ═══════════════════════════════════════════════════════════════════════════════

def compute_confidence(axis: AxisResult, rp: RadiusProfile, quality: QualityReport,
                       j_ref: float = 0.05, n_target: float = 20.0,
                       weights: Tuple[float, float, float, float] = (0.40, 0.30, 0.15, 0.15)
                       ) -> ConfidenceReport:
    """4 alt-skor → agregat → grade."""
    # axis: simetri maliyeti küçükse yüksek
    axis_score = float(np.clip(1.0 - axis.j_min / j_ref, 0.0, 1.0))
    if axis.degeneracy_flag:
        axis_score = min(axis_score, 0.4)

    # symmetry: asimetri r'ye göre küçükse yüksek
    r_max = float(rp.r_mm.max())
    sym_score = float(np.clip(1.0 - quality.asymmetry_mm / (0.10 * r_max + 1e-9), 0.0, 1.0))

    # mesh_density
    mesh_score = float(np.clip(np.mean(rp.n_per_bin) / n_target, 0.0, 1.0))

    # radial_fit: profilin kendi düzleştirilmişine uzaklığı
    smooth = _moving_median(rp.r_mm, window=7)
    rms = float(np.sqrt(np.mean((rp.r_mm - smooth) ** 2)))
    fit_score = float(np.clip(1.0 - rms / (0.05 * r_max + 1e-9), 0.0, 1.0))

    w = weights
    aggregate = (w[0] * axis_score + w[1] * sym_score
                 + w[2] * mesh_score + w[3] * fit_score)

    # Sınıf
    if aggregate >= 0.85:
        grade = "YÜKSEK"
    elif aggregate >= 0.65:
        grade = "ORTA"
    elif aggregate >= 0.45:
        grade = "DÜŞÜK"
    else:
        grade = "REDDET"
    # Ek kurallar
    if axis.degeneracy_flag and grade in ("YÜKSEK", "ORTA"):
        grade = "DÜŞÜK"
    if not quality.is_single_valued:
        grade = "REDDET"

    return ConfidenceReport(
        axis=axis_score, symmetry=sym_score, mesh_density=mesh_score,
        radial_fit=fit_score, aggregate=float(aggregate), grade=grade)


# ═══════════════════════════════════════════════════════════════════════════════
# Orkestratör
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_vertices(vertices: np.ndarray,
                     source_path: Optional[str] = None) -> StlIntelligenceReport:
    """Vertex bulutundan tam STL Intelligence raporu üret."""
    V = np.asarray(vertices, dtype=np.float64)
    if V.ndim != 2 or V.shape[1] != 3 or len(V) < 4:
        raise ValueError("Geçersiz vertex bulutu (en az 4 nokta, (N,3) gerekli).")

    axis = solve_axis(V)
    rp, single_valued = extract_radius_profile(V, axis)
    profile = MandrelProfile(z_mm=rp.z_mm.copy(), r_mm=rp.r_mm.copy())
    seg = segment_regions(rp)
    poles = detect_pole_regions(rp, seg)
    turn = detect_turnaround_candidates(profile)
    quality = analyze_quality(V, rp, axis, single_valued)
    conf = compute_confidence(axis, rp, quality)

    return StlIntelligenceReport(
        profile=profile, axis=axis, radius_profile=rp, segments=seg,
        pole_regions=poles, turnaround_candidates=turn,
        confidence=conf, quality=quality, source_path=source_path)


def analyze_stl(filepath: str) -> StlIntelligenceReport:
    """STL dosyasından tam STL Intelligence raporu üret."""
    from .stl_processor import parse_stl_vertices
    V = parse_stl_vertices(filepath)
    return analyze_vertices(V, source_path=filepath)
