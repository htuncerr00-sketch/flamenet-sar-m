# Filament Winding Dijital İkiz — Teknik Analiz ve Mimari Raporu

> **Durum:** Yalnızca analiz — bu belgede kod yazılmadı.
> **Tarih:** 2026-06-16
> **Odak:** STL→Mandrel Engine ve Digital Twin mimarisi
> **Branch:** `claude/amazing-feynman-XUXBf`

---

## 0. Yönetici Özeti (En Kritik Bulgu)

Kod tabanı taraması tek ve çarpıcı bir sonuç verdi:

> **Dijital ikiz arka ucu (backend) zaten ~%80 yazılmış ve test edilmiş durumda.
> Asıl boşluk arka uçta DEĞİL — UI ile backend arasındaki entegrasyonda.**

`faz17_d1/.../core/` altında, kullanıcının 1–6 numaralı hedeflerinin neredeyse
tamamını karşılayan, test edilmiş motorlar mevcut:

| Kullanıcı Hedefi | Zaten Var Olan Backend Modülü | Durum |
|---|---|---|
| 4. Coverage Engine (Z-θ grid, yoğunluk, overlap, heatmap) | `coverage_solver.py` (`CoverageMap`, `solve_coverage`) | ✅ Yazılı + test |
| 5. Layer Growth (efektif çap büyümesi) | `winding_twin.py` (`TwinState.current_radius_mm`), `fiber_band.py` (`layer_thickness_mm`) | ✅ Yazılı + test |
| 2. Tow kalınlığı / katman birikimi | `fiber_deposition.py` (`DepositionMap.thickness_mm[i,j]`) | ✅ Yazılı + test |
| 3. 4-eksen dijital ikiz (mandrel/carriage/payout/fiber) | `winding_twin.py` (`TwinState`: spindle/carriage/eye/contact/deposited) | ✅ Yazılı + test |
| 6. Non-geodesic solver | `non_geodesic_engine.py` (Koussios RK4) | ✅ Yazılı + test |
| 6. Fiber physics / contact | `fiber_contact_model.py` (`compute_contact_patch`, band edges) | ✅ Yazılı + test |
| 1. STL otomatik eksen bulma | **— YOK —** (Z ekseni hardcoded) | ❌ Gerçek boşluk |
| 2. Ribbon/mesh tow render | **— YOK —** (UI sadece `GLLinePlotItem`) | ❌ Gerçek boşluk |

**Kanıt:** `test_digital_twin.py` (858 satır), `test_winding_physics.py` (593),
`test_non_geodesic.py` (613), `test_industrial_validity.py` (500) — bu motorlar
düzenli olarak test ediliyor.

**Sonuç:** Bizim asıl işimiz "backend'i sıfırdan yazmak" **değil**. İşimiz:
1. UI'ı mevcut `winding_twin.simulate_winding()` motoruna **bağlamak**,
2. Genuinely eksik iki parçayı yazmak: **STL eksen otodetect** + **ribbon renderer**,
3. UI'ın naif kendi-yazımı animasyonunu (benim bir önceki commit'te yazdığım dahil)
   backend'in doğrulanmış `TwinState` akışıyla **değiştirmek**.

---

## A. Mevcut Sistemin Eksikleri

### A.1 — Mimari kopukluk (en büyük sorun)

UI paneli `entegre_tasarim_paneli.py` yalnızca şu 4 backend modülünü tüketiyor:

```
geometry_engine.MandrelProfile
path_generator.generate_path        →  ham WindingPath
motion_planner.plan_motion
gcode_postprocessor.generate_gcode
```

Buna karşılık şu motorların **HİÇBİRİ UI tarafından çağrılmıyor**
(`grep` ile doğrulandı — yalnızca backend-içi ve test importları var):

```
winding_twin.simulate_winding       ← gerçek 4-eksen twin (TwinState akışı)
coverage_solver.solve_coverage      ← Z-θ coverage + overlap heatmap
fiber_deposition.DepositionMap      ← katman kalınlık birikimi grid'i
fiber_contact_model                 ← temas noktası + band kenarları
trajectory_builder.build_timeline   ← tekdüze zaman-grid yörünge
machine_execution.simulate_execution← kinematik limitli yürütme
```

Yani UI, backend'de hazır olan dijital ikizi **görmezden gelip**, kendi
basitleştirilmiş animasyonunu (`_anim_xyz`, manuel `np.cos/np.sin` projeksiyonu)
yeniden üretmiş. Bu yüzden:

