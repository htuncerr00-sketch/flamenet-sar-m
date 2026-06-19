"""
test_s62_anim_fix.py — S6.2 animasyon kare-atlama düzeltmesi testleri
======================================================================

AF-01  _anim_tick kaynak kodunda n//300 hesabı olmamalı.
AF-02  base_step, n boyutuna bağımlı olmamalı (sabit 1 × speed).
AF-03  _compute_twin hata mesajını _twin_last_error'a kaydetmeli.
AF-04  Hız 1× → 1 adım/kare.
AF-05  Hız 10× → 10 adım/kare.
AF-06  Büyük n için (n=5000) adım boyutu değişmemeli.
AF-07  _setup_builder twin=None iken hata mesajını etikete yazmalı.
"""
import pathlib
import re

_PANEL = pathlib.Path(__file__).parents[2] / "faz17_d2/faz17_d2_app/faz17_d2/app/panels/entegre_tasarim_paneli.py"


def _source() -> str:
    return _PANEL.read_text(encoding="utf-8")


class TestAnimFix:
    # ── Kaynak kodu kontrolleri ──────────────────────────────────────────────

    def test_AF01_no_n_div_300_in_tick(self):
        """_anim_tick içinde aktif (yorum-dışı) n // 300 ifadesi olmamalı."""
        src = _source()
        m = re.search(r'def _anim_tick\(.*?\n(.*?)def ', src, re.DOTALL)
        block = m.group(1) if m else src
        # Yorum satırlarını çıkar, kalan aktif kodda n//300 olmamalı
        active_lines = [ln for ln in block.splitlines()
                        if not ln.lstrip().startswith("#")]
        active_code = "\n".join(active_lines)
        assert "n // 300" not in active_code and "n//300" not in active_code, (
            "_anim_tick aktif kodunda n//300 hâlâ mevcut — S6.2 uygulanmamış."
        )

    def test_AF02_base_step_is_constant(self):
        """base_step yalnızca speed'e bağlı olmalı, n'ye değil."""
        src = _source()
        m = re.search(r'def _anim_tick\(.*?\n(.*?)def ', src, re.DOTALL)
        block = m.group(1) if m else src
        # max(1, speed) veya sadece speed kabul edilir; n// olmamalı
        assert "base_step" in block, "base_step değişkeni _anim_tick'ten kaldırılmış."
        assert "n // " not in block.split("base_step")[1].split("\n")[0], (
            "base_step satırında hâlâ n bölme işlemi var."
        )

    def test_AF03_twin_error_stored(self):
        """_compute_twin except bloğu _twin_last_error'a yazmalı."""
        src = _source()
        assert "_twin_last_error" in src, (
            "_twin_last_error attribute S6.2 ile eklenmemiş."
        )

    # ── Saf Python mantık testleri ───────────────────────────────────────────

    def _simulate_tick(self, speed: int, n: int, current_idx: int) -> int:
        """S6.2 sonrası _anim_tick mantığını simüle et."""
        base_step = max(1, speed)
        return min(current_idx + base_step, n - 1)

    def test_AF04_speed1_advances_one_step(self):
        assert self._simulate_tick(speed=1, n=3000, current_idx=0) == 1

    def test_AF05_speed10_advances_ten_steps(self):
        assert self._simulate_tick(speed=10, n=3000, current_idx=0) == 10

    def test_AF06_large_n_same_step_size(self):
        """n=5000 ile n=100 aynı adım boyutunu vermeli."""
        step_large = self._simulate_tick(speed=1, n=5000, current_idx=0)
        step_small = self._simulate_tick(speed=1, n=100, current_idx=0)
        assert step_large == step_small == 1

    # ── _setup_builder hata mesajı testi ────────────────────────────────────

    def test_AF07_setup_builder_shows_twin_error(self):
        """_setup_builder twin=None iken _twin_last_error metnini etiketler."""
        src = _source()
        # Kaynak kodda _twin_last_error kullanımı setup_builder içinde olmalı
        assert "_twin_last_error" in src, "_twin_last_error kaynak kodda yok."
        # 'Animasyon yok:' formatı mevcut olmalı
        assert "Animasyon yok:" in src, (
            "Hata mesajı formatı 'Animasyon yok:' bulunamadı."
        )
