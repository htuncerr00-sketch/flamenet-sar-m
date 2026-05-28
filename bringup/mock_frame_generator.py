"""
bringup/mock_frame_generator.py — Synthetic ESP32 telemetry frame generator
============================================================================
Pure Python, no hardware required.  Generates valid wire frames with
optional fault injection for dry-run validation of the commissioning toolchain.

Design constraints:
  - No external dependencies (stdlib only)
  - Thread-safe: all mutable state behind a lock
  - Deterministic when seed is given
  - Fault injection is time-based (fires at elapsed_us thresholds)

Wire format produced (big-endian, 66 bytes):
  0xAA 0x55 | 64-byte payload | CRC-16/CCITT at payload[62..63]
  struct '>QHHffffffffffffHH'

Usage as module:
  from mock_frame_generator import FrameGenerator, MockBootLog
  gen = FrameGenerator(crc_corrupt_rate=0.05, jitter_us=50)
  frame_bytes = gen.next_frame_bytes(elapsed_us=1_000_000)

Usage as standalone:
  python mock_frame_generator.py --test
"""
from __future__ import annotations

import math
import random
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

# ── wire constants ────────────────────────────────────────────────────
MAGIC       = b"\xaa\x55"
WIRE_LEN    = 66
PAYLOAD_LEN = 64
CRC_DATA_LEN = 62
TELEM_FMT   = struct.Struct(">QHHffffffffffffHH")
assert TELEM_FMT.size == PAYLOAD_LEN

# ── flag bits (must match verify_bringup.py) ──────────────────────────
F_BOOT_OK        = 1 << 0
F_TENSION_OK     = 1 << 1
F_TEMP_OK        = 1 << 2
F_RPM_OK         = 1 << 3
F_VIBRATION_OK   = 1 << 4
F_HOMED          = 1 << 5
F_RUNNING        = 1 << 6
F_ESTOP          = 1 << 7
F_SAFE_HALT      = 1 << 8
F_INA226_OK      = 1 << 9
F_IMU_OK         = 1 << 10
F_THERMAL_OK     = 1 << 11
F_BROWNOUT       = 1 << 12
F_THERMAL_SHUT   = 1 << 13
F_WATCHDOG_RST   = 1 << 14

# ── baseline sensor values (realistic idle bench values) ──────────────
BASELINE_FLAGS = (F_BOOT_OK | F_INA226_OK | F_IMU_OK | F_THERMAL_OK |
                  F_TENSION_OK | F_TEMP_OK | F_RPM_OK | F_VIBRATION_OK)
