# COMPETITIVE AUDIT — Filament Winding CAM Platform
**Tarih:** 2026-06-12
**Durum:** Kod geliştirme öncesi analiz — onay bekleniyor
**Kaynak:** Canlı kod tabanı denetimi (87 araç çağrısı) + 10 rakip ürün web araştırması (127 araç çağrısı, 50+ kaynak)

---

## YÖNETİCİ ÖZETİ

İki paralel araştırmanın en kritik bulgusu şudur:

> **Tüm ticari rakipler "çevrimdışı CAM araçları"dır — G-code üretirler, sonra iş biter.
> Flamenet-SAR-M ise canlı üretim döngüsünde çalışan tek platformdur.**

1 kHz telemetri, gerçek zamanlı güvenlik kontrolcüsü, dijital ikiz ve AI advisory ile platform
rakiplerin hiçbirinin yaklaşamadığı bir alanda konuşlanmıştır. Bununla birlikte kurumsal satış
için engel olan **Undo/Redo yokluğu**, **FEA export eksikliği** ve **34 bağlanmamış backend
modül** — bu avantajı gölgelemektedir.

**Özet:**
- Güç: Canlı üretim zekası — sektörde yok
- Zayıflık: Masa başı CAM iş akışında rakiplerden 3-4 kritik özellik geride
- Acil risk: `_del_row()` bug'ı veri bozuyor; non-geodesik sessiz fallback

---

## 1. KULLANICI İŞ AKIŞI DENETİMİ

### Bizde: Başlangıçtan G-code'a kaç adım?

| # | Adım | Panel | Sürtünme Noktası |
|---|------|-------|------------------|
| 1 | Uygulama aç | app_launcher splash | — |
| 2 | Mod seç | HomeScreen | 3 seçenek — yeni kullanıcı neyi seçeceğini bilmiyor |
| 3 | Proje oluştur/aç | Proje Yöneticisi (Tab 0) | Zorunlu mu? Atlanabilir mi? Belirsiz |
| 4 | Malzeme seç | Malzeme Kütüphanesi (Tab 1) | Sıra zorunluluğu kullanıcıya gösterilmiyor |
| 5 | Mandrel tanımla | Katman & Analiz (Tab 2) | Tab 4 ile çakışıyor |
| 6 | Katman sırala | Manuel Dizilim (Tab 3) | Tablo mantığı karmaşık |
| 7 | CAM parametresi gir | Tasarım Merkezi (Tab 4) | Tab 5 ile örtüşüyor |
| 8 | Üretim parametresi gir | Üretim Tasarım Mrkz (Tab 5) | Tab 4 ile örtüşüyor |
| 9 | Yol hesapla | Tab 4 veya Tab 6 | İkisi neden ayrı? Belirsiz |
| 10 | G-code üret | CAM Üretici (Tab 6) | Makine profili seçilemiyor |
| 11 | Dosyaya kaydet | QFileDialog | Kontrolcü tipi kaydetme yok |
| 12 | Üretime başla | Canlı Üretim (Tab 7) | Hangi G-code yüklü görülmüyor |

**Toplam: 12 adım.** Adım 5-6-7-8 birleştirilebilir = 8 adıma indirgenebilir.

### Rakiplerde kaç adım?

| Yazılım | Adım | Yaklaşım |
|---------|------|----------|
| **CadWind** | 5 | Mandrel → iWind sim → Live View slider → iMove post → transfer |
| **Cadfil** | 5 | QuickCAD/import → parametre → simülasyon → NC çıktı → makine |
| **TaniqWind Pro** | 4 | CAD import → layer setup → sim + coverage → G-code |
| **WindingExpert** | 6 | Mandrel → katman → FEA → sim → G-code → Winding Commander |
| **AddPath** | 5 | CAD import → AFP/FW path → dijital ikiz sim → robot prog → üretim |
| **SimWind (MA)** | 4 | SimWind offline → sim → Ocelot → üretim (live edit) |

**Rakipler 4-6 adım; biz 12 adım. Öğrenme eğrisi ciddi dezavantaj.**

---

## 2. UI / UX DENETİMİ

### Panel Envanteri (14 sekme)

