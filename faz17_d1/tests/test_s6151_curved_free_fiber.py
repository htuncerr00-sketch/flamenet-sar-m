"""
test_s6151_curved_free_fiber.py — S6.15.1 Eğri serbest fiber + nozul türetme
=============================================================================

CF-01  fiber_geometry modülü derive_nozzle_point/sample_quadratic_bezier/
       free_fiber_curve fonksiyonlarını sağlar.
CF-02  derive_nozzle_point nozulu temas noktasına YAKIN konumlar (uzak eye değil).
CF-03  Türetilen nozul yüzeyin DIŞINDA (radius > temas radius) ama makul standoff.
CF-04  Eksenel lead işareti eye'ın eksenel tarafını izler.
CF-05  Küçük mandrelde min_standoff_m taban değeri uygulanır.
CF-06  sample_quadratic_bezier (n,3) döner; uç noktalar p0 ve p2.
CF-07  free_fiber_curve (18,3) döner; başlangıç=türetilen nozul, bitiş=contact.
CF-08  free_fiber_curve eğridir (orta nokta düz kirişten sapar).
CF-09  app.renderers fiber_geometry fonksiyonlarını yeniden ihraç eder.
CF-10  FreeFiberRenderer setup _N_CURVE (>2) noktalı GLLinePlotItem kurar.
CF-11  nozzle_renderer.py + payout_eye_renderer.py derive_nozzle_point kullanır.
CF-12  free_fiber_renderer.py free_fiber_curve kullanır (düz 2-nokta değil).
"""
import importlib.util
import pathlib
import sys

import numpy as np

_ROOT = pathlib.Path(__file__).parents[2]
_REND = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2/app/renderers"
_APP  = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2"


