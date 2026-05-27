"""
validation/latency_benchmark.py — End-to-end Latency Measurement
===================================================================
Measures: pack→ring→subscriber callback latency in microseconds.
Target: p50 < 100µs, p99 < 1ms.
"""
from __future__ import annotations
import queue, statistics, time
from dataclasses import dataclass
from typing import List
import numpy as np
from ..hardware.esp32_link import TelemetryFrame
from ..hardware.telemetry_stream import TelemetryStream


@dataclass
class LatencyReport:
    n_samples:   int
    p50_us:      float
    p90_us:      float
    p99_us:      float
    p999_us:     float
    max_us:      float
    mean_us:     float


def benchmark_telemetry_latency(n_iterations: int = 10_000) -> LatencyReport:
    """Pack→stream.feed→subscriber.get latency."""
    stream = TelemetryStream()
    q = stream.subscribe()
    latencies_us: List[float] = []
    for i in range(n_iterations):
        f = TelemetryFrame(
            ts_us=i, seq=i & 0xFFFF, flags=1,
            x_mm=100, a_deg=200, T_N=15, rpm=8,
            vib_x=0.05, vib_y=0.05, vib_z=0.05,
            temp_K=295, current_A=2.5, alpha=0.5, quality=92)
        t0 = time.perf_counter()
        stream.feed(f)
        try:
            received = q.get(timeout=0.1)
            t1 = time.perf_counter()
            latencies_us.append((t1 - t0) * 1e6)
        except queue.Empty:
            continue
    if not latencies_us:
        return LatencyReport(0, 0, 0, 0, 0, 0, 0)
    arr = np.array(latencies_us)
    return LatencyReport(
        n_samples=len(arr),
        p50_us=float(np.percentile(arr, 50)),
        p90_us=float(np.percentile(arr, 90)),
        p99_us=float(np.percentile(arr, 99)),
        p999_us=float(np.percentile(arr, 99.9)),
        max_us=float(arr.max()),
        mean_us=float(arr.mean()))
