/*
 * host_test/test_sensor_pipeline.c — Integrated pipeline test
 * ===================================================================
 * Wires the three host-mock sensors into the real sensor_pipeline,
 * runs sensor_pipeline_step() in a worker thread, and verifies:
 *
 *   1. All three sensors are read and cached
 *   2. sensor_pipeline_read() produces a frame with the real values
 *   3. Per-sensor fault doesn't take down the pipeline — others keep
 *      flowing, last-known-good preserved for the failed one, flag
 *      bit clears for the failed sensor
 *   4. Recovery: flag bit re-sets after I²C resumes
 *
 * Build:
 *   gcc -O2 -Wall -std=c11 -I../include -I. \\
 *       test_sensor_pipeline.c i2c_host_mock.c \\
 *       ../main/i2c_bus.c ../main/ina226.c ../main/mpu6050.c \\
 *       ../main/thermal.c ../main/sensor_pipeline.c \\
 *       ../main/telemetry_protocol.c \\
 *       -o test_sensor_pipeline -lpthread -lm
 */
#define _POSIX_C_SOURCE 200809L
#include "sensor_pipeline.h"
#include "telemetry_frame.h"
#include "ina226.h"
#include "mpu6050.h"
#include "thermal.h"
#include "i2c_bus.h"
#include "i2c_host_mock.h"

#include <stdio.h>
#include <string.h>
#include <math.h>
#include <stdint.h>
#include <unistd.h>
#include <time.h>

/* ── mock device state ── */

static struct {
    uint16_t cfg;
    int16_t  current;
    uint16_t bus_v;
    uint16_t cal;
} ina_state = {0};

static int ina_read(uint8_t reg, uint8_t *buf, size_t n, void *user) {
    (void)user;
    if (n != 2) return -1;
    uint16_t v = 0;
    switch (reg) {
        case INA226_REG_MFG_ID:  v = INA226_MFG_ID_TI; break;
        case INA226_REG_DIE_ID:  v = INA226_DIE_ID_NOMINAL; break;
        case INA226_REG_CONFIG:  v = ina_state.cfg; break;
        case INA226_REG_BUS_V:   v = ina_state.bus_v; break;
        case INA226_REG_CURRENT: v = (uint16_t)ina_state.current; break;
        case INA226_REG_CAL:     v = ina_state.cal; break;
        default: return -1;
    }
    buf[0] = (uint8_t)(v >> 8);
    buf[1] = (uint8_t)(v & 0xFF);
    return 0;
}
static int ina_write(uint8_t reg, const uint8_t *buf, size_t n, void *user) {
    (void)user;
    if (n != 2) return -1;
    uint16_t v = ((uint16_t)buf[0] << 8) | buf[1];
    switch (reg) {
        case INA226_REG_CONFIG: ina_state.cfg = v; return 0;
        case INA226_REG_CAL:    ina_state.cal = v; return 0;
        default: return -1;
    }
}

static struct {
    int16_t accel[3], gyro[3], temp;
    uint8_t pwr, smplrt, cfg, gcfg, acfg;
} imu_state = {0};

static int imu_read(uint8_t reg, uint8_t *buf, size_t n, void *user) {
    (void)user;
    if (reg == MPU6050_REG_WHO_AM_I && n == 1) {
        buf[0] = MPU6050_WHO_AM_I_VAL; return 0;
    }
    if (reg == MPU6050_REG_ACCEL_XOUT_H && n == 14) {
        int16_t v[7] = { imu_state.accel[0], imu_state.accel[1], imu_state.accel[2],
                         imu_state.temp,
                         imu_state.gyro[0], imu_state.gyro[1], imu_state.gyro[2] };
        for (int i = 0; i < 7; ++i) {
            buf[i*2]     = (uint8_t)(((uint16_t)v[i]) >> 8);
            buf[i*2 + 1] = (uint8_t)(((uint16_t)v[i]) & 0xFF);
        }
        return 0;
    }
    return -1;
}
static int imu_write(uint8_t reg, const uint8_t *buf, size_t n, void *user) {
    (void)user;
    if (n != 1) return -1;
    switch (reg) {
        case MPU6050_REG_PWR_MGMT_1:   imu_state.pwr = buf[0]; return 0;
        case MPU6050_REG_SMPLRT_DIV:   imu_state.smplrt = buf[0]; return 0;
        case MPU6050_REG_CONFIG:       imu_state.cfg = buf[0]; return 0;
        case MPU6050_REG_GYRO_CONFIG:  imu_state.gcfg = buf[0]; return 0;
        case MPU6050_REG_ACCEL_CONFIG: imu_state.acfg = buf[0]; return 0;
        default: return -1;
    }
}

