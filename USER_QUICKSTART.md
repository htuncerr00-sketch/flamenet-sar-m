# Filament Winding CAM — User Quick Start

Get up and running in under 5 minutes.

---

## What This Software Does

Filament Winding CAM lets you:
- Design helical winding programs for composite mandrels
- Generate machine G-code (X + spindle coordinated motion)
- Monitor real-time tension, RPM, temperature, and vibration
- Visualize the 3D fiber path before winding
- Run in simulation mode to learn the software without any hardware

---

## Step 1 — Install Python (one time only)

1. Open **https://www.python.org/downloads/** in your browser
2. Click the big yellow **Download Python 3.12.x** button
3. Run the downloaded installer
4. **Check the box "Add Python to PATH"** — this is important
5. Click **Install Now**
6. When finished, close the installer

**Verify it worked:** open a Command Prompt and type `python --version`
You should see `Python 3.12.x` (or similar 3.11+).

---

## Step 2 — Install packages (one time only)

1. Find `install_deps_windows.bat` in the application folder
2. Double-click it
3. A black window will appear and run for 1–3 minutes
4. When it says **"Done"**, close the window

---

## Step 3 — Start the Application

Double-click **START_FILAMENT_CAM.bat**

A splash screen will appear for a few seconds while the application checks
that everything is set up correctly.  Then the main launcher appears.

---

## Step 4 — Choose Your Mode

The launcher shows 5 options:

| Button | Use when… |
|---|---|
| **Simulation Mode** | You want to explore the software without an ESP32 board |
| **Connect ESP32** | Your ESP32 board is plugged in via USB |
| **G-code Generator** | You just want to design a winding program |
| **3D Visualizer** | You want to preview the fiber path in 3D |
| **Settings** | You need to change COM port or workspace folder |

**First time?** Click **Simulation Mode**.

---

## Step 5 — Inside the Application

The main window has 7 tabs across the top:

| Tab | Purpose |
|---|---|
| Live Production | Real-time charts for tension, RPM, temperature, vibration |
| 3D Visualizer | Rotating 3D view of the mandrel and fiber path |
| Alarms & Safety | Safety limits and emergency stop |
| Replay | Replay recorded sessions |
| Recipe Editor | Design winding recipes and generate G-code |
| Commissioning | Connect and configure the ESP32 hardware |
| Predictive Maint. | Trend analysis and maintenance forecasting |

---

## Creating Your First G-code Program

1. Click **Recipe Editor** tab (or launch with G-code Generator)
2. Fill in:
   - **Recipe ID**: a short name like `pipe_55deg`
   - **Winding angle α**: 55° is a common starting point
   - **Layers**: 4 for a balanced laminate
   - **Tension**: 15 N (default)
   - **Feed rate**: 80 mm/s
3. Click **Generate G-code…**
4. A window shows the complete machine program
5. Click **Save to File** to export as `.nc`

---

## Saved Files

All your files are saved in the **workspace** folder:

```
workspace/
  gcode/         ← generated G-code files (.nc)
  projects/      ← saved recipes (SQLite database)
  exports/       ← exported reports
  logs/          ← application logs
```

The workspace location is shown in the status bar at the bottom of the window.
You can change it in **Settings**.

---

## If Something Goes Wrong

- **App won't start**: run `install_deps_windows.bat` again
- **Charts are blank**: check that `pyqtgraph` installed correctly
- **3D view is black**: `PyOpenGL` may be missing — run `pip install PyOpenGL`
- **ESP32 not detected**: check Device Manager for COM port, install USB driver

See **TROUBLESHOOTING.md** for detailed fixes.

---

## Quick Keyboard Shortcuts

| Key | Action |
|---|---|
| `Ctrl+W` | Emergency stop |
| `Ctrl+H` | Home axes |
| `Ctrl+R` | Start / stop recording |
| `F1` | Jump to Alarms tab |
| `F2` | Jump to 3D Visualizer |
| `F3` | Jump to Recipe Editor |
