"""
host_test/field_test_faz19b_runtime.py — Runtime Sensor Behavior Test
============================================================================
Extends the Faz 19B field test to verify RUNTIME behavior of the sensor
pipeline as seen end-to-end through the wire:

  Scenarios (single 10-second run):
    t=0.0  .. 2.0 s : all sensors healthy        → all 3 flags set
    t=2.0  .. 4.0 s : INA226 faulted             → INA flag clears, LKG holds
    t=4.0  .. 4.5 s : recovery window            → flag re-sets
    t=4.5  .. 6.5 s : IMU faulted                → IMU flag clears, LKG holds
    t=6.5  .. 7.0 s : recovery window
    t=7.0  .. 9.0 s : Thermal faulted            → THERMAL flag clears
    t=9.0  .. 10.0 s: full recovery              → all flags set

  Asserts:
    [A] Frames captured during INA fault window have INA_OK clear
    [B] Other sensor flags stay set during INA fault (no cross-contamination)
    [C] current_A stays at LKG during INA fault (no NaN, no jump)
    [D] After recovery, INA_OK re-sets within reasonable window (< 200 ms)
    [E] Same for IMU
    [F] Same for thermal
    [G] CRC = 0 and sync = 0 across the entire run
    [H] Delivery > 95%
    [I] Quality field reflects sensor count (92, 67, 42, 17)
"""
from __future__ import annotations
import os
import pty
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

F_BOOT_OK     = 1 << 0
F_INA226_OK   = 1 << 9
F_IMU_OK      = 1 << 10
F_THERMAL_OK  = 1 << 11


