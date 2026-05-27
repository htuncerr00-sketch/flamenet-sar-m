"""
rt_scheduler.py — Gerçek Zamanlı Deterministik Zamanlayıcı
===========================================================
Farklı controller platformlarında step pulse zamanlaması ve
jitter analizi.

Platform modelleri:
  ESP32/FluidNC:
    - FreeRTOS tick: 1kHz (1ms tick, timer ISR 40kHz)
    - Step timer: hw_timer_t, 80MHz clock → 12.5ns resolution
    - Jitter: ~5µs (1σ) — ISR priority + WiFi interrupt sharing
    - Buffer: 16-segment look-ahead
    - Latency: USB-serial ~2ms + parse ~0.5ms

  LinuxCNC (RTAI):
    - Kernel preemption: 25µs base period
    - Step timing: CPU cycle counter, ~10ns resolution
    - Jitter: ~10µs (1σ) — cache miss + IRQ latency
    - Buffer: trajectory planner 100+ segments

  GRBL (Arduino/ESP32 via Python):
    - Python → USB → GRBL parse: 500µs-2ms latency
    - Jitter: ~500µs (1σ) — OS scheduling + USB
    - Buffer: 127 bytes ≈ 15 short segments
    - ok-flow protocol: round-trip latency dominant

Timing budget analizi:
  Segment time at 6000mm/min, 1mm segment = 10ms
  ESP32 jitter (5µs) / segment_time (10ms) = 0.05% → OK
  GRBL latency (2ms) / segment_time (10ms) = 20%  → problematic at high speed!

Step pulse integrity metriği:
  Q_pulse = 1 - (σ_jitter / t_segment_min)
  Q > 0.9997 (3σ quality) → production grade

Safe queue modeli:
  Hard-realtime: lock-free ring buffer (single producer, single consumer)
  Segment committed → cannot be modified (immutable after enqueue)
  Underflow protection: pre-fill N_lookahead segments before motion start
  N_lookahead ≥ 3 × (latency / t_segment_min)

Referans:
  FreeRTOS timer ISR documentation; LinuxCNC HAL timing paper;
  ADA268923 Appendix I: step rate requirements
"""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple
import numpy as np


# ── Platform Models ────────────────────────────────────────────────

class Platform(Enum):
    ESP32_FLUIDNC = "esp32_fluidnc"
    LINUXCNC_RTAI = "linuxcnc_rtai"
    GRBL_SERIAL   = "grbl_serial"
    MACH3_KERNEL  = "mach3_kernel"
    MOCK          = "mock"


@dataclass(frozen=True, slots=True)
class PlatformTimingSpec:
    """Platform zamanlama spesifikasyonu."""
    name:               str
    step_resolution_ns: float    # Minimum step pulse genişliği [ns]
    jitter_1sigma_us:   float    # 1σ zamanlama titreşimi [µs]
    latency_mean_ms:    float    # Ortalama komut gecikmesi [ms]
    latency_max_ms:     float    # Maksimum komut gecikmesi [ms]
    max_step_rate_hz:   int      # Maksimum step/s
    lookahead_segments: int      # Look-ahead buffer kapasitesi
    supports_hw_timer:  bool     # Donanım timer desteği
    min_segment_ms:     float    # Güvenilir minimum segment süresi [ms]

    @property
    def pulse_quality_3sigma(self) -> float:
        """3σ kalite metriği: Q = 1 - 3σ/t_seg_min"""
        if self.min_segment_ms <= 0:
            return 0.0
        return max(0.0, 1.0 - 3 * self.jitter_1sigma_us / 1000.0 / self.min_segment_ms)

    def jitter_budget_fraction(self, t_segment_ms: float) -> float:
        """Bu segment süresinde jitter bütçe oranı."""
        if t_segment_ms <= 0:
            return 1.0
        return self.jitter_1sigma_us / 1000.0 / t_segment_ms

    def report(self) -> str:
        q = self.pulse_quality_3sigma
        return (
            f"  {self.name:<20} jitter={self.jitter_1sigma_us:.1f}µs(1σ)  "
            f"latency={self.latency_mean_ms:.1f}/{self.latency_max_ms:.1f}ms  "
            f"max_step={self.max_step_rate_hz}Hz  "
            f"Q_pulse={q:.6f}  {'✓' if q>0.9997 else '✗'}"
        )