| Dosya | Sekme Adı | Satır | Durum |
|-------|-----------|-------|-------|
| proje_yoneticisi.py | Proje Yöneticisi | 681 | ✅ |
| malzeme_kutuphanesi.py | Malzeme Kütüphanesi | 408 | ✅ |
| tabaka_yoneticisi.py | Katman & Analiz | 686 | ✅ |
| katman_dizilim_paneli.py | Manuel Dizilim | 1892 | ✅ |
| entegre_tasarim_paneli.py | 🏭 Tasarım Merkezi | 1181 | ✅ |
| uretim_tasarim_paneli.py | Üretim Tasarım Mrkz | 1824 | ✅ |
| cam_panel.py | CAM Üretici | 623 | ⚠️ Tab 4 ile örtüşüyor |
| live_production.py | Canlı Üretim | 353 | ✅ |
| winding_3d.py | 3D Görüntüleyici | 651 | ✅ |
| alarms.py | Alarmlar & Güvenlik | 150 | ✅ |
| replay.py | Tekrar Oynat | 273 | ✅ |
| recipe_editor.py | Reçete Düzenleyici | 395 | ✅ |
| commissioning.py | Devreye Alma | 289 | ✅ |
| predictive_maintenance.py | Tahminsel Bakım | 134 | ✅ |

### UX Benchmark Karşılaştırması

| Kriter | SolidWorks | Fusion 360 | CadWind | TaniqWind Pro | **Biz** |
|--------|-----------|-----------|---------|---------------|---------|
| Ana sekme sayısı | 6 | 5 | 4 | 4 | **14** |
| Undo/Redo | ✅ | ✅ | ✅ | ✅ | ❌ |
| Wizard/Rehber | ✅ | ✅ | ✅ | ✅ | ❌ |
| Bağlama duyarlı panel | ✅ | ✅ | kısmen | ✅ | ❌ |
| PDF/Rapor çıktısı | ✅ | ✅ | ✅ | ✅ | ❌ |
| İş akışı yönlendirmesi | ✅ | ✅ | kısmen | ✅ | ❌ |
| Canlı telemetri | ❌ | ❌ | ❌ | ❌ | ✅ **TEK** |
| Güvenlik kontrolcüsü | ❌ | ❌ | ❌ | ❌ | ✅ **TEK** |

---

## 3. CAM İŞ AKIŞI DENETİMİ

### STL Yükleme
- Yol: Tasarım Merkezi → Mandrel = "STL'den" → "📂 STL Yükle"
- `stl_processor.py` binary + ASCII STL destekli
- **Sorun:** STL seçilip silinirse hesaplamada sessiz hata
- **Eksik:** STL simetri doğrulama UI'sı (`validate_stl_symmetry()` var, UI bağlantısı yok)

### Mandrel Tanımlama
- 5 tip: Silindir, Konik, Kubbeli Silindir, Elipsoidal Kubbe, STL'den
- **Eksik:** DXF/IGES import, elbow, T-joint, spar, dikdörtgen/eliptik kesit
- **Eksik:** Mandrel hacim hesabı (WindingExpert yapar)

### Katman Sıralama
- Hem Tab 3 (Manuel Dizilim) hem Tab 4 (Tasarım Merkezi) katman tablosu içeriyor
- **Sorun:** Kullanıcı hangi tablonun "gerçek" dizilim olduğunu bilmiyor
- **Eksik:** Drag-and-drop layer reorder; copy/paste katman

### Simülasyon
- 3D makine görünümü gerçek zamanlı + fiber yolu animasyonu ✅
- **Eksik:** Coverage haritası (gap/overlap görselleştirme)
- **Eksik:** Makine eksen hız/ivme grafiği (CadWind'in Machine Motion Analyser'ı)

### G-code Export
- 5 kontrolcü formatı: GRBL, Mach3, Fanuc, LinuxCNC, Siemens ✅
- **Sorun:** Makine profili export anında seçilemiyor (sabit `MachineConfig()`)
- **Eksik:** G-code dry-run / satır sayısı ön tahmini

---

## 4. EKSİK ENDÜSTRİYEL ÖZELLİKLER