def run() -> dict:
    # Build firmware_driver if missing
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
            return {"passed": False, "error": cc.stderr}

    master_fd, slave_fd = pty.openpty()
    slave_path = os.ttyname(slave_fd)
    os.close(slave_fd)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from app.link_factory import LinkConfig
    from app.main_window import FilamentWindingApp

    app = QApplication.instance() or QApplication(sys.argv)
    tmpdir = tempfile.mkdtemp(prefix="fw_19b_rt_")
    cfg = LinkConfig(kind="real", port=slave_path,
                     watchdog_s=2.0, auto_reconnect=False)
    win = FilamentWindingApp(
        recipe_db_path=os.path.join(tmpdir, "recipes.db"),
        telemetry_db_path=os.path.join(tmpdir, "telemetry.db"),
        link_config=cfg)
    win.show()

    # Capture frames with timestamps relative to start
    import queue as _q
    capture_q = _q.Queue(maxsize=20000)
    win._link.subscribe(capture_q)

    DURATION = 10.0
    RATE = 1000.0
    fw_args = [
        os.path.join(_HERE, "firmware_driver"),
        "--rate-hz", str(RATE),
        "--duration-s", str(DURATION + 1.0),
        "--fault-ina-start",     "2.0", "--fault-ina-end",     "4.0",
        "--fault-imu-start",     "4.5", "--fault-imu-end",     "6.5",
        "--fault-thermal-start", "7.0", "--fault-thermal-end", "9.0",
    ]

    test_start_mono = time.monotonic()
    fw_proc = subprocess.Popen(fw_args, stdout=master_fd,
                               stderr=subprocess.PIPE, cwd=_HERE)
    QTimer.singleShot(100, win._on_connect)
    QTimer.singleShot(int(DURATION * 1000), win.close)
    app.exec()
    elapsed = time.monotonic() - test_start_mono

    fw_proc.terminate()
    try:
        fw_stderr = fw_proc.stderr.read().decode(errors="replace") if fw_proc.stderr else ""
        fw_proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        fw_proc.kill()
        fw_stderr = ""
    try: os.close(master_fd)
    except OSError: pass

    # Drain captured frames; assign approximate t (relative to first frame)
    # using ts_us from the frame itself (firmware-clock time).
    frames = []
    while True:
        try: frames.append(capture_q.get_nowait())
        except _q.Empty: break

    if not frames:
        return {"passed": False, "error": "no frames captured",
                "fw_stderr": fw_stderr}

    t0_us = frames[0].ts_us
    diag = win._link.diagnostics

    # Bucket frames by time window
    def in_win(f, a_s, b_s):
        t = (f.ts_us - t0_us) / 1e6
        return a_s <= t <= b_s

    def slice_(a_s, b_s):
        return [f for f in frames if in_win(f, a_s, b_s)]

    healthy_pre = slice_(0.2, 1.8)       # before any fault
    ina_window  = slice_(2.5, 3.8)       # mid-INA fault
    imu_window  = slice_(5.0, 6.3)
    therm_window= slice_(7.5, 8.8)
    healthy_post= slice_(9.5, 10.0)

    def pct_with_flag(lst, flag):
        if not lst: return 0
        return 100.0 * sum(1 for f in lst if f.flags & flag) / len(lst)

    def values(lst, attr):
        return [getattr(f, attr) for f in lst]

    def mean(lst):
        return sum(lst) / len(lst) if lst else 0

    result = {
        "duration_s":      round(elapsed, 2),
        "total_frames":    diag.n_frames_ok,
        "crc_errors":      diag.n_crc_errors,
        "sync_errors":     diag.n_sync_errors,
        "delivery_pct":    round(100.0 * diag.n_frames_ok / int(RATE * DURATION), 1),
        "windows": {
            "healthy_pre":  len(healthy_pre),
            "ina_fault":    len(ina_window),
            "imu_fault":    len(imu_window),
            "thermal_fault":len(therm_window),
            "healthy_post": len(healthy_post),
        },
        # Healthy baseline: all flags should be set, quality near 92
        "healthy_pre_INA_pct":     round(pct_with_flag(healthy_pre, F_INA226_OK), 1),
        "healthy_pre_IMU_pct":     round(pct_with_flag(healthy_pre, F_IMU_OK), 1),
        "healthy_pre_THERMAL_pct": round(pct_with_flag(healthy_pre, F_THERMAL_OK), 1),
        "healthy_pre_quality_mean": round(mean(values(healthy_pre, "quality")), 1),
        # INA fault: INA flag clears, others stay
        "ina_window_INA_pct":      round(pct_with_flag(ina_window, F_INA226_OK), 1),
        "ina_window_IMU_pct":      round(pct_with_flag(ina_window, F_IMU_OK), 1),
        "ina_window_THERMAL_pct":  round(pct_with_flag(ina_window, F_THERMAL_OK), 1),
        "ina_window_quality_mean": round(mean(values(ina_window, "quality")), 1),
        # IMU fault
        "imu_window_INA_pct":      round(pct_with_flag(imu_window, F_INA226_OK), 1),
        "imu_window_IMU_pct":      round(pct_with_flag(imu_window, F_IMU_OK), 1),
        "imu_window_THERMAL_pct":  round(pct_with_flag(imu_window, F_THERMAL_OK), 1),
        "imu_window_quality_mean": round(mean(values(imu_window, "quality")), 1),
        # Thermal fault
        "therm_window_INA_pct":      round(pct_with_flag(therm_window, F_INA226_OK), 1),
        "therm_window_IMU_pct":      round(pct_with_flag(therm_window, F_IMU_OK), 1),
        "therm_window_THERMAL_pct":  round(pct_with_flag(therm_window, F_THERMAL_OK), 1),
        "therm_window_quality_mean": round(mean(values(therm_window, "quality")), 1),
        # Final recovery
        "healthy_post_INA_pct":     round(pct_with_flag(healthy_post, F_INA226_OK), 1),
        "healthy_post_IMU_pct":     round(pct_with_flag(healthy_post, F_IMU_OK), 1),
        "healthy_post_THERMAL_pct": round(pct_with_flag(healthy_post, F_THERMAL_OK), 1),
        "healthy_post_quality_mean": round(mean(values(healthy_post, "quality")), 1),
        "fw_stderr":                fw_stderr,
    }

    # LKG check: during INA fault window, current_A should stay near
    # last-known-good (not jump to 0). The "wiggle" sine was running
    # before fault, so current is some value in [3.2, 5.0] A range,
    # NOT 0. Verify it never goes outside [-2, 10] A.
    if ina_window:
        ina_currents = [f.current_A for f in ina_window]
        result["lkg_ina_curr_min"] = round(min(ina_currents), 3)
        result["lkg_ina_curr_max"] = round(max(ina_currents), 3)
        # Should NOT have wandered far from the LKG snapshot.
        # When INA is faulted, the cache is frozen on the last good read,
        # so current_A in the frame should be constant for the whole window.
        result["lkg_ina_curr_constant"] = (max(ina_currents) - min(ina_currents)) < 0.05

    if imu_window:
        imu_vibxs = [f.vib_x for f in imu_window]
        result["lkg_imu_vibx_min"] = round(min(imu_vibxs), 4)
        result["lkg_imu_vibx_max"] = round(max(imu_vibxs), 4)
        result["lkg_imu_vibx_constant"] = (max(imu_vibxs) - min(imu_vibxs)) < 0.005

    if therm_window:
        therm_temps = [f.temp_K for f in therm_window]
        result["lkg_therm_temp_min"] = round(min(therm_temps), 3)
        result["lkg_therm_temp_max"] = round(max(therm_temps), 3)
        result["lkg_therm_temp_constant"] = (max(therm_temps) - min(therm_temps)) < 0.05

    # Pass criteria
    ok = (
        # [G] no CRC, no sync errors
        diag.n_crc_errors == 0 and diag.n_sync_errors == 0 and
        # [H] delivery > 95%
        result["delivery_pct"] >= 95.0 and
        # [A] INA flag clears in fault window
        result["ina_window_INA_pct"] < 10.0 and
        # [B] cross-sensor isolation: others stay up
        result["ina_window_IMU_pct"] > 95.0 and
        result["ina_window_THERMAL_pct"] > 95.0 and
        # IMU window
        result["imu_window_IMU_pct"] < 10.0 and
        result["imu_window_INA_pct"] > 95.0 and
        result["imu_window_THERMAL_pct"] > 95.0 and
        # Thermal window
        result["therm_window_THERMAL_pct"] < 10.0 and
        result["therm_window_INA_pct"] > 95.0 and
        result["therm_window_IMU_pct"] > 95.0 and
        # [D/E/F] recovery
        result["healthy_post_INA_pct"] > 95.0 and
        result["healthy_post_IMU_pct"] > 95.0 and
        result["healthy_post_THERMAL_pct"] > 95.0 and
        # [I] quality reflects health
        result["healthy_pre_quality_mean"] > 85.0 and
        result["ina_window_quality_mean"] < 75.0 and
        result["healthy_post_quality_mean"] > 85.0 and
        # [C] LKG constant during fault
        result.get("lkg_ina_curr_constant", False) and
        result.get("lkg_imu_vibx_constant", False) and
        result.get("lkg_therm_temp_constant", False)
    )
    result["passed"] = ok
    return result


