"""
workers/telemetry_worker.py — Backend → UI Bridge (QThread)
==============================================================
Subscribes to backend TelemetryStream, coalesces 1kHz → ~30Hz UI rate,
emits Qt signals (queued connection) to UI thread.

CRITICAL: UI thread NEVER blocks. All blocking calls (queue.get, lock acquire)
happen INSIDE this worker's QThread.run().
"""
from __future__ import annotations
import queue
import time
from collections import deque
from typing import Optional, List

from PySide6.QtCore import QThread, Signal, Slot, Qt, QObject

# Backend imports — use the L0-L2 stack from D1
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from backend.hardware.esp32_link import ESP32LinkBase, MockESP32Link, TelemetryFrame
from backend.hardware.telemetry_stream import TelemetryStream
from backend.core.safety_controller import SafetyController, SafetyEvent, SafetyLevel
from backend.core.motion_controller import MotionController, MotionStatus, MotionState
from backend.core.digital_twin import DigitalTwin, TwinParams


# Coalesce config — UI gets at most this rate (Hz)
UI_REFRESH_HZ = 30
COALESCE_MS = int(1000 / UI_REFRESH_HZ)   # ~33ms


class TelemetryWorker(QThread):
    """
    Subscribes to backend telemetry. Emits coalesced UI updates.

    Signals (queued automatically because QThread.affinity differs from receiver):
      frameBatch(list)       — list of TelemetryFrame received in last coalesce window
      latestFrame(object)    — last frame in batch (for instant display)
      statsUpdated(dict)     — throughput, drops, gaps
      alarmReceived(object)  — SafetyEvent (immediate, not coalesced)
      motionStatusChanged(object)
    """
    frameBatch = Signal(list)
    latestFrame = Signal(object)
    statsUpdated = Signal(dict)
    alarmReceived = Signal(object)
    motionStatusChanged = Signal(object)
    connectionStateChanged = Signal(int)  # ConnectionState.value

    def __init__(self,
                 link: ESP32LinkBase,
                 stream: TelemetryStream,
                 safety: SafetyController,
                 motion: MotionController,
                 twin: Optional[DigitalTwin] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._link = link
        self._stream = stream
        self._safety = safety
        self._motion = motion
        self._twin = twin
        self._stop = False
        # Internal: subscriber queue from stream
        self._sub_q = stream.subscribe()
        # Coalesce buffer
        self._batch: List[TelemetryFrame] = []
        self._last_emit_ms = 0
        self._last_stats_emit_ms = 0
        # Register safety callback (fires from safety thread, NOT UI)
        safety.register_callback(self._on_safety_event)
        # Register motion listener
        motion.add_listener(self._on_motion_status)
        # Stats
        self._frames_total = 0
        self._frames_emitted = 0

    @Slot()
    def stop(self):
        self._stop = True

    def _on_safety_event(self, ev: SafetyEvent):
        """Called from safety thread — emit Qt signal (queued, thread-safe)."""
        # Qt automatically queues this since receivers are in main thread
        self.alarmReceived.emit(ev)

    def _on_motion_status(self, status: MotionStatus):
        """Called from motion thread or telemetry thread."""
        self.motionStatusChanged.emit(status)

    def run(self):
        """QThread entry point. Runs until stop()."""
        last_link_state = -1
        last_stats_emit = time.monotonic() * 1000
        while not self._stop:
            try:
                # Block on subscriber queue with short timeout
                # so we can check _stop flag periodically
                frame = self._sub_q.get(timeout=0.05)
            except queue.Empty:
                # Check if it's time to emit stats
                now_ms = time.monotonic() * 1000
                if now_ms - last_stats_emit > 500:
                    self._emit_stats()
                    last_stats_emit = now_ms
                # Check link state change
                cur_state = int(self._link.state.value)
                if cur_state != last_link_state:
                    self.connectionStateChanged.emit(cur_state)
                    last_link_state = cur_state
                continue

            self._frames_total += 1
            self._batch.append(frame)

            # Feed safety controller (this is on worker thread, not UI)
            self._safety.update_frame(frame)
            # Feed motion controller for state update
            self._motion.update_from_telemetry(frame)
            # Feed digital twin
            if self._twin is not None:
                self._twin.add_real_sample(frame)

            # Coalesce: emit batch every COALESCE_MS
            now_ms = time.monotonic() * 1000
            if now_ms - self._last_emit_ms >= COALESCE_MS:
                if self._batch:
                    # Emit copies via queued signal
                    batch_copy = list(self._batch)
                    self._batch.clear()
                    self.frameBatch.emit(batch_copy)
                    self.latestFrame.emit(batch_copy[-1])
                    self._frames_emitted += len(batch_copy)
                    self._last_emit_ms = now_ms

            # Stats emit
            if now_ms - last_stats_emit > 500:
                self._emit_stats()
                last_stats_emit = now_ms

        # Flush remaining batch on shutdown
        if self._batch:
            self.frameBatch.emit(list(self._batch))
            self._batch.clear()
        # Unsubscribe
        try:
            self._stream.unsubscribe(self._sub_q)
        except Exception:
            pass

    def _emit_stats(self):
        s = self._stream.stats
        self.statsUpdated.emit({
            "received":    s.n_received,
            "dispatched":  s.n_dispatched,
            "dropped":     s.n_dropped,
            "seq_gaps":    s.n_seq_gaps,
            "subscribers": s.n_subscribers,
            "ui_emitted":  self._frames_emitted,
            "ui_total":    self._frames_total,
        })

    @property
    def frames_emitted(self) -> int: return self._frames_emitted
    @property
    def frames_total(self) -> int: return self._frames_total
