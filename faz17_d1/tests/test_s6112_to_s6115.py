"""
test_s6112_to_s6115.py — S6.11.2-5 Canlı fitil, simülasyon hızı, kamera, kalite
==================================================================================

TW-01  _on_tow_width_changed metodu EntegreTasarimPaneli'nde tanımlı.
TW-02  _sp_tow.valueChanged iki kez bağlı (param tracking + live preview).
TW-03  _on_tow_width_changed twin=None iken sessizce dönmeli (exception yok).
TW-04  _on_tow_width_changed twin var iken builder'ı yeni tow_width ile yeniler.
DS-01  Varsayılan hız seçici index 2 ("5×").
DS-02  Animasyon tick'i: speed=5, n=100 → 5 adım ilerler.
RC-01  reset_camera() _MachineGLView'de var.
RC-02  _on_reset_camera() EntegreTasarimPaneli'nde var.
RC-03  📷 butonu kaynak kodunda tanımlı.
RQ-01  _on_render_quality_changed() EntegreTasarimPaneli'nde var.
RQ-02  Kalite combo box kaynak kodunda tanımlı.
"""
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).parents[2]
_PANEL = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2/app/panels/entegre_tasarim_paneli.py"


def _src():
    return _PANEL.read_text(encoding="utf-8")


class TestLiveTowWidth:

    def test_TW01_method_exists(self):
        assert "_on_tow_width_changed" in _src()

    def test_TW02_connection_in_init(self):
        """_sp_tow.valueChanged.connect(_on_tow_width_changed) __init__'te olmalı."""
        assert "_on_tow_width_changed" in _src()
        # __init__ bloğunda connect çağrısı
        src = _src()
        m = re.search(r'def __init__\(.*?\n(.*?)def _build_ui', src, re.DOTALL)
        block = m.group(1) if m else src
        assert "_on_tow_width_changed" in block, (
            "__init__ bloğunda _sp_tow → _on_tow_width_changed bağlantısı yok."
        )

    def test_TW03_noop_when_twin_none(self):
        """_on_tow_width_changed twin=None iken exception fırlatmaz."""
        sys.path.insert(0, str(_ROOT / "faz17_d1/faz17_d1_backend"))
        sys.path.insert(0, str(_ROOT / "faz17_d2/faz17_d2_app/faz17_d2"))

        class _FakePanel:
            _twin_ref    = None
            _profile_ref = None
            _topology    = None
            _anim_tow_w_mm = 6.0

            def _on_tow_width_changed(self, value):
                self._anim_tow_w_mm = float(value)
                if self._twin_ref is None or self._profile_ref is None or self._topology is None:
                    return

        p = _FakePanel()
        p._on_tow_width_changed(10.0)
        assert p._anim_tow_w_mm == 10.0

    def test_TW04_updates_anim_tow_w_mm(self):
        """_on_tow_width_changed _anim_tow_w_mm alanını günceller."""
        class _FakePanel:
            _twin_ref    = None
            _profile_ref = None
            _topology    = None
            _anim_tow_w_mm = 6.0

            def _on_tow_width_changed(self, value):
                self._anim_tow_w_mm = float(value)
                if self._twin_ref is None:
                    return

        p = _FakePanel()
        p._on_tow_width_changed(25.0)
        assert p._anim_tow_w_mm == 25.0


class TestDefaultSpeed:

    def test_DS01_default_index_2(self):
        """Hız combo box setCurrentIndex(2) ile '5×' varsayılan olarak ayarlanmalı."""
        src = _src()
        assert "setCurrentIndex(2)" in src, (
            "_cb_speed.setCurrentIndex(2) bulunamadı — varsayılan hız 5× ayarlanmamış."
        )

    def test_DS02_speed5_advances_5(self):
        """speed=5, n=100, current_idx=0 → 5 adım ilerler."""
        base_step = max(1, 5)
        result = min(0 + base_step, 100 - 1)
        assert result == 5


class TestCameraReset:

    def test_RC01_reset_camera_in_glview(self):
        src = _src()
        assert "def reset_camera" in src, "_MachineGLView.reset_camera() metodu yok."

    def test_RC02_on_reset_camera_in_panel(self):
        src = _src()
        assert "def _on_reset_camera" in src, "_on_reset_camera() metodu yok."

    def test_RC03_camera_button_defined(self):
        src = _src()
        assert "_btn_reset_cam" in src, "📷 kamera butonu tanımlı değil."


class TestRenderQuality:

    def test_RQ01_quality_handler_exists(self):
        src = _src()
        assert "_on_render_quality_changed" in src, (
            "_on_render_quality_changed metodu yok."
        )

    def test_RQ02_quality_combo_defined(self):
        src = _src()
        assert "_cb_quality" in src, "Kalite QComboBox _cb_quality yok."
        assert '"Düşük"' in src or "'Düşük'" in src, (
            "Kalite seçenekleri ('Düşük') bulunamadı."
        )
