# RECIPE_LIBRARY_ARCHITECTURE.md

> **Faz 25 — Sprint 4 ön tasarım raporu**
> Recipe Library'yi *kayıt sistemi* olmaktan çıkarıp, üretim zincirinin
> **veri omurgası** hâline getiren mimari.
> **Durum:** TASLAK — onay bekliyor. Bu rapor onaylanmadan **kod yazılmayacak.**
> Son güncelleme: 2026-06-15

---

## 0. TL;DR

Recipe Library, dokuz mevcut sistemin (Engineering Layer, Optimizer, LayeredRecipe,
Project History, Undo/Redo, Autosave, CAM Generator, Digital Twin, Production Report)
arasında **tek kanonik tasarım niyeti deposu** olacak.

Temel ilke — **üç katmanlı sorumluluk ayrımı**:

| Sorumluluk | Ne | Örnek |
|---|---|---|
| **SAKLA** (own) | Tasarım niyeti + provenans + kabul KPI'ları | LayerDef yığını, mandrel bağlamı, optimizer skoru |
| **REFERANSLA** (reference) | Başka kütüphanenin sahibi olduğu şeyler | Malzeme anahtarı, makine profili, ebeveyn reçete |
| **YENİDEN ÜRET** (regenerate) | Tamamen türetilebilir, büyük, makineye özel çıktı | WindingPath, G-code, üretim raporu |

Hedeflenen iş akışı (kullanıcının tarif ettiği zincir) **tam olarak desteklenir** ve
sonunda bir **geri besleme döngüsü** ile kapanır:

```
Engineering Layer → Optimizer → LayeredRecipe → Recipe Library
   → CAM Generator → Machine Execution → Digital Twin → Production Report
   → (as-built KPI'lar) ──┐
                          └──> Recipe Library sürümüne geri yazılır
```

---

## 1. Mevcut Mimari Değerlendirmesi (9 Sistem)

Aşağıdaki tablo, kod tabanında **bugün var olan** gerçek arayüzlere dayanır
(varsayım değil — `recipe_optimizer.py`, `manual_layer_sequencer.py`,
`digital_twin.py`, `manufacturing_report.py`, `cam_panel.py` vb. okunarak çıkarıldı).

| # | Sistem | Durum | Kanonik veri yapısı | Anahtar köprü |
|---|---|---|---|---|
| 1 | **Recipe Library** | ✅ var (düz) | `Recipe` + `RecipeDB` (SQLite/WAL/checksum) | `recipe_id`+`version` |
| 2 | **LayeredRecipe** | 📐 tasarlandı (SPRINT3_DESIGN §4) | `LayeredRecipe` + `LayerDef` | — (Sprint 4) |
| 3 | **Project History** | ✅ var (Sprint 3) | `AutoSaveVersion` + Sürüm Geçmişi sekmesi | `load_project_dict()` |
| 4 | **Undo/Redo** | ✅ var (Sprint 2) | `QUndoStack` + 10 komut sınıfı | `ReplaceStackCommand` |
| 5 | **Autosave** | ✅ var (Sprint 3) | `AutoSaveManager` (30 s debounce, 5 sürüm) | `force_save()` |
| 6 | **CAM Generator** | ✅ var | `geometry → path → motion → gcode` | `CAMPanel.set_layer_stack(dict)` |
| 7 | **Engineering Layer** | ✅ var | `LayerSpec`/`LayerStack`, `VesselDesignReport` | `LayerStack.to_dict()` |
| 8 | **Optimizer** | ✅ var | `optimize_recipe → List[OptimizedRecipe]` | `OptimizedRecipe.schedule` |
| 9 | **Digital Twin** | ✅ var | `DigitalTwin`/`TwinState`, `ProductionEngine` | `gcode_yukle()` + `correlation_rms()` |

### 1.1 Kritik gözlem: "katman yığını dict" zaten lingua franca

Sistemler arası tüm katman alışverişi **tek bir sözlük formatından** geçiyor:

