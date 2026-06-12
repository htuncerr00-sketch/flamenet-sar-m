# WORKFLOW HARDENING ANALYSIS — Phase 25
**Tarih:** 2026-06-12
**Durum:** Kod geliştirme öncesi mimari analiz — onay bekleniyor
**Kaynak:** Canlı kod tabanı tam okuma (entegre_tasarim_paneli.py, katman_dizilim_paneli.py,
proje_yoneticisi.py, recipe_editor.py, uretim_tasarim_paneli.py, main_window.py +
tüm ilgili backend modüller)

---

## 1. `_del_row()` KÖK NEDEN ANALİZİ

### Hatanın Tam Anatomisi

```python
# entegre_tasarim_paneli.py — _add_layer_row():
btn_del.clicked.connect(lambda _, r=row: self._del_row(r))
#                                 ↑
#                     row, lambda oluşturulduğu ANDAKİ tablo boyutuna göre
#                     yakalanıyor. Tablo sonradan değişirse bu değer stale.

def _del_row(self, row: int) -> None:
    tbl = self._layer_table
    for r in range(tbl.rowCount()):
        btn = tbl.cellWidget(r, COL_DEL)
        if btn and btn.sender() == self.sender():  # ← BUG #1
            tbl.removeRow(r)
            return
    # fallback:
    if 0 <= row < tbl.rowCount():                  # ← BUG #2
        tbl.removeRow(row)
```

### BUG #1 — `btn.sender()` her zaman `None` döner

`sender()` yalnızca sinyal-yuva bağlamında (bir `QObject` slot içinde çağrıldığında)
çalışır. `btn` bir `QPushButton`'dır; `btn.sender()` çağrısı bir slotta DEĞİLDİR.
`QObject.sender()` yalnızca **o anda yürütülen slot'u tetikleyen nesneyi** döndürür;
`btn` üzerinde doğrudan çağrıldığında `None` döner.

Sonuç: `btn.sender() == self.sender()` **her zaman False** → döngü hiçbir zaman
`removeRow()` çağırmaz → her zaman `fallback` bloğuna düşer.

### BUG #2 — Stale lambda capture → yanlış indeks

Lambda `r=row` ile yakalamayı yapıyor. `row`, `_add_layer_row()` çağrıldığındaki
`tbl.rowCount()` değeridir. Senaryolar:

| Tablo durumu | Lambda yakaladığı `row` | Tablo şu an | `removeRow(row)` sonucu |
|---|---|---|---|
| 3 katman, #2'yi sil | `row=2` | rowCount=3 | ✅ Doğru |
| 3 katman, #0'ı sil, sonra #1'i sil | `row=1` | rowCount=2 | ⚠️ Artık #1 eski #2 |
| 2 katman ekle, #0'ı sil, #0 tekrar ekle, #1'i sil | `row=1` | rowCount=2 | ⚠️ Yanlış satır |

**Kural:** Bir `QTableWidget` satırı eklendiğinde veya silindiğinde tüm satır indeksleri
kayar. Lambda'da yakalanan sabit `row` değeri artık geçerli değildir.

### Doğru Düzeltme (3 yaklaşım)

**Yaklaşım A — En basit: Her silme butonuna kendi widget referansını ver**

```python
def _add_layer_row(self, ...):
    ...
    btn_del = QPushButton("✕")
    btn_del.clicked.connect(lambda _, b=btn_del: self._del_row_by_widget(b))
    tbl.setCellWidget(row, COL_DEL, btn_del)

def _del_row_by_widget(self, btn: QPushButton) -> None:
    tbl = self._layer_table
    for r in range(tbl.rowCount()):
        if tbl.cellWidget(r, COL_DEL) is btn:  # ← kimlik karşılaştırması
            tbl.removeRow(r)
            return
```

**Yaklaşım B — Undo/Redo altyapısıyla entegre: Command pattern**

```python
def _del_row_by_widget(self, btn: QPushButton) -> None:
    tbl = self._layer_table
    for r in range(tbl.rowCount()):
        if tbl.cellWidget(r, COL_DEL) is btn:
            cmd = DeleteLayerCommand(self, r, self._read_row(r))
            self._undo_stack.push(cmd)  # Command hem siler hem de geri alınabilir
            return

class DeleteLayerCommand(QUndoCommand):
    def __init__(self, panel, row: int, row_data: dict):
        super().__init__("Katman Sil")
        self._panel = panel
        self._row = row
        self._data = row_data

    def redo(self):
        self._panel._layer_table.removeRow(self._row)

    def undo(self):
        self._panel._insert_row_at(self._row, self._data)
```

