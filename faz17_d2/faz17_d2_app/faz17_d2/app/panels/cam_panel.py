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
import logging
from typing import Optional

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QObject
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QGroupBox, QLabel, QComboBox, QPushButton,
    QDoubleSpinBox, QSpinBox, QFileDialog, QTextEdit,
    QGridLayout, QApplication, QMessageBox, QProgressBar,
    QFrame, QSizePolicy,
)

from .winding_3d import Winding3DPanel
from ..themes.dark_industrial import COLOR
from ..cam_engine import CamRequest
import app.cam_engine as _eng

log = logging.getLogger("faz17_d2.cam_panel")


class _Worker(QObject):
    """Arka planda yol hesaplaması (R1: yalnızca düz CamRequest alır).

    `gen` (nesil), sinyalin İÇİNDE taşınır — böylece slot'lar bound-method
    olarak doğrudan bağlanabilir (QueuedConnection → ANA THREAD'de çalışır).
    Lambda sarmalayıcı KULLANILMAZ: lambda'nın QObject afinitesi olmadığından
    Qt DirectConnection'a düşer ve slot worker thread'de çalışırdı (R1 ihlali).
    """
    finished = Signal(object, object, object, int)  # (path, model, all_layer_paths, gen)
    error = Signal(str, int)                         # (msg, gen)

    def __init__(self, fn, params: CamRequest, gen: int):
        super().__init__()
        self._fn = fn
        self._params = params
        self._gen = gen

    def run(self):
        log.info("[CAM] worker started (gen=%d)", self._gen)
        try:
            path, profile, all_layer_paths = self._fn(self._params)
            self.finished.emit(path, profile, all_layer_paths, self._gen)
        except Exception as e:
            log.exception("[CAM] worker error")
            self.error.emit(f"{type(e).__name__}: {e}", self._gen)


