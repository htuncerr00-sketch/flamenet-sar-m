# FIRST_SILICON_BRINGUP.md — First Real ESP32 Execution Record

**Phase:** Faz 19B (Faz 19C safety layer pending wiring)  
**Binary:** `faz19b_sensors/.../build/filament_winding_telem.bin` (228 KB, built 2026-05-27)  
**Toolchain:** xtensa-esp32-elf-gcc 13.2.0, ESP-IDF v5.3  
**Status:** ⏳ TEMPLATE — fill in during bring-up session

> This document transitions the project from `PASS [host-sim]` / `PASS [pty]`
> to `PASS [real-silicon]`.  Leave no field blank.  If a check cannot be
> performed, record the reason and the planned date.

---

## Pre-Flight Checklist

Complete all items before applying power.

| # | Item | Expected | Actual | OK? |
|---|------|----------|--------|-----|
| HW-01 | USB-serial adapter on UART0 (GPIO 1/3 or USB-OTG) | Device appears as `/dev/ttyUSB0` or `COM3` | `___________` | ☐ |
| HW-02 | UART1 telemetry adapter (GPIO 17=TX, 16=RX) | Separate device `/dev/ttyUSB1` or `COM4` | `___________` | ☐ |
| HW-03 | INA226 on I²C bus (SDA=GPIO 21, SCL=GPIO 22) | Pullups 4.7 kΩ to 3.3 V; address 0x40 | `___________` | ☐ |
| HW-04 | MPU6050 on I²C bus (same bus) | AD0 pin grounded; address 0x68; 100 nF Vcc decoupling | `___________` | ☐ |
| HW-05 | NTC thermistor on GPIO 36 (ADC1 CH0) | 10 kΩ pullup to 3.3 V; RC filter recommended | `___________` | ☐ |
| HW-06 | INA226 shunt resistor | Vishay WSL2512 2 mΩ ±1%; Kelvin 4-wire connection | `___________` | ☐ |
| HW-07 | ESP32 power supply | 3.3 V ±5%; ripple <50 mV under load | `___________` | ☐ |
| HW-08 | Firmware binary built | `build/filament_winding_telem.bin` exists | ☑ 228 KB | ☑ |

---

## Step 1 — Flash the Firmware

### Command
```bash
cd /path/to/flamenet-sar-m
./bringup/flash.sh /dev/ttyUSB0        # replace with your UART0 port
# or:  idf.py -C faz19b_sensors/faz19b_sensors/faz19b_sensors -p /dev/ttyUSB0 flash monitor
```

### Expected flash output
```
Connecting...
Chip is ESP32-D0WD-V3 (revision v3.1)
Features: WiFi, BT, Dual Core, 240MHz, VRef calibration in efuse, Coding Scheme None
...
Writing at 0x00010000... (100 %)
...
Hash of data verified.
Leaving...
Hard resetting via RTS pin...
```

### Actual flash output (copy from terminal)
```
[PASTE FLASH OUTPUT HERE]
```

---

## Step 2 — Boot Log Capture

Open `idf.py monitor` (or `python -m serial.tools.miniterm --raw /dev/ttyUSB0 115200`) immediately after flash. Capture the first 10 seconds.

### Expected boot log lines (in order)

```
I (xxx) app_main: Filament Winding Telemetry Firmware — Faz 19B
I (xxx) app_main: Reset reason: POWER_ON
I (xxx) uart_stream: UART1 initialized @ 921600 baud, TX=GPIO17, RX=GPIO16
I (xxx) telem_task: telemetry task started, period=1 tick(s)
I (xxx) sensor_task: sensor I²C task started @ 100 Hz
```

Expected health monitor log (after ~5 s):
```
I (5xxx) health: heap free=XXXXX  min_since_boot=XXXXX  iram_free=XXXXX
I (5xxx) health: stack HWM: telem=XXXX  sens=XXXX  health=XXXX  (bytes)
```

### Actual boot log (paste verbatim)
```
[PASTE BOOT LOG HERE — first 30 lines minimum]
```

### Boot log checklist

