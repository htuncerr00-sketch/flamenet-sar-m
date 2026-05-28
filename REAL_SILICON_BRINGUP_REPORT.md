# REAL_SILICON_BRINGUP_REPORT.md
## Faz 19A–19D Stack — Physical ESP32 Validation

**Status: PENDING — zero silicon evidence exists as of report creation**

> All host-test results (137/137 assertions, 37/37 CAN bridge assertions,
> 14/14 toolchain dry-run) are **NOT** silicon evidence. They are
> necessary preconditions, not sufficient proof. Every row in this report
> requires physical bench measurement before it can be marked PASS.

---

## 0. Report Metadata

| Field               | Value |
|---------------------|-------|
| Report created      | 2026-05-28 |
| Firmware commit     | `48b69d3` (branch `claude/amazing-feynman-XUXBf`) |
| Firmware binary     | `faz19c_safety/build/filament_winding_telem.bin` |
| Binary size         | 239,312 bytes (0x3a6d0) — 77% flash free |
| Target chip         | ESP32 (ESP-IDF v5.3, xtensa-esp-elf-gcc 13.2.0) |
| Operator            | *(fill in)* |
| Bench date          | *(fill in)* |
| Board serial        | *(fill in)* |
| Supply voltage      | *(fill in — measure with DMM before powering)* |

---

## 1. Hardware Pin Map — Verify Before Power-On

Cross-check each pin against the physical board before flashing.
A wiring mistake at this stage can destroy the chip.

| Signal        | GPIO | Direction | Expected connection        | Verified? |
|---------------|------|-----------|----------------------------|-----------|
| UART1 TX      |  17  | out       | USB-UART bridge RX         | [ ]       |
| UART1 RX      |  16  | in        | USB-UART bridge TX         | [ ]       |
| I²C SDA       |  21  | bidir     | INA226 SDA + MPU6050 SDA   | [ ]       |
| I²C SCL       |  22  | out       | INA226 SCL + MPU6050 SCL   | [ ]       |
| NTC ADC       |  36  | in        | NTC voltage divider (10kΩ) | [ ]       |
| TWAI TX       |   4  | out       | CAN transceiver TXD        | [ ]       |
| TWAI RX       |   5  | in        | CAN transceiver RXD        | [ ]       |
| UART0 TX      |   1  | out       | Boot/debug console only    | [ ]       |

**I²C pullups required:** 4.7 kΩ to 3.3 V on SDA and SCL.
**CAN termination required:** 120 Ω between CAN-H and CAN-L at each bus end.
**NTC circuit:** 10 kΩ pullup to 3.3 V → GPIO36; NTC to GND. β=3950.

---

## 2. Flash + Boot Verification (silicon ONLY)

### 2.1 Flash Command

```bash
# From faz19c_safety/ with ESP-IDF sourced:
. $IDF_PATH/export.sh
idf.py -p /dev/ttyUSB0 -b 460800 flash monitor

# OR, if idf.py is not available, use esptool directly:
python -m esptool --chip esp32 -p /dev/ttyUSB0 -b 460800 \
    --before default_reset --after hard_reset write_flash \
    --flash_mode dio --flash_size 2MB --flash_freq 40m \
    0x1000  build/bootloader/bootloader.bin \
    0x8000  build/partition_table/partition-table.bin \
    0x10000 build/filament_winding_telem.bin
```

### 2.2 Expected Boot Log (UART0, 115200 baud)

The following lines must appear within 5 s of reset, in this order:

```
I (NNN) app_main: Filament Winding Telemetry Firmware — Faz 19C
I (NNN) app_main: Reset reason: power-on
I (NNN) uart_stream: UART1 init OK: baud=921600 TX=GPIO17 RX=GPIO16 ring=4096B
I (NNN) app_main: CAN bridge started: TX=GPIO4 RX=GPIO5 @ 500000 bps
I (NNN) app_main: Telemetry streaming @ 1 kHz; sensors @ 100 Hz
I (NNN) can_bridge: CAN bridge host-mock init ...  <-- OR twai init OK if ESC connected
```

### 2.3 Actual Boot Log (silicon ONLY)

```
(paste raw UART0 output here)
```

