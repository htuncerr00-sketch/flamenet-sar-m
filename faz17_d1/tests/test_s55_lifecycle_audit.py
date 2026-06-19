"""
tests/test_s55_lifecycle_audit.py — S5.5.1 Renderer lifecycle audit
====================================================================
6 renderer sınıfının lifecycle API tutarlılığını doğrular:

LA-01..03 : 4 zorunlu metod varlığı + setup→_ready
LA-04..06 : Çift teardown / teardown-sonrası update / teardown-sonrası reset güvenliği
LA-07..08 : setup öncesi reset güvenliği + view temizliği
LA-09..10 : FiberPath _pts ve MachineRenderer referans temizliği
LA-11..12 : setup→update→teardown item cycle + reset visibility

GL/Qt bağımlılığı yok; mock ile çalışır.

Çalıştır:
    cd faz17_d1/faz17_d1_backend
    python -m pytest ../tests/test_s55_lifecycle_audit.py -v
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types

import numpy as np
import pytest

# ─── pyqtgraph.opengl mock ────────────────────────────────────────────────────

class _GLItem:
    def __init__(self, **kw):
        self._visible = False
    def setVisible(self, v): self._visible = v
    def setMeshData(self, **kw): pass
    def setData(self, **kw): pass
    def translate(self, *a, **kw): pass
    def resetTransform(self): pass
    def rotate(self, *a, **kw): pass


_gl_mock = types.ModuleType("pyqtgraph.opengl")
_gl_mock.GLMeshItem          = _GLItem
_gl_mock.GLLinePlotItem      = _GLItem
_gl_mock.GLScatterPlotItem   = _GLItem

sys.modules.setdefault("pyqtgraph", types.ModuleType("pyqtgraph"))
sys.modules["pyqtgraph.opengl"] = _gl_mock

# ─── backend.core.render_frame mock ──────────────────────────────────────────

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


ShellRenderer     = _load("shell_renderer").ShellRenderer
HeatmapRenderer   = _load("heatmap_renderer").HeatmapRenderer
RibbonRenderer    = _load("ribbon_renderer").RibbonRenderer
FiberPathRenderer = _load("fiber_path_renderer").FiberPathRenderer
PayoutEyeRenderer = _load("payout_eye_renderer").PayoutEyeRenderer
MachineRenderer   = _load("machine_renderer").MachineRenderer

_STD_CLASSES   = [ShellRenderer, HeatmapRenderer, RibbonRenderer,
                  FiberPathRenderer, PayoutEyeRenderer]
_ALL_CLASSES   = _STD_CLASSES + [MachineRenderer]

# ─── Yardımcı sınıflar ───────────────────────────────────────────────────────

class _View:
    def __init__(self):
        self._items: list = []
        self._on_scene_rebuild = None
        self._carriage_x_m     = 0.15
        self._anim_mandrel_angle = 0.0
        self.L_m             = 0.3
        self.carriage_items  = []
        self.mandrel_items   = []
    def addItem(self, item):
        self._items.append(item)
    def removeItem(self, item):
        if item in self._items:
            self._items.remove(item)


class _Topology:
    ribbon_max_seg = 50
    shell_faces    = np.zeros((10, 3),  dtype=np.int32)
    heatmap_verts  = np.zeros((96, 3),  dtype=np.float32)
    heatmap_faces  = np.zeros((80, 3),  dtype=np.int32)


class _MinFrame:
    """Tüm alanları "boş/sıfır" olan minimal RenderFrame."""
    is_shell_visible  = False
    shell_verts       = np.zeros((24, 3),  dtype=np.float32)
    shell_colors      = np.zeros((24, 4),  dtype=np.float32)
    heatmap_dirty     = False
    heatmap_vc        = np.zeros((96, 4),  dtype=np.float32)
    ribbon_n_segments = 0
    ribbon_verts      = np.zeros((4,  3),  dtype=np.float32)
    ribbon_faces      = np.zeros((2,  3),  dtype=np.int32)
    contact_xyz       = np.array([0.1, 0.0, 0.0], dtype=np.float32)
    eye_xyz           = np.array([0.1, 0.0, 0.0], dtype=np.float32)
    carriage_x_mm     = 150.0
    spindle_angle_deg = 0.0


class _VisFrame(_MinFrame):
    """Tüm renderer'ları görünür hale getirecek dolu frame."""
    is_shell_visible  = True
    shell_verts       = np.ones((24, 3),  dtype=np.float32)
    shell_colors      = np.ones((24, 4),  dtype=np.float32)
    heatmap_dirty     = True
    heatmap_vc        = np.ones((96, 4),  dtype=np.float32)
    ribbon_n_segments = 5
    ribbon_verts      = np.ones((10, 3),  dtype=np.float32)
    ribbon_faces      = np.ones((8,  3),  dtype=np.int32)
    contact_xyz       = np.array([0.1, 0.04, 0.0], dtype=np.float32)
    eye_xyz           = np.array([0.1, 0.0,  0.05], dtype=np.float32)
    carriage_x_mm     = 150.0
    spindle_angle_deg = 90.0


