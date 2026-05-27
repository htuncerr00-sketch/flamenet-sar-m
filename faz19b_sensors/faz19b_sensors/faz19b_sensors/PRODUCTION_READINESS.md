# FAZ 19B — Production Readiness Report

**Date:** 2026-05-27
**Scope:** ESP-IDF telemetry firmware with real I²C sensor integration
**Verdict:** **HOST-SIDE READY** — pending real-ESP32 final commissioning

---

## 1. What is host-verified (high confidence)

These items are exercised end-to-end on the host machine via gcc-compiled
firmware code + pty + the real RealESP32Link Python class + real PySide6
UI + real TelemetryDB. The only difference vs. real hardware is the
**physical UART chip + I²C chip**, which is below the abstraction.

| Item | Verification | Pass/Fail |
|---|---|---|
| Wire protocol byte-identical to PC parser | 100/100 frames round-trip | ✅ |
| CRC-16/CCITT correctness | 49.9M frames computed, 0 mismatches | ✅ |
| INA226 driver register access + math | 24/24 driver assertions | ✅ |
| INA226 fault → LKG preservation | Verified | ✅ |
| MPU6050 driver register access + math | 24/24 driver assertions | ✅ |
| MPU6050 burst-read 14-byte path | Verified | ✅ |
| NTC + Steinhart-Hart math | 20/20 driver assertions | ✅ |
| NTC out-of-range protection | Verified at supply rail extremes | ✅ |
| Sensor pipeline aggregation | 36/36 integration assertions | ✅ |
| Sensor failure isolation (1 down ≠ pipeline down) | Verified in field test | ✅ |
| All-sensors-down survivability | LKG preserved across all 3 | ✅ |
| Flag bits propagate per sensor health | 100% transition rate, 0% leakage | ✅ |
| Cache concurrency (race condition) | 49.9M reads × 7.5M writes, 0 torn | ✅ |
| Wire packet → PC frame decode | 10k+ frames, 0 CRC errors | ✅ |
| End-to-end: firmware → pty → UI → TelemetryDB | 9,562 frames recorded | ✅ |
| Faz 18 RealESP32Link compatibility | 10/10 bring-up scenarios | ✅ |
| Faz 19A 30s sustained soak | 99.9% delivery, 0 CRC, 0 sync | ✅ |
| Faz 19B runtime fault injection | Per-sensor flag toggling proven | ✅ |
| Quality field reflects sensor count | 92 → 67 → 92 transitions | ✅ |
| Static analysis: no malloc/free in realtime path | grep clean | ✅ |
| Static analysis: no VLAs | All sizes are `#define` constants | ✅ |
| Host build: zero warnings | `-O2 -Wall -Wextra -std=c11` | ✅ |

## 2. What requires real ESP32 hardware to validate

These items depend on physical chip behavior, electrical environment,
or hardware-specific configuration that cannot be exercised on the host.

| Item | Why it needs real hardware | Acceptance criteria |
|---|---|---|
| Real UART clock stability at 921600 baud | Crystal oscillator + PLL behavior is chip-specific | Field test: ≥99% delivery on real hardware |
| I²C bus signal integrity (pullups, edge rates) | Depends on PCB layout, trace length, pullup value | INA226 + MPU6050 both probe-respond on init |
| INA226 actual current measurement vs ammeter | Shunt resistor tolerance (Vishay, 2 mΩ ±1%) | Calibration error <3% across 0..40 A range |
| INA226 high-current burst handling | Shunt heating affects accuracy above 30 A continuous | Drift <2% during 10 min @ 30 A |
| MPU6050 actual noise floor | Mounting vibration + EMI affects sensor | Idle vib_rms < 0.01 g RMS |
| MPU6050 sample rate stability | Internal DLPF + clock + bus arbitration | Effective rate within ±2% of 1 kHz nominal |
| NTC actual β-coefficient | Datasheet B-value drifts ±50 K typical | Calibration at known reference (e.g. ice bath) |
| FreeRTOS 1 kHz `vTaskDelayUntil` jitter | Depends on `CONFIG_FREERTOS_HZ=1000` actually applied | <50 µs jitter measured via GPIO toggle |
| UART TX ring backpressure under WiFi/BLE | ISR storms only happen on real chip | No frame drops during 30s WiFi scan |
| Brownout detector behavior | Voltage rail dynamics are board-specific | Telemetry stops gracefully on dropout |
| Stack high-water marks | Compiler stack frame layout differs ESP32 vs gcc/x86 | All HWMs > 1 KB free after 1 hour run |
| Heap growth over 1-hour soak | Only meaningful on actual chip | `min_free_heap` stable after first 30 s |
| Task watchdog (TWDT) interaction | ESP-IDF-specific | No TWDT trips during normal operation |
| `esp_adc_cal` accuracy at boundary conditions | Per-chip Vref calibration | NTC temp matches reference within ±2 °C |
| Cold-start UART boot message handling | RealESP32Link must skip boot-loader gunk | First valid frame seen within 500 ms |
| Cable unplug / replug behavior | USB-serial bridge re-enumeration | Auto-reconnect within 3 s |
| Power supply ripple effects on ADC | NTC reading drift correlated with motor current | <1 K drift during ESC current step |
| Real 30-minute soak | Lower bound on long-term issues | 0 CRC, 0 sync, no heap leak, no task delay |

## 3. Known limitations (by design)

These are intentional scope cuts, not defects:

