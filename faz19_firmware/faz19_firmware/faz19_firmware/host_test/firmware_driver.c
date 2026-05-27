#define _POSIX_C_SOURCE 200809L
/*
 * host_test/firmware_driver.c — Faz 19 Saha Test Sürücüsü
 * =============================================================
 * This program plays the role of "real ESP32 plugged into PC via USB".
 *
 * What it does:
 *   - Initializes the same sensor_pipeline + telem_pack used by the
 *     ESP32 firmware
 *   - Generates frames at the target rate (default 1 kHz)
 *   - Writes them to stdout (which the Python field-test harness pipes
 *     into the pty master end → RealESP32Link reads them)
 *
 * Why this is meaningful:
 *   The firmware code path executed here is the SAME code that runs on
 *   ESP32 (telemetry_protocol.c + sensor_pipeline.c). Only the UART
 *   driver differs (host: write(stdout); ESP32: uart_write_bytes).
 *
 *   So if PC accepts these bytes, it will accept the bytes the real
 *   ESP32 produces — modulo only UART hardware timing.
 *
 * Build:
 *   gcc -O2 -Wall -I../include firmware_driver.c \\
 *       ../main/telemetry_protocol.c ../main/sensor_pipeline.c \\
 *       -o firmware_driver -lm
 *
 * Run:
 *   ./firmware_driver --rate-hz 1000 --duration-s 30 > /dev/pts/X
 */
#include "telemetry_frame.h"
#include "sensor_pipeline.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdint.h>
#include <unistd.h>

static uint64_t now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000ull + (uint64_t)ts.tv_nsec / 1000ull;
}

static void usleep_until(uint64_t target_us) {
    while (1) {
        uint64_t cur = now_us();
        if (cur >= target_us) return;
        uint64_t gap = target_us - cur;
        if (gap > 500) {
            struct timespec req = { .tv_sec = 0, .tv_nsec = (long)(gap - 200) * 1000 };
            nanosleep(&req, NULL);
        }
        /* spin the last ~200 us for low jitter */
    }
}

int main(int argc, char **argv) {
    double rate_hz   = 1000.0;
    double duration_s = -1.0;   /* -1 = forever */
    /* parse simple --rate-hz X --duration-s Y */
    for (int i = 1; i < argc - 1; ++i) {
        if (!strcmp(argv[i], "--rate-hz"))    rate_hz    = atof(argv[i+1]);
        if (!strcmp(argv[i], "--duration-s")) duration_s = atof(argv[i+1]);
    }
    if (rate_hz <= 0) { fprintf(stderr, "bad rate\n"); return 1; }

    sensor_pipeline_init();
    uint64_t period_us = (uint64_t)(1e6 / rate_hz);
    uint64_t start = now_us();
    uint64_t deadline = (duration_s > 0) ? start + (uint64_t)(duration_s * 1e6)
                                         : UINT64_MAX;
    uint64_t next_t = start;
    uint16_t seq = 0;

    /* Make stdout unbuffered so each frame leaves immediately */
    setvbuf(stdout, NULL, _IONBF, 0);

    uint8_t wire[TELEM_WIRE_LEN];
    telem_frame_t f;

    uint64_t n_frames = 0;
    while (1) {
        uint64_t t = now_us();
        if (t >= deadline) break;
        if (t >= next_t) {
            sensor_pipeline_read(&f, t - start, seq);
            telem_pack(wire, &f);
            size_t written = 0;
            while (written < TELEM_WIRE_LEN) {
                ssize_t n = write(STDOUT_FILENO,
                                  wire + written,
                                  TELEM_WIRE_LEN - written);
                if (n <= 0) {
                    fprintf(stderr, "stdout write failed/closed; exiting\n");
                    return 1;
                }
                written += (size_t)n;
            }
            seq = (uint16_t)(seq + 1u);
            n_frames++;
            next_t += period_us;
        }
        usleep_until(next_t);
    }
    fprintf(stderr, "firmware_driver: wrote %llu frames in %.2f s\n",
            (unsigned long long)n_frames,
            (double)(now_us() - start) / 1e6);
    return 0;
}
