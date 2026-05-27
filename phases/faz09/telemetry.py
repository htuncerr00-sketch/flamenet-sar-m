"""
telemetry.py — Canlı Telemetri Sistemi
========================================
Gerçek makineden veri toplama, zaman damgalı loglama ve
web dashboard için JSON stream altyapısı.

Bileşenler:
  TelemetryLogger   — CSV + bellek içi kayıt
  LiveComparator    — Digital twin vs gerçek karşılaştırma
  WebSocketEmitter  — JSON stream (port 8765, ileride web dashboard)
  TelemetryDashboard— Terminal tabanlı canlı görünüm

Veri paketi (her 100ms):
  {
    "t":         1234567890.123,  // Unix timestamp
    "x":         145.234,          // Carriage mm
    "a":         2879.34,          // Spindle °
    "rpm":       38.4,
    "vx":        5.23,
    "tension":   15.2,
    "buf_fill":  0.45,
    "twin_x_err":0.003,            // twin vs actual
    "twin_a_err":0.12,
    "quality":   0.94,
    "alerts":    []
  }

WebSocket protokolü (gelecek):
  Server: asyncio + websockets
  Port: 8765
  Client: web tarayıcı / Python script
  Format: newline-delimited JSON
"""

from __future__ import annotations

import csv
import json
import math
import os
import queue
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

from hal import ControllerInterface, MachineState


# ── Telemetry Packet ─────────────────────────────────────────────

@dataclass(slots=True)
class TelemetryPacket:
    """Tek telemetri paketi — tüm anlık ölçümler."""
    t:            float   # Unix timestamp
    x_mm:         float   # Carriage aktüel
    a_deg:        float   # Spindle kümülatif
    rpm:          float
    vx_mm_s:      float
    tension_N:    float
    buf_fill:     float   # [0,1]
    twin_x_err:   float   # Twin vs actual X hatası
    twin_a_err:   float   # Twin vs actual A hatası
    quality:      float   # [0,1] anlık kalite
    is_running:   bool
    alarm:        str     # alarm adı
    segment_idx:  int     # Aktif segment numarası

    def to_json(self) -> str:
        d = {
            "t": round(self.t, 3),
            "x": round(self.x_mm, 4),
            "a": round(self.a_deg, 3),
            "rpm": round(self.rpm, 2),
            "vx": round(self.vx_mm_s, 3),
            "tension": round(self.tension_N, 3),
            "buf": round(self.buf_fill, 3),
            "ex": round(self.twin_x_err, 5),
            "ea": round(self.twin_a_err, 4),
            "q": round(self.quality, 4),
            "run": int(self.is_running),
            "alarm": self.alarm,
            "seg": self.segment_idx,
        }
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_state(
        cls,
        state:        MachineState,
        twin_x_err:   float = 0.0,
        twin_a_err:   float = 0.0,
        quality:      float = 1.0,
        segment_idx:  int   = 0,
    ) -> "TelemetryPacket":
        return cls(
            t           = state.timestamp,
            x_mm        = state.x_actual_mm,
            a_deg       = state.a_actual_deg,
            rpm         = state.rpm,
            vx_mm_s     = state.vx_mm_s,
            tension_N   = state.tension_N,
            buf_fill    = state.buffer_fill,
            twin_x_err  = twin_x_err,
            twin_a_err  = twin_a_err,
            quality     = quality,
            is_running  = state.is_running,
            alarm       = state.alarm.value,
            segment_idx = segment_idx,
        )


# ── Telemetry Logger ────────────────────────────────────────────

