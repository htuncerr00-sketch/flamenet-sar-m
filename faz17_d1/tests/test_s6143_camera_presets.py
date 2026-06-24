"""
test_s6143_camera_presets.py — S6.14.3 Kamera preset testleri
==============================================================

CP-01  _MachineGLView.set_camera_top() metodu var.
CP-02  _MachineGLView.set_camera_side() metodu var.
CP-03  _MachineGLView.set_camera_front() metodu var.
CP-04  _MachineGLView.set_camera_isometric() metodu var.
CP-05  _MachineGLView.set_camera_home() metodu var.
CP-06  _MachineGLView._set_cam() yardımcı metodu var.
CP-07  Animasyon timer aralığı 16ms (≈60 FPS).
CP-08  Panel'de _btn_cam_top alanı var.
CP-09  Panel'de _btn_cam_side alanı var.
CP-10  Panel'de _btn_cam_front alanı var.
CP-11  Panel'de _btn_cam_iso alanı var.
CP-12  Panel'de _btn_cam_home alanı var.
CP-13  _on_camera_top slot metodu var.
CP-14  _on_camera_side slot metodu var.
CP-15  _on_camera_front slot metodu var.
CP-16  _on_camera_iso slot metodu var.
CP-17  _on_camera_home slot metodu var.
"""
import re
import pathlib

_ROOT  = pathlib.Path(__file__).parents[2]
_PANEL = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2/app/panels/entegre_tasarim_paneli.py"


def _src():
    return _PANEL.read_text(encoding="utf-8")


class TestCameraPresets:

    def test_CP01_set_camera_top(self):
        assert "def set_camera_top" in _src(), "_MachineGLView.set_camera_top() yok."

    def test_CP02_set_camera_side(self):
        assert "def set_camera_side" in _src(), "_MachineGLView.set_camera_side() yok."

    def test_CP03_set_camera_front(self):
        assert "def set_camera_front" in _src(), "_MachineGLView.set_camera_front() yok."

    def test_CP04_set_camera_isometric(self):
        assert "def set_camera_isometric" in _src(), "_MachineGLView.set_camera_isometric() yok."

    def test_CP05_set_camera_home(self):
        assert "def set_camera_home" in _src(), "_MachineGLView.set_camera_home() yok."

    def test_CP06_set_cam_helper(self):
        assert "def _set_cam" in _src(), "_MachineGLView._set_cam() yardımcısı yok."

    def test_CP07_timer_16ms(self):
        src = _src()
        m = re.search(r'setInterval\((\d+)\)', src)
        assert m, "setInterval() çağrısı bulunamadı."
        assert int(m.group(1)) == 16, f"Timer aralığı {m.group(1)}ms — 16ms olmalı."

    def test_CP08_btn_cam_top(self):
        assert "_btn_cam_top" in _src(), "_btn_cam_top alanı yok."

    def test_CP09_btn_cam_side(self):
        assert "_btn_cam_side" in _src(), "_btn_cam_side alanı yok."

    def test_CP10_btn_cam_front(self):
        assert "_btn_cam_front" in _src(), "_btn_cam_front alanı yok."

    def test_CP11_btn_cam_iso(self):
        assert "_btn_cam_iso" in _src(), "_btn_cam_iso alanı yok."

    def test_CP12_btn_cam_home(self):
        assert "_btn_cam_home" in _src(), "_btn_cam_home alanı yok."

    def test_CP13_on_camera_top(self):
        assert "def _on_camera_top" in _src(), "_on_camera_top slot metodu yok."

    def test_CP14_on_camera_side(self):
        assert "def _on_camera_side" in _src(), "_on_camera_side slot metodu yok."

    def test_CP15_on_camera_front(self):
        assert "def _on_camera_front" in _src(), "_on_camera_front slot metodu yok."

    def test_CP16_on_camera_iso(self):
        assert "def _on_camera_iso" in _src(), "_on_camera_iso slot metodu yok."

    def test_CP17_on_camera_home(self):
        assert "def _on_camera_home" in _src(), "_on_camera_home slot metodu yok."
