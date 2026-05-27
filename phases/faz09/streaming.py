"""
streaming.py — Real-time G-code Streaming Mimarisi
====================================================
İki çalışma modu:
  1. OFFLINE  — NC dosyaya yaz (Faz 5, mevcut)
  2. LIVE     — SegmentBuffer → RealtimeStreamer → ControllerInterface

SegmentBuffer:
  Thread-safe ring buffer. Producer (planner) ve Consumer (streamer)
  farklı thread'larda çalışır.

  Kapasitesi: N_max segment
  Fill level:  fill_level = len/capacity ∈ [0,1]
  Hedef:       fill_level ∈ [0.3, 0.7]
  Underflow:   fill_level < underflow_threshold → PAUSE signal
  Overflow:    fill_level > overflow_threshold → producer slow-down

Timing modeli:
  Her segment için beklenen süre:
    t_seg = |ΔX| / (F/60)   [s]
  Segment üretim hızı (planner): tipik > 100 seg/s → problem yok
  Segment tüketim hızı (GRBL): ≈ 1000 seg/s (parsing) ama makine
    daha yavaş işler → controller iç buffer doluluk önemli.

  Timing drift:
    Planner t=0: segment üretildi
    Streamer t=Δt1: buffer'dan alındı (network/serial gecikme)
    Controller t=Δt2: işlenmeye başlandı
    Machine t=Δt3: hareket tamamlandı

    Drift = (Δt1+Δt2+Δt3) - t_seg_beklenen
    Birikim → fiber gerilme sapması

ESP32/FluidNC vs Mach3 farkları:
  GRBL/FluidNC:
    - Serial ASCII: her satır "ok" yanıtı bekler
    - Flow control: ok tabanlı (send-response protocol)
    - Buffer: 127 byte RX buffer ≈ 15 kısa komut
    - WebSocket (FluidNC): burst gönderim mümkün, ok senkronizasyonu
    - Timing: serial latency ~1-5ms/segment

  Mach3:
    - Lookahead buffer: 50-100 satır önceden plan yapar
    - DLL interface: program içinde çalışır, kernel mode
    - Timing: ~1ms/segment (very low latency)
    - Real-time feedback: OEM API ile DRO okuma

Streaming stratejisi:
  GRBL: "ok-flow" — her ok alındıkça bir sonraki gönder
  Mach3: "burst" — 50 satır blok gönder, callback bekle
  Mock: "instant" — her şey anında kabul edilir
"""

from __future__ import annotations

import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Iterator, List, Optional

from hal import ControllerInterface, MachineAlarm, MachineState


# ── Buffer Events ────────────────────────────────────────────────

class StreamEvent(Enum):
    SEGMENT_SENT     = auto()
    BUFFER_UNDERFLOW = auto()
    BUFFER_OVERFLOW  = auto()
    PAUSE_REQUESTED  = auto()
    RESUME_REQUESTED = auto()
    ESTOP            = auto()
    STREAM_COMPLETE  = auto()
    ERROR            = auto()


@dataclass(slots=True)
class StreamSegment:
    """Buffer'daki tek segment."""
    gcode:        str
    index:        int        # Global segment indeksi
    t_planned:    float      # Beklenen hareket süresi [s]
    t_queued:     float      # Buffer'a eklenme zamanı
    pass_index:   int
    is_pause_point:bool = False  # M0 öncesi → fiber cut


@dataclass(slots=True)
class StreamStats:
    """Streaming istatistikleri."""
    segments_sent:      int   = 0
    segments_dropped:   int   = 0
    underflow_events:   int   = 0
    overflow_events:    int   = 0
    pauses:             int   = 0
    mean_latency_ms:    float = 0.0
    max_latency_ms:     float = 0.0
    timing_drift_ms:    float = 0.0
    total_time_s:       float = 0.0

    def report(self) -> str:
        return (
            f"  Streaming İstatistikleri:\n"
            f"    Gönderilen:    {self.segments_sent}\n"
            f"    Düşürülen:     {self.segments_dropped}\n"
            f"    Underflow:     {self.underflow_events}\n"
            f"    Overflow:      {self.overflow_events}\n"
            f"    Pause sayısı:  {self.pauses}\n"
            f"    Ort. gecikme:  {self.mean_latency_ms:.2f} ms\n"
            f"    Max gecikme:   {self.max_latency_ms:.2f} ms\n"
            f"    Timing drift:  {self.timing_drift_ms:.2f} ms\n"
            f"    Toplam süre:   {self.total_time_s:.1f} s\n"
        )


# ── Segment Buffer ───────────────────────────────────────────────

