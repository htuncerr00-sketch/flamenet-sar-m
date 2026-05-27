# EXPECTED_BOOT_LOG.md — Golden Boot Reference
## filament_winding_telem  |  Faz 19C  |  ESP32 / ESP-IDF v5.3

**Status:** TEMPLATE — evidence labels indicate derivation method  
**Last updated:** 2026-05-27  
**Binary:** `faz19c_safety/.../build/filament_winding_telem.bin`  
**Toolchain:** xtensa-esp32-elf-gcc 13.2.0, ESP-IDF v5.3  

Evidence labels used throughout this document:
- `(host-sim)` — verified by running the host-test build
- `(analytical)` — derived by reading source code exactly; not yet run on silicon
- `(silicon-pending)` — must be confirmed on real hardware; expected value is a best estimate

---

## BOOT SEQUENCE TIMELINE

```
T+0 ms      Power-on / reset assertion released
T+0 ms      Boot ROM executes — prints ROM banner to UART0
T+~50 ms    2nd-stage bootloader prints bootloader header
T+~300 ms   Application binary loaded, decompressed, CRC verified
T+~310 ms   FreeRTOS scheduler starts; app_main() invoked
T+~310 ms   safety_monitor_init() — classifies reset reason, sets boot flags
T+~312 ms   app_main: "Filament Winding Telemetry Firmware — Faz 19C"
T+~312 ms   app_main: "Reset reason: power-on"
T+~313 ms   uart_stream_init() — UART1 driver installed, pins routed
T+~314 ms   uart_stream: "UART1 @ 921600 baud, TX=GPIO17, RX=GPIO16, ringbuf=4096"
T+~315 ms   telemetry_task_start() calls sensor_pipeline_init()
T+~315 ms   sensor_pipeline_init(): i2c_bus_init() + ina226_init() + mpu6050_init() + thermal_init()
            (no ESP_LOGI from sensor_pipeline_init itself — analytical)
T+~320 ms   sensor_i2c_task created ("sens", prio 9, core 1)
T+~321 ms   sensor_task: "sensor I²C task started @ 100 Hz"
T+~322 ms   telemetry_task created ("telem", prio 10, core 1)
T+~322 ms   telem_task: "telemetry task started, period=1 tick(s)"
T+~323 ms   app_main: "Telemetry streaming @ 1 kHz; sensors @ 100 Hz"
T+~324 ms   safety_monitor_task created ("safety_mon", prio 3, core 1)
T+~325 ms   health_task created ("health", prio 1, any core)
T+~325 ms   app_main() returns; FreeRTOS keeps tasks running
T+~325 ms   First telemetry frame emitted on UART1 (binary, not human-readable)
T+5000 ms   health_task first log tick
```

---

## SECTION 1 — Boot ROM Output (UART0, 115200 baud)

These lines are produced by the ESP32 mask ROM before the IDF bootloader
runs. They are **not** controllable by firmware. The exact date string varies
by chip stepping but follows a fixed pattern.

```
ets Jun  8 2016 00:22:57
```
[REQUIRED] (silicon-pending) — The ROM banner always appears. Date string is
mask-ROM hardcoded; "Jun  8 2016" is the standard ESP32 date. A different date
indicates an ESP32-S2/S3 or a rare stepping. If absent entirely, UART0 is not
connected or the baud rate is wrong (115200 N81).

```
rst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)
```
[REQUIRED] (analytical) — `rst:0x1` = power-on reset (ESP_RST_POWERON).
The `boot:0x13` strapping (GPIO0=high, GPIO2=high, GPIO12=low) means SPI
fast flash boot with the standard DevKitC strapping. Acceptable values for
`boot:` are `0x13` or `0x17`; values with `0x01` (UART download mode)
indicate GPIO0 was held low — release it and reset.

```
configsip: 0, SPIWP:0xee
clk_drv:0x00,q_drv:0x00,d_drv:0x00,cs0_drv:0x00,hd_drv:0x00,wp_drv:0x00
mode:DIO, clock div:1
load:0x3fff0030,len:7172
load:0x40078000,len:15740
load:0x40080400,len:4
0x40080400: _init at ??:?
```
[OPTIONAL] (silicon-pending) — SPI flash configuration lines. `mode:DIO`
is normal for ESP32-DevKitC. `mode:QIO` also acceptable; `mode:SLOW_READ`
indicates marginal flash power.

---

## SECTION 2 — Second-Stage Bootloader (UART0)

IDF v5.3 bootloader banner. Timestamps in parentheses are milliseconds since
reset (FreeRTOS ticks have not started yet; this is the bootloader's own
counter).

```
ESP-IDF v5.3 2nd stage bootloader
```
[REQUIRED] (analytical) — Version string must say "v5.3". If a different
IDF version is shown, the binary was built with a mismatched toolchain.

```
compile time May 27 2026 XX:XX:XX
```
[OPTIONAL] (silicon-pending) — Build timestamp. Must be a date >= the binary
build date. Stale timestamps indicate a rebuild was not done.

```
Chip is ESP32-D0WD-V3 (revision v3.1)
```
[REQUIRED] (silicon-pending) — Chip identity. `D0WD-V3` is the standard dual-core
production ESP32. `revision v3.1` is common; `v1.0` and `v2.0` are older
stepping and are acceptable but may have errata.  
If `Single Core` appears: a single-core variant is installed — the firmware pins
tasks to core 1 and assumes dual-core; this WILL work for telemetry but the
intent is dual-core. Document the variant.

