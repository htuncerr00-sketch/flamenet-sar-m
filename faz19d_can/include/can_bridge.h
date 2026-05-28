/*
 * include/can_bridge.h — CAN/TWAI ESC Bridge (Faz 19D)
 * ======================================================
 *
 * Responsibilities:
 *   1. Initialise ESP32 TWAI peripheral (CAN 2.0B, 500 kbit/s default)
 *   2. Send periodic ESC heartbeat frames (throttle + direction)
 *   3. Receive ESC status frames (actual RPM, current limit, fault code)
 *   4. Expose a lock-free cache of the latest ESC status
 *   5. Propagate ESC fault → F_ESC_FAULT flag bit (bit 15 of telemetry flags)
 *   6. Propagate ESC actual RPM → wire field rpm (replaces hardcoded 0.0f)
 *
 * CAN frame IDs:
 *   0x100  ESC_CMD   — host → ESC  (throttle, direction, enable)
 *   0x101  ESC_STATUS — ESC → host (actual_rpm, dc_current_A, fault_code)
 *
 * Frame format (both 8-byte data):
 *   ESC_CMD   [0..1] throttle_pct × 100 (int16, -10000..10000)
 *             [2]    direction: 0=FWD 1=REV
 *             [3]    enable:    0=off  1=on
 *             [4..7] reserved (0x00)
 *
 *   ESC_STATUS [0..1] actual_rpm  (uint16, 0..65535)
 *              [2..3] dc_current_A × 10 (int16, signed)
 *              [4]    fault_code (0=OK)
 *              [5..7] reserved
 *
 * TWAI pin assignment (default, override via can_bridge_config_t):
 *   TX: GPIO 4
 *   RX: GPIO 5
 *   (120 Ω termination resistor required between CAN-H and CAN-L)
 *
 * Host build:
 *   TWAI driver replaced by in-process loopback via can_host_mock.
 *   All tests run without real CAN hardware.
 *
 * Thread safety:
 *   can_bridge_get_status() — lock-free volatile struct read (atomic on Xtensa)
 *   can_bridge_set_throttle() — same, lock-free volatile write
 *   TWAI ISR — handled by ESP-IDF driver; no application-level ISR
 *
 * Safety invariants:
 *   - On ESC fault (fault_code != 0) for 3 consecutive frames → set F_ESC_FAULT
 *   - F_ESC_FAULT is cleared only on recovery (3 consecutive clean frames)
 *   - If no ESC_STATUS received for > 500 ms → ESC_FAULT (comms timeout)
 *   - can_bridge_task() NEVER holds a mutex during status callbacks
 */
#ifndef FW_CAN_BRIDGE_H
#define FW_CAN_BRIDGE_H

#include <stdint.h>
#include "telemetry_frame.h"

#ifdef ESP_PLATFORM
#  include "freertos/FreeRTOS.h"
#  include "freertos/task.h"
#endif

/* ── CAN frame IDs ─────────────────────────────────────────────────── */
#define CAN_ID_ESC_CMD    0x100u
#define CAN_ID_ESC_STATUS 0x101u

/* ── Timing ────────────────────────────────────────────────────────── */
#define CAN_HEARTBEAT_MS       10     /* send ESC_CMD every 10 ms   */
#define CAN_STATUS_TIMEOUT_MS 500     /* no reply → ESC fault       */
#define CAN_FAULT_CONSEC        3     /* consecutive faults → flag  */
#define CAN_RECOVER_CONSEC      3     /* consecutive clean → clear  */

/* ── ESC command (written by motion controller) ────────────────────── */
typedef struct {
    int16_t  throttle_pct100;   /* -10000..10000 (× 0.01 %)        */
    uint8_t  direction;         /* 0=FWD 1=REV                     */
    uint8_t  enable;            /* 0=off 1=on                      */
} esc_command_t;

/* ── ESC status (filled by CAN receive path) ───────────────────────── */
typedef struct {
    uint16_t actual_rpm;        /* 0..65535                        */
    int16_t  dc_current_x10;   /* actual current × 10 (A × 10)   */
    uint8_t  fault_code;        /* 0 = OK                         */
    uint8_t  _pad[3];
} esc_status_t;

/* ── Configuration ─────────────────────────────────────────────────── */
typedef struct {
    int      tx_gpio;           /* default: 4                      */
    int      rx_gpio;           /* default: 5                      */
    uint32_t bitrate;           /* default: 500000                 */
} can_bridge_config_t;

#define CAN_BRIDGE_CONFIG_DEFAULT() { .tx_gpio = 4, .rx_gpio = 5, .bitrate = 500000 }

/* ── Public API ─────────────────────────────────────────────────────── */

/* Init TWAI driver. Call from app_main before can_bridge_start(). */
int can_bridge_init(const can_bridge_config_t *cfg);

/* Create CAN bridge FreeRTOS task (10 ms loop, prio 8). */
#ifdef ESP_PLATFORM
BaseType_t can_bridge_start(void);
#else
int can_bridge_start(void);
#endif

/* Set desired throttle + direction. Lock-free volatile write. */
void can_bridge_set_command(const esc_command_t *cmd);

/* Get latest ESC status. Lock-free volatile struct read. */
void can_bridge_get_status(esc_status_t *out);

/* Get ESC fault flag bits for merging into telemetry flags. */
uint16_t can_bridge_get_flags(void);

/* Get ESC actual_rpm as float (for wire field rpm). */
float can_bridge_get_rpm(void);

/* Get ESC current as float A (for blending with INA226 if needed). */
float can_bridge_get_current_a(void);

/* ── Host-only test helpers ─────────────────────────────────────────── */
#ifndef ESP_PLATFORM

/* Inject an ESC_STATUS frame as if received from the bus. */
void can_host_inject_status(uint16_t rpm, int16_t current_x10, uint8_t fault);

/* Return the last ESC_CMD frame that was "sent" to the bus. */
void can_host_get_last_cmd(esc_command_t *out);

/* Advance simulated time by ms milliseconds (for timeout tests). */
void can_host_advance_time_ms(uint32_t ms);

/* Query and reset internal CAN bridge state for test isolation. */
void can_host_reset(void);

#endif /* !ESP_PLATFORM */

#endif /* FW_CAN_BRIDGE_H */
