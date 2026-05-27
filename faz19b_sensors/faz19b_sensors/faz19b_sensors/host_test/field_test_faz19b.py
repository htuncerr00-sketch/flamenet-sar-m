"""
host_test/field_test_faz19b.py — Faz 19B Sensor Data Propagation
======================================================================
Extends Faz 19A field_test by ALSO verifying:
  - INA226 current values reach the wire (frame.current_A varies)
  - MPU6050 accel values reach the wire (frame.vib_x not constant)
  - NTC temperature reaches the wire (frame.temp_K plausible)
  - Sensor-OK flag bits propagate (TELEM_FLAG_INA226_OK, IMU_OK, THERMAL_OK)
  - quality field reflects sensor health (close to 92 when all healthy)
  - Faz 19A regression: still >95% delivery, 0 CRC, 0 sync
"""
from __future__ import annotations
import os
import pty
import struct
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FW_ROOT = os.path.dirname(_HERE)
_PROJECT_ROOT = os.path.dirname(_FW_ROOT)
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "faz17_d2"))


# Per Faz 19B telemetry_frame.h
F_BOOT_OK      = 1 << 0
F_TENSION_OK   = 1 << 1
F_TEMP_OK      = 1 << 2
F_VIBRATION_OK = 1 << 4
F_INA226_OK    = 1 << 9
F_IMU_OK       = 1 << 10
F_THERMAL_OK   = 1 << 11


def run_test(duration_s: float = 10.0, rate_hz: float = 1000.0):
    # Build firmware_driver if needed
    if not os.path.exists(os.path.join(_HERE, "firmware_driver")):
        cc = subprocess.run(
            ["gcc", "-O2", "-Wall", "-std=c11", "-I../include", "-I.",
             "firmware_driver.c",
             "../main/telemetry_protocol.c", "../main/sensor_pipeline.c",
             "../main/i2c_bus.c", "../main/ina226.c",
             "../main/mpu6050.c", "../main/thermal.c",
             "i2c_host_mock.c",
             "-o", "firmware_driver", "-lm", "-lpthread"],
            cwd=_HERE, capture_output=True, text=True)
        if cc.returncode != 0:
            return {"passed": False, "error": f"compile: {cc.stderr}"}

    master_fd, slave_fd = pty.openpty()
    slave_path = os.ttyname(slave_fd)
    os.close(slave_fd)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from app.link_factory import LinkConfig
    from app.main_window import FilamentWindingApp
    from backend.hardware.esp32_link import ConnectionState, TelemetryFrame

    app = QApplication.instance() or QApplication(sys.argv)
    tmpdir = tempfile.mkdtemp(prefix="fw_19b_")
    cfg = LinkConfig(kind="real", port=slave_path,
                     watchdog_s=2.0, auto_reconnect=False)
    win = FilamentWindingApp(
        recipe_db_path=os.path.join(tmpdir, "recipes.db"),
        telemetry_db_path=os.path.join(tmpdir, "telemetry.db"),
        link_config=cfg)
    win.show()

    # Subscribe to the link directly to capture decoded frames for analysis
    import queue as _q
    capture_q = _q.Queue(maxsize=20000)
    win._link.subscribe(capture_q)

    fw_proc = subprocess.Popen(
        [os.path.join(_HERE, "firmware_driver"),
         "--rate-hz", str(rate_hz),
         "--duration-s", str(duration_s + 1.0)],
        stdout=master_fd,
        stderr=subprocess.PIPE,
        cwd=_HERE)

    QTimer.singleShot(100, win._on_connect)
    QTimer.singleShot(int(duration_s * 1000), win.close)
    t0 = time.monotonic()
    app.exec()
    elapsed = time.monotonic() - t0

    fw_proc.terminate()
    try:
        fw_stderr = fw_proc.stderr.read().decode(errors="replace") if fw_proc.stderr else ""
        fw_proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        fw_proc.kill()
        fw_stderr = ""
    try: os.close(master_fd)
    except OSError: pass

    # Pull captured frames
    captured = []
    while True:
        try: captured.append(capture_q.get_nowait())
        except _q.Empty: break

    # Analyze
    n_total = len(captured)
    diag = win._link.diagnostics
    sessions = win._telem_db.list_sessions()

    # Sensor value statistics
    if captured:
        currents = [f.current_A for f in captured]
        vib_xs   = [f.vib_x for f in captured]
        temps_K  = [f.temp_K for f in captured]
        qualities = [f.quality for f in captured]
        flags    = [f.flags for f in captured]
        current_min = min(currents)
        current_max = max(currents)
        current_range = current_max - current_min
        vib_x_min = min(vib_xs)
        vib_x_max = max(vib_xs)
        vib_x_range = vib_x_max - vib_x_min
        temp_mean = sum(temps_K) / len(temps_K)
        quality_mean = sum(qualities) / len(qualities)
        ina_ok_pct = 100.0 * sum(1 for f in flags if f & F_INA226_OK) / n_total
        imu_ok_pct = 100.0 * sum(1 for f in flags if f & F_IMU_OK) / n_total
        therm_ok_pct = 100.0 * sum(1 for f in flags if f & F_THERMAL_OK) / n_total
    else:
        current_min = current_max = current_range = 0
        vib_x_min = vib_x_max = vib_x_range = 0
        temp_mean = quality_mean = 0
        ina_ok_pct = imu_ok_pct = therm_ok_pct = 0

    expected = int(rate_hz * duration_s)
    delivery_pct = 100.0 * diag.n_frames_ok / expected if expected else 0

    result = {
        "duration_s":         round(elapsed, 2),
        "expected_frames":    expected,
        "frames_decoded":     diag.n_frames_ok,
        "delivery_pct":       round(delivery_pct, 1),
        "crc_errors":         diag.n_crc_errors,
        "sync_errors":        diag.n_sync_errors,
        "captured_for_analysis": n_total,
        # Sensor data propagation checks
        "current_A_min":      round(current_min, 3),
        "current_A_max":      round(current_max, 3),
        "current_A_range":    round(current_range, 3),
        "vib_x_min":          round(vib_x_min, 4),
        "vib_x_max":          round(vib_x_max, 4),
        "vib_x_range":        round(vib_x_range, 4),
        "temp_K_mean":        round(temp_mean, 2),
        "quality_mean":       round(quality_mean, 2),
        "INA226_OK_pct":      round(ina_ok_pct, 1),
        "IMU_OK_pct":         round(imu_ok_pct, 1),
        "THERMAL_OK_pct":     round(therm_ok_pct, 1),
        # Session
        "sessions_recorded":  len(sessions),
        "session_frames":     sessions[0]["n_frames"] if sessions else 0,
        "firmware_stderr":    fw_stderr.strip(),
    }

    # Pass criteria
    result["passed"] = (
        delivery_pct >= 95.0
        and diag.n_crc_errors == 0
        and diag.n_sync_errors == 0
        and current_range > 0.5             # current actually wiggles
        and abs(vib_x_range) > 0.001        # vibration data present
        and 280.0 < temp_mean < 320.0       # NTC reasonable
        and ina_ok_pct > 95.0               # sensors stay up
        and imu_ok_pct > 95.0
        and therm_ok_pct > 95.0
        and quality_mean > 85.0             # all-healthy quality
        and len(sessions) >= 1
        and sessions[0]["n_frames"] > expected * 0.9
    )
    return result


