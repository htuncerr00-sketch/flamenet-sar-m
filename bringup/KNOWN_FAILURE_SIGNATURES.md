# KNOWN_FAILURE_SIGNATURES.md — Filament Winding CAM Firmware Troubleshooting Matrix

**Scope:** Faz 19B/19C ESP32 firmware (Faz 19B sensor integration + Faz 19C safety layer)  
**Hardware:** ESP32 dual-core (Xtensa LX6, 240 MHz), ESP-IDF v5.3  
**Task hierarchy:** sensor(12) > telem(10) > safety(3) > health(1)  
**Wire protocol:** `[0xAA][0x55][64-byte big-endian payload][CRC-16/CCITT]` = 66 bytes/frame at 1 kHz  
**Safety thresholds (Faz 19C):** thermal shutdown at 373.15 K (100°C), overcurrent at 45.0 A (3-sample gating)

Evidence key:
- **(host-sim)** — verified against gcc-compiled firmware + pty in host regression suite (108/137 assertions)
- **(analytical)** — derived from silicon datasheet, ESP-IDF source, or architectural reasoning; not yet run on real hardware
- **(silicon-pending)** — requires physical ESP32 to confirm; expected behaviour is stated but is a prediction

---

## QUICK TRIAGE FLOWCHART

```
START: Something wrong with the Faz 19B/19C system
              |
              v
     +--------+---------+
     | PC side: do you  |
     | get ANY frames?  |
     +------------------+
          |       |
         NO      YES
          |       |
          v       v
   +-----------+  +------------------------------+
   | UART mute |  | Are CRC errors > 0?          |
   +-----------+  +------------------------------+
          |             |            |
          v            YES           NO
   No 0xAA 0x55    (see #12)        |
   sync found?                      v
          |               +---------------------------+
         YES              | Are sensor flags healthy? |
          |               | (bits 9/10/11 in flags)   |
          v               +---------------------------+
   +-----------+               |            |
   | See #11   |              NO           YES
   | Desync    |               |            |
   +-----------+               v            v
          |             Which sensor?  +------------------+
          |              INA/IMU/NTC   | Is data frozen?  |
          |               (see #3-6)   | Fields constant  |
   No frames                          | across >1 sec?   |
   at all?                            +------------------+
          |                                |        |
         YES                             YES        NO
          |                               |         |
          v                          See #13     Check SAFE_HALT
   UART working?                    Sensor     flag (bit 8) in
   Check TX GPIO17                  Data       frames — see #15
          |                         Freeze
         NO
          |
          v
   +----------------+
   | See #1 UART    |
   | Garbage or     |
   | #2 Baud Rate   |
   +----------------+
          |
   Is chip rebooting?
   (seq resets to 0,
    rapid boot msgs?)
          |
    +-----+-----+
    |           |
   YES          NO
    |            \---> back to sensor checks above
    v
   Reboot loop!
   Check UART0 log:
   "reset reason: 3" -> see #8 Brownout
   "reset reason: 4" -> see #7 WDT
   "Guru Meditation" -> see #14 Boot Loop
   Stack corruption? -> see #9/#10
```

---

## STOP CONDITIONS

Do not continue commissioning if ANY of the following are observed. Investigate and resolve before proceeding.

| # | Stop Condition | Why it blocks commissioning |
|---|----------------|----------------------------|
| S-01 | `Guru Meditation Error` in UART0 log on any boot | Firmware crash — uncontrolled; further operation risks hardware damage |
| S-02 | `reset reason: 3 (BROWNOUT_RESET)` repeating every boot | Supply voltage below 2.4 V brownout threshold; ESP32 may corrupt flash on next write |
| S-03 | CRC error rate > 0.1% sustained over 30 seconds | Wire protocol integrity broken; all downstream data (safety, control, replay) is untrusted |
| S-04 | `TELEM_FLAG_SAFE_HALT` (bit 8) set in frames | Faz 19C safety threshold triggered — thermal or overcurrent condition declared; do not run winding operation |
| S-05 | `temp_K` in frames > 373.15 K (100°C) sustained | Thermal shutdown threshold — real physical temperature may be near sensor limit |
| S-06 | `current_A` in frames >= 45.0 A sustained | Overcurrent threshold — ESC or motor may be in fault state |
| S-07 | Stack HWM for any task < 128 bytes free | Stack overflow imminent; next function call or ISR may corrupt heap |
| S-08 | `min_free_heap` dropping monotonically after 60 seconds | Heap leak in post-init path; will eventually cause `ESP_ERR_NO_MEM` panic |
| S-09 | INA226 reads all returning 0xFFFF on SHUNT_VOLTAGE register | Open circuit or ADC overflow — current measurement is completely invalid; safety bounds cannot be enforced |
| S-10 | `seq` field never incrementing (stuck at 0) | Telemetry task not running; either never started or hung; system is non-functional |

---

## Failure Signatures

---

### 1. UART Garbage / Garbled Output

**Definition:** UART0 debug console (115200 baud) shows unreadable characters, random bytes, or partial ESP-IDF log messages on a terminal that worked previously.

| Symptom | Probable Cause | Confidence | Verification Method | Mitigation |
|---------|---------------|------------|--------------------|-----------:|
| Terminal shows `????` or random high-byte characters | Wrong baud rate on host terminal | [HIGH] | Run `python -m serial.tools.miniterm /dev/ttyUSB0 115200`; confirm baud matches | Set terminal to 115200 8N1, no flow control (analytical) |
| Partial log lines, then gibberish | Two programs reading same UART0 port simultaneously | [HIGH] | `lsof /dev/ttyUSB0` on Linux; `idf.py monitor` and `verify_bringup.py` both open? | Only one process may hold the port at a time (host-sim) |
| Garbled output only at 921600 baud on UART1 | USB-serial adapter (CP210x/CH340) does not support 921600 | [HIGH] | Check adapter model; CP2102N and FTDI FT232R support 921600; CH340 does NOT reliably | Use CP2102N or FT232H for UART1 telemetry; CH340 max ~460800 (analytical) |
| Clean output then sudden garbage burst | UART RX overrun — host cannot read fast enough | [MEDIUM] | Check `uart_overrun_err` in ESP-IDF event log; increase host read buffer | Use hardware flow control or increase pyserial `timeout` parameter (analytical) |
| Garbled after cable handling | EMI / ground loop on USB cable | [LOW] | Shorten cable, add ferrite bead, ensure shared GND between ESP32 and host | Use shielded cable <= 1 m; ensure USB hub has common ground (analytical) |

**Diagnostic steps:**

1. Connect to UART0 only with a known-working terminal at exactly 115200 8N1 no-parity no-flow-control.
2. Verify no other process holds the port: `fuser /dev/ttyUSB0 2>/dev/null`
3. Power-cycle ESP32 and capture the first 50 lines of boot log verbatim.
4. Confirm the first log line matches: `I (xxx) app_main: Filament Winding Telemetry Firmware — Faz 19B`
5. If UART0 is clean but UART1 (port for telemetry) is garbled: run `python3 bringup/verify_bringup.py --port /dev/ttyUSB1 --soak-minutes 0.5` and check the `crc_errors` and `sync_drops` counters.

**Expected vs Actual:**
- Expected UART0 first line: `I (nnn) app_main: Filament Winding Telemetry Firmware — Faz 19B`
- Expected UART1 packet: `AA 55` followed by 64 bytes, repeating at 1 kHz
- Actual if baud wrong: random non-ASCII bytes with no `AA 55` pattern

**Evidence:** (analytical) based on CP210x/CH340 datasheets and ESP-IDF UART driver limitations.

---

### 2. Baud Rate Mismatch

**Definition:** UART1 telemetry stream parsed by PC yields persistent CRC errors or no frame sync, despite clean UART0 debug output and confirmed wiring.

