/*
 * main/safety_monitor.c — Boot + Runtime Safety Layer (Faz 19C)
 * ==============================================================
 *
 * Boot path (safety_monitor_init):
 *   Read esp_reset_reason() → classify → set s_boot_flags
 *
 * Runtime path (safety_monitor_task, 100 ms):
 *   Read sensor snapshot (lock-free, never blocks) → check thresholds
 *   → consecutive-sample gating → safety_halt() on violation
 *
 * safety_halt():
 *   Sets SAFE_HALT + event flag first (so PC sees the flag before UART
 *   disconnects), then calls esp_restart(). On host-build: sets
 *   s_halted=1 without restarting so tests can inspect state.
 *
 * Invariant: this file contains ZERO mutex lock/unlock calls.
 * All inter-task state is written and read via volatile uint16_t
 * (single-word, aligned → atomic on ARMv7-M / ARMv8-M).
 */
#include "safety_monitor.h"

#include <string.h>
#include <stdint.h>

#ifdef ESP_PLATFORM
#  include "esp_system.h"      /* esp_reset_reason(), esp_restart() */
#  include "esp_log.h"
#  include "freertos/FreeRTOS.h"
#  include "freertos/task.h"
static const char *TAG = "safety_monitor";
#else
#  include <stdio.h>
#  include <stdlib.h>
#endif

/* ── Static state ───────────────────────────────────────────────────── */

static safety_reset_reason_t s_reset_reason = SAFETY_RESET_POWER_ON;

/* Flag buckets — volatile so compiler cannot cache them across tasks.
   Bit pattern: boot flags | runtime flags | halt flags.
   Combined view returned by safety_monitor_get_flags(). */
static volatile uint16_t s_boot_flags    = 0;
static volatile uint16_t s_runtime_flags = 0;
static volatile uint16_t s_halt_flags    = 0;

static volatile int s_halted = 0;

/* Consecutive violation counters (only written from safety_monitor_task) */
static int s_temp_consec = 0;
static int s_curr_consec = 0;

/* ── Host-only mock state ────────────────────────────────────────────── */
#ifndef ESP_PLATFORM
static safety_reset_reason_t s_host_reset_reason = SAFETY_RESET_POWER_ON;
static float s_host_temp_K    = 295.15f;
static float s_host_current_A = 0.0f;
#endif

/* ── Internal helpers ────────────────────────────────────────────────── */

static void safety_halt_thermal(void) {
    if (s_halted) return;
    s_halt_flags = (uint16_t)(TELEM_FLAG_SAFE_HALT | TELEM_FLAG_THERMAL_SHUTDOWN);
    s_halted = 1;
#ifdef ESP_PLATFORM
    ESP_LOGE(TAG, "THERMAL SHUTDOWN: temp exceeded %.1f K for %d samples — restarting",
             SAFETY_TEMP_SHUTDOWN_K, SAFETY_CONSEC_SHUTDOWN);
    /* Brief delay so UART FIFO drains and PC sees the SAFE_HALT flag */
    vTaskDelay(pdMS_TO_TICKS(100));
    esp_restart();
#else
    printf("[safety_monitor] THERMAL SHUTDOWN triggered (host — no restart)\n");
#endif
}

static void safety_halt_overcurrent(void) {
    if (s_halted) return;
    s_halt_flags = (uint16_t)(TELEM_FLAG_SAFE_HALT | TELEM_FLAG_THERMAL_SHUTDOWN);
    /* Overcurrent reuses THERMAL_SHUTDOWN bit as generic "runtime shutdown".
       A separate OVERCURRENT flag can be added in a future patch if needed. */
    s_halted = 1;
#ifdef ESP_PLATFORM
    ESP_LOGE(TAG, "OVERCURRENT SHUTDOWN: %.1f A >= %.1f A for %d samples — restarting",
             SAFETY_CURRENT_MAX_A, SAFETY_CURRENT_MAX_A, SAFETY_CONSEC_SHUTDOWN);
    vTaskDelay(pdMS_TO_TICKS(100));
    esp_restart();
#else
    printf("[safety_monitor] OVERCURRENT SHUTDOWN triggered (host — no restart)\n");
#endif
}

static void do_safety_check(float temp_K, float current_A) {
    /* Thermal check */
    if (temp_K >= SAFETY_TEMP_SHUTDOWN_K) {
        s_temp_consec++;
        if (s_temp_consec >= SAFETY_CONSEC_SHUTDOWN) {
            safety_halt_thermal();
        }
    } else {
        s_temp_consec = 0;
    }

    /* Overcurrent check */
    if (current_A >= SAFETY_CURRENT_MAX_A) {
        s_curr_consec++;
        if (s_curr_consec >= SAFETY_CONSEC_SHUTDOWN) {
            safety_halt_overcurrent();
        }
    } else {
        s_curr_consec = 0;
    }

    /* Update runtime "OK" flags when nominal */
    if (!s_halted) {
        s_runtime_flags = 0;
    }
}

/* ── Public API ─────────────────────────────────────────────────────── */

