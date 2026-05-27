"""
workers/replay_worker.py — Telemetry Replay Engine (QThread)
==============================================================
Loads a recorded session and plays it back with controllable speed.
Emits same signals as TelemetryWorker so panels work identically in
LIVE or REPLAY mode.

Modes:
  REALTIME (1x): original timing reconstructed from ts_us
  FAST (10x):    accelerated for analytics scrub
  STEP:          frame-by-frame
  PAUSE:         no advance
"""
from __future__ import annotations
import time
from enum import Enum
from typing import List, Optional

from PySide6.QtCore import QThread, Signal, Slot, QObject

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from backend.hardware.esp32_link import TelemetryFrame
from backend.persistence.telemetry_db import TelemetryDB


class ReplayMode(Enum):
    REALTIME = "realtime"
    FAST     = "fast"          # ~10x or as set
    STEP     = "step"
    PAUSE    = "pause"
    SEEK     = "seek"          # one-shot seek to position


class ReplayWorker(QThread):
    """
    Replay session frames as if they were live.

    Signals:
      frameBatch(list)     — coalesced batch
      latestFrame(object)  — last frame
      positionChanged(int) — current frame index
      progressUpdated(float) — fraction [0,1]
      finished()           — playback ended
      loaded(int)          — total frame count after load
    """
    frameBatch = Signal(list)
    latestFrame = Signal(object)
    positionChanged = Signal(int)
    progressUpdated = Signal(float)
    finished = Signal()
    loaded = Signal(int)

    COALESCE_MS = 33

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._frames: List[TelemetryFrame] = []
        self._idx = 0
        self._mode = ReplayMode.PAUSE
        self._speed = 1.0
        self._stop = False
        self._seek_target: Optional[int] = None

    def load_session(self, telemetry_db: TelemetryDB, session_id: str) -> int:
        """Load session frames synchronously (call before start)."""
        self._frames = telemetry_db.load_session(session_id)
        self._idx = 0
        self.loaded.emit(len(self._frames))
        return len(self._frames)

    def load_frames(self, frames: List[TelemetryFrame]) -> None:
        """Direct frame injection (for tests)."""
        self._frames = list(frames)
        self._idx = 0
        self.loaded.emit(len(self._frames))

    @Slot(str)
    def set_mode(self, mode_str: str):
        try:
            self._mode = ReplayMode(mode_str)
        except ValueError:
            pass

    @Slot(float)
    def set_speed(self, speed: float):
        self._speed = max(0.1, min(100.0, speed))

    @Slot(int)
    def seek(self, idx: int):
        self._seek_target = max(0, min(len(self._frames)-1, idx))

    @Slot()
    def stop(self):
        self._stop = True

    @property
    def n_frames(self) -> int: return len(self._frames)
    @property
    def position(self) -> int: return self._idx

    def run(self):
        batch: List[TelemetryFrame] = []
        last_emit_ms = time.monotonic() * 1000
        last_prog_ms = last_emit_ms

        while not self._stop:
            # Handle seek
            if self._seek_target is not None:
                self._idx = self._seek_target
                self._seek_target = None
                batch.clear()
                self.positionChanged.emit(self._idx)

            if self._mode == ReplayMode.PAUSE:
                time.sleep(0.05)
                continue
            if self._mode == ReplayMode.STEP:
                # One frame, then pause
                if self._idx < len(self._frames):
                    f = self._frames[self._idx]
                    self._idx += 1
                    self.latestFrame.emit(f)
                    self.frameBatch.emit([f])
                    self.positionChanged.emit(self._idx)
                self._mode = ReplayMode.PAUSE
                continue
            if self._idx >= len(self._frames):
                # Reached end
                if batch:
                    self.frameBatch.emit(list(batch))
                    self.latestFrame.emit(batch[-1])
                    batch.clear()
                self.finished.emit()
                self._mode = ReplayMode.PAUSE
                continue

            # Advance one frame
            f = self._frames[self._idx]
            self._idx += 1
            batch.append(f)

            # Timing
            if self._mode == ReplayMode.REALTIME and self._idx < len(self._frames):
                next_f = self._frames[self._idx]
                dt_us = next_f.ts_us - f.ts_us
                if dt_us > 0:
                    sleep_s = (dt_us / 1e6) / self._speed
                    if sleep_s > 0.001:
                        time.sleep(min(sleep_s, 0.1))   # cap so seek/stop responsive
            elif self._mode == ReplayMode.FAST:
                # Minimum sleep so UI can repaint
                time.sleep(0.0005)

            # Coalesce emit
            now_ms = time.monotonic() * 1000
            if now_ms - last_emit_ms >= self.COALESCE_MS:
                if batch:
                    self.frameBatch.emit(list(batch))
                    self.latestFrame.emit(batch[-1])
                    batch.clear()
                last_emit_ms = now_ms
            if now_ms - last_prog_ms >= 100:
                self.progressUpdated.emit(
                    self._idx / max(len(self._frames), 1))
                self.positionChanged.emit(self._idx)
                last_prog_ms = now_ms

        # Flush
        if batch:
            self.frameBatch.emit(list(batch))
            self.latestFrame.emit(batch[-1])
