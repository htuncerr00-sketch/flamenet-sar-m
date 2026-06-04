"""
panels/tabaka_yoneticisi.py — Katman Yöneticisi & Mühendislik Analizi
=======================================================================
Faz 23 ENG-7 pressure_vessel_sizing orchestratör ile entegre çalışan
laminat katman yönetimi ve basınçlı kap mühendislik analizi paneli.

Yetenekler:
  • Katman yığını görsel yönetimi (ekle/sil/yeniden sırala)
  • Otomatik kap tasarımı (ENG-7: VesselDesignInput → VesselDesignReport)
  • Manuel katman tablosu CLT analizi (ENG-3/4)
  • Burst tahmin karşılaştırması (CLT-FPF vs Netting)
  • Güvenlik kodu değerlendirmesi (ENG-6)
  • Kütle/fiber/üretim skoru
"""
from __future__ import annotations

import sys
import os
import time
from typing import List, Optional

from PySide6.QtCore import Qt, Signal, QThread, QObject, Slot
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QGroupBox, QLabel, QComboBox, QPushButton,
    QDoubleSpinBox, QSpinBox, QGridLayout,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QAbstractItemView, QMessageBox,
    QTabWidget, QFrame, QProgressBar,
)

from ..themes.dark_industrial import COLOR


# ── Backend yükleyici ────────────────────────────────────────────────────────

def _import_eng():
    try:
        from backend.core.material_allowables import (
            available_engineering_materials, get_engineering_material,
        )
        from backend.core.safety_factor import SafetyCode
        from backend.core.pressure_vessel_sizing import (
            VesselDesignInput, size_pressure_vessel,
        )
        return (available_engineering_materials, get_engineering_material,
                SafetyCode, VesselDesignInput, size_pressure_vessel)
    except Exception:
        return None, None, None, None, None


_FIELD_STYLE = (
    "background: #1A1A2E; color: #E8E8E8; "
    "border: 1px solid #3A3A5C; padding: 2px;"
)
_HDR_STYLE = "font-weight: bold; color: #A0C8F0;"
_PASS_STYLE = "color: #50FF50; font-weight: bold;"
_FAIL_STYLE = "color: #FF5050; font-weight: bold;"
_WARN_STYLE = "color: #FFB050;"


# ── Arkaplan analiz işçisi ───────────────────────────────────────────────────

class _AnalysisWorker(QObject):
    """ENG-7 analizini UI thread dışında çalıştırır."""
    finished = Signal(object)   # VesselDesignReport
    error    = Signal(str)

    def __init__(self, fn, args):
        super().__init__()
        self._fn   = fn
        self._args = args

    @Slot()
    def run(self):
        try:
            result = self._fn(*self._args)
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


# ── Ana panel ────────────────────────────────────────────────────────────────