| Symptom | Probable Cause | Confidence | Verification Method | Mitigation |
|---------|---------------|------------|--------------------|-----------:|
| All frames fail CRC; `AA 55` pattern found intermittently | PC host opening port at wrong baud | [HIGH] | Print `RealESP32Link` port config before open; confirm 921600 | Set `--baud 921600` explicitly in `python -m app.main` invocation (host-sim) |
| `sync_drops` counter climbs continuously, no `AA 55` found | `sdkconfig.defaults` `CONFIG_FREERTOS_HZ=1000` not applied — default 100 Hz tick gives wrong `pdMS_TO_TICKS(1)` = 0 ticks | [HIGH] | In UART0 log: search for telemetry task log `period=1 tick(s)` — if it says `period=0`, tick rate is wrong | Confirm `sdkconfig.defaults` was merged before `idf.py build`; run `idf.py menuconfig` → FreeRTOS → tick rate to verify 1000 Hz (analytical) |
| Frame rate measured as ~100 Hz instead of 1 kHz | `CONFIG_FREERTOS_HZ` defaults to 100; `pdMS_TO_TICKS(1)` rounds to 0, `vTaskDelayUntil` fires as fast as scheduler allows | [HIGH] | Count `seq` increments per second; should be ~1000; if ~100, tick rate wrong | Flash with corrected `sdkconfig.defaults`; confirm `CONFIG_FREERTOS_HZ=1000` before build (analytical) |
| Intermittent CRC errors (< 0.1%) | Cable length > 1 m inducing signal reflections | [MEDIUM] | Shorten cable to < 0.5 m; re-test. Check scope for ringing on UART TX pin | Use termination resistor (100 Ω series on TX); shorten cable (analytical) |
| Baud rate drifts above temperature | ESP32 crystal frequency shifts with temperature | [LOW] | Measure actual baud with oscilloscope (period of UART start bit); compare to 921600 at 20°C vs 60°C | ESP32 XTAL accuracy ±20 ppm; at 921600 baud, ±18 bps drift — within UART spec. If failing, switch to USB-OTG CDC which is USB-clocked (analytical) |

**Diagnostic steps:**

1. Open UART0 and search boot log for `UART1 initialized @ 921600 baud, TX=GPIO17, RX=GPIO16`.
2. Open UART1 at 921600 in raw binary mode: `python3 -c "import serial, sys; s=serial.Serial('/dev/ttyUSB1',921600,timeout=1); print(s.read(66).hex())"` — first two bytes should be `aa55`.
3. If no `aa55`: confirm telemetry task log line `period=1 tick(s)` is present in UART0. If missing or `period=0`, the tick rate is wrong.
4. Run `python3 bringup/verify_bringup.py --port /dev/ttyUSB1 --soak-minutes 0.1` and inspect `crc_errors`, `sync_drops`, `frames_decoded`.
5. Measure UART1 TX with oscilloscope: start bit period should be 1/921600 = 1.085 µs ± 20 ppm.

**Expected vs Actual:**
- Expected: `crc_errors=0`, `sync_drops=0`, `frames_decoded >= 990` over 1 second
- Actual with baud mismatch: `sync_drops` grows monotonically, `crc_errors` near 100%

**Evidence:** (analytical); `period=0` ticks scenario confirmed by `pdMS_TO_TICKS(1)` returning 0 when `CONFIG_FREERTOS_HZ=100`.

---

### 3. I²C Bus Timeout / NACK

**Definition:** One or more sensors report persistent I²C errors in the boot log or diagnostics; `i2c_bus_n_timeouts()` or `i2c_bus_n_errors()` counters non-zero; sensor-OK flag bits (9, 10, or 11) in telemetry frames at 0%.

| Symptom | Probable Cause | Confidence | Verification Method | Mitigation |
|---------|---------------|------------|--------------------|-----------:|
| Both INA226 and MPU6050 fail init simultaneously | Missing or wrong-value I²C pullup resistors | [HIGH] | Probe SDA (GPIO 21) and SCL (GPIO 22) with voltmeter — should read 3.3 V idle; if reads ~0.8 V, pullup missing | Install 4.7 kΩ pullups to 3.3 V on both SDA and SCL (silicon-pending) |
| One sensor ACKs, other does not | Sensor address collision or one device powered off | [MEDIUM] | Logic analyser on I²C: watch for ACK bit at clock 9; address 0x40 (INA226) vs 0x68 (MPU6050) are distinct — no collision possible unless hardware wired to wrong address | Verify INA226 A0=A1=GND → 0x40; verify MPU6050 AD0=GND → 0x68 (silicon-pending) |
| I²C works for 30 s then NACKs appear | Bus capacitance too high from long traces | [MEDIUM] | Reduce I²C speed: change `I2C_BUS_FREQ_HZ` in `i2c_bus.h` from 400000 to 100000; retest | Shorten SDA/SCL traces; reduce pullup to 2.2 kΩ if adding more devices; 100 kHz fallback (analytical) |
| NACKs only under current load | Ground bounce on shared GND when ESC switches | [MEDIUM] | Observe I²C errors correlated with current steps; check GND impedance | Separate AGND and PGND; add 100 nF + 10 µF decoupling on INA226 Vcc (analytical) |
| Occasional NACK (< 1% of transactions) | I²C transaction timeout `I2C_BUS_TIMEOUT_MS=20` ms too tight for a stretched slave | [LOW] | Check if slow device is performing internal calibration; increase timeout to 50 ms | Increase `I2C_BUS_TIMEOUT_MS` in `i2c_bus.h` and rebuild; LKG logic survives temporary NACKs (host-sim) |

**Diagnostic steps:**

1. On UART0 after power-on, look for `i2c_bus: I²C0 init: SDA=21 SCL=22 freq=400000 Hz` — confirms driver installed.
2. Sensor init is logged implicitly: if `INA226_OK` (bit 9) never sets in telemetry frames, INA226 init failed.
3. Run `verify_bringup.py` for 30 seconds; inspect `ina226_ok_pct`, `imu_ok_pct`, `thermal_ok_pct`.
4. Use logic analyser (or `i2cdetect` if another MCU is on the bus): scan for 0x40 and 0x68.
5. Measure SDA/SCL idle voltage with multimeter: should be 3.3 V ± 0.1 V.
6. Check `i2c_bus_n_timeouts()` and `i2c_bus_n_errors()` via health monitor or a diagnostic log line added to `sensor_pipeline_get_health()`.

**Expected vs Actual:**
- Expected: both 0x40 and 0x68 respond to `i2cdetect`; `ina226_ok_pct ≥ 98%`, `imu_ok_pct ≥ 98%`
- Actual with missing pullups: SDA idle at ~0.5 V; logic analyser shows no ACK bit; all transactions return `ESP_ERR_TIMEOUT`

**Evidence:** (silicon-pending) — I²C pullup requirement confirmed by ESP-IDF documentation and INA226/MPU6050 datasheets; exact threshold pending PCB measurement.

---

### 4. INA226 Returning 0xFFFF

**Definition:** INA226 `SHUNT_VOLTAGE` register (0x01) or `CURRENT` register (0x04) reads 0xFFFF consistently; `current_A` in telemetry frames shows approximately `65535 × 0.00125 A ≈ 81.9 A` (unsigned interpretation) or wraps negative.

