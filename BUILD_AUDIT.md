# BUILD_AUDIT.md — ESP-IDF v5.3 xtensa-esp32 First Real Build

**Date:** 2026-05-27  
**Phase:** Faz 19B firmware (pre-19C safety layer)  
**Target:** ESP32 (xtensa-lx6)  
**Toolchain:** xtensa-esp-elf-gcc 13.2.0 (esp-13.2.0_20240530)  
**ESP-IDF:** v5.3  
**Working directory:** `faz19b_sensors/faz19b_sensors/faz19b_sensors/`

---

## VERDICT

```
★★★  BUILD SUCCEEDED  ★★★
filament_winding_telem.bin  —  0x37ad0 bytes (228 KB), 78% flash free
Bootloader                  —  0x6880 bytes,  7% bootloader partition free
975 compilation units processed, 0 linker errors, 0 fatal compiler errors
```

The firmware **links and produces a flashable binary** against real xtensa
toolchain and ESP-IDF v5.3. All application source files compiled without
errors. No malloc in the realtime path. No undefined symbol errors.

---

## Build Environment

| Item | Value |
|------|-------|
| ESP-IDF version | v5.3 (`/opt/esp-idf`) |
| xtensa-esp-elf-gcc | 13.2.0 (esp-13.2.0_20240530) |
| Host OS | Linux 6.18.5 |
| CMake | invoked via `idf.py` |
| idf-component-manager | 2.4.10 (downgraded from 3.0.2 — see ERROR-1) |
| Python | 3.11.15 |
| Target | esp32 |

---

## Error / Warning Inventory

### ERROR-1: CMakeLists.txt — `esp_adc_cal` component removed (RESOLVED)

| Field | Value |
|-------|-------|
| **Severity** | BLOCKING (CMake configure fails — no build possible) |
| **File** | `main/CMakeLists.txt` line 19 |
| **Root cause** | `esp_adc_cal` was a standalone component in ESP-IDF ≤4.x. In ESP-IDF v5.x it was merged into `esp_adc`. The component name no longer resolves. |
| **CMake error** | `Failed to resolve component 'esp_adc_cal'` |
| **Fix applied** | Changed `esp_adc_cal` → `esp_adc` in `REQUIRES` list |
| **Impact after fix** | CMake configure proceeds; deprecated header still accessible at `esp_adc/deprecated/include/esp_adc_cal.h` |
| **Status** | **RESOLVED** — minimal one-line fix |

---

### WARN-1: `thermal.c` — Legacy ADC calibration driver deprecated

| Field | Value |
|-------|-------|
| **Severity** | WARNING only — compiles, links, runs |
| **File** | `thermal.c:12` (`#include "esp_adc_cal.h"`) |
| **Message** | `#warning "legacy adc calibration driver is deprecated, please migrate to use esp_adc/adc_cali.h and esp_adc/adc_cali_scheme.h"` |
| **Origin** | `esp_adc/deprecated/include/esp_adc_cal.h:17` |
| **Impact** | Will break in a future ESP-IDF major release when deprecated/ folder is removed |

---

### WARN-2: `thermal.c` — Legacy ADC oneshot driver deprecated

| Field | Value |
|-------|-------|
| **Severity** | WARNING only |
| **File** | `thermal.c:13` (`#include "driver/adc.h"`) |
| **Message** | `#warning "legacy adc driver is deprecated, please migrate to use esp_adc/adc_oneshot.h and esp_adc/adc_continuous.h"` |
| **Origin** | `driver/deprecated/driver/adc.h:19` |

---

### WARN-3: `thermal.c` — `ADC_ATTEN_DB_11` deprecated (×2)

| Field | Value |
|-------|-------|
| **Severity** | WARNING |
| **File** | `thermal.c:93`, `thermal.c:100` |
| **Message** | `'ADC_ATTEN_DB_11' is deprecated [-Wdeprecated-declarations]` |
| **Details** | `ADC_ATTEN_DB_11` is now an alias with `__attribute__((deprecated))` for `ADC_ATTEN_DB_12`. Current value is identical (11 dB attenuation), so behaviour is unchanged. |
| **Fix when migrating** | Replace `ADC_ATTEN_DB_11` → `ADC_ATTEN_DB_12` |

---

### WARN-4: `thermal.c` — Unused `TAG` variable

| Field | Value |
|-------|-------|
| **Severity** | WARNING |
| **File** | `thermal.c:15` |
| **Message** | `'TAG' defined but not used [-Wunused-variable]` |
| **Details** | `static const char *TAG = "thermal"` — ESP_LOGx calls were removed or guarded but the TAG declaration was left behind. |

---

### WARN-5: `sensor_pipeline.c` — Unused `TAG` variable

| Field | Value |
|-------|-------|
| **Severity** | WARNING |
| **File** | `sensor_pipeline.c:48` |
| **Message** | `'TAG' defined but not used [-Wunused-variable]` |
| **Details** | Same pattern as WARN-4: TAG declared but no ESP_LOGx path currently uses it in the compilation path. |

---

### WARN-6: esptool — Deprecated CLI option flags (cosmetic)

| Field | Value |
|-------|-------|
| **Severity** | COSMETIC only — from esptool v5.2 deprecating underscored CLI options |
| **Messages** | `--flash_mode` → `--flash-mode`, `--flash_freq` → `--flash-freq`, `--flash_size` → `--flash-size` |
| **Origin** | `idf.py` generating esptool command internally |
| **Impact** | None for current builds; will need idf.py update to suppress |

---

### WARN-7: Python — idf.py `MultiCommand` deprecation (cosmetic)

| Field | Value |
|-------|-------|
| **Severity** | COSMETIC — from Click library internals |
| **Message** | `DeprecationWarning: 'MultiCommand' is deprecated and will be removed in Click 9.0.` |
| **Origin** | `/opt/esp-idf/tools/idf.py:357` |
| **Impact** | None; internal to idf.py |

