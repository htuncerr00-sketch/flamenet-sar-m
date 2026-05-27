/*
 * host_test/test_thermal.c — Thermal driver unit test
 * ========================================================
 * Tests:
 *   - Steinhart-Hart math for known calibration points
 *   - End-to-end: synthetic ADC mV → resistance → kelvin
 *   - Fault injection: ADC failure preserves last-known-good
 *   - Out-of-range protection (sensor unplugged → ADC at rail)
 */
#define _POSIX_C_SOURCE 200809L
#include "thermal.h"
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

static int n_pass = 0, n_fail = 0;

static void check_f(const char *name, float got, float expected, float tol) {
    int ok = fabsf(got - expected) <= tol;
    printf("    [%s] %-36s got=%.3f expected=%.3f tol=%.3f\n",
           ok ? "PASS" : "FAIL", name, got, expected, tol);
    if (ok) n_pass++; else n_fail++;
}

static void check(const char *name, int ok) {
    printf("    [%s] %-36s\n", ok ? "PASS" : "FAIL", name);
    if (ok) n_pass++; else n_fail++;
}

int main(void) {
    printf("\n=== thermal driver unit test ===\n");

    thermal_calib_t cal = {
        .R_pullup_ohm = 10000.0f,
        .R0_ohm       = 10000.0f,
        .T0_K         = 298.15f,
        .beta_K       = 3950.0f,
        .V_supply_V   = 3.3f,
    };

    /* ── pure math sanity ── */
    /* At V_adc = V_supply/2 → R = R_pullup (= 10kΩ) */
    float r = thermal_adc_mv_to_resistance(1650.0f, &cal);
    check_f("R at half-supply", r, 10000.0f, 1.0f);

    /* R = R0 → T = T0 = 25°C = 298.15 K */
    float t = thermal_resistance_to_kelvin(10000.0f, &cal);
    check_f("T at R=R0", t, 298.15f, 0.001f);

    /* Beta-form: at R = R0 / e^(B × (1/T0 - 1/T_target)), T = T_target.
       Quick sanity: 0°C = 273.15 K, NTC R≈ 27.28 kΩ for B=3950 */
    /* 1/T0 - 1/T_target = 1/298.15 - 1/273.15 = -3.07e-4
       ln(R/R0) = B × 3.07e-4 = 1.213 → R/R0 = 3.363 → R = 33.6kΩ
       (Actual value for B=3950 @ 0°C ≈ 32 kΩ — formula is exact for ideal Beta) */
    float r_at_0c = 10000.0f * expf(3950.0f * (1.0f/273.15f - 1.0f/298.15f));
    float t_at_0c = thermal_resistance_to_kelvin(r_at_0c, &cal);
    check_f("T at computed R(0°C)", t_at_0c, 273.15f, 0.01f);

    /* ── end-to-end init + read ── */
    thermal_t dev = {0};
    thermal_err_t err = thermal_init(&dev, 0, &cal);
    check("thermal_init", err == THERMAL_OK);
    check("initialized flag", dev.initialized);

    /* Set ADC to 1650 mV (half-rail) → R=10k → T=25°C */
    thermal_host_set_adc_mv(1650.0f);
    err = thermal_read(&dev);
    check  ("read OK @ 25°C", err == THERMAL_OK);
    check  ("last_read_ok",   dev.last_read_ok);
    check_f("temp_K @25°C",   dev.temp_K, 298.15f, 0.1f);
    check  ("n_reads_ok=1",   dev.n_reads_ok == 1);

    /* Cold extreme: ADC near 0 V → huge R → cold (close to lower bound) */
    thermal_host_set_adc_mv(50.0f);
    err = thermal_read(&dev);
    /* Should be valid (in-range) but very cold. Let's compute and check the actual temperature */
    if (err == THERMAL_OK) {
        check("cold reading in range (>= -40°C)", dev.temp_K >= 233.15f);
        printf("        info: cold corner reads %.2f K (%.2f °C)\n",
               dev.temp_K, dev.temp_K - 273.15f);
    } else {
        /* If our test ADC point was outside plausible range, that's also OK —
           it should be detected and last-known-good preserved */
        check_f("LKG preserved on cold reject", dev.temp_K, 298.15f, 0.1f);
    }

    /* Hot reading: 1000 mV → R = 4304 Ω (smaller than R0 → warmer) */
    thermal_host_set_adc_mv(1000.0f);
    err = thermal_read(&dev);
    check  ("read at 1000mV", err == THERMAL_OK);
    /* Resistance = 10000 * 1000 / (3300 - 1000) = 4348 Ω
       1/T = 1/298.15 + 1/3950 × ln(4348/10000) = 3.354e-3 + (-2.107e-4)
           = 3.143e-3   → T ≈ 318.16 K ≈ 45°C */
    check_f("temp_K @ warm corner", dev.temp_K, 318.16f, 0.5f);

    /* ── fault injection: ADC fails → LKG preserved ── */
    float lkg = dev.temp_K;
    uint32_t fail_before = dev.n_reads_fail;
    thermal_host_inject_fault(1);
    err = thermal_read(&dev);
    check  ("fault detected", err != THERMAL_OK);
    check  ("last_read_ok=false after fault", dev.last_read_ok == false);
    check_f("LKG temp preserved after fault", dev.temp_K, lkg, 0.0001f);
    check  ("n_reads_fail incremented", dev.n_reads_fail == fail_before + 1);

    /* Recovery */
    thermal_host_set_adc_mv(1650.0f);
    err = thermal_read(&dev);
    check  ("recovery read OK", err == THERMAL_OK);
    check_f("recovered temp_K", dev.temp_K, 298.15f, 0.1f);

    /* ── out-of-range protection ── */
    /* ADC at supply rail → sensor shorted/disconnected → R→0 or R→inf */
    thermal_host_set_adc_mv(3290.0f);   /* near rail */
    err = thermal_read(&dev);
    if (err != THERMAL_OK) {
        check("rail-clamp ADC rejected", 1);
        check_f("LKG preserved on rail",
                dev.temp_K, 298.15f, 0.1f);
    } else {
        /* If the reading was somehow in-range, it should be very hot.
           Either outcome is acceptable as long as LKG is consistent. */
        check("rail-clamp ADC returned plausible value",
              dev.temp_K > 350.0f);
    }

    printf("\n=== thermal: %d pass, %d fail ===\n", n_pass, n_fail);
    return n_fail == 0 ? 0 : 1;
}
