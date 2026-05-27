/*
 * main/mpu6050.c — MPU6050 Driver Implementation
 * ===================================================
 * One-burst read of 14 bytes covers all 7 channels:
 *   0x3B..0x40  accel X,Y,Z   (6 bytes, big-endian signed)
 *   0x41..0x42  temp          (2 bytes)
 *   0x43..0x48  gyro  X,Y,Z   (6 bytes)
 *
 * Total: 14 bytes per sample → at 400 kHz I²C ≈ 0.4 ms/sample
 *   Plenty of headroom for 100 Hz sampling in sensor_i2c_task.
 */
#include "mpu6050.h"
#include <stddef.h>

static inline int16_t s16_from_be(const uint8_t *p) {
    return (int16_t)((p[0] << 8) | p[1]);
}

i2c_err_t mpu6050_init(mpu6050_t *dev) {
    if (dev == NULL) return (i2c_err_t)-1;
    dev->initialized = false;
    dev->last_read_ok = false;
    dev->n_reads_ok = 0;
    dev->n_reads_fail = 0;
    for (int i = 0; i < 3; ++i) {
        dev->accel_g[i] = 0.0f;
        dev->gyro_dps[i] = 0.0f;
    }
    dev->temp_C = 0.0f;

    /* 1. WHO_AM_I check */
    uint8_t who = 0;
    i2c_err_t err = i2c_bus_read_reg(
        MPU6050_I2C_ADDR, MPU6050_REG_WHO_AM_I, &who, 1);
    if (err != I2C_OK) return err;
    dev->who_am_i = who;
    if (who != MPU6050_WHO_AM_I_VAL) return (i2c_err_t)-2;

    /* 2. Power management: wake device, set clock to PLL with X gyro ref */
    uint8_t pwr = 0x01;
    err = i2c_bus_write_reg(MPU6050_I2C_ADDR, MPU6050_REG_PWR_MGMT_1, &pwr, 1);
    if (err != I2C_OK) return err;

    /* 3. Sample rate divider: 1 kHz / (1 + 0) = 1 kHz */
    uint8_t div = 0x00;
    err = i2c_bus_write_reg(MPU6050_I2C_ADDR, MPU6050_REG_SMPLRT_DIV, &div, 1);
    if (err != I2C_OK) return err;

    /* 4. DLPF = 44 Hz, fSync disabled */
    uint8_t cfg = 0x03;
    err = i2c_bus_write_reg(MPU6050_I2C_ADDR, MPU6050_REG_CONFIG, &cfg, 1);
    if (err != I2C_OK) return err;

    /* 5. Gyro range ±250 °/s (bits 4:3 = 00) */
    uint8_t gcfg = 0x00;
    err = i2c_bus_write_reg(MPU6050_I2C_ADDR, MPU6050_REG_GYRO_CONFIG, &gcfg, 1);
    if (err != I2C_OK) return err;

    /* 6. Accel range ±2 g (bits 4:3 = 00) */
    uint8_t acfg = 0x00;
    err = i2c_bus_write_reg(MPU6050_I2C_ADDR, MPU6050_REG_ACCEL_CONFIG, &acfg, 1);
    if (err != I2C_OK) return err;

    dev->initialized = true;
    return I2C_OK;
}

i2c_err_t mpu6050_read(mpu6050_t *dev) {
    if (dev == NULL || !dev->initialized) return (i2c_err_t)-1;

    /* Burst read 14 bytes starting at ACCEL_XOUT_H */
    uint8_t b[14] = {0};
    i2c_err_t err = i2c_bus_read_reg(
        MPU6050_I2C_ADDR, MPU6050_REG_ACCEL_XOUT_H, b, sizeof(b));
    if (err != I2C_OK) {
        dev->last_read_ok = false;
        dev->n_reads_fail++;
        return err;
    }

    /* Decode: each channel = signed 16-bit big-endian */
    int16_t ax = s16_from_be(b +  0);
    int16_t ay = s16_from_be(b +  2);
    int16_t az = s16_from_be(b +  4);
    int16_t tr = s16_from_be(b +  6);
    int16_t gx = s16_from_be(b +  8);
    int16_t gy = s16_from_be(b + 10);
    int16_t gz = s16_from_be(b + 12);

    dev->accel_g[0] = (float)ax / MPU6050_ACCEL_LSB_PER_G;
    dev->accel_g[1] = (float)ay / MPU6050_ACCEL_LSB_PER_G;
    dev->accel_g[2] = (float)az / MPU6050_ACCEL_LSB_PER_G;
    dev->gyro_dps[0] = (float)gx / MPU6050_GYRO_LSB_PER_DPS;
    dev->gyro_dps[1] = (float)gy / MPU6050_GYRO_LSB_PER_DPS;
    dev->gyro_dps[2] = (float)gz / MPU6050_GYRO_LSB_PER_DPS;
    dev->temp_C = (float)tr / MPU6050_TEMP_SCALE + MPU6050_TEMP_OFFSET;

    dev->last_read_ok = true;
    dev->n_reads_ok++;
    return I2C_OK;
}
