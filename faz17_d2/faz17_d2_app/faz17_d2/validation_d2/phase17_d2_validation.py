#!/usr/bin/env python3
"""
phase17_d2_validation.py — FAZ 17 TESLİMAT 2 Validation Orchestrator
========================================================================
Runs all D2 validation tests and produces the final readiness report.

Tests executed (in order):
  1. Backend regression — re-runs D1 validation as a sanity check
  2. Main window smoke — instantiate / show / close cleanly
  3. UI freeze test — signal flood, event loop latency
  4. FPS benchmark — actual paint events / second
  5. Memory leak — RSS growth in steady state
  6. Replay stress — 1M frame binary replay
  7. Shutdown integrity — repeated open/close cycles
  8. Soak simulation — 60s @ 5kHz extended run

Final composite score = mean of category scores.
Output: fw_phase17_d2_report.json + console verdict.
"""
from __future__ import annotations
import json
import os
import sys
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _safe_run(name: str, fn):
    """Run a test function, capture exceptions, return result dict + status."""
    t0 = time.perf_counter()
    try:
        result = fn()
        elapsed = time.perf_counter() - t0
        return {
            "name":      name,
            "result":    result,
            "elapsed_s": round(elapsed, 2),
            "passed":    bool(result.get("passed", False)),
            "error":     None,
        }
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return {
            "name":      name,
            "result":    {},
            "elapsed_s": round(elapsed, 2),
            "passed":    False,
            "error":     f"{type(e).__name__}: {e}",
            "trace":     traceback.format_exc()[:1000],
        }


