"""
panels/katman_dizilim_paneli.py — Manuel Katman Dizilim Tasarımcısı (Faz 24 ML-2)
==================================================================================

`core.manual_layer_sequencer` motorunu ön yüze çıkaran TaniqWind + WindingGuru
tarzı manuel katman dizilim arayüzü.

Yerleşim (3 sütun):
  Sol Şerit (Kontrol)   |  Orta (Katman Tablosu)  |  Sağ (Anlık Mühendislik)

İş akışı:
  1. Kullanıcı sol şeritteki butonlarla katman ekler ([+ Helisel], [+ Hoop], …)
  2. Helisel +α eklendiğinde dengeleyici −α katmanı otomatik öneri pop-up
  3. Tablo hücreleri çift tıkla düzenle → 200 ms debounce → arkaplanda
     CLT/Burst yeniden hesabı (QThread)
  4. Sağ panelde P_burst, SF, MoS, kütle, kalınlık anlık güncellenir
  5. Tablo satırına tıklayınca 3D panelde o katman vurgulanır
  6. Kayma şartını ihlal eden satır kırmızıya boyanır + alarms paneline log
"""
from __future__ import annotations

import math
import sys
import os
from typing import List, Optional, Dict, Any

from PySide6.QtCore import (
    Qt, QTimer, QThread, QObject, Signal, Slot,
)
from PySide6.QtGui import QColor, QBrush, QFont
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QFormLayout,
    QGroupBox, QLabel, QPushButton, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QComboBox, QMessageBox, QDoubleSpinBox, QSpinBox,
    QSizePolicy, QStyledItemDelegate, QStyleOptionViewItem,
)

from ..themes.dark_industrial import COLOR


# ──────────────────────────────────────────────────────────────────────────────
# Backend yükleyici (tek noktadan)
# ──────────────────────────────────────────────────────────────────────────────

def _import_backend():
    """LayerStack motoru + yardımcılar; başarısız olursa hepsi None."""
    try:
        from backend.core.manual_layer_sequencer import (
            LayerType, LayerSpec, LayerStack, LayerValidation, PitchInfo,
        )
        from backend.core.geometry_engine import MandrelProfile
        return (LayerType, LayerSpec, LayerStack,
                LayerValidation, PitchInfo, MandrelProfile)
    except Exception:
        return (None,) * 6


(LayerType, LayerSpec, LayerStack,
 LayerValidation, PitchInfo, MandrelProfile) = _import_backend()


# ──────────────────────────────────────────────────────────────────────────────
# Stil yardımcıları
# ──────────────────────────────────────────────────────────────────────────────

_BTN_PRIMARY = (
    "QPushButton {{"
    "  background: {bg};"
    "  color: {fg};"
    "  padding: 8px 10px;"
    "  border: 1px solid {bd};"
    "  border-radius: 3px;"
    "  font-weight: bold;"
    "  text-align: left;"
    "}}"
    "QPushButton:hover {{ background: {hv}; }}"
    "QPushButton:pressed {{ background: {pr}; }}"
    "QPushButton:disabled {{ color: {dt}; background: {bgw}; }}"
).format(
    bg=COLOR["bg_widget"], fg=COLOR["text_primary"], bd=COLOR["border"],
    hv=COLOR["bg_hover"], pr=COLOR["bg_selected"],
    dt=COLOR["text_disabled"], bgw=COLOR["bg_panel"],
)

_BTN_SECONDARY = (
    "QPushButton {{"
    "  background: {bg};"
    "  color: {fg};"
    "  padding: 6px 8px;"
    "  border: 1px solid {bd};"
    "  border-radius: 3px;"
    "}}"
    "QPushButton:hover {{ background: {hv}; }}"
).format(
    bg=COLOR["bg_panel"], fg=COLOR["text_secondary"], bd=COLOR["border"],
    hv=COLOR["bg_hover"],
)

_BTN_DANGER = (
    "QPushButton {{"
    "  background: {bg};"
    "  color: {fg};"
    "  padding: 6px 8px;"
    "  border: 1px solid {bd};"
    "  border-radius: 3px;"
    "}}"
    "QPushButton:hover {{ background: {hv}; }}"
).format(
    bg="#3a1f1f", fg=COLOR["crit"], bd="#5a2a2a", hv="#4a2a2a",
)

_GRP_STYLE = (
    "QGroupBox {{"
    "  color: {fg};"
    "  background: {bg};"
    "  border: 1px solid {bd};"
    "  border-radius: 4px;"
    "  margin-top: 10px;"
    "  font-weight: bold;"
    "}}"
    "QGroupBox::title {{"
    "  subcontrol-origin: margin;"
    "  left: 8px; top: 0px;"
    "  padding: 0 6px;"
    "  color: {ac};"
    "}}"
).format(
    fg=COLOR["text_primary"], bg=COLOR["bg_panel"], bd=COLOR["border"],
    ac=COLOR["accent_bright"],
)

_TABLE_STYLE = (
    "QTableWidget {{"
    "  background: {bg};"
    "  color: {fg};"
    "  gridline-color: {bd};"
    "  selection-background-color: {sel};"
    "  alternate-background-color: {alt};"
    "}}"
    "QHeaderView::section {{"
    "  background: {hdr};"
    "  color: {ac};"
    "  padding: 6px;"
    "  border: none;"
    "  border-right: 1px solid {bd};"
    "  font-weight: bold;"
    "}}"
).format(
    bg=COLOR["bg_widget"], fg=COLOR["text_primary"], bd=COLOR["border"],
    sel=COLOR["bg_selected"], alt=COLOR["bg_panel"],
    hdr=COLOR["bg_window"], ac=COLOR["accent_bright"],
)

_VALUE_STYLE = (
    "color: {fg}; font-family: 'Consolas','Courier New',monospace; "
    "font-size: 13px; padding: 2px;"
).format(fg=COLOR["text_primary"])

_BTN_SEND = (
    "QPushButton {{"
    "  background: #1a3a5a;"
    "  color: {fg};"
    "  padding: 8px 10px;"
    "  border: 1px solid #2a5a8a;"
    "  border-radius: 3px;"
    "  font-weight: bold;"
    "}}"
    "QPushButton:hover {{ background: #2a4a6a; }}"
    "QPushButton:pressed {{ background: #0a2a4a; }}"
    "QPushButton:disabled {{ color: {dt}; background: {bgw}; }}"
).format(
    fg=COLOR["accent_bright"],
    dt=COLOR["text_disabled"],
    bgw=COLOR["bg_panel"],
)


# Tablo sütun indeksleri (her yerde kullanılır)
COL_ID         = 0
COL_TYPE       = 1
COL_ALPHA      = 2
COL_WIDTH      = 3
COL_OVERLAP    = 4
COL_STRATEGY   = 5
COL_FEED       = 6
COL_RPM        = 7
COL_FRICTION   = 8
COL_STATUS     = 9
N_COLS         = 10


# ──────────────────────────────────────────────────────────────────────────────
# LED simülasyonu (sadece çizim)
# ──────────────────────────────────────────────────────────────────────────────

