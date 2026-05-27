/*
 * include/sensor_pipeline.h — Sensor Read API
 */
#ifndef FW_SENSOR_PIPELINE_H
#define FW_SENSOR_PIPELINE_H

#include <stdint.h>
#include "telemetry_frame.h"

/* Reset / initialize synthetic state. */
void sensor_pipeline_init(void);

/* Populate `out` with current physical readings.
   In Faz 19A: synthetic data.
   In Faz 19B: real INA226 / IMU / thermistor reads. */
void sensor_pipeline_read(telem_frame_t *out, uint64_t now_us, uint16_t seq);

#endif
