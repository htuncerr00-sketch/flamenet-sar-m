"""
core/lod_manager.py — Uyarlamalı LOD yöneticisi (S4.2.2)
==========================================================
AdaptiveLODManager: anlık FPS ölçümüne göre LOD seviyesini
(shell_nth, ribbon_max_seg, heatmap_nth) otomatik olarak yükseltir
ya da düşürür.

Tasarım kuralları
-----------------
1. Ölçüm: son N frame'in medyanı (ani spike'a dayanıklı).
2. Histerez: yükseltmek için fps_up_thresh, düşürmek için fps_dn_thresh.
3. Seviye değişimi min_hold_frames çerçeve boyunca kilitlenir (titreme önleme).
4. Qt / pyqtgraph / OpenGL bağımlılığı YOK → headless test edilebilir.
5. Tüm parametreler oluşturucuya verilir; varsayılanlar tipik 30 FPS hedefi içindir.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional


# ─────────────────────────────────────────────────────────────────────────────
# LODLevel — tek bir LOD yapılandırması
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LODLevel:
    """Tek bir LOD seviyesinin geometri parametreleri."""
    label:           str    # "LOW" | "MED" | "HIGH" | "ULTRA"
    shell_nz:        int    # Kabuk eksenel dilim sayısı
    shell_nth:       int    # Kabuk çevresel dilim sayısı
    heatmap_nz:      int    # Isı haritası eksenel çözünürlük
    heatmap_nth:     int    # Isı haritası çevresel çözünürlük
    ribbon_max_seg:  int    # Ribbon buffer maksimum segment sayısı


# Varsayılan LOD kademeleri (mandrel L~300mm, r~50mm referans)
_DEFAULT_LEVELS: tuple[LODLevel, ...] = (
    LODLevel("LOW",   shell_nz=10, shell_nth=16,  heatmap_nz=8,  heatmap_nth=12,  ribbon_max_seg=100),
    LODLevel("MED",   shell_nz=20, shell_nth=32,  heatmap_nz=16, heatmap_nth=24,  ribbon_max_seg=300),
    LODLevel("HIGH",  shell_nz=30, shell_nth=48,  heatmap_nz=24, heatmap_nth=36,  ribbon_max_seg=600),
    LODLevel("ULTRA", shell_nz=40, shell_nth=64,  heatmap_nz=32, heatmap_nth=48,  ribbon_max_seg=1000),
)


# ─────────────────────────────────────────────────────────────────────────────
# AdaptiveLODManager
# ─────────────────────────────────────────────────────────────────────────────

class AdaptiveLODManager:
    """
    FPS tabanlı uyarlamalı LOD yöneticisi.

    Kullanım
    --------
    >>> lod = AdaptiveLODManager()
    >>> lod.tick()           # her render çerçevesinin başında çağır
    >>> level = lod.current  # mevcut LODLevel
    >>> changed = lod.level_changed  # bu tick'te seviye değişti mi?

    Parametreler
    ------------
    levels : LOD seviyesi dizisi (artan kalite sırasında)
    fps_target : hedef kare hızı (Hz)
    fps_dn_thresh : bu altına düşerse seviye indirilir (target'ın oranı)
    fps_up_thresh : bu üzerine çıkarsa seviye yükseltilir (target'ın oranı)
    window : FPS ortalaması için kullanılan son N frame sayısı
    min_hold_frames : seviye değişimi sonrası bekleme (titreme önleme)
    initial_level_idx : başlangıç LOD seviyesi indeksi
    """

    def __init__(
        self,
        levels: Optional[tuple[LODLevel, ...]] = None,
        fps_target: float = 30.0,
        fps_dn_thresh: float = 0.80,   # < 24 FPS → düşür
        fps_up_thresh: float = 0.95,   # > 28.5 FPS → yükselt
        window: int = 20,
        min_hold_frames: int = 30,
        initial_level_idx: int = 1,    # varsayılan: MED
    ) -> None:
        self._levels: tuple[LODLevel, ...] = levels or _DEFAULT_LEVELS
        assert len(self._levels) >= 1, "En az bir LOD seviyesi gerekli"

        self._fps_target = fps_target
        self._fps_dn = fps_target * fps_dn_thresh
        self._fps_up = fps_target * fps_up_thresh
        self._window = window
        self._min_hold = min_hold_frames

        idx = max(0, min(initial_level_idx, len(self._levels) - 1))
        self._idx: int = idx
        self._hold_remaining: int = 0
        self._level_changed: bool = False

        self._frame_times: Deque[float] = deque(maxlen=window)
        self._last_tick_t: Optional[float] = None

    # ── Genel bilgiler ───────────────────────────────────────────────────────

    @property
    def current(self) -> LODLevel:
        """Mevcut LOD seviyesi."""
        return self._levels[self._idx]

    @property
    def level_idx(self) -> int:
        return self._idx

    @property
    def n_levels(self) -> int:
        return len(self._levels)

    @property
    def level_changed(self) -> bool:
        """Bu tick'te seviye değişti mi? (setup() çağrısı için)"""
        return self._level_changed

    @property
    def fps_measured(self) -> float:
        """Son window frame'in medyan FPS'i. Yeterli veri yoksa 0.0."""
        if len(self._frame_times) < 2:
            return 0.0
        sorted_dt = sorted(self._frame_times)
        n = len(sorted_dt)
        if n % 2 == 1:
            median_dt = sorted_dt[n // 2]
        else:
            median_dt = (sorted_dt[n // 2 - 1] + sorted_dt[n // 2]) / 2.0
        if median_dt <= 0.0:
            return 0.0
        return 1.0 / median_dt

    # ── Ana güncelleme ───────────────────────────────────────────────────────

    def tick(self) -> None:
        """
        Her render çerçevesinin **başında** çağır.

        Geçen süreyi ölçer, FPS medyanını günceller ve gerekirse LOD'u ayarlar.
        `level_changed` özelliği bu tick'te seviye değişip değişmediğini bildirir.
        """
        now = time.perf_counter()
        self._level_changed = False

        if self._last_tick_t is not None:
            dt = now - self._last_tick_t
            if 0.0 < dt < 1.0:          # 1 s üstü sapmaları (pause vs.) yoksay
                self._frame_times.append(dt)
        self._last_tick_t = now

        if self._hold_remaining > 0:
            self._hold_remaining -= 1
            return

        fps = self.fps_measured
        if fps <= 0.0:
            return

        if fps < self._fps_dn and self._idx > 0:
            self._idx -= 1
            self._hold_remaining = self._min_hold
            self._level_changed = True
        elif fps > self._fps_up and self._idx < len(self._levels) - 1:
            self._idx += 1
            self._hold_remaining = self._min_hold
            self._level_changed = True

    # ── Manuel kontrol ───────────────────────────────────────────────────────

    def force_level(self, idx: int) -> None:
        """LOD seviyesini zorla ayarla (test / kullanıcı müdahalesi)."""
        self._idx = max(0, min(idx, len(self._levels) - 1))
        self._hold_remaining = self._min_hold
        self._level_changed = True
        self._frame_times.clear()

    def reset(self) -> None:
        """Ölçüm geçmişini temizle, hold sayacını sıfırla."""
        self._frame_times.clear()
        self._hold_remaining = 0
        self._last_tick_t = None
        self._level_changed = False

    # ── Debug ────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"AdaptiveLODManager(level={self.current.label!r}, "
            f"fps={self.fps_measured:.1f}, hold={self._hold_remaining})"
        )


__all__ = [
    "LODLevel",
    "AdaptiveLODManager",
    "_DEFAULT_LEVELS",
]