| Check | Expected | Actual | OK? |
|-------|----------|--------|-----|
| Reset reason string | `POWER_ON` | `___________` | ☐ |
| UART1 init message | GPIO17/16 @ 921600 | `___________` | ☐ |
| Telemetry task started | present | `___________` | ☐ |
| Sensor I²C task started | present | `___________` | ☐ |
| Any ERROR log lines | none | `___________` | ☐ |
| Any WARN log lines | none (or explain) | `___________` | ☐ |

---

## Step 3 — Sensor Register Verification

While `idf.py monitor` is running, confirm sensor init results from the log.

> If the firmware does not log register values, add one-time ESP_LOGI calls
> to ina226_init() and mpu6050_init() before production flash — these are
> diagnostic reads that do not affect the wire format.

| Sensor | Register | Expected | Actual | OK? |
|--------|----------|----------|--------|-----|
| MPU6050 WHO_AM_I (reg 0x75) | `0x68` | `0x____` | ☐ |
| INA226 Manufacturer ID (reg 0xFE) | `0x5449` ("TI") | `0x____` | ☐ |
| INA226 Die ID (reg 0xFF) | `0x2260` | `0x____` | ☐ |
| INA226 calibration register (reg 0x05) | `2048 = 0x0800` | `0x____` | ☐ |
| NTC ADC raw code @ room temp (~25°C) | ~2000–2500 (0–4095 range) | `____` | ☐ |

**Wiring notes if sensor init fails:**
- `INA226 NACK`: Check SDA/SCL pullups (4.7 kΩ to 3.3 V). Probe bus with logic analyser. Verify 0x40 address (A0=A1=GND).
- `MPU6050 NACK`: Verify AD0 grounded. Check 100 nF decoupling on Vcc. Probe GPIO 21/22.
- `Thermal init FAIL`: Check GPIO 36 is ADC-capable and not strapped. Source impedance must be <10 kΩ.

---

## Step 4 — Telemetry Stream Verification (automated)

Open a **second terminal** (UART0 monitor stays open in first terminal). Run:

```bash
cd /path/to/flamenet-sar-m
pip install pyserial   # if not already installed
python bringup/verify_bringup.py --port /dev/ttyUSB1 --soak-minutes 10
```

Replace `/dev/ttyUSB1` with the UART1 telemetry port (GPIO 17 TX).

### Phase 1 — First frame

| Metric | Target | Actual | OK? |
|--------|--------|--------|-----|
| First frame latency | < 5 s | `_____` s | ☐ |
| First frame seq | any | `_____` | ☐ |
| BOOT_OK flag set | yes | `_____` | ☐ |

### Phase 2 — Sensor health flags (30-second window)

| Flag | Target uptime | Actual | OK? |
|------|---------------|--------|-----|
| `TELEM_FLAG_INA226_OK` (bit 9) | ≥ 98% | `_____` % | ☐ |
| `TELEM_FLAG_IMU_OK` (bit 10) | ≥ 98% | `_____` % | ☐ |
| `TELEM_FLAG_THERMAL_OK` (bit 11) | ≥ 98% | `_____` % | ☐ |

### Phase 3 — Data plausibility (30-second window)

| Field | Target range | Actual min | Actual max | Actual mean | OK? |
|-------|-------------|-----------|-----------|------------|-----|
| `temp_K` | 273–348 K (0–75°C) | `_____` | `_____` | `_____` K | ☐ |
| `temp_K` (°C equivalent) | 0–75°C | `_____` | `_____` | `_____` °C | ☐ |
| `current_A` | ±45 A max; idle ~0–2 A | `_____` | `_____` | — | ☐ |
| `vib_rms` | < 0.05 g at idle | — | `_____` g | — | ☐ |
| `quality` | 92 (all 3 sensors healthy) | `_____` | `_____` | — | ☐ |
| NaN/Inf frames | 0 | — | — | `_____` | ☐ |
| SAFE_HALT frames | 0 | — | — | `_____` | ☐ |

### Phase 4 — Wire integrity (30-second window)

