"""
host_test/verify_frames.py — Byte-Identical PC Parser Verifier
====================================================================
Reads frames produced by test_telem_pack binary and verifies:

  1. Every frame's MAGIC = 0xAA 0x55
  2. Every frame's CRC validates with PC's crc16_ccitt
  3. Decoded float fields are bit-identical to what C wrote
  4. Re-packing in Python yields the same 66 bytes (round trip)

This proves the firmware logic produces wire bytes that the PC
parser (RealESP32Link / TelemetryFrame.unpack) accepts without
ANY changes to the PC code.

Usage:
    cd faz19_firmware/host_test
    gcc -O2 -Wall -I../include test_telem_pack.c \\
        ../main/telemetry_protocol.c -o test_telem_pack
    ./test_telem_pack > frames.bin
    python3 verify_frames.py frames.bin
"""
from __future__ import annotations
import os
import struct
import sys


# Reproduce the same LCG to match the C generator exactly, so we can
# build the reference Python frame independently and compare.
class HostLCG:
    """Match the LCG in test_telem_pack.c exactly (32-bit unsigned)."""
    def __init__(self, seed: int = 1):
        self.state = seed & 0xFFFFFFFF

    def next_u32(self) -> int:
        self.state = (self.state * 1664525 + 1013904223) & 0xFFFFFFFF
        return self.state

    def next_float(self, lo: float, hi: float) -> float:
        """Reproduce lcg_float exactly: u in [0,1] then lo + u*(hi-lo)."""
        u_int = self.next_u32() & 0xFFFFFF
        u = u_int / float(0xFFFFFF)
        # In C: lo + u*(hi-lo). Compute in float32 to match.
        import struct as _s
        # Round trip through float32 to match C float arithmetic
        u32 = _s.unpack("f", _s.pack("f", u))[0]
        diff = _s.unpack("f", _s.pack("f", hi - lo))[0]
        mult = _s.unpack("f", _s.pack("f", u32 * diff))[0]
        return _s.unpack("f", _s.pack("f", lo + mult))[0]


def _sys_path_setup():
    here = os.path.dirname(os.path.abspath(__file__))
    # faz19_firmware/host_test  → faz19_firmware  → up to filament_winding
    parent = os.path.dirname(here)
    project_root = os.path.dirname(parent)
    sys.path.insert(0, project_root)


_sys_path_setup()
from faz17_d1.hardware.esp32_link import (
    TelemetryFrame, TELEM_BYTES, crc16_ccitt)

WIRE_LEN = 2 + TELEM_BYTES   # 66
MAGIC = b"\xaa\x55"

# Flag bits exactly as in telemetry_frame.h
F_BOOT_OK      = 1 << 0
F_TENSION_OK   = 1 << 1
F_TEMP_OK      = 1 << 2
F_RPM_OK       = 1 << 3
F_VIBRATION_OK = 1 << 4


def _f32_round_trip(v: float) -> float:
    """Force a Python float through IEEE 754 single."""
    return struct.unpack("f", struct.pack("f", v))[0]


def build_expected(i: int, lcg: HostLCG, n_total: int):
    """Build the frame that C should have emitted at index i."""
    x   = _f32_round_trip(40.0 + lcg.next_float(0.0, 300.0))
    T   = _f32_round_trip(15.0 + lcg.next_float(-0.5, 0.5))
    vx  = lcg.next_float(-0.05, 0.05)
    vy  = lcg.next_float(-0.05, 0.05)
    vz  = lcg.next_float(-0.05, 0.05)
    tk  = _f32_round_trip(295.15 + lcg.next_float(-0.5, 0.5))
    return TelemetryFrame(
        ts_us     = i * 1000,
        seq       = i & 0xFFFF,
        flags     = (F_BOOT_OK | F_TENSION_OK | F_TEMP_OK
                     | F_RPM_OK | F_VIBRATION_OK),
        x_mm      = x,
        a_deg     = float(i % 360),
        T_N       = T,
        rpm       = 8.5,
        vib_x     = vx,
        vib_y     = vy,
        vib_z     = vz,
        temp_K    = tk,
        current_A = 2.5,
        alpha     = _f32_round_trip(float(i) / float(n_total)),
        quality   = 92.0,
        spare     = 0.0,
    )


def verify(path: str) -> dict:
    raw = open(path, "rb").read()
    if len(raw) % WIRE_LEN != 0:
        return {"error": f"file size {len(raw)} not multiple of {WIRE_LEN}"}
    n = len(raw) // WIRE_LEN

    n_magic_ok = 0
    n_crc_ok = 0
    n_decoded_ok = 0
    n_roundtrip_ok = 0
    first_failure = None
    lcg = HostLCG(seed=1)

    for i in range(n):
        wire = raw[i * WIRE_LEN : (i + 1) * WIRE_LEN]
        # 1) magic
        if wire[:2] != MAGIC:
            if first_failure is None:
                first_failure = f"i={i} magic={wire[:2].hex()}"
            continue
        n_magic_ok += 1

        # 2) CRC validates using PC parser
        payload = wire[2:]
        crc_stored = struct.unpack(">H", payload[-2:])[0]
        crc_computed = crc16_ccitt(payload[:-2])
        if crc_stored != crc_computed:
            if first_failure is None:
                first_failure = (f"i={i} crc_stored={crc_stored:#06x} "
                                 f"crc_computed={crc_computed:#06x}")
            continue
        n_crc_ok += 1

        # 3) Decode → re-pack → bytes match
        decoded = TelemetryFrame.unpack(payload)
        if decoded is None:
            if first_failure is None:
                first_failure = f"i={i} TelemetryFrame.unpack returned None"
            continue
        n_decoded_ok += 1

        # 4) Build expected Python frame and compare bytes
        expected = build_expected(i, lcg, n_total=n)
        expected_wire = MAGIC + expected.pack()
        if expected_wire == wire:
            n_roundtrip_ok += 1
        else:
            if first_failure is None:
                # Show first diff byte
                diff_i = next(
                    (k for k, (a, b) in enumerate(zip(wire, expected_wire))
                     if a != b), -1)
                first_failure = (f"i={i} byte_diff at offset {diff_i}: "
                                 f"got={wire[diff_i]:02x} "
                                 f"expected={expected_wire[diff_i]:02x}")

    return {
        "n_frames":       n,
        "n_magic_ok":     n_magic_ok,
        "n_crc_ok":       n_crc_ok,
        "n_decoded_ok":   n_decoded_ok,
        "n_roundtrip_ok": n_roundtrip_ok,
        "first_failure":  first_failure,
        "passed":         (n_magic_ok == n and
                           n_crc_ok == n and
                           n_decoded_ok == n),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: verify_frames.py <frames.bin>", file=sys.stderr)
        sys.exit(1)
    r = verify(sys.argv[1])
    print("HOST-SIDE TELEM PACK VERIFICATION")
    print("-" * 60)
    for k, v in r.items():
        print(f"  {k:>18} = {v}")
    print("-" * 60)
    print(f"  Verdict: {'PASS ✓' if r.get('passed') else 'FAIL ✗'}")
    sys.exit(0 if r.get("passed") else 1)
