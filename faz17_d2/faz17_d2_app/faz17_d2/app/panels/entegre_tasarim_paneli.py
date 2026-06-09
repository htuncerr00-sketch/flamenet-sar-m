"""
panels/entegre_tasarim_paneli.py — Entegre CAM Tasarım Merkezi (Faz 11)
=======================================================================
Manuel Dizilim + CAM Üretici + Gerçekçi 3D Makine Görünüşü tek sayfada.

Birim sistemi (3D view):  METRE  (mm / 1000 dönüşümü)
Birim sistemi (UI):       MM

Yerleşim:
  Sol (285px)               |  Sağ üst: 3D Makine Görünüşü
  ─ Kalıp Ölçüleri          |  (makine çerçevesi + mandrel + fiber yolları)
  ─ Fiber & Sarma           |
  ─ [+ Helisel/Hoop/Polar]  |
  ─ [Hesapla] [G-kod Üret]  |
  ──────────────────────────┼──────────────────────
  Alt sol: Katman Tablosu  |  Alt sağ: G-kod Çıktı
"""
from __future__ import annotations

import math
import os
import sys
from typing import Optional, List

import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer, Slot
from PySide6.QtGui import QColor, QBrush, QFont
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFormLayout, QSplitter,
    QGroupBox, QLabel, QComboBox, QPushButton, QDoubleSpinBox,
    QSpinBox, QFileDialog, QTextEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QTabWidget, QScrollArea,
    QSizePolicy, QFrame, QProgressBar, QMessageBox, QApplication,
)
import pyqtgraph.opengl as gl
from pyqtgraph.Qt import QtGui

from ..themes.dark_industrial import COLOR

# ── Arka plan CAM motor import'u ─────────────────────────────────────────────

def _try_backend():
    try:
        sys.path.insert(0, os.path.join(
            os.path.dirname(__file__), "../../../../faz17_d1/faz17_d1_backend"))
        from faz17_d1.core.geometry_engine import MandrelProfile
        from faz17_d1.core.path_generator import WindingPathParams, generate_path
        from faz17_d1.core.motion_planner import plan_motion
        from faz17_d1.core.gcode_postprocessor import MachineConfig, generate_gcode
        return MandrelProfile, WindingPathParams, generate_path, plan_motion, MachineConfig, generate_gcode
    except Exception:
        pass
    try:
        from backend.core.geometry_engine import MandrelProfile
        from backend.core.path_generator import WindingPathParams, generate_path
        from backend.core.motion_planner import plan_motion
        from backend.core.gcode_postprocessor import MachineConfig, generate_gcode
        return MandrelProfile, WindingPathParams, generate_path, plan_motion, MachineConfig, generate_gcode
    except Exception:
        return (None,) * 6

# ═══════════════════════════════════════════════════════════════════════════════
# 3D Mesh yardımcıları  (birim: METRE)
# ═══════════════════════════════════════════════════════════════════════════════

def _box_mesh(x0, y0, z0, x1, y1, z1, color, edges=True):
    """Katı kutu GLMeshItem üret."""
    v = np.array([
        [x0,y0,z0],[x1,y0,z0],[x1,y1,z0],[x0,y1,z0],
        [x0,y0,z1],[x1,y0,z1],[x1,y1,z1],[x0,y1,z1],
    ], dtype=np.float32)
    f = np.array([
        [0,2,1],[0,3,2],  # -Y yüz
        [4,5,6],[4,6,7],  # +Y yüz
        [0,1,5],[0,5,4],  # -Z yüz
        [3,7,6],[3,6,2],  # +Z yüz
        [0,4,7],[0,7,3],  # -X yüz
        [1,2,6],[1,6,5],  # +X yüz
    ], dtype=np.int32)
    md = gl.MeshData(vertexes=v, faces=f)
    return gl.GLMeshItem(meshdata=md, color=color, smooth=False,
                          drawEdges=edges,
                          edgeColor=(0.06, 0.06, 0.09, 0.90))


def _cyl_mesh(x0, x1, r, n=40, color=(0.5, 0.6, 0.7, 0.55)):
    """X ekseni boyunca silindir (birim: metre)."""
    th = np.linspace(0, 2*np.pi, n, endpoint=False, dtype=np.float32)
    y_r = r * np.cos(th)
    z_r = r * np.sin(th)
    v = np.vstack([
        np.column_stack([np.full(n, float(x0), dtype=np.float32), y_r, z_r]),
        np.column_stack([np.full(n, float(x1), dtype=np.float32), y_r, z_r]),
    ])
    f = []
    for i in range(n):
        j = (i+1) % n
        f.append([i, j, n+j])
        f.append([i, n+j, n+i])
    md = gl.MeshData(vertexes=v, faces=np.array(f, dtype=np.int32))
    return gl.GLMeshItem(meshdata=md, color=color, smooth=True, drawEdges=False)


def _disc_mesh(x_pos, r, n=40, color=(0.40, 0.50, 0.60, 0.80)):
    """X konumunda dolu disk kapak."""
    th = np.linspace(0, 2*np.pi, n, endpoint=False, dtype=np.float32)
    ry = r * np.cos(th)
    rz = r * np.sin(th)
    v = np.vstack([
        np.column_stack([np.full(n, float(x_pos), dtype=np.float32), ry, rz]),
        np.array([[float(x_pos), 0.0, 0.0]], dtype=np.float32),
    ])
    f = [[n, i, (i+1) % n] for i in range(n)]
    md = gl.MeshData(vertexes=v, faces=np.array(f, dtype=np.int32))
    return gl.GLMeshItem(meshdata=md, color=color, smooth=True, drawEdges=False)


