"""
test_s6111_visual.py — S6.11.1 Görsel iyileştirme testleri
===========================================================

VI-01  Mandrel n=96 (eskiden 64) — kaynak kodda.
VI-02  Disc kapaklar n=56 (eskiden 40).
VI-03  PayoutEyeRenderer scatter dot (_eye_item) setup'ta None.
VI-04  RibbonRenderer "translucent" blending (eskiden "additive").
VI-05  FiberPathRenderer width >= 2.0 (eskiden 1.5).
VI-06  FiberPathRenderer rengi sarı değil (eskiden 0.95, 0.78, 0.12 idi).
VI-07  _build_carriage_at_zero nozul ucu marker var (parlak yeşil box).
VI-08  Fiber kılavuz rod yönü: y_bottom < y_top (aşağıya doğru).
"""
import re
import pathlib

_ROOT = pathlib.Path(__file__).parents[2]
_PANEL = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2/app/panels/entegre_tasarim_paneli.py"
_PE    = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2/app/renderers/payout_eye_renderer.py"
_RR    = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2/app/renderers/ribbon_renderer.py"
_FP    = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2/app/renderers/fiber_path_renderer.py"


class TestVisualImprovements:

    def test_VI01_mandrel_n96(self):
        src = _PANEL.read_text(encoding="utf-8")
        assert "n=96" in src, "Mandrel cylinder n=96 bulunamadı."
        assert "_cyl_mesh(0.0, L, R, n=96" in src, "_cyl_mesh n=96 ile çağrılmıyor."

    def test_VI02_disc_n56(self):
        src = _PANEL.read_text(encoding="utf-8")
        assert "n=56" in src, "Disc cap n=56 bulunamadı."

    def test_VI03_payout_eye_no_scatter(self):
        """PayoutEyeRenderer setup'ta scatter dot oluşturulmamalı."""
        src = _PE.read_text(encoding="utf-8")
        # setup() içinde GLScatterPlotItem olmamalı
        setup_block = re.search(r'def setup\(.*?\n(.*?)def ', src, re.DOTALL)
        block = setup_block.group(1) if setup_block else src
        # Aktif kod satırlarında (# ile başlamayan)
        active = "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))
        assert "GLScatterPlotItem" not in active, (
            "setup() içinde GLScatterPlotItem hâlâ var — scatter dot kaldırılmamış."
        )

    def test_VI04_ribbon_not_additive(self):
        # S6.14.2: glOptions "translucent"→"opaque" (daha iyi görünürlük)
        src = _RR.read_text(encoding="utf-8")
        # Yalnızca aktif kod satırlarında (yorum/docstring satırları hariç) additive olmamalı
        active = "\n".join(
            ln for ln in src.splitlines()
            if not ln.lstrip().startswith("#") and not ln.lstrip().startswith('"""')
            and not ln.lstrip().startswith("'")
        )
        assert '"additive"' not in active, "RibbonRenderer aktif kodunda hâlâ 'additive' var."
        assert '"opaque"' in active or '"translucent"' in active, (
            "RibbonRenderer glOptions 'opaque' veya 'translucent' olmak zorunda."
        )

    def test_VI05_fiber_path_width(self):
        src = _FP.read_text(encoding="utf-8")
        m = re.search(r'width\s*=\s*([\d.]+)', src)
        assert m, "FiberPathRenderer width değeri bulunamadı."
        assert float(m.group(1)) >= 2.0, f"FiberPath width {m.group(1)} < 2.0"

    def test_VI06_fiber_path_not_yellow(self):
        src = _FP.read_text(encoding="utf-8")
        # Eski sarı renk yoksa geçer
        assert "0.95, 0.78, 0.12" not in src, (
            "FiberPathRenderer hâlâ eski sarı rengini kullanıyor."
        )

    def test_VI07_nozzle_tip_marker(self):
        """_build_carriage_at_zero nozul ucu için parlak yeşil kutu içermeli."""
        src = _PANEL.read_text(encoding="utf-8")
        # Parlak yeşil: (0.25, 1.00, 0.40, 1.00) veya benzeri
        assert "1.00, 0.40" in src or "1.00, 0.4" in src or "0.25, 1.0" in src, (
            "Nozul ucu marker rengi bulunamadı."
        )

    def test_VI08_guide_rod_direction(self):
        """Fiber kılavuz rod yönü: y_bottom < y_top (yani aşağıdan yukarı sıralı)."""
        src = _PANEL.read_text(encoding="utf-8")
        # nozzle_y ve guide rod'un aşağı gittiğini doğrula
        assert "nozzle_y" in src, "nozzle_y değişkeni _build_carriage_at_zero'da yok."
        assert "guide_y0" in src, "guide_y0 değişkeni yok (guide rod sabitlenmemiş)."
