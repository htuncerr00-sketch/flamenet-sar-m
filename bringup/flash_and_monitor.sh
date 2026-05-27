#!/usr/bin/env bash
# bringup/flash_and_monitor.sh — Production-Grade Flash + Monitor Script
# =========================================================================
# Extends flash.sh with:
#   - Automatic idf.py build (optional, default on)
#   - ESP-IDF version verification
#   - Target chip verification
#   - Partition table verification
#   - Binary age warning (>24h since last build)
#   - Timestamped log directory
#   - Real-time serial log with tee
#   - Post-session boot-loop / Guru Meditation / brownout / watchdog detection
#
# Usage:
#   ./flash_and_monitor.sh [options] [PORT]
#
# Options:
#   --no-build       Skip automatic build step
#   --no-flash       Monitor only (device already flashed)
#   --log-dir DIR    Custom log directory (default: bringup/logs/)
#   --baud N         Monitor baud rate (default: 115200 on UART0)
#   PORT             Serial port (autodetects if omitted)
#
# Requires:
#   - ESP-IDF sourced (source /opt/esp-idf/export.sh)
#   - Python 3 with pyserial for post-analysis
#
# After flash: UART1 telemetry (921600, GPIO 17/16) on SEPARATE port.
# Run in a second terminal:
#   python bringup/verify_bringup.py --port /dev/ttyUSB1 --soak-minutes 10

set -uo pipefail

# ── script location & project root ────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FIRMWARE_DIR="${PROJECT_ROOT}/faz19b_sensors/faz19b_sensors/faz19b_sensors"
BINARY="${FIRMWARE_DIR}/build/filament_winding_telem.bin"
SDKCONFIG="${FIRMWARE_DIR}/sdkconfig"

# ── defaults ──────────────────────────────────────────────────────────
DO_BUILD=1
DO_FLASH=1
MONITOR_BAUD=115200
CUSTOM_LOG_DIR=""
PORT=""
REQUIRED_IDF_MAJOR=5
REQUIRED_IDF_MINOR=3
REQUIRED_TARGET="esp32"

# ── colours ───────────────────────────────────────────────────────────
if [ -t 1 ]; then
    G='\033[1;32m'; R='\033[1;31m'; Y='\033[1;33m'
    B='\033[1m'; CY='\033[1;36m'; Z='\033[0m'
else
    G=''; R=''; Y=''; B=''; CY=''; Z=''
fi

log_ok()   { echo -e "  ${G}✓${Z}  $*"; }
log_warn() { echo -e "  ${Y}⚠${Z}  $*"; }
log_err()  { echo -e "  ${R}✗${Z}  $*"; }
log_info() { echo -e "  ${CY}→${Z}  $*"; }

# ── argument parsing ──────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-build)   DO_BUILD=0;   shift ;;
        --no-flash)   DO_FLASH=0;   shift ;;
        --log-dir)    CUSTOM_LOG_DIR="$2"; shift 2 ;;
        --baud)       MONITOR_BAUD="$2"; shift 2 ;;
        --help|-h)
            grep '^#' "$0" | head -30 | sed 's/^# \{0,2\}//'
            exit 0 ;;
        -*)
            echo "Unknown option: $1"; exit 1 ;;
        *)
            PORT="$1"; shift ;;
    esac
done

# ── header ────────────────────────────────────────────────────────────
echo
echo -e "${B}══════════════════════════════════════════════════════════${Z}"
echo -e "${B} Filament Winding Telemetry — Flash + Monitor             ${Z}"
echo -e "${B} $(date '+%Y-%m-%d %H:%M:%S')                            ${Z}"
echo -e "${B}══════════════════════════════════════════════════════════${Z}"
echo

# ── check ESP-IDF ─────────────────────────────────────────────────────
echo -e "${B}[1/6] ESP-IDF Environment${Z}"
if [ -z "${IDF_PATH:-}" ]; then
    log_warn "IDF_PATH not set — attempting to source export.sh"
    if [ -f /opt/esp-idf/export.sh ]; then
        source /opt/esp-idf/export.sh 2>/dev/null || true
    elif [ -f "${HOME}/esp/esp-idf/export.sh" ]; then
        source "${HOME}/esp/esp-idf/export.sh" 2>/dev/null || true
    else
        log_err "Cannot find export.sh. Source it manually: source /opt/esp-idf/export.sh"
        exit 1
    fi
fi

IDF_VER_RAW=$(idf.py --version 2>/dev/null | grep -oE 'v[0-9]+\.[0-9]+' | head -1)
IDF_MAJOR=$(echo "${IDF_VER_RAW:-v0.0}" | grep -oE '[0-9]+' | head -1)
IDF_MINOR=$(echo "${IDF_VER_RAW:-v0.0}" | grep -oE '[0-9]+' | tail -1)

