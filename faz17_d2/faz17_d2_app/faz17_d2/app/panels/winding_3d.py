"""
panels/winding_3d.py — Real 3D Winding Visualizer (GLViewWidget)
====================================================================
Renders:
  - Mandrel cylinder mesh (real geometry: R, L)
  - Fiber path from winding_planner (Clairaut helical projection)
  - Live "current head" marker driven by telemetry
  - Tension-mapped color along fiber path
  - Orbit/pan/zoom camera (built into GLViewWidget)

Performance:
  - Mandrel mesh built ONCE (static)
  - Fiber path: precomputed at recipe load
  - Live position: lightweight GLScatterPlotItem update @ 30Hz
"""
from __future__ import annotations
import math
from typing import Optional, Tuple

import numpy as np
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QGroupBox, QSpinBox, QCheckBox, QPushButton, QComboBox, QGridLayout)

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from backend.hardware.esp32_link import TelemetryFrame
from backend.core.winding_planner import WindingParams

import pyqtgraph.opengl as gl
import pyqtgraph as pg

from ..themes.dark_industrial import COLOR


def cylinder_mesh(radius: float, length: float,
                  segments: int = 48, rings: int = 12) -> Tuple[np.ndarray, np.ndarray]:
    """Generate triangle mesh for an open cylinder (filament-winding mandrel)."""
    verts = []
    for r in range(rings + 1):
        z = -length / 2 + length * (r / rings)
        for s in range(segments):
            theta = 2 * math.pi * s / segments
            verts.append([radius * math.cos(theta), radius * math.sin(theta), z])
    verts = np.array(verts, dtype=np.float32)
    faces = []
    for r in range(rings):
        for s in range(segments):
            sn = (s + 1) % segments
            a = r * segments + s
            b = r * segments + sn
            c = (r + 1) * segments + s
            d = (r + 1) * segments + sn
            faces.append([a, b, c])
            faces.append([b, d, c])
    faces = np.array(faces, dtype=np.int32)
    return verts, faces


def helical_fiber_path(params: WindingParams,
                       n_points_per_circuit: int = 60) -> np.ndarray:
    """
    Generate 3D fiber path for the full multi-layer helical winding.

    Returns (N, 3) array of (x, y, z) where:
      mandrel axis is along Z
      helical path traces fiber laydown
    """
    R = params.mandrel_R_mm / 1000.0   # m
    L = params.mandrel_L_mm / 1000.0
    alpha = math.radians(params.alpha_deg)
    pitch_z = params.tow_width_mm / 1000.0 / math.cos(alpha)
    n_passes_per_layer = max(1, int(L / pitch_z))

    points = []
    for layer in range(params.n_layers):
        direction = 1 if layer % 2 == 0 else -1
        # Slight radial offset per layer (visualization aid)
        r_layer = R * (1 + 0.001 * layer)
        z_start = -L / 2 if direction == 1 else L / 2
        a_cumul = 0.0
        z_cur = z_start
        # Angular pitch per axial pitch
        ang_per_unit_z = math.tan(math.pi/2 - alpha)   # = cot(alpha)
        # Generate path: stepping forward in z
        n_total = n_passes_per_layer * n_points_per_circuit
        for i in range(n_total):
            frac = i / n_total
            z_pos = z_start + direction * frac * L
            theta = a_cumul + direction * frac * ang_per_unit_z * L / r_layer
            x = r_layer * math.cos(theta)
            y = r_layer * math.sin(theta)
            points.append([x, y, z_pos])
    return np.array(points, dtype=np.float32)


