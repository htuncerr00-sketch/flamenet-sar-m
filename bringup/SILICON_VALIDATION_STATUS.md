# SILICON VALIDATION STATUS
## Filament Winding CAM Platform — ESP32 Firmware (Faz 19B + 19C)

**Document class:** Adversarially-conservative engineering boundary statement  
**Date:** 2026-05-27  
**Author:** Engineering / Claude Code session  
**Status:** PRE-SILICON — no physical ESP32 testing has been performed  
**Cross-reference:** VALIDATION_MATRIX.md (full row-by-row detail), BUILD_AUDIT.md (xtensa warnings), TASK_SCHEDULING_AUDIT.md (priority fix record)

---

## PURPOSE OF THIS DOCUMENT

This document defines the strict boundary between what is known and what is
assumed about the firmware's correctness. It is not a marketing summary. It is
not an optimistic confidence assessment. It is a record that must survive code
review by a skeptical safety engineer.

**Rule applied throughout:** if a claim has not been measured on real ESP32
hardware, it is not marked as verified. Analytical or simulation evidence is
called out explicitly. Nothing is promoted from (host-sim) or (pty) to (silicon)
without a real measurement.

---

## 1. VALIDATION LEVEL DEFINITIONS

The following labels appear in every evidence citation throughout this document.
The definitions are intentionally narrow.

| Label | Platform | Mock peripherals? | Real silicon? | Meaning |
|---|---|---|---|---|
| **(host-sim)** | Linux x86_64, gcc 11, pthread | Yes — `i2c_host_mock.c`, synthetic ADC, stub `esp_restart()` | **No** | C source logic verified on a different ISA. Confirms algorithm correctness, not hardware behavior. ESP32 ABI, alignment, endianness, and peripheral register maps are NOT exercised. |
| **(analytical)** | Mathematical proof or static code analysis | N/A | **No** | Derived from first principles or source inspection. No measurement of any kind. |
| **(pty)** | Linux pseudo-terminal pair; real Python stack (`RealESP32Link`, `TelemetryDB`) | Yes — firmware binary is `firmware_driver.c` (x86 simulator), not ESP32 ELF | **No** | End-to-end byte-stream correctness proven at the software layer. USB-serial timing, crystal accuracy, real UART framing, and interrupt latency are NOT represented. |
| **(xtensa-build)** | ESP-IDF v5.3, xtensa-esp-elf-gcc 13.2.0, target: ESP32 | N/A | **No** — binary produced but not flashed or executed | The linker produced a 228 KB ELF/bin. This proves: includes resolve, ABI layout compiles, no undefined symbols. It does NOT prove the binary executes correctly, initializes peripherals, or produces valid telemetry. |
| **(silicon)** | Real ESP32 chip, real I²C bus, real UART, oscilloscope-verified signals | No | **Yes** | The only label that constitutes physical proof. **No item in this document carries this label.** First silicon session is pending. |

**Critical implication:** (host-sim) + (pty) + (xtensa-build) together do not
equal (silicon). They are necessary preconditions, not sufficient proof.

---

## 2. WHAT IS PROVEN — WITH EVIDENCE LABELS

### 2.1 Wire Protocol Logic

The 66-byte frame (`0xAA 0x55` + 64-byte big-endian payload, struct format
`>QHHffffffffffffHH`) is byte-identical between C firmware and Python parser.

| Claim | Evidence | Test count | Test file / method |
|---|---|---|---|
| C `telem_pack()` produces bytes accepted by Python `TelemetryFrame.unpack()` without modification | (host-sim) | 100/100 frames round-tripped | `host_test/test_telem_pack.c` + `verify_frames.py` |
| Frame byte count is exactly 66 on every `telem_pack()` call | (host-sim) | 100 invocations | `test_telem_pack.c` return value check |
| Python parser accepts real firmware byte stream with 0 sync errors | (pty) | 10,063 frames, 0 sync, 0 CRC errors | `field_test_faz19b_runtime.py` |
| Wire format struct layout compiles without error against xtensa toolchain | (xtensa-build) | 1 build | `idf.py build` — 0 errors |

**What this does NOT prove:** Whether ESP32 big-endian struct packing produces
identical bytes to the x86 test. The ESP32 is little-endian internally; struct
`>` (big-endian) packing is performed explicitly by `telem_pack()`, which was
verified correct on x86. The assumption that the same C code produces the same
bytes on xtensa-lx6 is valid by the C standard for portable integer operations,
but has not been confirmed by flashing and reading back a frame.

---

### 2.2 CRC-16/CCITT Computation

Polynomial 0x1021, initial value 0xFFFF, no reflection, no XOR-out. Computed
over payload bytes `[0..61]`, stored at `[62..63]`.

| Claim | Evidence | Test count | Test file / method |
|---|---|---|---|
| CRC algorithm is mathematically correct (matches known-good polynomial) | (analytical) | Golden-vector check | `verify_frames.py` reference table |
| CRC implementation produces 0 mismatches over stress run | (host-sim) | 49,950,331 reads in race stress (all frames CRC-checked) | `test_race_cache.c` coherence check |
| Python parser rejects frames with corrupted CRC | (pty) | Scenario 3 in `bringup_validation.py` | `bringup_validation.py` corrupt-CRC injection |
| CRC byte order (big-endian in payload[62..63]) accepted by Python unpack | (pty) | 10,063 frames, 0 CRC errors | `field_test_faz19b_runtime.py` |

**What this does NOT prove:** Whether silent register corruption or cache
coherency issues on the real ESP32 chip could produce a valid-CRC frame with
wrong field values. CRC detects random bit errors; it does not detect systematic
field value errors caused by a stale cache read (that is addressed by the cache
mutex, which is separately validated).

---

### 2.3 Sensor Driver Logic

