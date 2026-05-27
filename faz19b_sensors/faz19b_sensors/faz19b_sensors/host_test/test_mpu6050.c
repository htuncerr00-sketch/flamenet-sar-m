/*
 * host_test/test_mpu6050.c — MPU6050 driver unit test
 */
#define _POSIX_C_SOURCE 200809L
#include "mpu6050.h"
#include "i2c_bus.h"
#include "i2c_host_mock.h"
#include <stdio.h>
#include <string.h>
#include <math.h>
#include <stdint.h>

/* Mock state — accel and gyro in raw int16, temp register raw */
typedef struct {
    int16_t  accel_raw[3];
    int16_t  gyro_raw[3];
    int16_t  temp_raw;
    uint8_t  pwr_mgmt, smplrt_div, config, gyro_cfg, accel_cfg;
} mpu_mock_t;

static int mock_read(uint8_t reg, uint8_t *buf, size_t n, void *user) {
    mpu_mock_t *m = (mpu_mock_t *)user;

    if (reg == MPU6050_REG_WHO_AM_I && n == 1) {
        buf[0] = MPU6050_WHO_AM_I_VAL;
        return 0;
    }
    if (reg == MPU6050_REG_ACCEL_XOUT_H && n == 14) {
        int16_t vals[7] = {
            m->accel_raw[0], m->accel_raw[1], m->accel_raw[2],
            m->temp_raw,
            m->gyro_raw[0], m->gyro_raw[1], m->gyro_raw[2],
        };
        for (int i = 0; i < 7; ++i) {
            buf[i * 2]     = (uint8_t)(((uint16_t)vals[i]) >> 8);
            buf[i * 2 + 1] = (uint8_t)(((uint16_t)vals[i]) & 0xFF);
        }
        return 0;
    }
    return -1;
}

static int mock_write(uint8_t reg, const uint8_t *buf, size_t n, void *user) {
    mpu_mock_t *m = (mpu_mock_t *)user;
    if (n != 1) return -1;
    switch (reg) {
        case MPU6050_REG_PWR_MGMT_1:  m->pwr_mgmt = buf[0]; return 0;
        case MPU6050_REG_SMPLRT_DIV:  m->smplrt_div = buf[0]; return 0;
        case MPU6050_REG_CONFIG:      m->config = buf[0]; return 0;
        case MPU6050_REG_GYRO_CONFIG: m->gyro_cfg = buf[0]; return 0;
        case MPU6050_REG_ACCEL_CONFIG:m->accel_cfg = buf[0]; return 0;
        default: return -1;
    }
}

static int n_pass = 0, n_fail = 0;

static void chf(const char *name, float got, float exp, float tol) {
    int ok = fabsf(got - exp) <= tol;
    printf("    [%s] %-36s got=%.3f expected=%.3f tol=%.3f\n",
           ok ? "PASS" : "FAIL", name, got, exp, tol);
    if (ok) n_pass++; else n_fail++;
}
static void ch(const char *name, int ok) {
    printf("    [%s] %-36s\n", ok ? "PASS" : "FAIL", name);
    if (ok) n_pass++; else n_fail++;
}

int main(void) {
    printf("\n=== MPU6050 driver unit test ===\n");
    i2c_host_mock_init();

    mpu_mock_t state = {0};
    i2c_mock_device_t md = {
        .dev_addr = MPU6050_I2C_ADDR,
        .on_read = mock_read, .on_write = mock_write,
        .user = &state,
    };
    i2c_host_mock_register(&md);
    i2c_bus_init();

    mpu6050_t dev = {0};
    ch("init OK", mpu6050_init(&dev) == I2C_OK);
    ch("initialized flag", dev.initialized);
    ch("WHO_AM_I = 0x68", dev.who_am_i == MPU6050_WHO_AM_I_VAL);
    ch("PWR_MGMT = 0x01 (wake)", state.pwr_mgmt == 0x01);
    ch("SMPLRT_DIV = 0", state.smplrt_div == 0);
    ch("CONFIG DLPF = 3", state.config == 0x03);

    /* ── synthetic reading 1: 1g down on Z, no rotation ── */
    state.accel_raw[0] = 0;
    state.accel_raw[1] = 0;
    state.accel_raw[2] = (int16_t)MPU6050_ACCEL_LSB_PER_G;   /* 16384 */
    state.gyro_raw[0] = 0;
    state.gyro_raw[1] = 0;
    state.gyro_raw[2] = 0;
    state.temp_raw = (int16_t)((25.0f - MPU6050_TEMP_OFFSET) * MPU6050_TEMP_SCALE);

    ch ("read OK", mpu6050_read(&dev) == I2C_OK);
    ch ("last_read_ok", dev.last_read_ok);
    chf("accel.x at rest", dev.accel_g[0], 0.0f, 0.001f);
    chf("accel.y at rest", dev.accel_g[1], 0.0f, 0.001f);
    chf("accel.z = 1g",    dev.accel_g[2], 1.0f, 0.001f);
    chf("gyro.x = 0",      dev.gyro_dps[0], 0.0f, 0.001f);
    chf("temp ~= 25°C",    dev.temp_C, 25.0f, 0.5f);

    /* ── synthetic reading 2: spinning + tilting ── */
    state.accel_raw[0] = (int16_t)(0.5f * MPU6050_ACCEL_LSB_PER_G);
    state.accel_raw[1] = 0;
    state.accel_raw[2] = (int16_t)(0.866f * MPU6050_ACCEL_LSB_PER_G);  /* tilted 30° */
    state.gyro_raw[0] = (int16_t)(100.0f * MPU6050_GYRO_LSB_PER_DPS);  /* 100 °/s X */
    state.gyro_raw[2] = (int16_t)(-50.0f * MPU6050_GYRO_LSB_PER_DPS);  /* -50 °/s Z */

    ch ("read with motion OK", mpu6050_read(&dev) == I2C_OK);
    chf("accel.x = 0.5g",      dev.accel_g[0], 0.5f, 0.001f);
    chf("accel.z = 0.866g",    dev.accel_g[2], 0.866f, 0.001f);
    chf("gyro.x = +100 dps",   dev.gyro_dps[0], 100.0f, 0.05f);
    chf("gyro.z = -50 dps",    dev.gyro_dps[2], -50.0f, 0.05f);

    /* magnitude of vibration RMS — used in telemetry */
    float vib_rms = sqrtf(dev.accel_g[0] * dev.accel_g[0]
                        + dev.accel_g[1] * dev.accel_g[1]
                        + dev.accel_g[2] * dev.accel_g[2]);
    chf("|accel| = 1g (gravity)", vib_rms, 1.0f, 0.01f);

    /* ── fault injection ── */
    float lkg_x = dev.accel_g[0];
    uint32_t fail_before = dev.n_reads_fail;
    i2c_host_mock_inject_fault(1, -7);
    i2c_err_t err = mpu6050_read(&dev);
    ch ("fault detected", err != I2C_OK);
    ch ("last_read_ok=false after fault", dev.last_read_ok == false);
    chf("LKG accel.x preserved", dev.accel_g[0], lkg_x, 0.0001f);
    ch ("n_reads_fail++ ", dev.n_reads_fail == fail_before + 1);

    /* recovery */
    err = mpu6050_read(&dev);
    ch ("recovery OK", err == I2C_OK);

    printf("\n=== MPU6050: %d pass, %d fail ===\n", n_pass, n_fail);
    return n_fail == 0 ? 0 : 1;
}
