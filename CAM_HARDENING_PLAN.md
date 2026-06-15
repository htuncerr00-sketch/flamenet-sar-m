# CAM_HARDENING_PLAN.md — Üretim Sertleştirme Planı

> Tarih: 2026-06-15  
> Kapsam: R3 · R5 · R6  
> Durum: **ONAY BEKLENİYOR — Kod yazılmadı**

---

## 0. Özet

| Görev | Risk | Etki | Sprint |
|-------|------|------|--------|
| R3 — Path Explosion Protection | 🔴 KRİTİK | CAM'i çökertir / sistem belleğini bitirir | 3.1 |
| R6 — 3D Viewer Decimation | 🟠 YÜKSEK | Ana thread'i ~5-50 sn kilitler | 3.2 |
| R5 — Async G-code Generator | 🟡 ORTA | Ana thread'i ~3-8 sn kilitler | 3.3 |

Sıralama gerekçesi: R3 olmadan R5 ve R6 da anlamsız — önce patlayan patikayı kes,
sonra görsel ve G-code akışını zaman uyumsuz yap.

---

## 1. Kök Nedenler

### R3 — Path Explosion

**Saldırı vektörü:** `path_generator.py:127-148` → `clairaut_circuit_count()`

```python
eff_width = tow_width_mm * (1.0 - overlap_pct / 100.0)
eff_width = max(eff_width, 0.1)                      # ← tek güvenlik ağı, yetersiz
n_ideal   = 2 * π * r_avg * sin(alpha) / eff_width  # ← sınırsız büyüme
return max(1, ceil(n_ideal))
```

**İç döngü** (`_generate_helical`, satır 274-287):
```
for layer in range(n_layers):
    for circ in range(n_circuits_per_layer):   # ← kontrolsüz
        _geodesic_pass(... n_steps_per_pass=150 ...)
```

**Toplam nokta sayısı:** `n_layers × n_circuits × n_steps = SINIRSIZ`

#### Patlamayı tetikleyen parametre kombinasyonları

| Senaryo | D (mm) | α | Fitil | Çakışma | n_kat | Devreler/kat | Toplam nokta | Bellek |
|---------|--------|---|-------|---------|-------|-------------|--------------|--------|
| Normal  | 100    | 55° | 6mm | 5% | 4 | **46** | 27,600 | 1.5 MB |
| Uyarı bölgesi | 500 | 85° | 6mm | 40% | 8 | **436** | 523,200 | 29 MB |
| Tehlikeli | 1000 | 88° | 3mm | 30% | 16 | **1,496** | 3,590,400 | 200 MB |
| Felaket | 2000 | 89° | 0.5mm | 45% | 32 | **62,830** | ~301 M | **9.6 GB → Python çöker** |

`eff_width = 0.1mm` tabanı yalnızca `overlap_pct ≥ 98.4%` senaryosunu yakalar.
Küçük fitil genişliği (`tow=0.5mm, overlap=45%`) → `eff_width=0.275mm` → taban devreye girmez.

**Çok-katmanlı mod**: `stack_dict` içindeki her katman bağımsız `generate_path()` çağrır.
8 katmanlı yığın, patlama riskini 8× çarpar; mevcut kod hiçbir toplam limiti denetlemez.

---

### R5 — Senkron G-code Üretimi

`cam_panel.py:637` → `_generate_gcode()` tamamı **ana thread'de** çalışır:

```
_generate_gcode() [ANA THREAD — BLOKLAR]
  plan_motion(path)         # N-1 MotionSegment dataclass oluşturur
  generate_gcode(segs,…)    # N G-code satırı string olarak oluşturur
  gp.as_text()              # tüm satırları "\n".join() ile birleştirir
  QTextEdit.setPlainText()  # büyük metin → Qt yeniden çizer → BLOK
```

**Blokaj süresi tahmini:**

| Nokta sayısı | `plan_motion` | `generate_gcode` | `as_text` | `setPlainText` | Toplam blokaj |
|-------------|---------------|-----------------|-----------|----------------|---------------|
| 10,000 | ~0.05 s | ~0.05 s | <0.01 s | <0.1 s | **~0.2 s** (kabul edilebilir) |
| 100,000 | ~0.5 s | ~0.5 s | ~0.1 s | ~0.5 s | **~1.6 s** (hissedilir) |
| 500,000 | ~2.5 s | ~2.5 s | ~0.5 s | ~2 s | **~7.5 s** (kabul edilemez) |

