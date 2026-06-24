"""
test_s6141_new_renderers.py — S6.14.1 Yeni renderer sınıfları
==============================================================

NR-01  NozzleRenderer app.renderers'dan import edilebilir.
NR-02  NozzleRenderer setup/update/teardown/reset metodlarına sahip.
NR-03  FreeFiberRenderer app.renderers'dan import edilebilir.
NR-04  FreeFiberRenderer setup/update/teardown/reset metodlarına sahip.
NR-05  ContactPointRenderer app.renderers'dan import edilebilir.
NR-06  ContactPointRenderer setup/update/teardown/reset metodlarına sahip.
NR-07  LAYER_COLORS __init__.py'dan erişilebilir ve 4 renk içerir.
NR-08  LAYER_COLORS her öğesi 4-elemanlı RGBA tuple (float, 0-1 arası).
NR-09  Tüm 9 renderer (6 eski + 3 yeni) app.renderers'dan import edilebilir.
NR-10  entegre_tasarim_paneli.py NozzleRenderer import ediyor.
NR-11  entegre_tasarim_paneli.py FreeFiberRenderer import ediyor.
NR-12  entegre_tasarim_paneli.py ContactPointRenderer import ediyor.
"""
import pathlib
import sys
import importlib

_ROOT  = pathlib.Path(__file__).parents[2]
_APP   = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2"
_PANEL = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2/app/panels/entegre_tasarim_paneli.py"


def _panel_src() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _get_renderers_module():
    app_dir = str(_APP)
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    # force re-import to pick up new exports
    for key in list(sys.modules.keys()):
        if "app.renderers" in key:
            del sys.modules[key]
    return importlib.import_module("app.renderers")


class TestNewRenderers:

    def test_NR01_nozzle_renderer_importable(self):
        """NozzleRenderer app.renderers'dan import edilebilmeli."""
        mod = _get_renderers_module()
        cls = getattr(mod, "NozzleRenderer", None)
        assert cls is not None, "NozzleRenderer app.renderers'da yok."
        assert callable(cls), "NozzleRenderer çağrılabilir değil."

    def test_NR02_nozzle_renderer_interface(self):
        """NozzleRenderer setup/update/teardown/reset metodlarına sahip olmalı."""
        mod = _get_renderers_module()
        cls = mod.NozzleRenderer
        obj = cls()
        for method in ("setup", "update", "teardown", "reset"):
            assert hasattr(obj, method) and callable(getattr(obj, method)), (
                f"NozzleRenderer.{method} metodu eksik."
            )

    def test_NR03_free_fiber_renderer_importable(self):
        """FreeFiberRenderer app.renderers'dan import edilebilmeli."""
        mod = _get_renderers_module()
        cls = getattr(mod, "FreeFiberRenderer", None)
        assert cls is not None, "FreeFiberRenderer app.renderers'da yok."
        assert callable(cls), "FreeFiberRenderer çağrılabilir değil."

    def test_NR04_free_fiber_renderer_interface(self):
        """FreeFiberRenderer setup/update/teardown/reset metodlarına sahip olmalı."""
        mod = _get_renderers_module()
        cls = mod.FreeFiberRenderer
        obj = cls()
        for method in ("setup", "update", "teardown", "reset"):
            assert hasattr(obj, method) and callable(getattr(obj, method)), (
                f"FreeFiberRenderer.{method} metodu eksik."
            )

    def test_NR05_contact_point_renderer_importable(self):
        """ContactPointRenderer app.renderers'dan import edilebilmeli."""
        mod = _get_renderers_module()
        cls = getattr(mod, "ContactPointRenderer", None)
        assert cls is not None, "ContactPointRenderer app.renderers'da yok."
        assert callable(cls), "ContactPointRenderer çağrılabilir değil."

    def test_NR06_contact_point_renderer_interface(self):
        """ContactPointRenderer setup/update/teardown/reset metodlarına sahip olmalı."""
        mod = _get_renderers_module()
        cls = mod.ContactPointRenderer
        obj = cls()
        for method in ("setup", "update", "teardown", "reset"):
            assert hasattr(obj, method) and callable(getattr(obj, method)), (
                f"ContactPointRenderer.{method} metodu eksik."
            )

    def test_NR07_layer_colors_present_and_count(self):
        """LAYER_COLORS mevcut ve 4 renk içermeli."""
        mod = _get_renderers_module()
        lc = getattr(mod, "LAYER_COLORS", None)
        assert lc is not None, "LAYER_COLORS app.renderers'da yok."
        assert len(lc) == 4, f"LAYER_COLORS 4 renk içermeli, {len(lc)} var."

    def test_NR08_layer_colors_rgba_values(self):
        """LAYER_COLORS her öğesi 4-elemanlı RGBA tuple (0.0-1.0 arası)."""
        mod = _get_renderers_module()
        lc = mod.LAYER_COLORS
        for i, color in enumerate(lc):
            assert len(color) == 4, f"LAYER_COLORS[{i}] 4 eleman içermeli."
            for j, ch in enumerate(color):
                assert 0.0 <= float(ch) <= 1.0, (
                    f"LAYER_COLORS[{i}][{j}] = {ch} geçersiz (0-1 aralığı)."
                )

    def test_NR09_all_nine_renderers_importable(self):
        """9 renderer sınıfının tamamı app.renderers'dan import edilebilmeli."""
        mod = _get_renderers_module()
        expected = [
            "ShellRenderer", "HeatmapRenderer", "RibbonRenderer",
            "MachineRenderer", "FiberPathRenderer", "PayoutEyeRenderer",
            "NozzleRenderer", "FreeFiberRenderer", "ContactPointRenderer",
        ]
        for name in expected:
            cls = getattr(mod, name, None)
            assert cls is not None, f"{name} app.renderers'da bulunamadı."
            assert callable(cls), f"{name} çağrılabilir değil."

    def test_NR10_panel_imports_nozzle_renderer(self):
        """entegre_tasarim_paneli.py NozzleRenderer import etmeli."""
        src = _panel_src()
        assert "NozzleRenderer" in src, (
            "NozzleRenderer panelde import edilmiyor."
        )

    def test_NR11_panel_imports_free_fiber_renderer(self):
        """entegre_tasarim_paneli.py FreeFiberRenderer import etmeli."""
        src = _panel_src()
        assert "FreeFiberRenderer" in src, (
            "FreeFiberRenderer panelde import edilmiyor."
        )

    def test_NR12_panel_imports_contact_point_renderer(self):
        """entegre_tasarim_paneli.py ContactPointRenderer import etmeli."""
        src = _panel_src()
        assert "ContactPointRenderer" in src, (
            "ContactPointRenderer panelde import edilmiyor."
        )
