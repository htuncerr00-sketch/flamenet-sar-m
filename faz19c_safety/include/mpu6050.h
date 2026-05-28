/*
 * include/mpu6050.h — InvenSense MPU6050 IMU driver
 * ======================================================
 * Reads accelerometer + gyroscope over I²C.
 *
 * Used in Faz 19B for vibration RMS computation (mapped to vib_x/y/z
 * on the wire). Gyro readings are computed but not currently sent
 * over the wire — they're available for future safety/control use.
 *
 * Address:
 *   AD0=GND → 0x68 (default)
 *   AD0=VCC → 0x69
 *
 * Default config:
 *   - Accel range:  ±2 g       (16384 LSB/g)
 *   - Gyro range:   ±250 °/s   (131 LSB/°/s)
 *   - DLPF:         44 Hz (CONFIG = 3)
 *   - Sample rate:  1 kHz / (1 + SMPLRT_DIV) → with SMPLRT_DIV=0, 1 kHz
 *   - Power mode:   normal, internal 8 MHz oscillator → switch to PLL on gyro X
 */
#ifndef FW_MPU6050_H
#define FW_MPU6050_H

#include "i2c_bus.h"
#include <stdbool.h>

#define MPU6050_I2C_ADDR    0x68

/* Register map (subset we use) */
#define MPU6050_REG_SMPLRT_DIV   0x19
#define MPU6050_REG_CONFIG       0x1A
#define MPU6050_REG_GYRO_CONFIG  0x1B
#define MPU6050_REG_ACCEL_CONFIG 0x1C
#define MPU6050_REG_ACCEL_XOUT_H 0x3B
#define MPU6050_REG_TEMP_OUT_H   0x41
#define MPU6050_REG_GYRO_XOUT_H  0x43
#define MPU6050_REG_PWR_MGMT_1   0x6B
#define MPU6050_REG_WHO_AM_I     0x75

/* WHO_AM_I value (datasheet: returns I²C address with bit 0 cleared) */
#define MPU6050_WHO_AM_I_VAL     0x68

/* Scale factors after config */
#define MPU6050_ACCEL_LSB_PER_G  16384.0f   /* ±2g range */
#define MPU6050_GYRO_LSB_PER_DPS 131.0f     /* ±250 °/s range */
#define MPU6050_TEMP_SCALE       340.0f     /* (raw / 340) + 36.53 = °C */
#define MPU6050_TEMP_OFFSET      36.53f

typedef struct {
    bool       initialized;
    uint8_t    who_am_i;
    /* Last-known-good readings in physical units */
    float      accel_g[3];      /* g */
    float      gyro_dps[3];     /* deg/sec */
    float      temp_C;          /* die temp */
    /* Diagnostics */
    uint32_t   n_reads_ok;
    uint32_t   n_reads_fail;
    bool       last_read_ok;
} mpu6050_t;

i2c_err_t mpu6050_init(mpu6050_t *dev);

/* Read all 7 channels (accel ×3, temp, gyro ×3) in one bus transaction.
   Faster than 7 separate reads. */
i2c_err_t mpu6050_read(mpu6050_t *dev);

#endif