class SegmentBuffer:
    """
    Thread-safe segment ring buffer.

    Producer: plan() → put()
    Consumer: streamer → get()
    Monitor:  safety → fill_level, peek()

    Underflow koruması:
      fill_level < 0.15 → producer'a "hızlan" sinyali
      fill_level < 0.05 → pause sinyali (streamer bekle)
      fill_level > 0.90 → producer'a "yavaşla" sinyali
    """

    def __init__(
        self,
        capacity:            int   = 256,
        underflow_threshold: float = 0.15,
        overflow_threshold:  float = 0.85,
        pause_threshold:     float = 0.05,
    ) -> None:
        self._q         = deque(maxlen=capacity)
        self._capacity  = capacity
        self._lock      = threading.Lock()
        self._not_empty = threading.Event()
        self._not_full  = threading.Event()
        self._not_full.set()
        self._underflow_thr = underflow_threshold
        self._overflow_thr  = overflow_threshold
        self._pause_thr     = pause_threshold
        self._closed        = False
        self._stats         = StreamStats()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def fill_level(self) -> float:
        with self._lock:
            return len(self._q) / self._capacity

    @property
    def is_empty(self) -> bool:
        with self._lock:
            return len(self._q) == 0

    @property
    def is_full(self) -> bool:
        with self._lock:
            return len(self._q) >= self._capacity

    def put(
        self,
        segment: StreamSegment,
        timeout: float = 1.0,
    ) -> bool:
        """
        Segment ekle. Buffer doluysa timeout kadar bekle.
        Returns False if timeout or closed.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if len(self._q) < self._capacity:
                    self._q.append(segment)
                    self._not_empty.set()
                    fill = len(self._q) / self._capacity
                    if fill > self._overflow_thr:
                        self._stats.overflow_events += 1
                    return True
            time.sleep(0.001)
        self._stats.segments_dropped += 1
        return False

    def get(
        self,
        timeout: float = 0.1,
    ) -> Optional[StreamSegment]:
        """
        Segment al. Boşsa timeout kadar bekle.
        Returns None if timeout, empty, or closed.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._q:
                    seg = self._q.popleft()
                    if not self._q:
                        self._not_empty.clear()
                    fill = len(self._q) / self._capacity
                    if fill < self._underflow_thr:
                        self._stats.underflow_events += 1
                    return seg
            time.sleep(0.001)
        return None

    def peek_fill(self) -> float:
        """Lock-free fill level approximation."""
        return len(self._q) / self._capacity

    def should_pause(self) -> bool:
        """Underflow kritik seviyede mi?"""
        return self.fill_level < self._pause_thr

    def should_slow_producer(self) -> bool:
        """Overflow riski var mı?"""
        return self.fill_level > self._overflow_thr

    def close(self) -> None:
        """Buffer'ı kapat (üretim bitti)."""
        self._closed = True
        self._not_empty.set()  # Bekleyen consumer'ı uyandır

    @property
    def is_closed(self) -> bool:
        return self._closed

    def flush(self) -> int:
        """Tüm içeriği temizle. Returns count cleared."""
        with self._lock:
            n = len(self._q)
            self._q.clear()
            self._not_empty.clear()
            return n


# ── Realtime Streamer ────────────────────────────────────────────

