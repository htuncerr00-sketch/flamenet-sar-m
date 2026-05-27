"""
hardware/esp32_link.py — Unified ESP32 Link (Mock + Real, identical API)
==========================================================================
Production-grade hardware abstraction.

Mock vs Real: identical interface, drop-in replacement.
  - MockESP32Link:  1kHz synthetic telemetry, deterministic with seed
  - RealESP32Link:  USB-CDC @ 921600 baud, 0xAA55 frame sync + CRC-16

Telemetry V2 (64B, from Faz 16): '>QHHffffffffffHH'

Thread safety:
  - Listeners stored under lock
  - Callbacks fired OUTSIDE lock (deadlock-free guarantee)
  - put_nowait() to listener queues — never blocks reader thread
"""
from __future__ import annotations
import struct, threading, time, math, queue
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional
import numpy as np

# ── Telemetry V2 format (from Faz 16) ────────────────────────────
TELEM_FMT = ">QHHffffffffffffHH"
TELEM_STRUCT = struct.Struct(TELEM_FMT)
TELEM_BYTES = TELEM_STRUCT.size   # 64

assert TELEM_BYTES == 64, f"TELEM_BYTES should be 64, got {TELEM_BYTES}"

def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
        crc &= 0xFFFF
    return crc


@dataclass(slots=True)
class TelemetryFrame:
    ts_us:     int
    seq:       int
    flags:     int
    x_mm:      float
    a_deg:     float
    T_N:       float
    rpm:       float
    vib_x:     float
    vib_y:     float
    vib_z:     float
    temp_K:    float
    current_A: float
    alpha:     float
    quality:   float
    spare:     float = 0.0

    def pack(self) -> bytes:
        raw = TELEM_STRUCT.pack(
            int(self.ts_us) & 0xFFFFFFFFFFFFFFFF,
            int(self.seq) & 0xFFFF,
            int(self.flags) & 0xFFFF,
            float(self.x_mm), float(self.a_deg), float(self.T_N),
            float(self.rpm),
            float(self.vib_x), float(self.vib_y), float(self.vib_z),
            float(self.temp_K), float(self.current_A),
            float(self.alpha), float(self.quality), float(self.spare),
            0, 0)
        crc = crc16_ccitt(raw[:-2])
        return raw[:-2] + struct.pack(">H", crc)

    @classmethod
    def unpack(cls, data: bytes) -> Optional["TelemetryFrame"]:
        if len(data) < TELEM_BYTES:
            return None
        crc_stored = struct.unpack(">H", data[-2:])[0]
        if crc16_ccitt(data[:-2]) != crc_stored:
            return None
        f = TELEM_STRUCT.unpack(data)
        return cls(ts_us=f[0], seq=f[1], flags=f[2],
                   x_mm=f[3], a_deg=f[4], T_N=f[5], rpm=f[6],
                   vib_x=f[7], vib_y=f[8], vib_z=f[9],
                   temp_K=f[10], current_A=f[11],
                   alpha=f[12], quality=f[13], spare=f[14])


class ConnectionState(Enum):
    DISCONNECTED = 0
    CONNECTING   = 1
    CONNECTED    = 2
    ERROR        = 3


class ESP32LinkBase(ABC):
    """Abstract base — Mock and Real implementations share this API."""

    def __init__(self):
        self._listeners: List[queue.Queue] = []
        self._lock = threading.Lock()
        self._state = ConnectionState.DISCONNECTED
        self._stop  = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._n_frames = 0
        self._n_crc_err = 0
        self._n_sync_err = 0
        self._last_seq = -1

    @abstractmethod
    def connect(self) -> bool: ...
    @abstractmethod
    def disconnect(self) -> None: ...
    @abstractmethod
    def send_command(self, cmd: bytes) -> bool: ...

    @property
    def state(self) -> ConnectionState: return self._state
    @property
    def n_frames(self) -> int: return self._n_frames
    @property
    def n_crc_errors(self) -> int: return self._n_crc_err
    @property
    def n_sync_errors(self) -> int: return self._n_sync_err

    def subscribe(self, q: queue.Queue) -> None:
        """Register a queue to receive frames. Thread-safe."""
        with self._lock:
            if q not in self._listeners:
                self._listeners.append(q)

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def _dispatch(self, frame: TelemetryFrame) -> None:
        """Fan-out to all subscriber queues. Called OUTSIDE caller's lock."""
        with self._lock:
            listeners = list(self._listeners)   # snapshot
        # Now lock released — push to queues
        for q in listeners:
            try:
                q.put_nowait(frame)
            except queue.Full:
                pass   # listener too slow — drop, never block


