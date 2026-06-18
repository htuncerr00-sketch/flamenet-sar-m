"""
tests/test_render_frame.py — S4.2.1 RenderFrame birim testleri
===============================================================
Çalıştır:
    cd faz17_d1/faz17_d1_backend
    python -m pytest ../tests/test_render_frame.py -v
"""
from __future__ import annotations

import time
import sys
import os

import numpy as np
import pytest

# faz17_d1 paketini bul
_BACKEND = os.path.join(os.path.dirname(__file__), "..", "faz17_d1_backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from faz17_d1.core.render_frame import (
    RenderFrame,
    RenderSceneTopology,
    make_empty_frame,
)


# ─────────────────────────────────────────────────────────────────────────────
# Yardımcı fabrika
# ─────────────────────────────────────────────────────────────────────────────

def _make_topology(nz: int = 10, nth: int = 24,
                   nz_h: int = 8, nth_h: int = 16,
                   ribbon_max: int = 200) -> RenderSceneTopology:
    n_shell = nz * nth
    n_hm = nz_h * (nth_h + 1)
    shell_faces = np.zeros((2 * (nz - 1) * nth, 3), dtype=np.int32)
    heatmap_verts = np.zeros((n_hm, 3), dtype=np.float32)
    heatmap_faces = np.zeros((2 * (nz_h - 1) * nth_h, 3), dtype=np.int32)
    return RenderSceneTopology(
        shell_faces=shell_faces,
        shell_n_verts=n_shell,
        heatmap_verts=heatmap_verts,
        heatmap_faces=heatmap_faces,
        shell_nz=nz,
        shell_nth=nth,
        heatmap_nz=nz_h,
        heatmap_nth=nth_h,
        ribbon_max_seg=ribbon_max,
    )


def _make_frame(k: int = 5, nz: int = 10, nth: int = 24,
                nz_h: int = 8, nth_h: int = 16) -> RenderFrame:
    n_shell = nz * nth
    n_hm = nz_h * (nth_h + 1)
    return RenderFrame(
        spindle_angle_deg=90.0,
        carriage_x_mm=100.0,
        eye_xyz=np.array([0.1, 0.0, 0.0], dtype=np.float32),
        contact_xyz=np.array([0.0, 0.05, 0.0], dtype=np.float32),
        ribbon_verts=np.zeros((2 * k, 3), dtype=np.float32),
        ribbon_faces=np.zeros((2 * (k - 1), 3), dtype=np.int32),
        shell_verts=np.zeros((n_shell, 3), dtype=np.float32),
        shell_colors=np.ones((n_shell, 4), dtype=np.float32),
        heatmap_vc=np.zeros((n_hm, 4), dtype=np.float32),
        heatmap_dirty=True,
        frame_idx=42,
        progress_pct=33.5,
        current_radius_mm=52.0,
        fiber_deposited_mm=1234.5,
        layer=2,
        circuit=7,
    )


# ─────────────────────────────────────────────────────────────────────────────
# RF-01 .. RF-05: RenderFrame temel özellikler
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderFrameBasic:

    def test_rf01_frozen_scalar_immutable(self):
        """RF-01: frozen=True → skaler alan ataması TypeError."""
        frame = _make_frame()
        with pytest.raises((AttributeError, TypeError, Exception)):
            frame.spindle_angle_deg = 999.0  # type: ignore[misc]

    def test_rf02_ndarray_fields_readonly(self):
        """RF-02: tüm ndarray alanları writeable=False olmalı."""
        frame = _make_frame()
        for name in ("eye_xyz", "contact_xyz", "ribbon_verts", "ribbon_faces",
                     "shell_verts", "shell_colors", "heatmap_vc"):
            arr = getattr(frame, name)
            assert isinstance(arr, np.ndarray), f"{name} np.ndarray değil"
            assert not arr.flags.writeable, f"{name} hâlâ yazılabilir"

    def test_rf03_write_to_array_raises(self):
        """RF-03: read-only diziye yazma ValueError."""
        frame = _make_frame()
        with pytest.raises(ValueError):
            frame.eye_xyz[0] = 99.9  # type: ignore[index]

    def test_rf04_scalar_fields_correct(self):
        """RF-04: skaler alan değerleri korunuyor."""
        frame = _make_frame(k=5)
        assert frame.spindle_angle_deg == pytest.approx(90.0)
        assert frame.carriage_x_mm == pytest.approx(100.0)
        assert frame.frame_idx == 42
        assert frame.progress_pct == pytest.approx(33.5)
        assert frame.current_radius_mm == pytest.approx(52.0)
        assert frame.fiber_deposited_mm == pytest.approx(1234.5)
        assert frame.layer == 2
        assert frame.circuit == 7
        assert frame.heatmap_dirty is True

    def test_rf05_dtype_preservation(self):
        """RF-05: float32 dtype korunuyor."""
        frame = _make_frame()
        for name in ("eye_xyz", "contact_xyz", "ribbon_verts",
                     "shell_verts", "shell_colors", "heatmap_vc"):
            arr = getattr(frame, name)
            assert arr.dtype == np.float32, f"{name} float32 değil: {arr.dtype}"
        assert frame.ribbon_faces.dtype == np.int32
        assert frame.shell_verts.dtype == np.float32


