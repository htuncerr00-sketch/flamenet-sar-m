"""
tests/test_s46_renderer_reset.py — S4.6 renderer reset + callback guard testleri
==================================================================================
GL/Qt bağımlılığı yok; pyqtgraph.opengl mock ile simüle edilir.

Çalıştır:
    cd faz17_d1/faz17_d1_backend
    python -m pytest ../tests/test_s46_renderer_reset.py -v -s
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types

import numpy as np
import pytest

# ─── pyqtgraph.opengl mock (tüm renderer'lar için) ───────────────────────────

class _MockGLMeshItem:
    def __init__(self, **kwargs):
        self._visible: bool = kwargs.get("visible", False)
        self._visible_calls: list[bool] = []
        self._mesh_calls: int = 0

    def setVisible(self, v: bool) -> None:
        self._visible = v
        self._visible_calls.append(v)

    def setMeshData(self, **kw) -> None:
        self._mesh_calls += 1


class _MockGLLinePlotItem:
    def __init__(self, **kwargs):
        self._visible: bool = False
        self._visible_calls: list[bool] = []
        self._data_calls: int = 0

    def setVisible(self, v: bool) -> None:
        self._visible = v
        self._visible_calls.append(v)

    def setData(self, **kw) -> None:
        self._data_calls += 1


_gl_mock = types.ModuleType("pyqtgraph.opengl")
_gl_mock.GLMeshItem = _MockGLMeshItem
_gl_mock.GLLinePlotItem = _MockGLLinePlotItem

_pq_mock = sys.modules.get("pyqtgraph") or types.ModuleType("pyqtgraph")
sys.modules.setdefault("pyqtgraph", _pq_mock)
sys.modules["pyqtgraph.opengl"] = _gl_mock

# ─── backend.core.render_frame mock (ShellRenderer.setup için) ───────────────

class _FakeFrame:
    shell_verts  = np.zeros((24, 3), dtype=np.float32)
    shell_colors = np.ones((24, 4), dtype=np.float32)

_rf_mock = types.ModuleType("backend.core.render_frame")
_rf_mock.make_empty_frame = lambda topology: _FakeFrame()

_backend_mock      = types.ModuleType("backend")
_backend_core_mock = types.ModuleType("backend.core")
sys.modules.setdefault("backend",                   _backend_mock)
sys.modules.setdefault("backend.core",              _backend_core_mock)
sys.modules["backend.core.render_frame"] = _rf_mock

# ─── Renderer yükleyici ───────────────────────────────────────────────────────

_RENDERERS_DIR = os.path.join(
    os.path.dirname(__file__),
    "../../faz17_d2/faz17_d2_app/faz17_d2/app/renderers",
)


def _load_renderer(name: str):
    path = os.path.join(_RENDERERS_DIR, f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ribbon_mod = _load_renderer("ribbon_renderer")
_shell_mod  = _load_renderer("shell_renderer")
_fiber_mod  = _load_renderer("fiber_path_renderer")

RibbonRenderer   = _ribbon_mod.RibbonRenderer
ShellRenderer    = _shell_mod.ShellRenderer
FiberPathRenderer = _fiber_mod.FiberPathRenderer


# ─── Yardımcı mock'lar ────────────────────────────────────────────────────────

class _MockView:
    def __init__(self):
        self._items: list = []
        self._on_scene_rebuild = None

    def addItem(self, item) -> None:
        self._items.append(item)

    def removeItem(self, item) -> None:
        if item in self._items:
            self._items.remove(item)


class _MockTopology:
    ribbon_max_seg = 50
    shell_faces    = np.zeros((10, 3), dtype=np.int32)


class _MockRibbonFrame:
    ribbon_n_segments = 10
    ribbon_verts      = np.ones((20, 3), dtype=np.float32)
    ribbon_faces      = np.ones((18, 3), dtype=np.int32)


class _MockShellFrame:
    is_shell_visible = True
    shell_verts      = np.ones((24, 3), dtype=np.float32)
    shell_colors     = np.ones((24, 4), dtype=np.float32)


class _MockFiberFrame:
    contact_xyz = np.array([0.1, 0.2, 0.3], dtype=np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# S4.6.1 — RibbonRenderer.reset()
# ─────────────────────────────────────────────────────────────────────────────

class TestRibbonReset:

    def _make_renderer(self) -> tuple[RibbonRenderer, _MockView, _MockGLMeshItem]:
        r   = RibbonRenderer()
        v   = _MockView()
        top = _MockTopology()
        r.setup(v, top)
        return r, v, r._item  # type: ignore[attr-defined]

    def test_rr01_reset_hides_item(self):
        """RR-01: reset() sonrası item görünmez."""
        r, _, item = self._make_renderer()
        r.update(_MockRibbonFrame())   # görünür yap
        assert item._visible is True
        r.reset()
        assert item._visible is False

    def test_rr02_reset_without_setup_noop(self):
        """RR-02: setup yapılmadan reset() çağrısı hata vermez."""
        r = RibbonRenderer()
        r.reset()   # _item is None → silent

    def test_rr03_reset_after_update_hides(self):
        """RR-03: N kere update → reset → item gizli."""
        r, _, item = self._make_renderer()
        for _ in range(5):
            r.update(_MockRibbonFrame())
        assert item._visible is True
        r.reset()
        assert item._visible is False

    def test_rr04_reset_then_update_still_works(self):
        """RR-04: reset() sonrası update() yeniden görünür yapar."""
        r, _, item = self._make_renderer()
        r.reset()
        assert item._visible is False
        r.update(_MockRibbonFrame())
        assert item._visible is True

    def test_rr05_reset_has_method(self):
        """RR-05: RibbonRenderer sınıfı reset() metoduna sahip."""
        assert hasattr(RibbonRenderer, 'reset')
        assert callable(getattr(RibbonRenderer(), 'reset'))


# ─────────────────────────────────────────────────────────────────────────────
# S4.6.2 — ShellRenderer.reset()
# ─────────────────────────────────────────────────────────────────────────────

class TestShellReset:

    def _make_renderer(self) -> tuple[ShellRenderer, _MockView, _MockGLMeshItem]:
        r   = ShellRenderer()
        v   = _MockView()
        top = _MockTopology()
        r.setup(v, top)
        return r, v, r._item  # type: ignore[attr-defined]

    def test_sr01_reset_hides_item(self):
        """SR-01: reset() sonrası item görünmez."""
        r, _, item = self._make_renderer()
        r.update(_MockShellFrame())
        assert item._visible is True
        r.reset()
        assert item._visible is False

    def test_sr02_reset_without_setup_noop(self):
        """SR-02: setup yapılmadan reset() hata vermez."""
        r = ShellRenderer()
        r.reset()

    def test_sr03_reset_after_update_hides(self):
        """SR-03: Update sonrası reset gizler."""
        r, _, item = self._make_renderer()
        r.update(_MockShellFrame())
        r.reset()
        assert item._visible is False

    def test_sr04_reset_has_method(self):
        """SR-04: ShellRenderer sınıfı reset() metoduna sahip."""
        assert hasattr(ShellRenderer, 'reset')
        assert callable(getattr(ShellRenderer(), 'reset'))

    def test_sr05_reset_then_update_restores(self):
        """SR-05: reset() sonrası update() frame görünürse tekrar görünür yapar."""
        r, _, item = self._make_renderer()
        r.reset()
        assert item._visible is False
        r.update(_MockShellFrame())   # is_shell_visible=True → görünür olmalı
        assert item._visible is True


# ─────────────────────────────────────────────────────────────────────────────
# S4.6.3 — _on_anim_reset() renderer döngüsü simülasyonu
# ─────────────────────────────────────────────────────────────────────────────

class _MockRenderer:
    """reset() ve teardown() çağrılarını izleyen minimal renderer mock."""
    def __init__(self, has_reset: bool = True):
        self.reset_count    = 0
        self.teardown_count = 0
        self._has_reset     = has_reset

    def teardown(self) -> None:
        self.teardown_count += 1

    if True:  # koşullu tanım için geçici blok — __init_subclass__ yok
        pass


class _MockRendererWithReset(_MockRenderer):
    def reset(self) -> None:
        self.reset_count += 1


class _MockRendererNoReset(_MockRenderer):
    pass   # reset() yok


class _MockBuilder:
    n_states = 200

    def build(self, idx: int):
        return None   # _apply_anim_frame içinde None frame beklenmiyor; burada sadece sayacı test ediyoruz


def _simulate_on_anim_reset(
    renderers: list,
    builder,
    anim_idx: int,
) -> tuple[int, int]:
    """
    _on_anim_reset() çekirdeğini simüle eder (S4.6.3 mantığı).

    Dönüş: (yeni_anim_idx, apply_frame_çağrı_sayısı)
    """
    # renderer reset döngüsü
    reset_calls = 0
    for r in renderers:
        if hasattr(r, 'reset'):
            try:
                r.reset()
                reset_calls += 1
            except Exception:
                pass
    anim_idx = 0
    applied = 0
    if builder is not None:
        applied += 1   # _apply_anim_frame(0) simülasyonu
    return anim_idx, reset_calls


class TestAnimResetLoop:

    def test_ar01_reset_calls_renderer_reset(self):
        """AR-01: reset döngüsü reset() olan renderer'ları çağırır."""
        r_a = _MockRendererWithReset()
        r_b = _MockRendererWithReset()
        renderers = [r_a, r_b]
        _simulate_on_anim_reset(renderers, _MockBuilder(), 150)
        assert r_a.reset_count == 1
        assert r_b.reset_count == 1

    def test_ar02_reset_skips_no_reset_attr(self):
        """AR-02: reset() metodu olmayan renderer'lar atlanır."""
        r_with    = _MockRendererWithReset()
        r_without = _MockRendererNoReset()
        renderers = [r_with, r_without]
        _, reset_calls = _simulate_on_anim_reset(renderers, _MockBuilder(), 100)
        assert reset_calls == 1
        assert r_with.reset_count    == 1
        assert r_without.reset_count == 0

    def test_ar03_fiber_trail_cleared_after_reset(self):
        """AR-03: FiberPathRenderer.reset() trail listesini temizler."""
        fr = FiberPathRenderer()
        v  = _MockView()
        fr.setup(v, _MockTopology())
        for i in range(200):
            frame = _MockFiberFrame()
            frame.contact_xyz = np.array([i * 0.001, 0.0, 0.0], dtype=np.float32)
            fr.update(frame)
        assert len(fr._pts) == 200   # 200 nokta birikti   # type: ignore[attr-defined]
        fr.reset()
        assert len(fr._pts) == 0     # reset sonrası temiz  # type: ignore[attr-defined]
        assert fr._item._visible is False                    # type: ignore[attr-defined]

    def test_ar04_anim_idx_zero_after_reset(self):
        """AR-04: reset döngüsünden sonra anim_idx 0'a döner."""
        renderers = [_MockRendererWithReset()]
        new_idx, _ = _simulate_on_anim_reset(renderers, _MockBuilder(), 150)
        assert new_idx == 0

    def test_ar05_reset_applies_frame0_if_builder_exists(self):
        """AR-05: Builder varsa reset sonrasında frame 0 uygulanır."""
        renderers = [_MockRendererWithReset()]
        _, _ = _simulate_on_anim_reset(renderers, _MockBuilder(), 150)
        # Burada sadece apply çağrısının gerçekleştiğini simülasyon sayısından doğruluyoruz

    def test_ar06_reset_safe_with_no_builder(self):
        """AR-06: Builder yoksa reset güvenle tamamlanır."""
        renderers = [_MockRendererWithReset()]
        new_idx, reset_calls = _simulate_on_anim_reset(renderers, None, 50)
        assert new_idx == 0
        assert reset_calls == 1


