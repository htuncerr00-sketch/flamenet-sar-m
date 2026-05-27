# Commissioning Sequence — Filament Winding CAM Platform
## Faz 19B First Silicon Bring-Up

> **Evidence labels used throughout this document:**
> - `(host-sim)` — verified by firmware_driver.c + pty simulation
> - `(analytical)` — derived from timing formulas, no real hardware needed
> - `(silicon-pending)` — must be confirmed on real ESP32
> - `(silicon)` — confirmed on real hardware (fill in after each step)

---

## Sequence Diagram

```
OPERATOR                UART0 (115200)             UART1 (921600)          PC HOST
   │                   [Debug console]           [Binary telemetry]
   │
   ├─ Step 1: Setup ─────────────────────────────────────────────────────────────
   │  Connect USB adapter to ESP32 USB/UART bridge
   │  Identify ports: USB0=UART0, USB1=UART1 (or check dmesg/Device Manager)
   │
   ├─ Step 2: Start UART0 capture ───────────────────────────────────────────────
   │  Terminal A:
   │  python bringup/serial_capture.py --port /dev/ttyUSB0
   │
   │                    [Waiting for connection]
   │
   ├─ Step 3: Flash firmware ────────────────────────────────────────────────────
   │  Terminal B:
   │  ./bringup/flash_and_monitor.sh --no-monitor  (or use flash_and_monitor.sh)
   │
   │                    rst:0x1 (PowerOn)                                 [receiving]
   │                    ets Jun  8 2016 …
   │                    boot mode:(1,6)
   │                    configsip: 0, SPIWP:0xee
   │                    clk_drv:0x00,…
   │                    load:0x3fff0018,…          ← 2nd-stage bootloader start
   │                    Faz 19C — Filament Winding Telemetry
   │                    app_main: Reset reason: PowerOn (1)
   │                    uart_stream: UART1 @ 921600 baud, TX=GPIO17
   │
   ├─ Step 4: Verify boot banner ────────────────────────────────────────────────
   │  [Check Terminal A for "Filament Winding Telemetry"]           (silicon-pending)
   │  [Check Terminal A for "Reset reason: PowerOn"]                (silicon-pending)
   │  [Check Terminal A for "UART1 @ 921600 baud, TX=GPIO17"]       (silicon-pending)
   │
   │                    telemetry_task: telemetry task started
   │                    sensor_task: sensor I2C task started @ 100 Hz
   │                    health_monitor: started @ 0.2 Hz
   │
   ├─ Step 5: Smoke test (30 s) ─────────────────────────────────────────────────
   │  Terminal C:
   │  python bringup/verify_bringup.py --port /dev/ttyUSB1 --skip-soak
   │
   │                                               0xAA 0x55 [64 bytes] → frame OK
   │                    ────────────────────────── 1000 frames/sec (host-sim) ──────
   │
   │                                                              P1: first frame ✓
   │                                                              P2: sensor flags ✓
   │                                                              P3: plausibility ✓
   │                                                              P4: wire integrity ✓
   │
   ├─ Step 6: Sensor verification ──────────────────────────────────────────────
   │  [INA226 flag bit 9 set in every frame?]                     (silicon-pending)
   │  [IMU flag bit 10 set in every frame?]                       (silicon-pending)
   │  [Thermal flag bit 11 set in every frame?]                   (silicon-pending)
   │  [temp_K in [273.15, 348.15]?]                               (silicon-pending)
   │  [current_A plausible (≥ 0 on bench, < 1 A idle)?]          (silicon-pending)
   │  [vib_rms_max < 0.05 g at rest?]                             (silicon-pending)
   │
   ├─ Step 7: Health monitor first tick (~5 s after boot) ──────────────────────
   │
   │                    health: free heap: XXXXXX bytes
   │                    health: telem stack HWM: XXX bytes free
   │                    health: sens  stack HWM: XXX bytes free
   │
   │  [free heap > 100,000 bytes?]                                (silicon-pending)
   │  [telem stack HWM > 1024 bytes?]                             (silicon-pending)
   │  [sens stack HWM > 1024 bytes?]                              (silicon-pending)
   │
   ├─ Step 8: 10-minute soak ────────────────────────────────────────────────────
   │  (serial_capture.py running in Terminal A)
   │  Terminal C:
   │  python bringup/verify_bringup.py --port /dev/ttyUSB1 --soak-minutes 10
   │
   │                    [5-s status prints for 10 min]
   │                                                         rate ≈ 1000 Hz
   │                                                         CRC errors: 0
   │                                                         delivery > 99%
   │
   │  [Verdict: ★★★ READY ★★★ ?]                                  (silicon-pending)
   │
   ├─ Step 9: 30-minute production soak ────────────────────────────────────────
   │  Terminal C:
   │  python bringup/soak_test_runner.py --port /dev/ttyUSB1 --minutes 30 \
   │      --uart0-session ./bringup/logs/uart0/uart0_session_YYYYMMDD_HHMMSS.json
   │
   │                    [per-minute table printed live]
   │                    [jitter histogram at end]
   │
   │  [Final verdict: ★★★ READY ★★★ ?]                            (silicon-pending)
   │
   └─ Step 10: Sign-off ─────────────────────────────────────────────────────────
      Archive reports from bringup/reports/ and bringup/logs/
      Update SILICON_VALIDATION_STATUS.md with measured values
      Update FIRST_SILICON_BRINGUP.md §4 "Actual Results"
      Commit and push: "Faz 19B-HW: first silicon soak PASS"
```

