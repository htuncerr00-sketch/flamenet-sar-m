#!/usr/bin/env bash
# bringup/flash.sh — Flash filament_winding_telem.bin to ESP32
# =============================================================
# Autodetects the first available USB-serial port or accepts one
# as the first argument.  Requires ESP-IDF on PATH (source export.sh).
#
# Usage:
#   ./flash.sh                   # autodetect port
#   ./flash.sh /dev/ttyUSB0      # explicit port
#   ./flash.sh COM3              # Windows
#
# After a successful flash, press Ctrl-] to exit the monitor.
# UART1 telemetry (921600 baud, GPIO 17/16) is a SEPARATE port —
# run verify_bringup.py on that port in a second terminal.

set -euo pipefail

FIRMWARE_DIR="$(cd "$(dirname "$0")/../faz19b_sensors/faz19b_sensors/faz19b_sensors" && pwd)"
BINARY="${FIRMWARE_DIR}/build/filament_winding_telem.bin"

# ── colour ────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    G='\033[1;32m'; R='\033[1;31m'; Y='\033[1;33m'; B='\033[1m'; Z='\033[0m'
else
    G=''; R=''; Y=''; B=''; Z=''
fi

echo
echo -e "${B}══════════════════════════════════════════════════════${Z}"
echo -e "${B} Filament Winding Telemetry — First Silicon Flash     ${Z}"
echo -e "${B}══════════════════════════════════════════════════════${Z}"
echo

# ── check binary exists ───────────────────────────────────────────────
if [ ! -f "$BINARY" ]; then
    echo -e "${R}ERROR: binary not found at:${Z}"
    echo "  $BINARY"
    echo
    echo "Build first:"
    echo "  source /opt/esp-idf/export.sh"
    echo "  cd $FIRMWARE_DIR"
    echo "  idf.py build"
    exit 1
fi

BINARY_KB=$(( $(stat -c%s "$BINARY") / 1024 ))
echo -e "  Binary:  ${G}${BINARY}${Z}"
echo -e "  Size:    ${G}${BINARY_KB} KB${Z}"
echo

# ── find port ────────────────────────────────────────────────────────
if [ "${1:-}" != "" ]; then
    PORT="$1"
else
    # Autodetect: prefer ttyUSB, then ttyACM, then cu.usbserial on macOS
    PORT=""
    for candidate in /dev/ttyUSB0 /dev/ttyUSB1 /dev/ttyACM0 /dev/cu.usbserial* /dev/cu.SLAB_USBtoUART; do
        if compgen -G "$candidate" > /dev/null 2>&1; then
            PORT=$(compgen -G "$candidate" | head -1)
            break
        fi
    done
    if [ -z "$PORT" ]; then
        echo -e "${R}ERROR: No USB-serial port detected.${Z}"
        echo
        echo "Available devices:"
        ls /dev/tty* 2>/dev/null | grep -E "USB|ACM|usbserial|SLAB" || echo "  (none)"
        echo
        echo "Specify the port explicitly:  ./flash.sh /dev/ttyUSB0"
        exit 1
    fi
fi

echo -e "  Port:    ${G}${PORT}${Z}"
echo

# ── confirm ───────────────────────────────────────────────────────────
read -rp "Flash and open monitor? [y/N] " confirm
if [[ ! "${confirm}" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

# ── flash + monitor ───────────────────────────────────────────────────
echo
echo -e "${B}Flashing…${Z}"
echo

source /opt/esp-idf/export.sh 2>/dev/null || true

idf.py -C "$FIRMWARE_DIR" -p "$PORT" flash monitor

# Monitor exits with Ctrl-] (hold Ctrl, press ]).
# After exit: open a second terminal and run:
#   python bringup/verify_bringup.py --port <UART1-port> --soak-minutes 10
