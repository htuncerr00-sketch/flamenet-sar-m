"""
core/manual_layer_sequencer.py — Manuel Katman Dizilim Tasarımcısı (Faz 24 ML-1)
==================================================================================

Manuel "katman katman" filament sarım tasarım motoru. TaniqWind Pro + WindingGuru
ekollerinin hibridi: kullanıcı her katmanı tek tek elle ekler, parametrelerini
düzenler ve sistem anlık olarak Clairaut jeodezik / non-jeodezik sürtünme
sınırlarını, fitil çakışma adımını, mandrel build-up yarıçap güncellemesini
ve CLT laminat veri yapısını üretir.

================================================================================
MATEMATİKSEL MODEL
================================================================================

1. Helisel/Hoop Pitch (eksenel ilerleme):
       pitch_mm = fitil_genisligi_mm / sin(|α|)
       n_devre  = ⌈ π·D / (pitch · (1 − çakışma)) ⌉
   α → 0° (saf eksenel) ⟹ pitch → ∞; α → 90° (saf hoop) ⟹ pitch → fitil_genisligi.

2. Clairaut Jeodezik Koruma:
       r(z) · sin(α(z)) = c₀   (sabit, başlangıçta belirlenir)
   Saf jeodezik yolda (μ = 0) bu sabit korunmalıdır.

3. Non-Jeodezik Kayma Sınırı (Koussios 2004):
       |k_g| / k_n ≤ μ
   k_g = jeodezik eğrilik, k_n = normal eğrilik. Oran 1'i aşarsa lif kayar.
   `slip_ratio = max(|λ|)/μ` ⟹ 1.0'dan büyükse satır kırmızı + uyarı.

4. Build-up (yığılma) yarıçap güncellemesi:
       Δr(z) = t_ply / cos(α(z))     (helisel ply lokal kalınlık projeksiyonu)
   Hoop için α ≈ 90° olduğundan cos(α) çok küçük; pratik üst sınır:
       Δr_max = t_ply / cos(89°) ≈ 57·t_ply  ⟹ hoop için Δr = t_ply (direkt eksenel
       projeksiyon, klasik yaklaşım).

5. Yumuşatma filtresi (kubbe yığılma yönetimi):
       r_smooth[i] = (1/W) · Σ r[i-w..i+w]    W = 2w+1 = 5 (varsayılan)
   Hareketli ortalama; uçlarda boundary clamp.

================================================================================
REFERANSLAR
================================================================================

[1] Koussios, S., "Filament Winding: a Unified Approach", Delft U.P., 2004,
    Bölüm 5 (kayma sınırı), Bölüm 7 (build-up).
[2] Vasiliev, V.V. & Morozov, E.V., "Advanced Mechanics of Composite
    Materials and Structural Elements", 3rd ed., Elsevier 2013, Bölüm 5.4.
[3] Wells, G.M. & McAnulty, K.F., "Computer aided filament winding using
    non-geodesic trajectories", ICCM-VI, 1987.
"""
from __future__ import annotations

import math
import enum
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple, Dict, Any, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .geometry_engine import MandrelProfile
    from .material_allowables import LaminaProperties
    from .clt_engine import LaminateStackup


# ── Sabitler ─────────────────────────────────────────────────────────────────

_EPS_SIN          = 1e-3       # sin(α) için güvenli alt sınır (α ≈ 0.057°)
_EPS_COS          = 1e-3       # cos(α) için güvenli alt sınır
_DEFAULT_SMOOTH_W = 5          # hareketli ortalama pencere boyu
_DEFAULT_FRICTION = 0.30       # epoksi/karbon tipik statik sürtünme katsayısı
_MAX_ALPHA_DEG    = 89.5       # 90° = saf hoop singüleritesi
_MIN_ALPHA_DEG    = 0.5        # 0°  = saf eksenel singüleritesi


# ── Enum: Katman tipi ────────────────────────────────────────────────────────

class LayerType(enum.Enum):
    """
    Filament sarma katman tipleri.

    HELICAL     — ±α sarmal (tipik α ∈ [10°, 75°])
    HOOP        — Çember/Hoop sarımı (α ≈ 89-90°, sadece silindir gövde)
    POLAR       — Kutupsal sarım (α ≈ 5-15°, dome geçişli)
    TRANSITION  — Geçiş/dönüş katmanı (kubbe-silindir bandı)
    SKIN_FINISH — Bitiş / dış kabuk koruyucu katmanı (genelde hoop benzeri)
    """
    HELICAL     = "helical"
    HOOP        = "hoop"
    POLAR       = "polar"
    TRANSITION  = "transition"
    SKIN_FINISH = "skin_finish"

    @property
    def display_tr(self) -> str:
        """Türkçe görüntüleme adı."""
        return {
            "helical":     "Helisel (Sarmal)",
            "hoop":        "Çember (Hoop)",
            "polar":       "Kutupsal (Polar)",
            "transition":  "Geçiş",
            "skin_finish": "Bitiş/Kabuk",
        }[self.value]


# ── Veri yapıları ────────────────────────────────────────────────────────────