**ÖNERİ: Yaklaşım B** — Undo/Redo ile aynı anda düzeltilecek; 1 refactoring = 2 özellik.

---

## 2. UNDO / REDO MİMARİSİ

### Mevcut Veri Modeli Analizi

Uygulamada **iki farklı katman tablosu paradigması** var:

| Panel | Tablo tipi | Backing model | Serileştirme |
|-------|-----------|---------------|--------------|
| `KatmanDizilimPaneli` | `QTableWidget` | `LayerStack` backend nesnesi | `self._stack.to_dict()` ✅ |
| `EntegreTasarimPaneli` | `QTableWidget` | **Yok** — saf UI | Manuel okuma (fragile) |

`KatmanDizilimPaneli` için Undo/Redo uygundur: `LayerStack.to_dict()` → snapshot.
`EntegreTasarimPaneli` için önce backing model eklenmeli, sonra Undo/Redo.

### Komut Hiyerarşisi

```
QUndoStack (her panel için ayrı veya global)
│
├── LayerCommand (temel)
│   ├── AddLayerCommand         → _add_layer_row redo/undo
│   ├── DeleteLayerCommand      → _del_row_by_widget redo/undo  [BUG #1+#2 düzeltmesi]
│   ├── MoveLayerCommand        → drag-and-drop (gelecek)
│   └── EditLayerCommand        → hücre değişikliği redo/undo
│
├── MandrelCommand
│   └── ChangeMandrelCommand    → tip/çap/uzunluk/kubbe değişikliği
│
└── ProjectCommand
    └── LoadProjectCommand      → proje yükle (tek macro-undo adımı)
```

### Snapshot vs Command Yaklaşımı Kararı

| Yöntem | Avantaj | Dezavantaj | Uygun olduğu yer |
|--------|---------|------------|-----------------|
| **Snapshot** | Basit, hata dayanıklı | Her snapshot = full state kopyası (~KB) | Büyük değişiklikler (proje yükle) |
| **Command** | Minimal hafıza, granüler | Her komut tipi için ayrı kod | Küçük atomik değişiklikler |
| **Hibrit** | İkisinin avantajı | Karmaşıklık | **ÖNERİLEN** |

**Hibrit Strateji:**
- `AddLayerCommand`, `DeleteLayerCommand`, `EditLayerCommand` → pure Command (redo/undo metotları)
- `LoadProjectCommand` → snapshot (pre/post project dict)
- Grup işlemleri (`set_layer_stack()` gibi) → `QUndoStack.beginMacro("Yığın Yükle")`

### QUndoStack Mimarisi

```python
# main_window.py içinde merkezi stack
class MainWindow(QMainWindow):
    def __init__(self):
        ...
        self._undo_stack = QUndoStack(self)
        self._undo_stack.setUndoLimit(50)

        # Edit menüsüne undo/redo action'ları bağla
        undo_action = self._undo_stack.createUndoAction(self, "Geri Al")
        undo_action.setShortcut(QKeySequence.Undo)   # Ctrl+Z
        redo_action = self._undo_stack.createRedoAction(self, "İleri Al")
        redo_action.setShortcut(QKeySequence.Redo)   # Ctrl+Y / Ctrl+Shift+Z

        # Menü
        edit_menu = self.menuBar().addMenu("Düzenle")
        edit_menu.addAction(undo_action)
        edit_menu.addAction(redo_action)

        # Stack panellere dağıt
        self._panel_katman.set_undo_stack(self._undo_stack)
        self._panel_entegre.set_undo_stack(self._undo_stack)
```

### KatmanDizilimPaneli Undo Akışı

```
Kullanıcı "Sil" butonuna tıklar
  ↓
_del_row_by_widget(btn)
  ↓
satır widget referansıyla bulunur (doğru indeks)
  ↓
DeleteLayerCommand(panel=self, row=r, snapshot=self._stack.to_dict())
  ↓
undo_stack.push(cmd)
  ↓
cmd.redo() çağrılır → self._stack.remove(row) + _refresh_table()
  ↓
── Kullanıcı Ctrl+Z ──
  ↓
cmd.undo() çağrılır → self._stack = LayerStack.from_dict(snapshot) + _refresh_table()
```