| Metric | Target | Actual | OK? |
|--------|--------|--------|-----|
| Frame delivery rate | ≥ 99% | `_____` % | ☐ |
| CRC errors | 0 | `_____` | ☐ |
| Sync drops (bytes) | < 100 | `_____` | ☐ |
| Sequence number jumps | 0 | `_____` | ☐ |

### Phase 4 — Estimated frame rate calculation check

At 921600 baud, 66 bytes/frame × 1000 Hz × 10 bits/byte = 660,000 bps = 71.6% utilisation.
Expected frames in 30 s window: `30,000 ± 300` (±1% allowance for scheduler jitter).
Actual: `__________`

---

## Step 5 — 10-Minute Soak Test

Results written automatically to `bringup/bringup_results_YYYYMMDD_HHMMSS.json`.

### Soak summary (copy from verify_bringup.py output)

```
[PASTE SOAK SUMMARY HERE]
```

### Soak metrics table

| Metric | Target | Actual | OK? |
|--------|--------|--------|-----|
| Soak duration | ≥ 600 s (10 min) | `_____` s | ☐ |
| Total frames | ≥ 594,000 (99% of 600,000) | `_____` | ☐ |
| Frame delivery | ≥ 99% | `_____` % | ☐ |
| CRC errors (soak) | 0 | `_____` | ☐ |
| INA226_OK uptime | ≥ 98% | `_____` % | ☐ |
| IMU_OK uptime | ≥ 98% | `_____` % | ☐ |
| THERMAL_OK uptime | ≥ 98% | `_____` % | ☐ |
| SAFE_HALT events | 0 | `_____` | ☐ |
| THERMAL_SHUTDOWN events | 0 | `_____` | ☐ |
| WATCHDOG_RESET events | 0 | `_____` | ☐ |
| temp_K range (full soak) | 273–348 K | `_____ – _____` K | ☐ |

---

## Step 6 — Stack and Heap (from idf.py monitor)

Read `health_monitor` log lines captured after 5 min of soak.

| Task | Stack HWM (bytes free) | Target | OK? |
|------|----------------------|--------|-----|
| `telem` | `_____` bytes | > 512 | ☐ |
| `sens` | `_____` bytes | > 512 | ☐ |
| `health` | `_____` bytes | > 256 | ☐ |
| Free heap at 5 min | `_____` bytes | > 100 KB | ☐ |
| Min free heap (since boot) | `_____` bytes | > 80 KB | ☐ |
| Heap delta (0→5min) | `_____` bytes | < 1 KB (no leak) | ☐ |

---

## Step 7 — Sensor Calibration Spot-Checks

These require reference instruments. Record on the day of bring-up.

### INA226 Current Accuracy

| Reference current (A) | ESP32 reading (A) | Error (%) | OK? |
|----------------------|-------------------|-----------|-----|
| 0.00 A (open circuit) | `_____` A | `_____` % | ☐ |
| 5.00 A | `_____` A | `_____` % | ☐ |
| 10.00 A | `_____` A | `_____` % | ☐ |
| 20.00 A (if possible) | `_____` A | `_____` % | ☐ |

Pass criterion: error < 3% at each point.  If > 3%, recalculate `INA226_CALIBRATION` constant.

### NTC Temperature Accuracy

| Reference temp | Expected temp_K | Actual temp_K from wire | Error (K) | OK? |
|---------------|----------------|------------------------|-----------|-----|
| Ice bath (0°C) | 273.15 K | `_____` K | `_____` | ☐ |
| Room temp (~25°C) | 298.15 K | `_____` K | `_____` | ☐ |

Pass criterion: error < 2 K.  If > 2 K, adjust `beta_K` in `thermal.h`.

### MPU6050 Idle Noise Floor

| Axis | RMS noise (g) | Target | OK? |
|------|--------------|--------|-----|
| vib_x | `_____` g | < 0.01 g | ☐ |
| vib_y | `_____` g | < 0.01 g | ☐ |
| vib_z | `_____` g | < 0.01 g | ☐ |

If noise > 0.01 g: check mechanical mounting and PCB decoupling.

