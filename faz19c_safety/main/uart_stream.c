/*
 * main/uart_stream.c — UART TX streaming for telemetry
 * ============================================================
 * Initializes UART1 at 921600 baud with a TX ring buffer. Telemetry
 * task writes 66 bytes per frame at 1 kHz into the ring; the UART
 * driver DMAs them out without blocking the task.
 *
 * Why UART1 + GPIO 17/16:
 *   UART0 is normally USB-CDC / boot console on dev boards. We want
 *   to keep the console available for debugging. UART1 is free.
 *   Pins 17 (TX) / 16 (RX) match the spec in the Faz 19 brief.
 *
 *   For boards where the ESP32 is connected via a USB-to-serial chip
 *   (e.g. CP210x on DevKitC), simply wire that chip to UART1's pins
 *   instead of UART0. Or, if using ESP32-S3 native USB CDC, change
 *   UART_NUM to USB_SERIAL_JTAG and remove uart_set_pin().
 *
 * Ring buffer sizing:
 *   1 kHz × 66 bytes = 66 KB/s. We give the driver a 4 KB TX ring
 *   which holds ~60 frames in flight. That's >50 ms of buffering at
 *   line rate — far more than the 1 ms scheduler jitter we expect.
 */
#include "uart_stream.h"

#ifdef ESP_PLATFORM
#include "driver/uart.h"
#include "driver/gpio.h"
#include "esp_log.h"
#endif

#include <string.h>

#ifndef UART_NUM
#define UART_NUM        1     /* UART1 */
#endif
#ifndef UART_BAUD_RATE
#define UART_BAUD_RATE  921600
#endif
#ifndef UART_TX_PIN
#define UART_TX_PIN     17
#endif
#ifndef UART_RX_PIN
#define UART_RX_PIN     16
#endif

#define UART_TX_BUF_SIZE    4096    /* TX ring; 0 = blocking */
#define UART_RX_BUF_SIZE    1024    /* small; we mostly TX */

#ifdef ESP_PLATFORM

static const char *TAG = "uart_stream";

esp_err_t uart_stream_init(void) {
    uart_config_t cfg = {
        .baud_rate  = UART_BAUD_RATE,
        .data_bits  = UART_DATA_8_BITS,
        .parity     = UART_PARITY_DISABLE,
        .stop_bits  = UART_STOP_BITS_1,
        .flow_ctrl  = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };

    esp_err_t err;
    err = uart_driver_install(UART_NUM,
                              UART_RX_BUF_SIZE,
                              UART_TX_BUF_SIZE,
                              0, NULL, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "uart_driver_install: %d", err);
        return err;
    }
    err = uart_param_config(UART_NUM, &cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "uart_param_config: %d", err);
        return err;
    }
    err = uart_set_pin(UART_NUM,
                       UART_TX_PIN, UART_RX_PIN,
                       UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "uart_set_pin: %d", err);
        return err;
    }
    ESP_LOGI(TAG, "UART%d @ %d baud, TX=GPIO%d, RX=GPIO%d, ringbuf=%d",
             UART_NUM, UART_BAUD_RATE, UART_TX_PIN, UART_RX_PIN,
             UART_TX_BUF_SIZE);
    return ESP_OK;
}

int uart_stream_write(const uint8_t *buf, size_t len) {
    /*
     * uart_write_bytes is non-blocking when the TX ring has room.
     * Returns number of bytes queued. If the ring is full it WILL
     * block — but at 921600 baud the ring drains at ~92 KB/s, and
     * we put in 66 KB/s, so the ring stays at ~70% full steady-state
     * with no blocking.
     */
    int n = uart_write_bytes(UART_NUM, (const char *)buf, len);
    return n;
}

#else /* !ESP_PLATFORM — host build, no-op stubs */

int uart_stream_init(void) { return 0; }
int uart_stream_write(const uint8_t *buf, size_t len) {
    (void)buf; (void)len;
    return (int)len;
}

#endif