if [ "${IDF_MAJOR}" -ge "${REQUIRED_IDF_MAJOR}" ] && \
   [ "${IDF_MINOR}" -ge "${REQUIRED_IDF_MINOR}" ]; then
    log_ok "ESP-IDF ${IDF_VER_RAW} (required ≥ v${REQUIRED_IDF_MAJOR}.${REQUIRED_IDF_MINOR})"
else
    log_err "ESP-IDF ${IDF_VER_RAW:-unknown} is older than required v${REQUIRED_IDF_MAJOR}.${REQUIRED_IDF_MINOR}"
    echo "     Upgrade: cd /opt/esp-idf && git fetch && git checkout v5.3 && ./install.sh"
    exit 1
fi

# ── check sdkconfig target ────────────────────────────────────────────
if [ -f "${SDKCONFIG}" ]; then
    BUILT_TARGET=$(grep '^CONFIG_IDF_TARGET=' "${SDKCONFIG}" | cut -d= -f2 | tr -d '"' || echo "unknown")
    if [ "${BUILT_TARGET}" = "${REQUIRED_TARGET}" ]; then
        log_ok "Build target: ${BUILT_TARGET}"
    else
        log_err "Build target '${BUILT_TARGET}' != required '${REQUIRED_TARGET}'"
        echo "     Fix: idf.py set-target ${REQUIRED_TARGET}"
        exit 1
    fi
else
    log_warn "No sdkconfig found — run 'idf.py set-target ${REQUIRED_TARGET}' first"
fi

# ── optional build ────────────────────────────────────────────────────
echo
echo -e "${B}[2/6] Build${Z}"
if [ "${DO_BUILD}" -eq 1 ]; then
    log_info "Running idf.py build in ${FIRMWARE_DIR}"
    if ! idf.py -C "${FIRMWARE_DIR}" build 2>&1; then
        log_err "Build failed — fix errors before flashing"
        exit 1
    fi
    log_ok "Build succeeded"
else
    log_info "Build skipped (--no-build)"
fi

# ── binary checks ─────────────────────────────────────────────────────
echo
echo -e "${B}[3/6] Binary Verification${Z}"

if [ ! -f "${BINARY}" ]; then
    log_err "Binary not found: ${BINARY}"
    echo "     Run without --no-build to trigger automatic build."
    exit 1
fi

BINARY_BYTES=$(stat -c%s "${BINARY}" 2>/dev/null || stat -f%z "${BINARY}" 2>/dev/null)
BINARY_KB=$(( BINARY_BYTES / 1024 ))
log_ok "Binary: ${BINARY} (${BINARY_KB} KB)"

# Binary age check
BINARY_MTIME=$(stat -c%Y "${BINARY}" 2>/dev/null || stat -f%m "${BINARY}" 2>/dev/null)
NOW=$(date +%s)
AGE_H=$(( (NOW - BINARY_MTIME) / 3600 ))
if [ "${AGE_H}" -gt 24 ]; then
    log_warn "Binary is ${AGE_H}h old — consider rebuilding (--no-build skips this)"
else
    log_ok "Binary age: ${AGE_H}h"
fi

# Partition table check
PTABLE="${FIRMWARE_DIR}/build/partition_table/partition-table.bin"
if [ -f "${PTABLE}" ]; then
    PTABLE_KB=$(( $(stat -c%s "${PTABLE}" 2>/dev/null || echo 0) / 1024 ))
    log_ok "Partition table: ${PTABLE} (${PTABLE_KB} KB)"
else
    log_warn "Partition table binary not found — build may be incomplete"
fi

# Partition size check
APP_PARTITION_LIMIT=$((1024 * 1024))  # 1 MB typical for 'factory' app
if [ "${BINARY_BYTES}" -gt "${APP_PARTITION_LIMIT}" ]; then
    log_err "Binary ${BINARY_KB} KB exceeds app partition limit of 1024 KB"
    exit 1
fi

# ── port detection ────────────────────────────────────────────────────
echo
echo -e "${B}[4/6] Port Detection${Z}"

if [ -z "${PORT}" ]; then
    for candidate in /dev/ttyUSB0 /dev/ttyUSB1 /dev/ttyACM0 \
                     /dev/cu.usbserial-* /dev/cu.SLAB_USBtoUART; do
        if compgen -G "${candidate}" >/dev/null 2>&1; then
            PORT=$(compgen -G "${candidate}" | head -1)
            break
        fi
    done
fi