```python
{
  "versiyon": "1.0",
  "default_friction_mu": 0.30,
  "next_id": N,
  "layers": [ LayerSpec.to_dict(), ... ]   # id, type, alpha_deg, fitil_genisligi_mm,
                                           # cakisma_pct, thickness_mm, feed_mm_s,
                                           # spindle_rpm, friction_mu, strategy, label, notes
}
```

Bu format şu noktalarda üretiliyor/tüketiliyor:
- `KatmanDizilimPaneli.get_stack_dict()` → üretir
- `TabakaYoneticisiPanel._report_to_stack_dict()` → ENG-7 raporundan üretir
- `CAMPanel.set_layer_stack()` → tüketir (her katman için ayrı `generate_path`)
- `proje_yoneticisi` Schema v2.0 `katman_yigini` → diske yazar
- `ReplaceStackCommand` → undo/redo'da snapshot olarak saklar

**Sonuç:** Recipe Library'nin `LayerDef` yığını ile bu dict **birebir alanları
örtüştürmeli** ki sıfır-kayıp köprü kurulabilsin. Bu zaten SPRINT3_DESIGN §4'te
hedeflenmişti; bu rapor onu somutlaştırıyor.

### 1.2 Kritik boşluk: Optimizer çıktısı henüz kütüphaneye akmıyor

`optimize_recipe()` zengin bir çıktı veriyor ama hiçbir kalıcılık katmanına
bağlı değil:

```python
OptimizedRecipe(
  schedule = LayerSchedule(families=[
      AngleFamily(alpha_deg, n_layer_sets, strategy, symmetric, overlap_pct), ...
  ]),
  score = RecipeScore(f_time, f_thickness, f_coverage, f_cost, f_manufacturability, combined),
  cycle = CycleBreakdown(...),  cost = CostBreakdown(...),
  achieved_thickness_mm, coverage_pct, manufacturability_score, warnings, rank
)
```

`AngleFamily` → `LayerDef` adaptörü **Sprint 4'ün kalbi** olacak (bkz. §3.4).
`score`, `achieved_thickness_mm`, `coverage_pct` ise **provenans/kabul KPI'ı**
olarak saklanacak (bkz. §2.1-C).

---

## 2. (A) Sistem Veri Akış Diyagramı

### 2.1 Ana omurga (kullanıcının tarif ettiği zincir)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ENGINEERING LAYER                                                          │
│  TabakaYoneticisiPanel ──size_pressure_vessel()──> VesselDesignReport      │
│  KatmanDizilimPaneli   ──manuel düzenleme──────────> LayerStack            │
│         │ get_stack_dict()  /  _report_to_stack_dict()                     │
│         ▼                                                                  │
│   [ katman yığını dict ]  ◄── lingua franca (§1.1)                         │
└─────────┬───────────────────────────────────┬────────────────────────────┘
          │ (manuel yol)                        │ (otomatik yol)
          │                                     ▼
          │                       ┌─────────────────────────────────────┐
          │                       │ OPTIMIZER                           │
          │                       │  RecipeInput(profile, material,     │
          │                       │   constraints, objective)           │
          │                       │   optimize_recipe() →               │
          │                       │   List[OptimizedRecipe]             │
          │                       │   (.schedule = LayerSchedule)       │
          │                       └──────────────┬──────────────────────┘
          │                                       │ AngleFamily→LayerDef adaptörü (§3.4)
          ▼                                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ LayeredRecipe  (recipe_id, version, katmanlar=[LayerDef], mandrel bağlamı, │
│                 kaynak=manuel|optimizer|ai_advisory, skor, provenans)      │
└─────────┬──────────────────────────────────────────────────────────────────┘
          │ save_layered()  /  load_layered()
          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ RECIPE LIBRARY  (RecipeDB.layered_recipes tablosu — SQLite/WAL/checksum)   │
│   • SAKLA: tasarım niyeti + provenans + as-built KPI                       │
│   • REFERANSLA: malzeme anahtarı, makine profili, ebeveyn sürüm            │
│   • Sürümleme: v1 → v2 → v3 (append-only, §4)                              │
└─────────┬──────────────────────────────────────────────────────────────────┘
          │ LayerDef[] → katman yığını dict → CAMPanel.set_layer_stack()
          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ CAM GENERATOR  (YENİDEN ÜRET — saklanmaz)                                   │
