# SPRINT 3 — Teknik Tasarım Raporu
## Auto-Save + Crash Recovery + Project History + Recipe Library Mimarisi

> Faz 25 (Project Workflow Hardening), Sprint 3.
> Sprint 1 (`02ff3f6`): `_del_row` fix + Schema v2.0 + dirty flag.
> Sprint 2 (`c87fd9b`): Merkezi QUndoStack Undo/Redo.
> Bu rapor implementasyondan ÖNCE çıkarılmıştır (kullanıcı direktifi).

---

## 1. Auto-Save + Crash Recovery Mimarisi

### 1.1 Bileşenler

```
app/autosave_manager.py
├── AutoSaveVersion          # dataclass: path, zaman damgası, proje adı, özet
├── AutoSaveManager(QObject) # debounce + rotasyon + kilit + sürüm listesi
└── RecoveryWizard(QDialog)  # açılış kurtarma sihirbazı (sürüm seçimli)
```

### 1.2 AutoSaveManager

| Özellik | Karar | Gerekçe |
|---|---|---|
| Tetikleme | **30 s debounce** — ilk kirli sinyalinde tek atımlık QTimer başlar | Sürekli düzenlemede bile en geç 30 s'de bir yedek; boşta hiç yazma |
| Veri kaynağı | `data_provider()` callback → `ProjeYoneticisi._read_form()` | Tam v2.0 dict (katman_yigini + entegre + sarma + mandrel) — Sprint 1 sağlayıcıları yeniden kullanılır |
| Koşul | `dirty_provider()` True ise yaz | Temiz projede gereksiz disk I/O yok |
| Dizin | `~/.flamenet_sar/autosave/` (testlerde enjekte edilebilir `base_dir`) | Kullanıcı projelerinden ayrı, çökme sonrası hayatta kalır |
| Dosya adı | `{proje_slug}_{YYYYmmdd_HHMMSS}.fwp.bak` | Zaman damgası dosya adında — Project History doğrudan okur |
| Rotasyon | **En yeni 5 sürüm** saklanır; 6. yazımda en eski silinir | Disk sınırlı; 5 sürüm ≈ 2,5 dakikalık değişiklik penceresi |
| Atomik yazım | Önce `.tmp`, sonra `os.replace()` | Yazım sırasında çökme yarım dosya bırakmaz |
| UI thread kuralı | Yazım UI thread'de (<5 ms, JSON ~10 KB) | AD-008 safety thread'e dokunmaz; telemetri yolundan tamamen ayrı |

### 1.3 Çökme Tespiti — Oturum Kilidi

```
~/.flamenet_sar/session.lock        # açılışta oluştur, temiz kapanışta sil
```

- Açılışta kilit **varsa** → önceki oturum çökmüş → RecoveryWizard göster.
- closeEvent temiz tamamlanırsa kilit silinir.
- Headless/test modunda sihirbaz açılmaz (modal diyalog testleri kilitler).

### 1.4 RecoveryWizard (Kurtarma Sihirbazı)

Açılışta çökme tespit edilirse modal diyalog:

```
┌─ Çökme Kurtarma ─────────────────────────────────────────┐
│ Önceki oturum düzgün kapanmadı.                          │
│ Otomatik kaydedilen sürümlerden birini geri yükleyin:    │
│                                                          │
│  ● 2026-06-12 15:21:34  "Basınç Tankı A"  (7 katman)     │
│  ○ 2026-06-12 15:20:58  "Basınç Tankı A"  (6 katman)     │
│  ○ 2026-06-12 15:19:11  "Basınç Tankı A"  (6 katman)     │
│                                                          │
│  [Seçili Sürümü Geri Yükle]   [Yoksay — Yeni Oturum]     │
└──────────────────────────────────────────────────────────┘
```

- Kullanıcı **hangi sürümü yükleyeceğini seçer** (en yeni varsayılan).
- Geri yükleme `ProjeYoneticisi.load_project_dict()` → `_migrate_project()`
  → `projeYuklendi` yayını → tüm paneller (katman dizilimi dahil) geri gelir.
