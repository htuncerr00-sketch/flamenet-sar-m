"""
test_s6154_deposition_layers.py — S6.15.4 Deposition katman kalınlığı + ayrımı
================================================================================

Teşhis: dep mesh ribbon ile AYNI vertexleri kullanıyordu (eşdüzlemli → z-fight);
katmanlar neredeyse aynı yarıçapta (2 katmanda Δr=0.425mm) → görsel ayrım yok.

Çözüm: _radial_offset_by_layer() her katmanı (layer+1)·DEP_LAYER_GAP_M kadar radyal
dışa terasler → katmanlar (yeşil/sarı/turuncu/kırmızı) ayrışır + kalınlık birikimi.

DP-01  DEP_LAYER_GAP_M tanımlı, pozitif, makul (0 < gap < 5mm).
DP-02  _radial_offset_by_layer metodu var.
DP-03  Layer L kenarı (L+1)·gap kadar radyal ötelenir (X değişmez).
DP-04  Çok katmanlı builder'da dep_verts yarıçapları katmana göre ayrışır
       (layer1 ortalama yarıçapı > layer0 + ~gap).
DP-05  dep_colors katmana göre farklı (layer0 yeşil, layer1 sarı).
DP-06  Layer 0 dep yüzeyi kabuk taban yarıçapının ÜSTÜNDE (z-fight fix).
DP-07  dep_verts şekli hâlâ (2N, 3) — RenderFrame sözleşmesi korunur.
"""
import sys
import pathlib
import importlib
from dataclasses import dataclass

import numpy as np

_ROOT = pathlib.Path(__file__).parents[2]
_BACKEND = str(_ROOT / "faz17_d1/faz17_d1_backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _builder_module():
    for k in list(sys.modules.keys()):
        if "render_frame_builder" in k:
            del sys.modules[k]
    return importlib.import_module("faz17_d1.core.render_frame_builder")


def _make_builder(n_layers=2, N=40, radius=50.0):
    mod = _builder_module()
    RenderFrameBuilder = mod.RenderFrameBuilder

    @dataclass
    class _S:
        spindle_angle_deg: float = 0.0
        carriage_x_actual_mm: float = 0.0
        eye_x_mm: float = 0.0
        eye_r_mm: float = 80.0
        current_layer: int = 0
        current_circuit: int = 0
        fiber_deposited_mm: float = 0.0
        current_radius_mm: float = 50.0
        progress_pct: float = 0.0

    @dataclass
    class _T:
        states: list
        n_layers: int = 2
        base_radius_mm: float = 50.0
        final_radius_mm: float = 52.0
        final_deposition: object = None

    class _P:
        z_mm = np.linspace(0, 200, 20)
        r_mm = np.full(20, radius)
        def radius_at(self, z): return radius

    states = [_S(
        spindle_angle_deg=float(i * 9),
        carriage_x_actual_mm=float(i * 200 / max(N - 1, 1)),
        eye_x_mm=float(i * 200 / max(N - 1, 1)),
        current_layer=int(i * n_layers // N),
        current_radius_mm=radius,
        progress_pct=i * 100.0 / max(N - 1, 1),
    ) for i in range(N)]
    twin = _T(states=states, n_layers=n_layers, base_radius_mm=radius,
              final_radius_mm=radius + 2.0)
    profile = _P()
    topo = RenderFrameBuilder.build_topology(profile, shell_nz=8, shell_nth=12,
                                             heatmap_nz=6, heatmap_nth=8,
                                             ribbon_max_seg=60)
    builder = RenderFrameBuilder(twin, profile, topo, tow_width_mm=6.0)
    return builder, mod


class TestDepositionLayers:

    def test_DP01_gap_constant(self):
        _, mod = _make_builder()
        gap = getattr(mod, "DEP_LAYER_GAP_M", None)
        assert gap is not None, "DEP_LAYER_GAP_M yok."
        assert 0.0 < gap < 0.005, f"DEP_LAYER_GAP_M={gap} makul değil."

    def test_DP02_method_exists(self):
        b, _ = _make_builder()
        assert hasattr(b, "_radial_offset_by_layer"), "_radial_offset_by_layer yok."

    def test_DP03_offset_radial_by_layer(self):
        b, mod = _make_builder()
        gap = mod.DEP_LAYER_GAP_M
        # layer 2 olan sahte kenar (radius 50mm)
        b._layer = np.array([0, 1, 2], dtype=np.int32)
        edges = np.array([[0.0, 0.050, 0.0],
                          [0.0, 0.050, 0.0],
                          [0.0, 0.050, 0.0]], dtype=np.float32)
        out = b._radial_offset_by_layer(edges)
        r = np.hypot(out[:, 1], out[:, 2])
        assert abs(r[0] - (0.050 + 1 * gap)) < 1e-6, "layer0 ofset (0+1)gap değil."
        assert abs(r[1] - (0.050 + 2 * gap)) < 1e-6, "layer1 ofset (1+1)gap değil."
        assert abs(r[2] - (0.050 + 3 * gap)) < 1e-6, "layer2 ofset (2+1)gap değil."
        assert np.allclose(out[:, 0], 0.0), "X ekseni ötelendi (radyal olmalı)."

    def test_DP04_layers_separate_in_depmesh(self):
        b, mod = _make_builder(n_layers=2, N=40)
        dv = b._dep_verts
        if dv is None:
            import pytest
            pytest.skip("dep mesh yok")
        layer_per_vert = np.repeat(b._layer, 2)   # L,R aynı state
        r = np.hypot(dv[:, 1], dv[:, 2])
        r0 = r[layer_per_vert == 0].mean()
        r1 = r[layer_per_vert == 1].mean()
        assert r1 > r0 + 0.5 * mod.DEP_LAYER_GAP_M, (
            f"Katmanlar ayrışmadı: r0={r0*1000:.2f}mm r1={r1*1000:.2f}mm"
        )

    def test_DP05_layer_colors_distinct(self):
        b, _ = _make_builder(n_layers=2, N=40)
        dc = b._dep_colors
        if dc is None:
            import pytest
            pytest.skip("dep mesh yok")
        layer_per_vert = np.repeat(b._layer, 2)
        c0 = dc[layer_per_vert == 0][0]
        c1 = dc[layer_per_vert == 1][0]
        assert not np.allclose(c0, c1), "Katman renkleri aynı."
        # layer0 yeşil (G>R), layer1 sarı (R yüksek)
        assert c0[1] > c0[0], "layer0 yeşil değil."

    def test_DP06_layer0_above_shell_base(self):
        b, mod = _make_builder(n_layers=2, N=40, radius=50.0)
        dv = b._dep_verts
        if dv is None:
            import pytest
            pytest.skip("dep mesh yok")
        layer_per_vert = np.repeat(b._layer, 2)
        r0 = np.hypot(dv[:, 1], dv[:, 2])[layer_per_vert == 0].mean()
        assert r0 > 0.050, "Layer0 dep yüzeyi kabuk tabanının üstünde değil (z-fight riski)."

    def test_DP07_depverts_shape_preserved(self):
        b, _ = _make_builder(n_layers=2, N=40)
        dv = b._dep_verts
        if dv is None:
            import pytest
            pytest.skip("dep mesh yok")
        assert dv.shape == (2 * b.n_states, 3), f"dep_verts şekli {dv.shape}"