│   MandrelProfile → WindingPathParams → generate_path() → WindingPath        │
│   → plan_motion() → MotionSegment[] → generate_gcode() → GCodeProgram        │
└─────────┬──────────────────────────────────────────────────────────────────┘
          │ G-code metni
          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ MACHINE EXECUTION                                                          │
│   ProductionEngine.gcode_yukle() → GcodeMove[] timeline                     │
│   _tick() @50Hz → koordinatGuncellendi(x,y,z,a)                            │
└─────────┬──────────────────────────────────────────────────────────────────┘
          │ setpoint + (real mode) ESP32 telemetri
          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ DIGITAL TWIN                                                               │
│   DigitalTwin (100Hz birinci-derece dinamik) ↔ gerçek telemetri            │
│   correlation_rms() → {rms_x, rms_T, rms_phi}                              │
└─────────┬──────────────────────────────────────────────────────────────────┘
          │ telemetri oturumu + korelasyon
          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ PRODUCTION REPORT                                                          │
│   generate_manufacturing_report() → ProductionManufacturingReport          │
│   (is_manufacturable, material, cycle_time, hard_stops, warnings)          │
│   + TelemetryDB oturumu (gerçek teslimat, CRC, süre)                       │
└─────────┬──────────────────────────────────────────────────────────────────┘
          │  ★ GERİ BESLEME DÖNGÜSÜ ★  as-built KPI'lar
          └────────────────────────> Recipe Library: recipe_id+version'a
                                      "ProductionOutcome" kaydı olarak iliştirilir
