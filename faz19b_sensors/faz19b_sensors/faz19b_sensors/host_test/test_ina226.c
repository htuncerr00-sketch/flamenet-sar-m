/*
 * host_test/test_ina226.c — INA226 driver unit test
 * ======================================================
 * Registers a mock INA226 device, sets known register values, then
 * runs ina226_init() + ina226_read() and verifies the converted
 * physical values match expectations.
 *
 * Build:
 *   gcc -O2 -Wall -std=c11 -I../include -I. \
 *       test_ina226.c i2c_host_mock.c \
 *       ../main/i2c_bus.c ../main/ina226.c \
 *       -o test_ina226 -lpthread -lm
 */
#define _POSIX_C_SOURCE 200809L
#include "ina226.h"
#include "i2c_bus.h"
#include "i2c_host_mock.h"
#include <stdio.h>
#include <string.h>
#include <math.h>
#include <assert.h>

/* ── virtual INA226 register file ── */
typedef struct {
    uint16_t cfg;
    uint16_t shunt_v;
    uint16_t bus_v;
    uint16_t power;
    int16_t  current;       /* signed */
    uint16_t cal;
} ina226_regs_t;

static int mock_read(uint8_t reg, uint8_t *buf, size_t n, void *user) {
    ina226_regs_t *r = (ina226_regs_t *)user;
    if (n != 2) return -1;   /* INA226 registers are always 16-bit */
    uint16_t v = 0;
    switch (reg) {
        case INA226_REG_CONFIG:  v = r->cfg; break;
        case INA226_REG_SHUNT_V: v = r->shunt_v; break;
        case INA226_REG_BUS_V:   v = r->bus_v; break;
        case INA226_REG_POWER:   v = r->power; break;
        case INA226_REG_CURRENT: v = (uint16_t)r->current; break;
        case INA226_REG_CAL:     v = r->cal; break;
        case INA226_REG_MFG_ID:  v = INA226_MFG_ID_TI; break;
        case INA226_REG_DIE_ID:  v = INA226_DIE_ID_NOMINAL; break;
        default: return -1;
    }
    buf[0] = (uint8_t)(v >> 8);
    buf[1] = (uint8_t)(v & 0xFF);
    return 0;
}

static int mock_write(uint8_t reg, const uint8_t *buf, size_t n, void *user) {
    ina226_regs_t *r = (ina226_regs_t *)user;
    if (n != 2) return -1;
    uint16_t v = ((uint16_t)buf[0] << 8) | buf[1];
    switch (reg) {
        case INA226_REG_CONFIG: r->cfg = v; break;
        case INA226_REG_CAL:    r->cal = v; break;
        case INA226_REG_MASK_EN:
        case INA226_REG_ALERT_LIM: /* ignored */ break;
        default: return -1;
    }
    return 0;
}

static int n_pass = 0;
static int n_fail = 0;

static void check_eq_f(const char *name, float got, float expected, float tol) {
    int ok = fabsf(got - expected) <= tol;
    printf("    [%s] %-30s got=%.4f expected=%.4f tol=%.4f\n",
           ok ? "PASS" : "FAIL", name, got, expected, tol);
    if (ok) n_pass++; else n_fail++;
}

static void check_eq_u(const char *name, unsigned got, unsigned expected) {
    int ok = (got == expected);
    printf("    [%s] %-30s got=%u expected=%u\n",
           ok ? "PASS" : "FAIL", name, got, expected);
    if (ok) n_pass++; else n_fail++;
}

static void check_ok(const char *name, int ok) {
    printf("    [%s] %-30s\n", ok ? "PASS" : "FAIL", name);
    if (ok) n_pass++; else n_fail++;
}

int main(void) {
    printf("\n=== INA226 driver unit test ===\n");

    /* Set up mock device */
    ina226_regs_t regs = {
        .cfg = 0x4127, .shunt_v = 0, .bus_v = 0,
        .power = 0, .current = 0, .cal = 0,
    };
    i2c_host_mock_init();
    i2c_mock_device_t md = {
        .dev_addr = INA226_I2C_ADDR,
        .on_read = mock_read, .on_write = mock_write,
        .user = &regs,
    };
    int r = i2c_host_mock_register(&md);
    assert(r == 0);

    /* Init bus + driver */
    i2c_err_t e = i2c_bus_init();
    check_ok("i2c_bus_init", e == I2C_OK);

    ina226_t dev = {0};
    e = ina226_init(&dev);
    check_ok("ina226_init", e == I2C_OK);
    check_eq_u("mfg_id read", dev.mfg_id, INA226_MFG_ID_TI);
    check_ok ("initialized flag", dev.initialized);
    check_eq_u("CAL register written", regs.cal, INA226_CALIBRATION);
    check_eq_u("CFG register written", regs.cfg, 0x4127);

    /* ── Reading 1: known forward current + bus voltage ── */
    /* 12 V bus = 12 / 1.25e-3 = 9600 raw */
    regs.bus_v = 9600;
    /* 5 A current = 5 / 1.25e-3 = 4000 raw, positive */
    regs.current = 4000;

    e = ina226_read(&dev);
    check_ok ("read OK",         e == I2C_OK);
    check_ok ("last_read_ok",    dev.last_read_ok);
    check_eq_f("bus voltage @12V", dev.bus_voltage_V, 12.0f, 0.001f);
    check_eq_f("current @+5A",     dev.current_A,    5.0f, 0.001f);
    check_eq_u("n_reads_ok",       dev.n_reads_ok,   1u);

    /* ── Reading 2: high current ── */
    regs.current = 32000;   /* +40 A */
    regs.bus_v   = 19200;   /* 24 V */
    e = ina226_read(&dev);
    check_ok  ("read at high current", e == I2C_OK);
    check_eq_f("current @+40A",        dev.current_A,    40.0f, 0.02f);
    check_eq_f("bus voltage @24V",     dev.bus_voltage_V, 24.0f, 0.001f);

    /* ── Reading 3: negative current (regen/braking) ── */
    regs.current = (int16_t)-8000;   /* -10 A */
    e = ina226_read(&dev);
    check_ok  ("read at neg current", e == I2C_OK);
    check_eq_f("current @-10A",       dev.current_A, -10.0f, 0.001f);

    /* ── Reading 4: I²C fault → last-known-good preserved ── */
    float lkg_curr = dev.current_A;
    float lkg_volt = dev.bus_voltage_V;
    uint32_t fail_before = dev.n_reads_fail;
    i2c_host_mock_inject_fault(1, -7);   /* fail next 1 transaction */
    e = ina226_read(&dev);
    check_ok  ("fault detected",      e != I2C_OK);
    check_ok  ("last_read_ok = false", dev.last_read_ok == false);
    check_eq_f("lkg current preserved", dev.current_A, lkg_curr, 0.0001f);
    check_eq_f("lkg voltage preserved", dev.bus_voltage_V, lkg_volt, 0.0001f);
    check_eq_u("n_reads_fail incremented", dev.n_reads_fail, fail_before + 1);

    /* ── Reading 5: bus recovers ── */
    regs.current = 4000;
    e = ina226_read(&dev);
    check_ok  ("recovery read OK",     e == I2C_OK);
    check_ok  ("last_read_ok = true",  dev.last_read_ok);
    check_eq_f("recovered current",    dev.current_A, 5.0f, 0.001f);

    printf("\n=== INA226: %d pass, %d fail ===\n", n_pass, n_fail);
    return n_fail == 0 ? 0 : 1;
}