```
Features: WiFi, BT, Dual Core, 240MHz, VRef calibration in efuse, Coding Scheme None
```
[OPTIONAL] (silicon-pending) — "240MHz" confirms maximum CPU speed. "VRef
calibration in efuse" is required for accurate ADC readings (NTC thermistor
depends on ADC1 CH0). If absent, NTC readings may drift by ±6%.

```
Crystal is 40MHz
```
[REQUIRED] (silicon-pending) — Crystal frequency. UART baud generation
is derived from this. If the crystal is 26 MHz (some modules), the firmware
must be rebuilt with `CONFIG_ESP32_XTAL_FREQ_26=y` or UART will produce
garbage at 921600 baud.

```
MAC: XX:XX:XX:XX:XX:XX
```
[OPTIONAL] (silicon-pending) — Burned-in MAC address. Record for asset tracking.

```
Enabling RNG early entropy source...
Loading app partition at offset 0x10000
```
[OPTIONAL] (analytical) — Normal bootloader progress. Any `E (...)` error
here is FATAL — the partition table is corrupt or the flash erase failed.

```
Disabling RNG early entropy source...
Launching app partition at offset 0x10000
```
[REQUIRED] (analytical) — If this line does not appear and the bootloader
stalls, the binary at 0x10000 is corrupt or the flash write failed.
Re-flash.

---

## SECTION 3 — IDF System Init (UART0)

These messages come from the ESP-IDF startup code before `app_main()` is
called. The `I (NNN)` prefix uses the IDF log format:
`<level> (<timestamp_ms>) <tag>: <message>`.

```
I (29) boot: ESP-IDF v5.3 2nd stage bootloader
```
[OPTIONAL] (analytical) — Second occurrence of IDF version, now with IDF
timestamp prefix.

```
I (302) cpu_start: App cpu up.
```
[REQUIRED] (analytical) — Core 1 started. If absent, a single-core build
was flashed or the chip is a single-core variant. Tasks pinned to core 1
(telem, sens, safety_mon) will not start.

```
I (302) cpu_start: Pro cpu start user code
```
[REQUIRED] (analytical) — Core 0 proceeding to user code (app_main).

```
I (310) cpu_start: chip rev: v3.1
I (310) cpu_start: CPU Freq: 240 MHz
```
[OPTIONAL] (silicon-pending) — Confirms CPU speed. If "CPU Freq: 160 MHz"
appears, `CONFIG_ESP32_DEFAULT_CPU_FREQ_240=y` is not set in sdkconfig —
rebuild.

```
I (316) cpu_start: Application information:
I (321) cpu_start: Project name:     filament_winding_telem
I (327) cpu_start: App version:      1
I (331) cpu_start: Compile time:     May 27 2026 XX:XX:XX
I (337) cpu_start: ELF file SHA256:  XXXXXXXXXXXXXXXX
I (344) cpu_start: ESP-IDF:          v5.3
```
[REQUIRED] (analytical) — `Project name` must be `filament_winding_telem`
(from `CMakeLists.txt: project(filament_winding_telem)`). If a different
name appears, the wrong binary was flashed.

```
I (350) heap_init: Initializing. RAM available for dynamic allocation:
I (357) heap_init: At 3FFAE6E0 len 00001920 (6 KiB): DRAM
I (363) heap_init: At 3FFB3090 len 0002CF70 (179 KiB): DRAM
I (369) heap_init: At 3FFE0440 len 0001FBC0 (126 KiB): D/IRAM
I (375) heap_init: At 3FFE4350 len 0001BCB0 (111 KiB): D/IRAM
I (382) heap_init: At 400973E0 len 00008C20 (35 KiB): IRAM
```
[OPTIONAL] (silicon-pending) — Heap region layout. Exact addresses vary
by firmware size; the DRAM total should be >150 KiB for a healthy system.
If less than 80 KiB DRAM is shown at init, the firmware is unusually large
and the health monitor's 32 KB threshold may trigger immediately.

```
I (390) spi_flash: detected chip: generic
I (394) spi_flash: flash size: 4MB
```
[OPTIONAL] (silicon-pending) — Flash size. 4 MB is standard ESP32-DevKitC.
The firmware binary is ~228 KB; any module with >=2 MB flash is sufficient.

```
I (398) coexist: coex firmware version: XXXXXXXX
```
[OPTIONAL] (analytical) — Appears if WiFi/BT coexist is compiled in. This
firmware does not enable WiFi; the line may not appear.

```
I (410) main_task: Started on CPU0
```
[REQUIRED] (analytical) — The IDF "main task" (which calls app_main) started
on CPU0. If this is absent, the scheduler did not start cleanly.

---

## SECTION 4 — app_main() Startup Sequence (UART0)

This is the section most relevant to bring-up verification. Lines are produced
by source code in `faz19c_safety/main/app_main.c`.

### Step 1: safety_monitor_init() — reset reason classification

The following two lines come from `safety_monitor.c` (TAG = `"safety_monitor"`),
called from within `safety_monitor_init()` before any IDF task logging.

**Normal power-on (ESP_RST_POWERON):**