if [ -z "${PORT:-}" ]; then
    log_err "No USB-serial port found."
    echo "     Available TTY devices:"
    ls /dev/tty* 2>/dev/null | grep -E "USB|ACM|usbserial|SLAB" || echo "     (none)"
    echo "     Specify: ./flash_and_monitor.sh /dev/ttyUSB0"
    exit 1
fi

if [ ! -c "${PORT}" ] && [[ "${PORT}" != COM* ]]; then
    log_err "Port does not exist: ${PORT}"
    exit 1
fi

log_ok "UART0 monitor port: ${PORT} @ ${MONITOR_BAUD} baud"
log_info "UART1 telemetry (921600, GPIO 17/16) must be on a SEPARATE port"
log_info "Run after flash: python bringup/verify_bringup.py --port /dev/ttyUSB1"

# ── log directory ─────────────────────────────────────────────────────
echo
echo -e "${B}[5/6] Log Directory${Z}"

TS=$(date '+%Y%m%d_%H%M%S')
if [ -n "${CUSTOM_LOG_DIR}" ]; then
    LOG_DIR="${CUSTOM_LOG_DIR}"
else
    LOG_DIR="${SCRIPT_DIR}/logs/${TS}"
fi
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/monitor_${TS}.log"
ANALYSIS_FILE="${LOG_DIR}/boot_analysis_${TS}.txt"

log_ok "Log directory: ${LOG_DIR}"
log_ok "Monitor log:   ${LOG_FILE}"

# ── confirm and flash ─────────────────────────────────────────────────
echo
echo -e "${B}[6/6] Flash + Monitor${Z}"
echo
if [ "${DO_FLASH}" -eq 1 ]; then
    echo -e "  About to flash ${G}${BINARY}${Z}"
    echo -e "  to port ${G}${PORT}${Z}"
    echo
    read -rp "  Proceed? [y/N] " confirm
    if [[ ! "${confirm}" =~ ^[Yy]$ ]]; then
        echo "  Aborted."
        exit 0
    fi
    echo
    log_info "Flashing…"
    if ! idf.py -C "${FIRMWARE_DIR}" -p "${PORT}" flash; then
        log_err "Flash failed — check cable and port"
        exit 1
    fi
    log_ok "Flash complete"
    echo
fi

log_info "Starting monitor — logging to ${LOG_FILE}"
log_info "Press Ctrl-] to exit monitor"
echo
echo -e "${Y}─── UART0 BOOT LOG ─────────────────────────────────────────${Z}"

# Run monitor with tee to log file
# The `script` command forces a PTY so idf.py monitor works correctly
if command -v script >/dev/null 2>&1; then
    script -q -c "idf.py -C '${FIRMWARE_DIR}' -p '${PORT}' --baud '${MONITOR_BAUD}' monitor" \
           "${LOG_FILE}" 2>/dev/null || true
else
    idf.py -C "${FIRMWARE_DIR}" -p "${PORT}" --baud "${MONITOR_BAUD}" monitor \
        2>&1 | tee "${LOG_FILE}" || true
fi

echo
echo -e "${Y}─── END OF MONITOR SESSION ─────────────────────────────────${Z}"
echo

# ── post-session boot log analysis ───────────────────────────────────
echo -e "${B}Post-session analysis…${Z}"
{
    echo "Boot log analysis: ${LOG_FILE}"
    echo "Generated: $(date)"
    echo "======================================================"
    echo
} > "${ANALYSIS_FILE}"

# Helper: count occurrences
count_pattern() { grep -c "$1" "${LOG_FILE}" 2>/dev/null || echo 0; }
has_pattern()   { grep -q "$1" "${LOG_FILE}" 2>/dev/null; }

BOOT_LOOPS=0
GURU_MED=0
BROWNOUT=0
WDT=0
PANIC=0

# Detect boot loop: "rst:0x" appearing multiple times = repeated resets
RST_COUNT=$(grep -c 'rst:0x' "${LOG_FILE}" 2>/dev/null || echo 0)
if [ "${RST_COUNT}" -gt 2 ]; then
    BOOT_LOOPS=1
    echo -e "  ${R}BOOT LOOP DETECTED: ${RST_COUNT} reset events in log${Z}"
    echo "BOOT LOOP: ${RST_COUNT} reset events" >> "${ANALYSIS_FILE}"
fi

# Detect Guru Meditation
if has_pattern "Guru Meditation Error"; then
    GURU_MED=1
    COUNT=$(count_pattern "Guru Meditation Error")
    echo -e "  ${R}GURU MEDITATION: ${COUNT} panic(s) detected${Z}"
    grep "Guru Meditation Error" "${LOG_FILE}" | head -3 >> "${ANALYSIS_FILE}"
fi

