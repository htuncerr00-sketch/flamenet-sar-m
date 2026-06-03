"""
panels/winding_3d.py — Gerçek Zamanlı 3D Sarma Görüntüleyici
=============================================================
Görüntüler:
  - Mandrel ağ modeli (gerçek geometri: R, L veya CAM profili)
  - Sarma yolu (Clairaut helisinden veya CAM yolundan)
  - Canlı "kafa" pozisyon belirteci (telemetriden)
  - Gerilim ile renklendirme

Performans:
  - Mandrel ağı BİR KEZ oluşturulur (statik)
  - Fiber yolu: reçete yüklendiğinde önceden hesaplanır
  - Canlı pozisyon: GLScatterPlotItem @ 30Hz hafif güncelleme
"""
from __future__ import annotations
import math
from typing import Optional, Tuple

import numpy as np
from PySide6.QtCore import Qt, QTimer, Slot
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
    """Silindirik mandrel için üçgen ağı üret."""
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


def revolution_mesh(z_mm: np.ndarray, r_mm: np.ndarray,
                    segments: int = 48) -> Tuple[np.ndarray, np.ndarray]:
    """
    Dönel yüzey için ağ üret (z_mm, r_mm profili kullanarak).
    MandrelProfile'dan doğrudan 3D ağ oluşturur.
    """
    z_m = z_mm / 1000.0
    r_m = r_mm / 1000.0
    # Profili merkeze taşı
    z_center = (z_m.max() + z_m.min()) / 2.0
    z_m = z_m - z_center

    n_rings = len(z_m)
    verts = []
    for i in range(n_rings):
        for s in range(segments):
            theta = 2 * math.pi * s / segments
            verts.append([r_m[i] * math.cos(theta), r_m[i] * math.sin(theta), z_m[i]])
    verts = np.array(verts, dtype=np.float32)

    faces = []
    for r in range(n_rings - 1):
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
    """Geriye dönük uyumlu yardımcı: WindingParams'tan 3D yol üret."""
    R = params.mandrel_R_mm / 1000.0
    L = params.mandrel_L_mm / 1000.0
    alpha = math.radians(params.alpha_deg)
    pitch_z = params.tow_width_mm / 1000.0 / math.cos(alpha)
    n_passes_per_layer = max(1, int(L / pitch_z))
    points = []
    for layer in range(params.n_layers):
        direction = 1 if layer % 2 == 0 else -1
        r_layer = R * (1 + 0.001 * layer)
        z_start = -L / 2 if direction == 1 else L / 2
        ang_per_unit_z = math.tan(math.pi / 2 - alpha)
        n_total = n_passes_per_layer * n_points_per_circuit
        for i in range(n_total):
            frac = i / n_total
            z_pos = z_start + direction * frac * L
            theta = direction * frac * ang_per_unit_z * L / r_layer
            x = r_layer * math.cos(theta)
            y = r_layer * math.sin(theta)
            points.append([x, y, z_pos])
    return np.array(points, dtype=np.float32) if points else np.zeros((1, 3), np.float32)


def path_to_3d(z_mm_profile: np.ndarray, r_mm_profile: np.ndarray,
               winding_points) -> np.ndarray:
    """
    WindingPath.points listesini 3D koordinatlara dönüştür.
    Her nokta: x_mm (eksenel) ve a_deg (iş mili açısı) kullanır.
    """
    z_center = (z_mm_profile.max() + z_mm_profile.min()) / 2.0
    pts_3d = []
    for pt in winding_points:
        z_val = pt.x_mm
        r_val = float(np.interp(z_val, z_mm_profile, r_mm_profile)) / 1000.0
        theta = math.radians(pt.a_deg)
        x = r_val * math.cos(theta)
        y = r_val * math.sin(theta)
        z = (z_val - z_center) / 1000.0
        pts_3d.append([x, y, z])
    return np.array(pts_3d, dtype=np.float32) if pts_3d else np.zeros((1, 3), np.float32)


