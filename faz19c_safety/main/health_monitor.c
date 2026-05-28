/*
 * main/health_monitor.c — Runtime Resource Monitoring (ESP32-only)
 * =====================================================================
 * Periodically logs:
 *   - Task stack high-water marks (uxTaskGetStackHighWaterMark)
 *   - Free heap (xPortGetFreeHeapSize / heap_caps_get_free_size)
 *   - Minimum free heap since boot (heap_caps_get_minimum_free_size)
 *
 * Purpose: catches stack overflows BEFORE they crash the chip, and
 * proves there's no slow heap leak in the realtime path.
 *
 * Logging only — never fails the system. If a stack HWM falls below
 * 256 bytes, log a WARN; if free heap drops below 32 KB, log a WARN.
 *
 * Host build: no-op stub so this file compiles alongside the others.
 */
#include "health_monitor.h"

#ifdef ESP_PLATFORM

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"
#include "esp_log.h"

static const char *TAG = "health";

#define LOG_PERIOD_MS         5000
#define STACK_WARN_THRESHOLD  256       /* bytes free */
#define HEAP_WARN_THRESHOLD   (32 * 1024)

static void health_task(void *pv) {
    (void)pv;
    const TickType_t period = pdMS_TO_TICKS(LOG_PERIOD_MS);
    TickType_t last_wake = xTaskGetTickCount();
    for (;;) {
        /* Free heap totals */
        size_t free_heap     = heap_caps_get_free_size(MALLOC_CAP_DEFAULT);
        size_t min_free_heap = heap_caps_get_minimum_free_size(MALLOC_CAP_DEFAULT);
        size_t free_iram     = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);

        ESP_LOGI(TAG, "heap free=%u  min_since_boot=%u  iram_free=%u",
                 (unsigned)free_heap, (unsigned)min_free_heap, (unsigned)free_iram);
        if (free_heap < HEAP_WARN_THRESHOLD) {
            ESP_LOGW(TAG, "LOW HEAP: %u bytes free (warn < %u)",
                     (unsigned)free_heap, (unsigned)HEAP_WARN_THRESHOLD);
        }

        /* Stack HWMs for the two realtime tasks (and ourselves). */
        TaskHandle_t telem  = xTaskGetHandle("telem");
        TaskHandle_t sens   = xTaskGetHandle("sens");
        TaskHandle_t self   = xTaskGetCurrentTaskHandle();
        UBaseType_t hwm_telem  = telem ? uxTaskGetStackHighWaterMark(telem) : 0;
        UBaseType_t hwm_sens   = sens  ? uxTaskGetStackHighWaterMark(sens)  : 0;
        UBaseType_t hwm_self   = uxTaskGetStackHighWaterMark(self);

        ESP_LOGI(TAG, "stack HWM: telem=%u  sens=%u  health=%u  (bytes)",
                 (unsigned)hwm_telem, (unsigned)hwm_sens, (unsigned)hwm_self);
        if (telem && hwm_telem < STACK_WARN_THRESHOLD) {
            ESP_LOGW(TAG, "LOW STACK telem: %u bytes free", (unsigned)hwm_telem);
        }
        if (sens && hwm_sens < STACK_WARN_THRESHOLD) {
            ESP_LOGW(TAG, "LOW STACK sens:  %u bytes free", (unsigned)hwm_sens);
        }

        vTaskDelayUntil(&last_wake, period);
    }
}

BaseType_t health_monitor_start(void) {
    return xTaskCreatePinnedToCore(
        health_task, "health", 3072, NULL, 1 /* low prio */, NULL,
        tskNO_AFFINITY);
}

#else  /* host build */

int health_monitor_start(void) { return 0; }

#endif