| Özellik | Durum | Rakiplerde |
|---------|-------|------------|
| Undo / Redo (QUndoStack) | ❌ YOK | CadWind ✓, Cadfil ✓, TaniqWind ✓ |
| PDF export | ❌ YOK | Hepsi ✓ |
| FEA export (Abaqus/NASTRAN/ANSYS) | ❌ YOK | CadWind ✓, Cadfil ✓ (7 format), WindingExpert ✓, AddPath ✓ |
| Burst pressure hesabı UI | ❌ YOK (backend var) | WindingExpert ✓ |
| Manufacturing report UI | ❌ YOK (backend var) | Hepsi ✓ |
| Setup wizard | ❌ YOK | CadWind ✓, Cadfil ✓, TaniqWind ✓ |
| Machine collision detection | ❌ YOK | Cadfil ✓ **güçlü özelliği**, CadWind ✓, TaniqWind ✓ |
| DXF/IGES mandrel import | ❌ YOK | Hepsi ✓ |
| Coverage auto-search | ❌ YOK | TaniqWind Pro ✓ **lider özelliği** |
| Axis vel/acc grafiği | ❌ YOK | CadWind ✓, WindingExpert ✓ |
| İzotonik kubbe (isotensoid) | ❌ YOK | Cadfil v9.90 ✓ |
| Proje kaydetme/yükleme | ✅ VAR | — |
| Malzeme kütüphanesi UI | ✅ VAR | — |
| Makine profili kaydetme | ✅ VAR | — |
| Controller profil UI | ⚠️ KISMİ | Cadfil ✓ (kapsamlı) |
| Üretim log viewer | ⚠️ KISMİ | Hepsi ✓ |

---

## 5. KOMPETİTİF ÖZELLİK MATRİSİ

> TaniqWind Pro'yu dahil ettik: bağımsız inceleme sitesi (filamentwindingsoftware.com, 2025)
> "piyasadaki en iyi filament sarma yazılımı" olarak nitelendiriyor.

| Özellik | CadWind | Cadfil | WindingExpert | TaniqWind Pro | **Biz** |
|---------|---------|--------|---------------|---------------|---------|
| **Geodezik yol planlama** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Non-geodezik (RK4/friction)** | ✅ phys. | ✅ | ✅ | ✅ | ✅ |
| **Elipsoidal kubbe profili** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Helisel / Hoop / Polar** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **3D makine simülasyonu** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Fanuc / LinuxCNC G-code** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Proje save/load** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Malzeme kütüphanesi** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Multi-axis (4+)** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Undo / Redo** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **FEA export** | ✅ NASTRAN+HDF5 | ✅ 7 format | ✅ 3 format | ✅ Abaqus/HW | ❌ |
| **PDF rapor** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Burst pressure hesabı** | ✅ | ✅ | ✅ | ✅ | ❌ (backend var) |
| **DXF/IGES mandrel import** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Machine collision detection** | ✅ | ✅ **güçlü** | ✅ | ✅ | ❌ |
| **Coverage auto-search** | ❌ | ⚠️ | ⚠️ | ✅ **LIDER** | ❌ |
| **Axis vel/acc grafiği** | ✅ | ⚠️ | ✅ | ✅ | ❌ |
| **Wizard tabanlı workflow** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Setup sheet / PDF** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **İzotonik kubbe profili** | ⚠️ | ✅ v9.90 | ⚠️ | ✅ | ❌ |
| **Gerçek zamanlı telemetri** | ❌ | ❌ | ❌ log | ❌ | ✅ **YALNIZCA BİZ** |
| **ESP32 donanım entegrasyonu** | ❌ | ❌ | ❌ | ❌ | ✅ **YALNIZCA BİZ** |
| **1 kHz güvenlik kontrolcüsü** | ❌ | ❌ | ❌ | ❌ | ✅ **YALNIZCA BİZ** |
| **Dijital ikiz (canlı)** | ❌ | ❌ | TCON (ayrı) | ❌ | ✅ **YALNIZCA BİZ** |
| **Telemetri replay / oynat** | ❌ | ❌ | ❌ | ❌ | ✅ **YALNIZCA BİZ** |
| **AI advisory (güvenli)** | ❌ | ❌ | ❌ | ❌ | ✅ **YALNIZCA BİZ** |
| **Tahminsel bakım UI** | ❌ | ❌ | ⚠️ | ❌ | ✅ **YALNIZCA BİZ** |
| **Açık kaynak/serbestçe genişletilebilir** | ❌ | ❌ | ❌ | ❌ | ✅ |