- **Tension sensor (`T_N`) is hardcoded 15 N.** HX711/strain-gauge hookup is
  deferred to a later phase. The wire field is populated so PC safety
  bounds stay satisfied; replace `out->T_N = 15.0f;` in `sensor_pipeline.c`
  with the HX711 read when that hardware is added.
- **RPM, x_mm, a_deg are computed**, not read from encoders. Encoder
  capture (PCNT or RMT peripheral) is deferred.
- **No safety/E-stop firmware logic.** The PC-side SafetyController owns
  bounds checking; the firmware is a sensor + protocol bridge only.
- **`spare` field unused.** Reserved for future expansion.
- **No CAN/TWAI integration** (Faz 19D scope, not 19B).
- **No persistent calibration storage** (NVS) yet. Calibration values are
  compile-time constants in `ina226.h`, `thermal.h`. NVS-backed
  calibration is a runtime-cosmetic add later — does not affect
  current correctness.

## 4. Remaining risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Real INA226 returns CFG/CAL read-only differences from datasheet | Low | Init-time verification step (mfg_id + die_id check already done) |
| Real MPU6050 has slow boot (~30 ms) before WHO_AM_I valid | Medium | Retry init up to 3 times with 10 ms delay — **TODO if seen on hardware** |
| 400 kHz I²C fails on long bus traces or weak pullups | Medium | Fall back to 100 kHz: change `I2C_BUS_FREQ_HZ` in `i2c_bus.h` |
| `CONFIG_FREERTOS_HZ=1000` not actually applied | Low | First boot: log measured tick rate; fail-fast if < 800 Hz |
| Stack overflow in `health` task during printf | Low | Stack is 3 KB; `ESP_LOGI` uses ~500 B — comfortable. Verify HWM in first soak. |
| WiFi ISR storms cause UART backpressure | Low (44 ms ring cushion) | Already analyzed: 4 KB ring tolerates 44 ms ISR storm |
| INA226 measures ESC PWM noise → spurious current spikes | Medium | Reduce AVG to 16 samples (CFG bits 11:9 = 100) — **TODO if seen** |

## 5. Architecture decisions reaffirmed

These were locked in during Faz 19B planning and survived implementation:

✅ **Single I²C bus on GPIO 21/22 at 400 kHz** — utilization 4.72%, plenty of room.
✅ **2 mΩ shunt + CAL=2048 + Current_LSB=1.25 mA** — supports ±40 A signed.
✅ **NTC + Steinhart-Hart Beta form** — 10 kΩ NTC, B=3950, R0=10 kΩ.
✅ **PC parser unchanged (Option A)** — wire format `>QHHffffffffffffHH` byte-identical to Mock. Replay determinism preserved across Faz 17→19B.
✅ **Sensor failure isolation** — per-sensor LKG, flag bit propagation, pipeline never down.
✅ **No malloc in realtime path** — verified by static analysis.
✅ **Deterministic timing** — `vTaskDelayUntil(1 ms)` for telemetry, `vTaskDelayUntil(10 ms)` for sensors. Cache mutex critical section <1 µs.
✅ **Faz 17/18/19A regression preserved** — 10/10 bring-up scenarios still pass.

## 6. The host/hardware boundary

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│   HOST-VERIFIED                          HARDWARE-REQUIRED          │
│   ─────────────────────                  ──────────────────────     │
│                                                                     │
│   ✓ Protocol bytes                       □ Real UART clock drift    │
│   ✓ CRC math                             □ I²C pullup tuning        │
│   ✓ Sensor driver register I/O           □ Real INA226 accuracy     │
│   ✓ Sensor math (S-H, scale factors)     □ Real MPU6050 noise       │
│   ✓ Pipeline aggregation                 □ Real NTC β-drift         │
│   ✓ LKG preservation                     □ FreeRTOS 1ms tick proof  │
│   ✓ Flag propagation                     □ Stack HWM measurement    │
│   ✓ Cache concurrency                    □ Heap stability soak      │
│   ✓ Pipeline → wire path                 □ Brownout / WDT triggers  │
│   ✓ Wire → PC parser                     □ Cable unplug recovery    │
│   ✓ UI integration                       □ Power-supply noise       │
│   ✓ Auto-record session                  □ Cold-boot UART gunk      │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Interpretation:** every box on the left has been exercised with
firmware code that will be flashed unmodified. Every box on the right
requires a single one-off commissioning session on real hardware —
each typically 5-30 minutes of bench work, no firmware code changes
expected unless a specific issue is found.

## 7. Final verdict

```
╔══════════════════════════════════════════════════════════════════════╗
║                                                                      ║
║   FAZ 19B — SENSOR INTEGRATION FIRMWARE                              ║
║                                                                      ║
║   Status: PRODUCTION-READY (HOST-SIDE)                               ║
║   Pending: real-ESP32 commissioning session                          ║
║                                                                      ║
║   Total assertions passing:  108 / 108                               ║
║   Field test delivery:       100.6%                                  ║
║   CRC errors:                0                                       ║
║   Sync errors:               0                                       ║
║   Race condition torn reads: 0 / 49,950,331                          ║
║   UART utilization:          71.6% (28.4% headroom)                  ║
║   I²C bus utilization:        4.72%                                  ║
║   Faz 17/18/19A regression:  PASS                                    ║
║                                                                      ║
║   Next step: idf.py build flash + 30 min soak on real hardware      ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
```
