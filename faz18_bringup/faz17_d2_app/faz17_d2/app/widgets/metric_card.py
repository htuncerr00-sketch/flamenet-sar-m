"""
widgets/metric_card.py — KPI display card
"""
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame, QHBoxLayout

from ..themes.dark_industrial import COLOR


class MetricCard(QFrame):
    """
    Large metric display:
      - Title (small, secondary)
      - Value (large, primary, colored by status)
      - Unit (small, secondary)
      - Subtitle (small, secondary, optional)
    """
    def __init__(self, title: str, unit: str = "", subtitle: str = "",
                 parent=None, large: bool = False):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self._status = "ok"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)
        self._title_lbl = QLabel(title)
        self._title_lbl.setProperty("role", "caption")
        layout.addWidget(self._title_lbl)

        # Value + unit row
        value_row = QHBoxLayout()
        value_row.setSpacing(4)
        self._value_lbl = QLabel("—")
        self._value_lbl.setProperty(
            "role", "metric-large" if large else "metric")
        self._unit_lbl = QLabel(unit)
        self._unit_lbl.setProperty("role", "unit")
        self._unit_lbl.setAlignment(Qt.AlignBottom | Qt.AlignLeft)
        value_row.addWidget(self._value_lbl, alignment=Qt.AlignBottom)
        value_row.addWidget(self._unit_lbl, alignment=Qt.AlignBottom)
        value_row.addStretch()
        layout.addLayout(value_row)

        self._sub_lbl = QLabel(subtitle)
        self._sub_lbl.setProperty("role", "caption")
        layout.addWidget(self._sub_lbl)

        self.setMinimumHeight(80)
        self.setMinimumWidth(120)

    def set_value(self, value, fmt: str = "{:.2f}"):
        try:
            if isinstance(value, (int, float)):
                self._value_lbl.setText(fmt.format(value))
            else:
                self._value_lbl.setText(str(value))
        except Exception:
            self._value_lbl.setText(str(value))

    def set_status(self, status: str):
        """status: ok | info | warn | crit | fatal"""
        if status != self._status:
            self._status = status
            self._value_lbl.setProperty("status", status)
            # Force style refresh
            self._value_lbl.style().unpolish(self._value_lbl)
            self._value_lbl.style().polish(self._value_lbl)

    def set_subtitle(self, text: str):
        self._sub_lbl.setText(text)
