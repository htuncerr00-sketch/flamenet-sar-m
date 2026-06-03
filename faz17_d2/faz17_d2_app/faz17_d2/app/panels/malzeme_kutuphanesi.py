"""
panels/malzeme_kutuphanesi.py — Malzeme Kütüphanesi Paneli
============================================================
Faz 23 ENG-1 material_allowables modülünü görsel olarak sunar.
Fiber/reçine sistemleri, elastik sabitler, mukavemet limitleri,
A/B-basis istatistiksel allowables ve çevresel knockdown faktörleri.
"""
from __future__ import annotations
import sys
import os
from typing import Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QListWidget, QListWidgetItem,
    QGridLayout, QLineEdit, QTabWidget,
    QPushButton, QTableWidget, QTableWidgetItem,
    QMessageBox, QHeaderView, QGroupBox,
)

from ..themes.dark_industrial import COLOR


def _import_backend() -> Tuple:
    """Backend malzeme modülünü yükle; başarısız olursa (None,) döndür."""
    try:
        from backend.core.material_allowables import (
            available_laminas, get_lamina,
            available_engineering_materials, get_engineering_material,
        )
        return available_laminas, get_lamina, available_engineering_materials, get_engineering_material
    except Exception:
        return None, None, None, None


_FIELD_STYLE = (
    "background: #1A1A2E; color: #E8E8E8; "
    "border: 1px solid #3A3A5C; padding: 3px;"
)
_HDR_STYLE = "font-weight: bold; color: #A0C8F0;"