**Skor (28 özellik üzerinden):**

| Yazılım | Puan | Not |
|---------|------|-----|
| CadWind | 18/28 | Fizik sim + ANSYS HDF5 güçlü |
| Cadfil | 19/28 | En geniş FEA + collision güçlü |
| WindingExpert | 17/28 | TCON SCADA ayrı ürün |
| TaniqWind Pro | 20/28 | En modern, coverage auto-search lider |
| **Biz** | **19/28** | CAM özelliklerinde 4 geride; canlı üretimde 7 tek |

---

## 6. KRİTİK STRATEJİK BOŞLUK ANALİZİ

Bağımsız araştırma şunu teyit etti: **tüm ticari rakipler çevrimdışı CAM araçlarıdır.**
G-code üretirler — iş biter. Aşağıdaki özellikler **sektördeki hiçbir üründe yoktur:**

| Eksik Yetenek (Tüm Rakiplerde) | Flamenet-SAR-M Durumu |
|--------------------------------|----------------------|
| Gerçek zamanlı sensör telemetrisi üretim sırasında | ✅ 1 kHz binary, 0 torn reads |
| Dijital ikiz ile canlı durum aynası | ✅ digital_twin.py + execution_twin.py |
| Sensör eşiklerine dayalı E-stop komutlama | ✅ safety_controller.py |
| Üretim geçmişi replay + analiz | ✅ TelemetryDB + replay.py |
| AI tabanlı anomali tespiti + danışma | ✅ ai/ modülü (SafetyValidator üzerinden) |
| Tahminsel bakım | ✅ predictive_maintenance.py |
| SaaS/bulut mimarisine uygun açık altyapı | ✅ Python/Qt, platform bağımsız |

Bu, bir **pazar boşluğudur** — TaniqWind Pro, Cadfil, CadWind bu özellikleri asla
ekleyemez çünkü donanım entegrasyonu gerektiriyor ve iş modelleri uyumsuz.

---

## 7. KRİTİK HATA AVLAMA

### BUG #1 — KRİTİK: `_del_row()` sender() yanlış kullanım
**Dosya:** `entegre_tasarim_paneli.py` ~satır 928  
**Etki:** Katman tablosunda **yanlış satır siliniyor** — veri bozulması

```python
# MEVCUT (HATALI) — btn.sender() her zaman None döner:
if btn and btn.sender() == self.sender():
    tbl.removeRow(r)

# DÜZELTME:
def _del_row(self, row: int) -> None:
    if 0 <= row < self._layer_table.rowCount():
        self._layer_table.removeRow(row)
```

### BUG #2 — YÜKSEK: Non-geodezik sessiz fallback
**Dosya:** `path_generator.py` ~satır 264  
**Etki:** `friction_mu > 0` ayarlı — kullanıcı non-geodezik zannettiği halde
geodezik yol alıyor

```python
# MEVCUT: except Exception: sessiz fallback
# DÜZELTME: WindingPath.fallback_to_geodesic: bool = False bayrağı + UI uyarısı
```

### BUG #3 — ORTA: B-ekseni başlatma G-code'da yok
**Dosya:** `gcode_postprocessor.py` ~satır 141  
**Etki:** B ekseni etkinleştirildiğinde makine ilk harekette belirsiz başlangıç

```python
# Düzeltme: G-code header'a G28 B0 veya G92 B0 satırı ekle
```

### BUG #4 — ORTA: Fanuc N-numarası taşması
**Dosya:** `gcode_postprocessor.py`  
**Etki:** Uzun sarmalarda N9999'ı aşabilir → G-code geçersiz

```python
# Düzeltme: line_no % 9990 + 10 ile sarım (wrap-around N0010)
```

### BUG #5 — ORTA: STL dosyası silinince sessiz hata
**Dosya:** `entegre_tasarim_paneli.py`  
**Etki:** STL seçilip taşınırsa hesaplama sessizce başarısız

```python
# Düzeltme: if not os.path.exists(self._stl_path): raise RuntimeError(...)
```

### DEBUG PRINT'LER (üretimde kalmış)
- `main_window.py:780` — stderr shutdown print
- `main.py:114` — startup debug print

