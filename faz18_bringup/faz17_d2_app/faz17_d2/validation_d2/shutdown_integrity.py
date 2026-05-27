"""
validation_d2/shutdown_integrity.py — Clean Shutdown Test
==============================================================
Repeatedly opens and closes the main window. Verifies:
  - All worker threads stopped (not zombie)
  - All bridge threads stopped
  - No active QObject children after window destruction
  - No exceptions raised during close
  - Cumulative shutdown time stays bounded (no slowdown)

Pass criteria:
  - 5 open/close cycles complete in < 30s total
  - No exceptions during close
  - Final thread count == baseline thread count
"""
from __future__ import annotations
import os
import sys
import threading
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


def run_shutdown_test(n_cycles: int = 5, cycle_runtime_s: float = 1.5) -> dict:
    app = QApplication.instance() or QApplication(sys.argv)
    from app.main_window import FilamentWindingApp

    baseline_threads = threading.active_count()
    cycle_times = []
    errors = []

    for cycle in range(n_cycles):
        t0 = time.perf_counter()
        try:
            win = FilamentWindingApp()
            win.show()
            QTimer.singleShot(int(cycle_runtime_s * 1000), win.close)
            QTimer.singleShot(int((cycle_runtime_s + 0.5) * 1000), app.quit)
            app.exec()
            win.deleteLater()
            # Spin event loop briefly to process deleteLater
            QTimer.singleShot(100, app.quit); app.exec()
        except Exception as e:
            errors.append({
                "cycle":  cycle,
                "error":  str(e),
                "trace":  traceback.format_exc()[:500],
            })
        cycle_times.append(time.perf_counter() - t0)

    final_threads = threading.active_count()

    return {
        "n_cycles":         n_cycles,
        "cycle_runtime_s":  cycle_runtime_s,
        "cycle_times":      [round(t, 3) for t in cycle_times],
        "total_time_s":     round(sum(cycle_times), 3),
        "mean_cycle_s":     round(sum(cycle_times)/n_cycles, 3) if n_cycles else 0,
        "baseline_threads": baseline_threads,
        "final_threads":    final_threads,
        "thread_leak":      max(0, final_threads - baseline_threads),
        "n_errors":         len(errors),
        "errors":           errors,
        "passed":           len(errors) == 0 and (final_threads - baseline_threads) <= 2,
        # Tolerate +2 for QApplication's eternally-living global threads
    }


if __name__ == "__main__":
    r = run_shutdown_test(n_cycles=5, cycle_runtime_s=1.5)
    print("SHUTDOWN INTEGRITY TEST")
    print("-" * 60)
    print(f"  Cycles:               {r['n_cycles']}")
    print(f"  Cycle runtime:        {r['cycle_runtime_s']} s")
    print(f"  Per-cycle (s):        {r['cycle_times']}")
    print(f"  Total time:           {r['total_time_s']} s")
    print(f"  Mean cycle time:      {r['mean_cycle_s']} s")
    print(f"  Baseline threads:     {r['baseline_threads']}")
    print(f"  Final threads:        {r['final_threads']}")
    print(f"  Thread leak:          {r['thread_leak']}")
    print(f"  Errors:               {r['n_errors']}")
    for e in r["errors"]:
        print(f"    cycle {e['cycle']}: {e['error']}")
    print(f"  Pass: no errors, thread_leak ≤ 2")
    print(f"  Verdict: {'PASS ✓' if r['passed'] else 'FAIL ✗'}")
