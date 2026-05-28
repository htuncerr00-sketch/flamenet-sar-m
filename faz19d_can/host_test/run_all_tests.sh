#!/usr/bin/env bash
# host_test/run_all_tests.sh — Faz 19D CAN Bridge Host Test Suite
# =================================================================
# Compiles and runs test_can_bridge against the host mock.
# No ESP32 hardware required.
#
# Run from this directory:
#   cd faz19d_can/host_test && ./run_all_tests.sh
#
# Exit: 0 = all pass, 1 = any failure or build error.
set -u
set -o pipefail

if [ -t 1 ]; then
    C_GREEN='\033[1;32m'; C_RED='\033[1;31m'
    C_BOLD='\033[1m'; C_RESET='\033[0m'
else
    C_GREEN=''; C_RED=''; C_BOLD=''; C_RESET=''
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

CAN_INC="../../faz19d_can/include"
TELEM_INC="../../faz19c_safety/include"
CAN_SRC="../../faz19d_can/main/can_bridge.c"
CFLAGS="-O2 -Wall -Wextra -std=c11 -I${CAN_INC} -I${TELEM_INC}"

echo
echo -e "${C_BOLD}================================================================${C_RESET}"
echo -e "${C_BOLD} FAZ 19D — CAN Bridge Host Test Suite${C_RESET}"
echo -e "${C_BOLD}================================================================${C_RESET}"
echo

printf "${C_BOLD}── test_can_bridge ──${C_RESET}\n"

if ! gcc $CFLAGS test_can_bridge.c "$CAN_SRC" -o test_can_bridge -lm \
       2>/tmp/test_can_bridge_build.err; then
    echo -e "${C_RED}  BUILD FAILED${C_RESET}"
    cat /tmp/test_can_bridge_build.err
    exit 1
fi

if ! ./test_can_bridge > /tmp/test_can_bridge_run.out 2>&1; then
    echo -e "${C_RED}  RUNTIME FAIL (rc != 0)${C_RESET}"
    cat /tmp/test_can_bridge_run.out
    exit 1
fi

cat /tmp/test_can_bridge_run.out

summary=$(grep -E "[0-9]+ pass, [0-9]+ fail" /tmp/test_can_bridge_run.out | tail -1)
pass=$(echo "$summary" | grep -oE "[0-9]+ pass" | grep -oE "[0-9]+")
fail=$(echo "$summary" | grep -oE "[0-9]+ fail" | grep -oE "[0-9]+")
pass=${pass:-0}; fail=${fail:-0}

echo
echo -e "${C_BOLD}================================================================${C_RESET}"
echo -e "${C_BOLD} SUMMARY${C_RESET}"
echo -e "${C_BOLD}================================================================${C_RESET}"
echo "  Total assertions:  $((pass + fail))  (24 expected)"
echo "  Passed:            $pass"
echo "  Failed:            $fail"

if [ "$fail" -eq 0 ]; then
    echo
    echo -e "  ${C_GREEN}${C_BOLD}★★★  ALL TESTS PASSED  ★★★${C_RESET}"
    exit 0
else
    echo
    echo -e "  ${C_RED}${C_BOLD}FAILED — $fail assertion(s) failed${C_RESET}"
    exit 1
fi