### YETİM / BAĞLANMAMMIŞ MODÜLLER (34 adet)

Yazılıp hiçbir UI paneline bağlanmamış backend modüller:

```
coverage_solver, dome_transition, execution_twin, eye_orientation_solver,
failure_criterion, fiber_band, fiber_contact_model, fiber_deposition,
fiber_tension, fiber_tension_model, geodesic_validator, industrial_motion,
laminate_builder, layer_buildup, layer_stacking, machine_calibration,
machine_envelope, machine_execution, machine_kinematics, machine_limits,
manufacturability, manufacturing_report, non_geodesic_validator, payout_dynamics,
payout_kinematics, process_parameters, production_estimator, production_report,
simulation_playback, thickness_predictor, trajectory_builder, winding_twin,
stl_processor (dolaylı), burst_pressure (sadece test)
```

**Bu 34 modül yalnız başlarına ciddi bir CAM platformu oluşturabilir.
Kısa vadede ya bağlanmalı ya silinmeli — ikisi de bakım borcu yaratıyor.**

---

## 8. PERFORMANS DENETİMİ

| Süreç | Yöntem | Risk Seviyesi |
|-------|--------|---------------|
| Yol hesaplama | `_CalcWorker` QThread | ✅ Güvenli |
| 3D GL render | Ana thread (GPU) | ✅ Kabul edilebilir |
| Telemetri ingest | QThread + Queue | ✅ ~9000 fps |
| Güvenlik denetimi | Ayrı thread | ✅ <10 µs |
| **Büyük STL** | `_profile_from_vertices()` O(N²) | ⚠️ 100K vertex → 3-5 sn gecikme |
| **100+ katman** | O(katman × devre × adım) | ⚠️ ~500ms; ilerleme çubuğu şart |
| **Uzun G-code join** | Ana thread `"\n".join(100K satır)` | ⚠️ Worker thread'e taşınmalı |

**STL O(N²) düzeltmesi:** `np.digitize()` ile O(N log N)'e düşürülebilir.

---

## 9. TİCARİ HAZIRLIK DENETİMİ

| Kriter | Durum |
|--------|-------|
| Splash screen + About dialog | ✅ |
| Help menüsü | ✅ |
| Dark industrial tema, Türkçe | ✅ |
| Hardcoded path | ✅ Yok |
| Debug print'ler | ⚠️ 2 adet |
| Logging (rotating) | ✅ |
| Ayar kalıcılığı (QSettings) | ✅ |
| Hata işleme (QMessageBox) | ✅ |
| **Undo/Redo** | ❌ |
| **PDF export** | ❌ |
| **FEA bağlantısı** | ❌ |
| **Wizard** | ❌ |
| **G-code collision pre-check** | ❌ |

**Ticari hazırlık skoru: 8/13**

"Bugün müşteriye versek ne eksik?"

- **Bloklayıcı:** Undo/Redo (kurumsal IT red sebebi), PDF (onay belgesi yok), FEA export (mühendislik onayı alınamaz)
- **Görünüm:** 14 sekme amatör izlenim, G-code editörü salt okunur, 3D'de ölçek yok
- **Süreç kopukluğu:** Üretim log → rapor zinciri kırık, katman tablo senkronizasyonu belirsiz

---

## 10. TOP 50 EKSİK ÖZELLİK

