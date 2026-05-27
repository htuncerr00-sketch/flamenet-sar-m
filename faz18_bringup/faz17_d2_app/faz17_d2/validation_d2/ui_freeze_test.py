"""
validation_d2/ui_freeze_test.py — UI Responsiveness under Signal Flood
==========================================================================
Floods the UI thread with signals at high rate. Measures event-loop
latency (time between QTimer ticks at 100Hz). If UI freezes, gaps grow.

Pass criteria:
  - p99 event loop tick latency < 50ms
  - max < 200ms (no hangs)
  - frameBatch signal handled at sustainable rate

This validates the "UI thread NEVER blocks" requirement.
"""
from __future__ import annotations
import os
import sys
import time
import statistics
from typing import List

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

# sys.path inject so this can be run standalone
_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QObject, QTimer, Signal, QCoreApplication, Qt
from PySide6.QtWidgets import QApplication

from backend.hardware.esp32_link import TelemetryFrame
from app.workers.telemetry_worker import TelemetryWorker
from backend.hardware.esp32_link import MockESP32Link
from backend.hardware.telemetry_stream import TelemetryStream
from backend.core.safety_controller import SafetyController
from backend.core.motion_controller import MotionController


class TickRecorder(QObject):
    """Records QTimer tick intervals. Gaps reveal UI thread blocking."""
    def __init__(self, interval_ms: int = 10):
        super().__init__()
        self._interval = interval_ms
        self._last = 0.0
        self._intervals: List[float] = []
        self._timer = QTimer()
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._last = time.perf_counter()
        self._timer.start(self._interval)

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        now = time.perf_counter()
        gap_ms = (now - self._last) * 1000.0
        self._intervals.append(gap_ms)
        self._last = now

    def report(self) -> dict:
        if not self._intervals:
            return {"n": 0}
        arr = self._intervals
        arr_sorted = sorted(arr)
        n = len(arr_sorted)

        def pct(p: float) -> float:
            return arr_sorted[min(n - 1, int(n * p))]
        return {
            "n":         n,
            "target_ms": float(self._interval),
            "mean_ms":   statistics.mean(arr),
            "p50_ms":    pct(0.50),
            "p90_ms":    pct(0.90),
            "p99_ms":    pct(0.99),
            "p999_ms":   pct(0.999),
            "max_ms":    max(arr),
            "min_ms":    min(arr),
        }


class CountingSink(QObject):
    """Receives frameBatch signals and counts them."""
    def __init__(self):
        super().__init__()
        self.n_signals = 0
        self.n_frames = 0
        self.last_batch_size = 0

    def on_batch(self, batch: list) -> None:
        self.n_signals += 1
        self.n_frames += len(batch)
        self.last_batch_size = len(batch)


def run_ui_freeze_test(duration_s: float = 3.0,
                       target_rate_hz: float = 10000.0) -> dict:
    """
    Run signal flood test.
    Returns dict with event-loop latency stats + signal throughput.
    """
    app = QApplication.instance() or QApplication(sys.argv)

    # Real backend stack so test matches production path
    link = MockESP32Link(seed=42, rate_hz=target_rate_hz)
    stream = TelemetryStream(ring_capacity=20_000)
    safety = SafetyController()
    motion = MotionController(link, safety)
    worker = TelemetryWorker(link, stream, safety, motion)

    sink = CountingSink()
    worker.frameBatch.connect(sink.on_batch)

    # Tick recorder on UI thread
    rec = TickRecorder(interval_ms=10)

    # Bridge: link queue → stream feed (lives in background thread)
    import threading, queue as _q
    link_q = _q.Queue(maxsize=20000)
    link.subscribe(link_q)
    bridge_stop = threading.Event()

    def bridge():
        while not bridge_stop.is_set():
            try:
                f = link_q.get(timeout=0.05)
                stream.feed(f)
            except _q.Empty:
                continue

    th = threading.Thread(target=bridge, daemon=True, name="bridge")

    # Start everything
    link.connect()
    th.start()
    worker.start()
    rec.start()

    # Stop after duration_s
    QTimer.singleShot(int(duration_s * 1000), app.quit)
    app.exec()

    # Tear down
    rec.stop()
    worker.stop()
    worker.wait(2000)
    bridge_stop.set()
    th.join(timeout=1.0)
    link.disconnect()

    rep = rec.report()
    rep["n_signal_emissions"] = sink.n_signals
    rep["n_frames_received"] = sink.n_frames
    rep["frames_per_second"] = sink.n_frames / duration_s if duration_s > 0 else 0
    rep["signals_per_second"] = sink.n_signals / duration_s if duration_s > 0 else 0
    rep["stream_drops"] = stream.stats.n_dropped
    rep["stream_received"] = stream.stats.n_received
    # Pass criteria: p99 < 50ms, max < 200ms — UI thread never freezes
    rep["passed"] = (rep.get("p99_ms", 999) < 50.0 and
                     rep.get("max_ms", 999) < 200.0)
    return rep


if __name__ == "__main__":
    r = run_ui_freeze_test(duration_s=3.0, target_rate_hz=10000.0)
    print("\nUI FREEZE TEST RESULTS")
    print("-" * 60)
    for k, v in r.items():
        if isinstance(v, float):
            print(f"  {k:25s} = {v:10.3f}")
        else:
            print(f"  {k:25s} = {v}")
    print("-" * 60)
    passed = (r.get("p99_ms", 999) < 50 and r.get("max_ms", 999) < 200)
    print(f"  Pass criteria: p99<50ms, max<200ms")
    print(f"  Verdict: {'PASS ✓' if passed else 'FAIL ✗'}")