Three drivers exercise the virtual I²C bus via `i2c_host_mock.c`. The mock
faithfully implements the register-level interface of each real device.

**INA226 current/voltage sensor (`ina226.c`)**

| Claim | Evidence | Test count | Test file / method |
|---|---|---|---|
| Init reads MFG_ID (expect 0x5449), sets CAL=2048, CFG=0x4127 | (host-sim) | 5 assertions | `test_ina226.c` — sections init through CAL write |
| Current conversion: raw int16 × 1.25 mA/bit → `current_A`, signed (regen) | (host-sim) | 9 assertions | `test_ina226.c` — readings 1, 2, 3 (0 A, +5 A, +40 A, −10 A) |
| Bus voltage conversion: raw uint16 × 1.25 mV/bit → `bus_voltage_V` | (host-sim) | 4 assertions | `test_ina226.c` — readings 1 and 2 |
| I²C fault → `last_read_ok=false`, `n_reads_fail++`, fields preserved | (host-sim) | 5 assertions | `test_ina226.c` — reading 4 (fault injection) |
| Recovery after fault: `last_read_ok=true`, fresh value replaces LKG | (host-sim) | 2 assertions | `test_ina226.c` — reading 5 |
| **Total assertions: 24/24** | (host-sim) | 24 | `test_ina226.c` |

**MPU6050 accelerometer/gyroscope (`mpu6050.c`)**

| Claim | Evidence | Test count | Test file / method |
|---|---|---|---|
| Init sends correct config writes (PWR_MGMT, SMPLRT_DIV, DLPF, FS range) and reads WHO_AM_I=0x68 | (host-sim) | 6 assertions | `test_mpu6050.c` — init block |
| 14-byte burst read parsed: accel[0..2] in ±2g, temp_C, gyro_dps[0..2] | (host-sim) | 12 assertions | `test_mpu6050.c` — readings 1 and 2 |
| Vibration RMS magnitude = 1.0g in 1g gravity field | (host-sim) | 1 assertion | `test_mpu6050.c` — vib_rms check |
| I²C fault → LKG preserved, `n_reads_fail++`, recovery confirmed | (host-sim) | 5 assertions | `test_mpu6050.c` — fault + recovery |
| **Total assertions: 24/24** | (host-sim) | 24 | `test_mpu6050.c` |

**NTC Thermal driver (`thermal.c`)**

| Claim | Evidence | Test count | Test file / method |
|---|---|---|---|
| Steinhart-Hart β-form math: at R=R0, T=T0=298.15 K; at computed R(0°C), T=273.15 K ±0.01 K | (analytical) + (host-sim) | 2 assertions | `test_thermal.c` — pure math section |
| End-to-end: synthetic ADC mV → R via voltage divider → K via β-form | (host-sim) | 6 assertions | `test_thermal.c` — init + read at 25°C, warm corner, cold corner |
| ADC fault → LKG preserved, `n_reads_fail++`, recovery confirmed | (host-sim) | 4 assertions | `test_thermal.c` — fault injection section |
| Supply-rail ADC clamp: near-rail reading rejected or produces plausible hot value | (host-sim) | 2 assertions | `test_thermal.c` — out-of-range protection |
| **Total assertions: 20/20 (note: two are conditional branches; both paths asserted)** | (host-sim) | 20 | `test_thermal.c` |

---

### 2.4 LKG (Last-Known-Good) Preservation Logic

LKG is the mechanism by which a sensor failure leaves wire-frame fields at
their last valid value rather than driving them to zero or NaN. Flag bits
(INA226_OK, IMU_OK, THERMAL_OK) clear to indicate staleness.

| Claim | Evidence | Test count | Test file / method |
|---|---|---|---|
| INA226 fault: `current_A` held at 4.969 A for entire fault window | (pty) | 1 window, constant confirmed | `field_test_faz19b_runtime.py` — `ina_fault` window |
| IMU fault: `vib_x` held at 0.0118 g for entire fault window | (pty) | 1 window, constant confirmed | `field_test_faz19b_runtime.py` — `imu_fault` window |
| Thermal fault: `temp_K` held at 298.15 K for entire fault window | (pty) | 1 window, constant confirmed | `field_test_faz19b_runtime.py` — `thermal_fault` window |
| Single-sensor fault does not propagate to healthy sensors (isolation) | (host-sim) + (pty) | 15 assertions + 3 fault windows | `test_sensor_pipeline.c` sections + `field_test_faz19b_runtime.py` |
| All-three-sensors-down: pipeline alive, LKG frozen, quality=17 | (host-sim) | 8 assertions | `test_sensor_pipeline.c` — all-down section |
| Quality formula: 17 + 25×n_healthy matches per-sensor fault windows | (pty) | healthy=92, 1 fault=67, confirmed across all 3 sensors | `field_test_faz19b_runtime.py` quality column |

**What this does NOT prove:** Real I²C sensors may respond to bus glitches by
returning corrupted data without a NACK condition. In that case, the driver
receives a byte sequence with valid ACK timing but wrong values — LKG is NOT
triggered because no error code is returned. This scenario is hardware-dependent
and cannot be tested without a real bus and fault injection fixture.

---

### 2.5 Safety Monitor Logic — Reset Reason, Thermal, Overcurrent (Faz 19C)

`safety_monitor.c` provides boot-time reset reason classification and
runtime shutdown gating (3-sample consecutive threshold). It reads sensor
data via volatile floats (no mutex, no blocking I/O).

