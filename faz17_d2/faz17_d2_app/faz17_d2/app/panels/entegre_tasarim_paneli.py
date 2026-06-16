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
from dataclasses import dataclass as _dataclass
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
    QSlider,
)
import pyqtgraph.opengl as gl
from pyqtgraph.Qt import QtGui

from ..themes.dark_industrial import COLOR
from ..undo_commands import (
    AddRowCommand, DeleteRowCommand, EditRowCellCommand,
    ChangeMandrelCommand, ChangeMachineSettingsCommand,
)

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


def _build_static_frame(R_m: float, L_m: float) -> list:
    """Raylar, headstock, tailstock, zemin — taşıyıcı (carriage) HARİÇ. Birim: metre."""
    items = []
    overhang = max(0.20, L_m * 0.20)
    xL = -overhang
    xR = L_m + overhang
    rail_y0 = -(R_m + 0.10)
    rail_y1 = rail_y0 - 0.040
    rail_z_half = R_m + 0.065
    C_RAIL = (0.28, 0.30, 0.34, 1.0)
    items.append(_box_mesh(xL, rail_y1, rail_z_half - 0.035,
                            xR, rail_y0, rail_z_half + 0.035, C_RAIL))
    items.append(_box_mesh(xL, rail_y1, -(rail_z_half + 0.035),
                            xR, rail_y0, -(rail_z_half - 0.035), C_RAIL))
    C_DARK = (0.17, 0.18, 0.22, 1.0)
    beam_z = rail_z_half + 0.035
    for xc in [xL + 0.03, L_m * 0.30, L_m * 0.60, xR - 0.03]:
        items.append(_box_mesh(xc - 0.018, rail_y1 - 0.025, -beam_z,
                                xc + 0.018, rail_y0,          beam_z, C_DARK))
    C_HS = (0.18, 0.21, 0.28, 1.0)
    hs_z  = R_m + 0.060
    hs_y0 = rail_y1 - 0.035
    hs_y1 = R_m + 0.045
    items.append(_box_mesh(xL, hs_y0, -hs_z, -0.025, hs_y1, hs_z, C_HS))
    items.append(_box_mesh(xL + 0.02, hs_y1, -R_m * 0.55,
                            -0.036, hs_y1 + 0.085, R_m * 0.55,
                            (0.14, 0.16, 0.20, 1.0)))
    items.append(_disc_mesh(-0.025, R_m * 0.82, color=(0.22, 0.24, 0.30, 1.0)))
    C_TS = (0.22, 0.24, 0.30, 1.0)
    ts_z  = R_m + 0.045
    ts_y0 = rail_y1 - 0.030
    ts_y1 = R_m + 0.038
    items.append(_box_mesh(L_m + 0.025, ts_y0, -ts_z, xR, ts_y1, ts_z, C_TS))
    items.append(_disc_mesh(L_m + 0.025, R_m * 0.72, color=(0.28, 0.30, 0.36, 1.0)))
    floor_y = rail_y1 - 0.048
    floor_z = R_m + 0.130
    items.append(_box_mesh(xL - 0.010, floor_y - 0.020, -floor_z,
                            xR + 0.010, floor_y,          floor_z,
                            (0.12, 0.12, 0.15, 1.0)))
    return items


def _build_carriage_at_zero(R_m: float, L_m: float) -> list:
    """Taşıyıcı/nozul montajı X=0 merkezli inşa edilir; çağıran translate eder. Birim: metre."""
    items = []
    car_hw = max(0.055, L_m * 0.070)
    car_z  = R_m + 0.072
    rail_y0 = -(R_m + 0.10)
    rail_y1 = rail_y0 - 0.040
    C_CAR = (0.42, 0.22, 0.14, 1.0)
    C_ARM = (0.48, 0.26, 0.16, 1.0)
    C_SPL = (0.68, 0.65, 0.18, 0.92)
    # Ray sürücüsü
    items.append(_box_mesh(-car_hw, rail_y1 - 0.020, -car_z,
                            car_hw, rail_y0 + 0.010,  car_z, C_CAR))
    # Dikey kolon
    col_hw = car_hw * 0.28
    col_y1 = rail_y0 + R_m + 0.15
    items.append(_box_mesh(-col_hw, rail_y0, -col_hw, col_hw, col_y1, col_hw, C_ARM))
    # Yatay kol (payout arm)
    arm_y = col_y1
    arm_z = R_m + 0.035
    items.append(_box_mesh(-car_hw * 0.50, arm_y,         -arm_z,
                            car_hw * 0.50, arm_y + 0.020,  arm_z, C_ARM))
    # Makara (payout head)
    spool_r = 0.028
    spool_y = arm_y + 0.011
    items.append(_box_mesh(-0.022, spool_y,               -spool_r,
                            0.022, spool_y + spool_r * 2,  spool_r, C_SPL))
    # Fiber kılavuz rod
    items.append(_box_mesh(-0.004, spool_y + spool_r * 2, -0.004,
                            0.004, arm_y + R_m + 0.18,     0.004,
                            (0.70, 0.70, 0.75, 0.60)))
    return items