@dataclass
class LayerSpec:
    """
    Tek bir katmanın tüm tasarım parametreleri.

    UI tablosuyla 1-1 eşleşir. id alanı UI satır indeksinden bağımsız sabit
    katman tanımlayıcısıdır (move/insert sonrası bile satır izlemek için).
    """
    id: int                         # benzersiz katman kimliği (zaman içinde sabit)
    type: LayerType                 # katman tipi
    alpha_deg: float                # sarma açısı (derece, işaretli; ± yönü gösterir)
    fitil_genisligi_mm: float       # tow/bant genişliği (mm)
    cakisma_pct: float              # bant çakışması yüzdesi [0, 95]
    thickness_mm: float             # ply kalınlığı (mm)
    feed_mm_s: float                # taşıyıcı ilerleme hızı (mm/s)
    spindle_rpm: float              # iş mili devri (RPM)
    friction_mu: float              # statik sürtünme katsayısı
    strategy: str                   # "geodesic" | "non_geodesic"
    label: str = ""                 # görüntü etiketi; boş ise auto_label()
    notes: str = ""                 # serbest kullanıcı notu

    # ── Doğrulama ────────────────────────────────────────────────────────────

    def __post_init__(self) -> None:
        if not isinstance(self.type, LayerType):
            try:
                self.type = LayerType(self.type)
            except Exception:
                raise ValueError(f"Geçersiz katman tipi: {self.type}")

        if not (-_MAX_ALPHA_DEG <= self.alpha_deg <= _MAX_ALPHA_DEG):
            raise ValueError(
                f"alpha_deg ∈ [-{_MAX_ALPHA_DEG}, {_MAX_ALPHA_DEG}]: "
                f"{self.alpha_deg}"
            )
        if abs(self.alpha_deg) < _MIN_ALPHA_DEG:
            raise ValueError(
                f"|alpha_deg| ≥ {_MIN_ALPHA_DEG}° olmalı (singüleriteden kaçınma): "
                f"{self.alpha_deg}"
            )
        if self.fitil_genisligi_mm <= 0:
            raise ValueError(f"fitil_genisligi_mm > 0: {self.fitil_genisligi_mm}")
        if not (0.0 <= self.cakisma_pct < 100.0):
            raise ValueError(f"cakisma_pct ∈ [0, 100): {self.cakisma_pct}")
        if self.thickness_mm <= 0:
            raise ValueError(f"thickness_mm > 0: {self.thickness_mm}")
        if self.feed_mm_s <= 0:
            raise ValueError(f"feed_mm_s > 0: {self.feed_mm_s}")
        if self.spindle_rpm <= 0:
            raise ValueError(f"spindle_rpm > 0: {self.spindle_rpm}")
        if not (0.0 <= self.friction_mu <= 1.0):
            raise ValueError(f"friction_mu ∈ [0, 1]: {self.friction_mu}")
        if self.strategy not in ("geodesic", "non_geodesic"):
            raise ValueError(
                f"strategy ∈ {{geodesic, non_geodesic}}: {self.strategy}"
            )

        if not self.label:
            self.label = self.auto_label()

    # ── Otomatik etiket ──────────────────────────────────────────────────────

    def auto_label(self) -> str:
        """
        Katman için kısa otomatik etiket üret.
        Örnek: 'L7_Helical_+45.0deg', 'L3_Hoop_89.5deg'.
        """
        sign = "+" if self.alpha_deg >= 0 else ""
        tip_map = {
            LayerType.HELICAL:     "Helical",
            LayerType.HOOP:        "Hoop",
            LayerType.POLAR:       "Polar",
            LayerType.TRANSITION:  "Transition",
            LayerType.SKIN_FINISH: "Skin",
        }
        tip = tip_map.get(self.type, "Layer")
        return f"L{self.id}_{tip}_{sign}{self.alpha_deg:.1f}deg"

    # ── Türetilmiş özellikler ────────────────────────────────────────────────

    @property
    def alpha_abs_rad(self) -> float:
        """|α| radyan cinsinden."""
        return math.radians(abs(self.alpha_deg))

    @property
    def alpha_signed_rad(self) -> float:
        """İşaretli α radyan."""
        return math.radians(self.alpha_deg)

    def is_balanced_pair(self, other: "LayerSpec") -> bool:
        """İki katman ±α dengesi oluşturuyor mu kontrol et."""
        return (
            self.type == other.type == LayerType.HELICAL
            and abs(self.alpha_deg + other.alpha_deg) < 1e-3
            and abs(self.thickness_mm - other.thickness_mm) < 1e-6
        )

    def to_dict(self) -> Dict[str, Any]:
        """Proje dosyasına serileştirme için sözlük."""
        d = asdict(self)
        d["type"] = self.type.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LayerSpec":
        d2 = dict(d)
        d2["type"] = LayerType(d2.get("type", "helical"))
        return cls(**d2)


