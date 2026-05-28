/*
 * host_test/test_can_bridge.c — Faz 19D CAN/TWAI ESC bridge host unit tests
 * ===========================================================================
 *
 * 24 assertions covering:
 *   T01-T02  init / null-config guard
 *   T03-T06  ESC_CMD throttle encoding (positive, negative, zero, boundary)
 *   T07-T09  ESC_STATUS decode (RPM, current positive, current negative)
 *   T10-T12  fault detection gating (1, 2, 3 consecutive fault frames)
 *   T13-T14  recovery gating (partial = 2 samples, full = 3 samples)
 *   T15-T16  comms timeout (>500 ms triggers, exactly 500 ms does not)
 *   T17      fault during recovery resets recovery counter
 *   T18      get_rpm / get_current_a return 0 before any status received
 *   T19-T20  RPM edge values (0, 65535)
 *   T21      direction REV encoding
 *   T22      can_host_reset clears fault state
 *   T23      10 clean frames never set F_ESC_FAULT
 *   T24      F_ESC_FAULT is exactly bit 15
 *
 * Compile:
 *   cd faz19d_can/host_test
 *   gcc -O2 -Wall -Wextra -std=c11 \
 *       -I../include -I../../faz19c_safety/include \
 *       test_can_bridge.c ../main/can_bridge.c \
 *       -o test_can_bridge -lm && ./test_can_bridge
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <math.h>

#include "can_bridge.h"

static int s_pass = 0;
static int s_fail = 0;

/* ── assertion helpers ──────────────────────────────────────────────── */

#define ASSERT_EQ_INT(a, b, msg) do {                                        \
    long long _a = (long long)(a), _b = (long long)(b);                      \
    if (_a == _b) { printf("  PASS  %s\n", msg); s_pass++; }                 \
    else { printf("  FAIL  %s  (got %lld  expected %lld)\n",                 \
                  msg, _a, _b); s_fail++; }                                  \
} while(0)

#define ASSERT_NE_INT(a, b, msg) do {                                        \
    long long _a = (long long)(a), _b = (long long)(b);                      \
    if (_a != _b) { printf("  PASS  %s\n", msg); s_pass++; }                 \
    else { printf("  FAIL  %s  (expected != %lld)\n", msg, _b); s_fail++; }  \
} while(0)

#define ASSERT_NEAR(a, b, eps, msg) do {                                     \
    float _diff = (float)(a) - (float)(b);                                   \
    if (_diff < 0.0f) _diff = -_diff;                                        \
    if (_diff <= (float)(eps)) { printf("  PASS  %s\n", msg); s_pass++; }    \
    else { printf("  FAIL  %s  (got %.4f  expected %.4f)\n",                 \
                  msg, (float)(a), (float)(b)); s_fail++; }                  \
} while(0)

/* ── test fixture ───────────────────────────────────────────────────── */

static void reset_and_init(void) {
    can_host_reset();
    can_bridge_config_t cfg = CAN_BRIDGE_CONFIG_DEFAULT();
    can_bridge_init(&cfg);
}

/* ══════════════════════════════════════════════════════════════════════
 * T01 — init with default config returns 0
 * ════════════════════════════════════════════════════════════════════ */
static void test_init(void) {
    printf("[T01] Init with default config\n");
    can_host_reset();
    can_bridge_config_t cfg = CAN_BRIDGE_CONFIG_DEFAULT();
    int rc = can_bridge_init(&cfg);
    ASSERT_EQ_INT(rc, 0,                        "init() returns 0");
    ASSERT_EQ_INT(can_bridge_get_flags(), 0,     "no fault flags after init");
}

/* ══════════════════════════════════════════════════════════════════════
 * T02 — init(NULL) is rejected
 * ════════════════════════════════════════════════════════════════════ */
static void test_init_null(void) {
    printf("[T02] Init with NULL config returns error\n");
    can_host_reset();
    int rc = can_bridge_init(NULL);
    ASSERT_NE_INT(rc, 0, "init(NULL) returns non-zero");
}

/* ══════════════════════════════════════════════════════════════════════
 * T03 — throttle encoding: positive value
 * ════════════════════════════════════════════════════════════════════ */
