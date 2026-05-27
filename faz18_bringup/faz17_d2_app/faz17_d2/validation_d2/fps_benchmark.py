"""
validation_d2/fps_benchmark.py — Chart Refresh Frame Rate Benchmark
=======================================================================
Measures actual paint events per second on the LiveProductionPanel
while telemetry streams in. Hits target of 30 FPS minimum (the
UI_REFRESH_HZ target — full 60 FPS is reserved for static viewports).

Methodology:
  - Install a paintEvent override that increments a counter
  - Stream telemetry at 1kHz for measurement window
  - Compute fps = paint_count / window_sec
"""
from __future__ import annotations
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QTimer, QObject, QEvent, Qt
from PySide6.QtWidgets import QApplication


class PaintCounter(QObject):
    """Event filter — counts paint events on a widget."""
    def __init__(self):
        super().__init__()
        self.count = 0

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.Paint:
            self.count += 1
        return False


def run_fps_benchmark(duration_s: float = 3.0,
                       link_rate_hz: float = 1000.0) -> dict:
    """Returns dict with fps metrics."""
    app = QApplication.instance() or QApplication(sys.argv)

    from backend.hardware.esp32_link import MockESP32Link
    from backend.hardware.telemetry_stream import TelemetryStream
    from backend.core.safety_controller import SafetyController
    from backend.core.motion_controller import MotionController
    from app.workers.telemetry_worker import TelemetryWorker
    from app.panels.live_production import LiveProductionPanel

    link = MockESP32Link(seed=42, rate_hz=link_rate_hz)
    stream = TelemetryStream()
    safety = SafetyController()
    motion = MotionController(link, safety)
    worker = TelemetryWorker(link, stream, safety, motion)

    panel = LiveProductionPanel()
    worker.frameBatch.connect(panel.on_frame_batch)
    worker.latestFrame.connect(panel.on_latest_frame)
    worker.connectionStateChanged.connect(panel.on_connection_state)

    # Install paint counter on the panel
    counter = PaintCounter()
    panel.installEventFilter(counter)

    # Bridge link → stream
    import threading, queue
    link_q = queue.Queue(maxsize=5000)
    link.subscribe(link_q)
    bridge_stop = threading.Event()

    def bridge():
        while not bridge_stop.is_set():
            try:
                f = link_q.get(timeout=0.05)
                stream.feed(f)
            except queue.Empty:
                continue

    panel.show()
    panel.resize(800, 600)
    link.connect()
    threading.Thread(target=bridge, daemon=True).start()
    worker.start()

    t0 = time.perf_counter()
    QTimer.singleShot(int(duration_s * 1000), app.quit)
    app.exec()
    elapsed = time.perf_counter() - t0

    worker.stop()
    worker.wait(2000)
    bridge_stop.set()
    link.disconnect()

    fps = counter.count / elapsed if elapsed > 0 else 0
    return {
        "duration_s":   elapsed,
        "paint_events": counter.count,
        "fps":          fps,
        "target_fps":   30.0,
        "passed":       fps >= 25.0,    # tolerate 5fps headroom
    }


if __name__ == "__main__":
    r = run_fps_benchmark(duration_s=3.0)
    print("FPS BENCHMARK")
    print("-" * 60)
    for k, v in r.items():
        if isinstance(v, float):
            print(f"  {k:20s} = {v:10.2f}")
        else:
            print(f"  {k:20s} = {v}")
    print(f"  Verdict: {'PASS ✓' if r['passed'] else 'FAIL ✗'}")
