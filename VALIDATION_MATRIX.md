# Validation Matrix — Filament Winding CAM Platform

**Document version:** 2026-05-27  
**Scope:** Full-stack: ESP32 firmware (Faz 19C) + Python backend + PySide6 UI  
**Status authority:** This is the single source of truth for engineering confidence.  
**Last test run:** 137/137 host assertions, 0 real-hardware assertions.

---

## Verification Level Key

Every cell in this matrix uses one of four explicit evidence labels.
**Nothing is marked verified without the specific evidence listed.**

| Label | Meaning |
|---|---|
| `PASS [host-sim]` | Compiled test on Linux x86 with mock peripherals (i2c_host_mock, pty) |
| `PASS [analytical]` | Mathematical derivation or static analysis — code logic proven correct, not measured |
| `PASS [pty]` | End-to-end through pseudo-terminal pair; real Python stack, simulated UART |
| `PASS [static]` | Source-code grep / inspection — no measurement involved |
| `PENDING` | Not yet tested; entry shows what test is required |
| `N/A` | Not applicable to this subsystem |

> **Critical distinction:** `PASS [host-sim]` confirms the *logic* of C code compiled
> for x86. It does **not** confirm: ESP32 ABI layout, FreeRTOS scheduler behavior,
> I²C electrical timing, ADC Vref calibration, or crystal oscillator accuracy.
> Those require real hardware.

---

## Part 1 — Firmware Subsystems (C / ESP-IDF)