class Winding3DPanel(QWidget):
    """3D mandrel + fiber path visualizer."""

    REFRESH_HZ = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self._params: Optional[WindingParams] = None
        self._fiber_path_full: Optional[np.ndarray] = None
        self._current_progress = 0.0   # [0..1]
        self._latest_frame: Optional[TelemetryFrame] = None
        self._fps_t0 = 0
        self._fps_frames = 0
        self._fps = 0.0

        self._build_ui()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(int(1000 / self.REFRESH_HZ))
        self._refresh_timer.timeout.connect(self._refresh_view)
        self._refresh_timer.start()

        # Default mandrel
        self.set_winding_params(WindingParams())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Header
        header = QHBoxLayout()
        title = QLabel("3D Winding Visualizer")
        title.setProperty("role", "header")
        header.addWidget(title)
        header.addStretch()
        self._fps_lbl = QLabel("3D: -- FPS")
        self._fps_lbl.setProperty("role", "caption")
        header.addWidget(self._fps_lbl)
        layout.addLayout(header)

        # Main split: GL view + controls
        h_split = QHBoxLayout()
        # GL widget
        self._gl = gl.GLViewWidget()
        self._gl.setBackgroundColor(COLOR["bg_window"])
        self._gl.setCameraPosition(distance=0.7, elevation=25, azimuth=45)
        # Axes for reference
        self._gl_axes = gl.GLAxisItem(size=pg.Vector(0.05, 0.05, 0.05))
        self._gl.addItem(self._gl_axes)
        h_split.addWidget(self._gl, stretch=4)

        # Right controls
        controls = QGroupBox("View Controls")
        ctrl_layout = QGridLayout(controls)
        ctrl_layout.setSpacing(6)
        row = 0

        ctrl_layout.addWidget(QLabel("Camera distance:"), row, 0)
        self._dist_spin = QSpinBox(); self._dist_spin.setRange(10, 200)
        self._dist_spin.setValue(70); self._dist_spin.setSuffix(" %")
        self._dist_spin.valueChanged.connect(self._update_camera)
        ctrl_layout.addWidget(self._dist_spin, row, 1); row += 1

        ctrl_layout.addWidget(QLabel("Elevation:"), row, 0)
        self._elev_spin = QSpinBox(); self._elev_spin.setRange(-89, 89)
        self._elev_spin.setValue(25); self._elev_spin.setSuffix(" °")
        self._elev_spin.valueChanged.connect(self._update_camera)
        ctrl_layout.addWidget(self._elev_spin, row, 1); row += 1

        ctrl_layout.addWidget(QLabel("Azimuth:"), row, 0)
        self._azim_spin = QSpinBox(); self._azim_spin.setRange(0, 359)
        self._azim_spin.setValue(45); self._azim_spin.setSuffix(" °")
        self._azim_spin.valueChanged.connect(self._update_camera)
        ctrl_layout.addWidget(self._azim_spin, row, 1); row += 1

        # Display options
        self._show_mandrel = QCheckBox("Mandrel"); self._show_mandrel.setChecked(True)
        self._show_mandrel.stateChanged.connect(self._update_visibility)
        ctrl_layout.addWidget(self._show_mandrel, row, 0, 1, 2); row += 1

        self._show_path = QCheckBox("Fiber path"); self._show_path.setChecked(True)
        self._show_path.stateChanged.connect(self._update_visibility)
        ctrl_layout.addWidget(self._show_path, row, 0, 1, 2); row += 1

        self._show_progress = QCheckBox("Progress marker"); self._show_progress.setChecked(True)
        self._show_progress.stateChanged.connect(self._update_visibility)
        ctrl_layout.addWidget(self._show_progress, row, 0, 1, 2); row += 1

        ctrl_layout.addWidget(QLabel("Color by:"), row, 0)
        self._color_mode = QComboBox()
        self._color_mode.addItems(["Layer", "Tension", "Uniform"])
        self._color_mode.currentTextChanged.connect(self._rebuild_path_colors)
        ctrl_layout.addWidget(self._color_mode, row, 1); row += 1

        reset_btn = QPushButton("Reset View")
        reset_btn.clicked.connect(self._reset_view)
        ctrl_layout.addWidget(reset_btn, row, 0, 1, 2); row += 1

        info_lbl = QLabel("Drag: rotate\nRight-drag: pan\nWheel: zoom")
        info_lbl.setProperty("role", "caption")
        ctrl_layout.addWidget(info_lbl, row, 0, 1, 2); row += 1

        ctrl_layout.setRowStretch(row, 1)

        h_split.addWidget(controls, stretch=1)
        layout.addLayout(h_split, stretch=1)

        # Items (filled in set_winding_params)
        self._mesh_item: Optional[gl.GLMeshItem] = None
        self._path_item: Optional[gl.GLLinePlotItem] = None
        self._marker_item: Optional[gl.GLScatterPlotItem] = None

    def set_winding_params(self, params: WindingParams):
        """Rebuild mandrel + fiber path."""
        self._params = params
        # Remove old items (set attrs to None to avoid stale-ref bugs)
        if self._mesh_item is not None:
            self._gl.removeItem(self._mesh_item); self._mesh_item = None
        if self._path_item is not None:
            self._gl.removeItem(self._path_item); self._path_item = None
        if self._marker_item is not None:
            self._gl.removeItem(self._marker_item); self._marker_item = None

        # Mandrel mesh
        R = params.mandrel_R_mm / 1000.0
        L = params.mandrel_L_mm / 1000.0
        verts, faces = cylinder_mesh(R, L, segments=48, rings=8)
        mesh_data = gl.MeshData(vertexes=verts, faces=faces)
        self._mesh_item = gl.GLMeshItem(
            meshdata=mesh_data,
            smooth=False,
            color=(0.4, 0.45, 0.55, 0.4),
            shader='balloon',
            glOptions='additive')
        self._gl.addItem(self._mesh_item)

        # Fiber path (full)
        self._fiber_path_full = helical_fiber_path(params, n_points_per_circuit=40)
        self._rebuild_path_colors()

        # Progress marker
        self._marker_item = gl.GLScatterPlotItem(
            pos=np.array([[0, 0, 0]]),
            color=(1.0, 0.7, 0.0, 1.0),
            size=10.0)
        self._gl.addItem(self._marker_item)

        # Auto-fit camera distance based on mandrel size
        max_dim = max(R * 2.5, L * 1.5)
        self._gl.setCameraPosition(distance=max_dim)

    def _rebuild_path_colors(self):
        if self._fiber_path_full is None or self._params is None: return
        if self._path_item is not None:
            self._gl.removeItem(self._path_item)
        n = len(self._fiber_path_full)
        mode = self._color_mode.currentText() if hasattr(self, '_color_mode') else "Layer"
        if mode == "Layer":
            # Color by layer index
            n_per_layer = n // self._params.n_layers
            colors = np.zeros((n, 4), dtype=np.float32)
            for i in range(n):
                layer = min(self._params.n_layers - 1, i // max(n_per_layer, 1))
                hue = layer / max(self._params.n_layers, 1)
                # HSV → RGB approx
                r, g, b = _hsv_to_rgb(hue, 0.85, 1.0)
                colors[i] = [r, g, b, 0.9]
        elif mode == "Tension":
            colors = np.zeros((n, 4), dtype=np.float32)
            for i in range(n):
                frac = i / max(n - 1, 1)
                # Green → yellow → red
                r, g, b = _hsv_to_rgb(0.33 * (1 - frac), 0.9, 1.0)
                colors[i] = [r, g, b, 0.9]
        else:   # Uniform
            colors = np.tile(np.array([0.4, 0.85, 0.7, 0.8], dtype=np.float32),
                             (n, 1))
        self._path_item = gl.GLLinePlotItem(
            pos=self._fiber_path_full,
            color=colors,
            width=1.5,
            antialias=True,
            mode='line_strip')
        self._gl.addItem(self._path_item)
        self._update_visibility()

    def _update_visibility(self, *args):
        if self._mesh_item is not None:
            self._mesh_item.setVisible(self._show_mandrel.isChecked())
        if self._path_item is not None:
            self._path_item.setVisible(self._show_path.isChecked())
        if self._marker_item is not None:
            self._marker_item.setVisible(self._show_progress.isChecked())

    def _update_camera(self, *args):
        # Reasonable distance scaling: 10..200% → 0.1 .. 2.0 m (mandrel diameter scaled)
        if self._params is None: return
        base = max(self._params.mandrel_R_mm * 0.003,
                   self._params.mandrel_L_mm * 0.002)
        dist = base * (self._dist_spin.value() / 50.0)
        self._gl.setCameraPosition(
            distance=dist,
            elevation=self._elev_spin.value(),
            azimuth=self._azim_spin.value())

    def _reset_view(self):
        self._dist_spin.setValue(70)
        self._elev_spin.setValue(25)
        self._azim_spin.setValue(45)
        self._update_camera()

    @Slot(object)
    def on_latest_frame(self, f: TelemetryFrame):
        """Update progress marker from latest telemetry."""
        self._latest_frame = f
        if self._params is None: return
        # Determine progress along path from x_mm position
        x_frac = max(0, min(1, f.x_mm / max(self._params.mandrel_L_mm, 1)))
        # Modulate by layer (use angle accumulator as rough layer counter)
        n_layers_done = int(f.a_deg / 360) % max(self._params.n_layers, 1)
        total_progress = (n_layers_done + x_frac) / max(self._params.n_layers, 1)
        self._current_progress = max(0, min(1, total_progress))

    def _refresh_view(self):
        # Update marker position
        if (self._fiber_path_full is not None and
            self._marker_item is not None and
            len(self._fiber_path_full) > 0):
            idx = int(self._current_progress * (len(self._fiber_path_full) - 1))
            pos = self._fiber_path_full[idx:idx+1]
            self._marker_item.setData(pos=pos)

        # FPS counter
        import time as _t
        self._fps_frames += 1
        elapsed = _t.monotonic() - self._fps_t0
        if elapsed > 1.0:
            self._fps = self._fps_frames / elapsed
            self._fps_lbl.setText(f"3D: {self._fps:.1f} FPS")
            self._fps_frames = 0
            self._fps_t0 = _t.monotonic()


def _hsv_to_rgb(h, s, v):
    """HSV to RGB conversion (h, s, v in [0,1])."""
    i = int(h * 6)
    f = h * 6 - i
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    i %= 6
    if i == 0: return v, t, p
    if i == 1: return q, v, p
    if i == 2: return p, v, t
    if i == 3: return p, q, v
    if i == 4: return t, p, v
    return v, p, q