- Mandrel dönüşü gerçek `spindle_angle_deg`'den değil, ham `a_deg`'den türetiliyor.
- Carriage hareketi gerçek payout dinamiğini (lag error, eye pozisyonu) içermiyor.
- Katman büyümesi (`current_radius_mm`) hiç kullanılmıyor → tüm katmanlar aynı çapta.
- Coverage/overlap/thickness hiç gösterilmiyor.

### A.2 — STL→Mandrel motorundaki gerçek eksikler

`geometry_engine.py:_profile_from_vertices` (satır 93-111) ve
`stl_processor.py` analizinden:

1. **Eksen otodetect YOK.** Dönme ekseni **Z varsayılıyor** (`r = √(x²+y²)`,
   `geometry_engine.py:99`). STL farklı yönelimdeyse (X veya Y ekseni, ya da
   eğik) profil tamamen yanlış çıkar. Kullanıcının 1. hedefinin çekirdeği bu.
2. **Kesit = sadece max radius.** Her z-dilimi için `max(r[mask])` alınıyor
   (`geometry_engine.py:106`). Gürültülü/asimetrik STL'de tek bir aykırı vertex
   yarıçapı şişirir. Robust percentile (örn. p95) veya medyan-temelli fit yok.
3. **Eksen kaçıklığı (off-axis centering) düzeltmesi yok.** Mandrel ekseni
   orijinden geçmiyorsa `√(x²+y²)` yanlış. Önce centroid/eksen merkezleme gerek.
4. **Simetri doğrulaması var ama tüketilmiyor.** `validate_stl_symmetry`
   (`stl_processor.py:84`) mevcut, ancak UI yüklemede çağırmıyor → kullanıcı
   asimetrik STL yüklediğinde uyarı almıyor.
5. **Birim/ölçek belirsizliği.** STL mm mi m mi inç mi — varsayım yok, sorgu yok.

### A.3 — Görselleştirme eksikleri (UI)

1. **Sadece çizgi.** Tüm fiber `GLLinePlotItem` (piksel genişlikli). Tow width
   fiziksel metre olarak temsil edilmiyor — `width=4.0` ekran pikseli, kamera
   zoom'una göre yanıltıcı. Ribbon/mesh yok.
2. **Tow thickness görselde yok.** `fiber_band.tow_thickness_mm` var ama 3D'de
   kalınlık hiç çizilmiyor.
3. **Katman birikimi görünmüyor.** `DepositionMap.thickness_mm` hazır; mandrel
   yüzeyinde kabaran kabuk (shell) olarak render edilmiyor.
4. **Coverage heatmap yok.** `CoverageMap.overlap_heatmap()` hazır; mandrel
   yüzeyine boyanmıyor.

### A.4 — Path/fizik katmanındaki ikincil eksikler

1. **Layer growth path'e geri beslenmiyor.** `_generate_helical`
   (`path_generator.py:379`) Clairaut sabitini `c = r_max·sin(α)` ile **ilk
   katmanın** çapından hesaplıyor. Sonraki katmanlar büyümüş çapı kullanmıyor.
   (Not: `winding_twin` bunu twin seviyesinde modelliyor, ama saf `generate_path`
   yolu modellemiyor — ikisi arasında tutarsızlık var.)
2. **Non-geodesic sessiz fallback.** `friction_mu>0` çözümü hata verirse sessizce
   geodesic'e dönüyor (`path_generator.py:~450`) — kullanıcı bilgilendirilmiyor.

---

## B. Rakip Yazılımlarla Farklar (CadWind / Cadfil / Taniq)

