"""
test_s6153_ribbon_thickness.py — S6.15.3 Ribbon radyal kalınlığı (prepreg hacmi)
==================================================================================

Teşhis: ribbon düz 2-vertex şeritti (sıfır kalınlık), tow=0.5'te alttaki silindir
görünüyordu. extrude_ribbon() şeridi radyal dışa öteleyip hacimli bant yapar.

RT-01  extrude_ribbon fiber_geometry'de ve app.renderers'da mevcut.
RT-02  Düz şerit (2m,3)+faces → (4m,3) vertex + alt/üst/2 kenar duvar yüzleri.
RT-03  Üst vertexler radyal olarak +thickness ötelenmiş (X değişmez, r artar).
RT-04  Çıktı faces indeksleri vertex sınırları içinde.
RT-05  Boş/geçersiz girdi olduğu gibi döner (güvenli).
RT-06  Gerçek builder ribbon frame'i extrude edilince kalınlık kazanır
       (üst yüzey radyal yarıçapı alt yüzeyden büyük).
RT-07  RibbonRenderer extrude_ribbon + RIBBON_THICKNESS_M kullanır.
RT-08  RIBBON_THICKNESS_M pozitif ve makul (0 < t < 5mm).
"""
import importlib.util
import pathlib
import sys

import numpy as np