```
I (415) safety_monitor: safety_monitor_init: reason=power-on boot_flags=0x0000
```
[REQUIRED] (analytical) — This line is produced by the exact code:
`ESP_LOGI(TAG, "safety_monitor_init: reason=%s boot_flags=0x%04x", ...)`.
`reason=power-on` comes from `safety_monitor_reset_reason_str(SAFETY_RESET_POWER_ON)`.
`boot_flags=0x0000` means no brownout or watchdog flag was set.

Note: There is NO additional warning log on power-on. The `ESP_LOGW` calls
for BROWNOUT and WATCHDOG branches are NOT taken on a clean power-on.

### Step 2: app_main banner lines (TAG = `"app_main"`)

```
I (416) app_main: Filament Winding Telemetry Firmware — Faz 19C
```
[REQUIRED] (analytical) — Exact string from
`ESP_LOGI(TAG, "Filament Winding Telemetry Firmware — Faz 19C")` in
`faz19c_safety/main/app_main.c`. If "Faz 19A" or "Faz 19B" appears,
the wrong app_main.c was compiled (the repo has older versions in
`faz19_firmware/` and `faz19b_sensors/`).

```
I (418) app_main: Reset reason: power-on
```
[REQUIRED] (analytical) — Exact string from
`ESP_LOGI(TAG, "Reset reason: %s", safety_monitor_reset_reason_str(...))`.
On power-on this is `"power-on"`. String values from
`safety_monitor_reset_reason_str()`:
- `"power-on"` — ESP_RST_POWERON (expected on first flash)
- `"brownout"` — ESP_RST_BROWNOUT (see Section 8)
- `"watchdog"` — ESP_RST_WDT / INT_WDT / TASK_WDT (see Section 9)
- `"panic"` — ESP_RST_PANIC or ESP_RST_DEEPSLEEP (see Section 10)
- `"software-reset"` — ESP_RST_SW (intentional esp_restart() from safety_halt)
- `"unknown"` — unrecognized code (investigate)

### Step 3: uart_stream_init() (TAG = `"uart_stream"`)

```
I (420) uart_stream: UART1 @ 921600 baud, TX=GPIO17, RX=GPIO16, ringbuf=4096
```
[REQUIRED] (analytical) — Exact format string from `uart_stream.c`:
`ESP_LOGI(TAG, "UART%d @ %d baud, TX=GPIO%d, RX=GPIO%d, ringbuf=%d", ...)`.
Values: UART_NUM=1, UART_BAUD_RATE=921600, UART_TX_PIN=17, UART_RX_PIN=16,
UART_TX_BUF_SIZE=4096.

If this line does NOT appear, `uart_driver_install()` or `uart_param_config()`
or `uart_set_pin()` failed and the next line will be:

```
E (420) app_main: UART init failed; halting
```
[FATAL] (analytical) — app_main returns immediately. No telemetry will flow.
Common causes: UART1 already claimed by another driver; GPIO 17 or 16 in
use as a strapping pin on an unusual board variant. Check
`uart_driver_install` return code.

### Step 4: telemetry_task_start() — sensor pipeline and task creation

`telemetry_task_start()` first calls `sensor_pipeline_init()`. **There is no
ESP_LOGI call inside `sensor_pipeline_init()`** — the function is silent on
success. (The TAG variable exists in sensor_pipeline.c but is unused in
init path; this produces a compiler warning `-Wunused-variable` in the build
log, which is benign.) (analytical)

Sensor-level failures during init are also silent at the log level — the
pipeline degrades gracefully. To add diagnostic logging for bring-up, add a
one-time `ESP_LOGI` after each sensor init call. This is recommended before
the first silicon session.

After `sensor_pipeline_init()` returns, the two realtime tasks are created.

**sensor_i2c_task logs from within the task function** (not from the creator):

```
I (430) sensor_task: sensor I²C task started @ 100 Hz
```
[REQUIRED] (analytical) — Exact string from `telemetry_task.c` line 94:
`ESP_LOGI("sensor_task", "sensor I²C task started @ 100 Hz")`.
Note: the tag is the string literal `"sensor_task"`, NOT a TAG static
variable. This is consistent with the source — `sensor_i2c_task()` is a
static function in `telemetry_task.c` and does not have a module-level TAG.

**telemetry_task logs from within its task function:**

```
I (431) telem_task: telemetry task started, period=1 tick(s)
```
[REQUIRED] (analytical) — Exact string from `telemetry_task.c`:
`ESP_LOGI(TAG, "telemetry task started, period=%u tick(s)", (unsigned)period_ticks)`.
TAG = `"telem_task"`. `period=1` confirms `pdMS_TO_TICKS(1) = 1` because
`CONFIG_FREERTOS_HZ=1000` makes 1 tick = 1 ms. If `period=0` appears, the
FreeRTOS tick rate is higher than expected (unlikely) or lower than expected
(e.g., default 100 Hz gives `pdMS_TO_TICKS(1) = 0`, which means the task
runs as fast as possible without sleeping — a critical bug). If `period=0`:
check `CONFIG_FREERTOS_HZ` in sdkconfig.

**app_main confirmation line:**

```
I (432) app_main: Telemetry streaming @ 1 kHz; sensors @ 100 Hz
```
[REQUIRED] (analytical) — Appears only if `telemetry_task_start()` returned
`pdPASS`. If absent, the next line is:

