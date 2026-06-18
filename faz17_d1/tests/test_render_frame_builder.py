"""
tests/test_render_frame_builder.py — S4.2.3 RenderFrameBuilder birim testleri
==============================================================================
Gerçek TwinSimulationResult üretimi pahalı olduğu için basit mock twin
kullanılır (fizik çağrısı olmadan).

Çalıştır:
    cd faz17_d1/faz17_d1_backend
    python -m pytest ../tests/test_render_frame_builder.py -v
"""
from __future__ import annotations

import time
import sys
import os
import math
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np
import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "faz17_d1_backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from faz17_d1.core.render_frame import RenderFrame, RenderSceneTopology
from faz17_d1.core.render_frame_builder import (
    RenderFrameBuilder,
    HEATMAP_PERIOD,
    _cyl_shell_verts,
    _shell_faces,
    _heatmap_topology,
)


# ─────────────────────────────────────────────────────────────────────────────
# Mock nesneler (fizik çağrısı yok)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _MockState:
    t_s: float = 0.0
    spindle_angle_deg: float = 0.0
    spindle_rpm: float = 60.0
    carriage_x_mm: float = 0.0
    carriage_x_actual_mm: float = 0.0
    carriage_v_mm_s: float = 0.0
    eye_x_mm: float = 0.0
    eye_r_mm: float = 0.1
    contact_z_mm: float = 0.0
    contact_r_mm: float = 50.0
    current_layer: int = 0
    current_circuit: int = 0
    fiber_deposited_mm: float = 0.0
    current_radius_mm: float = 50.0
    lag_error_mm: float = 0.0
    progress_pct: float = 0.0


@dataclass
class _MockLayeredPaths:
    final_radius_mm: float = 52.0
    base_radius_mm: float = 50.0
    paths: list = field(default_factory=list)


@dataclass
class _MockDeposition:
    z_bins: np.ndarray = field(default_factory=lambda: np.linspace(0, 300, 8))
    theta_bins: np.ndarray = field(default_factory=lambda: np.linspace(0, 2*math.pi, 12))
    coverage_count: np.ndarray = field(default_factory=lambda: np.zeros((8, 12), dtype=np.int32))


@dataclass
class _MockTwin:
    states: List[_MockState]
    dt_s: float = 1.0
    total_time_s: float = 10.0
    n_layers: int = 2
    base_radius_mm: float = 50.0
    final_radius_mm: float = 52.0
    total_fiber_length_mm: float = 1000.0
    max_lag_error_mm: float = 0.5
    max_spindle_rpm: float = 65.0
    layer_time_ranges_s: List[Tuple[float, float]] = field(default_factory=list)
    layered_paths: _MockLayeredPaths = field(default_factory=_MockLayeredPaths)
    final_deposition: _MockDeposition = field(default_factory=_MockDeposition)


class _MockProfile:
    def __init__(self, L_mm: float = 300.0, r_mm: float = 50.0):
        self.z_mm = np.linspace(0, L_mm, 100)
        self.r_mm = np.full(100, r_mm)
        self.avg_radius_mm = r_mm

    def radius_at(self, z: float) -> float:
        return float(np.interp(z, self.z_mm, self.r_mm))


