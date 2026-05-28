#!/usr/bin/env bash
# host_test/run_all_tests.sh — Faz 19C Toplu Regression Suite
# ==============================================================
# Runs all Faz 19B sensor suites plus the new Faz 19C safety_monitor
# suite. Total assertions: 108 (19B) + 24 (19C) = 132.
#
# Expects to be run from the faz19c_safety/host_test/ directory.
# Source files for 19B suites are resolved via relative paths to the
# sibling faz19b_sensors inner tree.
#
# Exit: 0 all pass, 1 any fail or build error.
set -u
set -o pipefail

# Colors
if [ -t 1 ]; then
    C_GREEN='\033[1;32m'; C_RED='\033[1;31m'
    C_YELLOW='\033[1;33m'; C_BOLD='\033[1m'; C_RESET='\033[0m'
else
    C_GREEN=''; C_RED=''; C_YELLOW=''; C_BOLD=''; C_RESET=''
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Canonical source roots
FAZ19B="../../faz19b_sensors/faz19b_sensors/faz19b_sensors"
FAZ19C_INC="../include"
FAZ19C_MAIN="../main"

CFLAGS="-O2 -Wall -Wextra -std=c11 -I${FAZ19C_INC} -I${FAZ19B}/include -I${FAZ19B}/host_test"
LIBS="-lm -lpthread"

# Each entry: "name:test.c  src1.c  src2.c ..."
TESTS=(
    # ── Faz 19B suites (unchanged) ──
    "test_ina226:\
${FAZ19B}/host_test/test_ina226.c \
${FAZ19B}/host_test/i2c_host_mock.c \
${FAZ19B}/main/i2c_bus.c \
${FAZ19B}/main/ina226.c"

    "test_thermal:\
${FAZ19B}/host_test/test_thermal.c \
${FAZ19B}/main/thermal.c"

    "test_mpu6050:\
${FAZ19B}/host_test/test_mpu6050.c \
${FAZ19B}/host_test/i2c_host_mock.c \
${FAZ19B}/main/i2c_bus.c \
${FAZ19B}/main/mpu6050.c"

    "test_sensor_pipeline:\
${FAZ19B}/host_test/test_sensor_pipeline.c \
${FAZ19B}/host_test/i2c_host_mock.c \
${FAZ19B}/main/i2c_bus.c \
${FAZ19B}/main/ina226.c \
${FAZ19B}/main/mpu6050.c \
${FAZ19B}/main/thermal.c \
${FAZ19C_MAIN}/sensor_pipeline.c \
${FAZ19C_MAIN}/can_bridge.c \
${FAZ19B}/main/telemetry_protocol.c"

    "test_race_cache:\
${FAZ19B}/host_test/test_race_cache.c \
${FAZ19B}/host_test/i2c_host_mock.c \
${FAZ19B}/main/i2c_bus.c \
${FAZ19B}/main/ina226.c \
${FAZ19B}/main/mpu6050.c \
${FAZ19B}/main/thermal.c \
${FAZ19C_MAIN}/sensor_pipeline.c \
${FAZ19C_MAIN}/can_bridge.c \
${FAZ19B}/main/telemetry_protocol.c"

    # ── Faz 19C safety_monitor suite ──
    "test_safety_monitor:\
test_safety_monitor.c \
${FAZ19C_MAIN}/safety_monitor.c"
)

START_TIME=$(date +%s)
TOTAL_PASS=0
TOTAL_FAIL=0
FAILED_SUITES=()

echo
echo -e "${C_BOLD}================================================================${C_RESET}"
echo -e "${C_BOLD} FAZ 19C — Tam Regression Suite (19B + 19C safety_monitor)${C_RESET}"
echo -e "${C_BOLD}================================================================${C_RESET}"

for entry in "${TESTS[@]}"; do
    name="${entry%%:*}"
    sources="${entry#*:}"
    printf "\n${C_BOLD}── %s ──${C_RESET}\n" "$name"

    suite_t0=$(date +%s%N)
    if ! gcc $CFLAGS $sources -o "$name" $LIBS 2>/tmp/${name}_build.err; then
        echo -e "${C_RED}  BUILD FAILED${C_RESET}"
        cat /tmp/${name}_build.err
        FAILED_SUITES+=("$name (build)")
        TOTAL_FAIL=$((TOTAL_FAIL + 1))
        break
    fi

    if ! ./"$name" > /tmp/${name}_run.out 2>&1; then
        echo -e "${C_RED}  RUNTIME FAIL (rc != 0)${C_RESET}"
        cat /tmp/${name}_run.out
        FAILED_SUITES+=("$name")
    fi

    summary=$(grep -E "[0-9]+ pass, [0-9]+ fail" /tmp/${name}_run.out | tail -1)
    if [ -z "$summary" ]; then
        echo -e "${C_RED}  No summary line found${C_RESET}"
        cat /tmp/${name}_run.out | tail -20
        FAILED_SUITES+=("$name (no summary)")
        TOTAL_FAIL=$((TOTAL_FAIL + 1))
        break
    fi

    pass=$(echo "$summary" | grep -oE "[0-9]+ pass" | grep -oE "[0-9]+")
    fail=$(echo "$summary" | grep -oE "[0-9]+ fail" | grep -oE "[0-9]+")
    pass=${pass:-0}; fail=${fail:-0}

    suite_t1=$(date +%s%N)
    suite_ms=$(( (suite_t1 - suite_t0) / 1000000 ))

    if [ "$fail" -eq 0 ]; then
        printf "  ${C_GREEN}%s${C_RESET}  ${C_BOLD}%d pass${C_RESET}  (%d ms)\n" \
               "PASS" "$pass" "$suite_ms"
    else
        printf "  ${C_RED}%s${C_RESET}  ${C_BOLD}%d pass / %d fail${C_RESET}  (%d ms)\n" \
               "FAIL" "$pass" "$fail" "$suite_ms"
        echo "  --- output tail ---"
        tail -15 /tmp/${name}_run.out
        FAILED_SUITES+=("$name")
    fi

    TOTAL_PASS=$((TOTAL_PASS + pass))
    TOTAL_FAIL=$((TOTAL_FAIL + fail))

    if [ "$fail" -gt 0 ]; then break; fi
done

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo
echo -e "${C_BOLD}================================================================${C_RESET}"
echo -e "${C_BOLD} SUMMARY${C_RESET}"
echo -e "${C_BOLD}================================================================${C_RESET}"
TOTAL_ASSERT=$((TOTAL_PASS + TOTAL_FAIL))
echo "  Total assertions:  $TOTAL_ASSERT  (108 from 19B + 24 from 19C = 132 expected)"
echo "  Passed:            $TOTAL_PASS"
echo "  Failed:            $TOTAL_FAIL"
echo "  Wall-clock time:   ${ELAPSED} s"

if [ "$TOTAL_FAIL" -eq 0 ] && [ "${#FAILED_SUITES[@]}" -eq 0 ]; then
    echo
    echo -e "  ${C_GREEN}${C_BOLD}★★★  ALL TESTS PASSED  ★★★${C_RESET}"
    exit 0
else
    echo
    echo -e "  ${C_RED}${C_BOLD}FAILED SUITES:${C_RESET}"
    for s in "${FAILED_SUITES[@]}"; do
        echo -e "    ${C_RED}✗  $s${C_RESET}"
    done
    exit 1
fi