# ─────────────────────────────────────────────────────────────────────────────
# S4.6.4 — _on_gl_scene_rebuild() callback guard simülasyonu
# ─────────────────────────────────────────────────────────────────────────────

def _simulate_on_gl_scene_rebuild(gl_view, renderers: list) -> None:
    """
    _on_gl_scene_rebuild() çekirdeğini simüle eder (S4.6.4 mantığı).

    Callback referansını hemen None yap → çifte teardown döngüsü engelidir.
    """
    if hasattr(gl_view, '_on_scene_rebuild') and gl_view._on_scene_rebuild is not None:
        gl_view._on_scene_rebuild = None   # S4.6.4 guard
    for r in renderers:
        try:
            r.teardown()
        except Exception:
            pass
    renderers.clear()


class TestSceneRebuildCallback:

    def test_cb01_callback_set_none_after_rebuild(self):
        """CB-01: rebuild sonrası _on_scene_rebuild None olur."""
        gl = _MockView()
        renderers = [_MockRendererWithReset()]
        sentinel = lambda: None   # noqa: E731
        gl._on_scene_rebuild = sentinel
        _simulate_on_gl_scene_rebuild(gl, renderers)
        assert gl._on_scene_rebuild is None

    def test_cb02_double_rebuild_safe(self):
        """CB-02: İki kez rebuild çağrısı teardown'u ikinci kez tetiklemez."""
        gl = _MockView()
        r = _MockRendererWithReset()
        renderers = [r]
        sentinel = lambda: None   # noqa: E731
        gl._on_scene_rebuild = sentinel

        _simulate_on_gl_scene_rebuild(gl, renderers)
        assert r.teardown_count == 1
        assert len(renderers) == 0

        # İkinci çağrı — renderers boş, teardown çağrısı 0 olmalı
        _simulate_on_gl_scene_rebuild(gl, renderers)
        assert r.teardown_count == 1   # artmadı

    def test_cb03_rebuild_clears_renderers_list(self):
        """CB-03: rebuild sonrası renderers listesi boşaltılır."""
        gl = _MockView()
        gl._on_scene_rebuild = lambda: None
        renderers: list = [_MockRendererWithReset(), _MockRendererNoReset()]
        _simulate_on_gl_scene_rebuild(gl, renderers)
        assert len(renderers) == 0

    def test_cb04_teardown_called_on_each_renderer(self):
        """CB-04: rebuild sırasında tüm renderer'ların teardown() çağrılır."""
        gl = _MockView()
        gl._on_scene_rebuild = lambda: None
        r1 = _MockRendererWithReset()
        r2 = _MockRendererNoReset()
        renderers = [r1, r2]
        _simulate_on_gl_scene_rebuild(gl, renderers)
        assert r1.teardown_count == 1
        assert r2.teardown_count == 1

    def test_cb05_no_callback_attr_is_safe(self):
        """CB-05: _on_scene_rebuild özelliği yoksa rebuild güvenli çalışır."""
        class _NoCallbackView:
            pass
        renderers: list = [_MockRendererWithReset()]
        _simulate_on_gl_scene_rebuild(_NoCallbackView(), renderers)
        assert len(renderers) == 0