static void test_throttle_positive(void) {
    printf("[T03] Throttle encoding — positive (5000 = 50%%)\n");
    reset_and_init();
    esc_command_t cmd = { .throttle_pct100 = 5000, .direction = 0, .enable = 1 };
    can_bridge_set_command(&cmd);
    can_host_advance_time_ms(CAN_HEARTBEAT_MS);
    esc_command_t got;
    can_host_get_last_cmd(&got);
    ASSERT_EQ_INT(got.throttle_pct100, 5000, "throttle +5000 round-trips");
    ASSERT_EQ_INT(got.direction,          0, "direction FWD=0");
    ASSERT_EQ_INT(got.enable,             1, "enable=1");
}

/* ══════════════════════════════════════════════════════════════════════
 * T04 — throttle encoding: negative (regenerative braking)
 * ════════════════════════════════════════════════════════════════════ */
static void test_throttle_negative(void) {
    printf("[T04] Throttle encoding — negative (-3000 regen)\n");
    reset_and_init();
    esc_command_t cmd = { .throttle_pct100 = -3000, .direction = 0, .enable = 1 };
    can_bridge_set_command(&cmd);
    can_host_advance_time_ms(CAN_HEARTBEAT_MS);
    esc_command_t got;
    can_host_get_last_cmd(&got);
    ASSERT_EQ_INT(got.throttle_pct100, -3000, "throttle -3000 round-trips");
}

/* ══════════════════════════════════════════════════════════════════════
 * T05 — throttle encoding: zero / disabled
 * ════════════════════════════════════════════════════════════════════ */
static void test_throttle_zero(void) {
    printf("[T05] Throttle encoding — zero, enable=0\n");
    reset_and_init();
    esc_command_t cmd = { .throttle_pct100 = 0, .direction = 0, .enable = 0 };
    can_bridge_set_command(&cmd);
    can_host_advance_time_ms(CAN_HEARTBEAT_MS);
    esc_command_t got;
    can_host_get_last_cmd(&got);
    ASSERT_EQ_INT(got.throttle_pct100, 0, "throttle 0 round-trips");
    ASSERT_EQ_INT(got.enable,          0, "enable=0 round-trips");
}

/* ══════════════════════════════════════════════════════════════════════
 * T06 — throttle boundary values ±10000
 * ════════════════════════════════════════════════════════════════════ */
static void test_throttle_boundary(void) {
    printf("[T06] Throttle boundary values (±10000)\n");
    reset_and_init();
    esc_command_t cmd = { .throttle_pct100 = 10000, .direction = 0, .enable = 1 };
    can_bridge_set_command(&cmd);
    can_host_advance_time_ms(CAN_HEARTBEAT_MS);
    esc_command_t got;
    can_host_get_last_cmd(&got);
    ASSERT_EQ_INT(got.throttle_pct100, 10000, "throttle +10000 (max)");

    cmd.throttle_pct100 = -10000;
    can_bridge_set_command(&cmd);
    can_host_advance_time_ms(CAN_HEARTBEAT_MS);
    can_host_get_last_cmd(&got);
    ASSERT_EQ_INT(got.throttle_pct100, -10000, "throttle -10000 (min)");
}

/* ══════════════════════════════════════════════════════════════════════
 * T07 — ESC_STATUS decode: RPM field
 * ════════════════════════════════════════════════════════════════════ */
static void test_status_rpm(void) {
    printf("[T07] ESC_STATUS decode — RPM=1234\n");
    reset_and_init();
    can_host_inject_status(1234, 0, 0);
    esc_status_t st;
    can_bridge_get_status(&st);
    ASSERT_EQ_INT(st.actual_rpm,           1234,   "actual_rpm=1234 decoded");
    ASSERT_NEAR(can_bridge_get_rpm(), 1234.0f, 0.01f, "get_rpm()=1234.0");
}

/* ══════════════════════════════════════════════════════════════════════
 * T08 — ESC_STATUS decode: current positive (200 × 0.1 = 20.0 A)
 * ════════════════════════════════════════════════════════════════════ */
static void test_status_current_pos(void) {
    printf("[T08] ESC_STATUS decode — current +200 (20.0 A)\n");
    reset_and_init();
    can_host_inject_status(0, 200, 0);
    ASSERT_NEAR(can_bridge_get_current_a(), 20.0f, 0.01f, "get_current_a()=20.0");
}

/* ══════════════════════════════════════════════════════════════════════
 * T09 — ESC_STATUS decode: current negative (regen, -50 × 0.1 = -5.0 A)
 * ════════════════════════════════════════════════════════════════════ */