def _make_states(n: int = 20) -> List[_MockState]:
    states = []
    for i in range(n):
        t = i / max(n - 1, 1)
        states.append(_MockState(
            t_s=float(i),
            spindle_angle_deg=float(i * 18.0),
            carriage_x_actual_mm=float(i * 300.0 / max(n - 1, 1)),
            eye_x_mm=float(i * 300.0 / max(n - 1, 1)),
            eye_r_mm=80.0,
            current_layer=int(i >= n // 2),
            current_circuit=i % 5,
            fiber_deposited_mm=float(i * 10.0),
            current_radius_mm=50.0 + (i / max(n - 1, 1)) * 2.0,
            progress_pct=t * 100.0,
        ))
    return states


def _make_topology(
    shell_nz: int = 10, shell_nth: int = 16,
    heatmap_nz: int = 8, heatmap_nth: int = 12,
    ribbon_max: int = 50,
) -> RenderSceneTopology:
    return RenderFrameBuilder.build_topology(
        _MockProfile(),
        shell_nz=shell_nz, shell_nth=shell_nth,
        heatmap_nz=heatmap_nz, heatmap_nth=heatmap_nth,
        ribbon_max_seg=ribbon_max,
    )


def _make_builder(n: int = 20, ribbon_max: int = 50) -> RenderFrameBuilder:
    states = _make_states(n)
    twin = _MockTwin(states=states)
    profile = _MockProfile()
    topology = _make_topology(ribbon_max=ribbon_max)
    return RenderFrameBuilder(twin, profile, topology, tow_width_mm=6.0)


# ─────────────────────────────────────────────────────────────────────────────
# BLD-01 .. BLD-05: build_topology
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildTopology:

    def test_bld01_topology_is_render_scene_topology(self):
        """BLD-01: build_topology → RenderSceneTopology döner."""
        topo = _make_topology()
        assert isinstance(topo, RenderSceneTopology)

    def test_bld02_topology_shell_faces_shape(self):
        """BLD-02: shell_faces şekli doğru."""
        nz, nth = 10, 16
        topo = _make_topology(shell_nz=nz, shell_nth=nth)
        assert topo.shell_faces.shape == (2 * (nz - 1) * nth, 3)

    def test_bld03_topology_heatmap_shapes(self):
        """BLD-03: heatmap_verts ve heatmap_faces şekilleri doğru."""
        nz_h, nth_h = 8, 12
        topo = _make_topology(heatmap_nz=nz_h, heatmap_nth=nth_h)
        assert topo.heatmap_verts.shape == (nz_h * (nth_h + 1), 3)
        assert topo.heatmap_faces.shape == (2 * (nz_h - 1) * nth_h, 3)

    def test_bld04_topology_arrays_readonly(self):
        """BLD-04: topology dizileri read-only."""
        topo = _make_topology()
        assert not topo.shell_faces.flags.writeable
        assert not topo.heatmap_verts.flags.writeable
        assert not topo.heatmap_faces.flags.writeable

    def test_bld05_topology_n_verts_consistent(self):
        """BLD-05: shell_n_verts = shell_nz * shell_nth."""
        nz, nth = 12, 20
        topo = _make_topology(shell_nz=nz, shell_nth=nth)
        assert topo.shell_n_verts == nz * nth


# ─────────────────────────────────────────────────────────────────────────────
# BLD-06 .. BLD-10: build(k) temel özellikler
# ─────────────────────────────────────────────────────────────────────────────

class TestBuilderBuild:

    def test_bld06_returns_render_frame(self):
        """BLD-06: build(k) → RenderFrame döner."""
        builder = _make_builder()
        frame = builder.build(5)
        assert isinstance(frame, RenderFrame)

    def test_bld07_frame_immutable(self):
        """BLD-07: Üretilen RenderFrame frozen ve ndarray'leri read-only."""
        builder = _make_builder()
        frame = builder.build(5)
        with pytest.raises((AttributeError, TypeError, Exception)):
            frame.spindle_angle_deg = 999.0  # type: ignore[misc]
        assert not frame.eye_xyz.flags.writeable
        assert not frame.shell_verts.flags.writeable

    def test_bld08_frame_idx_matches_k(self):
        """BLD-08: frame.frame_idx = k."""
        builder = _make_builder(n=20)
        for k in [0, 5, 10, 19]:
            frame = builder.build(k)
            assert frame.frame_idx == k

    def test_bld09_spindle_angle_from_twin(self):
        """BLD-09: spindle_angle_deg TwinState'ten alındı."""
        states = _make_states(10)
        twin = _MockTwin(states=states)
        builder = RenderFrameBuilder(twin, _MockProfile(), _make_topology(), tow_width_mm=6.0)
        for k in range(10):
            frame = builder.build(k)
            assert frame.spindle_angle_deg == pytest.approx(states[k].spindle_angle_deg)

    def test_bld10_clamp_k_out_of_range(self):
        """BLD-10: k<0 veya k>=n_states sınırlanır, hata yok."""
        builder = _make_builder(n=10)
        frame_neg = builder.build(-5)
        assert frame_neg.frame_idx == 0
        frame_over = builder.build(100)
        assert frame_over.frame_idx == 9


# ─────────────────────────────────────────────────────────────────────────────
# BLD-11 .. BLD-14: Koordinat dönüşümü
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinateTransform:

    def test_bld11_contact_xyz_unit_metres(self):
        """BLD-11: contact_xyz birimi METRE (carriage_mm / 1000)."""
        states = [_MockState(carriage_x_actual_mm=100.0, current_radius_mm=50.0,
                             spindle_angle_deg=0.0)]
        twin = _MockTwin(states=states, n_layers=1)
        builder = RenderFrameBuilder(twin, _MockProfile(), _make_topology(), tow_width_mm=6.0)
        frame = builder.build(0)
        # panel-X = (carriage_mm - z0_mm) / 1000.0  (z0=0 → 100/1000=0.1)
        assert frame.contact_xyz[0] == pytest.approx(0.1, abs=1e-4)

    def test_bld12_eye_xyz_radial_correct(self):
        """BLD-12: eye_xyz radyal bileşen = eye_r_mm / 1000 (açı=0 → Y ekseni)."""
        states = [_MockState(eye_r_mm=80.0, spindle_angle_deg=0.0, eye_x_mm=0.0)]
        twin = _MockTwin(states=states, n_layers=1)
        builder = RenderFrameBuilder(twin, _MockProfile(), _make_topology(), tow_width_mm=6.0)
        frame = builder.build(0)
        # açı=0 → cos=1, sin=0 → eye_Y = 80/1000 = 0.08 m
        assert frame.eye_xyz[1] == pytest.approx(0.08, abs=1e-4)
        assert frame.eye_xyz[2] == pytest.approx(0.0,  abs=1e-4)

    def test_bld13_carriage_x_m_property(self):
        """BLD-13: frame.carriage_x_m = carriage_x_mm / 1000."""
        states = [_MockState(carriage_x_actual_mm=150.0)]
        twin = _MockTwin(states=states, n_layers=1)
        builder = RenderFrameBuilder(twin, _MockProfile(), _make_topology(), tow_width_mm=6.0)
        frame = builder.build(0)
        assert frame.carriage_x_m == pytest.approx(0.15, abs=1e-4)

    def test_bld14_progress_pct_from_twin(self):
        """BLD-14: progress_pct TwinState'ten doğrudan alındı."""
        states = _make_states(10)
        twin = _MockTwin(states=states)
        builder = RenderFrameBuilder(twin, _MockProfile(), _make_topology(), tow_width_mm=6.0)
        for k in range(10):
            frame = builder.build(k)
            assert frame.progress_pct == pytest.approx(states[k].progress_pct, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# BLD-15 .. BLD-18: Ribbon
# ─────────────────────────────────────────────────────────────────────────────

class TestRibbonBuild:

    def test_bld15_ribbon_grows_with_k(self):
        """BLD-15: Ribbon segment sayısı k ile artar."""
        builder = _make_builder(n=20, ribbon_max=50)
        prev_segs = 0
        for k in [0, 5, 10, 19]:
            frame = builder.build(k)
            segs = frame.ribbon_n_segments
            assert segs >= prev_segs, f"k={k}: segment sayısı düştü ({prev_segs}→{segs})"
            prev_segs = segs

    def test_bld16_ribbon_capped_at_max_seg(self):
        """BLD-16: Ribbon max_seg'i geçmez."""
        max_seg = 5
        builder = _make_builder(n=20, ribbon_max=max_seg)
        frame = builder.build(19)
        assert frame.ribbon_n_segments <= max_seg

    def test_bld17_ribbon_verts_dtype_float32(self):
        """BLD-17: ribbon_verts dtype float32."""
        builder = _make_builder(n=10)
        frame = builder.build(5)
        if frame.ribbon_verts.shape[0] > 0:
            assert frame.ribbon_verts.dtype == np.float32

    def test_bld18_ribbon_faces_dtype_int32(self):
        """BLD-18: ribbon_faces dtype int32."""
        builder = _make_builder(n=10)
        frame = builder.build(5)
        assert frame.ribbon_faces.dtype == np.int32


# ─────────────────────────────────────────────────────────────────────────────
# BLD-19 .. BLD-22: Shell
# ─────────────────────────────────────────────────────────────────────────────

class TestShellBuild:

    def test_bld19_shell_verts_shape(self):
        """BLD-19: shell_verts şekli = (shell_nz*shell_nth, 3)."""
        topo = _make_topology(shell_nz=10, shell_nth=16)
        states = _make_states(5)
        twin = _MockTwin(states=states)
        builder = RenderFrameBuilder(twin, _MockProfile(), topo, tow_width_mm=6.0)
        frame = builder.build(2)
        assert frame.shell_verts.shape == (10 * 16, 3)

    def test_bld20_shell_colors_shape(self):
        """BLD-20: shell_colors şekli = (shell_nz*shell_nth, 4)."""
        topo = _make_topology(shell_nz=10, shell_nth=16)
        states = _make_states(5)
        twin = _MockTwin(states=states)
        builder = RenderFrameBuilder(twin, _MockProfile(), topo, tow_width_mm=6.0)
        frame = builder.build(2)
        assert frame.shell_colors.shape == (10 * 16, 4)

    def test_bld21_shell_verts_dtype(self):
        """BLD-21: shell_verts dtype float32."""
        builder = _make_builder()
        frame = builder.build(3)
        assert frame.shell_verts.dtype == np.float32

    def test_bld22_shell_colors_alpha_positive(self):
        """BLD-22: shell_colors alpha > 0 (görünür)."""
        builder = _make_builder()
        frame = builder.build(3)
        assert float(frame.shell_colors[:, 3].min()) > 0.0


# ─────────────────────────────────────────────────────────────────────────────
# BLD-23 .. BLD-25: Heatmap dirty flag
# ─────────────────────────────────────────────────────────────────────────────

class TestHeatmapDirty:

    def test_bld23_dirty_at_period_multiples(self):
        """BLD-23: heatmap_dirty=True yalnız HEATMAP_PERIOD katlarında."""
        builder = _make_builder(n=50)
        for k in range(50):
            frame = builder.build(k)
            expected = (k % HEATMAP_PERIOD == 0)
            assert frame.heatmap_dirty == expected, (
                f"k={k}: heatmap_dirty={frame.heatmap_dirty}, beklenen={expected}")

    def test_bld24_heatmap_vc_shape(self):
        """BLD-24: heatmap_vc şekli = (nz_h*(nth_h+1), 4)."""
        topo = _make_topology(heatmap_nz=8, heatmap_nth=12)
        states = _make_states(5)
        twin = _MockTwin(states=states)
        builder = RenderFrameBuilder(twin, _MockProfile(), topo, tow_width_mm=6.0)
        frame = builder.build(0)
        assert frame.heatmap_vc.shape == (8 * (12 + 1), 4)

    def test_bld25_heatmap_vc_dtype(self):
        """BLD-25: heatmap_vc dtype float32."""
        builder = _make_builder()
        frame = builder.build(0)
        assert frame.heatmap_vc.dtype == np.float32


# ─────────────────────────────────────────────────────────────────────────────
# BM-04 .. BM-05: build(k) performans
# ─────────────────────────────────────────────────────────────────────────────

class TestBuilderBenchmark:

    def test_bm04_init_100_states(self):
        """BM-04: RenderFrameBuilder init (100 durum) < 500 ms."""
        states = _make_states(100)
        twin = _MockTwin(states=states)
        profile = _MockProfile()
        topology = _make_topology()

        t0 = time.perf_counter()
        builder = RenderFrameBuilder(twin, profile, topology, tow_width_mm=6.0)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        print(f"\n[BM-04] init (100 durum): {elapsed_ms:.1f} ms")
        assert elapsed_ms < 500.0, f"init çok yavaş: {elapsed_ms:.1f} ms"

    def test_bm05_build_single_frame(self):
        """BM-05: build(k) < 1 ms/kare (N=500 iterasyon)."""
        builder = _make_builder(n=100)

        N = 500
        t0 = time.perf_counter()
        for i in range(N):
            _ = builder.build(i % 100)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        avg_ms = elapsed_ms / N
        print(f"\n[BM-05] build(k): {avg_ms:.4f} ms/kare (N={N})")
        assert avg_ms < 1.0, f"build(k) çok yavaş: {avg_ms:.4f} ms/kare"