class _LEDIndicator(QFrame):
    """Yuvarlak LED — yeşil/sarı/kırmızı durum simülasyonu."""

    _COLORS = {
        "green":  ("#5cb85c", "#1a3a1a"),
        "yellow": ("#f0ad4e", "#3a2e15"),
        "red":    ("#d9534f", "#3a1a1a"),
        "gray":   ("#5a6068", "#2a2e35"),
    }

    def __init__(self, color: str = "gray", parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(16, 16)
        self.setFrameShape(QFrame.NoFrame)
        self._apply()

    def setColor(self, color: str) -> None:
        if color not in self._COLORS:
            color = "gray"
        if color != self._color:
            self._color = color
            self._apply()

    def _apply(self) -> None:
        fg, bg = self._COLORS[self._color]
        self.setStyleSheet(
            f"QFrame {{"
            f"  background: {fg};"
            f"  border: 2px solid {bg};"
            f"  border-radius: 8px;"
            f"}}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Arkaplan analiz işçisi (CLT/Burst 200 ms debounce sonrası)
# ──────────────────────────────────────────────────────────────────────────────

class _AnalysisWorker(QObject):
    """
    LayerStack snapshot'ından CLT/burst tahmini üretir.

    UI'ı bloklamamak için QThread içinde çalışır.
    """
    finished = Signal(dict)
    error    = Signal(str)

    def __init__(self,
                 stack_dict: Dict[str, Any],
                 mandrel_diameter_mm: float,
                 mandrel_length_mm: float,
                 mandrel_radius_mm: float,
                 P_operating_MPa: float,
                 material_key: str,
                 safety_code: str):
        super().__init__()
        self._stack_dict   = stack_dict
        self._D = mandrel_diameter_mm
        self._L = mandrel_length_mm
        self._R = mandrel_radius_mm
        self._P = P_operating_MPa
        self._mat = material_key
        self._code = safety_code

    @Slot()
    def run(self):
        """Hesaplamayı çalıştır ve sonucu emit et."""
        try:
            from backend.core.manual_layer_sequencer import LayerStack as _LS
            from backend.core.geometry_engine import MandrelProfile
            from backend.core.material_allowables import get_engineering_material

            stack = _LS.from_dict(self._stack_dict)
            mat   = get_engineering_material(self._mat)

            # Mandrel profili (manuel build-up için)
            profile = MandrelProfile.cylinder(
                length_mm=max(self._L, 1.0),
                radius_mm=max(self._R, 1.0),
                n_points=100,
            )

            # Doğrulama: her katmanın kayma kontrolü
            validations = []
            for i in range(len(stack)):
                try:
                    val = stack.validate_layer_slippage(i, profile)
                    validations.append({
                        "passes":         bool(val.passes),
                        "max_slip_ratio": float(val.max_slip_ratio),
                        "warning_msg":    str(val.warning_msg),
                        "status_color":   val.status_color,
                    })
                except Exception as exc:
                    validations.append({
                        "passes": False, "max_slip_ratio": float("nan"),
                        "warning_msg": f"Doğrulama hatası: {exc}",
                        "status_color": "red",
                    })

            # CLT tabanlı burst tahmini (yığın boş değilse)
            burst_MPa = 0.0
            SF = 0.0
            MoS = -1.0
            total_t = stack.total_thickness_mm()
            mass_kg = 0.0
            sigma_fiber_mean_MPa = mat.lamina.X_t_MPa
            t_hel = stack.total_helical_thickness_mm()
            t_hoop = stack.total_hoop_thickness_mm()

            if t_hel > 1e-6 or t_hoop > 1e-6:
                try:
                    # Netting analizi temel burst tahmini (hızlı, CLT'siz)
                    from backend.core.netting_analysis import combined_hoop_helical
                    # Temsili α: ilk helisel katmandan, yoksa 90°
                    rep_alpha = 90.0
                    for L in stack:
                        if L.type.value == "helical":
                            rep_alpha = abs(L.alpha_deg)
                            break
                    # Sigma_fiber B-basis (yüzey gerilimi)
                    sigma_design = mat.design_X_t(basis="B")
                    # Hoop+helisel netting çift basınç sınırı
                    if t_hel > 0 and t_hoop >= 0:
                        # T = burst basıncı eşdeğeri (Vasiliev 5.45)
                        # P_axial = 4σ·t_hel·cos²α / D
                        # P_hoop  = 2σ·(t_hel·sin²α + t_hoop) / D
                        sin2 = math.sin(math.radians(rep_alpha))**2
                        cos2 = math.cos(math.radians(rep_alpha))**2
                        P_ax  = 4.0 * sigma_design * t_hel * cos2 / self._D
                        P_hp  = 2.0 * sigma_design * (t_hel * sin2 + t_hoop) / self._D
                        burst_MPa = min(P_ax, P_hp)
                except Exception:
                    burst_MPa = 0.0

                # Güvenlik kodu eşleştirme
                try:
                    from backend.core.safety_factor import (
                        SafetyCode, assess_safety,
                    )
                    code = SafetyCode(self._code)
                    sa = assess_safety(self._P, burst_MPa, code)
                    SF  = float(sa.SF_actual)
                    MoS = float(sa.margin_of_safety)
                except Exception:
                    SF = burst_MPa / max(self._P, 1e-6)
                    MoS = SF / 2.25 - 1.0

                # Kütle tahmini: 2π·R·L·t·ρ (silindirik kabuk)
                rho_g_cm3 = mat.lamina.ply_density_g_cm3
                vol_cm3   = (2.0 * math.pi * self._R * self._L * total_t) / 1000.0
                mass_kg   = vol_cm3 * rho_g_cm3 / 1000.0

            result = {
                "validations":   validations,
                "burst_MPa":     burst_MPa,
                "SF":            SF,
                "MoS":           MoS,
                "mass_kg":       mass_kg,
                "total_t_mm":    total_t,
                "n_layers":      len(stack),
                "P_target_MPa":  self._P,
            }
            self.finished.emit(result)

        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# Ana panel
# ──────────────────────────────────────────────────────────────────────────────

class KatmanDizilimPaneli(QWidget):
    """
    Manuel Katman Dizilim Paneli.

    Sol şerit: katman ekleme/sıralama/silme butonları
    Orta:      katman tablosu (10 sütun, in-place edit, LED durum)
    Sağ:       anlık mühendislik çıktıları (P_burst, SF, MoS, kütle, kalınlık)
    """

    # ── Public sinyaller (mimari sözleşme) ──────────────────────────────────
    katmanDegisti  = Signal(object)   # LayerStack
    katmanSecildi  = Signal(int)      # satır indeksi
    kaymaUyarisi   = Signal(int, float)  # (layer_idx, slip_ratio)
    uretimeGonder  = Signal(dict)     # Manuel dizilim → üretim / CAM

    # ── Sabitler ─────────────────────────────────────────────────────────────
    _DEBOUNCE_MS = 200

    _LAYER_TYPE_LABELS: Dict[str, str] = {
        "helical": "Sarmal Helisel",
        "hoop":    "Çevre (Hoop)",
        "polar":   "Kutupsal",
        "skin":    "Bitiş Sarımı",
    }

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        # Backend hazır mı?
        self._backend_ok = LayerStack is not None
        self._stack = LayerStack() if self._backend_ok else None

        # Mandrel parametreleri (proje yöneticisinden gelir; varsayılan değerler)
        self._mandrel_diameter_mm = 200.0
        self._mandrel_length_mm   = 500.0
        self._mandrel_radius_mm   = 100.0
        self._P_operating_MPa     = 10.0
        self._material_key        = "carbon_t700_epoxy_pv"
        self._safety_code         = "iso_11119_2"

        # Hesap işçisi durumu
        self._thread: Optional[QThread] = None
        self._worker: Optional[_AnalysisWorker] = None
        self._pending_recalc = False
        self._last_validations: List[Dict[str, Any]] = []

        # Backend yokken saf-UI katman listesi
        self._ui_layers: List[Dict[str, Any]] = []

        # Tablo programatik güncelleme sırasında sinyal çakışmasını engelle
        self._suppress_cell_signal = False

        # Debounce timer (tablo edit → analiz)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(self._DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._recalculate)

        # Otomatik reçete köprüsü: tablo değişince (debounce sonrası) yığını
        # Üretim Tasarım Merkezi + CAM motoruna sessizce fırlat.
        self._autosend_timer = QTimer(self)
        self._autosend_timer.setSingleShot(True)
        self._autosend_timer.setInterval(self._DEBOUNCE_MS)
        self._autosend_timer.timeout.connect(self._emit_uretime_auto)

        self._build_ui()
        self._refresh_table()
        self._update_stats_blank()

    # ════════════════════════════════════════════════════════════════════════
    # UI inşası
    # ════════════════════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── Sol: kontrol şeridi ─────────────────────────────────────────────
        left = self._build_left_panel()
        left.setFixedWidth(200)
        root.addWidget(left)

        # ── Orta: tablo + başlık ────────────────────────────────────────────
        center = self._build_center_panel()
        root.addWidget(center, stretch=1)

        # ── Sağ: anlık çıktı ────────────────────────────────────────────────
        right = self._build_right_panel()
        right.setFixedWidth(290)
        root.addWidget(right)

    # ── Sol panel ────────────────────────────────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        title = QLabel("Katman Ekle")
        title.setStyleSheet(
            f"color: {COLOR['accent_bright']}; font-weight: bold; "
            f"font-size: 13px; padding: 4px 2px;"
        )
        v.addWidget(title)

        # Ekleme butonları — her zaman aktif (backend yokken UI-only satır ekler)
        for label, slot, tooltip in [
            ("+ Helisel Ekle",      self._on_add_helical,
             "Sarmal katman (±α). Genelde 45-65° aralığında."),
            ("+ Çember/Hoop Ekle",  self._on_add_hoop,
             "Çember katmanı (α ≈ 90°). Silindir gövde mukavemeti."),
            ("+ Polar Ekle",        self._on_add_polar,
             "Kutupsal katman (α ≈ 10-15°). Kubbe geçişi."),
            ("+ Bitiş Sarımı",      self._on_add_skin,
             "Bitiş/kabuk koruyucu katman."),
        ]:
            btn = QPushButton(label)
            btn.setToolTip(tooltip)
            btn.setStyleSheet(_BTN_PRIMARY)
            btn.clicked.connect(slot)
            v.addWidget(btn)

        # Ayraç
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {COLOR['border']};")
        v.addSpacing(6)
        v.addWidget(sep)
        v.addSpacing(6)

        sub = QLabel("Sıralama / Silme")
        sub.setStyleSheet(
            f"color: {COLOR['text_secondary']}; font-weight: bold; "
            f"font-size: 11px; padding: 2px;"
        )
        v.addWidget(sub)

        # Sıra/sil butonları
        for label, slot, tooltip in [
            ("↑ Yukarı",  self._on_move_up,   "Seçili katmanı bir üst sıraya taşı"),
            ("↓ Aşağı",   self._on_move_down, "Seçili katmanı bir alt sıraya taşı"),
        ]:
            btn = QPushButton(label)
            btn.setToolTip(tooltip)
            btn.setStyleSheet(_BTN_SECONDARY)
            btn.clicked.connect(slot)
            v.addWidget(btn)

        btn_del = QPushButton("✕ Sil")
        btn_del.setToolTip("Seçili katmanı sil")
        btn_del.setStyleSheet(_BTN_DANGER)
        btn_del.clicked.connect(self._on_delete)
        v.addWidget(btn_del)

        v.addSpacing(8)

        btn_clear = QPushButton("⌫ Tümünü Temizle")
        btn_clear.setToolTip("Tüm katmanları sil")
        btn_clear.setStyleSheet(_BTN_DANGER)
        btn_clear.clicked.connect(self._on_clear_all)
        v.addWidget(btn_clear)

        v.addStretch()

        # Üretim merkezine gönder
        btn_gonder = QPushButton("→ Üretime Gönder")
        btn_gonder.setToolTip(
            "Mevcut katman yığınını Üretim Tasarım Merkezi ve CAM Üretici'ye gönder"
        )
        btn_gonder.setStyleSheet(_BTN_SEND)
        btn_gonder.clicked.connect(self._on_gonder_uretim)
        v.addWidget(btn_gonder)

        # Mandrel özet kutusu (alt bilgi)
        grp = QGroupBox("Mandrel")
        grp.setStyleSheet(_GRP_STYLE)
        gv = QFormLayout(grp)
        gv.setLabelAlignment(Qt.AlignRight)
        gv.setContentsMargins(8, 14, 8, 8)

        self._lbl_mandrel_d = QLabel("200.0 mm")
        self._lbl_mandrel_d.setStyleSheet(_VALUE_STYLE)
        gv.addRow("Çap:", self._lbl_mandrel_d)

        self._lbl_mandrel_l = QLabel("500.0 mm")
        self._lbl_mandrel_l.setStyleSheet(_VALUE_STYLE)
        gv.addRow("Uzunluk:", self._lbl_mandrel_l)

        self._lbl_mandrel_p = QLabel("10.0 MPa")
        self._lbl_mandrel_p.setStyleSheet(_VALUE_STYLE)
        gv.addRow("P_op:", self._lbl_mandrel_p)

        v.addWidget(grp)

        if not self._backend_ok:
            err = QLabel("⚠ Backend yüklenemedi")
            err.setStyleSheet(f"color: {COLOR['crit']}; padding: 4px;")
            v.addWidget(err)

        return w

    # ── Orta panel: başlık + tablo ──────────────────────────────────────────

    def _build_center_panel(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        # Başlık
        hdr = QHBoxLayout()
        title = QLabel("Manuel Katman Dizilim Tablosu")
        title.setStyleSheet(
            f"color: {COLOR['text_primary']}; font-weight: bold; "
            f"font-size: 14px; padding: 2px;"
        )
        hdr.addWidget(title)
        hdr.addStretch()

        self._lbl_layer_count = QLabel("0 katman")
        self._lbl_layer_count.setStyleSheet(
            f"color: {COLOR['text_secondary']}; padding: 2px 8px;"
        )
        hdr.addWidget(self._lbl_layer_count)
        v.addLayout(hdr)

        # Tablo
        self._table = QTableWidget(0, N_COLS)
        self._table.setHorizontalHeaderLabels([
            "#",
            "Tip",
            "Açı α (°)",
            "Fitil (mm)",
            "Çakışma %",
            "Strateji",
            "Hız (mm/s)",
            "RPM",
            "Sürtünme μ",
            "Durum",
        ])
        self._table.setStyleSheet(_TABLE_STYLE)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)

        # Sütun genişlikleri
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        self._table.setColumnWidth(COL_ID,        36)
        self._table.setColumnWidth(COL_TYPE,      130)
        self._table.setColumnWidth(COL_ALPHA,     78)
        self._table.setColumnWidth(COL_WIDTH,     82)
        self._table.setColumnWidth(COL_OVERLAP,   82)
        self._table.setColumnWidth(COL_STRATEGY,  120)
        self._table.setColumnWidth(COL_FEED,      88)
        self._table.setColumnWidth(COL_RPM,       70)
        self._table.setColumnWidth(COL_FRICTION,  86)
        self._table.setColumnWidth(COL_STATUS,    100)

        # Sinyaller
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        v.addWidget(self._table, stretch=1)

        # Alt durum çubuğu
        self._status_label = QLabel("Hazır")
        self._status_label.setStyleSheet(
            f"color: {COLOR['text_secondary']}; padding: 4px;"
        )
        v.addWidget(self._status_label)

        return w

    # ── Sağ panel: anlık mühendislik çıktıları ──────────────────────────────

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        # Mühendislik kutusu
        grp = QGroupBox("Anlık Mühendislik Çıktısı")
        grp.setStyleSheet(_GRP_STYLE)
        f = QFormLayout(grp)
        f.setLabelAlignment(Qt.AlignRight)
        f.setContentsMargins(10, 18, 10, 10)
        f.setSpacing(8)

        self._lbl_burst = QLabel("—")
        self._lbl_burst.setStyleSheet(
            f"color: {COLOR['accent_bright']}; "
            f"font-family: monospace; font-size: 14px; font-weight: bold;"
        )
        f.addRow("Tahmini Burst (P):", self._lbl_burst)

        self._lbl_sf = QLabel("—")
        self._lbl_sf.setStyleSheet(_VALUE_STYLE)
        f.addRow("Güvenlik Faktörü (SF):", self._lbl_sf)

        self._lbl_mos = QLabel("—")
        self._lbl_mos.setStyleSheet(_VALUE_STYLE)
        f.addRow("Güvenlik Marjı (MoS):", self._lbl_mos)

        self._lbl_mass = QLabel("—")
        self._lbl_mass.setStyleSheet(_VALUE_STYLE)
        f.addRow("Toplam Kütle:", self._lbl_mass)

        self._lbl_thickness = QLabel("—")
        self._lbl_thickness.setStyleSheet(_VALUE_STYLE)
        f.addRow("Toplam Kalınlık:", self._lbl_thickness)

        # Çalışma basıncı (sadece referans)
        self._lbl_p_target = QLabel("—")
        self._lbl_p_target.setStyleSheet(
            f"color: {COLOR['text_secondary']}; "
            f"font-family: monospace; font-size: 12px;"
        )
        f.addRow("Çalışma Basıncı:", self._lbl_p_target)

        v.addWidget(grp)

        # Durum kutusu (büyük UYGUN/YETERSİZ etiketi)
        self._verdict_lbl = QLabel("Hesap bekleniyor")
        self._verdict_lbl.setAlignment(Qt.AlignCenter)
        self._verdict_lbl.setStyleSheet(
            f"color: {COLOR['text_secondary']}; "
            f"background: {COLOR['bg_widget']}; "
            f"border: 1px solid {COLOR['border']}; "
            f"border-radius: 4px; padding: 12px; "
            f"font-size: 13px; font-weight: bold;"
        )
        v.addWidget(self._verdict_lbl)

        # Uyarılar
        grp_warn = QGroupBox("Uyarılar")
        grp_warn.setStyleSheet(_GRP_STYLE)
        wv = QVBoxLayout(grp_warn)
        wv.setContentsMargins(10, 18, 10, 10)

        self._warn_label = QLabel("Uyarı yok.")
        self._warn_label.setStyleSheet(
            f"color: {COLOR['text_secondary']}; padding: 4px;"
        )
        self._warn_label.setWordWrap(True)
        self._warn_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._warn_label.setMinimumHeight(80)
        wv.addWidget(self._warn_label)

        v.addWidget(grp_warn, stretch=1)

        return w

    # ════════════════════════════════════════════════════════════════════════
    # Tablo render
    # ════════════════════════════════════════════════════════════════════════

    def _refresh_table(self) -> None:
        """LayerStack durumunu tabloya yansıt."""
        if not self._backend_ok:
            return

        self._suppress_cell_signal = True
        self._table.setRowCount(0)

        for i, L in enumerate(self._stack):
            self._table.insertRow(i)
            self._populate_row(i, L)

        self._suppress_cell_signal = False
        self._lbl_layer_count.setText(f"{len(self._stack)} katman")

    def _populate_row(self, row: int, L) -> None:
        """Tek satırı doldur (LayerSpec'ten)."""
        # # (ID, salt okunur)
        id_item = QTableWidgetItem(str(L.id))
        id_item.setTextAlignment(Qt.AlignCenter)
        id_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        id_item.setForeground(QBrush(QColor(COLOR["text_secondary"])))
        self._table.setItem(row, COL_ID, id_item)

        # Tip (combo)
        type_combo = QComboBox()
        type_combo.setStyleSheet(
            f"QComboBox {{ background: {COLOR['bg_widget']}; "
            f"color: {COLOR['text_primary']}; border: 1px solid {COLOR['border']}; }}"
        )
        for lt in LayerType:
            type_combo.addItem(lt.display_tr, lt.value)
        idx = type_combo.findData(L.type.value)
        if idx >= 0:
            type_combo.setCurrentIndex(idx)
        type_combo.currentIndexChanged.connect(
            lambda _i, r=row: self._on_type_changed(r)
        )
        self._table.setCellWidget(row, COL_TYPE, type_combo)

        # Açı, fitil, çakışma, hız, rpm, sürtünme — düzenlenebilir
        for col, val, fmt in [
            (COL_ALPHA,    L.alpha_deg,           "{:+.1f}"),
            (COL_WIDTH,    L.fitil_genisligi_mm,  "{:.2f}"),
            (COL_OVERLAP,  L.cakisma_pct,         "{:.1f}"),
            (COL_FEED,     L.feed_mm_s,           "{:.1f}"),
            (COL_RPM,      L.spindle_rpm,         "{:.1f}"),
            (COL_FRICTION, L.friction_mu,         "{:.2f}"),
        ]:
            it = QTableWidgetItem(fmt.format(val))
            it.setTextAlignment(Qt.AlignCenter)
            it.setForeground(QBrush(QColor(COLOR["text_primary"])))
            self._table.setItem(row, col, it)

        # Strateji (combo)
        strat_combo = QComboBox()
        strat_combo.setStyleSheet(
            f"QComboBox {{ background: {COLOR['bg_widget']}; "
            f"color: {COLOR['text_primary']}; border: 1px solid {COLOR['border']}; }}"
        )
        strat_combo.addItem("Jeodezik",        "geodesic")
        strat_combo.addItem("Non-Jeodezik",    "non_geodesic")
        sidx = strat_combo.findData(L.strategy)
        if sidx >= 0:
            strat_combo.setCurrentIndex(sidx)
        strat_combo.currentIndexChanged.connect(
            lambda _i, r=row: self._on_strategy_changed(r)
        )
        self._table.setCellWidget(row, COL_STRATEGY, strat_combo)

        # Durum: LED + metin
        self._set_status_widget(row, "gray", "—")

    def _set_status_widget(self, row: int, color: str, text: str) -> None:
        """Durum hücresine LED + kısa metin yerleştir."""
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(6)
        led = _LEDIndicator(color)
        lay.addWidget(led)
        text_lbl = QLabel(text)
        text_lbl.setStyleSheet(f"color: {COLOR['text_primary']}; font-size: 11px;")
        lay.addWidget(text_lbl)
        lay.addStretch()
        self._table.setCellWidget(row, COL_STATUS, cell)

    def _apply_row_color(self, row: int, color: str) -> None:
        """Tüm satırın arkaplan rengini kayma uyarısına göre boya."""
        color_map = {
            "green":  QColor(COLOR["bg_widget"]),
            "yellow": QColor("#3a2e15"),
            "red":    QColor("#3a1a1a"),
            "gray":   QColor(COLOR["bg_widget"]),
        }
        bg = color_map.get(color, QColor(COLOR["bg_widget"]))
        for col in range(N_COLS):
            it = self._table.item(row, col)
            if it is not None:
                it.setBackground(QBrush(bg))

    # ════════════════════════════════════════════════════════════════════════
    # Buton handler'ları
    # ════════════════════════════════════════════════════════════════════════

    def _on_add_helical(self) -> None:
        if not self._backend_ok:
            self._insert_raw_row("helical", alpha_deg=45.0)
            return
        spec = self._stack.make_helical(
            alpha_deg=45.0, fitil_genisligi_mm=6.0, cakisma_pct=5.0,
            thickness_mm=0.30, feed_mm_s=80.0, spindle_rpm=60.0,
            strategy="geodesic",
        )
        self._stack.add_layer(spec)

        # Dengeleyici çift önerisi
        reply = QMessageBox.question(
            self, "Denge Çifti",
            f"+{spec.alpha_deg:.1f}° helisel katman eklendi.\n\n"
            f"Burulma gerilmelerini sönümlemek için dengeleyici "
            f"−{spec.alpha_deg:.1f}° katmanı otomatik olarak eklensin mi?\n\n"
            f"(Dengeli laminat kuralı: her +α bir −α eşi gerektirir.)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            mate = self._stack.auto_suggest_anti_symmetric_pair(spec)
            if mate is not None:
                self._stack.add_layer(mate)

        self._refresh_table()
        self._schedule_recalc()
        self._emit_stack_changed()
        self._set_status(f"Helisel katman eklendi: {spec.label}")

    def _on_add_hoop(self) -> None:
        if not self._backend_ok:
            self._insert_raw_row("hoop", alpha_deg=89.5, feed=60.0)
            return
        spec = self._stack.make_hoop()
        self._stack.add_layer(spec)
        self._refresh_table()
        self._schedule_recalc()
        self._emit_stack_changed()
        self._set_status(f"Hoop katman eklendi: {spec.label}")

    def _on_add_polar(self) -> None:
        if not self._backend_ok:
            self._insert_raw_row("polar", alpha_deg=12.0)
            return
        spec = self._stack.make_polar()
        self._stack.add_layer(spec)
        self._refresh_table()
        self._schedule_recalc()
        self._emit_stack_changed()
        self._set_status(f"Polar katman eklendi: {spec.label}")

    def _on_add_skin(self) -> None:
        if not self._backend_ok:
            self._insert_raw_row("skin", alpha_deg=89.5, feed=60.0)
            return
        spec = self._stack.make_skin()
        self._stack.add_layer(spec)
        self._refresh_table()
        self._schedule_recalc()
        self._emit_stack_changed()
        self._set_status(f"Bitiş katmanı eklendi: {spec.label}")

    def _on_move_up(self) -> None:
        if not self._backend_ok:
            self._set_status("Yeniden sıralama için backend gerekli.")
            return
        row = self._table.currentRow()
        if row <= 0:
            return
        self._stack.move_layer(row, row - 1)
        self._refresh_table()
        self._table.selectRow(row - 1)
        self._schedule_recalc()
        self._emit_stack_changed()

    def _on_move_down(self) -> None:
        if not self._backend_ok:
            self._set_status("Yeniden sıralama için backend gerekli.")
            return
        row = self._table.currentRow()
        if row < 0 or row >= len(self._stack) - 1:
            return
        self._stack.move_layer(row, row + 1)
        self._refresh_table()
        self._table.selectRow(row + 1)
        self._schedule_recalc()
        self._emit_stack_changed()

    def _on_delete(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        if not self._backend_ok:
            reply = QMessageBox.question(
                self, "Katman Sil",
                f"Satır {row + 1} silinsin mi?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                if row < len(self._ui_layers):
                    self._ui_layers.pop(row)
                self._table.removeRow(row)
                self._lbl_layer_count.setText(f"{self._table.rowCount()} katman")
                self._set_status(f"Satır {row + 1} silindi.")
                self._schedule_autosend()
            return
        if row >= len(self._stack):
            return
        L = self._stack[row]
        reply = QMessageBox.question(
            self, "Katman Sil",
            f"'{L.label}' katmanı silinsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._stack.remove_layer(row)
            self._refresh_table()
            self._schedule_recalc()
            self._emit_stack_changed()
            self._set_status(f"Katman silindi: {L.label}")

    def _on_clear_all(self) -> None:
        if not self._backend_ok:
            if self._table.rowCount() == 0:
                return
            reply = QMessageBox.question(
                self, "Tümünü Temizle",
                f"Tablodaki {self._table.rowCount()} satırın tamamı silinsin mi?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._ui_layers.clear()
                self._table.setRowCount(0)
                self._lbl_layer_count.setText("0 katman")
                self._set_status("Tablo temizlendi.")
                self._schedule_autosend()
            return
        if len(self._stack) == 0:
            return
        reply = QMessageBox.question(
            self, "Tümünü Temizle",
            f"Yığındaki {len(self._stack)} katmanın tamamı silinsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._stack.clear()
            self._refresh_table()
            self._update_stats_blank()
            self._emit_stack_changed()
            self._set_status("Yığın temizlendi.")

    # ════════════════════════════════════════════════════════════════════════
    # Tablo edit handler'ları
    # ════════════════════════════════════════════════════════════════════════

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Tablo hücresi düzenlendiğinde backend LayerSpec'i güncelle."""
        if self._suppress_cell_signal:
            return

        if not self._backend_ok:
            # Backend yok: hücre değeri tabloda zaten duruyor; sadece
            # otomatik köprüyü tetikle (kullanıcı değeri elle düzenledi).
            self._schedule_autosend()
            return

        row = item.row()
        col = item.column()
        if not (0 <= row < len(self._stack)):
            return

        L = self._stack[row]
        try:
            txt = item.text().strip().replace(",", ".")
            val = float(txt)

            if col == COL_ALPHA:
                L.alpha_deg = val
            elif col == COL_WIDTH:
                if val <= 0:
                    raise ValueError("Fitil genişliği > 0 olmalı")
                L.fitil_genisligi_mm = val
            elif col == COL_OVERLAP:
                if not (0 <= val < 100):
                    raise ValueError("Çakışma ∈ [0, 100)")
                L.cakisma_pct = val
            elif col == COL_FEED:
                if val <= 0:
                    raise ValueError("Hız > 0 olmalı")
                L.feed_mm_s = val
            elif col == COL_RPM:
                if val <= 0:
                    raise ValueError("RPM > 0 olmalı")
                L.spindle_rpm = val
            elif col == COL_FRICTION:
                if not (0 <= val <= 1):
                    raise ValueError("μ ∈ [0, 1]")
                L.friction_mu = val
            else:
                return

            # Etiketi güncelle (açı değişmiş olabilir)
            L.label = L.auto_label()
            self._schedule_recalc()
            self._emit_stack_changed()

        except ValueError as exc:
            QMessageBox.warning(
                self, "Geçersiz Değer",
                f"Satır {row+1}, sütun {col}: {exc}",
            )
            # Eski değeri geri yükle
            self._suppress_cell_signal = True
            self._populate_row(row, L)
            self._suppress_cell_signal = False

    def _on_type_changed(self, row: int) -> None:
        if not self._backend_ok or not (0 <= row < len(self._stack)):
            return
        combo = self._table.cellWidget(row, COL_TYPE)
        if combo is None:
            return
        new_val = combo.currentData()
        try:
            self._stack[row].type = LayerType(new_val)
            self._stack[row].label = self._stack[row].auto_label()
        except Exception:
            return
        self._schedule_recalc()
        self._emit_stack_changed()

    def _on_strategy_changed(self, row: int) -> None:
        if not self._backend_ok or not (0 <= row < len(self._stack)):
            return
        combo = self._table.cellWidget(row, COL_STRATEGY)
        if combo is None:
            return
        new_val = combo.currentData()
        if new_val in ("geodesic", "non_geodesic"):
            self._stack[row].strategy = new_val
            self._schedule_recalc()
            self._emit_stack_changed()

    def _on_selection_changed(self) -> None:
        row = self._table.currentRow()
        if row >= 0:
            self.katmanSecildi.emit(row)

    # ════════════════════════════════════════════════════════════════════════
    # Debounce + Arkaplan analiz
    # ════════════════════════════════════════════════════════════════════════

    def _schedule_recalc(self) -> None:
        """200 ms debounce başlat; arka arkaya değişikliklerde son hesap kazanır."""
        if not self._backend_ok:
            return
        if self._thread and self._thread.isRunning():
            self._pending_recalc = True
            return
        self._debounce_timer.start(self._DEBOUNCE_MS)

    @Slot()
    def _recalculate(self) -> None:
        """Debounce sonrası gerçek analizi başlat."""
        if not self._backend_ok or len(self._stack) == 0:
            self._update_stats_blank()
            return
        if self._thread and self._thread.isRunning():
            self._pending_recalc = True
            return

        self._set_status("Hesaplanıyor…")

        self._thread = QThread(self)
        self._worker = _AnalysisWorker(
            stack_dict=self._stack.to_dict(),
            mandrel_diameter_mm=self._mandrel_diameter_mm,
            mandrel_length_mm=self._mandrel_length_mm,
            mandrel_radius_mm=self._mandrel_radius_mm,
            P_operating_MPa=self._P_operating_MPa,
            material_key=self._material_key,
            safety_code=self._safety_code,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_analysis_done)
        self._worker.error.connect(self._on_analysis_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    @Slot(dict)
    def _on_analysis_done(self, result: dict) -> None:
        """Worker sonucu UI'a uygula."""
        self._last_validations = result.get("validations", [])
        self._apply_validations_to_table(self._last_validations)

        burst = result.get("burst_MPa", 0.0)
        SF    = result.get("SF", 0.0)
        MoS   = result.get("MoS", -1.0)
        mass  = result.get("mass_kg", 0.0)
        t_tot = result.get("total_t_mm", 0.0)
        P_tar = result.get("P_target_MPa", self._P_operating_MPa)

        self._lbl_burst.setText(f"{burst:.2f} MPa")
        self._lbl_sf.setText(f"{SF:.3f}")
        mos_color = (COLOR["ok"] if MoS >= 0 else COLOR["crit"])
        self._lbl_mos.setStyleSheet(
            f"color: {mos_color}; font-family: monospace; "
            f"font-size: 13px; font-weight: bold;"
        )
        self._lbl_mos.setText(f"{MoS*100:+.1f}%")
        self._lbl_mass.setText(f"{mass:.3f} kg")
        self._lbl_thickness.setText(f"{t_tot:.3f} mm")
        self._lbl_p_target.setText(f"{P_tar:.2f} MPa")

        # Verdict
        all_pass = all(v.get("passes", False) for v in self._last_validations)
        if all_pass and MoS >= 0:
            self._verdict_lbl.setText("★ UYGUN ★")
            self._verdict_lbl.setStyleSheet(
                f"color: {COLOR['ok']}; "
                f"background: #1a3a1a; "
                f"border: 1px solid #3a5a3a; "
                f"border-radius: 4px; padding: 12px; "
                f"font-size: 15px; font-weight: bold;"
            )
        else:
            self._verdict_lbl.setText("✗ YETERSİZ ✗")
            self._verdict_lbl.setStyleSheet(
                f"color: {COLOR['crit']}; "
                f"background: #3a1a1a; "
                f"border: 1px solid #5a2a2a; "
                f"border-radius: 4px; padding: 12px; "
                f"font-size: 15px; font-weight: bold;"
            )

        # Uyarı metni
        warnings = []
        for i, v in enumerate(self._last_validations):
            if not v.get("passes", True):
                warnings.append(
                    f"Katman {i+1}: {v.get('warning_msg', 'uyarı')}"
                )
        if MoS < 0:
            warnings.append(
                f"Burst basıncı yetersiz: SF = {SF:.2f} < gerekli."
            )
        if warnings:
            self._warn_label.setText("\n".join(warnings))
            self._warn_label.setStyleSheet(
                f"color: {COLOR['warn']}; padding: 4px;"
            )
        else:
            self._warn_label.setText("Uyarı yok.")
            self._warn_label.setStyleSheet(
                f"color: {COLOR['text_secondary']}; padding: 4px;"
            )

        self._set_status(
            f"{len(self._stack)} katman · burst {burst:.2f} MPa · "
            f"SF {SF:.2f} · kütle {mass:.2f} kg"
        )

    @Slot(str)
    def _on_analysis_error(self, msg: str) -> None:
        self._set_status(f"Hesap hatası: {msg}")
        self._warn_label.setText(f"Hesap hatası:\n{msg}")
        self._warn_label.setStyleSheet(f"color: {COLOR['crit']}; padding: 4px;")

    @Slot()
    def _on_thread_done(self) -> None:
        self._thread = None
        self._worker = None
        if self._pending_recalc:
            self._pending_recalc = False
            self._debounce_timer.start(self._DEBOUNCE_MS)

    def _apply_validations_to_table(self, validations: List[Dict[str, Any]]) -> None:
        """Worker'dan gelen doğrulamaları LED + satır rengi olarak göster."""
        self._suppress_cell_signal = True

        n = min(len(validations), self._table.rowCount())
        for i in range(n):
            v = validations[i]
            color = v.get("status_color", "gray")
            slip  = v.get("max_slip_ratio", 0.0)

            if not v.get("passes", True):
                text = f"KAYMA · {slip:.2f}"
                self.kaymaUyarisi.emit(i, float(slip))
            elif color == "yellow":
                text = f"Sınırda · {slip:.2f}"
            else:
                text = f"OK · {slip:.2f}"

            self._set_status_widget(i, color, text)
            self._apply_row_color(i, color)

        self._suppress_cell_signal = False

    # ════════════════════════════════════════════════════════════════════════
    # Backend-bağımsız tablo yardımcıları
    # ════════════════════════════════════════════════════════════════════════

    def _insert_raw_row(self,
                        layer_type: str,
                        alpha_deg: float = 45.0,
                        tow_w: float = 6.0,
                        overlap: float = 5.0,
                        feed: float = 80.0,
                        rpm: float = 60.0,
                        friction: float = 0.30,
                        strategy: str = "geodesic") -> None:
        """Backend yokken doğrudan tabloya varsayılan değerlerle satır ekle."""
        row_id = self._table.rowCount()
        d: Dict[str, Any] = {
            "id": row_id, "type": layer_type, "layer_type": layer_type,
            "alpha_deg": alpha_deg,
            "fitil_genisligi_mm": tow_w,
            "cakisma_pct": overlap,
            "thickness_mm": 0.30,
            "feed_mm_s": feed,
            "spindle_rpm": rpm,
            "friction_mu": friction,
            "strategy": strategy,
            "label": (
                f"{self._LAYER_TYPE_LABELS.get(layer_type, layer_type)} "
                f"{alpha_deg:+.1f}°"
            ),
            "notes": "Manuel UI girişi",
        }
        self._ui_layers.append(d)

        self._suppress_cell_signal = True
        row = self._table.rowCount()
        self._table.insertRow(row)

        id_item = QTableWidgetItem(str(row_id))
        id_item.setTextAlignment(Qt.AlignCenter)
        id_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        id_item.setForeground(QBrush(QColor(COLOR["text_secondary"])))
        self._table.setItem(row, COL_ID, id_item)

        type_combo = QComboBox()
        type_combo.setStyleSheet(
            f"QComboBox {{ background: {COLOR['bg_widget']}; "
            f"color: {COLOR['text_primary']}; border: 1px solid {COLOR['border']}; }}"
        )
        for key, lbl in self._LAYER_TYPE_LABELS.items():
            type_combo.addItem(lbl, key)
        idx = type_combo.findData(layer_type)
        if idx >= 0:
            type_combo.setCurrentIndex(idx)
        type_combo.currentIndexChanged.connect(
            lambda _i: self._schedule_autosend())
        self._table.setCellWidget(row, COL_TYPE, type_combo)

        for col, val, fmt in [
            (COL_ALPHA,    alpha_deg, "{:+.1f}"),
            (COL_WIDTH,    tow_w,     "{:.2f}"),
            (COL_OVERLAP,  overlap,   "{:.1f}"),
            (COL_FEED,     feed,      "{:.1f}"),
            (COL_RPM,      rpm,       "{:.1f}"),
            (COL_FRICTION, friction,  "{:.2f}"),
        ]:
            it = QTableWidgetItem(fmt.format(val))
            it.setTextAlignment(Qt.AlignCenter)
            it.setForeground(QBrush(QColor(COLOR["text_primary"])))
            self._table.setItem(row, col, it)

        strat_combo = QComboBox()
        strat_combo.setStyleSheet(
            f"QComboBox {{ background: {COLOR['bg_widget']}; "
            f"color: {COLOR['text_primary']}; border: 1px solid {COLOR['border']}; }}"
        )
        strat_combo.addItem("Jeodezik",     "geodesic")
        strat_combo.addItem("Non-Jeodezik", "non_geodesic")
        sidx = strat_combo.findData(strategy)
        if sidx >= 0:
            strat_combo.setCurrentIndex(sidx)
        strat_combo.currentIndexChanged.connect(
            lambda _i: self._schedule_autosend())
        self._table.setCellWidget(row, COL_STRATEGY, strat_combo)

        self._set_status_widget(row, "gray", "—")
        self._suppress_cell_signal = False
        self._lbl_layer_count.setText(f"{self._table.rowCount()} katman")
        self._set_status(
            f"{self._LAYER_TYPE_LABELS.get(layer_type, layer_type)} "
            f"eklendi (α = {alpha_deg:+.1f}°)"
        )
        self._schedule_autosend()

    def _get_stack_as_dict(self) -> dict:
        """Mevcut katman yığınını dict formatında döndür (backend veya UI)."""
        if self._backend_ok and self._stack is not None:
            d = self._stack.to_dict()
            # Geriye dönük uyumluluk: her katmanda hem 'type' hem 'layer_type'
            for layer in d.get("layers", []):
                if "type" in layer and "layer_type" not in layer:
                    layer["layer_type"] = layer["type"]
                if "layer_type" in layer and "type" not in layer:
                    layer["type"] = layer["layer_type"]
            return d

        # Backend yok: tablodan oku
        layers: List[Dict[str, Any]] = []
        for row in range(self._table.rowCount()):
            type_combo = self._table.cellWidget(row, COL_TYPE)
            layer_type = type_combo.currentData() if type_combo else "helical"

            def _cell_float(col: int, default: float = 0.0) -> float:
                it = self._table.item(row, col)
                if it is None:
                    return default
                try:
                    return float(it.text().replace(",", "."))
                except ValueError:
                    return default

            strat_combo = self._table.cellWidget(row, COL_STRATEGY)
            strategy = strat_combo.currentData() if strat_combo else "geodesic"
            alpha = _cell_float(COL_ALPHA, 45.0)

            layers.append({
                "id": row,
                "type": layer_type,
                "layer_type": layer_type,
                "alpha_deg": alpha,
                "fitil_genisligi_mm": _cell_float(COL_WIDTH, 6.0),
                "cakisma_pct": _cell_float(COL_OVERLAP, 5.0),
                "thickness_mm": 0.30,
                "feed_mm_s": _cell_float(COL_FEED, 80.0),
                "spindle_rpm": _cell_float(COL_RPM, 60.0),
                "friction_mu": _cell_float(COL_FRICTION, 0.30),
                "strategy": strategy,
                "label": (
                    f"{self._LAYER_TYPE_LABELS.get(layer_type, layer_type)} "
                    f"{alpha:+.1f}°"
                ),
                "notes": "Manuel UI girişi",
            })

        return {
            "versiyon": "1.0",
            "default_friction_mu": 0.3,
            "next_id": len(layers),
            "layers": layers,
        }

    def _on_gonder_uretim(self) -> None:
        """Mevcut katman yığınını Üretim Tasarım Merkezi ve CAM Üretici'ye gönder."""
        n = self._table.rowCount() if not self._backend_ok else (
            len(self._stack) if self._stack else 0
        )
        if n == 0:
            QMessageBox.information(
                self, "Bilgi",
                "Göndermek için tabloya en az bir katman ekleyin.",
            )
            return
        stack_dict = self._get_stack_as_dict()
        self.uretimeGonder.emit(stack_dict)
        self._set_status(f"{n} katman Üretim Merkezi ve CAM'a gönderildi.")

    def _schedule_autosend(self) -> None:
        """Tablo mutasyonu sonrası otomatik gönderim için debounce başlat."""
        self._autosend_timer.start(self._DEBOUNCE_MS)

    @Slot()
    def _emit_uretime_auto(self) -> None:
        """Debounce sonrası: mevcut yığını sessizce üretim + CAM'a fırlat."""
        n = self._table.rowCount() if not self._backend_ok else (
            len(self._stack) if self._stack else 0
        )
        if n == 0:
            return
        self.uretimeGonder.emit(self._get_stack_as_dict())

    def _update_stats_blank(self) -> None:
        """Yığın boş veya hesap yok — sağ paneli sıfırla."""
        self._lbl_burst.setText("—")
        self._lbl_sf.setText("—")
        self._lbl_mos.setText("—")
        self._lbl_mass.setText("—")
        t_tot = self._stack.total_thickness_mm() if self._backend_ok else 0.0
        self._lbl_thickness.setText(f"{t_tot:.3f} mm")
        self._lbl_p_target.setText(f"{self._P_operating_MPa:.2f} MPa")
        self._verdict_lbl.setText("Katman ekleyin")
        self._verdict_lbl.setStyleSheet(
            f"color: {COLOR['text_secondary']}; "
            f"background: {COLOR['bg_widget']}; "
            f"border: 1px solid {COLOR['border']}; "
            f"border-radius: 4px; padding: 12px; "
            f"font-size: 13px; font-weight: bold;"
        )
        self._warn_label.setText("Uyarı yok.")
        self._warn_label.setStyleSheet(
            f"color: {COLOR['text_secondary']}; padding: 4px;"
        )

    def _set_status(self, txt: str) -> None:
        self._status_label.setText(txt)

    def _emit_stack_changed(self) -> None:
        """katmanDegisti sinyalini fırlat (CAM ve 3D paneller dinler)."""
        if self._backend_ok:
            self.katmanDegisti.emit(self._stack)
        # Her iki modda da CAM motoruna otomatik köprü
        self._schedule_autosend()

    # ════════════════════════════════════════════════════════════════════════
    # Harici API (proje yöneticisi ve diğer paneller için)
    # ════════════════════════════════════════════════════════════════════════

    def set_mandrel_parameters(self,
                                diameter_mm: float,
                                length_mm: float,
                                P_operating_MPa: float) -> None:
        """Proje yöneticisi mandrel/basınç güncellediğinde çağrılır."""
        self._mandrel_diameter_mm = max(1.0, float(diameter_mm))
        self._mandrel_radius_mm   = self._mandrel_diameter_mm / 2.0
        self._mandrel_length_mm   = max(1.0, float(length_mm))
        self._P_operating_MPa     = max(0.01, float(P_operating_MPa))

        self._lbl_mandrel_d.setText(f"{self._mandrel_diameter_mm:.1f} mm")
        self._lbl_mandrel_l.setText(f"{self._mandrel_length_mm:.1f} mm")
        self._lbl_mandrel_p.setText(f"{self._P_operating_MPa:.2f} MPa")
        self._lbl_p_target.setText(f"{self._P_operating_MPa:.2f} MPa")

        self._schedule_recalc()

    def set_material(self, material_key: str) -> None:
        """Malzeme kütüphanesinden seçim yapıldığında çağrılır."""
        if material_key:
            self._material_key = material_key
            self._schedule_recalc()

    def set_safety_code(self, code_value: str) -> None:
        """Güvenlik kodu değişikliği."""
        if code_value:
            self._safety_code = code_value
            self._schedule_recalc()

    def get_stack(self):
        """Mevcut LayerStack referansı (CAM panel okuma için)."""
        return self._stack

    def apply_project(self, proje: Dict[str, Any]) -> None:
        """ProjeYoneticisi.projeYuklendi sinyaline cevap olarak çağrılır."""
        if not self._backend_ok:
            return
        m = proje.get("mandrel", {})
        self.set_mandrel_parameters(
            diameter_mm=m.get("cap_mm", 200.0),
            length_mm=m.get("uzunluk_mm", 500.0),
            P_operating_MPa=proje.get("basinc_MPa", 10.0),
        )
        mat = proje.get("malzeme")
        if mat:
            self.set_material(mat)
        code = proje.get("guvenlik_kodu")
        if code:
            self.set_safety_code(code)

        # Katmanları proje'den yükle
        katmanlar = proje.get("katmanlar", [])
        if katmanlar:
            self._stack.clear()
            for k in katmanlar:
                try:
                    tip = k.get("tip", "helical")
                    # Eski formatı yeni LayerSpec'e dönüştür
                    if tip in ("sarmal", "helical"):
                        layer_type = LayerType.HELICAL
                    elif tip in ("cevre", "hoop"):
                        layer_type = LayerType.HOOP
                    else:
                        layer_type = LayerType(tip)
                    cift = max(1, int(k.get("cift_sayisi", 1)))
                    for _ in range(cift):
                        spec = LayerSpec(
                            id=self._stack._new_id(),
                            type=layer_type,
                            alpha_deg=float(k.get("aci_deg", 45.0)),
                            fitil_genisligi_mm=float(k.get("fitil_genisligi_mm", 6.0)),
                            cakisma_pct=float(k.get("cakisma_pct", 5.0)),
                            thickness_mm=float(k.get("ply_kalinlik_mm", 0.30)),
                            feed_mm_s=float(k.get("feed_mm_s", 80.0)),
                            spindle_rpm=float(k.get("spindle_rpm", 60.0)),
                            friction_mu=float(k.get("friction_mu", 0.30)),
                            strategy=k.get("strategy", "geodesic"),
                            label="",
                            notes=k.get("notlar", ""),
                        )
                        self._stack.add_layer(spec)
                except Exception:
                    continue
            self._refresh_table()
            self._schedule_recalc()
            self._emit_stack_changed()


__all__ = ["KatmanDizilimPaneli"]