---

### WARN-8: mbedtls — TLS certificate serial number negative (cosmetic)

| Field | Value |
|-------|-------|
| **Severity** | COSMETIC — from gen_crt_bundle.py processing bundled CA certs |
| **Message** | `CryptographyDeprecationWarning: Parsed a serial number which wasn't positive` |
| **Impact** | Affects TLS bundle, irrelevant to this project (no TLS used) |

---

## Full Deprecated API Surface in `thermal.c`

The following legacy API symbols are used in `thermal.c`. All compile and link
against ESP-IDF v5.3 via the deprecated shim layer. **They are not errors — they are time bombs** for the next major ESP-IDF bump:

| Symbol | Deprecated header | New API |
|--------|-------------------|---------|
| `esp_adc_cal_characteristics_t` | `esp_adc_cal.h` | `adc_cali_handle_t` |
| `esp_adc_cal_characterize()` | `esp_adc_cal.h` | `adc_cali_create_scheme_curve_fitting()` or `_line_fitting()` |
| `esp_adc_cal_raw_to_voltage()` | `esp_adc_cal.h` | `adc_cali_raw_to_voltage()` |
| `adc1_config_width()` | `driver/adc.h` | `adc_oneshot_config_channel()` |
| `adc1_config_channel_atten()` | `driver/adc.h` | `adc_oneshot_config_channel()` |
| `adc1_get_raw()` | `driver/adc.h` | `adc_oneshot_read()` |
| `ADC_ATTEN_DB_11` | `hal/adc_types.h` | `ADC_ATTEN_DB_12` |

**Estimated migration effort:** ~60 lines in `thermal.c`. Non-trivial because the new API is handle-based (init/deinit lifecycle). Architecture unchanged — only the ADC driver calls change.

---

## Binary Metrics

| Metric | Value |
|--------|-------|
| App binary size | 0x37ad0 = 228,048 bytes |
| App partition (smallest) | 0x100000 = 1,048,576 bytes |
| App flash utilisation | **22%** (228 KB / 1024 KB) |
| App flash free | 0xc8530 = 820,528 bytes (**78%**) |
| Bootloader size | 0x6880 = 26,752 bytes |
| Bootloader partition | ~0x7000 = 28,672 bytes |
| Bootloader free | 0x780 = 1,920 bytes (**7%**) |
| Total compilation units | 975 |
| Compiler errors | **0** |
| Linker errors | **0** |
| Application-code warnings | 5 (WARN-1 through WARN-5) |
| Cosmetic / toolchain warnings | 3 (WARN-6, 7, 8) |

---

## What Was NOT Tested (xtensa build scope boundary)

The following items require real hardware and are **outside this build audit**:

| Item | Why not tested here |
|------|---------------------|
| Runtime boot — esp_reset_reason() | Needs hardware boot |
| UART1 @ 921600 frame delivery | Needs hardware UART + USB-serial |
| INA226 I²C calibration accuracy | Needs hardware + reference ammeter |
| MPU6050 WHO_AM_I read | Needs hardware I²C |
| NTC temperature curve vs reference thermometer | Needs hardware + ice bath |
| FreeRTOS tick jitter measurement | Needs scope + GPIO toggle |
| WDT / brownout / panic restart handling | Needs hardware |
| 30-min soak (heap stability, CRC rate) | Needs hardware |
| Task stack HWM at runtime | Needs hardware |
| Priority inversion risk (sensor prio 9 < telem prio 10) | Runtime analysis needed |

---

## Open Issues After This Audit

### ISSUE-1 (P1): Migrate thermal.c to esp_adc v5.x API

The deprecated shim will eventually be removed. Migration is safe —
behaviour-identical, only the call site changes.

**Files to change:** `thermal.c` (all ADC calls), `main/CMakeLists.txt`
(already fixed: `esp_adc_cal` → `esp_adc`).

**Must NOT change:** wire format, calibration constants, Steinhart-Hart math,
`thermal_read()` return type, host-test mock path.

### ISSUE-2 (P1): Fix unused TAG warnings in thermal.c and sensor_pipeline.c

Either add `(void)TAG;` suppression or wrap TAG with
`#ifndef HOST_TEST ... #endif` since the host build already compiles cleanly.

### ISSUE-3 (P0, pre-existing): Task priority inversion risk

`sensor_i2c_task` runs at prio 9, `telemetry_task` at prio 10. The telemetry
task can preempt sensor while sensor holds the cache mutex. This inverts the
intended data flow: telemetry reads stale cache while sensor is mid-update.
Correct fix: raise `sensor_i2c_task` to prio 11 (above telemetry) so sensor
always completes its critical section before telemetry can preempt.

**This is a latent correctness bug — benign in host tests but observable on
real hardware as micro-stale reads.**

---

## Setup Issues Encountered (Pre-Build)

These were environment issues, not firmware defects:

| Issue | Resolution |
|-------|-----------|
| `idf-component-manager` 3.0.2 dropped `--interface_version 2` support | Downgraded to 2.4.10 |
| Missing ESP_IDF_VERSION env var → `TypeError` in semver check | `export ESP_IDF_VERSION=5.3.0` |
| Missing Python venv | Created manually, installed requirements.core.txt |
| Missing constraints file | `touch espidf.constraints.v5.3.txt` |
| `libusb-1.0.so.0` missing for openocd | `apt-get install libusb-1.0-0` |
| Stale non-CMake `build/` directory blocking `set-target` | `rm -rf build/` |

None of these are firmware code issues.

---

*End of BUILD_AUDIT.md — generated 2026-05-27 after first successful xtensa-esp32 build.*