def _load_fiber_geometry():
    """fiber_geometry.py'yi doğrudan yükle (Qt bağımlılığı yok)."""
    path = _REND / "fiber_geometry.py"
    spec = importlib.util.spec_from_file_location("fiber_geometry_s6151", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rend_src(name: str) -> str:
    return (_REND / name).read_text(encoding="utf-8")


class TestCurvedFreeFiber:

    def test_CF01_module_functions(self):
        fg = _load_fiber_geometry()
        for fn in ("derive_nozzle_point", "sample_quadratic_bezier", "free_fiber_curve"):
            assert hasattr(fg, fn) and callable(getattr(fg, fn)), f"{fn} eksik."

    def test_CF02_nozzle_near_contact(self):
        fg = _load_fiber_geometry()
        contact = np.array([0.0, 0.05, 0.0])      # radius 50mm
        eye_far = np.array([-0.21, 0.072, 0.187]) # gerçek uzak eye
        nozzle = np.asarray(fg.derive_nozzle_point(eye_far, contact), dtype=float)
        d_noz = np.linalg.norm(nozzle - contact)
        d_eye = np.linalg.norm(eye_far - contact)
        assert d_noz < d_eye * 0.5, (
            f"Nozul hâlâ uzak: d_noz={d_noz:.3f} vs d_eye={d_eye:.3f}"
        )
        assert d_noz < 0.12, f"Nozul standoff çok büyük: {d_noz:.3f}m"

    def test_CF03_nozzle_outside_surface(self):
        fg = _load_fiber_geometry()
        contact = np.array([0.10, 0.05, 0.0])
        eye = np.array([0.30, 0.05, 0.0])
        nozzle = np.asarray(fg.derive_nozzle_point(eye, contact), dtype=float)
        r_contact = np.hypot(contact[1], contact[2])
        r_nozzle = np.hypot(nozzle[1], nozzle[2])
        assert r_nozzle > r_contact, "Nozul yüzeyin dışında değil."
        assert r_nozzle < r_contact + 0.10, "Nozul standoff makul aralıkta değil."

    def test_CF04_lead_sign(self):
        fg = _load_fiber_geometry()
        contact = np.array([0.10, 0.05, 0.0])
        noz_pos = np.asarray(fg.derive_nozzle_point(np.array([0.30, 0.05, 0.0]), contact), float)
        noz_neg = np.asarray(fg.derive_nozzle_point(np.array([-0.10, 0.05, 0.0]), contact), float)
        assert noz_pos[0] > contact[0], "Pozitif eye tarafında lead +X olmalı."
        assert noz_neg[0] < contact[0], "Negatif eye tarafında lead -X olmalı."

    def test_CF05_min_standoff(self):
        fg = _load_fiber_geometry()
        contact = np.array([0.0, 0.005, 0.0])   # 5mm radius — çok küçük
        eye = np.array([0.05, 0.005, 0.0])
        nozzle = np.asarray(fg.derive_nozzle_point(eye, contact), float)
        r_nozzle = np.hypot(nozzle[1], nozzle[2])
        assert r_nozzle - 0.005 >= 0.030 - 1e-6, "min_standoff_m taban uygulanmadı."

    def test_CF06_bezier_shape_and_endpoints(self):
        fg = _load_fiber_geometry()
        p0 = np.array([0.0, 0.0, 0.0]); p1 = np.array([1.0, 2.0, 0.0]); p2 = np.array([2.0, 0.0, 0.0])
        pts = np.asarray(fg.sample_quadratic_bezier(p0, p1, p2, n=10), float)
        assert pts.shape == (10, 3), f"şekil {pts.shape}"
        assert np.allclose(pts[0], p0), "başlangıç p0 değil."
        assert np.allclose(pts[-1], p2), "bitiş p2 değil."

    def test_CF07_free_fiber_curve_endpoints(self):
        fg = _load_fiber_geometry()
        contact = np.array([0.0, 0.05, 0.0])
        eye = np.array([-0.21, 0.072, 0.187])
        pts = np.asarray(fg.free_fiber_curve(eye, contact, n=18), float)
        assert pts.shape == (18, 3), f"şekil {pts.shape}"
        nozzle = np.asarray(fg.derive_nozzle_point(eye, contact), float)
        assert np.allclose(pts[0], nozzle, atol=1e-5), "eğri başı türetilen nozul değil."
        assert np.allclose(pts[-1], contact, atol=1e-5), "eğri sonu contact değil."

    def test_CF08_curve_is_curved(self):
        fg = _load_fiber_geometry()
        contact = np.array([0.0, 0.05, 0.0])
        eye = np.array([-0.21, 0.072, 0.187])
        pts = np.asarray(fg.free_fiber_curve(eye, contact, n=18), float)
        # Orta nokta, düz kirişin orta noktasından sapmalı (bombe)
        chord_mid = 0.5 * (pts[0] + pts[-1])
        curve_mid = pts[len(pts) // 2]
        assert np.linalg.norm(curve_mid - chord_mid) > 1e-4, "Eğri düz çizgi (bombe yok)."

    def test_CF09_reexport_from_package(self):
        app_dir = str(_APP)
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        for k in list(sys.modules.keys()):
            if "app.renderers" in k:
                del sys.modules[k]
        import importlib as _il
        pkg = _il.import_module("app.renderers")
        for fn in ("derive_nozzle_point", "sample_quadratic_bezier", "free_fiber_curve"):
            assert hasattr(pkg, fn), f"app.renderers.{fn} yeniden ihraç edilmemiş."

    def test_CF10_free_fiber_renderer_n_curve(self):
        src = _rend_src("free_fiber_renderer.py")
        assert "_N_CURVE" in src, "_N_CURVE tanımı yok."
        import re
        m = re.search(r'_N_CURVE\s*=\s*(\d+)', src)
        assert m and int(m.group(1)) > 2, "_N_CURVE > 2 olmalı (eğri için)."

    def test_CF11_nozzle_payout_use_derive(self):
        assert "derive_nozzle_point" in _rend_src("nozzle_renderer.py"), (
            "nozzle_renderer derive_nozzle_point kullanmıyor."
        )
        assert "derive_nozzle_point" in _rend_src("payout_eye_renderer.py"), (
            "payout_eye_renderer derive_nozzle_point kullanmıyor."
        )

    def test_CF12_free_fiber_uses_curve(self):
        src = _rend_src("free_fiber_renderer.py")
        assert "free_fiber_curve" in src, "free_fiber_renderer free_fiber_curve kullanmıyor."
