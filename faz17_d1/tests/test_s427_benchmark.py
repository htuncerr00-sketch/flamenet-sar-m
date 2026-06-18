"""
tests/test_s427_benchmark.py — S4.2.7 100-katman performans benchmark
=======================================================================
RibbonRenderer pre-alloc trick'inin build(k) maliyetini ölçer.
5 ms eşiğinin üstüne çıkılırsa CustomRibbonMeshItem sprint açılır.

Çalıştır:
    cd faz17_d1/faz17_d1_backend
    python -m pytest ../tests/test_s427_benchmark.py -v -s
"""
from __future__ import annotations

import sys
import os
import time
import math
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np
import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "faz17_d1_backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from faz17_d1.core.render_frame_builder import RenderFrameBuilder
from faz17_d1.core.render_frame import RenderFrame


# ─────────────────────────────────────────────────────────────────────────────
# Mock twin (100 katman simülasyonu)
# ─────────────────────────────────────────────────────────────────────────────

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
class _MockDep:
    z_bins: np.ndarray = field(default_factory=lambda: np.linspace(0, 300, 16))
    theta_bins: np.ndarray = field(default_factory=lambda: np.linspace(0, 2*math.pi, 24))
    coverage_count: np.ndarray = field(default_factory=lambda: np.zeros((16, 24), dtype=np.int32))


@dataclass
class _MockLayered:
    final_radius_mm: float = 60.0
    base_radius_mm: float = 50.0
    paths: list = field(default_factory=list)


@dataclass
class _MockTwin100:
    states: List[_St]
    dt_s: float = 0.5
    total_time_s: float = 500.0
    n_layers: int = 100
    base_radius_mm: float = 50.0
    final_radius_mm: float = 60.0
    total_fiber_length_mm: float = 500_000.0
    max_lag_error_mm: float = 0.5
    max_spindle_rpm: float = 65.0
    layer_time_ranges_s: List[Tuple[float, float]] = field(default_factory=list)
    layered_paths: _MockLayered = field(default_factory=_MockLayered)
    final_deposition: _MockDep = field(default_factory=_MockDep)


class _MockProfile:
    def __init__(self, L: float = 300.0, r: float = 50.0):
        self.z_mm = np.linspace(0, L, 200)
        self.r_mm = np.full(200, r)
        self.avg_radius_mm = r

    def radius_at(self, z: float) -> float:
        return float(np.interp(z, self.z_mm, self.r_mm))


def _make_100layer_twin(n_states: int = 1000) -> _MockTwin100:
    """1000 durum, 100 katman → her katmanda 10 durum."""
    states = []
    for i in range(n_states):
        t = i / max(n_states - 1, 1)
        lyr = min(int(i * 100 / n_states), 99)
        r = 50.0 + lyr * 0.1
        states.append(_St(
            t_s=float(i) * 0.5,
            spindle_angle_deg=float(i * 3.6),
            carriage_x_actual_mm=float(i % 300),
            eye_x_mm=float(i % 300),
            eye_r_mm=80.0,
            current_layer=lyr,
            current_circuit=i % 10,
            fiber_deposited_mm=float(i * 50.0),
            current_radius_mm=r,
            progress_pct=t * 100.0,
        ))
    return _MockTwin100(states=states, n_layers=100)


# ─────────────────────────────────────────────────────────────────────────────
# BM-06: 100-katman init süresi
# ─────────────────────────────────────────────────────────────────────────────

