#define _POSIX_C_SOURCE 200809L
/*
 * host_test/firmware_driver.c — Faz 19 Saha Test Sürücüsü
 * =============================================================
 * This program plays the role of "real ESP32 plugged into PC via USB".
 *
 * What it does:
 *   - Initializes the same sensor_pipeline + telem_pack used by the
 *     ESP32 firmware
 *   - Generates frames at the target rate (default 1 kHz)
 *   - Writes them to stdout (which the Python field-test harness pipes
 *     into the pty master end → RealESP32Link reads them)
 *
 * Why this is meaningful:
 *   The firmware code path executed here is the SAME code that runs on
 *   ESP32 (telemetry_protocol.c + sensor_pipeline.c). Only the UART
 *   driver differs (host: write(stdout); ESP32: uart_write_bytes).
 *
 *   So if PC accepts these bytes, it will accept the bytes the real
 *   ESP32 produces — modulo only UART hardware timing.
 *
 * Build:
 *   gcc -O2 -Wall -I../include firmware_driver.c \\
 *       ../main/telemetry_protocol.c ../main/sensor_pipeline.c \\
 *       -o firmware_driver -lm
 *
 * Run:
 *   ./firmware_driver --rate-hz 1000 --duration-s 30 > /dev/pts/X
 */
#include "telemetry_frame.h"
#include "sensor_pipeline.h"
#include "ina226.h"
#include "mpu6050.h"
#include "thermal.h"
#include "i2c_host_mock.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdint.h>
#include <unistd.h>
#include <math.h>

/* ── Mock sensor handlers (compiled in only for host build) ── */

static int16_t  g_ina_current = 4000;       /* 5 A nominal */
static uint16_t g_ina_bus_v   = 9600;       /* 12 V nominal */
static int16_t  g_imu_accel[3] = {0, 0, 16384};   /* 1g down */
static int16_t  g_imu_gyro[3]  = {0, 0, 0};
static int16_t  g_imu_temp    = (int16_t)((25.0f - 36.53f) * 340.0f);

static int ina_mock_read(uint8_t reg, uint8_t *buf, size_t n, void *user) {
    (void)user;
    if (n != 2) return -1;
    uint16_t v = 0;
    static uint16_t cfg = 0x4127, cal = 0;
    switch (reg) {
        case INA226_REG_MFG_ID:  v = INA226_MFG_ID_TI; break;
        case INA226_REG_DIE_ID:  v = INA226_DIE_ID_NOMINAL; break;
        case INA226_REG_CONFIG:  v = cfg; break;
        case INA226_REG_BUS_V:   v = g_ina_bus_v; break;
        case INA226_REG_CURRENT: v = (uint16_t)g_ina_current; break;
        case INA226_REG_CAL:     v = cal; break;
        default: return -1;
    }
    buf[0] = (uint8_t)(v >> 8);
    buf[1] = (uint8_t)(v & 0xFF);
    return 0;
}
static int ina_mock_write(uint8_t reg, const uint8_t *buf, size_t n, void *user) {
    (void)user; (void)buf; (void)n; (void)reg;
    return 0;
}

static int imu_mock_read(uint8_t reg, uint8_t *buf, size_t n, void *user) {
    (void)user;
    if (reg == MPU6050_REG_WHO_AM_I && n == 1) {
        buf[0] = MPU6050_WHO_AM_I_VAL; return 0;
    }
    if (reg == MPU6050_REG_ACCEL_XOUT_H && n == 14) {
        int16_t v[7] = { g_imu_accel[0], g_imu_accel[1], g_imu_accel[2],
                         g_imu_temp,
                         g_imu_gyro[0], g_imu_gyro[1], g_imu_gyro[2] };
        for (int i = 0; i < 7; ++i) {
            buf[i*2]     = (uint8_t)(((uint16_t)v[i]) >> 8);
            buf[i*2 + 1] = (uint8_t)(((uint16_t)v[i]) & 0xFF);
        }
        return 0;
    }
    return -1;
}
static int imu_mock_write(uint8_t reg, const uint8_t *buf, size_t n, void *user) {
    (void)user; (void)buf; (void)n; (void)reg;
    return 0;
}

/* Per-sensor fault scheduling — windows expressed in elapsed seconds.
   Inside [start, end] the named sensor's I²C reads return error.
   Set via CLI: --fault-ina-start S --fault-ina-end S (and same for imu/thermal). */
static double g_fault_ina_start_s     = -1.0;
static double g_fault_ina_end_s       = -1.0;
static double g_fault_imu_start_s     = -1.0;
static double g_fault_imu_end_s       = -1.0;
static double g_fault_thermal_start_s = -1.0;
static double g_fault_thermal_end_s   = -1.0;

static int in_window(double t_s, double a, double b) {
    return a >= 0 && b >= 0 && t_s >= a && t_s <= b;
}

static int ina_mock_read_w(uint8_t reg, uint8_t *buf, size_t n, void *user) {
    extern double g_test_elapsed_s;
    if (in_window(g_test_elapsed_s, g_fault_ina_start_s, g_fault_ina_end_s))
        return -1;
    return ina_mock_read(reg, buf, n, user);
}
static int imu_mock_read_w(uint8_t reg, uint8_t *buf, size_t n, void *user) {
    extern double g_test_elapsed_s;
    if (in_window(g_test_elapsed_s, g_fault_imu_start_s, g_fault_imu_end_s))
        return -1;
    return imu_mock_read(reg, buf, n, user);
}

double g_test_elapsed_s = 0.0;   /* updated by main loop each iteration */

