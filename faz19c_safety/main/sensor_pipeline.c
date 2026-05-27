/*
 * main/sensor_pipeline.c — Faz 19B Real Sensor Pipeline (+ Faz 19C safety snapshot)
 * ====================================================================================
 * Replaces the Faz 19A synthetic generator with one that aggregates
 * real readings from INA226 + MPU6050 + NTC thermistor.
 *
 * Design rules (per Faz 19B spec):
 *   - Single sensor failure does NOT down the pipeline
 *   - Last-known-good values propagate with flag bits clear
 *   - No malloc, single static state object
 *   - Logical-to-wire mapping documented inline so PC parser is
 *     unchanged (Option A — replay determinism preserved)
 *
 * Faz 19C addition:
 *   - s_safe_temp_K / s_safe_current_A: volatile floats updated by
 *     sensor_pipeline_step() after releasing the cache mutex.
 *     safety_monitor_task() reads these without any lock (never blocks).
 *     Single-word (32-bit float) aligned reads are atomic on ARMv7-M.
 */
#define _POSIX_C_SOURCE 200809L
#include "sensor_pipeline.h"
#include "telemetry_frame.h"
#include "ina226.h"
#include "mpu6050.h"
#include "thermal.h"

#include <math.h>
#include <string.h>

#ifdef ESP_PLATFORM
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_log.h"
static SemaphoreHandle_t s_cache_mutex = NULL;
static const char *TAG = "sensor_pipeline";
#else
#include <pthread.h>
static pthread_mutex_t s_cache_mutex = PTHREAD_MUTEX_INITIALIZER;
#endif

/* Devices owned by this module — single static instance each */
static ina226_t  s_ina;
static mpu6050_t s_imu;
static thermal_t s_therm;

/* Cached snapshot updated by sensor_i2c_task. Protected by mutex. */
typedef struct {
    float    current_A;
    float    bus_voltage_V;
    float    accel_g[3];
    float    gyro_dps[3];
    float    imu_temp_C;
    float    ntc_temp_K;
    uint16_t flags_sensor_ok;   /* bits 9..11: INA226 / IMU / THERMAL OK */
    uint32_t n_snapshots;       /* sensor_i2c_task updates this */
} sensor_cache_t;

static sensor_cache_t s_cache;
static int s_initialized = 0;

/* Lock-free safety snapshot (Faz 19C): updated after cache_unlock().
   Aligned 32-bit float reads/writes are atomic on ARMv7-M. */
static volatile float s_safe_temp_K    = 295.15f;
static volatile float s_safe_current_A = 0.0f;

/* ───────────────────────── lock helpers ──────────────────────────── */

static void cache_lock(void) {
#ifdef ESP_PLATFORM
    if (s_cache_mutex) xSemaphoreTake(s_cache_mutex, portMAX_DELAY);
#else
    pthread_mutex_lock(&s_cache_mutex);
#endif
}

static void cache_unlock(void) {
#ifdef ESP_PLATFORM
    if (s_cache_mutex) xSemaphoreGive(s_cache_mutex);
#else
    pthread_mutex_unlock(&s_cache_mutex);
#endif
}

/* ───────────────────────── init ────────────────────────────────────── */

void sensor_pipeline_init(void) {
    /* Reset cache */
    memset(&s_cache, 0, sizeof(s_cache));
    s_cache.ntc_temp_K = 295.15f;        /* room temp default */
    s_safe_temp_K      = 295.15f;
    s_safe_current_A   = 0.0f;

#ifdef ESP_PLATFORM
    if (s_cache_mutex == NULL) {
        s_cache_mutex = xSemaphoreCreateMutex();
    }
#endif

    /* Initialize bus once */
    i2c_bus_init();

    /* Each sensor is best-effort: if init fails, the cache flag stays
       clear and the pipeline keeps running. */
    i2c_err_t err;
    err = ina226_init(&s_ina);
    if (err == I2C_OK) {
        s_cache.flags_sensor_ok |= TELEM_FLAG_INA226_OK;
    }

    err = mpu6050_init(&s_imu);
    if (err == I2C_OK) {
        s_cache.flags_sensor_ok |= TELEM_FLAG_IMU_OK;
    }

    thermal_calib_t calib = THERMAL_CALIB_DEFAULT;
    /* ESP32: ADC1 channel 0 is GPIO 36 on ESP32-DevKitC. */
    err = thermal_init(&s_therm, 0, &calib);
    if (err == THERMAL_OK) {
        s_cache.flags_sensor_ok |= TELEM_FLAG_THERMAL_OK;
    }

    s_initialized = 1;
}

/* ───────────────────────── sensor_i2c_task: 100 Hz drain ─────────── */