static void test_status_current_neg(void) {
    printf("[T09] ESC_STATUS decode — current -50 (-5.0 A regen)\n");
    reset_and_init();
    can_host_inject_status(0, -50, 0);
    ASSERT_NEAR(can_bridge_get_current_a(), -5.0f, 0.01f, "get_current_a()=-5.0");
}

/* ══════════════════════════════════════════════════════════════════════
 * T10 — fault gating: 1 fault sample → NO flag
 * ════════════════════════════════════════════════════════════════════ */
static void test_fault_1_sample(void) {
    printf("[T10] Fault gating — 1 fault sample, flag NOT set\n");
    reset_and_init();
    can_host_inject_status(100, 0, 1);
    ASSERT_EQ_INT(can_bridge_get_flags() & TELEM_FLAG_ESC_FAULT, 0,
                  "F_ESC_FAULT clear after 1 fault");
}

/* ══════════════════════════════════════════════════════════════════════
 * T11 — fault gating: 2 consecutive faults → NO flag
 * ════════════════════════════════════════════════════════════════════ */
static void test_fault_2_samples(void) {
    printf("[T11] Fault gating — 2 consecutive faults, flag NOT set\n");
    reset_and_init();
    can_host_inject_status(100, 0, 1);
    can_host_inject_status(100, 0, 1);
    ASSERT_EQ_INT(can_bridge_get_flags() & TELEM_FLAG_ESC_FAULT, 0,
                  "F_ESC_FAULT clear after 2 faults");
}

/* ══════════════════════════════════════════════════════════════════════
 * T12 — fault gating: 3 consecutive faults → F_ESC_FAULT SET
 * ════════════════════════════════════════════════════════════════════ */
static void test_fault_3_samples(void) {
    printf("[T12] Fault gating — 3 consecutive faults → F_ESC_FAULT SET\n");
    reset_and_init();
    can_host_inject_status(100, 0, 1);
    can_host_inject_status(100, 0, 1);
    can_host_inject_status(100, 0, 1);
    ASSERT_NE_INT(can_bridge_get_flags() & TELEM_FLAG_ESC_FAULT, 0,
                  "F_ESC_FAULT SET after 3 consecutive faults");
}

/* ══════════════════════════════════════════════════════════════════════
 * T13 — recovery gating: 2 clean frames — flag stays SET
 * ════════════════════════════════════════════════════════════════════ */
static void test_recovery_partial(void) {
    printf("[T13] Recovery gating — 2 clean samples, flag stays SET\n");
    reset_and_init();
    can_host_inject_status(100, 0, 1);
    can_host_inject_status(100, 0, 1);
    can_host_inject_status(100, 0, 1);   /* fault active */
    can_host_inject_status(200, 50, 0);  /* clean #1 */
    can_host_inject_status(200, 50, 0);  /* clean #2 */
    ASSERT_NE_INT(can_bridge_get_flags() & TELEM_FLAG_ESC_FAULT, 0,
                  "F_ESC_FAULT still SET after only 2 clean samples");
}

/* ══════════════════════════════════════════════════════════════════════
 * T14 — recovery gating: 3 clean frames → F_ESC_FAULT CLEARED
 * ════════════════════════════════════════════════════════════════════ */
static void test_recovery_full(void) {
    printf("[T14] Recovery gating — 3 clean samples → F_ESC_FAULT CLEARED\n");
    reset_and_init();
    can_host_inject_status(100, 0, 1);
    can_host_inject_status(100, 0, 1);
    can_host_inject_status(100, 0, 1);   /* fault active */
    can_host_inject_status(200, 50, 0);
    can_host_inject_status(200, 50, 0);
    can_host_inject_status(200, 50, 0);  /* 3 clean → recover */
    ASSERT_EQ_INT(can_bridge_get_flags() & TELEM_FLAG_ESC_FAULT, 0,
                  "F_ESC_FAULT CLEARED after 3 clean samples");
    ASSERT_NEAR(can_bridge_get_rpm(), 200.0f, 0.01f,
                "rpm updated to post-recovery value");
}

/* ══════════════════════════════════════════════════════════════════════
 * T15 — comms timeout: no status for > 500 ms → F_ESC_FAULT SET
 * ════════════════════════════════════════════════════════════════════ */