# Detect brownout
if has_pattern -iE "brownout|BROWNOUT"; then
    BROWNOUT=1
    echo -e "  ${R}BROWNOUT detected in boot log${Z}"
    grep -i "brownout" "${LOG_FILE}" | head -3 >> "${ANALYSIS_FILE}"
fi

# Detect watchdog
if has_pattern -E "WDT|watchdog|WATCHDOG|TG0WDT|TWDT"; then
    WDT=1
    echo -e "  ${R}WATCHDOG reset detected in boot log${Z}"
    grep -E "WDT|watchdog|WATCHDOG|TG0WDT|TWDT" "${LOG_FILE}" | head -3 >> "${ANALYSIS_FILE}"
fi

# Detect panic
if has_pattern "panic"; then
    PANIC=1
    echo -e "  ${R}PANIC detected in boot log${Z}"
    grep "panic" "${LOG_FILE}" | head -3 >> "${ANALYSIS_FILE}"
fi

# Positive checks
echo >> "${ANALYSIS_FILE}"
echo "=== Positive checks ===" >> "${ANALYSIS_FILE}"

if has_pattern "Filament Winding Telemetry"; then
    log_ok "Boot banner found"
    echo "PASS: Boot banner" >> "${ANALYSIS_FILE}"
else
    log_warn "Boot banner NOT found — firmware may not have started"
    echo "FAIL: Boot banner missing" >> "${ANALYSIS_FILE}"
fi

if has_pattern "Reset reason:"; then
    REASON=$(grep "Reset reason:" "${LOG_FILE}" | tail -1)
    log_ok "Reset reason: ${REASON}"
    echo "PASS: ${REASON}" >> "${ANALYSIS_FILE}"
else
    log_warn "No reset reason logged"
    echo "WARN: No reset reason" >> "${ANALYSIS_FILE}"
fi

if has_pattern "telemetry task started"; then
    log_ok "Telemetry task started"
    echo "PASS: telemetry task started" >> "${ANALYSIS_FILE}"
else
    log_warn "Telemetry task start not confirmed"
    echo "WARN: telemetry task start not found" >> "${ANALYSIS_FILE}"
fi

if has_pattern "sensor I"; then
    log_ok "Sensor I²C task started"
    echo "PASS: sensor task started" >> "${ANALYSIS_FILE}"
else
    log_warn "Sensor I²C task start not confirmed"
    echo "WARN: sensor task start not found" >> "${ANALYSIS_FILE}"
fi

if has_pattern "heap free"; then
    HEAP_LINE=$(grep "heap free" "${LOG_FILE}" | tail -1)
    log_ok "Heap monitor: ${HEAP_LINE}"
    echo "PASS: ${HEAP_LINE}" >> "${ANALYSIS_FILE}"
else
    log_info "Heap monitor log not yet captured (appears after ~5s)"
    echo "INFO: heap monitor not in log window" >> "${ANALYSIS_FILE}"
fi

# ── final verdict ─────────────────────────────────────────────────────
echo
ANY_FATAL=$(( GURU_MED + BOOT_LOOPS ))
ANY_WARN=$(( BROWNOUT + WDT + PANIC ))

{
    echo
    echo "=== Verdict ==="
    if [ "${ANY_FATAL}" -gt 0 ]; then
        echo "STOP — UNSAFE: fatal events detected"
    elif [ "${ANY_WARN}" -gt 0 ]; then
        echo "CAUTION: warning events detected — investigate before soak"
    else
        echo "PASS: no fatal events detected in boot log"
    fi
} >> "${ANALYSIS_FILE}"

echo -e "${B}══════════════════════════════════════════════════════════${Z}"
echo -e "${B} POST-SESSION VERDICT${Z}"
echo -e "${B}══════════════════════════════════════════════════════════${Z}"

if [ "${ANY_FATAL}" -gt 0 ]; then
    echo -e "  ${R}${B}STOP — UNSAFE: fatal events detected${Z}"
    echo -e "  ${R}  See: ${ANALYSIS_FILE}${Z}"
elif [ "${ANY_WARN}" -gt 0 ]; then
    echo -e "  ${Y}${B}CAUTION: review warning events before soak test${Z}"
    echo -e "  ${Y}  See: ${ANALYSIS_FILE}${Z}"
else
    echo -e "  ${G}${B}PASS: boot log clean — proceed to verify_bringup.py${Z}"
fi

echo
echo -e "  Log file:      ${CY}${LOG_FILE}${Z}"
echo -e "  Analysis:      ${CY}${ANALYSIS_FILE}${Z}"
echo
echo -e "  Next step (UART1 telemetry, second terminal):"
echo -e "  ${CY}python bringup/verify_bringup.py --port /dev/ttyUSB1 --soak-minutes 10${Z}"
echo
