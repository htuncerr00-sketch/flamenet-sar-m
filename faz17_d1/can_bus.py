"""
hardware/can_bus.py — CAN Bus Abstraction (Mock + Real)
==========================================================
TWAI/CAN 2.0B, 29-bit ID, 1Mbps. Per-priority queues.

Frame format (standard ESP32-S3 TWAI):
  CANFrame(id, data[0..8], dlc, is_extended)
"""
from __future__ import annotations
import threading, time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, List, Optional
import numpy as np

class CANPriority(IntEnum):
    SAFETY    = 0   # E-stop, alarms — highest
    MOTION    = 1   # Position commands
    TELEMETRY = 2   # State feedback
    DIAGNOSTIC= 3   # Logging

@dataclass(slots=True)
class CANFrame:
    can_id:     int
    data:       bytes
    dlc:        int = 0
    extended:   bool = True
    timestamp:  float = 0.0

    def __post_init__(self):
        if self.dlc == 0: self.dlc = len(self.data)
        if self.timestamp == 0.0: self.timestamp = time.monotonic()

    @property
    def priority(self) -> CANPriority:
        # Top 3 bits of 29-bit ID encode priority
        return CANPriority((self.can_id >> 26) & 0x7) if self.extended else CANPriority.TELEMETRY


class CANBusBase(ABC):
    @abstractmethod
    def open(self, channel: str = "can0", bitrate: int = 1_000_000) -> bool: ...
    @abstractmethod
    def close(self) -> None: ...
    @abstractmethod
    def send(self, frame: CANFrame) -> bool: ...
    @abstractmethod
    def recv(self, timeout_s: float = 0.1) -> Optional[CANFrame]: ...

    @property
    @abstractmethod
    def n_tx(self) -> int: ...
    @property
    @abstractmethod
    def n_rx(self) -> int: ...
    @property
    @abstractmethod
    def n_errors(self) -> int: ...


class MockCANBus(CANBusBase):
    """In-memory CAN simulator with realistic latency."""
    def __init__(self, latency_us: float = 100.0, error_rate: float = 0.0,
                 seed: int = 42):
        self._lat = latency_us / 1e6
        self._err_rate = error_rate
        self._rng = np.random.default_rng(seed)
        self._tx_q: deque = deque(maxlen=10_000)
        self._rx_q: deque = deque(maxlen=10_000)
        self._open = False
        self._n_tx = 0; self._n_rx = 0; self._n_err = 0
        self._lock = threading.Lock()

    def open(self, channel: str = "can0", bitrate: int = 1_000_000) -> bool:
        with self._lock: self._open = True
        return True

    def close(self) -> None:
        with self._lock: self._open = False

    def send(self, frame: CANFrame) -> bool:
        with self._lock:
            if not self._open: return False
            if float(self._rng.uniform()) < self._err_rate:
                self._n_err += 1; return False
            time.sleep(self._lat)
            # Echo to rx for loopback testing
            self._rx_q.append(frame)
            self._n_tx += 1
            return True

    def recv(self, timeout_s: float = 0.1) -> Optional[CANFrame]:
        t0 = time.monotonic()
        while True:
            with self._lock:
                if self._rx_q:
                    self._n_rx += 1
                    return self._rx_q.popleft()
                if not self._open:
                    return None
            if time.monotonic() - t0 > timeout_s:
                return None
            time.sleep(0.001)

    @property
    def n_tx(self) -> int: return self._n_tx
    @property
    def n_rx(self) -> int: return self._n_rx
    @property
    def n_errors(self) -> int: return self._n_err


class RealCANBus(CANBusBase):
    """SocketCAN (Linux) wrapper via python-can."""
    def __init__(self):
        self._bus = None
        self._n_tx = 0; self._n_rx = 0; self._n_err = 0

    def open(self, channel: str = "can0", bitrate: int = 1_000_000) -> bool:
        try:
            import can
            self._bus = can.interface.Bus(
                channel=channel, bustype='socketcan', bitrate=bitrate)
            return True
        except Exception:
            return False

    def close(self) -> None:
        if self._bus is not None:
            try: self._bus.shutdown()
            except Exception: pass
            self._bus = None

    def send(self, frame: CANFrame) -> bool:
        if self._bus is None: return False
        try:
            import can
            msg = can.Message(
                arbitration_id=frame.can_id, data=frame.data,
                is_extended_id=frame.extended,
                dlc=frame.dlc)
            self._bus.send(msg, timeout=0.05)
            self._n_tx += 1
            return True
        except Exception:
            self._n_err += 1
            return False

    def recv(self, timeout_s: float = 0.1) -> Optional[CANFrame]:
        if self._bus is None: return None
        try:
            msg = self._bus.recv(timeout=timeout_s)
            if msg is None: return None
            self._n_rx += 1
            return CANFrame(
                can_id=msg.arbitration_id, data=bytes(msg.data),
                dlc=msg.dlc, extended=msg.is_extended_id,
                timestamp=msg.timestamp)
        except Exception:
            self._n_err += 1
            return None

    @property
    def n_tx(self) -> int: return self._n_tx
    @property
    def n_rx(self) -> int: return self._n_rx
    @property
    def n_errors(self) -> int: return self._n_err
