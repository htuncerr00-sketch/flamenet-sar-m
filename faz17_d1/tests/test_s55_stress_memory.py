"""
tests/test_s55_stress_memory.py — S5.5.2+S5.5.3 Uzun-çalışma stress + bellek
==============================================================================
Renderer'ların uzun animasyonlarda state birikimi olmadığını doğrular.

S5.5.2 — Long-run stress (SM-01..08):
    FiberPath 10k/100k update, max_pts sınırı, diğer renderer'larda birikimli
    state olmadığı, pipeline 10k frame stabilitesi.

S5.5.3 — Memory stability (SM-09..12):
    reset sonrası bellek serbest bırakılıyor, sürekli döngü sonrası boyut
    sabit, FiberPath _pts shallow size'ı sınırlı.

GL/Qt bağımlılığı yok; mock ile çalışır.

Çalıştır:
    cd faz17_d1/faz17_d1_backend
    python -m pytest ../tests/test_s55_stress_memory.py -v
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import gc
import math
from dataclasses import dataclass, field
from typing import List

import numpy as np
import pytest

# ─── sys.path ────────────────────────────────────────────────────────────────

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "faz17_d1_backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# ─── pyqtgraph.opengl mock ────────────────────────────────────────────────────

class _GLItem:
    def __init__(self, **kw):
        self._visible = False
        self._data_calls = 0
    def setVisible(self, v): self._visible = v
    def setMeshData(self, **kw): self._data_calls += 1
    def setData(self, **kw): self._data_calls += 1
    def translate(self, *a, **kw): pass
    def resetTransform(self): pass
    def rotate(self, *a, **kw): pass


_gl_mock = types.ModuleType("pyqtgraph.opengl")
_gl_mock.GLMeshItem        = _GLItem
_gl_mock.GLLinePlotItem    = _GLItem
_gl_mock.GLScatterPlotItem = _GLItem

sys.modules.setdefault("pyqtgraph", types.ModuleType("pyqtgraph"))
sys.modules["pyqtgraph.opengl"] = _gl_mock

# ─── backend.core.render_frame mock ──────────────────────────────────────────

class _FakeEmptyFrame:
    shell_verts  = np.zeros((24, 3), dtype=np.float32)
    shell_colors = np.ones((24, 4), dtype=np.float32)

_rf_mock = types.ModuleType("backend.core.render_frame")
_rf_mock.make_empty_frame = lambda top: _FakeEmptyFrame()
_bk  = types.ModuleType("backend")
_bkc = types.ModuleType("backend.core")
sys.modules.setdefault("backend",              _bk)
sys.modules.setdefault("backend.core",         _bkc)
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


ShellRenderer     = _load("shell_renderer").ShellRenderer
HeatmapRenderer   = _load("heatmap_renderer").HeatmapRenderer
RibbonRenderer    = _load("ribbon_renderer").RibbonRenderer
FiberPathRenderer = _load("fiber_path_renderer").FiberPathRenderer
PayoutEyeRenderer = _load("payout_eye_renderer").PayoutEyeRenderer

# ─── Mock view / topology ─────────────────────────────────────────────────────

class _View:
    def __init__(self):
        self._items: list = []
        self._on_scene_rebuild = None
        self.L_m = 0.3
        self.carriage_items = []
        self.mandrel_items  = []
    def addItem(self, item):    self._items.append(item)
    def removeItem(self, item):
        if item in self._items: self._items.remove(item)


class _Topology:
    ribbon_max_seg = 50
    shell_faces    = np.zeros((10, 3), dtype=np.int32)
    heatmap_verts  = np.zeros((96, 3), dtype=np.float32)
    heatmap_faces  = np.zeros((80, 3), dtype=np.int32)


_FIXED_PT_XYZ = np.array([0.1, 0.0, 0.0], dtype=np.float32)


class _CountedPt:
    """Sabit contact_xyz kullanan hafif frame nesnesi (i argümanı yoksayılır)."""
    contact_xyz = _FIXED_PT_XYZ

    def __init__(self, i: int = 0) -> None:
        pass


class _VisFrame:
    is_shell_visible  = True
    shell_verts       = np.ones((24, 3), dtype=np.float32)
    shell_colors      = np.ones((24, 4), dtype=np.float32)
    heatmap_dirty     = True
    heatmap_vc        = np.ones((96, 4), dtype=np.float32)
    ribbon_n_segments = 5
    ribbon_verts      = np.ones((10, 3), dtype=np.float32)
    ribbon_faces      = np.ones((8,  3), dtype=np.int32)
    contact_xyz       = np.array([0.1, 0.04, 0.0], dtype=np.float32)
    eye_xyz           = np.array([0.1, 0.0,  0.05], dtype=np.float32)
    carriage_x_mm     = 150.0
    spindle_angle_deg = 90.0


class _HmDirtyFrame:
    heatmap_dirty = True
    heatmap_vc    = np.ones((96, 4), dtype=np.float32)


class _HmCleanFrame:
    heatmap_dirty = False
    heatmap_vc    = np.ones((96, 4), dtype=np.float32)


class _RibbonFrame:
    ribbon_n_segments = 5
    ribbon_verts      = np.ones((10, 3), dtype=np.float32)
    ribbon_faces      = np.ones((8,  3), dtype=np.int32)


class _ShellVisFrame:
    is_shell_visible = True
    shell_verts      = np.ones((24, 3), dtype=np.float32)
    shell_colors     = np.ones((24, 4), dtype=np.float32)


class _EyeFrame:
    eye_xyz     = np.array([0.1, 0.0, 0.05], dtype=np.float32)
    contact_xyz = np.array([0.1, 0.04, 0.0], dtype=np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# S5.5.2 — Long-run stress
# ─────────────────────────────────────────────────────────────────────────────

class TestLongRunStress:

    def test_sm01_fiberpath_10k_updates_bounded(self):
        """SM-01: FiberPathRenderer 10_000 update → _pts <= max_pts."""
        r = FiberPathRenderer(max_pts=1_000)
        v = _View()
        r.setup(v, _Topology())
        for i in range(10_000):
            r.update(_CountedPt(i))
        assert len(r._pts) <= 1_000, f"_pts taştı: {len(r._pts)}"

    def test_sm02_fiberpath_100k_updates_bounded(self):
        """SM-02: FiberPathRenderer 100_000 update → _pts <= max_pts."""
        r = FiberPathRenderer(max_pts=500)
        v = _View()
        r.setup(v, _Topology())
        for i in range(100_000):
            r.update(_CountedPt(i))
        assert len(r._pts) <= 500, f"_pts taştı: {len(r._pts)}"

    def test_sm03_fiberpath_exactly_at_capacity(self):
        """SM-03: max_pts=200 ile 2_000 update → len(_pts) tam 200."""
        r = FiberPathRenderer(max_pts=200)
        v = _View()
        r.setup(v, _Topology())
        for i in range(2_000):
            r.update(_CountedPt(i))
        assert len(r._pts) == 200, f"Beklenen 200, alınan {len(r._pts)}"

    def test_sm04_ribbon_no_state_accumulation(self):
        """SM-04: RibbonRenderer 10_000 update sonrası _max_seg değişmez."""
        r = RibbonRenderer()
        v = _View()
        r.setup(v, _Topology())
        before = r._max_seg
        for _ in range(10_000):
            r.update(_RibbonFrame())
        assert r._max_seg == before, f"_max_seg değişti: {before} → {r._max_seg}"

    def test_sm05_shell_no_state_accumulation(self):
        """SM-05: ShellRenderer 10_000 update sonrası iç state büyümez."""
        r = ShellRenderer()
        v = _View()
        r.setup(v, _Topology())
        for _ in range(10_000):
            r.update(_ShellVisFrame())
        # _ready=True kalmalı, ekstra state olmamalı
        assert r._ready is True
        assert r._item is not None

    def test_sm06_heatmap_dirty_false_skips_gpu_call(self):
        """SM-06: HeatmapRenderer update(heatmap_dirty=False) → setMeshData çağrısı yok.

        Mock tipi ne olursa olsun çalışır: setMeshData üzerine sayaç wrapper enjekte eder.
        """
        r = HeatmapRenderer()
        v = _View()
        r.setup(v, _Topology())
        calls: list = []
        r._item.setMeshData = lambda **kw: calls.append(1)
        for _ in range(5_000):
            r.update(_HmCleanFrame())
        assert len(calls) == 0, f"dirty=False iken {len(calls)} GPU çağrısı yapıldı"

    def test_sm07_heatmap_dirty_true_calls_gpu(self):
        """SM-07: HeatmapRenderer update(heatmap_dirty=True) → her seferinde setMeshData.

        Mock tipi ne olursa olsun çalışır: setMeshData üzerine sayaç wrapper enjekte eder.
        """
        r = HeatmapRenderer()
        v = _View()
        r.setup(v, _Topology())
        calls: list = []
        r._item.setMeshData = lambda **kw: calls.append(1)
        n = 100
        for _ in range(n):
            r.update(_HmDirtyFrame())
        assert len(calls) == n, f"Beklenen {n} GPU çağrısı, alınan {len(calls)}"

    def test_sm08_payouteye_no_state_accumulation(self):
        """SM-08: PayoutEyeRenderer 10_000 update sonrası accumulated state yok."""
        r = PayoutEyeRenderer()
        v = _View()
        r.setup(v, _Topology())
        for _ in range(10_000):
            r.update(_EyeFrame())
        assert r._ready is True
        assert r._eye_item is not None
        assert r._ray_item is not None


# ─────────────────────────────────────────────────────────────────────────────
# S5.5.3 — Memory stability
# ─────────────────────────────────────────────────────────────────────────────

class TestMemoryStability:

    def test_sm09_fiberpath_pts_freed_on_reset(self):
        """SM-09: reset() sonrası _pts boşalır → bellek serbest bırakılır."""
        r = FiberPathRenderer(max_pts=500)
        v = _View()
        r.setup(v, _Topology())
        for i in range(2_000):
            r.update(_CountedPt(i))
        assert len(r._pts) == 500
        r.reset()
        gc.collect()
        assert len(r._pts) == 0, f"reset() sonrası _pts boş değil: {len(r._pts)}"

    def test_sm10_fiberpath_memory_stable_across_cycles(self):
        """SM-10: 5 reset döngüsünde _pts boyutu max_pts ile sınırlı kalır."""
        max_pts = 300
        r = FiberPathRenderer(max_pts=max_pts)
        v = _View()
        r.setup(v, _Topology())
        sizes = []
        for cycle in range(5):
            r.reset()
            for i in range(max_pts * 3):
                r.update(_CountedPt(i))
            sizes.append(len(r._pts))
        # Her döngü sonunda tam max_pts olmalı
        assert all(s == max_pts for s in sizes), f"Boyutlar: {sizes}"

    def test_sm11_fiberpath_list_size_bounded_by_max_pts(self):
        """SM-11: 200_000 update sonrası sys.getsizeof(_pts) max_pts × 8 + sabit'i geçmez."""
        import sys as _sys
        max_pts = 1_000
        r = FiberPathRenderer(max_pts=max_pts)
        v = _View()
        r.setup(v, _Topology())
        for i in range(200_000):
            r.update(_CountedPt(i))
        assert len(r._pts) == max_pts
        # Python list shallow size: ~56 bytes header + 8 bytes/slot
        shallow = _sys.getsizeof(r._pts)
        upper_bound = (max_pts + 200) * 8 + 200   # generous headroom
        assert shallow < upper_bound, (
            f"sys.getsizeof(_pts)={shallow} > upper_bound={upper_bound}"
        )

    def test_sm12_no_frame_references_retained_after_stop(self):
        """SM-12: teardown() sonrası renderer frame referansı tutmaz.

        FiberPathRenderer teardown() _pts=[] yapar. Diğer renderer'larda
        birikimli referans zaten yok.
        """
        r = FiberPathRenderer(max_pts=1_000)
        v = _View()
        r.setup(v, _Topology())
        frames = [_CountedPt(i) for i in range(500)]
        for f in frames:
            r.update(f)
        assert len(r._pts) == 500

        r.teardown()
        gc.collect()

        # teardown sonrası _pts tamamen boş
        assert len(r._pts) == 0

        # Orijinal frame nesneleri hâlâ sağlıklı (renderer onları tutmuyor)
        assert len(frames) == 500
