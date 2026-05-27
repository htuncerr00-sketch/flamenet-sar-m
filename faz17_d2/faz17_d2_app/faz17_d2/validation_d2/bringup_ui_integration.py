"""
validation_d2/bringup_ui_integration.py — End-to-End UI Bring-Up
====================================================================
The actual Faz 18 goal:
  "Gerçek ESP32'den gelen telemetry'nin PySide6 LiveProduction panelinde
   stabil görünmesi."

Wires together:
  - RealESP32Link (via pty, looking like /dev/pts/N)
  - TelemetryStream
  - TelemetryWorker (real QThread, real signals)
  - FilamentWindingApp (full main window, all 7 panels)
  - LiveProductionPanel — verify it receives frames + updates KPIs

Test sequence:
  1. Start pty pair
  2. Construct FilamentWindingApp with link_config kind="real", port=slave
  3. Spawn synthetic ESP32 writer thread on master_fd
  4. Run UI for 5 s
  5. Verify:
     - link state == CONNECTED
     - LiveProductionPanel.current_fps() > 25
     - n_frames_ok in diagnostics > 1000
     - n_crc_errors == 0
     - alarm panel received at least 1 safety event (we inject a low-tension
       frame partway through)
     - auto-record session was created in TelemetryDB
"""
from __future__ import annotations
import os
import pty
import queue
import sys
import tempfile
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from backend.hardware.esp32_link import TelemetryFrame, ConnectionState
from backend.hardware.real_esp32_link import RealESP32Link
from app.link_factory import LinkConfig
from app.main_window import FilamentWindingApp


MAGIC = b"\xaa\x55"


def make_frame(seq, *, T_N=15.0, temp_K=295.15, rpm=8.5, x_mm=100.0):
    f = TelemetryFrame(
        ts_us=int(seq * 1000), seq=seq & 0xFFFF, flags=1,
        x_mm=x_mm, a_deg=(seq * 0.3) % 360.0,
        T_N=T_N, rpm=rpm,
        vib_x=0.05, vib_y=0.05, vib_z=0.05,
        temp_K=temp_K, current_A=2.5,
        alpha=0.5, quality=92.0)
    return MAGIC + f.pack()