### EntegreTasarimPaneli için Ek Refactoring

`EntegreTasarimPaneli` şu an backing model olmadan çalışıyor. Undo/Redo için:

```python
# Yeni: _table_to_rows() — QTableWidget'ten dict listesi oku
def _table_to_rows(self) -> list[dict]:
    rows = []
    tbl = self._layer_table
    for r in range(tbl.rowCount()):
        cb   = tbl.cellWidget(r, COL_TIP)
        rows.append({
            "type":  cb.currentText() if cb else "Sarmal",
            "alpha": float(tbl.item(r, COL_ALPHA).text() if tbl.item(r, COL_ALPHA) else "55.0"),
            "tow":   float(tbl.item(r, COL_TOW).text()   if tbl.item(r, COL_TOW)   else "6.0"),
            "n":     int(tbl.item(r, COL_N).text()        if tbl.item(r, COL_N)     else "1"),
        })
    return rows

# Yeni: _rows_to_table() — dict listesinden QTableWidget yeniden doldur
def _rows_to_table(self, rows: list[dict]) -> None:
    self._layer_table.setRowCount(0)
    for row in rows:
        self._add_layer_row(row["type"], row["alpha"], row["tow"], row["n"])
```

---

## 3. PROJE KAYDETME / YÜKLEME MİMARİSİ

### Mevcut Durum

`proje_yoneticisi.py` zaten `ProjeYoneticisiPanel` içinde JSON tabanlı kaydetme/yükleme
uygular. **Şema Versiyonu: "1.0"**

**Mevcut proje.json yapısı:**
```json
{
  "versiyon": "1.0",
  "proje_adi": "string",
  "aciklama": "string",
  "musteri": "string",
  "olusturma_tarihi": "ISO8601",
  "guncelleme_tarihi": "ISO8601",
  "malzeme": "carbon_t700_epoxy_pv",
  "guvenlik_kodu": "iso_11119_2",
  "mandrel": {
    "tip": "silindir|konik|kubbeli_silindir",
    "cap_mm": 200.0,
    "uzunluk_mm": 500.0,
    "konic_aci_deg": 0.0,
    "kubbe_yukseklik_mm": 50.0
  },
  "katmanlar": [],       ← BOŞ! KatmanDizilimPaneli verisi buraya kaydedilmiyor
  "basinc_MPa": 10.0,
  "notlar": "string"
}
```

**KRİTİK SORUN:** `katmanlar` alanı `uretim_tasarim_paneli.py::apply_project()` içinde
okunuyor ama `KatmanDizilimPaneli._stack.to_dict()` verisi projeye asla yazılmıyor.
Proje kaydedilince katman dizilimi kaybolur.

### Genişletilmiş Proje Şeması (v2.0)

```json
{
  "versiyon": "2.0",
  "proje_adi": "string",
  "aciklama": "string",
  "musteri": "string",
  "olusturma_tarihi": "ISO8601",
  "guncelleme_tarihi": "ISO8601",
  "malzeme": "string",
  "guvenlik_kodu": "string",

  "mandrel": {
    "tip": "silindir|konik|kubbeli_silindir|elipsoidal_kubbe|stl",
    "cap_mm": 200.0,
    "uzunluk_mm": 500.0,
    "konic_aci_deg": 0.0,
    "kubbe_yukseklik_mm": 50.0,
    "kubbe_hr_orani": 0.7,
    "stl_dosyasi": null
  },

  "katman_yigini": {           ← YENİ: KatmanDizilimPaneli._stack.to_dict()
    "layers": [
      {
        "id": "uuid-string",
        "type": "helical|hoop|polar",
        "alpha_deg": 55.0,
        "fitil_genisligi_mm": 6.0,
        "cakisma_pct": 5.0,
        "feed_mm_s": 80.0,
        "spindle_rpm": 60.0,
        "friction_mu": 0.0,
        "strategy": "geodesic|non_geodesic",
        "label": "string",
        "notes": "string"
      }
    ]
  },

  "entegre_panel_katmanlar": [  ← YENİ: EntegreTasarimPaneli tablosu
    {"type": "Sarmal", "alpha": 55.0, "tow": 6.0, "n": 2}
  ],

  "entegre_panel_mandrel": {   ← YENİ: EntegreTasarimPaneli mandrel ayarları
    "tip": "Silindir",
    "cap_mm": 100.0,
    "uzunluk_mm": 300.0,
    "kubbe_mm": 30.0,
    "kubbe_hr": 0.7,
    "stl_yolu": null
  },

  "sarma_parametreleri": {     ← YENİ: EntegreTasarimPaneli fiber ayarları
    "alpha_deg": 55.0,
    "fitil_genisligi_mm": 6.0,
    "kat_sayisi": 4,
    "cakisma_pct": 5.0,
    "strateji": "Sarmal (Helisel)",
    "ilerleme_mm_s": 80.0,
    "spindle_rpm": 60.0,
    "surtuname_mu": 0.0
  },

  "makine_profili_ismi": "Varsayılan Makine",  ← YENİ: aktif makine profili

  "basinc_MPa": 10.0,
  "notlar": "string"
}
```

