"""
panels/replay.py — Telemetry Replay Panel
=============================================
Timeline scrubber, playback controls, embedded charts that mirror live panel.

Modes: PAUSE / REALTIME (1x) / FAST (10x) / STEP / SEEK
"""
from __future__ import annotations
from typing import Optional, List

from PySide6.QtCore import Qt, Slot, Signal, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QGridLayout, QLabel, QPushButton, QSlider, QComboBox, QListWidget,
    QListWidgetItem, QMessageBox, QSplitter, QDoubleSpinBox)

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from backend.hardware.esp32_link import TelemetryFrame
from backend.persistence.telemetry_db import TelemetryDB

from ..widgets.realtime_chart import RealtimeChart
from ..widgets.metric_card import MetricCard
from ..themes.dark_industrial import COLOR, chart_pen_colors
from ..workers.replay_worker import ReplayWorker, ReplayMode


class ReplayPanel(QWidget):
    """Telemetry session replay UI."""

    REFRESH_HZ = 30

    def __init__(self, telemetry_db: Optional[TelemetryDB] = None, parent=None):
        super().__init__(parent)
        self._db = telemetry_db
        self._worker: Optional[ReplayWorker] = None
        self._total_frames = 0
        self._build_ui()
        self._refresh_session_list()
        # Chart refresh timer
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / self.REFRESH_HZ))
        self._timer.timeout.connect(self._refresh_charts)
        self._timer.start()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        title = QLabel("Telemetri Tekrarı")
        title.setProperty("role", "header")
        layout.addWidget(title)

        # Session selector row
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Oturum:"))
        self._session_combo = QComboBox()
        self._session_combo.setMinimumWidth(280)
        sel_row.addWidget(self._session_combo)
        refresh_btn = QPushButton("Yenile")
        refresh_btn.clicked.connect(self._refresh_session_list)
        sel_row.addWidget(refresh_btn)
        load_btn = QPushButton("Yükle")
        load_btn.setProperty("role", "primary")
        load_btn.clicked.connect(self._on_load)
        sel_row.addWidget(load_btn)
        sel_row.addStretch()
        layout.addLayout(sel_row)

        # Info row
        info_row = QHBoxLayout()
        self._n_frames_card = MetricCard("Kareler", "", "toplam")
        self._duration_card = MetricCard("Süre", "s", "oturum uzunluğu")
        self._position_card = MetricCard("Konum", "", "mevcut kare")
        self._mode_card = MetricCard("Mod", "", "oynatma")
        self._mode_card.set_value("DURAKLAT")
        info_row.addWidget(self._n_frames_card)
        info_row.addWidget(self._duration_card)
        info_row.addWidget(self._position_card)
        info_row.addWidget(self._mode_card)
        layout.addLayout(info_row)

        # Playback controls
        ctrl_row = QHBoxLayout()
        self._play_btn = QPushButton("▶  Oynat")
        self._play_btn.clicked.connect(self._on_play)
        self._pause_btn = QPushButton("⏸  Duraklat")
        self._pause_btn.clicked.connect(self._on_pause)
        self._step_btn = QPushButton("⏭  Adım")
        self._step_btn.clicked.connect(self._on_step)
        self._stop_btn = QPushButton("⏹  Durdur")
        self._stop_btn.clicked.connect(self._on_stop)
        ctrl_row.addWidget(self._play_btn)
        ctrl_row.addWidget(self._pause_btn)
        ctrl_row.addWidget(self._step_btn)
        ctrl_row.addWidget(self._stop_btn)

        ctrl_row.addWidget(QLabel("  Hız:"))
        self._speed_combo = QComboBox()
        self._speed_combo.addItems(["0.5x", "1x", "2x", "5x", "10x", "50x"])
        self._speed_combo.setCurrentText("1x")
        self._speed_combo.currentTextChanged.connect(self._on_speed_changed)
        ctrl_row.addWidget(self._speed_combo)

        ctrl_row.addWidget(QLabel("  Mod:"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["realtime", "fast"])
        self._mode_combo.currentTextChanged.connect(self._on_mode_changed)
        ctrl_row.addWidget(self._mode_combo)

        ctrl_row.addStretch()
        layout.addLayout(ctrl_row)

        # Timeline scrubber
        timeline_row = QHBoxLayout()
        self._scrubber = QSlider(Qt.Horizontal)
        self._scrubber.setRange(0, 0)
        self._scrubber.setEnabled(False)
        self._scrubber.sliderMoved.connect(self._on_seek)
        self._scrubber.sliderReleased.connect(self._on_seek_released)
        timeline_row.addWidget(self._scrubber)
        self._scrub_lbl = QLabel("0 / 0")
        self._scrub_lbl.setMinimumWidth(120)
        timeline_row.addWidget(self._scrub_lbl)
        layout.addLayout(timeline_row)

        # Replay charts
        charts_grid = QGridLayout()
        charts_grid.setSpacing(6)
        pens = chart_pen_colors()
        self._tension_chart = RealtimeChart(
            "Tension (N)", "N", y_range=(0, 40), max_points=2000, pen_colors=pens)
        self._tension_chart.add_series("tension")
        self._rpm_chart = RealtimeChart(
            "RPM", "rpm", max_points=2000, pen_colors=pens)
        self._rpm_chart.add_series("rpm")
        charts_grid.addWidget(self._tension_chart, 0, 0)
        charts_grid.addWidget(self._rpm_chart, 0, 1)
        layout.addLayout(charts_grid, stretch=1)

    def _refresh_session_list(self):
        self._session_combo.clear()
        if self._db is None: return
        for s in self._db.list_sessions():
            duration = s['end_time'] - s['start_time']
            label = f"{s['session_id']} — {s['n_frames']} frames, {duration:.1f}s"
            self._session_combo.addItem(label, userData=s['session_id'])

    def _on_load(self):
        if self._db is None:
            QMessageBox.warning(self, "DB Yok", "Telemetri veritabanı bağlı değil.")
            return
        if self._session_combo.count() == 0: return
        session_id = self._session_combo.currentData()
        if not session_id: return

        # Stop existing worker
        self._cleanup_worker()

        # Create new worker
        self._worker = ReplayWorker()
        self._worker.frameBatch.connect(self._on_frame_batch)
        self._worker.positionChanged.connect(self._on_position)
        self._worker.progressUpdated.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)

        n = self._worker.load_session(self._db, session_id)
        if n == 0:
            QMessageBox.warning(self, "Boş", "Oturumda kare yok.")
            return
        self._total_frames = n
        self._scrubber.setRange(0, max(0, n - 1))
        self._scrubber.setEnabled(True)
        self._n_frames_card.set_value(n, "{:d}")

        # Compute duration from first/last ts
        if n >= 2:
            f0 = self._worker._frames[0]
            f1 = self._worker._frames[-1]
            dur_s = (f1.ts_us - f0.ts_us) / 1e6
            self._duration_card.set_value(dur_s, "{:.1f}")

        self._tension_chart.clear_series()
        self._rpm_chart.clear_series()

        self._worker.set_mode("pause")
        self._worker.start()
        self._mode_card.set_value("DURAKLAT")

    def _cleanup_worker(self):
        if self._worker is not None:
            try:
                self._worker.stop()
                self._worker.wait(2000)
            except Exception:
                pass
            self._worker = None

    def _on_play(self):
        if self._worker is None: return
        mode = self._mode_combo.currentText()
        self._worker.set_mode(mode)
        self._mode_card.set_value("OYNAT")

    def _on_pause(self):
        if self._worker is None: return
        self._worker.set_mode("pause")
        self._mode_card.set_value("DURAKLAT")

    def _on_step(self):
        if self._worker is None: return
        self._worker.set_mode("step")
        self._mode_card.set_value("ADIM")

    def _on_stop(self):
        if self._worker is None: return
        self._worker.set_mode("pause")
        self._worker.seek(0)
        self._mode_card.set_value("DURDUR")

    def _on_speed_changed(self, text):
        if self._worker is None: return
        try:
            speed = float(text.replace("x", ""))
            self._worker.set_speed(speed)
        except ValueError:
            pass

    def _on_mode_changed(self, text):
        if self._worker is None: return
        self._worker.set_mode(text)
        self._mode_card.set_value("OYNAT")

    def _on_seek(self, val):
        # Live preview during drag — just update label
        self._scrub_lbl.setText(f"{val} / {self._total_frames}")

    def _on_seek_released(self):
        if self._worker is None: return
        self._worker.seek(self._scrubber.value())

    @Slot(list)
    def _on_frame_batch(self, batch: list):
        for f in batch:
            t = f.ts_us / 1e6
            self._tension_chart.append("tension", t, f.T_N)
            self._rpm_chart.append("rpm", t, f.rpm)

    @Slot(int)
    def _on_position(self, idx: int):
        self._position_card.set_value(idx, "{:d}")
        # Update scrubber WITHOUT triggering seek
        self._scrubber.blockSignals(True)
        self._scrubber.setValue(idx)
        self._scrubber.blockSignals(False)
        self._scrub_lbl.setText(f"{idx} / {self._total_frames}")

    @Slot(float)
    def _on_progress(self, frac: float):
        pass

    @Slot()
    def _on_finished(self):
        self._mode_card.set_value("BİTTİ")

    def _refresh_charts(self):
        self._tension_chart.refresh()
        self._rpm_chart.refresh()

    def shutdown(self):
        """Called by main window during close."""
        self._cleanup_worker()
