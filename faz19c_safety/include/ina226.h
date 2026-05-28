/*
 * include/ina226.h — Texas Instruments INA226 driver
 * =======================================================
 * High-side current/voltage monitor over I²C.
 *
 * Configuration (Faz 19B decisions):
 *   - Shunt resistor:  R_shunt = 2 mΩ
 *   - Target max current: ~40 A  (motor / ESC telemetry)
 *   - I²C address:     0x40 (A0=GND, A1=GND); change in INA226_I2C_ADDR
 *
 * Calibration math:
 *   Current_LSB = max_expected_A / 2^15
 *                = 40 A / 32768  ≈ 1.221 mA/bit
 *                Pick a round number → Current_LSB = 1.25 mA/bit
 *                (gives max ≈ 40.96 A, headroom for spikes)
 *
 *   CAL = 0.00512 / (Current_LSB × R_shunt)
 *       = 0.00512 / (0.00125 × 0.002)
 *       = 2048
 *
 *   shunt voltage LSB = 2.5 µV  (fixed by INA226)
 *   bus   voltage LSB = 1.25 mV (fixed by INA226)
 *
 * Refresh strategy:
 *   - Continuous mode: shunt + bus, 1.1 ms conversion each = ~2.2 ms total
 *   - We sample at 100 Hz from sensor_i2c_task → plenty of headroom
 */
#ifndef FW_INA226_H
#define FW_INA226_H

#include "i2c_bus.h"
#include <stdbool.h>

/* I²C device address (7-bit). A0=A1=GND → 0x40. */
#define INA226_I2C_ADDR     0x40

/* Register addresses */
#define INA226_REG_CONFIG    0x00
#define INA226_REG_SHUNT_V   0x01
#define INA226_REG_BUS_V     0x02
#define INA226_REG_POWER     0x03
#define INA226_REG_CURRENT   0x04
#define INA226_REG_CAL       0x05
#define INA226_REG_MASK_EN   0x06
#define INA226_REG_ALERT_LIM 0x07
#define INA226_REG_MFG_ID    0xFE
#define INA226_REG_DIE_ID    0xFF

/* Expected manufacturer / die IDs (sanity check on init) */
#define INA226_MFG_ID_TI      0x5449   /* "TI" ASCII */
#define INA226_DIE_ID_NOMINAL 0x2260

/* Calibration value for 2 mΩ shunt + 1.25 mA/bit LSB */
#define INA226_CALIBRATION    2048

/* Per-bit scaling factors (after calibration) */
#define INA226_CURRENT_LSB_A   0.00125f  /* 1.25 mA */
#define INA226_BUS_V_LSB_V     0.00125f  /* 1.25 mV */
#define INA226_SHUNT_V_LSB_V   0.0000025f /* 2.5 µV */

/* Driver state — keep small, no malloc, no globals leaking */
typedef struct {
    bool       initialized;
    uint16_t   mfg_id;
    uint16_t   die_id;
    /* Last successful readings (last-known-good for telemetry) */
    float      current_A;
    float      bus_voltage_V;
    /* Counters for diagnostics */
    uint32_t   n_reads_ok;
    uint32_t   n_reads_fail;
    bool       last_read_ok;
} ina226_t;

/* Probe device, verify ID, write calibration register.
   Returns I2C_OK on success. */
i2c_err_t ina226_init(ina226_t *dev);

/* Read both bus voltage and current. Updates dev->bus_voltage_V and
   dev->current_A on success. On failure, last-known-good is preserved
   and dev->last_read_ok is set false. */
i2c_err_t ina226_read(ina226_t *dev);

#endif