### Sinyal Mimarisi (Kaydet/Yükle akışı)

```
KAYDETME:
ProjeYoneticisiPanel._on_save()
  → _read_form() (mevcut form alanları)
  + self.projeKayitIstegi.emit()  ← YENİ sinyal
     ↓
  MainWindow._on_proje_kayit_istegi(proje_dict)
     ↓
  proje_dict["katman_yigini"]         = panel_katman._stack.to_dict()
  proje_dict["entegre_panel_katmanlar"] = panel_entegre._table_to_rows()
  proje_dict["entegre_panel_mandrel"] = panel_entegre._mandrel_state()
  proje_dict["sarma_parametreleri"]   = panel_entegre._fiber_state()
  proje_dict["makine_profili_ismi"]   = panel_uretim.get_machine_profile().isim
     ↓
  json.dump(proje_dict, ...)

YÜKLEME:
ProjeYoneticisiPanel._load_from_file()
  → json.load(...)
  → _fill_form(data)
  → self.projeYuklendi.emit(data)  ← zaten var
     ↓
  MainWindow._connect_signals():
    panel_proje.projeYuklendi → panel_katman.apply_project  ← genişletilecek
    panel_proje.projeYuklendi → panel_entegre.apply_project ← YENİ
    panel_proje.projeYuklendi → panel_uretim.apply_project  ← zaten var
```

### Dirty Flag (Kaydedilmemiş Değişiklik)

```python
# MainWindow içinde merkezi dirty flag
class MainWindow(QMainWindow):
    def __init__(self):
        self._dirty = False
        self._proje_yolu: Optional[str] = None

    def _mark_dirty(self) -> None:
        self._dirty = True
        # Başlık çubuğuna * ekle
        if self._proje_yolu:
            name = os.path.basename(self._proje_yolu)
            self.setWindowTitle(f"* {name} — Filament Sarma CAM")

    def closeEvent(self, event):
        if self._dirty:
            reply = QMessageBox.question(
                self, "Kaydedilmemiş Değişiklikler",
                "Kapatmadan önce kaydetmek ister misiniz?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel
            )
            if reply == QMessageBox.Save:
                self._panel_proje._on_save()
                event.accept()
            elif reply == QMessageBox.Discard:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

# Hangi panel değişiklikleri dirty yapar?
# katman_dizilim_paneli:  katmanDegisti signal → _mark_dirty()
# entegre_tasarim_paneli: _layer_table.itemChanged → _mark_dirty()
# uretim_tasarim_paneli:  herhangi parametre değişikliği → _mark_dirty()
# proje_yoneticisi:       form değişikliği → _mark_dirty()
# undo_stack.cleanChanged → dirty flag sync (QUndoStack.isClean() ile entegre)
```

---

## 4. AUTO-SAVE MİMARİSİ

### Strateji

```
Tetikleyici: Herhangi panel değişikliği (_mark_dirty ile aynı bağlantı noktaları)
  ↓
30 saniyelik debounce timer (MainWindow içinde)
  ↓
Auto-save yazılır: {workspace}/projects/.autosave/{proje_uuid}.autosave.json
  ↓
Maksimum 5 auto-save versiyonu tutulur (rotate: en eskisi silinir)
```

### Auto-save Dosya Yapısı

```
workspace/
├── projects/
│   ├── projem.fwp              ← Manuel kaydedilen
│   └── .autosave/
│       ├── 20260612_143022.autosave.json
│       ├── 20260612_143052.autosave.json
│       └── 20260612_143122.autosave.json  ← en yeni
```

### Kilitlenme Kurtarma Akışı

