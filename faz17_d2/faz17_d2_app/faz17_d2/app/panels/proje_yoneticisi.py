"""
panels/proje_yoneticisi.py — Proje Yöneticisi Paneli
======================================================
Filament sarma CAM projesini oluşturma, açma, kaydetme ve yönetme.

Proje dosyası biçimi: JSON (.fwp — Filament Winding Project)
Şema v2.0:
  {
    "versiyon": "2.0",
    "proje_adi": str,
    "aciklama": str,
    "musteri": str,
    "olusturma_tarihi": str,    # ISO 8601
    "guncelleme_tarihi": str,
    "malzeme": str,             # material_allowables key
    "guvenlik_kodu": str,       # SafetyCode.value
    "mandrel": {
      "tip": "silindir"|"konik"|"kubbeli_silindir",
      "cap_mm": float,
      "uzunluk_mm": float,
      "konic_aci_deg": float,
      "kubbe_yukseklik_mm": float
    },
    "katmanlar": [              # v1.0 özet liste (geriye dönük uyumluluk)
      {"tip": "sarmal"|"cevre", "aci_deg": float, "cift_sayisi": int,
       "ply_kalinlik_mm": float}
    ],
    "basinc_MPa": float,
    "notlar": str,

    # ── v2.0 alanları ──
    "katman_yigini": {          # KatmanDizilimPaneli LayerStack.to_dict()
      "versiyon": "1.0", "default_friction_mu": float,
      "next_id": int, "layers": [LayerSpec dict, ...]
    },
    "entegre_panel_katmanlar": [  # EntegreTasarimPaneli katman tablosu
      {"tip": str, "alpha_deg": float, "fitil_mm": float, "n_kat": int}
    ],
    "entegre_panel_mandrel": {...},   # EntegreTasarimPaneli mandrel ayarları
    "sarma_parametreleri": {...},     # EntegreTasarimPaneli fiber/sarma ayarları
    "makine_profili_ismi": str        # aktif makine profili (gelecek kullanım)
  }

Eski v1.0 dosyaları `_migrate_project()` ile kayıpsız olarak v2.0'a
yükseltilerek açılır — hiçbir mevcut proje kırılmaz.
"""
from __future__ import annotations

import json
import os
import time
import datetime
from pathlib import Path
from typing import Callable, Dict, Any, List, Optional

from PySide6.QtCore import Qt, Signal, QSettings
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QGroupBox, QLabel, QLineEdit, QTextEdit,
    QPushButton, QListWidget, QListWidgetItem,
    QFileDialog, QMessageBox, QComboBox,
    QDoubleSpinBox, QGridLayout, QTabWidget,
    QFormLayout, QFrame,
)

from ..themes.dark_industrial import COLOR

_SCHEMA_VERSION = "2.0"
_FILE_FILTER = "Filament Sarma Projesi (*.fwp);;JSON Dosyaları (*.json);;Tüm Dosyalar (*)"
_SETTINGS_KEY = "recent_projects"
_MAX_RECENT = 10

_FIELD_STYLE = (
    "background: #1A1A2E; color: #E8E8E8; "
    "border: 1px solid #3A3A5C; padding: 3px;"
)
_HDR_STYLE = "font-weight: bold; color: #A0C8F0;"
_TITLE_STYLE = "font-size: 16px; font-weight: bold; color: #E8E8E8;"


# ── Proje veri modeli ────────────────────────────────────────────────────────

def _empty_project() -> Dict[str, Any]:
    now = datetime.datetime.now().isoformat(timespec="seconds")
    return {
        "versiyon": _SCHEMA_VERSION,
        "proje_adi": "Yeni Proje",
        "aciklama": "",
        "musteri": "",
        "olusturma_tarihi": now,
        "guncelleme_tarihi": now,
        "malzeme": "carbon_t700_epoxy_pv",
        "guvenlik_kodu": "iso_11119_2",
        "mandrel": {
            "tip": "silindir",
            "cap_mm": 200.0,
            "uzunluk_mm": 500.0,
            "konic_aci_deg": 0.0,
            "kubbe_yukseklik_mm": 50.0,
        },
        "katmanlar": [],
        "basinc_MPa": 10.0,
        "notlar": "",
        # v2.0 alanları
        "katman_yigini": {"layers": []},
        "entegre_panel_katmanlar": [],
        "entegre_panel_mandrel": {},
        "sarma_parametreleri": {},
        "makine_profili_ismi": "",
    }