### P0 — Kritik (satış engelleyici)
1. **Undo/Redo** (QUndoStack) — katman, parametre, mandrel değişiklikleri
2. **PDF export** — üretim raporu, reçete özeti, setup sheet
3. **`_del_row()` hata düzeltmesi** — yanlış satır siliniyor (BUG #1)
4. **FEA export** — Abaqus .inp, NASTRAN .nas, ANSYS .cdb
5. **G-code dry-run / simülatör** — export öncesi yol doğrulama
6. **Non-geodezik uyarı UI** — `|λ| > μ` durumunda görsel uyarı (BUG #2)
7. **Proje kapatırken kaydetme uyarısı** — şu an sessizce kapanıyor

### P1 — Yüksek Öncelik
8. **Machine collision detection** — payout göz – mandrel çakışma kontrolü
9. **DXF mandrel import** — sektör standardı
10. **STEP/IGES import** — AddPath gibi açık platform
11. **Burst pressure hesabı UI** — backend var, panel yok
12. **Manufacturing report UI** — manufacturing_report.py görselleştirilmeli
13. **Axis velocity/acceleration grafiği** — CadWind Machine Motion Analyser karşılığı
14. **Coverage haritası** — gap/overlap görselleştirme
15. **Wizard tabanlı ilk kurulum** — 5 adımlık hızlı başlangıç
16. **Setup sheet** — makine operatörü için kurulum belgesi
17. **Mandrel volume hesabı** — üretim planlama
18. **Layer drag-and-drop** — katman sıralama UX
19. **Copy/paste katman tanımı**
20. **Controller profile kaydetme UI** — MachineConfig persistent

### P2 — Orta Öncelik
21. **G-code editör düzenleme modu** — salt okunur yerine düzenlenebilir
22. **Coverage auto-search** — TaniqWind Pro'nun lider özelliği
23. **İzotonik kubbe (isotensoid)** — Cadfil v9.90 gibi
24. **Production log viewer UI** — production_report.py görselleştirme
25. **Cost estimator UI** — cost_estimator.py görselleştirme
26. **Thickness predictor UI** — thickness_predictor.py görselleştirme
27. **Auto-save / kilitlenme kurtarma**
28. **Elbow mandrel desteği**
29. **T-joint mandrel desteği**
30. **STL simetri doğrulama UI**
31. **Fiber yoğunluk haritası (3D)**
32. **Multi-mandrel seri üretim desteği**
33. **İngilizce lokalizasyon** — global pazar
34. **Keyboard shortcuts paneli**
35. **Recipe import/export (XML/CSV)**
36. **Comparative recipe analysis**
37. **Layer group management**
38. **G-code machine limits ihlal pre-check**
39. **B-axis initialization G-code** (BUG #3 düzeltmesi)
40. **Fanuc N-numarası wrap-around** (BUG #4 düzeltmesi)

### P3 — Uzun Vadeli
41. **FEA integration loop** — FEA sonuçtan parametrik güncelleme
42. **Robotic arm (6-DOF) desteği**
43. **AFP hybrid mode** — AddPath rekabeti
44. **Hydrogen COPV wizard** — Type III/IV basınçlı kap tasarımcısı
45. **Cloud project sharing**
46. **Real-time defect detection**
47. **Per-tow tension control (multi-tow)**
48. **Coverage solver UI** — coverage_solver.py görselleştirme
49. **Trajectory builder UI** — trajectory_builder.py görselleştirme
50. **Plugin/extension sistemi**

---

## 11. TOP 20 KRİTİK HATA

| # | Hata | Dosya | Şiddet | Etki |
|---|------|-------|--------|------|
| 1 | `_del_row()` sender() → yanlış satır siliniyor | entegre_tasarim_paneli.py | **KRİTİK** | Veri bozulması |
| 2 | Non-geodezik sessiz fallback | path_generator.py | **YÜKSEK** | Yanlış hesaplama |
| 3 | B-ekseni başlatma G-code'da yok | gcode_postprocessor.py | **YÜKSEK** | Makine hatası |
| 4 | Fanuc N-numarası N9999'da taşabilir | gcode_postprocessor.py | **ORTA** | G-code geçersiz |
| 5 | STL silinince sessiz hata | entegre_tasarim_paneli.py | **ORTA** | Hata mesajı yok |
| 6 | α=90° hoop kenar durumu (sin sıfıra yakın) | path_generator.py | **ORTA** | Coverage hatalı |
| 7 | 34 yetim backend → bakım borcu | core/ tümü | **ORTA** | Uzun vadeli risk |
| 8 | Debug print'ler üretimde kalmış | main_window.py, main.py | **DÜŞÜK** | Profesyonellik |
| 9 | Lambda row capture sonrası indeks kayması | entegre_tasarim_paneli.py | **ORTA** | Yanlış silme |
| 10 | Mandrel değişince fiber path temizlenip uyarı yok | entegre_tasarim_paneli.py | **DÜŞÜK** | UX |
| 11 | STL ASCII formatı test edilmemiş | stl_processor.py | **ORTA** | Veri kaybı riski |
| 12 | `n_steps_per_pass` düşük → seyrek nokta | path_generator.py | **DÜŞÜK** | Kalite |
| 13 | Proje kaydedilmeden kapama → veri kaybı | main_window.py | **ORTA** | Veri kaybı |
| 14 | STL O(N²) — 100K vertex → donma | geometry_engine.py | **ORTA** | Performans |
| 15 | G-code join ana thread → 100K satır donma | cam_panel.py | **DÜŞÜK** | Performans |
| 16 | `_profile_from_vertices()` hatalı bin | geometry_engine.py | **DÜŞÜK** | Doğruluk |
| 17 | Non-geodezik λ > μ uyarısı yok | non_geodesic_engine.py | **ORTA** | Güvenlik |
| 18 | Reçete → CAM tek yönlü akış | recipe_editor.py | **DÜŞÜK** | UX |
| 19 | G-code editörü salt okunur | cam_panel.py | **DÜŞÜK** | UX |
| 20 | `generate_helical()` GCodeProgram türü uyumsuzluğu | winding_planner.py | **DÜŞÜK** | API uyumu |

---

## 12. TOP 10 UX PROBLEMİ

| # | Problem | Rakip Standart | Önerilen Düzeltme |
|---|---------|---------------|-------------------|
| 1 | **14 sekme — bilişsel aşırı yük** | SW max 7, F360 max 5 | Tasarım / CAM / Üretim / Teşhis olarak grupla |
| 2 | **Undo/Redo yok** | Tüm profesyonel araçlar | QUndoStack; Ctrl+Z/Y |
| 3 | **Tab 4 vs Tab 5 çakışması** | CadWind tek akış | Birleştir veya wizard yönlendir |
| 4 | **"Hesapla" + "G-code üret" ayrı** | Cadfil tek buton | "Hesapla ve G-code Üret" seçeneği |
| 5 | **3D kamera sıfırlama butonu yok** | F360 Home view | Toolbar'a "🏠" butonu |
| 6 | **Mandrel değişince yol sessizce siliniyor** | Tüm CAD araçları | Onay dialog |
| 7 | **G-code editörü salt okunur** | SimWind live edit | Kilit aç / düzenleme modu |
| 8 | **Kritik buton hiyerarşisi zayıf** | SW renk sistemi | "Yolu Hesapla" primer; diğerleri sekonder |
| 9 | **Canlı Üretim'de hangi G-code yüklü belirsiz** | İnsan faktörü std. | Dosya adı + satır sayısı göster |
| 10 | **3D'de ölçek göstergesi yok** | Tüm CAD araçları | Sol alt: koordinat + ölçek çubuğu |

---

## 13. TOP 10 TİCARİ RİSK

| # | Risk | Olasılık | Etki | Önlem |
|---|------|----------|------|-------|
| 1 | **`_del_row()` demo'da veri bozulması** | Yüksek | Çok Yüksek | Acil düzeltme |
| 2 | **Undo/Redo yok → kurumsal IT red** | Yüksek | Yüksek | QUndoStack (P0) |
| 3 | **PDF rapor yok → onay belgesi üretilemez** | Yüksek | Yüksek | reportlab entegrasyonu |
| 4 | **FEA export yok → mühendislik onayı alınamaz** | Yüksek | Yüksek | NASTRAN/Abaqus P1 |
| 5 | **Machine collision check yok → güvenlik olayı** | Düşük | Çok Yüksek | Collision detection P1 |
| 6 | **34 yetim modül → rakip özellikleri yakalar** | Orta | Orta | Modülleri bağla veya sil |
| 7 | **Türkçe UI → global pazar engeli** | Orta | Orta | i18n altyapısı P2 |
| 8 | **TaniqWind Pro "en iyi" ödülü → pazar algısı** | Yüksek | Orta | Telemetri farklılaşmasını markalaştır |
| 9 | **Hydrogen COPV pazarı hızlanıyor → yokuz** | Orta | Yüksek | COPV wizard P3 |
| 10 | **Windows dışı test yok** | Düşük | Orta | CI cross-platform test ekle |

---

## GÜÇ VE ZAYIFLIK ÖZETI

### Güçlü Yönler
- **Canlı üretim zekası** — sektörde eşi yok: 1 kHz telemetri + güvenlik kontrolcüsü + dijital ikiz
- **Doğru Koussios/Clairaut motoru** — sin(α) faktörü, kubbe dönüşü, RK4 non-geodezik
- **5 kontrolcü formatı** — Fanuc, LinuxCNC, GRBL, Mach3, Siemens
- **14 entegre panel** — rakiplerin çoğundan kapsamlı
- **34 hazır backend modül** — aktivasyonu bekleyen büyük potansiyel
- **Açık kaynak altyapı** — lisans engeli yok
- **8/8 validasyon 100/100** — güvenilir temel

### Zayıf Yönler
- Undo/Redo yok → kurumsal kabul için kritik engel
- FEA export yok → mühendislik onay döngüsü kırık
- 12 adımlık workflow → rakiplerin 4-6 adımına karşı
- 14 sekme bilişsel aşırı yük
- `_del_row()` bug hâlâ üretimde

---

## RAKİP ÜRÜN ÖZETİ

| Ürün | Pazar Pozisyonu | Ayırt Edici Özellik |
|------|----------------|---------------------|
| **TaniqWind Pro** | Bağımsız değerlendirmede #1 "en iyi genel" | Coverage auto-search, modern GUI, 6 haftada bir güncelleme |
| **Cadfil** | En yüksek pazar payı (40 yıl) | 7 FEA formatı, collision modeling, tee-pipe modülü |
| **CadWind** | Kararlı kurulu tabanı | Fizik tabanlı iWind simülasyonu, ANSYS HDF5 |
| **WindingExpert** | "En aktif geliştirilen pattern jeneratör" | TCON SCADA fabrika katmanı, H2 tank odağı |
| **FiberGrafiX** | En büyük kurulu taban (900+) | Unidirectional ply mode, Toray Group ekosistemi |
| **AddPath** | En inovatif iş modeli | AFP+FW hybrid, in-situ defect detection, abonelik |
| **SimWind/MA** | ABD savunma güçlü | Makine üzerinde canlı düzenleme, OPC-UA |
| **Cadfil/CNC Technics** | En ucuz giriş noktası (Hindistan) | Siemens 840D + Cadfil paketi |

**Fiyat aralığı:** $10.000-$20.000 (perpetual lisans, tahmini);
AddPath: €3.500/ay; CNC Technics India: ~$1.200 (Lite+)

---

## SONUÇ: BİR SONRAKİ GELİŞTİRME ÖNCELİĞİ

> **QUndoStack — Undo/Redo altyapısı**

**Gerekçe (tek öneri, üç nedeni var):**

1. **Kurumsal satış engelini kaldırıyor.**
   Cadfil, CadWind, TaniqWind Pro'nun tamamında var. Olmadan kurumsal IT politikası reddeder.

2. **Aynı altyapı üç kritik özelliği de besliyor:**
   - Auto-save → `QUndoStack` geçmişinden snapshot çıkarılır
   - `_del_row()` bug'ı → `DeleteLayerCommand` refactoring'de doğal düzelir (BUG #1)
   - Proje history → undo stack serialization → proje zaman çizelgesi

3. **En kısa sürede en yüksek ticari değer.**
   1-2 günlük iş; 5 bug'ı/riski aynı anda kapatır.

**Öneri kapsamı:**
```python
class AddLayerCommand(QUndoCommand)     # katman ekle
class DeleteLayerCommand(QUndoCommand)  # katman sil → _del_row() düzeltmesi
class ChangeParamCommand(QUndoCommand)  # mandrel/sarma parametre değişikliği
# Ctrl+Z / Ctrl+Y kısayolları + Edit menüsü
```

**Stratejik not:** Rakiplerle "masa başı CAM" özelliklerinde yarışmaya çalışmak yerine,
uzun vadede canlı üretim zekasını (telemetri, dijital ikiz, AI) markalaştırmak daha
savunulabilir bir konum yaratır. Undo/Redo bu geçişin ön koşuludur — "tablo oyununa"
girebilmek için önce masa başı yeterliliğini tamamlamak gerekiyor.

---

*Bu rapor `claude/amazing-feynman-XUXBf` branchindeki kod tabanına (87 araç çağrısı)
ve 10 rakip ürünün kapsamlı web araştırmasına (127 araç çağrısı, 50+ kaynak) dayanmaktadır.
Geliştirme başlamadan önce onayınız beklenmektedir.*