R3 limitler sonrası maksimum 500k nokta gelir. Bu durumda bile G-code ~7.5 sn bloklar.
`QTextEdit.setPlainText()` özellikle 1 MB+ metinde ek yavaşlama yaratır.

---

### R6 — 3D Nokta Bulutu

`winding_3d.py:119-135` → `path_to_3d()` saf Python döngüsü:

```python
for pt in winding_points:                           # ← saf Python, C yok
    r_val = float(np.interp(pt.x_mm, ...)) / 1000  # ← N kez np.interp çağrısı
    theta = math.radians(pt.a_deg)                 # ← N kez trig
    pts_3d.append([x, y, z])                       # ← N kez list.append
```

**`set_cam_path()` → `path_to_3d()` → `GLLinePlotItem` zinciri ANA THREAD'de:**

| Nokta sayısı | `path_to_3d` Python döngüsü | `GLLinePlotItem` GPU yükü | Toplam blokaj |
|-------------|----------------------------|--------------------------|---------------|
| 10,000 | ~0.05 s | ~0.01 s | **~0.06 s** |
| 100,000 | ~0.5 s | ~0.1 s | **~0.6 s** (hissedilir) |
| 500,000 | ~2.5 s | ~0.5 s | **~3 s** (kabul edilemez) |
| 1,000,000 | ~5-10 s | ~1 s | **~6-11 s** (çok kötü) |

`_on_path_done()` içindeki `self._viewer.set_cam_path(profile, path)` çağrısı
(cam_panel.py:622) R1/R2 düzeltmesinden sonra ana thread'de kaldı — **izole edilmedi.**

Ek sorun: `path_to_3d()` bir de `GLLinePlotItem` için `(N, 4)` renk dizisi oluşturur
(`_rebuild_path_colors()`). Bu da N büyüdükçe artar.

---

## 2. Teknik Çözüm

### R3 — Path Explosion Protection

#### 2.1 `ComplexityError` exception tipi

```python
# path_generator.py
class ComplexityError(ValueError):
    """Yol hesaplama kompleksitesi güvenli limiti aşıyor."""
```

#### 2.2 Limitler (sabite olarak)

```python
# path_generator.py
_MAX_CIRCUITS_PER_LAYER = 2_000   # tek kat başına
_MAX_TOTAL_CIRCUITS     = 20_000  # tüm katmanlar toplamı
_MAX_TOTAL_POINTS       = 500_000 # WindingPoint sayısı üst sınırı
```

**Kalibrasyon gerekçesi:**
- `_MAX_CIRCUITS_PER_LAYER=2000`: D=640mm, α=88°, tow=1mm'ye kadar izin verir
- `_MAX_TOTAL_POINTS=500_000`: ~167 MB WindingPath bellek, ~3 sn hesaplama
- Tipik filament sarma makineleri (CNC sarma): 200-600 devre/kat normal aralık

#### 2.3 `estimate_complexity()` — preflight fonksiyon

```python
# path_generator.py (yeni fonksiyon)
from dataclasses import dataclass as _dc

@_dc
class ComplexityEstimate:
    n_circuits_per_layer: int
    total_circuits: int
    total_points: int
    estimated_memory_mb: float
    estimated_runtime_s: float

def estimate_complexity(params: WindingPathParams) -> ComplexityEstimate:
    """
    Gerçek hesap başlamadan kompleksiteyi tahmin et.
    Hız: ~microseconds (sadece matematik, döngü yok).
    """
    r_avg = params.profile.avg_radius_mm
    alpha_rad = math.radians(params.alpha_deg)
    n_circ = clairaut_circuit_count(
        r_avg, alpha_rad, params.tow_width_mm, params.overlap_pct
    )
    total_circ  = n_circ * params.n_layers
    total_pts   = total_circ * params.n_steps_per_pass
    mem_mb      = total_pts * 56 / 1_000_000   # WindingPoint ~56 bytes
    runtime_s   = total_pts / 30_000            # ~30k pts/s ölçümlü

    return ComplexityEstimate(n_circ, total_circ, total_pts, mem_mb, runtime_s)
```