def _setup_renderer(cls, view, topology):
    """Her sınıf için doğru argümanlarla setup() çağır."""
    r = cls()
    if cls is MachineRenderer:
        r.setup(view, topology,
                carriage_items=view.carriage_items,
                mandrel_items=view.mandrel_items,
                L_m=view.L_m)
    else:
        r.setup(view, topology)
    return r


# ─────────────────────────────────────────────────────────────────────────────
# LA-01..03 — API varlığı ve setup sonrası _ready
# ─────────────────────────────────────────────────────────────────────────────

class TestLifecycleAPI:

    def test_la01_all_have_four_lifecycle_methods(self):
        """LA-01: Tüm 6 renderer sınıfı setup/update/teardown/reset metodlarına sahip."""
        for cls in _ALL_CLASSES:
            for meth in ("setup", "update", "teardown", "reset"):
                assert hasattr(cls, meth), f"{cls.__name__} eksik: {meth}"
                assert callable(getattr(cls(), meth)), f"{cls.__name__}.{meth} callable değil"

    def test_la02_ready_true_after_setup(self):
        """LA-02: setup() sonrası _ready=True (tüm renderer'lar)."""
        v   = _View()
        top = _Topology()
        for cls in _ALL_CLASSES:
            r = _setup_renderer(cls, v, top)
            assert r._ready is True, f"{cls.__name__}._ready=False after setup"
        # Temizle
        for item in v._items[:]:
            v.removeItem(item)

    def test_la03_ready_false_after_teardown(self):
        """LA-03: teardown() sonrası _ready=False (tüm renderer'lar)."""
        for cls in _ALL_CLASSES:
            v   = _View()
            top = _Topology()
            r   = _setup_renderer(cls, v, top)
            r.teardown()
            assert r._ready is False, f"{cls.__name__}._ready=True after teardown"


# ─────────────────────────────────────────────────────────────────────────────
# LA-04..06 — Güvenli teardown sonrası işlemler
# ─────────────────────────────────────────────────────────────────────────────

class TestPostTeardownSafety:

    def test_la04_double_teardown_no_exception(self):
        """LA-04: Çift teardown() exception üretmez (tüm renderer'lar)."""
        for cls in _ALL_CLASSES:
            v   = _View()
            top = _Topology()
            r   = _setup_renderer(cls, v, top)
            r.teardown()
            r.teardown()   # ikinci çağrı — exception olmamalı

    def test_la05_update_after_teardown_no_exception(self):
        """LA-05: teardown() sonrası update() exception üretmez (no-op)."""
        frame = _MinFrame()
        for cls in _ALL_CLASSES:
            v   = _View()
            top = _Topology()
            r   = _setup_renderer(cls, v, top)
            r.teardown()
            r.update(frame)   # no-op olmalı, exception yok

    def test_la06_reset_after_teardown_no_exception(self):
        """LA-06: teardown() sonrası reset() exception üretmez."""
        for cls in _ALL_CLASSES:
            v   = _View()
            top = _Topology()
            r   = _setup_renderer(cls, v, top)
            r.teardown()
            r.reset()   # exception olmamalı


# ─────────────────────────────────────────────────────────────────────────────
# LA-07..08 — Setup öncesi reset + view temizliği
# ─────────────────────────────────────────────────────────────────────────────