def _path_to_3d_fast(path_points, z_mm_profile, r_mm_profile,
                      ply_thickness_mm: float = 0.0) -> List[np.ndarray]:
    """Vektörize: WindingPoint listesi → kat başına 3D çizgi dizisi (birim: metre).

    R6 LOD: toplam > 50 000 nokta ise global_step ile indirgenir.
    ply_thickness_mm: her katmana eklenen radyal ofset (Sprint 5 kalınlık).
    """
    if not path_points:
        return []

    z_arr = np.asarray(z_mm_profile, dtype=np.float64)
    r_arr = np.asarray(r_mm_profile, dtype=np.float64)
    z_center   = (z_arr[0] + z_arr[-1]) / 2.0
    half_len_m = (z_arr[-1] - z_arr[0]) / 2000.0

    by_layer: dict = {}
    for pt in path_points:
        by_layer.setdefault(pt.layer, []).append(pt)

    MAX_PTS = 50_000
    total = sum(len(v) for v in by_layer.values())
    global_step = max(1, total // MAX_PTS) if total > MAX_PTS else 1

    result = []
    for layer_idx in sorted(by_layer):
        pts_l = by_layer[layer_idx][::global_step]
        if len(pts_l) < 2:
            continue
        z_vals = np.array([float(p.x_mm)  for p in pts_l], dtype=np.float64)
        a_rads = np.radians(
            np.array([float(p.a_deg) for p in pts_l], dtype=np.float64))
        r_base = np.interp(z_vals, z_arr, r_arr) / 1000.0
        r_vals = r_base + (ply_thickness_mm / 1000.0) * layer_idx
        xm = (z_vals - z_center) / 1000.0 + half_len_m
        ym = r_vals * np.cos(a_rads)
        zm = r_vals * np.sin(a_rads)
        result.append(np.column_stack([xm, ym, zm]).astype(np.float32))

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

        self._mandrel_items:   list = []
        self._frame_items:     list = []   # statik: raylar, headstock, tailstock, zemin
        self._carriage_items:  list = []   # dinamik: X boyunca hareket eder
        self._fiber_items:     list = []   # hesap sonrası statik fiber yolları
        self._anim_fiber_item       = None  # simülasyonda büyüyen GLLinePlotItem
        self._anim_mandrel_angle: float = 0.0
        self._carriage_x_m:  float = 0.0

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

    def update_fiber_paths(self, profile, path,
                           ply_thickness_mm: float = 0.0) -> None:
        for item in self._fiber_items:
            self.removeItem(item)
        self._fiber_items.clear()

        if path is None or profile is None:
            return

        try:
            layers_3d = _path_to_3d_fast(
                path.points,
                np.asarray(profile.z_mm),
                np.asarray(profile.r_mm),
                ply_thickness_mm=ply_thickness_mm,
            )
        except Exception:
            return

        for i, pts in enumerate(layers_3d):
            if len(pts) < 2:
                continue
            col = _FIBER_COLORS[i % len(_FIBER_COLORS)]
            line = gl.GLLinePlotItem(pos=pts, color=col, width=4.0,
                                     antialias=True, mode='line_strip')
            self.addItem(line)
            self._fiber_items.append(line)

    def clear_fiber_paths(self) -> None:
        for item in self._fiber_items:
            self.removeItem(item)
        self._fiber_items.clear()

    def set_head_position(self, xyz: np.ndarray) -> None:
        """Sarım kafası (winding head) işaretçisini güncelle."""
        if not hasattr(self, '_head_item') or self._head_item is None:
            self._head_item = gl.GLScatterPlotItem(
                pos=np.array([[0, 0, 0]], dtype=np.float32),
                size=12, color=(1.0, 0.9, 0.0, 1.0), pxMode=True
            )
            self.addItem(self._head_item)
        self._head_item.setData(pos=np.array([xyz], dtype=np.float32))

    def clear_head(self) -> None:
        if hasattr(self, '_head_item') and self._head_item is not None:
            self.removeItem(self._head_item)
            self._head_item = None

    # ── İç rebuild ───────────────────────────────────────────────────────────

    def _rebuild_scene(self) -> None:
        R = self._R_m
        L = self._L_m

        # Eski tüm nesneleri kaldır
        for item in (self._mandrel_items + self._frame_items
                     + self._carriage_items):
            self.removeItem(item)
        self._mandrel_items.clear()
        self._frame_items.clear()
        self._carriage_items.clear()
        if self._anim_fiber_item is not None:
            self.removeItem(self._anim_fiber_item)
            self._anim_fiber_item = None
        self._anim_mandrel_angle = 0.0
        self._carriage_x_m = L / 2.0

        # Mandrel — yarı saydam silindir
        cyl = _cyl_mesh(0.0, L, R, n=64, color=(0.50, 0.62, 0.76, 0.50))
        self.addItem(cyl)
        self._mandrel_items.append(cyl)
        # Kapaklar
        for xc in [0.0, L]:
            cap = _disc_mesh(xc, R, color=(0.42, 0.54, 0.66, 0.70))
            self.addItem(cap)
            self._mandrel_items.append(cap)
        # Döndürme göstergesi: 0° ve 180°'de çizgiler
        xs = np.linspace(0.0, L, 60, dtype=np.float32)
        for sign in [1.0, -1.0]:
            pts = np.column_stack([xs,
                                   np.full(60, sign * R * 1.003, dtype=np.float32),
                                   np.zeros(60, dtype=np.float32)])
            stripe = gl.GLLinePlotItem(pos=pts, color=(1.0, 0.55, 0.1, 0.9),
                                       width=2, antialias=False)
            self.addItem(stripe)
            self._mandrel_items.append(stripe)

        # Statik çerçeve (raylar, headstock, tailstock, zemin)
        for item in _build_static_frame(R, L):
            self.addItem(item)
            self._frame_items.append(item)

        # Taşıyıcı — başlangıçta mandrel ortasında
        for item in _build_carriage_at_zero(R, L):
            item.translate(self._carriage_x_m, 0, 0)
            self.addItem(item)
            self._carriage_items.append(item)

        # Zemin ızgarası
        self._grid.resetTransform()
        self._grid.translate(L / 2.0, -(R + 0.155), 0.0)

        self._fit_camera(R, L)

    # ── 4-eksen simülasyon API ────────────────────────────────────────────────

    def set_simulation_state(self, a_deg: float, x_mm: float,
                              pts_3d) -> None:
        """4-eksen güncelleme: mandrel döndür, taşıyıcı kaydır, fiber büyüt."""
        # 1. Mandrel dönüşü (X ekseni etrafında kümülatif)
        delta = a_deg - self._anim_mandrel_angle
        if abs(delta) > 0.05:
            for item in self._mandrel_items:
                item.resetTransform()
                item.rotate(a_deg, 1, 0, 0, local=False)
            self._anim_mandrel_angle = a_deg

        # 2. Taşıyıcı X hareketi
        x_m = float(np.clip(x_mm / 1000.0, 0.0, self._L_m))
        dx = x_m - self._carriage_x_m
        if abs(dx) > 1e-5:
            for item in self._carriage_items:
                item.translate(dx, 0, 0)
            self._carriage_x_m = x_m

        # 3. İlerleyen fiber
        if pts_3d is not None and len(pts_3d) >= 2:
            if self._anim_fiber_item is None:
                self._anim_fiber_item = gl.GLLinePlotItem(
                    pos=pts_3d, color=(1.0, 1.0, 0.15, 0.95),
                    width=3.5, antialias=True, mode='line_strip')
                self.addItem(self._anim_fiber_item)
            else:
                self._anim_fiber_item.setData(pos=pts_3d)

        # 4. Nozul işaretçisi
        if pts_3d is not None and len(pts_3d) >= 1:
            self.set_head_position(pts_3d[-1])

    def clear_simulation_state(self) -> None:
        """Simülasyonu sıfırla: büyüyen fiberi kaldır, mandrel + taşıyıcıyı dinlenme konumuna döndür."""
        if self._anim_fiber_item is not None:
            self.removeItem(self._anim_fiber_item)
            self._anim_fiber_item = None
        for item in self._mandrel_items:
            item.resetTransform()
        self._anim_mandrel_angle = 0.0
        target = self._L_m / 2.0
        dx = target - self._carriage_x_m
        for item in self._carriage_items:
            item.translate(dx, 0, 0)
        self._carriage_x_m = target
        self.clear_head()

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
# R1: Worker thread parametreleri — hiçbir Qt nesnesi içermez
# ═══════════════════════════════════════════════════════════════════════════════

@_dataclass
class _ECalcParams:
    """R1: Worker thread'e geçen düz parametreler — hiçbir Qt nesnesi içermez."""
    mandrel_type: str
    diameter_mm: float
    length_mm: float
    cone_angle_deg: float
    dome_h_mm: float
    dome_hr_ratio: float
    stl_path: object  # str or None
    alpha_deg: float
    n_layers: int
    tow_w_mm: float
    overlap_pct: float
    strategy_text: str
    feed_mm_s: float
    spindle_rpm: float
    friction_mu: float
    layer_rows: list  # list of plain dicts from get_layer_rows() — deepcopy


# ═══════════════════════════════════════════════════════════════════════════════
# Arka plan hesaplama işçisi (nesil tabanlı iptal desteğiyle)
# ═══════════════════════════════════════════════════════════════════════════════

class _EWorker(QObject):
    finished = Signal(object, object, object, int)  # (path, profile, all_paths, gen)
    error    = Signal(str, int)                      # (msg, gen)

    def __init__(self, fn, params: "_ECalcParams", gen: int):
        super().__init__()
        self._fn     = fn
        self._params = params
        self._gen    = gen

    def run(self):
        try:
            result = self._fn(self._params)
            self.finished.emit(*result, self._gen)
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}", self._gen)


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

    # Tasarım verisi (katman tablosu) kullanıcı tarafından değiştirildiğinde
    # fırlatılır — proje yöneticisi kirli bayrağı için dinler.
    tasarimDegisti = Signal()

    # Watchdog zaman aşımı (ms)
    _WATCHDOG_MS = 30_000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stl_path: Optional[str] = None
        self._winding_path = None
        self._profile      = None
        self._gcode_text   = ""
        self._worker_thread: Optional[QThread] = None
        self._calc_gen: int = 0
        self._watchdog = None
        self._worker_ref = None
        self._backend = _try_backend()
        self._backend_ok = self._backend[0] is not None
        # Proje yükleme sırasında tasarimDegisti fırlatılmasın
        self._suppress_dirty = False
        # Merkezi undo/redo yığını (MainWindow kurar; yoksa komutlar
        # doğrudan uygulanır)
        self._undo_stack = None
        self._build_ui()
        self._init_param_tracking()
        # Animasyon durumu (4-eksen simülasyon)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)  # ~30 FPS
        self._anim_timer.timeout.connect(self._anim_tick)
        self._anim_xyz:   object = None  # np.ndarray (N,3) float32 — 3D konumlar
        self._anim_a_deg: object = None  # np.ndarray (N,)  float32 — iş mili açısı
        self._anim_x_mm:  object = None  # np.ndarray (N,)  float32 — eksenel konum
        self._anim_idx: int = 0
        self._anim_playing: bool = False

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
        for t in ["Silindir", "Konik", "Kubbeli Silindir", "Elipsoidal Kubbe", "STL'den"]:
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

        self._sp_dome_hr = QDoubleSpinBox()
        self._sp_dome_hr.setRange(0.1, 2.0); self._sp_dome_hr.setValue(0.7)
        self._sp_dome_hr.setSuffix(""); self._sp_dome_hr.setDecimals(2)
        self._sp_dome_hr.setStyleSheet(_S_SPIN)
        self._sp_dome_hr.setVisible(False)
        self._sp_dome_hr_lbl = QLabel("Kubbe H/R:")
        fm.addRow(self._sp_dome_hr_lbl, self._sp_dome_hr)

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

        self._sp_friction = QDoubleSpinBox()
        self._sp_friction.setRange(0.0, 0.5); self._sp_friction.setValue(0.0)
        self._sp_friction.setSuffix(""); self._sp_friction.setDecimals(3)
        self._sp_friction.setToolTip(
            "Sürtünme katsayısı μ\n"
            "0 = geodezik (Clairaut)\n"
            ">0 = non-geodezik (Koussios RK4)")
        self._sp_friction.setStyleSheet(_S_SPIN)
        ff.addRow("Sürtünme μ:", self._sp_friction)

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

        # ── Animasyon Kontrolleri ──────────────────────────────────────────────
        grp_anim = QGroupBox("▶ Sarma Animasyonu")
        grp_anim.setStyleSheet(_S_GRP)
        ga_v = QVBoxLayout(grp_anim)
        ga_v.setContentsMargins(8, 16, 8, 8)
        ga_v.setSpacing(5)

        anim_btn_row = QHBoxLayout()
        self._btn_play = QPushButton("▶ Oynat")
        self._btn_play.setStyleSheet(_S_BTN_PRI)
        self._btn_play.setEnabled(False)
        self._btn_play.clicked.connect(self._on_anim_play)
        self._btn_pause = QPushButton("⏸ Duraklat")
        self._btn_pause.setStyleSheet(_S_BTN_PRI)
        self._btn_pause.setEnabled(False)
        self._btn_pause.clicked.connect(self._on_anim_pause)
        self._btn_stop_anim = QPushButton("⏹ Durdur")
        self._btn_stop_anim.setStyleSheet(_S_BTN_PRI)
        self._btn_stop_anim.setEnabled(False)
        self._btn_stop_anim.setToolTip("Mevcut konumda durdur (sıfırlamaz)")
        self._btn_stop_anim.clicked.connect(self._on_anim_stop)
        self._btn_reset_anim = QPushButton("↩ Sıfırla")
        self._btn_reset_anim.setStyleSheet(_S_BTN_PRI)
        self._btn_reset_anim.setEnabled(False)
        self._btn_reset_anim.clicked.connect(self._on_anim_reset)
        anim_btn_row.addWidget(self._btn_play)
        anim_btn_row.addWidget(self._btn_pause)
        anim_btn_row.addWidget(self._btn_stop_anim)
        anim_btn_row.addWidget(self._btn_reset_anim)
        ga_v.addLayout(anim_btn_row)

        # Hız seçici
        speed_row = QHBoxLayout()
        speed_lbl = QLabel("Hız:")
        speed_lbl.setStyleSheet(f"color:{COLOR['text_secondary']};font-size:10px;")
        self._cb_speed = QComboBox()
        self._cb_speed.setStyleSheet(_S_COMBO)
        for s in ["1×", "2×", "5×", "10×"]:
            self._cb_speed.addItem(s)
        speed_row.addWidget(speed_lbl)
        speed_row.addWidget(self._cb_speed)
        speed_row.addStretch()
        ga_v.addLayout(speed_row)

        self._anim_slider = QSlider(Qt.Horizontal)
        self._anim_slider.setRange(0, 1000)
        self._anim_slider.setValue(0)
        self._anim_slider.setEnabled(False)
        self._anim_slider.sliderMoved.connect(self._on_anim_seek)
        ga_v.addWidget(self._anim_slider)

        self._anim_lbl = QLabel("X: — mm  |  A: —°")
        self._anim_lbl.setStyleSheet(
            f"color:{COLOR['text_secondary']};font-size:10px;")
        ga_v.addWidget(self._anim_lbl)

        v.addWidget(grp_anim)

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
        self._layer_table.itemChanged.connect(self._on_layer_item_changed)

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
        is_stl   = (t == "STL'den")
        is_cone  = (t == "Konik")
        is_dome  = (t == "Kubbeli Silindir")
        is_ellip = (t == "Elipsoidal Kubbe")

        self._sp_cone.setVisible(is_cone)
        self._sp_cone_lbl.setVisible(is_cone)
        self._sp_dome.setVisible(is_dome)
        self._sp_dome_lbl.setVisible(is_dome)
        self._sp_dome_hr.setVisible(is_ellip)
        self._sp_dome_hr_lbl.setVisible(is_ellip)
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

    # ── Undo/Redo altyapısı (Faz 25 Sprint 2) ────────────────────────────────

    def set_undo_stack(self, stack) -> None:
        """MainWindow'daki merkezi QUndoStack'i kur."""
        self._undo_stack = stack

    def _push_cmd(self, cmd) -> None:
        """Komutu yığına it; yığın yoksa doğrudan uygula (standalone mod)."""
        if self._undo_stack is not None:
            self._undo_stack.push(cmd)
        else:
            cmd.redo()

    # ── Komut ilkeleri: tek mutasyon noktaları (sinyal güvenli) ──────────────

    def _cmd_insert_row(self, index: int, rd: dict) -> None:
        """Tablonun `index` konumuna satır ekle (komut redo/undo yolu)."""
        outer = self._suppress_dirty
        self._suppress_dirty = True
        try:
            tbl = self._layer_table
            tbl.insertRow(index)

            cb = QComboBox()
            cb.setStyleSheet(_S_COMBO)
            cb.addItems(["Sarmal", "Hoop", "Polar"])
            cb.setCurrentText(str(rd.get("tip", "Sarmal")))
            tbl.setCellWidget(index, COL_TIP, cb)

            for col, txt in [
                (COL_ALPHA, f"{float(rd.get('alpha_deg', 55.0)):.1f}"),
                (COL_TOW,   f"{float(rd.get('fitil_mm', 6.0)):.2f}"),
                (COL_N,     str(max(1, int(rd.get('n_kat', 1))))),
            ]:
                it = QTableWidgetItem(txt)
                it.setTextAlignment(Qt.AlignCenter)
                # UserRole = son bilinen değer (hücre düzenleme komutlarının
                # eski değeri okuyabilmesi için)
                it.setData(Qt.UserRole, txt)
                tbl.setItem(index, col, it)

            btn_del = QPushButton("✕")
            btn_del.setFixedSize(28, 24)
            btn_del.setStyleSheet(
                "QPushButton{background:#3a1f1f;color:#ff5050;"
                "border:1px solid #5a2a2a;border-radius:2px;}"
                "QPushButton:hover{background:#4a2a2a;}")
            btn_del.clicked.connect(
                lambda _=False, b=btn_del: self._del_row_by_widget(b))
            tbl.setCellWidget(index, COL_DEL, btn_del)
        finally:
            self._suppress_dirty = outer
        if not outer:
            self.tasarimDegisti.emit()

    def _cmd_remove_row(self, index: int) -> None:
        if 0 <= index < self._layer_table.rowCount():
            self._layer_table.removeRow(index)
            if not self._suppress_dirty:
                self.tasarimDegisti.emit()

    def _cmd_set_cell(self, row: int, col: int, text: str) -> None:
        it = self._layer_table.item(row, col)
        if it is None:
            return
        outer = self._suppress_dirty
        self._suppress_dirty = True
        try:
            it.setText(text)
            it.setData(Qt.UserRole, text)
        finally:
            self._suppress_dirty = outer
        if not outer:
            self.tasarimDegisti.emit()

    def _cmd_set_param(self, key: str, value) -> None:
        """Mandrel/makine parametresi widget'ını sinyalsiz güncelle."""
        w, is_mandrel = self._param_map[key]
        w.blockSignals(True)
        try:
            if isinstance(w, QComboBox):
                idx = w.findText(str(value))
                if idx >= 0:
                    w.setCurrentIndex(idx)
            else:
                w.setValue(value)
        finally:
            w.blockSignals(False)
        self._param_last[key] = self._param_value(key)
        if is_mandrel:
            # tip değişimi alan görünürlüklerini de günceller
            if key == "tip":
                self._on_mandrel_type_changed()
            else:
                self._on_mandrel_changed()
        if not self._suppress_dirty:
            self.tasarimDegisti.emit()

    # ── Parametre takibi (ChangeMandrel / ChangeMachineSettings) ────────────

    def _init_param_tracking(self) -> None:
        """Mandrel + makine ayarı widget'larını undo takibine bağla."""
        self._param_map = {
            # key: (widget, is_mandrel)
            "tip":                (self._cb_type,    True),
            "cap_mm":             (self._sp_diam,    True),
            "uzunluk_mm":         (self._sp_len,     True),
            "konik_aci_deg":      (self._sp_cone,    True),
            "kubbe_yukseklik_mm": (self._sp_dome,    True),
            "kubbe_hr_orani":     (self._sp_dome_hr, True),
            "alpha_deg":          (self._sp_alpha,    False),
            "fitil_mm":           (self._sp_tow,      False),
            "kat_sayisi":         (self._sp_nlayers,  False),
            "cakisma_pct":        (self._sp_overlap,  False),
            "strateji":           (self._cb_strat,    False),
            "ilerleme_mm_s":      (self._sp_feed,     False),
            "rpm":                (self._sp_rpm,      False),
            "surtunme_mu":        (self._sp_friction, False),
        }
        for key, (w, _is_m) in self._param_map.items():
            if isinstance(w, QComboBox):
                w.currentTextChanged.connect(
                    lambda txt, k=key: self._on_param_changed(k, txt))
            else:
                w.valueChanged.connect(
                    lambda v, k=key: self._on_param_changed(k, v))
        self._refresh_param_cache()

    def _param_value(self, key: str):
        w, _ = self._param_map[key]
        return w.currentText() if isinstance(w, QComboBox) else w.value()

    def _refresh_param_cache(self) -> None:
        """Son bilinen parametre değerlerini widget'lardan tazele
        (proje yükleme sonrası bayat 'old' değerleri engeller)."""
        self._param_last = {k: self._param_value(k) for k in self._param_map}

    def _on_param_changed(self, key: str, new_value) -> None:
        if self._suppress_dirty:
            return
        old_value = self._param_last.get(key)
        if old_value == new_value:
            return
        _, is_mandrel = self._param_map[key]
        cls = ChangeMandrelCommand if is_mandrel else ChangeMachineSettingsCommand
        self._push_cmd(cls(self, key, old_value, new_value))

    # ── Katman ekleme ─────────────────────────────────────────────────────────

    def _on_add_helical(self) -> None:
        self._push_cmd(AddRowCommand(self, {
            "tip": "Sarmal", "alpha_deg": self._sp_alpha.value(),
            "fitil_mm": self._sp_tow.value(),
            "n_kat": self._sp_nlayers.value()}))

    def _on_add_hoop(self) -> None:
        self._push_cmd(AddRowCommand(self, {
            "tip": "Hoop", "alpha_deg": 89.5,
            "fitil_mm": self._sp_tow.value(), "n_kat": 1}))

    def _on_add_polar(self) -> None:
        self._push_cmd(AddRowCommand(self, {
            "tip": "Polar", "alpha_deg": 12.0,
            "fitil_mm": self._sp_tow.value(), "n_kat": 1}))

    def _add_layer_row(self, ltype: str, alpha: float,
                       tow: float, n: int) -> None:
        """Tablonun sonuna satır ekle (undo'suz programatik yol —
        set_layer_rows / set_layer_stack tarafından kullanılır)."""
        self._cmd_insert_row(self._layer_table.rowCount(), {
            "tip": ltype, "alpha_deg": alpha, "fitil_mm": tow, "n_kat": n})

    def _del_row_by_widget(self, btn: QPushButton) -> None:
        # Satır indeksi ekleme/silme ile kaydığı için lambda'da yakalanan
        # indeks güvenilmez; butonun kendisi 'is' kimlik karşılaştırmasıyla
        # aranır — her zaman doğru satır silinir.
        tbl = self._layer_table
        for r in range(tbl.rowCount()):
            if tbl.cellWidget(r, COL_DEL) is btn:
                row_dict = self.get_layer_rows()[r]
                self._push_cmd(DeleteRowCommand(self, r, row_dict))
                return

    # ── CAM hesaplama ─────────────────────────────────────────────────────────

    def _collect_params(self) -> "_ECalcParams":
        """R1: Tüm widget değerlerini ana thread'de topla."""
        import copy
        rows = copy.deepcopy(self.get_layer_rows())
        return _ECalcParams(
            mandrel_type   = self._cb_type.currentText(),
            diameter_mm    = self._sp_diam.value(),
            length_mm      = self._sp_len.value(),
            cone_angle_deg = self._sp_cone.value(),
            dome_h_mm      = self._sp_dome.value(),
            dome_hr_ratio  = self._sp_dome_hr.value(),
            stl_path       = self._stl_path,
            alpha_deg      = self._sp_alpha.value(),
            n_layers       = self._sp_nlayers.value(),
            tow_w_mm       = self._sp_tow.value(),
            overlap_pct    = self._sp_overlap.value(),
            strategy_text  = self._cb_strat.currentText(),
            feed_mm_s      = self._sp_feed.value(),
            spindle_rpm    = self._sp_rpm.value(),
            friction_mu    = self._sp_friction.value(),
            layer_rows     = rows,
        )

    def _on_calculate(self) -> None:
        if self._worker_thread and self._worker_thread.isRunning():
            return

        # R1: tüm widget değerlerini ana thread'de topla, worker'a DÜZLÜK ver
        params = self._collect_params()

        self._calc_gen += 1
        gen = self._calc_gen

        self._progress.setVisible(True)
        self._btn_calc.setEnabled(False)
        self._btn_gcode.setEnabled(False)
        self._status_lbl.setText("Hesaplanıyor…")
        self._gl.clear_fiber_paths()
        self._stop_anim()

        thread = QThread(self)
        worker = _EWorker(self._do_calculate, params, gen)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_calc_done)
        worker.error.connect(self._on_calc_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_cleanup)
        self._worker_thread = thread
        self._worker_ref = worker
        self._start_watchdog(gen)
        thread.start()

    # ── Watchdog yönetimi ─────────────────────────────────────────────────────

    def _start_watchdog(self, gen: int) -> None:
        self._stop_watchdog()
        wd = QTimer(self)
        wd.setSingleShot(True)
        wd.timeout.connect(lambda g=gen: self._on_calc_timeout(g))
        wd.start(self._WATCHDOG_MS)
        self._watchdog = wd

    def _stop_watchdog(self) -> None:
        if self._watchdog is not None:
            self._watchdog.stop()
            self._watchdog.deleteLater()
            self._watchdog = None

    def _on_thread_cleanup(self) -> None:
        self._worker_thread = None
        self._worker_ref = None

    def _on_calc_timeout(self, gen: int) -> None:
        if gen != self._calc_gen:
            return
        if self._worker_thread is None or not self._worker_thread.isRunning():
            return
        self._calc_gen += 1
        self._progress.setVisible(False)
        self._btn_calc.setEnabled(True)
        self._status_lbl.setText(
            f"Zaman aşımı: {self._WATCHDOG_MS // 1000} sn içinde tamamlanamadı.")
        try:
            self._worker_thread.quit()
        except Exception:
            pass

    def _do_calculate(self, params: "_ECalcParams"):
        """R1: yalnızca düz params okunur — hiçbir Qt widget erişimi yok."""
        (MandrelProfile, WindingPathParams, generate_path,
         plan_motion, MachineConfig, generate_gcode) = self._backend
        if MandrelProfile is None:
            raise RuntimeError(
                "Backend modülleri yüklenemedi. faz17_d2/ dizininden başlatın.")

        # R3: preflight import
        try:
            from backend.core.path_generator import (
                preflight_check, preflight_check_stack, ComplexityError,
            )
            _have_preflight = True
        except Exception:
            _have_preflight = False

        import math as _m

        d_mm = params.diameter_mm
        l_mm = params.length_mm
        t    = params.mandrel_type
        dh   = params.dome_h_mm
        ca   = params.cone_angle_deg

        if t == "Silindir":
            profile = MandrelProfile.cylinder(l_mm, d_mm / 2.0)
        elif t == "Konik":
            r_end = d_mm / 2.0 + l_mm * _m.tan(_m.radians(ca))
            profile = MandrelProfile.cone(l_mm, d_mm / 2.0, r_end)
        elif t == "Kubbeli Silindir":
            profile = MandrelProfile.dome_cylinder_dome(l_mm, d_mm / 2.0, dh)
        elif t == "Elipsoidal Kubbe":
            profile = MandrelProfile.ellipsoidal_dome_cylinder_dome(
                l_mm, d_mm / 2.0, dome_hr_ratio=params.dome_hr_ratio)
        else:
            if not params.stl_path:
                raise RuntimeError("STL dosyası seçilmedi.")
            profile = MandrelProfile.from_stl(params.stl_path)

        # R3: Preflight — complexity gate
        if _have_preflight and params.layer_rows:
            _layer_params = []
            strat_map = {"Sarmal": "helical", "Hoop": "hoop", "Polar": "polar"}
            for row in params.layer_rows:
                _ltype = strat_map.get(str(row.get("tip", "Sarmal")), "helical")
                _alpha = float(row.get("alpha_deg", params.alpha_deg))
                if _ltype == "hoop":
                    _alpha = 88.0
                elif _ltype == "polar":
                    _alpha = min(max(_alpha, 5.0), 20.0)
                _layer_params.append(WindingPathParams(
                    profile=profile, alpha_deg=_alpha,
                    n_layers=max(1, int(row.get("n_kat", 1))),
                    tow_width_mm=float(row.get("fitil_mm", params.tow_w_mm)),
                    overlap_pct=params.overlap_pct,
                ))
            preflight_check_stack(_layer_params)
        elif _have_preflight:
            _salpha = params.alpha_deg
            _strat_pre = params.strategy_text
            if "Çevre" in _strat_pre or "Hoop" in _strat_pre:
                _salpha = 88.0
            elif "Kutup" in _strat_pre or "Polar" in _strat_pre:
                _salpha = min(max(_salpha, 5.0), 20.0)
            preflight_check(WindingPathParams(
                profile=profile, alpha_deg=_salpha,
                n_layers=params.n_layers, tow_width_mm=params.tow_w_mm,
                overlap_pct=params.overlap_pct,
            ))

        # Strateji eşleme
        strat_map = {
            "Sarmal (Helisel)": "helical", "Çevre (Hoop)": "hoop",
            "Kutupsal": "polar", "Sarmal": "helical",
            "Hoop": "hoop", "Polar": "polar",
        }

        if params.layer_rows:
            all_paths = []
            for row in params.layer_rows:
                lt    = str(row.get("tip", "Sarmal"))
                strat = strat_map.get(lt, "helical")
                alpha = float(row.get("alpha_deg", params.alpha_deg))
                if strat == "hoop":
                    alpha = 88.0
                tow   = float(row.get("fitil_mm", params.tow_w_mm))
                n_l   = max(1, int(row.get("n_kat", 1)))
                pp = WindingPathParams(
                    profile=profile, alpha_deg=alpha, n_layers=n_l,
                    tow_width_mm=tow, overlap_pct=params.overlap_pct,
                    feed_mm_s=params.feed_mm_s, spindle_rpm=params.spindle_rpm,
                    winding_strategy=strat, friction_mu=params.friction_mu,
                )
                path = generate_path(pp)
                all_paths.append(path)
            first = all_paths[0] if all_paths else None
            return first, profile, all_paths

        # Parametrik mod (katman tablosu boş)
        strat = strat_map.get(params.strategy_text, "helical")
        pp = WindingPathParams(
            profile=profile, alpha_deg=params.alpha_deg,
            n_layers=params.n_layers, tow_width_mm=params.tow_w_mm,
            overlap_pct=params.overlap_pct, feed_mm_s=params.feed_mm_s,
            spindle_rpm=params.spindle_rpm, winding_strategy=strat,
            friction_mu=params.friction_mu,
        )
        path = generate_path(pp)
        return path, profile, None

    @Slot(object, object, object, int)
    def _on_calc_done(self, path, profile, all_paths, gen) -> None:
        if gen != self._calc_gen:
            return
        self._stop_watchdog()
        self._progress.setVisible(False)
        self._btn_calc.setEnabled(True)
        self._winding_path = path
        self._profile      = profile

        if path is None:
            self._status_lbl.setText("Hesaplama başarısız.")
            return

        try:
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
        self._setup_animation(path, profile)

    @Slot(str, int)
    def _on_calc_error(self, msg: str, gen: int) -> None:
        if gen != self._calc_gen:
            return
        self._stop_watchdog()
        self._progress.setVisible(False)
        self._btn_calc.setEnabled(True)
        self._status_lbl.setText(f"Hata: {msg[:120]}")
        QMessageBox.warning(self, "Hesaplama Hatası", msg)

    # ── Animasyon yönetimi ────────────────────────────────────────────────────

    def _setup_animation(self, path, profile) -> None:
        """Hesap tamamlandı — 4-eksen animasyon verilerini vektörize olarak hazırla."""
        self._stop_anim()
        if path is None or not getattr(path, 'points', None):
            return
        try:
            z_arr = np.asarray(profile.z_mm, dtype=np.float64)
            r_arr = np.asarray(profile.r_mm, dtype=np.float64)
            z_center   = (z_arr[0] + z_arr[-1]) / 2.0
            half_len_m = (z_arr[-1] - z_arr[0]) / 2000.0

            pts = path.points
            step = max(1, len(pts) // 3000)
            pts_sub = pts[::step]
            n = len(pts_sub)
            if n == 0:
                return

            # Vektörize 3D konum hesabı
            z_vals = np.array([float(p.x_mm)  for p in pts_sub], dtype=np.float64)
            a_degs = np.array([float(p.a_deg) for p in pts_sub], dtype=np.float64)
            a_rads = np.radians(a_degs)
            r_vals = np.interp(z_vals, z_arr, r_arr) / 1000.0
            xm = (z_vals - z_center) / 1000.0 + half_len_m
            ym = r_vals * np.cos(a_rads)
            zm = r_vals * np.sin(a_rads)

            self._anim_xyz   = np.column_stack([xm, ym, zm]).astype(np.float32)
            self._anim_a_deg = a_degs.astype(np.float32)
            self._anim_x_mm  = z_vals.astype(np.float32)
            self._anim_idx   = 0

            self._btn_play.setEnabled(True)
            self._btn_stop_anim.setEnabled(True)
            self._btn_reset_anim.setEnabled(True)
            self._anim_slider.setEnabled(True)
            self._anim_slider.setValue(0)
            # İlk durumu göster
            self._gl.set_simulation_state(
                float(self._anim_a_deg[0]),
                float(self._anim_x_mm[0]),
                self._anim_xyz[:1])
        except Exception:
            pass

    def _on_anim_play(self) -> None:
        if self._anim_xyz is None:
            return
        self._anim_playing = True
        self._btn_pause.setEnabled(True)
        self._btn_stop_anim.setEnabled(True)
        self._anim_timer.start()

    def _on_anim_pause(self) -> None:
        self._anim_playing = False
        self._anim_timer.stop()

    def _on_anim_stop(self) -> None:
        """Mevcut konumda durdur — sıfırlamaz."""
        self._anim_timer.stop()
        self._anim_playing = False
        self._btn_pause.setEnabled(False)

    def _stop_anim(self) -> None:
        self._anim_timer.stop()
        self._anim_playing = False
        self._anim_xyz   = None
        self._anim_a_deg = None
        self._anim_x_mm  = None
        self._anim_idx   = 0
        for attr in ('_btn_play', '_btn_pause', '_btn_stop_anim', '_btn_reset_anim'):
            if hasattr(self, attr):
                getattr(self, attr).setEnabled(False)
        if hasattr(self, '_anim_slider'):
            self._anim_slider.setEnabled(False)
            self._anim_slider.setValue(0)
        if hasattr(self, '_anim_lbl'):
            self._anim_lbl.setText("X: — mm  |  A: —°")
        if hasattr(self, '_gl'):
            self._gl.clear_simulation_state()

    def _on_anim_reset(self) -> None:
        self._anim_timer.stop()
        self._anim_playing = False
        self._anim_idx = 0
        if self._anim_xyz is not None and self._anim_a_deg is not None:
            self._gl.set_simulation_state(
                float(self._anim_a_deg[0]),
                float(self._anim_x_mm[0]),
                self._anim_xyz[:1])
        self._anim_slider.setValue(0)
        self._anim_lbl.setText("X: — mm  |  A: —°  (başa sarıldı)")

    def _on_anim_seek(self, value: int) -> None:
        if self._anim_xyz is None or self._anim_a_deg is None:
            return
        n = len(self._anim_xyz)
        self._anim_idx = max(0, min(n - 1, int(value / 1000.0 * (n - 1))))
        pts = self._anim_xyz[:self._anim_idx + 1]
        a_deg = float(self._anim_a_deg[self._anim_idx])
        x_mm  = float(self._anim_x_mm[self._anim_idx])
        self._gl.set_simulation_state(a_deg, x_mm, pts)
        self._anim_lbl.setText(
            f"X: {x_mm:.1f} mm  |  A: {a_deg:.0f}°  |  {self._anim_idx}/{n-1}")

    def _anim_tick(self) -> None:
        if self._anim_xyz is None or self._anim_a_deg is None:
            self._anim_timer.stop()
            return
        n = len(self._anim_xyz)
        try:
            speed = int(
                self._cb_speed.currentText().replace("×", "").replace("x", ""))
        except Exception:
            speed = 1
        base_step = max(1, n // 300)
        self._anim_idx = min(self._anim_idx + base_step * speed, n - 1)

        a_deg = float(self._anim_a_deg[self._anim_idx])
        x_mm  = float(self._anim_x_mm[self._anim_idx])
        pts   = self._anim_xyz[:self._anim_idx + 1]

        self._gl.set_simulation_state(a_deg, x_mm, pts)

        slider_val = int(self._anim_idx / max(1, n - 1) * 1000)
        self._anim_slider.setValue(slider_val)
        self._anim_lbl.setText(
            f"X: {x_mm:.1f} mm  |  A: {a_deg:.0f}°  |  {self._anim_idx}/{n-1}")

        if self._anim_idx >= n - 1:
            self._anim_timer.stop()
            self._anim_playing = False
            self._btn_stop_anim.setEnabled(False)

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

    def _on_layer_item_changed(self, item) -> None:
        """Katman tablosu hücresi düzenlendi — undo komutu oluştur.

        Eski değer öğenin UserRole verisinden okunur; komut uygulandığında
        UserRole yeni değere güncellenir. Ardışık düzenlemeler
        EditRowCellCommand.mergeWith ile tek komuta birleşir.
        """
        if self._suppress_dirty or item is None:
            return
        new_text = item.text()
        old_text = item.data(Qt.UserRole)
        if old_text is None:
            # İlk kayıt — sadece son bilinen değeri başlat
            item.setData(Qt.UserRole, new_text)
            self.tasarimDegisti.emit()
            return
        if str(old_text) == new_text:
            return
        self._push_cmd(EditRowCellCommand(
            self, item.row(), item.column(), str(old_text), new_text))

    # ── Proje kalıcılığı (Schema v2.0) ────────────────────────────────────────

    def get_layer_rows(self) -> List[dict]:
        """Katman tablosunu seri hale getirilebilir satır listesine çevir."""
        tbl = self._layer_table
        rows: List[dict] = []
        for r in range(tbl.rowCount()):
            cb = tbl.cellWidget(r, COL_TIP)
            tip = cb.currentText() if cb else "Sarmal"

            def _cell(col: int, default: float) -> float:
                it = tbl.item(r, col)
                if it is None:
                    return default
                try:
                    return float(it.text().replace(",", "."))
                except ValueError:
                    return default

            rows.append({
                "tip":       tip,
                "alpha_deg": _cell(COL_ALPHA, 55.0),
                "fitil_mm":  _cell(COL_TOW, 6.0),
                "n_kat":     max(1, int(_cell(COL_N, 1))),
            })
        return rows

    def set_layer_rows(self, rows: List[dict]) -> None:
        """Satır listesinden katman tablosunu yeniden kur (sinyalsiz)."""
        self._suppress_dirty = True
        try:
            self._layer_table.setRowCount(0)
            for rd in rows or []:
                self._add_layer_row(
                    str(rd.get("tip", "Sarmal")),
                    float(rd.get("alpha_deg", 55.0)),
                    float(rd.get("fitil_mm", rd.get("fitil_genisligi_mm", 6.0))),
                    max(1, int(rd.get("n_kat", rd.get("n_layers", 1)))),
                )
        finally:
            self._suppress_dirty = False

    def get_design_state(self) -> dict:
        """Panelin tüm tasarım durumu — proje dosyasına (v2.0) kaydedilir."""
        return {
            "mandrel": {
                "tip":                self._cb_type.currentText(),
                "cap_mm":             self._sp_diam.value(),
                "uzunluk_mm":         self._sp_len.value(),
                "konik_aci_deg":      self._sp_cone.value(),
                "kubbe_yukseklik_mm": self._sp_dome.value(),
                "kubbe_hr_orani":     self._sp_dome_hr.value(),
                "stl_yolu":           self._stl_path or "",
            },
            "sarma": {
                "alpha_deg":      self._sp_alpha.value(),
                "fitil_mm":       self._sp_tow.value(),
                "kat_sayisi":     self._sp_nlayers.value(),
                "cakisma_pct":    self._sp_overlap.value(),
                "strateji":       self._cb_strat.currentText(),
                "ilerleme_mm_s":  self._sp_feed.value(),
                "rpm":            self._sp_rpm.value(),
                "surtunme_mu":    self._sp_friction.value(),
            },
            "katmanlar": self.get_layer_rows(),
        }

    def apply_project(self, proje: dict) -> None:
        """ProjeYoneticisi.projeYuklendi → tasarım durumunu geri yükle."""
        if not isinstance(proje, dict):
            return
        m = proje.get("entegre_panel_mandrel") or {}
        s = proje.get("sarma_parametreleri") or {}
        katmanlar = proje.get("entegre_panel_katmanlar") or []
        if not (m or s or katmanlar):
            return  # v1.0 projesi — entegre panel verisi yok

        self._suppress_dirty = True
        try:
            if m:
                for w in (self._cb_type, self._sp_diam, self._sp_len,
                          self._sp_cone, self._sp_dome, self._sp_dome_hr):
                    w.blockSignals(True)
                tip = str(m.get("tip", "Silindir"))
                idx = self._cb_type.findText(tip)
                if idx >= 0:
                    self._cb_type.setCurrentIndex(idx)
                self._sp_diam.setValue(float(m.get("cap_mm", 100.0)))
                self._sp_len.setValue(float(m.get("uzunluk_mm", 300.0)))
                self._sp_cone.setValue(float(m.get("konik_aci_deg", 10.0)))
                self._sp_dome.setValue(float(m.get("kubbe_yukseklik_mm", 30.0)))
                self._sp_dome_hr.setValue(float(m.get("kubbe_hr_orani", 0.7)))
                stl = m.get("stl_yolu") or ""
                if stl and os.path.exists(stl):
                    self._stl_path = stl
                    self._lbl_stl.setText(os.path.basename(stl))
                for w in (self._cb_type, self._sp_diam, self._sp_len,
                          self._sp_cone, self._sp_dome, self._sp_dome_hr):
                    w.blockSignals(False)
                self._on_mandrel_type_changed()

            if s:
                self._sp_alpha.setValue(float(s.get("alpha_deg", 55.0)))
                self._sp_tow.setValue(float(s.get("fitil_mm", 6.0)))
                self._sp_nlayers.setValue(int(s.get("kat_sayisi", 4)))
                self._sp_overlap.setValue(float(s.get("cakisma_pct", 5.0)))
                sidx = self._cb_strat.findText(str(s.get("strateji", "")))
                if sidx >= 0:
                    self._cb_strat.setCurrentIndex(sidx)
                self._sp_feed.setValue(float(s.get("ilerleme_mm_s", 80.0)))
                self._sp_rpm.setValue(float(s.get("rpm", 60.0)))
                self._sp_friction.setValue(float(s.get("surtunme_mu", 0.0)))

            if katmanlar:
                self.set_layer_rows(katmanlar)
        finally:
            self._suppress_dirty = False
            # Bayat 'old' değerleriyle undo komutu üretilmesin
            self._refresh_param_cache()

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
