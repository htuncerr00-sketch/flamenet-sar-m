# Windows Setup Guide

Complete guide for setting up the Filament Winding Desktop on a fresh Windows
10 / 11 machine.

---

## Requirements

| Item | Minimum | Recommended |
|---|---|---|
| OS | Windows 10 1903 | Windows 11 |
| Python | 3.11 | 3.12 |
| RAM | 4 GB | 8 GB |
| Disk | 2 GB free | 5 GB free |
| GPU | Any (software fallback works) | DirectX 11 capable |

---

## Step 1 — Install Python

1. Go to **https://www.python.org/downloads/windows/**
2. Download the latest **Python 3.12.x** (or 3.11.x) Windows installer
   (choose the 64-bit version: `python-3.12.x-amd64.exe`)
3. Run the installer
4. **Check "Add Python to PATH"** before clicking Install
5. Click "Install Now"

Verify the install:
```cmd
python --version
```
Expected output: `Python 3.12.x` (or 3.11.x)

---

## Step 2 — Get the Repository

**Option A — zip download (no git required)**

1. Download the repository zip from GitHub
2. Extract it to a folder such as `C:\filament-winding\`

**Option B — git clone**

```cmd
git clone https://github.com/htuncerr00-sketch/flamenet-sar-m.git
cd flamenet-sar-m
```

---

## Step 3 — Install Python Dependencies

Open a Command Prompt in the repository folder and run:

```cmd
install_deps_windows.bat
```

Or with PowerShell (you may need to unblock first — see note below):

```powershell
.\install_deps_windows.ps1
```

This installs:
- `PySide6` — Qt6 GUI framework
- `pyqtgraph` — real-time charts
- `numpy` — numerical computations
- `PyOpenGL` — 3D winding panel (optional)
- `pyserial` — real ESP32 mode (optional)

Installation takes 1–3 minutes depending on internet speed.

> **PowerShell execution policy** — if you see *"running scripts is disabled"*:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```
> Then re-run `.\install_deps_windows.ps1`.

---

## Step 4 — Launch

Double-click `run_desktop_sim.bat`, or from a command prompt:

```cmd
run_desktop_sim.bat
```

PowerShell:
```powershell
.\run_desktop_sim.ps1
```

The desktop application opens within 5–10 seconds. Charts begin updating
immediately with simulated telemetry.

---

## Optional: Virtual Environment

If you prefer to keep packages isolated from the system Python:

```cmd
python -m venv venv
venv\Scripts\activate
pip install PySide6 pyqtgraph numpy PyOpenGL pyserial
run_desktop_sim.bat
```

Activate the venv each session with `venv\Scripts\activate` before launching.

---

## Directory Structure After Setup

```
flamenet-sar-m\
├── sim_launcher.py              ← Python launcher
├── run_desktop_sim.bat          ← double-click to launch (CMD)
├── run_desktop_sim.ps1          ← double-click to launch (PowerShell)
├── install_deps_windows.bat     ← run once
├── install_deps_windows.ps1     ← run once (PowerShell)
├── DESKTOP_SIMULATION_SETUP.md  ← simulation mode details
├── WINDOWS_SETUP.md             ← this file
├── TROUBLESHOOTING.md           ← common error fixes
├── faz17_d1\                    ← Python backend
├── faz17_d2\                    ← PySide6 desktop UI
├── faz18_bringup\               ← production serial link
├── faz19_firmware\              ← ESP-IDF firmware
└── ...
```

---

## Updating

```cmd
git pull
```

No reinstall needed unless new packages appear in the install script.

---

## Uninstalling

1. Delete the repository folder.
2. `pip uninstall PySide6 pyqtgraph numpy PyOpenGL pyserial`
3. Optionally uninstall Python via Windows Settings → Apps.