| Symptom | Probable Cause | Confidence | Verification Method | Mitigation |
|---------|---------------|------------|--------------------|-----------:|
| `current_A` ≈ ±81.9 A with no load connected | INA226 SHUNT_VOLTAGE overflow: shunt voltage exceeds ±81.92 mV (the ±32768 raw count limit at 2.5 µV/bit) | [HIGH] | Measure shunt voltage directly: V_shunt = R_shunt × I = 2 mΩ × I. At 81.9 A: V_shunt = 163.8 mV — far above the ±81.92 mV limit. Real current must be > 40 A | Check that load is off; measure actual current with clamp meter (silicon-pending) |
| 0xFFFF on SHUNT register at zero load | Open-circuit shunt: shunt resistor not soldered or trace broken | [HIGH] | Measure resistance across shunt pads with ohmmeter — should be ≈ 2 mΩ; open circuit reads infinite | Rework shunt solder joint; verify Kelvin 4-wire connection (silicon-pending) |
| 0xFFFF on CURRENT register despite valid SHUNT | CAL register (0x05) written as 0 — division by zero in INA226 | [HIGH] | Read CAL register after init: `i2c_bus_read_reg16_be(0x40, 0x05, &val)` — should return 2048 (0x0800); if 0, write failed | Verify `ina226_init()` returns `I2C_OK`; check write error handling in `ina226.c:L56` (host-sim) |
| 0xFFFF intermittently under load | Shunt resistor self-heating shifts resistance; transient spike pushes shunt voltage above limit | [MEDIUM] | Log raw SHUNT_V register for 10 s under load; count 0xFFFF occurrences | Increase averaging: change INA226 CONFIG `AVG` bits from 4 samples to 16 (bits 11:9 = 0b100); reduces spike sensitivity (analytical) |
| MFG_ID returns 0xFFFF | Device not present or bus fault before register read | [HIGH] | `ina226_init()` checks `mfg_id != 0x5449` ("TI") and refuses init; UART0 will show no INA226 init success | Fix I²C bus first (see failure #3); 0xFFFF on MFG_ID = no ACK received (host-sim) |

**Diagnostic steps:**

1. Verify MFG_ID register returns 0x5449: add a log line in `ina226_init()` after the read: `ESP_LOGI(TAG, "INA226 MFG_ID=0x%04X", dev->mfg_id)`.
2. Read CAL register back after write — it must equal 2048. Log: `ESP_LOGI(TAG, "INA226 CAL=0x%04X", cal_readback)`.
3. With shunt in circuit but zero load current: SHUNT_VOLTAGE register should return a value very close to 0x0000 (within ±10 counts = ±25 µV offset).
4. If SHUNT_VOLTAGE reads 0x7FFF or 0xFFFF (positive or negative overflow): actual current exceeds ±40 A. Disconnect load and re-test.
5. Measure shunt resistance with 4-wire Kelvin technique: target 2.0 mΩ ± 1% (Vishay WSL2512). If open (>10 Ω): shunt is not making contact.

**Expected vs Actual:**
- Expected: MFG_ID = `0x5449`, CAL = `0x0800` = 2048, SHUNT_V near 0 at zero load, CURRENT near 0
- Actual with open shunt: SHUNT_V = `0x7FFF` (positive rail saturation); CURRENT = `0x7FFF`; `current_A` = +40.95 A erroneously

**Evidence:** (host-sim) for CAL=0 scenario; (silicon-pending) for open-circuit shunt; (analytical) for overflow math.

---

### 5. MPU6050 WHO_AM_I Mismatch

**Definition:** `mpu6050_init()` returns error because `WHO_AM_I` register (0x75) does not return 0x68; initialization fails; `TELEM_FLAG_IMU_OK` (bit 10) never sets; `vib_x/y/z` fields stuck at 0.0 (cache initial value).

| Symptom | Probable Cause | Confidence | Verification Method | Mitigation |
|---------|---------------|------------|--------------------|-----------:|
| WHO_AM_I returns 0x72 | Device is MPU6000 not MPU6050 — different die, same footprint | [HIGH] | `WHO_AM_I=0x72` = MPU6000 (older variant). Both work identically electrically; the check in `mpu6050.c:L37` rejects it | Change `MPU6050_WHO_AM_I_VAL` in `mpu6050.h` to `0x72` if using MPU6000 (analytical) |
| WHO_AM_I returns 0x71 | Device is ICM-20600 (drop-in replacement) | [MEDIUM] | 0x71 = InvenSense ICM-20600. Electrically and register-compatible with MPU6050 | Change `MPU6050_WHO_AM_I_VAL` to `0x71`; registers are compatible (analytical) |
| WHO_AM_I returns 0xFF | I²C NACK — device not at 0x68, or bus fault | [HIGH] | Logic analyser on address phase; 0xFF means no slave responded | Check AD0 pin voltage: should be 0 V (GND) for address 0x68. If AD0 floating, address may be 0x69 | Pull AD0 to GND with 10 kΩ; change `MPU6050_I2C_ADDR` to 0x69 if AD0=VCC (silicon-pending) |
| WHO_AM_I returns 0x68 but init fails | `PWR_MGMT_1` write NACK | [MEDIUM] | Log return value of each `i2c_bus_write_reg()` call in `mpu6050_init()` | Retry init after 10 ms delay — MPU6050 has ~30 ms internal boot-up time from power-on before register writes are accepted (analytical) |
| WHO_AM_I correct but vib_x always 0.0 | MPU6050 in sleep mode — `PWR_MGMT_1[6]` sleep bit | [MEDIUM] | Read `PWR_MGMT_1` back; bit 6 should be 0 after our `0x01` write (wake + PLL) | Confirm write value `0x01` clears sleep bit and selects PLL-X clock (host-sim) |

**Diagnostic steps:**

1. Add a log line to `mpu6050_init()` after the WHO_AM_I read: `ESP_LOGI(TAG, "MPU6050 WHO_AM_I=0x%02X (expected 0x%02X)", who, MPU6050_WHO_AM_I_VAL)`.
2. Confirm AD0 pin on MPU6050 is hard-wired to GND (not floating). Measure with multimeter: should be < 0.1 V.
3. Check 100 nF decoupling capacitor on MPU6050 Vcc pin, placed < 5 mm from the pin. Without it, the device may NACK sporadically on power-up.
4. After confirmed init failure, try init retry: add a loop with up to 3 attempts and 15 ms delay between each.
5. If WHO_AM_I = 0x72: the hardware is MPU6000 (same package, different silicon rev). Change the constant and rebuild.

**Expected vs Actual:**
- Expected: WHO_AM_I register (0x75) = `0x68` for MPU6050; init returns `I2C_OK`; vib_x/y/z show ±noise around 0 g within first frame
- Actual with MPU6000: WHO_AM_I = `0x72`; `mpu6050_init()` returns error code `-2`; IMU_OK bit stays clear; vib fields stay at 0.0 (LKG initial)

**Evidence:** (host-sim) for return code `-2`; (analytical) for MPU6000/ICM-20600 variants; (silicon-pending) for AD0 floating behaviour.

---

### 6. Thermal ADC Saturation

**Definition:** `temp_K` in telemetry frames reads impossibly high (> 473.15 K = 200°C) or impossibly low (< 233.15 K = -40°C); `thermal_read()` rejects the value with error -2 and preserves LKG; `TELEM_FLAG_THERMAL_OK` (bit 11) stays clear.

| Symptom | Probable Cause | Confidence | Verification Method | Mitigation |
|---------|---------------|------------|--------------------|-----------:|
| `temp_K` stuck at initial 295.15 K; THERMAL_OK = 0% | ADC raw code returning 4095 (saturation) — NTC disconnected or pullup to wrong rail | [HIGH] | ADC raw code 4095 means Vin ≥ Vref (~1.1 V internal, but with 11 dB attenuation the full-scale is 3.3 V). NTC disconnected leaves GPIO36 floating at V_pullup = 3.3 V → R_ntc = ∞ → `thermal_adc_mv_to_resistance()` returns 0 (very cold, clamps out-of-range) | Verify NTC is connected and pullup is 10 kΩ to 3.3 V; read raw ADC code with `adc1_get_raw(ADC1_CHANNEL_0)` (silicon-pending) |
| `temp_K` extremely cold (< 233.15 K = -40°C); THERMAL_OK = 0% | ADC raw code near 0 — NTC shorted to GND, or GPIO36 driven low by external circuit | [HIGH] | Measure GPIO36 voltage: should be ~1.65 V at 25°C (half V_supply for R_ntc = R_pullup). If reads 0 V, NTC or path to GND is shorted | Disconnect NTC; measure pin voltage. If still 0 V, pin is externally driven or damaged (silicon-pending) |
| `temp_K` physically reasonable but jumping ±50 K each frame | ADC noise from high source impedance; GPIO36 source impedance must be < 10 kΩ per ESP32 ADC spec | [HIGH] | Source impedance = R_pullup ∥ R_ntc. At 25°C: 10 kΩ ∥ 10 kΩ = 5 kΩ — within spec, but long wiring adds series resistance. Add RC filter: 10 kΩ + 100 nF before GPIO36 | Add RC LPF (10 kΩ + 100 nF) on NTC divider output to GPIO36; this limits bandwidth to 1/(2π × 10k × 100n) = 159 Hz, well below sensor sample rate of 100 Hz (analytical) |
| `temp_K` systematically > 2 K from reference thermometer | Beta coefficient error or R_pullup tolerance | [MEDIUM] | At ice bath (273.15 K): measure `temp_K` from telemetry frames. Compute actual β: β = ln(R_ice / R25) / (1/273.15 - 1/298.15) | Re-derive β from two-point calibration (ice bath + room temp); update `beta_K` in `thermal.h` (silicon-pending) |
| ADC readings drift with motor current | Power supply ripple coupling into ADC via 3.3 V rail | [MEDIUM] | Correlate `temp_K` noise amplitude with `current_A` magnitude; if correlated, power supply noise is the cause | Add 100 µF bulk capacitor on ESP32 3.3 V rail; add ferrite bead between motor controller power and ESP32 power (analytical) |

**Diagnostic steps:**

1. Add a log line in `thermal_read()` to print raw ADC millivolts: the intermediate value `mv` before Steinhart-Hart computation.
2. At room temperature (~25°C), expect ADC reading ≈ 1650 mV (half the 3.3 V supply, since R_ntc ≈ R_pullup = 10 kΩ at 25°C).
3. Raw ADC code 4095 = input ≥ 3.3 V = NTC disconnected (R_ntc → ∞ → V_adc → V_supply).
4. Raw ADC code 0 = input ≤ 0 V = NTC shorted to GND.
5. Check `n_reads_fail` in `thermal_t` struct: if equal to `n_reads_ok + n_reads_fail`, every read is failing the range check.

**Expected vs Actual:**
- Expected: `temp_K` ∈ [273, 348 K] at room/operating temperature; THERMAL_OK uptime ≥ 98%; ADC reading ~1650 mV at 25°C
- Actual with NTC disconnected: ADC → 3300 mV → R_ntc = 0 → `thermal_adc_mv_to_resistance()` returns 0.0 → `thermal_resistance_to_kelvin(0, ...)` returns 0.0 → range check fails → LKG 295.15 K preserved; THERMAL_OK = 0%

**Evidence:** (host-sim) for LKG/range-check path; (analytical) for ADC saturation codes; (silicon-pending) for real ADC accuracy under power supply noise.

---

### 7. Watchdog Reset Loop

**Definition:** ESP32 reboots repeatedly with UART0 showing `reset reason: 4 (TG0WDT_SYS_RESET)` or `reset reason: 7 (TG1WDT_SYS_RESET)` or `reset reason: 5 (INT_WDT_RESET)` on each boot. The Faz 19C safety monitor classifies these as `SAFETY_RESET_WATCHDOG` and sets `TELEM_FLAG_WATCHDOG_RESET` (bit 14) in the first telemetry frame of each boot.

| Symptom | Probable Cause | Confidence | Verification Method | Mitigation |
|---------|---------------|------------|--------------------|-----------:|
| WDT fires within first 5 s of boot | `UART_ISR_IN_IRAM=y` not applied; UART ISR blocked during flash cache miss, starves WDT feed | [HIGH] | Check `sdkconfig.defaults` contains `CONFIG_UART_ISR_IN_IRAM=y`; rebuild and reflash | Confirm sdkconfig option applied; if still firing, check for `ESP_LOGI` calls in IRAM-blocked paths (analytical) |
| WDT fires after ~5 seconds into normal operation (CONFIG timeout) | `sensor_i2c_task` blocked on stuck I²C peripheral → `vTaskDelayUntil` not called → task WDT fires | [HIGH] | UART0 will show WDT task name before reset: `Task watchdog got triggered. The following tasks/contexts did not reset the watchdog in time: ... IDLE0` or task name | Set `I2C_BUS_TIMEOUT_MS=20` ms (already done in `i2c_bus.h`) to bound I²C call. Verify I²C driver does not block indefinitely on stuck SDA (silicon-pending) |
| WDT fires only when USB WiFi dongle is active | WiFi ISR storm disables interrupts for >5 s | [MEDIUM] | Enable WiFi, observe UART0 for WDT. This firmware does not use WiFi; ensure WiFi/BT is not compiled in | Confirm `sdkconfig.defaults` disables WiFi/BT components; check `menuconfig` → Component → WiFi (analytical) |
| Intermittent WDT, no pattern | Stack overflow corrupting FreeRTOS internal structures | [MEDIUM] | Check health monitor UART0 output for `LOW STACK` warning; any HWM < 256 bytes is dangerous | Increase task stack size from 4096 to 6144 bytes; monitor HWM after 30-minute soak (silicon-pending) |
| WDT after safety_halt: `safety_monitor.c` calls `vTaskDelay(100)` before `esp_restart()` | 100 ms delay before restart is fine; WDT period is 5 s. WDT fires if `esp_restart()` itself hangs (cache flush taking >5 s) | [LOW] | Check if WDT fires immediately after `THERMAL SHUTDOWN` or `OVERCURRENT SHUTDOWN` log message | If confirmed, reduce `vTaskDelay(100)` to `vTaskDelay(10)` in `safety_halt_thermal()` and `safety_halt_overcurrent()` (analytical) |

**Diagnostic steps:**

1. On boot, read: `I (xxx) app_main: Reset reason: WATCHDOG` — confirms the WDT fired in the previous boot.
2. Search UART0 log for: `Task watchdog got triggered` — this line appears before the crash log and identifies the offending task.
3. Check `TELEM_FLAG_WATCHDOG_RESET` (bit 14) in the very first telemetry frame after boot: if set, prior boot was WDT-induced.
4. After a WDT reset: run `bringup/verify_bringup.py` and check the `watchdog_reset` counter.
5. Insert a `uxTaskGetStackHighWaterMark()` call in each task's main loop and log every 10 iterations during initial commissioning.

**Expected vs Actual:**
- Expected: No WDT triggers during 30-minute soak; `TELEM_FLAG_WATCHDOG_RESET` clear on all frames after initial power-on
- Actual during I²C bus hang: `sensor_i2c_task` blocks in `i2c_master_cmd_begin()` longer than 5 s; WDT fires; UART0 shows `Task watchdog got triggered. ... sens`

**Evidence:** (analytical) for WDT/ISR interaction; (silicon-pending) for actual WDT trigger conditions; `CONFIG_ESP_TASK_WDT_TIMEOUT_S=5` from `sdkconfig.defaults`.

---

### 8. Brownout Reset Loop

**Definition:** ESP32 reboots repeatedly with UART0 showing `reset reason: 3 (BROWNOUT_RESET)`. Faz 19C safety monitor sets `TELEM_FLAG_BROWNOUT` (bit 12) in each boot's first frame. The device may power-cycle faster than it can complete the boot sequence, producing a rapid boot loop with only partial log output each time.

| Symptom | Probable Cause | Confidence | Verification Method | Mitigation |
|---------|---------------|------------|--------------------|-----------:|
| `reset reason: 3 (BROWNOUT_RESET)` on every boot | Supply voltage below ESP32 brownout threshold (~2.43 V for BOD level 6, the default) | [HIGH] | Measure 3.3 V rail with multimeter under load; if it dips below 2.5 V transiently, brownout fires. ESP32 BOD is level 6 = ~2.43 V | Increase power supply headroom; add 220 µF bulk capacitor on 3.3 V rail near ESP32 (silicon-pending) |
| Brownout only at startup (cold boot) | Inrush current when UART interface IC powers on pulls rail low | [HIGH] | Add 100 µF electrolytic capacitor after LDO regulator; measure rail during power-on with oscilloscope — expect < 100 mV droop | Use LDO with higher current rating (>500 mA); add soft-start circuit (analytical) |
| Brownout only when motor runs at high load | Motor ESC switching noise coupling back into 3.3 V supply | [HIGH] | Correlate brownout events with `current_A` spikes in telemetry; if > 30 A correlates with brownout, this is the cause | Separate supply rails for ESP32 and ESC; add LC filter (10 µH + 100 µF) on ESP32 3.3 V supply (analytical) |
| Brownout once then stable | Electrolytic capacitor charging on cold boot | [LOW] | Monitor first 5 boots: if only first boot shows brownout, a larger bulk capacitor (470 µF) will typically fix it | Add 470 µF capacitor; power supply turn-on time must be < 1 s to avoid watchdog on boot (analytical) |
| `TELEM_FLAG_BROWNOUT` (bit 12) set on first frame but no reset loop | Single brownout event recovered; Faz 19C correctly classifies and reports it | [HIGH] | This is correct and expected behaviour for a single transient dropout. No action needed if stable thereafter. | No mitigation required; flag is informational. Log the event and check power supply (host-sim) |

**Diagnostic steps:**

1. Read UART0 immediately on boot: `reset reason: 3 (BROWNOUT_RESET)` confirms hardware brownout detection fired.
2. Measure the 3.3 V supply rail with an oscilloscope during the brownout window: look for voltage dipping below 2.5 V.
3. Check `TELEM_FLAG_BROWNOUT` (bit 12) in the first telemetry frame after each boot. If set: log the event and continue.
4. If repeating: unplug the motor/ESC load and retry — if brownout stops, it is power supply noise from the load.
5. Check LDO or buck converter datasheet: maximum output current must exceed ESP32 peak (350 mA typical, 800 mA peak) plus all sensor load (INA226: 1 mA, MPU6050: 3.9 mA, NTC divider: 0.33 mA) = total ≈ 360 mA margin required.

**Expected vs Actual:**
- Expected: `reset reason: 1 (POWER_ON)` on first boot; no brownout flags ever set during bench operation
- Actual with undersized supply: `reset reason: 3 (BROWNOUT_RESET)` every 200–500 ms; partial boot logs; `TELEM_FLAG_BROWNOUT` bit set on any frame that does arrive

**Evidence:** (silicon-pending) for specific voltage threshold measurements; (analytical) for brownout level calculation; (host-sim) for `TELEM_FLAG_BROWNOUT` flag propagation.

---

### 9. FreeRTOS Task Starvation

**Definition:** One or more lower-priority tasks stop executing or execute far less frequently than expected. Visible as: health monitor logs ceasing, safety monitor check rate dropping, or `quality` field anomalies (if sensor task starved).

| Symptom | Probable Cause | Confidence | Verification Method | Mitigation |
|---------|---------------|------------|--------------------|-----------:|
| Health monitor (`health_task`, prio 1) stops logging after first entry | `sensor_i2c_task` (prio 12) or `telemetry_task` (prio 10) in a busy loop — never yields | [HIGH] | Both tasks use `vTaskDelayUntil()` which yields to the scheduler; if `vTaskDelayUntil` code path is wrong (e.g. `period_ticks=0` from wrong tick rate), task never sleeps. Check `period=1 tick(s)` log from telem task | Confirm tick rate; verify `vTaskDelayUntil` vs `vTaskDelay` usage. All tasks must yield; check for `for(;;){}` busy-wait anywhere (analytical) |
| Safety monitor check interval > 200 ms (should be 100 ms) | `telemetry_task` at prio 10 preempting safety monitor at prio 3 for > 100 ms continuously | [LOW] | This should not happen: telemetry sleeps 1 ms between iterations; safety at prio 3 should run during those 1 ms gaps. If sensor I²C hangs, sensor at prio 12 blocks Core 1 for > 100 ms — safety miss | Instrument safety monitor: log `xTaskGetTickCount()` at each iteration; watch for gaps > 150 ms (silicon-pending) |
| Sensor I²C task never runs after startup | `telemetry_task` started before `sensor_i2c_task` in `telemetry_task_start()`, but both on same core | [LOW] | Check task creation order in `telemetry_task.c:L116-L122`: sensor is created FIRST; this is correct. If incorrect build: sensor_i2c_task created after telemetry starts, and if telemetry runs before sensor, first frames get default 0 cache | This is correct in current code. Verify sensor task appears in UART0 log: `sensor I²C task started @ 100 Hz` (host-sim) |
| All tasks stopped (`idf.py monitor` shows only WDT msgs) | FreeRTOS scheduler suspended via `vTaskSuspendAll()` — should never happen in this codebase | [LOW] | grep codebase for `vTaskSuspendAll` — not present. Only external library could cause this | Do not link any library that calls `vTaskSuspendAll` for more than a few microseconds (analytical) |

**Diagnostic steps:**

1. After 30 seconds of operation, verify health monitor output appears in UART0 at ~5-second intervals.
2. Count health monitor lines per minute: should be ~12. If zero after 30 s, task is starved or dead.
3. Add a GPIO toggle in each task's main loop body during initial commissioning; use oscilloscope to verify all three task rates (1 kHz, 100 Hz, 0.2 Hz).
4. Check `sensor I²C task started @ 100 Hz` appears in boot log before `telemetry task started`.
5. If starvation suspected: use `vTaskList()` (FreeRTOS debug function) to print all task states and priorities.

**Expected vs Actual:**
- Expected: health monitor logs every ~5 s; safety monitor runs at 100 ms; sensor task every 10 ms; all tasks alive after 1-hour soak
- Actual with `period_ticks=0`: telemetry and sensor tasks spin at full CPU speed; health and safety tasks receive no CPU time; health monitor never logs

**Evidence:** (analytical) for priority interactions; (host-sim) for task creation order in current code.

---

### 10. Heap Exhaustion

**Definition:** `heap_caps_get_free_size(MALLOC_CAP_DEFAULT)` continuously decreasing after the first 60 seconds of operation, eventually triggering `ESP_ERR_NO_MEM` and a panic. Visible in health monitor UART0 log as decreasing `heap free` values and a `LOW HEAP` warning.

| Symptom | Probable Cause | Confidence | Verification Method | Mitigation |
|---------|---------------|------------|--------------------|-----------:|
| `heap free` drops ~100 B per second monotonically | Allocation in a post-init code path (bug) | [HIGH] | Grep source for `malloc`/`calloc`/`pvPortMalloc` outside of initialization: `grep -r "malloc\|pvPortMalloc" faz19b_sensors/main/` — should return 0 matches in realtime path | All static analysis passes (grep clean); if new code added, maintain this invariant (host-sim) |
| `heap free` drops quickly only after INA226/MPU6050 fails | ESP-IDF I²C error recovery allocating retry command links | [MEDIUM] | ESP-IDF `i2c_cmd_link_create()` allocates from heap each transaction. If transactions fail and cmd links are not deleted, heap leaks. Code calls `i2c_cmd_link_delete(cmd)` in `i2c_bus.c` after every transaction — including error paths | Verify `i2c_cmd_link_delete(cmd)` is called on ALL code paths in `i2c_bus.c:L108-L128`, including the error branch (analytical) |
| `heap free` stable but absolute value < 32 KB | FreeRTOS internal allocations during task creation; this is normal | [HIGH] | ESP32 starts with ~300 KB free; task creation uses ~4 KB per task (stack) + overhead. 4 tasks × ~4 KB = ~20 KB consumed at startup | No action: > 100 KB free after 4 tasks is expected. Warning triggers only below 32 KB (analytical) |
| `heap free` drops then stabilizes after ~30 s | ESP-IDF internal caching (e.g., log buffer, timer storage) growing during startup | [LOW] | Common and expected behaviour; ESP-IDF lazy-allocates some internal structures on first use | Normal behaviour; check `min_free_heap` is stable after 60 s (silicon-pending) |

**Diagnostic steps:**

1. Read health monitor UART0 lines every 5 s: `heap free=XXXXX min_since_boot=XXXXX`.
2. Record `heap free` at t=30 s and t=120 s. Delta should be < 1 KB over 90 seconds.
3. If heap is dropping: run `heap_caps_dump_all()` (add one-time call in health monitor) to identify largest allocations.
4. Static analysis: `grep -rn "malloc\|calloc\|pvPortMalloc\|free\b" faz19b_sensors/main/ faz19c_safety/main/ | grep -v "//\|^.*#"` — should find zero in realtime code paths.
5. Acceptable heap baseline: after 60 s of normal operation, free heap should be > 200 KB (ESP32 has ~300 KB DRAM available).

**Expected vs Actual:**
- Expected: `heap free` stable within ±2 KB after 30 s; `min_free_heap` equals or approaches steady `heap free`
- Actual with leak: `heap free` decreases ~100–1000 B per second; eventually `heap_caps_get_free_size()` < 8 KB; `xSemaphoreCreateMutex()` returns NULL; next I²C transaction crashes

**Evidence:** (host-sim) for malloc-free realtime path verification; (silicon-pending) for actual heap measurements; (analytical) for ESP-IDF internal allocation patterns.

---

### 11. Telemetry Desync (no 0xAA 0x55 found)

**Definition:** PC-side `RealESP32Link` or `verify_bringup.py` cannot find the `0xAA 0x55` magic bytes in the UART1 byte stream. `sync_drops` counter climbs. No frames are decoded, even though the UART port opens successfully and bytes are arriving.

| Symptom | Probable Cause | Confidence | Verification Method | Mitigation |
|---------|---------------|------------|--------------------|-----------:|
| Bytes arriving but no sync; stream is pure ASCII | UART1 port is actually UART0 console — wrong `/dev/ttyUSBx` selected | [HIGH] | Capture 100 bytes raw from the port; if they start with `I (` this is the debug console | Swap port: UART0=console (GPIO 1/3), UART1=telemetry (GPIO 17). Verify which USB-serial chip is wired to which GPIO (silicon-pending) |
| `0xAA 0x55` appears for a few frames then disappears | ESP32 boot loader outputting startup messages on UART1 (TX strapped to UART0 on some boards) | [MEDIUM] | DevKit boards sometimes route UART0 and UART1 TX through same USB chip. Use `idf.py menuconfig` → Serial Flasher → Default baud to identify which physical pin is which | Wire UART1 TX (GPIO17) directly to a dedicated USB-serial chip, NOT through the on-board USB-serial chip which is typically UART0 (silicon-pending) |
| Sync works after 500–2000 ms then stable | Boot-time UART0 messages leaking into UART1 capture window | [LOW] | The `RealESP32Link` Faz 18 sync algorithm already skips garbage before finding `0xAA 0x55`; > 500 ms sync time is within spec | Faz 18 bring-up validation confirmed this: sync within 500 ms of port open (host-sim) |
| Constant stream of bytes but none match `0xAA 0x55` | Telemetry task never started (see `app_main.c` bail-out on `uart_stream_init` failure) | [HIGH] | Check UART0 log for `UART init failed; halting` — if present, the telemetry task was never created and the UART ring was never populated | Fix UART1 init error (see failure #1 and #2); `uart_stream_init()` returns non-zero on failure (host-sim) |
| Stream empty (0 bytes per second) | GPIO17 not connected to USB-serial RX, or wrong GPIO used for TX | [HIGH] | Logic analyser or oscilloscope on GPIO17: should show 8N1 activity at 921600 baud when firmware is running | Probe GPIO17 with oscilloscope; if flat, either task not running or wrong GPIO pinout (silicon-pending) |

**Diagnostic steps:**

1. Open UART1 (telemetry port) in raw hex mode and capture 200 bytes: `python3 -c "import serial; s=serial.Serial('/dev/ttyUSB1',921600,timeout=2); d=s.read(200); print(d.hex())"`.
2. Scan for the pattern `aa55` in the output. If present: sync is possible; issue is in the PC parser configuration. If absent: either wrong port or firmware not running.
3. Open UART0 (console port) at 115200 and verify the telemetry task started: `telemetry task started, period=1 tick(s)` must be present.
4. Oscilloscope on GPIO17: should show rapid UART transitions at 921600 baud when telemetry is running. No transitions = task not running or wrong GPIO.
5. Check that `uart_stream.c` is not using `UART_NUM 0` by mistake: `grep -n "UART_NUM" faz19b_sensors/main/uart_stream.c` — should show `#define UART_NUM 1`.

**Expected vs Actual:**
- Expected: `0xAA 0x55` appears in first 200 bytes captured from UART1; `verify_bringup.py` finds first frame within 2 s
- Actual with wrong port: all bytes are ASCII (`49 20 28` = `I (` = ESP log prefix); no `aa55` pattern found

**Evidence:** (host-sim) for sync algorithm resilience; (silicon-pending) for GPIO routing on specific dev boards; (analytical) for UART0/UART1 GPIO mapping.

---

### 12. CRC Failures (persistent)

**Definition:** Telemetry frames with `0xAA 0x55` magic are found but CRC verification fails. `crc_errors` counter in `verify_bringup.py` is non-zero and growing. PC-side `RealESP32Link` discards corrupted frames; delivery rate drops.

| Symptom | Probable Cause | Confidence | Verification Method | Mitigation |
|---------|---------------|------------|--------------------|-----------:|
| CRC failures clustered in bursts, with clean frames in between | UART TX ring overflow — 4 KB ring (62 frames) exceeded; some frame bytes overwritten mid-transmission | [HIGH] | At 1 kHz × 66 B = 66 KB/s TX rate and 921600 baud = 92.16 KB/s capacity, steady-state utilisation is 71.6%. Ring overflow should not occur normally. But: WiFi ISR storms can block UART ISR for > 44 ms (ring's burst tolerance). Confirm firmware does not use WiFi | If WiFi is compiled in: confirm it is disabled. Increase ring buffer: change `UART_TX_BUF_SIZE` from 4096 to 8192 in `uart_stream.c` (analytical) |
| CRC failures on every frame consistently | Byte-swap error introduced in `telem_pack()` — field endianness wrong | [HIGH] | Run `faz19_firmware/host_test/test_telem_pack.c` on host: should show 100/100 pass. If any fail, the pack function has a bug. Also run `verify_frames.py` against a captured binary | Re-run `host_test/run_all_tests.sh`; any test failure here is a code regression (host-sim) |
| CRC errors only on specific bytes of the frame | USB-serial chip error rate at 921600 baud — CH340 chip drops bits at high baud | [HIGH] | Replace CH340 with CP2102N or FTDI FT232H. CH340G max reliable baud is typically 460800 | Use CP2102N (Silicon Labs) or FT232H (FTDI) for UART1 telemetry; CH340 is not rated for 921600 on all OS drivers (analytical) |
| CRC failures correlate with long cable | Signal integrity: cable acting as an antenna at 921600 baud (bit period = 1.085 µs, 1/4 wavelength ≈ 75 m — resonance not an issue but reflections can corrupt at >1 m) | [MEDIUM] | Try a cable < 0.5 m; if CRC errors drop to zero, cable length was the cause | Use shielded cable; add 33 Ω series termination resistor on GPIO17 TX; keep cable < 1 m (analytical) |
| CRC failures after firmware reflash, was clean before | `telem_pack()` CRC polynomial changed by mistake | [HIGH] | CRC must be: poly `0x1021`, init `0xFFFF`, no reflection, no xorout. Verify with `crc16_ccitt()` in `telemetry_protocol.c`. The 100/100 test in `test_telem_pack.c` covers this | Re-run `test_telem_pack.c`; the Python parser is the authority — firmware must match it (host-sim) |

**Diagnostic steps:**

1. Run `host_test/run_all_tests.sh` — if all 108/137 (or 137/137) tests pass, `telem_pack()` is correct; the issue is hardware.
2. Capture 1000 raw frames: `python3 -c "import serial; s=serial.Serial('/dev/ttyUSB1',921600,timeout=5); print(s.read(66000).hex())"` and pipe through `verify_frames.py`. Count CRC failures.
3. Shorten cable to < 0.3 m and re-test. If errors drop: cable is the cause.
4. Replace USB-serial adapter with known-good CP2102N unit. If errors drop: adapter was the cause.
5. Check `uart_stream_write()` return value: if it returns < 66, the TX ring is full and bytes are being dropped silently.

**Expected vs Actual:**
- Expected: `crc_errors = 0` over a 10-minute soak (10,063 frames in host field test achieved 0 CRC errors)
- Actual with CH340 at 921600: ~0.5–2% of frames fail CRC; errors are random single-bit corruptions

**Evidence:** (host-sim) for `telem_pack()` CRC correctness; (analytical) for CH340 baud rate limitation; (silicon-pending) for actual cable-induced BER measurement.

---

### 13. Sensor Data Freeze (field stuck)

**Definition:** A specific telemetry field (`current_A`, `vib_x/y/z`, or `temp_K`) reads a constant value for > 1 second. The corresponding sensor-OK flag bit (9, 10, or 11) may be clear (LKG scenario) or set (unexpected freeze).

| Symptom | Probable Cause | Confidence | Verification Method | Mitigation |
|---------|---------------|------------|--------------------|-----------:|
| `current_A` stuck at a specific value; `INA226_OK` (bit 9) = 0% | INA226 failed; LKG value preserved — this is CORRECT system behaviour, not a bug | [HIGH] | LKG is intentional: `ina226_read()` failure does not zero the field; last good value is preserved. The flag bit distinguishes "stale" from "current". Check `n_reads_fail` in INA226 struct | Treat as failure #3 (I²C timeout). The LKG mechanism is working correctly; fix the underlying I²C issue (host-sim) |
| `vib_x/y/z` all read exactly 0.0; `IMU_OK` (bit 10) = 0% | `s_cache.accel_g[]` was memset to 0 at init; MPU6050 never successfully read | [HIGH] | Cache initial value is 0.0 for accel (from `memset(&s_cache, 0, ...)`). If MPU6050 never init'd, all reads fail, cache stays 0.0 | Confirm MPU6050 init success (see failure #5). At idle, vib_rms should be ~0.005–0.02 g, never exactly 0.000 (host-sim) |
| `temp_K` stuck at 295.15 K; `THERMAL_OK` (bit 11) = 0% | NTC initial LKG is 295.15 K (room temp default set in `sensor_pipeline_init()`); ADC reads failing | [HIGH] | `s_cache.ntc_temp_K = 295.15f` at init in `sensor_pipeline.c:L97`. If thermal_read fails every time, cache never updates. Flag stays clear | Treat as failure #6 (thermal ADC saturation). The 295.15 K is a deliberate safe default, not a measurement (host-sim) |
| Field stuck but corresponding flag IS SET (sensor_ok bit = 1) | I²C reading a stale register — sensor in power-down mode not updating its internal ADC | [MEDIUM] | Read `INA226_REG_CONFIG` (0x00): bits 2:0 should be `111` = continuous shunt+bus mode. If `000`, device is in power-down. Our init writes `0x4127` which sets continuous mode | Add a readback of CONFIG register after init and log it. If CONFIG reads as `0` after power-on, the write failed silently | (silicon-pending) |
| `alpha` field stuck at 0.0 | `esp_timer_get_time()` returning 0 on every call | [LOW] | On real hardware this should not happen; `esp_timer_get_time()` is hardware-backed by ESP32's 64-bit microsecond counter. Stuck at 0 only occurs in host-build stubs | Verify firmware is compiled with `ESP_PLATFORM` defined; check `esp_timer_init()` called (part of ESP-IDF default startup) (analytical) |

**Diagnostic steps:**

1. For any frozen field: first check the corresponding flag bit in the `flags` field of the frame. If the bit is clear, LKG is active — this is expected failure-isolation behaviour.
2. Check the commissioning panel or `verify_bringup.py` output for per-sensor ok percentages.
3. For `current_A` freeze: verify INA226 CONTINUOUS mode by reading CONFIG back; value should be `0x4127`.
4. For `vib_x/y/z` all exactly 0.000: MPU6050 never initialized. Check WHO_AM_I (see failure #5).
5. Compute vib_rms from a 1-second window: if exactly 0.0000000, this is the initial cache value. Any real MPU6050 reading will have ≥ 0.001 g noise floor.

**Expected vs Actual:**
- Expected: When a sensor is healthy (flag set), field values change each measurement cycle (100 Hz sensor rate)
- Actual with LKG active: field is constant; flag bit clear; `n_reads_fail` counter in sensor struct is increasing. Both are CORRECT by design

**Evidence:** (host-sim) for LKG/flag propagation; confirmed in Faz 19B field test: `INA current during fault: 4.969..4.969 A (constant)`.

---

### 14. Boot Loop (rapid repeated resets)

**Definition:** ESP32 reboots every 1–10 seconds with partial boot logs, cycling indefinitely. May combine multiple root causes. The Guru Meditation Error message may or may not be visible depending on crash type and reboot speed.

| Symptom | Probable Cause | Confidence | Verification Method | Mitigation |
|---------|---------------|------------|--------------------|-----------:|
| `Guru Meditation Error: Core 0 panic'ed (InstrFetchProhibited)` | Null or corrupt function pointer call; stack overflow into text segment | [HIGH] | Stack HWM from previous boot (not available after crash). Check health monitor log from last successful run | Increase stack from 4096 to 6144 words; use `CONFIG_ESP_SYSTEM_PANIC_GDBSTUB=y` to enable GDB stub for crash inspection (analytical) |
| `Guru Meditation Error: Core 1 panic'ed (LoadProhibited)` | NULL pointer dereference in sensor read path (e.g., `out == NULL` — but we already check this) | [HIGH] | Check `telem_frame_t frame` local variable in `telemetry_task` is stack-allocated (not heap); frame pointer passed correctly to `sensor_pipeline_read()` | This is checked: `if (out == NULL) return;` in `sensor_pipeline_read()`. A crash here means `frame` on the stack was corrupted, implying stack overflow (host-sim) |
| `Guru Meditation Error: Core X panic'ed (IllegalInstruction)` | Stack overflow clobbering the return address of a function | [HIGH] | `IllegalInstruction` typically means EPC points to a stack-corrupted address, which is code-aligned random data decoded as illegal opcode | Enable `CONFIG_ESP_SYSTEM_PANIC_PRINT_HALT=y` to see full register dump before reboot; enable `CONFIG_ESP_TASK_WDT=n` temporarily to prevent WDT masking the crash (analytical) |
| Boot log incomplete (cuts off mid-line) | Brownout occurring during boot before UART driver is stable | [HIGH] | See failure #8. Incomplete logs mean power failure before `app_main` completes | Fix power supply first; do not debug code until power is stable (silicon-pending) |
| Boot completes, runs 5 s, then crashes with `free() called with invalid pointer` | malloc was used somewhere in code (violating the no-malloc invariant) | [MEDIUM] | `grep -r "malloc\|calloc\|pvPortMalloc" faz19b_sensors/main/` — if any match: the invariant was broken. The host-build grep is clean; this would be a new addition | Remove the malloc; use static buffer (host-sim) |
| `assert failed: xSemaphore ...` in log | Semaphore used before creation (init order bug) | [MEDIUM] | `s_cache_mutex = xSemaphoreCreateMutex()` in `sensor_pipeline_init()` — if `sensor_pipeline_step()` called before `init()`, mutex is NULL; `cache_lock()` does `if (s_cache_mutex)` guard which handles this safely | Confirm `sensor_pipeline_init()` is always called before any `step()` or `read()`. In current code, `init()` is called in `telemetry_task_start()` before either task starts (host-sim) |

**Diagnostic steps:**

1. Add `CONFIG_ESP_SYSTEM_PANIC_PRINT_HALT=y` to `sdkconfig.defaults` temporarily; this keeps the crash log visible instead of immediately rebooting.
2. Capture the complete Guru Meditation Error message including: `Core X panic'ed (ExceptionType)`, the EPC register value, and the backtrace.
3. Decode the backtrace using `xtensa-esp32-elf-addr2line -pfiaC -e build/filament_winding_telem.elf <EPC_ADDRESS>`.
4. Check for stack overflow first: `Core 1` tasks are `telem` (4096 word stack) and `sens` (4096 word stack). If the crash trace points to addresses in FreeRTOS internals, stack overflow is likely.
5. Check health monitor log from before the crash: any `LOW STACK` warnings (HWM < 256 bytes) preceding the crash confirm stack overflow.

**Expected vs Actual:**
- Expected: No panics; 30-minute soak completes without reset; `reset reason: 1 (POWER_ON)` on every cold boot only
- Actual during stack overflow: `Guru Meditation Error: Core 1 panic'ed (LoadProhibited)` at a near-zero address; stack was overwritten with 0x00 fill pattern from FreeRTOS stack guard

**Evidence:** (analytical) for Guru Meditation error types; (silicon-pending) for actual panic address correlation; (host-sim) for init order guard.

---

### 15. SAFE_HALT event in telemetry frames

**Definition:** `TELEM_FLAG_SAFE_HALT` (bit 8) appears set in one or more telemetry frames, followed by UART silence (ESP32 has called `esp_restart()` via the Faz 19C safety monitor). PC-side `RealESP32Link` auto-reconnect triggers within 3 s.

| Symptom | Probable Cause | Confidence | Verification Method | Mitigation |
|---------|---------------|------------|--------------------|-----------:|
| `SAFE_HALT` set; UART0 shows `THERMAL SHUTDOWN: temp exceeded 373.15 K for 3 samples` | Real temperature reached 100°C shutdown threshold (3 consecutive safety monitor samples, at 100 ms each = 300 ms gating) | [HIGH] | Check `temp_K` in the frames immediately before SAFE_HALT: if 3+ consecutive frames show `temp_K >= 373.15`, this is correct firmware behaviour | Investigate why temperature reached 100°C. Improve thermal dissipation. If NTC is misread (see failure #6), fix the ADC circuit first (silicon-pending) |
| `SAFE_HALT` set; UART0 shows `OVERCURRENT SHUTDOWN: ... >= 45.0 A for 3 samples` | Real current reached 45.0 A for 3 consecutive samples (300 ms gating) | [HIGH] | Check `current_A` in frames before SAFE_HALT: 3+ consecutive frames ≥ 45.0 A | Inspect ESC/motor load. Check wiring at shunt resistor — if shunt is wrong value, calibrate. Threshold is 45.0 A (defined in `safety_monitor.h`) (silicon-pending) |
| `SAFE_HALT` set with `temp_K ≈ 295 K` (room temperature) | False positive: NTC disconnected (LKG 295.15 K) but safety check using a stale value that should NOT trigger thermal shutdown | [HIGH] | Thermal shutdown threshold is 373.15 K; LKG default 295.15 K is well below this. If SAFE_HALT fires at 295 K, the cause is overcurrent not thermal. Check `current_A` | Distinguish thermal vs overcurrent shutdown: check UART0 for the specific `THERMAL SHUTDOWN` vs `OVERCURRENT SHUTDOWN` log message (host-sim) |
| `SAFE_HALT` immediately on first boot, before any sensor data | Safety monitor initialized with stale NVS data or corrupted state | [LOW] | First boot should set `s_halted=0`, `s_temp_consec=0`, `s_curr_consec=0` in `safety_monitor_init()`. Stale NVS cannot cause this since NVS is not used for safety state in current code | This should not happen. If it does: check that `safety_monitor_init()` is called before `safety_monitor_start()` in `app_main()` (host-sim) |
| `SAFE_HALT` set but ESP32 does NOT reboot | Host-build behaviour: on host, `esp_restart()` is stubbed as `printf("[safety_monitor] THERMAL SHUTDOWN triggered (host — no restart)")` | [HIGH] | This is correct host-build behaviour used in `test_safety_monitor.c`. On real hardware, `esp_restart()` WILL reboot the chip | Only observed during host-simulation testing. On real silicon, SAFE_HALT always precedes a reboot (host-sim) |

**Diagnostic steps:**

1. When SAFE_HALT is observed: capture the last 10 frames before the event from `TelemetryDB`. Check: was `temp_K >= 373.15` for 3+ frames? Was `current_A >= 45.0` for 3+ frames?
2. Check UART0 for: `THERMAL SHUTDOWN: temp exceeded 373.15 K for 3 samples` vs `OVERCURRENT SHUTDOWN`.
3. Faz 19C safety monitor checks every 100 ms. Three consecutive violations = 300 ms minimum hold time before halt. The first frame with SAFE_HALT set will arrive within 100 ms of the third violation.
4. After reconnect (auto within 3 s via Faz 18 `RealESP32Link`): check `TELEM_FLAG_BROWNOUT` (bit 12) — if set, the restart was WDT/brownout induced, not a clean safety halt.
5. Check `reset reason` on next boot: `ESP_RST_SW` = 4 (software restart via `esp_restart()`) confirms safety halt was the cause. Any other reason = different root cause.

**Expected vs Actual:**
- Expected: SAFE_HALT is only triggered by sustained (300 ms) thermal or overcurrent violation; `n_reads_ok` > 0 for all sensors before halt; `reset reason: 4 (SW_RESET)` on next boot
- Actual with false thermal trigger (NTC fault): NTC read fails, LKG 295.15 K is used; 295.15 K < 373.15 K threshold; thermal SAFE_HALT will NOT be triggered by NTC fault alone. The system is designed to fail safe in this direction.

**Evidence:** (host-sim) for 3-sample gating and halt behaviour — `test_safety_monitor.c` exercises thermal and overcurrent paths; threshold constants from `safety_monitor.h:L34-L36`.

---

## Cross-Reference Index

| Failure # | Title | Most Likely Hardware Trigger |
|-----------|-------|------------------------------|
| 1 | UART Garbage | Wrong USB adapter for baud rate |
| 2 | Baud Rate Mismatch | `CONFIG_FREERTOS_HZ` not 1000; wrong host baud |
| 3 | I²C Timeout / NACK | Missing pullup resistors; wrong I²C address |
| 4 | INA226 0xFFFF | Open shunt; CAL=0 write failure; overflow |
| 5 | MPU6050 WHO_AM_I | AD0 floating; device variant; slow boot |
| 6 | Thermal ADC Saturation | NTC disconnected; ADC source impedance |
| 7 | Watchdog Reset Loop | I²C hang; `UART_ISR_IN_IRAM=y` missing |
| 8 | Brownout Reset Loop | Undersized power supply; load transients |
| 9 | Task Starvation | Period tick rate wrong (see #2); busy-loop |
| 10 | Heap Exhaustion | malloc in post-init path; i2c_cmd_link leak |
| 11 | Telemetry Desync | Wrong UART port; telemetry task not started |
| 12 | CRC Failures | CH340 adapter; cable; TX ring overflow |
| 13 | Sensor Data Freeze | I²C failure + LKG active (expected); sensor init fail |
| 14 | Boot Loop | Stack overflow; brownout; null pointer |
| 15 | SAFE_HALT Event | Real thermal/overcurrent; NTC/INA226 fault |

---

## Calibration Constants Reference (Faz 19B — compile-time, no NVS yet)

| Constant | Value | Location | Impact if wrong |
|----------|-------|----------|-----------------|
| `INA226_I2C_ADDR` | `0x40` | `ina226.h` | Wrong address → NACK on every transaction |
| `INA226_CALIBRATION` | `2048` | `ina226.h` | Wrong → `current_A` linearly scaled wrong |
| `INA226_CURRENT_LSB_A` | `0.00125f` | `ina226.h` | Wrong → `current_A` scaled wrong (must match CAL) |
| `MPU6050_I2C_ADDR` | `0x68` | `mpu6050.h` | Wrong → NACK; change to `0x69` if AD0=VCC |
| `MPU6050_WHO_AM_I_VAL` | `0x68` | `mpu6050.h` | Wrong → init rejection; MPU6000=0x72, ICM-20600=0x71 |
| `MPU6050_ACCEL_LSB_PER_G` | `16384.0f` | `mpu6050.h` | Wrong → vib g-values scaled wrong (depends on range ±2 g) |
| `THERMAL_CALIB_DEFAULT.beta_K` | `3950.0f` | `thermal.h` | Wrong → temp_K offset; calibrate at ice bath |
| `THERMAL_CALIB_DEFAULT.R_pullup_ohm` | `10000.0f` | `thermal.h` | Wrong → temp_K nonlinear error; measure actual pullup |
| `SAFETY_TEMP_SHUTDOWN_K` | `373.15f` (100°C) | `safety_monitor.h` | Too low → false SAFE_HALT; too high → thermal damage |
| `SAFETY_CURRENT_MAX_A` | `45.0f` | `safety_monitor.h` | Too low → false SAFE_HALT; too high → ESC damage |
| `SAFETY_CONSEC_SHUTDOWN` | `3` | `safety_monitor.h` | Controls gating time: 3 × 100 ms = 300 ms hold |

---

*Document generated 2026-05-27 — Faz 19B/19C commissioning reference. Evidence labels: (host-sim) = verified in host regression suite 108/137 assertions; (analytical) = derived from datasheet / architectural reasoning; (silicon-pending) = prediction, requires physical ESP32 to confirm.*
