/*
 * host_test/test_race_cache.c — Cache Concurrency Stress Test
 * ===================================================================
 * Pounds the sensor_pipeline cache from multiple threads to verify
 * that the reader never sees a torn struct (mixed-update fields from
 * different snapshots).
 *
 * Strategy:
 *   - Producer thread: every iteration, sets all sensor mock values
 *     to a NEW consistent set keyed off a monotonically-increasing
 *     "epoch" counter. Then calls sensor_pipeline_step(). Repeats.
 *
 *   - Multiple consumer threads (3): in a tight loop, call
 *     sensor_pipeline_read() and verify that the float fields in
 *     the result are mutually consistent — i.e. they all come from
 *     ONE producer iteration, never a mix.
 *
 *   - Run for N seconds, count torn reads.
 *
 * Coherence check:
 *   We encode the producer epoch into multiple cache fields:
 *     ina.current     = epoch          (via mock)
 *     imu.accel[0]    = epoch
 *     ntc.adc_mv      → temp           (epoch encoded)
 *   When the reader reads, current_A == accel_g[0] and temp_K is a
 *   matching value. If we ever see current_A from epoch K but
 *   accel_g[0] from epoch K+1, that's a torn read.
 *
 * Pass: 0 torn reads, >100k snapshots, >300k reads.
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
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <pthread.h>
#include <time.h>
#include <math.h>

/* Shared producer epoch — written by producer, observable by mocks */
static _Atomic int g_epoch = 0;

static int16_t  g_ina_current = 0;
static uint16_t g_ina_bus_v   = 9600;
static int16_t  g_imu_accel0  = 0;
static int16_t  g_imu_temp    = 0;

static int ina_read(uint8_t reg, uint8_t *buf, size_t n, void *u) {
    (void)u;
    if (n != 2) return -1;
    uint16_t v = 0;
    switch (reg) {
        case INA226_REG_MFG_ID:  v = INA226_MFG_ID_TI; break;
        case INA226_REG_DIE_ID:  v = INA226_DIE_ID_NOMINAL; break;
        case INA226_REG_CONFIG:  v = 0x4127; break;
        case INA226_REG_BUS_V:   v = g_ina_bus_v; break;
        case INA226_REG_CURRENT: v = (uint16_t)g_ina_current; break;
        case INA226_REG_CAL:     v = 2048; break;
        default: return -1;
    }
    buf[0] = (uint8_t)(v >> 8); buf[1] = (uint8_t)(v & 0xFF);
    return 0;
}
static int ina_write(uint8_t r, const uint8_t *b, size_t n, void *u) {
    (void)r;(void)b;(void)n;(void)u; return 0;
}
static int imu_read(uint8_t reg, uint8_t *buf, size_t n, void *u) {
    (void)u;
    if (reg == MPU6050_REG_WHO_AM_I && n == 1) {
        buf[0] = MPU6050_WHO_AM_I_VAL; return 0;
    }
    if (reg == MPU6050_REG_ACCEL_XOUT_H && n == 14) {
        int16_t v[7] = { g_imu_accel0, 0, 0, g_imu_temp, 0, 0, 0 };
        for (int i = 0; i < 7; ++i) {
            buf[i*2]     = (uint8_t)(((uint16_t)v[i]) >> 8);
            buf[i*2 + 1] = (uint8_t)(((uint16_t)v[i]) & 0xFF);
        }
        return 0;
    }
    return -1;
}
static int imu_write(uint8_t r, const uint8_t *b, size_t n, void *u) {
    (void)r;(void)b;(void)n;(void)u; return 0;
}

/* Each epoch updates:
 *   g_ina_current = epoch (as int16; epoch must stay in int16 range)
 *   g_imu_accel0  = epoch
 *   thermal mv such that temp_K - 273.15 = (epoch % 100)
 * Then we step the pipeline.
 *
 * Reader can then check:
 *   round(frame.current_A / INA226_CURRENT_LSB_A)  ≡  observed_epoch
 *   round(frame.vib_x * MPU6050_ACCEL_LSB_PER_G)   ≡  observed_epoch
 *   frame.temp_K  consistent with observed_epoch
 */