def main_window_smoke_test():
    """Quick sanity check: instantiate FilamentWindingApp, run 1s, close.

    IMPORTANT: We use window.close() to trigger natural shutdown via
    quitOnLastWindowClosed, NOT app.quit(). Explicit app.quit() sets a
    sticky flag that causes subsequent app.exec() calls in the same
    process to return immediately, breaking the test orchestrator.
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from app.main_window import FilamentWindingApp
    win = FilamentWindingApp()
    win.show()
    n_tabs = win._tabs.count() if hasattr(win, "_tabs") else 0
    QTimer.singleShot(1000, win.close)
    t0 = time.perf_counter()
    rc = app.exec()
    elapsed = time.perf_counter() - t0
    # The window grew from 7 → 13 tabs across Faz 23/24 (project/material/layer
    # design panels + production design centre). Require the full current set.
    return {
        "n_tabs":   n_tabs,
        "rc":       rc,
        "elapsed":  round(elapsed, 2),
        "passed":   (n_tabs >= 13 and rc == 0 and elapsed < 3.0),
    }


def _d1_package_cwd() -> str:
    """Locate the directory from which ``python -m faz17_d1.phase17_validation``
    resolves — i.e. the ancestor holding a ``faz17_d1`` package that contains
    the structured ``hardware`` subpackage.

    Avoids a hardcoded absolute path so the regression runs from any checkout
    location (CI, container, developer machine). Falls back to the repo root.
    """
    here = os.path.abspath(__file__)
    d = here
    repo_root = None
    for _ in range(8):
        d = os.path.dirname(d)
        pkg = os.path.join(d, "faz17_d1")
        if os.path.isdir(pkg):
            if repo_root is None:
                repo_root = d
            # Prefer the layout where faz17_d1 has the structured subpackages.
            if os.path.isdir(os.path.join(pkg, "hardware")):
                return d
            nested = os.path.join(pkg, "faz17_d1_backend")
            if os.path.isdir(os.path.join(nested, "faz17_d1", "hardware")):
                return nested
    return repo_root or os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(here))))


def backend_d1_regression():
    """Re-run D1 backend validation as a regression check."""
    import subprocess
    proc = subprocess.run(
        [sys.executable, "-m", "faz17_d1.phase17_validation"],
        cwd=_d1_package_cwd(),
        capture_output=True, text=True, timeout=120)
    out = proc.stdout
    ready = "BACKEND READY" in out
    return {
        "passed":      ready,
        "return_code": proc.returncode,
        "ready_token": ready,
    }


def main():
    print("\n" + "="*72)
    print(" FAZ 17 — TESLİMAT 2 VALIDATION ORCHESTRATOR")
    print("="*72)

    # Import test runners
    from validation_d2.ui_freeze_test import run_ui_freeze_test
    from validation_d2.fps_benchmark import run_fps_benchmark
    from validation_d2.memory_leak import run_memory_leak_test
    from validation_d2.replay_stress import run_replay_stress_test
    from validation_d2.shutdown_integrity import run_shutdown_test
    from validation_d2.soak_simulation import run_soak_test

    tests = [
        ("backend_d1_regression",   backend_d1_regression),
        ("main_window_smoke",       main_window_smoke_test),
        ("ui_freeze_test",          lambda: run_ui_freeze_test(duration_s=3.0)),
        ("fps_benchmark",           lambda: run_fps_benchmark(duration_s=3.0)),
        ("memory_leak",             lambda: run_memory_leak_test()),
        ("replay_stress_1M",        lambda: run_replay_stress_test(n_frames=1_000_000)),
        ("shutdown_integrity",      lambda: run_shutdown_test(n_cycles=3, cycle_runtime_s=1.0)),
        ("soak_simulation_60s",     lambda: run_soak_test(total_seconds=60.0, link_rate_hz=5000.0)),
    ]

    outcomes = []
    for name, fn in tests:
        print(f"\n--- Running: {name} ---")
        outcome = _safe_run(name, fn)
        outcomes.append(outcome)
        verdict = "PASS ✓" if outcome["passed"] else "FAIL ✗"
        print(f"    {verdict}  ({outcome['elapsed_s']}s)")
        if outcome["error"]:
            print(f"    ERROR: {outcome['error']}")

    # ── Compute scores ──
    n_total = len(outcomes)
    n_passed = sum(1 for o in outcomes if o["passed"])
    composite = 100.0 * n_passed / n_total

    # ── Build full report ──
    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "delivery":  "Faz 17 — Teslimat 2 (Desktop UI Application)",
        "total_tests":   n_total,
        "tests_passed":  n_passed,
        "tests_failed":  n_total - n_passed,
        "composite_score": round(composite, 1),
        "outcomes":   outcomes,
    }
    out_dir = "/mnt/user-data/outputs"
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, "fw_phase17_d2_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    # ── Print summary ──
    print(f"\n" + "="*72)
    print(" READINESS REPORT")
    print("="*72)
    print(f"""
  ╔══════════════════════════════════════════════════════════════════╗
  ║  FAZ 17 — TESLİMAT 2 DESKTOP UI READINESS                        ║
  ╠══════════════════════════════════════════════════════════════════╣""")
    for o in outcomes:
        icon = "✓" if o["passed"] else "✗"
        bar = "████████████████████" if o["passed"] else "░░░░░░░░░░░░░░░░░░░░"
        sc = "100.0" if o["passed"] else "  0.0"
        print(f"  ║  {icon} {o['name']:<30} [{bar}] {sc}  ║")
    print(f"""  ╠══════════════════════════════════════════════════════════════════╣
  ║  Composite:  {composite:5.1f}/100  ({n_passed}/{n_total} tests passed)                  ║""")

    # ── Key metrics summary ──
    metrics = {}
    for o in outcomes:
        if o["name"] == "ui_freeze_test":
            r = o["result"]
            metrics["ui_p99_ms"] = r.get("p99_ms")
            metrics["ui_max_ms"] = r.get("max_ms")
            metrics["throughput_fps"] = r.get("frames_per_second")
        elif o["name"] == "fps_benchmark":
            metrics["chart_fps"] = o["result"].get("fps")
        elif o["name"] == "memory_leak":
            metrics["rss_growth_kb"] = o["result"].get("rss_growth_kb")
        elif o["name"] == "replay_stress_1M":
            metrics["replay_decode_fps"] = o["result"].get("load_decode_rate_fps")
        elif o["name"] == "soak_simulation_60s":
            metrics["soak_fps_mean"] = o["result"].get("fps_mean")
            metrics["soak_fps_jitter_pct"] = o["result"].get("fps_jitter_pct")

    print(f"""  ╠══════════════════════════════════════════════════════════════════╣
  ║  KEY METRICS:                                                    ║""")
    print(f"  ║    UI loop p99:        {metrics.get('ui_p99_ms', '?'):>8} ms                            ║")
    print(f"  ║    UI loop max:        {metrics.get('ui_max_ms', '?'):>8} ms                            ║")
    print(f"  ║    Telemetry ingest:   {metrics.get('throughput_fps', '?'):>8} frames/s                      ║")
    print(f"  ║    Chart FPS:          {metrics.get('chart_fps', '?'):>8} fps                           ║")
    print(f"  ║    Memory growth:      {metrics.get('rss_growth_kb', '?'):>+8} KB (over 10s)                ║")
    print(f"  ║    Replay decode:      {metrics.get('replay_decode_fps', '?'):>8} fps (1M frames)              ║")
    print(f"  ║    Soak FPS mean:      {metrics.get('soak_fps_mean', '?'):>8} fps (60s @ 5kHz)             ║")
    print(f"  ║    Soak FPS jitter:    {metrics.get('soak_fps_jitter_pct', '?'):>8} %                             ║")

    # ── Final verdict ──
    print(f"  ╠══════════════════════════════════════════════════════════════════╣")
    if n_passed == n_total:
        print(f"  ║  ★★★  FAZ 17 D2 DESKTOP UI READY  ★★★                            ║")
    else:
        failed_names = [o['name'] for o in outcomes if not o['passed']]
        print(f"  ║  STOP — UI NOT READY: {str(failed_names)[:42]:<42}  ║")
    print(f"  ╚══════════════════════════════════════════════════════════════════╝")
    print(f"\n  Report: {report_path}")

    return 0 if n_passed == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