class MockESP32Link(ESP32LinkBase):
    """
    Synthetic telemetry generator.
    1kHz nominal rate, deterministic with seed.
    Simulates: helical winding motion + realistic noise.
    """
    def __init__(self, seed: int = 42, rate_hz: float = 1000.0):
        super().__init__()
        self._seed = seed
        self._rate = rate_hz
        self._rng  = np.random.default_rng(seed)
        self._t0   = 0
        self._seq  = 0

    def connect(self) -> bool:
        self._state = ConnectionState.CONNECTING
        self._stop.clear()
        self._t0 = time.monotonic()
        self._rng = np.random.default_rng(self._seed)
        self._seq = 0
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="MockESP32Link")
        self._thread.start()
        self._state = ConnectionState.CONNECTED
        return True

    def disconnect(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        self._state = ConnectionState.DISCONNECTED

    def send_command(self, cmd: bytes) -> bool:
        if self._state != ConnectionState.CONNECTED:
            return False
        # Mock: accept all commands, do nothing
        return True

    def _loop(self) -> None:
        period = 1.0 / self._rate
        next_t = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            t   = now - self._t0
            # Synthetic helical motion
            x = 40.0 + 150.0 * (1 + math.sin(t * 0.5)) / 2
            a = (t * 30.0) % 360.0          # 5 RPM
            T = 15.0 + float(self._rng.normal(0, 0.3))
            rpm = 5.0
            vib_x = float(self._rng.normal(0, 0.05))
            vib_y = float(self._rng.normal(0, 0.05))
            vib_z = float(self._rng.normal(0, 0.05))
            temp_K = 295.15 + 0.1 * t      # slow rise
            current = 2.5 + float(self._rng.normal(0, 0.1))
            alpha = min(1.0, t / 3600.0)
            quality = 92.0 + float(self._rng.normal(0, 1.0))
            frame = TelemetryFrame(
                ts_us=int(t * 1e6), seq=self._seq & 0xFFFF, flags=1,
                x_mm=x, a_deg=a, T_N=T, rpm=rpm,
                vib_x=vib_x, vib_y=vib_y, vib_z=vib_z,
                temp_K=temp_K, current_A=current,
                alpha=alpha, quality=quality)
            self._n_frames += 1
            self._seq += 1
            self._dispatch(frame)
            next_t += period
            sleep_time = next_t - time.monotonic()
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                next_t = time.monotonic()   # catch up


class RealESP32Link(ESP32LinkBase):
    """
    USB-CDC link to real ESP32 at 921600 baud.
    Binary framing: 0xAA55 magic + 64B payload + 2B CRC-16.
    """
    MAGIC = b'\xaa\x55'

    def __init__(self, port: str = "/dev/ttyUSB0", baud: int = 921600,
                 timeout_s: float = 0.5):
        super().__init__()
        self._port_path = port
        self._baud = baud
        self._timeout = timeout_s
        self._port = None   # pyserial Serial object (lazy import)

    def connect(self) -> bool:
        self._state = ConnectionState.CONNECTING
        try:
            import serial   # pyserial — optional dep
            self._port = serial.Serial(
                self._port_path, self._baud,
                timeout=self._timeout, write_timeout=self._timeout)
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="RealESP32Link")
            self._thread.start()
            self._state = ConnectionState.CONNECTED
            return True
        except Exception:
            self._state = ConnectionState.ERROR
            return False

    def disconnect(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._port is not None:
            try: self._port.close()
            except Exception: pass
        self._state = ConnectionState.DISCONNECTED

    def send_command(self, cmd: bytes) -> bool:
        if self._port is None or self._state != ConnectionState.CONNECTED:
            return False
        try:
            self._port.write(cmd)
            return True
        except Exception:
            self._state = ConnectionState.ERROR
            return False

    def _loop(self) -> None:
        if self._port is None: return
        buf = bytearray()
        while not self._stop.is_set():
            try:
                chunk = self._port.read(128)
                if not chunk:
                    continue
                buf += chunk
                while len(buf) >= len(self.MAGIC) + TELEM_BYTES:
                    # Sync to magic
                    idx = buf.find(self.MAGIC)
                    if idx < 0:
                        # No magic in buffer — drop all but last byte
                        self._n_sync_err += 1
                        buf = buf[-1:]
                        break
                    if idx > 0:
                        self._n_sync_err += 1
                        buf = buf[idx:]
                        continue
                    # Have magic at index 0
                    if len(buf) < len(self.MAGIC) + TELEM_BYTES:
                        break
                    payload = bytes(buf[len(self.MAGIC):
                                        len(self.MAGIC) + TELEM_BYTES])
                    frame = TelemetryFrame.unpack(payload)
                    buf = buf[len(self.MAGIC) + TELEM_BYTES:]
                    if frame is None:
                        self._n_crc_err += 1
                        continue
                    self._n_frames += 1
                    self._dispatch(frame)
            except Exception:
                self._state = ConnectionState.ERROR
                break
