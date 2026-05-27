/*
 * include/safety_monitor.h — Boot + Runtime Safety Layer (Faz 19C)
 * ==================================================================
 *
 * Responsibilities:
 *   1. Classify esp_reset_reason() at boot: brownout / WDT / panic / power-on
 *   2. Monitor live temp_K + current_A for hard-shutdown thresholds
 *   3. Consecutive-sample gating: N_CONSEC violations before halt
 *   4. Export flag bits (bits 12-14) merged into telemetry frame
 *
 * Safety invariants:
 *   - safety_monitor_task() NEVER blocks on any mutex or I²C call
 *   - All flag reads/writes use volatile uint16_t (single-word atomic on ARMv7-M)
 *   - No malloc anywhere; single static state object
 *   - On halt: SAFE_HALT flag set first, then esp_restart() — PC sees halt
 *     before UART disconnect
 *
 * Thread model:
 *   safety_monitor_task — low priority, 100 ms loop
 *   caller (any task)   — safety_monitor_get_flags() lock-free read
 */
#ifndef FW_SAFETY_MONITOR_H
#define FW_SAFETY_MONITOR_H

#include <stdint.h>
#include "telemetry_frame.h"

#ifdef ESP_PLATFORM
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#endif

/* ── Shutdown thresholds ────────────────────────────────────────────── */
#define SAFETY_TEMP_SHUTDOWN_K   373.15f   /* 100 °C hard limit         */
#define SAFETY_CURRENT_MAX_A      45.0f    /* > 40 A rated + 12% margin  */
#define SAFETY_CONSEC_SHUTDOWN       3     /* consecutive samples → halt  */

/* ── Reset reason classification ────────────────────────────────────── */
typedef enum {
    SAFETY_RESET_POWER_ON  = 0,
    SAFETY_RESET_BROWNOUT  = 1,
    SAFETY_RESET_WATCHDOG  = 2,   /* HW watchdog or task WDT */
    SAFETY_RESET_PANIC     = 3,   /* software exception / guru meditation */
    SAFETY_RESET_SOFTWARE  = 4,   /* intentional esp_restart() */
    SAFETY_RESET_UNKNOWN   = 5,
} safety_reset_reason_t;

/* ── Public API ─────────────────────────────────────────────────────── */

/*
 * Read and classify esp_reset_reason(). Must be called before
 * safety_monitor_start(). Safe to call from app_main() before any tasks
 * are created.
 */
void safety_monitor_init(void);

/*
 * Return the reset reason determined at init time.
 */
safety_reset_reason_t safety_monitor_get_reset_reason(void);

/*
 * Human-readable label for the reset reason. Never returns NULL.
 */
const char *safety_monitor_reset_reason_str(safety_reset_reason_t r);

/*
 * Create and start the safety monitor FreeRTOS task (100 ms loop).
 * Returns pdPASS on success.
 */
#ifdef ESP_PLATFORM
BaseType_t safety_monitor_start(void);
#else
int safety_monitor_start(void);
#endif

/*
 * Atomic lock-free read of accumulated safety flag bits.
 * Result is ORed into the telemetry frame's flags field by sensor_pipeline.
 *
 * Flag bits returned:
 *   TELEM_FLAG_SAFE_HALT         (bit  8) — any halt triggered
 *   TELEM_FLAG_BROWNOUT          (bit 12) — brownout at boot
 *   TELEM_FLAG_THERMAL_SHUTDOWN  (bit 13) — thermal halt
 *   TELEM_FLAG_WATCHDOG_RESET    (bit 14) — WDT / panic at boot
 */
uint16_t safety_monitor_get_flags(void);

/* ── Host-only test helpers (compiled out on ESP32) ─────────────────── */
#ifndef ESP_PLATFORM

/* Override reset reason used by safety_monitor_init() */
void safety_monitor_host_set_reset_reason(safety_reset_reason_t r);

/* Override sensor readings for one or more steps */
void safety_monitor_host_inject_temp(float temp_K);
void safety_monitor_host_inject_current(float current_A);

/* Run exactly one monitoring iteration (no sleep) */
void safety_monitor_host_step(void);

/* Query and reset internal state for test isolation */
int  safety_monitor_host_halted(void);
void safety_monitor_host_reset(void);

#endif /* !ESP_PLATFORM */

#endif /* FW_SAFETY_MONITOR_H */