#### 2.4 `preflight_check()` — limit denetimi

```python
# path_generator.py (yeni fonksiyon)
def preflight_check(params: WindingPathParams) -> ComplexityEstimate:
    """
    Limit aşılırsa ComplexityError fırlatır.
    Geçerse ComplexityEstimate döner (log için kullanılır).
    """
    est = estimate_complexity(params)

    if est.n_circuits_per_layer > _MAX_CIRCUITS_PER_LAYER:
        raise ComplexityError(
            f"Devre sayısı çok yüksek: {est.n_circuits_per_layer} devre/kat "
            f"(limit: {_MAX_CIRCUITS_PER_LAYER}).\n"
            f"Fitil genişliğini artırın (şu an: {params.tow_width_mm:.1f} mm) "
            f"veya çakışma yüzdesini azaltın (şu an: {params.overlap_pct:.0f}%)."
        )
    if est.total_points > _MAX_TOTAL_POINTS:
        raise ComplexityError(
            f"Toplam nokta sayısı çok yüksek: {est.total_points:,} nokta "
            f"(limit: {_MAX_TOTAL_POINTS:,}).\n"
            f"Kat sayısını azaltın (şu an: {params.n_layers}) "
            f"veya fitil genişliğini artırın."
        )
    return est
```

#### 2.5 `cam_panel._do_calculate()` — preflight entegrasyonu

```python
# cam_panel.py — _do_calculate() başına ekle
def _do_calculate(self, req: _CalcParams):
    MandrelProfile, WindingPathParams, generate_path, \
        plan_motion, MachineConfig, generate_gcode = _make_backend()
    from backend.core.path_generator import preflight_check, ComplexityError
    import math as _math

    # ... profil oluştur (aynı) ...

    # ── Preflight — stack_dict varsa her katman ayrı denetlenir ─────────
    stack = req.stack_dict
    if stack and stack.get("layers"):
        total_pts_all = 0
        for layer in stack["layers"]:
            pp = WindingPathParams(
                profile=profile,
                alpha_deg=float(layer.get("alpha_deg", 55.0)),
                n_layers=1,
                tow_width_mm=float(layer.get("fitil_genisligi_mm", req.tow_w_mm)),
                overlap_pct=float(layer.get("cakisma_pct", req.overlap_pct)),
                # ... diğer parametreler ...
            )
            est = preflight_check(pp)          # ComplexityError fırlatır
            total_pts_all += est.total_points
        if total_pts_all > _MAX_TOTAL_POINTS * 2:  # stack toplamı için 2× tolerans
            raise ComplexityError(
                f"Tüm katmanlar toplamı çok yüksek: {total_pts_all:,} nokta. "
                f"Katman dizilimini basitleştirin."
            )
    else:
        path_params_pre = WindingPathParams(
            profile=profile,
            alpha_deg=req.alpha_deg,
            n_layers=req.n_layers,
            tow_width_mm=req.tow_w_mm,
            overlap_pct=req.overlap_pct,
        )
        est = preflight_check(path_params_pre)
        log.info("[CAM] preflight OK: %d devre/kat, %d toplam nokta, %.1f MB",
                 est.n_circuits_per_layer, est.total_points, est.estimated_memory_mb)

    # ... mevcut generate_path çağrıları (değişmez) ...
```

---

### R5 — Async G-code Generator

Mevcut `_Worker` / `_CalcParams` pattern'ının birebir kopyası uygulanır.
Aynı R1/R2/R4 garantileri sağlanır.

#### 2.6 `_GCodeParams` dataclass (R1 uyumlu)

```python
# cam_panel.py
@dataclass
class _GCodeParams:
    """R5: G-code worker'a geçen düz parametreler — Qt nesnesi içermez."""
    path: object              # WindingPath (değişmez sonuç, thread-safe)
    all_layer_paths: object   # Optional[List[(dict, WindingPath)]]
    ctrl_type: str
    x_axis: str
    a_axis: str
    max_x_feed: float
    gcode_gen: int            # nesil sayacı
```

#### 2.7 `_GCodeWorker` QObject

