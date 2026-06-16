# MandrelModel — Veri Modeli Tasarım Belgesi

> **Durum:** Veri modeli tasarımı — kod YOK, implementasyon YOK.
> **Tarih:** 2026-06-16 · **Branch:** `claude/amazing-feynman-XUXBf`
> **Bağlam:** STL_CEKIRDEK_ENTEGRASYON_RAPORU §8 onayı — Seçenek C (`MandrelModel`
> kompozit). `MandrelProfile` saf geometri kalır; `MandrelModel` STL zekâsını
> (özellikle confidence) pipeline boyunca taşır.

Bölümler: 1) Sınıf alanları · 2) Yaşam döngüsü · 3) Serialization ·
4) Backward compatibility · 5) UML · 6) Risk analizi.

---

## 1. MandrelModel Sınıfı ve Alt Yapılar

> **Tasarım ilkesi:** `MandrelModel` bir **kompozisyon kökü** (veri konteyneri).
> İş mantığı içermez — analiz `stl_intelligence`'de kalır. Tek işi: sade
> geometriyi + onun zekâsını bir arada, kayıpsız taşımak.

### 1.0 — Çekirdek nesne

```
MandrelModel:
    # ── Zorunlu çekirdek ──────────────────────────────────────────────
    profile: MandrelProfile          # SADE geometri (downstream sözleşmesi)
    source_type: str                 # "stl" | "parametric"

    # ── STL zekâsı (opsiyonel — parametrik profilde None olabilir) ─────
    axis: Optional[AxisResult]            # eksen yönü + merkez + güven metrikleri
    confidence: Optional[ConfidenceReport]# 4 alt-skor + agregat + grade
    quality: Optional[QualityReport]      # winding uygunluk verdict + nedenler
    segments: Optional[RegionSegmentation]# cylinder/dome/taper span'leri
    pole_regions: Optional[List[PoleRegion]]      # kutup bölgeleri (r→0)
    turnaround_candidates: Optional[TurnaroundCandidates]  # α→(z_left,z_right,c)

    # ── Metadata ──────────────────────────────────────────────────────
    source_path: Optional[str]       # STL dosya yolu (parametrikte None)
    units: str = "mm"                # birim (mm varsayılan; F9 birim şüphesi)
    model_version: int = 1           # şema evrimi için
    created_at: Optional[str]        # ISO zaman damgası

    # ── Davranış (iş mantığı DEĞİL — erişim/kapı) ─────────────────────
    def as_profile() -> MandrelProfile        # downstream'e sade profil ver
    def is_winding_ready() -> bool            # confidence/quality kapısı
    def cylinder_segments() -> List[Segment]  # segments içinden kolay erişim
    def dome_segments() -> List[Segment]      # segments içinden kolay erişim
    def to_dict() -> dict / from_dict(d)      # serialization
    @classmethod from_stl(report) / from_parametric(profile)
```

> Not: Kullanıcının listelediği `cylinder_segments` / `dome_segments` ayrı alan
> yerine `segments: RegionSegmentation` içinde tutulur; `.cylinder_segments()` /
> `.dome_segments()` **convenience erişimcileridir**. Gerekçe: tek kaynak
> (segments) → tutarsızlık (cylinder ve dome listelerinin senkron kalmaması) riski yok.

### 1.1 — Alt yapılar (STL Intelligence çıktıları)

