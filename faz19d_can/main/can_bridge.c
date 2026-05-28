/*
 * main/can_bridge.c — CAN/TWAI ESC Bridge Implementation (Faz 19D)
 * ==================================================================
 *
 * Design:
 *   - can_bridge_task() runs at 10 ms (prio 8, Core 1).
 *   - Every 10 ms: encode esc_command → ESC_CMD frame → twai_transmit().
 *   - Receive loop (non-blocking poll): drain RX queue, decode ESC_STATUS frames.
 *   - Fault detection: 3 consecutive fault_code != 0 OR 500 ms no reply.
 *   - Status cache: volatile esc_status_t (single struct, ≤ 32 bytes,
 *     Xtensa LX6 single-word reads are atomic — safe for lock-free access
 *     on fields ≤ 4 bytes; uint16_t actual_rpm is atomic).
 *
 * Host build (no ESP32):
 *   TWAI replaced by in-process ring buffer + can_host_inject_status().
 *   All timing uses a software counter advanced by can_host_advance_time_ms().
 */
#include "can_bridge.h"

#include <string.h>
#include <stdint.h>
#include <stdio.h>

#ifdef ESP_PLATFORM
#  include "driver/twai.h"
#  include "esp_log.h"
#  include "freertos/FreeRTOS.h"
#  include "freertos/task.h"
#  include "esp_timer.h"
   static const char *TAG = "can_bridge";
#  define LOG_I(fmt, ...)  ESP_LOGI(TAG, fmt, ##__VA_ARGS__)
#  define LOG_W(fmt, ...)  ESP_LOGW(TAG, fmt, ##__VA_ARGS__)
#  define LOG_E(fmt, ...)  ESP_LOGE(TAG, fmt, ##__VA_ARGS__)
#else
#  define LOG_I(fmt, ...)  printf("[can_bridge] " fmt "\n", ##__VA_ARGS__)
#  define LOG_W(fmt, ...)  printf("[can_bridge WARN] " fmt "\n", ##__VA_ARGS__)
#  define LOG_E(fmt, ...)  printf("[can_bridge ERR] " fmt "\n", ##__VA_ARGS__)
#endif

/* ── Static state ───────────────────────────────────────────────────── */

static volatile esc_command_t s_cmd;
static volatile esc_status_t  s_status;
static volatile uint16_t      s_flags         = 0;
static volatile int           s_initialised   = 0;
static volatile int           s_fault_consec  = 0;
static volatile int           s_recover_consec = 0;

/* ── Time abstraction ────────────────────────────────────────────────── */

#ifdef ESP_PLATFORM
static inline uint32_t _now_ms(void) {
    return (uint32_t)(esp_timer_get_time() / 1000ULL);
}
#else
static uint32_t s_sim_time_ms = 0;
static inline uint32_t _now_ms(void) { return s_sim_time_ms; }
#endif

static uint32_t s_last_status_ms = 0;

/* ── Frame encode / decode ───────────────────────────────────────────── */

static void _encode_cmd(const volatile esc_command_t *cmd, uint8_t data[8]) {
    int16_t th = cmd->throttle_pct100;
    data[0] = (uint8_t)(th >> 8);
    data[1] = (uint8_t)(th & 0xFF);
    data[2] = cmd->direction;
    data[3] = cmd->enable;
    data[4] = 0; data[5] = 0; data[6] = 0; data[7] = 0;
}

static void _decode_status(const uint8_t data[8], volatile esc_status_t *st) {
    st->actual_rpm    = (uint16_t)((data[0] << 8) | data[1]);
    st->dc_current_x10 = (int16_t)((data[2] << 8) | data[3]);
    st->fault_code    = data[4];
}

/* ── Fault / recovery state machine ─────────────────────────────────── */

static void _update_fault_state(uint8_t fault_code) {
    if (fault_code != 0) {
        s_recover_consec = 0;
        s_fault_consec++;
        if (s_fault_consec >= CAN_FAULT_CONSEC) {
            if (!(s_flags & TELEM_FLAG_ESC_FAULT)) {
                s_flags |= TELEM_FLAG_ESC_FAULT;
                LOG_W("ESC fault: code=%u — F_ESC_FAULT SET", fault_code);
            }
        }
    } else {
        s_fault_consec = 0;
        s_recover_consec++;
        if (s_recover_consec >= CAN_RECOVER_CONSEC) {
            if (s_flags & TELEM_FLAG_ESC_FAULT) {
                s_flags &= (uint16_t)~TELEM_FLAG_ESC_FAULT;
                LOG_I("ESC recovered — F_ESC_FAULT cleared");
            }
        }
    }
}

