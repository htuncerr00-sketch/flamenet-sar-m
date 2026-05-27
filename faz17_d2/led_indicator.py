"""
widgets/led_indicator.py — Status LED with text label
"""
from __future__ import annotations
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QSizePolicy

from ..themes.dark_industrial import COLOR

LED_COLORS = {
    "ok":      QColor(COLOR["ok"]),
    "info":    QColor(COLOR["info"]),
    "warn":    QColor(COLOR["warn"]),
    "crit":    QColor(COLOR["crit"]),
    "fatal":   QColor(COLOR["fatal"]),
    "off":     QColor("#3a3a3a"),
}


class LedIndicator(QWidget):
    """Round LED with optional text label."""
    def __init__(self, text: str = "", status: str = "off",
                 diameter: int = 14, parent=None):
        super().__init__(parent)
        self._diameter = diameter
        self._status = status
        self._text = text
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(2, 2, 6, 2)
        self._layout.setSpacing(6)
        self._led_widget = _LedDot(self._diameter, status, self)
        self._layout.addWidget(self._led_widget)
        if text:
            self._label = QLabel(text)
            self._layout.addWidget(self._label)
        else:
            self._label = None
        self._layout.addStretch()
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def set_status(self, status: str):
        if status != self._status:
            self._status = status
            self._led_widget.set_status(status)

    def set_text(self, text: str):
        if self._label:
            self._label.setText(text)
        self._text = text


class _LedDot(QWidget):
    def __init__(self, diameter: int, status: str = "off", parent=None):
        super().__init__(parent)
        self._d = diameter
        self._status = status
        self.setFixedSize(diameter + 4, diameter + 4)

    def set_status(self, status: str):
        if status != self._status:
            self._status = status
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        col = LED_COLORS.get(self._status, LED_COLORS["off"])
        # Radial gradient for 3D appearance
        cx, cy = self.width() / 2, self.height() / 2
        grad = QRadialGradient(cx - 1, cy - 1, self._d / 2)
        grad.setColorAt(0.0, col.lighter(180))
        grad.setColorAt(0.7, col)
        grad.setColorAt(1.0, col.darker(180))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(col.darker(200), 1))
        p.drawEllipse(int(cx - self._d/2), int(cy - self._d/2),
                      self._d, self._d)