---

## Operator Checklist

Complete each item in order. Do not proceed past a STOP condition.

### Pre-Flash

- [ ] **HW-01** ESP32 board connected via USB-serial adapter. Two separate ports visible:
  - UART0 port (debug, 115200): `_________________`
  - UART1 port (telemetry, 921600): `_________________`
- [ ] **HW-02** ESP-IDF v5.3+ environment sourced: `idf.py --version` shows ≥ v5.3
- [ ] **HW-03** `python -c "import serial"` succeeds (pyserial installed)
- [ ] **HW-04** Firmware binary exists: `faz19b_sensors/.../build/filament_winding_telem.bin`
  - Binary size: `________ KB` (expected: ~228 KB)
- [ ] **HW-05** Power supply capable of ≥ 500 mA at 3.3 V connected. Voltage measured: `________ V`

### Flash

- [ ] **FL-01** `serial_capture.py` started in Terminal A on UART0 port
- [ ] **FL-02** `flash_and_monitor.sh` (or `flash_and_monitor.ps1`) run in Terminal B
- [ ] **FL-03** Flash completed without `esptool.py` error

### Boot Verification

- [ ] **BV-01** Boot banner seen: `Faz 19C — Filament Winding Telemetry`
  - Actual banner: `_________________________________`
- [ ] **BV-02** Reset reason printed: `Reset reason: PowerOn (1)`
  - Actual reason: `_________________________________`
- [ ] **BV-03** UART1 init message: `UART1 @ 921600 baud, TX=GPIO17`
- [ ] **BV-04** `telemetry task started` seen in UART0 log
- [ ] **BV-05** `sensor I2C task started @ 100 Hz` seen in UART0 log
- [ ] **BV-06** No `Guru Meditation Error` in UART0 log  ← **STOP if missing**
- [ ] **BV-07** No `brownout detector was triggered`  ← **STOP if present**

### Smoke Test (verify_bringup.py --skip-soak)

- [ ] **SM-01** P1 PASS — first frame received in < 5 s. Actual: `________ s`
- [ ] **SM-02** P2 PASS — INA226 `_____`% / IMU `_____`% / Thermal `_____`%
- [ ] **SM-03** P3 PASS — temp_K mean: `________ K` (`________ °C`)
- [ ] **SM-04** P3 PASS — no NaN frames, no SAFE_HALT
- [ ] **SM-05** P4 PASS — delivery `______`%, CRC errors: `______`

### Sensor Calibration Checks