```
E (432) app_main: telemetry_task_start failed: -1
```
[FATAL] (analytical) — Heap exhaustion or core 1 not available.
`xTaskCreatePinnedToCore` returns `errCOULD_NOT_ALLOCATE_REQUIRED_MEMORY`
(-1) if the 4096-word stack cannot be allocated. Total stack demand:
`sens` (4096 words = 16 KB) + `telem` (4096 words = 16 KB) = 32 KB minimum.
If heap at this point is <32 KB, task creation fails.

### Step 5: safety_monitor_start() (TAG = `"safety_monitor"` — but NO log on success)

`safety_monitor_start()` calls `xTaskCreatePinnedToCore` for `"safety_mon"`
(priority 3, core 1, 2048 words stack). There is **no ESP_LOGI on success**
in the current source. On failure:

```
W (433) app_main: safety_monitor_start failed — running without runtime safety
```
[WARNING] (analytical) — Heap is very tight; thermal and overcurrent shutdown
monitoring is inactive. Treat as a STOP condition unless the shortage is
explained. Common cause: heap near-exhausted by previous task allocations.

### Step 6: health_monitor_start()

`health_monitor_start()` creates the `"health"` task (priority 1, any core,
3072 words stack). There is **no log line on success** — the task itself logs
at its first 5-second tick. (analytical)

### End of app_main()

```
I (434) main_task: Returned from app_main()
```
[OPTIONAL] (analytical) — IDF framework logs this when app_main returns.
This is normal and expected — FreeRTOS task scheduling continues.

---

## SECTION 5 — Steady-State UART0 Output (First 5 Seconds)

After app_main() returns, the only UART0 output should come from the
`health` task (every 5 seconds) and from `safety_monitor` on fault events.
During the first 5 seconds, UART0 should be SILENT. (analytical)

If any of the following appear during the first 5 seconds, investigate:

```
E (...) <any tag>: ...
```
[WARNING] — Any ERROR-level log in steady state indicates a driver fault
or assertion failure. Identify the tag and consult the relevant .c file.

```
W (...) <any tag>: ...
```
[WARNING] — WARN-level logs in steady state indicate a threshold breach
(low heap, low stack) or sensor fault. Expected count during normal bring-up
with healthy sensors: zero.

UART1 (921600 baud, GPIO17 TX) will be streaming binary telemetry frames
continuously from this point. Do not connect a text-mode terminal to UART1.

---

## SECTION 6 — Health Monitor First Tick (~T+5000 ms)

The health_task fires every 5000 ms using `vTaskDelayUntil`. The first tick
occurs approximately 5 seconds after the task was created (T+~5325 ms from
reset). TAG = `"health"`.

```
I (5334) health: heap free=183240  min_since_boot=181920  iram_free=33792
```
[REQUIRED] (silicon-pending) — Exact format:
`ESP_LOGI(TAG, "heap free=%u  min_since_boot=%u  iram_free=%u", ...)`.
Specific values are silicon-dependent. Expected ranges:
- `heap free` > 150,000 (150 KB) for a clean Faz 19C binary
- `min_since_boot` should be within 5 KB of `heap free` (no large allocations
  occurred after startup)
- `iram_free` typically 30,000–40,000 bytes on ESP32 with this firmware

If `heap free` < 32,768 (32 KB), the next line will also appear:

```
W (5334) health: LOW HEAP: XXXXX bytes free (warn < 32768)
```
[WARNING] — Low heap. If this fires on first tick, something allocated heap
after tasks were created (a violation of the no-malloc rule in the realtime
path — investigate). The `sensor_pipeline_init()` creates a FreeRTOS mutex
(`xSemaphoreCreateMutex()`) which allocates ~80 bytes of heap once at init
time. This is the only expected dynamic allocation.

```
I (5335) health: stack HWM: telem=2856  sens=2640  health=1984  (bytes)
```
[REQUIRED] (silicon-pending) — Exact format:
`ESP_LOGI(TAG, "stack HWM: telem=%u  sens=%u  health=%u  (bytes)", ...)`.
`telem` and `sens` are looked up by task name `"telem"` and `"sens"` via
`xTaskGetHandle()`. `health` is the health_task's own HWM.

Expected healthy values (silicon-pending, estimates based on stack sizes):
- `telem` > 1000 bytes (4096-word stack, light task — only struct copy + telem_pack)
- `sens` > 800 bytes (4096-word stack, heavier due to I²C driver frames)
- `health` > 500 bytes (3072-word stack)

If `telem=0` or `sens=0` appears: the task name lookup by `xTaskGetHandle`
failed, which means the task was created with a different name or crashed.
This is a STOP condition.

If any HWM < 256 bytes, the corresponding low-stack warning fires:

```
W (5335) health: LOW STACK telem: XXX bytes free
W (5335) health: LOW STACK sens:  XXX bytes free
```
[WARNING] — Stack overflow imminent. Increase stack size in
`xTaskCreatePinnedToCore` call for the affected task (currently 4096 words).

---

## SECTION 7 — Normal Power-On: Complete Golden Boot Log

The following is the full expected UART0 output for a clean power-on of a
correctly wired, correctly flashed Faz 19C board. Timestamps are illustrative;
actual values vary by ±50 ms depending on flash read speed and chip temperature.