BASELINE_TEMP_K    = 298.15   # 25 °C — room temperature
BASELINE_CURRENT_A = 4.969    # nominal bench idle current (matches field test)
BASELINE_VIB_X     = 0.0118   # g — idle MPU6050 noise floor
BASELINE_VIB_Y     = 0.0050   # small non-zero to avoid spurious freeze detection
BASELINE_VIB_Z     = 0.0050
BASELINE_QUALITY   = 92.0     # 3 sensors healthy: 17 + 25*3 = 92


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT: poly 0x1021, init 0xFFFF, no reflection."""
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _build_frame(ts_us: int, seq: int, flags: int,
                 temp_K: float, current_A: float,
                 vib_x: float, vib_y: float, vib_z: float,
                 quality: float,
                 corrupt_crc: bool = False,
                 prepend_garbage: bytes = b"") -> bytes:
    """Construct a single wire frame (66 bytes), optionally with bad CRC."""
    payload = bytearray(PAYLOAD_LEN)
    TELEM_FMT.pack_into(payload, 0,
        ts_us, seq, flags,
        0.0,   # x_mm
        0.0,   # a_deg
        15.0,  # T_N (synthetic until HX711 wired)
        0.0,   # rpm
        vib_x, vib_y, vib_z,
        temp_K, current_A,
        0.0,   # alpha
        quality,
        0.0,   # spare
        0,     # reserved
        0,     # crc placeholder
    )
    crc = crc16_ccitt(bytes(payload[:CRC_DATA_LEN]))
    if corrupt_crc:
        crc = (crc ^ 0xFFFF) & 0xFFFF  # flip all bits → guaranteed wrong CRC
    struct.pack_into(">H", payload, CRC_DATA_LEN, crc)
    return prepend_garbage + MAGIC + bytes(payload)


@dataclass
class FaultSchedule:
    """Time-based fault injection schedule (all times in microseconds)."""

    # Rate-based faults (probability per frame, 0.0–1.0)
    crc_corrupt_rate: float = 0.0    # fraction of frames with flipped CRC
    desync_rate:      float = 0.0    # fraction of frames with N random bytes prepended

    # Flag-based faults (set flags for duration starting at start_us)
    watchdog_start_us:   int = -1    # set F_WATCHDOG_RST for watchdog_dur_us
    watchdog_dur_us:     int = 2_000_000   # 2 s default
    brownout_start_us:   int = -1    # set F_BROWNOUT for brownout_dur_us
    brownout_dur_us:     int = 1_000_000
    safe_halt_start_us:  int = -1    # set F_SAFE_HALT for safe_halt_dur_us
    safe_halt_dur_us:    int = 5_000_000

    # Sensor freeze (INA226 value stops changing at freeze_start_us)
    ina226_freeze_start_us: int = -1
    imu_freeze_start_us:    int = -1
    thermal_freeze_start_us: int = -1

    # Sensor dropout (flag cleared, field goes to LKG)
    ina226_drop_start_us: int = -1
    ina226_drop_dur_us:   int = 5_000_000
    imu_drop_start_us:    int = -1
    imu_drop_dur_us:      int = 5_000_000
    thermal_drop_start_us: int = -1
    thermal_drop_dur_us:  int = 5_000_000

    # Jitter in µs (±jitter_us added to ts_us, simulating FreeRTOS tick variation)
    jitter_us: int = 0

    # Low frame rate (deliver only this fraction of 1kHz frames)
    delivery_fraction: float = 1.0  # 1.0 = full 1kHz; 0.7 = 700 Hz effective

    # Delivery stop (no frames after this time)
    no_frames_after_us: int = -1


class FrameGenerator:
    """
    Generates synthetic telemetry frames with controllable fault injection.

    Thread-safe.  Call next_frame_bytes(elapsed_us) from any thread.
    """

    def __init__(
        self,
        seed: int = 42,
        faults: Optional[FaultSchedule] = None,
    ) -> None:
        self._rng    = random.Random(seed)
        self._faults = faults or FaultSchedule()
        self._seq    = 0
        self._lock   = threading.Lock()

        # LKG state for sensor freeze / dropout
        self._lkg_current_A = BASELINE_CURRENT_A
        self._lkg_temp_K    = BASELINE_TEMP_K
        self._lkg_vib_x     = BASELINE_VIB_X

    def next_frame_bytes(self, elapsed_us: int) -> bytes:
        """
        Generate the next frame bytes for the given elapsed time.

        Returns empty bytes if no_frames_after_us is set and elapsed > threshold.
        May return > 66 bytes if desync garbage is prepended.
        May return a frame with bad CRC if crc_corrupt_rate fires.
        """
        with self._lock:
            f = self._faults

            # Delivery stop
            if f.no_frames_after_us >= 0 and elapsed_us >= f.no_frames_after_us:
                return b""

            # Low delivery rate: skip some frames randomly
            if f.delivery_fraction < 1.0:
                if self._rng.random() > f.delivery_fraction:
                    return b""

            # Build flags
            flags = BASELINE_FLAGS

            # Watchdog flag window
            if (f.watchdog_start_us >= 0
                    and f.watchdog_start_us <= elapsed_us
                    < f.watchdog_start_us + f.watchdog_dur_us):
                flags |= F_WATCHDOG_RST

            # Brownout flag window
            if (f.brownout_start_us >= 0
                    and f.brownout_start_us <= elapsed_us
                    < f.brownout_start_us + f.brownout_dur_us):
                flags |= F_BROWNOUT

            # SAFE_HALT flag window
            if (f.safe_halt_start_us >= 0
                    and f.safe_halt_start_us <= elapsed_us
                    < f.safe_halt_start_us + f.safe_halt_dur_us):
                flags |= F_SAFE_HALT

            # INA226 dropout
            ina_ok = True
            if (f.ina226_drop_start_us >= 0
                    and f.ina226_drop_start_us <= elapsed_us
                    < f.ina226_drop_start_us + f.ina226_drop_dur_us):
                flags &= ~F_INA226_OK
                ina_ok = False

            # IMU dropout
            imu_ok = True
            if (f.imu_drop_start_us >= 0
                    and f.imu_drop_start_us <= elapsed_us
                    < f.imu_drop_start_us + f.imu_drop_dur_us):
                flags &= ~F_IMU_OK
                imu_ok = False

            # Thermal dropout
            therm_ok = True
            if (f.thermal_drop_start_us >= 0
                    and f.thermal_drop_start_us <= elapsed_us
                    < f.thermal_drop_start_us + f.thermal_drop_dur_us):
                flags &= ~F_THERMAL_OK
                therm_ok = False

            # Quality field reflects sensor health (AD-020)
            n_healthy = sum([ina_ok, imu_ok, therm_ok])
            quality = 17.0 + 25.0 * n_healthy

            # Sensor values with optional drift/noise
            if f.ina226_freeze_start_us >= 0 and elapsed_us >= f.ina226_freeze_start_us:
                current_A = self._lkg_current_A  # frozen
            else:
                # Add small noise to prevent accidental freeze detection on healthy path
                current_A = BASELINE_CURRENT_A + self._rng.gauss(0, 0.001)
                self._lkg_current_A = current_A

            if f.thermal_freeze_start_us >= 0 and elapsed_us >= f.thermal_freeze_start_us:
                temp_K = self._lkg_temp_K  # frozen
            else:
                temp_K = BASELINE_TEMP_K + self._rng.gauss(0, 0.01)
                self._lkg_temp_K = temp_K

            if f.imu_freeze_start_us >= 0 and elapsed_us >= f.imu_freeze_start_us:
                vib_x = self._lkg_vib_x  # frozen
                vib_y = BASELINE_VIB_Y   # also freeze y,z
                vib_z = BASELINE_VIB_Z
            else:
                vib_x = BASELINE_VIB_X + self._rng.gauss(0, 0.001)
                self._lkg_vib_x = vib_x
                vib_y = BASELINE_VIB_Y + self._rng.gauss(0, 0.001)
                vib_z = BASELINE_VIB_Z + self._rng.gauss(0, 0.001)

            # Apply jitter to ts_us
            ts_us = elapsed_us
            if f.jitter_us > 0:
                ts_us += self._rng.randint(-f.jitter_us, f.jitter_us)
                ts_us = max(0, ts_us)

            # CRC corruption
            corrupt = (self._rng.random() < f.crc_corrupt_rate)

            # Desync: prepend random bytes
            garbage = b""
            if f.desync_rate > 0 and self._rng.random() < f.desync_rate:
                n_garbage = self._rng.randint(1, 8)
                garbage = bytes(self._rng.randint(0, 255) for _ in range(n_garbage))

            seq = self._seq
            self._seq = (self._seq + 1) & 0xFFFF

            return _build_frame(
                ts_us=ts_us,
                seq=seq,
                flags=flags,
                temp_K=temp_K,
                current_A=current_A,
                vib_x=vib_x,
                vib_y=BASELINE_VIB_Y,
                vib_z=BASELINE_VIB_Z,
                quality=quality,
                corrupt_crc=corrupt,
                prepend_garbage=garbage,
            )


class MockBootLog:
    """
    Synthetic UART0 debug log for serial_capture.py testing.

    Call get_lines_at(elapsed_s) to get any log lines due at that time.
    """

    # (delay_s, log_line)
    _EVENTS: list[tuple[float, str]] = [
        (0.00, "rst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)"),
        (0.01, "configsip: 0, SPIWP:0xee"),
        (0.02, "clk_drv:0x00,q_drv:0x00,d_drv:0x00,cs0_drv:0x00,hd_drv:0x00,wp_drv:0x00"),
        (0.03, "mode:DIO, clock div:2"),
        (0.04, "load:0x3fff0018,len:4"),
        (0.05, "load:0x3fff001c,len:7552"),
        (0.10, "I (123) boot: Chip is ESP32-D0WDQ6 (revision 1)"),
        (0.11, "I (124) boot: Secure Boot disabled"),
        (0.20, "I (200) heap_init: At 3FFAE6E0 len 00001920 (6 KiB): DRAM"),
        (0.25, "I (250) heap_init: At 3FFB5D60 len 0002A2A0 (168 KiB): DRAM"),
        (0.30, "I (300) cpu_start: App cpu up."),
        (0.40, "I (400) app_main: Faz 19C — Filament Winding Telemetry"),
        (0.41, "I (401) app_main: Reset reason: PowerOn (1)"),
        (0.42, "I (402) uart_stream: UART1 @ 921600 baud, TX=GPIO17"),
        (0.43, "I (403) telemetry_task: telemetry task started"),
        (0.44, "I (444) sensor_task: sensor I2C task started @ 100 Hz"),
        (0.45, "I (445) health_monitor: started @ 0.2 Hz"),
        # Health monitor first tick at ~5 s
        (5.00, "I (5000) health_monitor: free heap: 183424 bytes"),
        (5.01, "I (5001) health_monitor: telem stack HWM: 1892 bytes free"),
        (5.02, "I (5002) health_monitor: sens stack HWM: 2104 bytes free"),
        # Periodic health ticks
        (10.00, "I (10000) health_monitor: free heap: 183412 bytes"),
        (15.00, "I (15000) health_monitor: free heap: 183408 bytes"),
        (20.00, "I (20000) health_monitor: free heap: 183400 bytes"),
        (25.00, "I (25000) health_monitor: free heap: 183396 bytes"),
        (30.00, "I (30000) health_monitor: free heap: 183392 bytes"),
    ]

    def __init__(self) -> None:
        self._emitted: set[int] = set()

    def get_lines_at(self, elapsed_s: float) -> list[str]:
        """Returns log lines due at or before elapsed_s (each line returned only once)."""
        lines = []
        for i, (delay, line) in enumerate(self._EVENTS):
            if elapsed_s >= delay and i not in self._emitted:
                self._emitted.add(i)
                lines.append(line + "\r\n")
        return lines


class MockBootLogFaultMode(MockBootLog):
    """Boot log variant that injects fault events."""

    def __init__(self, fault: str = "guru_meditation") -> None:
        super().__init__()
        self._fault = fault

    def get_lines_at(self, elapsed_s: float) -> list[str]:
        lines = super().get_lines_at(elapsed_s)
        # Inject fault events at specific times
        if self._fault == "guru_meditation" and 2.0 <= elapsed_s < 2.1:
            lines.append("Guru Meditation Error: Core  0 panic'ed (LoadProhibited)\r\n")
            lines.append("Backtrace:0x400d1f4c:0x3ffb3050\r\n")
        elif self._fault == "brownout" and 2.0 <= elapsed_s < 2.1:
            lines.append("I (2000) brownout: Brownout detector was triggered\r\n")
            lines.append("rst:0x10 (RTCWDT_RTC_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)\r\n")
        elif self._fault == "watchdog" and 2.0 <= elapsed_s < 2.1:
            lines.append("I (2000) task_wdt: Task watchdog got triggered.\r\n")
            lines.append("rst:0x4 (TG0WDT_SYS_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)\r\n")
        return lines


# ── self-test ─────────────────────────────────────────────────────────

def _self_test() -> None:
    print("mock_frame_generator self-test…")
    gen = FrameGenerator(seed=42)

    # Generate 10 clean frames and verify CRC
    for i in range(10):
        b = gen.next_frame_bytes(i * 1_000)
        assert len(b) == WIRE_LEN, f"Frame {i}: expected {WIRE_LEN} bytes, got {len(b)}"
        payload = b[2:]
        crc_stored = struct.unpack_from(">H", payload, CRC_DATA_LEN)[0]
        crc_calc   = crc16_ccitt(payload[:CRC_DATA_LEN])
        assert crc_stored == crc_calc, f"Frame {i}: CRC mismatch"
    print(f"  {10} clean frames: CRC OK")

    # Corrupt CRC
    gen_corrupt = FrameGenerator(seed=42, faults=FaultSchedule(crc_corrupt_rate=1.0))
    b = gen_corrupt.next_frame_bytes(0)
    payload = b[2:]
    crc_stored = struct.unpack_from(">H", payload, CRC_DATA_LEN)[0]
    crc_calc   = crc16_ccitt(payload[:CRC_DATA_LEN])
    assert crc_stored != crc_calc, "CRC corruption did not work"
    print("  CRC corruption: OK")

    # Desync injection
    gen_desync = FrameGenerator(seed=42, faults=FaultSchedule(desync_rate=1.0))
    b = gen_desync.next_frame_bytes(0)
    assert len(b) > WIRE_LEN, "Desync should prepend bytes"
    magic_idx = b.find(b"\xaa\x55")
    assert magic_idx >= 0, "Magic not found in desync frame"
    print(f"  Desync injection: OK (garbage={magic_idx} bytes)")

    # SAFE_HALT flag
    gen_halt = FrameGenerator(seed=42, faults=FaultSchedule(safe_halt_start_us=0, safe_halt_dur_us=10_000_000))
    b = gen_halt.next_frame_bytes(1_000)
    payload = b[2:]
    flags = struct.unpack_from(">H", payload, 10)[0]   # flags at offset 10 in payload (after ts_us=8, seq=2)
    assert flags & (1 << 8), f"SAFE_HALT flag not set, flags=0x{flags:04x}"
    print(f"  SAFE_HALT flag: OK (flags=0x{flags:04x})")

    # No-frame delivery
    gen_stop = FrameGenerator(seed=42, faults=FaultSchedule(no_frames_after_us=5_000))
    b = gen_stop.next_frame_bytes(10_000)
    assert b == b"", "Should return empty after stop time"
    print("  No-frame delivery: OK")

    # Boot log
    log = MockBootLog()
    lines = log.get_lines_at(1.0)
    assert any("telemetry task started" in l for l in lines), "telemetry task line missing"
    print(f"  MockBootLog: OK ({len(lines)} lines at t=1.0s)")

    # Freeze: same value for 250 frames
    # temp_K field offset in payload: ts_us(8)+seq(2)+flags(2)+x_mm(4)+a_deg(4)+T_N(4)+rpm(4)
    #   +vib_x(4)+vib_y(4)+vib_z(4) = offset 40
    TEMP_K_PAYLOAD_OFFSET = 40
    gen_freeze = FrameGenerator(seed=42, faults=FaultSchedule(thermal_freeze_start_us=0))
    temps = [struct.unpack_from(">f", gen_freeze.next_frame_bytes(i*1000)[2:],
                                TEMP_K_PAYLOAD_OFFSET)[0]
             for i in range(250)]
    assert len(set(temps)) == 1, f"Thermal should be frozen, got {len(set(temps))} unique values"
    print(f"  Thermal freeze: OK ({len(set(temps))} unique value)")

    print("All self-tests PASS")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="Run self-tests")
    ap.add_argument("--dump", type=int, default=0, metavar="N", help="Dump N frame bytes (hex)")
    args = ap.parse_args()

    if args.test:
        _self_test()
    if args.dump > 0:
        gen = FrameGenerator()
        for i in range(args.dump):
            b = gen.next_frame_bytes(i * 1_000)
            print(b.hex())
