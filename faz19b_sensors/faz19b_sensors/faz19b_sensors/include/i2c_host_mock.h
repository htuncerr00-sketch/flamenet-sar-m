/*
 * include/i2c_host_mock.h — Host-only I²C bus mock
 * =====================================================
 * Provides a stand-in I²C bus for host-side sensor driver tests.
 * Each registered device is a function pointer pair that handles
 * read/write of one register. Driver tests register their sensor's
 * register-map handler; the production driver code then runs
 * unchanged on the host because all bus calls land here.
 *
 * NOT compiled into the ESP32 firmware (gated by !ESP_PLATFORM).
 */
#ifndef FW_I2C_HOST_MOCK_H
#define FW_I2C_HOST_MOCK_H

#ifndef ESP_PLATFORM

#include <stdint.h>
#include <stddef.h>

/* Per-device handlers. Return 0 on OK, nonzero to simulate I²C error. */
typedef int (*i2c_mock_read_fn)(uint8_t reg, uint8_t *buf, size_t n,
                                 void *user);
typedef int (*i2c_mock_write_fn)(uint8_t reg, const uint8_t *buf,
                                  size_t n, void *user);

typedef struct {
    uint8_t            dev_addr;
    i2c_mock_read_fn   on_read;
    i2c_mock_write_fn  on_write;
    void              *user;
} i2c_mock_device_t;

/* Initialize / reset the mock bus. */
void i2c_host_mock_init(void);

/* Register a virtual device. Returns 0 on success, -1 if full. */
int  i2c_host_mock_register(const i2c_mock_device_t *dev);

/* Unregister all devices (for between-test cleanup). */
void i2c_host_mock_clear(void);

/* Inject a one-shot fault: next N transactions return this error code,
   for any device. Use to simulate cable jiggle / disconnect. */
void i2c_host_mock_inject_fault(int n_transactions, int err_code);

/* The functions called by i2c_bus.c when ESP_PLATFORM is undefined. */
int  i2c_host_mock_read_reg (uint8_t dev_addr, uint8_t reg,
                             uint8_t *buf, size_t n);
int  i2c_host_mock_write_reg(uint8_t dev_addr, uint8_t reg,
                             const uint8_t *buf, size_t n);

/* Stats (for tests) */
unsigned i2c_host_mock_n_reads(void);
unsigned i2c_host_mock_n_writes(void);
unsigned i2c_host_mock_n_unknown_addr(void);

#endif /* !ESP_PLATFORM */
#endif /* FW_I2C_HOST_MOCK_H */
