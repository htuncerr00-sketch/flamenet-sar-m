"""
test_s6152_tow_width_verify.py — S6.15.2 Fitil genişliği → ribbon genişliği doğrulaması
=========================================================================================

Teşhiste tow width'in ribbon genişliğine doğru yansıdığı gerçek GL render ile
görülmüştü (0.5→0.5, 1→1, 3→3, 6→6 mm). Bu test bunu headless olarak GERÇEK
RenderFrameBuilder üzerinde sayısal olarak sabitler.

TW-01  Builder tow=0.5/1/3/6mm için ribbon L/R kenar mesafesi = tow (±%2).
TW-02  Deposition mesh genişliği de tow ile ölçeklenir (dep_verts L/R çifti).
TW-03  Tow arttıkça ribbon kenar mesafesi monoton artar.
TW-04  _tow_half_m = tow/2/1000 (metre yarı-genişlik) doğru.
TW-05  Panel _on_tow_width_changed dep renderer'ı yeniden yükler (latent hata fix).
"""
import pathlib
import sys

import numpy as np
import pytest

_ROOT = pathlib.Path(__file__).parents[2]
_PANEL = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2/app/panels/entegre_tasarim_paneli.py"
_BACKEND = str(_ROOT / "faz17_d1/faz17_d1_backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _make_builder(tow_mm):
    """Gerçek twin (silindir, küçük) + RenderFrameBuilder kur."""
    from faz17_d1.core.geometry_engine import MandrelProfile
    from faz17_d1.core.fiber_band import FiberBand
    from faz17_d1.core.path_generator import WindingPathParams
    from faz17_d1.core.winding_twin import simulate_winding
    from faz17_d1.core.render_frame_builder import RenderFrameBuilder

    prof = MandrelProfile.cylinder(300.0, 50.0, n_points=60)
    params = WindingPathParams(profile=prof, alpha_deg=55.0, n_layers=1,
                               tow_width_mm=tow_mm, overlap_pct=5.0,
                               n_steps_per_pass=20)
    band = FiberBand(tow_width_mm=tow_mm, overlap_pct=5.0)
    twin = simulate_winding(prof, band, params, n_layers=1, dt_s=0.5)
    topo = RenderFrameBuilder.build_topology(prof, shell_nz=10, shell_nth=16,
                                             heatmap_nz=8, heatmap_nth=12,
                                             ribbon_max_seg=200)
    builder = RenderFrameBuilder(twin, prof, topo, tow_width_mm=tow_mm,
                                 deposition=getattr(twin, 'final_deposition', None))
    return builder


def _ribbon_pair_width_mm(builder):
    """Son karenin ribbon ilk L/R çiftinin mesafesi (mm)."""
    frame = builder.build(builder.n_states - 1)
    rv = frame.ribbon_verts
    if rv.shape[0] < 2:
        return None
    return float(np.linalg.norm(rv[0] - rv[1]) * 1000.0)


_TOWS = [0.5, 1.0, 3.0, 6.0]


class TestTowWidthVerify:

    @pytest.mark.parametrize("tow", _TOWS)
    def test_TW01_ribbon_width_matches_tow(self, tow):
        b = _make_builder(tow)
        w = _ribbon_pair_width_mm(b)
        if w is None:
            pytest.skip("ribbon kenarları hesaplanamadı (fiber_contact_model bağımlılığı)")
        assert abs(w - tow) <= max(0.02 * tow, 1e-3), (
            f"tow={tow}mm → ribbon genişliği {w:.4f}mm (sapma > %2)"
        )

    @pytest.mark.parametrize("tow", _TOWS)
    def test_TW02_dep_mesh_width_scales(self, tow):
        b = _make_builder(tow)
        dv = b._dep_verts
        if dv is None or dv.shape[0] < 2:
            pytest.skip("dep mesh yok")
        w = float(np.linalg.norm(dv[0] - dv[1]) * 1000.0)
        assert abs(w - tow) <= max(0.02 * tow, 1e-3), (
            f"tow={tow}mm → dep mesh genişliği {w:.4f}mm"
        )

    def test_TW03_monotonic_increase(self):
        widths = []
        for tow in _TOWS:
            w = _ribbon_pair_width_mm(_make_builder(tow))
            if w is None:
                pytest.skip("ribbon kenarları hesaplanamadı")
            widths.append(w)
        assert all(widths[i] < widths[i + 1] for i in range(len(widths) - 1)), (
            f"Ribbon genişliği monoton artmıyor: {widths}"
        )

    def test_TW04_tow_half_m(self):
        b = _make_builder(6.0)
        assert abs(b._tow_half_m - 6.0 * 0.5 / 1000.0) < 1e-9, (
            f"_tow_half_m yanlış: {b._tow_half_m}"
        )

    def test_TW05_panel_reloads_dep_on_tow_change(self):
        src = _PANEL.read_text(encoding="utf-8")
        # _on_tow_width_changed gövdesini çıkar
        idx = src.find("def _on_tow_width_changed")
        assert idx != -1, "_on_tow_width_changed yok."
        nxt = src.find("\n    def ", idx + 1)
        body = src[idx:nxt if nxt != -1 else len(src)]
        assert "_setup_dep_renderer" in body, (
            "_on_tow_width_changed dep renderer'ı yeniden yüklemiyor (latent hata)."
        )
