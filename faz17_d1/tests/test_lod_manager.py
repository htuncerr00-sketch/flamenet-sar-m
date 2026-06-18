"""
tests/test_lod_manager.py — S4.2.2 AdaptiveLODManager birim testleri
======================================================================
Çalıştır:
    cd faz17_d1/faz17_d1_backend
    python -m pytest ../tests/test_lod_manager.py -v
"""
from __future__ import annotations

import time
import sys
import os

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "faz17_d1_backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from faz17_d1.core.lod_manager import AdaptiveLODManager, LODLevel, _DEFAULT_LEVELS


# ─────────────────────────────────────────────────────────────────────────────
# LM-01 .. LM-05: Başlangıç durumu
# ─────────────────────────────────────────────────────────────────────────────

class TestLODManagerInit:

    def test_lm01_default_level(self):
        """LM-01: Varsayılan başlangıç seviyesi MED (idx=1)."""
        lod = AdaptiveLODManager()
        assert lod.current.label == "MED"
        assert lod.level_idx == 1

    def test_lm02_initial_level_override(self):
        """LM-02: initial_level_idx parametresi çalışıyor."""
        lod = AdaptiveLODManager(initial_level_idx=0)
        assert lod.current.label == "LOW"

    def test_lm03_fps_measured_zero_before_ticks(self):
        """LM-03: tick çağrılmadan fps_measured=0.0."""
        lod = AdaptiveLODManager()
        assert lod.fps_measured == pytest.approx(0.0)

    def test_lm04_level_changed_false_at_start(self):
        """LM-04: Başlangıçta level_changed=False."""
        lod = AdaptiveLODManager()
        assert lod.level_changed is False

    def test_lm05_n_levels(self):
        """LM-05: Varsayılan 4 LOD seviyesi var."""
        lod = AdaptiveLODManager()
        assert lod.n_levels == 4


# ─────────────────────────────────────────────────────────────────────────────
# LM-06 .. LM-10: FPS ölçümü
# ─────────────────────────────────────────────────────────────────────────────

class TestFPSMeasurement:

    def _tick_at_fps(self, lod: AdaptiveLODManager, fps: float, n: int) -> None:
        """Simüle edilmiş sabit FPS için tick'leri oynat."""
        dt = 1.0 / fps
        for _ in range(n):
            if lod._last_tick_t is None:
                lod._last_tick_t = time.perf_counter()
            else:
                lod._last_tick_t -= dt  # zaman geriye çekilerek delta oluşturulur
            lod._frame_times.append(dt)

    def test_lm06_fps_converges(self):
        """LM-06: Sabit 60 FPS ile tick → fps_measured ~60."""
        lod = AdaptiveLODManager(window=30, min_hold_frames=0)
        self._tick_at_fps(lod, 60.0, 30)
        assert lod.fps_measured == pytest.approx(60.0, rel=0.05)

    def test_lm07_median_resists_spike(self):
        """LM-07: Tek spike frame medyanı bozmaz."""
        lod = AdaptiveLODManager(window=21, min_hold_frames=0)
        # 20 frame @ 30 FPS, 1 frame çok yavaş
        for _ in range(20):
            lod._frame_times.append(1.0 / 30.0)
        lod._frame_times.append(2.0)   # 0.5 FPS spike
        assert lod.fps_measured == pytest.approx(30.0, rel=0.05)

    def test_lm08_fps_zero_with_one_sample(self):
        """LM-08: Tek frame_time ile fps_measured=0 (medyan için en az 2 lazım)."""
        lod = AdaptiveLODManager()
        lod._frame_times.append(1.0 / 30.0)
        assert lod.fps_measured == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# LM-11 .. LM-16: Seviye geçişleri
# ─────────────────────────────────────────────────────────────────────────────

