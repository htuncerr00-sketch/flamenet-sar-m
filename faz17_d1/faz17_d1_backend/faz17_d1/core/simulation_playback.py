"""
core/simulation_playback.py — Simülasyon Oynatma Motoru
=========================================================
Digital twin simülasyon sonucunu (TwinSimulationResult) oynatmak için
durum yönetimi motoru: oynat/duraklat, zaman çizelgesi scrubber, hız
çarpanı, katman katman tekrar ve canlı telemetri katmanı.

Bu motor UI'dan BAĞIMSIZDIR — saf durum mantığı. UI (PySide6 QTimer vb.)
her wall-clock adımında `advance(dt_wall)` çağırır ve `current_state()`
ile `telemetry_snapshot()` okur.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .winding_twin import TwinSimulationResult, TwinState


class SimulationPlayback:
    """
    Digital twin oynatma denetleyicisi.

    Kullanım
    --------
    pb = SimulationPlayback(result)
    pb.play()
    # UI döngüsünde her kare:
    pb.advance(dt_wall_s)          # gerçek geçen süre × hız çarpanı kadar ilerle
    state = pb.current_state()
    tele = pb.telemetry_snapshot()
    """

    def __init__(self, result: TwinSimulationResult):
        if not result.states:
            raise ValueError("Oynatılacak durum yok (boş simülasyon)")
        self._result = result
        self._t = 0.0                  # Sanal oynatma zamanı (s)
        self._playing = False
        self._speed = 1.0              # Hız çarpanı
        self._duration = result.total_time_s

    # ── Aktarım kontrolü ─────────────────────────────────────────────────────

    def play(self) -> None:
        self._playing = True

    def pause(self) -> None:
        self._playing = False

    def toggle(self) -> None:
        self._playing = not self._playing

    @property
    def is_playing(self) -> bool:
        return self._playing

    def stop(self) -> None:
        """Durdur ve başa sar."""
        self._playing = False
        self._t = 0.0

    # ── Hız ──────────────────────────────────────────────────────────────────

    def set_speed(self, multiplier: float) -> None:
        """Oynatma hız çarpanını ayarla (örn. 0.5×, 2×, 10×)."""
        if multiplier <= 0:
            raise ValueError("Hız çarpanı > 0 olmalı")
        self._speed = multiplier

    @property
    def speed(self) -> float:
        return self._speed

    # ── Zaman çizelgesi ──────────────────────────────────────────────────────

    @property
    def time_s(self) -> float:
        return self._t

    @property
    def duration_s(self) -> float:
        return self._duration

    @property
    def progress_pct(self) -> float:
        return (self._t / self._duration * 100.0) if self._duration > 0 else 0.0

    @property
    def at_end(self) -> bool:
        return self._t >= self._duration - 1e-9

    def seek(self, t_s: float) -> None:
        """Belirtilen sanal zamana atla (scrubber)."""
        self._t = max(0.0, min(self._duration, t_s))

    def seek_fraction(self, fraction: float) -> None:
        """Toplam sürenin [0,1] kesrine atla."""
        self.seek(fraction * self._duration)

    def advance(self, dt_wall_s: float) -> None:
        """
        Wall-clock dt kadar (hız çarpanıyla) sanal zamanı ilerlet.

        Oynatma duraklatılmışsa hiçbir şey yapmaz. Sona ulaşınca durur.
        """
        if not self._playing:
            return
        self._t += dt_wall_s * self._speed
        if self._t >= self._duration:
            self._t = self._duration
            self._playing = False

    # ── Katman katman tekrar ─────────────────────────────────────────────────

    @property
    def n_layers(self) -> int:
        return self._result.n_layers

    def current_layer(self) -> int:
        return self.current_state().current_layer

    def layer_time_range(self, layer_index: int) -> Tuple[float, float]:
        """Bir katmanın (t_başlangıç, t_bitiş) zaman aralığı."""
        ranges = self._result.layer_time_ranges_s
        if not (0 <= layer_index < len(ranges)):
            raise IndexError(f"Katman {layer_index} aralık dışında")
        return ranges[layer_index]

    def seek_layer(self, layer_index: int) -> None:
        """Belirtilen katmanın başına atla."""
        t0, _ = self.layer_time_range(layer_index)
        self.seek(t0)

    def play_layer(self, layer_index: int) -> None:
        """Bir katmanı baştan oynat."""
        self.seek_layer(layer_index)
        self.play()

    # ── Durum erişimi ────────────────────────────────────────────────────────

    def current_state(self) -> TwinState:
        return self._result.state_at(self._t)

    def state_at(self, t_s: float) -> TwinState:
        return self._result.state_at(t_s)

    # ── Canlı telemetri katmanı ──────────────────────────────────────────────

    def telemetry_snapshot(self) -> Dict[str, float]:
        """
        UI üst-katmanı için anlık telemetri sözlüğü.

        Tüm değerler o anki sanal zamandaki twin durumundan türetilir.
        """
        s = self.current_state()
        return {
            "t_s": s.t_s,
            "ilerleme_pct": s.progress_pct,
            "is_mili_aci_deg": s.spindle_angle_deg,
            "is_mili_rpm": s.spindle_rpm,
            "tasiyici_x_mm": s.carriage_x_mm,
            "tasiyici_x_gercek_mm": s.carriage_x_actual_mm,
            "tasiyici_hiz_mm_s": s.carriage_v_mm_s,
            "goz_x_mm": s.eye_x_mm,
            "goz_r_mm": s.eye_r_mm,
            "temas_z_mm": s.contact_z_mm,
            "temas_r_mm": s.contact_r_mm,
            "katman": float(s.current_layer),
            "devre": float(s.current_circuit),
            "fiber_yatirilan_mm": s.fiber_deposited_mm,
            "yuzey_yaricap_mm": s.current_radius_mm,
            "gecikme_hata_mm": s.lag_error_mm,
        }

    def state_count(self) -> int:
        return len(self._result.states)
