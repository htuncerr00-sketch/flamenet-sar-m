/*
 * host_test/i2c_host_mock.c — Host-only I²C bus mock
 * =======================================================
 * Implementation. See i2c_host_mock.h for usage.
 */
#ifndef ESP_PLATFORM
#include "i2c_host_mock.h"
#include <string.h>

#define MAX_DEVS 8

static i2c_mock_device_t s_devs[MAX_DEVS];
static int s_n_devs = 0;
static int s_inject_n = 0;
static int s_inject_err = 0;
static unsigned s_n_reads = 0, s_n_writes = 0, s_n_unknown = 0;

void i2c_host_mock_init(void) {
    s_n_devs = 0;
    s_inject_n = 0;
    s_inject_err = 0;
    s_n_reads = s_n_writes = s_n_unknown = 0;
    memset(s_devs, 0, sizeof(s_devs));
}

int i2c_host_mock_register(const i2c_mock_device_t *dev) {
    if (s_n_devs >= MAX_DEVS || dev == NULL) return -1;
    s_devs[s_n_devs++] = *dev;
    return 0;
}

void i2c_host_mock_clear(void) {
    s_n_devs = 0;
    s_inject_n = 0;
    s_inject_err = 0;
}

void i2c_host_mock_inject_fault(int n_transactions, int err_code) {
    s_inject_n = n_transactions;
    s_inject_err = err_code;
}

static i2c_mock_device_t *find_dev(uint8_t addr) {
    for (int i = 0; i < s_n_devs; ++i) {
        if (s_devs[i].dev_addr == addr) return &s_devs[i];
    }
    return NULL;
}

int i2c_host_mock_read_reg(uint8_t addr, uint8_t reg,
                            uint8_t *buf, size_t n) {
    if (s_inject_n > 0) { s_inject_n--; return s_inject_err; }
    i2c_mock_device_t *d = find_dev(addr);
    if (d == NULL) { s_n_unknown++; return -1; }
    s_n_reads++;
    if (d->on_read == NULL) return -1;
    return d->on_read(reg, buf, n, d->user);
}

int i2c_host_mock_write_reg(uint8_t addr, uint8_t reg,
                             const uint8_t *buf, size_t n) {
    if (s_inject_n > 0) { s_inject_n--; return s_inject_err; }
    i2c_mock_device_t *d = find_dev(addr);
    if (d == NULL) { s_n_unknown++; return -1; }
    s_n_writes++;
    if (d->on_write == NULL) return -1;
    return d->on_write(reg, buf, n, d->user);
}

unsigned i2c_host_mock_n_reads(void)        { return s_n_reads; }
unsigned i2c_host_mock_n_writes(void)       { return s_n_writes; }
unsigned i2c_host_mock_n_unknown_addr(void) { return s_n_unknown; }

#endif /* !ESP_PLATFORM */
