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
# S5.2 — PayoutEyeRenderer.reset()
# ─────────────────────────────────────────────────────────────────────────────

class TestPayoutEyeReset:

    def _make(self):
        r = PayoutEyeRenderer()
        v = _View()
        r.setup(v, _Topology())
        return r, v, r._eye_item, r._ray_item

    def test_pe01_reset_has_method(self):
        """PE-01: PayoutEyeRenderer sınıfı reset() metoduna sahip."""
        assert hasattr(PayoutEyeRenderer, "reset")
        assert callable(PayoutEyeRenderer().reset)

    def test_pe02_reset_hides_eye_item(self):
        """PE-02: reset() _eye_item'ı gizler."""
        r, _, eye, ray = self._make()
        r.update(_EyeFrame())
        assert eye._visible is True
        r.reset()
        assert eye._visible is False

    def test_pe03_reset_hides_ray_item(self):
        """PE-03: reset() _ray_item'ı gizler."""
        r, _, eye, ray = self._make()
        r.update(_EyeFrame())
        assert ray._visible is True
        r.reset()
        assert ray._visible is False

    def test_pe04_reset_without_setup_noop(self):
        """PE-04: setup yapılmadan reset() exception vermez."""
        PayoutEyeRenderer().reset()

    def test_pe05_reset_accepts_kwargs(self):
        """PE-05: reset() center_x_mm kwarg'ı alır, yoksayar."""
        r, _, eye, ray = self._make()
        r.update(_EyeFrame())
        r.reset(center_x_mm=150.0)
        assert eye._visible is False
        assert ray._visible is False

    def test_pe06_reset_then_update_restores(self):
        """PE-06: reset() sonrası update() tekrar görünür yapar."""
        r, _, eye, ray = self._make()
        r.reset()
        r.update(_EyeFrame())   # update() her iki öğeyi de setVisible(True) yapıyor
        assert eye._visible is True
        assert ray._visible is True


# ─────────────────────────────────────────────────────────────────────────────
# S5.3 — **kwargs uyumluluğu: Shell / Ribbon / FiberPath
# ─────────────────────────────────────────────────────────────────────────────

class TestKwargsCompat:
    """KC-01..09: Tüm renderer reset() imzaları center_x_mm kwarg'ı kabul etmeli."""

    def test_kc01_shell_reset_no_args(self):
        """KC-01: ShellRenderer.reset() argümansız çalışır."""
        r = ShellRenderer()
        v = _View()
        r.setup(v, _Topology())
        r.reset()  # TypeError vermemeli

    def test_kc02_shell_reset_with_kwargs(self):
        """KC-02: ShellRenderer.reset(center_x_mm=...) TypeError vermez."""
        r = ShellRenderer()
        v = _View()
        r.setup(v, _Topology())
        r.reset(center_x_mm=150.0)

    def test_kc03_shell_reset_hides(self):
        """KC-03: reset(center_x_mm=...) sonrası item gizlenir."""
        r = ShellRenderer()
        v = _View()
        r.setup(v, _Topology())
        r.update(_ShellFrame())
        assert r._item._visible is True
        r.reset(center_x_mm=150.0)
        assert r._item._visible is False

    def test_kc04_ribbon_reset_no_args(self):
        """KC-04: RibbonRenderer.reset() argümansız çalışır."""
        r = RibbonRenderer()
        v = _View()
        r.setup(v, _Topology())
        r.reset()

    def test_kc05_ribbon_reset_with_kwargs(self):
        """KC-05: RibbonRenderer.reset(center_x_mm=...) TypeError vermez."""
        r = RibbonRenderer()
        v = _View()
        r.setup(v, _Topology())
        r.reset(center_x_mm=150.0)

    def test_kc06_ribbon_reset_hides(self):
        """KC-06: reset(center_x_mm=...) sonrası ribbon item gizlenir."""
        r = RibbonRenderer()
        v = _View()
        r.setup(v, _Topology())
        r.update(_RibbonFrame())
        assert r._item._visible is True
        r.reset(center_x_mm=150.0)
        assert r._item._visible is False

    def test_kc07_fiberpath_reset_no_args(self):
        """KC-07: FiberPathRenderer.reset() argümansız çalışır."""
        r = FiberPathRenderer()
        v = _View()
        r.setup(v, _Topology())
        r.reset()

    def test_kc08_fiberpath_reset_with_kwargs(self):
        """KC-08: FiberPathRenderer.reset(center_x_mm=...) TypeError vermez."""
        r = FiberPathRenderer()
        v = _View()
        r.setup(v, _Topology())
        r.reset(center_x_mm=150.0)

    def test_kc09_fiberpath_reset_clears_pts(self):
        """KC-09: reset(center_x_mm=...) sonrası _pts temizlenir."""
        r = FiberPathRenderer()
        v = _View()
        r.setup(v, _Topology())
        r.update(_FiberFrame())
        r.update(_FiberFrame())
        assert len(r._pts) == 2
        r.reset(center_x_mm=150.0)
        assert len(r._pts) == 0


