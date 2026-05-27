/*
 * host_test/test_telem_pack.c — Byte-Identical Verification
 * ================================================================
 * Compile on host (Linux, gcc) and dump packed frames to stdout in
 * a format the Python verifier can consume. This proves the firmware
 * logic produces bytes that PC parser accepts BYTE-FOR-BYTE.
 *
 * Build:
 *   gcc -O2 -Wall -I../include test_telem_pack.c \
 *       ../main/telemetry_protocol.c -o test_telem_pack
 *
 * Run:
 *   ./test_telem_pack > frames.bin
 *
 * Verify with:
 *   python3 verify_frames.py frames.bin
 */
#include "telemetry_frame.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

/*
 * Build N deterministic frames using a simple LCG so the host C and
 * Python produce identical inputs. We test ASCII-mode output (one
 * frame per line, hex) for easy diffing, then binary mode.
 */

static uint32_t lcg_state = 1u;

static uint32_t lcg_next(void) {
    lcg_state = lcg_state * 1664525u + 1013904223u;
    return lcg_state;
}

static float lcg_float(float lo, float hi) {
    float u = (float)(lcg_next() & 0xFFFFFFu) / (float)0xFFFFFFu;
    return lo + u * (hi - lo);
}

/* Emit either ASCII hex (each frame on its own line) or raw binary
 * depending on argv[1]. Default: binary so the file can be diff'd
 * against the Python reference byte-for-byte.
 */
int main(int argc, char **argv) {
    int n = 100;
    int ascii_mode = 0;
    if (argc > 1 && strcmp(argv[1], "--ascii") == 0) { ascii_mode = 1; }
    if (argc > 2) { n = atoi(argv[2]); if (n < 1) n = 1; }

    lcg_state = 1u;
    uint8_t buf[TELEM_WIRE_LEN];

    for (int i = 0; i < n; ++i) {
        telem_frame_t f = {
            .ts_us     = (uint64_t)i * 1000ull,
            .seq       = (uint16_t)(i & 0xFFFF),
            .flags     = (uint16_t)(TELEM_FLAG_BOOT_OK
                                  | TELEM_FLAG_TENSION_OK
                                  | TELEM_FLAG_TEMP_OK
                                  | TELEM_FLAG_RPM_OK
                                  | TELEM_FLAG_VIBRATION_OK),
            .x_mm      = 40.0f + lcg_float(0.0f, 300.0f),
            .a_deg     = (float)(i % 360),
            .T_N       = 15.0f + lcg_float(-0.5f, 0.5f),
            .rpm       = 8.5f,
            .vib_x     = lcg_float(-0.05f, 0.05f),
            .vib_y     = lcg_float(-0.05f, 0.05f),
            .vib_z     = lcg_float(-0.05f, 0.05f),
            .temp_K    = 295.15f + lcg_float(-0.5f, 0.5f),
            .current_A = 2.5f,
            .alpha     = (float)i / (float)n,
            .quality   = 92.0f,
            .spare     = 0.0f,
        };
        size_t w = telem_pack(buf, &f);
        if (w != TELEM_WIRE_LEN) {
            fprintf(stderr, "pack failed at i=%d\n", i);
            return 1;
        }

        if (ascii_mode) {
            for (size_t b = 0; b < TELEM_WIRE_LEN; ++b) {
                printf("%02x", buf[b]);
            }
            printf("\n");
        } else {
            if (fwrite(buf, 1, TELEM_WIRE_LEN, stdout) != TELEM_WIRE_LEN) {
                fprintf(stderr, "write failed\n");
                return 1;
            }
        }
    }
    return 0;
}