def _build_machine_frame(R_m: float, L_m: float) -> list:
    """
    Filament sarma makinesi çerçevesi — birim metre.

    Koordinat:  X = sarma ekseni, Y = yukarı, Z = yan
    Mandrel:    X=[0, L_m], merkezi Y=0, Z=0

    Görseldeki makineye benzer şekilde:
    ─ İki paralel alt ray (X ekseni boyunca)
    ─ Sol ana gövde (headstock / motor)
    ─ Sağ punta (tailstock)
    ─ Orta taşıyıcı (carriage / fiber iletim)
    ─ Zemin plakası
    """
    items = []

    overhang = max(0.20, L_m * 0.20)   # mandrel dışına taşma
    xL = -overhang
    xR = L_m + overhang

    # ── Alt raylar (iki adet, X boyunca) ─────────────────────────────────
    rail_y0 = -(R_m + 0.10)   # ray üst yüzeyi: mandrel altı − 100mm
    rail_y1 = rail_y0 - 0.040  # ray alt yüzeyi: 40mm kalınlık
    rail_z_half = R_m + 0.065  # ray merkez Z'si

    C_RAIL = (0.28, 0.30, 0.34, 1.0)

    # Ön ray
    items.append(_box_mesh(xL, rail_y1, rail_z_half - 0.035,
                            xR, rail_y0, rail_z_half + 0.035, C_RAIL))
    # Arka ray
    items.append(_box_mesh(xL, rail_y1, -(rail_z_half + 0.035),
                            xR, rail_y0, -(rail_z_half - 0.035), C_RAIL))

    # ── Enine bağlantı kirişleri ──────────────────────────────────────────
    C_DARK = (0.17, 0.18, 0.22, 1.0)
    beam_z = rail_z_half + 0.035
    for xc in [xL + 0.03, L_m * 0.30, L_m * 0.60, xR - 0.03]:
        items.append(_box_mesh(
            xc - 0.018, rail_y1 - 0.025, -beam_z,
            xc + 0.018, rail_y0,          beam_z, C_DARK))

    # ── Headstock (sol, motor + mandrel tutucu) ─────────────────────────
    C_HS = (0.18, 0.21, 0.28, 1.0)
    hs_z = R_m + 0.060
    hs_x0 = xL
    hs_x1 = -0.025
    hs_y0 = rail_y1 - 0.035
    hs_y1 = R_m + 0.045

    # Ana gövde
    items.append(_box_mesh(hs_x0, hs_y0, -hs_z, hs_x1, hs_y1, hs_z, C_HS))

    # Motor kutusu (üste yerleştirilmiş)
    items.append(_box_mesh(hs_x0 + 0.02, hs_y1,         -R_m * 0.55,
                            hs_x1 - 0.01, hs_y1 + 0.085,  R_m * 0.55,
                            (0.14, 0.16, 0.20, 1.0)))

    # Chuck (mandrel tutucu disk)
    items.append(_disc_mesh(hs_x1, R_m * 0.82,
                             color=(0.22, 0.24, 0.30, 1.0)))

    # ── Tailstock (sağ, punta) ────────────────────────────────────────────
    C_TS = (0.22, 0.24, 0.30, 1.0)
    ts_z = R_m + 0.045
    ts_x0 = L_m + 0.025
    ts_x1 = xR
    ts_y0 = rail_y1 - 0.030
    ts_y1 = R_m + 0.038

    items.append(_box_mesh(ts_x0, ts_y0, -ts_z, ts_x1, ts_y1, ts_z, C_TS))
    items.append(_disc_mesh(ts_x0, R_m * 0.72,
                             color=(0.28, 0.30, 0.36, 1.0)))

    # ── Carriage / Taşıyıcı (fiber iletim sistemi) ────────────────────────
    C_CAR  = (0.42, 0.22, 0.14, 1.0)   # pas-turuncu
    C_ARM  = (0.48, 0.26, 0.16, 1.0)
    C_SPL  = (0.68, 0.65, 0.18, 0.92)  # makara (sarı-altın)

    car_x  = L_m * 0.35               # taşıyıcı X konumu
    car_hw = max(0.055, L_m * 0.070)  # yarı-genişlik
    car_z  = R_m + 0.072

    # Alt kısım (ray sürücüsü)
    items.append(_box_mesh(
        car_x - car_hw, rail_y1 - 0.020, -car_z,
        car_x + car_hw, rail_y0 + 0.010,  car_z, C_CAR))

    # Orta dikey kolon
    col_hw = car_hw * 0.28
    col_y1 = rail_y0 + R_m + 0.15
    items.append(_box_mesh(
        car_x - col_hw, rail_y0,  -col_hw,
        car_x + col_hw, col_y1,    col_hw, C_ARM))

    # Üst yatay kol (payout arm)
    arm_y = col_y1
    arm_z = R_m + 0.035
    items.append(_box_mesh(
        car_x - car_hw * 0.50, arm_y,          -arm_z,
        car_x + car_hw * 0.50, arm_y + 0.020,   arm_z, C_ARM))

    # Fiber makarası (silindir — payout head)
    spool_x0 = car_x - 0.022
    spool_x1 = car_x + 0.022
    spool_y  = arm_y + 0.011   # kolun üzerinde
    spool_r  = 0.028
    # Makara: silindir (X boyunca küçük)
    items.append(_box_mesh(
        spool_x0, spool_y,           -spool_r,
        spool_x1, spool_y + spool_r * 2, spool_r, C_SPL))

    # Fiber kılavuzu (ince rod)
    guide_z = 0.0  # merkeze yönlendirme hattı (gösterimlik)
    items.append(_box_mesh(
        car_x - 0.004, spool_y + spool_r * 2, guide_z - 0.004,
        car_x + 0.004, arm_y + R_m + 0.18,   guide_z + 0.004,
        (0.70, 0.70, 0.75, 0.60)))

    # ── Zemin plakası ─────────────────────────────────────────────────────
    floor_y = rail_y1 - 0.048
    floor_z = R_m + 0.130
    items.append(_box_mesh(
        xL - 0.010, floor_y - 0.020, -floor_z,
        xR + 0.010, floor_y,          floor_z,
        (0.12, 0.12, 0.15, 1.0)))

    return items


