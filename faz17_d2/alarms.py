"""
panels/alarms.py — Alarm & Safety Dashboard
================================================
Shows:
  - Active alarm list (deduplicated, most recent on top)
  - Safety status LEDs per subsystem
  - Acknowledged/active counters
  - Clear-acknowledged button (operator action)
"""
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt, Slot, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QGridLayout, QLabel, QPushButton, QFrame)

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from backend.core.safety_controller import SafetyEvent, SafetyLevel

from ..widgets.led_indicator import LedIndicator
from ..widgets.alarm_list import AlarmList
from ..widgets.metric_card import MetricCard
from ..themes.dark_industrial import COLOR


class AlarmsPanel(QWidget):
    """Alarm & safety dashboard."""

    estopRequested = Signal()
    clearHaltRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._n_warn = 0
        self._n_crit = 0
        self._n_fatal = 0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Title
        title = QLabel("Alarm & Safety Dashboard")
        title.setProperty("role", "header")
        layout.addWidget(title)

        # Status grid: subsystem LEDs
        status_box = QGroupBox("Safety Status")
        status_grid = QGridLayout(status_box)
        status_grid.setSpacing(6)

        self._leds = {
            "tension": LedIndicator("Tension", "ok"),
            "rpm":     LedIndicator("Spindle RPM", "ok"),
            "motion":  LedIndicator("Motion Bounds", "ok"),
            "vib":     LedIndicator("Vibration", "ok"),
            "thermal": LedIndicator("Thermal", "ok"),
            "encoder": LedIndicator("Encoder", "ok"),
            "estop":   LedIndicator("E-Stop Chain", "ok"),
            "comms":   LedIndicator("Communications", "ok"),
        }
        row, col = 0, 0
        for led in self._leds.values():
            status_grid.addWidget(led, row, col)
            col += 1
            if col >= 4:
                col = 0; row += 1
        layout.addWidget(status_box)

        # Counter cards
        counter_row = QHBoxLayout()
        self._warn_card = MetricCard("WARN", "", "warnings")
        self._crit_card = MetricCard("CRIT", "", "critical")
        self._fatal_card = MetricCard("FATAL", "", "fatal events")
        self._halted_card = MetricCard("Halted", "", "system state")
        counter_row.addWidget(self._warn_card)
        counter_row.addWidget(self._crit_card)
        counter_row.addWidget(self._fatal_card)
        counter_row.addWidget(self._halted_card)
        layout.addLayout(counter_row)
        self._warn_card.set_value(0, "{:d}")
        self._crit_card.set_value(0, "{:d}")
        self._fatal_card.set_value(0, "{:d}")
        self._halted_card.set_value("No")

        # Alarm list
        self._alarm_list = AlarmList()
        layout.addWidget(self._alarm_list, stretch=1)

        # Operator action buttons
        action_row = QHBoxLayout()
        self._estop_btn = QPushButton("⚠  EMERGENCY STOP")
        self._estop_btn.setProperty("role", "danger")
        self._estop_btn.setMinimumHeight(40)
        self._estop_btn.clicked.connect(self.estopRequested.emit)
        action_row.addWidget(self._estop_btn)

        self._clear_btn = QPushButton("Clear Halt (after fault resolved)")
        self._clear_btn.clicked.connect(self.clearHaltRequested.emit)
        action_row.addWidget(self._clear_btn)
        layout.addLayout(action_row)

    @Slot(object)
    def on_safety_event(self, ev: SafetyEvent):
        """Display incoming safety event."""
        self._alarm_list.add_event(ev)

        # Update counters
        if ev.level == SafetyLevel.WARN:
            self._n_warn += 1
            self._warn_card.set_value(self._n_warn, "{:d}")
            self._warn_card.set_status("warn")
        elif ev.level == SafetyLevel.CRIT:
            self._n_crit += 1
            self._crit_card.set_value(self._n_crit, "{:d}")
            self._crit_card.set_status("crit")
        elif ev.level == SafetyLevel.FATAL:
            self._n_fatal += 1
            self._fatal_card.set_value(self._n_fatal, "{:d}")
            self._fatal_card.set_status("fatal")
            self._halted_card.set_value("YES")
            self._halted_card.set_status("fatal")

        # Update specific LED based on event code
        code = ev.code
        status = ("fatal" if ev.level == SafetyLevel.FATAL else
                  "crit"  if ev.level == SafetyLevel.CRIT  else
                  "warn"  if ev.level == SafetyLevel.WARN  else "ok")
        if "TENSION" in code:        self._leds["tension"].set_status(status)
        elif "RPM" in code:          self._leds["rpm"].set_status(status)
        elif "X_OUT" in code or "MOTION" in code:
            self._leds["motion"].set_status(status)
        elif "VIB" in code:          self._leds["vib"].set_status(status)
        elif "TEMP" in code or "THERMAL" in code:
            self._leds["thermal"].set_status(status)
        elif "ENCODER" in code:      self._leds["encoder"].set_status(status)
        elif "ESTOP" in code:        self._leds["estop"].set_status(status)
        elif "SEQ" in code or "CAN" in code:
            self._leds["comms"].set_status(status)

    @Slot()
    def on_halt_cleared(self):
        """Backend has cleared halt — reset UI."""
        self._halted_card.set_value("No")
        self._halted_card.set_status("ok")
        for led in self._leds.values():
            led.set_status("ok")
