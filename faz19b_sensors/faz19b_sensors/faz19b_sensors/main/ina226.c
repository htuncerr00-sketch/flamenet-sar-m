/*
 * main/ina226.c — INA226 Implementation
 * ==========================================
 * Reads:
 *   - Bus voltage   (0..36 V, 1.25 mV/bit, unsigned)
 *   - Current       (signed 16-bit, calibrated via CAL register)
 *
 * The current register's bit 15 is the sign bit when calibration is
 * configured for bidirectional current (motor regen / braking). We
 * interpret as signed int16, then multiply by Current_LSB.
 */
#include "ina226.h"
#include <stddef.h>

/*
 * Default config (register 0x00):
 *   AVG = 4 samples (bits 11..9 = 010)
 *   VBUSCT = 1.1 ms (bits 8..6 = 100)
 *   VSHCT  = 1.1 ms (bits 5..3 = 100)
 *   MODE   = shunt + bus, continuous (bits 2..0 = 111)
 *   Reserved bits high nibble must be set per datasheet → 0x4000
 *
 *   binary: 0100 0001 0010 0111  = 0x4127
 */
#define INA226_CFG_DEFAULT  0x4127

i2c_err_t ina226_init(ina226_t *dev) {
    if (dev == NULL) return (i2c_err_t)-1;
    dev->initialized = false;
    dev->last_read_ok = false;
    dev->n_reads_ok = 0;
    dev->n_reads_fail = 0;
    dev->current_A = 0.0f;
    dev->bus_voltage_V = 0.0f;

    /* 1. Read mfg ID for presence check */
    i2c_err_t err = i2c_bus_read_reg16_be(
        INA226_I2C_ADDR, INA226_REG_MFG_ID, &dev->mfg_id);
    if (err != I2C_OK) return err;
    if (dev->mfg_id != INA226_MFG_ID_TI) {
        /* Not a TI INA226. Refuse to init. */
        return (i2c_err_t)-2;
    }

    /* 2. Read die ID (informational) */
    err = i2c_bus_read_reg16_be(
        INA226_I2C_ADDR, INA226_REG_DIE_ID, &dev->die_id);
    /* die ID mismatch is non-fatal; some marking variants exist */

    /* 3. Write configuration */
    err = i2c_bus_write_reg16_be(
        INA226_I2C_ADDR, INA226_REG_CONFIG, INA226_CFG_DEFAULT);
    if (err != I2C_OK) return err;

    /* 4. Write calibration */
    err = i2c_bus_write_reg16_be(
        INA226_I2C_ADDR, INA226_REG_CAL, INA226_CALIBRATION);
    if (err != I2C_OK) return err;

    dev->initialized = true;
    return I2C_OK;
}

i2c_err_t ina226_read(ina226_t *dev) {
    if (dev == NULL || !dev->initialized) return (i2c_err_t)-1;

    uint16_t raw_bus = 0, raw_curr = 0;
    i2c_err_t e1 = i2c_bus_read_reg16_be(
        INA226_I2C_ADDR, INA226_REG_BUS_V, &raw_bus);
    i2c_err_t e2 = i2c_bus_read_reg16_be(
        INA226_I2C_ADDR, INA226_REG_CURRENT, &raw_curr);

    if (e1 != I2C_OK || e2 != I2C_OK) {
        dev->last_read_ok = false;
        dev->n_reads_fail++;
        /* DO NOT overwrite cached values — preserve last-known-good */
        return (e1 != I2C_OK) ? e1 : e2;
    }

    /* Bus voltage is unsigned 16-bit, 1.25 mV/bit */
    dev->bus_voltage_V = (float)raw_bus * INA226_BUS_V_LSB_V;

    /* Current register is signed (two's complement) — cast through int16 */
    int16_t signed_curr = (int16_t)raw_curr;
    dev->current_A = (float)signed_curr * INA226_CURRENT_LSB_A;

    dev->last_read_ok = true;
    dev->n_reads_ok++;
    return I2C_OK;
}
