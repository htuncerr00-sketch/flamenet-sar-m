# CAM_REBUILD_PLAN.md — CAM Çekirdeği Tekleştirme + Profesyonel Görselleştirme

> **Durum:** Onay bekliyor (kod yazılmadan önce).
> **Hedef:** CadWind / Cadfil / TaniqWind Pro seviyesine yaklaşan profesyonel,
> hızlı, kararlı, gerçek filament görünümlü, gerçek simülasyonlu, güvenilir
> G-code üreten bir filament winding CAM sistemi.
> **Kısıt:** Wire protokolü FROZEN. Tüm UI Türkçe. AI advisory yalnızca
> SafetyValidator üzerinden. Geliştirme dalı: `claude/amazing-feynman-XUXBf`.

---

## 0. Onaylanan Kararlar

| # | Karar | Kaynak |
|---|-------|--------|
| D1 | **Üretim Tasarım Merkezi → "Analiz & Dışa Aktarım"a indirgenir.** Kendi G-code üretimi kaldırılır (CAM paneli G-code'un tek sahibi). FEA (Abaqus/Nastran), maliyet, kayma analizi, makine profilleri, PDF **korunur**. | Kullanıcı onayı |
| D2 | **Önce yazılı plan** (bu doküman), onay sonrası kod. | Kullanıcı onayı |
| D3 | **Motor tekleştirme:** Tüm CAM hesaplama mantığı `app/cam_engine.py` içinde tek kaynak olur. `cam_panel.py` silinir. | Kullanıcı vizyonu §9 |
| D4 | **Ply (kat) kalınlığı:** sol panele "Kat Kalınlığı (mm)" alanı eklenir, default 0.30 mm. `layer_rows[i].thickness_mm` varsa o kullanılır. | Plan kararı |
| D5 | **Ribbon performans stratejisi:** katman sayısı ≤ 8 ise tüm katmanlar ribbon (şerit); > 8 ise seçili katman ribbon + diğerleri ince çizgi (LOD). | Plan kararı |

---

## 1. Mevcut Mimari (Kod Gerçekleri)

### 1.1 Veri modelleri (backend — DEĞİŞMEZ)

```
WindingPoint   : x_mm, a_deg, feed, z_fiber, layer, circuit
WindingPath    : points, n_circuits, n_layers, total_fiber_length_mm,
                 estimated_time_s, coverage_pct, clairaut_c, params
WindingPathParams: profile, alpha_deg, n_layers, tow_width_mm, overlap_pct,
                 feed_mm_s, spindle_rpm, winding_strategy, carriage_min/max_mm,
                 reverse_at_ends, n_steps_per_pass(=150), friction_mu, lambda_slip
MandrelProfile : z_mm[], r_mm[]  + .length_mm .max_radius_mm
MotionSegment  : x_start, x_end, a_start, a_end, feed_mm_min, segment_type
GCodeProgram   : lines, n_circuits, n_layers, total_length_mm,
                 estimated_time_s, coverage_pct, soft_limit_violations,
                 is_safe, warnings
```

`WindingPoint.layer` ve `.circuit` **zaten mevcut** → katman gruplama ve
thickness offset için yeni backend alanı GEREKMEZ.

### 1.2 Tespit edilen problemler

| Sorun | Kod konumu |
|-------|-----------|
| G-code main thread'de (freeze) | `entegre:1537`, `cam_panel:690`, `uretim:1072` |
| Fiber = piksel çizgi (`width=4.0`) | `entegre:366` |
| Tow width 3D'de yok (`r_val` sadece mandrel) | `entegre:273` |
| Katman radyal offset yok (hepsi aynı r) | `entegre:270-276` |
| Tablo seçimi → 3D highlight bağlı değil | `entegre:923` |
| Coverage tek skaler (2D harita yok) | `path_generator:475-482` |
| Motor 2 kopya | `cam_panel:530` + `entegre:1285` |
| 3D dönüşüm Python loop (yavaş) | `entegre:254-288` |

### 1.3 Panel rolleri (D1 sonrası hedef)

```
Katman & Analiz          → (değişmez) malzeme/laminat analizi
Manuel Dizilim           → (değişmez) katman dizilim editörü
🏭 CAM Tasarım Merkezi   → TEK CAM akışı: geometri → yol → simülasyon → G-code
Analiz & Dışa Aktarım    → (eski "Üretim") FEA/maliyet/kayma/PDF — G-code YOK
```

---

## 2. Sprint Planı (Onaylanan Öncelik Sırası)

### SPRINT 1 — Motor Tekleştirme + CAMPanel Silme  (D3)

**Yeni dosya: `app/cam_engine.py`** (Qt'ye bağımsız, saf Python)

```python
@dataclass
class CamRequest:
    """Düz değerler — Qt nesnesi yok. Worker'a güvenle geçer."""
    mandrel_type: str
    diameter_mm: float; length_mm: float
    cone_angle_deg: float; dome_h_mm: float; dome_hr_ratio: float
    stl_path: Optional[str]
    alpha_deg: float; n_layers: int
    tow_w_mm: float; overlap_pct: float
    strategy_text: str
    feed_mm_s: float; spindle_rpm: float; friction_mu: float
    ply_thickness_mm: float            # D4
    layer_rows: list                   # [{tip, alpha_deg, fitil_mm, n_kat, thickness_mm}]
    x_min: float; x_max: float

@dataclass
class CamResult:
    path: WindingPath                  # ilk/birincil yol
    profile: MandrelProfile
    all_layer_paths: Optional[list]    # [(layer_dict, WindingPath)] | None

def build_profile(req: CamRequest) -> MandrelProfile: ...
def preflight(req: CamRequest) -> None:               # R3 — ComplexityError fırlatır
def compute_path(req: CamRequest) -> CamResult:       # build_profile + preflight + generate_path
def compute_gcode(result: CamResult,
                  cfg: GCodeCfg) -> GCodeProgram:      # plan_motion + generate_gcode
```

- `compute_path` ve `compute_gcode` **saf** — UI/QThread bilmez, test edilebilir.
- R3 preflight `compute_path` içine taşınır (tek kapı).

**`cam_panel.py` silinir.** Bağımlılıklar:
- `main_window.py`: zaten import yok (Sprint 0'da temizlendi) — doğrula.
- `cam_stress_test.py`: `EntegreTasarimPaneli` kullanacak şekilde güncellenir
  (widget alias'ları: `_mandrel_type`→`_cb_type`, `_diameter`→`_sp_diam`,
  `_length`→`_sp_len`, `_alpha`→`_sp_alpha`, `_n_layers`→`_sp_nlayers`,
  `_tow_w`→`_sp_tow`, `_overlap`→`_sp_overlap`, `_calculate_path`→`_on_calculate`,
  `_generate_gcode`→`_on_generate_gcode`, `_worker_thread`, `_watchdog`,
  `set_layer_stack`, `_winding_path`, `_gcode_program`).

**`entegre_tasarim_paneli.py`:** `_do_calculate` → `cam_engine.compute_path(req)`
çağırır. `_ECalcParams` → `CamRequest`'e dönüştürülür (veya doğrudan kullanılır).

**Test:** `cam_stress_test.py` 100/100 + Senaryo A; regression 8/8.

---

### SPRINT 2 — R5: Async G-code

```python
@dataclass
class _GCodeParams:                   # düz veri, Qt yok
    winding_path: object
    all_layer_paths: list
    ctrl_type: str; x_axis: str; a_axis: str; max_x_feed: float

class _GCodeWorker(QObject):
    finished = Signal(object, int)    # (GCodeProgram, gen)
    error    = Signal(str, int)
    def run(self):                    # cam_engine.compute_gcode() — worker thread
```

- `_GCODE_WATCHDOG_MS = 60_000` (60 sn) — path watchdog (30 sn) ile aynı desen.
- Nesil sayacı (`_gcode_gen`) → geç gelen sonuç yok sayılır.
- UI: buton disable + progress bar; bitince ilk **1.000 satır** önizleme,
  tam metin bellekte (`self._gcode_text_full`). "Tümünü Kaydet" tam yazar.
- **Ana thread hiçbir koşulda bloke olmaz** (kullanıcı §2 hedefi).

**Test:** 100-katman G-code üretimi sırasında UI responsive (processEvents
ile freeze ölçümü); watchdog tetiklenince UI serbest.

---

### SPRINT 3 — R6: Viewer Optimizasyonu (LOD + numpy)

**Yeni: `_path_to_3d_fast(points, z_arr, r_arr, ply_mm) -> Dict[int, np.ndarray]`**

- Python loop YOK — `np.fromiter` / vektörel `np.interp` / `np.cos/sin`.
- Layer offset (thickness, D4): `r_eff = r_mandrel + layer_idx * ply_mm`.
- Döndürür: `{layer_idx: (N,3) float32}`.

**LOD tablosu (katman başına):**

| Nokta | Stride | Gösterilen |
|-------|--------|-----------|
| ≤ 50.000 | 1 | tam |
| 50k–200k | 4 | ~50k |
| 200k–500k | 10 | ~50k |
| > 500k | 20 | ~50k |

- `_MAX_DISPLAY_POINTS = 50_000`.
- Decimation bilgi etiketi: `"3D: 12.500 / 250.000 nokta (×20 LOD)"`.

**Test:** 1M+ nokta için `_path_to_3d_fast` < 200 ms; eski loop ile sonuç
eşdeğer (örnek noktalarda ≤ %1 sapma).

---

### SPRINT 4 — Ribbon Fiber Renderer  (D5)

**Yeni: `_build_ribbon_mesh(pts, tow_width_m, ...) -> gl.GLMeshItem`**

- Her nokta: tangent = merkezi fark; mandrel normal = radyal yön;
  `ribbon_dir = normalize(cross(tangent, normal))`.
- `sol = p - ribbon_dir*w/2`, `sağ = p + ribbon_dir*w/2`.
- Quad strip → `(N-1)*2` üçgen, batch `GLMeshItem`.
- Ribbon nokta limiti: 5.000/katman (üstü stride ile seyreltilir).

**Mod seçimi (D5):**
- katman ≤ 8 → tüm katmanlar ribbon.
- katman > 8 → seçili katman ribbon, diğerleri ince çizgi.
- UI: "Şerit Modu" toggle (varsayılan açık) — kullanıcı çizgiye düşürebilir.

**Test:** ribbon mesh üçgen sayısı = `(N-1)*2`; 8 katman ribbon render
başlangıcı < 500 ms.

---

### SPRINT 5 — Thickness Görselleştirme  (D4)

- Sol panele **"Kat Kalınlığı (mm)"** alanı (`QDoubleSpinBox`, 0.05–5.0,
  default 0.30) → `CamRequest.ply_thickness_mm`.
- `_path_to_3d_fast` zaten offset uyguluyor (Sprint 3) → burada UI + veri akışı.
- `r_eff = r_mandrel + layer_idx * ply_mm` → katmanlar üst üste birikir,
  kalınlık artışı ve pole bölgesi büyümesi görünür.

**Test:** N katmanlı sonucun max yarıçapı ≈ `r + (N-1)*ply` (≤ %1).

---

### SPRINT 6 — Layer Highlight

- `update_fiber_paths` → `self._fiber_items_by_layer: Dict[int, item]`.
- `self._layer_table.itemSelectionChanged.connect(self._on_layer_selection_changed)`
  (şu an eksik bağlantı).
- Seçili katman → sarı opak `(1.0, 0.95, 0.0, 1.0)`; diğerleri yarı saydam
  `(*renk, 0.15)`. Ribbon modunda seçili katman ribbon'a yükseltilir.

**Test:** seçim sinyali → doğru item renk değişimi (offscreen item.color kontrol).

---

### SPRINT 7 — Coverage Heatmap

**Yeni (cam_engine): `coverage_grid(path, profile, n_z=80, n_a=120) -> np.ndarray`**

- Grid hücresi: o bölgeden geçen tow pass yoğunluğu (başlangıçta direkt hücre
  atama; Gaussian opsiyonel/sonraki iterasyon).
- Normalize: beklenen kapsama = 1.0.

**Yeni: `coverage_mesh(grid, profile) -> gl.GLMeshItem`**
- Renk skala: `<0.5` mavi · `≈1.0` yeşil · `1–2` sarı · `>2` kırmızı.
- Mandrel yüzeyine giydirilir; "Coverage Haritası" toggle ile açılır.

**Test:** uniform helical → çoğunluk yeşil; yüksek overlap → sarı/kırmızı
hücre oranı artar (monoton).

---

### SPRINT 8 — Simülasyon Geliştirmeleri

Mevcut animasyona ek (kullanıcı §5):
- **Durdur** butonu (reset'ten ayrı: stop = sıfıra dön + durdur).
- **Hız çarpanı**: 2x / 5x / 10x (QComboBox) → `_anim_tick` step ölçeklenir.
- Sarım kafası mevcut; "fiber adım adım sarılma": ribbon/çizgi'yi `_anim_idx`'e
  kadar progresif çiz (büyüyen mesh) — opsiyonel, performans bütçesine göre.

**Test:** hız çarpanı timeline tamamlanma süresini doğru ölçekler.

---

### SPRINT 9 — Stress / Benchmark

**Yeni: `validation_d2/cam_benchmark.py`**

İstenen senaryo: 100 katman, D=250 mm, L=1000 mm, tow=6 mm, α=50°.

> **Ön analiz (R3):** `n_circ ≈ ceil(π·125 /(6·0.95)) = 69 devre/kat`,
> `pts/kat = 69·150 = 10.350`, `toplam = 1.035.000 > 250.000` → **K2b REJECT**.
> Bu yüzden benchmark iki kol çalıştırır:
> - **Kol A (preflight reddi):** tam 100 katman → `ComplexityError` ölçülür
>   (graceful, hang yok). Bu beklenen davranış.
> - **Kol B (ölçüm):** R3 limitini geçen en büyük geçerli yapı
>   (≈ 24 katman → 248.400 nokta) → gerçek metrikler.
>
> **Açık karar gerektirir:** 100 katmanın gerçekten hesaplanması isteniyorsa
> K2b limiti yükseltilmeli (ör. 1.5M). Bunu Sprint 9'da kullanıcıya sunacağım.

**Raporlanan metrikler:** preflight süresi · path hesaplama süresi · nokta
sayısı · peak RSS (tracemalloc) · G-code üretim süresi · 3D render başlangıç
süresi. Çıktı: tablo + `★★★ READY ★★★` / `STOP` verdikti.

---

### SPRINT 10 — Üretim → "Analiz & Dışa Aktarım" İndirgeme  (D1)

- Sekme adı: "Üretim Tasarım Merkezi" → **"Analiz & Dışa Aktarım"**.
- `_gen_gcode` (uretim:1072) ve export formatlarındaki **G-code seçeneği
  kaldırılır**; CSV/Abaqus/Nastran/HTML/PDF **korunur**.
- `get_gcode()` API'si: CAM panelinden gelen G-code'u proxy'ler veya kaldırılır
  (çağıran taraf kontrol edilir).
- Makine profili / maliyet / kayma analizi / FEA **dokunulmaz**.
- `main_window.py` sinyal yeniden bağlama gözden geçirilir.

**Test:** regression 8/8; FEA/CSV/PDF export hâlâ üretiliyor.

---

## 3. Dosya Değişiklik Özeti

| Dosya | Aksiyon |
|-------|---------|
| `app/cam_engine.py` | **YENİ** — tek motor (compute_path/gcode/coverage_grid) |
| `app/panels/entegre_tasarim_paneli.py` | Büyük: engine bağla, async gcode, numpy LOD, ribbon, thickness, highlight, heatmap, sim |
| `app/panels/cam_panel.py` | **SİL** |
| `app/panels/uretim_tasarim_paneli.py` | Orta: gcode kaldır, "Analiz & Dışa Aktarım" |
| `app/main_window.py` | Küçük: sekme adı, sinyal temizlik |
| `validation_d2/cam_stress_test.py` | Güncelle: Entegre paneli + alias'lar |
| `validation_d2/cam_benchmark.py` | **YENİ** — stress/metrik raporu |

---

## 4. Riskler ve Önlemler

| Risk | Önlem |
|------|-------|
| R1 (worker'da Qt erişimi) | Tüm widget okuma ana thread'de `CamRequest`'e; engine saf |
| Ribbon performansı | D5 LOD: >8 katman seçili-ribbon + çizgi; nokta limiti |
| 100 katman R3 reddi | Benchmark iki kol; limit yükseltme kullanıcıya sunulur |
| Üretim paneli işlev kaybı | D1: yalnız G-code kaldır; FEA/maliyet/kayma/PDF korunur |
| Wire protokolü | Hiç dokunulmaz; tüm görselleştirme türetilmiş veriden |
| Stress test API kırılması | Entegre paneline geriye-uyumlu alias'lar |

---

## 5. Tamamlama Kriteri (Son Hedef §10)

- [ ] Tek CAM akışı: tasarım → dizilim → yol → simülasyon → G-code
- [ ] Hiçbir zaman sonsuz "Hesaplanıyor…" (path 30 sn + gcode 60 sn watchdog)
- [ ] Gerçek tow width + ribbon + thickness + overlap + pole 3D'de görünür
- [ ] Tablo seçimi → 3D katman highlight
- [ ] Oynat/Duraklat/Durdur/Zaman çizgisi + 2x/5x/10x
- [ ] Mandrel üzerinde coverage heatmap (mavi→yeşil→sarı→kırmızı)
- [ ] Motor tek kaynak (`cam_engine.py`); `cam_panel.py` yok
- [ ] 100+ katmanda UI donmuyor (async gcode + LOD)
- [ ] Benchmark raporu (süre/nokta/bellek/gcode/render)
- [ ] regression 8/8 + stress 100/100 korunur

---

**Onay sonrası uygulama sırası:** Sprint 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10.
Recipe Library ikinci planda (askıda).
