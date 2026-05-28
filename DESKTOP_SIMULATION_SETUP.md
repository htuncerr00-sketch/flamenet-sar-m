# Desktop Simulation Setup

Run the full Filament Winding PySide6 desktop application on Windows **without
any ESP32 hardware**. A synthetic telemetry stream replaces the real sensor
pipeline so every panel is functional.

---

## Quick Start (3 steps)

```
1.  install_deps_windows.bat          (one time)
2.  run_desktop_sim.bat               (every time)
```

PowerShell equivalent:
```powershell
1.  .\install_deps_windows.ps1
2.  .\run_desktop_sim.ps1
```

The app opens in simulation mode automatically. No flags, no config edits.

---

## What "Simulation Mode" Means

Simulation mode uses `MockESP32Link`, the built-in software simulator that
ships with the backend. It generates a deterministic 1 kHz telemetry stream
identical in wire format to a real ESP32.

| Property | Value |
|---|---|
| Telemetry rate | 1,000 frames / second (same as real firmware) |
| UI update rate | 30 Hz (charts, gauges, alarm panel) |
| Random seed | 42 (bit-reproducible across runs) |
| Protocol | Frozen wire format — byte-identical to real ESP32 |

### Simulated sensor values

| Field | Simulation behaviour |
|---|---|
| `x_mm` | Sinusoidal carriage motion 40 mm → 190 mm |
| `a_deg` | Continuous spindle rotation at ~5 RPM |
| `T_N` | Tension 15 N ± 0.3 N Gaussian noise |
| `rpm` | 5.0 RPM nominal |
| `vib_x/y/z` | Low-amplitude white noise ~0.01 g |
| `temp_K` | 295.15 K rising at 0.1 K/s |
| `current_A` | 2.5 A ± 0.1 A |
| `quality` | ~92% (all sensor flags healthy) |
| `flags` | BOOT_OK + TENSION_OK + TEMP_OK + RPM_OK + VIBRATION_OK |

### What the mock does NOT simulate

- INA226 / MPU6050 / NTC fault injection (flags always healthy)
- CAN/TWAI ESC heartbeat
- Real-time jitter and cable noise
- Reconnect / watchdog events

---

## Application Panels

All 7 panels are fully functional in simulation mode:

| Panel | What you see |
|---|---|
| Live Production | 30 FPS rolling charts for tension, RPM, temp, current, vibration |
| 3D Winding | Helical path visualization on rotating mandrel |
| Alarms | Safety bounds checking active; no alarms expected in simulation |
| Recipe Editor | Create / edit / save winding recipes to SQLite |
| Commissioning | Port picker (no port required in mock mode), live diagnostics |
| Replay | Recorded session playback after running for a few seconds |
| Predictive Maintenance | ML-based anomaly scoring on the mock stream |

---

## Expected Resource Usage

| Resource | Typical |
|---|---|
| CPU (simulation + UI) | 5–15% single core |
| RAM | 150–250 MB |
| Disk (SQLite session) | ~4 MB / 10 min |
| GPU | Near zero (software rasteriser on most VMs / RDP sessions) |

---

## Switching to Real ESP32

Once hardware arrives:

```bat
run_desktop_sim.bat --link real --port COM3 --baud 921600
```

```powershell
.\run_desktop_sim.ps1 --link real --port COM3 --baud 921600
```

Or set environment variables permanently:
```
FW_LINK_KIND=real
FW_LINK_PORT=COM3
FW_LINK_BAUD=921600
```

The commissioning panel shows live byte counters, CRC errors, and sync
recoveries; use it to verify signal quality before starting a winding cycle.

---

## Known Limitations in Simulation Mode

1. **No real sensor calibration** — tension, RPM, encoder values are synthetic.
2. **3D panel requires PyOpenGL** — if PyOpenGL is absent the panel shows a
   placeholder; all other panels are unaffected.
3. **Replay panel** — session starts recording immediately; wait ~30 s before
   using replay scrubber to have meaningful data.
4. **No ESC feedback** — CAN bridge flag (`TELEM_FLAG_ESC_FAULT`) is not set
   in simulation; the mock reports all flags healthy.
5. **Clock drift** — mock timestamps are wall-clock based; expect ±1 ms jitter
   vs real firmware's `vTaskDelayUntil` ±50 µs.

---

## File Reference

| File | Purpose |
|---|---|
| `sim_launcher.py` | Python launcher — wires backend aliases, runs app |
| `run_desktop_sim.bat` | Double-click Windows launcher (CMD) |
| `run_desktop_sim.ps1` | PowerShell launcher |
| `install_deps_windows.bat` | One-time dependency installer (CMD) |
| `install_deps_windows.ps1` | One-time dependency installer (PowerShell) |
| `WINDOWS_SETUP.md` | Step-by-step Windows environment setup |
| `TROUBLESHOOTING.md` | Fixes for common error messages |
