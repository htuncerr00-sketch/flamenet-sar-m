"""
thermal_camera_integration.py + thermocouple_logger.py (merged)
================================================================
Thermal camera (FLIR Lepton 3.5 or similar): 160×120 px @ 9Hz.
Thermocouple (MAX31856 Type-K): 1Hz, ±0.15°C accuracy.

Used for:
  - Mandrel surface temperature map
  - Hot spot detection (early thermal runaway)
  - Cure uniformity validation
"""
from __future__ import annotations
import math, threading, time
from collections import deque
from dataclasses import dataclass
from typing import List, Optional
import numpy as np

@dataclass(frozen=True, slots=True)
class ThermalFrame:
    timestamp_s:    float
    T_mean_C:       float
    T_max_C:        float
    T_min_C:        float
    T_std_C:        float
    n_hotspots:     int        # >threshold
    gradient_C_mm:  float      # Max spatial gradient
    uniformity:     float      # [0,1], 1=perfect

class ThermalCameraIntegration:
    """FLIR Lepton-like camera frame processor."""
    HOT_THRESHOLD_C = 60.0  # Hot spot detection
    UNIFORMITY_THRESHOLD = 5.0   # ±5°C max deviation for "uniform"

    def __init__(self, fps: float = 9.0):
        self._fps = fps
        self._frames: deque = deque(maxlen=500)
        self._lock = threading.Lock()

    def process_frame(self, T_field: np.ndarray, dx_mm: float = 1.5) -> ThermalFrame:
        """Process raw temperature field [°C] from camera."""
        ts = time.monotonic()
        T = T_field.flatten()
        T_max = float(T.max()); T_min = float(T.min())
        T_mean = float(T.mean()); T_std = float(T.std())
        # Hot spots
        n_hot = int(np.sum(T > self.HOT_THRESHOLD_C))
        # Spatial gradient
        if T_field.ndim == 2 and min(T_field.shape) > 1:
            gx = np.diff(T_field, axis=1) / dx_mm
            gy = np.diff(T_field, axis=0) / dx_mm
            grad = float(np.sqrt(np.max(gx**2 + 0) + np.max(gy**2 + 0)))
        else:
            grad = 0.0
        uni = max(0.0, 1.0 - T_std / self.UNIFORMITY_THRESHOLD)
        frame = ThermalFrame(timestamp_s=ts, T_mean_C=T_mean, T_max_C=T_max,
            T_min_C=T_min, T_std_C=T_std, n_hotspots=n_hot,
            gradient_C_mm=grad, uniformity=uni)
        with self._lock: self._frames.append(frame)
        return frame

    def summary(self) -> dict:
        with self._lock: frames = list(self._frames)
        if not frames: return {"n":0}
        arr = np.array([f.T_mean_C for f in frames])
        return {"n":len(frames), "T_mean":float(arr.mean()),
                "T_std":float(arr.std()),
                "n_hotspot_frames":sum(1 for f in frames if f.n_hotspots>0)}

class ThermocoupleLogger:
    """MAX31856 Type-K thermocouple, 1Hz."""
    def __init__(self):
        self._samples: deque = deque(maxlen=10_000)
        self._lock = threading.Lock()

    def log(self, T_C: float) -> None:
        with self._lock: self._samples.append((time.monotonic(), float(T_C)))

    def history(self) -> List[tuple]:
        with self._lock: return list(self._samples)

    def current(self) -> Optional[float]:
        with self._lock: return self._samples[-1][1] if self._samples else None
