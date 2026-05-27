/*
 * main/thermal.c — NTC + Steinhart-Hart Implementation
 * =========================================================
 * Math is portable between ESP32 and host. Only the ADC read path
 * differs (esp_adc_cal vs synthetic value).
 */
#include "thermal.h"
#include <math.h>
#include <stddef.h>

#ifdef ESP_PLATFORM
#include "esp_adc_cal.h"
#include "driver/adc.h"
#include "esp_log.h"
static const char *TAG = "thermal";
static esp_adc_cal_characteristics_t s_adc_chars;
static int s_adc_chars_ready = 0;
#endif

/* ───────────────────────── pure math (portable) ──────────────────── */

float thermal_adc_mv_to_resistance(float adc_mv,
                                    const thermal_calib_t *cal) {
    if (cal == NULL) return 0.0f;
    float v_supply_mv = cal->V_supply_V * 1000.0f;
    /* Guard against div-by-zero when ADC saturated near rail */
    if (adc_mv <= 1.0f) return 1e9f;            /* very cold → huge R */
    if (adc_mv >= v_supply_mv - 1.0f) return 0.0f;   /* shorted → tiny R */
    /* R_ntc = R_pullup × V_adc / (V_supply - V_adc) */
    return cal->R_pullup_ohm * adc_mv / (v_supply_mv - adc_mv);
}

float thermal_resistance_to_kelvin(float r_ohm,
                                    const thermal_calib_t *cal) {
    if (cal == NULL || r_ohm <= 0.0f || cal->R0_ohm <= 0.0f) return 0.0f;
    /* 1/T = 1/T0 + (1/B) × ln(R/R0) */
    float inv_T = 1.0f / cal->T0_K
                + (1.0f / cal->beta_K) * logf(r_ohm / cal->R0_ohm);
    if (inv_T <= 0.0f) return 0.0f;
    return 1.0f / inv_T;
}

/* ───────────────────────── ADC read (platform-specific) ─────────── */

#ifdef ESP_PLATFORM

static thermal_err_t adc_read_mv(int channel, float *out_mv) {
    if (!s_adc_chars_ready) return ESP_ERR_INVALID_STATE;
    int raw = adc1_get_raw((adc1_channel_t)channel);
    if (raw < 0) return ESP_FAIL;
    uint32_t mv = esp_adc_cal_raw_to_voltage((uint32_t)raw, &s_adc_chars);
    *out_mv = (float)mv;
    return ESP_OK;
}

#else

static float s_host_adc_mv = 1650.0f;   /* synthetic; 1.65 V default */
static int s_host_fault_n = 0;

void thermal_host_set_adc_mv(float mv) { s_host_adc_mv = mv; }
void thermal_host_inject_fault(int n)  { s_host_fault_n = n; }

static thermal_err_t adc_read_mv(int channel, float *out_mv) {
    (void)channel;
    if (s_host_fault_n > 0) { s_host_fault_n--; return -1; }
    *out_mv = s_host_adc_mv;
    return THERMAL_OK;
}

#endif

/* ───────────────────────── public API ────────────────────────────── */

thermal_err_t thermal_init(thermal_t *dev,
                           int adc_channel,
                           const thermal_calib_t *cal_or_null) {
    if (dev == NULL) return (thermal_err_t)-1;
    thermal_calib_t default_cal = THERMAL_CALIB_DEFAULT;
    dev->cal = (cal_or_null != NULL) ? *cal_or_null : default_cal;
    dev->adc_channel = adc_channel;
    dev->initialized = false;
    dev->last_read_ok = false;
    dev->temp_K = 0.0f;
    dev->n_reads_ok = 0;
    dev->n_reads_fail = 0;

#ifdef ESP_PLATFORM
    /* Configure ADC1 once globally */
    if (!s_adc_chars_ready) {
        adc1_config_width(ADC_WIDTH_BIT_12);
        esp_adc_cal_characterize(ADC_UNIT_1,
                                 ADC_ATTEN_DB_11,
                                 ADC_WIDTH_BIT_12,
                                 1100,  /* default Vref */
                                 &s_adc_chars);
        s_adc_chars_ready = 1;
    }
    /* Per-channel attenuation */
    adc1_config_channel_atten((adc1_channel_t)adc_channel, ADC_ATTEN_DB_11);
#endif

    dev->initialized = true;
    return THERMAL_OK;
}

thermal_err_t thermal_read(thermal_t *dev) {
    if (dev == NULL || !dev->initialized) return (thermal_err_t)-1;

    float mv = 0.0f;
    thermal_err_t err = adc_read_mv(dev->adc_channel, &mv);
    if (err != THERMAL_OK) {
        dev->last_read_ok = false;
        dev->n_reads_fail++;
        return err;
    }

    float r = thermal_adc_mv_to_resistance(mv, &dev->cal);
    float t = thermal_resistance_to_kelvin(r, &dev->cal);

    /* Sanity check: any reading outside physically plausible range
       (< -40°C / > 200°C) is treated as a sensor fault */
    if (t < 233.15f || t > 473.15f) {
        dev->last_read_ok = false;
        dev->n_reads_fail++;
        return (thermal_err_t)-2;
    }

    dev->temp_K = t;
    dev->last_read_ok = true;
    dev->n_reads_ok++;
    return THERMAL_OK;
}
