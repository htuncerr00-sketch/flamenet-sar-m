"""
test_s612_import_fix.py — S6.12 Renderer modülü import düzeltmesi
===================================================================

IM-01  entegre_tasarim_paneli.py içinde `from .renderers import` yok (yanlış).
IM-02  entegre_tasarim_paneli.py içinde `from ..renderers import` var (doğru).
IM-03  Her iki import sitesi de `..renderers` kullanıyor (toplam 2 satır).
IM-04  app.renderers paketi gerçekten var (renderer modülleri erişilebilir).
IM-05  app.panels.renderers paketi yok (import yapılsaydı hata verecekti).
IM-06  6 renderer sınıfının tamamı app.renderers üzerinden import edilebilir.
"""
import pathlib
import importlib
import sys
import re

_ROOT   = pathlib.Path(__file__).parents[2]
_PANEL  = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2/app/panels/entegre_tasarim_paneli.py"
_APP    = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2"


def _src() -> str:
    return _PANEL.read_text(encoding="utf-8")


class TestImportFix:

    def test_IM01_no_single_dot_renderers(self):
        """from .renderers import — bu satır panelde OLMAMALI."""
        lines = [
            ln for ln in _src().splitlines()
            if re.search(r'from\s+\.renderers\s+import', ln)
               and not ln.strip().startswith("#")
        ]
        assert lines == [], (
            f"Hâlâ tek noktalı import var → {lines}\n"
            "from .renderers → app.panels.renderers (yok!) → ImportError"
        )

    def test_IM02_double_dot_renderers_present(self):
        """from ..renderers import — panelde bu satır OLMALI."""
        matches = re.findall(r'from\s+\.\.renderers\s+import', _src())
        assert matches, "from ..renderers import satırı bulunamadı."

    def test_IM03_exactly_two_import_sites(self):
        """_setup_builder ve _setup_renderers — toplam 2 import sitesi olmalı."""
        matches = re.findall(r'from\s+\.\.renderers\s+import', _src())
        assert len(matches) == 2, (
            f"Beklenen 2 import sitesi, bulunan: {len(matches)}"
        )

    def test_IM04_app_renderers_package_exists(self):
        """app/renderers/__init__.py fiziksel olarak var olmalı."""
        pkg = _APP / "app" / "renderers" / "__init__.py"
        assert pkg.exists(), f"app.renderers paketi yok: {pkg}"

    def test_IM05_app_panels_renderers_does_not_exist(self):
        """app/panels/renderers/ dizini OLMAMALI (import o paketle başarısız olurdu)."""
        bad = _APP / "app" / "panels" / "renderers"
        assert not bad.exists(), (
            f"app.panels.renderers beklenmedik biçimde var: {bad}"
        )

    def test_IM06_all_six_renderers_importable(self):
        """6 renderer sınıfının tamamı app.renderers'dan import edilebilmeli."""
        app_dir = str(_APP)
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)

        try:
            from app.renderers import (
                ShellRenderer,
                HeatmapRenderer,
                RibbonRenderer,
                FiberPathRenderer,
                PayoutEyeRenderer,
                MachineRenderer,
            )
        except ImportError as exc:
            raise AssertionError(f"app.renderers import başarısız: {exc}") from exc

        for name, cls in [
            ("ShellRenderer",     ShellRenderer),
            ("HeatmapRenderer",   HeatmapRenderer),
            ("RibbonRenderer",    RibbonRenderer),
            ("FiberPathRenderer", FiberPathRenderer),
            ("PayoutEyeRenderer", PayoutEyeRenderer),
            ("MachineRenderer",   MachineRenderer),
        ]:
            assert cls is not None, f"{name} None döndü."
            assert callable(cls),   f"{name} çağrılabilir değil."
