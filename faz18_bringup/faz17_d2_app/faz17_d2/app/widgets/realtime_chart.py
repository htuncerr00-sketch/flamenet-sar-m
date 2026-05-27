"""
widgets/realtime_chart.py — pyqtgraph realtime line chart wrapper
=====================================================================
Bounded-history rolling chart with auto-downsampling for performance.

Performance notes:
  - Uses setData() with downsample=True
  - History bounded to MAX_POINTS (deque)
  - Render throttled by parent (calls update_pen_data at <=30Hz)
"""
from __future__ import annotations
from collections import deque
from typing import Optional, List

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout

from ..themes.dark_industrial import COLOR


pg.setConfigOptions(antialias=False, useOpenGL=False)   # speed > quality


class RealtimeChart(QWidget):
    """
    Single-axis (or dual) line chart with rolling history.

    Args:
        title:   plot title
        unit:    y-axis label unit
        y_range: optional (min, max) — None = auto
        max_points: rolling buffer size
        pen_colors: dict name → color hex
    """
    def __init__(self, title: str = "", unit: str = "",
                 y_range: Optional[tuple] = None,
                 max_points: int = 1000,
                 pen_colors: Optional[dict] = None,
                 parent=None):
        super().__init__(parent)
        self._max = max_points
        self._series: dict = {}   # name → (deque, PlotCurveItem)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._plot = pg.PlotWidget()
        self._plot.setBackground(COLOR["bg_panel"])
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        self._plot.getAxis('left').setPen(COLOR["text_secondary"])
        self._plot.getAxis('bottom').setPen(COLOR["text_secondary"])
        self._plot.getAxis('left').setTextPen(COLOR["text_secondary"])
        self._plot.getAxis('bottom').setTextPen(COLOR["text_secondary"])
        self._plot.setLabel('left', unit, color=COLOR["text_secondary"])
        self._plot.setLabel('bottom', 't (s)', color=COLOR["text_secondary"])
        if title:
            self._plot.setTitle(title, color=COLOR["text_primary"], size='10pt')
        if y_range is not None:
            self._plot.setYRange(*y_range)
            self._plot.enableAutoRange('y', False)
        else:
            self._plot.enableAutoRange('y', True)
        # X always auto-scrolling
        self._plot.enableAutoRange('x', True)
        # Disable mouse interactions for performance during live
        self._plot.setMouseEnabled(x=False, y=False)
        self._plot.setMenuEnabled(False)

        layout.addWidget(self._plot)
        self._pen_colors = pen_colors or {}
        self._legend = None
        self._t0 = None

    def add_series(self, name: str, color: Optional[str] = None,
                   width: int = 2) -> None:
        if name in self._series:
            return
        c = color or self._pen_colors.get(name, COLOR["accent"])
        curve = self._plot.plot([], [],
            pen=pg.mkPen(c, width=width),
            name=name, antialias=False)
        # downsample at draw time
        curve.setDownsampling(method='peak', auto=True)
        curve.setClipToView(True)
        self._series[name] = (deque(maxlen=self._max),
                              deque(maxlen=self._max),
                              curve)
        if self._legend is None:
            self._legend = self._plot.addLegend(offset=(10, 10),
                labelTextColor=COLOR["text_primary"])

    def append(self, name: str, t: float, value: float) -> None:
        if name not in self._series: return
        if self._t0 is None: self._t0 = t
        t_buf, v_buf, _ = self._series[name]
        t_buf.append(t - self._t0)
        v_buf.append(value)

    def refresh(self) -> None:
        """Push deques into plot. Call from UI thread at <=30Hz."""
        for name, (t_buf, v_buf, curve) in self._series.items():
            if t_buf and v_buf:
                curve.setData(list(t_buf), list(v_buf))

    def clear_series(self) -> None:
        for name, (t_buf, v_buf, _) in self._series.items():
            t_buf.clear(); v_buf.clear()
        self._t0 = None

    def set_y_range(self, ymin: float, ymax: float) -> None:
        self._plot.setYRange(ymin, ymax)


class FFTChart(QWidget):
    """Bar chart for FFT spectrum display."""
    def __init__(self, title: str = "Vibration FFT", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._plot = pg.PlotWidget()
        self._plot.setBackground(COLOR["bg_panel"])
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        self._plot.setLabel('left', 'Amplitude (g)',
            color=COLOR["text_secondary"])
        self._plot.setLabel('bottom', 'Frequency (Hz)',
            color=COLOR["text_secondary"])
        self._plot.setTitle(title, color=COLOR["text_primary"], size='10pt')
        self._plot.getAxis('left').setTextPen(COLOR["text_secondary"])
        self._plot.getAxis('bottom').setTextPen(COLOR["text_secondary"])
        self._plot.setMouseEnabled(x=False, y=False)
        self._plot.setMenuEnabled(False)
        self._bar = pg.BarGraphItem(x=[], height=[], width=0.5,
            brush=COLOR["chart_vib"])
        self._plot.addItem(self._bar)
        layout.addWidget(self._plot)

    def update_spectrum(self, freqs: np.ndarray, amps: np.ndarray) -> None:
        # Keep only first 100 bins for display
        n = min(len(freqs), 100)
        if n == 0: return
        if len(freqs) > 1:
            width = float(freqs[1] - freqs[0]) * 0.8
        else:
            width = 0.5
        self._bar.setOpts(x=freqs[:n].tolist(),
            height=amps[:n].tolist(), width=width)