if __name__ == "__main__":
    duration = 10.0
    rate = 1000.0
    if len(sys.argv) > 1: duration = float(sys.argv[1])
    if len(sys.argv) > 2: rate = float(sys.argv[2])
    r = run_test(duration_s=duration, rate_hz=rate)
    print("\n" + "=" * 72)
    print(" FAZ 19B — REAL SENSOR PIPELINE FIELD TEST")
    print("=" * 72)
    print(f"  Duration:           {r['duration_s']} s")
    print(f"  Expected frames:    {r['expected_frames']:,d}")
    print(f"  Frames decoded:     {r['frames_decoded']:,d}")
    print(f"  Delivery:           {r['delivery_pct']}%")
    print(f"  CRC errors:         {r['crc_errors']}")
    print(f"  Sync errors:        {r['sync_errors']}")
    print()
    print(f"  --- Sensor data propagation ---")
    print(f"  INA226 current_A:   min={r['current_A_min']}  max={r['current_A_max']}  range={r['current_A_range']}")
    print(f"  IMU vib_x:          min={r['vib_x_min']}  max={r['vib_x_max']}  range={r['vib_x_range']}")
    print(f"  NTC temp_K mean:    {r['temp_K_mean']} K  ({r['temp_K_mean'] - 273.15:.1f} °C)")
    print(f"  Quality mean:       {r['quality_mean']}")
    print(f"  Sensor-OK uptime:   INA={r['INA226_OK_pct']}%  IMU={r['IMU_OK_pct']}%  THERMAL={r['THERMAL_OK_pct']}%")
    print()
    print(f"  Sessions:           {r['sessions_recorded']}  ({r['session_frames']:,d} frames)")
    print()
    print(f"  ★ Verdict: {'PASS ✓' if r['passed'] else 'FAIL ✗'}")
    sys.exit(0 if r["passed"] else 1)