static void test_comms_timeout(void) {
    printf("[T15] Comms timeout — no status for >%d ms → fault\n",
           CAN_STATUS_TIMEOUT_MS);
    reset_and_init();
    /* Seed one valid status so s_last_status_ms != 0 */
    can_host_inject_status(100, 0, 0);
    ASSERT_EQ_INT(can_bridge_get_flags() & TELEM_FLAG_ESC_FAULT, 0,
                  "no fault immediately after status");
    /* Advance past the timeout: needs > 500 ms elapsed since last status.
       inject already advanced 10 ms; 51 more steps = +510 ms → total 520 ms
       elapsed from last_status(10ms): 520-10=510 > 500 → timeout */
    can_host_advance_time_ms(CAN_STATUS_TIMEOUT_MS + CAN_HEARTBEAT_MS);
    ASSERT_NE_INT(can_bridge_get_flags() & TELEM_FLAG_ESC_FAULT, 0,
                  "F_ESC_FAULT SET after comms timeout");
}

/* ══════════════════════════════════════════════════════════════════════
 * T16 — comms boundary: exactly 500 ms elapsed — NO fault
 *        _check_timeout uses strict > so 500 ms is still safe.
 * ════════════════════════════════════════════════════════════════════ */
static void test_comms_boundary(void) {
    printf("[T16] Comms boundary — exactly %d ms elapsed, no fault\n",
           CAN_STATUS_TIMEOUT_MS);
    reset_and_init();
    can_host_inject_status(100, 0, 0);
    /* inject ran 1 step (sim=10ms, last_status=10ms).
       advancing 500ms (50 steps) → sim=510ms.
       Check: 510-10=500, 500 > 500 is FALSE → no fault. */
    can_host_advance_time_ms(CAN_STATUS_TIMEOUT_MS);
    ASSERT_EQ_INT(can_bridge_get_flags() & TELEM_FLAG_ESC_FAULT, 0,
                  "F_ESC_FAULT NOT set at exactly 500 ms (boundary)");
}

/* ══════════════════════════════════════════════════════════════════════
 * T17 — fault during recovery resets recovery counter
 * ════════════════════════════════════════════════════════════════════ */
static void test_fault_resets_recovery(void) {
    printf("[T17] Fault during recovery resets recovery counter\n");
    reset_and_init();
    /* Trigger fault */
    can_host_inject_status(0, 0, 5);
    can_host_inject_status(0, 0, 5);
    can_host_inject_status(0, 0, 5);
    /* Partial recovery (2 clean) */
    can_host_inject_status(0, 0, 0);
    can_host_inject_status(0, 0, 0);
    /* Fault re-occurs — resets recovery counter */
    can_host_inject_status(0, 0, 2);
    /* 2 more clean = total 2 in new sequence, not 4 */
    can_host_inject_status(0, 0, 0);
    can_host_inject_status(0, 0, 0);
    ASSERT_NE_INT(can_bridge_get_flags() & TELEM_FLAG_ESC_FAULT, 0,
                  "F_ESC_FAULT still SET — recovery counter was reset");
}

/* ══════════════════════════════════════════════════════════════════════
 * T18 — get_rpm / get_current_a return 0 before any status injected
 * ════════════════════════════════════════════════════════════════════ */
static void test_get_rpm_initial(void) {
    printf("[T18] Initial state — get_rpm() and get_current_a() return 0\n");
    can_host_reset();
    can_bridge_config_t cfg = CAN_BRIDGE_CONFIG_DEFAULT();
    can_bridge_init(&cfg);
    ASSERT_NEAR(can_bridge_get_rpm(),       0.0f, 0.01f, "initial get_rpm()=0");
    ASSERT_NEAR(can_bridge_get_current_a(), 0.0f, 0.01f, "initial get_current_a()=0");
}

/* ══════════════════════════════════════════════════════════════════════
 * T19 — RPM = 0 (ESC stopped)
 * ════════════════════════════════════════════════════════════════════ */
static void test_rpm_zero(void) {
    printf("[T19] RPM=0 (ESC stopped)\n");
    reset_and_init();
    can_host_inject_status(0, 0, 0);
    ASSERT_NEAR(can_bridge_get_rpm(), 0.0f, 0.01f, "get_rpm()=0.0 for stopped ESC");
}

/* ══════════════════════════════════════════════════════════════════════
 * T20 — RPM = 65535 (uint16 max)
 * ════════════════════════════════════════════════════════════════════ */
