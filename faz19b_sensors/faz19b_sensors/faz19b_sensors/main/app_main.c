/*
 * main/app_main.c — Firmware Entry Point
 * ==========================================
 * Faz 19A scope (per spec):
 *   ✓ ESP-IDF proje skeleton
 *   ✓ UART streaming
 *   ✓ Gerçek 0xAA55 packets
 *   ✓ CRC verified
 *   ✓ Synthetic telemetry generation
 *   ✓ 1 kHz stable stream
 *   ✓ PC bağlantısı doğrulama
 *
 * Real sensors come in Faz 19B (INA226, IMU, thermistors).
 * Safety + brownout + watchdog reasons come in Faz 19C.
 */
#include "telemetry_task.h"
#include "uart_stream.h"
#include "health_monitor.h"

#ifdef ESP_PLATFORM
#include "esp_log.h"
#endif

#ifdef ESP_PLATFORM

static const char *TAG = "app_main";

void app_main(void) {
    ESP_LOGI(TAG, "Filament Winding Telemetry Firmware — Faz 19B");
    ESP_LOGI(TAG, "Wire format: 0xAA 0x55 + 64B big-endian payload + CRC-16");

    /* UART first — if this fails there's nothing to log to */
    if (uart_stream_init() != 0) {
        ESP_LOGE(TAG, "UART init failed; halting");
        return;
    }

    /* Start the telemetry + sensor tasks (sensor_pipeline_init runs inside) */
    BaseType_t rc = telemetry_task_start();
    if (rc != pdPASS) {
        ESP_LOGE(TAG, "telemetry_task_start failed: %d", (int)rc);
        return;
    }
    ESP_LOGI(TAG, "Telemetry streaming @ 1 kHz; sensors @ 100 Hz");

    /* Low-priority health monitor (stack/heap watcher) */
    health_monitor_start();

    /* app_main returns; FreeRTOS keeps tasks running */
}

#else

int main(void) { return 0; }   /* host-build no-op */

#endif