```

### 2.2 "Sadece depo değil" — neden geri besleme döngüsü kritik?

Recipe Library'yi pasif bir dosya kasası olmaktan ayıran şey, son oktur:
**üretim sonucu ölçülen KPI'lar reçete sürümüne geri yazılır.** Böylece:

- Bir reçetenin *tahmini* (optimizer skoru, `achieved_thickness_mm`) ile
  *gerçekleşeni* (Digital Twin `correlation_rms`, gerçek cycle time, gerçek
  teslimat %) yan yana saklanır.
- Gelecekteki **AI Recipe Optimizer** bu (tasarım → sonuç) çiftlerini eğitim
  verisi olarak okuyabilir. Bu, AD-005'i (AI yalnızca tavsiye) ihlal etmez:
  geri besleme sadece *gözlem* kaydıdır, motora doğrudan komut değildir.

---

## 3. (B) Recipe Library: Sakla / Referansla / Yeniden Üret

### 3.1 SAKLA — Kütüphanenin sahibi olduğu, bayt olarak kalıcı veriler

| Veri | Tip | Neden saklanır |
|---|---|---|
| Kimlik | `recipe_id: str` ("lr_" önekli), `version: int` | Sürüm ekseni |
| Üst veri | `name`, `aciklama`, `etiketler: list[str]` | Arama/filtre |
| **Tasarım niyeti** | `katmanlar: list[LayerDef]` | **Kanonik kaynak** — her şey buradan türer |
| Mandrel bağlamı | `mandrel_tip`, `cap_mm`, `uzunluk_mm`, dome params | Reçetenin doğrulandığı geometri |
| Provenans | `kaynak` (manuel/optimizer/ai_advisory), `skor: float` | İzlenebilirlik + AI hazırlığı |
| Optimizer girdisi | `constraints_json`, `objective_json` (opsiyonel) | Tekrar-üretilebilirlik (aynı girdi → aynı reçete) |
| **Kabul KPI'ı (cache)** | `achieved_thickness_mm`, `coverage_pct`, `burst_MPa`, `SF`, `cycle_time_s`, `cost_usd` | Yeniden hesaplamadan göz atma/karşılaştırma |
| Zaman | `olusturma: str` (ISO 8601) | Sıralama, geçmiş |
| Bütünlük | `checksum: str` (SHA-256, mevcut `RecipeDB` deseni) | Bozulma tespiti |

> **Tasarım kuralı:** `LayerDef` alanları `LayerSpec` ile **birebir** hizalanır
> (SPRINT3_DESIGN §4 sözü). Böylece `LayerDef ↔ katman yığını dict ↔ LayerSpec`
> üçgeni sıfır-kayıp döner.

### 3.2 REFERANSLA — Başka kütüphanenin sahibi, sadece anahtar tutulur

| Referans | Anahtar alan | Gerçek sahibi |
|---|---|---|
| Malzeme | `malzeme_anahtari: str` (örn. `"carbon_t700_epoxy_pv"`) | `material_database` |
| Makine profili | `makine_profili: str` | makine profili kütüphanesi (`MachineConfig`) |
| Ebeveyn reçete | `parent_recipe_id`, `parent_version` | Recipe Library'nin kendisi (soyağacı) |
| Kaynak optimizer çalışması | `optimizer_run_id` (opsiyonel) | optimizer oturum kaydı |

**Neden referans, embed değil?** Malzeme özellikleri (ρ, E, σ_design) veya makine
limitleri değişirse, reçete bunları **kopyalamış olmamalı** — aksi hâlde kütüphane
eskimiş malzeme verisiyle dolar. Referans, "tek doğru kaynak" ilkesini korur.
(Karşıt durum: eğer malzeme **silinirse** reçete kırılmasın diye, referans + son
bilinen özet `malzeme_ozet_json` opsiyonel olarak cache'lenebilir — kararı §7'de.)

### 3.3 YENİDEN ÜRET — Asla saklanmaz, talep üzerine hesaplanır

| Türetilen | Üreten | Neden saklanmaz |
|---|---|---|
| `WindingPath` (noktalar) | `generate_path(LayerDef + MandrelProfile)` | Büyük (binlerce nokta); CAM motoru geliştikçe eskir |
| `MotionSegment[]` | `plan_motion(WindingPath)` | Tamamen türev |
| **G-code** (`GCodeProgram`) | `generate_gcode(segments, path, MachineConfig)` | **Makineye özel** — grbl/fanuc/mach3 her hedef için ayrı; saklamak çoğullaşma yaratır |
| Üretim raporu | `generate_manufacturing_report()` | Doğrulayıcılar geliştikçe değişir |
| BuildUp / deposition haritası | `LayerStack.apply_build_up()` | Türev, büyük |
| 3D önizleme | `Winding3DPanel.set_cam_path()` | Görsel, türev |

**İlke (CAD/CAM endüstri standardı):** Feature tree / reçete saklanır; toolpath
yeniden üretilir. Bizde **LayerDef yığını = feature tree**, **G-code = toolpath**.
Bu, hem DB şişmesini önler hem de "tasarım niyeti kanoniktir" ilkesini korur.

> **İstisna (opsiyonel, §7 kararı):** Üretime *gönderilmiş* (released) bir sürüm
> için, fiilen makineye giden G-code'un bir **hash'i** (G-code'un kendisi değil)
> `released_gcode_sha` olarak saklanabilir — denetim izi için. Tam G-code metni
> yine de bir *artefakt dosyası* olarak Project'e gider, kütüphaneye değil.

---

## 4. (C) LayeredRecipe Sürümleme Sistemi

### 4.1 İki seviyeli kimlik + append-only geçmiş

```
recipe_id = "lr_tank_A"          (kararlı kimlik)
   ├── version 1   kaynak=optimizer   skor=0.182   2026-06-15T10:02  "ilk optimizer önerisi"
   ├── version 2   kaynak=manuel      skor=—       2026-06-15T11:14  "α 55→52, hoop +2 (mühendis ayarı)"
   └── version 3   kaynak=manuel      skor=—       2026-06-16T09:30  "burst SF 2.1→2.4 için skin katman"
                   ▲ latest = MAX(version)