void safety_monitor_init(void) {
    /* Reset all state */
    s_boot_flags    = 0;
    s_runtime_flags = 0;
    s_halt_flags    = 0;
    s_halted        = 0;
    s_temp_consec   = 0;
    s_curr_consec   = 0;

#ifdef ESP_PLATFORM
    esp_reset_reason_t hw_reason = esp_reset_reason();
    switch (hw_reason) {
        case ESP_RST_POWERON:
            s_reset_reason = SAFETY_RESET_POWER_ON;
            break;
        case ESP_RST_BROWNOUT:
            s_reset_reason = SAFETY_RESET_BROWNOUT;
            s_boot_flags  |= (uint16_t)TELEM_FLAG_BROWNOUT;
            ESP_LOGW(TAG, "Boot reason: BROWNOUT — supply voltage dipped");
            break;
        case ESP_RST_WDT:
        case ESP_RST_INT_WDT:
        case ESP_RST_TASK_WDT:
            s_reset_reason = SAFETY_RESET_WATCHDOG;
            s_boot_flags  |= (uint16_t)TELEM_FLAG_WATCHDOG_RESET;
            ESP_LOGW(TAG, "Boot reason: WATCHDOG RESET");
            break;
        case ESP_RST_PANIC:
        case ESP_RST_DEEPSLEEP:
            s_reset_reason = SAFETY_RESET_PANIC;
            s_boot_flags  |= (uint16_t)TELEM_FLAG_WATCHDOG_RESET;
            ESP_LOGW(TAG, "Boot reason: PANIC / unexpected reset");
            break;
        case ESP_RST_SW:
            s_reset_reason = SAFETY_RESET_SOFTWARE;
            break;
        default:
            s_reset_reason = SAFETY_RESET_UNKNOWN;
            break;
    }
    ESP_LOGI(TAG, "safety_monitor_init: reason=%s boot_flags=0x%04x",
             safety_monitor_reset_reason_str(s_reset_reason), (unsigned)s_boot_flags);
#else
    /* Host: use injected reset reason */
    s_reset_reason = s_host_reset_reason;
    switch (s_reset_reason) {
        case SAFETY_RESET_BROWNOUT:
            s_boot_flags |= (uint16_t)TELEM_FLAG_BROWNOUT;
            break;
        case SAFETY_RESET_WATCHDOG:
        case SAFETY_RESET_PANIC:
            s_boot_flags |= (uint16_t)TELEM_FLAG_WATCHDOG_RESET;
            break;
        default:
            break;
    }
#endif
}

safety_reset_reason_t safety_monitor_get_reset_reason(void) {
    return s_reset_reason;
}

const char *safety_monitor_reset_reason_str(safety_reset_reason_t r) {
    switch (r) {
        case SAFETY_RESET_POWER_ON:  return "power-on";
        case SAFETY_RESET_BROWNOUT:  return "brownout";
        case SAFETY_RESET_WATCHDOG:  return "watchdog";
        case SAFETY_RESET_PANIC:     return "panic";
        case SAFETY_RESET_SOFTWARE:  return "software-reset";
        case SAFETY_RESET_UNKNOWN:   return "unknown";
        default:                     return "invalid";
    }
}

uint16_t safety_monitor_get_flags(void) {
    /* Three volatile uint16_t reads — each atomic on ARMv7-M */
    return s_boot_flags | s_runtime_flags | s_halt_flags;
}

/* ── FreeRTOS task ──────────────────────────────────────────────────── */

#ifdef ESP_PLATFORM

static void safety_monitor_task(void *arg) {
    (void)arg;
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(100));
        if (s_halted) continue;  /* halting path calls esp_restart() */

        float temp_K = 295.15f, current_A = 0.0f;
        sensor_pipeline_get_safety_snapshot(&temp_K, &current_A);
        do_safety_check(temp_K, current_A);
    }
}

#define SAFETY_TASK_PRIORITY 3   /* above health(1); below telemetry(10)/sensor(12) */

BaseType_t safety_monitor_start(void) {
    /* Pin to Core 1 alongside sensor and telemetry tasks so volatile float
       reads of s_safe_temp_K / s_safe_current_A are same-core — no cross-core
       coherency concerns with the sensor_pipeline safety snapshot. */
    return xTaskCreatePinnedToCore(safety_monitor_task, "safety_mon",
                                   2048,
                                   NULL,
                                   SAFETY_TASK_PRIORITY,
                                   NULL,
                                   1 /* Core 1 */);
}

#else /* host build */

int safety_monitor_start(void) {
    /* Host: task not created; test drives steps manually */
    return 0;
}

/* ── Host-only test helpers ─────────────────────────────────────────── */

void safety_monitor_host_set_reset_reason(safety_reset_reason_t r) {
    s_host_reset_reason = r;
}

void safety_monitor_host_inject_temp(float temp_K) {
    s_host_temp_K = temp_K;
}

void safety_monitor_host_inject_current(float current_A) {
    s_host_current_A = current_A;
}

void safety_monitor_host_step(void) {
    if (s_halted) return;
    do_safety_check(s_host_temp_K, s_host_current_A);
}

int safety_monitor_host_halted(void) {
    return s_halted;
}

void safety_monitor_host_reset(void) {
    s_boot_flags         = 0;
    s_runtime_flags      = 0;
    s_halt_flags         = 0;
    s_halted             = 0;
    s_temp_consec        = 0;
    s_curr_consec        = 0;
    s_host_reset_reason  = SAFETY_RESET_POWER_ON;
    s_host_temp_K        = 295.15f;
    s_host_current_A     = 0.0f;
    s_reset_reason       = SAFETY_RESET_POWER_ON;
}

#endif /* ESP_PLATFORM */
