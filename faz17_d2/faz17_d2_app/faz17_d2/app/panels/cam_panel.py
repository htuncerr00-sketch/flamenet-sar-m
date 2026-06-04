"""
panels/cam_panel.py — Ana CAM Arayüzü
=======================================
İki sekmeli CAM paneli:
  Sekme A: Geometri & Parametreler
  Sekme B: G-code & Çıktı

Sol taraf: 3D önizleme  |  Sağ taraf: parametreler + çıktı
"""
from __future__ import annotations
import os
import sys
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QGroupBox, QLabel, QComboBox, QPushButton,
    QDoubleSpinBox, QSpinBox, QFileDialog, QTextEdit,
    QGridLayout, QApplication, QMessageBox, QProgressBar,
    QFrame, QSizePolicy,
)

from .winding_3d import Winding3DPanel
from ..themes.dark_industrial import COLOR


def _make_backend():
    """Backend CAM modüllerini içe aktar."""
    from backend.core.geometry_engine import MandrelProfile
    from backend.core.path_generator import WindingPathParams, generate_path
    from backend.core.motion_planner import plan_motion
    from backend.core.gcode_postprocessor import MachineConfig, generate_gcode
    return MandrelProfile, WindingPathParams, generate_path, plan_motion, MachineConfig, generate_gcode


class _Worker(QObject):
    """Arka planda yol hesaplaması."""
    finished = Signal(object, object, object)  # (WindingPath, profile, all_layer_paths_or_None)
    error = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self._fn = fn
        self._args = args

    def run(self):
        try:
            result = self._fn(*self._args)
            self.finished.emit(*result)
        except Exception as e:
            self.error.emit(str(e))