static void _check_timeout(uint32_t now_ms) {
    if (s_last_status_ms == 0) return;   /* not yet received any status */
    if ((now_ms - s_last_status_ms) > CAN_STATUS_TIMEOUT_MS) {
        if (!(s_flags & TELEM_FLAG_ESC_FAULT)) {
            s_flags |= TELEM_FLAG_ESC_FAULT;
            LOG_W("ESC comms timeout (> %d ms) — F_ESC_FAULT SET",
                  CAN_STATUS_TIMEOUT_MS);
        }
    }
}

/* ── TWAI / host-mock send ───────────────────────────────────────────── */

#ifdef ESP_PLATFORM

static void _send_cmd_frame(void) {
    uint8_t data[8];
    _encode_cmd(&s_cmd, data);
    twai_message_t msg = {
        .identifier = CAN_ID_ESC_CMD,
        .data_length_code = 8,
        .flags = 0,
    };
    memcpy(msg.data, data, 8);
    esp_err_t err = twai_transmit(&msg, 0);  /* non-blocking */
    if (err != ESP_OK && err != ESP_ERR_TIMEOUT) {
        LOG_W("twai_transmit failed: %d", err);
    }
}

static void _poll_rx(void) {
    twai_message_t msg;
    while (twai_receive(&msg, 0) == ESP_OK) {
        if (msg.identifier == CAN_ID_ESC_STATUS && msg.data_length_code == 8) {
            _decode_status(msg.data, &s_status);
            _update_fault_state(s_status.fault_code);
            s_last_status_ms = _now_ms();
        }
    }
}

#else  /* HOST BUILD */

/* In-process ring buffer mimics the CAN bus RX queue */
#define HOST_RX_DEPTH 8
typedef struct { uint32_t id; uint8_t data[8]; } host_frame_t;
static host_frame_t s_host_rx[HOST_RX_DEPTH];
static int s_host_rx_head = 0;
static int s_host_rx_tail = 0;
static esc_command_t s_host_last_cmd;

static void _send_cmd_frame(void) {
    /* Store for test inspection */
    s_host_last_cmd.throttle_pct100 = s_cmd.throttle_pct100;
    s_host_last_cmd.direction       = s_cmd.direction;
    s_host_last_cmd.enable          = s_cmd.enable;
}

static void _poll_rx(void) {
    while (s_host_rx_head != s_host_rx_tail) {
        host_frame_t *f = &s_host_rx[s_host_rx_head % HOST_RX_DEPTH];
        if (f->id == CAN_ID_ESC_STATUS) {
            _decode_status(f->data, &s_status);
            _update_fault_state(s_status.fault_code);
            s_last_status_ms = _now_ms();
        }
        s_host_rx_head++;
    }
}

#endif  /* ESP_PLATFORM */

/* ── Public API ─────────────────────────────────────────────────────── */

int can_bridge_init(const can_bridge_config_t *cfg) {
    if (!cfg) return -1;
#ifdef ESP_PLATFORM
    twai_general_config_t g_cfg = TWAI_GENERAL_CONFIG_DEFAULT(
        (gpio_num_t)cfg->tx_gpio, (gpio_num_t)cfg->rx_gpio,
        TWAI_MODE_NORMAL);
    g_cfg.tx_queue_len = 16;
    g_cfg.rx_queue_len = 16;

    /* ESP-IDF v5 TWAI macros expand to brace initializers, not compound
       literals — cannot be used in assignment expressions directly. */
    twai_timing_config_t t_cfg;
    if (cfg->bitrate == 1000000) {
        twai_timing_config_t _t = TWAI_TIMING_CONFIG_1MBITS();
        t_cfg = _t;
    } else if (cfg->bitrate == 250000) {
        twai_timing_config_t _t = TWAI_TIMING_CONFIG_250KBITS();
        t_cfg = _t;
    } else {
        twai_timing_config_t _t = TWAI_TIMING_CONFIG_500KBITS();
        t_cfg = _t;
    }
    twai_filter_config_t f_cfg = TWAI_FILTER_CONFIG_ACCEPT_ALL();

    esp_err_t err = twai_driver_install(&g_cfg, &t_cfg, &f_cfg);
    if (err != ESP_OK) { LOG_E("twai_driver_install failed: %d", err); return -1; }
    err = twai_start();
    if (err != ESP_OK) { LOG_E("twai_start failed: %d", err); return -1; }
    LOG_I("TWAI init OK: TX=GPIO%d RX=GPIO%d baud=%lu",
          cfg->tx_gpio, cfg->rx_gpio, (unsigned long)cfg->bitrate);
#else
    LOG_I("CAN bridge host-mock init: TX=%d RX=%d baud=%lu",
          cfg->tx_gpio, cfg->rx_gpio, (unsigned long)cfg->bitrate);
#endif
    s_initialised = 1;
    return 0;
}