def _path_to_3d_lines(path_points, z_mm_profile, r_mm_profile) -> List[np.ndarray]:
    """WindingPoint listesini 3D çizgi segmentlerine dönüştür (birim: metre)."""
    if not path_points:
        return []

    z_arr = np.asarray(z_mm_profile, dtype=np.float64)
    r_arr = np.asarray(r_mm_profile, dtype=np.float64)
    z_center = (z_arr[0] + z_arr[-1]) / 2.0

    by_layer = {}
    for pt in path_points:
        by_layer.setdefault(pt.layer, []).append(pt)

    result = []
    for layer_idx in sorted(by_layer):
        pts_3d = []
        for pt in by_layer[layer_idx]:
            z_val = float(pt.x_mm)
            a_rad = math.radians(float(pt.a_deg))
            r_val = float(np.interp(z_val, z_arr, r_arr)) / 1000.0
            xm = (z_val - z_center) / 1000.0 + (z_arr[-1] - z_arr[0]) / 2000.0
            ym = r_val * math.cos(a_rad)
            zm = r_val * math.sin(a_rad)
            pts_3d.append([xm, ym, zm])

        if len(pts_3d) < 2:
            continue
        arr = np.array(pts_3d, dtype=np.float32)
        # Downsample for performance
        if len(arr) > 4000:
            step = len(arr) // 4000 + 1
            arr = arr[::step]
        result.append(arr)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 3D Makine Widget'ı
# ═══════════════════════════════════════════════════════════════════════════════

_FIBER_COLORS = [
    (1.0, 0.30, 0.80, 0.90),  # magenta
    (0.30, 0.85, 1.00, 0.90),  # cyan
    (1.00, 0.85, 0.25, 0.90),  # altın
    (0.40, 1.00, 0.55, 0.90),  # yeşil
    (0.90, 0.50, 0.20, 0.90),  # turuncu
]


class _MachineGLView(gl.GLViewWidget):
    """
    Filament sarma makinesi 3D görünüşü.

    Mandrel boyutu değişince _rebuild() çağrılır ve tüm sahne yenilenir.
    Birim: METRE (mm / 1000).
    """

    def __init__(self, parent=None):
        try:
            super().__init__(parent, rotationMethod='quaternion')
        except TypeError:
            super().__init__(parent)

        self.setBackgroundColor((0.06, 0.07, 0.10, 1.0))

        # Zemin ızgarası
        self._grid = gl.GLGridItem()
        self._grid.setSize(x=4.0, y=4.0)
        self._grid.setSpacing(x=0.10, y=0.10)
        self._grid.setColor((0.20, 0.20, 0.25, 0.45))
        self.addItem(self._grid)

        self._mandrel_items: list = []
        self._frame_items:   list = []
        self._fiber_items:   list = []

        # Başlangıç sahnesi
        self._R_m = 0.050
        self._L_m = 0.300
        self._rebuild_scene()

    # ── Dışarıdan çağrılan API ───────────────────────────────────────────────

    def update_mandrel(self, diameter_mm: float, length_mm: float,
                       mandrel_type: str = "Silindir",
                       dome_h_mm: float = 30.0) -> None:
        self._R_m = max(0.005, diameter_mm / 2000.0)
        self._L_m = max(0.010, length_mm / 1000.0)
        self._rebuild_scene()

    def update_fiber_paths(self, profile, path) -> None:
        for item in self._fiber_items:
            self.removeItem(item)
        self._fiber_items.clear()

        if path is None or profile is None:
            return

        try:
            layers_3d = _path_to_3d_lines(
                path.points,
                np.asarray(profile.z_mm),
                np.asarray(profile.r_mm),
            )
        except Exception:
            return

        for i, pts in enumerate(layers_3d):
            if len(pts) < 2:
                continue
            col = _FIBER_COLORS[i % len(_FIBER_COLORS)]
            line = gl.GLLinePlotItem(pos=pts, color=col, width=1.5, antialias=True)
            self.addItem(line)
            self._fiber_items.append(line)

    def clear_fiber_paths(self) -> None:
        for item in self._fiber_items:
            self.removeItem(item)
        self._fiber_items.clear()

    # ── İç rebuild ───────────────────────────────────────────────────────────

    def _rebuild_scene(self) -> None:
        R = self._R_m
        L = self._L_m

        # Eski nesneleri kaldır
        for item in self._mandrel_items + self._frame_items:
            self.removeItem(item)
        self._mandrel_items.clear()
        self._frame_items.clear()

        # Mandrel — yarı saydam mavi-gri silindir
        x0 = 0.0
        x1 = L
        cyl = _cyl_mesh(x0, x1, R, n=64,
                         color=(0.50, 0.62, 0.76, 0.50))
        self.addItem(cyl)
        self._mandrel_items.append(cyl)

        # Mandrel kapakları
        for xc in [x0, x1]:
            cap = _disc_mesh(xc, R, color=(0.42, 0.54, 0.66, 0.70))
            self.addItem(cap)
            self._mandrel_items.append(cap)

        # Makine çerçevesi
        frame_items = _build_machine_frame(R, L)
        for item in frame_items:
            self.addItem(item)
        self._frame_items.extend(frame_items)

        # Zemin ızgarası konumu güncelle
        self._grid.resetTransform()
        floor_y = -(R + 0.155)
        self._grid.translate(L / 2.0, floor_y, 0.0)

        # Kamera güncelle
        self._fit_camera(R, L)

    def _fit_camera(self, R: float, L: float) -> None:
        dist = max(L * 1.6, R * 8.0)
        cx = L / 2.0
        try:
            self.opts['center'] = QtGui.QVector3D(cx, 0.0, 0.0)
        except Exception:
            pass
        try:
            q = QtGui.QQuaternion.fromEulerAngles(-20.0, 0.0, 40.0)
            self.opts['rotation'] = q
        except Exception:
            pass
        self.opts['distance'] = dist
        self.update()