| Claim | Evidence | Test count | Test file / method |
|---|---|---|---|
| Power-on, brownout, watchdog, panic, software reset reasons classified into correct flag bits | (host-sim) | 9 assertions (A1–A5) | `test_safety_monitor.c` section A |
| Reset reason string API returns non-NULL for all reasons | (host-sim) | 2 assertions (B1) | `test_safety_monitor.c` section B |
| Thermal: 373.14 K never halts; 373.15 K halts after exactly 3 consecutive samples | (host-sim) | 7 assertions (C1–C7) | `test_safety_monitor.c` section C |
| Thermal: consecutive counter resets to zero on one in-range sample | (host-sim) | 2 assertions (C7) | `test_safety_monitor.c` — C7 interrupted sequence |
| Overcurrent: 44.9 A never halts; 45.0 A halts after exactly 3 consecutive samples | (host-sim) | 5 assertions (D1–D5) | `test_safety_monitor.c` section D |
| Safe halt: fresh init reports halted=0; 3 violations → halted=1; double-halt is idempotent | (host-sim) | 3 assertions (E1–E3) | `test_safety_monitor.c` section E |
| Threshold boundary confirmed: 373.14K → 373.15K transition at exact 0.01 K step | (host-sim) | 1 assertion (F2) | `test_safety_monitor.c` section F |
| Fresh power-on flags are zero (no spurious BROWNOUT/WATCHDOG/THERMAL/SAFE_HALT at boot) | (host-sim) | 1 assertion (F1) | `test_safety_monitor.c` section F |
| **Total assertions: 29/29** | (host-sim) | 29 | `test_safety_monitor.c` |
| No-blocking invariant: zero mutex calls in `safety_monitor.c` source | (analytical) | grep confirmation | Source inspection — zero `xSemaphoreTake` / `xSemaphoreGive` calls |
| `esp_restart()` is called after SAFE_HALT flag set in telemetry frame | (analytical) | Code trace | `safety_monitor.c` — halt sequence: set flags → 100 ms delay → `esp_restart()` |

**What this does NOT prove:** On the host build, `esp_restart()` is a stub that
returns immediately. The 100 ms UART drain window before restart is simulated
only. Whether the real ESP32 UART FIFO drains within 100 ms before hard reset
has never been measured. The reset reason API (`esp_reset_reason()`) uses
ESP-IDF enum values that are assumed to match compile-time constants — this
assumption is unverified post-link.

---

### 2.6 Python / PySide6 Backend

The Python stack (backend, UI, workers) runs entirely on the development host
and is therefore in a better validation state than firmware.

| Claim | Evidence | Test count | Test file / method |
|---|---|---|---|
| Safety controller bounds (T, RPM, x_mm, vib, temp, dT/dt) enforced correctly | (host-sim) | 10/10 fault scenarios | `phase17_validation.py` |
| UI 7 panels render without freeze at 30 Hz | (host-sim) | 30.6 FPS measured | `fps_benchmark.py` (offscreen) |
| Memory: 0 KB growth over 10 s live telemetry | (host-sim) | 0 KB delta | `memory_leak.py` |
| RealESP32Link handles partial packets, corrupt CRC, reconnect, junk+resync, watchdog | (pty) | 10/10 bring-up scenarios | `bringup_validation.py` |
| TelemetryDB records 9,562 frames with session integrity | (pty) | 1 session, verified | `field_test_faz19b_runtime.py` |
| End-to-end (firmware_driver → pty → Python stack → DB): 10,063 frames, 0 CRC, 0 sync | (pty) | 10,063 frames | `field_test_faz19b_runtime.py` |

---

### 2.7 FreeRTOS Build — Firmware Compiles Without Errors

| Claim | Evidence | Test count | Test file / method |
|---|---|---|---|
| All application source files compile with xtensa-esp-elf-gcc 13.2.0 | (xtensa-build) | 975 compilation units, 0 errors | `idf.py build` — 2026-05-27 |
| Linker produces valid ELF / flashable binary | (xtensa-build) | 1 binary, 228 KB, 0 undefined symbols | `filament_winding_telem.bin` — `BUILD_AUDIT.md` |
| Binary fits in flash partition (22% used, 78% free) | (xtensa-build) | 1 build | `BUILD_AUDIT.md` flash map |
| 5 compiler warnings catalogued and understood | (xtensa-build) | WARN-1 through WARN-5 | `BUILD_AUDIT.md` — deprecated ADC API (WARN-1,2,3), unused TAG (WARN-4,5) |
| FreeRTOS task priority hierarchy correct: sensor(12) > telem(10) > safety(3) > health(1) | (analytical) + (xtensa-build) | Source inspection + compilation | `TASK_SCHEDULING_AUDIT.md` — priority inversion RESOLVED 2026-05-27 |
| No `malloc`/`calloc`/`realloc`/`free` calls in realtime path (`telemetry_task.c`, `sensor_pipeline.c`) | (analytical) | grep: 0 matches in realtime path | Source grep — only comments match |
| sdkconfig.defaults sets FREERTOS_HZ=1000, UART_ISR_IN_IRAM=y, WDT_TIMEOUT=5 | (analytical) | File inspection | `sdkconfig.defaults` — values present in source |

**What this does NOT prove:** The binary has not been flashed or booted.
`sdkconfig.defaults` sets desired values, but `idf.py menuconfig` can override
them; the compiled `sdkconfig` (produced by the actual build) has not been
inspected to confirm `CONFIG_FREERTOS_HZ=1000` was actually compiled in.

---

## 3. WHAT IS NOT PROVEN — MUST NOT BE ASSUMED

Each item below is an assumption, not a measured fact. Assuming these without
silicon evidence is an engineering error.

**Risk severity definitions:**
- **CRITICAL:** Assumption failure could produce a physically unsafe condition (undetected overcurrent, missed thermal shutdown, corrupted telemetry displayed as valid data).
- **HIGH:** Assumption failure causes functional failure (telemetry stops, sensors read wrong, reconnect fails) but not immediate physical hazard.
- **MEDIUM:** Assumption failure degrades reliability or accuracy but system continues operating in degraded mode.
- **LOW:** Assumption failure causes cosmetic or tooling issues with no runtime impact.