---

## Step 8 — FreeRTOS Timing Verification (optional, requires scope)

> Required for production confidence; can be deferred to commissioning session.

Set up GPIO toggle in `telemetry_task` on each `vTaskDelayUntil` wake, then measure with oscilloscope.

| Measurement | Target | Actual | OK? |
|------------|--------|--------|-----|
| Telem period mean | 1.000 ms | `_____` ms | ☐ |
| Telem period jitter (σ) | < 50 µs | `_____` µs | ☐ |
| Sensor task period mean | 10.00 ms | `_____` ms | ☐ |
| Max single-cycle latency | < 2 ms | `_____` µs | ☐ |

---

## Bring-Up Session Record

| Field | Value |
|-------|-------|
| Date | `YYYY-MM-DD` |
| Engineer | `___________` |
| Hardware revision | `___________` |
| ESP32 chip revision | `___________` (from flash output: "revision v?.?") |
| USB-serial adapter (UART0) | `___________` (e.g., CP2102, CH340) |
| USB-serial adapter (UART1) | `___________` |
| Ambient temperature | `___________` °C |
| Power supply voltage measured | `___________` V |
| Total session duration | `___________` min |
| Issues encountered | `___________` |
| Next steps | `___________` |

---

## Verdict

Fill in after all steps complete:

| Phase | Result |
|-------|--------|
| P1 First frame | ☐ PASS  ☐ FAIL |
| P2 Sensor flags | ☐ PASS  ☐ FAIL |
| P3 Data plausibility | ☐ PASS  ☐ FAIL |
| P4 Wire integrity | ☐ PASS  ☐ FAIL |
| P5 Soak (10 min) | ☐ PASS  ☐ FAIL |
| Stack HWMs | ☐ PASS  ☐ FAIL |
| Calibration spot-checks | ☐ PASS  ☐ FAIL  ☐ DEFERRED |

**Overall:**

```
☐  ★★★  READY  ★★★  — all PASS above; silicon bring-up complete
☐  STOP — UNSAFE — see FAIL items
☐  PARTIAL — deferred items recorded above; proceed with documented risks
```

**Signed off by:** `___________`  
**Date:** `YYYY-MM-DD`

---

## Automated Results JSON

Paste the contents of `bringup/bringup_results_YYYYMMDD_HHMMSS.json` here
after running `verify_bringup.py`:

```json
[PASTE JSON HERE]
```

---

## Failure Mode Reference

| Symptom | Likely cause | Action |
|---------|-------------|--------|
| No frame within 10 s | UART1 TX not wired / wrong port | Check GPIO 17 → USB-serial RX; verify port selection |
| CRC errors > 0 | Baud mismatch; cable noise; USB-serial adapter error | Check adapter; try lower baud temporarily; shorten cable |
| INA226_OK = 0% | I²C NACK; wrong address; missing pullups | Logic-analyser on SDA/SCL; check 4.7 kΩ pullups |
| IMU_OK = 0% | MPU6050 NACK; AD0 floating | Measure AD0 voltage (should be 0 V); check pullup |
| THERMAL_OK = 0% | ADC channel mismatch; source impedance too high | Verify GPIO 36; add RC filter (10 kΩ + 100 nF) before ADC |
| temp_K > 373 K | NTC disconnected (pulled to VCC) or wrong pullup value | Measure NTC divider with multimeter; check R_pullup |
| SAFE_HALT event | Thermal or overcurrent threshold triggered | Check temp_K and current_A in frames before halt; check safety thresholds |
| Heap leak (min_free_heap dropping after 30s) | malloc in init path (post-init allocation) | Grep for malloc in main/; check sensor driver init calls |
| Stack HWM < 256 | Stack overflow imminent | Increase stack size in xTaskCreatePinnedToCore (currently 4096 words) |
| Seq jumps > 0 | UART TX ring overflow (burst loss) or frame loss | Check ring buffer depth (4 KB = 62 frames); verify no malloc in telem path |

---

*Template generated 2026-05-27 — fill in during first silicon bring-up session.*
