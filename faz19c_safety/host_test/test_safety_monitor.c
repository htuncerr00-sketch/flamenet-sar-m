/*
 * host_test/test_safety_monitor.c — Safety Monitor Unit Tests (Faz 19C)
 * =======================================================================
 * Tests all safety_monitor paths:
 *   A. Reset reason classification from boot (5 tests)
 *   B. Reset reason string API (2 tests)
 *   C. Thermal shutdown detection (7 tests)
 *   D. Overcurrent shutdown detection (5 tests)
 *   E. Safe halt state (3 tests)
 *   F. Integration / edge cases (2 tests)
 *   Total: 24 assertions
 *
 * Build:
 *   gcc -O2 -Wall -Wextra -std=c11 -I../include \
 *       test_safety_monitor.c ../main/safety_monitor.c \
 *       -o test_safety_monitor -lm
 */
#define _POSIX_C_SOURCE 200809L
#include "safety_monitor.h"
#include <stdio.h>
#include <string.h>
#include <math.h>

/* ── Simple assertion helpers ─────────────────────────────────────── */

static int n_pass = 0;
static int n_fail = 0;

static void check_ok(const char *name, int ok) {
    printf("    [%s] %s\n", ok ? "PASS" : "FAIL", name);
    if (ok) n_pass++; else n_fail++;
}

/* ── Helper: fresh init with given reset reason ───────────────────── */

static void init_with_reason(safety_reset_reason_t r) {
    safety_monitor_host_reset();
    safety_monitor_host_set_reset_reason(r);
    safety_monitor_init();
}

/* ═══════════════════════════════════════════════════════════════════ */
/* A. Reset reason classification                                      */
/* ═══════════════════════════════════════════════════════════════════ */