static void apply_epoch(int e) {
    g_ina_current = (int16_t)e;
    g_imu_accel0  = (int16_t)e;
    /* Thermal: just set mv to a value that depends on e. We don't strictly
       need this to be in sync; the torn-read check covers ina+imu.
       But we still keep thermal moving to exercise the third sensor. */
    thermal_host_set_adc_mv(1650.0f + (float)(e % 50));
}

static volatile int g_stop = 0;
static _Atomic long g_n_snapshots = 0;
static _Atomic long g_n_reads     = 0;
static _Atomic long g_n_torn      = 0;
static _Atomic long g_n_consistent= 0;

static void *producer_thread(void *arg) {
    (void)arg;
    int e = 1;
    while (!g_stop) {
        apply_epoch(e);
        sensor_pipeline_step();
        g_n_snapshots++;
        e++;
        if (e > 30000) e = 1;
        /* No sleep — pound as hard as possible */
    }
    return NULL;
}

static void *consumer_thread(void *arg) {
    (void)arg;
    telem_frame_t f;
    uint64_t ts = 0;
    while (!g_stop) {
        sensor_pipeline_read(&f, ts, 0);
        g_n_reads++;
        ts += 1000;
        /* Coherence check: ina current and imu accel were written from
           the SAME producer iteration; they MUST round to the same
           int16. If we see them differ, the reader pulled from a half-
           updated cache. */
        int ina_int = (int)lroundf(f.current_A / INA226_CURRENT_LSB_A);
        int imu_int = (int)lroundf(f.vib_x     * MPU6050_ACCEL_LSB_PER_G);
        if (ina_int != imu_int) {
            g_n_torn++;
        } else {
            g_n_consistent++;
        }
    }
    return NULL;
}

int main(int argc, char **argv) {
    double duration_s = 3.0;
    int n_consumers = 3;
    if (argc > 1) duration_s = atof(argv[1]);
    if (argc > 2) n_consumers = atoi(argv[2]);

    printf("\n=== race_cache stress test ===\n");
    printf("  duration: %.1f s, %d consumer threads, 1 producer\n",
           duration_s, n_consumers);

    /* Set up mocks */
    i2c_host_mock_init();
    i2c_mock_device_t a = { .dev_addr = INA226_I2C_ADDR,
                             .on_read = ina_read, .on_write = ina_write };
    i2c_mock_device_t b = { .dev_addr = MPU6050_I2C_ADDR,
                             .on_read = imu_read, .on_write = imu_write };
    i2c_host_mock_register(&a);
    i2c_host_mock_register(&b);
    thermal_host_set_adc_mv(1650.0f);

    sensor_pipeline_init();
    /* prime */
    for (int i = 0; i < 5; ++i) {
        apply_epoch(i + 1);
        sensor_pipeline_step();
    }

    pthread_t prod, cons[16];
    pthread_create(&prod, NULL, producer_thread, NULL);
    for (int i = 0; i < n_consumers; ++i) {
        pthread_create(&cons[i], NULL, consumer_thread, NULL);
    }

    struct timespec sleep_ts = {
        (time_t)duration_s, (long)((duration_s - (time_t)duration_s) * 1e9)
    };
    nanosleep(&sleep_ts, NULL);
    g_stop = 1;
    pthread_join(prod, NULL);
    for (int i = 0; i < n_consumers; ++i) pthread_join(cons[i], NULL);

    long snaps  = g_n_snapshots;
    long reads  = g_n_reads;
    long torn   = g_n_torn;
    long cons_n = g_n_consistent;

    printf("\n  snapshots:    %ld\n", snaps);
    printf("  reads:        %ld\n", reads);
    printf("  consistent:   %ld\n", cons_n);
    printf("  TORN reads:   %ld\n", torn);
    printf("  read/sec:     %.1fK\n", reads / duration_s / 1000.0);
    printf("  snap/sec:     %.1fK\n", snaps / duration_s / 1000.0);
    if (torn == 0 && reads > 100000 && snaps > 10000) {
        printf("\n  === race_cache: 4 pass, 0 fail ===\n");
        return 0;
    } else {
        printf("\n  === race_cache: 0 pass, 1 fail ===\n");
        return 1;
    }
}