| Check                                    | Expected      | Observed | Pass? |
|------------------------------------------|---------------|----------|-------|
| "Faz 19C" string present                 | yes           |          | [ ]   |
| Reset reason logged as "power-on"        | yes           |          | [ ]   |
| UART1 init logged (921600 baud, GPIO17)  | yes           |          | [ ]   |
| CAN bridge start logged                  | yes           |          | [ ]   |
| "1 kHz; sensors @ 100 Hz" logged         | yes           |          | [ ]   |
| No PANIC or LoadProhibited exception     | no panic      |          | [ ]   |
| Boot completes within 5 s of reset       | < 5 s         |          | [ ]   |

**Boot verdict:** [ ] PASS  [ ] FAIL  [ ] BLOCKED

---

## 3. UART Telemetry Stream Verification (silicon ONLY)

Telemetry flows on **UART1 (GPIO17/16) at 921600 baud**, NOT UART0.
Connect a second USB-UART adapter to GPIO17/16 for this step.

### 3.1 Capture Command

```bash
# From faz19c_safety/host_test/ (or faz19_firmware/host_test/):
python3 field_test_faz19.py 10 1000 /dev/ttyUSB1

# OR using serial_capture.py from bringup/:
python3 bringup/serial_capture.py --port /dev/ttyUSB1 --baud 921600 --duration 10
```

### 3.2 Expected Results

| Metric                        | Target           | Observed | Pass? |
|-------------------------------|------------------|----------|-------|
| Frames decoded in 10 s        | ≥ 9900           |          | [ ]   |
| Frame delivery rate           | ≥ 99%            |          | [ ]   |
| CRC errors                    | 0                |          | [ ]   |
| Sync errors                   | 0                |          | [ ]   |
| Sequence gaps                 | 0                |          | [ ]   |
| MAGIC bytes 0xAA 0x55         | all frames       |          | [ ]   |
| Payload length per frame      | 64 bytes         |          | [ ]   |
| Wire packet length per frame  | 66 bytes         |          | [ ]   |
| ts_us monotonically increasing| yes              |          | [ ]   |
| seq rolls over correctly       | yes (at 65535)   |          | [ ]   |

### 3.3 Actual Capture Output (silicon ONLY)

```
(paste python script output here)
```

**UART verdict:** [ ] PASS  [ ] FAIL  [ ] BLOCKED

---

## 4. I²C Device Enumeration (silicon ONLY)

Both INA226 (0x40) and MPU6050 (0x68) must ACK on the bus before sensor
reads are meaningful. If either is absent, check pullup resistors and
solder joints first.

### 4.1 Expected Log Lines at Boot

```
I (NNN) ina226: INA226 init OK (addr=0x40, CAL=2048)
I (NNN) mpu6050: MPU6050 init OK (addr=0x68)
I (NNN) sensor_pipeline: sensor_pipeline_init: INA226_OK IMU_OK THERMAL_OK
```

### 4.2 Observed Boot Log Lines (silicon ONLY)

```
(paste relevant lines here)
```

| Device   | I²C Addr | Expected Init Log           | Observed | ACK? |
|----------|----------|-----------------------------|----------|------|
| INA226   | 0x40     | "INA226 init OK"            |          | [ ]  |
| MPU6050  | 0x68     | "MPU6050 init OK"           |          | [ ]  |
| NTC/ADC  | GPIO36   | thermal_init → THERMAL_OK   |          | [ ]  |

### 4.3 Flag Bits in First Telemetry Frame

Decode the first received frame's `flags` field (uint16 BE, bytes [10..11]):

| Bit  | Flag              | Value Expected | Observed |
|------|-------------------|----------------|----------|
|  9   | TELEM_FLAG_INA226_OK  | 1          |          |
| 10   | TELEM_FLAG_IMU_OK     | 1          |          |
| 11   | TELEM_FLAG_THERMAL_OK | 1          |          |
|  0   | TELEM_FLAG_BOOT_OK    | 1          |          |
| 15   | TELEM_FLAG_ESC_FAULT  | 0 (no ESC) |          |

**I²C verdict:** [ ] PASS  [ ] FAIL  [ ] BLOCKED

---

## 5. INA226 Real Current Read (silicon ONLY)

### 5.1 Calibration Verification

INA226 configuration baked in firmware:
- I²C address: 0x40
- Shunt resistor: 2 mΩ
- Calibration register (CAL): 2048
- Current_LSB: 1.25 mA/bit
- Measurement range: ±40.96 A