```
ets Jun  8 2016 00:22:57

rst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)
configsip: 0, SPIWP:0xee
clk_drv:0x00,q_drv:0x00,d_drv:0x00,cs0_drv:0x00,hd_drv:0x00,wp_drv:0x00
mode:DIO, clock div:1
load:0x3fff0030,len:7172
load:0x40078000,len:15740
load:0x40080400,len:4
0x40080400: _init at ??:?

ESP-IDF v5.3 2nd stage bootloader
compile time May 27 2026 XX:XX:XX
Chip is ESP32-D0WD-V3 (revision v3.1)
Features: WiFi, BT, Dual Core, 240MHz, VRef calibration in efuse, Coding Scheme None
Crystal is 40MHz
MAC: XX:XX:XX:XX:XX:XX
Enabling RNG early entropy source...
Loading app partition at offset 0x10000
Disabling RNG early entropy source...
Launching app partition at offset 0x10000

I (29) boot: ESP-IDF v5.3 2nd stage bootloader
I (302) cpu_start: App cpu up.
I (302) cpu_start: Pro cpu start user code
I (310) cpu_start: chip rev: v3.1
I (310) cpu_start: CPU Freq: 240 MHz
I (316) cpu_start: Application information:
I (321) cpu_start: Project name:     filament_winding_telem
I (327) cpu_start: App version:      1
I (331) cpu_start: Compile time:     May 27 2026 XX:XX:XX
I (337) cpu_start: ELF file SHA256:  XXXXXXXXXXXXXXXX
I (344) cpu_start: ESP-IDF:          v5.3
I (350) heap_init: Initializing. RAM available for dynamic allocation:
I (357) heap_init: At 3FFAE6E0 len 00001920 (6 KiB): DRAM
I (363) heap_init: At 3FFB3090 len 0002CF70 (179 KiB): DRAM
...
I (410) main_task: Started on CPU0
I (415) safety_monitor: safety_monitor_init: reason=power-on boot_flags=0x0000
I (416) app_main: Filament Winding Telemetry Firmware — Faz 19C
I (418) app_main: Reset reason: power-on
I (420) uart_stream: UART1 @ 921600 baud, TX=GPIO17, RX=GPIO16, ringbuf=4096
I (430) sensor_task: sensor I²C task started @ 100 Hz
I (431) telem_task: telemetry task started, period=1 tick(s)
I (432) app_main: Telemetry streaming @ 1 kHz; sensors @ 100 Hz
I (434) main_task: Returned from app_main()

[~5 seconds of silence on UART0]
[UART1 is streaming binary telemetry at 1 kHz during this window]

I (5334) health: heap free=183240  min_since_boot=181920  iram_free=33792
I (5335) health: stack HWM: telem=2856  sens=2640  health=1984  (bytes)

[health_task repeats every 5 s; UART0 is otherwise silent]
```
(analytical) — All log text derived from reading source; timestamp values
and heap/stack numbers are estimates pending silicon confirmation.

---

## SECTION 8 — BROWNOUT RESET Boot Log

A brownout reset occurs when VCC drops below the brownout detection threshold
(default ~2.43 V on ESP32). The ESP32 latches `ESP_RST_BROWNOUT` as the
reset reason.

**Changes vs normal power-on (only differences shown):**

```
rst:0xC (RTCWDT_BROWN_OUT_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)
```
[REQUIRED] (analytical) — ROM reset reason code `0xC` = brownout. The
exact string `RTCWDT_BROWN_OUT_RESET` is ROM-defined and will appear
verbatim.

```
W (415) safety_monitor: Boot reason: BROWNOUT — supply voltage dipped
```
[REQUIRED] (analytical) — Produced by the `ESP_RST_BROWNOUT` branch in
`safety_monitor_init()`. The `W` (WARN) level is intentional — this is
not a crash but a hardware event requiring attention.

```
I (415) safety_monitor: safety_monitor_init: reason=brownout boot_flags=0x1000
```
[REQUIRED] (analytical) — `reason=brownout` from
`safety_monitor_reset_reason_str(SAFETY_RESET_BROWNOUT)`.
`boot_flags=0x1000` = `TELEM_FLAG_BROWNOUT` (bit 12 = 0x1000).
This flag will be ORed into every telemetry frame's `flags` field until
the next power-cycle — the PC UI will see `BROWNOUT` bit set.

```
I (416) app_main: Filament Winding Telemetry Firmware — Faz 19C
I (418) app_main: Reset reason: brownout
```
[REQUIRED] (analytical) — `"brownout"` from reset reason string function.

All subsequent lines are identical to the normal power-on boot.

**Implications:** A brownout boot followed by another brownout boot within
60 seconds indicates an unstable power supply. The firmware will run but
the PC-side `TelemetryDB` will record the brownout flag. The safety bounds
checker in the PC code is unaffected (safety is PC-side). The ESP32
brownout detector does NOT protect against slow supply sag — only
instantaneous drops. Check:
- Decoupling capacitors on ESP32 VCC (100 µF electrolytic + 100 nF ceramic)
- Cable resistance under load current
- Power supply current rating vs motor inrush

---

## SECTION 9 — WATCHDOG RESET Boot Log