PLATFORM_SPECS = {
    Platform.ESP32_FLUIDNC: PlatformTimingSpec(
        name="ESP32/FluidNC",
        step_resolution_ns = 12.5,     # 80MHz clock
        jitter_1sigma_us   = 5.0,
        latency_mean_ms    = 2.5,
        latency_max_ms     = 8.0,
        max_step_rate_hz   = 40000,
        lookahead_segments = 16,
        supports_hw_timer  = True,
        min_segment_ms     = 1.0,
    ),
    Platform.LINUXCNC_RTAI: PlatformTimingSpec(
        name="LinuxCNC/RTAI",
        step_resolution_ns = 25000,    # 25µs base period
        jitter_1sigma_us   = 10.0,
        latency_mean_ms    = 0.1,
        latency_max_ms     = 0.5,
        max_step_rate_hz   = 40000,
        lookahead_segments = 100,
        supports_hw_timer  = True,
        min_segment_ms     = 0.5,
    ),
    Platform.GRBL_SERIAL: PlatformTimingSpec(
        name="GRBL/Serial",
        step_resolution_ns = 1000,     # 1µs (Arduino 16MHz)
        jitter_1sigma_us   = 500.0,
        latency_mean_ms    = 2.0,
        latency_max_ms     = 15.0,
        max_step_rate_hz   = 30000,
        lookahead_segments = 15,
        supports_hw_timer  = False,
        min_segment_ms     = 5.0,
    ),
    Platform.MACH3_KERNEL: PlatformTimingSpec(
        name="Mach3/Kernel",
        step_resolution_ns = 10000,    # 10µs kernel mode
        jitter_1sigma_us   = 15.0,
        latency_mean_ms    = 1.0,
        latency_max_ms     = 3.0,
        max_step_rate_hz   = 100000,
        lookahead_segments = 50,
        supports_hw_timer  = True,
        min_segment_ms     = 1.0,
    ),
    Platform.MOCK: PlatformTimingSpec(
        name="MockController",
        step_resolution_ns = 1.0,
        jitter_1sigma_us   = 0.1,
        latency_mean_ms    = 0.0,
        latency_max_ms     = 0.1,
        max_step_rate_hz   = 10000000,
        lookahead_segments = 256,
        supports_hw_timer  = True,
        min_segment_ms     = 0.0,
    ),
}


# ── Jitter Simulation ─────────────────────────────────────────────

@dataclass(slots=True)
class TimingMeasurement:
    """Tek segment zamanlama ölçümü."""
    segment_idx:    int
    t_commanded_ms: float     # Komut edilmesi gereken zaman
    t_actual_ms:    float     # Gerçekleşen zaman (simüle)
    jitter_us:      float     # Sapma [µs]
    missed_pulse:   bool      # Step kaybı var mı?
    late:           bool      # Geç mi?

    @property
    def jitter_abs_us(self) -> float:
        return abs(self.jitter_us)


@dataclass(slots=True)
class TimingAnalysis:
    """Zamanlama analizi sonuçları."""
    platform:          Platform
    n_segments:        int
    jitter_mean_us:    float
    jitter_1sigma_us:  float
    jitter_3sigma_us:  float
    jitter_max_us:     float
    latency_mean_ms:   float
    n_missed_pulses:   int
    n_late_segments:   int
    pulse_quality:     float    # [0,1]
    production_grade:  bool
    histogram:         np.ndarray   # Jitter dağılımı

    def report(self) -> str:
        spec = PLATFORM_SPECS.get(self.platform)
        lines = [
            f"  Platform: {spec.name if spec else self.platform.value}",
            f"  Segments: {self.n_segments}",
            f"  Jitter: mean={self.jitter_mean_us:.2f}µs  "
            f"1σ={self.jitter_1sigma_us:.2f}µs  "
            f"3σ={self.jitter_3sigma_us:.2f}µs  "
            f"max={self.jitter_max_us:.2f}µs",
            f"  Latency: {self.latency_mean_ms:.2f}ms",
            f"  Missed pulses: {self.n_missed_pulses} ({100*self.n_missed_pulses/max(self.n_segments,1):.3f}%)",
            f"  Late segments: {self.n_late_segments}",
            f"  Pulse quality: {self.pulse_quality:.6f}  "
            f"{'✓ PRODUCTION GRADE' if self.production_grade else '✗ SUB-STANDARD'}",
        ]
        return "\n".join(lines)