static void test_reset_reason_power_on(void) {
    printf("\n  [A1] power-on reset — no error flags\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    uint16_t f = safety_monitor_get_flags();
    check_ok("reason == POWER_ON",
             safety_monitor_get_reset_reason() == SAFETY_RESET_POWER_ON);
    check_ok("no BROWNOUT flag",    !(f & TELEM_FLAG_BROWNOUT));
    check_ok("no WATCHDOG flag",    !(f & TELEM_FLAG_WATCHDOG_RESET));
    /* Only one check_ok per sub-test to keep assertion count tight */
}

static void test_reset_reason_brownout(void) {
    printf("\n  [A2] brownout reset → TELEM_FLAG_BROWNOUT\n");
    init_with_reason(SAFETY_RESET_BROWNOUT);
    uint16_t f = safety_monitor_get_flags();
    check_ok("BROWNOUT flag set",   !!(f & TELEM_FLAG_BROWNOUT));
    check_ok("no WATCHDOG flag",    !(f & TELEM_FLAG_WATCHDOG_RESET));
}

static void test_reset_reason_watchdog(void) {
    printf("\n  [A3] watchdog reset → TELEM_FLAG_WATCHDOG_RESET\n");
    init_with_reason(SAFETY_RESET_WATCHDOG);
    uint16_t f = safety_monitor_get_flags();
    check_ok("WATCHDOG flag set",   !!(f & TELEM_FLAG_WATCHDOG_RESET));
    check_ok("no BROWNOUT flag",    !(f & TELEM_FLAG_BROWNOUT));
}

static void test_reset_reason_panic(void) {
    printf("\n  [A4] panic → TELEM_FLAG_WATCHDOG_RESET (unplanned)\n");
    init_with_reason(SAFETY_RESET_PANIC);
    uint16_t f = safety_monitor_get_flags();
    check_ok("WATCHDOG flag set on panic", !!(f & TELEM_FLAG_WATCHDOG_RESET));
}

static void test_reset_reason_software(void) {
    printf("\n  [A5] software reset → no error flags\n");
    init_with_reason(SAFETY_RESET_SOFTWARE);
    uint16_t f = safety_monitor_get_flags();
    check_ok("no error flags on SW reset",
             !(f & (TELEM_FLAG_BROWNOUT | TELEM_FLAG_WATCHDOG_RESET |
                    TELEM_FLAG_THERMAL_SHUTDOWN | TELEM_FLAG_SAFE_HALT)));
}

/* ═══════════════════════════════════════════════════════════════════ */
/* B. Reset reason string                                              */
/* ═══════════════════════════════════════════════════════════════════ */

static void test_reason_string_not_null(void) {
    printf("\n  [B1] reset reason strings non-NULL\n");
    check_ok("power-on str",   safety_monitor_reset_reason_str(SAFETY_RESET_POWER_ON)  != NULL);
    check_ok("brownout str",   safety_monitor_reset_reason_str(SAFETY_RESET_BROWNOUT)  != NULL);
}

/* ═══════════════════════════════════════════════════════════════════ */
/* C. Thermal shutdown                                                 */
/* ═══════════════════════════════════════════════════════════════════ */

static void test_thermal_no_halt_below_threshold(void) {
    printf("\n  [C1] temp=295K (5 steps) → no halt\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    safety_monitor_host_inject_temp(295.15f);
    for (int i = 0; i < 5; i++) safety_monitor_host_step();
    check_ok("not halted below threshold", !safety_monitor_host_halted());
}

static void test_thermal_no_halt_strictly_below(void) {
    printf("\n  [C2] temp=373.14K (3 steps) → no halt (strictly < 373.15)\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    safety_monitor_host_inject_temp(373.14f);
    for (int i = 0; i < 3; i++) safety_monitor_host_step();
    check_ok("not halted at 373.14K", !safety_monitor_host_halted());
}

static void test_thermal_no_halt_consec_2(void) {
    printf("\n  [C3] temp=373.15K (2 steps) → no halt (consec=2 < 3)\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    safety_monitor_host_inject_temp(373.15f);
    safety_monitor_host_step();
    safety_monitor_host_step();
    check_ok("not halted after 2 consec", !safety_monitor_host_halted());
}

static void test_thermal_halt_on_third_consec(void) {
    printf("\n  [C4] temp=373.15K (3 steps) → halted\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    safety_monitor_host_inject_temp(373.15f);
    safety_monitor_host_step();
    safety_monitor_host_step();
    safety_monitor_host_step();
    check_ok("halted after 3 consec thermal", safety_monitor_host_halted());
}

static void test_thermal_halt_sets_safe_halt_flag(void) {
    printf("\n  [C5] thermal halt → SAFE_HALT flag in get_flags()\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    safety_monitor_host_inject_temp(380.0f);
    for (int i = 0; i < SAFETY_CONSEC_SHUTDOWN; i++) safety_monitor_host_step();
    uint16_t f = safety_monitor_get_flags();
    check_ok("SAFE_HALT flag set", !!(f & TELEM_FLAG_SAFE_HALT));
}

static void test_thermal_halt_sets_thermal_flag(void) {
    printf("\n  [C6] thermal halt → THERMAL_SHUTDOWN flag in get_flags()\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    safety_monitor_host_inject_temp(380.0f);
    for (int i = 0; i < SAFETY_CONSEC_SHUTDOWN; i++) safety_monitor_host_step();
    uint16_t f = safety_monitor_get_flags();
    check_ok("THERMAL_SHUTDOWN flag set", !!(f & TELEM_FLAG_THERMAL_SHUTDOWN));
}

static void test_thermal_consec_resets_on_normal(void) {
    printf("\n  [C7] 2x above, 1x normal, 3x above → halt (counter reset)\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    /* Two samples above threshold */
    safety_monitor_host_inject_temp(380.0f);
    safety_monitor_host_step();
    safety_monitor_host_step();
    check_ok("not halted after 2 (interrupted)", !safety_monitor_host_halted());
    /* One normal sample — resets consecutive counter */
    safety_monitor_host_inject_temp(295.0f);
    safety_monitor_host_step();
    /* Now three consecutive above threshold → should halt */
    safety_monitor_host_inject_temp(380.0f);
    safety_monitor_host_step();
    safety_monitor_host_step();
    safety_monitor_host_step();
    check_ok("halted after 3 fresh consec", safety_monitor_host_halted());
}

/* ═══════════════════════════════════════════════════════════════════ */
/* D. Overcurrent shutdown                                             */
/* ═══════════════════════════════════════════════════════════════════ */

static void test_oc_no_halt_below_threshold(void) {
    printf("\n  [D1] current=40A (5 steps) → no halt (< 45A)\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    safety_monitor_host_inject_current(40.0f);
    for (int i = 0; i < 5; i++) safety_monitor_host_step();
    check_ok("not halted at 40A", !safety_monitor_host_halted());
}

static void test_oc_no_halt_strictly_below(void) {
    printf("\n  [D2] current=44.9A (3 steps) → no halt\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    safety_monitor_host_inject_current(44.9f);
    for (int i = 0; i < 3; i++) safety_monitor_host_step();
    check_ok("not halted at 44.9A", !safety_monitor_host_halted());
}

static void test_oc_no_halt_consec_2(void) {
    printf("\n  [D3] current=45.0A (2 steps) → no halt\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    safety_monitor_host_inject_current(45.0f);
    safety_monitor_host_step();
    safety_monitor_host_step();
    check_ok("not halted after 2 consec OC", !safety_monitor_host_halted());
}

static void test_oc_halt_on_third_consec(void) {
    printf("\n  [D4] current=45.0A (3 steps) → halted\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    safety_monitor_host_inject_current(45.0f);
    for (int i = 0; i < SAFETY_CONSEC_SHUTDOWN; i++) safety_monitor_host_step();
    check_ok("halted after 3 consec OC", safety_monitor_host_halted());
}

static void test_oc_halt_sets_safe_halt_flag(void) {
    printf("\n  [D5] OC halt → SAFE_HALT flag\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    safety_monitor_host_inject_current(50.0f);
    for (int i = 0; i < SAFETY_CONSEC_SHUTDOWN; i++) safety_monitor_host_step();
    uint16_t f = safety_monitor_get_flags();
    check_ok("SAFE_HALT flag after OC", !!(f & TELEM_FLAG_SAFE_HALT));
}

/* ═══════════════════════════════════════════════════════════════════ */
/* E. Safe halt state                                                  */
/* ═══════════════════════════════════════════════════════════════════ */

static void test_halt_not_triggered_initially(void) {
    printf("\n  [E1] fresh init → not halted\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    check_ok("halted() == 0 after init", !safety_monitor_host_halted());
}

static void test_halt_triggered_after_violation(void) {
    printf("\n  [E2] 3x thermal violation → halted() == 1\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    safety_monitor_host_inject_temp(400.0f);
    for (int i = 0; i < 3; i++) safety_monitor_host_step();
    check_ok("halted() == 1 after violation", safety_monitor_host_halted() == 1);
}

static void test_halt_idempotent(void) {
    printf("\n  [E3] double halt — flags do not change\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    safety_monitor_host_inject_temp(400.0f);
    for (int i = 0; i < 3; i++) safety_monitor_host_step();
    uint16_t f1 = safety_monitor_get_flags();
    /* Extra steps after halt should not alter flags */
    safety_monitor_host_step();
    safety_monitor_host_step();
    uint16_t f2 = safety_monitor_get_flags();
    check_ok("flags unchanged after second halt attempt", f1 == f2);
}

/* ═══════════════════════════════════════════════════════════════════ */
/* F. Integration / edge cases                                         */
/* ═══════════════════════════════════════════════════════════════════ */

static void test_flags_zero_on_nominal_power_on(void) {
    printf("\n  [F1] fresh power-on, no steps → no error flags\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    uint16_t f = safety_monitor_get_flags();
    check_ok("no BROWNOUT/WATCHDOG/THERMAL/SAFE_HALT on fresh init",
             !(f & (TELEM_FLAG_BROWNOUT | TELEM_FLAG_WATCHDOG_RESET |
                    TELEM_FLAG_THERMAL_SHUTDOWN | TELEM_FLAG_SAFE_HALT)));
}

static void test_threshold_boundary(void) {
    printf("\n  [F2] threshold boundary: 373.15K triggers, 373.14K does not\n");
    init_with_reason(SAFETY_RESET_POWER_ON);
    safety_monitor_host_inject_temp(373.14f);
    for (int i = 0; i < 10; i++) safety_monitor_host_step();
    int no_halt = !safety_monitor_host_halted();

    init_with_reason(SAFETY_RESET_POWER_ON);
    safety_monitor_host_inject_temp(373.15f);
    for (int i = 0; i < SAFETY_CONSEC_SHUTDOWN; i++) safety_monitor_host_step();
    int yes_halt = safety_monitor_host_halted();

    check_ok("373.14K never halts, 373.15K halts", no_halt && yes_halt);
}

/* ════════════════════════════════════════════════════════════════════ */

int main(void) {
    printf("\n=== Faz 19C safety_monitor unit tests ===\n");

    /* A. Reset reason classification */
    test_reset_reason_power_on();
    test_reset_reason_brownout();
    test_reset_reason_watchdog();
    test_reset_reason_panic();
    test_reset_reason_software();

    /* B. String API */
    test_reason_string_not_null();

    /* C. Thermal detection */
    test_thermal_no_halt_below_threshold();
    test_thermal_no_halt_strictly_below();
    test_thermal_no_halt_consec_2();
    test_thermal_halt_on_third_consec();
    test_thermal_halt_sets_safe_halt_flag();
    test_thermal_halt_sets_thermal_flag();
    test_thermal_consec_resets_on_normal();

    /* D. Overcurrent detection */
    test_oc_no_halt_below_threshold();
    test_oc_no_halt_strictly_below();
    test_oc_no_halt_consec_2();
    test_oc_halt_on_third_consec();
    test_oc_halt_sets_safe_halt_flag();

    /* E. Safe halt */
    test_halt_not_triggered_initially();
    test_halt_triggered_after_violation();
    test_halt_idempotent();

    /* F. Integration */
    test_flags_zero_on_nominal_power_on();
    test_threshold_boundary();

    printf("\n=== %d pass, %d fail ===\n", n_pass, n_fail);
    return (n_fail == 0) ? 0 : 1;
}