class TestLevelTransitions:

    def _fill_fps(self, lod: AdaptiveLODManager, fps: float, n: int = 30) -> None:
        """Frame time deque'sini verilen FPS ile doldur."""
        dt = 1.0 / fps
        for _ in range(n):
            lod._frame_times.append(dt)

    def test_lm11_downgrade_on_low_fps(self):
        """LM-11: FPS < eşik → seviye düşürülür."""
        lod = AdaptiveLODManager(
            fps_target=30.0, fps_dn_thresh=0.80,
            window=20, min_hold_frames=0, initial_level_idx=2,
        )
        self._fill_fps(lod, 20.0)   # < 30 × 0.8 = 24
        lod.tick()
        assert lod.level_idx == 1
        assert lod.level_changed is True

    def test_lm12_upgrade_on_high_fps(self):
        """LM-12: FPS > eşik → seviye yükseltilir."""
        lod = AdaptiveLODManager(
            fps_target=30.0, fps_up_thresh=0.95,
            window=20, min_hold_frames=0, initial_level_idx=0,
        )
        self._fill_fps(lod, 35.0)   # > 30 × 0.95 = 28.5
        lod.tick()
        assert lod.level_idx == 1
        assert lod.level_changed is True

    def test_lm13_hold_prevents_rapid_change(self):
        """LM-13: min_hold_frames süresince ek geçiş engellenir."""
        lod = AdaptiveLODManager(
            fps_target=30.0, fps_dn_thresh=0.80,
            window=5, min_hold_frames=10, initial_level_idx=3,
        )
        self._fill_fps(lod, 10.0)
        lod.tick()   # düşürme tetiklenir, hold=10 başlar
        assert lod.level_idx == 2
        self._fill_fps(lod, 10.0)
        lod.tick()   # hold devam ediyor → tekrar düşürme olmaz
        assert lod.level_idx == 2   # 2'de kaldı (1'e düşmedi)

    def test_lm14_no_downgrade_below_min(self):
        """LM-14: Zaten en düşük seviyedeyken düşürme yok."""
        lod = AdaptiveLODManager(
            fps_target=30.0, fps_dn_thresh=0.80,
            window=5, min_hold_frames=0, initial_level_idx=0,
        )
        self._fill_fps(lod, 5.0)
        lod.tick()
        assert lod.level_idx == 0

    def test_lm15_no_upgrade_above_max(self):
        """LM-15: Zaten en yüksek seviyedeyken yükseltme yok."""
        lod = AdaptiveLODManager(
            fps_target=30.0, fps_up_thresh=0.95,
            window=5, min_hold_frames=0, initial_level_idx=3,
        )
        self._fill_fps(lod, 120.0)
        lod.tick()
        assert lod.level_idx == 3

    def test_lm16_force_level(self):
        """LM-16: force_level() seviyeyi anında değiştirir."""
        lod = AdaptiveLODManager(initial_level_idx=0)
        lod.force_level(3)
        assert lod.level_idx == 3
        assert lod.current.label == "ULTRA"
        assert lod.level_changed is True


# ─────────────────────────────────────────────────────────────────────────────
# LM-17 .. LM-18: reset + repr
# ─────────────────────────────────────────────────────────────────────────────

class TestLODManagerUtility:

    def test_lm17_reset_clears_history(self):
        """LM-17: reset() sonrası fps_measured=0."""
        lod = AdaptiveLODManager()
        for _ in range(20):
            lod._frame_times.append(1.0 / 30.0)
        lod.reset()
        assert lod.fps_measured == pytest.approx(0.0)
        assert lod._hold_remaining == 0

    def test_lm18_repr(self):
        """LM-18: __repr__ çalışıyor, hata vermiyor."""
        lod = AdaptiveLODManager()
        r = repr(lod)
        assert "AdaptiveLODManager" in r
        assert "MED" in r


# ─────────────────────────────────────────────────────────────────────────────
# LM-19 .. LM-20: LODLevel alanları
# ─────────────────────────────────────────────────────────────────────────────

class TestLODLevel:

    def test_lm19_default_levels_ordered(self):
        """LM-19: Varsayılan seviyeler artan kalite sırasında."""
        for i in range(len(_DEFAULT_LEVELS) - 1):
            a, b = _DEFAULT_LEVELS[i], _DEFAULT_LEVELS[i + 1]
            assert b.shell_nth >= a.shell_nth
            assert b.ribbon_max_seg >= a.ribbon_max_seg
            assert b.heatmap_nth >= a.heatmap_nth

    def test_lm20_lodlevel_frozen(self):
        """LM-20: LODLevel frozen=True → değiştirilemez."""
        level = LODLevel("TEST", 10, 16, 8, 12, 100)
        with pytest.raises((AttributeError, TypeError, Exception)):
            level.shell_nth = 999  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
# BM-03: tick() performansı
# ─────────────────────────────────────────────────────────────────────────────

class TestLODBenchmark:

    def test_bm03_tick_performance(self):
        """BM-03: tick() < 0.01 ms/kare (N=10 000)."""
        lod = AdaptiveLODManager(window=20, min_hold_frames=0)
        # Deque'yi doldur
        for _ in range(20):
            lod._frame_times.append(1.0 / 30.0)

        N = 10_000
        t0 = time.perf_counter()
        for _ in range(N):
            lod.tick()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        avg_us = (elapsed_ms / N) * 1000.0
        print(f"\n[BM-03] tick() maliyet: {avg_us:.2f} µs/kare (N={N})")
        assert avg_us < 10.0, f"tick() çok yavaş: {avg_us:.2f} µs (hedef < 10 µs)"
