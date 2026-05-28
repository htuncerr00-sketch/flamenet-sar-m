"""
panels/predictive_maintenance.py — Predictive Maintenance + Analytics
========================================================================
Displays:
  - Per-component health bars + RUL countdown
  - Trend chart: system health vs runtime
  - Maintenance action list
  - Cp/Cpk panel (from production stats)
"""
from __future__ import annotations
import time
from typing import Optional, List

from PySide6.QtCore import Qt, Slot, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QGridLayout, QLabel, QProgressBar, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame, QPushButton)

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from backend.ai.predictive_maintenance import PredictiveMaintenance, ComponentRUL

from ..widgets.metric_card import MetricCard
from ..widgets.realtime_chart import RealtimeChart
from ..themes.dark_industrial import COLOR, chart_pen_colors


class PredictiveMaintenancePanel(QWidget):
    """Predictive maintenance dashboard."""

    REFRESH_HZ = 2   # 2Hz is plenty for trend display

    def __init__(self, pm: Optional[PredictiveMaintenance] = None, parent=None):
        super().__init__(parent)
        self._pm = pm or PredictiveMaintenance()
        self._t0 = time.monotonic()
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / self.REFRESH_HZ))
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        title = QLabel("Tahminsel Bakım")
        title.setProperty("role", "header")
        layout.addWidget(title)

        # KPI row
        kpi_row = QHBoxLayout()
        self._health_card = MetricCard(
            "Sistem Sağlığı", "%", "ağırlıklı ortalama", large=True)
        self._minrul_card = MetricCard(
            "Min RUL", "h", "en zayıf bileşen", large=True)
        self._actions_card = MetricCard(
            "Açık görevler", "", "planlanmış görevler")
        self._runtime_card = MetricCard(
            "Çalışma süresi", "h", "oturum")
        for c in (self._health_card, self._minrul_card,
                  self._actions_card, self._runtime_card):
            kpi_row.addWidget(c)
        layout.addLayout(kpi_row)

        # Components grid (bars + RUL)
        comp_box = QGroupBox("Bileşen Sağlığı")
        comp_layout = QVBoxLayout(comp_box)
        self._comp_table = QTableWidget(0, 4)
        self._comp_table.setHorizontalHeaderLabels(
            ["Bileşen", "Sağlık", "RUL", "Çalışma süresi"])
        self._comp_table.horizontalHeader().setStretchLastSection(True)
        self._comp_table.verticalHeader().setVisible(False)
        self._comp_table.setEditTriggers(QTableWidget.NoEditTriggers)
        comp_layout.addWidget(self._comp_table)
        layout.addWidget(comp_box, stretch=1)

        # Trend chart
        trend_box = QGroupBox("Sistem Sağlığı Trendi")
        trend_layout = QVBoxLayout(trend_box)
        self._trend_chart = RealtimeChart(
            "", "%", y_range=(0, 100), max_points=600,
            pen_colors={"health": COLOR["chart_tension"]})
        self._trend_chart.add_series("health")
        trend_layout.addWidget(self._trend_chart)
        layout.addWidget(trend_box, stretch=1)

    def step_pm(self, dt_h: float, vib_rms: float, temp_C: float,
                current_A: float):
        """Backend bridge: advance predictive maintenance simulation."""
        self._pm.step(dt_h, vib_rms, temp_C, current_A)

    def _refresh(self):
        sys_health = self._pm.system_health_pct()
        self._health_card.set_value(sys_health, "{:.2f}")
        self._health_card.set_status(
            "ok" if sys_health > 80 else
            "warn" if sys_health > 50 else "crit")

        min_rul = self._pm.min_RUL_h()
        self._minrul_card.set_value(min_rul, "{:.0f}")
        self._minrul_card.set_status(
            "ok" if min_rul > 1000 else
            "warn" if min_rul > 100 else "crit")

        comps = self._pm.components()
        self._actions_card.set_value(
            sum(1 for c in comps if c.RUL_h < 168), "{:d}")

        elapsed_h = (time.monotonic() - self._t0) / 3600.0
        self._runtime_card.set_value(elapsed_h, "{:.3f}")

        # Update table
        self._comp_table.setRowCount(len(comps))
        for i, c in enumerate(comps):
            self._comp_table.setItem(i, 0, QTableWidgetItem(c.name))
            # Health bar
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(c.health_pct))
            bar.setFormat(f"{c.health_pct:.2f}%")
            self._comp_table.setCellWidget(i, 1, bar)
            # RUL
            rul_item = QTableWidgetItem(f"{c.RUL_h:.0f} h")
            self._comp_table.setItem(i, 2, rul_item)
            # Runtime
            rt_item = QTableWidgetItem(f"{c.runtime_h:.1f} h")
            self._comp_table.setItem(i, 3, rt_item)

        # Trend
        self._trend_chart.append("health", time.monotonic(), sys_health)
        self._trend_chart.refresh()