def _migrate_project(data: Dict[str, Any]) -> Dict[str, Any]:
    """Eski şema sürümlerini kayıpsız olarak v2.0'a yükselt.

    v1.0 dosyalarında bulunmayan v2.0 alanları güvenli varsayılanlarla
    eklenir; mevcut tüm alanlar olduğu gibi korunur. Her dosya yüklemesinde
    zorunlu olarak çağrılır — hiçbir eski proje kırılmaz.
    """
    if not isinstance(data, dict):
        raise ValueError("Proje dosyası geçerli bir JSON nesnesi değil")

    ver = str(data.get("versiyon", "1.0"))
    if ver in ("1.0", "1"):
        data["versiyon"] = _SCHEMA_VERSION

    # v1.0 çekirdek alanlarına tolerans (eksik/bozuk dosyalar)
    data.setdefault("proje_adi", "Adsız Proje")
    data.setdefault("mandrel", {})
    data.setdefault("katmanlar", [])

    # v2.0 alanları
    yigin = data.get("katman_yigini")
    if not isinstance(yigin, dict) or "layers" not in yigin:
        data["katman_yigini"] = {"layers": []}
    if not isinstance(data.get("entegre_panel_katmanlar"), list):
        data["entegre_panel_katmanlar"] = []
    if not isinstance(data.get("entegre_panel_mandrel"), dict):
        data["entegre_panel_mandrel"] = {}
    if not isinstance(data.get("sarma_parametreleri"), dict):
        data["sarma_parametreleri"] = {}
    data.setdefault("makine_profili_ismi", "")
    return data


# ── Ana panel ────────────────────────────────────────────────────────────────

