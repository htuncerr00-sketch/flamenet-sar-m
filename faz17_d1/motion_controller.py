"""
core/motion_controller.py — Motion State Machine + Command Dispatcher
=======================================================================
States: IDLE → HOMING → READY → RUNNING → PAUSED → STOPPING → IDLE
                                  ↓
                                ESTOP → IDLE (after reset)

Commands flow:  user → MotionController → ESP32Link.send_command()
                        ↓ also publishes
                        controller_state_q for UI
"""
from __future__ import annotations
import queue, threading, time
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, List, Optional
from ..hardware.esp32_link import ESP32LinkBase, TelemetryFrame
from .safety_controller import SafetyController


class MotionState(IntEnum):
    IDLE     = 0
    HOMING   = 1
    READY    = 2
    RUNNING  = 3
    PAUSED   = 4
    STOPPING = 5
    ESTOP    = 6


@dataclass(slots=True)
class MotionStatus:
    state:          MotionState = MotionState.IDLE
    x_mm:           float = 0.0
    a_deg:          float = 0.0
    feed_mm_s:      float = 0.0
    rpm:            float = 0.0
    tension_N:      float = 0.0
    line_number:    int   = 0
    program_lines:  int   = 0
    elapsed_s:      float = 0.0
    halted:         bool  = False
    halt_reason:    str   = ""


class MotionController:
    """
    Motion state machine.
    Thread-safe. State transitions atomic. Telemetry-driven updates.
    Safety integrated: ESTOP transition is irreversible from worker thread.
    """
    def __init__(self, link: ESP32LinkBase, safety: SafetyController):
        self._link = link
        self._safety = safety
        self._status = MotionStatus()
        self._lock = threading.Lock()
        self._gcode_lines: List[str] = []
        self._listeners: List[Callable[[MotionStatus], None]] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._telem_q = link.subscribe if hasattr(link, 'subscribe') else None
        self._start_time = 0.0
        # Subscribe to safety events
        self._safety.register_callback(self._on_safety_event)

    def add_listener(self, cb: Callable[[MotionStatus], None]) -> None:
        with self._lock:
            self._listeners.append(cb)

    def _on_safety_event(self, ev) -> None:
        """Called from safety thread, OUTSIDE its lock."""
        from .safety_controller import SafetyLevel
        if ev.level == SafetyLevel.FATAL:
            self._enter_estop(ev.code)

    def _enter_estop(self, reason: str) -> None:
        """Force ESTOP state. Idempotent."""
        with self._lock:
            if self._status.state == MotionState.ESTOP:
                return
            self._status.state = MotionState.ESTOP
            self._status.halted = True
            self._status.halt_reason = reason
            listeners = list(self._listeners)
        # ESTOP command to hardware (fire and forget)
        try:
            self._link.send_command(b'!')   # GRBL feedhold
            time.sleep(0.001)
            self._link.send_command(b'\x18')   # GRBL soft reset
        except Exception:
            pass
        # Notify listeners OUTSIDE lock
        for cb in listeners:
            try: cb(self._status)
            except Exception: pass

    def update_from_telemetry(self, frame: TelemetryFrame) -> None:
        """Update motion status from telemetry. Non-blocking."""
        with self._lock:
            self._status.x_mm = frame.x_mm
            self._status.a_deg = frame.a_deg
            self._status.rpm = frame.rpm
            self._status.tension_N = frame.T_N
            if self._start_time > 0:
                self._status.elapsed_s = time.monotonic() - self._start_time
            listeners = list(self._listeners)
        for cb in listeners:
            try: cb(self._status)
            except Exception: pass

    def home(self) -> bool:
        with self._lock:
            if self._status.state == MotionState.ESTOP: return False
            self._status.state = MotionState.HOMING
        ok = self._link.send_command(b'$H\n')
        time.sleep(0.5)   # mock: homing duration
        with self._lock:
            if self._status.state != MotionState.ESTOP:
                self._status.state = MotionState.READY
                self._status.x_mm = 0.0
                self._status.a_deg = 0.0
        return ok

    def jog(self, axis: str, distance_mm: float, feed_mm_min: float = 1000.0) -> bool:
        with self._lock:
            if self._status.state not in (MotionState.READY, MotionState.IDLE):
                return False
        if axis not in ('X', 'A'): return False
        cmd = f"$J=G91 {axis}{distance_mm:.3f} F{feed_mm_min:.0f}\n".encode()
        return self._link.send_command(cmd)

    def load_program(self, gcode: List[str]) -> int:
        with self._lock:
            self._gcode_lines = [l.strip() for l in gcode if l.strip() and not l.startswith(';')]
            self._status.program_lines = len(self._gcode_lines)
            self._status.line_number = 0
        return len(self._gcode_lines)

    def run(self) -> bool:
        with self._lock:
            if self._status.state != MotionState.READY: return False
            if not self._gcode_lines: return False
            self._status.state = MotionState.RUNNING
            self._start_time = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="MotionRun")
        self._thread.start()
        return True

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                if self._status.state != MotionState.RUNNING:
                    if self._status.state == MotionState.PAUSED:
                        time.sleep(0.05); continue
                    break
                if self._status.line_number >= len(self._gcode_lines):
                    self._status.state = MotionState.READY
                    break
                line = self._gcode_lines[self._status.line_number]
                self._status.line_number += 1
            try:
                self._link.send_command((line + '\n').encode())
            except Exception:
                pass
            time.sleep(0.01)   # mock: gcode pacing

    def pause(self) -> bool:
        with self._lock:
            if self._status.state != MotionState.RUNNING: return False
            self._status.state = MotionState.PAUSED
        return self._link.send_command(b'!')

    def resume(self) -> bool:
        with self._lock:
            if self._status.state != MotionState.PAUSED: return False
            self._status.state = MotionState.RUNNING
        return self._link.send_command(b'~')

    def stop(self) -> bool:
        with self._lock:
            self._status.state = MotionState.STOPPING
        self._stop.set()
        ok = self._link.send_command(b'\x18')   # soft reset
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        with self._lock:
            self._status.state = MotionState.IDLE
        return ok

    def emergency_stop(self, reason: str = "OPERATOR") -> None:
        self._enter_estop(reason)

    @property
    def status(self) -> MotionStatus:
        with self._lock:
            return MotionStatus(
                state=self._status.state,
                x_mm=self._status.x_mm, a_deg=self._status.a_deg,
                feed_mm_s=self._status.feed_mm_s,
                rpm=self._status.rpm, tension_N=self._status.tension_N,
                line_number=self._status.line_number,
                program_lines=self._status.program_lines,
                elapsed_s=self._status.elapsed_s,
                halted=self._status.halted,
                halt_reason=self._status.halt_reason)
