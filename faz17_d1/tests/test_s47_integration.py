"""
tests/test_s47_integration.py — S4.7 headless entegrasyon testleri
===================================================================
Panel animasyon yaşam döngüsünü Qt/GL olmadan doğrular.

Gerçek bileşenler : RenderFrameBuilder, AdaptiveLODManager (headless)
Mock bileşenler   : renderer'lar, GL view, Qt widget'ları
Test konuları     : setup→apply→stop, guard'lar, LOD rebuild, trail temizleme,
                    double-stop, scene rebuild, bellek stabilitesi, null twin

Çalıştır:
    cd faz17_d1/faz17_d1_backend
    python -m pytest ../tests/test_s47_integration.py -v
"""
from __future__ import annotations

import sys
import os
import math
import importlib.util
import types
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
import pytest

# ─── Python yolu ─────────────────────────────────────────────────────────────

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "faz17_d1_backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from faz17_d1.core.render_frame_builder import RenderFrameBuilder
from faz17_d1.core.render_frame import RenderSceneTopology
from faz17_d1.core.lod_manager import AdaptiveLODManager, _DEFAULT_LEVELS

# ─── pyqtgraph.opengl mock (renderer'lar import ediyor) ──────────────────────

class _GLItem:
    def __init__(self, **kw): self._visible = False
    def setVisible(self, v): self._visible = v
    def setMeshData(self, **kw): pass
    def setData(self, **kw): pass


_gl_mock = types.ModuleType("pyqtgraph.opengl")
_gl_mock.GLMeshItem      = _GLItem
_gl_mock.GLLinePlotItem  = _GLItem
sys.modules.setdefault("pyqtgraph", types.ModuleType("pyqtgraph"))
sys.modules["pyqtgraph.opengl"] = _gl_mock

# ─── backend.core.render_frame mock (ShellRenderer.setup için) ───────────────

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

_RENDERERS = os.path.join(
    os.path.dirname(__file__),
    "../../faz17_d2/faz17_d2_app/faz17_d2/app/renderers",
)


