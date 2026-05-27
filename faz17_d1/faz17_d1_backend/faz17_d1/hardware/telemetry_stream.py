"""
hardware/telemetry_stream.py — Lock-free Pub/Sub Pipeline
============================================================
SPSC ring buffer + multi-subscriber dispatch.

Architecture:
  ESP32Link → TelemetryStream.feed() → LockFreeRing (10s @ 1kHz)
                                       → per-subscriber bounded queue
                                       → callbacks fire OUTSIDE lock
"""
from __future__ import annotations
import queue, threading, time
from collections import deque
from dataclasses import dataclass
from typing import Callable, List, Optional
from .esp32_link import TelemetryFrame


class LockFreeRing:
    """SPSC bounded ring (single producer, single consumer)."""
    def __init__(self, capacity: int = 10_000):
        self._buf: List[Optional[TelemetryFrame]] = [None] * capacity
        self._cap = capacity
        self._head = 0   # consumer reads from here
        self._tail = 0   # producer writes to here
        self._count = 0
        self._lock = threading.Lock()   # ring metadata only, no callbacks

    def push(self, frame: TelemetryFrame) -> bool:
        with self._lock:
            self._buf[self._tail] = frame
            self._tail = (self._tail + 1) % self._cap
            if self._count < self._cap:
                self._count += 1
            else:
                self._head = (self._head + 1) % self._cap
        return True

    def pop(self) -> Optional[TelemetryFrame]:
        with self._lock:
            if self._count == 0:
                return None
            f = self._buf[self._head]
            self._buf[self._head] = None
            self._head = (self._head + 1) % self._cap
            self._count -= 1
        return f

    def snapshot(self, n: int = 100) -> List[TelemetryFrame]:
        """Read recent n frames without removing."""
        out: List[TelemetryFrame] = []
        with self._lock:
            n = min(n, self._count)
            for i in range(n):
                idx = (self._tail - n + i) % self._cap
                f = self._buf[idx]
                if f is not None:
                    out.append(f)
        return out

    @property
    def count(self) -> int: return self._count
    @property
    def capacity(self) -> int: return self._cap


@dataclass(slots=True)
class StreamStats:
    n_received:     int = 0
    n_dispatched:   int = 0
    n_dropped:      int = 0
    n_subscribers:  int = 0
    last_seq:       int = -1
    n_seq_gaps:     int = 0


class TelemetryStream:
    """
    Pub/sub telemetry stream.
    Each subscriber gets own bounded queue; slow subscribers are dropped
    (per-queue back-pressure, never blocks producer).
    """
    SUBSCRIBER_QUEUE_SIZE = 500

    def __init__(self, ring_capacity: int = 10_000):
        self._ring = LockFreeRing(ring_capacity)
        self._subs: List[queue.Queue] = []
        self._lock = threading.Lock()
        self._stats = StreamStats()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self.SUBSCRIBER_QUEUE_SIZE)
        with self._lock:
            self._subs.append(q)
            self._stats.n_subscribers = len(self._subs)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)
                self._stats.n_subscribers = len(self._subs)

    def feed(self, frame: TelemetryFrame) -> None:
        """Producer: called from ESP32 link thread. Non-blocking."""
        self._ring.push(frame)
        with self._lock:
            self._stats.n_received += 1
            if self._stats.last_seq >= 0:
                expected = (self._stats.last_seq + 1) & 0xFFFF
                if frame.seq != expected:
                    self._stats.n_seq_gaps += 1
            self._stats.last_seq = frame.seq
            subs_snapshot = list(self._subs)
        # OUTSIDE lock — dispatch to subscribers
        for q in subs_snapshot:
            try:
                q.put_nowait(frame)
                self._stats.n_dispatched += 1
            except queue.Full:
                self._stats.n_dropped += 1

    def snapshot(self, n: int = 100) -> List[TelemetryFrame]:
        return self._ring.snapshot(n)

    @property
    def stats(self) -> StreamStats:
        with self._lock:
            return StreamStats(
                n_received=self._stats.n_received,
                n_dispatched=self._stats.n_dispatched,
                n_dropped=self._stats.n_dropped,
                n_subscribers=self._stats.n_subscribers,
                last_seq=self._stats.last_seq,
                n_seq_gaps=self._stats.n_seq_gaps)