---

**Risk 1 — FreeRTOS scheduler behavior under real task preemption** [CRITICAL]

The cache concurrency test (49.9 M reads × 7.5 M producer snapshots, 0 torn
reads) was executed under Linux `pthread` on x86_64. `pthread_mutex_lock()`
has different latency, scheduling quantum, and memory ordering semantics than
FreeRTOS `xSemaphoreTake()` on ESP32. The priority fix (sensor prio 12 >
telem prio 10, both pinned to Core 1) structurally eliminates one inversion
path. However:

- FreeRTOS tick-based preemption at 1 kHz means a task switch can occur at any
  instruction boundary within the 1 ms window.
- The cache `memcpy` is ~50 bytes. On Xtensa LX6 at 240 MHz, this is
  ~20–50 ns. A tick interrupt cannot fire mid-`memcpy` (interrupts are
  disabled during the critical section via the mutex), but the baseline
  interrupt latency of FreeRTOS on ESP32 has not been measured.
- Volatile float reads in `safety_monitor_task()` rely on 32-bit aligned
  access being atomic on Xtensa LX6. This is true per the Xtensa ISA
  specification but has not been confirmed by inspection of the compiled
  assembly for the `safety_monitor.c` volatile read paths.

Evidence label: (silicon-pending)

---

**Risk 2 — I²C bus electrical timing on real hardware** [CRITICAL]

The I²C bus is configured for 400 kHz (Fast Mode). At 400 kHz, the SCL rise
time specification is <300 ns. Rise time is determined by the pullup resistor
value (4.7 kΩ assumed) and the total bus capacitance (PCB traces + sensor
pins + ESP32 GPIO pad capacitance). If the actual bus capacitance exceeds
the design assumption:

- Rise times >300 ns cause ACK timing violations.
- The ESP-IDF I²C driver may retry, producing 2×–4× expected bus utilization.
- In the worst case, the sensor returns NACK on every transaction, triggering
  permanent LKG state for all three sensors simultaneously.

Additionally, the I²C bus-hang recovery path (SCL stuck low due to sensor
power glitch or firmware crash mid-transaction) has not been tested. ESP32
has a hardware I²C timeout register; whether it is configured and whether
`i2c_bus.c` calls the recovery procedure has not been verified on real
hardware.

Evidence label: (silicon-pending)

---

**Risk 3 — ADC Vref per-chip calibration** [HIGH]

The NTC temperature reading depends on the ADC measuring the voltage divider
output accurately. The ESP32 ADC has a factory-specified Vref tolerance of
±6% (approximately 1.0–1.2 V). Without calling `esp_adc_cal_characterize()`
with the eFuse-stored calibration data, temperature readings carry a
systematic error that scales linearly with Vref error. At ±6% Vref error,
the temperature error at the shutdown threshold (373.15 K) is approximately
±5°C. This could cause:

- Premature thermal shutdown (temperature reads 5°C higher than actual).
- Missed thermal shutdown (temperature reads 5°C lower than actual; shutdown
  triggers 5°C too late).

The `thermal.c` source uses the deprecated `esp_adc_cal` API (WARN-1, WARN-2,
WARN-3 in `BUILD_AUDIT.md`). Whether this deprecated path correctly reads
eFuse calibration on the specific silicon revision in use has not been
confirmed.

Evidence label: (silicon-pending)

---

**Risk 4 — Crystal oscillator accuracy at 921600 baud** [HIGH]

The UART baud rate of 921600 is derived from the 40 MHz crystal oscillator
via the ESP32 internal PLL. Crystal accuracy is typically ±20–50 ppm at
room temperature, with drift up to ±100 ppm over temperature. At 921600 baud
with 10 bits per frame:

- Bit period = 1.085 µs
- Acceptable timing error per bit: ±46.9% (UART NRZ) → ±509 ns
- At ±100 ppm: accumulated error per byte = ±0.108 µs (negligible)
- At the receive end (USB-serial adapter), CP2102 is ±0.3%; CH340 is ±1.0%

The real risk is the **USB-serial adapter**, not the ESP32 crystal. A CH340
adapter at 921600 baud accumulates ~1% timing error per byte, leading to
framing errors on long uninterrupted byte streams. The pty-based field test
avoids this entirely because pty is a software construct with no clock domain
mismatch.

Evidence label: (silicon-pending)

---

**Risk 5 — INA226 current measurement accuracy vs reference** [HIGH]

The INA226 is calibrated by writing CAL=2048 to register 0x05, giving
Current_LSB = 1.25 mA/bit. This calibration constant is correct only if the
shunt resistance is exactly 2 mΩ. In practice:

- Shunt resistor tolerance: ±1% (Vishay WSL2512 specified) → ±1% current
  error at any current level.
- PCB trace resistance in series with shunt (if non-Kelvin connection): a
  1 mΩ parasitic adds 50% error at 2 mΩ nominal shunt.
- Shunt self-heating at 40 A: P = I²R = 40² × 0.002 = 3.2 W into a 2 mΩ
  resistor. Temperature coefficient of resistance for the shunt material
  will shift the reading as the resistor heats. This is uncharacterized.

The calibration sweep (0, 5, 10, 20, 40 A vs reference ammeter) has not
been performed. Without it, current reading accuracy is unknown.

Evidence label: (silicon-pending)

---

**Risk 6 — NTC temperature accuracy vs reference thermometer** [HIGH]

The Steinhart-Hart β-form uses β=3950 K as the default constant
(`THERMAL_CALIB_DEFAULT`). NTC manufacturers specify β with a tolerance of
±1–2% (±40–80 K). The impact of a β error of 50 K on the temperature
reading at the shutdown threshold (373.15 K):