```python
class _GCodeWorker(QObject):
    finished = Signal(object, int)   # (GCodeProgram, gen)
    error    = Signal(str, int)       # (msg, gen)
    progress = Signal(int)            # 0-100 tamamlanma %

    def __init__(self, fn, params: _GCodeParams):
        super().__init__()
        self._fn = fn
        self._params = params

    def run(self):
        log.info("[CAM] gcode worker started (gen=%d)", self._params.gcode_gen)
        try:
            gp = self._fn(self._params)
            self.finished.emit(gp, self._params.gcode_gen)
        except Exception as e:
            log.exception("[CAM] gcode worker error")
            self.error.emit(f"{type(e).__name__}: {e}", self._params.gcode_gen)
```

#### 2.8 `CAMPanel` G-code state değişkenleri

```python
self._gcode_gen: int = 0                          # nesil sayacı
self._gcode_thread: Optional[QThread] = None
self._gcode_worker_ref: Optional[_GCodeWorker] = None
self._gcode_watchdog: Optional[QTimer] = None
_GCODE_WATCHDOG_MS = 60_000   # 60 sn (G-code path daha hızlı ama yine de limit)
```

#### 2.9 Refactor edilmiş `_generate_gcode()`

```python
def _generate_gcode(self):
    if self._winding_path is None:
        QMessageBox.information(...)
        return
    if self._gcode_thread is not None and self._gcode_thread.isRunning():
        log.warning("[CAM] G-code üretimi zaten sürüyor; yeni istek yok sayıldı")
        return

    # R1: tüm değerler ana thread'de toplanır
    self._gcode_gen += 1
    gen = self._gcode_gen
    params = _GCodeParams(
        path=self._winding_path,
        all_layer_paths=self._all_layer_paths,
        ctrl_type=...,   # widget değerleri burada okunur
        x_axis=...,
        a_axis=...,
        max_x_feed=...,
        gcode_gen=gen,
    )
    self._gen_btn.setEnabled(False)
    self._gcode_progress.setVisible(True)

    thread = QThread(self)
    worker = _GCodeWorker(self._do_generate_gcode, params)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(self._on_gcode_done)
    worker.error.connect(self._on_gcode_error)
    worker.finished.connect(thread.quit)
    worker.error.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.finished.connect(self._on_gcode_cleanup)
    self._gcode_thread = thread
    self._gcode_worker_ref = worker
    self._start_gcode_watchdog(gen)
    thread.start()
```

#### 2.10 G-code önizleme limiti

`QTextEdit` büyük metin için yavaşlar. Çözüm: ilk `MAX_PREVIEW_LINES=1000` satır göster,
tam metin bellekte tutulur ve panoya kopyala / dosyaya kaydet için kullanılır.

```python
def _on_gcode_done(self, gp, gen):
    if gen != self._gcode_gen: return
    self._gcode_program = gp
    lines = gp.lines
    MAX_PREVIEW = 1000
    if len(lines) > MAX_PREVIEW:
        preview = lines[:MAX_PREVIEW] + [
            f"; ... (toplam {len(lines):,} satır — tam metin dosyaya kaydet)"
        ]
        self._gcode_edit.setPlainText("\n".join(preview))
    else:
        self._gcode_edit.setPlainText(gp.as_text())
    # istatistikler aynı
```

---

### R6 — 3D Viewer Decimation

#### 2.11 `MAX_DISPLAY_POINTS` sabiti

```python
# winding_3d.py
_MAX_DISPLAY_POINTS = 50_000   # GLLinePlotItem için güvenli üst sınır
```

**Gerekçe:** pyqtgraph GLLinePlotItem, 50k nokta ile <10 ms GPU yükü yapar.
100k+ noktada GPU yükü doğrusal artar; 500k+ 'da renderlama yavaşlar.

#### 2.12 `path_to_3d_fast()` — vektörize NumPy versiyonu

Mevcut saf Python döngüsünün yerini alır:

```python
def path_to_3d_fast(z_mm_profile: np.ndarray, r_mm_profile: np.ndarray,
                    winding_points) -> np.ndarray:
    """
    WindingPoint listesini 3D koordinata dönüştür — tamamen vektörize.

    Karşılaştırma:
      Mevcut (saf Python): 100k pts → ~0.5 s
      Yeni (NumPy C):      100k pts → ~0.005 s  (~100× hızlanma)
    """
    z_center = (z_mm_profile.max() + z_mm_profile.min()) / 2.0
    # Tüm alanları tek liste comprehension yerine tek array dönüşümüyle al
    z_vals = np.fromiter((pt.x_mm for pt in winding_points),
                         dtype=np.float64, count=len(winding_points))
    a_vals = np.fromiter((pt.a_deg for pt in winding_points),
                         dtype=np.float64, count=len(winding_points))
    # C katmanında vektörize interpolasyon
    r_vals = np.interp(z_vals, z_mm_profile, r_mm_profile) / 1000.0
    # C katmanında vektörize trigonometri
    thetas = np.radians(a_vals)
    xs = r_vals * np.cos(thetas)
    ys = r_vals * np.sin(thetas)
    zs = (z_vals - z_center) / 1000.0
    return np.column_stack([xs, ys, zs]).astype(np.float32)
```

#### 2.13 Decimation + LOD bilgi etiketi

```python
# winding_3d.py — set_cam_path() içine ekle
def set_cam_path(self, profile, path):
    ...
    pts_raw = path.points
    n_raw   = len(pts_raw)

    if n_raw > _MAX_DISPLAY_POINTS:
        step = math.ceil(n_raw / _MAX_DISPLAY_POINTS)
        pts_display = pts_raw[::step]
        log.info("[3D] decimation: %d → %d nokta (1:%d)", n_raw, len(pts_display), step)
        decim_info = f"3D: {len(pts_display):,}/{n_raw:,} nokta gösteriliyor (1:{step})"
    else:
        pts_display = pts_raw
        decim_info  = f"3D: {n_raw:,} nokta"

    pts_3d = path_to_3d_fast(
        np.asarray(profile.z_mm),
        np.asarray(profile.r_mm),
        pts_display
    )
    self._fiber_path_full = pts_3d
    self._decim_info = decim_info
    self._rebuild_path_colors()
    ...
```

UI'de `_decim_info_lbl` etiketi ile kullanıcıya "3D: 50,000/1,200,000 nokta gösteriliyor (1:24)" bilgisi verilir.

---

## 3. Mimari Etkiler

### Değiştirilen dosyalar

| Dosya | Değişiklik |
|-------|-----------|
| `faz17_d1/core/path_generator.py` | `ComplexityError`, `ComplexityEstimate`, `estimate_complexity()`, `preflight_check()`, `_MAX_*` sabitleri |
| `faz17_d2/app/panels/cam_panel.py` | `_GCodeParams`, `_GCodeWorker`, `_generate_gcode()` refactor, `_on_gcode_done/error/cleanup`, gcode watchdog, preflight çağrısı |
| `faz17_d2/app/panels/winding_3d.py` | `path_to_3d_fast()`, `set_cam_path()` decimation, `_decim_info_lbl` |

### Değişmeyen şeyler

- `WindingPoint`, `WindingPath`, `WindingPathParams` dataclass'ları: **dokunulmaz**
- `generate_path()`, `generate_hoop_path()`, `generate_polar_path()`: **dokunulmaz**
- `_generate_helical()`, `_geodesic_pass()`: **dokunulmaz**
- Wire protocol: **dokunulmaz**
- Mevcut `_Worker` / `_calculate_path()` / watchdog: **dokunulmaz**
- `clairaut_circuit_count()`: **dokunulmaz** (sadece üstüne denetim ekleniyor)

### `cam_stress_test.py` güncellemesi

```python
# Yeni senaryolar eklenecek (mevcut 100 döngü korunur):
# 1. Patlamalı parametre → ComplexityError → graceful fail
# 2. Büyük path (edge of limit) → gcode async → tamamlanır
# 3. 3D decimation → set_cam_path <100ms → doğrula
```

---

## 4. Riskler

### R3 Riskleri

| Risk | Olasılık | Etki | Azaltma |
|------|----------|------|---------|
| `_MAX_CIRCUITS_PER_LAYER=2000` bazı gerçek reçeteleri reddeder | 🟡 ORTA | Kullanıcı "neden reddedildi?" diye sorar | Hata mesajı açıklayıcı ve eyleme dönüştürülebilir |
| `preflight_check` hatalı hesap yaparsa (`clairaut_circuit_count` ile uyumsuz) | 🟢 DÜŞÜK | Yanlış kabul/red | `clairaut_circuit_count` ile aynı formül kullanılır |
| Stack dict preflight hesabı genişliği yanlış alırsa | 🟡 ORTA | Patlamalı stack'ı gözden kaçırır | Her katman için widget değerleri değil `layer` dict değerleri kullanılır |

