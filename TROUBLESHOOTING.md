# Troubleshooting

Fixes for the most common problems when running the desktop simulation on
Windows.

---

## "Python not found on PATH"

**Symptom:**
```
ERROR: Python not found on PATH.
```

**Fix:**
1. Open Windows Settings → Apps → search "Python" → verify it is installed.
2. If installed but not on PATH: open the installer again → "Modify" →
   check "Add Python to environment variables".
3. Close and reopen the command prompt, then retry.

---

## "No module named 'PySide6'"

**Symptom:**
```
ModuleNotFoundError: No module named 'PySide6'
```

**Fix:**
```cmd
install_deps_windows.bat
```
Or manually:
```cmd
pip install PySide6
```

---

## "No module named 'pyqtgraph'"

**Fix:**
```cmd
pip install pyqtgraph
```

---

## "No module named 'numpy'"

**Fix:**
```cmd
pip install numpy
```

---

## "No module named 'backend'"

**Symptom:**
```
ModuleNotFoundError: No module named 'backend'
```

**Cause:** The app is being run directly instead of through `sim_launcher.py`.

**Fix:** Always launch via the provided scripts:
```cmd
run_desktop_sim.bat
```
Do NOT run `python -m app.main` directly from inside `faz17_d2\`.

---

## "No module named 'real_esp32_link'" / FileNotFoundError: real_esp32_link.py

**Symptom:**
```
FileNotFoundError: real_esp32_link.py not found at ...faz18_bringup\real_esp32_link.py
```

**Cause:** Repository is incomplete — `faz18_bringup\real_esp32_link.py` is missing.

**Fix:** Re-download / re-clone the repository. Verify the file exists:
```cmd
dir faz18_bringup\real_esp32_link.py
```

---

## App opens but charts are blank / frozen

**Cause A:** `pyqtgraph` not installed.
```cmd
pip install pyqtgraph
```

**Cause B:** Display driver issue on RDP or VM.
Set the software rasteriser before launching:
```cmd
set QT_OPENGL=software
run_desktop_sim.bat
```

---

## 3D winding panel shows placeholder / black box

**Cause:** `PyOpenGL` not installed or no OpenGL support.

**Fix:**
```cmd
pip install PyOpenGL
```
All other panels are unaffected. The 3D panel falls back gracefully.

---

## "running scripts is disabled" (PowerShell)

**Symptom:**
```
.\run_desktop_sim.ps1 cannot be loaded because running scripts is disabled...
```

**Fix:**
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```
Re-run the script. This is a one-time change per user account.

---

## App crashes immediately with "qt.qpa.plugin: Could not load the Qt platform plugin"

**Cause:** PySide6 platform plugin missing, usually in a stripped venv or
unusual Python install.

**Fix A:** Reinstall PySide6:
```cmd
pip uninstall PySide6 PySide6-Addons PySide6-Essentials shiboken6
pip install PySide6
```

**Fix B:** Set the platform explicitly:
```cmd
set QT_QPA_PLATFORM=windows
run_desktop_sim.bat
```

---

## "DLL load failed" on import PySide6

**Cause:** Microsoft Visual C++ Redistributable not installed, or 32-bit Python
with 64-bit packages.

**Fix:**
1. Install **Microsoft Visual C++ Redistributable 2015–2022 (x64)** from
   Microsoft's website.
2. Confirm Python is 64-bit: `python -c "import struct; print(struct.calcsize('P')*8)"` → must print `64`.

---

## High CPU usage (>50% sustained)

**Cause:** The mock telemetry generates 1,000 frames/second. On very slow
machines this can be heavy.

**Fix:** Reduce the mock rate by passing a rate flag (not currently exposed in
the CLI, but the `MockESP32Link` constructor accepts `rate_hz`). As a
workaround, 30 Hz is sufficient to see all chart updates — the UI already
coalesces 1 kHz → 30 Hz.

---

## Session data not saving to SQLite

**Symptom:** Replay panel shows no sessions after running for 30+ seconds.

**Fix:** Check for write permission in the working directory:
```cmd
echo test > test_write.txt
del test_write.txt
```
If that fails, move the repository out of `C:\Program Files\` to a user-writable
location such as `C:\Users\<you>\Documents\filament-winding\`.

---

## Real ESP32 mode: "serial.serialutil.SerialException: [Error 2]"

**Symptom:**
```
serial.serialutil.SerialException: [Error 2] could not open port COM3
```

**Cause:** Wrong COM port or the ESP32 USB driver not installed.

**Fix:**
1. Open Device Manager → Ports (COM & LPT) → find the ESP32 device.
2. If it shows a yellow warning: install CP210x or CH340 USB-serial driver.
3. Note the actual COM port number (e.g., COM4) and retry:
   ```cmd
   run_desktop_sim.bat --link real --port COM4 --baud 921600
   ```

---

## Collecting a diagnostic log

If none of the above fixes work, capture the full traceback:

```cmd
run_desktop_sim.bat > crash_log.txt 2>&1
```

Then open `crash_log.txt` and look for the last `Traceback` block. Include
that block when reporting the issue.