- Using the β-form: ΔT ≈ T² × Δβ / β² ≈ (373)² × 50 / (3950)² ≈ 0.44°C
  at room temperature, increasing to ~1.5°C at 100°C.
- This is below the 3-sample gating threshold hysteresis but adds uncertainty
  to absolute accuracy.

A two-point calibration (ice bath at 273.15 K and room at 298.15 K) has not
been performed. Without it, the installed β constant for the specific NTC
batch is assumed, not measured.

Evidence label: (silicon-pending)

---

**Risk 7 — MPU6050 mechanical noise floor** [MEDIUM]

The MPU6050 DLPF is set to 44 Hz bandwidth (config byte 0x03). At rest on
a bench, the expected noise floor is <0.01 g RMS. When mounted on the
winding machine frame, mechanical vibration from the spindle motor and
carriage drive will appear as real accelerometer signal. The driver has no
low-pass filter beyond the DLPF built into the MPU6050. The alarm threshold
for vibration is 2g (safety controller, Python side). Whether real machine
vibration at idle approaches this threshold is unknown.

Additionally, the DLPF register must be written correctly at init. If the
SPI/I²C config byte for DLPF is silently ignored (a known issue on some
MPU6050 clones with register write ACK but no actual effect), the sensor
would operate at its default 260 Hz bandwidth, producing a higher noise floor
and aliased high-frequency motor vibration.

Evidence label: (silicon-pending)

---

**Risk 8 — ESP32 brownout threshold behavior** [HIGH]

The brownout detector threshold is configurable in ESP-IDF menuconfig
(`CONFIG_ESP32_BROWNOUT_DET_LVL`). The default threshold is approximately
2.43 V. The CLAUDE.md §11 P0 checklist tests brownout by dropping Vcc to
2.8 V. If the actual threshold is not confirmed, the test may not trigger
the detector. Conversely, if the power supply has significant ripple under
motor load, the brownout detector may fire spuriously.

The behavior of the UART transmit FIFO at brownout (whether the SAFE_HALT
frame is emitted before the chip resets) has not been verified. The 100 ms
delay in `safety_halt()` before `esp_restart()` is designed to allow this
frame to drain, but the drain time was calculated analytically for a steady
1 kHz rate and has not been measured during a real brownout event.

Evidence label: (silicon-pending)

---

**Risk 9 — Priority inheritance under real FreeRTOS conditions** [MEDIUM]

The priority inversion fix (sensor prio 12 > telem prio 10) was applied
2026-05-27 (see `TASK_SCHEDULING_AUDIT.md`). The fix is structural: the
producer can never be preempted by the consumer while holding the cache mutex
because the producer has higher priority. However:

- FreeRTOS priority inheritance is still active on `xSemaphoreCreateMutex()`.
  If a third task at intermediate priority (e.g., a future CAN bridge task
  at prio 11) is added between sensor and telem, the inversion scenario
  reappears.
- The safety_monitor_task at prio 3 uses volatile float reads (no mutex).
  If the compiler reorders volatile reads on xtensa-lx6 in a way that violates
  the intended load ordering, the snapshot could be internally inconsistent.
  The C11 standard guarantees volatile reads are not reordered relative to
  other volatiles for the same object, but the `s_safe_temp_K` and
  `s_safe_current_A` are separate volatile objects.

Evidence label: (silicon-pending)

---

**Risk 10 — UART1 ring buffer behavior under real ISR timing** [HIGH]

The UART1 TX ring buffer is 4 KB = 62 frames at 66 bytes/frame. Analytically,
this provides 44 ms of burst cushion when the ISR is blocked. This analysis
assumes the ISR block duration is bounded by the worst-case WiFi/BLE
interference window, estimated at <10 ms per CLAUDE.md §6 telemetry notes.

On real silicon:
- WiFi scan can block Core 0 (where WiFi task runs) for up to 50 ms in
  active scan mode, and may steal Cache 0 from Core 1 via cache invalidation.
- Whether the UART TX ISR (pinned to IRAM via `UART_ISR_IN_IRAM=y` in
  sdkconfig.defaults) can service the ring buffer while WiFi is scanning
  has not been measured.
- Ring buffer overflow behavior (silent frame drop vs `uart_write_bytes()`
  blocking) depends on the `pdMS_TO_TICKS(100)` timeout passed to
  `uart_write_bytes()`. Under ISR storm, the 100 ms timeout is longer than
  desired; it would block the telemetry task for 100 ms rather than dropping.

Evidence label: (silicon-pending)

---

**Risk 11 — Heap fragmentation over 30-minute soak** [HIGH]

The realtime path (telemetry_task, sensor_pipeline) contains zero heap
allocation (verified by grep, as (analytical)). However:

- ESP-IDF infrastructure (WiFi, Bluetooth, TCP/IP stack, logging) allocates
  heap internally. Even if this firmware does not use WiFi, the ESP-IDF
  initialization calls may perform allocations during boot.
- FreeRTOS task creation itself calls `pvPortMalloc()` for the stack.
  All stacks are allocated once at startup and are not freed; however, the
  allocation pattern at boot determines initial heap fragmentation.
- After 30 minutes at 1 kHz, ESP_LOGI calls in `health_monitor.c` (at
  0.2 Hz) write to the ESP-IDF log buffer, which may have internal dynamic
  allocation.
- The minimum free heap after boot and 30 minutes of steady-state operation
  has never been measured. The threshold for concern (per CLAUDE.md §11 P0)
  is heap stability after 30 s; this has not been confirmed.

Evidence label: (silicon-pending)

---

**Risk 12 — Stack HWM under real task scheduling** [MEDIUM]