_ROOT = pathlib.Path(__file__).parents[2]
_REND = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2/app/renderers"
_BACKEND = str(_ROOT / "faz17_d1/faz17_d1_backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _load_fg():
    path = _REND / "fiber_geometry.py"
    spec = importlib.util.spec_from_file_location("fiber_geometry_s6153", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _flat_strip(m=4, radius=0.05):
    """m segmentlik düz ribbon şeridi: L0,R0,L1,R1,... yüzeyde."""
    verts = np.zeros((2 * m, 3), dtype=np.float32)
    for i in range(m):
        x = 0.02 * i
        verts[2 * i]     = [x, radius,  0.003]   # L
        verts[2 * i + 1] = [x, radius, -0.003]   # R
    faces = np.empty((2 * (m - 1), 3), dtype=np.int32)
    for i in range(m - 1):
        faces[2 * i]     = [2 * i,     2 * i + 2, 2 * i + 1]
        faces[2 * i + 1] = [2 * i + 1, 2 * i + 2, 2 * i + 3]
    return verts, faces


class TestRibbonThickness:

    def test_RT01_function_present(self):
        fg = _load_fg()
        assert hasattr(fg, "extrude_ribbon") and callable(fg.extrude_ribbon)
        app_dir = str(_ROOT / "faz17_d2/faz17_d2_app/faz17_d2")
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        for k in list(sys.modules.keys()):
            if "app.renderers" in k:
                del sys.modules[k]
        import importlib as _il
        pkg = _il.import_module("app.renderers")
        assert hasattr(pkg, "extrude_ribbon"), "app.renderers extrude_ribbon ihraç etmiyor."

    def test_RT02_vertex_face_counts(self):
        fg = _load_fg()
        m = 4
        verts, faces = _flat_strip(m)
        v2, f2 = fg.extrude_ribbon(verts, faces, 0.0006)
        assert v2.shape == (4 * m, 3), f"vertex sayısı {v2.shape} (4m beklenir)"
        # alt(2(m-1)) + üst(2(m-1)) + Lduvar(2(m-1)) + Rduvar(2(m-1)) = 8(m-1)
        assert f2.shape == (8 * (m - 1), 3), f"face sayısı {f2.shape}"

    def test_RT03_top_offset_radial(self):
        fg = _load_fg()
        verts, faces = _flat_strip(4, radius=0.05)
        t = 0.0008
        v2, _ = fg.extrude_ribbon(verts, faces, t)
        n = verts.shape[0]
        bottom = v2[:n]
        top = v2[n:]
        # X değişmemeli
        assert np.allclose(bottom[:, 0], top[:, 0], atol=1e-6), "Üst öteleme X'i bozdu."
        # radyal yarıçap +t artmalı
        r_bot = np.hypot(bottom[:, 1], bottom[:, 2])
        r_top = np.hypot(top[:, 1], top[:, 2])
        assert np.allclose(r_top - r_bot, t, atol=1e-5), (
            f"Radyal öteleme {np.mean(r_top - r_bot):.5f} != {t}"
        )

    def test_RT04_face_indices_valid(self):
        fg = _load_fg()
        verts, faces = _flat_strip(5)
        v2, f2 = fg.extrude_ribbon(verts, faces, 0.0006)
        assert int(f2.min()) >= 0
        assert int(f2.max()) < v2.shape[0], "face indeksi vertex sınırını aşıyor."

    def test_RT05_empty_input_safe(self):
        fg = _load_fg()
        v = np.zeros((0, 3), dtype=np.float32)
        f = np.zeros((0, 3), dtype=np.int32)
        v2, f2 = fg.extrude_ribbon(v, f, 0.0006)
        assert v2.shape[0] == 0 and f2.shape[0] == 0, "Boş girdi güvenli dönmedi."
        # tek vertex çifti (2,3) ama faces yok → girdi geri
        v1 = np.zeros((2, 3), dtype=np.float32)
        v2b, f2b = fg.extrude_ribbon(v1, f, 0.0006)
        assert v2b.shape == (2, 3)

    def test_RT06_real_builder_ribbon_gains_thickness(self):
        from faz17_d1.core.geometry_engine import MandrelProfile
        from faz17_d1.core.fiber_band import FiberBand
        from faz17_d1.core.path_generator import WindingPathParams
        from faz17_d1.core.winding_twin import simulate_winding
        from faz17_d1.core.render_frame_builder import RenderFrameBuilder
        fg = _load_fg()

        prof = MandrelProfile.cylinder(300.0, 50.0, n_points=50)
        params = WindingPathParams(profile=prof, alpha_deg=55.0, n_layers=1,
                                   tow_width_mm=6.0, overlap_pct=5.0, n_steps_per_pass=20)
        band = FiberBand(tow_width_mm=6.0, overlap_pct=5.0)
        twin = simulate_winding(prof, band, params, n_layers=1, dt_s=0.5)
        topo = RenderFrameBuilder.build_topology(prof, shell_nz=8, shell_nth=12,
                                                 heatmap_nz=6, heatmap_nth=8,
                                                 ribbon_max_seg=150)
        builder = RenderFrameBuilder(twin, prof, topo, tow_width_mm=6.0)
        frame = builder.build(builder.n_states - 1)
        verts, faces = frame.ribbon_verts, frame.ribbon_faces
        if verts.shape[0] < 4 or faces.shape[0] == 0:
            import pytest
            pytest.skip("ribbon mesh yetersiz")
        t = 0.0006
        v2, f2 = fg.extrude_ribbon(verts, faces, t)
        n = verts.shape[0]
        r_bot = np.hypot(v2[:n, 1], v2[:n, 2])
        r_top = np.hypot(v2[n:, 1], v2[n:, 2])
        assert float(np.mean(r_top - r_bot)) > 0.0, "Gerçek ribbon kalınlık kazanmadı."

    def test_RT07_renderer_uses_extrude(self):
        src = (_REND / "ribbon_renderer.py").read_text(encoding="utf-8")
        assert "extrude_ribbon" in src, "RibbonRenderer extrude_ribbon kullanmıyor."
        assert "RIBBON_THICKNESS_M" in src, "RIBBON_THICKNESS_M yok."

    def test_RT08_thickness_reasonable(self):
        src = (_REND / "ribbon_renderer.py").read_text(encoding="utf-8")
        import re
        m = re.search(r'RIBBON_THICKNESS_M\s*=\s*([\d.]+)', src)
        assert m, "RIBBON_THICKNESS_M değeri bulunamadı."
        t = float(m.group(1))
        assert 0.0 < t < 0.005, f"RIBBON_THICKNESS_M={t} makul aralıkta değil (0-5mm)."