### R5 Riskleri

| Risk | Olasılık | Etki | Azaltma |
|------|----------|------|---------|
| `_winding_path` G-code worker çalışırken yeni hesap başlatılırsa | 🟢 DÜŞÜK | `path` referansı değişmiş olabilir | `_GCodeParams.path` = referans kopyası; R3 nedeniyle path immutable |
| `QTextEdit.setPlainText(1000 satır)` hâlâ yavaşsa | 🟢 DÜŞÜK | Kısa blokaj | `setPlainText` <100ms için <1000 satır sorun değil |
| G-code watchdog 60 sn timeout çok kısa | 🟡 ORTA | Gerçek 500k-noktalı program reddedilir | 60 sn = ~500k nokta × 7.5 sn / 500k ile hesaplanmış, gerçekte 60 sn yeterli |

### R6 Riskleri

| Risk | Olasılık | Etki | Azaltma |
|------|----------|------|---------|
| Decimation görsel kalite kaybı kullanıcıyı şaşırtır | 🟡 ORTA | "Yol doğru görünmüyor" şikayeti | Açık bilgi etiketi, "1:K" oranı gösterilir |
| `path_to_3d_fast` sonuçları `path_to_3d` ile birebir aynı mı? | 🟡 ORTA | 3D konum hatası | Float64 → Float32 dönüşümü aynı; birim test karşılaştırması yapılır |
| `GLLinePlotItem` renk dizisi (N,4) decimation ile küçülüyor mu? | 🟢 DÜŞÜK | Renk çizgisi kayması | `_rebuild_path_colors()` zaten `len(self._fiber_path_full)` kullanır |

---

## 5. Test Planı

### 5.1 R3 Birim Testleri — `validation_d2/test_r3_preflight.py`

```python
# İzole, Qt gerektirmeyen testler (import path_generator doğrudan)

def test_normal_params_passes():
    """D=100, α=55°, tow=6, overlap=5%, n=4 → limit içi"""
    params = _make_params(D=100, alpha=55, tow=6, overlap=5, n_layers=4)
    est = preflight_check(params)
    assert est.n_circuits_per_layer <= 100
    assert est.total_points <= 100_000

def test_exploding_mandrel_rejected():
    """D=2000, α=89°, tow=0.5, overlap=45%, n=32 → ComplexityError"""
    params = _make_params(D=2000, alpha=89, tow=0.5, overlap=45, n_layers=32)
    with pytest.raises(ComplexityError) as exc:
        preflight_check(params)
    assert "62,830" in str(exc.value) or "Devre sayısı" in str(exc.value)

def test_circuits_per_layer_limit():
    """Tam limit noktası: 2000 devre/kat → RED"""
    # r_avg ~ 637mm, alpha=88°, tow=2mm → ~2001 devre
    params = _make_params(D=1274, alpha=88, tow=2, overlap=0, n_layers=1)
    with pytest.raises(ComplexityError):
        preflight_check(params)

def test_total_points_limit():
    """Toplam nokta limiti: n_layers×circuits×steps > 500k → RED"""
    # 4 kat × 1000 devre × 150 adım = 600k > 500k
    params = _make_params(D=640, alpha=88, tow=2, overlap=0, n_layers=4)
    with pytest.raises(ComplexityError):
        preflight_check(params)

def test_error_message_is_user_friendly():
    """Hata mesajı Türkçe ve eyleme dönüştürülebilir olmalı"""
    try:
        preflight_check(_make_params(D=2000, alpha=89, tow=0.5, overlap=45, n=32))
    except ComplexityError as e:
        assert "fitil" in str(e).lower() or "çakışma" in str(e).lower()

def test_stack_dict_preflight_multi_layer():
    """8 katmanlı stack → her katman ayrı denetlenir"""
    # ... (cam_panel._do_calculate benzeri mantık)
```

### 5.2 R6 Birim Testleri — `validation_d2/test_r6_decimation.py`