Task stack sizes are set in `app_main.c`. The stack HWM
(`uxTaskGetStackHighWaterMark()`) is reported by `health_monitor.c` at 0.2 Hz
via ESP_LOGI. The HWM is in words (4 bytes each on ESP32). The health
monitor will log a warning if free stack < 256 bytes (64 words). This
threshold and the initial stack sizes have been set based on code inspection,
not measurement.

Real function call depths in the sensor tasks (I²C bus HAL → driver →
pipeline) may exceed the estimate if ESP-IDF internal functions have deeper
call chains than their headers suggest. This cannot be confirmed without
running the firmware and observing the HWM output after one hour.

Evidence label: (silicon-pending)

---

**Risk 13 — Safety shutdown triggering correctly at hardware level** [CRITICAL]

The `safety_monitor.c` shutdown logic (thermal ≥373.15 K for 3 consecutive
100 ms samples; overcurrent ≥45.0 A for 3 consecutive samples) has been
verified in isolation at the logic level (host-sim, 29/29 assertions). The
end-to-end path from a real sensor reading to an actual `esp_restart()` call
has never been exercised:

- The temperature reading path: ADC → mV → Resistance → Kelvin (Steinhart-Hart)
  → `s_safe_temp_K` (volatile float write) → `safety_monitor_task()` volatile
  float read → threshold comparison → halt decision.
- At each step, there is an unverified assumption (ADC accuracy, β constant,
  volatile atomicity, FreeRTOS task scheduling of `safety_monitor_task()`).
- The halt path: `safety_halt()` sets SAFE_HALT flag in the telemetry frame,
  calls `vTaskDelay(pdMS_TO_TICKS(100))` (100 ms wait for UART drain), then
  calls `esp_restart()`. On host, `esp_restart()` is a no-op stub. On real
  silicon, it triggers an immediate chip reset. Whether the UART TX FIFO
  drains within 100 ms has not been measured.

Evidence label: (silicon-pending)

---

**Risk 14 — `esp_restart()` behavior and 100 ms UART drain** [HIGH]

`safety_monitor.c` calls `esp_restart()` after a 100 ms `vTaskDelay()`.
The intent is to allow the UART TX ring buffer to drain so the PC receives
the SAFE_HALT frame before the chip resets. This requires:

1. The UART TX ISR is still running during `vTaskDelay()` (it is, because
   it is an interrupt, not a task). Confirmed (analytical).
2. The TX ring buffer drains within 100 ms at 921600 baud. At 66 bytes/frame
   × 1000 frames/s = 66,000 bytes/s, a single frame takes 0.72 ms to
   transmit. Ring buffer drain time from full (4 KB) is 4096 / 92160 = 44 ms.
   So 100 ms is sufficient if the ring starts from worst case. Confirmed
   (analytical).
3. The PC parser receives the SAFE_HALT frame before the chip resets. The
   USB-serial adapter may have a receive FIFO. If the adapter's FIFO is full
   at the moment of reset, the last frame may be lost. Not confirmed.
4. `esp_restart()` does not immediately abort the UART transmit mid-frame.
   The ESP-IDF implementation of `esp_restart()` disables interrupts and
   then triggers a software reset via the RTC_CTRL register. Whether an
   in-progress UART byte is cleanly terminated has not been verified.

Evidence label: (silicon-pending)

---

**Risk 15 — Volatile float atomicity on real Xtensa LX6 hardware** [CRITICAL]

The safety monitor reads `s_safe_temp_K` and `s_safe_current_A` as
`volatile float` without a mutex. The design rationale (per
`fw_phase19c_report.json`) is that 32-bit aligned float loads are atomic on
Xtensa LX6 per the ISA specification. This is correct per Section 4.3 of the
Xtensa LX6 Instruction Set Architecture Reference Manual for aligned 32-bit
loads/stores (they are single-cycle, non-interruptible). However:

- The compiler must emit aligned 32-bit load/store instructions for the
  volatile float access. If the variable is not 4-byte aligned in the .data
  section, the compiler may emit two 16-bit loads, which are not atomic.
- `volatile float` in C11 does not guarantee alignment; it guarantees that
  the variable is re-read from memory on every access, not that the access
  is atomic.
- The actual assembly generated for `s_safe_temp_K` and `s_safe_current_A`
  reads in the compiled `safety_monitor.c` has not been inspected.
- If the write side (sensor_pipeline `sensor_pipeline_set_safety_snapshot()`)
  and the read side (safety_monitor_task) are on the same core (both pinned
  to Core 1), the FreeRTOS task switch is the only interleaving point, and
  single-instruction atomicity is sufficient. However, if they are ever
  moved to separate cores, this assumption breaks entirely.

Evidence label: (silicon-pending)

---

## 4. PENDING SILICON VALIDATION CHECKLIST

Items are ordered by risk severity. Each item must be completed and its result
recorded before the corresponding row in `VALIDATION_MATRIX.md` can be
marked as silicon-verified.

### CRITICAL — Must complete before any production winding run

1. **Flash firmware and verify boot log** via `idf.py monitor`. Expected log
   lines: `app_main: Faz 19C`, `UART1 @ 921600 baud`, `telemetry task started`,
   `sensor I²C task started @ 100 Hz`, `safety_monitor started`.
   — Blocks: all other items below.

2. **Confirm `CONFIG_FREERTOS_HZ=1000` compiled in**: `grep CONFIG_FREERTOS_HZ sdkconfig`
   after `idf.py build`. Value must be 1000, not 100 (ESP-IDF default).

3. **Verify volatile float assembly alignment**: inspect compiled assembly for
   `safety_monitor_task()` and `sensor_pipeline_set_safety_snapshot()`. Both
   must use `L32I`/`S32I` (4-byte aligned load/store), not `L16UI`/`S16I`.