class TestPreSetupAndCleanup:

    def test_la07_reset_before_setup_no_exception(self):
        """LA-07: setup() çağrılmadan reset() exception üretmez."""
        for cls in _ALL_CLASSES:
            r = cls()
            r.reset()   # exception olmamalı

    def test_la08_teardown_removes_items_from_view(self):
        """LA-08: teardown() sonrası view._items boş (standart 5 renderer)."""
        top = _Topology()
        for cls in _STD_CLASSES:
            v = _View()
            r = _setup_renderer(cls, v, top)
            n_before = len(v._items)
            assert n_before > 0, f"{cls.__name__} setup() view'a item eklemedi"
            r.teardown()
            assert len(v._items) == 0, (
                f"{cls.__name__}: teardown sonrası {len(v._items)} item kaldı"
            )


# ─────────────────────────────────────────────────────────────────────────────
# LA-09..10 — İç state temizliği
# ─────────────────────────────────────────────────────────────────────────────

class TestInternalStateCleanup:

    def test_la09_fiberpath_pts_empty_after_teardown(self):
        """LA-09: FiberPathRenderer teardown() _pts listesini temizler."""
        v   = _View()
        top = _Topology()
        r   = FiberPathRenderer()
        r.setup(v, top)
        for _ in range(10):
            r.update(_VisFrame())
        assert len(r._pts) > 0, "update() _pts doldurmadı"
        r.teardown()
        assert len(r._pts) == 0, f"teardown sonrası _pts boş değil: {len(r._pts)}"

    def test_la10_machine_renderer_clears_item_lists_on_teardown(self):
        """LA-10: MachineRenderer teardown() carriage/mandrel listelerini temizler."""
        v   = _View()
        top = _Topology()
        # Sahte item listesi oluştur
        dummy_items = [object(), object()]
        r = MachineRenderer()
        r.setup(v, top,
                carriage_items=list(dummy_items),
                mandrel_items=list(dummy_items),
                L_m=v.L_m)
        assert len(r._carriage_items) == 2
        assert len(r._mandrel_items)  == 2
        r.teardown()
        assert r._carriage_items == [], f"_carriage_items temizlenmedi: {r._carriage_items}"
        assert r._mandrel_items  == [], f"_mandrel_items temizlenmedi: {r._mandrel_items}"


# ─────────────────────────────────────────────────────────────────────────────
# LA-11..12 — Item döngüsü ve reset sonrası visibility
# ─────────────────────────────────────────────────────────────────────────────

class TestItemCycleAndVisibility:

    def test_la11_setup_update_teardown_item_cycle(self):
        """LA-11: setup→update→teardown; teardown sonrası view._items boş."""
        top  = _Topology()
        vis  = _VisFrame()
        for cls in _STD_CLASSES:
            v = _View()
            r = _setup_renderer(cls, v, top)
            # Birkaç update
            for _ in range(5):
                r.update(vis)
            assert len(v._items) > 0, f"{cls.__name__}: update sonrası item yok"
            r.teardown()
            assert len(v._items) == 0, (
                f"{cls.__name__}: teardown sonrası {len(v._items)} item kaldı"
            )

    def test_la12_reset_hides_visible_items(self):
        """LA-12: update() ile görünür yapılan item'lar reset() sonrası gizlenir.

        HeatmapRenderer update() setVisible çağırmaz → HM hariç.
        MachineRenderer kendi GL item'ı yok → hariç.
        """
        top = _Topology()
        vis = _VisFrame()

        # S6.11.1: PayoutEyeRenderer._eye_item scatter dot kaldırıldı (None).
        # Görünürlük _ray_item üzerinden kontrol edilir.
        checks = {
            ShellRenderer:     lambda r: r._item._visible,
            RibbonRenderer:    lambda r: r._item._visible,
            FiberPathRenderer: lambda r: r._item._visible,
            PayoutEyeRenderer: lambda r: r._ray_item._visible,
        }

        for cls, get_visible in checks.items():
            v = _View()
            r = _setup_renderer(cls, v, top)
            # FiberPath için 2 update (1 nokta yeterli değil)
            r.update(vis)
            r.update(vis)
            assert get_visible(r) is True, f"{cls.__name__}: update sonrası visible değil"
            r.reset()
            assert get_visible(r) is False, f"{cls.__name__}: reset sonrası hâlâ visible"