class TelemetryLogger:
    """
    Zaman damgalı telemetri kayıt sistemi.

    CSV'ye yazar ve bellek içi ring buffer tutar.
    Thread-safe: logger thread + reader thread ayrı çalışabilir.

    Kullanım:
        logger = TelemetryLogger("/tmp/winding_telemetry.csv")
        logger.start()
        logger.log(packet)
        summary = logger.get_summary()
        logger.stop()
    """

    RING_BUFFER_SIZE = 10000  # Son 10000 paket bellekte

    def __init__(
        self,
        filepath:     str = "/tmp/winding_telemetry.csv",
        flush_every:  int = 50,   # Her 50 pakette CSV'ye flush
    ) -> None:
        self._filepath    = filepath
        self._flush_every = flush_every
        self._ring        = deque(maxlen=self.RING_BUFFER_SIZE)
        self._q:          queue.Queue = queue.Queue(maxsize=1000)
        self._lock        = threading.Lock()
        self._stop_flag   = threading.Event()
        self._thread:     Optional[threading.Thread] = None
        self._file        = None
        self._writer      = None
        self._count       = 0

    def start(self) -> None:
        """Logger thread'i başlat."""
        os.makedirs(os.path.dirname(self._filepath) or ".", exist_ok=True)
        self._file   = open(self._filepath, "w", newline="")
        self._writer = csv.writer(self._file)
        self._writer.writerow([
            "t", "x_mm", "a_deg", "rpm", "vx_mm_s",
            "tension_N", "buf_fill", "twin_x_err", "twin_a_err",
            "quality", "is_running", "alarm", "segment_idx",
        ])
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._write_loop, name="TelemetryLogger", daemon=True)
        self._thread.start()

    def log(self, packet: TelemetryPacket) -> None:
        """Paket ekle (non-blocking)."""
        with self._lock:
            self._ring.append(packet)
        try:
            self._q.put_nowait(packet)
        except queue.Full:
            pass  # Drop if queue full

    def stop(self) -> None:
        """Logger'ı durdur."""
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._file:
            self._file.flush()
            self._file.close()

    def recent(self, n: int = 100) -> List[TelemetryPacket]:
        """Son n paketi döndür."""
        with self._lock:
            return list(self._ring)[-n:]

    def get_summary(self) -> dict:
        """İstatistik özeti."""
        with self._lock:
            pkts = list(self._ring)
        if not pkts:
            return {}
        tensions = [p.tension_N for p in pkts]
        qualities = [p.quality for p in pkts]
        x_errs   = [abs(p.twin_x_err) for p in pkts]
        return {
            "n_packets":     len(pkts),
            "duration_s":    pkts[-1].t - pkts[0].t if len(pkts) > 1 else 0.0,
            "mean_tension":  sum(tensions) / len(tensions),
            "max_tension":   max(tensions),
            "min_tension":   min(tensions),
            "mean_quality":  sum(qualities) / len(qualities),
            "rms_x_error_mm":math.sqrt(sum(e**2 for e in x_errs) / len(x_errs)),
            "alarm_count":   sum(1 for p in pkts if p.alarm != "none"),
        }

    def _write_loop(self) -> None:
        """CSV yazma döngüsü."""
        pending = 0
        while not self._stop_flag.is_set() or not self._q.empty():
            try:
                pkt = self._q.get(timeout=0.05)
                if self._writer:
                    self._writer.writerow([
                        f"{pkt.t:.3f}", f"{pkt.x_mm:.4f}",
                        f"{pkt.a_deg:.3f}", f"{pkt.rpm:.2f}",
                        f"{pkt.vx_mm_s:.3f}", f"{pkt.tension_N:.3f}",
                        f"{pkt.buf_fill:.3f}", f"{pkt.twin_x_err:.6f}",
                        f"{pkt.twin_a_err:.5f}", f"{pkt.quality:.4f}",
                        int(pkt.is_running), pkt.alarm, pkt.segment_idx,
                    ])
                    pending += 1
                    if pending >= self._flush_every:
                        self._file.flush()
                        pending = 0
            except queue.Empty:
                continue


# ── Live Comparator ──────────────────────────────────────────────

