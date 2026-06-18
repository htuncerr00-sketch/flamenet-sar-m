"""
tests/test_s45_lod_tick.py — S4.5 LOD tick + FPS akışı testleri
================================================================
Qt / GL bağımlılığı yok; AdaptiveLODManager doğrudan test edilir.

Çalıştır:
    cd faz17_d1/faz17_d1_backend
    python -m pytest ../tests/test_s45_lod_tick.py -v -s
"""
from __future__ import annotations

import sys
import os
import time

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "faz17_d1_backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from faz17_d1.core.lod_manager import AdaptiveLODManager, LODLevel, _DEFAULT_LEVELS


# ─────────────────────────────────────────────────────────────────────────────
# Mock: level_changed'ı kontrollü üretmek için minimal LOD mock
# ─────────────────────────────────────────────────────────────────────────────

class _ControllableLOD:
    """
    level_changed davranışı dışarıdan kontrol edilebilen LOD mock.

    level_changed_at_tick: bu tick numarasında level_changed=True üretir.
    Gerçek FPS ölçümü yapmaz; deterministik test için.
    """
    def __init__(self, change_at_tick: int | None = None, n_levels: int = 4):
        self.change_at_tick = change_at_tick
        self.tick_count = 0
        self._level_idx = 1
        self._labels = ["LOW", "MED", "HIGH", "ULTRA"][:n_levels]

    def tick(self):
        self.tick_count += 1
        if self.change_at_tick is not None and self.tick_count == self.change_at_tick:
            self._level_idx = min(self._level_idx + 1, len(self._labels) - 1)

    @property
    def level_changed(self) -> bool:
        return (
            self.change_at_tick is not None
            and self.tick_count == self.change_at_tick
        )

    @property
    def fps_measured(self) -> float:
        return 28.5

    class _Level:
        label = "MED"
    @property
    def current(self):
        return self._Level()


class _MockBuilder:
    def __init__(self, n=500):
        self.n_states = n


class _RebuildTracker:
    def __init__(self):
        self.rebuild_count = 0
        self.idx_at_rebuild: list[int] = []

    def rebuild(self, current_idx: int):
        self.rebuild_count += 1
        self.idx_at_rebuild.append(current_idx)


