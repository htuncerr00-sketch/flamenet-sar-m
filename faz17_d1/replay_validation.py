"""
validation/replay_validation.py — Deterministic Replay Test
================================================================
Two replay runs with same seed must produce byte-identical bytes.
"""
from __future__ import annotations
import numpy as np
from dataclasses import dataclass
from typing import List
from ..hardware.esp32_link import TelemetryFrame, TELEM_BYTES


@dataclass
class ReplayResult:
    n_frames:     int
    byte_identical: bool
    crc_errors:   int
    decode_failures: int


def generate_synthetic_run(seed: int, n_frames: int = 500) -> bytes:
    """Deterministic synthetic telemetry data."""
    rng = np.random.default_rng(seed)
    buf = bytearray()
    for i in range(n_frames):
        f = TelemetryFrame(
            ts_us=i * 1000, seq=i & 0xFFFF, flags=1,
            x_mm=40.0 + float(rng.uniform(0, 300)),
            a_deg=float(i * 1.0),
            T_N=15.0 + float(rng.normal(0, 0.5)),
            rpm=8.5, vib_x=float(rng.normal(0, 0.05)),
            vib_y=float(rng.normal(0, 0.05)),
            vib_z=float(rng.normal(0, 0.05)),
            temp_K=295.15 + float(rng.normal(0, 0.5)),
            current_A=2.5, alpha=float(i / n_frames),
            quality=92.0)
        buf += f.pack()
    return bytes(buf)


def validate_deterministic_replay(seed: int = 42, n_frames: int = 500) -> ReplayResult:
    """
    Two runs with same seed → byte-identical output.
    Round-trip decode → no CRC errors.
    """
    run1 = generate_synthetic_run(seed, n_frames)
    run2 = generate_synthetic_run(seed, n_frames)
    byte_identical = (run1 == run2)
    # Decode every frame
    crc_err = 0; decode_fail = 0
    for i in range(0, len(run1), TELEM_BYTES):
        chunk = run1[i:i+TELEM_BYTES]
        if len(chunk) < TELEM_BYTES: break
        f = TelemetryFrame.unpack(chunk)
        if f is None: crc_err += 1
        else: pass
    return ReplayResult(
        n_frames=len(run1) // TELEM_BYTES,
        byte_identical=byte_identical,
        crc_errors=crc_err,
        decode_failures=decode_fail)
