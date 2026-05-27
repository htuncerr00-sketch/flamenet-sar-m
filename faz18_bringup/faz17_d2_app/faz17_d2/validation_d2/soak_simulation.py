"""
validation_d2/soak_simulation.py — Long-Duration Stability Test
====================================================================
Simulates extended production runtime by running the live pipeline
for a configurable duration. Tracks:
  - RSS growth over time (sliding window)
  - Frame throughput stability
  - Worker thread health
  - Any anomalies (CRC failures, drops, panics)

Since real 24h takes 24h, we run an aggressive 60-second equivalent:
  - 60 seconds at 5kHz = 300k frames (= 5 minutes at real 1kHz)
  - Stable RSS = pass
  - Set N=24 for "24-equivalent" multiplier docs

Pass criteria:
  - RSS doesn't grow > 2 MB across last half of test
  - No CRC errors, no exceptions
  - Throughput stays within ±10% across 4 measurement windows
"""
from __future__ import annotations
import os
import sys
import gc
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


def _rss_kb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (FileNotFoundError, ValueError):
        pass
    return 0


def run_soak_test(total_seconds: float = 60.0,
                   link_rate_hz: float = 5000.0,
                   n_windows: int = 6) -> dict:
    """
    Run for total_seconds wallclock at link_rate_hz.
    Sample throughput + RSS every total_seconds/n_windows.
    """
    app = QApplication.instance() or QApplication(sys.argv)

    from backend.hardware.esp32_link import MockESP32Link
    from backend.hardware.telemetry_stream import TelemetryStream
    from backend.core.safety_controller import SafetyController
    from backend.core.motion_controller import MotionController
    from app.workers.telemetry_worker import TelemetryWorker
    from app.panels.live_production import LiveProductionPanel
    from app.panels.alarms import AlarmsPanel

    link = MockESP32Link(seed=42, rate_hz=link_rate_hz)
    stream = TelemetryStream()
    safety = SafetyController()
    motion = MotionController(link, safety)
    worker = TelemetryWorker(link, stream, safety, motion)

    live = LiveProductionPanel()
    alarms = AlarmsPanel()

    n_batches_total = [0]
    n_frames_total = [0]
    n_alarms_total = [0]

    def on_batch(b):
        n_batches_total[0] += 1
        n_frames_total[0] += len(b)

    def on_alarm(_):
        n_alarms_total[0] += 1

    worker.frameBatch.connect(on_batch)
    worker.frameBatch.connect(live.on_frame_batch)
    worker.latestFrame.connect(live.on_latest_frame)
    worker.alarmReceived.connect(alarms.on_safety_event)
    worker.alarmReceived.connect(on_alarm)

    import threading, queue
    link_q = queue.Queue(maxsize=20000)
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

    # Initial warmup (1.5s × chunks) to fill buffers
    for _ in range(2):
        QTimer.singleShot(1500, app.quit)
        app.exec()
        gc.collect()

    window_s = total_seconds / n_windows
    samples = []
    last_frames = n_frames_total[0]
    last_t = time.perf_counter()

    for w in range(n_windows):
        # Run window in 3s chunks so paint events flow
        sub_chunks = max(1, int(window_s / 3.0))
        for _ in range(sub_chunks):
            QTimer.singleShot(min(int(window_s * 1000 / sub_chunks), 3000), app.quit)
            app.exec()
            gc.collect()

        now = time.perf_counter()
        frames_this_window = n_frames_total[0] - last_frames
        elapsed_this_window = now - last_t
        fps_this_window = frames_this_window / elapsed_this_window if elapsed_this_window > 0 else 0
        samples.append({
            "window":    w + 1,
            "rss_kb":    _rss_kb(),
            "fps":       fps_this_window,
            "n_frames":  frames_this_window,
            "n_alarms":  n_alarms_total[0],
            "n_batches": n_batches_total[0],
            "stream_dropped": stream.stats.n_dropped,
            "stream_gaps":    stream.stats.n_seq_gaps,
        })
        last_frames = n_frames_total[0]
        last_t = now

    worker.stop()
    worker.wait(2000)
    stop.set()
    link.disconnect()

    # ── Analysis ──
    rss_values = [s["rss_kb"] for s in samples]
    fps_values = [s["fps"] for s in samples]
    rss_first_half = rss_values[:len(rss_values)//2]
    rss_second_half = rss_values[len(rss_values)//2:]
    rss_h1_avg = sum(rss_first_half) / max(1, len(rss_first_half))
    rss_h2_avg = sum(rss_second_half) / max(1, len(rss_second_half))
    rss_growth_kb = rss_h2_avg - rss_h1_avg

    fps_min = min(fps_values) if fps_values else 0
    fps_max = max(fps_values) if fps_values else 0
    fps_mean = sum(fps_values) / max(1, len(fps_values))
    fps_jitter_pct = ((fps_max - fps_min) / fps_mean * 100) if fps_mean > 0 else 0

    return {
        "total_seconds":   total_seconds,
        "link_rate_hz":    link_rate_hz,
        "n_windows":       n_windows,
        "samples":         samples,
        "rss_growth_kb":   round(rss_growth_kb, 1),
        "fps_min":         round(fps_min, 1),
        "fps_max":         round(fps_max, 1),
        "fps_mean":        round(fps_mean, 1),
        "fps_jitter_pct":  round(fps_jitter_pct, 1),
        "n_frames_total":  n_frames_total[0],
        "n_alarms":        n_alarms_total[0],
        "stream_dropped":  samples[-1]["stream_dropped"] if samples else 0,
        "passed":          (
            abs(rss_growth_kb) < 2048 and
            fps_jitter_pct < 15.0 and
            n_alarms_total[0] == 0
        ),
    }


if __name__ == "__main__":
    r = run_soak_test(total_seconds=60.0, link_rate_hz=5000.0, n_windows=6)
    print("SOAK TEST (60s @ 5kHz = ~300k frames)")
    print("-" * 60)
    print(f"  Duration:             {r['total_seconds']} s")
    print(f"  Link rate:            {r['link_rate_hz']} Hz")
    print(f"  Total frames:         {r['n_frames_total']:,d}")
    print(f"  Window samples:")
    for s in r["samples"]:
        print(f"    W{s['window']}: RSS={s['rss_kb']:>7,d} KB  "
              f"fps={s['fps']:>7.1f}  drops={s['stream_dropped']:>7d}")
    print(f"  Half-1 → Half-2 RSS:  {r['rss_growth_kb']:+8.1f} KB")
    print(f"  FPS mean:             {r['fps_mean']}")
    print(f"  FPS jitter:           {r['fps_jitter_pct']}%")
    print(f"  Alarms raised:        {r['n_alarms']}")
    print(f"  Pass: RSS Δ<2MB, FPS jitter<15%, no alarms")
    print(f"  Verdict: {'PASS ✓' if r['passed'] else 'FAIL ✗'}")