A watchdog reset occurs when a FreeRTOS task does not yield within the WDT
timeout (default 5 s per `sdkconfig.defaults: ESP_TASK_WDT_TIMEOUT_S=5`).
The chip stores `ESP_RST_TASK_WDT` (or `ESP_RST_INT_WDT` for the interrupt
watchdog).

**Changes vs normal power-on (only differences shown):**

```
rst:0x8 (TG1WDT_SYS_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)
```
[REQUIRED] (silicon-pending) — ROM reset reason code for task WDT.
Codes `0x8` (TG1WDT_SYS_RESET) or `0x9` (TG0WDT_SYS_RESET) indicate
watchdog. Code `0x6` (SW_CPU_RESET) with `TG0WDT_CPU_RESET` is the
interrupt watchdog variant.

```
W (415) safety_monitor: Boot reason: WATCHDOG RESET
```
[REQUIRED] (analytical) — Produced by the `ESP_RST_WDT / ESP_RST_INT_WDT
/ ESP_RST_TASK_WDT` branch. Same `W` level for all three WDT variants.

```
I (415) safety_monitor: safety_monitor_init: reason=watchdog boot_flags=0x4000
```
[REQUIRED] (analytical) — `reason=watchdog`,
`boot_flags=0x4000` = `TELEM_FLAG_WATCHDOG_RESET` (bit 14 = 0x4000).

```
I (418) app_main: Reset reason: watchdog
```
[REQUIRED] (analytical) — `"watchdog"` string.

**Implications:** The most likely cause in this firmware is the `telem_task`
or `sensor_i2c_task` blocking unexpectedly on I²C. The telemetry task itself
feeds the WDT implicitly via `vTaskDelayUntil` (yields every 1 ms).
A blocked I²C call in `sensor_i2c_task` that does not time out within 5 s
would trigger the task WDT. The I²C driver has a built-in timeout parameter;
verify `i2c_bus_init()` configures a timeout of <1 s (100 ms recommended).

A watchdog reset is a STOP condition for bring-up. Do not proceed to soak
testing without identifying and fixing the root cause.

---

## SECTION 10 — PANIC (GURU MEDITATION) Boot Log

A panic occurs on: NULL pointer dereference, stack overflow reaching the
guard page, assertion failure via `ESP_ERROR_CHECK`, abort(), or undefined
instruction.

**Before the reset (during the panic session, visible in previous UART0
session):**

```
Guru Meditation Error: Core  0 panic'ed (LoadProhibited). Exception was unhandled.
```
[WARNING] (analytical) — Format is ROM-defined. `Core 0` or `Core 1`
depending on where the fault occurred. Exception type varies.

```
Core 0 register dump:
PC      : 0x400XXXXX  PS      : 0x00060XX0  A0      : 0x800XXXXX  A1      : 0x3FFXXXXX
...
Backtrace: 0x400XXXXX:0x3FFXXXXX 0x400XXXXX:0x3FFXXXXX ...
```
[WARNING] (silicon-pending) — Capture and decode with
`xtensa-esp32-elf-addr2line -pfiaC -e build/filament_winding_telem.elf <PC>`.

**On the next boot (after panic-induced reset):**

```
rst:0x3 (SW_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)
```
[REQUIRED] (silicon-pending) — Code `0x3` = software reset after panic.
Note: in `safety_monitor.c`, both `ESP_RST_PANIC` and `ESP_RST_DEEPSLEEP`
map to `SAFETY_RESET_PANIC`, and the boot flag set is
`TELEM_FLAG_WATCHDOG_RESET` (bit 14). This is a deliberate reuse of the
available flag bit — a separate PANIC flag bit is not yet defined.

```
W (415) safety_monitor: Boot reason: PANIC / unexpected reset
```
[REQUIRED] (analytical) — Exact string from the `ESP_RST_PANIC` branch.

```
I (415) safety_monitor: safety_monitor_init: reason=panic boot_flags=0x4000
```
[REQUIRED] (analytical) — `reason=panic`,
`boot_flags=0x4000` = `TELEM_FLAG_WATCHDOG_RESET`.
The PC side sees WATCHDOG_RESET bit set on a panic boot — this is a known
limitation of the current flag assignment. The human-readable UART log
distinguishes between watchdog and panic via the text string.

```
I (418) app_main: Reset reason: panic
```
[REQUIRED] (analytical) — `"panic"` string.

**Implications:** A panic is a STOP condition. Never accept repeated panics.
Decode the backtrace, fix the root cause, and verify the fix with a full
soak test before production commissioning.

---

## SECTION 11 — SOFTWARE RESET (Intentional safety_halt)

When the safety monitor triggers (`THERMAL SHUTDOWN` or `OVERCURRENT SHUTDOWN`),
it calls `esp_restart()` after a 100 ms UART drain delay. The next boot will
show `ESP_RST_SW`.

**Before the restart (visible in the safety-halt session):**

```
E (XXXX) safety_monitor: THERMAL SHUTDOWN: temp exceeded 373.1 K for 3 samples — restarting
```
[WARNING] (analytical) — Exact format from `safety_halt_thermal()`:
`ESP_LOGE(TAG, "THERMAL SHUTDOWN: temp exceeded %.1f K for %d samples — restarting", ...)`.
The `SAFETY_TEMP_SHUTDOWN_K = 373.15f` threshold prints as `373.1 K`.
`SAFETY_CONSEC_SHUTDOWN = 3` samples.