@dataclass
class LayerValidation:
    """
    Tek katmanın doğrulama sonucu.

    passes          : Tüm sınırları geçti mi
    max_slip_ratio  : max(|kg|/kn)/μ; 1.0 üzeri = kayma
    warning_msg     : Hata/uyarı açıklaması (TR)
    """
    passes: bool
    max_slip_ratio: float
    warning_msg: str = ""

    @property
    def status_color(self) -> str:
        """UI'da satır rengi: 'green' | 'yellow' | 'red'."""
        if not self.passes:
            return "red"
        if self.max_slip_ratio > 0.85:
            return "yellow"
        return "green"


@dataclass
class PitchInfo:
    """
    Bir katmanın geometrik adım/devre bilgisi.
    """
    pitch_mm: float                 # eksenel adım
    n_circuits: int                 # devre sayısı (kapsama için)
    effective_coverage_pct: float   # gerçek yüzey kapsama %
    band_axial_step_mm: float       # bandın net eksenel ilerlemesi (= pitch · (1−çakışma))
    warning: str = ""


@dataclass
class BuildUpResult:
    """
    `calculate_build_up()` çıktısı.

    z_mm        : Eksen koordinatları
    r_pre_mm    : Katman öncesi yarıçap
    r_post_mm   : Katman sonrası yarıçap (yumuşatılmış)
    delta_r_mm  : Lokal Δr profili (pre → post)
    delta_r_mean: Ortalama Δr (raporlama)
    """
    z_mm: np.ndarray
    r_pre_mm: np.ndarray
    r_post_mm: np.ndarray
    delta_r_mm: np.ndarray
    delta_r_mean: float


# ══════════════════════════════════════════════════════════════════════════════
# Çekirdek matematik fonksiyonları (sınıf dışı — birim test edilebilir)
# ══════════════════════════════════════════════════════════════════════════════

def calculate_pitch_and_circuits(
    spec: LayerSpec,
    mandrel_diameter_mm: float,
) -> PitchInfo:
    """
    Helisel/hoop katmanın bant adımını ve devre sayısını hesapla.

    pitch_mm   = fitil_genisligi / sin(|α|)
    n_circuits = ⌈ π·D / (pitch · (1 − çakışma)) ⌉

    α = 0 ve sin(α) çok küçük durumlar için güvenli sınır değer atanır.
    """
    try:
        if mandrel_diameter_mm <= 0:
            raise ValueError(
                f"mandrel_diameter_mm > 0: {mandrel_diameter_mm}"
            )

        sin_a = math.sin(spec.alpha_abs_rad)

        warn = ""
        if sin_a < _EPS_SIN:
            # α ≈ 0° pratikte saf eksenel; bant adımı sınırsızı sınırla
            sin_a = _EPS_SIN
            warn = "α ≈ 0°, pitch güvenli sınır değere kırpıldı"

        pitch = spec.fitil_genisligi_mm / sin_a

        cakisma_frac = max(0.0, min(0.95, spec.cakisma_pct / 100.0))
        net_band_step = pitch * (1.0 - cakisma_frac)
        if net_band_step <= 0:
            net_band_step = pitch  # güvenli fallback

        perim = math.pi * mandrel_diameter_mm
        n_circuits = max(1, math.ceil(perim / max(net_band_step, 1e-9)))

        # Gerçek kapsama (devre sayısı yuvarlanır → genelde >100% küçük artış)
        coverage = (n_circuits * net_band_step / perim) * 100.0
        coverage = min(coverage, 100.0 * (1.0 / max(1 - cakisma_frac, 1e-9)))

        return PitchInfo(
            pitch_mm=pitch,
            n_circuits=n_circuits,
            effective_coverage_pct=coverage,
            band_axial_step_mm=net_band_step,
            warning=warn,
        )

    except Exception as exc:
        return PitchInfo(
            pitch_mm=0.0,
            n_circuits=0,
            effective_coverage_pct=0.0,
            band_axial_step_mm=0.0,
            warning=f"Pitch hesaplama hatası: {exc}",
        )


def _moving_average(arr: np.ndarray, window: int = _DEFAULT_SMOOTH_W) -> np.ndarray:
    """
    Hareketli ortalama yumuşatma. Uçlarda boundary clamp (kenar değer tekrar).

    Window tek sayı olmalı; çift verilirse +1 yapılır.
    """
    n = len(arr)
    if n < 2 or window <= 1:
        return np.asarray(arr, dtype=np.float64).copy()

    if window % 2 == 0:
        window += 1
    w = min(window, n if n % 2 == 1 else n - 1)
    if w < 3:
        return np.asarray(arr, dtype=np.float64).copy()

    half = w // 2
    a = np.asarray(arr, dtype=np.float64)
    # Reflect (kenar yansıması) ile padding
    padded = np.pad(a, half, mode="edge")
    kernel = np.ones(w, dtype=np.float64) / float(w)
    smoothed = np.convolve(padded, kernel, mode="valid")
    return smoothed[:n]


