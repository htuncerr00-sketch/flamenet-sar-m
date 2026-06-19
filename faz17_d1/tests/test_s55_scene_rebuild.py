"""
tests/test_s55_scene_rebuild.py — S5.5.4 Scene rebuild stress testleri
=======================================================================
_AnimPipeline benzeri koşum takımı kullanarak sahne yeniden oluşturma
senaryolarını doğrular:

RB-01..02 : Rapid rebuild döngüsü (20x) renderer sayısı tutarlılığı
RB-03..04 : Callback guard — çift tetikleme double-teardown üretmez
RB-05..06 : Rebuild sonrası setup/teardown sayacı dengede (sızıntı yok)
RB-07..08 : Rebuild sırasında builder korunuyor; apply_frame çalışıyor

Qt/GL bağımlılığı yok; RenderFrameBuilder gerçek, renderer'lar mock.

Çalıştır:
    cd faz17_d1/faz17_d1_backend
    python -m pytest ../tests/test_s55_scene_rebuild.py -v
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
from faz17_d1.core.lod_manager import AdaptiveLODManager, _DEFAULT_LEVELS

# ─── pyqtgraph.opengl mock ────────────────────────────────────────────────────

if "pyqtgraph.opengl" not in sys.modules:
    _gl_mock = types.ModuleType("pyqtgraph.opengl")

    class _GLItem:
        def __init__(self, **kw): self._visible = False
        def setVisible(self, v): self._visible = v
        def setMeshData(self, **kw): pass
        def setData(self, **kw): pass

    _gl_mock.GLMeshItem        = _GLItem
    _gl_mock.GLLinePlotItem    = _GLItem
    _gl_mock.GLScatterPlotItem = _GLItem
    sys.modules.setdefault("pyqtgraph", types.ModuleType("pyqtgraph"))
    sys.modules["pyqtgraph.opengl"] = _gl_mock

# ─── backend mock ─────────────────────────────────────────────────────────────

if "backend.core.render_frame" not in sys.modules:
    class _FakeEmptyFrame:
        shell_verts  = np.zeros((24, 3), dtype=np.float32)
        shell_colors = np.ones((24, 4), dtype=np.float32)

    _rf_mock = types.ModuleType("backend.core.render_frame")
    _rf_mock.make_empty_frame = lambda top: _FakeEmptyFrame()
    sys.modules.setdefault("backend",         types.ModuleType("backend"))
    sys.modules.setdefault("backend.core",    types.ModuleType("backend.core"))
    sys.modules["backend.core.render_frame"]  = _rf_mock

# ─── Mock twin + profile ──────────────────────────────────────────────────────

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


def _make_states(n: int = 100) -> List[_State]:
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


def _twin_and_profile() -> Tuple[_MockTwin, _MockProfile]:
    return _MockTwin(states=_make_states(100)), _MockProfile()


# ─── Mock GL view ─────────────────────────────────────────────────────────────

class _MockGL:
    def __init__(self):
        self._on_scene_rebuild   = None
        self._carriage_x_m       = 0.15
        self._anim_mandrel_angle = 0.0
        self.L_m                 = 0.3
    def addItem(self, item):    pass
    def removeItem(self, item): pass


# ─── Tracking renderer ────────────────────────────────────────────────────────

class _TrackingRenderer:
    """setup/update/teardown/reset çağrılarını sayar."""
    def __init__(self):
        self.setup_count    = 0
        self.teardown_count = 0
        self.update_count   = 0
        self.reset_count    = 0

    def setup(self, view, topology) -> None:
        self.setup_count += 1

    def teardown(self) -> None:
        self.teardown_count += 1

    def update(self, frame) -> None:
        self.update_count += 1

    def reset(self, **kwargs) -> None:
        self.reset_count += 1


# ─── Minimal _AnimPipeline (S5.5.4 kapsamına özel) ───────────────────────────

class _RebuildPipeline:
    """
    Scene rebuild lifecycle'ını test eden minimal pipeline.

    - _renderers listesi ve callback guard gerçek panel davranışını yansıtır.
    - Renderer'lar _TrackingRenderer; sayaçlar üzerinden doğrulama yapılır.
    """

    def __init__(self, n_renderers: int = 3):
        self._n             = n_renderers
        self._renderers: list = []
        self._all_trackers: list = []   # tüm oluşturulan tracker'lar
        self._builder       = None
        self._topology      = None
        self._twin_ref      = None
        self._profile_ref   = None
        self._lod           = AdaptiveLODManager()
        self._gl            = _MockGL()

    def setup(self, twin, profile) -> None:
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
        self._apply_frame(0)

    def _setup_renderers(self) -> None:
        self._teardown_renderers()
        trackers = [_TrackingRenderer() for _ in range(self._n)]
        self._all_trackers.extend(trackers)
        self._renderers = trackers
        for r in self._renderers:
            r.setup(self._gl, self._topology)
        self._gl._on_scene_rebuild = self._on_gl_scene_rebuild

    def _teardown_renderers(self) -> None:
        for r in self._renderers:
            r.teardown()
        self._renderers.clear()
        self._gl._on_scene_rebuild = None

    def _on_gl_scene_rebuild(self) -> None:
        """S4.6.4 guard: callback referansı hemen sıfırlanır."""
        gl = self._gl
        if gl is not None:
            gl._on_scene_rebuild = None
        for r in self._renderers:
            r.teardown()
        self._renderers.clear()

    def rebuild_topology(self) -> None:
        """LOD değişimi veya sahne yeniden oluşturma simülasyonu."""
        if self._twin_ref is None or self._profile_ref is None:
            return
        lod = self._lod.current
        self._topology = RenderFrameBuilder.build_topology(
            self._profile_ref,
            shell_nz=lod.shell_nz, shell_nth=lod.shell_nth,
            heatmap_nz=lod.heatmap_nz, heatmap_nth=lod.heatmap_nth,
            ribbon_max_seg=lod.ribbon_max_seg,
        )
        dep = getattr(self._twin_ref, 'final_deposition', None)
        self._builder = RenderFrameBuilder(
            self._twin_ref, self._profile_ref, self._topology,
            tow_width_mm=6.0, deposition=dep,
        )
        self._setup_renderers()
        self._apply_frame(0)

    def _apply_frame(self, idx: int) -> None:
        if self._builder is None:
            return
        n = self._builder.n_states
        idx = max(0, min(n - 1, idx))
        frame = self._builder.build(idx)
        for r in self._renderers:
            r.update(frame)

    def fire_scene_rebuild_callback(self) -> None:
        """_MachineGLView._rebuild_scene() çağrısını simüle eder."""
        if callable(self._gl._on_scene_rebuild):
            self._gl._on_scene_rebuild()


# ─────────────────────────────────────────────────────────────────────────────
# RB-01..02 — Rapid rebuild döngüsü
# ─────────────────────────────────────────────────────────────────────────────

class TestRapidRebuild:

    def test_rb01_renderer_count_stable_after_20_rebuilds(self):
        """RB-01: 20 ardışık rebuild sonrası renderer sayısı daima n_renderers."""
        twin, profile = _twin_and_profile()
        pipe = _RebuildPipeline(n_renderers=3)
        pipe.setup(twin, profile)
        for _ in range(20):
            pipe.rebuild_topology()
        assert len(pipe._renderers) == 3, (
            f"Rebuild sonrası renderer sayısı: {len(pipe._renderers)}"
        )

    def test_rb02_no_exception_during_rapid_rebuild(self):
        """RB-02: 50 ardışık rebuild exception üretmez."""
        twin, profile = _twin_and_profile()
        pipe = _RebuildPipeline(n_renderers=4)
        pipe.setup(twin, profile)
        for _ in range(50):
            pipe.rebuild_topology()


# ─────────────────────────────────────────────────────────────────────────────
# RB-03..04 — Callback guard
# ─────────────────────────────────────────────────────────────────────────────

class TestCallbackGuard:

    def test_rb03_callback_none_after_fire(self):
        """RB-03: _on_gl_scene_rebuild() callback sonrası gl._on_scene_rebuild=None."""
        twin, profile = _twin_and_profile()
        pipe = _RebuildPipeline(n_renderers=2)
        pipe.setup(twin, profile)
        assert callable(pipe._gl._on_scene_rebuild), "Callback setup sonrası set değil"
        pipe.fire_scene_rebuild_callback()
        assert pipe._gl._on_scene_rebuild is None, "Callback guard çalışmadı"

    def test_rb04_double_callback_no_double_teardown(self):
        """RB-04: Callback iki kez tetiklenirse teardown sayısı eşleşir.

        Guard sayesinde ikinci çağrı _renderers zaten boş bulur → ek teardown yok.
        """
        twin, profile = _twin_and_profile()
        pipe = _RebuildPipeline(n_renderers=3)
        pipe.setup(twin, profile)
        first_gen = list(pipe._renderers)   # ilk nesil renderer'lar

        # İlk callback
        pipe.fire_scene_rebuild_callback()
        first_teardown = [r.teardown_count for r in first_gen]

        # İkinci callback — guard None yaptı; çağrılsa dahi renderer listesi boş
        if callable(pipe._gl._on_scene_rebuild):
            pipe.fire_scene_rebuild_callback()

        second_teardown = [r.teardown_count for r in first_gen]
        assert first_teardown == second_teardown, (
            f"Çift teardown oluştu: {first_teardown} → {second_teardown}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# RB-05..06 — setup/teardown dengesi
# ─────────────────────────────────────────────────────────────────────────────

class TestSetupTeardownBalance:

    def test_rb05_teardown_equals_setup_after_n_rebuilds(self):
        """RB-05: N rebuild sonrası; önceki nesillerde teardown == setup."""
        twin, profile = _twin_and_profile()
        n_rebuild = 10
        pipe = _RebuildPipeline(n_renderers=3)
        pipe.setup(twin, profile)
        prev_gens = []
        for _ in range(n_rebuild):
            prev_gens.extend(pipe._renderers[:])
            pipe.rebuild_topology()

        for r in prev_gens:
            assert r.teardown_count == r.setup_count, (
                f"Sızdırma: setup={r.setup_count}, teardown={r.teardown_count}"
            )

    def test_rb06_active_renderers_have_one_setup_zero_teardown(self):
        """RB-06: Aktif (son nesil) renderer'lar 1 setup, 0 teardown'a sahip."""
        twin, profile = _twin_and_profile()
        pipe = _RebuildPipeline(n_renderers=3)
        pipe.setup(twin, profile)
        for _ in range(5):
            pipe.rebuild_topology()
        for r in pipe._renderers:
            assert r.setup_count    == 1, f"setup_count={r.setup_count}"
            assert r.teardown_count == 0, f"teardown_count={r.teardown_count}"


# ─────────────────────────────────────────────────────────────────────────────
# RB-07..08 — Rebuild sonrası builder ve apply_frame
# ─────────────────────────────────────────────────────────────────────────────

class TestRebuildBuilderIntegrity:

    def test_rb07_builder_not_none_after_rebuild(self):
        """RB-07: rebuild_topology() sonrası _builder None değil."""
        twin, profile = _twin_and_profile()
        pipe = _RebuildPipeline(n_renderers=2)
        pipe.setup(twin, profile)
        for _ in range(5):
            pipe.rebuild_topology()
        assert pipe._builder is not None, "Rebuild sonrası builder None"

    def test_rb08_apply_frame_after_rebuild_increments_update_count(self):
        """RB-08: rebuild sonrası apply_frame(k) renderer'ların update sayacını artırır."""
        twin, profile = _twin_and_profile()
        pipe = _RebuildPipeline(n_renderers=3)
        pipe.setup(twin, profile)
        pipe.rebuild_topology()
        current_gen = list(pipe._renderers)
        before = [r.update_count for r in current_gen]
        pipe._apply_frame(50)
        after = [r.update_count for r in current_gen]
        assert all(a == b + 1 for a, b in zip(after, before)), (
            f"Beklenen her birinde +1: before={before} after={after}"
        )
