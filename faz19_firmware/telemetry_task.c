/*
 * main/telemetry_task.c — 1 kHz Deterministic Telemetry Loop
 * =================================================================
 * Runs as a FreeRTOS task pinned to a core, scheduled with
 * vTaskDelayUntil() so jitter is bounded by the tick rate.
 *
 * Why pinned + vTaskDelayUntil:
 *   - vTaskDelay() drifts (drift accumulates with task wake jitter).
 *   - vTaskDelayUntil() schedules to ABSOLUTE wake times — drift-free.
 *   - Pinning to a core avoids cross-core scheduling latency.
 *
 * Watchdog:
 *   ESP-IDF's task watchdog is enabled by default. We feed it implicitly
 *   by yielding via vTaskDelayUntil(). If the task ever stalls > the
 *   WDT timeout (default 5 s), the chip resets — visible in PC side as
 *   a cable_unplug + reconnect (already handled by Faz 18 RealESP32Link).
 *
 * Static buffer:
 *   The 66-byte wire packet is built in a static buffer, NO malloc.
 *
 * Tick rate:
 *   ESP-IDF default tick = 100 Hz (10 ms). For 1 kHz telemetry we need
 *   either: (a) raise CONFIG_FREERTOS_HZ to 1000, OR (b) emit multiple
 *   frames per tick. We pick (a) via sdkconfig (more deterministic).
 *
 *   FREERTOS_HZ=1000 → vTaskDelayUntil(1 tick) = 1 ms precise.
 */
#include "telemetry_task.h"
#include "telemetry_frame.h"
#include "sensor_pipeline.h"
#include "uart_stream.h"

#ifdef ESP_PLATFORM
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#endif

#include <stdint.h>

#ifndef ESP_PLATFORM
/* Host build stubs so this compiles for unit tests */
typedef void * TaskHandle_t;
typedef unsigned int TickType_t;
#define portTICK_PERIOD_MS 1
#define configMAX_PRIORITIES 25
#define tskNO_AFFINITY -1
static inline TickType_t xTaskGetTickCount(void) { return 0; }
static inline void vTaskDelayUntil(TickType_t *p, TickType_t inc)
    { (void)p; (void)inc; }
static inline uint64_t esp_timer_get_time(void) { return 0; }
#define ESP_LOGI(tag, fmt, ...)  ((void)0)
#endif

#ifdef ESP_PLATFORM

static const char *TAG = "telem_task";

/* Static frame buffer — survives the lifetime of the task */
static uint8_t s_wire_buf[TELEM_WIRE_LEN];
static uint16_t s_seq = 0;

void telemetry_task(void *pv) {
    (void)pv;
    sensor_pipeline_init();

    const TickType_t period_ticks = pdMS_TO_TICKS(1);  /* 1 ms = 1 kHz */
    TickType_t last_wake = xTaskGetTickCount();

    ESP_LOGI(TAG, "telemetry task started, period=%u tick(s)",
             (unsigned)period_ticks);

    telem_frame_t frame;
    for (;;) {
        uint64_t now_us = esp_timer_get_time();
        sensor_pipeline_read(&frame, now_us, s_seq);
        size_t w = telem_pack(s_wire_buf, &frame);

        /* Non-blocking write — UART driver handles the ring buffer. */
        if (w == TELEM_WIRE_LEN) {
            uart_stream_write(s_wire_buf, w);
        }

        s_seq = (uint16_t)(s_seq + 1u);

        /* Drift-free 1 kHz */
        vTaskDelayUntil(&last_wake, period_ticks);
    }
}

BaseType_t telemetry_task_start(void) {
    /* Stack: 4 KB is plenty — no recursion, no big locals. Priority
       above the default task priority but below absolute-critical
       interrupts. Pin to core 1 to keep core 0 free for WiFi/BLE etc. */
    return xTaskCreatePinnedToCore(
        telemetry_task,
        "telem",
        4096,
        NULL,
        10,            /* priority */
        NULL,
        1              /* core */
    );
}

#else  /* host build — used by host_test */

void telemetry_task(void *pv) { (void)pv; }
int telemetry_task_start(void) { return 0; }

#endif