```

**Kurallar:**
1. `UNIQUE(recipe_id, version)` — her sürüm satırı **değişmez** (append-only).
2. `version` = ilgili `recipe_id` altında monoton artan tamsayı.
3. "En son" = `MAX(version)`. Üretime onaylı sürüm `released: bool` bayrağıyla
   sabitlenir (pin).
4. **Geri dönüş asla tarihi değiştirmez:** v2'ye dönmek = v2'nin içeriğini kopyalayan
   **yeni bir v4** yaratmak. Bu, Project History'nin (Sprint 3) "önce güvenlik
   yedeği al, sonra yükle" desenini birebir izler.

### 4.2 Sürüm soyağacı + değişiklik özeti

Her sürüm satırı şunları taşır:
- `parent_version: int | None` — hangi sürümden türedi (fork/derivation grafiği)
- `change_summary: str` — insan ya da otomatik üretilen değişiklik notu
- `kaynak: str` — bu sürümü kim üretti (manuel/optimizer/ai_advisory)

### 4.3 Sürüm farkı (diff)

İki sürüm arası **yapısal diff** (yeniden üretim gerektirmez, sadece `LayerDef`
listelerini karşılaştırır):

```
diff(v1, v2):
  Katman 3:  alpha_deg  55.0 → 52.0
  Katman 6:  + EKLENDİ   hoop 89.5°, t=0.30
  Skor:      0.182 → (manuel, skor yok)
  Kalınlık:  4.12mm → 4.40mm  (cache KPI)
