"""
panels/commissioning.py — Commissioning & Calibration Panel
================================================================
Operator workflows:
  - Connect / Home
  - Manual jog (X, A axes)
  - Tension calibration (tare + apply known weight)
  - Encoder verification
  - E-stop test
  - FAT/SAT checklist tracking
"""
from __future__ import annotations
from typing import Optional, List
from PySide6.QtCore import Qt, Slot, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QGridLayout, QLabel, QPushButton, QDoubleSpinBox, QSpinBox,
    QListWidget, QListWidgetItem, QFrame, QCheckBox, QPlainTextEdit,
    QSizePolicy)

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from backend.core.motion_controller import MotionController, MotionState
from backend.hardware.esp32_link import ESP32LinkBase, ConnectionState

from ..widgets.metric_card import MetricCard
from ..widgets.led_indicator import LedIndicator
from ..themes.dark_industrial import COLOR


FAT_CHECKLIST = [
    "E-dur işlevi: 100ms içinde durur",
    "STO rölesi sürücü etkinleştirmesini keser",
    "X ekseni hareketi 0..395mm (±0.5mm)",
    "X ekseni tekrarlanabilirliği ±0.05mm",
    "A ekseni 360° (±0.1°)",
    "A ekseni RPM 0-60 düzgün",
    "Enkoder X çözünürlüğü 3.125µm",
    "Enkoder A çözünürlüğü 0.045°/pulse",
    "Fiber olmadan gerilim sıfır",
    "Gerilim kalibrasyonu 5/10/15N (±%2)",
    "İş mili senkr. faz hatası <1°",
    "Yazılım limitleri X -5/+395mm'de durur",
    "Donanım limitleri X anahtarları devreye girer",
    "CAN kalp atışı %100 @ 10ms",
    "Telemetri CRC hata oranı <%0.1",
    "Termal güvenlik dT/dt>10°C/s'de durur",
    "Kesinti sonrası oturum devam eder",
    "OTA firmware yükleme + doğrulama",
    "Kuru-çalışma sarma deseni TAMAM",
    "İlk ıslak sarma 15N±1N",
]