def calculate_build_up(
    spec: LayerSpec,
    mandrel_profile: "MandrelProfile",
    smoothing_window: int = _DEFAULT_SMOOTH_W,
) -> BuildUpResult:
    """
    Mandrel profili üzerine bir katman serildiğinde yarıçap artışını hesapla.

    Hoop için Δr = t_ply (eksenel projeksiyon ≈ ply kalınlığı).
    Helisel için Δr(z) = t_ply / cos(α_lokal(z)); α_lokal Clairaut'tan türetilir.
    Lift-off (cos→0) bölgelerinde hoop sınırına kırpılır.

    Sonuçta tüm profile (window=5) hareketli ortalama uygulanarak kubbe-silindir
    geçişindeki sıçramalar yumuşatılır.
    """
    z = np.asarray(mandrel_profile.z_mm, dtype=np.float64)
    r_pre = np.asarray(mandrel_profile.r_mm, dtype=np.float64).copy()
    n = len(z)
    t = spec.thickness_mm

    if n < 2:
        return BuildUpResult(
            z_mm=z, r_pre_mm=r_pre, r_post_mm=r_pre.copy(),
            delta_r_mm=np.zeros_like(r_pre), delta_r_mean=0.0,
        )

    try:
        # Hoop / Skin: doğrudan eksenel projeksiyon
        if spec.type in (LayerType.HOOP, LayerType.SKIN_FINISH):
            delta = np.full(n, t, dtype=np.float64)

        else:
            # Clairaut sabiti: c = r_eq · sin(α_eq), eşit-yarıçap noktasında
            # (silindir bölgesinde r ≈ sabit, dome'da r azalır → α artar).
            r_ref = float(np.median(r_pre))
            c0 = r_ref * math.sin(spec.alpha_abs_rad)

            delta = np.empty(n, dtype=np.float64)
            for i, ri in enumerate(r_pre):
                # arcsin domain protect: c0 > r ise lift-off → hoop davranışı
                if ri <= 1e-6 or c0 >= ri:
                    delta[i] = t  # hoop sınırına kırp
                    continue
                sin_a_local = c0 / ri
                cos_a_local = math.sqrt(max(0.0, 1.0 - sin_a_local * sin_a_local))
                if cos_a_local < _EPS_COS:
                    delta[i] = t / _EPS_COS  # üst sınır
                else:
                    delta[i] = t / cos_a_local

            # Aşırı yığılma kırpma: praktik üst sınır ~ 10·t
            delta = np.clip(delta, t, 10.0 * t)

        # Yığılma profili yumuşatma (kubbe bölgesindeki sıçramalar için)
        delta_smooth = _moving_average(delta, window=smoothing_window)

        r_post = r_pre + delta_smooth
        delta_mean = float(np.mean(delta_smooth))

        return BuildUpResult(
            z_mm=z,
            r_pre_mm=r_pre,
            r_post_mm=r_post,
            delta_r_mm=delta_smooth,
            delta_r_mean=delta_mean,
        )

    except Exception as exc:
        # Güvenli fallback: lokal artış yok
        return BuildUpResult(
            z_mm=z,
            r_pre_mm=r_pre,
            r_post_mm=r_pre.copy(),
            delta_r_mm=np.zeros_like(r_pre),
            delta_r_mean=0.0,
        )


def suggest_anti_symmetric_pair(layer_spec: LayerSpec,
                                next_id: Optional[int] = None) -> Optional[LayerSpec]:
    """
    Helisel +α katmana karşı simetrik −α katman önerisi üret.

    Burulma gerilmelerini sönümlemek için her +α helisel katman bir −α
    eşiyle dengelenmelidir (balanced laminate kuralı).

    Sadece HELICAL tipi katmanlar için dönüş üretir; aksi halde None.
    """
    if layer_spec.type != LayerType.HELICAL:
        return None
    if abs(layer_spec.alpha_deg) < _MIN_ALPHA_DEG:
        return None

    new_id = next_id if next_id is not None else (layer_spec.id + 1)
    paired = LayerSpec(
        id=new_id,
        type=LayerType.HELICAL,
        alpha_deg=-layer_spec.alpha_deg,
        fitil_genisligi_mm=layer_spec.fitil_genisligi_mm,
        cakisma_pct=layer_spec.cakisma_pct,
        thickness_mm=layer_spec.thickness_mm,
        feed_mm_s=layer_spec.feed_mm_s,
        spindle_rpm=layer_spec.spindle_rpm,
        friction_mu=layer_spec.friction_mu,
        strategy=layer_spec.strategy,
        label="",  # auto
        notes=f"{layer_spec.label} ile dengelenmiş denge çifti",
    )
    return paired


# ══════════════════════════════════════════════════════════════════════════════
# LayerStack — UI tablosuyla birebir eşleşen orchestratör
# ══════════════════════════════════════════════════════════════════════════════

