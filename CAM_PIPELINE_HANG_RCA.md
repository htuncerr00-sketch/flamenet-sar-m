# CAM_PIPELINE_HANG_RCA.md

> **Kök Neden Analizi** — "Yolu Hesapla / 3D Güncelle / G-code Üret" akışının
> zaman zaman kalıcı olarak **"Hesaplanıyor…"** durumunda takılması.
> **Durum:** TANI RAPORU — kod değişikliği yok. Düzeltme onayı bekleniyor.
> Tarih: 2026-06-15

---

## 0. TL;DR — Kök neden

Takılmanın tek bir hatadan değil, **üst üste binen beş kusurdan** kaynaklandığını
tespit ettim. Mekanizma şu:

> CAM panelinde "Hesaplanıyor…" durumu **yalnızca** worker thread'in
> `finished` **veya** `error` sinyaliyle temizlenir. Worker bu iki sinyalden
> hiçbirini üretmezse (sonsuz/çok-uzun hesap, thread-içi widget erişimi kaynaklı
> kilitlenme, ya da thread'in çöküşü) durum **kalıcı olarak temizlenmez** ve
> buton sonsuza kadar devre dışı kalır. **Watchdog/timeout yoktur.**

En kritik tetikleyici: **`_do_calculate()` worker thread içinde çalışırken Qt
widget'larından (`QDoubleSpinBox.value()`, `QComboBox.currentText()`) doğrudan
okuma yapıyor** — bu Qt'de yasak (GUI nesnelerine yalnızca ana thread erişebilir)
ve "zaman zaman" (intermittent) kilitlenmenin birincil kaynağı.

İlginç olan: **aynı kod tabanındaki `katman_dizilim_paneli.py` bu işi DOĞRU
yapıyor** — worker'a ham değerler geçiriliyor, widget'a dokunulmuyor, re-entrancy
ve thread temizliği var. CAM paneli bu güvenli deseni izlememiş.

---

## 1. İzlenen zincir (uçtan uca)

```
Katman Dizilim Paneli
  │  "→ Üretime Gönder" butonu
  │  uretimeGonder.emit(stack_dict)              [katman_dizilim_paneli.py:1514/1529]
  ▼
main_window._connect_signals()
  │  self._panel_katman.uretimeGonder.connect(self._panel_cam.set_layer_stack)
  │                                              [main_window.py:370-373]
  ▼
CAMPanel.set_layer_stack(stack)                  [cam_panel.py:589-624]
  │  self._stack_dict = stack (dict) | stack.to_dict() | {}
  │  layers boşsa → self._stack_dict = None  (sessiz parametrik moda dönüş)
  ▼
[kullanıcı] "Yolu Hesapla"  →  CAMPanel._calculate_path()   [cam_panel.py:366-383]
  │  _calc_btn.setEnabled(False); _progress_bar.setVisible(True)
  │  _status_lbl.setText("Yol hesaplanıyor…")
  │  QThread() + _Worker(self._do_calculate).moveToThread(thread)
  │  started→worker.run ; finished→_on_path_done ; error→_on_path_error
  ▼
_Worker.run() [worker thread]                    [cam_panel.py:47-52]
  │  result = self._do_calculate()    ◄── BURADA WIDGET'LARA THREAD-DIŞI ERİŞİM
  ▼
CAMPanel._do_calculate() [worker thread]         [cam_panel.py:385-453]
  │  self._mandrel_type.currentText(), self._diameter.value(), ...  ◄── İHLAL
  │  generate_path(WindingPathParams)   ◄── sınırsız devre sayısı riski
  ▼
backend.path_generator.generate_path()           [path_generator.py:73-310]
  │  n_circuits = clairaut_circuit_count(...)   ◄── patlama riski (eff_width tabanı 0.1mm)
  │  her devre × n_steps(150) WindingPoint üretir
  ▼
worker.finished.emit(path, profile, all_layer_paths)
  ▼
CAMPanel._on_path_done() [ana thread]            [cam_panel.py:455-473]
  │  _calc_btn.setEnabled(True); _progress_bar.setVisible(False)  ◄── temizleme YALNIZCA burada
  │  self._viewer.set_cam_path(profile, path)  try/except: pass   ◄── 3D hatası yutuluyor
  ▼
Winding3DPanel.set_cam_path() [ana thread]       [winding_3d.py:333-376]
  │  path_to_3d + GLLinePlotItem(pos=tüm noktalar)  ◄── ana thread'de GPU yükü (donma)
  ▼
[kullanıcı] "G-code Oluştur" → CAMPanel._generate_gcode() [cam_panel.py:481-564]
     plan_motion()+generate_gcode() her katman için  ◄── ANA THREAD'DE SENKRON (worker yok)
```

### Hangi sinyal hangi slotu tetikliyor (CAM zinciri)

| Sinyal | Kaynak | Slot | Dosya:satır |
|---|---|---|---|
| `uretimeGonder(dict)` | Manuel Dizilim butonu | `CAMPanel.set_layer_stack` | main_window.py:370-373 |
| `uretimeGonder(dict)` | aynı | `_panel_uretim.set_layer_stack` | main_window.py:368-369 |
| `uretimeGonder(dict)` | aynı | `_panel_entegre.set_layer_stack` | main_window.py:372-373 |
| `clicked` | `_calc_btn` | `_calculate_path` | cam_panel.py:235 |
| `QThread.started` | worker thread | `_Worker.run` | cam_panel.py:377 |
| `_Worker.finished` | worker | `_on_path_done` **+** `thread.quit` | cam_panel.py:378,380 |
| `_Worker.error` | worker | `_on_path_error` **+** `thread.quit` | cam_panel.py:379,381 |
| `clicked` | `_gen_btn` | `_generate_gcode` (worker YOK) | cam_panel.py:332 |

---

## 2. Audit 1 — CAM pipeline

### 2.1 🔴 KRİTİK — "Hesaplanıyor…" yalnızca worker sinyaliyle temizlenir, watchdog yok

`_calculate_path` durumu "Yol hesaplanıyor…" yapar (cam_panel.py:372). Bu durum
**sadece** iki yerde temizlenir:

- `_on_path_done` (cam_panel.py:458-459) — `_Worker.finished`'a bağlı
- `_on_path_error` (cam_panel.py:476-477) — `_Worker.error`'a bağlı

`_Worker.run` (cam_panel.py:47-52) ya `finished` ya `error` yayar — **ama yalnızca
`run()` geri dönerse.** `run()` geri dönmezse (sonsuz döngü, thread-dışı widget
erişiminde kilitlenme, ya da C++ thread'inin yıkılması) **hiçbir sinyal yayılmaz**,
buton sonsuza kadar disabled, progress bar sonsuza kadar döner.

> **Bu, kullanıcının gördüğü kalıcı "Hesaplanıyor…" durumunun doğrudan
> mekanizmasıdır.** Zaman aşımı (QTimer watchdog) yok.

### 2.2 🔴 KRİTİK — G-code üretimi ana thread'de senkron

`_generate_gcode` (cam_panel.py:481-564) **worker kullanmaz.** `plan_motion()` ve
`generate_gcode()` her katman için ana thread'de çalışır (cam_panel.py:511-512,
550-551). Büyük/çok-katmanlı yollarda GUI **donar** — ilerleme göstergesi yok,
iptal yok. "G-code Üret bazen tamamlanmıyor" şikâyetinin kaynağı budur:
tamamlanıyor ama UI uzun süre bloklu kalıyor (ya da §2.5'teki patlama ile pratikte
bitmiyor).

### 2.3 🟠 YÜKSEK — 3D güncelleme ana thread'de tüm nokta bulutunu GPU'ya yüklüyor

`_on_path_done` → `set_cam_path` (winding_3d.py:333-376) ana thread'de çalışır;
`GLLinePlotItem(pos=self._fiber_path_full)` (winding_3d.py:435-441) **tüm sarma
noktalarını** tek seferde yükler. Nokta sayısı patlarsa (§2.5) bu adım ana thread'i
uzun süre bloklar. Not: progress bar bu noktada zaten gizlenmiş (cam_panel.py:459),
durum "Yol hesaplandı" yazıyor → bu durumda donma "hesaplandı" mesajından *sonra*
olur. "Hesaplanıyor"da takılma ise §2.4/§2.5 kaynaklı (worker içi).

### 2.4 🔴 KRİTİK — `_do_calculate` worker thread'de Qt widget'larına erişiyor

`_do_calculate` (cam_panel.py:385-453) worker thread'de çalışır ama şunları okur:
`self._mandrel_type.currentText()`, `self._diameter.value()`, `self._length.value()`,
`self._alpha.value()`, `self._n_layers.value()`, `self._tow_w.value()`,
`self._overlap.value()`, `self._feed.value()`, `self._rpm.value()`,
`self._x_min.value()`, `self._x_max.value()`, `self._strategy.currentText()`.

Qt kuralı: **GUI nesnelerine yalnızca ana (GUI) thread erişebilir.** Bu erişim
tanımsız davranıştır; çoğu zaman çalışır, **zaman zaman kilitlenir/çöker** — tam
olarak kullanıcının tarif ettiği "zaman zaman sonsuza kadar" semptomu.

Ek olarak `self._stack_dict` (cam_panel.py:405) worker thread'de okunurken ana
thread `set_layer_stack` ile aynı alanı yazabilir → **veri yarışı**.

### 2.5 🟠 YÜKSEK — Sınırsız devre sayısı patlaması (etkin sonsuz hesap)

`clairaut_circuit_count` (path_generator.py:127-148):

```
eff_width = max(tow_width*(1 - overlap/100), 0.1)   # taban 0.1 mm
n = ceil(2π · r_avg · sin(α) / eff_width)
```

`eff_width` tabanı 0.1 mm olduğundan, büyük çap + küçük fitil + yüksek çakışma
kombinasyonunda `n` patlar. Örnek: r_avg=1000 mm, eff_width=0.1 →
`n ≈ 62 831 devre`. Her devre `_geodesic_pass` ile `n_steps=150` nokta üretir
(path_generator.py:326-363) → ~9.4 milyon nokta/kat. Çok-katmanlı modda
(`_do_calculate` her katman için ayrı `generate_path`, cam_panel.py:408-431) bu
**toplanır**. Sonuç: worker dakikalarca/saatlerce çalışır + bellek patlar →
pratikte "sonsuza kadar Hesaplanıyor". Üst sınır/uyarı yok.

> Not: UI `_overlap` 0-50 ile sınırlı (cam_panel.py:189) ama **katman yığınından
> gelen `cakisma_pct`** (LayerSpec'te [0,95)) sınırlanmıyor (cam_panel.py:422) →
> patlama çok-katmanlı modda daha olası.

---

## 3. Audit 2 — Signal-loop (sonsuz döngü)

**Bulgu: CAM zincirinde sonsuz SİNYAL döngüsü YOK (düşük risk).**

- `CAMPanel.set_layer_stack` (cam_panel.py:589-624) **hiçbir sinyal yaymaz**;
  yalnızca etiket/enable durumu değiştirir → geri besleme yok.
- `uretimeGonder` yalnızca explicit buton ile yayılır (katman_dizilim_paneli.py:
  1514,1529), CAM'e tek yön akar.
- `katmanDegisti` (katman_dizilim_paneli.py:1559) CAM'e değil `_panel_uretim` +
  `mark_dirty_external`'a gider → CAM'e dönüş yok.

**Sonuç:** Kullanıcının "sonsuza kadar" algısı sinyal özyinelemesi değil, worker
thread'in tamamlanmaması (§2.1/§2.4/§2.5). Yine de küçük bir yapısal not:
`set_layer_stack` izlenen spinbox'ların `valueChanged`'ini tetiklemediği için
(yalnızca label + setEnabled) re-entrant komut/dirty döngüsü oluşmuyor — bu
**doğru** ama kırılgan; ileride spinbox değeri set edilirse döngü riski doğar.

---

## 4. Audit 3 — Layer stack propagation (stack_dict nerede boşalıyor)

| Yer | Davranış | Risk |
|---|---|---|
| `__init__` | `self._stack_dict = None` | Başlangıç parametrik mod — beklenen |
| `set_layer_stack`, gelen `layers` boş | **`self._stack_dict = None`** (cam_panel.py:614) | 🟠 Boş yığın gelince **sessizce** parametrik moda döner — kullanıcı çok-katmanlı beklerken tek-açı G-code üretir |
| `set_layer_stack`, `stack` ne dict ne to_dict | `self._stack_dict = {}` sonra `n=0` → None | Tip uyumsuzluğu sessizce yutulur |
| `_do_calculate` (worker) | `stack = self._stack_dict` okunur (cam_panel.py:405) | 🔴 ana thread yazarken yarış (§2.4) |

**Kök bulgu:** `stack_dict` iki yolla "boşalır": (1) gelen yığının `layers`'ı boşsa
satır 614'te `None`'a çekilir; (2) `to_dict()` olmayan beklenmeyen tip gelince
`{}`→`None`. Her ikisi de **sessiz** — log/uyarı yok. Bir senkronizasyon
sırasında (örn. Manuel Dizilim'de yığın temizlenip yeniden kuruluyorken
"Üretime Gönder" tetiklenirse) CAM beklenmedik şekilde parametrik moda düşebilir.

---

## 5. Audit 4 — Worker thread tamamlanma

### 5.1 CAM paneli (hatalı desen) — cam_panel.py:374-383

```python
self._worker_thread = QThread()                    # ← parent YOK
worker = _Worker(self._do_calculate)
worker.moveToThread(self._worker_thread)
self._worker_thread.started.connect(worker.run)
worker.finished.connect(self._on_path_done)
worker.error.connect(self._on_path_error)
worker.finished.connect(self._worker_thread.quit)
worker.error.connect(self._worker_thread.quit)
self._worker_thread.start()
self._worker_ref = worker                          # tek referans
```

Sorunlar:
1. 🔴 `_do_calculate` widget okur (§2.4).
2. 🟠 `QThread()` **parent'sız**; `_thread.finished` temizliği yok; `deleteLater`
   yok. Bir sonraki hesapta `self._worker_thread`/`self._worker_ref` **üzerine
   yazılır** → önceki QThread henüz tam sonlanmadıysa Python GC onu toplayıp
   *"QThread: Destroyed while thread is still running"* aborterini tetikleyebilir
   (intermittent çökme/donma).
3. 🟠 Re-entrancy koruması yalnızca butonun disabled olmasına dayanır. Hang
   durumunda buton kalıcı disabled kalır → ikinci hesap zaten engellenir (semptomu
   pekiştirir).
4. 🟠 `_pending` mekanizması yok: hesap sürerken parametre değişirse sıraya
   alınmaz.

### 5.2 Karşılaştırma — Manuel Dizilim paneli (DOĞRU desen) — katman_dizilim_paneli.py:1206-1327

Aynı kod tabanı bunu doğru yapıyor; CAM düzeltmesi için **referans desen** budur:

```python
if self._thread and self._thread.isRunning():      # re-entrancy guard
    self._pending_recalc = True; return
self._thread = QThread(self)                       # parent VAR
self._worker = _AnalysisWorker(
    stack_dict=self._stack.to_dict(),              # ← ham değerler GEÇİRİLİR
    mandrel_diameter_mm=self._mandrel_diameter_mm, #   (worker widget'a dokunmaz)
    ... )
self._worker.moveToThread(self._thread)
self._thread.started.connect(self._worker.run)
self._worker.finished.connect(self._on_analysis_done)
self._worker.error.connect(self._on_analysis_error)
self._worker.finished.connect(self._thread.quit)
self._worker.error.connect(self._thread.quit)
self._thread.finished.connect(self._on_thread_done)  # ← temizlik + pending işleme
```

Farklar (CAM'de eksik olanlar): ham-değer geçişi, re-entrancy guard,
`_thread.finished → _on_thread_done` ile `self._thread=None; self._worker=None`
ve `_pending_recalc` tekrar tetikleme (katman_dizilim_paneli.py:1321-1327),
parent'lı QThread.

> **Not:** Manuel Dizilim worker'ı thread-güvenli olsa da onun da **timeout'u yok**
> — backend hesabı sonsuz döngüye girerse "Hesaplanıyor…" orada da temizlenmez.
> Watchdog ihtiyacı her iki panel için geçerli.

---

## 6. Audit 5 — Exception yutma (swallowed)

| # | Konum | Kod | Etki |
|---|---|---|---|
| 1 | cam_panel.py:470-473 | `try: self._viewer.set_cam_path(...) except Exception: pass` | 🔴 **3D Güncelle hatası tamamen yutulur** — 3D boş kalır, tek satır log/uyarı yok |
| 2 | winding_3d.py:349-350 | mesh `except Exception: pass` | 🟠 Yüzey ağı sessiz başarısız |
| 3 | winding_3d.py:361-362 | fiber yolu `except: _fiber_path_full=None` | 🟠 Yol sessizce çizilmez |
| 4 | winding_3d.py:375-376 | kamera `except: pass` | 🟡 benign |
| 5 | cam_panel.py:51-52 | `_Worker.run` `except Exception as e: self.error.emit(str(e))` | 🟠 Hata yüzeye çıkar ama **traceback kaybolur** (kök neden teşhisi zor) |
| 6 | cam_panel.py:563-564 | `_generate_gcode` `except: QMessageBox.critical(str(e))` | 🟠 Yüzeye çıkar ama traceback yok; kısmi gcode kaybolur |
| 7 | katman_dizilim_paneli.py:367-368 | worker `except: self.error.emit(f"{type}: {exc}")` | 🟢 En azından tip+mesaj; yine traceback yok |

**Kök bulgu:** En zararlı yutma **#1**: 3D güncelleme başarısızlığı tamamen
sessiz. Bir kullanıcı "3D güncellenmiyor" derken arkada tekrarlayan bir istisna
olabilir ve hiçbir iz bırakmaz. Worker hataları string'e indirgendiği için
(traceback yok) intermittent kilitlenmenin gerçek nedeni log'lardan okunamıyor.

---

## 7. Kök nedenlerin önceliklendirilmiş özeti

| Sıra | Kök neden | Tip | Dosya:satır | Semptom |
|---|---|---|---|---|
| **R1** | Worker thread içinde Qt widget erişimi (+ `_stack_dict` yarışı) | Kilitlenme/UB | cam_panel.py:389-450, 405 | "zaman zaman" kalıcı Hesaplanıyor |
| **R2** | "Hesaplanıyor…" yalnızca finished/error ile temizlenir; **watchdog yok** | Tasarım | cam_panel.py:372,458,476 | Worker bitmezse kalıcı takılma |
| **R3** | Sınırsız devre sayısı patlaması (eff_width tabanı + çok-katman çarpımı) | Algoritmik | path_generator.py:144-148; cam_panel.py:408-431 | Etkin sonsuz hesap/OOM |
| **R4** | QThread yaşam döngüsü: parent yok, finished-temizlik yok, referans üzerine yazma | Yaşam döngüsü | cam_panel.py:374-383 | "Destroyed while running" çökme/donma |
| **R5** | G-code üretimi ana thread'de senkron | Tasarım | cam_panel.py:481-564 | "G-code Üret" UI donması |
| **R6** | 3D `set_cam_path` ana thread'de tüm nokta bulutunu yükler | Performans | winding_3d.py:435-441 | Büyük yolda donma |
| **R7** | İstisna yutma (özellikle 3D try/except: pass) + traceback kaybı | Görünürlük | cam_panel.py:470-473; winding_3d.py:349,361 | Sessiz başarısızlık, teşhis zor |
| **R8** | `stack_dict` sessizce None'a düşer (boş layers / tip uyumsuzluğu) | Doğruluk | cam_panel.py:599,614 | Beklenmeyen parametrik moda dönüş |

---

## 8. Önerilen düzeltme yönü (yalnızca öneri — onay sonrası kodlanır)

> Bu rapor **tanı** içindir; aşağısı tartışma içindir, henüz uygulanmayacak.

1. **R1 (öncelik):** `_do_calculate`'i widget'tan tamamen ayır. Tüm parametreleri
   ana thread'de oku, `_Worker`'a **ham değer** olarak geçir — `katman_dizilim_paneli`
   `_AnalysisWorker` deseninin birebir aynısı. `_stack_dict`'in **kopyasını** geçir.
2. **R2:** Worker başlatınca bir **QTimer watchdog** (örn. 20-30 s) kur; süre
   dolarsa worker'ı iptal işaretle, durumu "Zaman aşımı — iptal edildi" yap, butonu
   geri ver. Hem CAM hem Manuel Dizilim için.
3. **R4:** `QThread(self)` (parent'lı), `_thread.finished → temizlik`, `deleteLater`,
   re-entrancy guard + `_pending` deseni.
4. **R3:** `generate_path`/`clairaut_circuit_count`'a **makul üst sınır** (örn.
   `n_circuits` ve toplam nokta için tavan) + aşılırsa anlamlı hata; CAM girişinde
   `cakisma_pct`/tow doğrulaması.
5. **R5:** G-code üretimini de worker thread'e taşı + ilerleme göstergesi.
6. **R7:** `set_cam_path` try/except'i en azından `logging`'e yazsın; worker
   hatalarında `traceback.format_exc()` ilet.
7. **R8:** Boş/uyumsuz yığın gelince durum çubuğuna görünür bilgi ver; sessiz
   mod değişimini logla.

**Doğrulama planı (düzeltme sonrası):** degenerate-girdi testi (büyük çap+küçük
fitil+yüksek çakışma → sınır hatası, hang yok), thread-güvenlik testi (worker'da
widget erişimi olmadığının statik kontrolü), watchdog testi (sahte sonsuz backend →
timeout ile temizlenir), `phase17_d2_validation` 8/8 regresyon korunur.

---

## 9. Korunan ilkeler

- Wire protokolü / firmware / telemetri **etkilenmez**.
- Yeni özellik eklenmez — yalnızca mevcut akışın güvenilirliği hedeflenir.
- Düzeltme deseni zaten kod tabanında kanıtlı (`katman_dizilim_paneli` worker'ı).

---

**Onay isteği:** Bu kök neden analizi (özellikle R1-R8 sıralaması) onaylanırsa,
§8'deki düzeltme yönüyle uygulamaya geçilebilir. Onaya kadar **kod yazılmaz.**