```
E (XXXX) safety_monitor: OVERCURRENT SHUTDOWN: 45.0 A >= 45.0 A for 3 samples — restarting
```
[WARNING] (analytical) — Note: the overcurrent log message has a bug —
both the measured value and the threshold print as `SAFETY_CURRENT_MAX_A`
(the same variable). The measured current value is not passed to the log
format in the current source (`safety_halt_overcurrent()` in
`faz19c_safety/main/safety_monitor.c` line 85–86 uses
`SAFETY_CURRENT_MAX_A` for both arguments). This is a cosmetic bug; the
shutdown behaviour is correct.

**On the next boot:**

```
rst:0x3 (SW_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)
```
[REQUIRED] (silicon-pending) — `0x3` = SW_RESET. Same code as panic boot.

```
I (415) safety_monitor: safety_monitor_init: reason=software-reset boot_flags=0x0000
```
[REQUIRED] (analytical) — `reason=software-reset` (from `ESP_RST_SW` branch
which sets `SAFETY_RESET_SOFTWARE`; no warning flag is set for an intentional
restart). boot_flags=0x0000 — the SAFE_HALT and THERMAL_SHUTDOWN flags from
the previous session are NOT persisted across resets (they live in RAM only).
The PC UI will see a clean boot. This is by design — the safety event is
visible in the TelemetryDB session record from the previous session.

```
I (418) app_main: Reset reason: software-reset
```
[REQUIRED] (analytical) — `"software-reset"` string.

---

## SECTION 12 — Deviations: Acceptable vs Concerning

### Acceptable Variations

| Observation | Acceptable Reason | Action |
|---|---|---|
| Timestamp values differ from this document by ±200 ms | Flash read speed varies by chip temperature and flash vendor | None |
| `heap free` differs by ±20 KB from the examples in Section 6 | Firmware size changes affect initial heap; IDF version changes heap overhead | None if >150 KB |
| `stack HWM: telem=XXXX` higher than examples | Compiler optimization level or IDF version changes stack frame depths | None if >512 bytes |
| `Chip is ESP32-D0WD-V3 (revision v1.0)` or `v2.0` | Older chip revision — has some errata (see Espressif errata doc) | Check errata list; note in session record |
| `mode:QIO` instead of `mode:DIO` | QIO-capable flash on the module | None — faster is fine |
| `Crystal is 26MHz` | 26 MHz crystal module | STOP — rebuild firmware with `CONFIG_ESP32_XTAL_FREQ_26=y` or baud rate will be wrong |
| One W-level WARN from `sensor_pipeline` on first tick | I²C bus capacitance causes a NACK on first transaction; second attempt succeeds | Acceptable if `INA226_OK` / `IMU_OK` / `THERMAL_OK` all reach ≥98% within 2 seconds |
| `I (...) main_task: Returned from app_main()` present | Normal IDF behavior when app_main() returns | None |
| health_task first tick at T+5100 ms instead of T+5000 ms | FreeRTOS scheduling jitter on first `vTaskDelayUntil` | None; subsequent ticks will be drift-free |

### Concerning Variations (Investigate Before Proceeding)

| Observation | Likely Cause | Action |
|---|---|---|
| `period=0` in `telem_task: telemetry task started, period=0 tick(s)` | `CONFIG_FREERTOS_HZ` is not 1000; `pdMS_TO_TICKS(1)` rounded to 0 | Check sdkconfig; `CONFIG_FREERTOS_HZ=1000` must be set |
| `telem=0` or `sens=0` in health HWM line | Task lookup by name failed; task may have crashed | Check for earlier FATAL or guru meditation |
| `heap free` < 100,000 on first health tick | Unusual heap fragmentation or unexpected malloc | Grep for `malloc` in all .c files linked |
| `boot_flags=0x4000` on a board that was NOT intentionally watchdog-reset | Task blocked too long on I²C; firmware hung silently before reset | Check I²C timeout configuration; add logic-analyser to SDA/SCL |
| `boot_flags=0x1000` (BROWNOUT) more than once per session | Persistent power supply instability | Fix power supply before soak test |
| Any `E (...)` line during steady state (after T+500 ms) | Driver or sensor fault | Identify tag; read corresponding .c file; add diagnostic logging |
| `sensor_task: sensor I²C task started @ 100 Hz` absent | Task creation failed (heap) or sensor_pipeline_init blocked | Check heap; check I²C for bus contention |
| `telem_task: telemetry task started, period=1 tick(s)` absent | Task creation failed after sensor task creation succeeded | Check heap; `sens` (16 KB stack) may have exhausted remaining heap |

### Stop Conditions (Do Not Proceed to Soak)

| Observation | Stop Reason |
|---|---|
| `E (...) app_main: UART init failed; halting` | No telemetry stream possible |
| `E (...) app_main: telemetry_task_start failed: -1` | Core telemetry infrastructure did not start |
| `Guru Meditation Error` during normal operation | Firmware crash; fix and reflash |
| `rst:0x8` (WDT) on every boot in a boot loop | Task blocking loop; firmware cannot run stably |
| `Crystal is 26MHz` with a 921600-baud binary | UART baud divisor calculated for 40 MHz; all frames will be garbled |
| `Project name: filament_winding` (old name) or any name other than `filament_winding_telem` | Wrong binary flashed |
| `Faz 19A` or `Faz 19B` in the banner line | Wrong app_main.c compiled; Faz 19C safety layer is absent |