```python
def test_path_to_3d_fast_vs_original():
    """Vektörize versiyon orijinal ile numerik olarak aynı sonuç verir"""
    pts_new = path_to_3d_fast(z_arr, r_arr, sample_points)
    pts_old = path_to_3d(z_arr, r_arr, sample_points)
    np.testing.assert_allclose(pts_new, pts_old, atol=1e-5)

def test_decimation_below_limit():
    """50k nokta → step=1, tüm noktalar gösterilir"""
    step = math.ceil(50_000 / _MAX_DISPLAY_POINTS)
    assert step == 1

def test_decimation_above_limit():
    """100k nokta → step=2, 50k nokta gösterilir"""
    n = 100_000
    step = math.ceil(n / _MAX_DISPLAY_POINTS)
    assert step == 2
    assert len(range(0, n, step)) == 50_000

def test_decimation_1m():
    """1M nokta → step=20, ~50k nokta gösterilir"""
    n = 1_000_000
    step = math.ceil(n / _MAX_DISPLAY_POINTS)
    assert step == 20
    shown = math.ceil(n / step)
    assert shown <= _MAX_DISPLAY_POINTS * 1.02  # 2% tolerans

def test_path_to_3d_fast_speed(benchmark):
    """100k nokta → <50ms (mevcut ~500ms vs yeni <5ms)"""
    result = benchmark(path_to_3d_fast, z_arr, r_arr, points_100k)
    assert result.shape == (100_000, 3)
```

### 5.3 R5 Entegrasyon Testi — `validation_d2/test_r5_gcode_async.py`

```python
QMessageBox.* = staticmethod(lambda *a, **k: None)

def test_gcode_worker_completes():
    """Küçük path → GCodeWorker tamamlanır, signal alınır"""
    app = QApplication.instance() or QApplication(sys.argv)
    panel = CAMPanel()
    panel._calculate_path()  # hızlı: D=100mm, L=300mm
    _wait_done(app, panel)
    assert panel._winding_path is not None

    panel._generate_gcode()
    t0 = time.time()
    while panel._gcode_thread is not None:
        app.processEvents()
        assert time.time() - t0 < 30, "G-code zaman aşımı"
        time.sleep(0.003)
    assert panel._gcode_program is not None

def test_gcode_watchdog():
    """Watchdog: 60 sn sonra gen artırılır, geç signal yok sayılır"""
    # ... gen invalidation testi

def test_gcode_cancel_on_new_path():
    """Yeni yol hesabı başlarsa eski gcode gen invalidated"""
    # ...

def test_gcode_preview_truncation():
    """500k satırlık G-code → QTextEdit sadece 1000 satır gösterir"""
    # ...
```

### 5.4 `cam_stress_test.py` Genişletme

Mevcut 100 döngüye ek olarak:

```python
# Senaryo A: exploding params → ComplexityError → graceful fail (path=None)
panel._diameter.setValue(2000.0)
panel._alpha.setValue(89.0)
panel._tow_w.setValue(0.5)
panel._calculate_path()
_wait_done(app, panel)
assert panel._winding_path is None  # ComplexityError yakalandı

# Senaryo B: edge-of-limit path → gcode async OK
# (R3 geçer, ama büyük = ~490k nokta)

# Senaryo C: 3D decimation performance
# set_cam_path ile 1M noktalı sahte path → <200ms
```

---

## 6. Sprint Sıralaması

### Sprint 3.1 — R3: Path Explosion Protection (bağımsız, önce yapılır)

**Dosyalar:**
1. `faz17_d1/core/path_generator.py` → `ComplexityError`, `ComplexityEstimate`, `estimate_complexity()`, `preflight_check()`, `_MAX_*`
2. `faz17_d2/app/panels/cam_panel.py` → `_do_calculate()` başına preflight entegrasyonu
3. `validation_d2/test_r3_preflight.py` → birim testler (RC=0 doğrulama)
4. `validation_d2/cam_stress_test.py` → senaryo A eklenir

**Teslim kriteri:**
- `test_r3_preflight.py` RC=0
- `cam_stress_test.py` RC=0 (tüm 100 döngü + senaryo A)
- `phase17_d2_validation.py` 8/8 PASS korunur

---

### Sprint 3.2 — R6: 3D Viewer Decimation (R3'ten bağımsız)