### 5.2 Zero-Current Baseline

With **no load on the shunt** (power supply disconnected from load side),
read `current_A` from decoded telemetry frames for 5 s.

| Metric                          | Target          | Observed | Pass? |
|---------------------------------|-----------------|----------|-------|
| current_A idle noise floor      | ±50 mA (±0.05 A)|          | [ ]   |
| current_A mean at zero load     | < 0.1 A         |          | [ ]   |
| current_A sign correct (no flip)| positive = source|          | [ ]   |

### 5.3 Known-Load Calibration

Apply a known resistive load and measure with a calibrated reference ammeter.
Record readings at 3 operating points:

| Load point | Reference (A) | Firmware reads (A) | Error (%) | Pass (<3%)? |
|------------|---------------|--------------------|-----------|-------------|
| ~0 A (idle)|               |                    |           | [ ]         |
| ~5 A       |               |                    |           | [ ]         |
| ~10 A      |               |                    |           | [ ]         |

Acceptable calibration error: < 3% full-scale. If error > 3%:
→ Measure actual shunt resistance with 4-wire DMM.
→ Recalculate CAL = 0.00512 / (current_LSB × R_shunt).
→ Update `INA226_CALIBRATION` in `ina226.h`.

**INA226 verdict:** [ ] PASS  [ ] FAIL  [ ] BLOCKED

---

## 6. MPU6050 WHO_AM_I Verification (silicon ONLY)

### 6.1 WHO_AM_I Register

Register 0x75 must return 0x68 (MPU6050) or 0x72 (MPU6050C variant).
The firmware reads this during `mpu6050_init()` and logs the result.

### 6.2 Expected Log Line

```
I (NNN) mpu6050: WHO_AM_I=0x68 OK
```

### 6.3 Actual Observed (silicon ONLY)

```
WHO_AM_I value: 0x____  (fill in from UART0 log)
```

| Check                          | Expected      | Observed | Pass? |
|--------------------------------|---------------|----------|-------|
| WHO_AM_I = 0x68 or 0x72        | 0x68/0x72     |          | [ ]   |
| vib_x idle noise < 0.01 g RMS  | < 0.01 g      |          | [ ]   |
| vib_y idle noise < 0.01 g RMS  | < 0.01 g      |          | [ ]   |
| vib_z idle noise ≈ 1.0 g (grav)| 0.95..1.05 g  |          | [ ]   |
| accel values change on tilt    | yes           |          | [ ]   |

### 6.4 Noise Floor Measurement

With board held still on bench for 10 s, record vib_x/y/z from telemetry:

| Axis  | Mean (g) | Std dev (g) | Max excursion (g) |
|-------|----------|-------------|-------------------|
| vib_x |          |             |                   |
| vib_y |          |             |                   |
| vib_z |          |             |                   |

**MPU6050 verdict:** [ ] PASS  [ ] FAIL  [ ] BLOCKED

---

## 7. Thermal ADC Sanity Check (silicon ONLY)

### 7.1 NTC Circuit

- GPIO36 (ADC1 CH0)
- 10 kΩ NTC (β=3950, R0=10 kΩ at 25°C/298.15 K)
- 10 kΩ pullup to 3.3 V
- Steinhart-Hart β-form: 1/T = 1/T0 + (1/β) × ln(R/R0)

At room temperature (25°C = 298.15 K), NTC resistance ≈ 10 kΩ,
voltage divider output ≈ 1.65 V, ADC raw ≈ 2048/4095 × Vref.

### 7.2 Ice Bath Verification (optional but recommended)

NTC in ice-water slurry → expected 0°C = 273.15 K.

| Measurement point  | Reference temp (K) | Firmware temp_K | Error (K) | Pass (<2 K)? |
|--------------------|--------------------|-----------------|-----------|--------------|
| Ice bath (0°C)     | 273.15             |                 |           | [ ]          |
| Room temp (ambient)| (measure with thermometer)|           |           | [ ]          |

### 7.3 Range Check

