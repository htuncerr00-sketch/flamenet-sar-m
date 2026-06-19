"""
tests/test_s55_cycles.py — S5.5.5+S5.5.6 Lifecycle cycle + renderer ordering
==============================================================================
Play→Stop→Play→Reset→Play döngülerini ve renderer sırasını doğrular.

S5.5.5 — Lifecycle cycles (LC-01..10):
    Stop temizleme, reset temizleme, double stop güvenliği,
    reset-without-setup güvenliği, FiberPath trail cycle davranışı,
    play→stop→play fresh state.

S5.5.6 — Renderer ordering (LC-11..13):
    _renderers listesi deterministik sırada, sayı tutarlı, çoklu
    setup döngüsünde sıra değişmiyor.

RenderFrameBuilder gerçek; renderer'lar _TrackingRenderer (trail
takibi için _TrailedRenderer türevi de var).

Çalıştır:
    cd faz17_d1/faz17_d1_backend
    python -m pytest ../tests/test_s55_cycles.py -v
"""
from __future__ import annotations

import os
import sys
import math
import types
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np
import pytest

# ─── sys.path ─────────────────────────────────────────────────────────────────

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "faz17_d1_backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from faz17_d1.core.render_frame_builder import RenderFrameBuilder
from faz17_d1.core.lod_manager import AdaptiveLODManager

# ─── pyqtgraph + backend mocks ────────────────────────────────────────────────

if "pyqtgraph.opengl" not in sys.modules:
    _gl = types.ModuleType("pyqtgraph.opengl")

    class _GLItem:
        def __init__(self, **kw): pass
        def setVisible(self, v): pass
        def setMeshData(self, **kw): pass
        def setData(self, **kw): pass

    _gl.GLMeshItem = _GLItem
    _gl.GLLinePlotItem = _GLItem
    _gl.GLScatterPlotItem = _GLItem
    sys.modules.setdefault("pyqtgraph", types.ModuleType("pyqtgraph"))
    sys.modules["pyqtgraph.opengl"] = _gl

if "backend.core.render_frame" not in sys.modules:
    class _FakeEmpty:
        shell_verts  = np.zeros((24, 3), dtype=np.float32)
        shell_colors = np.ones((24, 4), dtype=np.float32)

    _rfm = types.ModuleType("backend.core.render_frame")
    _rfm.make_empty_frame = lambda top: _FakeEmpty()
    sys.modules.setdefault("backend",         types.ModuleType("backend"))
    sys.modules.setdefault("backend.core",    types.ModuleType("backend.core"))
    sys.modules["backend.core.render_frame"]  = _rfm

# ─── Mock twin + profile ──────────────────────────────────────────────────────

@dataclass
class _St:
    t_s: float = 0.0
    spindle_angle_deg: float = 0.0
    spindle_rpm: float = 60.0
    carriage_x_mm: float = 0.0
    carriage_x_actual_mm: float = 0.0
    carriage_v_mm_s: float = 0.0
    eye_x_mm: float = 0.0
    eye_r_mm: float = 80.0
    contact_z_mm: float = 0.0
    contact_r_mm: float = 50.0
    current_layer: int = 0
    current_circuit: int = 0
    fiber_deposited_mm: float = 0.0
    current_radius_mm: float = 50.0
    lag_error_mm: float = 0.0
    progress_pct: float = 0.0


@dataclass
class _Dep:
    z_bins: np.ndarray = field(
        default_factory=lambda: np.linspace(0, 300, 8))
    theta_bins: np.ndarray = field(
        default_factory=lambda: np.linspace(0, 2 * math.pi, 12))
    coverage_count: np.ndarray = field(
        default_factory=lambda: np.zeros((8, 12), dtype=np.int32))


@dataclass
class _Twin:
    states: List[_St]
    n_layers: int = 2
    base_radius_mm: float = 50.0
    final_radius_mm: float = 52.0
    total_fiber_length_mm: float = 1000.0
    max_lag_error_mm: float = 0.5
    max_spindle_rpm: float = 65.0
    layer_time_ranges_s: List[Tuple[float, float]] = field(default_factory=list)
    final_deposition: _Dep = field(default_factory=_Dep)


class _Profile:
    def __init__(self, L: float = 300.0, r: float = 50.0):
        self.z_mm = np.linspace(0, L, 100)
        self.r_mm = np.full(100, r, dtype=np.float64)
        self.avg_radius_mm = r

    def radius_at(self, z: float) -> float:
        return float(np.interp(z, self.z_mm, self.r_mm))