# ─────────────────────────────────────────────────────────────────────────────
# S5.3 — _stop_anim() loop simülasyonu: tüm renderer'lar birlikte
# ─────────────────────────────────────────────────────────────────────────────

class TestStopAnimLoop:
    """SA-01..03: _stop_anim() r.reset(center_x_mm=...) çağrısı tüm renderer'larda çalışır."""

    def _make_all(self):
        renderers = [
            HeatmapRenderer(),
            PayoutEyeRenderer(),
            ShellRenderer(),
            RibbonRenderer(),
            FiberPathRenderer(),
        ]
        v = _View()
        top = _Topology()
        for r in renderers:
            r.setup(v, top)
        return renderers, v

    def test_sa01_all_reset_no_exception(self):
        """SA-01: Tüm renderer'lar r.reset(center_x_mm=150.0) ile exception vermez."""
        renderers, _ = self._make_all()
        center_mm = 150.0
        for r in renderers:
            if hasattr(r, 'reset'):
                r.reset(center_x_mm=center_mm)

    def test_sa02_stop_anim_pattern_exact(self):
        """SA-02: _stop_anim() try/except döngüsünü tam simüle et, exception yoktur."""
        renderers, _ = self._make_all()
        center_mm = 150.0
        exceptions_caught = []
        for r in renderers:
            if hasattr(r, 'reset'):
                try:
                    r.reset(center_x_mm=center_mm)
                except Exception as e:
                    exceptions_caught.append((type(r).__name__, e))
        assert exceptions_caught == [], f"Beklenmeyen exception'lar: {exceptions_caught}"

    def test_sa03_all_items_hidden_after_stop(self):
        """SA-03: Animasyon durdurulunca tüm renderer öğeleri gizlenir."""
        renderers, _ = self._make_all()
        # Önce görünür yap
        hm, pe, sh, rb, fp = renderers
        hm.update(_HmFrame())
        pe.update(_EyeFrame())
        sh.update(_ShellFrame())
        rb.update(_RibbonFrame())
        fp.update(_FiberFrame())
        fp.update(_FiberFrame())

        # _stop_anim() simülasyonu
        center_mm = 150.0
        for r in renderers:
            if hasattr(r, 'reset'):
                r.reset(center_x_mm=center_mm)

        # HeatmapRenderer update() setVisible(True) çağırmaz — setup'tan gelen durum
        assert pe._eye_item._visible is False
        assert pe._ray_item._visible is False
        assert sh._item._visible is False
        assert rb._item._visible is False
        assert fp._item._visible is False
        assert len(fp._pts) == 0


# ─────────────────────────────────────────────────────────────────────────────
# S5.4 — FiberPathRenderer max_pts cap
# ─────────────────────────────────────────────────────────────────────────────

class TestFiberPathMaxPts:
    """FP-01..06: FiberPathRenderer max_pts parametresi ve trail pruning."""

    def test_fp01_default_max_pts(self):
        """FP-01: Varsayılan max_pts 10_000'dir."""
        r = FiberPathRenderer()
        assert r._max_pts == 10_000

    def test_fp02_custom_max_pts(self):
        """FP-02: __init__(max_pts=50) doğru ayarlanır."""
        r = FiberPathRenderer(max_pts=50)
        assert r._max_pts == 50

    def test_fp03_pts_not_exceed_max(self):
        """FP-03: update() çağrıları max_pts'i aşmaz."""
        r = FiberPathRenderer(max_pts=5)
        v = _View()
        r.setup(v, _Topology())
        for _ in range(20):
            r.update(_FiberFrame())
        assert len(r._pts) <= 5

    def test_fp04_pts_keeps_recent(self):
        """FP-04: Taşma olunca en son noktalar korunur (FIFO kuyruğu)."""
        r = FiberPathRenderer(max_pts=3)
        v = _View()
        r.setup(v, _Topology())

        class _PtFrame:
            def __init__(self, val):
                self.contact_xyz = np.array([val, 0.0, 0.0], dtype=np.float32)

        for i in range(5):
            r.update(_PtFrame(float(i)))

        # Son 3 nokta: [2, 3, 4]
        assert len(r._pts) == 3
        assert r._pts[-1][0] == pytest.approx(4.0)
        assert r._pts[0][0] == pytest.approx(2.0)

    def test_fp05_reset_clears_pts(self):
        """FP-05: reset() sonrası _pts boşaltılır."""
        r = FiberPathRenderer(max_pts=5)
        v = _View()
        r.setup(v, _Topology())
        for _ in range(5):
            r.update(_FiberFrame())
        assert len(r._pts) == 5
        r.reset()
        assert len(r._pts) == 0

    def test_fp06_reset_preserves_max_pts(self):
        """FP-06: reset() sonrası max_pts değeri korunur."""
        r = FiberPathRenderer(max_pts=42)
        v = _View()
        r.setup(v, _Topology())
        r.reset()
        assert r._max_pts == 42