```

UI'da "Sürüm Geçmişi" sekmesinin (Sprint 3) reçeteye özel bir kardeşi:
**"Reçete Sürümleri"** listesi — her satır `vN · kaynak · zaman · change_summary ·
Δskor`. Çift tık → diff görünümü. "Bu sürüme dön" → güvenlik-yedekli yeni sürüm.

### 4.4 Mevcut sistemlerle hizalama

- **RecipeDB** zaten `recipe_id`+`version` ile çalışıyor → desen kanıtlı.
- **Project History** revert semantiği (güvenlik yedeği önce) → birebir tekrar
  kullanılır.
- **Undo/Redo** `ReplaceStackCommand(old_stack, new_stack)` → "reçeteyi projeye
  uygula" işlemi tek undo adımı olur; reçete sürümü değişmez (kütüphane salt-okunur,
  yalnızca explicit "kaydet" ile yeni sürüm yazılır).

---

## 5. (D) Endüstriyel Reçete Kavramı Analizi

Hedef: **Recipe / Pattern / Job / Program / Project** kavramlarının lider
yazılımlarda nasıl ayrıldığını anlamak ve bizim eşlememizi netleştirmek.

### 5.1 Yazılım bazında

| Yazılım | Üretici | Kavram ayrımı (gözlemlenen) | Güçlü yön |
|---|---|---|---|
| **CadWind** | Material SA (BE) | **Project** = mandrel + setup; **Pattern** = sarma deseni (bant yerleşimi, *pattern number*, dwell, devre kapanışı); çıktı = makine programı | Pattern/dwell ve devre kapanış matematiği |
| **Cadfil** | Crescent Consultants (UK) | **Design/Datafile** ↔ **Machine file** ayrı; **QuickCAD** şablonları (boru, tank, dirsek); **Payout** kinematiği birinci sınıf | Şablon kütüphanesi + payout (göz) modeli |
| **TaniqWind Pro** | Taniq (NL) | **Recipe** = FEA-optimize tam tasarım; **Job** = üretim emri; bulut tabanlı simülasyon→reçete→makine | Simülasyon-güdümlü reçete, optimizasyon |
| **FiberGrafiX** | McClean Anderson (US) | **Program** üretimi merkez; pattern editörü, eye position; bant-genişliği/pattern odaklı | Makineye sıkı bağlı program + pattern editör |

### 5.2 Ortak taksonomi ve bizim eşlememiz

| Kavram | Endüstri tanımı | Bizdeki karşılık | Durum |
|---|---|---|---|
| **Project** | Konteyner: parça + müşteri + revizyonlar + tüm artefaktlar | `.fwp` Schema v2.0 proje dosyası | ✅ var (Sprint 1) |
| **Recipe** | Süreç tasarım niyeti: katman planı + malzeme + kür; geometri-farkında ama **makineden bağımsız** | `LayeredRecipe` | 📐 Sprint 4 |
| **Pattern** | Sarma deseninin geometrisi: devreler, *pattern number*, dwell, bant ilerlemesi, başlangıç fazı — fiberin mandrel etrafında nasıl kapandığı | path_generator içinde **örtük** (`clairaut_circuit_count`, `n_circuits`) — birinci sınıf nesne **DEĞİL** | ⚠️ **BOŞLUK** |
| **Program / Job** | Makineye hazır çıktı: belirli kontrolör için G-code; çalıştırılabilir örnek | `GCodeProgram` (yeniden üretilir) + `ProductionEngine` yükü = "Job çalıştırması" | ✅ var (yeniden üretilir) |

### 5.3 En kritik bulgu: "Pattern" birinci sınıf değil

Endüstri lideri yazılımlar **Recipe (ne/neden, taşınabilir)** ile **Pattern (sarma
geometrisi/kapanışı)** ile **Program/Job (makineye özel, tek kullanımlık)** arasında
net ayrım yapar. Bizde Pattern, CAM yol üretim adımının içine gömülü — *pattern
number*, *dwell açısı*, *bant ilerleme*, *başlangıç fazı* gibi parametreler
`LayerDef`'te açık alan değil.

**Öneri (Sprint 4 kapsamında değerlendirilecek, zorunlu değil):**
`LayerDef`'e isteğe bağlı **pattern alanları** eklemek —
`pattern_number: int`, `dwell_deg: float`, `band_advance: int`, `start_phase_deg: float`.
Bunlar None olduğunda mevcut davranış korunur (geriye uyumlu). Dolu olduğunda reçete
**farklı makinelerde tekrar-üretilebilir** olur — bu, TaniqWind/CadWind paritesi için
gereken adımdır.

> Bu, wire protokolünü etkilemez (firmware/telemetri ile ilgisiz) ve mevcut
> `katman yığını dict`'e ekleme-only alanlardır → Schema v2.0 ileri-uyumlu.

### 5.4 Eksik kaldığımız noktalar (proje/reçete/UX/hata kurtarma)

| Eksen | Eksik | Önem | Sprint |
|---|---|---|---|
| Proje yönetimi | Proje şablonları (hazır tank/boru geometrileri) | Orta | 5 |
| Reçete yönetimi | **Çok-katmanlı reçete kalıcılığı** | **Yüksek** | **4 (bu)** |
| Reçete yönetimi | Reçete dışa/içe aktarma (JSON paylaşımı) | Orta | 4/5 |
| Reçete yönetimi | **Pattern** birinci sınıf nesne | Orta-Yüksek | 4 (ops.) / 5 |
| UX | 12 adımlı akış vs rakip 4-6 adım; Tasarım Merkezi / Entegre / CAM sekme çakışması | **Yüksek (UX borcu)** | 5 |
| Hata kurtarma | Sürüm-seçilebilir kurtarma + canlı donanım kurtarma | ✅ **bizde benzersiz** (Sprint 3 + Faz 18) | — |

---

## 6. Veri Sözleşmeleri (öneri — onay sonrası kodlanacak)

### 6.1 `LayerDef` (LayerSpec ile birebir hizalı)

```python
@dataclass
class LayerDef:
    layer_type: str = "helical"      # helical | hoop | polar | transition | skin_finish
    alpha_deg: float = 55.0
    fitil_genisligi_mm: float = 6.0
    cakisma_pct: float = 5.0
    thickness_mm: float = 0.30
    feed_mm_s: float = 80.0
    spindle_rpm: float = 60.0
    friction_mu: float = 0.0         # 0 = geodezik
    strategy: str = "geodesic"       # geodesic | non_geodesic
    label: str = ""
    notes: str = ""
    # — opsiyonel Pattern alanları (§5.3; None → mevcut davranış) —
    pattern_number: Optional[int] = None
    dwell_deg: Optional[float] = None
    band_advance: Optional[int] = None
    start_phase_deg: Optional[float] = None
