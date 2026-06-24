"""
test_s6155_camera_focus.py — S6.15.5 Nozul/temas odaklı kamera presetleri
==========================================================================

S6.14.3'te izo/yan/üst/ön/home presetleri eklenmişti. S6.15.5 winding'e yakın
iki odak ekler: nozul ucu ve temas noktası.

CFoc-01  _MachineGLView.set_camera_focus metodu var.
CFoc-02  Panel _focus_camera + _on_camera_nozzle + _on_camera_contact slotları var.
CFoc-03  Panel'de _btn_cam_nozzle ve _btn_cam_contact butonları var.
CFoc-04  Butonlar Türkçe etiketli ("Nozul", "Temas").
CFoc-05  Butonlar slotlara bağlı (clicked.connect).
CFoc-06  _focus_camera "nozzle" dalı derive_nozzle_point kullanır.
CFoc-07  Eski S6.14.3 presetleri (izo/yan/üst) korunur (regresyon yok).
"""
import pathlib

_ROOT = pathlib.Path(__file__).parents[2]
_PANEL = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2/app/panels/entegre_tasarim_paneli.py"


def _src() -> str:
    return _PANEL.read_text(encoding="utf-8")


class TestCameraFocus:

    def test_CFoc01_set_camera_focus(self):
        assert "def set_camera_focus" in _src(), "set_camera_focus metodu yok."

    def test_CFoc02_panel_slots(self):
        src = _src()
        for slot in ("def _focus_camera", "def _on_camera_nozzle", "def _on_camera_contact"):
            assert slot in src, f"{slot} yok."

    def test_CFoc03_buttons_exist(self):
        src = _src()
        assert "_btn_cam_nozzle" in src, "_btn_cam_nozzle yok."
        assert "_btn_cam_contact" in src, "_btn_cam_contact yok."

    def test_CFoc04_buttons_turkish(self):
        src = _src()
        assert 'QPushButton("Nozul")' in src, "Nozul butonu etiketi yok."
        assert 'QPushButton("Temas")' in src, "Temas butonu etiketi yok."

    def test_CFoc05_buttons_connected(self):
        src = _src()
        assert "self._btn_cam_nozzle.clicked.connect(self._on_camera_nozzle)" in src
        assert "self._btn_cam_contact.clicked.connect(self._on_camera_contact)" in src

    def test_CFoc06_nozzle_uses_derive(self):
        src = _src()
        idx = src.find("def _focus_camera")
        nxt = src.find("\n    def ", idx + 1)
        body = src[idx:nxt if nxt != -1 else len(src)]
        assert "derive_nozzle_point" in body, (
            "_focus_camera nozul dalı derive_nozzle_point kullanmıyor."
        )

    def test_CFoc07_old_presets_preserved(self):
        src = _src()
        for m in ("def set_camera_top", "def set_camera_side",
                  "def set_camera_isometric", "def _on_camera_top"):
            assert m in src, f"Eski preset {m} kaybolmuş (regresyon)."