/* ── per-sensor fault injection (separate from generic bus fault) ── */
static int ina_force_fail = 0;   /* drop next N ina reads */
static int imu_force_fail = 0;

static int ina_read_with_fault(uint8_t reg, uint8_t *buf, size_t n, void *user) {
    if (ina_force_fail > 0) { ina_force_fail--; return -1; }
    return ina_read(reg, buf, n, user);
}
static int imu_read_with_fault(uint8_t reg, uint8_t *buf, size_t n, void *user) {
    if (imu_force_fail > 0) { imu_force_fail--; return -1; }
    return imu_read(reg, buf, n, user);
}

/* ── test machinery ── */

static int n_pass = 0, n_fail = 0;
static void chf(const char *name, float got, float exp, float tol) {
    int ok = fabsf(got - exp) <= tol;
    printf("    [%s] %-44s got=%.3f exp=%.3f tol=%.3f\n",
           ok ? "PASS" : "FAIL", name, got, exp, tol);
    if (ok) n_pass++; else n_fail++;
}
static void ch(const char *name, int ok) {
    printf("    [%s] %-44s\n", ok ? "PASS" : "FAIL", name);
    if (ok) n_pass++; else n_fail++;
}

static void sleep_ms(int ms) {
    struct timespec ts = { ms/1000, (ms%1000)*1000000L };
    nanosleep(&ts, NULL);
}

