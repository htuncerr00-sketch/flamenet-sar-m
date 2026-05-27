/*
 * main/telemetry_protocol.c — Wire Packet Builder
 * ====================================================
 * Implements telem_pack() and crc16_ccitt() to produce byte-identical
 * output to the Python `TelemetryFrame.pack()` reference parser.
 *
 * Endianness:
 *   ESP32 is little-endian. The wire format is big-endian. We do
 *   the byte swap manually instead of relying on htonl/htons macros
 *   — that way this file also compiles standalone on a host for
 *   the host-side verification test (test_telem_pack.c).
 *
 * Allocation:
 *   No malloc. Caller provides the output buffer.
 *
 * Float handling:
 *   We type-pun float → uint32_t via memcpy (strict-aliasing safe),
 *   then write big-endian bytes. This avoids any platform-specific
 *   float representation issues — IEEE 754 single is universal on
 *   ESP32 + every Python platform we test against.
 */
#include "telemetry_frame.h"
#include <string.h>

/* ─── byte writers (big-endian) ─────────────────────────────────── */

static inline void w_u16_be(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)(v >> 8);
    p[1] = (uint8_t)(v & 0xFF);
}

static inline void w_u32_be(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)((v >> 24) & 0xFF);
    p[1] = (uint8_t)((v >> 16) & 0xFF);
    p[2] = (uint8_t)((v >>  8) & 0xFF);
    p[3] = (uint8_t)( v        & 0xFF);
}

static inline void w_u64_be(uint8_t *p, uint64_t v) {
    p[0] = (uint8_t)((v >> 56) & 0xFF);
    p[1] = (uint8_t)((v >> 48) & 0xFF);
    p[2] = (uint8_t)((v >> 40) & 0xFF);
    p[3] = (uint8_t)((v >> 32) & 0xFF);
    p[4] = (uint8_t)((v >> 24) & 0xFF);
    p[5] = (uint8_t)((v >> 16) & 0xFF);
    p[6] = (uint8_t)((v >>  8) & 0xFF);
    p[7] = (uint8_t)( v        & 0xFF);
}

static inline void w_f32_be(uint8_t *p, float v) {
    uint32_t bits;
    memcpy(&bits, &v, 4);     /* type-pun without UB */
    w_u32_be(p, bits);
}

/* ─── CRC-16/CCITT ──────────────────────────────────────────────── */
/*
 * Polynomial: 0x1021
 * Initial:    0xFFFF
 * No reflection, no xorout.
 * Bytewise, MSB-first. Matches Python reference exactly.
 */
uint16_t crc16_ccitt(const uint8_t *data, size_t len) {
    uint16_t crc = 0xFFFFu;
    for (size_t i = 0; i < len; ++i) {
        crc ^= ((uint16_t)data[i]) << 8;
        for (int b = 0; b < 8; ++b) {
            if (crc & 0x8000u) {
                crc = (uint16_t)((crc << 1) ^ 0x1021u);
            } else {
                crc = (uint16_t)(crc << 1);
            }
        }
    }
    return crc;
}

/* ─── telem_pack ────────────────────────────────────────────────── */

size_t telem_pack(uint8_t *out, const telem_frame_t *f) {
    if (out == NULL || f == NULL) return 0;

    /* magic */
    out[0] = TELEM_MAGIC_0;
    out[1] = TELEM_MAGIC_1;

    /* payload starts at out + 2 */
    uint8_t *p = out + 2;

    w_u64_be(p +  0, f->ts_us);
    w_u16_be(p +  8, f->seq);
    w_u16_be(p + 10, f->flags);
    w_f32_be(p + 12, f->x_mm);
    w_f32_be(p + 16, f->a_deg);
    w_f32_be(p + 20, f->T_N);
    w_f32_be(p + 24, f->rpm);
    w_f32_be(p + 28, f->vib_x);
    w_f32_be(p + 32, f->vib_y);
    w_f32_be(p + 36, f->vib_z);
    w_f32_be(p + 40, f->temp_K);
    w_f32_be(p + 44, f->current_A);
    w_f32_be(p + 48, f->alpha);
    w_f32_be(p + 52, f->quality);
    w_f32_be(p + 56, f->spare);

    /* bytes [60..61] = reserved 0x0000 (will be CRC's view) */
    p[60] = 0x00;
    p[61] = 0x00;

    /* CRC-16 over payload bytes [0..61]   (62 bytes), stored big-endian
       at payload bytes [62..63]. PC reference: `crc16_ccitt(raw[:-2])`. */
    uint16_t crc = crc16_ccitt(p, 62);
    w_u16_be(p + 62, crc);

    return TELEM_WIRE_LEN;
}