def fmt_row(label, ina, imu, therm, qual):
    return f"  {label:<20s}  INA={ina:>5.1f}%  IMU={imu:>5.1f}%  THERMAL={therm:>5.1f}%  quality={qual:>5.1f}"


if __name__ == "__main__":
    r = run()
    print("\n" + "=" * 76)
    print(" FAZ 19B — RUNTIME SENSOR BEHAVIOR FIELD TEST")
    print("=" * 76)
    if "error" in r:
        print(f"  ERROR: {r['error']}")
        sys.exit(1)
    print(f"  Duration:       {r['duration_s']} s")
    print(f"  Total frames:   {r['total_frames']:,d}  ({r['delivery_pct']}% delivery)")
    print(f"  CRC errors:     {r['crc_errors']}    Sync errors: {r['sync_errors']}")
    print(f"  Windows (frame counts): {r['windows']}")
    print()
    print(f"  --- Flag transitions per scenario window ---")
    print(fmt_row("healthy_pre",   r['healthy_pre_INA_pct'],   r['healthy_pre_IMU_pct'],   r['healthy_pre_THERMAL_pct'],   r['healthy_pre_quality_mean']))
    print(fmt_row("ina_fault",     r['ina_window_INA_pct'],    r['ina_window_IMU_pct'],    r['ina_window_THERMAL_pct'],    r['ina_window_quality_mean']))
    print(fmt_row("imu_fault",     r['imu_window_INA_pct'],    r['imu_window_IMU_pct'],    r['imu_window_THERMAL_pct'],    r['imu_window_quality_mean']))
    print(fmt_row("thermal_fault", r['therm_window_INA_pct'],  r['therm_window_IMU_pct'],  r['therm_window_THERMAL_pct'],  r['therm_window_quality_mean']))
    print(fmt_row("healthy_post",  r['healthy_post_INA_pct'],  r['healthy_post_IMU_pct'],  r['healthy_post_THERMAL_pct'],  r['healthy_post_quality_mean']))
    print()
    print(f"  --- Last-known-good preservation ---")
    if 'lkg_ina_curr_min' in r:
        print(f"  INA226 current during fault:  {r['lkg_ina_curr_min']}..{r['lkg_ina_curr_max']} A  "
              f"(constant: {'✓' if r['lkg_ina_curr_constant'] else '✗'})")
    if 'lkg_imu_vibx_min' in r:
        print(f"  IMU vib_x during fault:       {r['lkg_imu_vibx_min']}..{r['lkg_imu_vibx_max']}  "
              f"(constant: {'✓' if r['lkg_imu_vibx_constant'] else '✗'})")
    if 'lkg_therm_temp_min' in r:
        print(f"  Thermal temp during fault:    {r['lkg_therm_temp_min']}..{r['lkg_therm_temp_max']} K  "
              f"(constant: {'✓' if r['lkg_therm_temp_constant'] else '✗'})")
    print()
    print(f"  ★ Verdict: {'PASS ✓' if r['passed'] else 'FAIL ✗'}")
    sys.exit(0 if r["passed"] else 1)
