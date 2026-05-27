/*
 * include/uart_stream.h — UART streaming API
 */
#ifndef FW_UART_STREAM_H
#define FW_UART_STREAM_H

#include <stddef.h>
#include <stdint.h>

#ifdef ESP_PLATFORM
#include "esp_err.h"
typedef esp_err_t uart_err_t;
#else
typedef int uart_err_t;
#endif

/* Initialize UART, set pins, install driver with TX ring buffer. */
uart_err_t uart_stream_init(void);

/* Write bytes to UART. Returns number queued, or negative on error.
   Non-blocking unless TX ring is full. */
int uart_stream_write(const uint8_t *buf, size_t len);

#endif