# ═══════════════════════════════════════════════════════════════════════════════
# Arka plan hesaplama işçisi
# ═══════════════════════════════════════════════════════════════════════════════

class _CalcWorker(QObject):
    finished = Signal(object, object, object)  # (path, profile, layer_paths_or_None)
    error    = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self._fn = fn
        self._args = args

    def run(self):
        try:
            r = self._fn(*self._args)
            self.finished.emit(*r)
        except Exception as exc:
            self.error.emit(str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# Stil sabitleri
# ═══════════════════════════════════════════════════════════════════════════════

_S_GRP = (
    "QGroupBox{{"
    "color:{fg};background:{bg};border:1px solid {bd};"
    "border-radius:4px;margin-top:10px;font-weight:bold;}}"
    "QGroupBox::title{{subcontrol-origin:margin;left:8px;top:0px;"
    "padding:0 6px;color:{ac};}}"
).format(fg=COLOR["text_primary"], bg=COLOR["bg_panel"],
         bd=COLOR["border"], ac=COLOR["accent_bright"])

_S_BTN_PRI = (
    "QPushButton{{background:{bg};color:{fg};padding:7px 10px;"
    "border:1px solid {bd};border-radius:3px;font-weight:bold;"
    "text-align:left;}}"
    "QPushButton:hover{{background:{hv};}}"
    "QPushButton:disabled{{color:{dt};background:{bgw};}}"
).format(bg=COLOR["bg_widget"], fg=COLOR["text_primary"],
         bd=COLOR["border"], hv=COLOR["bg_hover"],
         dt=COLOR["text_disabled"], bgw=COLOR["bg_panel"])

_S_BTN_ACT = (
    "QPushButton{{background:#1a3a5a;color:{fg};padding:8px 10px;"
    "border:1px solid #2a5a8a;border-radius:3px;font-weight:bold;}}"
    "QPushButton:hover{{background:#2a4a6a;}}"
    "QPushButton:disabled{{color:{dt};background:{bgw};}}"
).format(fg=COLOR["accent_bright"],
         dt=COLOR["text_disabled"], bgw=COLOR["bg_panel"])

_S_BTN_GEN = (
    "QPushButton{{background:#1f3d20;color:#70ff80;padding:10px 10px;"
    "border:1px solid #2a6a30;border-radius:4px;"
    "font-weight:bold;font-size:13px;}}"
    "QPushButton:hover{{background:#2a5030;}}"
    "QPushButton:disabled{{color:{dt};background:{bgw};}}"
).format(dt=COLOR["text_disabled"], bgw=COLOR["bg_panel"])

_S_TBL = (
    "QTableWidget{{background:{bg};color:{fg};gridline-color:{bd};"
    "selection-background-color:{sel};}}"
    "QHeaderView::section{{background:{hdr};color:{ac};"
    "padding:5px;border:none;border-right:1px solid {bd};font-weight:bold;}}"
).format(bg=COLOR["bg_widget"], fg=COLOR["text_primary"],
         bd=COLOR["border"], sel=COLOR["bg_selected"],
         hdr=COLOR["bg_window"], ac=COLOR["accent_bright"])

_S_SPIN = (
    "QDoubleSpinBox,QSpinBox{{background:{bg};color:{fg};"
    "border:1px solid {bd};border-radius:2px;padding:2px;}}"
).format(bg=COLOR["bg_widget"], fg=COLOR["text_primary"], bd=COLOR["border"])

_S_COMBO = (
    "QComboBox{{background:{bg};color:{fg};border:1px solid {bd};"
    "border-radius:2px;padding:2px;}}"
).format(bg=COLOR["bg_widget"], fg=COLOR["text_primary"], bd=COLOR["border"])

_V_STYLE = (
    "color:{fg};font-family:'Consolas','Courier New',monospace;"
    "font-size:12px;padding:2px;"
).format(fg=COLOR["text_primary"])


# ═══════════════════════════════════════════════════════════════════════════════
# Ana Panel
# ═══════════════════════════════════════════════════════════════════════════════

COL_TIP   = 0
COL_ALPHA = 1
COL_TOW   = 2
COL_N     = 3
COL_DEL   = 4
_N_COLS   = 5


class EntegreTasarimPaneli(QWidget):
    """
    Entegre CAM Tasarım Merkezi.

    Manuel katman dizilimi + CAM yol üretimi + G-kod çıktısı +
    gerçekçi 3D makine görünüşü tek bir sayfada.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stl_path: Optional[str] = None
        self._winding_path = None
        self._profile      = None
        self._gcode_text   = ""
        self._worker_thread: Optional[QThread] = None
        self._backend = _try_backend()
        self._backend_ok = self._backend[0] is not None
        self._build_ui()

    # ── UI inşası ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        main_split = QSplitter(Qt.Horizontal)
        main_split.setHandleWidth(4)

        # Sol panel
        left = self._build_left()
        main_split.addWidget(left)

        # Sağ panel (3D + alt)
        right = self._build_right()
        main_split.addWidget(right)

        main_split.setSizes([285, 900])
        root.addWidget(main_split)

    def _build_left(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumWidth(300)
        scroll.setMinimumWidth(260)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea{{background:{COLOR['bg_panel']};"
            f"border:none;}}"
        )

        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        # ── Kalıp (Mandrel) ──────────────────────────────────────────────────
        grp_m = QGroupBox("🔩 Kalıp (Mandrel)")
        grp_m.setStyleSheet(_S_GRP)
        fm = QFormLayout(grp_m)
        fm.setLabelAlignment(Qt.AlignRight)
        fm.setContentsMargins(8, 16, 8, 8)
        fm.setSpacing(6)

        self._cb_type = QComboBox()
        self._cb_type.setStyleSheet(_S_COMBO)
        for t in ["Silindir", "Konik", "Kubbeli Silindir", "STL'den"]:
            self._cb_type.addItem(t)
        self._cb_type.currentIndexChanged.connect(self._on_mandrel_type_changed)
        fm.addRow("Tip:", self._cb_type)

        self._sp_diam = QDoubleSpinBox()
        self._sp_diam.setRange(10, 2000); self._sp_diam.setValue(100.0)
        self._sp_diam.setSuffix(" mm"); self._sp_diam.setDecimals(1)
        self._sp_diam.setStyleSheet(_S_SPIN)
        self._sp_diam.valueChanged.connect(self._on_mandrel_changed)
        fm.addRow("Çap:", self._sp_diam)

        self._sp_len = QDoubleSpinBox()
        self._sp_len.setRange(10, 5000); self._sp_len.setValue(300.0)
        self._sp_len.setSuffix(" mm"); self._sp_len.setDecimals(1)
        self._sp_len.setStyleSheet(_S_SPIN)
        self._sp_len.valueChanged.connect(self._on_mandrel_changed)
        fm.addRow("Uzunluk:", self._sp_len)

        self._sp_cone = QDoubleSpinBox()
        self._sp_cone.setRange(0.1, 45); self._sp_cone.setValue(10.0)
        self._sp_cone.setSuffix(" °"); self._sp_cone.setDecimals(1)
        self._sp_cone.setStyleSheet(_S_SPIN)
        self._sp_cone.setVisible(False)
        self._sp_cone_lbl = QLabel("Konik Açı:")
        fm.addRow(self._sp_cone_lbl, self._sp_cone)

        self._sp_dome = QDoubleSpinBox()
        self._sp_dome.setRange(5, 500); self._sp_dome.setValue(30.0)
        self._sp_dome.setSuffix(" mm"); self._sp_dome.setDecimals(1)
        self._sp_dome.setStyleSheet(_S_SPIN)
        self._sp_dome.setVisible(False)
        self._sp_dome_lbl = QLabel("Kubbe Yük.:")
        fm.addRow(self._sp_dome_lbl, self._sp_dome)

        self._btn_stl = QPushButton("📂 STL Yükle")
        self._btn_stl.setStyleSheet(_S_BTN_PRI)
        self._btn_stl.setVisible(False)
        self._btn_stl.clicked.connect(self._on_stl_browse)
        self._lbl_stl = QLabel("─ dosya seçilmedi ─")
        self._lbl_stl.setStyleSheet(f"color:{COLOR['text_secondary']};font-size:10px;")
        self._lbl_stl.setVisible(False)
        fm.addRow("", self._btn_stl)
        fm.addRow("", self._lbl_stl)

        v.addWidget(grp_m)

        # ── Fiber & Sarma ─────────────────────────────────────────────────────
        grp_f = QGroupBox("🧵 Fiber & Sarma Parametreleri")
        grp_f.setStyleSheet(_S_GRP)
        ff = QFormLayout(grp_f)
        ff.setLabelAlignment(Qt.AlignRight)
        ff.setContentsMargins(8, 16, 8, 8)
        ff.setSpacing(6)

        self._sp_alpha = QDoubleSpinBox()
        self._sp_alpha.setRange(1, 89); self._sp_alpha.setValue(55.0)
        self._sp_alpha.setSuffix(" °"); self._sp_alpha.setDecimals(1)
        self._sp_alpha.setStyleSheet(_S_SPIN)
        ff.addRow("Sarma Açısı α:", self._sp_alpha)

        self._sp_tow = QDoubleSpinBox()
        self._sp_tow.setRange(1, 50); self._sp_tow.setValue(6.0)
        self._sp_tow.setSuffix(" mm"); self._sp_tow.setDecimals(2)
        self._sp_tow.setStyleSheet(_S_SPIN)
        ff.addRow("Fitil Genişliği:", self._sp_tow)

        self._sp_nlayers = QSpinBox()
        self._sp_nlayers.setRange(1, 20); self._sp_nlayers.setValue(4)
        self._sp_nlayers.setStyleSheet(_S_SPIN)
        ff.addRow("Kat Sayısı:", self._sp_nlayers)

        self._sp_overlap = QDoubleSpinBox()
        self._sp_overlap.setRange(0, 40); self._sp_overlap.setValue(5.0)
        self._sp_overlap.setSuffix(" %"); self._sp_overlap.setDecimals(1)
        self._sp_overlap.setStyleSheet(_S_SPIN)
        ff.addRow("Çakışma:", self._sp_overlap)

        self._cb_strat = QComboBox()
        self._cb_strat.setStyleSheet(_S_COMBO)
        for s in ["Sarmal (Helisel)", "Çevre (Hoop)", "Kutupsal"]:
            self._cb_strat.addItem(s)
        ff.addRow("Strateji:", self._cb_strat)

        self._sp_feed = QDoubleSpinBox()
        self._sp_feed.setRange(5, 500); self._sp_feed.setValue(80.0)
        self._sp_feed.setSuffix(" mm/s"); self._sp_feed.setDecimals(1)
        self._sp_feed.setStyleSheet(_S_SPIN)
        ff.addRow("İlerleme Hızı:", self._sp_feed)

        self._sp_rpm = QDoubleSpinBox()
        self._sp_rpm.setRange(1, 260); self._sp_rpm.setValue(60.0)
        self._sp_rpm.setSuffix(" RPM"); self._sp_rpm.setDecimals(1)
        self._sp_rpm.setStyleSheet(_S_SPIN)
        ff.addRow("İş Mili RPM:", self._sp_rpm)

        v.addWidget(grp_f)

        # ── Hızlı katman ekleme ───────────────────────────────────────────────
        grp_add = QGroupBox("📋 Katman Ekle")
        grp_add.setStyleSheet(_S_GRP)
        ga = QVBoxLayout(grp_add)
        ga.setContentsMargins(8, 16, 8, 8)
        ga.setSpacing(5)

        for label, slot, tip in [
            ("+ Helisel Ekle",    self._on_add_helical,
             "Sarmal/helisel katman ekle (±α)"),
            ("+ Çevre (Hoop) Ekle", self._on_add_hoop,
             "Çevre sarma katmanı ekle (~90°)"),
            ("+ Polar Ekle",     self._on_add_polar,
             "Kutupsal sarma katmanı ekle (~12°)"),
        ]:
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setStyleSheet(_S_BTN_PRI)
            btn.clicked.connect(slot)
            ga.addWidget(btn)

        v.addWidget(grp_add)

        # ── Hesapla / G-kod ──────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{COLOR['border']};")
        v.addWidget(sep)

        self._btn_calc = QPushButton("▶  Yolu Hesapla & 3D Güncelle")
        self._btn_calc.setStyleSheet(_S_BTN_ACT)
        self._btn_calc.setToolTip(
            "CAM yolunu hesapla ve 3D makinede fiber yollarını göster")
        self._btn_calc.clicked.connect(self._on_calculate)
        v.addWidget(self._btn_calc)

        self._btn_gcode = QPushButton("💾  G-kod Üret & Kaydet")
        self._btn_gcode.setStyleSheet(_S_BTN_GEN)
        self._btn_gcode.setEnabled(False)
        self._btn_gcode.setToolTip("Makineye hazır G-kod dosyası üret")
        self._btn_gcode.clicked.connect(self._on_generate_gcode)
        v.addWidget(self._btn_gcode)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setFixedHeight(5)
        self._progress.setStyleSheet(
            f"QProgressBar{{background:{COLOR['bg_widget']};border:none;border-radius:2px;}}"
            f"QProgressBar::chunk{{background:{COLOR['accent_bright']};border-radius:2px;}}"
        )
        v.addWidget(self._progress)

        self._status_lbl = QLabel("Hazır")
        self._status_lbl.setStyleSheet(
            f"color:{COLOR['text_secondary']};font-size:11px;padding:2px;")
        self._status_lbl.setWordWrap(True)
        v.addWidget(self._status_lbl)

        v.addStretch()

        scroll.setWidget(inner)
        return scroll

    def _build_right(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        vsplit = QSplitter(Qt.Vertical)

        # 3D makine görünüşü
        self._gl = _MachineGLView()
        self._gl.setMinimumHeight(350)
        vsplit.addWidget(self._gl)

        # Alt: katman tablosu + G-kod
        bottom = self._build_bottom()
        vsplit.addWidget(bottom)

        vsplit.setSizes([580, 220])
        v.addWidget(vsplit)
        return w

    def _build_bottom(self) -> QWidget:
        tabs = QTabWidget()
        tabs.setStyleSheet(
            f"QTabWidget::pane{{background:{COLOR['bg_panel']};"
            f"border:1px solid {COLOR['border']};}}"
            f"QTabBar::tab{{background:{COLOR['bg_widget']};color:{COLOR['text_secondary']};"
            f"padding:5px 12px;border:1px solid {COLOR['border']};}}"
            f"QTabBar::tab:selected{{background:{COLOR['bg_selected']};"
            f"color:{COLOR['text_primary']};}}"
        )

        # ── Katman tablosu ────────────────────────────────────────────────────
        tbl_w = QWidget()
        tbl_v = QVBoxLayout(tbl_w)
        tbl_v.setContentsMargins(4, 4, 4, 4)

        self._layer_table = QTableWidget(0, _N_COLS)
        self._layer_table.setHorizontalHeaderLabels(
            ["Tip", "Açı α (°)", "Fitil (mm)", "Kat", "✕"])
        self._layer_table.setStyleSheet(_S_TBL)
        self._layer_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in [1, 2, 3, 4]:
            self._layer_table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeToContents)
        self._layer_table.setEditTriggers(QAbstractItemView.DoubleClicked)
        self._layer_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._layer_table.setAlternatingRowColors(True)
        self._layer_table.verticalHeader().setVisible(False)

        tbl_v.addWidget(QLabel(
            "Katmanlar  —  Çift tıkla düzenle  |  '+ Helisel/Hoop/Polar' ile ekle"))
        tbl_v.addWidget(self._layer_table)
        tabs.addTab(tbl_w, "📋 Katman Dizilimi")

        # ── G-kod çıktı ───────────────────────────────────────────────────────
        gc_w = QWidget()
        gc_v = QVBoxLayout(gc_w)
        gc_v.setContentsMargins(4, 4, 4, 4)

        self._gcode_edit = QTextEdit()
        self._gcode_edit.setReadOnly(True)
        self._gcode_edit.setFont(QFont("Consolas,Courier New", 10))
        self._gcode_edit.setStyleSheet(
            f"background:{COLOR['bg_widget']};color:{COLOR['text_primary']};"
            f"border:1px solid {COLOR['border']};")
        self._gcode_edit.setPlaceholderText(
            "G-kod burada görünecek — önce 'Yolu Hesapla' ardından 'G-kod Üret'e tıklayın.")

        gc_btn_row = QHBoxLayout()
        btn_copy = QPushButton("📋 Panoya Kopyala")
        btn_copy.setStyleSheet(_S_BTN_PRI)
        btn_copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self._gcode_edit.toPlainText()))
        btn_save2 = QPushButton("💾 Dosyaya Kaydet")
        btn_save2.setStyleSheet(_S_BTN_ACT)
        btn_save2.clicked.connect(self._on_save_gcode)
        gc_btn_row.addWidget(btn_copy)
        gc_btn_row.addWidget(btn_save2)
        gc_btn_row.addStretch()

        # İstatistik çubukları
        self._stat_lbl = QLabel("─")
        self._stat_lbl.setStyleSheet(_V_STYLE)

        gc_v.addLayout(gc_btn_row)
        gc_v.addWidget(self._stat_lbl)
        gc_v.addWidget(self._gcode_edit)
        tabs.addTab(gc_w, "⚙ G-kod Çıktı")

        return tabs

    # ── Mandrel tipi değişimi ─────────────────────────────────────────────────

    def _on_mandrel_type_changed(self) -> None:
        t = self._cb_type.currentText()
        is_stl  = (t == "STL'den")
        is_cone = (t == "Konik")
        is_dome = (t == "Kubbeli Silindir")

        self._sp_cone.setVisible(is_cone)
        self._sp_cone_lbl.setVisible(is_cone)
        self._sp_dome.setVisible(is_dome)
        self._sp_dome_lbl.setVisible(is_dome)
        self._btn_stl.setVisible(is_stl)
        self._lbl_stl.setVisible(is_stl)
        self._on_mandrel_changed()

    def _on_mandrel_changed(self) -> None:
        """Mandrel boyutu değişince 3D sahnyi güncelle."""
        d  = self._sp_diam.value()
        l  = self._sp_len.value()
        t  = self._cb_type.currentText()
        dh = self._sp_dome.value()
        self._gl.update_mandrel(d, l, t, dh)
        self._gl.clear_fiber_paths()
        self._btn_gcode.setEnabled(False)
        self._winding_path = None
        self._profile = None

    def _on_stl_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "STL Dosyası Seç", "", "STL Files (*.stl *.STL)")
        if path:
            self._stl_path = path
            self._lbl_stl.setText(os.path.basename(path))
            self._on_mandrel_changed()

    # ── Katman ekleme ─────────────────────────────────────────────────────────

    def _on_add_helical(self) -> None:
        self._add_layer_row("Sarmal", self._sp_alpha.value(),
                             self._sp_tow.value(), self._sp_nlayers.value())

    def _on_add_hoop(self) -> None:
        self._add_layer_row("Hoop", 89.5, self._sp_tow.value(), 1)

    def _on_add_polar(self) -> None:
        self._add_layer_row("Polar", 12.0, self._sp_tow.value(), 1)

    def _add_layer_row(self, ltype: str, alpha: float,
                       tow: float, n: int) -> None:
        tbl = self._layer_table
        row = tbl.rowCount()
        tbl.insertRow(row)

        # Tip combo
        cb = QComboBox()
        cb.setStyleSheet(_S_COMBO)
        cb.addItems(["Sarmal", "Hoop", "Polar"])
        cb.setCurrentText(ltype)
        tbl.setCellWidget(row, COL_TIP, cb)

        # Açı
        a_item = QTableWidgetItem(f"{alpha:.1f}")
        a_item.setTextAlignment(Qt.AlignCenter)
        tbl.setItem(row, COL_ALPHA, a_item)

        # Fitil
        t_item = QTableWidgetItem(f"{tow:.2f}")
        t_item.setTextAlignment(Qt.AlignCenter)
        tbl.setItem(row, COL_TOW, t_item)

        # Kat sayısı
        n_item = QTableWidgetItem(str(n))
        n_item.setTextAlignment(Qt.AlignCenter)
        tbl.setItem(row, COL_N, n_item)

        # Sil butonu
        btn_del = QPushButton("✕")
        btn_del.setFixedSize(28, 24)
        btn_del.setStyleSheet(
            "QPushButton{background:#3a1f1f;color:#ff5050;"
            "border:1px solid #5a2a2a;border-radius:2px;}"
            "QPushButton:hover{background:#4a2a2a;}")
        btn_del.clicked.connect(lambda _, r=row: self._del_row(r))
        tbl.setCellWidget(row, COL_DEL, btn_del)

    def _del_row(self, row: int) -> None:
        # Silme butonları satır indeksi kaydıkça güncellenmez —
        # mevcut satır indeksini bul
        tbl = self._layer_table
        for r in range(tbl.rowCount()):
            btn = tbl.cellWidget(r, COL_DEL)
            if btn and btn.sender() == self.sender():
                tbl.removeRow(r)
                return
        # fallback
        if 0 <= row < tbl.rowCount():
            tbl.removeRow(row)

    # ── CAM hesaplama ─────────────────────────────────────────────────────────

    def _on_calculate(self) -> None:
        if self._worker_thread and self._worker_thread.isRunning():
            return
        self._progress.setVisible(True)
        self._btn_calc.setEnabled(False)
        self._status_lbl.setText("Hesaplanıyor…")
        self._gl.clear_fiber_paths()

        thread = QThread()
        worker = _CalcWorker(self._do_calculate)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        worker.finished.connect(self._on_calc_done)
        worker.error.connect(self._on_calc_error)
        thread.finished.connect(thread.deleteLater)
        self._worker_thread = thread
        thread.start()

    def _do_calculate(self):
        (MandrelProfile, WindingPathParams, generate_path,
         plan_motion, MachineConfig, generate_gcode) = self._backend

        if MandrelProfile is None:
            raise RuntimeError(
                "Backend modülleri yüklenemedi.\n"
                "Lütfen uygulamayı faz17_d2/ dizininden başlatın.")

        d_mm = self._sp_diam.value()
        l_mm = self._sp_len.value()
        t    = self._cb_type.currentText()
        dh   = self._sp_dome.value()
        ca   = self._sp_cone.value()

        import math as _m
        if t == "Silindir":
            profile = MandrelProfile.cylinder(l_mm, d_mm / 2.0)
        elif t == "Konik":
            r_end = d_mm / 2.0 + l_mm * _m.tan(_m.radians(ca))
            profile = MandrelProfile.cone(l_mm, d_mm / 2.0, r_end)
        elif t == "Kubbeli Silindir":
            profile = MandrelProfile.dome_cylinder_dome(l_mm, d_mm / 2.0, dh)
        else:
            if not self._stl_path:
                raise RuntimeError("STL dosyası seçilmedi.")
            profile = MandrelProfile.from_stl(self._stl_path)

        # Katman tablosundan dizilim oku (varsa)
        tbl = self._layer_table
        if tbl.rowCount() > 0:
            all_paths = []
            for row in range(tbl.rowCount()):
                cb = tbl.cellWidget(row, COL_TIP)
                ltype = cb.currentText() if cb else "Sarmal"
                try:
                    alpha = float((tbl.item(row, COL_ALPHA) or
                                   type('', (), {'text': lambda s: '55.0'})()).text())
                except Exception:
                    alpha = 55.0
                try:
                    tow_w = float((tbl.item(row, COL_TOW) or
                                   type('', (), {'text': lambda s: '6.0'})()).text())
                except Exception:
                    tow_w = 6.0
                try:
                    n_l = int((tbl.item(row, COL_N) or
                               type('', (), {'text': lambda s: '1'})()).text())
                except Exception:
                    n_l = 1

                strat_map = {"Sarmal": "helical", "Hoop": "hoop", "Polar": "polar"}
                strat = strat_map.get(ltype, "helical")

                pp = WindingPathParams(
                    profile=profile,
                    alpha_deg=alpha,
                    n_layers=max(1, n_l),
                    tow_width_mm=tow_w,
                    overlap_pct=self._sp_overlap.value(),
                    feed_mm_s=self._sp_feed.value(),
                    spindle_rpm=self._sp_rpm.value(),
                    winding_strategy=strat,
                )
                path = generate_path(pp)
                all_paths.append(path)

            first = all_paths[0] if all_paths else None
            return first, profile, all_paths
        else:
            # Tek-açı parametrik mod
            strat_map = {
                "Sarmal (Helisel)": "helical",
                "Çevre (Hoop)":     "hoop",
                "Kutupsal":         "polar",
            }
            strat = strat_map.get(self._cb_strat.currentText(), "helical")
            pp = WindingPathParams(
                profile=profile,
                alpha_deg=self._sp_alpha.value(),
                n_layers=self._sp_nlayers.value(),
                tow_width_mm=self._sp_tow.value(),
                overlap_pct=self._sp_overlap.value(),
                feed_mm_s=self._sp_feed.value(),
                spindle_rpm=self._sp_rpm.value(),
                winding_strategy=strat,
            )
            path = generate_path(pp)
            return path, profile, None

    @Slot(object, object, object)
    def _on_calc_done(self, path, profile, all_paths) -> None:
        self._progress.setVisible(False)
        self._btn_calc.setEnabled(True)
        self._winding_path = path
        self._profile      = profile

        if path is None:
            self._status_lbl.setText("Hesaplama başarısız.")
            return

        try:
            n_pts    = len(path.points)
            circuits = path.n_circuits
            fiber_m  = path.total_fiber_length_mm / 1000.0
            cov      = path.coverage_pct
            t_s      = path.estimated_time_s
            self._status_lbl.setText(
                f"✓ {circuits} devre | {fiber_m:.1f} m fiber | "
                f"{cov:.1f}% kapsama | ~{t_s/60:.1f} dk")
            self._stat_lbl.setText(
                f"Devre: {circuits}   Fiber: {fiber_m:.2f} m   "
                f"Kapsama: {cov:.1f}%   Süre: {t_s/60:.1f} dk")
        except Exception:
            self._status_lbl.setText("Yol hesaplandı.")

        # 3D fiber yollarını güncelle
        self._gl.update_fiber_paths(profile, path)
        self._btn_gcode.setEnabled(True)

    @Slot(str)
    def _on_calc_error(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._btn_calc.setEnabled(True)
        self._status_lbl.setText(f"Hata: {msg}")
        QMessageBox.warning(self, "Hesaplama Hatası", msg)

    # ── G-kod üretimi ─────────────────────────────────────────────────────────

    def _on_generate_gcode(self) -> None:
        if self._winding_path is None or self._profile is None:
            return
        (_, _, _, plan_motion, MachineConfig, generate_gcode) = self._backend
        if plan_motion is None:
            return

        try:
            segs = plan_motion(self._winding_path)
            cfg  = MachineConfig()
            prog = generate_gcode(segs, self._winding_path, cfg)
            self._gcode_text = "\n".join(prog.lines)
            self._gcode_edit.setPlainText(self._gcode_text)
            self._status_lbl.setText(
                f"G-kod hazır: {len(prog.lines)} satır | "
                f"Kapsama: {prog.coverage_pct:.1f}%")
        except Exception as exc:
            QMessageBox.warning(self, "G-kod Hatası", str(exc))

    def _on_save_gcode(self) -> None:
        if not self._gcode_text:
            QMessageBox.information(self, "Bilgi", "Önce G-kod üretin.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "G-kod Dosyasını Kaydet", "sarma.nc",
            "G-code (*.nc *.gcode *.txt);;All files (*)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._gcode_text)
            self._status_lbl.setText(f"Kaydedildi: {os.path.basename(path)}")

    # ── Dış panel entegrasyonu ────────────────────────────────────────────────

    def set_layer_stack(self, stack) -> None:
        """KatmanDizilimPaneli / TabakaYoneticisi'nden yığın al."""
        layers = []
        if isinstance(stack, dict):
            layers = stack.get("layers", [])
        elif hasattr(stack, "layers"):
            for L in stack.layers:
                layers.append({
                    "type": L.type.value if hasattr(L.type, 'value') else str(L.type),
                    "alpha_deg": L.alpha_deg,
                    "fitil_genisligi_mm": L.fitil_genisligi_mm,
                    "n_layers": 1,
                })

        if not layers:
            return

        self._layer_table.setRowCount(0)
        type_map = {
            "helical": "Sarmal", "hoop": "Hoop", "polar": "Polar",
            "skin_finish": "Hoop", "transition": "Sarmal",
        }
        for ld in layers:
            lt = ld.get("layer_type") or ld.get("type", "helical")
            alpha = float(ld.get("alpha_deg", 55.0))
            tow   = float(ld.get("fitil_genisligi_mm", 6.0))
            n_l   = int(ld.get("n_layers", 1))
            self._add_layer_row(type_map.get(lt, "Sarmal"), alpha, tow, n_l)


__all__ = ["EntegreTasarimPaneli"]