class MalzemeKutuphanesiPanel(QWidget):
    """
    Malzeme kütüphanesi paneli.

    ENG-1 kataloğundaki fiber/reçine sistemlerini görsel olarak sunar.
    Elastik özellikler, mukavemet, A/B-basis ve çevresel knockdown
    sekmelerinde organize edilmiştir.
    """

    malzemeSecildi = Signal(str)  # seçilen malzeme key'i

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        (self._avail_lam, self._get_lam,
         self._avail_eng, self._get_eng) = _import_backend()
        self._current_key: Optional[str] = None
        self._current_kind: Optional[str] = None
        self._build_ui()
        self._populate_list()

    # ── UI kurulumu ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        title = QLabel("Malzeme Kütüphanesi")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #E8E8E8;")
        root.addWidget(title)

        sub = QLabel("Fiber/reçine sistemleri · Elastik sabitler · Mukavemet · A/B-basis · Çevresel knockdown")
        sub.setStyleSheet("color: #808080; font-size: 11px;")
        root.addWidget(sub)

        splitter = QSplitter(Qt.Horizontal)

        # Sol: liste ────────────────────────────────────────────────────────
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)

        lbl = QLabel("Malzeme Sistemleri")
        lbl.setStyleSheet(_HDR_STYLE)
        ll.addWidget(lbl)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget { background: #1A1A2E; color: #E8E8E8; "
            "border: 1px solid #3A3A5C; }"
            "QListWidget::item:selected { background: #2A3A6A; }"
        )
        self._list.currentItemChanged.connect(self._on_selection)
        ll.addWidget(self._list)

        self._btn_uygula = QPushButton("Projeye Uygula")
        self._btn_uygula.setEnabled(False)
        self._btn_uygula.clicked.connect(self._on_apply)
        self._btn_uygula.setStyleSheet(
            "QPushButton { background: #1A6B3C; color: white; "
            "padding: 7px; border: none; border-radius: 3px; }"
            "QPushButton:disabled { background: #2A2A3A; color: #606060; }"
        )
        ll.addWidget(self._btn_uygula)
        left.setMinimumWidth(200)
        left.setMaximumWidth(280)
        splitter.addWidget(left)

        # Sağ: özellik sekmeleri ────────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)

        self._prop_tabs = QTabWidget()
        self._prop_tabs.setStyleSheet(
            "QTabBar::tab { padding: 6px 14px; }"
            "QTabBar::tab:selected { color: #A0C8F0; }"
        )
        self._build_elastik_tab()
        self._build_mukavemet_tab()
        self._build_allowables_tab()
        self._build_cevre_tab()
        rl.addWidget(self._prop_tabs)

        self._lbl_summary = QLabel("")
        self._lbl_summary.setWordWrap(True)
        self._lbl_summary.setStyleSheet(
            "color: #A0C8F0; font-family: monospace; font-size: 11px; "
            "padding: 4px; border-top: 1px solid #3A3A5C;"
        )
        rl.addWidget(self._lbl_summary)

        splitter.addWidget(right)
        splitter.setSizes([230, 560])
        root.addWidget(splitter)

    def _build_elastik_tab(self) -> None:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(12, 12, 12, 12)
        g.setSpacing(8)

        rows = [
            ("E₁ — Fiber Yönü Elastik Modülü", "GPa", "_e1"),
            ("E₂ — Enine Elastik Modülü", "GPa", "_e2"),
            ("G₁₂ — Düzlem İçi Kayma Modülü", "GPa", "_g12"),
            ("ν₁₂ — Büyük Poisson Oranı", "—", "_nu12"),
            ("ν₂₁ — Küçük Poisson Oranı (ν₁₂·E₂/E₁)", "—", "_nu21"),
            ("T_g — Camsı Geçiş Sıcaklığı", "°C", "_tg"),
            ("ρ — Ply Yoğunluğu", "g/cm³", "_density"),
            ("t_ply — Nominal Ply Kalınlığı", "mm", "_ply_t"),
        ]
        for row, (label, unit, attr) in enumerate(rows):
            g.addWidget(QLabel(label), row, 0)
            edit = QLineEdit("—")
            edit.setReadOnly(True)
            edit.setStyleSheet(_FIELD_STYLE)
            g.addWidget(edit, row, 1)
            g.addWidget(QLabel(unit), row, 2)
            setattr(self, attr, edit)
        g.setRowStretch(len(rows), 1)
        self._prop_tabs.addTab(w, "Elastik Özellikler")

    def _build_mukavemet_tab(self) -> None:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(12, 12, 12, 12)
        g.setSpacing(8)

        for col, txt in enumerate(("Özellik", "Ortalama", "A-Basis", "B-Basis", "Birim")):
            lbl = QLabel(txt)
            lbl.setStyleSheet(_HDR_STYLE)
            g.addWidget(lbl, 0, col)

        rows = [
            ("X_t — Boyuna Çekme", "_xt", "_xt_a", "_xt_b"),
            ("X_c — Boyuna Basma", "_xc", "_xc_a", "_xc_b"),
            ("Y_t — Enine Çekme", "_yt", "_yt_a", "_yt_b"),
            ("Y_c — Enine Basma", "_yc", "_yc_a", "_yc_b"),
            ("S — Düzlem İçi Kayma", "_s", "_s_a", "_s_b"),
        ]
        for row, (label, am, aa, ab) in enumerate(rows, start=1):
            g.addWidget(QLabel(label), row, 0)
            for col, attr in [(1, am), (2, aa), (3, ab)]:
                e = QLineEdit("—")
                e.setReadOnly(True)
                e.setStyleSheet(_FIELD_STYLE)
                g.addWidget(e, row, col)
                setattr(self, attr, e)
            g.addWidget(QLabel("MPa"), row, 4)

        cv_row = len(rows) + 1
        g.addWidget(QLabel("CV — Değişkenlik Katsayısı"), cv_row, 0)
        self._muk_cv = QLineEdit("—")
        self._muk_cv.setReadOnly(True)
        self._muk_cv.setStyleSheet(_FIELD_STYLE)
        g.addWidget(self._muk_cv, cv_row, 1)
        g.addWidget(QLabel("%"), cv_row, 2)
        g.setRowStretch(cv_row + 1, 1)
        self._prop_tabs.addTab(w, "Mukavemet")

    def _build_allowables_tab(self) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        note = QLabel(
            "A-Basis: %99 güvenirlik, %95 güven · "
            "B-Basis: %90 güvenirlik, %95 güven\n"
            "Normal dağılım varsayımı (CMH-17 / MIL-HDBK-17)"
        )
        note.setStyleSheet("color: #909090; font-size: 11px;")
        v.addWidget(note)

        tw_lbl = QLabel("Tsai-Wu Etkileşim Katsayıları:")
        tw_lbl.setStyleSheet(_HDR_STYLE)
        v.addWidget(tw_lbl)

        self._tw_table = QTableWidget(6, 2)
        self._tw_table.setHorizontalHeaderLabels(["Katsayı", "Değer"])
        self._tw_table.verticalHeader().setVisible(False)
        hh = self._tw_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        self._tw_table.setStyleSheet(
            "QTableWidget { background: #1A1A2E; color: #E8E8E8; "
            "gridline-color: #3A3A5C; }"
            "QHeaderView::section { background: #252540; color: #A0C8F0; padding: 4px; }"
        )
        for i, name in enumerate(["F₁", "F₂", "F₁₁", "F₂₂", "F₆₆", "F₁₂"]):
            n_item = QTableWidgetItem(name)
            n_item.setFlags(Qt.ItemIsEnabled)
            v_item = QTableWidgetItem("—")
            v_item.setFlags(Qt.ItemIsEnabled)
            self._tw_table.setItem(i, 0, n_item)
            self._tw_table.setItem(i, 1, v_item)
        v.addWidget(self._tw_table)
        v.addStretch()
        self._prop_tabs.addTab(w, "Tsai-Wu / Allowables")

    def _build_cevre_tab(self) -> None:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(12, 12, 12, 12)
        g.setSpacing(8)

        for col, txt in enumerate(("Faktör", "Değer", "Açıklama")):
            lbl = QLabel(txt)
            lbl.setStyleSheet(_HDR_STYLE)
            g.addWidget(lbl, 0, col)

        rows = [
            ("K_hotwet — Sıcak/Islak", "_kw", "T < T_g − 50°C, doymuş nem"),
            ("K_fatigue — Yorulma", "_kf", "R=0.1, 10⁶ döngü"),
            ("K_aging — Yaşlanma", "_ka", "20 yıl saklama"),
            ("K_uv — UV Bozunma", "_ku", "Açık UV maruziyeti"),
            ("K_creep — Sünme", "_kc", "Uzun süreli yükleme"),
            ("K_impact — Darbe", "_ki", "BVID sonrası dayanım kaybı"),
            ("K_TOPLAM = ∏ Kᵢ", "_kt", "Tüm faktörlerin çarpımı"),
        ]
        for row, (label, attr, desc) in enumerate(rows, start=1):
            g.addWidget(QLabel(label), row, 0)
            style = _FIELD_STYLE
            if attr == "_kt":
                style = ("background: #0A2A0A; color: #90FF90; "
                         "border: 1px solid #3A5C3A; padding: 3px; font-weight: bold;")
            e = QLineEdit("—")
            e.setReadOnly(True)
            e.setStyleSheet(style)
            g.addWidget(e, row, 1)
            g.addWidget(QLabel(desc), row, 2)
            setattr(self, attr, e)
        g.setRowStretch(len(rows) + 1, 1)
        self._prop_tabs.addTab(w, "Çevresel Faktörler")

    # ── Liste doldurma ──────────────────────────────────────────────────────

    def _populate_list(self) -> None:
        self._list.clear()
        if self._avail_lam is None:
            self._list.addItem(QListWidgetItem("⚠ Backend yüklenemedi"))
            return

        for key in self._avail_lam():
            try:
                lam = self._get_lam(key)
                display = lam.name
            except Exception:
                display = key
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, ("lamina", key))
            self._list.addItem(item)

        if self._avail_eng is not None:
            for key in self._avail_eng():
                try:
                    mat = self._get_eng(key)
                    display = f"★  {mat.name}"
                except Exception:
                    display = f"★  {key}"
                item = QListWidgetItem(display)
                item.setData(Qt.UserRole, ("eng", key))
                self._list.addItem(item)

    # ── Olay yönetimi ────────────────────────────────────────────────────────

    def _on_selection(self, current: Optional[QListWidgetItem], _previous) -> None:
        if current is None:
            self._btn_uygula.setEnabled(False)
            return
        data = current.data(Qt.UserRole)
        if data is None:
            return
        kind, key = data
        self._current_key = key
        self._current_kind = kind
        self._btn_uygula.setEnabled(True)
        self._load_props(kind, key)

    def _load_props(self, kind: str, key: str) -> None:
        if self._get_lam is None:
            return
        try:
            if kind == "lamina":
                lam = self._get_lam(key)
                self._fill_lamina(lam)
                for attr in ("_kw", "_kf", "_ka", "_ku", "_kc", "_ki", "_kt"):
                    getattr(self, attr).setText("—")
            elif kind == "eng" and self._get_eng is not None:
                mat = self._get_eng(key)
                self._fill_lamina(mat.lamina)
                self._fill_env(mat.environment)
        except Exception as exc:
            self._lbl_summary.setText(f"Yükleme hatası: {exc}")

    def _fill_lamina(self, lam) -> None:
        self._e1.setText(f"{lam.E_1_GPa:.1f}")
        self._e2.setText(f"{lam.E_2_GPa:.1f}")
        self._g12.setText(f"{lam.G_12_GPa:.1f}")
        self._nu12.setText(f"{lam.nu_12:.4f}")
        self._nu21.setText(f"{lam.nu_21:.4f}")
        self._tg.setText(f"{lam.glass_transition_T_C:.0f}")
        self._density.setText(f"{lam.ply_density_g_cm3:.2f}")
        self._ply_t.setText(f"{lam.nominal_ply_thickness_mm:.3f}")

        self._xt.setText(f"{lam.X_t_MPa:.0f}")
        self._xc.setText(f"{lam.X_c_MPa:.0f}")
        self._yt.setText(f"{lam.Y_t_MPa:.0f}")
        self._yc.setText(f"{lam.Y_c_MPa:.0f}")
        self._s.setText(f"{lam.S_MPa:.0f}")

        self._xt_a.setText(f"{lam.X_t_A_basis():.0f}")
        self._xc_a.setText(f"{lam.X_c_A_basis():.0f}")
        self._yt_a.setText(f"{lam.Y_t_A_basis():.0f}")
        self._yc_a.setText(f"{lam.Y_c_A_basis():.0f}")
        self._s_a.setText(f"{lam.S_A_basis():.0f}")

        self._xt_b.setText(f"{lam.X_t_B_basis():.0f}")
        self._xc_b.setText(f"{lam.X_c_B_basis():.0f}")
        self._yt_b.setText(f"{lam.Y_t_B_basis():.0f}")
        self._yc_b.setText(f"{lam.Y_c_B_basis():.0f}")
        self._s_b.setText(f"{lam.S_B_basis():.0f}")

        self._muk_cv.setText(f"{lam.coefficient_of_variation * 100:.1f}")

        tw = lam.tsai_wu_coefficients()
        for i, k in enumerate(["F_1", "F_2", "F_11", "F_22", "F_66", "F_12"]):
            self._tw_table.item(i, 1).setText(f"{tw[k]:.4e}")

        self._lbl_summary.setText(lam.summary())

    def _fill_env(self, env) -> None:
        self._kw.setText(f"{env.K_hotwet:.3f}")
        self._kf.setText(f"{env.K_fatigue:.3f}")
        self._ka.setText(f"{env.K_aging:.3f}")
        self._ku.setText(f"{env.K_uv:.3f}")
        self._kc.setText(f"{env.K_creep:.3f}")
        self._ki.setText(f"{env.K_impact:.3f}")
        self._kt.setText(f"{env.K_total:.4f}")

    def _on_apply(self) -> None:
        if self._current_key:
            self.malzemeSecildi.emit(self._current_key)
            QMessageBox.information(
                self, "Malzeme Seçildi",
                f"'{self._current_key}' malzemesi projeye uygulandı.",
            )

    # ── Harici API ──────────────────────────────────────────────────────────

    def get_selected_key(self) -> Optional[str]:
        """Mevcut seçili malzeme key'ini döndür."""
        return self._current_key

    def select_material(self, key: str) -> None:
        """Programatik olarak bir malzeme seçer."""
        for i in range(self._list.count()):
            item = self._list.item(i)
            data = item.data(Qt.UserRole)
            if data and data[1] == key:
                self._list.setCurrentItem(item)
                break