```python
# app_launcher.py::_check_autosave() içinde (uygulama açılışında)
def _check_autosave(self) -> Optional[str]:
    autosave_dir = Path(workspace) / "projects" / ".autosave"
    if not autosave_dir.exists():
        return None
    autosaves = sorted(autosave_dir.glob("*.autosave.json"), key=os.path.mtime)
    if not autosaves:
        return None
    latest = autosaves[-1]
    # Kurtarma teklifi
    reply = QMessageBox.question(
        None,
        "Kurtarma Dosyası Bulundu",
        f"Son çalışmadan otomatik kayıt var:\n{latest.name}\n\nYüklensin mi?",
        QMessageBox.Yes | QMessageBox.No
    )
    return str(latest) if reply == QMessageBox.Yes else None
```

### Auto-save Timer (MainWindow içinde)

```python
self._autosave_timer = QTimer(self)
self._autosave_timer.setInterval(30_000)  # 30 saniye
self._autosave_timer.timeout.connect(self._do_autosave)
self._autosave_timer.start()

def _do_autosave(self) -> None:
    if not self._dirty:
        return  # Değişiklik yoksa yazma
    proje = self._collect_full_project()
    autosave_dir = Path(self._workspace) / "projects" / ".autosave"
    autosave_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = autosave_dir / f"{ts}.autosave.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(proje, f, ensure_ascii=False)
    # Eski auto-save'leri temizle (max 5 tut)
    all_saves = sorted(autosave_dir.glob("*.autosave.json"))
    for old in all_saves[:-5]:
        old.unlink()
```

---

## 5. RECIPE LIBRARY MİMARİSİ

### Mevcut Durum Analizi

Mevcut `Recipe` dataclass yalnızca **tek açılı** sarma parametrelerini saklar:

```python
@dataclass
class Recipe:
    recipe_id:    str
    version:      int
    name:         str
    alpha_deg:    float    # Tek açı
    n_layers:     int
    tension_N:    float
    feed_mm_s:    float
    # ... ama tüm katman dizilimi yok
```

Bu, gerçek bir reçete için yetersizdir. Endüstride bir "reçete":
`[55°/90°/55°/polar]` gibi çok katmanlı bir dizilim + makine profili'dir.

### Genişletilmiş Recipe Şeması

**İki seviyeli mimari:**

```
RecipeLibrary
│
├── SimpleRecipe (mevcut) — tek açı, hızlı test/prototip
│   └── fields: alpha_deg, n_layers, tension, feed, cure
│
└── LayeredRecipe (YENİ) — tam üretim reçetesi
    └── fields: name, mandrel_config, layer_sequence[], machine_profile, notes
```

```python
@dataclass
class LayerDef:
    """Bir katmanın tam tanımı."""
    layer_type:          str    = "helical"  # "helical"|"hoop"|"polar"
    alpha_deg:           float  = 55.0
    fitil_genisligi_mm:  float  = 6.0
    cakisma_pct:         float  = 5.0
    feed_mm_s:           float  = 80.0
    spindle_rpm:         float  = 60.0
    friction_mu:         float  = 0.0
    strategy:            str    = "geodesic"

@dataclass
class LayeredRecipe:
    """Tam çok katmanlı sarma reçetesi."""
    recipe_id:        str
    version:          int
    name:             str
    created_at:       float     = field(default_factory=time.time)
    notes:            str       = ""

    # Mandrel tanımı
    mandrel_tip:      str       = "silindir"
    cap_mm:           float     = 100.0
    uzunluk_mm:       float     = 300.0
    kubbe_mm:         float     = 30.0

    # Çok katmanlı dizilim
    katmanlar:        list[LayerDef] = field(default_factory=list)

    # Makine profili adı
    makine_profili:   str       = "Varsayılan Makine"

    # İşlem parametreleri
    basinc_MPa:       float     = 10.0
    malzeme:          str       = "carbon_t700_epoxy_pv"

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "LayeredRecipe":
        layers = [LayerDef(**l) for l in d.pop("katmanlar", [])]
        r = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        r.katmanlar = layers
        return r

    def checksum(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]
```

### Veritabanı Şeması (Genişletme)