- [ ] **SC-01** INA226 flag (bit 9) set ≥ 98% of frames
  - If NOT set: check I²C pullups (4.7 kΩ to 3.3 V on SDA/SCL)
  - Check I²C address: INA226 default = 0x40 (A0=GND, A1=GND)
- [ ] **SC-02** IMU flag (bit 10) set ≥ 98% of frames
  - If NOT set: check AD0 pin on MPU6050 (must be GND for addr 0x68)
  - Verify WHO_AM_I returns 0x68
- [ ] **SC-03** Thermal flag (bit 11) set ≥ 98% of frames
  - If NOT set: check NTC wiring on ADC1 CH0 (GPIO 36)
  - Verify ADC reads < 4095 (not saturated at 3.3 V)
- [ ] **SC-04** `temp_K` value at room temperature ≈ 293–303 K. Actual: `________ K`
- [ ] **SC-05** `current_A` at idle (no load) ≈ 0 ± 0.1 A. Actual: `________ A`
- [ ] **SC-06** `vib_rms` at rest < 0.05 g. Actual: `________ g`

### Health Monitor (first tick after ~5 s)

- [ ] **HM-01** Free heap > 100,000 bytes. Actual: `________ bytes`
- [ ] **HM-02** Telemetry task stack HWM > 1024 bytes. Actual: `________ bytes`
- [ ] **HM-03** Sensor task stack HWM > 1024 bytes. Actual: `________ bytes`

### 10-Minute Soak

- [ ] **S10-01** verify_bringup.py --soak-minutes 10 PASS
- [ ] **S10-02** Delivery ≥ 99%. Actual: `______`%
- [ ] **S10-03** CRC errors = 0. Actual: `______`
- [ ] **S10-04** No SAFE_HALT events
- [ ] **S10-05** No WATCHDOG_RST events
- [ ] **S10-06** Jitter mean ≈ 1000 µs. Actual: `________ µs`
- [ ] **S10-07** Jitter σ < 100 µs. Actual: `________ µs`
- [ ] **S10-08** Heap stable (check serial_capture.py for heap drift < 8 KB)

### 30-Minute Production Soak

- [ ] **S30-01** soak_test_runner.py --minutes 30 completes without STOP
- [ ] **S30-02** Per-minute frame rate stable (no declining trend)
- [ ] **S30-03** Sensor uptimes: INA ≥ 98%, IMU ≥ 98%, Thermal ≥ 98%
- [ ] **S30-04** temp_K max < 348.15 K (75 °C). Actual max: `________ K`
- [ ] **S30-05** No freeze events with sensor flag SET
- [ ] **S30-06** Jitter histogram: ≥ 99% of samples in 0.5–2 ms bins
- [ ] **S30-07** Verdict: **★★★ READY ★★★**

### Post-Soak

- [ ] **PS-01** Reports archived: `bringup/reports/YYYYMMDD_HHMMSS/`
- [ ] **PS-02** SILICON_VALIDATION_STATUS.md updated with measured values
- [ ] **PS-03** FIRST_SILICON_BRINGUP.md §4 filled with actual measurements
- [ ] **PS-04** Commit with message: `"Faz 19B-HW: first silicon soak PASS"`

---

## STOP Conditions

If any of the following occur, **stop and diagnose before proceeding**:

| # | Condition | Likely Cause | Action |
|---|-----------|--------------|--------|
| S1 | `Guru Meditation Error` on UART0 | Stack overflow, null deref, divide-by-zero | Read backtrace; check stack HWMs; review fault address |
| S2 | Repeated brownout resets (≥ 3 in 60 s) | Insufficient power supply, voltage drop on load | Measure VCC with load; check decoupling caps; use bench supply |
| S3 | P1 FAIL — no frames in 10 s | UART1 not wired, wrong port, baud mismatch | Try UART0 port accidentally; check GPIO 17/16 connections |
| S4 | CRC errors > 0 | Cable noise, loose connector, wrong baud | Check cable quality; measure signal integrity; try shorter cable |
| S5 | SAFE_HALT flag set | Safety threshold exceeded on bench | Disconnect load; check temp_K (thermal shutdown at 373.15 K); check current_A |
| S6 | Delivery < 90% | UART RX buffer overflow, USB bandwidth, PCIe interrupt storm | Check USB hub; dedicate USB port; measure ISR timing |
| S7 | Free heap < 32,000 bytes after 30 s | Memory leak, oversized buffers | Reduce UART ring buffer; check task stack sizes |
| S8 | Stack HWM < 256 bytes | Stack too small, deep call chain | Increase task stack in `xTaskCreatePinnedToCore` |
| S9 | temp_K > 348.15 K (75 °C) during bench soak | Inadequate cooling, high ambient, sensor placement | Add heatsink; move sensor away from heat source |
| S10 | Watchdog reset > 2× in soak | telemetry_task blocked on I²C, I²C bus lock-up | Check for I²C timeout; verify bus pullups; check for address conflict |
| S11 | Jitter σ > 500 µs | FreeRTOS tick rate wrong (not 1000 Hz), priority inversion | Verify `FREERTOS_HZ=1000` in sdkconfig; verify task priorities |
| S12 | INA226 uptime < 50% | I²C bus fault, NACK from INA226 | Check 4.7 kΩ pullups to 3.3 V; scope SDA/SCL; check CAL register |
| S13 | MPU6050 uptime < 50% | AD0 not grounded, I²C address conflict | Measure AD0 pin voltage (must be 0 V); check WHO_AM_I |
| S14 | Thermal uptime < 50% | NTC open circuit, ADC wiring | Measure resistance of NTC at room temp (~10 kΩ); check ADC1 CH0 |
| S15 | THERMAL_SHUT flag set | temp_K ≥ 373.15 K (100 °C) on bench — physically wrong | Verify NTC calibration; check short circuit on NTC; β = 3950? |

---

## Expected Bring-Up Duration

| Phase | Estimated Duration | Notes |
|-------|--------------------|-------|
| Setup + cable check | 5–10 min | Port enumeration on Windows can take longer |
| Flash | 2–3 min | First flash; subsequent flashes ~30 s |
| Boot verification | 2–5 min | UART0 capture must show all 5 banner lines |
| Smoke test | 5 min | 30 s P4 window + result review |
| Sensor calibration | 10–20 min | Especially NTC β adjustment if off by > 2 K |
| Health monitor check | 2 min | Wait 10 s for first health tick |
| 10-min soak | 12–15 min | Including setup and report review |
| 30-min production soak | 35–40 min | Including report generation |
| **Total** | **~75–100 min** | First-time; subsequent sessions < 45 min |

---

## Quick Command Reference

```bash
# Terminal A — UART0 debug capture
python bringup/serial_capture.py --port /dev/ttyUSB0

# Terminal B — Flash
./bringup/flash_and_monitor.sh --no-monitor --port /dev/ttyUSB0
# Windows:
.\bringup\flash_and_monitor.ps1 -NoFlash:$false -Port COM3

# Terminal C — Smoke test (30 s, no soak)
python bringup/verify_bringup.py --port /dev/ttyUSB1 --skip-soak

# Terminal C — 10-min soak
python bringup/verify_bringup.py --port /dev/ttyUSB1 --soak-minutes 10 \
    --report-dir ./bringup/reports/session1

# Terminal C — 30-min production soak
python bringup/soak_test_runner.py --port /dev/ttyUSB1 --minutes 30 \
    --report-dir ./bringup/reports/soak_$(date +%Y%m%d_%H%M%S) \
    --uart0-session ./bringup/logs/uart0/uart0_session_YYYYMMDD_HHMMSS.json
```

---

*Commissioning sequence — Faz 19B First Silicon.*  
*Evidence: (host-sim) for timing/delivery targets; (silicon-pending) for all hardware measurements.*  
*All STOP conditions use adversarial engineering standard: prefer STOP over continue when uncertain.*