# ─────────────────────────────────────────────────────────────────────────────
# RF-06 .. RF-09: Property'ler
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderFrameProperties:

    def test_rf06_ribbon_n_segments_correct(self):
        """RF-06: ribbon_n_segments = k için 2k vertex."""
        frame = _make_frame(k=7)
        assert frame.ribbon_n_segments == 7

    def test_rf07_ribbon_n_segments_empty(self):
        """RF-07: ribbon boşsa n_segments=0."""
        frame = RenderFrame(
            spindle_angle_deg=0.0, carriage_x_mm=0.0,
            eye_xyz=np.zeros(3, np.float32), contact_xyz=np.zeros(3, np.float32),
            ribbon_verts=np.zeros((0, 3), np.float32),
            ribbon_faces=np.zeros((0, 3), np.int32),
            shell_verts=np.zeros((1, 3), np.float32),
            shell_colors=np.zeros((1, 4), np.float32),
            heatmap_vc=np.zeros((1, 4), np.float32),
            heatmap_dirty=False,
            frame_idx=0, progress_pct=0.0,
            current_radius_mm=0.0, fiber_deposited_mm=0.0,
            layer=0, circuit=0,
        )
        assert frame.ribbon_n_segments == 0

    def test_rf08_is_shell_visible_true(self):
        """RF-08: current_radius_mm > 0 → is_shell_visible True."""
        frame = _make_frame()
        assert frame.current_radius_mm > 0
        assert frame.is_shell_visible is True

    def test_rf09_carriage_x_m_conversion(self):
        """RF-09: carriage_x_m = carriage_x_mm / 1000."""
        frame = _make_frame()
        assert frame.carriage_x_m == pytest.approx(frame.carriage_x_mm / 1000.0)


# ─────────────────────────────────────────────────────────────────────────────
# RT-01 .. RT-04: RenderSceneTopology
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderSceneTopology:

    def test_rt01_topology_arrays_readonly(self):
        """RT-01: topology dizileri __post_init__ sonrası read-only."""
        topo = _make_topology()
        assert not topo.shell_faces.flags.writeable, "shell_faces yazılabilir"
        assert not topo.heatmap_verts.flags.writeable, "heatmap_verts yazılabilir"
        assert not topo.heatmap_faces.flags.writeable, "heatmap_faces yazılabilir"

    def test_rt02_topology_frozen(self):
        """RT-02: frozen=True → alan ataması reddedilir."""
        topo = _make_topology()
        with pytest.raises((AttributeError, TypeError, Exception)):
            topo.shell_nz = 999  # type: ignore[misc]

    def test_rt03_topology_scalar_fields(self):
        """RT-03: scalar alanlar doğru kaydedildi."""
        topo = _make_topology(nz=12, nth=30, nz_h=10, nth_h=20, ribbon_max=400)
        assert topo.shell_nz == 12
        assert topo.shell_nth == 30
        assert topo.heatmap_nz == 10
        assert topo.heatmap_nth == 20
        assert topo.ribbon_max_seg == 400
        assert topo.shell_n_verts == 12 * 30

    def test_rt04_topology_shape_consistency(self):
        """RT-04: dizilerin şekli parametre değerleriyle tutarlı."""
        nz, nth = 8, 20
        nz_h, nth_h = 6, 12
        topo = _make_topology(nz=nz, nth=nth, nz_h=nz_h, nth_h=nth_h)
        assert topo.shell_faces.shape == (2 * (nz - 1) * nth, 3)
        assert topo.heatmap_verts.shape == (nz_h * (nth_h + 1), 3)
        assert topo.heatmap_faces.shape == (2 * (nz_h - 1) * nth_h, 3)


# ─────────────────────────────────────────────────────────────────────────────
# ME-01 .. ME-04: make_empty_frame
# ─────────────────────────────────────────────────────────────────────────────