```sql
-- Mevcut tablo korunur (backward compatibility)
-- recipes tablosu: SimpleRecipe için kullanılmaya devam eder

-- Yeni tablo: tam katmanlı reçeteler
CREATE TABLE IF NOT EXISTS layered_recipes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id    TEXT    NOT NULL,
    version      INTEGER NOT NULL,
    name         TEXT    NOT NULL,
    params_json  TEXT    NOT NULL,   -- LayeredRecipe.to_dict() → JSON
    created_at   REAL    NOT NULL,
    checksum     TEXT    NOT NULL,
    UNIQUE(recipe_id, version)
);
CREATE INDEX IF NOT EXISTS idx_lr_recipe_id ON layered_recipes(recipe_id);
```

### RecipeLibraryPanel UI Tasarımı

```
┌─────────────────────────────────────────────────────────────────────┐
│ 📚 Reçete Kütüphanesi                                               │
├──────────────────────────┬──────────────────────────────────────────┤
│ Reçete Listesi           │ Reçete Detayı                           │
│ ┌──────────────────────┐ │ ┌──────────────────────────────────────┐ │
│ │ 🔍 Ara...            │ │ │ Ad: [____________________]           │ │
│ │                      │ │ │ Mandrel: Ø200 × L500 mm (Silindir)  │ │
│ │ ▶ CF55 Basınçlı Kap  │ │ │                                      │ │
│ │   CF55/GF90 Hibrit   │ │ │ Katmanlar:                           │ │
│ │   Polar-Only Test    │ │ │  #1 Sarmal  55° × 2  6.0mm           │ │
│ │   Hoop-Wrap v2       │ │ │  #2 Hoop    90° × 1  6.0mm           │ │
│ │                      │ │ │  #3 Sarmal  55° × 2  6.0mm           │ │
│ │                      │ │ │                                      │ │
│ │                      │ │ │ Makine: Fanuc 4-axis                 │ │
│ └──────────────────────┘ │ │ Basınç: 10 MPa                      │ │
│                          │ └──────────────────────────────────────┘ │
│ [+ Yeni] [Sil] [Dışa]   │     [Yükle →] [Üzerine Kaydet] [Kopyala] │
└──────────────────────────┴──────────────────────────────────────────┘
```

### Recipe ↔ Panel Entegrasyonu

```python
# main_window.py sinyal bağlantıları (YENİ):
panel_recipe_lib.recipeYuklendi.connect(panel_entegre.apply_layered_recipe)
panel_recipe_lib.recipeYuklendi.connect(panel_katman.apply_layered_recipe)
panel_recipe_lib.recipeYuklendi.connect(panel_uretim.set_layer_stack)

# EntegreTasarimPaneli'ne eklenecek metot:
def apply_layered_recipe(self, recipe: LayeredRecipe) -> None:
    """Reçeteden mandrel + katman + parametreleri yükle."""
    self._cb_type.setCurrentText(_TIP_MAP[recipe.mandrel_tip])
    self._sp_diam.setValue(recipe.cap_mm)
    self._sp_len.setValue(recipe.uzunluk_mm)
    self._layer_table.setRowCount(0)
    for ld in recipe.katmanlar:
        type_map = {"helical": "Sarmal", "hoop": "Hoop", "polar": "Polar"}
        self._add_layer_row(type_map.get(ld.layer_type, "Sarmal"),
                            ld.alpha_deg, ld.fitil_genisligi_mm, 1)
```

---

## 6. GÖÇ PLANI (MIGRATION PLAN)

### Mevcut Veri Uyumluluğu

| Konu | Risk | Strateji |
|------|------|----------|
| Mevcut `.fwp` projeleri (v1.0 format) | Düşük | Şema versiyonu kontrolü; v1.0 dosyalar `katman_yigini` olmadan yüklenir (boş başlar) |
| Mevcut `recipes.db` SQLite | Sıfır | Yeni `layered_recipes` tablosu ek olarak eklenir; eski tablo dokunulmaz |
| Mevcut `_del_row()` davranışı | Kritik | Widget-referans yaklaşımına geçiş; mevcut testler güncellenir |
| Mevcut `UretimTasarimPaneli.apply_project()` | Düşük | `katmanlar` → `katman_yigini` adaptörü eklenir |
| Mevcut sinyal bağlantıları | Orta | Yeni sinyaller additive; mevcut bağlantılar korunur |

### Şema Versiyon Yükseltme