```
AxisResult:
    axis_unit: np.ndarray        # (3,) birim vektör — dönme ekseni
    center:    np.ndarray        # (3,) eksen üzerinde bir nokta (centroid)
    j_min:     float             # simetri maliyeti (küçük = iyi)
    eig_ratios: Tuple[float,float,float]  # λ oranları (dejenerasyon teşhisi)
    degeneracy_flag: bool        # küre/küresel-en-boy uyarısı

Segment:
    z_start_mm: float
    z_end_mm:   float
    kind: str                    # "cylinder" | "dome" | "taper"
    r_mean_mm: float
    dr_dz_mean: float            # bölge eğimi (bilgi/görsel)

PoleRegion:
    side: str                    # "left" | "right"
    z_start_mm: float
    z_end_mm:   float
    min_radius_mm: float         # kutup açıklığı yarıçapı (clamp sonrası)

TurnaroundCandidate:
    alpha_deg: float
    z_left_mm: float
    z_right_mm: float
    polar_radius_c_mm: float     # c = r_max·sin(α)
    reachable: bool              # z_right > z_left

TurnaroundCandidates:
    by_alpha: Dict[float, TurnaroundCandidate]

ConfidenceReport:
    axis: float                  # 0..1
    symmetry: float              # 0..1
    mesh_density: float          # 0..1
    radial_fit: float            # 0..1
    aggregate: float             # 0..1 ağırlıklı toplam
    grade: str                   # "YÜKSEK" | "ORTA" | "DÜŞÜK" | "REDDET"

QualityReport:
    aspect_ratio: float          # L / (2·r_max)
    min_radius_mm: float
    is_single_valued: bool       # body-of-revolution mu (torus testi)
    asymmetry_mm: float
    degenerate_tri_count: int
    mesh_density: float
    winding_suitable: bool
    reasons: List[str]           # başarısız kriter açıklamaları (TR)
```

---

## 2. Yaşam Döngüsü (Hangi modül hangi alanı kullanır)

```
[STL dosyası]
   │ parse_stl_vertices (stl_processor — MEVCUT)
   ▼
[STL Intelligence: analyze_stl]
   │ → AxisResult, RadiusProfile, RegionSegmentation,
   │   PoleRegion[], TurnaroundCandidates, ConfidenceReport, QualityReport
   ▼
[MandrelModel.from_stl(report)]   ← tüm zekâ burada toplanır, kayıp yok
   │
   ├─► Geodesic (path_generator)
   ├─► Coverage (coverage_solver)
   ├─► TwinState (winding_twin)
   └─► Renderer (UI 3D)
```

| Modül | Okuduğu MandrelModel alanları | Nasıl kullanır |
|---|---|---|
| **STL Intelligence** | *(üretir)* | `from_stl()` ile modeli kurar |
| **Geodesic** (`path_generator`) | `as_profile()`, `turnaround_candidates`, `confidence` (kapı) | `r(z)`'den `c, _geodesic_pass`; turnaround cache; düşük güvende girme |
| **Coverage** (`coverage_solver`) | `as_profile()`, `axis` (hizalama), `confidence` (kapı) | yüzey ızgarası + bant boyama; güven düşükse heatmap'i "şüpheli" işaretle |
| **TwinState** (`winding_twin`) | `as_profile()`, `turnaround_candidates`, `segments`/`pole_regions` (dome davranışı), `confidence` (kapı) | layer buildup + timeline; dome'da turnaround; güven kapısı |
| **Renderer** (UI 3D) | `axis`, `as_profile()`, `segments`, `pole_regions`, `confidence`, `quality` | gerçek eksen çiz; dome/pole işaretle; güven/uygunluk rozetini göster |

**Kritik kural:** Backend modülleri (geodesic/coverage/twin) modelden **yalnız
`as_profile()` + birkaç skaler** okur — sade profil sözleşmesi korunur. Zengin
alanlar (segments, quality) ağırlıkla **UI/render** tarafından tüketilir.
`confidence` ise **her aşamada kapı** olarak okunur (sessiz hata önleme).

---

## 3. Serialization

### 3.1 — JSON'a yazılabilir mi? → **Evet** (numpy dönüşümüyle)

```
to_dict():
    profile      → {"z_mm": list, "r_mm": list}        # np → list
    axis         → {"axis_unit": list, "center": list, "j_min": float,
                    "eig_ratios": list, "degeneracy_flag": bool}
    segments     → [{"z_start_mm","z_end_mm","kind","r_mean_mm","dr_dz_mean"}...]
    pole_regions → [{...}...]
    turnaround   → {"by_alpha": {"55.0": {...}}}        # anahtar str
    confidence   → {...skaler...}
    quality      → {...skaler + reasons list...}
    metadata     → source_type, source_path, units, model_version, created_at
```
Tüm alanlar skaler / liste / dict → **tam JSON-uyumlu.** `from_dict()` ters
dönüşüm (list → np.ndarray).

### 3.2 — Proje dosyasında saklanabilir mi? → **Evet, ama seçici**

