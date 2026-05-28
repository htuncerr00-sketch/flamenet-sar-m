/*
 * include/i2c_bus.h — Shared I²C Bus Manager
 * ==============================================
 * Single I²C0 bus on GPIO 21 (SDA) / 22 (SCL).
 * Mutex-protected so multiple sensor tasks can share.
 * Timeout-recoverable: a stuck sensor can't deadlock the others.
 */
#ifndef FW_I2C_BUS_H
#define FW_I2C_BUS_H

#include <stdint.h>
#include <stddef.h>

#ifdef ESP_PLATFORM
#include "esp_err.h"
typedef esp_err_t i2c_err_t;
#define I2C_OK ESP_OK
#else
typedef int i2c_err_t;
#define I2C_OK 0
#endif

#define I2C_BUS_SDA_GPIO    21
#define I2C_BUS_SCL_GPIO    22
#define I2C_BUS_FREQ_HZ     400000     /* 400 kHz fast-mode */
#define I2C_BUS_TIMEOUT_MS  20         /* per-transaction watchdog */

/* Initialize the bus once at boot. Idempotent. */
i2c_err_t i2c_bus_init(void);

/* Read N bytes from a register. Returns I2C_OK on success.
   Internally uses a write(reg_addr) → restart → read(N) sequence. */
i2c_err_t i2c_bus_read_reg(uint8_t dev_addr, uint8_t reg_addr,
                           uint8_t *buf, size_t n);

/* Write N bytes to a register. */
i2c_err_t i2c_bus_write_reg(uint8_t dev_addr, uint8_t reg_addr,
                            const uint8_t *buf, size_t n);

/* Convenience: read/write a single 16-bit big-endian register
   (most I²C sensors expose this layout). */
i2c_err_t i2c_bus_read_reg16_be(uint8_t dev_addr, uint8_t reg_addr,
                                uint16_t *out);
i2c_err_t i2c_bus_write_reg16_be(uint8_t dev_addr, uint8_t reg_addr,
                                 uint16_t value);

/* Diagnostics for telemetry / commissioning panel */
uint32_t i2c_bus_n_transactions_ok(void);
uint32_t i2c_bus_n_timeouts(void);
uint32_t i2c_bus_n_errors(void);

#endif
