/*
 * main/telemetry_task.c — 1 kHz Telemetry + 100 Hz Sensor I²C Loops
 * =====================================================================
 * Two tasks:
 *   telemetry_task     @ 1 kHz, pinned to core 1
 *     - Reads cached sensor values (no I²C from this task)
 *     - Packs wire frame
 *     - Writes to UART
 *
 *   sensor_i2c_task    @ 100 Hz, pinned to core 1
 *     - Drains INA226 / MPU6050 / NTC via I²C
 *     - Updates the sensor_pipeline cache
 *
 * Why two tasks:
 *   1 kHz I²C transactions would saturate the bus (66 KB/s bus traffic
 *   leaves no time for sensor reads). Decoupling sample rate (100 Hz)
 *   from telemetry rate (1 kHz) gives the bus headroom AND keeps the
 *   telemetry loop deterministic (no I²C latency variance).
 *
 * Priority order (producer > consumer — see TASK_SCHEDULING_AUDIT.md):
 *   sensor_i2c_task priority 12  (producer: I²C reads + cache write complete
 *                                 before telemetry task can run; no mutex
 *                                 contention possible from below)
 *   telemetry_task  priority 10  (consumer: reads stable cache snapshot)
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

/* ───────────────────────── telemetry task @ 1 kHz ─────────────────── */

void telemetry_task(void *pv) {
    (void)pv;

    const TickType_t period_ticks = pdMS_TO_TICKS(1);  /* 1 ms = 1 kHz */
    TickType_t last_wake = xTaskGetTickCount();

    ESP_LOGI(TAG, "telemetry task started, period=%u tick(s)",
             (unsigned)period_ticks);

    telem_frame_t frame;
    for (;;) {
        uint64_t now_us = esp_timer_get_time();
        sensor_pipeline_read(&frame, now_us, s_seq);
        size_t w = telem_pack(s_wire_buf, &frame);

        if (w == TELEM_WIRE_LEN) {
            uart_stream_write(s_wire_buf, w);
        }

        s_seq = (uint16_t)(s_seq + 1u);
        vTaskDelayUntil(&last_wake, period_ticks);
    }
}

/* ───────────────────────── sensor I²C task @ 100 Hz ──────────────── */

static void sensor_i2c_task(void *pv) {
    (void)pv;
    const TickType_t period = pdMS_TO_TICKS(10);   /* 10 ms = 100 Hz */
    TickType_t last_wake = xTaskGetTickCount();

    ESP_LOGI("sensor_task", "sensor I²C task started @ 100 Hz");

    for (;;) {
        sensor_pipeline_step();    /* drains INA + IMU + thermal */
        vTaskDelayUntil(&last_wake, period);
    }
}

/* ───────────────────────── start both ────────────────────────────── */

BaseType_t telemetry_task_start(void) {
    /* Sensor pipeline owns the I²C bus + sensor drivers. Init it before
       any task starts reading. */
    sensor_pipeline_init();

#define SENSOR_TASK_PRIORITY 12  /* producer: above telemetry */
#define TELEM_TASK_PRIORITY  10  /* consumer: below sensor */

    /* sensor_i2c_task: higher priority than telemetry (producer > consumer).
       Ensures I²C reads and cache write complete atomically before telemetry
       can read the cache — true priority inversion is impossible. */
    BaseType_t rc = xTaskCreatePinnedToCore(
        sensor_i2c_task, "sens", 4096, NULL, SENSOR_TASK_PRIORITY, NULL, 1);
    if (rc != pdPASS) return rc;

    /* telemetry_task: reads stable cache snapshot at 1 kHz. */
    return xTaskCreatePinnedToCore(
        telemetry_task, "telem", 4096, NULL, TELEM_TASK_PRIORITY, NULL, 1);
}

#else  /* host build — used by host_test */

void telemetry_task(void *pv) { (void)pv; }
int telemetry_task_start(void) { return 0; }

#endif