class CAMPanel(QWidget):
    """Ana CAM paneli — filament sarma yolu + G-code üretici."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._winding_path = None
        self._gcode_program = None
        self._stl_path: Optional[str] = None
        self._worker_thread: Optional[QThread] = None
        self._stack_dict: Optional[dict] = None         # Manuel Dizilim katman yığını
        self._all_layer_paths: Optional[list] = None    # [(layer_dict, WindingPath), ...]
        self._build_ui()

    # ── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        main = QHBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)

        # Sol: 3D görüntüleyici
        self._viewer = Winding3DPanel()
        splitter.addWidget(self._viewer)

        # Sağ: sekmeli kontrol paneli
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(6)

        title = QLabel("CAM Üretici")
        title.setProperty("role", "header")
        right_layout.addWidget(title)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_params_tab(), "Geometri & Parametreler")
        self._tabs.addTab(self._build_gcode_tab(), "G-code & Çıktı")
        right_layout.addWidget(self._tabs, stretch=1)

        splitter.addWidget(right)
        splitter.setSizes([500, 400])
        main.addWidget(splitter)

    def _build_params_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)

        # ── Mandrel geometrisi ───────────────────────────────────────────
        geo_box = QGroupBox("Mandrel Geometrisi")
        geo_grid = QGridLayout(geo_box)
        geo_grid.setSpacing(6)
        row = 0

        geo_grid.addWidget(QLabel("Mandrel tipi:"), row, 0)
        self._mandrel_type = QComboBox()
        self._mandrel_type.addItems(["Silindir", "Konik", "Kubbeli Silindir", "STL'den"])
        self._mandrel_type.currentTextChanged.connect(self._on_mandrel_type_changed)
        geo_grid.addWidget(self._mandrel_type, row, 1, 1, 2); row += 1

        self._stl_btn = QPushButton("STL Dosyası Yükle…")
        self._stl_btn.setEnabled(False)
        self._stl_btn.clicked.connect(self._pick_stl)
        self._stl_lbl = QLabel("Dosya seçilmedi")
        self._stl_lbl.setProperty("role", "caption")
        geo_grid.addWidget(self._stl_btn, row, 0, 1, 1)
        geo_grid.addWidget(self._stl_lbl, row, 1, 1, 2); row += 1

        geo_grid.addWidget(QLabel("Çap (mm):"), row, 0)
        self._diameter = QDoubleSpinBox()
        self._diameter.setRange(10.0, 2000.0); self._diameter.setValue(100.0)
        self._diameter.setSuffix(" mm"); self._diameter.setDecimals(1)
        geo_grid.addWidget(self._diameter, row, 1, 1, 2); row += 1

        geo_grid.addWidget(QLabel("Uzunluk (mm):"), row, 0)
        self._length = QDoubleSpinBox()
        self._length.setRange(10.0, 5000.0); self._length.setValue(300.0)
        self._length.setSuffix(" mm"); self._length.setDecimals(1)
        geo_grid.addWidget(self._length, row, 1, 1, 2); row += 1

        geo_grid.addWidget(QLabel("Konik açı (°):"), row, 0)
        self._cone_angle = QDoubleSpinBox()
        self._cone_angle.setRange(0.0, 45.0); self._cone_angle.setValue(5.0)
        self._cone_angle.setSuffix(" °"); self._cone_angle.setDecimals(1)
        self._cone_angle.setEnabled(False)
        geo_grid.addWidget(self._cone_angle, row, 1, 1, 2); row += 1

        geo_grid.addWidget(QLabel("Kubbe yüksekliği (mm):"), row, 0)
        self._dome_h = QDoubleSpinBox()
        self._dome_h.setRange(0.0, 500.0); self._dome_h.setValue(50.0)
        self._dome_h.setSuffix(" mm"); self._dome_h.setDecimals(1)
        self._dome_h.setEnabled(False)
        geo_grid.addWidget(self._dome_h, row, 1, 1, 2); row += 1

        layout.addWidget(geo_box)

        # ── Katman yığını bilgi çubuğu ───────────────────────────────────
        self._stack_info_lbl = QLabel("Parametrik mod  (Manuel Dizilim'den yığın bekleniyor)")
        self._stack_info_lbl.setStyleSheet(
            f"color: {COLOR['text_secondary']}; padding: 4px 8px; "
            f"background: {COLOR['bg_widget']}; border-radius: 3px; font-style: italic;"
        )
        self._stack_info_lbl.setWordWrap(True)
        layout.addWidget(self._stack_info_lbl)

        # ── Sarma parametreleri ──────────────────────────────────────────
        wind_box = QGroupBox("Sarma Parametreleri")
        wind_grid = QGridLayout(wind_box)
        wind_grid.setSpacing(6)
        row = 0

        wind_grid.addWidget(QLabel("Sarma açısı (α°):"), row, 0)
        self._alpha = QDoubleSpinBox()
        self._alpha.setRange(1.0, 89.0); self._alpha.setValue(55.0)
        self._alpha.setSuffix(" °"); self._alpha.setDecimals(1)
        self._alpha.setToolTip("Manuel Dizilim aktifken bu değer yok sayılır")
        wind_grid.addWidget(self._alpha, row, 1); row += 1

        wind_grid.addWidget(QLabel("Kat sayısı:"), row, 0)
        self._n_layers = QSpinBox()
        self._n_layers.setRange(1, 32); self._n_layers.setValue(4)
        self._n_layers.setToolTip("Manuel Dizilim aktifken bu değer yok sayılır")
        wind_grid.addWidget(self._n_layers, row, 1); row += 1

        wind_grid.addWidget(QLabel("Fitil genişliği (mm):"), row, 0)
        self._tow_w = QDoubleSpinBox()
        self._tow_w.setRange(0.5, 50.0); self._tow_w.setValue(6.0)
        self._tow_w.setSuffix(" mm"); self._tow_w.setDecimals(1)
        wind_grid.addWidget(self._tow_w, row, 1); row += 1

        wind_grid.addWidget(QLabel("Çakışma (%):"), row, 0)
        self._overlap = QDoubleSpinBox()
        self._overlap.setRange(0.0, 50.0); self._overlap.setValue(5.0)
        self._overlap.setSuffix(" %"); self._overlap.setDecimals(1)
        wind_grid.addWidget(self._overlap, row, 1); row += 1

        wind_grid.addWidget(QLabel("Strateji:"), row, 0)
        self._strategy = QComboBox()
        self._strategy.addItems(["Sarmal", "Çevre", "Kutupsal"])
        wind_grid.addWidget(self._strategy, row, 1); row += 1

        layout.addWidget(wind_box)

        # ── Makine parametreleri ─────────────────────────────────────────
        mach_box = QGroupBox("Makine Parametreleri")
        mach_grid = QGridLayout(mach_box)
        mach_grid.setSpacing(6)
        row = 0

        mach_grid.addWidget(QLabel("İlerleme hızı (mm/s):"), row, 0)
        self._feed = QDoubleSpinBox()
        self._feed.setRange(1.0, 500.0); self._feed.setValue(80.0)
        self._feed.setSuffix(" mm/s"); self._feed.setDecimals(1)
        mach_grid.addWidget(self._feed, row, 1); row += 1

        mach_grid.addWidget(QLabel("İş mili (RPM):"), row, 0)
        self._rpm = QDoubleSpinBox()
        self._rpm.setRange(1.0, 600.0); self._rpm.setValue(60.0)
        self._rpm.setSuffix(" RPM"); self._rpm.setDecimals(1)
        mach_grid.addWidget(self._rpm, row, 1); row += 1

        mach_grid.addWidget(QLabel("Taşıyıcı min (mm):"), row, 0)
        self._x_min = QDoubleSpinBox()
        self._x_min.setRange(-100.0, 0.0); self._x_min.setValue(-5.0)
        self._x_min.setSuffix(" mm"); self._x_min.setDecimals(1)
        mach_grid.addWidget(self._x_min, row, 1); row += 1

        mach_grid.addWidget(QLabel("Taşıyıcı maks (mm):"), row, 0)
        self._x_max = QDoubleSpinBox()
        self._x_max.setRange(0.0, 5000.0); self._x_max.setValue(395.0)
        self._x_max.setSuffix(" mm"); self._x_max.setDecimals(1)
        mach_grid.addWidget(self._x_max, row, 1); row += 1

        layout.addWidget(mach_box)

        # ── İşlem butonu ────────────────────────────────────────────────
        self._calc_btn = QPushButton("Yolu Hesapla")
        self._calc_btn.setMinimumHeight(36)
        self._calc_btn.clicked.connect(self._calculate_path)
        layout.addWidget(self._calc_btn)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        self._status_lbl = QLabel("Hazır.")
        self._status_lbl.setProperty("role", "caption")
        layout.addWidget(self._status_lbl)

        layout.addStretch(1)
        return tab

    def _build_gcode_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)

        # ── Makine yapılandırması ────────────────────────────────────────
        cfg_box = QGroupBox("Makine Yapılandırması")
        cfg_grid = QGridLayout(cfg_box)
        cfg_grid.setSpacing(6)
        row = 0

        cfg_grid.addWidget(QLabel("Kontrolör tipi:"), row, 0)
        self._ctrl_type = QComboBox()
        self._ctrl_type.addItems(["grbl", "mach3", "fanuc", "özel"])
        cfg_grid.addWidget(self._ctrl_type, row, 1); row += 1

        cfg_grid.addWidget(QLabel("X ekseni adı:"), row, 0)
        self._x_axis_name = QComboBox()
        self._x_axis_name.addItems(["X", "Z", "W"])
        cfg_grid.addWidget(self._x_axis_name, row, 1); row += 1

        cfg_grid.addWidget(QLabel("A ekseni adı:"), row, 0)
        self._a_axis_name = QComboBox()
        self._a_axis_name.addItems(["A", "B", "C"])
        cfg_grid.addWidget(self._a_axis_name, row, 1); row += 1

        cfg_grid.addWidget(QLabel("Maks X hızı (mm/dak):"), row, 0)
        self._max_x_feed = QDoubleSpinBox()
        self._max_x_feed.setRange(100.0, 20000.0); self._max_x_feed.setValue(5000.0)
        self._max_x_feed.setSuffix(" mm/dak"); self._max_x_feed.setDecimals(0)
        cfg_grid.addWidget(self._max_x_feed, row, 1); row += 1

        layout.addWidget(cfg_box)

        # ── Hesaplanan istatistikler ─────────────────────────────────────
        stat_box = QGroupBox("Hesaplanan İstatistikler")
        stat_grid = QGridLayout(stat_box)
        stat_grid.setSpacing(4)

        self._stat_circuits = QLabel("—")
        self._stat_coverage = QLabel("—")
        self._stat_fiber = QLabel("—")
        self._stat_time = QLabel("—")
        self._stat_lines = QLabel("—")

        for i, (lbl, val) in enumerate([
            ("Devre sayısı:", self._stat_circuits),
            ("Kapsama:", self._stat_coverage),
            ("Fiber uzunluğu:", self._stat_fiber),
            ("Tahmini süre:", self._stat_time),
            ("G-code satırı:", self._stat_lines),
        ]):
            stat_grid.addWidget(QLabel(lbl), i, 0)
            stat_grid.addWidget(val, i, 1)

        layout.addWidget(stat_box)

        # ── G-code metin editörü ─────────────────────────────────────────
        gcode_box = QGroupBox("G-code Çıktısı")
        gcode_inner = QVBoxLayout(gcode_box)
        self._gcode_edit = QTextEdit()
        self._gcode_edit.setReadOnly(True)
        self._gcode_edit.setFont(self._gcode_edit.font())
        try:
            from PySide6.QtGui import QFontDatabase
            mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
            mono.setPointSize(9)
            self._gcode_edit.setFont(mono)
        except Exception:
            pass
        self._gcode_edit.setPlaceholderText(
            "G-code burada görünecek…\n"
            "Önce 'Geometri & Parametreler' sekmesinden yolu hesaplayın,\n"
            "ardından 'G-code Oluştur' butonuna tıklayın."
        )
        gcode_inner.addWidget(self._gcode_edit)
        layout.addWidget(gcode_box, stretch=1)

        # ── Aksiyon butonları ────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._gen_btn = QPushButton("G-code Oluştur")
        self._gen_btn.setMinimumHeight(34)
        self._gen_btn.clicked.connect(self._generate_gcode)
        self._copy_btn = QPushButton("Panoya Kopyala")
        self._copy_btn.setMinimumHeight(34)
        self._copy_btn.clicked.connect(self._copy_to_clipboard)
        self._save_btn = QPushButton("Dosyaya Kaydet…")
        self._save_btn.setMinimumHeight(34)
        self._save_btn.clicked.connect(self._save_to_file)
        for b in (self._gen_btn, self._copy_btn, self._save_btn):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        return tab

    # ── Olaylar ─────────────────────────────────────────────────────────────

    def _on_mandrel_type_changed(self, text: str):
        is_stl = text == "STL'den"
        is_cone = text == "Konik"
        is_dome = text == "Kubbeli Silindir"
        self._stl_btn.setEnabled(is_stl)
        self._cone_angle.setEnabled(is_cone)
        self._dome_h.setEnabled(is_dome)
        self._diameter.setEnabled(not is_stl)
        self._length.setEnabled(not is_stl)

    def _pick_stl(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "STL Dosyası Seç", "",
            "STL Dosyaları (*.stl *.STL);;Tüm Dosyalar (*)"
        )
        if path:
            self._stl_path = path
            self._stl_lbl.setText(os.path.basename(path))

    def _calculate_path(self):
        if self._mandrel_type.currentText() == "STL'den" and not self._stl_path:
            QMessageBox.warning(self, "Uyarı", "Lütfen önce bir STL dosyası seçin.")
            return
        self._calc_btn.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._status_lbl.setText("Yol hesaplanıyor…")

        self._worker_thread = QThread()
        worker = _Worker(self._do_calculate)
        worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(worker.run)
        worker.finished.connect(self._on_path_done)
        worker.error.connect(self._on_path_error)
        worker.finished.connect(self._worker_thread.quit)
        worker.error.connect(self._worker_thread.quit)
        self._worker_thread.start()
        self._worker_ref = worker  # keep reference

    def _do_calculate(self):
        MandrelProfile, WindingPathParams, generate_path, plan_motion, MachineConfig, generate_gcode = _make_backend()
        import math as _math

        mtype = self._mandrel_type.currentText()
        r_mm = self._diameter.value() / 2.0
        l_mm = self._length.value()

        if mtype == "Silindir":
            profile = MandrelProfile.cylinder(l_mm, r_mm)
        elif mtype == "Konik":
            cone_deg = self._cone_angle.value()
            r_end = r_mm + l_mm * _math.tan(_math.radians(cone_deg))
            profile = MandrelProfile.cone(l_mm, r_mm, r_end)
        elif mtype == "Kubbeli Silindir":
            profile = MandrelProfile.dome_cylinder_dome(l_mm, r_mm, self._dome_h.value())
        else:
            profile = MandrelProfile.from_stl(self._stl_path)

        # ── Çok katmanlı mod (Manuel Dizilim'den yığın geldi) ────────────────
        stack = self._stack_dict
        if stack and stack.get("layers"):
            all_layer_paths = []
            for layer in stack["layers"]:
                ltype = layer.get("layer_type") or layer.get("type", "helical")
                if ltype == "hoop":
                    strategy = "hoop"
                elif ltype == "polar":
                    strategy = "polar"
                else:
                    strategy = "helical"
                pp = WindingPathParams(
                    profile=profile,
                    alpha_deg=float(layer.get("alpha_deg", 55.0)),
                    n_layers=1,
                    tow_width_mm=float(layer.get("fitil_genisligi_mm",
                                                  self._tow_w.value())),
                    overlap_pct=float(layer.get("cakisma_pct",
                                                self._overlap.value())),
                    feed_mm_s=float(layer.get("feed_mm_s", self._feed.value())),
                    spindle_rpm=float(layer.get("spindle_rpm", self._rpm.value())),
                    winding_strategy=strategy,
                    carriage_min_mm=self._x_min.value(),
                    carriage_max_mm=self._x_max.value(),
                )
                p = generate_path(pp)
                all_layer_paths.append((layer, p))
            first_path = all_layer_paths[0][1] if all_layer_paths else None
            if first_path is None:
                raise RuntimeError("Katman yığınından yol üretilemedi.")
            return first_path, profile, all_layer_paths

        # ── Tek-açı parametrik mod ───────────────────────────────────────────
        strategy_map = {"Sarmal": "helical", "Çevre": "hoop", "Kutupsal": "polar"}
        strategy = strategy_map.get(self._strategy.currentText(), "helical")
        path_params = WindingPathParams(
            profile=profile,
            alpha_deg=self._alpha.value(),
            n_layers=self._n_layers.value(),
            tow_width_mm=self._tow_w.value(),
            overlap_pct=self._overlap.value(),
            feed_mm_s=self._feed.value(),
            spindle_rpm=self._rpm.value(),
            winding_strategy=strategy,
            carriage_min_mm=self._x_min.value(),
            carriage_max_mm=self._x_max.value(),
        )
        path = generate_path(path_params)
        return path, profile, None

    def _on_path_done(self, path, profile, all_layer_paths):
        self._winding_path = path
        self._all_layer_paths = all_layer_paths
        self._calc_btn.setEnabled(True)
        self._progress_bar.setVisible(False)
        n = len(path.points)
        n_layers_info = (
            f"{len(all_layer_paths)} katman, "
            if all_layer_paths else ""
        )
        self._status_lbl.setText(
            f"Yol hesaplandı: {n_layers_info}{n} nokta, "
            f"{path.n_circuits} devre, {path.coverage_pct:.1f}% kapsama"
        )
        # 3D önizlemeyi güncelle
        try:
            self._viewer.set_cam_path(profile, path)
        except Exception:
            pass

    def _on_path_error(self, msg: str):
        self._calc_btn.setEnabled(True)
        self._progress_bar.setVisible(False)
        self._status_lbl.setText(f"Hata: {msg}")
        QMessageBox.critical(self, "Yol Hesaplama Hatası", msg)

    def _generate_gcode(self):
        if self._winding_path is None:
            QMessageBox.information(self, "Bilgi",
                "Önce 'Geometri & Parametreler' sekmesinde yolu hesaplayın.")
            return
        try:
            _, _, _, plan_motion, MachineConfig, generate_gcode = _make_backend()
            ctrl_map = {"özel": "custom"}
            ctrl = ctrl_map.get(self._ctrl_type.currentText(),
                                self._ctrl_type.currentText())
            cfg = MachineConfig(
                x_axis=self._x_axis_name.currentText(),
                a_axis=self._a_axis_name.currentText(),
                max_x_feed_mm_min=self._max_x_feed.value(),
                controller_type=ctrl,
            )

            if self._all_layer_paths:
                # Çok katmanlı mod: her katman için ayrı G-code bloğu, sıralı birleştir
                all_lines: list = []
                total_len = 0.0
                total_time = 0.0
                total_circuits = 0
                coverage = 0.0

                for i, (layer_dict, wpath) in enumerate(self._all_layer_paths):
                    ltype = layer_dict.get("layer_type") or layer_dict.get("type", "?")
                    alpha = layer_dict.get("alpha_deg", 0.0)
                    lbl = layer_dict.get("label", f"Katman {i + 1}")
                    all_lines.append(f"; === Katman {i + 1}: {lbl} ({ltype} α={alpha:+.1f}°) ===")
                    segs = plan_motion(wpath)
                    gp_layer = generate_gcode(segs, wpath, cfg)
                    # Skip header/footer lines for middle layers
                    body = [ln for ln in gp_layer.lines
                            if not ln.startswith("G21") and not ln.startswith("G90")
                            and not ln.startswith("G28") and ln != "M30"
                            and not ln.startswith("; Filament")
                            and not ln.startswith("; Mandrel")
                            and not ln.startswith("; Sarma")
                            and not ln.startswith("; Toplam")
                            and not ln.startswith("; Tahmini")]
                    if i == 0:
                        # Keep full header for first layer
                        all_lines.extend(gp_layer.lines[:6])  # preamble
                        body = gp_layer.lines[6:]
                        body = [ln for ln in body if ln != "M30"]
                    all_lines.extend(body)
                    total_len += gp_layer.total_length_mm
                    total_time += gp_layer.estimated_time_s
                    total_circuits += gp_layer.n_circuits
                    coverage = max(coverage, gp_layer.coverage_pct)

                all_lines.append("M30  ; Program sonu")

                # Build a combined GCodeProgram-like object
                class _Combined:
                    lines = all_lines
                    n_circuits = total_circuits
                    coverage_pct = coverage
                    total_length_mm = total_len
                    estimated_time_s = total_time

                    def as_text(self):
                        return "\n".join(self.lines)

                gp = _Combined()

            else:
                # Tek-açı modu
                segments = plan_motion(self._winding_path)
                gp = generate_gcode(segments, self._winding_path, cfg)

            self._gcode_program = gp
            self._gcode_edit.setPlainText(gp.as_text())
            self._stat_circuits.setText(str(gp.n_circuits))
            self._stat_coverage.setText(f"{gp.coverage_pct:.1f}%")
            fiber_m = gp.total_length_mm / 1000.0
            self._stat_fiber.setText(f"{fiber_m:.2f} m")
            mins = int(gp.estimated_time_s / 60)
            secs = int(gp.estimated_time_s % 60)
            self._stat_time.setText(f"{mins}d {secs}s")
            self._stat_lines.setText(str(len(gp.lines)))
        except Exception as e:
            QMessageBox.critical(self, "G-code Hatası", str(e))

    def _copy_to_clipboard(self):
        text = self._gcode_edit.toPlainText()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self._status_lbl.setText("G-code panoya kopyalandı.")

    def _save_to_file(self):
        text = self._gcode_edit.toPlainText()
        if not text:
            QMessageBox.information(self, "Bilgi", "Önce G-code oluşturun.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "G-code Kaydet", "sarma_programi.nc",
            "G-code Dosyaları (*.nc *.gcode *.tap);;Tüm Dosyalar (*)"
        )
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
            self._status_lbl.setText(f"Kaydedildi: {os.path.basename(path)}")

    # ── Harici API ───────────────────────────────────────────────────────────

    def set_layer_stack(self, stack) -> None:
        """
        Manuel Dizilim'den katman yığını al.
        stack: dict (versiyon, layers) veya to_dict() metoduna sahip LayerStack.
        """
        if isinstance(stack, dict):
            self._stack_dict = stack
        elif hasattr(stack, "to_dict"):
            self._stack_dict = stack.to_dict()
        else:
            self._stack_dict = {}

        n = len((self._stack_dict or {}).get("layers", []))
        if n > 0:
            self._stack_info_lbl.setText(
                f"Manuel Dizilim aktif: {n} katman  "
                f"(α ve kat sayısı parametreleri yok sayılır)"
            )
            self._stack_info_lbl.setStyleSheet(
                f"color: {COLOR['accent_bright']}; padding: 4px 8px; "
                f"background: #1a3a1a; border-radius: 3px; font-weight: bold;"
            )
            self._alpha.setEnabled(False)
            self._n_layers.setEnabled(False)
        else:
            self._stack_dict = None
            self._stack_info_lbl.setText(
                "Parametrik mod  (Manuel Dizilim'den yığın bekleniyor)"
            )
            self._stack_info_lbl.setStyleSheet(
                f"color: {COLOR['text_secondary']}; padding: 4px 8px; "
                f"background: {COLOR['bg_widget']}; border-radius: 3px; font-style: italic;"
            )
            self._alpha.setEnabled(True)
            self._n_layers.setEnabled(True)