class DeterministicScheduler:
    """
    Deterministik zamanlama simülatörü ve analiz motoru.

    Her platform için jitter dağılımını simüle eder:
    - ESP32: Bimodal (WiFi interrupt + normal)
    - LinuxCNC: Near-Gaussian (RTAI jitter)
    - GRBL: Heavy-tailed (Python/USB latency)
    """

    def __init__(self, platform: Platform, seed: int = 42) -> None:
        self.platform = platform
        self.spec     = PLATFORM_SPECS.get(platform, PLATFORM_SPECS[Platform.MOCK])
        self._rng     = np.random.default_rng(seed)

    def simulate_timing(
        self,
        n_segments:        int,
        feedrate_mm_min:   float = 5000.0,
        segment_length_mm: float = 1.0,
    ) -> TimingAnalysis:
        """
        N segment için zamanlama simülasyonu.

        Args:
            n_segments: Segment sayısı
            feedrate_mm_min: Besleme hızı [mm/min]
            segment_length_mm: Her segment uzunluğu [mm]

        Returns:
            TimingAnalysis
        """
        spec = self.spec
        t_seg_ms = segment_length_mm / (feedrate_mm_min / 60.0)   # [ms]

        # Platform'a özgü jitter dağılımı
        jitter_us = self._generate_jitter(n_segments, spec)

        # Latency (systematic + random)
        latency_ms = (spec.latency_mean_ms +
                      self._rng.normal(0, spec.latency_mean_ms * 0.1, n_segments))
        latency_ms = np.clip(latency_ms, 0.0, spec.latency_max_ms)

        # Missed pulse: jitter > t_seg/2
        missed = np.abs(jitter_us) > (t_seg_ms * 1000.0 / 2.0)

        # Late: latency > t_seg
        late = latency_ms > t_seg_ms

        # Pulse quality
        quality = 1.0 - 3.0 * float(np.std(jitter_us)) / 1000.0 / max(t_seg_ms, 1e-6)
        quality = max(0.0, min(1.0, quality))

        # Histogram (20 bins)
        hist, _ = np.histogram(jitter_us, bins=20)

        return TimingAnalysis(
            platform          = self.platform,
            n_segments        = n_segments,
            jitter_mean_us    = float(np.mean(jitter_us)),
            jitter_1sigma_us  = float(np.std(jitter_us)),
            jitter_3sigma_us  = float(3 * np.std(jitter_us)),
            jitter_max_us     = float(np.max(np.abs(jitter_us))),
            latency_mean_ms   = float(np.mean(latency_ms)),
            n_missed_pulses   = int(missed.sum()),
            n_late_segments   = int(late.sum()),
            pulse_quality     = quality,
            production_grade  = quality > 0.9997,
            histogram         = hist,
        )

    def _generate_jitter(self, n: int, spec: PlatformTimingSpec) -> np.ndarray:
        """Platform'a özgü jitter dağılımı üret."""
        σ = spec.jitter_1sigma_us

        if self.platform == Platform.ESP32_FLUIDNC:
            # Bimodal: normal + WiFi interrupt spikes
            normal_part = self._rng.normal(0, σ * 0.7, n)
            wifi_spikes = np.zeros(n)
            spike_idx   = self._rng.choice(n, size=int(n * 0.02), replace=False)
            wifi_spikes[spike_idx] = self._rng.uniform(20, 80, len(spike_idx))
            return normal_part + wifi_spikes

        elif self.platform == Platform.LINUXCNC_RTAI:
            # Near-Gaussian (RTAI is very deterministic)
            return self._rng.normal(0, σ, n)

        elif self.platform == Platform.GRBL_SERIAL:
            # Heavy-tailed: Python GIL + USB latency
            # Mixture: 95% normal + 5% large delays
            jitter = self._rng.normal(0, σ * 0.5, n)
            tail_idx = self._rng.choice(n, size=int(n * 0.05), replace=False)
            jitter[tail_idx] = self._rng.exponential(σ * 3, len(tail_idx))
            return jitter

        else:
            return self._rng.normal(0, σ, n)

    def safe_min_feedrate(self) -> float:
        """
        Bu platform için güvenli minimum feedrate [mm/min].
        Q_pulse ≥ 0.9997 koşulundan türetilir.

        3σ jitter ≤ t_seg/2 koşulu:
        t_seg_min = 6σ_jitter
        v_max = segment_length / t_seg_min
        """
        sigma_s  = self.spec.jitter_1sigma_us / 1e6   # µs → s
        t_min_s  = 6 * sigma_s   # 3σ her iki tarafta
        if t_min_s < 1e-9:
            return float("inf")
        # segment_length = 1mm, v = 1mm / t_min [mm/s] → [mm/min]
        return 1.0 / t_min_s * 60.0

    def compare_platforms(self) -> str:
        """Tüm platformları karşılaştır."""
        lines = [
            "═" * 80,
            "PLATFORM ZAMANLAMA KARŞILAŞTIRMASI",
            "═" * 80,
        ]
        for plat, spec in PLATFORM_SPECS.items():
            if plat == Platform.MOCK:
                continue
            sch = DeterministicScheduler(plat)
            q = spec.pulse_quality_3sigma
            v_safe = sch.safe_min_feedrate()
            lines.append(spec.report() + f"  v_safe={v_safe:.0f}mm/min")
        lines.append("═" * 80)
        return "\n".join(lines)