| Check                                  | Target              | Observed | Pass? |
|----------------------------------------|---------------------|----------|-------|
| temp_K at room temp (23–27°C)          | 296–300 K           |          | [ ]   |
| temp_K changes when NTC is touched     | increases (body heat)|          | [ ]   |
| temp_K never NaN or 0                  | always finite       |          | [ ]   |
| TELEM_FLAG_THERMAL_OK set in flags     | bit 11 = 1          |          | [ ]   |

**Thermal verdict:** [ ] PASS  [ ] FAIL  [ ] BLOCKED

---

## 8. FreeRTOS Task Timing Observation (silicon ONLY)

### 8.1 Telemetry Rate Verification

The telemetry task must produce exactly 1000 frames/s ± 1%.
Measure by counting frames received over a 10 s window:

```bash
python3 bringup/serial_capture.py --port /dev/ttyUSB1 --baud 921600 --duration 10
# Look for: "frames/s" or count seq numbers over 10 s window
```

| Metric                          | Target         | Observed | Pass? |
|---------------------------------|----------------|----------|-------|
| Frame rate over 10 s            | 990–1010 Hz    |          | [ ]   |
| ts_us drift over 10 s           | < 1 ms/s       |          | [ ]   |
| seq gaps in 10 s window         | 0              |          | [ ]   |

### 8.2 GPIO Toggle Jitter (optional — requires scope)

To measure 1 ms `vTaskDelayUntil` jitter directly:
1. Add a GPIO toggle to `telemetry_task.c` (temporary, revert after bench)
2. Probe GPIO output on oscilloscope at 1 ms/div
3. Measure pulse-to-pulse jitter

```
GPIO jitter:  ±_____ µs  (target: < 50 µs per AD-011)
```

| Check                               | Target       | Observed | Pass? |
|-------------------------------------|--------------|----------|-------|
| Frame rate ≈ 1000 Hz (software meas)| 990–1010 Hz  |          | [ ]   |
| vTaskDelayUntil jitter (if scoped)  | < 50 µs      |          | [ ]   |
| health_monitor log every ~5 s        | yes          |          | [ ]   |
| No task watchdog trigger (WDT log)  | none         |          | [ ]   |

### 8.3 Stack/Heap Health (from health_monitor logs)

Look for health_monitor output on UART0 (every ~5 s):

```
I (NNN) health_monitor: telemetry HWM=____ heap_free=____
I (NNN) health_monitor: sensor_i2c HWM=____ heap_free=____
I (NNN) health_monitor: safety_mon HWM=____ heap_free=____
I (NNN) health_monitor: can_bridge HWM=____ heap_free=____
```

| Task          | Stack HWM (bytes free) | Min acceptable | Pass? |
|---------------|------------------------|----------------|-------|
| telemetry     |                        | > 512          | [ ]   |
| sensor_i2c    |                        | > 512          | [ ]   |
| safety_mon    |                        | > 512          | [ ]   |
| can_bridge    |                        | > 512          | [ ]   |
| health_mon    |                        | > 256          | [ ]   |
| Heap free     |                        | > 32 KB        | [ ]   |

**Timing verdict:** [ ] PASS  [ ] FAIL  [ ] BLOCKED

---

## 9. 10–30 Minute Soak Test (silicon ONLY)

### 9.1 Soak Procedure

```bash
# Start capture — 30 minutes = 1800 s:
python3 bringup/serial_capture.py \
    --port /dev/ttyUSB1 --baud 921600 \
    --duration 1800 \
    --output soak_30min_$(date +%Y%m%d_%H%M%S).bin

# Watch UART0 for WDT, PANIC, BROWNOUT lines during soak:
idf.py -p /dev/ttyUSB0 monitor
```

### 9.2 Soak Results

| Metric                          | Target        | Observed | Pass? |
|---------------------------------|---------------|----------|-------|
| Total frames in 30 min          | ≥ 1,782,000   |          | [ ]   |
| Frame delivery rate             | ≥ 99%         |          | [ ]   |
| CRC errors                      | 0             |          | [ ]   |
| Sync errors                     | 0             |          | [ ]   |
| WDT resets                      | 0             |          | [ ]   |
| PANIC events                    | 0             |          | [ ]   |
| BROWNOUT events                 | 0             |          | [ ]   |
| Heap free stable (no leak)      | Δ < 1 KB      |          | [ ]   |
| Stack HWMs stable (no growth)   | unchanged     |          | [ ]   |
| temp_K drift over soak           | < 5 K (thermal soak) |   | [ ]   |

