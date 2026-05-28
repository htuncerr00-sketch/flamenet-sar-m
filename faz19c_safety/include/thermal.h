/*
 * include/thermal.h — NTC Thermistor Driver
 * ===============================================
 * Reads an NTC thermistor via ESP32 ADC1 and converts to Kelvin
 * using the Steinhart-Hart equation.
 *
 * Circuit assumption:
 *   3.3 V ── R_pullup ── ADC_in ── NTC ── GND
 *
 *   so V_adc = 3.3 × R_ntc / (R_pullup + R_ntc)
 *   → R_ntc  = R_pullup × V_adc / (3.3 - V_adc)
 *
 * Default values match a typical 10 kΩ NTC @ 25°C with B = 3950:
 *   R_pullup = 10 kΩ
 *   R0       = 10 kΩ (NTC resistance at T0)
 *   T0       = 298.15 K (25°C)
 *   beta     = 3950 K
 *
 * Steinhart-Hart (simplified Beta form):
 *   1/T = 1/T0 + (1/beta) × ln(R/R0)
 *   T   = 1 / (1/T0 + (1/beta) × ln(R/R0))
 *
 * Override params at init for different thermistors (e.g. Vishay
 * NTCLE413E2103, EPCOS B57164, etc.).
 *
 * ADC notes:
 *   ESP32 ADC is 12-bit (0..4095) with attenuation. We assume the
 *   ADC is configured for 11dB attenuation (0..3.3 V range).
 *   In production the driver reads esp_adc_cal-calibrated mV directly.
 *
 * Host build:
 *   Returns a controllable synthetic ADC value (via
 *   thermal_host_set_adc_mv()) so tests run without ADC hardware.
 */
#ifndef FW_THERMAL_H
#define FW_THERMAL_H

#include <stdint.h>
#include <stdbool.h>

#ifdef ESP_PLATFORM
#include "esp_err.h"
typedef esp_err_t thermal_err_t;
#define THERMAL_OK ESP_OK
#else
typedef int thermal_err_t;
#define THERMAL_OK 0
#endif

/* Steinhart-Hart Beta-form parameters */
typedef struct {
    float R_pullup_ohm;     /* Series resistor to 3V3 */
    float R0_ohm;           /* NTC resistance at T0 */
    float T0_K;             /* Reference temperature in Kelvin (25°C = 298.15) */
    float beta_K;           /* Beta coefficient (typical 3380..4500) */
    float V_supply_V;       /* Supply voltage (typ 3.3) */
} thermal_calib_t;

#define THERMAL_CALIB_DEFAULT \
    { .R_pullup_ohm = 10000.0f, .R0_ohm = 10000.0f, \
      .T0_K = 298.15f, .beta_K = 3950.0f, .V_supply_V = 3.3f }

typedef struct {
    bool             initialized;
    thermal_calib_t  cal;
    int              adc_channel;     /* ESP32: ADC1_CHANNEL_x; host: ignored */
    float            temp_K;           /* Last-known-good temperature */
    uint32_t         n_reads_ok;
    uint32_t         n_reads_fail;
    bool             last_read_ok;
} thermal_t;

/* Init the thermal driver. Configures the ADC (on ESP32). */
thermal_err_t thermal_init(thermal_t *dev,
                           int adc_channel,
                           const thermal_calib_t *cal_or_null);

/* Read one sample. Updates dev->temp_K on success.
   Returns THERMAL_OK on success, last-known-good preserved on failure. */
thermal_err_t thermal_read(thermal_t *dev);

/* Pure-math helpers, exposed for unit testing */
float thermal_adc_mv_to_resistance(float adc_mv,
                                    const thermal_calib_t *cal);
float thermal_resistance_to_kelvin(float r_ohm,
                                    const thermal_calib_t *cal);

#ifndef ESP_PLATFORM
/* Host build only: inject a synthetic ADC reading (mV).
   Used by tests to simulate a temperature without hardware. */
void thermal_host_set_adc_mv(float mv);
void thermal_host_inject_fault(int n_reads);
#endif

#endif