def _simulate_tick_loop(
    lod,
    builder: _MockBuilder,
    tracker: _RebuildTracker,
    n_ticks: int,
    speed: int = 1,
    *,
    skip_on_rebuild: bool,
) -> list[int]:
    """
    _anim_tick() çekirdeğini simüle eder.

    skip_on_rebuild=True  → eski davranış ('return' vardı)
    skip_on_rebuild=False → yeni davranış (devam eder)

    Dönüş: her tick'te işlenen _anim_idx değerleri.
    """
    anim_idx = 0
    processed: list[int] = []

    for _ in range(n_ticks):
        lod.tick()
        if lod.level_changed:
            tracker.rebuild(anim_idx)
            if skip_on_rebuild:
                continue            # eski 'return'
            # Yeni: devam et — n'i yenile (rebuild builder'ı değiştirdi)

        n = builder.n_states
        base_step = max(1, n // 300)
        anim_idx = min(anim_idx + base_step * speed, n - 1)
        processed.append(anim_idx)

        if anim_idx >= n - 1:
            break

    return processed


# ─────────────────────────────────────────────────────────────────────────────
# LT-01: Normal akış — level_changed yok
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalFlow:

    def test_lt01_no_level_change_all_frames_processed(self):
        """LT-01: Level change olmadığında tüm tick'ler işlenir."""
        lod = _ControllableLOD(change_at_tick=None)   # hiç değişmez
        builder = _MockBuilder(500)
        tracker = _RebuildTracker()

        processed = _simulate_tick_loop(
            lod, builder, tracker, n_ticks=10,
            skip_on_rebuild=False)

        assert len(processed) == 10
        assert tracker.rebuild_count == 0
        for i in range(1, len(processed)):
            assert processed[i] > processed[i - 1], "Index geriledi"

    def test_lt02_no_rebuild_without_level_change(self):
        """LT-02: level_changed olmadığında rebuild çağrılmaz."""
        lod = _ControllableLOD(change_at_tick=None)
        builder = _MockBuilder(200)
        tracker = _RebuildTracker()

        _simulate_tick_loop(lod, builder, tracker, n_ticks=20,
                            skip_on_rebuild=False)
        assert tracker.rebuild_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# LT-03/04: S4.5.1 — frame atlanması düzeltmesi
# ─────────────────────────────────────────────────────────────────────────────

class TestFrameSkipFix:

    def test_lt03_old_behavior_skips_frame_on_rebuild(self):
        """LT-03: Eski davranış — rebuild tick'inde frame atlanır."""
        lod = _ControllableLOD(change_at_tick=3)  # tick 3'te level_changed
        builder = _MockBuilder(500)
        tracker = _RebuildTracker()

        processed = _simulate_tick_loop(
            lod, builder, tracker, n_ticks=5,
            skip_on_rebuild=True)   # eski davranış

        assert tracker.rebuild_count == 1
        assert len(processed) == 4, \
            f"Eski: 5 tick - 1 atlanan = 4 bekleniyor, {len(processed)} alındı"

    def test_lt04_new_behavior_no_frame_skip(self):
        """LT-04: Yeni davranış — rebuild tick'inde frame atlanmaz."""
        lod = _ControllableLOD(change_at_tick=3)
        builder = _MockBuilder(500)
        tracker = _RebuildTracker()

        processed = _simulate_tick_loop(
            lod, builder, tracker, n_ticks=5,
            skip_on_rebuild=False)   # yeni davranış

        assert tracker.rebuild_count == 1
        assert len(processed) == 5, \
            f"Yeni: 5 tick = 5 frame bekleniyor, {len(processed)} alındı"

    def test_lt05_anim_idx_continuous_after_rebuild(self):
        """LT-05: Rebuild tick'inden sonra _anim_idx düzenli artmaya devam eder."""
        lod = _ControllableLOD(change_at_tick=2)
        builder = _MockBuilder(500)
        tracker = _RebuildTracker()

        processed = _simulate_tick_loop(
            lod, builder, tracker, n_ticks=8,
            skip_on_rebuild=False)

        assert len(processed) == 8
        for i in range(1, len(processed)):
            assert processed[i] >= processed[i - 1], \
                f"Gerileme: {processed[i-1]}→{processed[i]}"

    def test_lt06_rebuild_at_tick1_no_skip(self):
        """LT-06: İlk tick'te rebuild — frame0'dan sonra ilerleme."""
        lod = _ControllableLOD(change_at_tick=1)
        builder = _MockBuilder(300)
        tracker = _RebuildTracker()

        processed = _simulate_tick_loop(
            lod, builder, tracker, n_ticks=3,
            skip_on_rebuild=False)

        assert len(processed) == 3
        assert tracker.rebuild_count == 1
        assert tracker.idx_at_rebuild[0] == 0   # rebuild sırasında idx=0

    def test_lt07_old_vs_new_processed_count(self):
        """LT-07: Eski ve yeni davranış arasındaki fark: rebuild sayısı kadar frame."""
        n_ticks = 10
        rebuild_ticks = [3, 7]     # iki kez rebuild

        class _MultiChangeLOD:
            def __init__(self):
                self.tick_count = 0
                self._changed = False
            def tick(self):
                self.tick_count += 1
                self._changed = self.tick_count in rebuild_ticks
            @property
            def level_changed(self): return self._changed
            @property
            def fps_measured(self): return 28.5
            class _Level:
                label = "MED"
            @property
            def current(self): return self._Level()

        builder = _MockBuilder(500)

        lod_old = _MultiChangeLOD()
        tracker_old = _RebuildTracker()
        processed_old = _simulate_tick_loop(
            lod_old, builder, tracker_old, n_ticks=n_ticks,
            skip_on_rebuild=True)

        lod_new = _MultiChangeLOD()
        tracker_new = _RebuildTracker()
        processed_new = _simulate_tick_loop(
            lod_new, builder, tracker_new, n_ticks=n_ticks,
            skip_on_rebuild=False)

        # Yeni: rebuild sayısı kadar fazla frame işlendi
        assert len(processed_new) - len(processed_old) == len(rebuild_ticks)


# ─────────────────────────────────────────────────────────────────────────────
# LT-08: AdaptiveLODManager.reset() (S4.5.3 için ön kontrol)
# ─────────────────────────────────────────────────────────────────────────────

class TestLODReset:

    def test_lt08_reset_clears_frame_times(self):
        """LT-08: reset() _frame_times'ı temizler."""
        lod = AdaptiveLODManager()
        for _ in range(10):
            lod.tick()
            time.sleep(0.001)
        assert len(lod._frame_times) > 0
        lod.reset()
        assert len(lod._frame_times) == 0

    def test_lt09_reset_clears_last_tick_t(self):
        """LT-09: reset() sonrası _last_tick_t None olur."""
        lod = AdaptiveLODManager()
        lod.tick()
        assert lod._last_tick_t is not None
        lod.reset()
        assert lod._last_tick_t is None

    def test_lt10_reset_clears_hold_counter(self):
        """LT-10: reset() hold sayacını sıfırlar."""
        lod = AdaptiveLODManager(min_hold_frames=30)
        lod.force_level(2)
        assert lod._hold_remaining == 30
        lod.reset()
        assert lod._hold_remaining == 0

    def test_lt11_fps_zero_after_reset(self):
        """LT-11: reset() sonrası fps_measured == 0.0."""
        lod = AdaptiveLODManager()
        for _ in range(15):
            lod.tick()
            time.sleep(0.001)
        assert lod.fps_measured > 0.0
        lod.reset()
        assert lod.fps_measured == 0.0

    def test_lt12_play_stop_play_no_stale_fps(self):
        """LT-12: Play→Stop(reset)→Play sonrası play1 FPS geçmişi taşınmaz."""
        lod = AdaptiveLODManager(window=5)
        # Play 1: hızlı tick → yüksek FPS ölçümü
        for _ in range(8):
            lod.tick()
            time.sleep(0.001)
        fps_play1 = lod.fps_measured
        assert fps_play1 > 0.0

        # Stop → reset
        lod.reset()
        assert lod.fps_measured == 0.0   # geçmiş temizlendi

        # Play 2: iki tick
        lod.tick()
        time.sleep(0.001)
        lod.tick()
        # 1 dt var → fps hesaplanabilir; ancak play1 verisinden bağımsız
        assert lod._last_tick_t is not None


# ─────────────────────────────────────────────────────────────────────────────
# LT-13: fps_measured erişimi (S4.5.2 için ön kontrol)
# ─────────────────────────────────────────────────────────────────────────────

class TestFPSAccess:

    def test_lt13_fps_measured_returns_float(self):
        """LT-13: fps_measured property float döner."""
        lod = AdaptiveLODManager()
        assert isinstance(lod.fps_measured, float)
        assert lod.fps_measured == 0.0   # başlangıçta veri yok

    def test_lt14_current_label_string(self):
        """LT-14: lod.current.label string döner."""
        lod = AdaptiveLODManager(initial_level_idx=2)
        label = lod.current.label
        assert isinstance(label, str)
        assert label == "HIGH"

    def test_lt15_fps_positive_after_ticks(self):
        """LT-15: Yeterli tick sonrası fps_measured > 0."""
        lod = AdaptiveLODManager(window=3)
        for _ in range(5):
            lod.tick()
            time.sleep(0.002)
        fps = lod.fps_measured
        print(f"\n[LT-15] fps_measured={fps:.1f}")
        assert fps > 0.0
