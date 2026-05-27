/*
 * main/sensor_pipeline.c — Sensor Read & Pack
 * =================================================
 * In Faz 19A there are NO real sensors yet. This module generates
 * synthetic data deterministically so the PC side sees the exact
 * stream it expects.
 *
 * In Faz 19B real sensors take over — only this file changes; the
 * telemetry_task / uart_stream stay identical.
 *
 * Source separation:
 *   sensor_pipeline_read() fills a telem_frame_t with logical values
 *   in physical units. Whether the source is a synthetic generator
 *   or a real INA226+MPU6050+thermistor is hidden here.
 */
#include "sensor_pipeline.h"
#include "telemetry_frame.h"
#include <math.h>

/*
 * Simple deterministic generator that mirrors MockESP32Link in PC code,
 * so the firmware-vs-mock stream is bit-identical for replay logs.
 *
 * NOTE: this uses small-state LCG so a re-flash with same boot time
 * gives same sequence — useful for soak tests. Real-sensor variant
 * will replace this.
 */

static uint32_t s_lcg = 1u;

static uint32_t lcg_next(void) {
    s_lcg = s_lcg * 1664525u + 1013904223u;
    return s_lcg;
}

static float lcg_uniform(float lo, float hi) {
    float u = (float)(lcg_next() & 0xFFFFFFu) / (float)0xFFFFFFu;
    return lo + u * (hi - lo);
}

void sensor_pipeline_init(void) {
    s_lcg = 1u;
}

void sensor_pipeline_read(telem_frame_t *out, uint64_t now_us, uint16_t seq) {
    if (out == NULL) return;

    /* Time-based pseudo-physics so the chart shows nice motion */
    float t_s = (float)((double)now_us * 1e-6);
    float x   = 40.0f + 150.0f * (1.0f + sinf(t_s * 0.5f)) * 0.5f;
    float a   = fmodf(t_s * 30.0f, 360.0f);    /* ~5 RPM @ 30 deg/s */

    out->ts_us     = now_us;
    out->seq       = seq;
    out->flags     = (uint16_t)(TELEM_FLAG_BOOT_OK
                              | TELEM_FLAG_TENSION_OK
                              | TELEM_FLAG_TEMP_OK
                              | TELEM_FLAG_RPM_OK
                              | TELEM_FLAG_VIBRATION_OK);
    out->x_mm      = x;
    out->a_deg     = a;
    out->T_N       = 15.0f + lcg_uniform(-0.3f, 0.3f);
    out->rpm       = 5.0f;
    out->vib_x     = lcg_uniform(-0.05f, 0.05f);
    out->vib_y     = lcg_uniform(-0.05f, 0.05f);
    out->vib_z     = lcg_uniform(-0.05f, 0.05f);
    out->temp_K    = 295.15f + 0.1f * t_s;
    out->current_A = 2.5f + lcg_uniform(-0.1f, 0.1f);
    out->alpha     = fminf(1.0f, t_s / 3600.0f);
    out->quality   = 92.0f + lcg_uniform(-1.0f, 1.0f);
    out->spare     = 0.0f;
}
