/*
 * include/sensor_pipeline.h — Sensor Aggregation API (Faz 19B)
 * =================================================================
 * Two-task model:
 *   - sensor_i2c_task @ 100 Hz calls sensor_pipeline_step() to drain
 *     all I²C sensors and update a thread-safe cache.
 *   - telemetry_task @ 1 kHz calls sensor_pipeline_read() to snapshot
 *     the cache into a wire frame.
 *
 * This decouples sensor sample rate (100 Hz) from telemetry rate (1 kHz)
 * without doing 1 kHz I²C (which would saturate the bus and starve
 * other sensors).
 */
#ifndef FW_SENSOR_PIPELINE_H
#define FW_SENSOR_PIPELINE_H

#include <stdint.h>
#include "telemetry_frame.h"

/* Initialize bus + all sensor drivers. Best-effort: failed sensors
   stay offline but the pipeline keeps running. */
void sensor_pipeline_init(void);

/* Drain all sensors and update cache. Called from sensor_i2c_task @ 100 Hz.
   Safe to call from a single thread only (sensor I/O serialized). */
void sensor_pipeline_step(void);

/* Snapshot cache → wire frame. Thread-safe. Called from telemetry_task @ 1 kHz.
   Does NOT touch I²C bus. */
void sensor_pipeline_read(telem_frame_t *out, uint64_t now_us, uint16_t seq);

/* Health snapshot for diagnostics / commissioning panel */
typedef struct {
    uint32_t  n_snapshots;
    uint16_t  flags_sensor_ok;
    uint32_t  ina226_reads_ok,  ina226_reads_fail;
    uint32_t  imu_reads_ok,     imu_reads_fail;
    uint32_t  thermal_reads_ok, thermal_reads_fail;
} sensor_pipeline_health_t;

void sensor_pipeline_get_health(sensor_pipeline_health_t *out);

#ifndef ESP_PLATFORM
/* Test-only: read internal cache directly (no wire encoding step). */
void sensor_pipeline_host_peek_cache(float *current_A, float *bus_V,
                                      float accel[3], float *temp_K,
                                      uint16_t *flags_ok);
#endif

#endif