int main(void) {
    printf("\n=== sensor_pipeline integration test ===\n");

    /* Set up mocks */
    i2c_host_mock_init();
    i2c_mock_device_t ina_md = {
        .dev_addr = INA226_I2C_ADDR,
        .on_read = ina_read_with_fault, .on_write = ina_write, .user = NULL };
    i2c_mock_device_t imu_md = {
        .dev_addr = MPU6050_I2C_ADDR,
        .on_read = imu_read_with_fault, .on_write = imu_write, .user = NULL };
    i2c_host_mock_register(&ina_md);
    i2c_host_mock_register(&imu_md);

    /* Set realistic sensor values */
    ina_state.bus_v   = 9600;             /* 12 V */
    ina_state.current = 4000;             /* 5 A */
    imu_state.accel[0] = 0;
    imu_state.accel[1] = 0;
    imu_state.accel[2] = (int16_t)MPU6050_ACCEL_LSB_PER_G;  /* 1g down */
    imu_state.gyro[0] = 0;
    imu_state.gyro[1] = 0;
    imu_state.gyro[2] = 0;
    imu_state.temp = (int16_t)((25.0f - MPU6050_TEMP_OFFSET) * MPU6050_TEMP_SCALE);

    /* Set NTC mock to ~25°C */
    thermal_host_set_adc_mv(1650.0f);

    /* Init pipeline */
    sensor_pipeline_init();
    /* Step a few times to fill cache */
    for (int i = 0; i < 5; ++i) {
        sensor_pipeline_step();
    }

    /* ── verify cache populated ── */
    float curr, bus, accel[3], tk;
    uint16_t flags_ok;
    sensor_pipeline_host_peek_cache(&curr, &bus, accel, &tk, &flags_ok);
    chf("cache.current_A     after step", curr, 5.0f,  0.01f);
    chf("cache.bus_V         after step", bus,  12.0f, 0.01f);
    chf("cache.accel[2]      after step", accel[2], 1.0f, 0.01f);
    chf("cache.ntc_temp_K    after step", tk, 298.15f, 0.5f);
    ch ("INA226_OK flag set",    flags_ok & TELEM_FLAG_INA226_OK);
    ch ("IMU_OK    flag set",    flags_ok & TELEM_FLAG_IMU_OK);
    ch ("THERMAL_OK flag set",   flags_ok & TELEM_FLAG_THERMAL_OK);

    /* ── verify frame propagation ── */
    telem_frame_t frame;
    sensor_pipeline_read(&frame, 1000000ull, 1);
    chf("frame.current_A",     frame.current_A, 5.0f, 0.01f);
    chf("frame.temp_K",        frame.temp_K, 298.15f, 0.5f);
    chf("frame.vib_z (1g)",    frame.vib_z, 1.0f, 0.01f);
    ch ("frame.flags INA226_OK", frame.flags & TELEM_FLAG_INA226_OK);
    ch ("frame.flags IMU_OK",    frame.flags & TELEM_FLAG_IMU_OK);
    ch ("frame.flags THERMAL_OK", frame.flags & TELEM_FLAG_THERMAL_OK);
    chf("frame.quality (all 3 healthy)", frame.quality, 92.0f, 0.5f);

    /* ── selective fault: kill INA226 only ── */
    /* INA reads 1 register at a time → 2 reads per ina226_read call.
       Force fail many times so it stays down across multiple steps. */
    ina_force_fail = 100;
    for (int i = 0; i < 5; ++i) sensor_pipeline_step();

    sensor_pipeline_host_peek_cache(&curr, &bus, accel, &tk, &flags_ok);
    ch ("INA226_OK clears when INA down",  !(flags_ok & TELEM_FLAG_INA226_OK));
    ch ("IMU_OK still set",                flags_ok & TELEM_FLAG_IMU_OK);
    ch ("THERMAL_OK still set",            flags_ok & TELEM_FLAG_THERMAL_OK);
    chf("INA LKG current preserved",       curr, 5.0f, 0.01f);
    chf("IMU still updating (accel)",      accel[2], 1.0f, 0.01f);

    sensor_pipeline_read(&frame, 2000000ull, 2);
    ch ("frame: INA flag clear",   !(frame.flags & TELEM_FLAG_INA226_OK));
    ch ("frame: IMU flag set",      frame.flags & TELEM_FLAG_IMU_OK);
    chf("frame.quality drops (1 sensor down)", frame.quality, 67.0f, 0.5f);

    /* ── recovery ── */
    ina_force_fail = 0;
    for (int i = 0; i < 3; ++i) sensor_pipeline_step();

    sensor_pipeline_host_peek_cache(&curr, &bus, accel, &tk, &flags_ok);
    ch ("INA226_OK re-sets on recovery", flags_ok & TELEM_FLAG_INA226_OK);

    /* ── all three down simultaneously: pipeline still alive ── */
    ina_force_fail = 100;
    imu_force_fail = 100;
    thermal_host_inject_fault(100);
    for (int i = 0; i < 5; ++i) sensor_pipeline_step();
    sensor_pipeline_host_peek_cache(&curr, &bus, accel, &tk, &flags_ok);
    ch ("all 3 flags clear when all down", flags_ok == 0);
    chf("LKG current still 5A",          curr, 5.0f, 0.01f);
    chf("LKG accel still ~1g",           accel[2], 1.0f, 0.01f);
    chf("LKG temp still ~25°C",          tk, 298.15f, 0.5f);

    sensor_pipeline_read(&frame, 3000000ull, 3);
    chf("frame.quality minimum (all down)", frame.quality, 17.0f, 0.5f);
    ch ("frame.flags BOOT_OK still set",    frame.flags & TELEM_FLAG_BOOT_OK);

    /* recovery all */
    ina_force_fail = imu_force_fail = 0;
    /* Cancel any remaining thermal faults from previous injection */
    thermal_host_inject_fault(0);
    for (int i = 0; i < 3; ++i) sensor_pipeline_step();
    sensor_pipeline_host_peek_cache(&curr, &bus, accel, &tk, &flags_ok);
    ch ("all 3 flags re-set on recovery",
        (flags_ok & TELEM_FLAG_INA226_OK)
       && (flags_ok & TELEM_FLAG_IMU_OK)
       && (flags_ok & TELEM_FLAG_THERMAL_OK));

    /* ── health snapshot ── */
    sensor_pipeline_health_t h;
    sensor_pipeline_get_health(&h);
    ch ("health: snapshots > 0",  h.n_snapshots > 0);
    ch ("health: ina_ok > 0",     h.ina226_reads_ok > 0);
    ch ("health: ina_fail > 0",   h.ina226_reads_fail > 0);
    ch ("health: imu_ok > 0",     h.imu_reads_ok > 0);
    ch ("health: imu_fail > 0",   h.imu_reads_fail > 0);
    ch ("health: thermal_ok > 0", h.thermal_reads_ok > 0);

    printf("\n=== sensor_pipeline: %d pass, %d fail ===\n", n_pass, n_fail);
    return n_fail == 0 ? 0 : 1;
}