```

### 6.2 `LayeredRecipe`

```python
@dataclass
class LayeredRecipe:
    recipe_id: str                   # "lr_" önekli
    version: int
    name: str
    aciklama: str = ""
    etiketler: list[str] = field(default_factory=list)
    # mandrel bağlamı
    mandrel_tip: str = "silindir"
    cap_mm: float = 100.0
    uzunluk_mm: float = 300.0
    kubbe_yukseklik_mm: float = 0.0
    # çekirdek
    katmanlar: list[LayerDef] = field(default_factory=list)
    # referanslar (embed değil)
    malzeme_anahtari: str = ""
    makine_profili: str = ""
    parent_recipe_id: str = ""
    parent_version: int = 0
    # provenans
    kaynak: str = "manuel"           # manuel | optimizer | ai_advisory
    skor: float = 0.0
    constraints_json: str = ""       # optimizer girdisi (tekrar-üretim)
    objective_json: str = ""
    change_summary: str = ""
    released: bool = False
    # kabul KPI cache (yeniden üretmeden göz atma)
    achieved_thickness_mm: float = 0.0
    coverage_pct: float = 0.0
    burst_MPa: float = 0.0
    SF: float = 0.0
    cycle_time_s: float = 0.0
    cost_usd: float = 0.0
    olusturma: str = ""              # ISO 8601
```

### 6.3 SQLite şeması (mevcut `RecipeDB`'ye eklenir, eski `recipes` tablosuna dokunulmaz)

```sql
CREATE TABLE IF NOT EXISTS layered_recipes (
    recipe_id   TEXT NOT NULL,
    version     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    json_body   TEXT NOT NULL,       -- tam LayeredRecipe serileştirmesi
    checksum    TEXT NOT NULL,       -- SHA-256 (RecipeDB deseni)
    kaynak      TEXT NOT NULL,
    skor        REAL DEFAULT 0,
    parent_version INTEGER DEFAULT 0,
    released    INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL,       -- ISO 8601
    UNIQUE(recipe_id, version)
);
CREATE INDEX IF NOT EXISTS idx_lr_recipe_id ON layered_recipes(recipe_id);

-- geri besleme döngüsü (§2.2); reçete sürümüne as-built sonuç bağlar
CREATE TABLE IF NOT EXISTS production_outcomes (
    recipe_id     TEXT NOT NULL,
    version       INTEGER NOT NULL,
    session_id    TEXT NOT NULL,     -- TelemetryDB oturumu
    rms_x         REAL,  rms_T REAL,  rms_phi REAL,   -- DigitalTwin korelasyonu
    actual_cycle_s REAL,
    delivery_pct  REAL,  crc_errors INTEGER,
    is_manufacturable INTEGER,
    created_at    TEXT NOT NULL
);
```

### 6.4 `RecipeDB` yeni metotları

```python
save_layered(rec: LayeredRecipe) -> bool          # checksum + UNIQUE upsert
load_layered(recipe_id, version=None) -> Optional[LayeredRecipe]   # None → en son
list_layered(filter_tags=None) -> List[dict]      # recipe_id, version, name, kaynak, skor, created_at
list_versions(recipe_id) -> List[dict]            # sürüm geçmişi
diff_versions(recipe_id, va, vb) -> List[dict]    # yapısal katman farkı
record_outcome(recipe_id, version, outcome) -> bool   # geri besleme
```

### 6.5 Adaptörler (köprüler)

```python
# Optimizer → Library
def layered_from_optimized(rec: OptimizedRecipe, *, name, mandrel, material_key,
                           constraints, objective) -> LayeredRecipe:
    katmanlar = []
    for fam in rec.schedule.families:                 # AngleFamily
        for _ in range(fam.n_layer_sets):
            katmanlar.append(LayerDef(layer_type=fam.strategy, alpha_deg=fam.alpha_deg,
                                      cakisma_pct=fam.overlap_pct, strategy="geodesic"))
            if fam.symmetric and fam.strategy != "hoop":
                katmanlar.append(LayerDef(layer_type=fam.strategy, alpha_deg=-fam.alpha_deg,
                                          cakisma_pct=fam.overlap_pct))
    return LayeredRecipe(..., katmanlar=katmanlar, kaynak="optimizer",
                         skor=rec.score.combined,
                         achieved_thickness_mm=rec.achieved_thickness_mm,
                         coverage_pct=rec.coverage_pct, ...)

