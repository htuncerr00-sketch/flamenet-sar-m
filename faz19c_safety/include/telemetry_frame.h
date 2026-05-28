/*
 * include/telemetry_frame.h — Wire-Compatible Telemetry Frame
 * ============================================================
 *
 * MUST match Python `>QHHffffffffffffHH` exactly. All multi-byte
 * integers and floats are BIG-ENDIAN on the wire. ESP32 is little-
 * endian, so we explicitly byte-swap before TX.
 *
 * Field LOGICAL meaning (firmware reads physical sensors into these):
 *   ts_us       : timestamp_us  -- monotonic microseconds since boot
 *   seq         : rolling 16-bit sequence (wraps at 65535)
 *   flags       : status/alarm bitfield (see TELEM_FLAG_* below)
 *   x_mm        : carriage X position in mm  -- mapped to encoder pulses
 *   a_deg       : spindle angle in degrees   -- mapped to A-axis encoder
 *   T_N         : fiber tension in newtons   -- HX711/strain gauge
 *   rpm         : spindle RPM                 -- derived from A-axis
 *   vib_x,y,z   : IMU vibration in g          -- MPU6050/BMI160 axes
 *   temp_K      : primary temp in kelvin      -- thermistor or NTC
 *   current_A   : motor current in amps       -- INA226 bus current
 *   alpha       : cycle progress [0..1]       -- computed in firmware
 *   quality     : quality score [0..100]      -- computed/passthrough
 *   spare       : reserved, write 0.0f
 *
 * On-wire byte order (offset / size / type):
 *      0   8  big-endian uint64   ts_us
 *      8   2  big-endian uint16   seq
 *     10   2  big-endian uint16   flags
 *     12   4  big-endian float    x_mm
 *     16   4  big-endian float    a_deg
 *     20   4  big-endian float    T_N
 *     24   4  big-endian float    rpm
 *     28   4  big-endian float    vib_x
 *     32   4  big-endian float    vib_y
 *     36   4  big-endian float    vib_z
 *     40   4  big-endian float    temp_K
 *     44   4  big-endian float    current_A
 *     48   4  big-endian float    alpha
 *     52   4  big-endian float    quality
 *     56   4  big-endian float    spare
 *     60   2  big-endian uint16   reserved (set 0; CRC overwrites)
 *     62   2  big-endian uint16   crc16  (CRC-16/CCITT of bytes 0..61)
 *
 *   Full wire packet:  [0xAA 0x55][64-byte payload]   = 66 bytes
 */
#ifndef FW_TELEMETRY_FRAME_H
#define FW_TELEMETRY_FRAME_H

#include <stdint.h>
#include <stddef.h>

#define TELEM_MAGIC_0     0xAA
#define TELEM_MAGIC_1     0x55
#define TELEM_PAYLOAD_LEN 64
#define TELEM_WIRE_LEN    (2 + TELEM_PAYLOAD_LEN)   /* 66 */

/* Status/alarm flags (mirror SafetyController bounds in PC) */
#define TELEM_FLAG_BOOT_OK        (1u << 0)
#define TELEM_FLAG_TENSION_OK     (1u << 1)
#define TELEM_FLAG_TEMP_OK        (1u << 2)
#define TELEM_FLAG_RPM_OK         (1u << 3)
#define TELEM_FLAG_VIBRATION_OK   (1u << 4)
#define TELEM_FLAG_HOMED          (1u << 5)
#define TELEM_FLAG_RUNNING        (1u << 6)
#define TELEM_FLAG_ESTOP_ACTIVE   (1u << 7)
#define TELEM_FLAG_SAFE_HALT      (1u << 8)
/* Per-sensor OK bits (Faz 19B — last-known-good propagation).
   These were previously "reserved" bits so PC parser is unaffected. */
#define TELEM_FLAG_INA226_OK      (1u << 9)
#define TELEM_FLAG_IMU_OK         (1u << 10)
#define TELEM_FLAG_THERMAL_OK     (1u << 11)
/* Safety event bits (Faz 19C — boot + runtime safety layer) */
#define TELEM_FLAG_BROWNOUT          (1u << 12)
#define TELEM_FLAG_THERMAL_SHUTDOWN  (1u << 13)
#define TELEM_FLAG_WATCHDOG_RESET    (1u << 14)
/* CAN/TWAI ESC bridge fault (Faz 19D) */
#define TELEM_FLAG_ESC_FAULT         (1u << 15)

/*
 * Host-friendly logical struct. NOT used for wire — wire format is
 * built by telem_pack() byte-by-byte to ensure exact endianness.
 */
typedef struct {
    uint64_t ts_us;
    uint16_t seq;
    uint16_t flags;
    float    x_mm;
    float    a_deg;
    float    T_N;
    float    rpm;
    float    vib_x;
    float    vib_y;
    float    vib_z;
    float    temp_K;
    float    current_A;
    float    alpha;
    float    quality;
    float    spare;
} telem_frame_t;

/*
 * Pack a frame into the 64-byte big-endian payload, then prepend the
 * 2-byte magic so `out` contains a full 66-byte wire packet.
 *
 * out      : caller-owned buffer of at least TELEM_WIRE_LEN bytes
 * f        : logical frame data
 * returns  : TELEM_WIRE_LEN on success, 0 on bad arg
 *
 * NOTE: CRC-16/CCITT is computed over payload bytes [0..61] and stored
 * big-endian at payload bytes [62..63]. Result is verified byte-identical
 * to the PC parser via host_test/test_telem_pack.c.
 */
size_t telem_pack(uint8_t *out, const telem_frame_t *f);

/* CRC-16/CCITT, polynomial 0x1021, init 0xFFFF, no reflection, no xorout. */
uint16_t crc16_ccitt(const uint8_t *data, size_t len);

#endif /* FW_TELEMETRY_FRAME_H */