**Dosyalar:**
1. `faz17_d2/app/panels/winding_3d.py` → `path_to_3d_fast()`, `_MAX_DISPLAY_POINTS`, `set_cam_path()` decimation, `_decim_info_lbl`
2. `validation_d2/test_r6_decimation.py` → birim testler

**Teslim kriteri:**
- `test_r6_decimation.py` RC=0
- `path_to_3d_fast` ile 100k nokta işleme < 50ms
- Görsel: decimation bilgi etiketi ekran testinde görünür

---

### Sprint 3.3 — R5: Async G-code Generator (R3 tamamlanınca yapılır)

**Dosyalar:**
1. `faz17_d2/app/panels/cam_panel.py` → `_GCodeParams`, `_GCodeWorker`, `_generate_gcode()` refactor, watchdog, cleanup, preview limit
2. `validation_d2/test_r5_gcode_async.py` → entegrasyon testler

**Teslim kriteri:**
- `test_r5_gcode_async.py` RC=0
- `cam_stress_test.py` RC=0 (tüm senaryolar)
- `phase17_d2_validation.py` 8/8 PASS korunur
- Thread analizi: 0 `QObject::killTimer` / thread-affinity uyarısı

---

### Sprint 3.4 — Regresyon & Commit (tüm R3/R5/R6 tamamlanınca)

1. `cam_stress_test.py` tam çalıştırma — 100 döngü + 3 ek senaryo → RC=0
2. `phase17_d2_validation.py` → 8/8 PASS
3. Git commit + push → `claude/amazing-feynman-XUXBf`

---

## 7. Onay Beklenen Kararlar

Kod yazmadan önce aşağıdakileri onaylayın:

### K1 — `_MAX_CIRCUITS_PER_LAYER` değeri

Önerilen: **2,000**

Bu değer şu anlama gelir:
- D=640mm mandrel, α=88°, tow=1mm → 2,000 devre → **İZİN VERİLİR**
- D=1000mm mandrel, α=89°, tow=0.5mm → ~6,280 devre → **REDDEDİLİR**

Daha büyük bir mandrel ile çalışmanız gerekiyorsa bu değer artırılabilir.

### K2 — `_MAX_TOTAL_POINTS` değeri

Önerilen: **500,000** (~29 MB bellek, ~3 sn hesap süresi)

Daha az: 200,000 (daha hızlı ama bazı gerçek reçeteler reddedilir)  
Daha fazla: 1,000,000 (daha uzun hesap, ~60 sn watchdog'a yaklaşır)

### K3 — `_MAX_DISPLAY_POINTS` değeri

Önerilen: **50,000** (GLLinePlotItem için güvenli üst sınır)

Daha fazla görmek için: 100,000 (GPU'ya göre değişir, orta GPU'da ~50ms yükleme)

### K4 — G-code önizleme satır limiti

Önerilen: **1,000** satır QTextEdit'te göster, tam metin panoya/dosyaya

Alternatif: 500 satır (daha hızlı), 5,000 satır (daha zengin önizleme)

### K5 — Sprint 3.2 (R6) ve Sprint 3.1 (R3) sırası

Önerilen: **R3 → R6 → R5** (yukarıdaki sıralama)

Alternatif: R3 ve R6 paralel (bağımsızlar, ama kod inceleme daha zor)

---

## 8. Etkilenmeyen Bileşenler

- Wire protokolü: **DOKUNULMAZ**
- `WindingPoint`, `WindingPath` veri yapıları: **DOKUNULMAZ**
- `clairaut_circuit_count()`: **DOKUNULMAZ** (yalnızca üstüne denetim eklenir)
- `_generate_helical()`, `_geodesic_pass()`: **DOKUNULMAZ**
- Mevcut `_Worker` / `_calculate_path()` / R1/R2/R4 düzeltmeleri: **DOKUNULMAZ**
- Telemetri, canlı izleme, replay, güvenlik denetleyicisi: **DOKUNULMAZ**

---

**Sonraki adım:** Yukarıdaki 5 kararı (K1–K5) onaylayın veya değerleri değiştirin.  
Onay alındıktan sonra Sprint 3.1 → 3.2 → 3.3 → 3.4 sırasıyla uygulanacak.