# Library → CAM  (mevcut lingua franca'ya)
def layered_to_stack_dict(rec: LayeredRecipe) -> dict:
    return {"versiyon": "1.0", "default_friction_mu": 0.30,
            "next_id": len(rec.katmanlar),
            "layers": [layerdef_to_layerspec_dict(l, i) for i, l in enumerate(rec.katmanlar)]}
    # → CAMPanel.set_layer_stack(...) ya da proje 'katman_yigini'
```

---

## 7. Açık Kararlar (onay sırasında netleştirilecek)

| # | Karar | Öneri |
|---|---|---|
| K1 | Malzeme silinirse reçete kırılsın mı? | Referans + opsiyonel `malzeme_ozet_json` cache (kırılmaz, ama "eski" işaretlenir) |
| K2 | Pattern alanları Sprint 4'te mi yoksa 5'te mi? | `LayerDef`'e **alan olarak** Sprint 4'te ekle (None-default, geriye uyumlu); UI editörü Sprint 5 |
| K3 | Geri besleme tablosu (`production_outcomes`) Sprint 4 kapsamında mı? | Şema + `record_outcome()` Sprint 4; UI gösterimi Sprint 5 |
| K4 | Released G-code hash'i saklansın mı? | Evet, sadece `released=True` sürümlerde `released_gcode_sha` |
| K5 | Reçete JSON dışa/içe aktarma | Sprint 4'e küçük ek (tek dosya export/import) |

---

## 8. Sprint 4 Uygulama Sırası (onay sonrası)

1. **Backend veri modeli:** `LayerDef`, `LayeredRecipe` dataclass'ları + `to_dict/from_dict`
   (bilinmeyen alan toleranslı, `LayerSpec.from_dict` deseni).
2. **Persistence:** `RecipeDB`'ye `layered_recipes` + `production_outcomes` tabloları
   ve metotlar (checksum/WAL korunur, eski `recipes` tablosu **dokunulmaz**).
3. **Adaptörler:** `layered_from_optimized`, `layered_to_stack_dict`,
   `layerdef_to_layerspec_dict` + ters yön.
4. **UI:** "Reçete Kütüphanesi" paneli — liste/filtre/etiket, sürüm geçmişi + diff,
   "Projeye Uygula" (→ `ReplaceStackCommand` ile tek undo), "Optimizer'dan Kaydet".
5. **Geri besleme:** Production Report tamamlanınca `record_outcome()` çağrısı.
6. **Doğrulama:** `test_recipe_library.py` — roundtrip, sürümleme (v1→v2→v3),
   diff, optimizer→recipe→stack_dict→CAM sıfır-kayıp, geri besleme; +
   `phase17_d2_validation` regresyonu 8/8 korunur.
7. **AD-005 koruması:** `kaynak="ai_advisory"` reçeteler yalnızca **öneri**;
   üretime ancak kullanıcı onayı + `SafetyValidator` zincirinden geçer.

---

## 9. Korunan Mimari İlkeler

- **Wire protokolü** (0xAA 0x55 + CRC-16/CCITT): bu çalışma firmware/telemetri
  katmanına **dokunmaz**.
- **Schema v2.0 ileri-uyumluluk:** tüm yeni alanlar ekleme-only; eski `.fwp` ve
  autosave dosyaları kırılmaz.
- **Tek doğru kaynak:** tasarım niyeti (LayerDef) kanonik; G-code/yol türev.
- **AD-005:** AI çıktısı yalnızca tavsiye; doğrudan motora gitmez.
- **Append-only geçmiş:** sürümler değişmez; geri dönüş = yeni sürüm.

---

**Onay isteği:** Yukarıdaki mimari (özellikle §3 sakla/referansla/yeniden-üret
ayrımı, §4 sürümleme, §6 veri sözleşmeleri ve §7 açık kararlar K1-K5) onaylanırsa
Sprint 4 implementasyonuna §8 sırasıyla başlanacaktır. Onaya kadar **kod yazılmaz.**