| Veri | Projede sakla? | Gerekçe |
|---|---|---|
| `profile` (z,r) | ✅ Evet | Geometri taban — yeniden hesaplamak pahalı/STL gerektirir |
| `axis`, `segments`, `pole_regions`, `turnaround`, `confidence`, `quality` | ✅ Evet (hafif) | Yalnız skaler/küçük liste — yeniden analiz maliyetinden kurtarır |
| **ham vertex bulutu (V)** | ❌ Hayır | Megabaytlarca; gerekirse `source_path`'ten yeniden parse edilir |
| `source_path` | ✅ Evet | Yeniden analiz / yeniden bağlama için |

> **İlke:** *Türetilmiş özet sakla, ham veri saklama.* STL deterministik
> olduğundan (aynı dosya+param → aynı sonuç), ham bulut yerine `source_path` +
> özet yeterli. Proje şeması `model_version` ile evrilir.

### 3.3 — Cache edilebilir mi? → **Evet**

```
cache_key = hash(stl_file_bytes) + hash(analiz_parametreleri)
cache_value = MandrelModel.to_dict()
```
- STL büyük + analiz (PCA + grid arama) maliyetli → cache anlamlı.
- Anahtar dosya içeriği hash'i → dosya değişirse otomatik invalidasyon.
- Analiz parametreleri (bin sayısı, p-percentile) anahtara dahil → param
  değişince yeniden hesap.
- Determinizm (sabit seed) cache tutarlılığını garanti eder.

---

## 4. Backward Compatibility

Mevcut modüller (`path_generator`, `coverage_solver`, `winding_twin`,
`motion_planner`, ...) yalnız `MandrelProfile` bekler. Üç mekanizma:

### 4.1 — `as_profile()` adaptörü (birincil)
```
backend_fn(model.as_profile(), ...)   # her zaman sade MandrelProfile geçer
```
UI/twin `MandrelModel` taşır; **backend sınırında** `as_profile()` çağrılır.
Backend kodu **hiç değişmez.**

### 4.2 — Parametrik köprü
```
MandrelModel.from_parametric(MandrelProfile.cylinder(...))
   → source_type="parametric", confidence=tam (1.0/"YÜKSEK"), axis=Z varsayılan
```
Cylinder/cone/dome da MandrelModel olur → UI tek tip taşır, dallanma yok.

### 4.3 — Geçiş stratejisi (kademeli, kırılmasız)
```
Adım 1: MandrelModel eklenir; UI üretir ama backend'e as_profile() geçer.
Adım 2: Render/twin kademeli olarak zengin alanları (axis, segments) okur.
Adım 3: Hiçbir backend imzası değişmez — eski testler/replay aynen geçer.
```
> **Garanti:** `as_profile()` her zaman geçerli bir `MandrelProfile` döndürür →
> mevcut 8/8 regresyon + R3 preflight + replay **kırılmaz.**

---

## 5. UML Seviyesinde Sınıf Diyagramı (metin)

```
┌─────────────────────────────────────────────────────────────┐
│                       MandrelModel                          │
│  (kompozisyon kökü — veri konteyneri, iş mantığı yok)       │
├─────────────────────────────────────────────────────────────┤
│ + profile: MandrelProfile            «zorunlu»              │
│ + source_type: str                                          │
│ + axis: AxisResult                   «0..1»                 │
│ + confidence: ConfidenceReport       «0..1»                 │
│ + quality: QualityReport             «0..1»                 │
│ + segments: RegionSegmentation       «0..1»                 │
│ + pole_regions: List<PoleRegion>     «0..*»                 │
│ + turnaround_candidates: TurnaroundCandidates  «0..1»       │
│ + source_path: str / units / model_version / created_at     │
├─────────────────────────────────────────────────────────────┤
│ + as_profile(): MandrelProfile                              │
│ + is_winding_ready(): bool                                  │
│ + cylinder_segments(): List<Segment>                        │
│ + dome_segments(): List<Segment>                            │
│ + to_dict() / from_dict()                                   │
│ + from_stl(report) / from_parametric(profile)               │
└─────────────────────────────────────────────────────────────┘
        ◆ (composition — MandrelModel HAS-A)
        │
        ├──────────────► MandrelProfile        (DEĞİŞMEZ — saf geometri)
        │                  + z_mm, r_mm
        │                  + radius_at(), arc_length(), avg_radius_mm ...
        │
        ├──────────────► AxisResult
        │                  + axis_unit, center, j_min, eig_ratios, degeneracy_flag
        │
        ├──────────────► ConfidenceReport
        │                  + axis, symmetry, mesh_density, radial_fit, aggregate, grade
        │
        ├──────────────► QualityReport
        │                  + aspect_ratio, min_radius_mm, is_single_valued,
        │                    asymmetry_mm, winding_suitable, reasons[]
        │
        ├──────────────► RegionSegmentation
        │                  + spans: List<Segment>   ◆──► Segment
        │                  + transitions: List<float>     (z_start,z_end,kind,...)
        │
        ├──────────────► List<PoleRegion>
        │                  + side, z_start, z_end, min_radius_mm
        │
        └──────────────► TurnaroundCandidates
                           + by_alpha: Map<float, TurnaroundCandidate>
                                             ◆──► TurnaroundCandidate
                                                   (alpha,z_left,z_right,c,reachable)

  Tüketiciler (bağımlılık yönü → MandrelModel'e DOĞRU):
    UI/Renderer ───uses──► MandrelModel (tüm alanlar)
    winding_twin ──uses──► MandrelModel.as_profile() + turnaround + confidence
    coverage ─────uses──► MandrelModel.as_profile() + axis + confidence
    path_generator ─uses─► MandrelModel.as_profile() + turnaround + confidence
```