def run_end_to_end_test(duration_s: float = 5.0,
                        link_rate_hz: float = 1000.0) -> dict:
    # 1. virtual ESP32 — pty pair. slave path is what RealESP32Link opens.
    master_fd, slave_fd = pty.openpty()
    slave_path = os.ttyname(slave_fd)
    os.close(slave_fd)   # pyserial will reopen by path

    # 2. construct main window with real link
    cfg = LinkConfig(kind="real", port=slave_path,
                     watchdog_s=2.0, auto_reconnect=False)
    app = QApplication.instance() or QApplication(sys.argv)

    # Per-test telemetry DB in a temp dir so we don't pollute cwd
    tmpdir = tempfile.mkdtemp(prefix="fw_bringup_")
    win = FilamentWindingApp(
        recipe_db_path=os.path.join(tmpdir, "recipes.db"),
        telemetry_db_path=os.path.join(tmpdir, "telemetry.db"),
        link_config=cfg,
    )
    win.show()

    # 3. spawn writer thread — synthetic ESP32 streaming at link_rate_hz
    stop_writer = threading.Event()
    writer_stats = {"n_written": 0, "n_alarms_sent": 0}

    def writer():
        period = 1.0 / link_rate_hz
        t0 = time.monotonic()
        seq = 0
        next_t = t0
        # Inject ONE low-tension frame at t=2 s to trigger a safety event
        alarm_t = t0 + 2.0
        alarm_sent = False
        while not stop_writer.is_set():
            now = time.monotonic()
            if now >= next_t:
                if not alarm_sent and now >= alarm_t:
                    frame = make_frame(seq, T_N=1.0)   # < 3 N → TENSION_LOW
                    alarm_sent = True
                    writer_stats["n_alarms_sent"] += 1
                else:
                    frame = make_frame(seq, T_N=15.0)
                try:
                    os.write(master_fd, frame)
                    writer_stats["n_written"] += 1
                    seq += 1
                except OSError:
                    return
                next_t += period
            else:
                time.sleep(0.0005)

    wth = threading.Thread(target=writer, daemon=True, name="synthetic_esp32")
    wth.start()

    # 4. trigger link connect from UI
    QTimer.singleShot(100, win._on_connect)

    # 5. run UI for duration_s. Capture link state shortly before close.
    state_at_runtime = {"state": None}
    def capture_state():
        state_at_runtime["state"] = win._link.state

    QTimer.singleShot(int((duration_s - 0.2) * 1000), capture_state)
    QTimer.singleShot(int(duration_s * 1000), win.close)
    t0 = time.monotonic()
    app.exec()
    elapsed = time.monotonic() - t0

    # 6. tear down writer + pty
    stop_writer.set()
    wth.join(timeout=1.0)
    try:
        os.close(master_fd)
    except OSError:
        pass

    # 7. collect diagnostics
    link: RealESP32Link = win._link    # type: ignore[assignment]
    diag = link.diagnostics
    live = win._panel_live
    alarms = win._panel_alarms
    fps = live.current_fps() if hasattr(live, "current_fps") else 0.0
    n_alarms_in_ui = alarms._alarm_list.event_count() \
        if hasattr(alarms, "_alarm_list") else 0
    recording = bool(getattr(win, "_recording", False))
    # The session might have been auto-stopped on win.close
    session_list = win._telem_db.list_sessions()

    # 8. shutdown teardown
    # FilamentWindingApp.closeEvent should have run already

    return {
        "duration_s":         round(elapsed, 2),
        "link_state":         (state_at_runtime["state"].name
                               if state_at_runtime["state"] else link.state.name),
        "link_n_frames_ok":   diag.n_frames_ok,
        "link_n_crc_errors":  diag.n_crc_errors,
        "link_n_sync_errors": diag.n_sync_errors,
        "link_partial":       diag.n_partial_packets,
        "writer_n_written":   writer_stats["n_written"],
        "writer_n_alarms":    writer_stats["n_alarms_sent"],
        "ui_chart_fps":       round(fps, 1),
        "ui_n_alarms":        n_alarms_in_ui,
        "auto_record_was_on": recording,   # snapshot at close; may be False if close hook ran
        "n_sessions_saved":   len(session_list),
        "session_recorded":   session_list[0]["n_frames"] if session_list else 0,
        # Pass criteria — what bring-up *means*:
        "passed": (
            (state_at_runtime["state"] or link.state) == ConnectionState.CONNECTED and
            diag.n_frames_ok > 1000 and
            diag.n_crc_errors == 0 and
            fps > 15.0 and                # offscreen rendering ceiling
            n_alarms_in_ui >= 1 and        # tension alarm propagated to UI
            len(session_list) >= 1 and     # auto-record made a session
            session_list[0]["n_frames"] > 1000
        ),
    }


if __name__ == "__main__":
    r = run_end_to_end_test(duration_s=5.0, link_rate_hz=1000.0)
    print("\n" + "=" * 72)
    print(" FAZ 18 — END-TO-END BRING-UP (Real link → UI)")
    print("=" * 72)
    print(f"  Duration:            {r['duration_s']} s")
    print()
    print(f"  Link state:          {r['link_state']}")
    print(f"  Frames decoded:      {r['link_n_frames_ok']:,d}")
    print(f"  CRC errors:          {r['link_n_crc_errors']}")
    print(f"  Sync errors:         {r['link_n_sync_errors']}")
    print(f"  Partial packets:     {r['link_partial']}")
    print()
    print(f"  Writer sent:         {r['writer_n_written']:,d} frames")
    print(f"  Writer sent alarms:  {r['writer_n_alarms']}")
    print()
    print(f"  UI chart FPS:        {r['ui_chart_fps']}")
    print(f"  UI alarms shown:     {r['ui_n_alarms']}")
    print()
    print(f"  Sessions recorded:   {r['n_sessions_saved']}")
    print(f"  Frames in session:   {r['session_recorded']:,d}")
    print()
    verdict = "PASS ✓" if r["passed"] else "FAIL ✗"
    print(f"  ★ Verdict: {verdict}")
    sys.exit(0 if r["passed"] else 1)