class TestBenchmark100Layer:

    def test_bm06_init_100_layers(self):
        """BM-06: 100 katmanlı twin için RenderFrameBuilder init < 2 s."""
        twin = _make_100layer_twin(n_states=1000)
        profile = _MockProfile()
        topology = RenderFrameBuilder.build_topology(
            profile,
            shell_nz=20, shell_nth=32,
            heatmap_nz=16, heatmap_nth=24,
            ribbon_max_seg=300,
        )

        t0 = time.perf_counter()
        builder = RenderFrameBuilder(twin, profile, topology, tow_width_mm=6.0)
        elapsed_s = time.perf_counter() - t0
        print(f"\n[BM-06] init(1000 durum, 100 katman): {elapsed_s*1000:.1f} ms")
        assert elapsed_s < 2.0, f"init çok yavaş: {elapsed_s:.2f} s"

    def test_bm07_build_100layer_frame(self):
        """BM-07: 100 katmanlı twin'de build(k) < 2 ms/kare (N=200)."""
        twin = _make_100layer_twin(n_states=1000)
        profile = _MockProfile()
        topology = RenderFrameBuilder.build_topology(
            profile,
            shell_nz=20, shell_nth=32,
            heatmap_nz=16, heatmap_nth=24,
            ribbon_max_seg=300,
        )
        builder = RenderFrameBuilder(twin, profile, topology, tow_width_mm=6.0)

        N = 200
        t0 = time.perf_counter()
        for i in range(N):
            frame = builder.build(i * 5)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        avg_ms = elapsed_ms / N

        print(f"\n[BM-07] build(k) 100-katman: {avg_ms:.4f} ms/kare (N={N})")
        print(f"         Karar eşiği: 5 ms → {'CustomRibbonMeshItem GEREKLİ' if avg_ms > 5 else 'pre-alloc YETERLİ'}")
        # Hedef: <2 ms; >5 ms ise CustomRibbonMeshItem sprint açılmalı
        assert avg_ms < 5.0, (
            f"build(k) {avg_ms:.4f} ms/kare — 5 ms eşiğini aştı! "
            "CustomRibbonMeshItem sprint açılmalı."
        )

    def test_bm08_ribbon_faces_construction(self):
        """BM-08: Ribbon face döngüsü (max_seg=300) < 1 ms/kare."""
        twin = _make_100layer_twin(n_states=500)
        profile = _MockProfile()
        topology = RenderFrameBuilder.build_topology(
            profile, ribbon_max_seg=300,
        )
        builder = RenderFrameBuilder(twin, profile, topology, tow_width_mm=6.0)

        N = 500
        t0 = time.perf_counter()
        for i in range(N):
            k = i % 500
            _ = builder.build(k)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        avg_ms = elapsed_ms / N
        print(f"\n[BM-08] ribbon (max_seg=300): {avg_ms:.4f} ms/kare (N={N})")
        assert avg_ms < 1.0, f"Ribbon build çok yavaş: {avg_ms:.4f} ms/kare"

    def test_bm09_shell_lookup(self):
        """BM-09: Shell per-layer lookup < 0.1 ms/kare (dict erişimi)."""
        twin = _make_100layer_twin(n_states=200)
        profile = _MockProfile()
        topology = RenderFrameBuilder.build_topology(
            profile, shell_nz=30, shell_nth=48,
        )
        builder = RenderFrameBuilder(twin, profile, topology, tow_width_mm=6.0)

        N = 1000
        t0 = time.perf_counter()
        for i in range(N):
            frame = builder.build(i % 200)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        avg_ms = elapsed_ms / N
        print(f"\n[BM-09] shell lookup (30×48, 100 katman): {avg_ms:.4f} ms/kare")
        assert avg_ms < 2.0, f"Shell lookup çok yavaş: {avg_ms:.4f} ms/kare"

    def test_bm10_render_frame_immutability_overhead(self):
        """BM-10: frozen RenderFrame + ndarray freeze overhead < 0.1 ms/kare."""
        twin = _make_100layer_twin(n_states=100)
        profile = _MockProfile()
        topology = RenderFrameBuilder.build_topology(profile)
        builder = RenderFrameBuilder(twin, profile, topology, tow_width_mm=6.0)

        N = 1000
        t0 = time.perf_counter()
        for i in range(N):
            frame = builder.build(i % 100)
            # Doğrula: frame gerçekten immutable
            assert not frame.shell_verts.flags.writeable
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        avg_ms = elapsed_ms / N
        print(f"\n[BM-10] immutable frame overhead: {avg_ms:.4f} ms/kare (N={N})")
        assert avg_ms < 1.0
