"""
host_test/field_test_faz19.py — Faz 19 Saha Bağlantısı Test Süreci
=======================================================================
This is the FINAL Faz 19A verification. It exercises the full chain:

  C firmware code  →  stdout (acts as ESP32 UART TX)
        ↓
  pty master fd  (acts as USB-serial cable)
        ↓
  pty slave path  (looks like /dev/ttyUSB0 to PC)
        ↓
  RealESP32Link  (Faz 18 production code, unchanged)
        ↓
  TelemetryStream  (Faz 17 backend, unchanged)
        ↓
  TelemetryWorker  (Faz 17 D2 worker, unchanged)
        ↓
  FilamentWindingApp + LiveProductionPanel (Faz 17 D2 UI, unchanged)
        ↓
  TelemetryDB  (auto-recorded session)

If this passes, plugging in a real ESP32 with the actual firmware
flashed should produce the same numbers.

Faz 19 success criteria (from spec):
   Frames decoded:      >95%
   CRC errors:          0
   Sync errors:         0
   UI alarms propagate: PASS (we don't trigger alarms here; firmware
                              is healthy. Tested separately.)
   TelemetryDB record:  PASS
   30 min soak:         tested separately
"""
from __future__ import annotations
import os
import pty
import subprocess
import sys
import tempfile
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FW_ROOT = os.path.dirname(_HERE)
_PROJECT_ROOT = os.path.dirname(_FW_ROOT)
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "faz17_d2"))


def run_field_test(duration_s: float = 10.0,
                   rate_hz: float = 1000.0) -> dict:
    """
    1. Build firmware_driver (if not already built)
    2. Open pty pair
    3. Launch firmware_driver with stdout → master fd
    4. Construct FilamentWindingApp with real link on slave path
    5. Run for duration_s
    6. Collect metrics
    """
    # Build firmware_driver
    src_dir = _HERE
    driver_path = os.path.join(src_dir, "firmware_driver")
    if not os.path.exists(driver_path):
        cc = subprocess.run(
            ["gcc", "-O2", "-Wall", "-std=c11", "-I../include",
             "firmware_driver.c",
             "../main/telemetry_protocol.c", "../main/sensor_pipeline.c",
             "-o", "firmware_driver", "-lm"],
            cwd=src_dir, capture_output=True, text=True)
        if cc.returncode != 0:
            return {"passed": False,
                    "error": f"compile failed: {cc.stderr}"}

    # Open pty pair
    master_fd, slave_fd = pty.openpty()
    slave_path = os.ttyname(slave_fd)
    os.close(slave_fd)

    # Bring up Qt + main window pointing at slave
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from app.link_factory import LinkConfig
    from app.main_window import FilamentWindingApp
    from backend.hardware.esp32_link import ConnectionState

    app = QApplication.instance() or QApplication(sys.argv)
    tmpdir = tempfile.mkdtemp(prefix="fw_field_")
    cfg = LinkConfig(kind="real", port=slave_path,
                     watchdog_s=2.0, auto_reconnect=False)
    win = FilamentWindingApp(
        recipe_db_path=os.path.join(tmpdir, "recipes.db"),
        telemetry_db_path=os.path.join(tmpdir, "telemetry.db"),
        link_config=cfg)
    win.show()

    # Launch firmware_driver — its stdout writes into master_fd
    fw_proc = subprocess.Popen(
        [driver_path,
         "--rate-hz", str(rate_hz),
         "--duration-s", str(duration_s + 1.0)],
        stdout=master_fd,
        stderr=subprocess.PIPE,
        cwd=src_dir)

    # Connect link
    QTimer.singleShot(100, win._on_connect)

    # Snapshot link state shortly before close
    captured = {"state": None}
    QTimer.singleShot(
        int((duration_s - 0.2) * 1000),
        lambda: captured.update(state=win._link.state))

    QTimer.singleShot(int(duration_s * 1000), win.close)
    t0 = time.monotonic()
    app.exec()
    elapsed = time.monotonic() - t0

    # Tear down firmware driver
    fw_proc.terminate()
    try:
        fw_stderr = fw_proc.stderr.read().decode(errors="replace") if fw_proc.stderr else ""
        fw_proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        fw_proc.kill()
        fw_stderr = ""
    try:
        os.close(master_fd)
    except OSError:
        pass

    # Collect metrics
    link = win._link
    diag = link.diagnostics
    live = win._panel_live
    fps = live.current_fps() if hasattr(live, "current_fps") else 0.0
    sessions = win._telem_db.list_sessions()

    expected_frames = int(rate_hz * duration_s)
    delivery_pct = (100.0 * diag.n_frames_ok / expected_frames
                    if expected_frames > 0 else 0)

    return {
        "duration_s":         round(elapsed, 2),
        "target_rate_hz":     rate_hz,
        "expected_frames":    expected_frames,
        "link_state":         (captured["state"].name
                               if captured["state"] else link.state.name),
        "frames_decoded":     diag.n_frames_ok,
        "delivery_pct":       round(delivery_pct, 1),
        "crc_errors":         diag.n_crc_errors,
        "sync_errors":        diag.n_sync_errors,
        "partial_packets":    diag.n_partial_packets,
        "ui_chart_fps":       round(fps, 1),
        "sessions_recorded":  len(sessions),
        "session_frames":     sessions[0]["n_frames"] if sessions else 0,
        "firmware_driver_stderr": fw_stderr.strip(),
        # Pass criteria from Faz 19 spec
        "passed":  (
            (captured["state"] or link.state) == ConnectionState.CONNECTED and
            delivery_pct >= 95.0 and
            diag.n_crc_errors == 0 and
            diag.n_sync_errors == 0 and
            len(sessions) >= 1 and
            sessions[0]["n_frames"] >= int(expected_frames * 0.9)
        ),
    }


if __name__ == "__main__":
    duration = 10.0
    rate = 1000.0
    if len(sys.argv) > 1:
        duration = float(sys.argv[1])
    if len(sys.argv) > 2:
        rate = float(sys.argv[2])
    r = run_field_test(duration_s=duration, rate_hz=rate)
    print("\n" + "=" * 72)
    print(" FAZ 19 — SAHA BAĞLANTISI TESTİ")
    print(" (C firmware → pty → RealESP32Link → UI → TelemetryDB)")
    print("=" * 72)
    print(f"  Duration:            {r['duration_s']} s")
    print(f"  Target rate:         {r['target_rate_hz']} Hz")
    print(f"  Expected frames:     {r['expected_frames']:,d}")
    print()
    print(f"  Link state:          {r['link_state']}")
    print(f"  Frames decoded:      {r['frames_decoded']:,d}")
    print(f"  Delivery:            {r['delivery_pct']}%")
    print(f"  CRC errors:          {r['crc_errors']}")
    print(f"  Sync errors:         {r['sync_errors']}")
    print(f"  Partial packets:     {r['partial_packets']}")
    print()
    print(f"  UI chart FPS:        {r['ui_chart_fps']}")
    print()
    print(f"  Sessions recorded:   {r['sessions_recorded']}")
    print(f"  Frames in session:   {r['session_frames']:,d}")
    print()
    if r.get("firmware_driver_stderr"):
        print(f"  Firmware driver:     {r['firmware_driver_stderr']}")
    print()
    verdict = "PASS ✓" if r["passed"] else "FAIL ✗"
    print(f"  ★ Verdict: {verdict}")
    sys.exit(0 if r["passed"] else 1)