class LayerStack:
    """
    Manuel katman dizilim yöneticisi.

    UI'daki QTableWidget satırlarıyla 1-1 eşleşir:
      - Tablo satırı = listedeki indeks
      - add_layer        → tablonun sonuna ekler
      - insert_layer     → belirli satıra ekler
      - remove_layer     → satır siler
      - move_layer       → satır yukarı/aşağı kaydırır

    Tüm operasyonlar atomic; hata durumunda yığın değişmez.
    """

    def __init__(self, default_friction_mu: float = _DEFAULT_FRICTION) -> None:
        self.layers: List[LayerSpec] = []
        self._next_id: int = 1
        self.default_friction_mu: float = float(default_friction_mu)

    # ── Genel sorgular ───────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self.layers)

    def __iter__(self):
        return iter(self.layers)

    def __getitem__(self, idx: int) -> LayerSpec:
        return self.layers[idx]

    @property
    def n_layers(self) -> int:
        return len(self.layers)

    def total_thickness_mm(self) -> float:
        """Tüm katmanların nominal toplam kalınlığı."""
        return sum(L.thickness_mm for L in self.layers)

    def total_helical_thickness_mm(self) -> float:
        return sum(L.thickness_mm for L in self.layers
                   if L.type == LayerType.HELICAL)

    def total_hoop_thickness_mm(self) -> float:
        return sum(L.thickness_mm for L in self.layers
                   if L.type in (LayerType.HOOP, LayerType.SKIN_FINISH))

    def find_index_by_id(self, layer_id: int) -> Optional[int]:
        for i, L in enumerate(self.layers):
            if L.id == layer_id:
                return i
        return None

    # ── ID üreteci ───────────────────────────────────────────────────────────

    def _new_id(self) -> int:
        nid = self._next_id
        self._next_id += 1
        return nid

    # ── Katman fabrikaları ───────────────────────────────────────────────────

    def make_helical(self,
                     alpha_deg: float = 45.0,
                     fitil_genisligi_mm: float = 6.0,
                     cakisma_pct: float = 5.0,
                     thickness_mm: float = 0.30,
                     feed_mm_s: float = 80.0,
                     spindle_rpm: float = 60.0,
                     friction_mu: Optional[float] = None,
                     strategy: str = "geodesic",
                     notes: str = "") -> LayerSpec:
        """Varsayılan helisel katman üretimi (UI butonu için)."""
        return LayerSpec(
            id=self._new_id(),
            type=LayerType.HELICAL,
            alpha_deg=alpha_deg,
            fitil_genisligi_mm=fitil_genisligi_mm,
            cakisma_pct=cakisma_pct,
            thickness_mm=thickness_mm,
            feed_mm_s=feed_mm_s,
            spindle_rpm=spindle_rpm,
            friction_mu=friction_mu if friction_mu is not None
                        else self.default_friction_mu,
            strategy=strategy,
            label="",
            notes=notes,
        )

    def make_hoop(self,
                  alpha_deg: float = 89.5,
                  fitil_genisligi_mm: float = 6.0,
                  cakisma_pct: float = 10.0,
                  thickness_mm: float = 0.30,
                  feed_mm_s: float = 60.0,
                  spindle_rpm: float = 80.0,
                  friction_mu: Optional[float] = None,
                  notes: str = "") -> LayerSpec:
        """Varsayılan hoop/çember katman."""
        return LayerSpec(
            id=self._new_id(),
            type=LayerType.HOOP,
            alpha_deg=alpha_deg,
            fitil_genisligi_mm=fitil_genisligi_mm,
            cakisma_pct=cakisma_pct,
            thickness_mm=thickness_mm,
            feed_mm_s=feed_mm_s,
            spindle_rpm=spindle_rpm,
            friction_mu=friction_mu if friction_mu is not None
                        else self.default_friction_mu,
            strategy="geodesic",
            label="",
            notes=notes,
        )

    def make_polar(self,
                   alpha_deg: float = 12.0,
                   fitil_genisligi_mm: float = 4.0,
                   cakisma_pct: float = 0.0,
                   thickness_mm: float = 0.20,
                   feed_mm_s: float = 50.0,
                   spindle_rpm: float = 30.0,
                   friction_mu: Optional[float] = None,
                   notes: str = "") -> LayerSpec:
        """Varsayılan polar (kubbe sarımı) katman."""
        return LayerSpec(
            id=self._new_id(),
            type=LayerType.POLAR,
            alpha_deg=alpha_deg,
            fitil_genisligi_mm=fitil_genisligi_mm,
            cakisma_pct=cakisma_pct,
            thickness_mm=thickness_mm,
            feed_mm_s=feed_mm_s,
            spindle_rpm=spindle_rpm,
            friction_mu=friction_mu if friction_mu is not None
                        else self.default_friction_mu,
            strategy="non_geodesic",
            label="",
            notes=notes,
        )

    def make_skin(self,
                  alpha_deg: float = 88.0,
                  fitil_genisligi_mm: float = 6.0,
                  cakisma_pct: float = 15.0,
                  thickness_mm: float = 0.15,
                  feed_mm_s: float = 60.0,
                  spindle_rpm: float = 70.0,
                  friction_mu: Optional[float] = None,
                  notes: str = "") -> LayerSpec:
        """Bitiş/kabuk koruyucu katman."""
        return LayerSpec(
            id=self._new_id(),
            type=LayerType.SKIN_FINISH,
            alpha_deg=alpha_deg,
            fitil_genisligi_mm=fitil_genisligi_mm,
            cakisma_pct=cakisma_pct,
            thickness_mm=thickness_mm,
            feed_mm_s=feed_mm_s,
            spindle_rpm=spindle_rpm,
            friction_mu=friction_mu if friction_mu is not None
                        else self.default_friction_mu,
            strategy="geodesic",
            label="",
            notes=notes,
        )

    # ── CRUD operasyonları ───────────────────────────────────────────────────

    def add_layer(self, spec: LayerSpec) -> int:
        """Yığının sonuna ekle, eklenen satır indeksini döndür."""
        if not isinstance(spec, LayerSpec):
            raise TypeError(f"LayerSpec bekleniyor: {type(spec).__name__}")
        # ID çatışması varsa yeni ID ata
        if any(L.id == spec.id for L in self.layers):
            spec.id = self._new_id()
            spec.label = spec.auto_label()
        else:
            self._next_id = max(self._next_id, spec.id + 1)
        self.layers.append(spec)
        return len(self.layers) - 1

    def insert_layer(self, index: int, spec: LayerSpec) -> int:
        """Belirli satıra ekle. Negatif index Python listesi semantiği."""
        if not isinstance(spec, LayerSpec):
            raise TypeError(f"LayerSpec bekleniyor: {type(spec).__name__}")
        if not self.layers:
            return self.add_layer(spec)

        n = len(self.layers)
        # Range kontrolü (append'e izin ver)
        if index < 0:
            index = max(0, n + index + 1)
        index = min(index, n)

        if any(L.id == spec.id for L in self.layers):
            spec.id = self._new_id()
            spec.label = spec.auto_label()
        else:
            self._next_id = max(self._next_id, spec.id + 1)

        self.layers.insert(index, spec)
        return index

    def remove_layer(self, index: int) -> LayerSpec:
        """Satırı sil, silinen LayerSpec'i döndür."""
        if not (0 <= index < len(self.layers)):
            raise IndexError(f"Geçersiz satır indeksi: {index}")
        return self.layers.pop(index)

    def move_layer(self, from_index: int, to_index: int) -> None:
        """
        Katmanın sırasını değiştir.

        Negatif indeks Python semantiği. UI [Yukarı]/[Aşağı] butonları için:
          - move_layer(i, i-1)  → yukarı (üst sıraya)
          - move_layer(i, i+1)  → aşağı (alt sıraya)
        """
        n = len(self.layers)
        if n < 2:
            return
        if from_index < 0:
            from_index += n
        if to_index < 0:
            to_index += n
        if not (0 <= from_index < n):
            raise IndexError(f"from_index ∈ [0, {n}): {from_index}")
        to_index = max(0, min(n - 1, to_index))
        if from_index == to_index:
            return
        spec = self.layers.pop(from_index)
        self.layers.insert(to_index, spec)

    def clear(self) -> None:
        """Tüm katmanları sil."""
        self.layers.clear()
        self._next_id = 1

    def replace_layer(self, index: int, spec: LayerSpec) -> None:
        """Mevcut satırın LayerSpec'ini değiştir (UI hücre düzenlemesi sonrası)."""
        if not (0 <= index < len(self.layers)):
            raise IndexError(f"Geçersiz satır indeksi: {index}")
        if not isinstance(spec, LayerSpec):
            raise TypeError(f"LayerSpec bekleniyor: {type(spec).__name__}")
        # ID'yi koru
        old_id = self.layers[index].id
        spec.id = old_id
        spec.label = spec.auto_label() if not spec.notes.strip() else spec.label
        self.layers[index] = spec

    # ── Denge çifti otomatik öner ────────────────────────────────────────────

    def auto_suggest_anti_symmetric_pair(self,
                                          layer_spec: LayerSpec
                                          ) -> Optional[LayerSpec]:
        """
        Verilen helisel +α katman için karşılık gelen −α katmanı üretir.

        Sadece HELICAL katmanlar için döner; aksi halde None.
        Yığına eklenmez — UI bunu kullanıcıya öneri olarak gösterip
        kullanıcı onayıyla `add_layer()` çağırmalıdır.
        """
        return suggest_anti_symmetric_pair(layer_spec, next_id=self._next_id)

    def add_balanced_helical_pair(self,
                                   alpha_deg: float = 45.0,
                                   **kwargs) -> Tuple[int, int]:
        """
        Tek seferde ±α helisel çift ekle.

        UI'da "Dengeli Helisel Çift Ekle" butonu için kısayol.
        Dönüş: (+α satır indeksi, −α satır indeksi).
        """
        plus = self.make_helical(alpha_deg=+abs(alpha_deg), **kwargs)
        i1 = self.add_layer(plus)
        minus = self.make_helical(alpha_deg=-abs(alpha_deg), **kwargs)
        minus.notes = f"{plus.label} ile dengelenmiş denge çifti"
        i2 = self.add_layer(minus)
        return i1, i2

    # ── Doğrulama ────────────────────────────────────────────────────────────

    def validate_layer_slippage(
        self,
        index: int,
        mandrel_profile: "MandrelProfile",
    ) -> LayerValidation:
        """
        Tek katman için non-jeodezik kayma şartını kontrol et.

        Şart: |k_g| / k_n ≤ μ  (eşdeğer slip_ratio = |λ|/μ ≤ 1).

        `core.non_geodesic_engine.solve_non_geodesic_path` çağrısı yapılır;
        mevcut değilse veya hata oluşursa Clairaut-tabanlı basit lokal
        analiz (saf jeodezikten sapma yok varsayımı) uygulanır.

        Hoop / skin katmanları kontrol kapsamı dışıdır (sabit α ≈ 90°,
        kayma teorik olarak yok kabul edilir) — passes=True, slip=0 döner.
        """
        if not (0 <= index < len(self.layers)):
            raise IndexError(f"Geçersiz satır indeksi: {index}")

        spec = self.layers[index]

        # Hoop / skin: kayma kontrolü atla
        if spec.type in (LayerType.HOOP, LayerType.SKIN_FINISH):
            return LayerValidation(
                passes=True, max_slip_ratio=0.0,
                warning_msg="Hoop/Skin: sabit α ≈ 90°, kayma kontrolü uygulanmaz",
            )

        # Jeodezik strateji: λ = 0, kayma yok
        if spec.strategy == "geodesic":
            try:
                slip_ratio = self._estimate_geodesic_slip_drift(spec, mandrel_profile)
            except Exception:
                slip_ratio = 0.0
            return LayerValidation(
                passes=True, max_slip_ratio=slip_ratio,
                warning_msg="Jeodezik yol — Clairaut korunumu varsayılır",
            )

        # Non-jeodezik: gerçek çözücüyü çağır
        try:
            from .non_geodesic_engine import (
                NonGeodesicParams, solve_non_geodesic_path,
            )
            # Sürtünme sınırında çalış (λ = μ) — en agresif test
            params = NonGeodesicParams(
                profile=mandrel_profile,
                alpha_start_deg=abs(spec.alpha_deg),
                lambda_slip=spec.friction_mu,
                friction_coefficient=spec.friction_mu,
                integration_step_mm=1.0,
                n_circuits=1,
            )
            report = solve_non_geodesic_path(params)
            slip = float(report.max_slip_ratio)
            passes = bool(report.is_slip_safe) and not report.lift_off_detected
            if not passes:
                msg = (f"Kayma Riski: |kg/kn| = {slip:.3f} > 1.0 "
                       f"(μ = {spec.friction_mu:.2f})")
                if report.lift_off_detected:
                    msg += " · Lift-off algılandı"
            else:
                msg = f"Sürtünme sınırı dahilinde (slip = {slip:.3f})"
            return LayerValidation(passes=passes, max_slip_ratio=slip,
                                   warning_msg=msg)

        except ImportError:
            # Engine yoksa konservatif tahmin: |α| > 65° ise riskli
            risky = abs(spec.alpha_deg) > 65.0
            slip = (abs(spec.alpha_deg) - 45.0) / 45.0 if risky else 0.3
            return LayerValidation(
                passes=not risky,
                max_slip_ratio=max(0.0, slip),
                warning_msg=("non_geodesic_engine yüklenemedi — "
                             "konservatif tahmin"),
            )
        except Exception as exc:
            return LayerValidation(
                passes=False, max_slip_ratio=float("nan"),
                warning_msg=f"Doğrulama hatası: {exc}",
            )

    def _estimate_geodesic_slip_drift(
        self,
        spec: LayerSpec,
        mandrel_profile: "MandrelProfile",
    ) -> float:
        """
        Jeodezik yol için Clairaut c = r·sin(α) sapma metriği.

        Eğer Clairaut sabiti uygulanabilir değilse (c > r_min ⟹ lift-off)
        slip_ratio > 1 döndürür.
        """
        r = np.asarray(mandrel_profile.r_mm, dtype=np.float64)
        if r.size == 0:
            return 0.0
        r_ref = float(np.median(r))
        c0 = r_ref * math.sin(spec.alpha_abs_rad)
        r_min = float(r.min())
        if r_min <= 0:
            return 1.0
        # c > r_min ⟹ lift-off olası; oranı slip-benzeri ölç
        return max(0.0, c0 / r_min - 1.0) if c0 > r_min else 0.0

    def validate_all(
        self,
        mandrel_profile: "MandrelProfile",
    ) -> List[LayerValidation]:
        """Tüm katmanları sırasıyla doğrula."""
        return [
            self.validate_layer_slippage(i, mandrel_profile)
            for i in range(len(self.layers))
        ]

    # ── Pitch / build-up köprüleri ───────────────────────────────────────────

    def pitch_info(self, index: int,
                   mandrel_diameter_mm: float) -> PitchInfo:
        if not (0 <= index < len(self.layers)):
            raise IndexError(f"Geçersiz satır indeksi: {index}")
        return calculate_pitch_and_circuits(
            self.layers[index], mandrel_diameter_mm
        )

    def apply_build_up(
        self,
        mandrel_profile: "MandrelProfile",
        smoothing_window: int = _DEFAULT_SMOOTH_W,
    ) -> Tuple["MandrelProfile", List[BuildUpResult]]:
        """
        Tüm katmanları sırasıyla mandrel üzerine ser, güncel profili ve
        her katmanın BuildUpResult listesini döndür.

        Mevcut mandrel_profile DEĞİŞTİRİLMEZ — yeni bir MandrelProfile
        nesnesi oluşturulup döndürülür.
        """
        from .geometry_engine import MandrelProfile  # döngüsel import kaçınma

        current_z = np.asarray(mandrel_profile.z_mm, dtype=np.float64).copy()
        current_r = np.asarray(mandrel_profile.r_mm, dtype=np.float64).copy()
        results: List[BuildUpResult] = []

        for spec in self.layers:
            try:
                temp_profile = MandrelProfile(z_mm=current_z, r_mm=current_r)
                bu = calculate_build_up(
                    spec, temp_profile, smoothing_window=smoothing_window
                )
                current_r = bu.r_post_mm.copy()
                results.append(bu)
            except Exception:
                # Bir katmanda hata olursa profili sabit tut, raporda boş ekle
                results.append(BuildUpResult(
                    z_mm=current_z.copy(),
                    r_pre_mm=current_r.copy(),
                    r_post_mm=current_r.copy(),
                    delta_r_mm=np.zeros_like(current_r),
                    delta_r_mean=0.0,
                ))

        new_profile = MandrelProfile(z_mm=current_z, r_mm=current_r)
        return new_profile, results

    # ══════════════════════════════════════════════════════════════════════════
    # CLT entegrasyon köprüsü
    # ══════════════════════════════════════════════════════════════════════════

    def to_clt_stackup_data(self) -> List[Tuple[float, float]]:
        """
        CLT modülüne (clt_engine.LaminateStackup) gönderilecek minimum veri.

        Dönüş: liste of (angle_deg, thickness_mm) tuple
          [
            (+45.0, 0.30),
            (-45.0, 0.30),
            (90.0, 0.30),
            ...
          ]

        Bu liste `Ply(angle_deg, thickness_mm, lamina)` kurucusuna doğrudan
        beslenebilir. Lamina malzemesi katman dışı bilgi olduğundan UI veya
        çağıran kod tarafından eklenir.
        """
        out: List[Tuple[float, float]] = []
        for L in self.layers:
            out.append((float(L.alpha_deg), float(L.thickness_mm)))
        return out

    def to_clt_stackup(
        self,
        lamina: "LaminaProperties",
    ) -> "LaminateStackup":
        """
        Doğrudan `LaminateStackup` nesnesi üret (lamina malzeme bilgisi
        verilmişse).

        Tipik kullanım:
            from backend.core.material_allowables import get_lamina
            stack = layer_stack.to_clt_stackup(get_lamina('t700s_epoxy'))
            abd   = compute_ABD(stack)
        """
        from .clt_engine import LaminateStackup, Ply
        plies: List[Ply] = []
        for angle_deg, t_mm in self.to_clt_stackup_data():
            try:
                plies.append(Ply(
                    angle_deg=angle_deg,
                    thickness_mm=t_mm,
                    lamina=lamina,
                ))
            except Exception:
                continue
        return LaminateStackup(plies=plies)

    # ══════════════════════════════════════════════════════════════════════════
    # Serileştirme (proje dosyası entegrasyonu)
    # ══════════════════════════════════════════════════════════════════════════

    def to_dict(self) -> Dict[str, Any]:
        """JSON proje dosyasına yazılabilir sözlük."""
        return {
            "versiyon": "1.0",
            "default_friction_mu": self.default_friction_mu,
            "next_id": self._next_id,
            "layers": [L.to_dict() for L in self.layers],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LayerStack":
        """Sözlükten LayerStack yeniden inşa et."""
        stack = cls(default_friction_mu=float(d.get("default_friction_mu",
                                                     _DEFAULT_FRICTION)))
        for ld in d.get("layers", []):
            try:
                spec = LayerSpec.from_dict(ld)
                stack.layers.append(spec)
                stack._next_id = max(stack._next_id, spec.id + 1)
            except Exception:
                continue
        stack._next_id = max(stack._next_id, int(d.get("next_id",
                                                       stack._next_id)))
        return stack

    # ══════════════════════════════════════════════════════════════════════════
    # Özet rapor
    # ══════════════════════════════════════════════════════════════════════════

    def summary(self) -> str:
        n_hel = sum(1 for L in self.layers if L.type == LayerType.HELICAL)
        n_hoop = sum(1 for L in self.layers if L.type == LayerType.HOOP)
        n_pol = sum(1 for L in self.layers if L.type == LayerType.POLAR)
        n_trn = sum(1 for L in self.layers if L.type == LayerType.TRANSITION)
        n_skn = sum(1 for L in self.layers if L.type == LayerType.SKIN_FINISH)
        t_tot = self.total_thickness_mm()
        return (
            f"LayerStack özeti — {len(self.layers)} katman | "
            f"Helisel: {n_hel} | Hoop: {n_hoop} | Polar: {n_pol} | "
            f"Geçiş: {n_trn} | Bitiş: {n_skn} | "
            f"Toplam kalınlık: {t_tot:.3f} mm"
        )


# ── Modülün dışa açık API'si ─────────────────────────────────────────────────

__all__ = [
    "LayerType",
    "LayerSpec",
    "LayerValidation",
    "PitchInfo",
    "BuildUpResult",
    "LayerStack",
    "calculate_pitch_and_circuits",
    "calculate_build_up",
    "suggest_anti_symmetric_pair",
]