def _load(name: str):
    path = os.path.join(_RENDERERS, f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FiberPathRenderer = _load("fiber_path_renderer").FiberPathRenderer

# ─────────────────────────────────────────────────────────────────────────────
# Minimal mock twin + profile (fizik çağrısı yok)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _State:
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
class _MockDep:
    z_bins: np.ndarray = field(
        default_factory=lambda: np.linspace(0, 300, 8))
    theta_bins: np.ndarray = field(
        default_factory=lambda: np.linspace(0, 2 * math.pi, 12))
    coverage_count: np.ndarray = field(
        default_factory=lambda: np.zeros((8, 12), dtype=np.int32))


@dataclass
class _MockTwin:
    states: List[_State]
    n_layers: int = 2
    base_radius_mm: float = 50.0
    final_radius_mm: float = 52.0
    total_fiber_length_mm: float = 1000.0
    max_lag_error_mm: float = 0.5
    max_spindle_rpm: float = 65.0
    layer_time_ranges_s: List[Tuple[float, float]] = field(default_factory=list)
    final_deposition: _MockDep = field(default_factory=_MockDep)


class _MockProfile:
    def __init__(self, L_mm: float = 300.0, r_mm: float = 50.0):
        self.z_mm = np.linspace(0, L_mm, 100)
        self.r_mm = np.full(100, r_mm, dtype=np.float64)
        self.avg_radius_mm = r_mm

    def radius_at(self, z: float) -> float:
        return float(np.interp(z, self.z_mm, self.r_mm))


def _make_states(n: int = 500) -> List[_State]:
    states = []
    for i in range(n):
        t = i / max(n - 1, 1)
        states.append(_State(
            t_s=float(i),
            spindle_angle_deg=float(i * 18.0),
            carriage_x_actual_mm=float(i * 300.0 / max(n - 1, 1)),
            eye_x_mm=float(i * 300.0 / max(n - 1, 1)),
            current_layer=int(i >= n // 2),
            current_radius_mm=50.0 + t * 2.0,
            progress_pct=t * 100.0,
        ))
    return states


def _twin_and_profile(n: int = 500) -> Tuple[_MockTwin, _MockProfile]:
    return _MockTwin(states=_make_states(n)), _MockProfile()

# ─────────────────────────────────────────────────────────────────────────────
# Mock renderer'lar
# ─────────────────────────────────────────────────────────────────────────────

class _TrackingRenderer:
    """setup/update/teardown/reset çağrılarını sayan minimal renderer mock."""

    def __init__(self):
        self.setup_count    = 0
        self.update_count   = 0
        self.teardown_count = 0
        self.reset_count    = 0

    def setup(self, view, topology) -> None:
        self.setup_count += 1

    def update(self, frame) -> None:
        self.update_count += 1

    def teardown(self) -> None:
        self.teardown_count += 1

    def reset(self) -> None:
        self.reset_count += 1


class _TrailedRenderer(_TrackingRenderer):
    """FiberPathRenderer izini simüle eder: _pts biriktirir, reset() temizler."""

    def __init__(self):
        super().__init__()
        self._pts: list = []

    def update(self, frame) -> None:
        super().update(frame)
        self._pts.append(id(frame))   # herhangi bir izlenebilir değer

    def reset(self) -> None:
        super().reset()
        self._pts = []


class _MockGL:
    """_MachineGLView mock — _on_scene_rebuild callback alanı için."""
    def __init__(self):
        self._on_scene_rebuild = None
        self._carriage_x_m     = 0.15
        self._anim_mandrel_angle = 0.0
        self.L_m = 0.3

    def addItem(self, item): pass
    def removeItem(self, item): pass


# ─────────────────────────────────────────────────────────────────────────────
# _ControllableLOD — deterministik level_changed için
# ─────────────────────────────────────────────────────────────────────────────

class _ControllableLOD:
    """
    Belirli tick numarasında level_changed=True üreten LOD mock.

    Gerçek AdaptiveLODManager'ın tick()/force_level() reset sorununu atlatır.
    """

    def __init__(self, change_at_tick: Optional[int] = None, start_idx: int = 1):
        self.change_at_tick = change_at_tick
        self.tick_count     = 0
        self._idx           = start_idx
        self._levels        = _DEFAULT_LEVELS

    def tick(self) -> None:
        self.tick_count += 1
        if (self.change_at_tick is not None
                and self.tick_count == self.change_at_tick):
            self._idx = min(self._idx + 1, len(self._levels) - 1)

    @property
    def level_changed(self) -> bool:
        return (self.change_at_tick is not None
                and self.tick_count == self.change_at_tick)

    @property
    def current(self):
        return self._levels[self._idx]

    @property
    def fps_measured(self) -> float:
        return 28.5

    def reset(self) -> None:
        self.tick_count = 0

    def force_level(self, idx: int) -> None:
        self._idx = max(0, min(idx, len(self._levels) - 1))


# ─────────────────────────────────────────────────────────────────────────────
# _AnimPipeline — test koşum takımı
# ─────────────────────────────────────────────────────────────────────────────

class _AnimPipeline:
    """
    Panel animasyon yaşam döngüsünü Qt olmadan simüle eden test koşum takımı.

    Gerçek : RenderFrameBuilder, AdaptiveLODManager (headless)
    Mock   : renderer'lar (_TrackingRenderer), GL view (_MockGL)
    """

    def __init__(self,
                 renderer_factory=None,
                 n_renderers: int = 3) -> None:
        self._renderer_factory = renderer_factory or (lambda: _TrackingRenderer())
        self._n_renderers = n_renderers

        self._builder    = None
        self._topology   = None
        self._renderers: list = []
        self._anim_idx: int = 0
        self._twin_ref   = None
        self._profile_ref = None
        self._lod        = None
        self._last_coverage = None
        self._anim_tow_w_mm = 6.0
        self._gl         = _MockGL()

    # ── Yaşam döngüsü (panel metodlarını ayna eder) ───────────────────────────

    def setup_builder(self, twin, profile,
                      tow_width_mm: float = 6.0,
                      coverage=None) -> bool:
        self._stop_anim()
        if twin is None or not getattr(twin, 'states', None):
            return False
        try:
            self._twin_ref    = twin
            self._profile_ref = profile
            self._last_coverage = coverage
            self._anim_tow_w_mm = float(tow_width_mm)

            if self._lod is None:
                self._lod = AdaptiveLODManager()

            lod = self._lod.current
            self._topology = RenderFrameBuilder.build_topology(
                profile,
                shell_nz=lod.shell_nz, shell_nth=lod.shell_nth,
                heatmap_nz=lod.heatmap_nz, heatmap_nth=lod.heatmap_nth,
                ribbon_max_seg=lod.ribbon_max_seg,
            )
            dep = coverage or getattr(twin, 'final_deposition', None)
            self._builder = RenderFrameBuilder(
                twin, profile, self._topology,
                tow_width_mm=float(tow_width_mm),
                deposition=dep,
            )
            self._setup_renderers(self._topology)
            self._anim_idx = 0
            self._apply_anim_frame(0)
            return True
        except Exception:
            return False

    def _setup_renderers(self, topology) -> None:
        self._teardown_renderers()
        self._renderers = [self._renderer_factory()
                           for _ in range(self._n_renderers)]
        for r in self._renderers:
            try:
                r.setup(None, topology)
            except Exception:
                pass
        self._gl._on_scene_rebuild = self._on_gl_scene_rebuild

    def _teardown_renderers(self) -> None:
        for r in self._renderers:
            try:
                r.teardown()
            except Exception:
                pass
        self._renderers.clear()

    def _apply_anim_frame(self, idx: int) -> None:
        if self._builder is None:
            return
        n = self._builder.n_states
        idx = max(0, min(n - 1, idx))
        frame = self._builder.build(idx)
        for r in self._renderers:
            try:
                r.update(frame)
            except Exception:
                pass

    def _stop_anim(self) -> None:
        lod = self._lod
        if lod is not None:
            try:
                lod.reset()
            except Exception:
                pass
        self._builder  = None
        self._anim_idx = 0
        self._teardown_renderers()

    def _on_anim_reset(self) -> None:
        for r in self._renderers:
            if hasattr(r, 'reset'):
                try:
                    r.reset()
                except Exception:
                    pass
        self._anim_idx = 0
        if self._builder is not None:
            self._apply_anim_frame(0)

    def _rebuild_topology(self) -> None:
        if self._twin_ref is None or self._profile_ref is None:
            return
        lod = self._lod.current
        self._topology = RenderFrameBuilder.build_topology(
            self._profile_ref,
            shell_nz=lod.shell_nz, shell_nth=lod.shell_nth,
            heatmap_nz=lod.heatmap_nz, heatmap_nth=lod.heatmap_nth,
            ribbon_max_seg=lod.ribbon_max_seg,
        )
        dep = self._last_coverage or getattr(self._twin_ref, 'final_deposition', None)
        self._builder = RenderFrameBuilder(
            self._twin_ref, self._profile_ref, self._topology,
            tow_width_mm=self._anim_tow_w_mm,
            deposition=dep,
        )
        self._setup_renderers(self._topology)
        self._apply_anim_frame(self._anim_idx)

    def _anim_tick(self) -> bool:
        """Tek tick simülasyonu. True → animasyon devam ediyor."""
        if self._builder is None:
            return False
        if self._lod is not None:
            self._lod.tick()
            if self._lod.level_changed:
                self._rebuild_topology()
        n = self._builder.n_states
        base_step = max(1, n // 300)
        self._anim_idx = min(self._anim_idx + base_step, n - 1)
        self._apply_anim_frame(self._anim_idx)
        return self._anim_idx < n - 1

    def _on_gl_scene_rebuild(self) -> None:
        gl = self._gl
        if gl is not None:
            gl._on_scene_rebuild = None   # S4.6.4 guard
        for r in self._renderers:
            try:
                r.teardown()
            except Exception:
                pass
        self._renderers.clear()


# ─────────────────────────────────────────────────────────────────────────────
# S4.7.1 — Temel yaşam döngüsü
# ─────────────────────────────────────────────────────────────────────────────

class TestBasicLifecycle:

    def test_it01_setup_apply_stop_counts(self):
        """IT-01: setup → apply×3 → stop; her renderer 1 setup, 3 update, 1 teardown."""
        twin, profile = _twin_and_profile(n=500)
        pipeline = _AnimPipeline(n_renderers=3)
        pipeline.setup_builder(twin, profile)

        # setup_builder içinde apply(0) çağrılıyor → update_count = 1
        trackers = list(pipeline._renderers)   # teardown'dan önce referansları al
        pipeline._apply_anim_frame(100)        # update_count = 2
        pipeline._apply_anim_frame(499)        # update_count = 3
        pipeline._stop_anim()

        assert len(pipeline._renderers) == 0,  "_stop_anim sonrası renderers boş"
        assert pipeline._builder is None,      "_stop_anim sonrası builder None"
        assert len(trackers) == 3

        for r in trackers:
            assert r.setup_count    == 1, f"setup_count={r.setup_count}"
            assert r.update_count   == 3, f"update_count={r.update_count}"
            assert r.teardown_count == 1, f"teardown_count={r.teardown_count}"

    def test_it02_anim_idx_zero_after_stop(self):
        """IT-02: _stop_anim() sonrası _anim_idx sıfırlanır."""
        twin, profile = _twin_and_profile()
        pipeline = _AnimPipeline()
        pipeline.setup_builder(twin, profile)
        pipeline._anim_idx = 250
        pipeline._stop_anim()
        assert pipeline._anim_idx == 0

    def test_it03_builder_not_none_after_setup(self):
        """IT-03: Geçerli twin ile setup sonrası builder None değil."""
        twin, profile = _twin_and_profile()
        pipeline = _AnimPipeline()
        ok = pipeline.setup_builder(twin, profile)
        assert ok is True
        assert pipeline._builder is not None
        assert pipeline._builder.n_states == 500

    def test_it04_n_renderers_correct_after_setup(self):
        """IT-04: setup sonrası _renderers listesi n_renderers uzunluğunda."""
        twin, profile = _twin_and_profile()
        pipeline = _AnimPipeline(n_renderers=4)
        pipeline.setup_builder(twin, profile)
        assert len(pipeline._renderers) == 4


# ─────────────────────────────────────────────────────────────────────────────
# S4.7.2 — builder=None guard
# ─────────────────────────────────────────────────────────────────────────────

class TestNullBuilderGuard:

    def test_it05_stop_without_builder_no_exception(self):
        """IT-05: _builder=None iken _stop_anim() exception üretmez."""
        pipeline = _AnimPipeline()
        assert pipeline._builder is None
        pipeline._stop_anim()   # must not raise

    def test_it06_apply_without_builder_no_exception(self):
        """IT-06: _builder=None iken _apply_anim_frame() exception üretmez."""
        pipeline = _AnimPipeline()
        pipeline._apply_anim_frame(0)
        pipeline._apply_anim_frame(100)

    def test_it07_reset_without_builder_no_exception(self):
        """IT-07: _builder=None iken _on_anim_reset() exception üretmez."""
        pipeline = _AnimPipeline()
        pipeline._on_anim_reset()

    def test_it08_tick_without_builder_returns_false(self):
        """IT-08: _builder=None iken _anim_tick() False döner."""
        pipeline = _AnimPipeline()
        result = pipeline._anim_tick()
        assert result is False

    def test_it09_double_stop_safe(self):
        """IT-09: setup → stop → stop; ikinci stop güvenli."""
        twin, profile = _twin_and_profile()
        pipeline = _AnimPipeline()
        pipeline.setup_builder(twin, profile)
        pipeline._stop_anim()   # ilk stop → builder = None
        pipeline._stop_anim()   # ikinci stop → güvenli


# ─────────────────────────────────────────────────────────────────────────────
# S4.7.4 — trail reset (200 frame sonrası _on_anim_reset)
# ─────────────────────────────────────────────────────────────────────────────

class TestTrailReset:

    def test_it13_trail_empty_after_reset(self):
        """IT-13: 200 frame → _on_anim_reset() → trail temizlendi."""
        twin, profile = _twin_and_profile(n=500)
        trailed = _TrailedRenderer()
        pipeline = _AnimPipeline(
            renderer_factory=lambda: trailed,
            n_renderers=1,
        )
        pipeline.setup_builder(twin, profile)

        for i in range(200):
            pipeline._apply_anim_frame(i)

        assert len(trailed._pts) > 0, "Trail birikmiş olmalı"
        accumulated = len(trailed._pts)
        pipeline._on_anim_reset()
        # _on_anim_reset: reset() → _pts=[] → _apply_anim_frame(0) → _pts=[1 giriş]
        # Trail 200'den ≤ 1'e düştü: temizleme başarılı
        assert len(trailed._pts) < accumulated, "Reset trail birikintisini temizledi"
        assert len(trailed._pts) <= 1, "Reset sonrası en fazla 1 giriş (frame 0)"

    def test_it14_trail_accumulates_before_reset(self):
        """IT-14: reset öncesi trail birikiyor."""
        twin, profile = _twin_and_profile()
        trailed = _TrailedRenderer()
        pipeline = _AnimPipeline(renderer_factory=lambda: trailed, n_renderers=1)
        pipeline.setup_builder(twin, profile)
        before = len(trailed._pts)
        for i in range(50):
            pipeline._apply_anim_frame(i)
        assert len(trailed._pts) > before

    def test_it15_reset_count_incremented(self):
        """IT-15: _on_anim_reset() sonrası reset_count artar."""
        twin, profile = _twin_and_profile()
        trailed = _TrailedRenderer()
        pipeline = _AnimPipeline(renderer_factory=lambda: trailed, n_renderers=1)
        pipeline.setup_builder(twin, profile)
        assert trailed.reset_count == 0
        pipeline._on_anim_reset()
        assert trailed.reset_count == 1

    def test_it16_trail_restarts_after_reset(self):
        """IT-16: reset sonrası frame uygulamak trail'i yeniden başlatır."""
        twin, profile = _twin_and_profile()
        trailed = _TrailedRenderer()
        pipeline = _AnimPipeline(renderer_factory=lambda: trailed, n_renderers=1)
        pipeline.setup_builder(twin, profile)
        for i in range(100):
            pipeline._apply_anim_frame(i)
        pipeline._on_anim_reset()
        # reset() temizler; _apply_anim_frame(0) 1 giriş ekler → toplam 1
        after_reset = len(trailed._pts)
        assert after_reset <= 1, f"Reset sonrası maksimum 1 giriş, alınan={after_reset}"
        # Sonraki frame → trail devam ediyor
        pipeline._apply_anim_frame(10)
        assert len(trailed._pts) == after_reset + 1


# ─────────────────────────────────────────────────────────────────────────────
# S4.7.3 — double stop
# ─────────────────────────────────────────────────────────────────────────────

class TestDoubleStop:

    def test_it10_teardown_called_exactly_once(self):
        """IT-10: stop() → stop(); teardown her renderer için yalnız 1 kez çağrılır."""
        twin, profile = _twin_and_profile()
        pipeline = _AnimPipeline(n_renderers=3)
        pipeline.setup_builder(twin, profile)

        trackers = list(pipeline._renderers)
        pipeline._stop_anim()   # teardown_count = 1; _renderers temizlendi

        for r in trackers:
            assert r.teardown_count == 1, "İlk stop sonrası teardown=1"

        pipeline._stop_anim()   # ikinci stop — _renderers boş, tekrar teardown YOK

        for r in trackers:
            assert r.teardown_count == 1, "İkinci stop teardown_count'u artırmamalı"

    def test_it11_renderers_empty_after_double_stop(self):
        """IT-11: Double stop sonrası _renderers boş kalır."""
        twin, profile = _twin_and_profile()
        pipeline = _AnimPipeline()
        pipeline.setup_builder(twin, profile)
        pipeline._stop_anim()
        pipeline._stop_anim()
        assert len(pipeline._renderers) == 0

    def test_it12_builder_none_after_double_stop(self):
        """IT-12: Double stop sonrası _builder None kalır."""
        twin, profile = _twin_and_profile()
        pipeline = _AnimPipeline()
        pipeline.setup_builder(twin, profile)
        pipeline._stop_anim()
        pipeline._stop_anim()
        assert pipeline._builder is None


# ─────────────────────────────────────────────────────────────────────────────
# S4.7.5 — LOD rebuild
# ─────────────────────────────────────────────────────────────────────────────

class TestLODRebuild:

    def test_it17_rebuild_creates_new_builder(self):
        """IT-17: LOD rebuild → yeni builder nesnesi oluşturulur."""
        twin, profile = _twin_and_profile()
        pipeline = _AnimPipeline(n_renderers=2)
        pipeline.setup_builder(twin, profile)

        old_builder = pipeline._builder
        ctrl_lod = _ControllableLOD(change_at_tick=1)
        pipeline._lod = ctrl_lod

        pipeline._anim_tick()   # tick 1 → level_changed → rebuild

        assert pipeline._builder is not old_builder, "Yeni builder üretilmeli"
        assert pipeline._builder is not None

    def test_it18_anim_idx_advances_after_rebuild(self):
        """IT-18: Rebuild olan tick'ten sonra _anim_idx ilerler."""
        twin, profile = _twin_and_profile()
        pipeline = _AnimPipeline()
        pipeline.setup_builder(twin, profile)

        pipeline._lod = _ControllableLOD(change_at_tick=1)

        pipeline._anim_tick()   # rebuild + adım
        assert pipeline._anim_idx > 0, "Rebuild tick'inde frame atlanmadı"

    def test_it19_renderers_resetup_after_rebuild(self):
        """IT-19: Rebuild → renderer'lar yeniden setup edildi."""
        twin, profile = _twin_and_profile()
        pipeline = _AnimPipeline(n_renderers=2)
        pipeline.setup_builder(twin, profile)

        pipeline._lod = _ControllableLOD(change_at_tick=1)

        pipeline._anim_tick()   # rebuild

        # Yeni renderer listesi oluşturulmuş; her biri setup_count=1
        assert len(pipeline._renderers) == 2
        for r in pipeline._renderers:
            assert r.setup_count == 1

    def test_it20_no_rebuild_without_level_change(self):
        """IT-20: level_changed=False iken builder değişmez."""
        twin, profile = _twin_and_profile()
        pipeline = _AnimPipeline()
        pipeline.setup_builder(twin, profile)

        ctrl_lod = _ControllableLOD(change_at_tick=None)   # asla değişmez
        pipeline._lod = ctrl_lod
        old_builder = pipeline._builder

        for _ in range(5):
            pipeline._anim_tick()

        assert pipeline._builder is old_builder, "Builder değişmemeli"

    def test_it21_lod_idx_changes_on_rebuild(self):
        """IT-21: Rebuild sonrası _lod seviyesi değişti."""
        twin, profile = _twin_and_profile()
        pipeline = _AnimPipeline()
        pipeline.setup_builder(twin, profile)

        ctrl_lod = _ControllableLOD(change_at_tick=1, start_idx=1)
        pipeline._lod = ctrl_lod

        pipeline._anim_tick()

        # start_idx=1 (MED), tick 1'de level arttı → HIGH (idx=2)
        assert ctrl_lod._idx == 2