class CommissioningPanel(QWidget):
    """Commissioning + manual jog + calibration."""

    connectRequested = Signal()
    disconnectRequested = Signal()
    homeRequested = Signal()
    jogRequested = Signal(str, float, float)  # axis, distance, feed
    estopRequested = Signal()
    tareRequested = Signal()
    portChangeRequested = Signal(str, int)   # port, baud

    def __init__(self, motion: Optional[MotionController] = None,
                 link: Optional[ESP32LinkBase] = None, parent=None):
        super().__init__(parent)
        self._motion = motion
        self._link = link
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        title = QLabel("Devreye Alma & Kalibrasyon")
        title.setProperty("role", "header")
        layout.addWidget(title)

        # Top row: connection + position cards
        top_row = QHBoxLayout()
        conn_box = QGroupBox("Bağlantı")
        conn_layout = QVBoxLayout(conn_box)
        self._conn_led = LedIndicator("Link", "off")
        conn_layout.addWidget(self._conn_led)

        # Port picker row (bring-up support)
        from PySide6.QtWidgets import QComboBox, QLineEdit
        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Port:"))
        self._port_edit = QLineEdit("/dev/ttyUSB0")
        self._port_edit.setMinimumWidth(140)
        port_row.addWidget(self._port_edit, stretch=1)
        port_row.addWidget(QLabel("Baud:"))
        self._baud_combo = QComboBox()
        for b in (115200, 230400, 460800, 921600, 1500000, 2000000):
            self._baud_combo.addItem(str(b), b)
        self._baud_combo.setCurrentText("921600")
        port_row.addWidget(self._baud_combo)
        conn_layout.addLayout(port_row)

        conn_btn_row = QHBoxLayout()
        self._connect_btn = QPushButton("Bağlan")
        self._connect_btn.setProperty("role", "primary")
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        self._disconnect_btn = QPushButton("Bağlantıyı Kes")
        self._disconnect_btn.clicked.connect(self.disconnectRequested.emit)
        conn_btn_row.addWidget(self._connect_btn)
        conn_btn_row.addWidget(self._disconnect_btn)
        conn_layout.addLayout(conn_btn_row)
        self._home_btn = QPushButton("$H — Tüm eksenleri başlat")
        self._home_btn.clicked.connect(self.homeRequested.emit)
        conn_layout.addWidget(self._home_btn)
        self._state_led = LedIndicator("State", "off")
        conn_layout.addWidget(self._state_led)

        # Link diagnostics — live counters for bring-up debugging
        diag_grid = QGridLayout()
        self._diag_labels = {}
        for i, (key, label) in enumerate([
                ("frames",     "Tamam kareler:"),
                ("crc",        "CRC hataları:"),
                ("sync",       "Senkr. hataları:"),
                ("partial",    "Kısmi pkt:"),
                ("watchdog",   "Bekçi sayısı:"),
                ("reconnect",  "Yeniden bağlanma:"),
                ("byte_in",    "Gelen bayt:"),
                ("age",        "Kare yaşı:"),
        ]):
            diag_grid.addWidget(QLabel(label), i // 2, (i % 2) * 2)
            v = QLabel("—")
            v.setProperty("role", "value")
            v.setMinimumWidth(60)
            diag_grid.addWidget(v, i // 2, (i % 2) * 2 + 1)
            self._diag_labels[key] = v
        conn_layout.addLayout(diag_grid)

        conn_layout.addStretch()
        top_row.addWidget(conn_box)

        pos_box = QGroupBox("Konum")
        pos_grid = QGridLayout(pos_box)
        self._x_card = MetricCard("X", "mm")
        self._a_card = MetricCard("A", "°")
        self._t_card = MetricCard("Gerilim", "N")
        pos_grid.addWidget(self._x_card, 0, 0)
        pos_grid.addWidget(self._a_card, 0, 1)
        pos_grid.addWidget(self._t_card, 0, 2)
        top_row.addWidget(pos_box, stretch=1)
        layout.addLayout(top_row)

        # Jog controls
        jog_box = QGroupBox("Manuel Hareket")
        jog_grid = QGridLayout(jog_box)
        jog_grid.addWidget(QLabel("Mesafe:"), 0, 0)
        self._jog_dist = QDoubleSpinBox()
        self._jog_dist.setRange(-100, 100); self._jog_dist.setValue(1.0)
        self._jog_dist.setSuffix(" mm/°"); self._jog_dist.setDecimals(2)
        jog_grid.addWidget(self._jog_dist, 0, 1)

        jog_grid.addWidget(QLabel("Hız:"), 0, 2)
        self._jog_feed = QDoubleSpinBox()
        self._jog_feed.setRange(50, 5000); self._jog_feed.setValue(500)
        self._jog_feed.setSuffix(" mm/min")
        jog_grid.addWidget(self._jog_feed, 0, 3)

        # Jog buttons grid
        x_minus_big = QPushButton("◀◀ X-10")
        x_minus = QPushButton("◀ X-")
        x_plus = QPushButton("X+ ▶")
        x_plus_big = QPushButton("X+10 ▶▶")
        a_minus = QPushButton("◀ A-")
        a_plus = QPushButton("A+ ▶")
        x_minus_big.clicked.connect(lambda: self._do_jog("X", -10, big=True))
        x_minus.clicked.connect(lambda: self._do_jog("X", -1))
        x_plus.clicked.connect(lambda: self._do_jog("X", +1))
        x_plus_big.clicked.connect(lambda: self._do_jog("X", +10, big=True))
        a_minus.clicked.connect(lambda: self._do_jog("A", -1))
        a_plus.clicked.connect(lambda: self._do_jog("A", +1))
        for btn in (x_minus_big, x_minus, x_plus, x_plus_big, a_minus, a_plus):
            btn.setMinimumHeight(36)

        jog_grid.addWidget(x_minus_big, 1, 0)
        jog_grid.addWidget(x_minus, 1, 1)
        jog_grid.addWidget(x_plus, 1, 2)
        jog_grid.addWidget(x_plus_big, 1, 3)
        jog_grid.addWidget(a_minus, 2, 1)
        jog_grid.addWidget(a_plus, 2, 2)
        layout.addWidget(jog_box)

        # Calibration row
        cal_row = QHBoxLayout()
        tens_box = QGroupBox("Gerilim Kalibrasyonu")
        tens_layout = QVBoxLayout(tens_box)
        tare_btn = QPushButton("Sıfırla (yük yok)")
        tare_btn.clicked.connect(self.tareRequested.emit)
        tens_layout.addWidget(tare_btn)
        cal_row.addWidget(tens_box)

        estop_box = QGroupBox("Güvenlik Testleri")
        estop_layout = QVBoxLayout(estop_box)
        estop_btn = QPushButton("⚠ E-Dur Testi")
        estop_btn.setProperty("role", "danger")
        estop_btn.clicked.connect(self.estopRequested.emit)
        estop_layout.addWidget(estop_btn)
        cal_row.addWidget(estop_box)
        layout.addLayout(cal_row)

        # FAT checklist
        fat_box = QGroupBox(f"FAT Kontrol Listesi ({len(FAT_CHECKLIST)} madde)")
        fat_layout = QVBoxLayout(fat_box)
        self._fat_list = QListWidget()
        for item_text in FAT_CHECKLIST:
            it = QListWidgetItem(item_text)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            self._fat_list.addItem(it)
        self._fat_list.itemChanged.connect(self._update_fat_progress)
        fat_layout.addWidget(self._fat_list)
        self._fat_progress_lbl = QLabel("0 / 0 madde işaretlendi")
        fat_layout.addWidget(self._fat_progress_lbl)
        self._update_fat_progress()
        layout.addWidget(fat_box, stretch=1)

    def _do_jog(self, axis: str, dist: float, big: bool = False):
        if not big:
            dist = self._jog_dist.value() * (1 if dist > 0 else -1)
        self.jogRequested.emit(axis, dist, self._jog_feed.value())

    def _update_fat_progress(self, *args):
        n_checked = sum(1 for i in range(self._fat_list.count())
                        if self._fat_list.item(i).checkState() == Qt.Checked)
        n_total = self._fat_list.count()
        self._fat_progress_lbl.setText(f"{n_checked} / {n_total} madde işaretlendi")
        if n_checked == n_total:
            self._fat_progress_lbl.setProperty("status", "ok")
        else:
            self._fat_progress_lbl.setProperty("status", "warn")
        self._fat_progress_lbl.style().unpolish(self._fat_progress_lbl)
        self._fat_progress_lbl.style().polish(self._fat_progress_lbl)

    @Slot(object)
    def on_motion_status(self, status):
        self._x_card.set_value(status.x_mm, "{:.3f}")
        self._a_card.set_value(status.a_deg, "{:.2f}")
        self._t_card.set_value(status.tension_N, "{:.2f}")
        state_name = status.state.name if hasattr(status.state, "name") else str(status.state)
        state_status = "fatal" if state_name == "ESTOP" else (
                       "ok" if state_name in ("READY", "RUNNING") else "warn")
        self._state_led.set_status(state_status)
        self._state_led.set_text(f"Durum: {state_name}")

    @Slot(int)
    def on_connection_state(self, state: int):
        statuses = {0: "off", 1: "warn", 2: "ok", 3: "crit"}
        names = {0: "Bağlı değil", 1: "Bağlanıyor", 2: "Bağlandı", 3: "Hata"}
        self._conn_led.set_status(statuses.get(state, "off"))
        self._conn_led.set_text(f"Bağlantı: {names.get(state, '?')}")

    @Slot()
    def _on_connect_clicked(self):
        """Connect button — emit portChange first (if changed), then connect."""
        port = self._port_edit.text().strip()
        baud = self._baud_combo.currentData()
        if port and baud:
            self.portChangeRequested.emit(port, int(baud))
        self.connectRequested.emit()

    @Slot(dict)
    def on_diagnostics(self, d: dict):
        """Slot to update live diagnostics labels.

        Expects keys: frames, crc, sync, partial, watchdog, reconnect,
        byte_in, age (already formatted strings or numbers).
        """
        if not hasattr(self, "_diag_labels"):
            return
        for key, label in self._diag_labels.items():
            v = d.get(key)
            if v is None:
                continue
            if isinstance(v, float):
                label.setText(f"{v:.2f}")
            elif isinstance(v, int):
                label.setText(f"{v:,d}")
            else:
                label.setText(str(v))