def _make_twin(n: int = 100) -> Tuple[_Twin, _Profile]:
    states = []
    for i in range(n):
        t = i / max(n - 1, 1)
        states.append(_St(
            t_s=float(i),
            spindle_angle_deg=float(i * 18.0),
            carriage_x_actual_mm=float(i * 300.0 / max(n - 1, 1)),
            eye_x_mm=float(i * 300.0 / max(n - 1, 1)),
            current_layer=int(i >= n // 2),
            current_radius_mm=50.0 + t * 2.0,
            progress_pct=t * 100.0,
        ))
    return _Twin(states=states), _Profile()


# ─── Mock GL view ─────────────────────────────────────────────────────────────

class _GL:
    def __init__(self):
        self._on_scene_rebuild   = None
        self._carriage_x_m       = 0.15
        self._anim_mandrel_angle = 0.0
        self.L_m                 = 0.3
    def addItem(self, item):    pass
    def removeItem(self, item): pass


# ─── Mock renderer'lar ────────────────────────────────────────────────────────

class _Tracker:
    """setup/update/teardown/reset sayacı."""
    def __init__(self, label: str = ""):
        self.label          = label
        self.setup_count    = 0
        self.teardown_count = 0
        self.update_count   = 0
        self.reset_count    = 0

    def setup(self, view, topology)  -> None: self.setup_count    += 1
    def teardown(self)               -> None: self.teardown_count += 1
    def update(self, frame)          -> None: self.update_count   += 1
    def reset(self, **kwargs)        -> None: self.reset_count    += 1


class _Trailed(_Tracker):
    """FiberPathRenderer izini simüle eder."""
    def __init__(self, label: str = ""):
        super().__init__(label)
        self._pts: list = []

    def update(self, frame) -> None:
        super().update(frame)
        self._pts.append(id(frame))

    def reset(self, **kwargs) -> None:
        super().reset(**kwargs)
        self._pts = []

    def teardown(self) -> None:
        super().teardown()
        self._pts = []


# ─── Cycle pipeline ───────────────────────────────────────────────────────────

class _CyclePipeline:
    """
    Panel animasyon döngüsünü minimal şekilde simüle eder.

    factory: callable() → renderer; her setup'ta n_renderers kadar çağrılır.
    """

    def __init__(self, n_renderers: int = 3, factory=None):
        self._n            = n_renderers
        self._factory      = factory or (lambda: _Tracker())
        self._renderers: list = []
        self._all: list   = []   # tüm oluşturulan renderer örnekleri
        self._builder      = None
        self._topology     = None
        self._twin_ref     = None
        self._profile_ref  = None
        self._lod          = AdaptiveLODManager()
        self._gl           = _GL()
        self._anim_idx     = 0

    def setup_builder(self, twin, profile) -> bool:
        self._stop_anim()
        if twin is None or not getattr(twin, 'states', None):
            return False
        self._twin_ref    = twin
        self._profile_ref = profile
        lod = self._lod.current
        self._topology = RenderFrameBuilder.build_topology(
            profile,
            shell_nz=lod.shell_nz, shell_nth=lod.shell_nth,
            heatmap_nz=lod.heatmap_nz, heatmap_nth=lod.heatmap_nth,
            ribbon_max_seg=lod.ribbon_max_seg,
        )
        dep = getattr(twin, 'final_deposition', None)
        self._builder = RenderFrameBuilder(
            twin, profile, self._topology, tow_width_mm=6.0, deposition=dep,
        )
        self._setup_renderers()
        self._apply(0)
        return True

    def _setup_renderers(self) -> None:
        self._teardown_renderers()
        rs = [self._factory() for _ in range(self._n)]
        self._all.extend(rs)
        self._renderers = rs
        for r in rs:
            r.setup(self._gl, self._topology)
        self._gl._on_scene_rebuild = self._on_gl_rebuild

    def _teardown_renderers(self) -> None:
        for r in self._renderers:
            r.teardown()
        self._renderers.clear()
        self._gl._on_scene_rebuild = None

    def _on_gl_rebuild(self) -> None:
        self._gl._on_scene_rebuild = None
        for r in self._renderers:
            r.teardown()
        self._renderers.clear()

    def _apply(self, idx: int) -> None:
        if self._builder is None:
            return
        n = self._builder.n_states
        idx = max(0, min(n - 1, idx))
        frame = self._builder.build(idx)
        for r in self._renderers:
            r.update(frame)
        self._anim_idx = idx

    def _stop_anim(self) -> None:
        """_stop_anim() panel davranışı: reset → teardown."""
        lod = self._lod
        if lod is not None:
            lod.reset()
        self._builder  = None
        self._anim_idx = 0
        center_mm = self._gl.L_m * 1000.0 / 2.0
        for r in self._renderers:
            if hasattr(r, 'reset'):
                try:
                    r.reset(center_x_mm=center_mm)
                except Exception:
                    pass
        self._teardown_renderers()

    def _on_anim_reset(self) -> None:
        """_on_anim_reset() panel davranışı: reset → apply(0)."""
        for r in self._renderers:
            if hasattr(r, 'reset'):
                try:
                    r.reset()
                except Exception:
                    pass
        self._anim_idx = 0
        if self._builder is not None:
            self._apply(0)

    def play_n(self, n: int) -> None:
        """n animasyon tick simülasyonu."""
        if self._builder is None:
            return
        total = self._builder.n_states
        for _ in range(n):
            self._anim_idx = min(self._anim_idx + 1, total - 1)
            self._apply(self._anim_idx)


# ─────────────────────────────────────────────────────────────────────────────
# S5.5.5 — Lifecycle cycles
# ─────────────────────────────────────────────────────────────────────────────

class TestLifecycleCycles:

    def test_lc01_stop_clears_trail(self):
        """LC-01: play→stop sonrası FiberPath trail boş (yeni renderers oluşuyor)."""
        twin, profile = _make_twin()
        pipe = _CyclePipeline(n_renderers=2, factory=lambda: _Trailed())
        pipe.setup_builder(twin, profile)
        pipe.play_n(20)
        # En az bir trailed renderer'ın trail'i dolmuş olmalı
        assert any(len(r._pts) > 0 for r in pipe._renderers), "play sonrası trail boş"
        pipe._stop_anim()
        # stop: teardown yaptı → tüm renderer'lar gitii; _renderers boş
        assert len(pipe._renderers) == 0

    def test_lc02_reset_clears_trail(self):
        """LC-02: play→reset sonrası trail temizlenir."""
        twin, profile = _make_twin()
        pipe = _CyclePipeline(n_renderers=2, factory=lambda: _Trailed())
        pipe.setup_builder(twin, profile)
        pipe.play_n(20)
        accumulated = sum(len(r._pts) for r in pipe._renderers)
        assert accumulated > 0, "play sonrası trail birikmedi"
        pipe._on_anim_reset()
        after = sum(len(r._pts) for r in pipe._renderers)
        # reset() _pts temizledi; _apply(0) tek frame ekledi
        assert after <= pipe._n, (
            f"reset sonrası trail beklenen ≤ {pipe._n}, alınan {after}"
        )

    def test_lc03_stop_then_play_fresh_renderers(self):
        """LC-03: stop→play yeni renderer örnekleri oluşturur (eski state yok)."""
        twin, profile = _make_twin()
        call_order: list = []
        def factory():
            r = _Tracker(label=str(len(call_order)))
            call_order.append(r)
            return r
        pipe = _CyclePipeline(n_renderers=2, factory=factory)
        pipe.setup_builder(twin, profile)
        first_gen = list(pipe._renderers)
        pipe.play_n(10)
        pipe._stop_anim()
        pipe.setup_builder(twin, profile)
        second_gen = list(pipe._renderers)
        # İkinci nesil tamamen farklı objeler
        assert not any(r in second_gen for r in first_gen), (
            "stop→play: eski renderer örnekleri yeniden kullanıldı"
        )

    def test_lc04_double_stop_no_exception(self):
        """LC-04: Çift _stop_anim() exception üretmez."""
        twin, profile = _make_twin()
        pipe = _CyclePipeline()
        pipe.setup_builder(twin, profile)
        pipe._stop_anim()
        pipe._stop_anim()   # ikinci stop — exception olmamalı

    def test_lc05_reset_without_setup_no_exception(self):
        """LC-05: setup yapılmadan _on_anim_reset() exception üretmez."""
        pipe = _CyclePipeline()
        pipe._on_anim_reset()

    def test_lc06_reset_then_stop_no_exception(self):
        """LC-06: reset → stop sırası exception üretmez."""
        twin, profile = _make_twin()
        pipe = _CyclePipeline()
        pipe.setup_builder(twin, profile)
        pipe._on_anim_reset()
        pipe._stop_anim()

    def test_lc07_play_stop_play_reset_play(self):
        """LC-07: play→stop→play→reset→play tam döngüsü exception üretmez."""
        twin, profile = _make_twin()
        pipe = _CyclePipeline(n_renderers=3)
        pipe.setup_builder(twin, profile)
        pipe.play_n(10)
        pipe._stop_anim()
        pipe.setup_builder(twin, profile)
        pipe.play_n(10)
        pipe._on_anim_reset()
        pipe.play_n(5)

    def test_lc08_renderers_count_consistent_after_stop_play(self):
        """LC-08: stop→play sonrası _renderers sayısı n_renderers."""
        twin, profile = _make_twin()
        pipe = _CyclePipeline(n_renderers=4)
        pipe.setup_builder(twin, profile)
        assert len(pipe._renderers) == 4
        pipe._stop_anim()
        assert len(pipe._renderers) == 0
        pipe.setup_builder(twin, profile)
        assert len(pipe._renderers) == 4

    def test_lc09_fiberpath_pts_empty_after_reset_before_play(self):
        """LC-09: reset() hemen ardından pts boş; play başlamadan durum temiz."""
        twin, profile = _make_twin()
        pipe = _CyclePipeline(n_renderers=2, factory=lambda: _Trailed())
        pipe.setup_builder(twin, profile)
        pipe.play_n(30)
        pipe._on_anim_reset()
        # _apply(0) çağrısı sonrası en fazla 1 nokta her trail'de
        for r in pipe._renderers:
            assert len(r._pts) <= 1, (
                f"reset sonrası trail beklenen ≤1, alınan {len(r._pts)}"
            )

    def test_lc10_reset_count_matches_reset_calls(self):
        """LC-10: _on_anim_reset() R kez çağrılırsa her renderer R reset aldı."""
        twin, profile = _make_twin()
        pipe = _CyclePipeline(n_renderers=3)
        pipe.setup_builder(twin, profile)
        trackers = list(pipe._renderers)
        for _ in range(5):
            pipe._on_anim_reset()
        for r in trackers:
            assert r.reset_count == 5, (
                f"Beklenen 5 reset, alınan {r.reset_count}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# S5.5.6 — Renderer ordering
# ─────────────────────────────────────────────────────────────────────────────

class TestRendererOrdering:

    def test_lc11_renderers_count_is_n_after_setup(self):
        """LC-11: setup_builder sonrası _renderers listesi tam n_renderers uzunluğu."""
        twin, profile = _make_twin()
        for n in (1, 3, 5):
            pipe = _CyclePipeline(n_renderers=n)
            pipe.setup_builder(twin, profile)
            assert len(pipe._renderers) == n, (
                f"n={n}: beklenen {n}, alınan {len(pipe._renderers)}"
            )

    def test_lc12_renderer_order_deterministic_across_setups(self):
        """LC-12: Aynı factory ile iki ardışık setup → örnekler farklı ama sıra aynı."""
        twin, profile = _make_twin()
        counter = [0]
        def factory():
            counter[0] += 1
            r = _Tracker(label=str(counter[0]))
            return r

        pipe = _CyclePipeline(n_renderers=4, factory=factory)
        pipe.setup_builder(twin, profile)
        first_labels = [r.label for r in pipe._renderers]
        pipe._stop_anim()
        pipe.setup_builder(twin, profile)
        second_labels = [r.label for r in pipe._renderers]

        # Hem ilk hem ikinci sıra sıralı (factory çağrı sırası korunuyor)
        assert first_labels  == sorted(first_labels,  key=int), \
            f"İlk sıra bozuk: {first_labels}"
        assert second_labels == sorted(second_labels, key=int), \
            f"İkinci sıra bozuk: {second_labels}"
        # İki set çakışmıyor (tamamen farklı örnekler)
        assert set(first_labels).isdisjoint(set(second_labels)), \
            "stop→play: eski renderer'lar listede kaldı"

    def test_lc13_stop_does_not_leave_orphan_renderers(self):
        """LC-13: stop sonrası _renderers boş; önceki örnekler teardown aldı."""
        twin, profile = _make_twin()
        pipe = _CyclePipeline(n_renderers=3)
        pipe.setup_builder(twin, profile)
        prev = list(pipe._renderers)
        pipe._stop_anim()
        assert len(pipe._renderers) == 0, f"stop sonrası orphan: {len(pipe._renderers)}"
        for r in prev:
            assert r.teardown_count == 1, (
                f"teardown_count={r.teardown_count} (beklenen 1)"
            )