---

## SECTION 13 — Known Absence: No Log from sensor_pipeline_init()

`sensor_pipeline_init()` in `faz19c_safety/main/sensor_pipeline.c` does
**not** emit any `ESP_LOGI` line on success or failure. The `TAG` variable
exists (`static const char *TAG = "sensor_pipeline"`) but is only reachable
by the `ESP_PLATFORM` build; no call to `ESP_LOGI` appears in the init path.

This means the first silicon session will have no direct visibility into which
sensors initialized successfully from the UART0 log alone. Options:

1. **Infer from telemetry flags** — read flags field from UART1 binary stream
   within the first second: bit 9 (`INA226_OK`), bit 10 (`IMU_OK`), bit 11
   (`THERMAL_OK`). If any bit is 0, that sensor failed init or its first read.

2. **Add diagnostic log lines (recommended for bring-up)** — add to
   `sensor_pipeline_init()` in the Faz 19C source, after each sensor init:
   ```c
   ESP_LOGI(TAG, "i2c_bus_init done");
   ESP_LOGI(TAG, "ina226_init: %s", err == I2C_OK ? "OK" : "FAIL");
   ESP_LOGI(TAG, "mpu6050_init: %s", err == I2C_OK ? "OK" : "FAIL");
   ESP_LOGI(TAG, "thermal_init: %s", err == THERMAL_OK ? "OK" : "FAIL");
   ```
   These lines do NOT affect the wire format. Remove before production build.

3. **Add register-level diagnostic** — in `ina226_init()` and `mpu6050_init()`,
   log the WHO_AM_I / manufacturer ID register values. This is the most useful
   single addition for first silicon bring-up.

(analytical)

---

## SECTION 14 — Quick Reference: Log Tags to Source Files

| Tag string (as appears in log) | Source file | Notes |
|---|---|---|
| `app_main` | `faz19c_safety/main/app_main.c` | `static const char *TAG = "app_main"` |
| `safety_monitor` | `faz19c_safety/main/safety_monitor.c` | `static const char *TAG = "safety_monitor"` |
| `uart_stream` | `faz19_firmware/uart_stream.c` (shared) | `static const char *TAG = "uart_stream"` |
| `telem_task` | `faz19b_sensors/telemetry_task.c` (Faz 19B/C shared) | `static const char *TAG = "telem_task"` |
| `sensor_task` | `faz19b_sensors/telemetry_task.c` | Literal string `"sensor_task"` in `ESP_LOGI` call, not a TAG variable |
| `health` | `faz19b_sensors/health_monitor.c` | `static const char *TAG = "health"` |
| `sensor_pipeline` | `faz19c_safety/main/sensor_pipeline.c` | TAG defined but unused in log calls; tag will NOT appear in any current log line |
| `boot` | IDF 2nd-stage bootloader | ROM/IDF controlled; not in this project's source |
| `cpu_start` | IDF startup code | ROM/IDF controlled |
| `heap_init` | IDF heap init | ROM/IDF controlled |
| `main_task` | IDF main task wrapper | ROM/IDF controlled |

---

## SECTION 15 — Verification Checklist (Fill In During Bring-Up)

| # | Check | Expected | Actual | Status |
|---|---|---|---|---|
| B-01 | Boot ROM banner present | `ets Jun  8 2016 00:22:57` | | |
| B-02 | Reset reason code (normal boot) | `rst:0x1 (POWERON_RESET)` | | |
| B-03 | Crystal frequency | `Crystal is 40MHz` | | |
| B-04 | Project name | `filament_winding_telem` | | |
| B-05 | Faz version in banner | `Faz 19C` | | |
| B-06 | safety_monitor_init log | `reason=power-on boot_flags=0x0000` | | |
| B-07 | UART1 init log | `UART1 @ 921600 baud, TX=GPIO17, RX=GPIO16, ringbuf=4096` | | |
| B-08 | sensor_task log | `sensor I²C task started @ 100 Hz` | | |
| B-09 | telem_task log | `telemetry task started, period=1 tick(s)` | | |
| B-10 | Telemetry streaming confirmation | `Telemetry streaming @ 1 kHz; sensors @ 100 Hz` | | |
| B-11 | No ERROR lines in first 5 s | (none) | | |
| B-12 | health first tick heap | > 150,000 bytes | | |
| B-13 | health telem HWM | > 512 bytes | | |
| B-14 | health sens HWM | > 512 bytes | | |
| B-15 | UART1 first frame (binary) | within 1 s of B-09 | | |
| B-16 | BOOT_OK flag in first frame | bit 0 = 1 | | |

**Sign-off:** `___________`  **Date:** `YYYY-MM-DD`

---

*Generated 2026-05-27 from source code audit of faz19c_safety/main/app_main.c,
faz19c_safety/main/safety_monitor.c, faz19b_sensors/telemetry_task.c,
faz19b_sensors/health_monitor.c, faz19_firmware/uart_stream.c.
All (analytical) claims are verifiable by grep on the cited source files.
All (silicon-pending) values require confirmation on real hardware.*