class LiveComparator:
    """
    Digital twin vs gerçek makine canlı karşılaştırması.

    Twin durumu: simülasyon segmentlerinden
    Gerçek durum: controller.read_state()
    """

    def __init__(
        self,
        twin_x_arr: Optional[list] = None,
        twin_a_arr: Optional[list] = None,
    ) -> None:
        self._twin_x = twin_x_arr or []
        self._twin_a = twin_a_arr or []
        self._x_errors: deque = deque(maxlen=1000)
        self._a_errors: deque = deque(maxlen=1000)

    def update_twin_data(self, x_arr: list, a_arr: list) -> None:
        """Twin referans verilerini güncelle."""
        self._twin_x = x_arr
        self._twin_a = a_arr

    def compare(
        self,
        state:       MachineState,
        seg_idx:     int,
    ) -> tuple:
        """
        Anlık karşılaştırma.
        Returns: (x_error_mm, a_error_deg, quality)
        """
        x_err = 0.0; a_err = 0.0

        if seg_idx < len(self._twin_x):
            x_err = state.x_actual_mm - self._twin_x[seg_idx]
        if seg_idx < len(self._twin_a):
            a_err = state.a_actual_deg - self._twin_a[seg_idx]

        self._x_errors.append(x_err)
        self._a_errors.append(a_err)

        # Anlık kalite
        q_x = max(0.0, 1.0 - abs(x_err) / 0.5)
        q_a = max(0.0, 1.0 - abs(a_err) / 2.0)
        q   = 0.5 * q_x + 0.5 * q_a
        return x_err, a_err, q

    def rms_errors(self) -> tuple:
        """(rms_x_mm, rms_a_deg)"""
        if not self._x_errors:
            return (0.0, 0.0)
        import numpy as np
        x = np.array(self._x_errors)
        a = np.array(self._a_errors)
        return float(np.sqrt(np.mean(x**2))), float(np.sqrt(np.mean(a**2)))


# ── Terminal Dashboard (lightweight) ─────────────────────────────

class TelemetryDashboard:
    """
    Terminal tabanlı canlı monitoring görünümü.
    Gerçek web dashboard'u için WebSocket'e genişletilir.
    """

    def __init__(
        self,
        logger:      TelemetryLogger,
        comparator:  LiveComparator,
        refresh_s:   float = 1.0,
    ) -> None:
        self._logger  = logger
        self._comp    = comparator
        self._refresh = refresh_s
        self._stop    = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="Dashboard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            recent = self._logger.recent(1)
            if recent:
                p = recent[-1]
                rms_x, rms_a = self._comp.rms_errors()
                self._print_status(p, rms_x, rms_a)
            time.sleep(self._refresh)

    def _print_status(
        self,
        p:     TelemetryPacket,
        rms_x: float,
        rms_a: float,
    ) -> None:
        alarm_icon = "⚡" if p.alarm != "none" else " "
        run_icon   = "▶" if p.is_running else "■"
        q_bar      = "█" * int(p.quality * 10) + "░" * (10 - int(p.quality * 10))
        buf_bar    = "█" * int(p.buf_fill * 10) + "░" * (10 - int(p.buf_fill * 10))
        print(
            f"\r  {alarm_icon}{run_icon} "
            f"X={p.x_mm:7.2f}mm  A={p.a_deg:8.2f}°  "
            f"rpm={p.rpm:5.1f}  T={p.tension_N:5.1f}N  "
            f"buf=[{buf_bar}]{p.buf_fill:.2f}  "
            f"Q=[{q_bar}]{p.quality:.2f}  "
            f"RMS_x={rms_x*1000:.1f}µm  RMS_a={rms_a:.2f}°",
            end="", flush=True,
        )


# ── WebSocket Emitter (Stub — gelecek) ────────────────────────────

class WebSocketEmitter:
    """
    JSON stream via WebSocket (port 8765).

    Gerçek implementasyon için: pip install websockets
    Web dashboard bu stream'i tüketir.

    Protokol:
      ws://localhost:8765
      Her paket: newline-delimited JSON

    Şu an: stub (print to stdout veya dosyaya yazar)
    """

    def __init__(self, port: int = 8765, enabled: bool = False) -> None:
        self.port    = port
        self.enabled = enabled
        self._q:     queue.Queue = queue.Queue(maxsize=100)
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.enabled:
            return
        print(f"    [WS] WebSocket emitter: ws://localhost:{self.port}")
        print("    [WS] 'pip install websockets' ile aktif edilebilir")
        # Gelecekte: asyncio + websockets.serve

    def emit(self, packet: TelemetryPacket) -> None:
        """Paketi WebSocket üzerinden yayınla."""
        if not self.enabled:
            return
        try:
            self._q.put_nowait(packet.to_json())
        except queue.Full:
            pass

    def stop(self) -> None:
        pass