### 9.3 Soak Start/End Health Snapshot

| Metric        | At T=0 | At T=30 min | Delta | Pass? |
|---------------|--------|-------------|-------|-------|
| Heap free (B) |        |             |       | [ ]   |
| temp_K        |        |             |       | [ ]   |
| current_A idle|        |             |       | [ ]   |
| Frame seq     |        |             |       | [ ]   |

**Soak verdict:** [ ] PASS  [ ] FAIL  [ ] BLOCKED

---

## 10. Safety Monitor Verification (silicon ONLY)

### 10.1 Brownout Reset Flag

Procedure: momentarily droop VCC to ~2.9 V using a bench supply current limit.
After recovery, first telemetry frame must have `TELEM_FLAG_BROWNOUT` (bit 12) set.

| Check                                         | Expected  | Observed | Pass? |
|-----------------------------------------------|-----------|----------|-------|
| TELEM_FLAG_BROWNOUT set after brownout reset  | bit 12 = 1|          | [ ]   |
| UART0 logs "Boot reason: BROWNOUT"            | yes       |          | [ ]   |
| System recovers cleanly (no hang)             | yes       |          | [ ]   |

### 10.2 Watchdog Reset Flag

Procedure: cannot safely test WDT without firmware modification.
**Deferred — mark as NOT VALIDATED on this bench session.**

### 10.3 Thermal Shutdown Threshold

Threshold: 373.15 K (100°C) for 3 consecutive samples @ 100 Hz = 30 ms.
**Do NOT test by heating board to 100°C.** Verify only that the correct
constant is compiled in (grep check):

```bash
grep SAFETY_TEMP_SHUTDOWN_K faz19c_safety/include/safety_monitor.h
# Expected: #define SAFETY_TEMP_SHUTDOWN_K   373.15f
```

(silicon ONLY — actual thermal trip not tested in this session)

**Safety verdict:** [ ] PASS  [ ] PARTIAL  [ ] FAIL

---

## 11. CAN/TWAI Bridge Verification (silicon ONLY)

> **Note:** CAN bridge requires external ESC with 120 Ω termination.
> If no ESC is connected, TELEM_FLAG_ESC_FAULT (bit 15) will be set
> after 500 ms. This is expected behavior — not a firmware bug.

### 11.1 Without ESC Connected

| Check                                        | Expected      | Observed | Pass? |
|----------------------------------------------|---------------|----------|-------|
| TWAI driver initialises (log line present)   | yes           |          | [ ]   |
| TELEM_FLAG_ESC_FAULT set after 500 ms        | bit 15 = 1    |          | [ ]   |
| No crash or panic from CAN init              | no crash      |          | [ ]   |

### 11.2 With ESC Connected (if available)

| Check                                        | Expected          | Observed | Pass? |
|----------------------------------------------|-------------------|----------|-------|
| ESC_CMD (0x100) frames sent every 10 ms      | CAN scope trace   |          | [ ]   |
| ESC_STATUS (0x101) frames received           | yes               |          | [ ]   |
| rpm field reflects ESC actual RPM            | yes               |          | [ ]   |
| TELEM_FLAG_ESC_FAULT = 0 when ESC healthy    | bit 15 = 0        |          | [ ]   |

**CAN verdict:** [ ] PASS  [ ] PARTIAL (no ESC)  [ ] FAIL

---

## 12. Auto-Reconnect Verification (silicon ONLY)

Procedure: disconnect USB-UART adapter from UART1 during soak, wait 5 s, reconnect.

| Check                                        | Expected    | Observed | Pass? |
|----------------------------------------------|-------------|----------|-------|
| RealESP32Link on PC detects disconnect       | within 1 s  |          | [ ]   |
| RealESP32Link reconnects after re-plug       | within 3 s  |          | [ ]   |
| Frame delivery resumes with no seq gap reset | yes         |          | [ ]   |

**Reconnect verdict:** [ ] PASS  [ ] FAIL  [ ] NOT TESTED

---

## 13. Known Risks — Check Before Closing Session