class TabakaYoneticisiPanel(QWidget):
    """
    Katman yöneticisi + mühendislik analizi birleşik paneli.

    Sağ taraf: kap parametreleri + katman yığını tanımı.
    Sol taraf: analiz sonuçları + rapor.
    """

    # Başarılı analiz sonucunu diğer panellere ilet
    raporHazir        = Signal(object)          # VesselDesignReport
    mandrelDegisti    = Signal(float, float, float)  # D_mm, L_mm, P_MPa
    katmanYiginiHazir = Signal(dict)            # LayerStack uyumlu sözlük

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        (self._avail_eng, self._get_eng,
         self._SafetyCode, self._VDInput,
         self._size_fn) = _import_eng()

        self._thread: Optional[QThread] = None
        self._worker: Optional[_AnalysisWorker] = None
        self._last_report = None

        self._build_ui()
        self._populate_combos()

    # ── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        title = QLabel("Katman Yöneticisi & Mühendislik Analizi")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #E8E8E8;")
        root.addWidget(title)

        splitter = QSplitter(Qt.Horizontal)

        # Sol: giriş paneli ─────────────────────────────────────────────────
        left = self._build_input_panel()
        left.setMinimumWidth(360)
        left.setMaximumWidth(440)
        splitter.addWidget(left)

        # Sağ: çıktı paneli ─────────────────────────────────────────────────
        right = self._build_output_panel()
        splitter.addWidget(right)
        splitter.setSizes([400, 600])

        root.addWidget(splitter)

    def _build_input_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # ── Kap Parametreleri ────────────────────────────────────────────
        grp_kap = QGroupBox("Basınçlı Kap Parametreleri")
        grp_kap.setStyleSheet("QGroupBox { color: #A0C8F0; font-weight: bold; }")
        gg = QGridLayout(grp_kap)
        gg.setSpacing(6)

        gg.addWidget(QLabel("İç Çap (mm)"), 0, 0)
        self._cap = QDoubleSpinBox()
        self._cap.setRange(50, 5000)
        self._cap.setValue(200)
        self._cap.setSingleStep(10)
        self._cap.setDecimals(1)
        self._cap.setStyleSheet(_FIELD_STYLE)
        gg.addWidget(self._cap, 0, 1)

        gg.addWidget(QLabel("Silindir Uzunluğu (mm)"), 1, 0)
        self._uzunluk = QDoubleSpinBox()
        self._uzunluk.setRange(50, 20000)
        self._uzunluk.setValue(500)
        self._uzunluk.setSingleStep(10)
        self._uzunluk.setDecimals(1)
        self._uzunluk.setStyleSheet(_FIELD_STYLE)
        gg.addWidget(self._uzunluk, 1, 1)

        gg.addWidget(QLabel("Çalışma Basıncı (MPa)"), 2, 0)
        self._basinc = QDoubleSpinBox()
        self._basinc.setRange(0.1, 200)
        self._basinc.setValue(10)
        self._basinc.setSingleStep(0.5)
        self._basinc.setDecimals(2)
        self._basinc.setStyleSheet(_FIELD_STYLE)
        gg.addWidget(self._basinc, 2, 1)

        gg.addWidget(QLabel("Malzeme"), 3, 0)
        self._combo_mat = QComboBox()
        self._combo_mat.setStyleSheet(_FIELD_STYLE)
        gg.addWidget(self._combo_mat, 3, 1)

        gg.addWidget(QLabel("Güvenlik Kodu"), 4, 0)
        self._combo_kod = QComboBox()
        self._combo_kod.setStyleSheet(_FIELD_STYLE)
        gg.addWidget(self._combo_kod, 4, 1)

        gg.addWidget(QLabel("Sarma Açısı α (°)"), 5, 0)
        self._alpha = QDoubleSpinBox()
        self._alpha.setRange(1, 89)
        self._alpha.setValue(55)
        self._alpha.setSingleStep(1)
        self._alpha.setDecimals(1)
        self._alpha.setSpecialValueText("Otomatik")
        self._alpha.setStyleSheet(_FIELD_STYLE)
        gg.addWidget(self._alpha, 5, 1)

        gg.addWidget(QLabel("Basis"), 6, 0)
        self._combo_basis = QComboBox()
        self._combo_basis.addItems(["B-Basis", "A-Basis", "Ortalama"])
        self._combo_basis.setStyleSheet(_FIELD_STYLE)
        gg.addWidget(self._combo_basis, 6, 1)

        layout.addWidget(grp_kap)

        # ── Otomatik Tasarım ────────────────────────────────────────────
        grp_oto = QGroupBox("Otomatik Kap Tasarımı (ENG-7)")
        grp_oto.setStyleSheet("QGroupBox { color: #A0C8F0; font-weight: bold; }")
        gg2 = QVBoxLayout(grp_oto)

        self._btn_tasarla = QPushButton("Otomatik Tasarım Çalıştır")
        self._btn_tasarla.setStyleSheet(
            "QPushButton { background: #1A3A6A; color: white; "
            "padding: 8px; border: none; border-radius: 3px; font-weight: bold; }"
            "QPushButton:hover { background: #2A4A8A; }"
            "QPushButton:disabled { background: #2A2A3A; color: #606060; }"
        )
        self._btn_tasarla.clicked.connect(self._on_run_analysis)
        if self._size_fn is None:
            self._btn_tasarla.setEnabled(False)
        gg2.addWidget(self._btn_tasarla)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar { background: #1A1A2E; border: 1px solid #3A3A5C; } "
            "QProgressBar::chunk { background: #2A6ABF; }"
        )
        gg2.addWidget(self._progress)

        lbl_note = QLabel(
            "ENG-7 orchestratör: netting analizi → CLT-FPF yinelemeli"
            " yakınsama → kütle/fiber/üretim raporu"
        )
        lbl_note.setWordWrap(True)
        lbl_note.setStyleSheet("color: #707070; font-size: 11px;")
        gg2.addWidget(lbl_note)

        layout.addWidget(grp_oto)

        # ── Manuel Katman Tablosu ────────────────────────────────────────
        grp_tab = QGroupBox("Manuel Katman Yığını")
        grp_tab.setStyleSheet("QGroupBox { color: #A0C8F0; font-weight: bold; }")
        gv = QVBoxLayout(grp_tab)

        self._layer_table = QTableWidget(0, 4)
        self._layer_table.setHorizontalHeaderLabels(
            ["Tip", "Açı (°)", "Kalınlık (mm)", "Çifter"])
        self._layer_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._layer_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._layer_table.setStyleSheet(
            "QTableWidget { background: #1A1A2E; color: #E8E8E8; "
            "gridline-color: #3A3A5C; }"
            "QHeaderView::section { background: #252540; color: #A0C8F0; padding: 4px; }"
        )
        self._layer_table.setMinimumHeight(120)
        gv.addWidget(self._layer_table)

        btn_row = QHBoxLayout()
        for label, handler in [
            ("+ Sarmal", self._add_helical),
            ("+ Çevre", self._add_hoop),
            ("↑ Yukarı", self._move_up),
            ("↓ Aşağı", self._move_down),
            ("Sil", self._delete_layer),
        ]:
            b = QPushButton(label)
            b.setStyleSheet(
                "QPushButton { background: #252540; color: #C0C0E0; "
                "padding: 4px 6px; border: 1px solid #3A3A5C; border-radius: 2px; }"
                "QPushButton:hover { background: #3A3A60; }"
            )
            b.clicked.connect(handler)
            btn_row.addWidget(b)
        gv.addLayout(btn_row)

        self._lbl_toplam = QLabel("Toplam: 0 kat · 0.000 mm")
        self._lbl_toplam.setStyleSheet("color: #A0C8F0; font-size: 11px;")
        gv.addWidget(self._lbl_toplam)
        layout.addWidget(grp_tab)

        # Mandrel boyutu değişimlerini sinyal otobüsüne bağla
        self._cap.valueChanged.connect(self._on_mandrel_changed)
        self._uzunluk.valueChanged.connect(self._on_mandrel_changed)
        self._basinc.valueChanged.connect(self._on_mandrel_changed)

        layout.addStretch()
        return w

    def _build_output_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        tabs = QTabWidget()
        tabs.setStyleSheet("QTabBar::tab { padding: 6px 14px; }")

        # Tab 1: Özet ────────────────────────────────────────────────────
        summary_w = QWidget()
        sg = QGridLayout(summary_w)
        sg.setContentsMargins(12, 12, 12, 12)
        sg.setSpacing(8)

        metrics = [
            ("Önerilen Sarma Açısı", "_res_alpha", "°"),
            ("Helisel Çift Sayısı", "_res_nhel", "çift"),
            ("Çevre Kat Sayısı", "_res_nhoop", "kat"),
            ("Toplam Ply Sayısı", "_res_nply", "ply"),
            ("Toplam Kalınlık", "_res_t", "mm"),
            ("Burst — CLT-FPF", "_res_burst_clt", "MPa"),
            ("Burst — Netting", "_res_burst_net", "MPa"),
            ("Güvenlik Faktörü", "_res_sf", "—"),
            ("Gerekli SF", "_res_sf_req", "—"),
            ("Güvenlik Marjı (MoS)", "_res_mos", "%"),
            ("Tahmini Kütle", "_res_mass", "kg"),
            ("Fiber Kütlesi", "_res_fiber_mass", "kg"),
            ("Fiber Uzunluğu", "_res_fiber_len", "m"),
            ("Üretim Skoru", "_res_mfg", "/100"),
        ]
        for row, (label, attr, unit) in enumerate(metrics):
            sg.addWidget(QLabel(label), row, 0)
            val_lbl = QLabel("—")
            val_lbl.setStyleSheet("color: #E8E8E8; font-family: monospace;")
            val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            sg.addWidget(val_lbl, row, 1)
            sg.addWidget(QLabel(unit), row, 2)
            setattr(self, attr, val_lbl)

        self._res_durum = QLabel("Analiz bekleniyor")
        self._res_durum.setStyleSheet("font-size: 14px; font-weight: bold; color: #808080;")
        self._res_durum.setAlignment(Qt.AlignCenter)
        sg.addWidget(self._res_durum, len(metrics), 0, 1, 3)
        sg.setRowStretch(len(metrics) + 1, 1)
        tabs.addTab(summary_w, "Tasarım Özeti")

        # Tab 2: Tam Rapor ────────────────────────────────────────────────
        self._rapor_text = QTextEdit()
        self._rapor_text.setReadOnly(True)
        self._rapor_text.setStyleSheet(
            "QTextEdit { background: #0E0E1E; color: #C0D0C0; "
            "font-family: monospace; font-size: 12px; }"
        )
        self._rapor_text.setPlaceholderText(
            "Analiz çalıştırıldığında tam rapor burada görünecek...")
        tabs.addTab(self._rapor_text, "Tam Rapor")

        # Tab 3: Uyarılar ────────────────────────────────────────────────
        self._warn_text = QTextEdit()
        self._warn_text.setReadOnly(True)
        self._warn_text.setStyleSheet(
            "QTextEdit { background: #0E0E0E; color: #FFB050; "
            "font-family: monospace; font-size: 12px; }"
        )
        self._warn_text.setPlaceholderText("Uyarılar ve notlar burada görünecek...")
        tabs.addTab(self._warn_text, "Uyarılar")

        layout.addWidget(tabs)
        return w

    # ── Combo doldurma ───────────────────────────────────────────────────────

    def _populate_combos(self) -> None:
        if self._avail_eng is None:
            self._combo_mat.addItem("⚠ Backend yok", None)
            return

        for key in self._avail_eng():
            try:
                mat = self._get_eng(key)
                label = mat.name
            except Exception:
                label = key
            self._combo_mat.addItem(label, key)

        if self._SafetyCode is None:
            return

        code_labels = {
            "asme_bpvc_x":  "ASME BPVC X (SF=2.25)",
            "iso_11119_2":  "ISO 11119-2 (SF=2.25)",
            "iso_11119_3":  "ISO 11119-3 (SF=2.35)",
            "aiaa_s_080":   "AIAA S-080 (SF=2.0)",
            "dot_cffc":     "US DOT CFFC (SF=3.0)",
            "en_12245":     "EN 12245 (SF=2.25)",
            "un_ece_r134":  "UN ECE R134 (SF=2.25)",
        }
        for code_val, label in code_labels.items():
            self._combo_kod.addItem(label, code_val)
        self._combo_kod.setCurrentIndex(1)  # ISO 11119-2 varsayılan

    # ── Katman tablosu yönetimi ──────────────────────────────────────────────

    def _add_layer(self, tip: str, aci: float, kalinlik: float = 0.125,
                   cift: int = 1) -> None:
        row = self._layer_table.rowCount()
        self._layer_table.insertRow(row)

        tip_item = QTableWidgetItem(tip)
        tip_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self._layer_table.setItem(row, 0, tip_item)

        aci_item = QTableWidgetItem(f"{aci:.1f}")
        aci_item.setTextAlignment(Qt.AlignCenter)
        self._layer_table.setItem(row, 1, aci_item)

        t_item = QTableWidgetItem(f"{kalinlik:.3f}")
        t_item.setTextAlignment(Qt.AlignCenter)
        self._layer_table.setItem(row, 2, t_item)

        c_item = QTableWidgetItem(str(cift))
        c_item.setTextAlignment(Qt.AlignCenter)
        self._layer_table.setItem(row, 3, c_item)

        self._update_layer_total()

    def _add_helical(self) -> None:
        alpha = self._alpha.value() if self._alpha.value() > 1.0 else 55.0
        self._add_layer("Sarmal ±α", alpha)

    def _add_hoop(self) -> None:
        self._add_layer("Çevre 90°", 90.0)

    def _move_up(self) -> None:
        row = self._layer_table.currentRow()
        if row <= 0:
            return
        self._swap_rows(row, row - 1)
        self._layer_table.setCurrentCell(row - 1, 0)

    def _move_down(self) -> None:
        row = self._layer_table.currentRow()
        if row < 0 or row >= self._layer_table.rowCount() - 1:
            return
        self._swap_rows(row, row + 1)
        self._layer_table.setCurrentCell(row + 1, 0)

    def _swap_rows(self, r1: int, r2: int) -> None:
        for col in range(self._layer_table.columnCount()):
            item1 = self._layer_table.takeItem(r1, col)
            item2 = self._layer_table.takeItem(r2, col)
            self._layer_table.setItem(r1, col, item2)
            self._layer_table.setItem(r2, col, item1)

    def _delete_layer(self) -> None:
        row = self._layer_table.currentRow()
        if row >= 0:
            self._layer_table.removeRow(row)
            self._update_layer_total()

    def _update_layer_total(self) -> None:
        n = self._layer_table.rowCount()
        total_t = 0.0
        for r in range(n):
            try:
                t = float(self._layer_table.item(r, 2).text())
                c = int(self._layer_table.item(r, 3).text())
                total_t += t * c * 2  # çift = ±α = 2 ply
            except (AttributeError, ValueError):
                pass
        self._lbl_toplam.setText(
            f"Toplam: {n} katman · {total_t:.3f} mm tahmini kalınlık")

    # ── Analiz çalıştırma ────────────────────────────────────────────────────

    def _on_run_analysis(self) -> None:
        if self._size_fn is None:
            QMessageBox.warning(self, "Backend Yok",
                                "Mühendislik backend'i yüklenemedi.")
            return

        mat_key = self._combo_mat.currentData()
        if mat_key is None:
            QMessageBox.warning(self, "Malzeme Seç",
                                "Lütfen bir malzeme seçin.")
            return

        try:
            material = self._get_eng(mat_key)
        except Exception as exc:
            QMessageBox.critical(self, "Malzeme Hatası", str(exc))
            return

        P  = self._basinc.value()
        D  = self._cap.value()
        L  = self._uzunluk.value()
        sc = self._combo_kod.currentData() or "iso_11119_2"

        alpha_val = self._alpha.value()
        alpha_arg = None if alpha_val <= 1.0 else alpha_val

        basis_map = {"B-Basis": "B", "A-Basis": "A", "Ortalama": "MEAN"}
        basis = basis_map.get(self._combo_basis.currentText(), "B")

        inp = self._VDInput(
            P_operating_MPa=P,
            diameter_mm=D,
            length_mm=L,
            material=material,
            safety_code=self._SafetyCode(sc),
            target_alpha_deg=alpha_arg,
            basis=basis,
        )

        self._start_worker(inp)

    def _start_worker(self, inp) -> None:
        if self._thread and self._thread.isRunning():
            return

        self._btn_tasarla.setEnabled(False)
        self._progress.setVisible(True)
        self._res_durum.setText("Analiz çalışıyor…")
        self._res_durum.setStyleSheet("font-size: 14px; font-weight: bold; color: #FFB050;")

        self._thread = QThread(self)
        self._worker = _AnalysisWorker(self._size_fn, (inp,))
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_analysis_done)
        self._worker.error.connect(self._on_analysis_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_done)

        self._thread.start()

    @Slot(object)
    def _on_analysis_done(self, report) -> None:
        self._last_report = report
        self._fill_results(report)
        self.raporHazir.emit(report)

        # Manuel tabloya öneriyi yükle
        self._load_schedule_to_table(report)

        # ENG-7 katman yığınını diğer panellere otomatik besle
        stack_dict = self._report_to_stack_dict(report)
        self.katmanYiginiHazir.emit(stack_dict)

    @Slot(str)
    def _on_analysis_error(self, msg: str) -> None:
        self._res_durum.setText("Hata!")
        self._res_durum.setStyleSheet(_FAIL_STYLE)
        self._rapor_text.setPlainText(f"Analiz hatası:\n{msg}")
        QMessageBox.critical(self, "Analiz Hatası", msg)

    @Slot()
    def _on_thread_done(self) -> None:
        self._btn_tasarla.setEnabled(True)
        self._progress.setVisible(False)
        self._thread = None
        self._worker = None

    # ── Sonuç doldurma ───────────────────────────────────────────────────────

    def _fill_results(self, r) -> None:
        sch = r.layer_schedule
        sa  = r.safety_assessment

        self._res_alpha.setText(f"{sch.alpha_deg:.1f}")
        self._res_nhel.setText(str(sch.n_helical_pairs))
        self._res_nhoop.setText(str(sch.n_hoop))
        self._res_nply.setText(str(sch.n_total_plies))
        self._res_t.setText(f"{sch.total_thickness_mm:.3f}")
        self._res_burst_clt.setText(f"{r.burst_clt.P_burst_design_MPa:.2f}")
        self._res_burst_net.setText(f"{r.burst_netting.P_burst_design_MPa:.2f}")
        self._res_sf.setText(f"{sa.SF_actual:.3f}")
        self._res_sf_req.setText(f"{sa.SF_required:.2f}")
        mos = sa.margin_of_safety * 100
        self._res_mos.setText(f"{mos:+.1f}")
        self._res_mos.setStyleSheet(_PASS_STYLE if mos >= 0 else _FAIL_STYLE)
        self._res_mass.setText(f"{r.estimated_mass_kg:.3f}")
        self._res_fiber_mass.setText(f"{r.estimated_fiber_mass_kg:.3f}")
        self._res_fiber_len.setText(f"{r.estimated_fiber_length_mm / 1000:.1f}")
        self._res_mfg.setText(f"{r.manufacturability_score:.0f}")

        if r.passes_code:
            self._res_durum.setText("★ UYGUN ★")
            self._res_durum.setStyleSheet(
                "font-size: 16px; font-weight: bold; color: #50FF50;")
        else:
            self._res_durum.setText("✗ YETERSİZ ✗")
            self._res_durum.setStyleSheet(
                "font-size: 16px; font-weight: bold; color: #FF5050;")

        self._rapor_text.setPlainText(r.summary())

        warn_lines = r.warnings + r.manufacturability_notes
        if warn_lines:
            self._warn_text.setPlainText("\n".join(warn_lines))
        else:
            self._warn_text.setPlainText("Uyarı yok.")

    def _load_schedule_to_table(self, r) -> None:
        sch = r.layer_schedule
        self._layer_table.setRowCount(0)

        t = sch.ply_thickness_mm
        for _ in range(sch.n_helical_pairs):
            self._add_layer("Sarmal ±α", sch.alpha_deg, t, 1)
        for _ in range(sch.n_hoop):
            self._add_layer("Çevre 90°", 90.0, t, 1)

    # ── Harici API ───────────────────────────────────────────────────────────

    def set_material_key(self, key: str) -> None:
        """Dışarıdan malzeme seçimi (MalzemeKutuphanesiPanel'den sinyal alır)."""
        for i in range(self._combo_mat.count()):
            if self._combo_mat.itemData(i) == key:
                self._combo_mat.setCurrentIndex(i)
                break

    def get_last_report(self):
        return self._last_report

    @Slot()
    def _on_mandrel_changed(self) -> None:
        """Mandrel geometrisi değiştiğinde mandrelDegisti sinyalini yayınla."""
        self.mandrelDegisti.emit(
            self._cap.value(),
            self._uzunluk.value(),
            self._basinc.value(),
        )

    def set_mandrel_parameters(self, D_mm: float, L_mm: float,
                               P_MPa: float = 10.0) -> None:
        """Dışarıdan mandrel parametrelerini güncelle (sinyali bloke ederek)."""
        self._cap.blockSignals(True)
        self._uzunluk.blockSignals(True)
        self._basinc.blockSignals(True)
        try:
            self._cap.setValue(D_mm)
            self._uzunluk.setValue(L_mm)
            self._basinc.setValue(P_MPa)
        finally:
            self._cap.blockSignals(False)
            self._uzunluk.blockSignals(False)
            self._basinc.blockSignals(False)

    def _report_to_stack_dict(self, report) -> dict:
        """VesselDesignReport → LayerStack.to_dict() uyumlu sözlük."""
        try:
            sch = report.layer_schedule
            t   = sch.ply_thickness_mm
            layers = []
            for i in range(sch.n_helical_pairs):
                layers.append({
                    "id": i,
                    "type": "helical",
                    "layer_type": "helical",
                    "alpha_deg": float(sch.alpha_deg),
                    "fitil_genisligi_mm": 3.175,
                    "cakisma_pct": 5.0,
                    "thickness_mm": float(t),
                    "feed_mm_s": 80.0,
                    "spindle_rpm": 60.0,
                    "friction_mu": 0.3,
                    "strategy": "geodesic",
                    "label": f"Sarmal ±{sch.alpha_deg:.0f}°",
                    "notes": "ENG-7 otomatik",
                })
            for j in range(sch.n_hoop):
                layers.append({
                    "id": sch.n_helical_pairs + j,
                    "type": "hoop",
                    "layer_type": "hoop",
                    "alpha_deg": 89.5,
                    "fitil_genisligi_mm": 3.175,
                    "cakisma_pct": 5.0,
                    "thickness_mm": float(t),
                    "feed_mm_s": 60.0,
                    "spindle_rpm": 60.0,
                    "friction_mu": 0.3,
                    "strategy": "geodesic",
                    "label": "Çevre 90°",
                    "notes": "ENG-7 otomatik",
                })
            return {
                "versiyon": "1.0",
                "default_friction_mu": 0.3,
                "next_id": len(layers),
                "layers": layers,
            }
        except Exception:
            return {"layers": []}