void sensor_pipeline_step(void) {
    if (!s_initialized) return;

    /* Read each sensor. Each call protects last-known-good internally. */
    if (s_ina.initialized) (void)ina226_read(&s_ina);
    if (s_imu.initialized) (void)mpu6050_read(&s_imu);
    if (s_therm.initialized) (void)thermal_read(&s_therm);

    /* Snapshot into cache under lock */
    cache_lock();
    {
        uint16_t flags = 0;

        if (s_ina.last_read_ok) {
            s_cache.current_A    = s_ina.current_A;
            s_cache.bus_voltage_V = s_ina.bus_voltage_V;
            flags |= TELEM_FLAG_INA226_OK;
        }

        if (s_imu.last_read_ok) {
            s_cache.accel_g[0] = s_imu.accel_g[0];
            s_cache.accel_g[1] = s_imu.accel_g[1];
            s_cache.accel_g[2] = s_imu.accel_g[2];
            s_cache.gyro_dps[0] = s_imu.gyro_dps[0];
            s_cache.gyro_dps[1] = s_imu.gyro_dps[1];
            s_cache.gyro_dps[2] = s_imu.gyro_dps[2];
            s_cache.imu_temp_C  = s_imu.temp_C;
            flags |= TELEM_FLAG_IMU_OK;
        }

        if (s_therm.last_read_ok) {
            s_cache.ntc_temp_K = s_therm.temp_K;
            flags |= TELEM_FLAG_THERMAL_OK;
        }

        s_cache.flags_sensor_ok = flags;
        s_cache.n_snapshots++;
    }
    cache_unlock();

    /* Update volatile safety snapshot AFTER releasing the mutex.
       Safety monitor reads these without any lock — zero blocking. */
    s_safe_temp_K    = s_cache.ntc_temp_K;
    s_safe_current_A = s_cache.current_A;
}

/* ───────────────────────── telemetry_task: snapshot to frame ─────── */

void sensor_pipeline_read(telem_frame_t *out, uint64_t now_us, uint16_t seq) {
    if (out == NULL) return;

    /* Snapshot cache under lock. Do not hold lock across any expensive
       operation — copy minimal struct then process outside. */
    sensor_cache_t snap;
    cache_lock();
    snap = s_cache;
    cache_unlock();

    /* Build the wire frame.
       Base flags: BOOT_OK always set; safety flags driven from values.
       Sensor-OK flags come from the snapshot. */
    uint16_t flags = (uint16_t)TELEM_FLAG_BOOT_OK | snap.flags_sensor_ok;

    /* Mock-style motion (until encoder hookup). Smooth periodic motion
       so the LiveProductionPanel chart shows believable traces. */
    float t_s = (float)((double)now_us * 1e-6);
    float x   = 40.0f + 150.0f * (1.0f + sinf(t_s * 0.5f)) * 0.5f;
    float a   = fmodf(t_s * 30.0f, 360.0f);

    out->ts_us     = now_us;
    out->seq       = seq;
    out->x_mm      = x;
    out->a_deg     = a;
    out->T_N       = 15.0f;
    out->rpm       = 5.0f;

    out->vib_x     = snap.accel_g[0];
    out->vib_y     = snap.accel_g[1];
    out->vib_z     = snap.accel_g[2];

    out->temp_K    = snap.ntc_temp_K;
    out->current_A = snap.current_A;

    out->alpha     = fminf(1.0f, t_s / 3600.0f);

    int healthy = 0;
    if (flags & TELEM_FLAG_INA226_OK)  healthy++;
    if (flags & TELEM_FLAG_IMU_OK)     healthy++;
    if (flags & TELEM_FLAG_THERMAL_OK) healthy++;
    out->quality   = 25.0f * (float)healthy + 17.0f;

    out->spare     = 0.0f;

    if (out->T_N >= 3.0f && out->T_N <= 38.0f) flags |= TELEM_FLAG_TENSION_OK;
    if (out->temp_K >= 233.15f && out->temp_K <= 473.15f)
        flags |= TELEM_FLAG_TEMP_OK;
    if (out->rpm <= 260.0f) flags |= TELEM_FLAG_RPM_OK;
    float vib_rms = sqrtf(out->vib_x * out->vib_x
                       +  out->vib_y * out->vib_y
                       +  out->vib_z * out->vib_z);
    if (vib_rms <= 2.0f) flags |= TELEM_FLAG_VIBRATION_OK;

    out->flags = flags;
}

/* ───────────────────── safety snapshot (Faz 19C) ───────────────────── */

void sensor_pipeline_get_safety_snapshot(float *temp_K, float *current_A) {
    /* Volatile single-word reads — atomic on ARMv7-M. Never blocks. */
    if (temp_K)    *temp_K    = s_safe_temp_K;
    if (current_A) *current_A = s_safe_current_A;
}

/* ───────────────────────── diagnostics (for telemetry/UI) ─────────── */

void sensor_pipeline_get_health(sensor_pipeline_health_t *out) {
    if (out == NULL) return;
    cache_lock();
    out->n_snapshots         = s_cache.n_snapshots;
    out->flags_sensor_ok     = s_cache.flags_sensor_ok;
    cache_unlock();
    out->ina226_reads_ok     = s_ina.n_reads_ok;
    out->ina226_reads_fail   = s_ina.n_reads_fail;
    out->imu_reads_ok        = s_imu.n_reads_ok;
    out->imu_reads_fail      = s_imu.n_reads_fail;
    out->thermal_reads_ok    = s_therm.n_reads_ok;
    out->thermal_reads_fail  = s_therm.n_reads_fail;
}

#ifndef ESP_PLATFORM
void sensor_pipeline_host_peek_cache(float *current_A, float *bus_V,
                                      float accel[3], float *temp_K,
                                      uint16_t *flags_ok) {
    cache_lock();
    if (current_A) *current_A = s_cache.current_A;
    if (bus_V)     *bus_V     = s_cache.bus_voltage_V;
    if (accel) {
        accel[0] = s_cache.accel_g[0];
        accel[1] = s_cache.accel_g[1];
        accel[2] = s_cache.accel_g[2];
    }
    if (temp_K)    *temp_K    = s_cache.ntc_temp_K;
    if (flags_ok)  *flags_ok  = s_cache.flags_sensor_ok;
    cache_unlock();
}
#endif