static void register_mock_sensors(void) {
    i2c_host_mock_init();
    i2c_mock_device_t a = { .dev_addr = INA226_I2C_ADDR,
                            .on_read = ina_mock_read_w,
                            .on_write = ina_mock_write };
    i2c_mock_device_t b = { .dev_addr = MPU6050_I2C_ADDR,
                            .on_read = imu_mock_read_w,
                            .on_write = imu_mock_write };
    i2c_host_mock_register(&a);
    i2c_host_mock_register(&b);
    /* Thermal mock has its own injection API; set room temp */
    thermal_host_set_adc_mv(1650.0f);
}

/* Simulate a little realistic motion in the mock sensor values so the
   PC chart shows movement. Called every ~10ms. */
static void wiggle_sensors(uint64_t t_us) {
    float t = (float)((double)t_us * 1e-6);
    /* Current sine 4..6 A around 5 A nominal at 0.5 Hz */
    g_ina_current = (int16_t)(4000.0f + 800.0f * sinf(t * 3.14159f));
    /* Slight vibration on accel */
    g_imu_accel[0] = (int16_t)(200.0f * sinf(t * 20.0f));   /* tiny X */
    g_imu_accel[1] = (int16_t)(150.0f * cosf(t * 23.0f));   /* tiny Y */
    /* Z stays at 1g */
}

static uint64_t now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000ull + (uint64_t)ts.tv_nsec / 1000ull;
}

static void usleep_until(uint64_t target_us) {
    while (1) {
        uint64_t cur = now_us();
        if (cur >= target_us) return;
        uint64_t gap = target_us - cur;
        if (gap > 500) {
            struct timespec req = { .tv_sec = 0, .tv_nsec = (long)(gap - 200) * 1000 };
            nanosleep(&req, NULL);
        }
        /* spin the last ~200 us for low jitter */
    }
}

int main(int argc, char **argv) {
    double rate_hz   = 1000.0;
    double duration_s = -1.0;   /* -1 = forever */
    /* parse CLI */
    for (int i = 1; i < argc - 1; ++i) {
        if (!strcmp(argv[i], "--rate-hz"))           rate_hz    = atof(argv[i+1]);
        else if (!strcmp(argv[i], "--duration-s"))   duration_s = atof(argv[i+1]);
        else if (!strcmp(argv[i], "--fault-ina-start"))     g_fault_ina_start_s     = atof(argv[i+1]);
        else if (!strcmp(argv[i], "--fault-ina-end"))       g_fault_ina_end_s       = atof(argv[i+1]);
        else if (!strcmp(argv[i], "--fault-imu-start"))     g_fault_imu_start_s     = atof(argv[i+1]);
        else if (!strcmp(argv[i], "--fault-imu-end"))       g_fault_imu_end_s       = atof(argv[i+1]);
        else if (!strcmp(argv[i], "--fault-thermal-start")) g_fault_thermal_start_s = atof(argv[i+1]);
        else if (!strcmp(argv[i], "--fault-thermal-end"))   g_fault_thermal_end_s   = atof(argv[i+1]);
    }
    if (rate_hz <= 0) { fprintf(stderr, "bad rate\n"); return 1; }

    /* Faz 19B: register host mocks for the three I²C sensors so
       sensor_pipeline_init() can see them. */
    register_mock_sensors();
    sensor_pipeline_init();
    /* Prime the cache so first telemetry frame has data */
    for (int i = 0; i < 3; ++i) sensor_pipeline_step();

    uint64_t period_us = (uint64_t)(1e6 / rate_hz);
    uint64_t sensor_period_us = 10000;  /* 100 Hz sensor sampling */
    uint64_t start = now_us();
    uint64_t deadline = (duration_s > 0) ? start + (uint64_t)(duration_s * 1e6)
                                         : UINT64_MAX;
    uint64_t next_t = start;
    uint64_t next_sensor_t = start;
    uint16_t seq = 0;

    /* Make stdout unbuffered so each frame leaves immediately */
    setvbuf(stdout, NULL, _IONBF, 0);

    uint8_t wire[TELEM_WIRE_LEN];
    telem_frame_t f;

    uint64_t n_frames = 0;
    int thermal_fault_active = 0;
    while (1) {
        uint64_t t = now_us();
        if (t >= deadline) break;
        g_test_elapsed_s = (double)(t - start) * 1e-6;

        /* Thermal fault window: keep injecting while inside window */
        int want_thermal_fault = in_window(g_test_elapsed_s,
                                            g_fault_thermal_start_s,
                                            g_fault_thermal_end_s);
        if (want_thermal_fault && !thermal_fault_active) {
            thermal_host_inject_fault(100000);   /* large; cleared at window exit */
            thermal_fault_active = 1;
        } else if (!want_thermal_fault && thermal_fault_active) {
            thermal_host_inject_fault(0);        /* clear residual */
            thermal_fault_active = 0;
        }

        /* 100 Hz: update sensors + drive sensor pipeline */
        if (t >= next_sensor_t) {
            wiggle_sensors(t - start);
            sensor_pipeline_step();
            next_sensor_t += sensor_period_us;
        }

        if (t >= next_t) {
            sensor_pipeline_read(&f, t - start, seq);
            telem_pack(wire, &f);
            size_t written = 0;
            while (written < TELEM_WIRE_LEN) {
                ssize_t n = write(STDOUT_FILENO,
                                  wire + written,
                                  TELEM_WIRE_LEN - written);
                if (n <= 0) {
                    fprintf(stderr, "stdout write failed/closed; exiting\n");
                    return 1;
                }
                written += (size_t)n;
            }
            seq = (uint16_t)(seq + 1u);
            n_frames++;
            next_t += period_us;
        }
        usleep_until(next_t);
    }
    fprintf(stderr, "firmware_driver: wrote %llu frames in %.2f s\n",
            (unsigned long long)n_frames,
            (double)(now_us() - start) / 1e6);
    return 0;
}