class Winding3DPanel(QWidget):
    """3D mandrel + fiber yolu görüntüleyici."""

    REFRESH_HZ = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self._params: Optional[WindingParams] = None
        self._fiber_path_full: Optional[np.ndarray] = None
        self._current_progress = 0.0
        self._latest_frame: Optional[TelemetryFrame] = None
        self._fps_t0 = 0
        self._fps_frames = 0
        self._fps = 0.0
        self._highlighted_layer: int = -1  # -1 = no highlight

        self._build_ui()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(int(1000 / self.REFRESH_HZ))
        self._refresh_timer.timeout.connect(self._refresh_view)
        self._refresh_timer.start()

        self.set_winding_params(WindingParams())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("3D Sarma Görüntüleyici")
        title.setProperty("role", "header")
        header.addWidget(title)
        header.addStretch()
        self._fps_lbl = QLabel("3D: -- FPS")
        self._fps_lbl.setProperty("role", "caption")
        header.addWidget(self._fps_lbl)
        layout.addLayout(header)

        h_split = QHBoxLayout()
        self._gl = gl.GLViewWidget()
        self._gl.setBackgroundColor(COLOR["bg_window"])
        self._gl.setCameraPosition(distance=0.7, elevation=25, azimuth=45)
        self._gl_axes = gl.GLAxisItem(size=pg.Vector(0.05, 0.05, 0.05))
        self._gl.addItem(self._gl_axes)
        h_split.addWidget(self._gl, stretch=4)

        controls = QGroupBox("Görünüm Ayarları")
        ctrl_layout = QGridLayout(controls)
        ctrl_layout.setSpacing(6)
        row = 0

        ctrl_layout.addWidget(QLabel("Kamera uzaklığı:"), row, 0)
        self._dist_spin = QSpinBox(); self._dist_spin.setRange(10, 200)
        self._dist_spin.setValue(70); self._dist_spin.setSuffix(" %")
        self._dist_spin.valueChanged.connect(self._update_camera)
        ctrl_layout.addWidget(self._dist_spin, row, 1); row += 1

        ctrl_layout.addWidget(QLabel("Yükseklik açısı:"), row, 0)
        self._elev_spin = QSpinBox(); self._elev_spin.setRange(-89, 89)
        self._elev_spin.setValue(25); self._elev_spin.setSuffix(" °")
        self._elev_spin.valueChanged.connect(self._update_camera)
        ctrl_layout.addWidget(self._elev_spin, row, 1); row += 1

        ctrl_layout.addWidget(QLabel("Yatay açı:"), row, 0)
        self._azim_spin = QSpinBox(); self._azim_spin.setRange(0, 359)
        self._azim_spin.setValue(45); self._azim_spin.setSuffix(" °")
        self._azim_spin.valueChanged.connect(self._update_camera)
        ctrl_layout.addWidget(self._azim_spin, row, 1); row += 1

        self._show_mandrel = QCheckBox("Mandrel"); self._show_mandrel.setChecked(True)
        self._show_mandrel.stateChanged.connect(self._update_visibility)
        ctrl_layout.addWidget(self._show_mandrel, row, 0, 1, 2); row += 1

        self._show_path = QCheckBox("Fiber yolu"); self._show_path.setChecked(True)
        self._show_path.stateChanged.connect(self._update_visibility)
        ctrl_layout.addWidget(self._show_path, row, 0, 1, 2); row += 1

        self._show_progress = QCheckBox("İlerleme belirteci"); self._show_progress.setChecked(True)
        self._show_progress.stateChanged.connect(self._update_visibility)
        ctrl_layout.addWidget(self._show_progress, row, 0, 1, 2); row += 1

        ctrl_layout.addWidget(QLabel("Renk modu:"), row, 0)
        self._color_mode = QComboBox()
        self._color_mode.addItems(["Katman", "Gerilim", "Tekdüze"])
        self._color_mode.currentTextChanged.connect(self._rebuild_path_colors)
        ctrl_layout.addWidget(self._color_mode, row, 1); row += 1

        reset_btn = QPushButton("Görünümü Sıfırla")
        reset_btn.clicked.connect(self._reset_view)
        ctrl_layout.addWidget(reset_btn, row, 0, 1, 2); row += 1

        info_lbl = QLabel("Sürükle: döndür\nSağ-sürükle: kaydır\nTekerlek: yakınlaştır")
        info_lbl.setProperty("role", "caption")
        ctrl_layout.addWidget(info_lbl, row, 0, 1, 2); row += 1

        ctrl_layout.setRowStretch(row, 1)
        h_split.addWidget(controls, stretch=1)
        layout.addLayout(h_split, stretch=1)

        self._mesh_item: Optional[gl.GLMeshItem] = None
        self._path_item: Optional[gl.GLLinePlotItem] = None
        self._marker_item: Optional[gl.GLScatterPlotItem] = None

    def set_winding_params(self, params: WindingParams):
        """Silindirik mandrel ve fiber yolunu yeniden oluştur."""
        self._params = params
        self._clear_items()
        R = params.mandrel_R_mm / 1000.0
        L = params.mandrel_L_mm / 1000.0
        verts, faces = cylinder_mesh(R, L, segments=48, rings=8)
        self._add_mesh(verts, faces)
        self._fiber_path_full = helical_fiber_path(params, n_points_per_circuit=40)
        self._rebuild_path_colors()
        self._marker_item = gl.GLScatterPlotItem(
            pos=np.array([[0, 0, 0]]),
            color=(1.0, 0.7, 0.0, 1.0),
            size=10.0)
        self._gl.addItem(self._marker_item)
        max_dim = max(R * 2.5, L * 1.5)
        self._gl.setCameraPosition(distance=max_dim)

    def set_cam_path(self, profile, path):
        """
        CAM yolunu görüntüle: profile = MandrelProfile, path = WindingPath.
        Silindirik olmayan mandrel profilleri için kullanılır.
        """
        self._params = None
        self._clear_items()

        # Dönel yüzey ağı
        try:
            z_arr = np.asarray(profile.z_mm, dtype=np.float64)
            r_arr = np.asarray(profile.r_mm, dtype=np.float64)
            # Her 4 noktadan birini al (çok yoğun profil varsa)
            step = max(1, len(z_arr) // 80)
            verts, faces = revolution_mesh(z_arr[::step], r_arr[::step], segments=48)
            self._add_mesh(verts, faces)
        except Exception:
            pass

        # Fiber yolunu 3D'ye çevir
        try:
            pts = path_to_3d(
                np.asarray(profile.z_mm),
                np.asarray(profile.r_mm),
                path.points
            )
            self._fiber_path_full = pts
            self._rebuild_path_colors()
        except Exception:
            self._fiber_path_full = None

        self._marker_item = gl.GLScatterPlotItem(
            pos=np.array([[0, 0, 0]]),
            color=(1.0, 0.7, 0.0, 1.0),
            size=10.0)
        self._gl.addItem(self._marker_item)

        try:
            max_r = float(np.asarray(profile.r_mm).max()) / 1000.0
            length = float(profile.length_mm) / 1000.0
            max_dim = max(max_r * 2.5, length * 1.5)
            self._gl.setCameraPosition(distance=max_dim)
        except Exception:
            pass

    def _clear_items(self):
        for attr in ('_mesh_item', '_path_item', '_marker_item'):
            item = getattr(self, attr, None)
            if item is not None:
                self._gl.removeItem(item)
            setattr(self, attr, None)

    def _add_mesh(self, verts, faces):
        mesh_data = gl.MeshData(vertexes=verts, faces=faces)
        self._mesh_item = gl.GLMeshItem(
            meshdata=mesh_data,
            smooth=False,
            color=(0.4, 0.45, 0.55, 0.4),
            shader='balloon',
            glOptions='additive')
        self._gl.addItem(self._mesh_item)

    def _rebuild_path_colors(self):
        if self._fiber_path_full is None: return
        if self._path_item is not None:
            self._gl.removeItem(self._path_item)
        n = len(self._fiber_path_full)
        mode = self._color_mode.currentText() if hasattr(self, '_color_mode') else "Katman"
        n_layers = getattr(self._params, 'n_layers', 4) if self._params else 4

        if mode == "Katman":
            n_per_layer = n // max(n_layers, 1)
            colors = np.zeros((n, 4), dtype=np.float32)
            for i in range(n):
                layer = min(n_layers - 1, i // max(n_per_layer, 1))
                if self._highlighted_layer >= 0 and layer == self._highlighted_layer:
                    colors[i] = [1.0, 1.0, 0.0, 1.0]  # bright yellow highlight
                else:
                    alpha = 0.3 if self._highlighted_layer >= 0 else 0.9
                    r, g, b = _hsv_to_rgb(layer / max(n_layers, 1), 0.85, 1.0)
                    colors[i] = [r, g, b, alpha]
        elif mode == "Gerilim":
            colors = np.zeros((n, 4), dtype=np.float32)
            for i in range(n):
                frac = i / max(n - 1, 1)
                r, g, b = _hsv_to_rgb(0.33 * (1 - frac), 0.9, 1.0)
                colors[i] = [r, g, b, 0.9]
        else:
            colors = np.tile(np.array([0.4, 0.85, 0.7, 0.8], dtype=np.float32), (n, 1))

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
        self._latest_frame = f
        if self._params is None: return
        x_frac = max(0, min(1, f.x_mm / max(self._params.mandrel_L_mm, 1)))
        n_layers_done = int(f.a_deg / 360) % max(self._params.n_layers, 1)
        self._current_progress = max(0, min(1,
            (n_layers_done + x_frac) / max(self._params.n_layers, 1)))

    def _refresh_view(self):
        if (self._fiber_path_full is not None and
                self._marker_item is not None and
                len(self._fiber_path_full) > 0):
            idx = int(self._current_progress * (len(self._fiber_path_full) - 1))
            self._marker_item.setData(pos=self._fiber_path_full[idx:idx + 1])

        import time as _t
        self._fps_frames += 1
        elapsed = _t.monotonic() - self._fps_t0
        if elapsed > 1.0:
            self._fps = self._fps_frames / elapsed
            self._fps_lbl.setText(f"3D: {self._fps:.1f} FPS")
            self._fps_frames = 0
            self._fps_t0 = _t.monotonic()

    @Slot(int)
    def highlight_layer(self, layer_idx: int):
        """Belirtilen katmanı sarı ile vurgula; -1 vurguyu kaldırır."""
        self._highlighted_layer = layer_idx
        self._rebuild_path_colors()


def _hsv_to_rgb(h, s, v):
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
