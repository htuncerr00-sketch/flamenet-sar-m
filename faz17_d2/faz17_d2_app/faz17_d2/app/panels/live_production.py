"""
panels/live_production.py — Live Production Dashboard
=========================================================
Realtime telemetry dashboard.

Layout:
  TOP ROW:     Metric cards (Tension, RPM, Angle, Temp, Vib RMS, Quality)
  MIDDLE:      Tension chart | RPM chart | Temperature chart
  BOTTOM:      Vibration FFT spectrum | Health score gauge
"""
from __future__ import annotations
import math, time
from collections import deque
from typing import List, Optional

import numpy as np
from PySide6.QtCore import Qt, QTimer, Slot, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QFrame, QPushButton, QProgressBar, QSlider)

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from backend.hardware.esp32_link import TelemetryFrame

from ..widgets.metric_card import MetricCard
from ..widgets.led_indicator import LedIndicator
from ..widgets.realtime_chart import RealtimeChart, FFTChart
from ..themes.dark_industrial import COLOR, chart_pen_colors


class LiveProductionPanel(QWidget):
    """Main live dashboard. Receives coalesced telemetry batches."""

    REFRESH_HZ = 30
    HISTORY_POINTS = 1500   # 50s @ 30Hz coalesced

    # ── Üretim kontrol sinyalleri (main_window üzerinden engine'e yönlenir) ──
    sarmaBaslat  = Signal()
    sarmaDuraklat = Signal()
    sarmaDevam   = Signal()
    sarmaAcilDur  = Signal()
    sarmaSifirla  = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_batch_count = 0
        self._fps_t0 = time.monotonic()
        self._fps_frames = 0
        self._fps = 0.0
        # FFT vibration buffer
        self._vib_buf: deque = deque(maxlen=512)
        # Build UI
        self._build_ui()
        # Refresh timer (UI repaint @30Hz)
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / self.REFRESH_HZ))
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Title row with status LEDs
        title_row = QHBoxLayout()
        title_lbl = QLabel("Canlı Üretim")
        title_lbl.setProperty("role", "header")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        self._connection_led = LedIndicator("Bağlantı", "off")
        self._safety_led = LedIndicator("Güvenlik", "ok")
        self._running_led = LedIndicator("Çalışıyor", "off")
        self._fps_lbl = QLabel("Arayüz: -- FPS")
        self._fps_lbl.setProperty("role", "caption")
        title_row.addWidget(self._connection_led)
        title_row.addWidget(self._safety_led)
        title_row.addWidget(self._running_led)
        title_row.addWidget(self._fps_lbl)
        layout.addLayout(title_row)

        # ── Üretim kontrol çubuğu ──────────────────────────────────────────────
        ctrl_box = QGroupBox("Üretim Kontrolü")
        ctrl_box.setStyleSheet("QGroupBox { color: #A0C8F0; font-weight: bold; }")
        ctrl_lay = QHBoxLayout(ctrl_box)
        ctrl_lay.setSpacing(8)

        self._btn_basla = QPushButton("▶  Sarmayı Başlat")
        self._btn_basla.setMinimumHeight(38)
        self._btn_basla.setStyleSheet(
            "QPushButton{background:#1A5A1A;color:white;font-weight:bold;"
            "border:none;border-radius:4px;padding:0 14px;}"
            "QPushButton:hover{background:#267A26;}"
            "QPushButton:disabled{background:#2A2A3A;color:#606060;}")
        self._btn_basla.clicked.connect(self.sarmaBaslat)
        ctrl_lay.addWidget(self._btn_basla)

        self._btn_duraklat = QPushButton("⏸  Duraklat")
        self._btn_duraklat.setMinimumHeight(38)
        self._btn_duraklat.setEnabled(False)
        self._btn_duraklat.setStyleSheet(
            "QPushButton{background:#4A4A00;color:white;font-weight:bold;"
            "border:none;border-radius:4px;padding:0 14px;}"
            "QPushButton:hover{background:#6A6A00;}"
            "QPushButton:disabled{background:#2A2A3A;color:#606060;}")
        self._btn_duraklat.clicked.connect(self.sarmaDuraklat)
        ctrl_lay.addWidget(self._btn_duraklat)

        self._btn_devam = QPushButton("▶▶  Devam Et")
        self._btn_devam.setMinimumHeight(38)
        self._btn_devam.setEnabled(False)
        self._btn_devam.setStyleSheet(
            "QPushButton{background:#004A4A;color:white;font-weight:bold;"
            "border:none;border-radius:4px;padding:0 14px;}"
            "QPushButton:hover{background:#006A6A;}"
            "QPushButton:disabled{background:#2A2A3A;color:#606060;}")
        self._btn_devam.clicked.connect(self.sarmaDevam)
        ctrl_lay.addWidget(self._btn_devam)

        self._btn_estop = QPushButton("⚠  ACİL DURDUR")
        self._btn_estop.setMinimumHeight(38)
        self._btn_estop.setStyleSheet(
            "QPushButton{background:#8A0000;color:white;font-weight:bold;"
            "border:none;border-radius:4px;padding:0 14px;}"
            "QPushButton:hover{background:#AA0000;}")
        self._btn_estop.clicked.connect(self.sarmaAcilDur)
        ctrl_lay.addWidget(self._btn_estop)

        self._btn_sifirla = QPushButton("↺  Sıfırla")
        self._btn_sifirla.setMinimumHeight(38)
        self._btn_sifirla.setStyleSheet(
            "QPushButton{background:#252540;color:#C0C0E0;"
            "border:1px solid #3A3A5C;border-radius:4px;padding:0 14px;}"
            "QPushButton:hover{background:#3A3A60;}")
        self._btn_sifirla.clicked.connect(self.sarmaSifirla)
        ctrl_lay.addWidget(self._btn_sifirla)

        ctrl_lay.addStretch()

        self._durum_lbl = QLabel("Hazır")
        self._durum_lbl.setStyleSheet(
            "color:#A0C8F0;font-weight:bold;font-size:13px;")
        ctrl_lay.addWidget(self._durum_lbl)

        layout.addWidget(ctrl_box)

        # ── İlerleme çubuğu ────────────────────────────────────────────────────
        prog_box = QGroupBox("Sarma İlerlemesi")
        prog_box.setStyleSheet("QGroupBox { color: #A0C8F0; font-weight: bold; }")
        prog_lay = QHBoxLayout(prog_box)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 1000)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%p%")
        self._progress_bar.setMinimumHeight(22)
        self._progress_bar.setStyleSheet(
            "QProgressBar{background:#1A1A2E;border:1px solid #3A3A5C;"
            "color:#E8E8E8;text-align:center;}"
            "QProgressBar::chunk{background:qlineargradient("
            "x1:0,y1:0,x2:1,y2:0,stop:0 #1A5A8A,stop:1 #30A0E0);}")
        prog_lay.addWidget(self._progress_bar, stretch=4)

        self._prog_info_lbl = QLabel("—  Katman: —/—  Devre: —/—  Hız: —")
        self._prog_info_lbl.setStyleSheet("color:#A0C8F0;font-size:11px;")
        prog_lay.addWidget(self._prog_info_lbl, stretch=3)

        layout.addWidget(prog_box)

        # Metric cards row
        metric_row = QHBoxLayout()
        metric_row.setSpacing(6)
        self._tension_card = MetricCard("Gerilim", "N", "hedef 15.0", large=True)
        self._rpm_card = MetricCard("RPM", "rpm", "iş mili", large=True)
        self._x_card = MetricCard("X Konumu", "mm", "taşıyıcı", large=True)
        self._angle_card = MetricCard("Açı", "°", "sarma")
        self._temp_card = MetricCard("Sıcaklık", "°C", "proses")
        self._vib_card = MetricCard("Titreşim", "g RMS", "3-eksen")
        self._quality_card = MetricCard("Kalite", "", "tahmin")
        for card in (self._tension_card, self._rpm_card, self._x_card,
                     self._angle_card, self._temp_card, self._vib_card,
                     self._quality_card):
            metric_row.addWidget(card)
        layout.addLayout(metric_row)

        # Charts grid
        charts_grid = QGridLayout()
        charts_grid.setSpacing(6)
        pens = chart_pen_colors()

        self._tension_chart = RealtimeChart(
            "Gerilim (N)", "N", y_range=(0, 40),
            max_points=self.HISTORY_POINTS, pen_colors=pens)
        self._tension_chart.add_series("tension", color=pens["tension"])

        self._rpm_chart = RealtimeChart(
            "RPM", "rpm", y_range=None,
            max_points=self.HISTORY_POINTS, pen_colors=pens)
        self._rpm_chart.add_series("rpm", color=pens["rpm"])

        self._temp_chart = RealtimeChart(
            "Sıcaklık (°C)", "°C", y_range=None,
            max_points=self.HISTORY_POINTS, pen_colors=pens)
        self._temp_chart.add_series("temp", color=pens["temp"])

        self._fft_chart = FFTChart("Titreşim FFT (Z-ekseni)")

        charts_grid.addWidget(self._tension_chart, 0, 0)
        charts_grid.addWidget(self._rpm_chart, 0, 1)
        charts_grid.addWidget(self._temp_chart, 1, 0)
        charts_grid.addWidget(self._fft_chart, 1, 1)
        layout.addLayout(charts_grid)

    @Slot(list)
    def on_frame_batch(self, batch: list):
        """Receive coalesced batch from TelemetryWorker."""
        if not batch:
            return
        self._last_batch_count += len(batch)
        for f in batch:
            t = f.ts_us / 1e6
            self._tension_chart.append("tension", t, f.T_N)
            self._rpm_chart.append("rpm", t, f.rpm)
            self._temp_chart.append("temp", t, f.temp_K - 273.15)
            self._vib_buf.append(f.vib_z)

    @Slot(object)
    def on_latest_frame(self, f: TelemetryFrame):
        """Receive single latest frame for metric cards."""
        # Cards
        self._tension_card.set_value(f.T_N, "{:.2f}")
        self._tension_card.set_status(
            "ok" if 12 <= f.T_N <= 18 else
            "warn" if 3 < f.T_N < 38 else "crit")

        self._rpm_card.set_value(f.rpm, "{:.1f}")
        self._rpm_card.set_status("ok" if f.rpm < 260 else "crit")

        self._x_card.set_value(f.x_mm, "{:.2f}")
        self._x_card.set_status(
            "ok" if -5 <= f.x_mm <= 395 else "crit")

        self._angle_card.set_value(f.a_deg % 360, "{:.1f}")

        temp_C = f.temp_K - 273.15
        self._temp_card.set_value(temp_C, "{:.1f}")
        self._temp_card.set_status(
            "ok" if temp_C < 150 else
            "warn" if temp_C < 220 else "crit")

        vib_rms = math.sqrt(f.vib_x**2 + f.vib_y**2 + f.vib_z**2)
        self._vib_card.set_value(vib_rms, "{:.3f}")
        self._vib_card.set_status(
            "ok" if vib_rms < 0.5 else
            "warn" if vib_rms < 2.0 else "crit")

        self._quality_card.set_value(f.quality, "{:.1f}")
        self._quality_card.set_status(
            "ok" if f.quality > 85 else
            "warn" if f.quality > 70 else "crit")

    @Slot(dict)
    def on_production_status(self, d: dict) -> None:
        """Motor durum paketini ilerleme çubuğu + bilgi etiketine yaz."""
        pct = float(d.get("yuzde", 0.0))
        self._progress_bar.setValue(int(pct * 10))   # 0..1000

        katman  = d.get("katman", "—")
        t_kat   = d.get("toplam_katman", "—")
        devre   = d.get("devre", "—")
        t_dev   = d.get("toplam_devre", "—")
        hiz     = d.get("hiz_mm_dak", 0.0)
        gecen   = d.get("gecen_s", 0.0)
        toplam  = d.get("toplam_s", 0.0)

        # Kalan süre tahmini
        kalan_s = max(0.0, toplam - gecen)
        dk, sn = divmod(int(kalan_s), 60)
        kalan_str = f"{dk}d {sn:02d}s" if dk else f"{sn}s"

        self._prog_info_lbl.setText(
            f"{pct:.1f}%  Katman: {katman}/{t_kat}  "
            f"Devre: {devre}/{t_dev}  "
            f"Hız: {hiz:.0f} mm/dak  "
            f"Kalan: {kalan_str}")

    @Slot(str)
    def on_production_state(self, durum: str) -> None:
        """Durum adı değişiminde butonları güncelle."""
        self._durum_lbl.setText(durum)
        calisiyor   = (durum == "Sarılıyor")
        duraklatildi = (durum == "Duraklatıldı")
        acil_durdu  = (durum == "ACİL DURDU")
        tamamlandi  = (durum == "Tamamlandı")
        hazir       = (durum == "Hazır")

        self._btn_basla.setEnabled(hazir or tamamlandi or acil_durdu)
        self._btn_duraklat.setEnabled(calisiyor)
        self._btn_devam.setEnabled(duraklatildi)

        color = {
            "Sarılıyor":   "#50FF50",
            "Duraklatıldı": "#FFB050",
            "ACİL DURDU":  "#FF5050",
            "Tamamlandı":  "#50A0FF",
        }.get(durum, "#A0C8F0")
        self._durum_lbl.setStyleSheet(
            f"color:{color};font-weight:bold;font-size:13px;")
        self.set_running_status(calisiyor)

    @Slot(int)
    def on_connection_state(self, state: int):
        statuses = {0: "off", 1: "warn", 2: "ok", 3: "crit"}
        names = {0: "Bağlı değil", 1: "Bağlanıyor", 2: "Bağlandı", 3: "Hata"}
        self._connection_led.set_status(statuses.get(state, "off"))
        self._connection_led.set_text(f"Bağlantı: {names.get(state, '?')}")

    def set_safety_status(self, status: str):
        self._safety_led.set_status(status)

    def set_running_status(self, running: bool):
        self._running_led.set_status("ok" if running else "off")

    def _refresh(self):
        """UI thread refresh — repaint charts."""
        self._tension_chart.refresh()
        self._rpm_chart.refresh()
        self._temp_chart.refresh()

        # FFT (Welch-like simple spectrum)
        if len(self._vib_buf) >= 128:
            arr = np.array(self._vib_buf)
            arr = arr - arr.mean()
            win = np.hanning(len(arr))
            spec = np.abs(np.fft.rfft(arr * win)) / len(arr)
            freqs = np.fft.rfftfreq(len(arr), d=1/1000.0)   # assume 1kHz
            # Display first 100Hz only
            mask = freqs < 100
            self._fft_chart.update_spectrum(freqs[mask], spec[mask] * 2)

        # FPS counter
        self._fps_frames += 1
        elapsed = time.monotonic() - self._fps_t0
        if elapsed > 1.0:
            self._fps = self._fps_frames / elapsed
            self._fps_lbl.setText(f"Arayüz: {self._fps:.1f} FPS")
            self._fps_frames = 0
            self._fps_t0 = time.monotonic()

    def current_fps(self) -> float:
        return self._fps