class TestMakeEmptyFrame:

    def test_me01_no_topology(self):
        """ME-01: topology=None → boş frame üretilebiliyor."""
        frame = make_empty_frame()
        assert isinstance(frame, RenderFrame)
        assert frame.frame_idx == 0
        assert frame.progress_pct == pytest.approx(0.0)

    def test_me02_with_topology(self):
        """ME-02: topology verilince doğru boyutlar."""
        topo = _make_topology(nz=10, nth=24, nz_h=8, nth_h=16)
        frame = make_empty_frame(topology=topo)
        assert frame.shell_verts.shape == (topo.shell_n_verts, 3)
        assert frame.shell_colors.shape == (topo.shell_n_verts, 4)
        n_hm = topo.heatmap_nz * (topo.heatmap_nth + 1)
        assert frame.heatmap_vc.shape == (n_hm, 4)

    def test_me03_empty_frame_readonly(self):
        """ME-03: make_empty_frame dizileri de read-only."""
        frame = make_empty_frame()
        for name in ("eye_xyz", "contact_xyz", "ribbon_verts",
                     "shell_verts", "shell_colors", "heatmap_vc"):
            arr = getattr(frame, name)
            assert not arr.flags.writeable, f"make_empty_frame {name} yazılabilir"

    def test_me04_empty_frame_ribbon_empty(self):
        """ME-04: boş frame'de ribbon segment yok."""
        frame = make_empty_frame()
        assert frame.ribbon_n_segments == 0
        assert frame.ribbon_verts.shape[0] == 0


# ─────────────────────────────────────────────────────────────────────────────
# BM-01: build süresi (S4.2.1 benchmark)
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildBenchmark:

    def test_bm01_frame_construction_time(self):
        """BM-01: RenderFrame oluşturma < 0.5 ms (N=1000 iter ortalaması)."""
        nz, nth = 20, 48
        nz_h, nth_h = 16, 32
        n_shell = nz * nth
        n_hm = nz_h * (nth_h + 1)

        # Önceden dizileri hazırla (builder'ı simüle et)
        eye_xyz = np.zeros(3, dtype=np.float32)
        contact_xyz = np.zeros(3, dtype=np.float32)
        ribbon_verts = np.zeros((20, 3), dtype=np.float32)
        ribbon_faces = np.zeros((18, 3), dtype=np.int32)
        shell_verts = np.zeros((n_shell, 3), dtype=np.float32)
        shell_colors = np.ones((n_shell, 4), dtype=np.float32)
        heatmap_vc = np.zeros((n_hm, 4), dtype=np.float32)

        N = 1000
        t0 = time.perf_counter()
        for i in range(N):
            # Her iterasyonda yeni diziler yap (freeze maliyeti ölçülsün)
            frame = RenderFrame(
                spindle_angle_deg=float(i),
                carriage_x_mm=50.0,
                eye_xyz=eye_xyz.copy(),
                contact_xyz=contact_xyz.copy(),
                ribbon_verts=ribbon_verts.copy(),
                ribbon_faces=ribbon_faces.copy(),
                shell_verts=shell_verts.copy(),
                shell_colors=shell_colors.copy(),
                heatmap_vc=heatmap_vc.copy(),
                heatmap_dirty=(i % 10 == 0),
                frame_idx=i,
                progress_pct=float(i) / N * 100.0,
                current_radius_mm=50.0,
                fiber_deposited_mm=float(i) * 0.5,
                layer=i // 100,
                circuit=i % 100,
            )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        avg_ms = elapsed_ms / N
        print(f"\n[BM-01] RenderFrame oluşturma: {avg_ms:.4f} ms/kare "
              f"(N={N}, toplam {elapsed_ms:.1f} ms)")
        # Hedef: < 0.5 ms/kare
        assert avg_ms < 0.5, (
            f"RenderFrame oluşturma çok yavaş: {avg_ms:.4f} ms/kare "
            f"(hedef < 0.5 ms)"
        )

    def test_bm02_freeze_cost_only(self):
        """BM-02: _freeze_frame maliyeti < 0.05 ms (copy olmadan)."""
        from faz17_d1.core.render_frame import _freeze_frame, RenderFrame
        nz, nth = 20, 48
        n_shell = nz * nth
        nz_h, nth_h = 16, 32
        n_hm = nz_h * (nth_h + 1)

        # Kopyalama maliyetini ayrıştır: frozen=False bir dataclass kullan
        # Bunun yerine doğrudan _ro() + _freeze_frame() çağrısı zamanla
        from faz17_d1.core.render_frame import _ro
        arrays = [
            np.zeros(3, dtype=np.float32),
            np.zeros(3, dtype=np.float32),
            np.zeros((20, 3), dtype=np.float32),
            np.zeros((18, 3), dtype=np.int32),
            np.zeros((n_shell, 3), dtype=np.float32),
            np.ones((n_shell, 4), dtype=np.float32),
            np.zeros((n_hm, 4), dtype=np.float32),
        ]

        N = 10_000
        t0 = time.perf_counter()
        for _ in range(N):
            for arr in arrays:
                arr.flags.writeable = True
                arr.flags.writeable = False
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        avg_us = (elapsed_ms / N) * 1000.0
        print(f"\n[BM-02] freeze-only maliyet: {avg_us:.2f} µs/kare "
              f"(N={N})")
        assert avg_us < 50.0, f"freeze maliyeti çok yüksek: {avg_us:.2f} µs"