- "Yoksay" sürümleri SİLMEZ — Project History panelinden hâlâ erişilebilir.

---

## 2. Project History (Sürüm Geçmişi)

### 2.1 Tasarım

- **Yerleşim:** ProjeYoneticisiPanel içinde yeni **"Sürüm Geçmişi"** sekmesi
  (mevcut Bilgi/Mandrel/Özet sekmelerinin yanına). Ayrı ana sekme açmak 14
  sekme sorununu büyütürdü (bkz. §5 UX analizi).
- Her auto-save bir **zaman damgalı sürümdür**; liste en yeniden eskiye.
- Satır formatı: `🕒 {tarih saat} — {proje adı} ({n} katman)`
- **"Bu Sürüme Dön"** butonu: onay diyaloğu → `load_project_dict()` →
  mevcut durum önce güvenlik yedeği olarak yazılır (`force_save()`) —
  yanlış geri dönüş de geri alınabilir.
- "Yenile" butonu listeyi diskten tazeler.

### 2.2 Bilinçli sınırlar (basit tutulan kısımlar)

- Sürüm diff görüntüleme YOK (Sprint 3 kapsamı dışı; gelecekte katman
  sayısı/parametre karşılaştırması eklenebilir).
- Dallanma yok — doğrusal geçmiş, 5 sürüm penceresi.

---

## 3. Recovery Validation Planı

`validation_d2/test_autosave_recovery.py` — çökme senaryoları:

| # | Senaryo | Doğrulama |
|---|---|---|
| R1 | Debounce: değişiklik → 30 s (testte kısaltılmış) → dosya yazıldı | tek dosya, geçerli JSON, v2.0 |
| R2 | Rotasyon: 7 yazım → 5 dosya kalır, en yeniler korunur | dosya sayısı + zaman sırası |
| R3 | Temiz projede yazım olmaz | dirty_provider False → 0 dosya |
| R4 | Kilit: temiz kapanış → kilit yok; çökme simülasyonu → stale kilit tespit | `has_stale_lock()` |
| R5 | **Katman dizilimi** çökme sonrası eksiksiz (3 katman ±55/hoop, tüm alanlar) | LayerStack roundtrip |
| R6 | **Mandrel parametreleri** eksiksiz (tip/çap/uzunluk/konik/kubbe/HR) | entegre_panel_mandrel |
| R7 | **CAM reçeteleri** eksiksiz (entegre katman tablosu + strateji) | entegre_panel_katmanlar |
| R8 | **Makine ayarları** eksiksiz (feed/rpm/μ/çakışma/fitil) | sarma_parametreleri |
| R9 | Sihirbaz sürümleri listeler, seçim geri yüklenir | RecoveryWizard (exec'siz) |
| R10 | v1.0 autosave dosyası bile migrate edilerek açılır | `_migrate_project` zorunlu yol |
| R11 | Atomik yazım: yarım `.tmp` dosyası sürüm listesine girmez | bozuk dosya toleransı |
| R12 | Geri dönüş öncesi güvenlik yedeği alınır | force_save çağrısı |

Ek kapılar: `sprint1` 50/50 + `test_undo_redo` 70/70 + `phase17_d2` 8/8 + smoke rc=0.

---

## 4. Recipe Library Mimarisi (tasarım — implementasyon Sprint 4)

### 4.1 Veri Modeli

Mevcut `Recipe` (tek `alpha_deg`, düz alan listesi) çok katmanlı üretimi
temsil edemiyor. Yeni model — mevcut `Recipe`/`recipes` tablosuna DOKUNMADAN
paralel eklenir:

```python
# backend/persistence/layered_recipe.py  (Sprint 4)

@dataclass
class LayerDef:
    """Tek katman tanımı — LayerSpec ile alan-uyumlu (kayıpsız köprü)."""
    layer_type: str = "helical"        # helical | hoop | polar | skin
    alpha_deg: float = 55.0
    fitil_genisligi_mm: float = 6.0
    cakisma_pct: float = 5.0
    thickness_mm: float = 0.30
    feed_mm_s: float = 80.0
    spindle_rpm: float = 60.0
    friction_mu: float = 0.0           # 0 = jeodezik
    strategy: str = "geodesic"

@dataclass
class LayeredRecipe:
    recipe_id: str                     # "lr_" öneki (eski "r_" ile çakışmaz)
    version: int                       # aynı id altında artan sürüm
    name: str
    aciklama: str = ""
    etiketler: list[str] = field(default_factory=list)
    # Mandrel bağlamı (reçete hangi geometri için doğrulandı)
    mandrel_tip: str = "Silindir"
    cap_mm: float = 100.0
    uzunluk_mm: float = 300.0
    # Çok katmanlı dizilim — çekirdek
    katmanlar: list[LayerDef] = field(default_factory=list)
    # Makine bağlamı
    makine_profili: str = ""           # kontrolcü tipi (grbl/fanuc/...)
    # Köken izleme (AI hazırlığı)
    kaynak: str = "manuel"             # manuel | optimizer | ai_advisory
    skor: float = 0.0                  # optimizer/AI birleşik skoru
    olusturma: str = ""                # ISO 8601
```

### 4.2 Kalıcılık

```sql
CREATE TABLE IF NOT EXISTS layered_recipes (
    recipe_id  TEXT NOT NULL,
    version    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    json_body  TEXT NOT NULL,          -- LayeredRecipe tam serileştirme
    checksum   TEXT NOT NULL,          -- SHA-256 (mevcut RecipeDB deseni)
    created_at TEXT NOT NULL,
    UNIQUE(recipe_id, version)
);
```

- Mevcut `RecipeDB` sınıfına yeni metotlar (`save_layered`, `load_layered`,
  `list_layered`) — WAL modu ve checksum doğrulama deseni aynen korunur.
- JSON gövde = şema esnekliği: yeni LayerDef alanı eski kayıtları kırmaz
  (`from_dict` Sprint 1'deki dayanıklı desenle bilinmeyen anahtarları yutar).

### 4.3 CAM Motoru Köprüsü

```
LayeredRecipe.katmanlar ──→ LayerStack (LayerSpec.from_dict)  [kayıpsız]
                      └───→ WindingPathParams (katman başına generate_path)
```

- `LayerDef` alanları `LayerSpec` ile birebir hizalı → `katman_yigini`
  formatına ve KatmanDizilimPaneli'ne doğrudan yüklenebilir.
- "Reçeteden Üret": reçete → `apply_project` benzeri yol → Manuel Dizilim
  tablosu + Tasarım Merkezi → mevcut CAM hattı değişmeden çalışır.

### 4.4 AI Recipe Optimizer Bağlantı Noktası (gelecek)

- Mevcut `recipe_optimizer.optimize_recipe()` çıktısı `kaynak="optimizer"`,
  `skor=score.combined` ile `LayeredRecipe`'ye dönüştürülür — tek adaptör
  fonksiyonu: `layered_recipe_from_optimizer(result) -> LayeredRecipe`.
- AI advisory önerileri AYNI veri sınıfını üretir (`kaynak="ai_advisory"`).
- **AD-005 korunur:** AI çıktısı kütüphaneye yalnızca ÖNERİ olarak girer;
  üretime gönderim her zaman kullanıcı onayı + SafetyValidator yolundan.

---

## 5. Rakip İş Akışı Yeniden Analizi — Eksik Raporu

Kaynak: COMPETITIVE_AUDIT.md (commit `b8a943a`) + bu sprint odaklı yeniden
değerlendirme. Eksen: proje yönetimi / reçete yönetimi / UX / hata kurtarma.

### 5.1 Proje Yönetimi

| Yetenek | TaniqWind Pro | CadWind | Cadfil | **Biz (Sprint 3 sonrası)** |
|---|---|---|---|---|
| Proje dosyası (tam durum) | ✓ | ✓ | ✓ | ✓ v2.0 (Sprint 1) |
| Auto-save / crash recovery | ✓ | kısmi | kısmi | ✓ **bu sprint** |
| Sürüm geçmişi | ✗ | ✗ | ✗ | ✓ **bu sprint — FARKLILAŞMA** |
| Proje şablonları | ✓ | ✓ | ✓ (QuickCAD) | ✗ **EKSİK** |
| Son projeler listesi | ✓ | ✓ | ✓ | ✓ |

**Eksik kalan:** proje şablonları (yaygın tank/boru geometrileri için
hazır başlangıç). Düşük maliyet, yüksek ilk-izlenim etkisi — Sprint 5 adayı.

### 5.2 Reçete Yönetimi

| Yetenek | TaniqWind Pro | CadWind | Cadfil | **Biz** |
|---|---|---|---|---|
| Çok katmanlı reçete kaydı | ✓ | ✓ | ✓ | Sprint 4 (tasarım hazır, §4) |
| Reçete sürümleme | kısmi | ✗ | ✗ | ✓ tasarımda (version sütunu) |
| Reçete → makine programı izlenebilirliği | ✓ | ✓ | ✓ | kısmi — `kaynak`/`skor` alanları bunu kapatacak |
| Reçete paylaşımı/dışa aktarım | ✓ | ✓ | ✓ | ✗ **EKSİK** (JSON export kolay eklenir) |
| Optimizer → reçete köprüsü | ✓ (coverage auto-search) | kısmi | kısmi | ✓ tasarımda (§4.4) |

### 5.3 Kullanıcı Deneyimi

- **12 adım vs rakiplerin 4-6 adımı** hâlâ en büyük açık. Sprint 1-3
  veri bütünlüğünü çözdü; adım sayısını ÇÖZMEDİ.
- Tab 4 (Tasarım Merkezi) / Tab 5 (Üretim Tasarım Mrkz) / Tab 6 (CAM
  Üretici) örtüşmesi sürüyor — kullanıcı "gerçek" dizilimin hangisi
  olduğunu bilmiyor. **En yüksek öncelikli UX borcu.**
- Undo/Redo açığı Sprint 2 ile KAPANDI (rakip paritesi sağlandı).
- Setup wizard yok (3 rakipte de var) — kurtarma sihirbazı bu sprintte
  ilk "wizard" desenini kuruyor; setup wizard'a şablon olur.

### 5.4 Hata Kurtarma

| Yetenek | TaniqWind Pro | CadWind | Cadfil | **Biz (Sprint 3 sonrası)** |
|---|---|---|---|---|
| Çökme sonrası oturum kurtarma | ✓ | kısmi | kısmi | ✓ kilit + sihirbaz |
| Sürüm seçimli geri dönüş | ✗ | ✗ | ✗ | ✓ **TEK — FARKLILAŞMA** |
| Kaydedilmemiş değişiklik uyarısı | ✓ | ✓ | ✓ | ✓ (Sprint 1) |
| G-code üretim hatası açıklaması | ✓ | ✓ | ✓ | kısmi (genel mesajlar) |
| Donanım kopması kurtarma | ✗ (offline) | ✗ | ✗ | ✓ **TEK** (Faz 18 auto-reconnect) |

**Sonuç:** Hata kurtarma ekseninde Sprint 3 sonrası rakiplerin ÖNÜNE
geçiyoruz (sürüm seçimli kurtarma + canlı donanım kurtarma hiçbirinde yok).
En kritik kalan açıklar: (1) iş akışı adım sayısı/panel örtüşmesi,
(2) proje şablonları, (3) reçete dışa aktarımı.

---

## 6. Uygulama Sırası (bu sprint)

1. `app/autosave_manager.py` — AutoSaveManager + RecoveryWizard
2. `ProjeYoneticisiPanel` — "Sürüm Geçmişi" sekmesi + `load_project_dict()`
3. `MainWindow` — manager kurulumu, kilit yaşam döngüsü, açılış sihirbazı
4. `validation_d2/test_autosave_recovery.py` — R1–R12
5. Regresyon: sprint1 + undo_redo + phase17_d2 + smoke

Recipe Library implementasyonu Sprint 4'te (§4 mimarisi onaylanmış sayılır,
itiraz gelirse kodlamadan önce revize edilir).
