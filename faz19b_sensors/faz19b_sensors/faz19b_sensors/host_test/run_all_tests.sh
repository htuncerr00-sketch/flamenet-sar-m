#!/usr/bin/env bash
# host_test/run_all_tests.sh — Faz 19B Toplu Regression Runner
# ===============================================================
# Compiles every host-side test binary, runs each, parses the
# "N pass, M fail" summary line, totals across all suites.
#
# Behavior:
#   - fail-fast: ilk fail'da çıkar (set -e)
#   - colored PASS/FAIL summary
#   - toplam assertion sayısı (pass + fail)
#   - toplam wall-clock süre
#
# Exit: 0 if all pass, 1 if any fail / build error.
set -u
set -o pipefail

# Colors (disable if not a tty)
if [ -t 1 ]; then
    C_GREEN='\033[1;32m'
    C_RED='\033[1;31m'
    C_YELLOW='\033[1;33m'
    C_BOLD='\033[1m'
    C_RESET='\033[0m'
else
    C_GREEN=''; C_RED=''; C_YELLOW=''; C_BOLD=''; C_RESET=''
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Common compile flags
CFLAGS="-O2 -Wall -Wextra -std=c11 -I../include -I."
LIBS="-lm -lpthread"

# Source aggregation: which .c files each test needs.
# Each entry: "testname:test_src.c extra1.c extra2.c ..."
TESTS=(
    "test_ina226:test_ina226.c i2c_host_mock.c ../main/i2c_bus.c ../main/ina226.c"
    "test_thermal:test_thermal.c ../main/thermal.c"
    "test_mpu6050:test_mpu6050.c i2c_host_mock.c ../main/i2c_bus.c ../main/mpu6050.c"
    "test_sensor_pipeline:test_sensor_pipeline.c i2c_host_mock.c ../main/i2c_bus.c ../main/ina226.c ../main/mpu6050.c ../main/thermal.c ../main/sensor_pipeline.c ../main/telemetry_protocol.c"
    "test_race_cache:test_race_cache.c i2c_host_mock.c ../main/i2c_bus.c ../main/ina226.c ../main/mpu6050.c ../main/thermal.c ../main/sensor_pipeline.c ../main/telemetry_protocol.c"
)

START_TIME=$(date +%s)
TOTAL_PASS=0
TOTAL_FAIL=0
FAILED_SUITES=()

echo
echo -e "${C_BOLD}==============================================================${C_RESET}"
echo -e "${C_BOLD} FAZ 19B — Tam Regression Suite${C_RESET}"
echo -e "${C_BOLD}==============================================================${C_RESET}"

for entry in "${TESTS[@]}"; do
    name="${entry%%:*}"
    sources="${entry#*:}"
    printf "\n${C_BOLD}── %s ──${C_RESET}\n" "$name"

    # Compile
    suite_t0=$(date +%s%N)
    if ! gcc $CFLAGS $sources -o "$name" $LIBS 2>/tmp/${name}_build.err; then
        echo -e "${C_RED}  BUILD FAILED${C_RESET}"
        cat /tmp/${name}_build.err
        FAILED_SUITES+=("$name (build)")
        TOTAL_FAIL=$((TOTAL_FAIL + 1))
        break  # fail-fast
    fi

    # Run, capture output
    if ! ./"$name" > /tmp/${name}_run.out 2>&1; then
        echo -e "${C_RED}  RUNTIME FAIL (rc != 0)${C_RESET}"
        cat /tmp/${name}_run.out
        FAILED_SUITES+=("$name")
        # Try to extract pass/fail counts anyway
    fi

    # Parse final "=== N pass, M fail ===" line
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

    # fail-fast
    if [ "$fail" -gt 0 ]; then break; fi
done

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo
echo -e "${C_BOLD}==============================================================${C_RESET}"
echo -e "${C_BOLD} SUMMARY${C_RESET}"
echo -e "${C_BOLD}==============================================================${C_RESET}"
TOTAL_ASSERT=$((TOTAL_PASS + TOTAL_FAIL))
echo "  Total assertions:  $TOTAL_ASSERT"
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
