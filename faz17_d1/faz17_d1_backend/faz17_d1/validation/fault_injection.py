"""
validation/fault_injection.py — Deterministic Fault Scenarios
==================================================================
Each scenario: setup → inject → assert detection → cleanup.
"""
from __future__ import annotations
import struct, time
from dataclasses import dataclass
from typing import Callable, List, Optional
from ..hardware.esp32_link import TelemetryFrame, TELEM_BYTES, crc16_ccitt
from ..core.safety_controller import SafetyController, SafetyLevel


@dataclass
class FaultScenarioResult:
    name:        str
    passed:      bool
    detail:      str
    elapsed_ms:  float


class FaultInjector:
    """Battery of fault scenarios. All deterministic."""

    def __init__(self):
        self._results: List[FaultScenarioResult] = []

    def run_all(self) -> List[FaultScenarioResult]:
        self._results = []
        self._test_crc_corruption()
        self._test_tension_collapse()
        self._test_tension_overload()
        self._test_thermal_runaway()
        self._test_rpm_overspeed()
        self._test_x_out_of_range()
        self._test_vibration_overload()
        self._test_sequence_gap()
        self._test_advisory_rejected()
        self._test_estop_idempotent()
        return self._results

    def _test_crc_corruption(self):
        t0 = time.perf_counter()
        f = TelemetryFrame(ts_us=1000, seq=1, flags=1,
            x_mm=100, a_deg=200, T_N=15, rpm=8,
            vib_x=0, vib_y=0, vib_z=0, temp_K=295,
            current_A=2, alpha=0.5, quality=90)
        packed = f.pack()
        # Corrupt one byte
        corrupted = bytearray(packed)
        corrupted[10] ^= 0xFF
        decoded = TelemetryFrame.unpack(bytes(corrupted))
        passed = decoded is None
        self._results.append(FaultScenarioResult(
            "crc_corruption", passed,
            f"corrupted bit flip -> {'rejected' if passed else 'ACCEPTED (FAIL)'}",
            (time.perf_counter()-t0)*1000))

    def _test_tension_collapse(self):
        t0 = time.perf_counter()
        sc = SafetyController()
        events_captured = []
        sc.register_callback(lambda e: events_captured.append(e))
        f = TelemetryFrame(ts_us=1000, seq=1, flags=1,
            x_mm=100, a_deg=200, T_N=1.0, rpm=8,   # T_N=1 << T_MIN_N=3
            vib_x=0, vib_y=0, vib_z=0, temp_K=295,
            current_A=2, alpha=0.5, quality=90)
        sc.update_frame(f)
        passed = any(e.code == "TENSION_LOW" for e in events_captured)
        self._results.append(FaultScenarioResult(
            "tension_collapse", passed,
            f"T_N=1N captured {len(events_captured)} events, "
            f"TENSION_LOW={passed}",
            (time.perf_counter()-t0)*1000))

    def _test_tension_overload(self):
        t0 = time.perf_counter()
        sc = SafetyController()
        events_captured = []
        sc.register_callback(lambda e: events_captured.append(e))
        f = TelemetryFrame(ts_us=1000, seq=1, flags=1,
            x_mm=100, a_deg=200, T_N=45.0, rpm=8,   # > T_MAX_N
            vib_x=0, vib_y=0, vib_z=0, temp_K=295,
            current_A=2, alpha=0.5, quality=90)
        sc.update_frame(f)
        passed = any(e.code == "TENSION_HIGH" for e in events_captured)
        self._results.append(FaultScenarioResult(
            "tension_overload", passed,
            f"T_N=45N -> TENSION_HIGH={passed}",
            (time.perf_counter()-t0)*1000))

    def _test_thermal_runaway(self):
        t0 = time.perf_counter()
        sc = SafetyController()
        events_captured = []
        sc.register_callback(lambda e: events_captured.append(e))
        # Feed 5 frames with rapid temperature rise (>10K/s)
        for i in range(5):
            f = TelemetryFrame(ts_us=int(i * 100_000), seq=i, flags=1,
                x_mm=100, a_deg=200, T_N=15, rpm=8,
                vib_x=0, vib_y=0, vib_z=0,
                temp_K=295 + i * 5.0,   # 5K per 0.1s = 50K/s
                current_A=2, alpha=0.5, quality=90)
            sc.update_frame(f)
        passed = any(e.code == "THERMAL_RUNAWAY" for e in events_captured)
        self._results.append(FaultScenarioResult(
            "thermal_runaway", passed,
            f"dT/dt=50K/s -> THERMAL_RUNAWAY={passed}",
            (time.perf_counter()-t0)*1000))

    def _test_rpm_overspeed(self):
        t0 = time.perf_counter()
        sc = SafetyController()
        events_captured = []
        sc.register_callback(lambda e: events_captured.append(e))
        f = TelemetryFrame(ts_us=1000, seq=1, flags=1,
            x_mm=100, a_deg=200, T_N=15, rpm=300,   # > RPM_MAX=260
            vib_x=0, vib_y=0, vib_z=0, temp_K=295,
            current_A=2, alpha=0.5, quality=90)
        sc.update_frame(f)
        passed = any(e.code == "RPM_OVER" for e in events_captured)
        self._results.append(FaultScenarioResult(
            "rpm_overspeed", passed,
            f"RPM=300 -> RPM_OVER={passed}",
            (time.perf_counter()-t0)*1000))

    def _test_x_out_of_range(self):
        t0 = time.perf_counter()
        sc = SafetyController()
        events_captured = []
        sc.register_callback(lambda e: events_captured.append(e))
        f = TelemetryFrame(ts_us=1000, seq=1, flags=1,
            x_mm=500.0, a_deg=200, T_N=15, rpm=8,   # > X_MAX=395
            vib_x=0, vib_y=0, vib_z=0, temp_K=295,
            current_A=2, alpha=0.5, quality=90)
        sc.update_frame(f)
        passed = any(e.code == "X_OUT_OF_RANGE" for e in events_captured)
        self._results.append(FaultScenarioResult(
            "x_out_of_range", passed,
            f"x=500mm -> X_OUT_OF_RANGE={passed}",
            (time.perf_counter()-t0)*1000))

    def _test_vibration_overload(self):
        t0 = time.perf_counter()
        sc = SafetyController()
        events_captured = []
        sc.register_callback(lambda e: events_captured.append(e))
        f = TelemetryFrame(ts_us=1000, seq=1, flags=1,
            x_mm=100, a_deg=200, T_N=15, rpm=8,
            vib_x=2.0, vib_y=2.0, vib_z=2.0,   # RMS ~3.4g > 2.0g
            temp_K=295, current_A=2, alpha=0.5, quality=90)
        sc.update_frame(f)
        passed = any(e.code == "VIB_OVER" for e in events_captured)
        self._results.append(FaultScenarioResult(
            "vibration_overload", passed,
            f"vib_rms=3.46g -> VIB_OVER={passed}",
            (time.perf_counter()-t0)*1000))

    def _test_sequence_gap(self):
        t0 = time.perf_counter()
        from ..hardware.telemetry_stream import TelemetryStream
        stream = TelemetryStream()
        # Feed frames with gap
        for seq in [0, 1, 2, 5, 6, 7]:
            f = TelemetryFrame(ts_us=seq*1000, seq=seq, flags=1,
                x_mm=100, a_deg=200, T_N=15, rpm=8,
                vib_x=0, vib_y=0, vib_z=0, temp_K=295,
                current_A=2, alpha=0.5, quality=90)
            stream.feed(f)
        stats = stream.stats
        passed = stats.n_seq_gaps >= 1
        self._results.append(FaultScenarioResult(
            "sequence_gap", passed,
            f"injected gap 3,4 -> detected={stats.n_seq_gaps}",
            (time.perf_counter()-t0)*1000))

    def _test_advisory_rejected(self):
        t0 = time.perf_counter()
        sc = SafetyController()
        # Try advisory with tension=50N (way above max)
        ok = sc.validate_advisory(T_N=50.0, rpm=8, feed_mm_s=80.0)
        passed = (ok is False)
        self._results.append(FaultScenarioResult(
            "advisory_rejected", passed,
            f"advisory T=50N validate -> {'rejected' if not ok else 'ACCEPTED (FAIL)'}",
            (time.perf_counter()-t0)*1000))

    def _test_estop_idempotent(self):
        """Verify ESTOP can be called multiple times without state corruption."""
        t0 = time.perf_counter()
        from ..hardware.esp32_link import MockESP32Link
        from ..core.motion_controller import MotionController, MotionState
        link = MockESP32Link()
        link.connect()
        sc = SafetyController()
        mc = MotionController(link, sc)
        mc.emergency_stop("test1")
        s1 = mc.status.state
        mc.emergency_stop("test2")
        s2 = mc.status.state
        mc.emergency_stop("test3")
        s3 = mc.status.state
        link.disconnect()
        passed = (s1 == MotionState.ESTOP and s2 == MotionState.ESTOP and s3 == MotionState.ESTOP)
        self._results.append(FaultScenarioResult(
            "estop_idempotent", passed,
            f"3x ESTOP -> states: {s1.name},{s2.name},{s3.name}",
            (time.perf_counter()-t0)*1000))

    def n_passed(self) -> int:
        return sum(1 for r in self._results if r.passed)