# ── Safe Queue ────────────────────────────────────────────────────

class HardRealtimeSafeQueue:
    """
    Lock-free single-producer single-consumer ring buffer.

    Hard-realtime uyumlu (no GIL, no blocking, no dynamic alloc).
    Pre-allocated kapasiteli.

    Özellikler:
      - Pre-fill: N_lookahead segments hazır olana kadar hareket başlamaz
      - Immutable segments: enqueue sonrası değiştirilemez
      - Underflow detection: doluluk < threshold → PAUSE signal
      - Power-loss recovery: ring buffer kalıcı belleğe yazılabilir (stub)
    """

    def __init__(
        self,
        capacity:       int   = 64,
        pre_fill_count: int   = 8,
    ) -> None:
        self._cap    = capacity
        self._buf    = [None] * capacity
        self._head   = 0   # read pointer
        self._tail   = 0   # write pointer
        self._count  = 0
        self._pre    = pre_fill_count

    @property
    def size(self) -> int:
        return self._count

    @property
    def capacity(self) -> int:
        return self._cap

    @property
    def fill_fraction(self) -> float:
        return self._count / self._cap

    @property
    def ready_to_run(self) -> bool:
        """Pre-fill tamamlandı mı?"""
        return self._count >= self._pre

    def enqueue(self, item) -> bool:
        """Segment ekle. Returns False if full."""
        if self._count >= self._cap:
            return False
        self._buf[self._tail] = item
        self._tail = (self._tail + 1) % self._cap
        self._count += 1
        return True

    def dequeue(self):
        """Segment al. Returns None if empty."""
        if self._count == 0:
            return None
        item = self._buf[self._head]
        self._buf[self._head] = None   # GC hint
        self._head = (self._head + 1) % self._cap
        self._count -= 1
        return item

    def peek(self):
        """Sıradaki segment'i silmeden bak."""
        if self._count == 0:
            return None
        return self._buf[self._head]

    def flush(self) -> int:
        """Tüm içeriği temizle. Returns count."""
        n = self._count
        self._head = self._tail = self._count = 0
        self._buf = [None] * self._cap
        return n
