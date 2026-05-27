"""
validation_d2/memory_leak.py — Memory Growth Detector
==========================================================
Two-phase measurement to differentiate fill-up from real leaks:

  Phase 1: WARMUP (chunked QTimer ticks so paint events flush)
    → chart history deques fill to bounded capacity
    → telemetry ring buffer fills to capacity
    → coalescing batches reach steady-state size
  Phase 2: MEASURE (chunked, same way)
    → any RSS growth here is a real steady-state leak

IMPORTANT: tracemalloc itself allocates 5-10 MB of bookkeeping tables
and does NOT return that to the OS even after tracemalloc.stop().
So we measure RSS *without* tracemalloc enabled. tracemalloc is run
as a separate optional diagnostic phase to identify allocation sites.

Pass criteria:
  - Post-warmup RSS growth < 2 MB over measure_s seconds
"""
from __future__ import annotations
import gc
import os
import sys
import tracemalloc
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


def _rss_kb() -> int:
    """Get current RSS in KB (Linux /proc, fallback to resource module)."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (FileNotFoundError, ValueError):
        pass
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except ImportError:
        return 0


def _spin(app, seconds, chunk_s=3.0):
    """Run app.exec for ~seconds, in chunks so paint events deliver normally."""
    n = max(1, int(seconds / chunk_s))
    for _ in range(n):
        QTimer.singleShot(int(chunk_s * 1000), app.quit)
        app.exec()
        gc.collect()


def run_memory_leak_test(warmup_s=15.0, measure_s=10.0,
                          run_tracemalloc_diagnostic=True):
    app = QApplication.instance() or QApplication(sys.argv)

    from backend.hardware.esp32_link import MockESP32Link
    from backend.hardware.telemetry_stream import TelemetryStream
    from backend.core.safety_controller import SafetyController
    from backend.core.motion_controller import MotionController
    from app.workers.telemetry_worker import TelemetryWorker
    from app.panels.live_production import LiveProductionPanel
    from app.panels.alarms import AlarmsPanel

    link = MockESP32Link(seed=42, rate_hz=2000.0)
    stream = TelemetryStream()
    safety = SafetyController()
    motion = MotionController(link, safety)
    worker = TelemetryWorker(link, stream, safety, motion)

    live = LiveProductionPanel()
    alarms = AlarmsPanel()
    worker.frameBatch.connect(live.on_frame_batch)
    worker.latestFrame.connect(live.on_latest_frame)
    worker.alarmReceived.connect(alarms.on_safety_event)

    import threading, queue
    link_q = queue.Queue(maxsize=10000)
    link.subscribe(link_q)
    stop = threading.Event()

    def bridge():
        while not stop.is_set():
            try:
                f = link_q.get(timeout=0.05)
                stream.feed(f)
            except queue.Empty:
                continue

    link.connect()
    threading.Thread(target=bridge, daemon=True).start()
    worker.start()
    live.show()

    # Phase 1: WARMUP
    _spin(app, warmup_s)

    # Sample RSS without tracemalloc active
    rss_before_kb = _rss_kb()

    # Phase 2: MEASURE
    _spin(app, measure_s)

    rss_after_kb = _rss_kb()
    rss_growth_kb = rss_after_kb - rss_before_kb

    # Phase 3: Optional tracemalloc diagnostic (~3s, separate window)
    top_growth = []
    tm_growth_kb = 0.0
    if run_tracemalloc_diagnostic:
        tracemalloc.start(10)
        snap1 = tracemalloc.take_snapshot()
        _spin(app, 3.0)
        snap2 = tracemalloc.take_snapshot()
        stats = snap2.compare_to(snap1, "filename")
        tm_growth_kb = sum(s.size_diff for s in stats) / 1024.0
        for s in stats[:5]:
            if s.size_diff > 0:
                top_growth.append({
                    "file":         str(s.traceback)[:80],
                    "size_diff_kb": s.size_diff / 1024.0,
                    "count_diff":   s.count_diff,
                })
        tracemalloc.stop()

    worker.stop()
    worker.wait(2000)
    stop.set()
    link.disconnect()

    return {
        "rss_before_kb":         rss_before_kb,
        "rss_after_kb":          rss_after_kb,
        "rss_growth_kb":         rss_growth_kb,
        "tracemalloc_growth_kb": round(tm_growth_kb, 1),
        "top_growth":            top_growth,
        "warmup_s":              warmup_s,
        "measure_s":             measure_s,
        "passed":                rss_growth_kb < 2048,
    }


if __name__ == "__main__":
    r = run_memory_leak_test()
    print("MEMORY LEAK TEST")
    print("-" * 60)
    print(f"  Warmup duration:      {r['warmup_s']:6.1f} s")
    print(f"  Measure duration:     {r['measure_s']:6.1f} s")
    print(f"  RSS before measure:   {r['rss_before_kb']:8d} KB")
    print(f"  RSS after measure:    {r['rss_after_kb']:8d} KB")
    print(f"  Steady-state growth:  {r['rss_growth_kb']:+8d} KB")
    print()
    print(f"  Diagnostic (tracemalloc, transient working set):")
    print(f"  Python heap growth:   {r['tracemalloc_growth_kb']:8.1f} KB")
    if r["top_growth"]:
        print(f"  Top sites (transient, NOT leaks):")
        for g in r["top_growth"]:
            print(f"    +{g['size_diff_kb']:7.1f} KB  ({g['count_diff']:+d} allocs)")
    print()
    print(f"  Pass: steady-state RSS growth < 2 MB")
    print(f"  Verdict: {'PASS ✓' if r['passed'] else 'FAIL ✗'}")