class CAMPanel(QWidget):
    """Ana CAM paneli — filament sarma yolu + G-code üretici."""

    _WATCHDOG_MS = 30_000   # R2: 30 sn worker zaman aşımı

    def __init__(self, parent=None):
        super().__init__(parent)
        self._winding_path = None
        self._mandrel_model = None                       # S2: MandrelModel (güven + profil)
        self._gcode_program = None
        self._stl_path: Optional[str] = None
        self._worker_thread: Optional[QThread] = None
        self._worker_ref: Optional[_Worker] = None
        self._watchdog: Optional[QTimer] = None
        self._calc_gen: int = 0                          # nesil sayacı (R2 watchdog)
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

    def _collect_params(self) -> CamRequest:
        """R1: TÜM widget değerlerini ANA THREAD'de CamRequest'e kopyala.

        stack_dict'in derin kopyası geçilir — worker okurken ana thread
        `set_layer_stack` ile aynı sözlüğü değiştirse bile yarış olmaz.
        """
        import copy
        sd = self._stack_dict
        stack_copy = copy.deepcopy(sd) if sd else None
        return CamRequest(
            mandrel_type=self._mandrel_type.currentText(),
            diameter_mm=self._diameter.value(),
            length_mm=self._length.value(),
            cone_angle_deg=self._cone_angle.value(),
            dome_h_mm=self._dome_h.value(),
            stl_path=self._stl_path,
            alpha_deg=self._alpha.value(),
            n_layers=self._n_layers.value(),
            tow_w_mm=self._tow_w.value(),
            overlap_pct=self._overlap.value(),
            strategy=self._strategy.currentText(),
            feed_mm_s=self._feed.value(),
            rpm=self._rpm.value(),
            x_min=self._x_min.value(),
            x_max=self._x_max.value(),
            stack_dict=stack_copy,
        )

    def _calculate_path(self):
        if self._mandrel_type.currentText() == "STL'den" and not self._stl_path:
            QMessageBox.warning(self, "Uyarı", "Lütfen önce bir STL dosyası seçin.")
            return
        # R4: Re-entrancy guard — süren bir hesap varsa yeni istek yok say
        if self._worker_thread is not None and self._worker_thread.isRunning():
            log.warning("[CAM] hesaplama zaten sürüyor; yeni istek yok sayıldı")
            return

        # R1: tüm widget okumaları ANA THREAD'de burada toplanır
        params = self._collect_params()
        mode = "çok-katman" if params.stack_dict else "parametrik"
        n_layers = len((params.stack_dict or {}).get("layers", []))
        log.info("[CAM] Yolu Hesapla: params received "
                 "(mandrel=%s, mod=%s, %d katman)",
                 params.mandrel_type, mode, n_layers)

        self._calc_gen += 1
        gen = self._calc_gen
        self._calc_btn.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._status_lbl.setText("Yol hesaplanıyor…")

        thread = QThread(self)
        worker = _Worker(self._do_calculate, params, gen)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Bound-method bağlantı → QueuedConnection → slot ANA THREAD'de çalışır.
        # gen sinyalin içinde taşınır (lambda yok → DirectConnection riski yok).
        worker.finished.connect(self._on_path_done)
        worker.error.connect(self._on_path_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        # R4: güvenli yaşam döngüsü — thread bitince C++ nesneleri silinir
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_cleanup)
        self._worker_thread = thread
        self._worker_ref = worker

        self._start_watchdog(gen)   # R2: 30 sn watchdog
        thread.start()

    # ── R2/R4: watchdog + thread yaşam döngüsü yardımcıları ──────────────────

    def _start_watchdog(self, gen: int) -> None:
        self._stop_watchdog()
        wd = QTimer(self)
        wd.setSingleShot(True)
        wd.timeout.connect(lambda g=gen: self._on_calc_timeout(g))
        wd.start(self._WATCHDOG_MS)
        self._watchdog = wd

    def _stop_watchdog(self) -> None:
        if self._watchdog is not None:
            self._watchdog.stop()
            self._watchdog.deleteLater()
            self._watchdog = None

    def _on_thread_cleanup(self) -> None:
        """R4: thread sonlandı — Python referansları bırakılır
        (C++ nesneleri deleteLater ile temizlenir)."""
        self._worker_thread = None
        self._worker_ref = None

    def _on_calc_timeout(self, gen: int) -> None:
        """R2: worker 30 sn içinde bitmedi — UI serbest, sonuç geçersiz sayılır."""
        if gen != self._calc_gen:
            return
        if self._worker_thread is None or not self._worker_thread.isRunning():
            return
        log.error("[CAM] hesaplama zaman aşımı (%d s) — gen %d iptal",
                  self._WATCHDOG_MS // 1000, gen)
        self._calc_gen += 1   # geç gelen finished/error yok sayılsın
        self._calc_btn.setEnabled(True)
        self._progress_bar.setVisible(False)
        self._status_lbl.setText(
            f"Zaman aşımı: hesaplama {self._WATCHDOG_MS // 1000} sn içinde "
            f"tamamlanmadı, iptal edildi."
        )
        try:
            self._worker_thread.quit()
        except Exception:
            log.exception("[CAM] watchdog quit hatası")

    def _do_calculate(self, req: CamRequest):
        """R1: yalnızca düz CamRequest okunur — hiçbir Qt widget erişimi yok.

        cam_engine facade'ı üzerinden çalışır; tüm backend importları orada.
        Dönüş: (path, model, all_layer_paths)  [model = MandrelModel]
        """
        log.info("[CAM] hesaplama başlıyor: mandrel=%s D=%.1f L=%.1f alpha=%.1f "
                 "n=%d strat=%s stack=%d",
                 req.mandrel_type, req.diameter_mm, req.length_mm, req.alpha_deg,
                 req.n_layers, req.strategy,
                 len((req.stack_dict or {}).get("layers", [])))

        model = _eng.load_mandrel(req)
        path, all_layer_paths = _eng.compute_path(req, model)
        return path, model, all_layer_paths

    def _on_path_done(self, path, model, all_layer_paths, gen):
        # R2: watchdog iptaliyle geçersizleşen geç sonucu yok say
        if gen != self._calc_gen:
            log.info("[CAM] geç gelen sonuç yok sayıldı (gen %s != %s)",
                     gen, self._calc_gen)
            return
        self._stop_watchdog()
        self._winding_path = path
        self._mandrel_model = model                      # S2: MandrelModel sakla
        self._all_layer_paths = all_layer_paths
        self._calc_btn.setEnabled(True)
        self._progress_bar.setVisible(False)
        n = len(path.points)
        n_layers_info = (
            f"{len(all_layer_paths)} katman, "
            if all_layer_paths else ""
        )
        # Güven bilgisini durum çubuğuna yansıt
        conf_info = ""
        if model is not None and model.confidence is not None:
            conf_info = f" | Güven: {model.confidence.grade}"
        self._status_lbl.setText(
            f"Yol hesaplandı: {n_layers_info}{n} nokta, "
            f"{path.n_circuits} devre, {path.coverage_pct:.1f}% kapsama{conf_info}"
        )
        # 3D önizlemeyi güncelle (R7: hata artık yutulmaz, loglanır)
        try:
            profile = model.as_profile() if model is not None else None
            if profile is not None:
                self._viewer.set_cam_path(profile, path)
            log.info("[CAM] 3d updated (%d nokta)", n)
        except Exception:
            log.exception("[CAM] 3D güncelleme hatası")

    def _on_path_error(self, msg: str, gen):
        if gen != self._calc_gen:
            return
        self._stop_watchdog()
        self._calc_btn.setEnabled(True)
        self._progress_bar.setVisible(False)
        self._status_lbl.setText(f"Hata: {msg}")
        log.error("[CAM] path error: %s", msg)
        QMessageBox.critical(self, "Yol Hesaplama Hatası", msg)

    def _generate_gcode(self):
        if self._winding_path is None:
            QMessageBox.information(self, "Bilgi",
                "Önce 'Geometri & Parametreler' sekmesinde yolu hesaplayın.")
            return
        mode = "çok-katman" if self._all_layer_paths else "parametrik"
        log.info("[CAM] G-code Üret: başladı (mod=%s)", mode)
        try:
            from backend.core.gcode_postprocessor import MachineConfig
            ctrl_map = {"özel": "custom"}
            ctrl = ctrl_map.get(self._ctrl_type.currentText(),
                                self._ctrl_type.currentText())
            cfg = MachineConfig(
                x_axis=self._x_axis_name.currentText(),
                a_axis=self._a_axis_name.currentText(),
                max_x_feed_mm_min=self._max_x_feed.value(),
                controller_type=ctrl,
            )

            gp = _eng.compute_gcode(
                self._winding_path, cfg, self._all_layer_paths)

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
            log.info("[CAM] gcode generated (%d satır, %d devre)",
                     len(gp.lines), gp.n_circuits)
        except Exception as e:
            log.exception("[CAM] gcode üretim hatası")
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
        # R8: stack_dict'in NEREDEN geldiğini ve neden boşaldığını izle
        if isinstance(stack, dict):
            self._stack_dict = stack
            log.info("[CAM] set_layer_stack: dict alındı (%d katman)",
                     len(stack.get("layers", [])))
        elif hasattr(stack, "to_dict"):
            self._stack_dict = stack.to_dict()
            log.info("[CAM] set_layer_stack: LayerStack nesnesi alındı (%d katman)",
                     len((self._stack_dict or {}).get("layers", [])))
        else:
            self._stack_dict = {}
            log.warning("[CAM] set_layer_stack: beklenmeyen tip %s — boş yığın varsayıldı",
                        type(stack).__name__)

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
            log.info("[CAM] set_layer_stack: çok-katmanlı mod aktif (%d katman)", n)
        else:
            # R8: BURASI stack_dict'in None'a düştüğü tek nokta —
            # gelen yığının layers'ı boş ya da tip uyumsuz → parametrik moda dönülür
            log.info("[CAM] set_layer_stack: layers boş → parametrik moda "
                     "dönüldü (stack_dict None'a çekildi)")
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
