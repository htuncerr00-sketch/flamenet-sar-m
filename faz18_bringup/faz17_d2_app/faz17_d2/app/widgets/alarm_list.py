"""
widgets/alarm_list.py — Alarm event list with severity icons
"""
from __future__ import annotations
import time
from collections import deque
from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QHBoxLayout, QPushButton, QLabel)

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from backend.core.safety_controller import SafetyEvent, SafetyLevel

from ..themes.dark_industrial import COLOR

LEVEL_COLORS = {
    SafetyLevel.OK:    QColor(COLOR["ok"]),
    SafetyLevel.INFO:  QColor(COLOR["info"]),
    SafetyLevel.WARN:  QColor(COLOR["warn"]),
    SafetyLevel.CRIT:  QColor(COLOR["crit"]),
    SafetyLevel.FATAL: QColor(COLOR["fatal"]),
}
LEVEL_NAMES = {
    SafetyLevel.OK:    "OK",
    SafetyLevel.INFO:  "INFO",
    SafetyLevel.WARN:  "WARN",
    SafetyLevel.CRIT:  "CRIT",
    SafetyLevel.FATAL: "FATAL",
}


class AlarmList(QWidget):
    """Time-ordered alarm list (newest on top)."""
    MAX_ENTRIES = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self._events: deque = deque(maxlen=self.MAX_ENTRIES)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Header row with counter + clear
        header = QHBoxLayout()
        self._counter = QLabel("0 events")
        self._counter.setProperty("role", "caption")
        header.addWidget(self._counter)
        header.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.clear)
        header.addWidget(clear_btn)
        layout.addLayout(header)

        # Table
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Time", "Level", "Code", "Message"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

    def add_event(self, ev: SafetyEvent) -> None:
        """Insert event at top. Called from UI thread (Qt signal handler)."""
        self._events.appendleft(ev)
        # Update table — insert row 0
        self._table.insertRow(0)
        # Cap rows
        if self._table.rowCount() > self.MAX_ENTRIES:
            self._table.removeRow(self._table.rowCount() - 1)

        # Time
        t = time.strftime("%H:%M:%S", time.localtime(ev.timestamp))
        ms = int((ev.timestamp - int(ev.timestamp)) * 1000)
        time_item = QTableWidgetItem(f"{t}.{ms:03d}")
        self._table.setItem(0, 0, time_item)

        # Level
        level_item = QTableWidgetItem(LEVEL_NAMES.get(ev.level, "?"))
        col = LEVEL_COLORS.get(ev.level, QColor(COLOR["text_primary"]))
        level_item.setForeground(col)
        self._table.setItem(0, 1, level_item)

        # Code
        self._table.setItem(0, 2, QTableWidgetItem(ev.code))
        # Message
        msg_item = QTableWidgetItem(ev.msg)
        self._table.setItem(0, 3, msg_item)

        self._counter.setText(f"{len(self._events)} events")

    def clear(self):
        self._events.clear()
        self._table.setRowCount(0)
        self._counter.setText("0 events")

    def event_count(self) -> int:
        return len(self._events)
