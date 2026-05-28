/*
 * main/app_main.c — Firmware Entry Point (Faz 19C)
 * ==================================================
 * Startup sequence:
 *   1. safety_monitor_init()  — classify reset reason, set boot flags
 *   2. uart_stream_init()     — UART must be up before logging
 *   3. telemetry_task_start() — spawns sensor_i2c_task + telemetry_task
 *   4. safety_monitor_start() — 100 ms runtime watchdog task
 *   5. health_monitor_start() — stack/heap diagnostics (lowest priority)
 *
 * Safety boot flags are logged via UART before telemetry starts, giving
 * the PC the brownout/WDT reason in the first telemetry frame.
 */
#include "telemetry_task.h"
#include "uart_stream.h"
#include "health_monitor.h"
#include "safety_monitor.h"
#include "can_bridge.h"

#ifdef ESP_PLATFORM
#include "esp_log.h"
static const char *TAG = "app_main";

void app_main(void) {
    /* Step 1: classify reset reason before anything else */
    safety_monitor_init();

    ESP_LOGI(TAG, "Filament Winding Telemetry Firmware — Faz 19C");
    ESP_LOGI(TAG, "Reset reason: %s",
             safety_monitor_reset_reason_str(safety_monitor_get_reset_reason()));

    /* Step 2: UART — if this fails there is nothing to log to */
    if (uart_stream_init() != 0) {
        ESP_LOGE(TAG, "UART init failed; halting");
        return;
    }

    /* Step 2.5: CAN/TWAI ESC bridge (Faz 19D) */
    can_bridge_config_t can_cfg = CAN_BRIDGE_CONFIG_DEFAULT();
    if (can_bridge_init(&can_cfg) != 0) {
        ESP_LOGW(TAG, "can_bridge_init failed — ESC bridge offline");
    } else {
        can_bridge_start();
        ESP_LOGI(TAG, "CAN bridge started: TX=GPIO%d RX=GPIO%d @ %lu bps",
                 can_cfg.tx_gpio, can_cfg.rx_gpio,
                 (unsigned long)can_cfg.bitrate);
    }

    /* Step 3: telemetry + sensor tasks */
    BaseType_t rc = telemetry_task_start();
    if (rc != pdPASS) {
        ESP_LOGE(TAG, "telemetry_task_start failed: %d", (int)rc);
        return;
    }
    ESP_LOGI(TAG, "Telemetry streaming @ 1 kHz; sensors @ 100 Hz");

    /* Step 4: safety monitor task (100 ms loop, never blocks) */
    rc = safety_monitor_start();
    if (rc != pdPASS) {
        ESP_LOGW(TAG, "safety_monitor_start failed — running without runtime safety");
    }

    /* Step 5: low-priority health monitor (stack/heap watcher) */
    health_monitor_start();

    /* app_main returns; FreeRTOS keeps tasks running */
}

#else

int main(void) { return 0; }   /* host-build no-op */

#endif