static void test_rpm_max(void) {
    printf("[T20] RPM=65535 (uint16 max)\n");
    reset_and_init();
    can_host_inject_status(65535, 0, 0);
    ASSERT_NEAR(can_bridge_get_rpm(), 65535.0f, 1.0f, "get_rpm()=65535.0");
}

/* ══════════════════════════════════════════════════════════════════════
 * T21 — direction REV encoding
 * ════════════════════════════════════════════════════════════════════ */
static void test_direction_rev(void) {
    printf("[T21] Direction REV (direction=1) encoding\n");
    reset_and_init();
    esc_command_t cmd = { .throttle_pct100 = 2000, .direction = 1, .enable = 1 };
    can_bridge_set_command(&cmd);
    can_host_advance_time_ms(CAN_HEARTBEAT_MS);
    esc_command_t got;
    can_host_get_last_cmd(&got);
    ASSERT_EQ_INT(got.direction,          1,    "direction REV=1 preserved");
    ASSERT_EQ_INT(got.throttle_pct100,  2000,   "throttle preserved with REV");
}

/* ══════════════════════════════════════════════════════════════════════
 * T22 — can_host_reset clears fault and status state
 * ════════════════════════════════════════════════════════════════════ */
static void test_reset_clears_fault(void) {
    printf("[T22] can_host_reset() clears fault state\n");
    reset_and_init();
    can_host_inject_status(0, 0, 7);
    can_host_inject_status(0, 0, 7);
    can_host_inject_status(0, 0, 7);
    ASSERT_NE_INT(can_bridge_get_flags() & TELEM_FLAG_ESC_FAULT, 0,
                  "fault set before reset");
    can_host_reset();
    ASSERT_EQ_INT(can_bridge_get_flags() & TELEM_FLAG_ESC_FAULT, 0,
                  "fault cleared after reset");
    ASSERT_NEAR(can_bridge_get_rpm(), 0.0f, 0.01f, "rpm cleared after reset");
}

/* ══════════════════════════════════════════════════════════════════════
 * T23 — 10 clean frames never trigger F_ESC_FAULT
 * ════════════════════════════════════════════════════════════════════ */
static void test_clean_frames_no_fault(void) {
    printf("[T23] 10 clean frames — F_ESC_FAULT never asserted\n");
    reset_and_init();
    for (int i = 0; i < 10; i++) {
        can_host_inject_status(1000, 100, 0);
    }
    ASSERT_EQ_INT(can_bridge_get_flags() & TELEM_FLAG_ESC_FAULT, 0,
                  "10 clean frames produce no fault");
}

/* ══════════════════════════════════════════════════════════════════════
 * T24 — F_ESC_FAULT occupies exactly bit 15
 * ════════════════════════════════════════════════════════════════════ */
static void test_esc_fault_bit_position(void) {
    printf("[T24] F_ESC_FAULT occupies bit 15 (0x8000)\n");
    reset_and_init();
    can_host_inject_status(0, 0, 1);
    can_host_inject_status(0, 0, 1);
    can_host_inject_status(0, 0, 1);
    uint16_t f = can_bridge_get_flags();
    ASSERT_NE_INT(f & TELEM_FLAG_ESC_FAULT,   0,      "F_ESC_FAULT bit is set");
    ASSERT_EQ_INT(f & TELEM_FLAG_ESC_FAULT, 0x8000u,  "F_ESC_FAULT = bit 15 (0x8000)");
}

/* ── main ─────────────────────────────────────────────────────────── */

int main(void) {
    printf("=== test_can_bridge (Faz 19D host suite) ===\n\n");

    test_init();
    test_init_null();
    test_throttle_positive();
    test_throttle_negative();
    test_throttle_zero();
    test_throttle_boundary();
    test_status_rpm();
    test_status_current_pos();
    test_status_current_neg();
    test_fault_1_sample();
    test_fault_2_samples();
    test_fault_3_samples();
    test_recovery_partial();
    test_recovery_full();
    test_comms_timeout();
    test_comms_boundary();
    test_fault_resets_recovery();
    test_get_rpm_initial();
    test_rpm_zero();
    test_rpm_max();
    test_direction_rev();
    test_reset_clears_fault();
    test_clean_frames_no_fault();
    test_esc_fault_bit_position();

    printf("\n--- %d pass, %d fail ---\n", s_pass, s_fail);
    return s_fail > 0 ? 1 : 0;
}
