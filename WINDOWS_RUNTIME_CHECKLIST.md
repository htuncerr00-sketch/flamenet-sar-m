# Windows Runtime Checklist

Use this checklist to verify a clean Windows machine is ready to run
Filament Winding CAM.  Work through each section top-to-bottom.

---

## Section 1 — Python Environment

- [ ] **Python version ≥ 3.11 installed**
  ```cmd
  python --version
  ```
  Expected: `Python 3.11.x` or `Python 3.12.x`
  Fix: install from https://python.org — check "Add to PATH"

- [ ] **Python is 64-bit**
  ```cmd
  python -c "import struct; print(struct.calcsize('P')*8, 'bit')"
  ```
  Expected: `64 bit`
  Fix: download the `amd64` installer, not the 32-bit one

- [ ] **pip works**
  ```cmd
  python -m pip --version
  ```
  Expected: `pip 24.x …`
  Fix: `python -m ensurepip --upgrade`

---

## Section 2 — Required Packages

Run these one at a time.  Each should print a version number, not an error.

- [ ] **PySide6**
  ```cmd
  python -c "import PySide6; print(PySide6.__version__)"
  ```
  Fix: `pip install PySide6`

- [ ] **pyqtgraph**
  ```cmd
  python -c "import pyqtgraph; print(pyqtgraph.__version__)"
  ```
  Fix: `pip install pyqtgraph`

- [ ] **NumPy**
  ```cmd
  python -c "import numpy; print(numpy.__version__)"
  ```
  Fix: `pip install numpy`

---

## Section 3 — Optional Packages

These are optional.  If missing, specific features are disabled but the
application still runs.

- [ ] **PyOpenGL** (3D winding visualization)
  ```cmd
  python -c "import OpenGL; print(OpenGL.__version__)"
  ```
  Fix: `pip install PyOpenGL`
  If absent: 3D Visualizer tab shows a placeholder panel

- [ ] **pyserial** (real ESP32 hardware mode)
  ```cmd
  python -c "import serial; print(serial.__version__)"
  ```
  Fix: `pip install pyserial`
  If absent: Hardware Mode button will fail at COM port open

---

## Section 4 — Backend Module Check

Verifies the Python source packages resolve correctly.

- [ ] **Backend aliasing works**
  ```cmd
  cd <repo-root>
  python -c "
  import sys
  sys.path.insert(0, 'faz17_d1/faz17_d1_backend')
  import faz17_d1.core.winding_planner as wp
  p = wp.generate_helical(wp.WindingParams())
  print('G-code OK:', p.n_circuits, 'circuits')
  "
  ```
  Expected: `G-code OK: <N> circuits`

- [ ] **Mock link connects**
  ```cmd
  python -c "
  import sys
  sys.path.insert(0, 'faz17_d1/faz17_d1_backend')
  import faz17_d1, faz17_d1.hardware
  sys.modules['backend'] = faz17_d1
  sys.modules['backend.hardware'] = faz17_d1.hardware
  import importlib; importlib.import_module('backend.hardware.esp32_link')
  from backend.hardware.esp32_link import MockESP32Link
  link = MockESP32Link(rate_hz=10, seed=42)
  link.connect()
  print('MockESP32Link connected OK')
  link.disconnect()
  "
  ```

---

## Section 5 — Graphics / Display

- [ ] **Qt can open a window**
  ```cmd
  python -c "
  from PySide6.QtWidgets import QApplication, QLabel
  app = QApplication([])
  w = QLabel('Qt OK'); w.show()
  import threading; threading.Timer(1.0, app.quit).start()
  app.exec()
  print('Qt window OK')
  "
  ```

- [ ] **OpenGL available** (optional)
  ```cmd
  python -c "
  from PySide6.QtWidgets import QApplication
  from PySide6.QtOpenGLWidgets import QOpenGLWidget
  print('OpenGL widget available')
  "
  ```

- [ ] **Software fallback works on RDP/VM**
  If you are on Remote Desktop or a virtual machine and the app shows a
  blank 3D panel, set this before launching:
  ```cmd
  set QT_OPENGL=software
  START_FILAMENT_CAM.bat
  ```

---

## Section 6 — Filesystem

- [ ] **Working directory is writable**
  ```cmd
  cd <repo-root>
  echo test > write_test.txt && del write_test.txt && echo WRITABLE
  ```

- [ ] **Workspace directory exists or can be created**
  The app creates `workspace/` automatically on first run.
  If it fails, check that the repo folder is not in `C:\Program Files\`.

- [ ] **Log directory is writable**
  After first run, confirm `logs/YYYYMMDD/startup_HHMMSS.log` exists.

---

## Section 7 — Hardware (skip if simulation only)

- [ ] **ESP32 USB driver installed**
  Open Device Manager → Ports (COM & LPT).
  The ESP32 should appear as `Silicon Labs CP210x` or `CH340`.
  If it shows a yellow warning icon, download and install the driver:
  - CP210x: Silicon Labs website
  - CH340: manufacturer website or search "CH340 Windows driver"

- [ ] **COM port identified**
  Note the port number (e.g., COM3, COM7) in Device Manager.

- [ ] **Baud rate 921600 supported**
  Most USB-serial adapters support this rate.  If you see CRC errors in
  the commissioning panel, try a shorter USB cable (< 1 m).

- [ ] **Only one app on the port at a time**
  Close `idf.py monitor` and any other serial terminal before launching.
  Multiple apps on the same COM port causes scrambled data.

---

## Section 8 — Application Launch Verification

Run through this after all sections above pass:

- [ ] `START_FILAMENT_CAM.bat` launches without error
- [ ] Splash screen appears and all dependency checks show ✓
- [ ] Home screen shows with live winding preview animation
- [ ] "Simulation Mode" button opens the main window
- [ ] 3D Visualizer tab shows a spinning mandrel with fiber paths
- [ ] Live Production charts update at ~30 FPS
- [ ] Recipe Editor loads and "Generate G-code…" produces output
- [ ] G-code can be saved to `workspace/gcode/`
- [ ] Application closes cleanly with ✕ button
- [ ] `logs/YYYYMMDD/startup_*.log` contains no CRITICAL lines

---

## Pass Criteria

| Item | Result |
|---|---|
| Python 3.11+ | |
| PySide6 installed | |
| pyqtgraph installed | |
| NumPy installed | |
| Backend resolves | |
| Qt window opens | |
| Splash passes all checks | |
| Home screen visible | |
| Simulation mode launches | |
| G-code generated + saved | |
| No crash log created | |

All items checked = **READY FOR USE**.