class RealtimeStreamer:
    """
    SegmentBuffer → ControllerInterface canlı streaming motoru.

    Çalışma modu:
      - Ayrı thread'da çalışır
      - Controller protokolüne göre akış kontrolü sağlar
      - Underflow durumunda geçici pause
      - Buffer fill level monitoring
      - Timing drift ölçümü

    GRBL ok-flow protokolü:
      1. Buffer'dan segment al
      2. Controller'a gönder
      3. "ok" bekle (≤ timeout)
      4. fill_level kontrol
      5. Tekrar

    Mach3 burst protokolü:
      1. N segment biriktir (50)
      2. Toplu gönder
      3. Lookahead buffer ack bekle
    """

    def __init__(
        self,
        buffer:       SegmentBuffer,
        controller:   ControllerInterface,
        on_event:     Optional[Callable[[StreamEvent, dict], None]] = None,
        underflow_pause_s: float = 0.5,
        poll_interval_s:   float = 0.005,
    ) -> None:
        self._buffer    = buffer
        self._ctrl      = controller
        self._on_event  = on_event or (lambda e, d: None)
        self._paused    = False
        self._stop_flag = threading.Event()
        self._thread:   Optional[threading.Thread] = None
        self._stats     = StreamStats()
        self._underflow_pause = underflow_pause_s
        self._poll_interval   = poll_interval_s
        self._latencies: List[float] = []

    def start(self) -> None:
        """Streaming thread'i başlat."""
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._stream_loop, name="RealtimeStreamer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Thread'i durdur."""
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def pause(self) -> None:
        self._paused = True
        self._ctrl.pause()
        self._stats.pauses += 1
        self._on_event(StreamEvent.PAUSE_REQUESTED, {})

    def resume(self) -> None:
        self._paused = False
        self._ctrl.resume()
        self._on_event(StreamEvent.RESUME_REQUESTED, {})

    def emergency_stop(self) -> None:
        self._stop_flag.set()
        self._ctrl.emergency_stop()
        self._buffer.flush()
        self._on_event(StreamEvent.ESTOP, {})

    @property
    def stats(self) -> StreamStats:
        return self._stats

    # ── Main streaming loop ──────────────────────────────────────

    def _stream_loop(self) -> None:
        """
        Main streaming loop — ayrı thread.

        Timing drift hesabı:
          t_expected: planlanan segment süresi
          t_actual:   segment gönderiminden bir sonrakine kadar geçen süre
          drift = t_actual - t_expected → birikimli → timing_drift_ms
        """
        t_loop_start = time.monotonic()
        cumulative_drift = 0.0

        while not self._stop_flag.is_set():
            # ── Buffer boşsa bekle ───────────────────────────────
            if self._buffer.is_empty and self._buffer.is_closed:
                self._on_event(StreamEvent.STREAM_COMPLETE, {
                    "segments_sent": self._stats.segments_sent})
                break

            # ── Underflow kontrolü ───────────────────────────────
            if self._buffer.should_pause():
                self._on_event(StreamEvent.BUFFER_UNDERFLOW, {
                    "fill": self._buffer.fill_level})
                self._stats.underflow_events += 1
                time.sleep(self._underflow_pause)
                continue

            # ── Pause bekle ──────────────────────────────────────
            if self._paused:
                time.sleep(0.05)
                continue

            # ── Segment al ve gönder ─────────────────────────────
            seg = self._buffer.get(timeout=0.05)
            if seg is None:
                continue

            t_send_start = time.monotonic()
            success = self._ctrl.stream_segment(seg.gcode)
            t_send_end = time.monotonic()

            if not success:
                self._stats.segments_dropped += 1
                self._on_event(StreamEvent.ERROR, {
                    "segment": seg.index, "gcode": seg.gcode})
                # Retry after brief pause
                time.sleep(0.01)
                self._buffer.put(seg)  # Geri koy
                continue

            # ── Latency tracking ─────────────────────────────────
            latency_ms = (t_send_end - t_send_start) * 1000.0
            q_latency  = (t_send_start - seg.t_queued) * 1000.0
            self._latencies.append(q_latency)
            if len(self._latencies) > 200:
                self._latencies.pop(0)

            # ── Timing drift ─────────────────────────────────────
            actual_interval = t_send_end - t_loop_start
            expected_interval = seg.t_planned
            drift = actual_interval - expected_interval
            cumulative_drift += drift

            # ── Stats update ─────────────────────────────────────
            self._stats.segments_sent += 1
            if self._latencies:
                self._stats.mean_latency_ms = sum(self._latencies) / len(self._latencies)
                self._stats.max_latency_ms  = max(self._latencies)
            self._stats.timing_drift_ms = cumulative_drift * 1000.0

            # ── Fiber kesme noktası ──────────────────────────────
            if seg.is_pause_point:
                self.pause()

            self._on_event(StreamEvent.SEGMENT_SENT, {
                "index": seg.index, "fill": self._buffer.fill_level,
                "latency_ms": latency_ms})

            t_loop_start = t_send_end
            time.sleep(self._poll_interval)

        self._stats.total_time_s = time.monotonic() - t_loop_start


# ── Offline Export (Faz 5 uyumluluğu) ───────────────────────────

class OfflineExporter:
    """
    Geriye dönük uyumluluk: segment listesini NC dosyaya yaz.
    """

    @staticmethod
    def export(
        segments: List[StreamSegment],
        filepath: str,
        controller_name: str = "GRBL",
    ) -> int:
        """Returns number of lines written."""
        lines = [
            f"; Offline NC Export — {controller_name}",
            f"; Segment count: {len(segments)}",
        ]
        for seg in segments:
            if seg.is_pause_point:
                lines.append("M0 ; Fiber cut point")
            lines.append(seg.gcode)
        lines.append("M30")
        with open(filepath, "w") as f:
            f.write("\n".join(lines))
        return len(lines)


# ── Producer helper ──────────────────────────────────────────────

def gcode_to_segments(
    gcode_lines:  List[str],
    feedrate_mm_min: float = 800.0,
    step_z_mm:    float = 10.0,
) -> Iterator[StreamSegment]:
    """
    G-code satırlarını StreamSegment'e dönüştür.

    t_planned: Her segment için beklenen süre.
    """
    import re
    idx = 0
    for i, line in enumerate(gcode_lines):
        line = line.strip()
        if not line or line.startswith(";") or line.startswith("("):
            continue
        # t_planned: |ΔX| / (F/60)
        x_m = re.search(r'X([-\d.]+)', line.upper())
        f_m = re.search(r'F([\d.]+)', line.upper())
        f   = float(f_m.group(1)) if f_m else feedrate_mm_min
        t_p = step_z_mm / (f / 60.0) if f > 0 else 0.0
        is_pause = "M0" in line.upper() or "M1" in line.upper()

        yield StreamSegment(
            gcode        = line,
            index        = idx,
            t_planned    = t_p,
            t_queued     = time.monotonic(),
            pass_index   = 0,
            is_pause_point = is_pause,
        )
        idx += 1