void can_bridge_set_command(const esc_command_t *cmd) {
    if (!cmd) return;
    s_cmd.throttle_pct100 = cmd->throttle_pct100;
    s_cmd.direction       = cmd->direction;
    s_cmd.enable          = cmd->enable;
}

void can_bridge_get_status(esc_status_t *out) {
    if (!out) return;
    out->actual_rpm     = s_status.actual_rpm;
    out->dc_current_x10 = s_status.dc_current_x10;
    out->fault_code     = s_status.fault_code;
}

uint16_t can_bridge_get_flags(void) {
    return s_flags;
}

float can_bridge_get_rpm(void) {
    return (float)s_status.actual_rpm;
}

float can_bridge_get_current_a(void) {
    return (float)s_status.dc_current_x10 * 0.1f;
}

/* ── FreeRTOS task ──────────────────────────────────────────────────── */

#ifdef ESP_PLATFORM

static void can_bridge_task(void *arg) {
    (void)arg;
    TickType_t last_wake = xTaskGetTickCount();
    while (1) {
        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(CAN_HEARTBEAT_MS));
        if (!s_initialised) continue;
        _send_cmd_frame();
        _poll_rx();
        _check_timeout(_now_ms());
    }
}

#define CAN_TASK_PRIORITY  8   /* above safety(3); below sensor(12), telem(10) */
#define CAN_TASK_STACK  2048

BaseType_t can_bridge_start(void) {
    return xTaskCreatePinnedToCore(can_bridge_task, "can_bridge",
                                   CAN_TASK_STACK, NULL,
                                   CAN_TASK_PRIORITY, NULL, 1);
}

#else  /* HOST BUILD */

int can_bridge_start(void) {
    LOG_I("can_bridge_start (host mock — no FreeRTOS task created)");
    return 0;  /* tasks not started on host; test calls can_host_step() directly */
}

/* Host step: manually advance one 10 ms tick */
static void can_host_step(void) {
    if (!s_initialised) return;
    s_sim_time_ms += CAN_HEARTBEAT_MS;
    _send_cmd_frame();
    _poll_rx();
    _check_timeout(s_sim_time_ms);
}

/* ── Host test helpers ──────────────────────────────────────────────── */

void can_host_inject_status(uint16_t rpm, int16_t current_x10, uint8_t fault) {
    int tail = s_host_rx_tail % HOST_RX_DEPTH;
    s_host_rx[tail].id = CAN_ID_ESC_STATUS;
    s_host_rx[tail].data[0] = (uint8_t)(rpm >> 8);
    s_host_rx[tail].data[1] = (uint8_t)(rpm & 0xFF);
    s_host_rx[tail].data[2] = (uint8_t)(current_x10 >> 8);
    s_host_rx[tail].data[3] = (uint8_t)(current_x10 & 0xFF);
    s_host_rx[tail].data[4] = fault;
    s_host_rx[tail].data[5] = 0;
    s_host_rx[tail].data[6] = 0;
    s_host_rx[tail].data[7] = 0;
    s_host_rx_tail++;
    can_host_step();  /* process the injected frame immediately */
}

void can_host_get_last_cmd(esc_command_t *out) {
    if (!out) return;
    *out = s_host_last_cmd;
}

void can_host_advance_time_ms(uint32_t ms) {
    uint32_t steps = ms / CAN_HEARTBEAT_MS;
    for (uint32_t i = 0; i < steps; i++) {
        can_host_step();
    }
}

void can_host_reset(void) {
    memset((void *)&s_cmd,    0, sizeof(s_cmd));
    memset((void *)&s_status, 0, sizeof(s_status));
    s_flags          = 0;
    s_fault_consec   = 0;
    s_recover_consec = 0;
    s_last_status_ms = 0;
    s_sim_time_ms    = 0;
    s_host_rx_head   = 0;
    s_host_rx_tail   = 0;
    s_initialised    = 1;  /* re-init implicitly */
    LOG_I("can_host_reset: state cleared");
}

#endif  /* !ESP_PLATFORM */