4. **Run `field_test_faz19.py` on real `/dev/ttyUSB0`**: target ≥99% frame
   delivery, 0 CRC errors, 0 sync errors over 10 s at 1 kHz. This is the
   first confirmation that the xtensa-compiled binary produces valid wire frames.

5. **Verify INA226 MFG_ID = 0x5449 ("TI")**: confirms I²C bus is operational
   and pullup resistors are correct. If this fails, the I²C bus is non-functional
   and all sensor readings are LKG-only.

6. **Verify MPU6050 WHO_AM_I = 0x68**: confirms I²C address and AD0 grounding.
   A return of 0x69 indicates AD0 is floating.

7. **Calibrate INA226 vs reference ammeter** at 0, 5, 10, 20, 40 A. Record
   error %. Target: <3% error at all points. Document shunt connection type
   (Kelvin vs non-Kelvin).

8. **Calibrate NTC at two reference temperatures** (ice bath 273.15 K and room
   temperature 298.15 K). If drift >2 K, update β in `THERMAL_CALIB_DEFAULT`.

9. **Measure vTaskDelayUntil jitter** via GPIO toggle on each telemetry cycle.
   Oscilloscope capture over 1000 cycles. Target: <50 µs peak jitter.
   — Addresses Risk 1 (FreeRTOS scheduler timing).

10. **Test brownout**: slowly ramp supply voltage to 2.8 V. Confirm
    `TELEM_FLAG_BROWNOUT` appears in the first telemetry frame after power
    recovery. Confirm `esp_restart()` occurred (boot log shows it).
    — Addresses Risk 8 (brownout threshold).

11. **Test thermal shutdown at real temperature**: heat NTC to ≥100°C with
    heat gun or calibrated hot plate. Confirm firmware issues `esp_restart()`
    and PC receives a frame with `TELEM_FLAG_SAFE_HALT` + `TELEM_FLAG_THERMAL_SHUTDOWN`.
    — Addresses Risk 13 (safety shutdown end-to-end) and Risk 14 (UART drain).

12. **30-minute sustained soak**: run `field_test_faz19.py` for 30 minutes
    with real hardware. Target: ≥99% delivery, 0 CRC errors, heap stable after
    30 s, all task HWMs >1 KB free.
    — Addresses Risks 11 (heap fragmentation) and 12 (stack HWM).

### HIGH — Complete before extended production use

13. **ADC Vref calibration**: confirm `esp_adc_cal_characterize()` reads
    eFuse calibration data on this specific chip revision. Measure NTC ADC
    output at a known voltage (precision reference source); compare to
    expected ADC code. If eFuse data is absent (some engineering samples),
    perform one-point linearity correction.
    — Addresses Risk 3 (ADC Vref accuracy).

14. **USB-serial adapter baud accuracy check**: measure actual baud rate of
    the connected adapter at 921600 using oscilloscope. If adapter is CH340,
    confirm <1% error or switch to CP2102/FT232.
    — Addresses Risk 4 (crystal / adapter accuracy).

15. **Cable unplug → auto-reconnect**: unplug USB cable physically. Measure
    time to reconnect. Target: <3 s including USB re-enumeration and
    `RealESP32Link` resync.

16. **Task watchdog verification**: confirm watchdog does not fire during 30
    minutes of normal operation (`idf.py monitor` log: no WDT warnings).

### MEDIUM — Complete before first article winding trial

17. **MPU6050 idle noise floor**: measure `vib_x`, `vib_y`, `vib_z` RMS with
    machine at rest on bench. Target: <0.01 g RMS. If higher, check mounting
    resonance and DLPF config byte.
    — Addresses Risk 7 (MPU6050 noise floor).

18. **I²C bus signal integrity**: probe SDA/SCL with oscilloscope during
    3-sensor initialization. Measure rise time (target <300 ns for 400 kHz).
    Confirm no ACK errors in 1000 consecutive transactions.
    — Addresses Risk 2 (I²C electrical timing).

19. **vTaskDelayUntil jitter under WiFi scan**: enable a background WiFi scan
    task; repeat GPIO toggle jitter measurement. Target: no additional frame
    drops with 44 ms ring buffer cushion.
    — Addresses Risk 10 (UART ring buffer under ISR storm).

20. **Stack HWM after 1 hour run**: record `uxTaskGetStackHighWaterMark()`
    for all four tasks after 1 hour of operation. All must report >1 KB free
    (>256 words free).
    — Addresses Risk 12 (stack HWM).

---

## 5. BOUNDARY STATEMENT

### What "Ready for Silicon" MEANS in this context

As of 2026-05-27, the firmware has cleared the following gates:

- 137/137 host assertions pass (host-sim): logic of all C modules confirmed.
- 100/100 wire-format frames byte-identical (host-sim): protocol correctness confirmed.
- 10,063 frames delivered with 0 CRC/sync errors (pty): end-to-end byte-stream confirmed.
- xtensa-esp32 build succeeds: 228 KB binary produced, 0 linker errors, 5 known warnings.
- FreeRTOS priority inversion resolved: sensor(12) > telem(10) eliminates producer-starvation path.

This means the firmware is ready for the **first silicon bring-up session**:
flashing, booting, verifying the UART stream against the PC parser. This session
is the entry point to the silicon validation checklist in §4.

### What "Ready for Silicon" DOES NOT MEAN

The firmware is **not** ready for the following without completing the §4 checklist:

- Unattended 30-minute winding runs. Heap fragmentation, WDT, and I²C lock-up
  are invisible without a real soak.
- Production current sensing. INA226 accuracy is unknown without calibration
  against a reference ammeter. A 10% current measurement error could delay or
  prevent overcurrent shutdown.