---

## 6. Risk Analizi — MandrelModel Büyürse

| # | Risk | Belirti | Azaltma |
|---|---|---|---|
| R1 | **God-object** (her şeyi bilen dev nesne) | Analiz mantığı modele sızar, SRP ihlali | Model = **yalnız veri konteyneri**; tüm analiz `stl_intelligence`'de; modelde sadece `as_profile`/`is_winding_ready`/erişimci |
| R2 | **Opsiyonel alan patlaması** | 20+ `Optional` → her yerde `None` kontrolü | Alanları alt-rapora grupla (axis/segments/quality...); `is_winding_ready()` tek kapı; erişimciler None-safe |
| R3 | **Serialization şişmesi** | Proje dosyası megabaytlara çıkar | Ham vertex SAKLAMA (§3.2); yalnız özet; cache ayrı |
| R4 | **Sürüm kayması** | Eski proje yeni şemayı okuyamaz | `model_version` + `from_dict` geriye uyumlu migrasyon; bilinmeyen alan toleransı |
| R5 | **Sıkı bağ (coupling)** | Her modül MandrelModel'e bağımlı | Backend MandrelModel'i **görmez** — yalnız `as_profile()` sınırında; bağımlılık UI/twin ile sınırlı |
| R6 | **Alan çoğalması** (yeni faz → yeni alan) | Model sürekli büyür | Yeni analiz → yeni alt-rapor dataclass + opsiyonel alan; çekirdek (profile+source) sabit kalır |
| R7 | **Mutasyon/tutarsızlık** | profile ve segments senkron kalmaz | Model **immutable** (frozen dataclass) düşün; segments tek kaynak, cylinder/dome erişimci (kopya alan yok) |
| R8 | **Performans** (büyük model kopyalama) | UI'da sık kopya | Frozen + paylaşımlı referans; np dizileri kopyalanmaz, paylaşılır |

> **Anahtar koruma:** MandrelModel "akıllı" değil **derli toplu** olmalı. Zekâ
> `stl_intelligence`'de; model sadece sonucu kayıpsız taşır. Bu sınır korunduğu
> sürece büyüme kontrollüdür: yeni faz = yeni opsiyonel alt-rapor, çekirdek sabit.

---

## Onay

Bu veri modeli onaylanırsa implementasyon şu sırayla başlar:
1. `stl_intelligence.py` alt-yapıları (AxisResult, RadiusProfile, Segment,
   PoleRegion, TurnaroundCandidate(s), ConfidenceReport, QualityReport).
2. `MandrelModel` kompoziti (`as_profile`, `is_winding_ready`, `from_stl`,
   `from_parametric`, `to_dict`/`from_dict`).
3. 6 STL Intelligence bileşeni (Axis Solver → Turnaround Detection).
4. T1–T14 test senaryoları.

`MandrelProfile` **dokunulmaz** kalır; backend `as_profile()` sınırından beslenir;
regresyon + replay korunur. Kodlama yalnız bu onaydan sonra başlar.
