/*
 * main/i2c_bus.c — I²C Bus Manager Implementation
 * ====================================================
 * ESP-IDF: uses driver/i2c.h master mode on I2C_NUM_0.
 * Host:    delegates to i2c_host_mock so tests run without hardware.
 */
#include "i2c_bus.h"

#include <string.h>

#ifdef ESP_PLATFORM
#include "driver/i2c.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#define I2C_PORT I2C_NUM_0
static SemaphoreHandle_t s_mutex = NULL;
static const char *TAG = "i2c_bus";
#else
/* Host-mode: use a simple mutex from i2c_host_mock */
#include "i2c_host_mock.h"
#include <pthread.h>
static pthread_mutex_t s_mutex = PTHREAD_MUTEX_INITIALIZER;
#endif

/* Bus stats (lock-free, single producer per counter) */
static volatile uint32_t s_n_ok = 0;
static volatile uint32_t s_n_timeouts = 0;
static volatile uint32_t s_n_errors = 0;
static int s_initialized = 0;

/* ───────────────────────── lock helpers ────────────────────────── */

static int lock_take(uint32_t timeout_ms) {
#ifdef ESP_PLATFORM
    if (s_mutex == NULL) return 0;
    return xSemaphoreTake(s_mutex, pdMS_TO_TICKS(timeout_ms)) == pdTRUE;
#else
    (void)timeout_ms;
    return pthread_mutex_lock(&s_mutex) == 0;
#endif
}

static void lock_give(void) {
#ifdef ESP_PLATFORM
    if (s_mutex == NULL) return;
    xSemaphoreGive(s_mutex);
#else
    pthread_mutex_unlock(&s_mutex);
#endif
}

/* ───────────────────────── init ────────────────────────────────── */

i2c_err_t i2c_bus_init(void) {
    if (s_initialized) return I2C_OK;

#ifdef ESP_PLATFORM
    s_mutex = xSemaphoreCreateMutex();
    if (s_mutex == NULL) {
        ESP_LOGE(TAG, "mutex create failed");
        return ESP_ERR_NO_MEM;
    }

    i2c_config_t cfg = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = I2C_BUS_SDA_GPIO,
        .scl_io_num = I2C_BUS_SCL_GPIO,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = I2C_BUS_FREQ_HZ,
    };
    esp_err_t err = i2c_param_config(I2C_PORT, &cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2c_param_config: %d", err);
        return err;
    }
    err = i2c_driver_install(I2C_PORT, cfg.mode, 0, 0, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2c_driver_install: %d", err);
        return err;
    }
    ESP_LOGI(TAG, "I²C0 init: SDA=%d SCL=%d freq=%d Hz",
             I2C_BUS_SDA_GPIO, I2C_BUS_SCL_GPIO, I2C_BUS_FREQ_HZ);
#else
    /* host mode: nothing to set up. Tests own the mock state and
       must call i2c_host_mock_init() before any registrations. */
#endif

    s_initialized = 1;
    return I2C_OK;
}

/* ───────────────────────── read/write reg ──────────────────────── */

i2c_err_t i2c_bus_read_reg(uint8_t dev_addr, uint8_t reg_addr,
                           uint8_t *buf, size_t n) {
    if (buf == NULL || n == 0) return I2C_OK;
    if (!s_initialized) return (i2c_err_t)-1;
    if (!lock_take(I2C_BUS_TIMEOUT_MS)) {
        s_n_timeouts++;
        return (i2c_err_t)-1;
    }

    i2c_err_t err = I2C_OK;

#ifdef ESP_PLATFORM
    i2c_cmd_handle_t cmd = i2c_cmd_link_create();
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (dev_addr << 1) | I2C_MASTER_WRITE, true);
    i2c_master_write_byte(cmd, reg_addr, true);
    i2c_master_start(cmd);   /* repeated start */
    i2c_master_write_byte(cmd, (dev_addr << 1) | I2C_MASTER_READ, true);
    if (n > 1) i2c_master_read(cmd, buf, n - 1, I2C_MASTER_ACK);
    i2c_master_read_byte(cmd, buf + n - 1, I2C_MASTER_NACK);
    i2c_master_stop(cmd);
    err = i2c_master_cmd_begin(I2C_PORT, cmd, pdMS_TO_TICKS(I2C_BUS_TIMEOUT_MS));
    i2c_cmd_link_delete(cmd);
#else
    err = i2c_host_mock_read_reg(dev_addr, reg_addr, buf, n);
#endif

    if (err == I2C_OK) s_n_ok++;
    else if (err == (i2c_err_t)0x107 /* ESP_ERR_TIMEOUT */) s_n_timeouts++;
    else s_n_errors++;

    lock_give();
    return err;
}

i2c_err_t i2c_bus_write_reg(uint8_t dev_addr, uint8_t reg_addr,
                            const uint8_t *buf, size_t n) {
    if (!s_initialized) return (i2c_err_t)-1;
    if (!lock_take(I2C_BUS_TIMEOUT_MS)) {
        s_n_timeouts++;
        return (i2c_err_t)-1;
    }

    i2c_err_t err = I2C_OK;

#ifdef ESP_PLATFORM
    i2c_cmd_handle_t cmd = i2c_cmd_link_create();
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (dev_addr << 1) | I2C_MASTER_WRITE, true);
    i2c_master_write_byte(cmd, reg_addr, true);
    if (n > 0) i2c_master_write(cmd, buf, n, true);
    i2c_master_stop(cmd);
    err = i2c_master_cmd_begin(I2C_PORT, cmd, pdMS_TO_TICKS(I2C_BUS_TIMEOUT_MS));
    i2c_cmd_link_delete(cmd);
#else
    err = i2c_host_mock_write_reg(dev_addr, reg_addr, buf, n);
#endif

    if (err == I2C_OK) s_n_ok++;
    else if (err == (i2c_err_t)0x107) s_n_timeouts++;
    else s_n_errors++;

    lock_give();
    return err;
}

/* ───────────────────────── 16-bit helpers ──────────────────────── */

i2c_err_t i2c_bus_read_reg16_be(uint8_t dev_addr, uint8_t reg_addr,
                                uint16_t *out) {
    if (out == NULL) return (i2c_err_t)-1;
    uint8_t b[2];
    i2c_err_t err = i2c_bus_read_reg(dev_addr, reg_addr, b, 2);
    if (err != I2C_OK) return err;
    *out = ((uint16_t)b[0] << 8) | b[1];
    return I2C_OK;
}

i2c_err_t i2c_bus_write_reg16_be(uint8_t dev_addr, uint8_t reg_addr,
                                 uint16_t value) {
    uint8_t b[2] = { (uint8_t)(value >> 8), (uint8_t)(value & 0xFF) };
    return i2c_bus_write_reg(dev_addr, reg_addr, b, 2);
}

/* ───────────────────────── diagnostics ─────────────────────────── */

uint32_t i2c_bus_n_transactions_ok(void) { return s_n_ok; }
uint32_t i2c_bus_n_timeouts(void)        { return s_n_timeouts; }
uint32_t i2c_bus_n_errors(void)          { return s_n_errors; }
