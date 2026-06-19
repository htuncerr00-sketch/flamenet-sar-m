"""
test_s61_tab_removal.py — S6.1 "3D Görüntüleyici" sekmesi kaldırma testleri
============================================================================

TR-01  addTab satırının kaynak kodda yoruma alındığını doğrula.
TR-02  Panelin (_panel_3d) hâlâ tanımlandığını doğrula.
TR-03  Tüm _panel_3d sinyal bağlantılarının kaynak kodda mevcut olduğunu doğrula.
"""
import re
import pathlib

_MW = pathlib.Path(__file__).parents[2] / "faz17_d2/faz17_d2_app/faz17_d2/app/main_window.py"


def _source() -> str:
    return _MW.read_text(encoding="utf-8")


class TestTabRemoval:
    def test_TR01_tab_line_commented_out(self):
        """addTab satırı aktif kod satırı olmamalı."""
        src = _source()
        # Aktif (yorumsuz) addTab çağrısı "3D Görüntüleyici" ile eşleşmemeli.
        active_add = re.compile(r'^\s*self\._tabs\.addTab.*3D Görüntüleyici', re.MULTILINE)
        assert not active_add.search(src), (
            "'3D Görüntüleyici' addTab satırı hâlâ aktif — S6.1 uygulanmamış."
        )

    def test_TR02_panel_3d_still_defined(self):
        """_panel_3d nesnesi tanımlanmış olmalı (sinyaller için)."""
        src = _source()
        assert "self._panel_3d" in src, (
            "_panel_3d tanımı bulunamadı — panel tamamen silinmiş."
        )

    def test_TR03_signal_connections_intact(self):
        """latestFrame ve on_latest_frame bağlantısı kaynak kodda mevcut olmalı."""
        src = _source()
        assert "on_latest_frame" in src, (
            "on_latest_frame sinyal bağlantısı kaynak koddan silinmiş."
        )