class ProjeYoneticisiPanel(QWidget):
    """
    Proje yöneticisi paneli.

    Yeni proje oluştur, mevcut proje aç, kaydet, farklı kaydet.
    Son açılan projeler listesi kalıcı QSettings ile saklanır.
    Proje bilgileri (ad, müşteri, açıklama, mandrel, katman özetleri)
    düzenlenebilir formda gösterilir.
    """

    # Diğer paneller proje değişikliklerini dinler
    projeYuklendi  = Signal(dict)   # proje dict
    malzemeSecildi = Signal(str)    # material key
    degisiklikDurumu = Signal(bool) # kirli bayrak değişimi (True = kaydedilmemiş)

    # Proje yükleme/uygulama sonrası panellerin debounce'lu sinyalleri sahte
    # kirlilik üretmesin diye uygulanan bağışıklık penceresi (saniye).
    _DIRTY_GRACE_S = 1.5

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._proje: Dict[str, Any] = _empty_project()
        self._dosya_yolu: Optional[str] = None
        self._degistirildi = False
        self._dirty_grace_until = 0.0
        # Kayıt sırasında katman verisi sağlayan callable'lar (MainWindow kurar)
        self._katman_provider: Optional[Callable[[], Dict[str, Any]]] = None
        self._entegre_provider: Optional[Callable[[], Dict[str, Any]]] = None
        # Sprint 3: otomatik kayıt yöneticisi (MainWindow kurar)
        self._autosave_mgr = None
        self._settings = QSettings("FilamentSarma", "ProjeYoneticisi")
        self._build_ui()
        self._load_recent_list()
        self._fill_form(self._proje)

    # ── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Başlık + araç çubuğu
        hdr = QHBoxLayout()
        title = QLabel("Proje Yöneticisi")
        title.setStyleSheet(_TITLE_STYLE)
        hdr.addWidget(title)
        hdr.addStretch()

        for label, handler, tooltip in [
            ("Yeni",          self._on_new,    "Yeni proje oluştur (Ctrl+N)"),
            ("Aç…",           self._on_open,   "Proje dosyası aç (Ctrl+O)"),
            ("Kaydet",        self._on_save,   "Projeyi kaydet (Ctrl+S)"),
            ("Farklı Kaydet…",self._on_save_as,"Farklı konuma kaydet"),
        ]:
            b = QPushButton(label)
            b.setToolTip(tooltip)
            b.clicked.connect(handler)
            b.setStyleSheet(
                "QPushButton { background: #252540; color: #C0C0E0; "
                "padding: 6px 12px; border: 1px solid #3A3A5C; border-radius: 3px; }"
                "QPushButton:hover { background: #3A3A60; }"
            )
            hdr.addWidget(b)
        root.addLayout(hdr)

        self._lbl_path = QLabel("Kaydedilmemiş proje")
        self._lbl_path.setStyleSheet("color: #707070; font-size: 11px;")
        root.addWidget(self._lbl_path)

        splitter = QSplitter(Qt.Horizontal)

        # Sol: son projeler ─────────────────────────────────────────────────
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)

        lbl = QLabel("Son Projeler")
        lbl.setStyleSheet(_HDR_STYLE)
        ll.addWidget(lbl)

        self._recent_list = QListWidget()
        self._recent_list.setStyleSheet(
            "QListWidget { background: #1A1A2E; color: #E8E8E8; "
            "border: 1px solid #3A3A5C; }"
            "QListWidget::item:selected { background: #2A3A6A; }"
        )
        self._recent_list.itemDoubleClicked.connect(self._on_recent_open)
        ll.addWidget(self._recent_list)

        btn_temizle = QPushButton("Listeyi Temizle")
        btn_temizle.setStyleSheet(
            "QPushButton { background: #2A1A1A; color: #C06060; "
            "padding: 4px; border: 1px solid #5A2A2A; border-radius: 2px; }"
        )
        btn_temizle.clicked.connect(self._clear_recent)
        ll.addWidget(btn_temizle)

        left.setMinimumWidth(220)
        left.setMaximumWidth(300)
        splitter.addWidget(left)

        # Sağ: proje formu ──────────────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)
        rl.setSpacing(6)

        self._form_tabs = QTabWidget()
        self._form_tabs.setStyleSheet("QTabBar::tab { padding: 6px 14px; }")

        self._build_bilgi_tab()
        self._build_mandrel_tab()
        self._build_ozet_tab()
        self._build_history_tab()

        rl.addWidget(self._form_tabs)
        rl.addWidget(self._build_action_bar())

        splitter.addWidget(right)
        splitter.setSizes([260, 680])
        root.addWidget(splitter)

    def _build_bilgi_tab(self) -> None:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(12, 12, 12, 12)
        f.setSpacing(10)
        f.setLabelAlignment(Qt.AlignRight)

        self._fld_adi = QLineEdit()
        self._fld_adi.setStyleSheet(_FIELD_STYLE)
        self._fld_adi.textChanged.connect(self._mark_dirty)
        f.addRow("Proje Adı:", self._fld_adi)

        self._fld_musteri = QLineEdit()
        self._fld_musteri.setStyleSheet(_FIELD_STYLE)
        self._fld_musteri.textChanged.connect(self._mark_dirty)
        f.addRow("Müşteri:", self._fld_musteri)

        self._fld_aciklama = QTextEdit()
        self._fld_aciklama.setMaximumHeight(80)
        self._fld_aciklama.setStyleSheet(_FIELD_STYLE)
        self._fld_aciklama.textChanged.connect(self._mark_dirty)
        f.addRow("Açıklama:", self._fld_aciklama)

        self._fld_tarih_olustur = QLineEdit()
        self._fld_tarih_olustur.setReadOnly(True)
        self._fld_tarih_olustur.setStyleSheet(_FIELD_STYLE)
        f.addRow("Oluşturulma:", self._fld_tarih_olustur)

        self._fld_tarih_guncelle = QLineEdit()
        self._fld_tarih_guncelle.setReadOnly(True)
        self._fld_tarih_guncelle.setStyleSheet(_FIELD_STYLE)
        f.addRow("Son Güncelleme:", self._fld_tarih_guncelle)

        self._fld_basinc = QDoubleSpinBox()
        self._fld_basinc.setRange(0.1, 200)
        self._fld_basinc.setValue(10)
        self._fld_basinc.setSingleStep(0.5)
        self._fld_basinc.setDecimals(2)
        self._fld_basinc.setSuffix(" MPa")
        self._fld_basinc.setStyleSheet(_FIELD_STYLE)
        self._fld_basinc.valueChanged.connect(self._mark_dirty)
        f.addRow("Çalışma Basıncı:", self._fld_basinc)

        self._fld_malzeme = QComboBox()
        self._fld_malzeme.setStyleSheet(_FIELD_STYLE)
        self._populate_material_combo()
        self._fld_malzeme.currentIndexChanged.connect(self._mark_dirty)
        f.addRow("Malzeme:", self._fld_malzeme)

        self._fld_kod = QComboBox()
        self._fld_kod.setStyleSheet(_FIELD_STYLE)
        code_opts = [
            ("ASME BPVC X (SF=2.25)",       "asme_bpvc_x"),
            ("ISO 11119-2 (SF=2.25)",        "iso_11119_2"),
            ("ISO 11119-3 (SF=2.35)",        "iso_11119_3"),
            ("AIAA S-080 (SF=2.0)",          "aiaa_s_080"),
            ("US DOT CFFC (SF=3.0)",         "dot_cffc"),
            ("EN 12245 (SF=2.25)",           "en_12245"),
            ("UN ECE R134 (SF=2.25)",        "un_ece_r134"),
        ]
        for label, val in code_opts:
            self._fld_kod.addItem(label, val)
        self._fld_kod.setCurrentIndex(1)
        self._fld_kod.currentIndexChanged.connect(self._mark_dirty)
        f.addRow("Güvenlik Kodu:", self._fld_kod)

        self._fld_notlar = QTextEdit()
        self._fld_notlar.setMaximumHeight(80)
        self._fld_notlar.setStyleSheet(_FIELD_STYLE)
        self._fld_notlar.textChanged.connect(self._mark_dirty)
        f.addRow("Notlar:", self._fld_notlar)

        self._form_tabs.addTab(w, "Proje Bilgileri")

    def _build_mandrel_tab(self) -> None:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(12, 12, 12, 12)
        f.setSpacing(10)
        f.setLabelAlignment(Qt.AlignRight)

        self._fld_mandrel_tip = QComboBox()
        self._fld_mandrel_tip.addItems(
            ["Silindir", "Konik", "Kubbeli Silindir"])
        self._fld_mandrel_tip.setStyleSheet(_FIELD_STYLE)
        self._fld_mandrel_tip.currentIndexChanged.connect(self._on_mandrel_tip_changed)
        self._fld_mandrel_tip.currentIndexChanged.connect(self._mark_dirty)
        f.addRow("Mandrel Tipi:", self._fld_mandrel_tip)

        self._fld_cap = QDoubleSpinBox()
        self._fld_cap.setRange(10, 5000)
        self._fld_cap.setValue(200)
        self._fld_cap.setSingleStep(10)
        self._fld_cap.setDecimals(1)
        self._fld_cap.setSuffix(" mm")
        self._fld_cap.setStyleSheet(_FIELD_STYLE)
        self._fld_cap.valueChanged.connect(self._mark_dirty)
        f.addRow("İç Çap:", self._fld_cap)

        self._fld_uzunluk = QDoubleSpinBox()
        self._fld_uzunluk.setRange(10, 20000)
        self._fld_uzunluk.setValue(500)
        self._fld_uzunluk.setSingleStep(10)
        self._fld_uzunluk.setDecimals(1)
        self._fld_uzunluk.setSuffix(" mm")
        self._fld_uzunluk.setStyleSheet(_FIELD_STYLE)
        self._fld_uzunluk.valueChanged.connect(self._mark_dirty)
        f.addRow("Silindir Uzunluğu:", self._fld_uzunluk)

        self._fld_konic_aci = QDoubleSpinBox()
        self._fld_konic_aci.setRange(0, 60)
        self._fld_konic_aci.setValue(0)
        self._fld_konic_aci.setDecimals(1)
        self._fld_konic_aci.setSuffix(" °")
        self._fld_konic_aci.setStyleSheet(_FIELD_STYLE)
        self._fld_konic_aci.valueChanged.connect(self._mark_dirty)
        f.addRow("Konik Açısı:", self._fld_konic_aci)

        self._fld_kubbe = QDoubleSpinBox()
        self._fld_kubbe.setRange(0, 2000)
        self._fld_kubbe.setValue(50)
        self._fld_kubbe.setSingleStep(5)
        self._fld_kubbe.setDecimals(1)
        self._fld_kubbe.setSuffix(" mm")
        self._fld_kubbe.setStyleSheet(_FIELD_STYLE)
        self._fld_kubbe.valueChanged.connect(self._mark_dirty)
        f.addRow("Kubbe Yüksekliği:", self._fld_kubbe)

        self._form_tabs.addTab(w, "Mandrel Geometrisi")
        self._on_mandrel_tip_changed(0)

    def _build_ozet_tab(self) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        lbl = QLabel("Proje Özeti")
        lbl.setStyleSheet(_HDR_STYLE)
        v.addWidget(lbl)

        self._ozet_text = QTextEdit()
        self._ozet_text.setReadOnly(True)
        self._ozet_text.setStyleSheet(
            "QTextEdit { background: #0E0E1E; color: #C0D0C0; "
            "font-family: monospace; font-size: 12px; }"
        )
        v.addWidget(self._ozet_text)

        btn_guncelle = QPushButton("Özeti Güncelle")
        btn_guncelle.setStyleSheet(
            "QPushButton { background: #252540; color: #C0C0E0; "
            "padding: 5px; border: 1px solid #3A3A5C; border-radius: 2px; }"
        )
        btn_guncelle.clicked.connect(self._update_summary)
        v.addWidget(btn_guncelle)

        self._form_tabs.addTab(w, "Özet")

    def _build_history_tab(self) -> None:
        """Sürüm Geçmişi sekmesi (Faz 25 Sprint 3 — Project History).

        Otomatik kayıt sürümlerini zaman damgasıyla listeler; kullanıcı
        eski bir sürüme dönebilir. Liste, autosave yöneticisi MainWindow
        tarafından `set_autosave_manager()` ile kurulduğunda dolar.
        """
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        lbl = QLabel("Otomatik Kayıt Sürümleri (en yeni üstte)")
        lbl.setStyleSheet(_HDR_STYLE)
        v.addWidget(lbl)

        self._history_list = QListWidget()
        self._history_list.setStyleSheet(
            "QListWidget { background: #1A1A2E; color: #E8E8E8; "
            "border: 1px solid #3A3A5C; }"
            "QListWidget::item { padding: 5px; }"
            "QListWidget::item:selected { background: #2A3A6A; }")
        v.addWidget(self._history_list, stretch=1)

        hb = QHBoxLayout()
        btn_refresh = QPushButton("⟳ Yenile")
        btn_refresh.setStyleSheet(
            "QPushButton { background: #252540; color: #C0C0E0; "
            "padding: 6px 12px; border: 1px solid #3A3A5C; "
            "border-radius: 3px; }")
        btn_refresh.clicked.connect(self.refresh_history)
        hb.addWidget(btn_refresh)
        hb.addStretch()

        btn_revert = QPushButton("⤺ Bu Sürüme Dön")
        btn_revert.setStyleSheet(
            "QPushButton { background: #1A6B3C; color: white; "
            "padding: 6px 14px; border: none; border-radius: 3px; "
            "font-weight: bold; }"
            "QPushButton:hover { background: #2A8B4C; }")
        btn_revert.clicked.connect(self._on_revert_to_version)
        hb.addWidget(btn_revert)
        v.addLayout(hb)

        self._history_hint = QLabel(
            "Otomatik kayıt etkin değil — sürüm listesi boş.")
        self._history_hint.setStyleSheet("color: #707070; font-size: 11px;")
        v.addWidget(self._history_hint)

        self._form_tabs.addTab(w, "Sürüm Geçmişi")

    def _build_action_bar(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 4, 0, 0)
        h.setSpacing(8)
        h.addStretch()

        self._lbl_durum = QLabel("Kaydedilmemiş değişiklikler")
        self._lbl_durum.setStyleSheet("color: #707070; font-size: 11px;")
        h.addWidget(self._lbl_durum)

        btn_uygula = QPushButton("Projeyi Kaydet ve Panellere Uygula")
        btn_uygula.setStyleSheet(
            "QPushButton { background: #1A6B3C; color: white; "
            "padding: 8px 16px; border: none; border-radius: 3px; font-weight: bold; }"
            "QPushButton:hover { background: #2A8B4C; }"
        )
        btn_uygula.clicked.connect(self._on_apply_to_panels)
        h.addWidget(btn_uygula)
        return w

    # ── Combo doldurma ───────────────────────────────────────────────────────

    def _populate_material_combo(self) -> None:
        try:
            from backend.core.material_allowables import (
                available_engineering_materials, get_engineering_material,
            )
            for key in available_engineering_materials():
                try:
                    mat = get_engineering_material(key)
                    self._fld_malzeme.addItem(mat.name, key)
                except Exception:
                    self._fld_malzeme.addItem(key, key)
        except Exception:
            self._fld_malzeme.addItem("T700S/Epoksi (varsayılan)", "carbon_t700_epoxy_pv")

    # ── Form doldurma / okuma ────────────────────────────────────────────────

    def _fill_form(self, proje: Dict[str, Any]) -> None:
        self._fld_adi.blockSignals(True)
        self._fld_adi.setText(proje.get("proje_adi", ""))
        self._fld_adi.blockSignals(False)

        self._fld_musteri.setText(proje.get("musteri", ""))
        self._fld_aciklama.setPlainText(proje.get("aciklama", ""))
        self._fld_tarih_olustur.setText(proje.get("olusturma_tarihi", ""))
        self._fld_tarih_guncelle.setText(proje.get("guncelleme_tarihi", ""))
        self._fld_basinc.setValue(proje.get("basinc_MPa", 10.0))
        self._fld_notlar.setPlainText(proje.get("notlar", ""))

        mat_key = proje.get("malzeme", "")
        for i in range(self._fld_malzeme.count()):
            if self._fld_malzeme.itemData(i) == mat_key:
                self._fld_malzeme.setCurrentIndex(i)
                break

        kod = proje.get("guvenlik_kodu", "iso_11119_2")
        for i in range(self._fld_kod.count()):
            if self._fld_kod.itemData(i) == kod:
                self._fld_kod.setCurrentIndex(i)
                break

        m = proje.get("mandrel", {})
        tip_map = {"silindir": 0, "konik": 1, "kubbeli_silindir": 2}
        self._fld_mandrel_tip.setCurrentIndex(tip_map.get(m.get("tip", "silindir"), 0))
        self._fld_cap.setValue(m.get("cap_mm", 200.0))
        self._fld_uzunluk.setValue(m.get("uzunluk_mm", 500.0))
        self._fld_konic_aci.setValue(m.get("konic_aci_deg", 0.0))
        self._fld_kubbe.setValue(m.get("kubbe_yukseklik_mm", 50.0))

        self._update_summary()
        self._degistirildi = False
        self._dirty_grace_until = time.monotonic() + self._DIRTY_GRACE_S
        self._update_status()

    def _read_form(self) -> Dict[str, Any]:
        tip_map = {0: "silindir", 1: "konik", 2: "kubbeli_silindir"}
        now = datetime.datetime.now().isoformat(timespec="seconds")
        data = {
            "versiyon": _SCHEMA_VERSION,
            "proje_adi": self._fld_adi.text().strip() or "Adsız Proje",
            "aciklama": self._fld_aciklama.toPlainText().strip(),
            "musteri": self._fld_musteri.text().strip(),
            "olusturma_tarihi": self._proje.get("olusturma_tarihi", now),
            "guncelleme_tarihi": now,
            "malzeme": self._fld_malzeme.currentData() or "carbon_t700_epoxy_pv",
            "guvenlik_kodu": self._fld_kod.currentData() or "iso_11119_2",
            "mandrel": {
                "tip": tip_map[self._fld_mandrel_tip.currentIndex()],
                "cap_mm": self._fld_cap.value(),
                "uzunluk_mm": self._fld_uzunluk.value(),
                "konic_aci_deg": self._fld_konic_aci.value(),
                "kubbe_yukseklik_mm": self._fld_kubbe.value(),
            },
            "katmanlar": self._proje.get("katmanlar", []),
            "basinc_MPa": self._fld_basinc.value(),
            "notlar": self._fld_notlar.toPlainText().strip(),
            # v2.0 alanları — paneller veri sağlamazsa son bilinen değer korunur
            "katman_yigini": self._proje.get("katman_yigini", {"layers": []}),
            "entegre_panel_katmanlar":
                self._proje.get("entegre_panel_katmanlar", []),
            "entegre_panel_mandrel":
                self._proje.get("entegre_panel_mandrel", {}),
            "sarma_parametreleri":
                self._proje.get("sarma_parametreleri", {}),
            "makine_profili_ismi":
                self._proje.get("makine_profili_ismi", ""),
        }

        # Canlı panel verisi (MainWindow'un kurduğu sağlayıcılar üzerinden)
        if self._katman_provider is not None:
            try:
                yigin = self._katman_provider()
                if isinstance(yigin, dict) and "layers" in yigin:
                    data["katman_yigini"] = yigin
            except Exception:
                pass  # sağlayıcı hatası kaydı engellemesin
        if self._entegre_provider is not None:
            try:
                st = self._entegre_provider() or {}
                data["entegre_panel_mandrel"]   = st.get("mandrel", {})
                data["sarma_parametreleri"]     = st.get("sarma", {})
                data["entegre_panel_katmanlar"] = st.get("katmanlar", [])
            except Exception:
                pass

        return data

    # ── Durum yönetimi ───────────────────────────────────────────────────────

    def _mark_dirty(self) -> None:
        self._degistirildi = True
        self._update_status()

    def mark_dirty_external(self) -> None:
        """Diğer panellerden (katman tablosu vb.) gelen değişiklik bildirimi.

        Proje yüklemesinin hemen ardından panellerin debounce'lu sinyalleri
        (autosend, katmanDegisti) sahte kirlilik üretmesin diye kısa bir
        bağışıklık penceresi uygulanır.
        """
        if time.monotonic() < self._dirty_grace_until:
            return
        self._mark_dirty()

    def has_unsaved_changes(self) -> bool:
        """Kaydedilmemiş değişiklik var mı (MainWindow.closeEvent için)."""
        return self._degistirildi

    def save_current(self) -> bool:
        """Mevcut projeyi kaydet (MainWindow.closeEvent için). True = başarılı."""
        return self._on_save()

    def set_data_providers(
        self,
        katman_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        entegre_provider: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> None:
        """Kayıt sırasında katman verisi sağlayan callable'ları kur.

        katman_provider  : KatmanDizilimPaneli.get_stack_dict
        entegre_provider : EntegreTasarimPaneli.get_design_state
        """
        self._katman_provider = katman_provider
        self._entegre_provider = entegre_provider

    def _update_status(self) -> None:
        if self._degistirildi:
            self._lbl_durum.setText("⚠ Kaydedilmemiş değişiklikler")
            self._lbl_durum.setStyleSheet("color: #FFB050; font-size: 11px;")
        else:
            self._lbl_durum.setText("✓ Kaydedildi")
            self._lbl_durum.setStyleSheet("color: #50C050; font-size: 11px;")

        path_txt = self._dosya_yolu or "Kaydedilmemiş proje"
        self._lbl_path.setText(path_txt)
        self.degisiklikDurumu.emit(self._degistirildi)

    def _update_summary(self) -> None:
        p = self._read_form()
        m = p.get("mandrel", {})
        tip_names = {
            "silindir": "Silindir",
            "konik": "Konik",
            "kubbeli_silindir": "Kubbeli Silindir",
        }
        katman_sayisi = len(p.get("katmanlar", []))

        lines = [
            f"Proje Adı    : {p['proje_adi']}",
            f"Müşteri      : {p.get('musteri') or '—'}",
            f"Açıklama     : {p.get('aciklama') or '—'}",
            "",
            f"Malzeme      : {p.get('malzeme', '—')}",
            f"Güvenlik Kodu: {p.get('guvenlik_kodu', '—')}",
            f"Çalışma Bas. : {p.get('basinc_MPa', 0):.2f} MPa",
            "",
            f"Mandrel Tipi : {tip_names.get(m.get('tip',''), '—')}",
            f"İç Çap       : {m.get('cap_mm', 0):.1f} mm",
            f"Uzunluk      : {m.get('uzunluk_mm', 0):.1f} mm",
            f"Katman Sayısı: {katman_sayisi}",
            "",
            f"Oluşturulma  : {p.get('olusturma_tarihi', '—')}",
            f"Son Güncelle : {p.get('guncelleme_tarihi', '—')}",
        ]
        if p.get("notlar"):
            lines += ["", "Notlar:", p["notlar"]]

        self._ozet_text.setPlainText("\n".join(lines))

    # ── Mandrel tipi değişimi ────────────────────────────────────────────────

    def _on_mandrel_tip_changed(self, idx: int) -> None:
        self._fld_konic_aci.setEnabled(idx == 1)
        self._fld_kubbe.setEnabled(idx == 2)

    # ── Dosya işlemleri ──────────────────────────────────────────────────────

    def _ask_save_if_dirty(self) -> bool:
        """Kaydedilmemiş değişiklik varsa sor. True = devam et, False = iptal."""
        if not self._degistirildi:
            return True
        reply = QMessageBox.question(
            self, "Kaydedilmemiş Değişiklikler",
            "Mevcut projede kaydedilmemiş değişiklikler var.\n"
            "Devam etmeden önce kaydetmek ister misiniz?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if reply == QMessageBox.Save:
            return self._on_save()
        if reply == QMessageBox.Discard:
            return True
        return False

    def _on_new(self) -> None:
        if not self._ask_save_if_dirty():
            return
        self._proje = _empty_project()
        self._dosya_yolu = None
        self._fill_form(self._proje)

    def _on_open(self) -> None:
        if not self._ask_save_if_dirty():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Proje Aç", "", _FILE_FILTER)
        if path:
            self._load_from_file(path)

    def _on_save(self) -> bool:
        if self._dosya_yolu:
            return self._save_to_file(self._dosya_yolu)
        return self._on_save_as()

    def _on_save_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self, "Farklı Kaydet", "", _FILE_FILTER)
        if path:
            if not path.endswith((".fwp", ".json")):
                path += ".fwp"
            return self._save_to_file(path)
        return False

    def _load_from_file(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.load_project_dict(data, dosya_yolu=path)
            self._add_to_recent(path)
        except Exception as exc:
            QMessageBox.critical(self, "Açma Hatası", f"Dosya okunamadı:\n{exc}")

    def load_project_dict(self, data: Dict[str, Any],
                          dosya_yolu: Optional[str] = None) -> None:
        """Proje dict'ini yükle ve panellere uygula (dosya, autosave
        kurtarması ve sürüm geçmişi geri dönüşü için ortak yol).

        Migrasyon her zaman zorunlu — v1.0 verisi kayıpsız v2.0 olur.
        """
        self._proje = _migrate_project(data)
        self._dosya_yolu = dosya_yolu
        self._fill_form(self._proje)
        # Yüklenen projeyi panellere otomatik uygula — katman dizilimi
        # dahil tüm tasarım verisi geri yüklenir
        self.projeYuklendi.emit(self._proje)
        mat_key = self._proje.get("malzeme", "")
        if mat_key:
            self.malzemeSecildi.emit(mat_key)

    def _save_to_file(self, path: str) -> bool:
        try:
            self._proje = self._read_form()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._proje, f, ensure_ascii=False, indent=2)
            self._dosya_yolu = path
            self._degistirildi = False
            self._update_status()
            self._add_to_recent(path)
            self._fld_tarih_guncelle.setText(
                self._proje.get("guncelleme_tarihi", ""))
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Kaydetme Hatası",
                                 f"Dosya yazılamadı:\n{exc}")
            return False

    # ── Son projeler ─────────────────────────────────────────────────────────

    def _add_to_recent(self, path: str) -> None:
        recent: List[str] = self._settings.value(_SETTINGS_KEY, []) or []
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        recent = recent[:_MAX_RECENT]
        self._settings.setValue(_SETTINGS_KEY, recent)
        self._load_recent_list()

    def _load_recent_list(self) -> None:
        self._recent_list.clear()
        recent: List[str] = self._settings.value(_SETTINGS_KEY, []) or []
        for path in recent:
            name = Path(path).stem
            item = QListWidgetItem(f"📄 {name}")
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            exists = os.path.exists(path)
            if not exists:
                item.setForeground(Qt.gray)
                item.setText(f"✗ {name}")
            self._recent_list.addItem(item)

    def _on_recent_open(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if not path:
            return
        if not os.path.exists(path):
            QMessageBox.warning(self, "Dosya Bulunamadı",
                                f"Dosya mevcut değil:\n{path}")
            return
        if not self._ask_save_if_dirty():
            return
        self._load_from_file(path)

    def _clear_recent(self) -> None:
        self._settings.remove(_SETTINGS_KEY)
        self._load_recent_list()

    # ── Panellere uygula ─────────────────────────────────────────────────────

    def _on_apply_to_panels(self) -> None:
        self._proje = self._read_form()
        self._degistirildi = False
        self._dirty_grace_until = time.monotonic() + self._DIRTY_GRACE_S
        self._update_status()
        self._update_summary()

        self.projeYuklendi.emit(self._proje)

        mat_key = self._proje.get("malzeme", "")
        if mat_key:
            self.malzemeSecildi.emit(mat_key)

        QMessageBox.information(
            self, "Proje Uygulandı",
            f"Proje '{self._proje['proje_adi']}' tüm panellere uygulandı.",
        )

    # ── Sürüm Geçmişi (Faz 25 Sprint 3) ─────────────────────────────────────

    def set_autosave_manager(self, mgr) -> None:
        """MainWindow'un AutoSaveManager'ını kur ve listeyi doldur."""
        self._autosave_mgr = mgr
        if mgr is not None:
            mgr.autosaved.connect(lambda _p: self.refresh_history())
        self.refresh_history()

    def refresh_history(self) -> None:
        """Otomatik kayıt sürüm listesini diskten tazele."""
        self._history_list.clear()
        if self._autosave_mgr is None:
            self._history_hint.setText(
                "Otomatik kayıt etkin değil — sürüm listesi boş.")
            return
        versions = self._autosave_mgr.list_versions()
        for ver in versions:
            item = QListWidgetItem(ver.label)
            item.setData(Qt.UserRole, ver.path)
            item.setToolTip(ver.path)
            self._history_list.addItem(item)
        self._history_hint.setText(
            f"{len(versions)} sürüm — her 30 sn'de bir otomatik kayıt "
            f"(son 5 sürüm saklanır).")

    def _on_revert_to_version(self) -> None:
        """Seçili otomatik kayıt sürümüne geri dön."""
        if self._autosave_mgr is None:
            return
        item = self._history_list.currentItem()
        if item is None:
            QMessageBox.information(
                self, "Sürüm Geçmişi", "Önce listeden bir sürüm seçin.")
            return
        path = item.data(Qt.UserRole)
        reply = QMessageBox.question(
            self, "Sürüme Dön",
            f"Seçili sürüme dönülecek:\n{item.text()}\n\n"
            "Mevcut durum önce güvenlik yedeği olarak kaydedilir.\n"
            "Devam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        # Yanlış geri dönüş de geri alınabilsin: önce mevcut durumu yedekle
        self._autosave_mgr.force_save()
        data = self._autosave_mgr.load_version(path)
        if data is None:
            QMessageBox.critical(
                self, "Sürüm Geçmişi", "Sürüm dosyası okunamadı.")
            return
        self.load_project_dict(data, dosya_yolu=self._dosya_yolu)
        self._mark_dirty()   # geri dönülen durum diskteki .fwp ile farklı
        self.refresh_history()

    # ── Harici API ───────────────────────────────────────────────────────────

    def get_proje(self) -> Dict[str, Any]:
        return dict(self._proje)

    def apply_report(self, report) -> None:
        """Mühendislik analizi raporundan katman listesini güncelle."""
        if report is None:
            return
        sch = report.layer_schedule
        t = sch.ply_thickness_mm
        katmanlar = []
        for _ in range(sch.n_helical_pairs):
            katmanlar.append({
                "tip": "sarmal",
                "aci_deg": sch.alpha_deg,
                "cift_sayisi": 1,
                "ply_kalinlik_mm": t,
            })
        for _ in range(sch.n_hoop):
            katmanlar.append({
                "tip": "cevre",
                "aci_deg": 90.0,
                "cift_sayisi": 1,
                "ply_kalinlik_mm": t,
            })
        self._proje["katmanlar"] = katmanlar
        self._mark_dirty()
        self._update_summary()