| Risk ID | Description                                           | Mitigated? |
|---------|-------------------------------------------------------|------------|
| R01     | I²C stuck-bus (SDA held low by device after power glitch) | [ ] checked |
| R04     | INA226 missing pullups → init fail, LKG at 0 A        | [ ] checked |
| R07     | NTC open circuit → thermal_read fails, LKG at 295.15 K | [ ] checked |
| R08     | Brownout causes infinite boot loop (WDT reset → BROWNOUT → WDT) | [ ] checked |
| R14     | T_N hardcoded 15 N, x_mm/a_deg hardcoded — do NOT run real winding | [ ] acknowledged |

---

## 14. Final Verdict

Fill in after completing all sections above.

| Section                         | Verdict |
|---------------------------------|---------|
| 2. Flash + Boot                 | PENDING |
| 3. UART Telemetry Stream        | PENDING |
| 4. I²C Enumeration              | PENDING |
| 5. INA226 Current Read          | PENDING |
| 6. MPU6050 WHO_AM_I             | PENDING |
| 7. Thermal ADC                  | PENDING |
| 8. FreeRTOS Task Timing         | PENDING |
| 9. 30-min Soak Test             | PENDING |
| 10. Safety Monitor              | PENDING |
| 11. CAN/TWAI Bridge             | PENDING |
| 12. Auto-Reconnect              | PENDING |

**Overall Silicon Validation Status:**

```
[ ] ★★★ READY FOR PRODUCTION ★★★  — all sections PASS, 0 FAIL, 0 BLOCKED
[ ] STOP — UNSAFE                  — specify which section failed and exact failure
[ ] PARTIAL                        — list which sections are BLOCKED and why
```

**Signed off by:** *(operator name)*
**Bench date:** *(date)*
**Firmware commit confirmed on device:** *(paste `idf.py monitor` boot log commit hash if logged)*

---

## Appendix A — Evidence Labeling Convention

All entries in this report that contain real hardware measurements must be
tagged `(silicon ONLY)`. Entries copied from host-test output are NOT
valid silicon evidence and must be clearly marked `(host-test, NOT silicon)`.

| Label              | Meaning                                           |
|--------------------|---------------------------------------------------|
| `(silicon ONLY)`   | Measured on physical ESP32 with real sensors      |
| `(host-test)`      | gcc-compiled host test, no hardware involved      |
| `(xtensa-build)`   | Cross-compiled for ESP32, not yet flashed/run     |
| `(PENDING)`        | Not yet measured — do not treat as evidence       |

**Preconditions already satisfied (host-test / xtensa-build only):**

| Evidence                         | Label            | Result   |
|----------------------------------|------------------|----------|
| INA226 driver unit tests         | (host-test)      | 24/24 ✓  |
| MPU6050 driver unit tests        | (host-test)      | 24/24 ✓  |
| thermal driver unit tests        | (host-test)      | 20/20 ✓  |
| sensor_pipeline integration tests| (host-test)      | 36/36 ✓  |
| race_cache stress test           | (host-test)      | 4/4 ✓    |
| safety_monitor unit tests        | (host-test)      | 29/29 ✓  |
| CAN bridge unit tests            | (host-test)      | 37/37 ✓  |
| xtensa-esp32 build               | (xtensa-build)   | 239 KB ✓ |
| Toolchain dry-run                | (host-test)      | 14/14 ✓  |

None of the above rows constitute silicon evidence.

---

## Appendix B — Failure Response Guide

| Symptom                          | First action                                    |
|----------------------------------|-------------------------------------------------|
| No UART0 output after flash      | Check USB cable; try `esptool.py --chip esp32 chip_id` |
| Boot loop / repeated resets      | Check VCC stability; reduce idf.py monitor baud |
| INA226 init fail                 | Measure SDA/SCL with DMM; check 4.7 kΩ pullups  |
| MPU6050 WHO_AM_I wrong           | Check AD0 pin grounded; try 0x69 address         |
| temp_K = 295.15 (LKG)           | Check ADC GPIO36 connection; measure V with DMM  |
| TELEM_FLAG_ESC_FAULT stuck       | Expected if no ESC; otherwise check GPIO4/5 + 120Ω |
| CRC errors in telemetry          | Check UART1 GPIO17 connection; verify 921600 baud |
| Heap free decreasing             | Run `valgrind`-equivalent: idf.py heap profiling |
| Stack overflow                   | Increase stack in `xTaskCreate` call for that task |