| Yetenek | CadWind / Cadfil / Taniq | Bizim Sistem (bugün) | Bizim Backend (gizli) |
|---|---|---|---|
| STL/CAD import + eksen otodetect | ✅ tam (CAD çekirdeği) | ❌ Z hardcoded | ❌ yok |
| Dome/geodesic/non-geodesic solver | ✅ olgun | ⚠️ var ama UI sınırlı | ✅ `non_geodesic_engine` |
| Gerçek band/ribbon render | ✅ kaplı yüzey | ❌ çizgi | — (render UI'da olmalı) |
| Katman kalınlık birikimi | ✅ shell büyümesi | ❌ | ✅ `DepositionMap` |
| Coverage / overlap heatmap | ✅ | ❌ | ✅ `coverage_solver` |
| Slip/turn riski analizi | ✅ | ⚠️ kısmi | ✅ `fiber_contact_model`, `non_geodesic_validator` |
| Gerçek zamanlı makine twin | ✅ (Taniq güçlü) | ⚠️ naif | ✅ `winding_twin` (TwinState) |
| Makine kinematik limiti | ✅ | ⚠️ | ✅ `machine_execution`, `machine_limits` |
| G-code post | ✅ çok kontrolör | ✅ grbl/mach3/fanuc... | ✅ `gcode_postprocessor` |

**Yorum:** Rakiplere karşı en büyük gerçek açığımız **CAD/STL eksen çözümleme**
ve **görsel sadakat (ribbon + shell)**. Fizik/çözücü tarafında backend zaten
rekabetçi — sadece UI'a bağlı değil. Yani "Taniq seviyesi" hedefine giden en
kısa yol bir backend yeniden yazımı değil, **entegrasyon + iki eksik parça**.

---

## C. Önerilen Yeni Mimari

### C.1 — Katmanlı hedef mimari

```
┌──────────────────────────────────────────────────────────────────────┐
│  UI KATMANI  (faz17_d2 / entegre_tasarim_paneli.py)                    │
│                                                                        │
│   ┌────────────┐   ┌──────────────┐   ┌────────────────────────────┐  │
│   │ Parametre  │   │ TwinPlayer   │   │  3D Sahne (_MachineGLView)  │  │
│   │ toplama    │──▶│ (zaman-grid  │──▶│  • RibbonRenderer (mesh)    │  │
│   │ (R1 temiz) │   │  oynatıcı)   │   │  • ShellRenderer (kalınlık) │  │
│   └────────────┘   └──────┬───────┘   │  • CoverageHeatmapRenderer  │  │
│                           │           │  • Makine (mandrel/carriage/│  │
│                           │           │    payout eye)              │  │
│                           │           └────────────────────────────┘  │
└───────────────────────────┼────────────────────────────────────────────┘
                            │  (yalnız düz veri: np.ndarray, dataclass)
┌───────────────────────────▼────────────────────────────────────────────┐
│  CAM FACADE  (YENİ — app/cam_engine.py, ince adaptör)                   │
│   compute_twin(req) → TwinResultDTO   (winding_twin.simulate_winding)   │
│   compute_coverage(req) → CoverageDTO (coverage_solver.solve_coverage)  │
│   compute_gcode(req) → GCodeDTO       (motion_planner + postprocessor)  │
│   load_mandrel(stl) → MandrelProfile  (YENİ stl_axis_solver + geometry) │
└───────────────────────────┬────────────────────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────────────────────┐
│  BACKEND ÇEKİRDEK  (faz17_d1 — ÇOĞUNLUKLA KORUNUR)                       │
│   winding_twin · coverage_solver · fiber_deposition · fiber_band        │
│   fiber_contact_model · non_geodesic_engine · trajectory_builder        │
│   machine_execution · path_generator · motion_planner · geometry_engine │
│   + YENİ: stl_axis_solver.py (PCA tabanlı eksen + robust kesit)         │
└─────────────────────────────────────────────────────────────────────────┘
```

### C.2 — İki gerçek yeni backend parçası

**(1) `stl_axis_solver.py` (YENİ)** — Kullanıcı hedef 1'in çekirdeği:

```
Algoritma (önerilen):
  1. Vertex bulutu yükle (parse_stl_vertices — MEVCUT, korunur).
  2. Centroid'e merkezle.
  3. PCA (kovaryans matrisi özvektörleri) → 3 ana eksen.
     - Dönel cisimde EN BÜYÜK varyans = uzun eksen = dönme ekseni adayı.
     - Doğrulama: diğer iki eksende varyans ~eşit olmalı (dönel simetri testi).
  4. Vertex'leri ana eksen referans çerçevesine döndür (R^T·v).
  5. Yeni eksende z' = ana eksen projeksiyonu, r' = kalan düzlemdeki yarıçap.
  6. Robust kesit: her z'-dilimi için max yerine yüksek-percentile (p95)
     → aykırı vertex'lere dayanıklı radius(z).
  7. validate_stl_symmetry'yi YENİ eksende çalıştır → güven skoru döndür.
  8. MandrelProfile(z_mm=z', r_mm=r') üret (mevcut dataclass'a bağlanır).
Çıktı: (MandrelProfile, axis_vector, symmetry_confidence, scale_warning)
```

Bu, `geometry_engine.from_stl`'in hardcoded-Z varsayımını **değiştirir**;
geri kalan `MandrelProfile` API'si aynı kalır → downstream kırılmaz.

**(2) UI `RibbonRenderer` (YENİ, UI tarafı)** — Kullanıcı hedef 2:

```
Girdi:  TwinState akışı (veya WindingPath + FiberBand)
Üretim: her tow segmenti için GLMeshItem şeridi
  - genişlik = band.bandwidth_at_angle(α) / 1000  (fiziksel metre)
  - kalınlık = band.compacted_thickness_mm × katman   (radyal ofset)
  - yüzey normali = fiber_contact_model._surface_normal_unit (MEVCUT)
LOD: >8 katman → seçili katman ribbon, diğerleri çizgi (D5 kuralı)
```

### C.3 — Veri akışı (yeni "Simüle Et" davranışı)

```
[Kullanıcı "Simüle Et"] 
   → _collect_params()  (R1: ana thread, sadece düz veri)
   → QThread worker: cam_engine.compute_twin(req)
        → winding_twin.simulate_winding(profile, band, params, n_layers, dt_s)
        → TwinSimulationResult (states[], final_deposition, base→final_radius)
   → finished signal → UI'da:
        TwinPlayer states[] dizisini 30 FPS'te oynatır
        her frame TwinState'ten:
           mandrel.rotate(state.spindle_angle_deg)
           carriage.x = state.carriage_x_actual_mm
           payout_eye.pos = (state.eye_x_mm, state.eye_r_mm)
           ribbon.grow(state.fiber_deposited_mm, state.current_radius_mm)
        bitişte: coverage heatmap + thickness shell göster
```

---

## D. Geliştirme Sırası (Bağımlılık Temelli)

```
1. cam_engine.py facade  ──┐  (her şeyin tek giriş noktası; mevcut motorları sarar)
                          │
2. stl_axis_solver.py  ───┤  (hedef 1 — bağımsız, paralel yazılabilir)
                          │
3. TwinPlayer + TwinState ┘  (UI animasyonunu winding_twin'e bağla — hedef 3,5)
        │
        ▼
4. RibbonRenderer (mesh)     (hedef 2 — TwinPlayer'a takılır)
        │
        ▼
5. ShellRenderer (kalınlık)  (hedef 2,5 — DepositionMap'ten)
        │
        ▼
6. CoverageHeatmapRenderer   (hedef 4 — coverage_solver'dan)
        │
        ▼
7. Slip/contact overlay      (hedef 6 — fiber_contact_model'den)
```

**Mantık:** Önce facade (3-7'nin hepsi ona bağlanır), sonra STL (bağımsız),
sonra twin akışı (görsellerin veri kaynağı), en son görsel zenginleştirmeler.

---

## E. Sprint Planı

| Sprint | Başlık | Kapsam | Çıktı / Kabul Kriteri |
|---|---|---|---|
| **S1** | CAM Facade | `app/cam_engine.py`: `compute_twin/compute_coverage/compute_gcode/load_mandrel`. `entegre_tasarim_paneli` bu facade'ı kullanır. | UI hâlâ çalışır; tüm backend çağrıları tek dosyadan geçer. |
| **S2** | STL Eksen Çözücü | `stl_axis_solver.py` (PCA eksen + robust p95 kesit + simetri skoru). `from_stl` bunu kullanır. | Eğik yönelimli test STL'i doğru radius(z) verir; birim testi. |
| **S3** | Twin Entegrasyonu | UI animasyonu → `winding_twin.simulate_winding` → `TwinState` oynatımı. Naif `_anim_xyz` kaldırılır. | Mandrel gerçek `spindle_angle_deg` ile döner; carriage+eye gerçek pozisyonda. |
| **S4** | Ribbon Renderer | `GLMeshItem` şerit; fiziksel tow width; yüzey normali. D5 LOD. | Tow genişliği kamera zoom'undan bağımsız fiziksel görünür. |
| **S5** | Shell / Kalınlık | `DepositionMap` → mandrel üzerinde kabaran katman kabuğu; `current_radius_mm` katman büyümesi. | Katman ekledikçe çap görsel olarak büyür. |
| **S6** | Coverage Heatmap | `coverage_solver.solve_coverage` → mandrel yüzeyine BGYR boyama; overlap bölgeleri. | Z-θ heatmap + boşluk/overlap görünür. |
| **S7** | Slip & Temas | `fiber_contact_model` + `non_geodesic_validator` → riskli bölge overlay'i. | Slip riski yüksek devreler kırmızı işaretlenir. |
| **S8** | Stres & Doğrulama | 100 katman / D=250 / L=1000 / tow=6 / α=50° benchmark; R3 reddi + max geçerli config metrikleri. | Donma yok; preflight doğru reddeder; FPS raporu. |

---

## F. Yeniden Yazılacak Modüller

> "Yeniden yazma" = mevcut davranış değişecek veya sıfırdan yeni dosya.

| Modül | Eylem | Gerekçe |
|---|---|---|
| `app/panels/entegre_tasarim_paneli.py` (animasyon kısmı) | **Yeniden yaz** | Naif `_anim_xyz` projeksiyonu → `winding_twin` TwinState akışı. (Benim son commit'imdeki 4-eksen animasyon dahil bu kapsamda değişecek.) |
| `_MachineGLView` (fiber render) | **Yeniden yaz** | `GLLinePlotItem` → `RibbonRenderer`/`ShellRenderer`/`CoverageHeatmapRenderer`. |
| `geometry_engine.from_stl` / `_profile_from_vertices` | **Yeniden yaz (kısmi)** | Hardcoded-Z + max-radius → `stl_axis_solver` çağrısı (PCA + robust). |
| `app/cam_engine.py` | **YENİ** | Tek giriş facade'ı (şu an yok). |
| `core/stl_axis_solver.py` | **YENİ** | PCA eksen otodetect + robust kesit. |

**Opsiyonel düzeltme (yeniden yazım değil, küçük yama):**
- `path_generator._generate_helical`: çok-katman çağrılarında katman büyümesini
  Clairaut sabitine geri besle (veya bu tutarlılığı tamamen `winding_twin`'e
  devret ve `generate_path`'i tek-katman path için bırak).
- Non-geodesic sessiz fallback → UI'a uyarı sinyali.

---

## G. Korunacak Modüller (DEĞİŞTİRİLMEYECEK)

Bunlar yazılı, test edilmiş ve doğru — **dokunmuyoruz, tüketiyoruz**:

| Modül | Neden korunur |
|---|---|
| `winding_twin.py` | Tam 4-eksen twin; `TwinState` tüm gerekli alanları taşıyor; `test_digital_twin.py` ile doğrulanmış. |
| `coverage_solver.py` | Z-θ grid + overlap + gap + heatmap zaten tam. |
| `fiber_deposition.py` | `DepositionMap.thickness_mm` katman kalınlık grid'i hazır. |
| `fiber_band.py` | Tow width/thickness/compaction/bandwidth fiziği hazır. |
| `fiber_contact_model.py` | Temas noktası, yüzey normali, band kenarı — ribbon+slip için gerekli. |
| `non_geodesic_engine.py` | Koussios RK4 non-geodesic çözücü; `test_non_geodesic.py`. |
| `trajectory_builder.py` / `machine_execution.py` | Zaman-grid yörünge + kinematik limit. |
| `path_generator.py` (geodesic çekirdek) | Clairaut çözümü doğru; sadece layer-growth geri beslemesi opsiyonel yama. |
| `motion_planner.py`, `gcode_postprocessor.py` | G-code zinciri sağlam. |
| `geometry_engine.py` (parametrik fabrikalar) | `cylinder/cone/dome/ellipsoidal` doğru; sadece `from_stl` değişir. |
| **Wire protokolü** (`>QHHffffffffffffHH` + CRC) | DONMUŞ — asla değişmez. |

---

## H. STL→Mandrel Engine — Derin Dalış (Odak 1)

### Mevcut durum (kanıtlı)
- `stl_processor.parse_stl_vertices` (satır 14-37): binary+ASCII STL → (N,3) vertex.
  **Bu kısım sağlam, korunur.**
- `geometry_engine._profile_from_vertices` (satır 93-111): `r=√(x²+y²)`,
  z-bin başına `max(r)`, boşlukları `np.interp`. **Z hardcoded, max-radius naif.**
- `validate_stl_symmetry` (satır 84-106): 50 z-bin, slice içi `max(r)-min(r)`,
  eşik 5mm. **Mevcut ama UI tüketmiyor.**

### Önerilen yeni boru hattı
```
parse_stl_vertices (KORU)
   → axis_detect (PCA, YENİ)            : dönme eksenini bul, çerçeveyi döndür
   → robust_section (p95, YENİ)         : aykırıya dayanıklı radius(z)
   → symmetry_confidence (YENİDEN BAĞLA): yeni eksende skor, UI'a uyarı
   → MandrelProfile (KORU)              : aynı dataclass, downstream kırılmaz
```
Tasarım ilkesi: **Çıktı tipi `MandrelProfile` aynı kalır.** Böylece
`path_generator`, `winding_twin`, `coverage_solver` hiç etkilenmez —
sadece profilin nasıl türetildiği iyileşir.

---

## I. Digital Twin Mimarisi — Derin Dalış (Odak 3)

### Veri yapısı zaten ideal (`winding_twin.TwinState`, satır 52-70)
Animasyonun ihtiyaç duyduğu **her alan** zaten mevcut:

| Animasyon ihtiyacı | TwinState alanı | Hedef |
|---|---|---|
| Mandrel dönüşü | `spindle_angle_deg` | 3 |
| Carriage hareketi | `carriage_x_actual_mm`, `carriage_v_mm_s` | 3 |
| Payout göz hareketi | `eye_x_mm`, `eye_r_mm` | 3 |
| Fiber temas noktası | `contact_z_mm`, `contact_r_mm` | 3 |
| Gerçek zamanlı yatırma | `fiber_deposited_mm` | 3 |
| **Katman büyümesi** | `current_radius_mm` ("katman büyümesi dahil") | 5 |
| İlerleme | `progress_pct`, `current_layer`, `current_circuit` | 3 |

`TwinSimulationResult` ayrıca `final_deposition: DepositionMap` (kalınlık grid'i)
ve `base_radius_mm → final_radius_mm` (toplam büyüme) taşıyor → hedef 2,4,5.

### Önerilen UI oynatıcı (TwinPlayer)
```
simulate_winding(...) → states: List[TwinState]   (deterministik, dt_s ile)
QTimer 30 FPS:
   idx = result.state_at(t)  (O(1) round(t/dt))
   frame'i states[idx]'ten kur (yukarıdaki tablo)
Hız çarpanı = t adımını ölçekle (1×/2×/5×/10×)
```
Bu, benim önceki commit'teki kendi-yazımı `_anim_xyz`/`_anim_tick` mekanizmasının
yerini alır; çünkü o, gerçek payout dinamiğini ve katman büyümesini modellemiyor.

---

## J. Riskler ve Notlar

1. **`simulate_winding` performansı:** 100 katman × yüksek çözünürlük büyük
   `states[]` üretebilir. `dt_s` ve `deposition_grid` ile ölçeklenmeli; R3
   preflight bu motora da uygulanmalı (S8'de doğrulanır).
2. **Ribbon mesh çokgen sayısı:** Yüksek katmanda mesh patlaması — D5 LOD
   (≤8 katman tam ribbon, üstü seçili katman) zorunlu.
3. **Tutarlılık:** `generate_path` (saf) ile `winding_twin` (büyümeli) farklı
   çap varsayıyor. UI artık twin'i kaynak almalı; `generate_path` yalnızca tek
   katman/G-code yolu için kalmalı — aksi halde iki ayrı "gerçek" oluşur.
4. **Determinizm korunmalı:** `simulate_winding` "aynı girdi → bit-aynı" söz
   veriyor (satır 185-187); replay/test bu garantiye yaslanmalı.

---

## K. Sonuç / Tavsiye

Hedef "CadWind/Cadfil/Taniq seviyesi gerçek dijital ikiz" için en kısa yol bir
backend yeniden yazımı **değildir**. Backend zaten oradadır ve test edilmiştir.
Öncelik sırası net:

1. **cam_engine facade** (S1) — her şeyin omurgası.
2. **STL eksen çözücü** (S2) — tek gerçek backend boşluğu, rakip farkının çekirdeği.
3. **Twin entegrasyonu** (S3) — naif animasyonu doğrulanmış `TwinState` ile değiştir.
4. **Ribbon + Shell + Heatmap** (S4-S6) — görsel sadakat, mevcut veri zaten var.
5. **Slip overlay + stres testi** (S7-S8).

> Bir cümlede: *"Dijital ikiz beyni hazır; ona göz ve kas (UI render + STL gözü)
> takıyoruz."*

**Bu rapor onaylanırsa S1'den (cam_engine facade) başlanması önerilir.**