- Production temperature shutdown. ADC Vref calibration uncertainty of ±6%
  (Risk 3) produces ±5°C temperature error, which degrades the thermal shutdown
  threshold from 373.15 K to an effective range of 368–378 K.
- Any winding run where safety shutdown must be relied upon. The
  end-to-end thermal shutdown path (sensor → volatile float → safety_monitor_task
  → esp_restart()) has never been exercised end-to-end on real hardware.
- Deployment to a site where the firmware engineer is not physically present
  with a laptop, oscilloscope, and power supply for debugging.

### Definitive no-silicon-proof statement

No assertion in this document, in `VALIDATION_MATRIX.md`, or in any
`fw_phaseXX_report.json` constitutes silicon-level proof. The phrases
"host-verified", "pty-proven", "analytical", and "xtensa-build" are
explicitly defined in §1 to exclude real hardware. A reviewer who treats any
of those labels as equivalent to "tested on real ESP32" is misreading the
evidence.

---

## 6. SIGN-OFF MATRIX

This table will be updated as silicon validation items are completed. Every
row starts unsigned. A signed row requires a real measurement result, not
an assertion about simulation.

| # | Item | Evidence Level | Signed Off | Date | Result / Notes |
|---|---|---|---|---|---|
| S-01 | Wire protocol: C→Python byte-identical | (host-sim) | No | — | 100/100 frames on x86; silicon pending |
| S-02 | CRC-16/CCITT algorithm correctness | (analytical) + (host-sim) | No | — | Math proven; 49.9M stress reads on x86 |
| S-03 | INA226 driver logic | (host-sim) | No | — | 24/24 assertions on x86 mock |
| S-04 | MPU6050 driver logic | (host-sim) | No | — | 24/24 assertions on x86 mock |
| S-05 | Thermal / NTC driver logic | (host-sim) | No | — | 20/20 assertions on x86 mock |
| S-06 | Sensor pipeline LKG isolation | (host-sim) + (pty) | No | — | 36/36 + 3 fault windows; silicon pending |
| S-07 | Cache concurrency: 0 torn reads | (host-sim) | No | — | 49.9M reads on pthread; FreeRTOS pending |
| S-08 | Safety monitor: reset reason classification | (host-sim) | No | — | 29/29 on x86 stub; `esp_reset_reason()` untested |
| S-09 | Safety monitor: thermal shutdown logic | (host-sim) | No | — | 29/29 on x86; end-to-end path untested |
| S-10 | Safety monitor: overcurrent shutdown logic | (host-sim) | No | — | 29/29 on x86; high-current bench test pending |
| S-11 | Safety monitor: no-blocking invariant | (analytical) | No | — | Source grep: 0 mutex calls; assembly not inspected |
| S-12 | xtensa build succeeds | (xtensa-build) | No | — | 228 KB binary, 0 errors, 5 warnings — not flashed |
| S-13 | FreeRTOS task priorities correct | (analytical) + (xtensa-build) | No | — | Source + compile confirmed; runtime scheduling untested |
| S-14 | `CONFIG_FREERTOS_HZ=1000` active post-build | (silicon-pending) | No | — | In sdkconfig.defaults; compiled sdkconfig not inspected |
| S-15 | UART 921600 baud delivery ≥99% on real chip | (silicon-pending) | No | — | pty: 100.6%; real crystal + adapter pending |
| S-16 | vTaskDelayUntil jitter <50 µs at 1 kHz | (analytical) | No | — | Design proves drift-free; oscilloscope pending |
| S-17 | I²C 400 kHz signal integrity | (silicon-pending) | No | — | Not measured; pullup values assumed |
| S-18 | INA226 accuracy vs reference ammeter | (silicon-pending) | No | — | Not calibrated |
| S-19 | MPU6050 WHO_AM_I = 0x68 on real chip | (silicon-pending) | No | — | Not confirmed; AD0 grounding assumed |
| S-20 | NTC temperature accuracy at known points | (silicon-pending) | No | — | β=3950 assumed; two-point calibration pending |
| S-21 | ADC Vref eFuse calibration active | (silicon-pending) | No | — | Deprecated ADC API (WARN-1,2,3); calibration read path not confirmed |
| S-22 | Thermal shutdown end-to-end on real chip | (silicon-pending) | No | — | Logic proven on host; real sensor→restart path untested |
| S-23 | Brownout detection and flag propagation | (silicon-pending) | No | — | Host stub only; real Vcc ramp-down test pending |
| S-24 | 30-minute soak: 0 CRC, heap stable, WDT quiet | (silicon-pending) | No | — | 10.2 s pty run only; real 30-min soak pending |
| S-25 | Stack HWMs >1 KB free after 1 hour | (silicon-pending) | No | — | Not measured |
| S-26 | Volatile float alignment atomic on Xtensa LX6 | (analytical) | No | — | ISA spec cited; assembly not inspected |
| S-27 | `esp_restart()` UART drain ≤100 ms | (analytical) | No | — | Calculated sufficient; not measured under real brownout |
| S-28 | Auto-reconnect <3 s on real USB unplug | (pty) | No | — | pty scenario 4 passed; real USB re-enumeration pending |
| S-29 | Python safety controller bounds correct | (host-sim) | No | — | 10/10 fault scenarios; PC-side, not hardware-dependent |
| S-30 | End-to-end telemetry pipeline on real chip | (silicon-pending) | No | — | pty proven; first real flash session pending |

**Legend for Signed Off column:** "No" means the evidence is insufficient for
silicon sign-off. Once a real measurement is performed, record: engineer name,
date, and the specific numerical result that satisfies the criterion.

---

*End of SILICON_VALIDATION_STATUS.md — 2026-05-27*
*Update this document whenever a silicon measurement is taken. Do not update evidence labels without a corresponding measurement record.*
