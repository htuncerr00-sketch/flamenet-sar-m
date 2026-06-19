"""
tests/test_s5_renderer_reset.py — S5 renderer reset + lifecycle testleri
=========================================================================
S5.1 HeatmapRenderer.reset()
S5.2 PayoutEyeRenderer.reset()
S5.3 _stop_anim kwarg fix
S5.4 FiberPathRenderer max_pts cap

GL/Qt bağımlılığı yok; mock ile çalışır.

Çalıştır:
    cd faz17_d1/faz17_d1_backend
    python -m pytest ../tests/test_s5_renderer_reset.py -v
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types

import numpy as np
import pytest

# ─── pyqtgraph.opengl mock ────────────────────────────────────────────────────

class _MockGLMeshItem:
    def __init__(self, **kw):
        self._visible = False
        self._visible_calls: list[bool] = []
        self._mesh_calls = 0
    def setVisible(self, v):
        self._visible = v
        self._visible_calls.append(v)
    def setMeshData(self, **kw):
        self._mesh_calls += 1


class _MockGLScatterPlotItem:
    def __init__(self, **kw):
        self._visible = False
        self._visible_calls: list[bool] = []
    def setVisible(self, v):
        self._visible = v
        self._visible_calls.append(v)
    def setData(self, **kw): pass


class _MockGLLinePlotItem:
    def __init__(self, **kw):
        self._visible = False
        self._visible_calls: list[bool] = []
    def setVisible(self, v):
        self._visible = v
        self._visible_calls.append(v)
    def setData(self, **kw): pass


_gl_mock = types.ModuleType("pyqtgraph.opengl")
_gl_mock.GLMeshItem          = _MockGLMeshItem
_gl_mock.GLScatterPlotItem   = _MockGLScatterPlotItem
_gl_mock.GLLinePlotItem      = _MockGLLinePlotItem

sys.modules.setdefault("pyqtgraph", types.ModuleType("pyqtgraph"))
sys.modules["pyqtgraph.opengl"] = _gl_mock

# ─── backend.core.render_frame mock (HeatmapRenderer.setup için) ─────────────

class _FakeFrame:
    shell_verts  = np.zeros((24, 3), dtype=np.float32)
    shell_colors = np.ones((24, 4), dtype=np.float32)

_rf_mock = types.ModuleType("backend.core.render_frame")
_rf_mock.make_empty_frame = lambda top: _FakeFrame()
sys.modules.setdefault("backend",           types.ModuleType("backend"))
sys.modules.setdefault("backend.core",      types.ModuleType("backend.core"))
sys.modules["backend.core.render_frame"] = _rf_mock

# ─── Renderer yükleyici ───────────────────────────────────────────────────────

_REND = os.path.join(
    os.path.dirname(__file__),
    "../../faz17_d2/faz17_d2_app/faz17_d2/app/renderers",
)


def _load(name: str):
    path = os.path.join(_REND, f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HeatmapRenderer   = _load("heatmap_renderer").HeatmapRenderer
PayoutEyeRenderer = _load("payout_eye_renderer").PayoutEyeRenderer
FiberPathRenderer = _load("fiber_path_renderer").FiberPathRenderer
RibbonRenderer    = _load("ribbon_renderer").RibbonRenderer
ShellRenderer     = _load("shell_renderer").ShellRenderer


# ─── Mock view/topology ───────────────────────────────────────────────────────

class _View:
    def __init__(self):
        self._items: list = []
        self._on_scene_rebuild = None
    def addItem(self, item):  self._items.append(item)
    def removeItem(self, item):
        if item in self._items: self._items.remove(item)


class _Topology:
    ribbon_max_seg = 50
    shell_faces    = np.zeros((10, 3), dtype=np.int32)
    heatmap_verts  = np.zeros((96, 3), dtype=np.float32)
    heatmap_faces  = np.zeros((80, 3), dtype=np.int32)


class _HmFrame:
    heatmap_dirty = True
    heatmap_vc    = np.ones((96, 4), dtype=np.float32)


class _EyeFrame:
    eye_xyz     = np.array([0.1, 0.0, 0.05], dtype=np.float32)
    contact_xyz = np.array([0.1, 0.04, 0.0], dtype=np.float32)


class _FiberFrame:
    contact_xyz = np.array([0.1, 0.0, 0.0], dtype=np.float32)


class _ShellFrame:
    is_shell_visible = True
    shell_verts      = np.ones((24, 3), dtype=np.float32)
    shell_colors     = np.ones((24, 4), dtype=np.float32)


class _RibbonFrame:
    ribbon_n_segments = 5
    ribbon_verts      = np.ones((10, 3), dtype=np.float32)
    ribbon_faces      = np.ones((8, 3), dtype=np.int32)


# ─────────────────────────────────────────────────────────────────────────────
# S5.1 — HeatmapRenderer.reset()
# ─────────────────────────────────────────────────────────────────────────────

class TestHeatmapReset:

    def _make(self):
        r = HeatmapRenderer()
        v = _View()
        r.setup(v, _Topology())
        return r, v, r._item

    def test_hm01_reset_has_method(self):
        """HM-01: HeatmapRenderer sınıfı reset() metoduna sahip."""
        assert hasattr(HeatmapRenderer, "reset")
        assert callable(HeatmapRenderer().reset)

    def test_hm02_reset_hides_item(self):
        """HM-02: reset() sonrası item görünmez."""
        r, _, item = self._make()
        r.update(_HmFrame())
        assert item._visible is True
        r.reset()
        assert item._visible is False

    def test_hm03_reset_without_setup_noop(self):
        """HM-03: setup yapılmadan reset() exception vermez."""
        HeatmapRenderer().reset()

    def test_hm04_reset_accepts_kwargs(self):
        """HM-04: reset() center_x_mm kwarg'ı alır, yoksayar."""
        r, _, item = self._make()
        r.update(_HmFrame())
        r.reset(center_x_mm=150.0)   # MachineRenderer uyumlu çağrı
        assert item._visible is False

    def test_hm05_reset_hides_until_set_visible(self):
        """HM-05: reset() gizler; set_visible(True) tekrar görünür yapar.

        HeatmapRenderer.update() visibility değiştirmez (tasarım gereği —
        Ribbon/Shell'den farklı). Görünürlük yalnız setup() veya set_visible() ile döner.
        """
        r, _, item = self._make()
        r.reset()
        assert item._visible is False
        r.set_visible(True)
        assert item._visible is True

    def test_hm06_set_visible_before_reset(self):
        """HM-06: set_visible(False) → reset() → hala gizli."""
        r, _, item = self._make()
        r.set_visible(False)
        r.reset()
        assert item._visible is False


# ─────────────────────────────────────────────────────────────────────────────
# S5.2 — PayoutEyeRenderer.reset()  (S5.2 commit'inde eklenecek)
# ─────────────────────────────────────────────────────────────────────────────

# placeholder — S5.2 uygulandıktan sonra eklenecek