| # | Subsystem | host\_verified | esp32\_verified | electrically\_verified | soak\_tested | failure\_injection\_tested | remaining\_risks | verification\_method |
|---|---|---|---|---|---|---|---|---|
| F-01 | **Wire protocol packing** `telemetry_protocol.c` — telem\_pack() byte order, magic 0xAA55, CRC placement | `PASS [host-sim]` 100/100 frames byte-identical to Python parser (`test_telem_pack.c`) | `PENDING` flash + run `field_test_faz19.py` on real port | `N/A` (pure logic) | `N/A` | `N/A` | Endianness assumption valid only if ESP32 is little-endian (it is, by datasheet — but untested post-build) | `verify_frames.py` round-trip; C→Python→repack comparison |
| F-02 | **CRC-16/CCITT** `telemetry_protocol.c` — polynomial 0x1021, init 0xFFFF, no reflection | `PASS [host-sim]` 49.9M frames computed, 0 mismatches in race test | `PENDING` | `N/A` | `PENDING` 30 min real soak with 0 CRC errors target | `N/A` | None identified for the algorithm itself; risk is silent register corruption on real chip | Golden-vector table test + race stress |
| F-03 | **UART streaming** `uart_stream.c` — UART1 GPIO 17/16, 921600 baud, 4 KB TX ring | `PASS [pty]` field_test_faz19.py: 10,063 frames, 100.6% delivery, 0 sync errors | `PENDING` real chip; baud rate depends on crystal accuracy | `PENDING` oscilloscope eye diagram at 921600; check framing errors | `PENDING` 30 min @ 1 kHz | `PENDING` RTS/CTS overrun injection | Baud clock derived from 40 MHz crystal + PLL; ±0.5% tolerance. At 921600 accumulates ~0.5 bit error / 10 bytes | pty-based field test; `uart_throughput_report.py` analytic margin |
| F-04 | **UART TX ring buffer / backpressure** 4 KB ring, 44 ms burst cushion | `PASS [analytical]` 71.6% utilization; 44 ms tolerance vs <10 ms ISR storm | `PENDING` measure actual ring depth under WiFi BLE co-activation | `N/A` | `PENDING` enable WiFi scan during 30s run, watch frame drops | `N/A` | WiFi ISR storm duration on real chip may differ from design assumption | `uart_throughput_report.py` bandwidth calculation |
| F-05 | **Telemetry task timing** `telemetry_task.c` — vTaskDelayUntil @ 1 kHz | `PASS [analytical]` vTaskDelayUntil drift-free by design vs vTaskDelay | `PENDING` GPIO toggle + oscilloscope, target <50 µs jitter | `N/A` | `PENDING` 30 min jitter log | `N/A` | CONFIG\_FREERTOS\_HZ=1000 must appear in compiled sdkconfig, not just sdkconfig.defaults; never confirmed post-build | `idf.py menuconfig` verification + GPIO scope measurement |
| F-06 | **I²C bus HAL** `i2c_bus.c` — GPIO 21/22, 400 kHz, shared mutex | `PASS [host-sim]` via i2c\_host\_mock; mutex contention path exercised | `PENDING` probe SDA/SCL on real bus | `PENDING` pullup resistor value (4.7 kΩ to 3.3 V typical); edge rise time spec | `PENDING` 30 min 3-sensor @ 100 Hz | `PENDING` bus hang injection (SCL stuck low), confirm recovery | I²C timeout recovery path is untested; stuck-clock condition can freeze the bus permanently on some ESP32 errata | i2c_host_mock register exerciser + scope verification |
| F-07 | **INA226 driver** `ina226.c` — reg access, CAL=2048, 1.25 mA/bit, LKG | `PASS [host-sim]` 24/24 assertions (init + read + fault + LKG) | `PENDING` read mfg\_id (expect 0x5449), then calibrate vs reference ammeter | `PENDING` 2 mΩ shunt thermal stability; Kelvin connection required | `PENDING` continuous 30 A for 10 min; check drift | `PENDING` I²C NACK injection → LKG hold | Shunt self-heating at 40 A: ΔT ≈ 3.2 W / thermal resistance. Tolerance creep unquantified | `test_ina226.c` mock + hardware calibration sweep |
| F-08 | **INA226 measurement accuracy** calibration vs ammeter | `N/A` (hardware property) | `PENDING` measure at 0, 5, 10, 20, 40 A vs reference; target <3% error | `PENDING` confirm Vishay 2 mΩ ±1% shunt; Kelvin 4-wire connection | `N/A` | `N/A` | PCB layout: high-current trace inductance and thermal gradient can shift zero offset by ±5 mA; Kelvin connections mandatory | Multi-point calibration sweep with reference ammeter |
| F-09 | **MPU6050 driver** `mpu6050.c` — 14-byte burst, ±2g, DLPF 44 Hz, LKG | `PASS [host-sim]` 24/24 assertions (init + read + fault + LKG) | `PENDING` WHO\_AM\_I register (expect 0x68), check AD0 pin grounded | `PENDING` PCB decoupling capacitor (100 nF on Vcc pin) | `PENDING` 30 min; check idle noise floor <0.01 g RMS | `PENDING` I²C NACK injection → LKG hold | MPU6050 address conflict if AD0 is floating (pulls to 0x69); mechanical mounting affects idle noise floor | `test_mpu6050.c` mock + hardware noise floor measurement |
| F-10 | **MPU6050 gyroscope path** raw gyro\_dps cached but not on wire | `PASS [host-sim]` data stored in cache correctly | `PENDING` confirm gyro values plausible on real chip | `N/A` | `N/A` | Cache-only; never reaches wire. No wire regression risk, but data is wasted if future phase expects it | `test_mpu6050.c` cache peek |
| F-11 | **Thermal / NTC driver** `thermal.c` — ADC, Steinhart-Hart β=3950, range guard | `PASS [host-sim]` 20/20 assertions (nominal, ice bath, supply rail, β sensitivity) | `PENDING` measure at ≥2 known reference temps (ice bath 273.15 K, room 298.15 K) | `PENDING` 10 kΩ pullup to 3.3 V; ADC input impedance vs source impedance | `PENDING` 30 min; check drift vs room thermometer | `PENDING` open-circuit NTC (ADC rail) → LKG hold | β tolerance ±50–100 K typical; at 100°C, 50 K β error → ~2°C temp error. Acceptable for shutdown threshold, marginal for precision | `test_thermal.c` known-value inputs + hardware reference calibration |
| F-12 | **ADC accuracy** `esp_adc_cal` Vref calibration (per-chip) | `PASS [host-sim]` math verified against synthetic ADC codes | `PENDING` run `esp_adc_cal` characterization; factory Vref varies ±6% chip-to-chip | `PENDING` check ADC input noise with RC filter on NTC divider | `N/A` | `PENDING` out-of-range ADC code injection | Per-chip Vref can be 1.0–1.2 V uncalibrated; affects temp reading by ±5°C without calibration. ESP32 has eFuse-stored calibration — code must use it | `esp_adc_cal_characterize()` call verification + reference measurement |
| F-13 | **Sensor pipeline aggregation** `sensor_pipeline.c` — 2-task model, flags, quality | `PASS [host-sim]` 36/36 assertions (init, step, read, all-down, partial-down, flag transition) | `PENDING` validate frame fields on real chip | `N/A` | `PENDING` 30 min; check quality field reflects real sensor health | `PASS [pty]` per-sensor fault windows validated: INA/IMU/THERM each isolated, quality 92→67→92 | quality = 17 + 25×n\_healthy; formula assumes exactly 3 sensors. Adding future sensors (HX711, encoder) will break this formula | `test_sensor_pipeline.c` + `field_test_faz19b_runtime.py` |
| F-14 | **LKG (last-known-good) preservation** — stale values held on sensor fault | `PASS [pty]` field test runtime: INA226 fault held 4.969 A, IMU held 0.0118 g, NTC held 298.15 K constant for entire fault window | `PENDING` confirm same behavior with real sensors (sensor may output garbage vs clean NACK) | `N/A` | `N/A` | `PASS [pty]` 3 fault windows, all LKG constants confirmed | Real I²C NACK vs. garbage read: if sensor returns corrupted data without NACK (e.g. bus glitch), LKG is NOT triggered. Hardware-level protection only | `field_test_faz19b_runtime.py` fault window validation |
| F-15 | **Cache concurrency** — mutex between sensor\_i2c\_task and telemetry\_task | `PASS [host-sim]` 49.9M reads × 7.5M writes, 0 torn reads (pthread mutex on x86) | `PENDING` verify FreeRTOS xSemaphoreTake latency <125 ns on real chip | `N/A` | `PENDING` 30 min at full rate on ESP32 | `N/A` | pthread on x86 ≠ FreeRTOS on ESP32. Critical section timing differs; priority inversion possible if prios misconfigured. Currently: sensor(9) > telemetry(10) — wrong order — **telemetry is higher priority than sensor, should be reviewed** | `test_race_cache.c` 49.9M stress; FreeRTOS priority audit |
| F-16 | **Safety monitor — reset reason classification** `safety_monitor.c` | `PASS [host-sim]` 29/29 assertions: power-on, brownout, watchdog, panic, software all classified correctly | `PENDING` actual brownout event: drop Vcc to 2.8 V, check TELEM\_FLAG\_BROWNOUT in next boot frame | `PENDING` verify actual `esp_reset_reason()` enum values match compile-time constants | `N/A` | `PENDING` real brownout injection; real WDT trigger | `esp_reset_reason()` values are defined in ESP-IDF headers; match assumed to be correct but untested at link time | `test_safety_monitor.c` host mock + real brownout power supply test |
| F-17 | **Safety monitor — thermal shutdown** 373.15 K, 3-sample gating | `PASS [host-sim]` 29/29: boundary (373.14K no halt, 373.15K halts after 3 consec) confirmed | `PENDING` confirm temp sensor delivers 373.15 K reading before firmware halts | `N/A` | `N/A` | `PENDING` heat NTC to 102°C, confirm halt | Safety halt calls `esp_restart()` — on host this is a stub. The 100ms drain delay before restart is untested; UART FIFO may not drain in time | `test_safety_monitor.c` boundary tests + hardware heat test |
| F-18 | **Safety monitor — overcurrent shutdown** ≥45.0 A, 3-sample gating | `PASS [host-sim]` 29/29: 44.9A no halt, 45.0A 3-sample halt confirmed | `PENDING` inject 45 A via bench supply + sense resistor, confirm halt | `N/A` | `N/A` | `PENDING` | Overcurrent and thermal share the same THERMAL\_SHUTDOWN flag bit (design note in safety\_monitor.c). A future phase should add OVERCURRENT flag (bit 15 reserved) to distinguish them | `test_safety_monitor.c` + high-current bench test |
| F-19 | **Safety monitor — no-blocking invariant** volatile float reads, zero mutex | `PASS [static]` source inspection: safety\_monitor.c contains zero mutex calls; reads `s_safe_temp_K` / `s_safe_current_A` (volatile float) directly | `PENDING` verify under RTOS by inspecting stack trace during safety task | `N/A` | `N/A` | `N/A` | Volatile float reads are atomic on ARMv7-M for aligned 32-bit access per ARM ARM §A3.5.3 — not guaranteed by C11 standard for all platforms | Code inspection + ARM architecture reference |
| F-20 | **Health monitor** `health_monitor.c` — stack HWM + heap logging @ 0.2 Hz | `PASS [static]` code compiles; function exists and is called from app\_main | `PENDING` read log output; confirm HWMs >1 KB free after 1 hour | `N/A` | `PENDING` 1-hour run; check heap stable after 30 s | `N/A` | uxTaskGetStackHighWaterMark is in words (4 bytes each); misreading the unit would miss a real stack overflow. Log must say bytes or words explicitly | `idf.py monitor` log inspection after 1-hour soak |
| F-21 | **No malloc in realtime path** (telemetry\_task, sensor\_pipeline) | `PASS [static]` grep `\b(malloc|calloc|realloc|free)\b` across main/*.c: 0 matches in realtime path | `N/A` | `N/A` | `N/A` | `N/A` | Third-party libraries (ESP-IDF internals called indirectly) may allocate; verified only for first-party code | Source grep + sanitizer build (if available) |
| F-22 | **Firmware build (idf.py)** complete clean build, 0 warnings | `PENDING` — never built with real ESP-IDF toolchain in this project | `PENDING` | `N/A` | `N/A` | `N/A` | **HIGH RISK:** host gcc compilation (x86) is used for tests but `idf.py build` (xtensa-esp32-elf) has never been run. Header-only ESP-IDF mocks may hide real include errors. This is the single highest-risk gap. | `idf.py build` clean with `-Werror`; confirm .bin size fits flash |

---

## Part 2 — Hardware / Physical / Electrical

| # | Subsystem | host\_verified | esp32\_verified | electrically\_verified | soak\_tested | failure\_injection\_tested | remaining\_risks | verification\_method |
|---|---|---|---|---|---|---|---|---|
| H-01 | **FreeRTOS tick rate** CONFIG\_FREERTOS\_HZ=1000 actually active | `N/A` (kernel config) | `PENDING` confirm in sdkconfig after build; log tick count over 1 s | `N/A` | `N/A` | `N/A` | sdkconfig.defaults sets it, but idf.py menuconfig can override. Must verify in compiled sdkconfig, not just the defaults file | `grep CONFIG_FREERTOS_HZ sdkconfig` after `idf.py build` |
| H-02 | **vTaskDelayUntil jitter** < 50 µs at 1 kHz | `PASS [analytical]` design proves drift-free by construction (vs vTaskDelay) | `PENDING` GPIO toggle on each telemetry cycle; capture with oscilloscope | `N/A` | `PENDING` 1 hour GPIO toggle capture | `N/A` | Interrupt latency and cache misses can push single cycles >100 µs. vTaskDelayUntil averages correctly but individual cycles may spike | Oscilloscope trigger on GPIO, measure period histogram |
| H-03 | **UART 921600 baud signal integrity** | `PASS [pty]` pty proves protocol correctness at software level | `PENDING` measure eye diagram on TX/RX pins with scope | `PENDING` verify 3.3 V levels; USB-serial adapter (CP2102/CH340) baud accuracy | `PENDING` 30 min 0-errors | `N/A` | CP2102: ±0.3% baud error. CH340: ±1%. At 921600, CH340 error → framing errors on long runs. Adapter choice matters | Scope eye + 30 min delivery test with 0 framing errors |
| H-04 | **I²C bus electrical** SDA/SCL pullups, edge rate, 400 kHz timing | `N/A` | `PENDING` probe SDA/SCL with scope; measure rise time (target <300 ns for 400 kHz) | `PENDING` confirm 4.7 kΩ to 3.3 V on both lines; check no bus contention | `N/A` | `PENDING` short SDA to GND (stuck-low), check recovery | Rise time >300 ns at 400 kHz causes ACK errors. PCB trace length + capacitance dominates | Oscilloscope on SDA/SCL during 3-sensor init + steady-state |
| H-05 | **INA226 electrical** 2 mΩ shunt, Kelvin connections | `N/A` | `PENDING` I²C ACK on address 0x40 at power-on | `PENDING` verify Kelvin (4-wire) connection; confirm shunt is Vishay WSL2512 2mΩ ±1% | `PENDING` 30 min @ rated current | `N/A` | Non-Kelvin shunt connection adds PCB trace resistance (typical 0.5–2 mΩ) → doubles measurement error | Continuity + resistance measurement; calibration sweep |
| H-06 | **MPU6050 electrical** address 0x68, AD0 grounding, Vcc decoupling | `N/A` | `PENDING` WHO\_AM\_I read (expect 0x68) | `PENDING` confirm AD0 pin to GND; 100 nF decoupling on Vcc pin; 3.3 V logic | `N/A` | `N/A` | AD0 floating → random address (0x68 or 0x69); init will work intermittently | Logic analyser on I²C init sequence; confirm register 0x75 = 0x68 |
| H-07 | **NTC thermistor circuit** 10 kΩ pullup, ADC GPIO 36 | `N/A` | `PENDING` measure raw ADC code at known temperature | `PENDING` confirm 10 kΩ (1%) pullup to 3.3 V; check ADC source impedance <10 kΩ (ESP32 ADC input requirement) | `N/A` | `PENDING` disconnect NTC (open circuit) → confirm LKG activated | Source impedance >10 kΩ degrades ADC accuracy significantly. RC filter needed if motor PWM noise couples into ADC | Multimeter + reference thermometer calibration |
| H-08 | **ESP32 power supply / brownout** Vcc stability, brownout threshold | `N/A` | `PENDING` measure Vcc ripple under full motor load | `PENDING` measure Vcc with scope during motor step; ripple <50 mV target | `N/A` | `PENDING` slow Vcc ramp-down to 2.8 V, confirm brownout flag in next boot | ESP32 brownout threshold is configurable (2.43–3.19 V); must match actual regulator dropout voltage | Power supply scope + deliberate brownout test |
| H-09 | **Physical mounting / vibration isolation** MPU6050 on winding machine | `N/A` | `N/A` | `PENDING` inspect sensor PCB mounting; rubber grommets recommended | `N/A` | `N/A` | Machine vibration will alias into MPU6050 readings if sensor is rigidly mounted on frame. Resonance frequency of mount may amplify specific frequencies | Idle vib\_rms measurement on machine vs. on bench: target <0.01 g delta |

---

## Part 3 — Python Backend (PC-side)

| # | Subsystem | host\_verified | esp32\_verified | electrically\_verified | soak\_tested | failure\_injection\_tested | remaining\_risks | verification\_method |
|---|---|---|---|---|---|---|---|---|
| P-01 | **TelemetryFrame parser** `esp32_link.py` — unpack, CRC check | `PASS [host-sim]` 100/100 round-trip frames via `verify_frames.py` | `PASS [pty]` field\_test\_faz19b: 10,063 frames, 0 CRC errors | `N/A` | `PASS [pty]` 10.2 s soak via pty | `PASS [pty]` corrupt CRC injection: bringup\_validation scenario 3 confirmed resync | PC Python float unpacking (struct `>f`) assumes IEEE 754 big-endian — universal on modern OS | Python round-trip test + pty field test |
| P-02 | **RealESP32Link** `real_esp32_link.py` — watchdog, auto-reconnect, partial packets, 16 KB resync buffer | `PASS [pty]` bringup\_validation.py 10/10: happy path, partial packets, corrupt CRC, cable unplug, reconnect, burst, junk+resync, watchdog, CPU load, queue overflow | `PENDING` test with real /dev/ttyUSB0 | `N/A` | `PENDING` 30 min real hardware | `PASS [pty]` 10 scenarios including disconnect/reconnect and junk+resync | pty byte delivery may differ from real USB-serial (kernel buffering, latency spikes). Reconnect assumes device node reappears; USB re-enumeration delays vary by OS | bringup\_validation.py 10/10 + real-hardware connect/disconnect test |
| P-03 | **SafetyController** bounds (T∈[3,38]N, RPM≤260, x∈[-5,395]mm, vib≤2g, temp≤523K, dT/dt≤10K/s) | `PASS [host-sim]` phase17\_validation.py 10/10 fault scenarios; each bound triggered and confirmed | `N/A` (PC-side) | `N/A` | `PENDING` 30 min mock soak with synthetic boundary violations | `PASS [host-sim]` 10 deterministic fault scenarios | Bounds are hardcoded; not read from config file. PCB thermal limit (523 K) may be too permissive for specific resin systems | phase17\_validation.py 10/10 |
| P-04 | **MotionController FSM** IDLE→HOMING→READY→RUNNING→PAUSED→ESTOP | `PASS [host-sim]` phase17\_validation.py; all state transitions exercised | `N/A` | `N/A` | `N/A` | `PASS [host-sim]` ESTOP injection from all states | No timeout on HOMING state; if machine never homes, FSM stalls | phase17\_validation.py state transition coverage |
| P-05 | **WindingPlanner** Clairaut geodesics, c=8.827 mm, k=21 circuits, α₀=10.17° | `PASS [host-sim]` math validated; overlap=0.013 mm, buildup=1.527x | `N/A` | `N/A` | `N/A` | `N/A` | G-code output never run on a real machine; tool-path correctness (avoid mandrel collision, dome clearance) requires physical trial winding | Math validation + first-article winding trial |
| P-06 | **TelemetryDB / SQLite recording** | `PASS [pty]` 9,562 frames recorded in field\_test\_faz19b; session integrity verified | `PENDING` 30 min soak: 108,000+ frames, check no write errors | `N/A` | `PENDING` 1-hour soak, check DB file size growth and WAL behavior | `PENDING` disk-full injection | SQLite WAL mode requires periodic checkpoint; unbounded WAL growth on long runs if checkpoint never triggered | field\_test\_faz19b + long-soak DB integrity check |
| P-07 | **AnomalyDetector / CUSUM** tension + vibration | `PASS [host-sim]` phase17\_validation.py | `N/A` | `N/A` | `N/A` | `PASS [host-sim]` synthetic anomaly injection | CUSUM threshold k=0.5σ tuned on synthetic data only; real machine noise distribution unknown | phase17\_validation.py + real-machine baseline calibration |
| P-08 | **AI advisory path isolation** AI output → SafetyValidator → MotionController only | `PASS [static]` code inspection: no direct path from AI module to motion command | `N/A` | `N/A` | `N/A` | `PENDING` adversarial advisory (output extreme values) → confirm blocked by SafetyValidator | One-hop: if SafetyValidator has a bug, AI reaches motion. No independent second barrier | Code inspection + adversarial input test |

---

## Part 4 — PySide6 UI

| # | Subsystem | host\_verified | esp32\_verified | electrically\_verified | soak\_tested | failure\_injection\_tested | remaining\_risks | verification\_method |
|---|---|---|---|---|---|---|---|---|
| U-01 | **TelemetryWorker** 1 kHz→30 Hz coalesce, queued Qt signals | `PASS [host-sim]` phase17\_d2\_validation.py 8/8; no UI freeze in ui\_freeze\_test.py | `N/A` | `N/A` | `PASS [host-sim]` soak\_simulation: 1.4% FPS jitter over 10 s | `N/A` | Queue depth not bounded in published validation; under pathological 1 kHz + slow GPU, queue could grow unboundedly | ui\_freeze\_test + soak\_simulation |
| U-02 | **LiveProductionPanel** 30 Hz pyqtgraph charts, 7 KPIs | `PASS [host-sim]` fps\_benchmark: 30.6 FPS offscreen | `N/A` | `N/A` | `PASS [host-sim]` 10 s soak, +0 KB memory growth | `N/A` | Offscreen rendering does not exercise GPU compositing; real GPU FPS may differ. PyOpenGL fallback path untested on all drivers | fps\_benchmark (offscreen) + real-display manual test |
| U-03 | **CommissioningPanel** port picker, 8 live diagnostic counters | `PASS [host-sim]` bringup\_validation.py 10/10 | `N/A` | `N/A` | `N/A` | `PASS [host-sim]` disconnect + reconnect counter updates | Port auto-discovery not implemented (CLAUDE.md §11 P2); requires manual port entry | bringup\_validation.py counter update checks |
| U-04 | **ReplayPanel** REALTIME/FAST/STEP/PAUSE/SEEK modes | `PASS [host-sim]` replay\_stress.py | `N/A` | `N/A` | `N/A` | `N/A` | Seek on very long sessions (>10M frames) not stress-tested; DB query time uncharacterized | replay\_stress.py + large-session test |
| U-05 | **3D winding visualization** `winding_3d.py` PyOpenGL mandrel + fiber path | `PASS [static]` code exists and renders in smoke test | `N/A` | `N/A` | `N/A` | `N/A` | PyOpenGL requires hardware-accelerated GL; headless offscreen uses software fallback which may differ visually | Manual visual inspection on real display |
| U-06 | **Shutdown integrity** DB flush on SIGINT / window close | `PASS [host-sim]` shutdown\_integrity.py | `N/A` | `N/A` | `N/A` | `PENDING` SIGKILL (ungraceful) during active record: confirm no DB corruption | SIGKILL bypasses Qt shutdown handler; WAL journal may leave DB in recoverable-but-unmerged state | shutdown\_integrity.py + SIGKILL corruption test |

---

## Part 5 — System Integration

| # | Subsystem | host\_verified | esp32\_verified | electrically\_verified | soak\_tested | failure\_injection\_tested | remaining\_risks | verification\_method |
|---|---|---|---|---|---|---|---|---|
| I-01 | **End-to-end: ESP32 → UART → PC parser → UI → TelemetryDB** | `PASS [pty]` field\_test\_faz19b: 10,063 frames, 0 CRC, 0 sync, 9,562 in DB, ~24 FPS offscreen | `PENDING` run same test on real /dev/ttyUSB0 port | `PENDING` oscilloscope confirmation of UART signal levels | `PENDING` 30 min at 1 kHz: target ≥99% delivery, 0 CRC, DB write errors = 0 | `PASS [pty]` fault-window injection: INA/IMU/THERM each isolated and recovered | pty introduces ~0.1 ms added latency vs real USB-UART; framing errors not possible in pty (kernel handles it) | field\_test\_faz19b + real-hardware end-to-end run |
| I-02 | **Auto-reconnect on cable unplug** < 3 s reconnect | `PASS [pty]` bringup\_validation scenario 4 (disconnect+reconnect within 3 s window) | `PENDING` physically unplug USB cable; measure reconnect time | `N/A` | `N/A` | `PASS [pty]` disconnect injected; confirmed reconnect + resync | USB re-enumeration time varies (0.5–2.5 s typical); combined with 3 s target leaves little margin on slow hosts | bringup\_validation.py + real unplug test with timer |
| I-03 | **Boot sequence** safety\_monitor\_init() → uart\_stream\_init() → telemetry\_task\_start() → safety\_monitor\_start() | `PASS [static]` app\_main.c order is correct; health monitor is last | `PENDING` observe idf.py monitor output for correct log sequence | `N/A` | `N/A` | `N/A` | If uart\_stream\_init() is called before safety\_monitor\_init(), the reset reason log is lost — but it is in the right order in current code | `idf.py monitor` boot log inspection |
| I-04 | **Cold-start UART boot gunk skip** RealESP32Link syncs within 500 ms | `PASS [pty]` bringup\_validation scenario 7 (junk+resync) | `PENDING` observe real ESP32 boot output on USB-serial | `N/A` | `N/A` | `N/A` | ESP32 boot messages include Unicode characters (ROM boot log). RealESP32Link must not mistake them for valid frame starts — tested in pty but ROM message byte sequence may differ | Real ESP32 power-cycle with monitor + `field_test_faz19.py` timing |
| I-05 | **30-minute sustained soak** | `PASS [pty]` field\_test\_faz19b: 10.2 s (not 30 min — this is a abbreviated test) | `PENDING` real ESP32 hardware; 30 min minimum | `N/A` | `PENDING` **THIS IS THE CRITICAL MISSING VALIDATION** | `N/A` | Long-run failure modes (heap fragmentation, watchdog, I²C bus lock-up, thermal drift) only manifest after minutes to hours | `idf.py monitor` + `field_test_faz19.py` with real port, 30 min wall clock |

---

## Summary Table — Confidence by Category

| Category | Host-Verified | ESP32-Verified | Electrically-Verified | Overall Confidence |
|---|---|---|---|---|
| Wire protocol / CRC | ✅ Complete | ⏳ Pending | N/A | **HIGH** (logic proven, physical untested) |
| UART streaming | ✅ pty-proven | ⏳ Pending | ⏳ Pending | **MEDIUM** (pty ≠ real baud clock) |
| I²C drivers (INA226, MPU6050, NTC) | ✅ Mock-proven | ⏳ Pending | ⏳ Pending | **MEDIUM** (logic proven, electrical untested) |
| Sensor pipeline (LKG, flags, concurrency) | ✅ Complete | ⏳ Pending | N/A | **HIGH** (best-tested subsystem) |
| Safety monitor (C firmware) | ✅ 29/29 | ⏳ Pending | ⏳ Pending | **MEDIUM** (esp_restart() stub on host) |
| FreeRTOS timing (1 kHz, jitter) | ✅ Analytical | ⏳ Pending | ⏳ Pending | **LOW** (cannot validate without real chip) |
| Firmware build (idf.py) | ❌ **NEVER BUILT** | ⏳ Pending | N/A | **CRITICAL GAP** |
| Hardware electrical | N/A | ⏳ Pending | ⏳ Pending | **UNKNOWN** |
| Python backend | ✅ Complete | N/A | N/A | **HIGH** |
| PySide6 UI | ✅ 30.6 FPS, 0 KB growth | N/A | N/A | **HIGH** (offscreen; real GPU untested) |
| End-to-end integration | ✅ pty-proven | ⏳ Pending | ⏳ Pending | **MEDIUM** |

---

## Critical Gaps — Ordered by Risk

| Priority | Gap | Consequence if not addressed |
|---|---|---|
| 🔴 **P0-A** | Firmware never built with `idf.py` (xtensa toolchain) | Any include error, linker issue, or ABI mismatch will be discovered at flash time, not before |
| 🔴 **P0-B** | FreeRTOS task priority: telemetry\_task prio=10 > sensor\_i2c\_task prio=9 — potential priority inversion if sensor task holds cache mutex when telemetry task preempts it | Under worst-case scheduling, telemetry may busy-wait on mutex while sensor task is preempted — violates <10 µs timing spec |
| 🔴 **P0-C** | 30-minute hardware soak never performed | Heap fragmentation, watchdog, I²C lock-up, thermal drift are all invisible without sustained run |
| 🟡 **P1-A** | ADC Vref not calibrated per-chip | NTC temperature error up to ±5°C without `esp_adc_cal`; could trigger thermal shutdown prematurely |
| 🟡 **P1-B** | INA226 calibration vs reference ammeter not done | ±10% measurement error possible with uncalibrated shunt path |
| 🟡 **P1-C** | Overcurrent and thermal shutdown share same flag bit (THERMAL\_SHUTDOWN) | PC-side cannot distinguish cause; diagnosis on post-mortem is harder |
| 🟢 **P2-A** | SQLite WAL unbounded growth on multi-hour run | Not a correctness issue; disk-space issue on embedded targets |
| 🟢 **P2-B** | Port auto-discovery not implemented | Operator must know USB device path; usability issue, not safety issue |

---

*Last updated: 2026-05-27 by Claude Code session. Update this document whenever a test is run or new evidence is obtained.*