```python
def _migrate_project(data: dict) -> dict:
    """v1.0 → v2.0 otomatik migrasyon."""
    if data.get("versiyon") == "1.0":
        data["versiyon"] = "2.0"
        # v1.0'da katmanlar alanı UretimTasarim formatında, yeni şemaya taşı
        old_layers = data.pop("katmanlar", [])
        data["katman_yigini"] = {"layers": [
            {
                "id": str(uuid.uuid4()),
                "type": l.get("tip", "helical"),
                "alpha_deg": l.get("aci_deg", 45.0),
                "fitil_genisligi_mm": 6.0,
                "cakisma_pct": 5.0,
                "feed_mm_s": 80.0,
                "spindle_rpm": 60.0,
                "friction_mu": 0.0,
                "strategy": "geodesic",
                "label": f"L{i+1}",
                "notes": ""
            }
            for i, l in enumerate(old_layers)
        ]}
        data["entegre_panel_katmanlar"] = []
        data["sarma_parametreleri"] = {}
        data["makine_profili_ismi"] = "Varsayılan Makine"
    return data
```

---

## 7. REGRESYON RİSKLERİ

| Risk | Etki | Olasılık | Önlem |
|------|------|----------|-------|
| `_del_row()` refactoring mevcut testleri kırar | Orta | Orta | `test_cam_core.py`'ye katman tablosu testleri ekle |
| Yeni sinyal bağlantıları döngü yaratır | Yüksek | Düşük | Tüm bağlantılar `connect()` ile tek yönlü; döngü kontrolü |
| `QUndoStack` debounce ile çakışır | Orta | Orta | Debounce timer ateşlenince yalnızca snapshot alınır, stack push edilmez |
| Auto-save timer workspace yokken hata | Düşük | Düşük | `mkdir(parents=True, exist_ok=True)` zaten var |
| Şema v2.0 `katman_yigini` ile `katmanlar` alanı çakışır | Yüksek | Orta | `_migrate_project()` her yüklemede çalıştırılır |
| Recipe DB'ye yeni tablo eklemek mevcut DB'yi bozar | Düşük | Düşük | `CREATE TABLE IF NOT EXISTS` — güvenli |
| 8/8 phase17_d2_validation testi kırılır | Yüksek | Düşük | Validation koşturulur; regresyon yoksa commit |

---

## 8. UYGULAMA SIRASI (ÖNERILEN)

```
Sprint 1 (1 gün) — Temel kararlılık
  1a. BUG FIX: _del_row() → widget kimliği karşılaştırmasına geçiş
  1b. Dirty flag altyapısı + closeEvent kaydetme uyarısı
  1c. Proje şeması v2.0 + _migrate_project()
  1d. Proje kaydetme/yükleme katman_yigini + entegre_panel_katmanlar dahil
  ── Validation: 8/8 PASS ──

Sprint 2 (1-2 gün) — Undo/Redo
  2a. QUndoStack MainWindow'a ekleme + Edit menüsü
  2b. AddLayerCommand, DeleteLayerCommand (KatmanDizilimPaneli)
  2c. EditLayerCommand (hücre değişikliği)
  2d. ChangeMandrelCommand (EntegreTasarimPaneli)
  2e. EntegreTasarimPaneli _table_to_rows() + _rows_to_table() + Undo bağlantısı
  ── Validation: 8/8 PASS ──

Sprint 3 (0.5 gün) — Auto-save
  3a. Auto-save timer (30s) MainWindow
  3b. _collect_full_project() toplama metodu
  3c. Açılışta autosave kurtarma teklifi
  ── Validation: 8/8 PASS ──

Sprint 4 (1 gün) — Recipe Library
  4a. LayerDef + LayeredRecipe dataclass
  4b. RecipeDB'ye layered_recipes tablosu
  4c. RecipeLibraryPanel UI (liste + detay + CRUD)
  4d. recipe → panel sinyal bağlantıları
  ── Validation: 8/8 PASS ──
```

### Önerilen Geliştirme Sırası Özeti

```
BUG #1 FIX → Dirty Flag → Proje Şema v2.0 → Undo/Redo → Auto-save → Recipe Library
    ↑                           ↑                ↑
  1 saat                     yarım gün          1-2 gün
```

**Sprint 1 bütünüyle 1 iş günü** — en yüksek güvenlik-getiri oranına sahip değişiklikler.
Sprint 2-4 kümülatif 3-4 gün.

---

*Bu analiz `claude/amazing-feynman-XUXBf` branchindeki tam kaynak okumaya dayanmaktadır.
Kod geliştirmeye başlamadan önce onayınız beklenmektedir.*
