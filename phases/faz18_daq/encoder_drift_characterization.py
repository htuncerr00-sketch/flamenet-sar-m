"""
encoder_drift_characterization.py — Encoder Drift Tracking
============================================================
ε_enc(t) = x_real - x_expected

Sources:
  - Quantization: 1/(4×steps_per_mm)
  - Mechanical: backlash, leadscrew wear
  - Thermal: encoder disk expansion
  - EMI: electrical noise → false pulses
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from typing import Optional
import numpy as np

@dataclass(frozen=True, slots=True)
class EncoderDriftReport:
    n_samples:        int
    rms_mm:           float
    bias_mm:          float          # Systematic offset
    std_mm:           float
    trend_mm_per_h:   float
    quantization_mm:  float
    pulse_loss_count: int
    is_stable:        bool

class EncoderDriftCharacterization:
    """Online encoder drift estimator."""
    def __init__(self, steps_per_mm: float = 80.0, window: int = 5000):
        self.spm = steps_per_mm
        self._buf: deque = deque(maxlen=window)
        self._x_cmd_buf: deque = deque(maxlen=window)
        self._t_buf: deque = deque(maxlen=window)
        self._pulse_loss = 0
        self._n = 0

    def add(self, x_real: float, x_cmd: float, t_s: float) -> None:
        err = x_real - x_cmd
        self._buf.append(err)
        self._x_cmd_buf.append(x_cmd)
        self._t_buf.append(t_s)
        self._n += 1
        # Pulse loss detect: large sudden jump
        if len(self._buf) >= 2 and abs(self._buf[-1] - self._buf[-2]) > 0.1:
            self._pulse_loss += 1

    def analyze(self) -> EncoderDriftReport:
        if self._n < 10:
            return EncoderDriftReport(0,0,0,0,0,0,0,True)
        err_arr = np.array(self._buf)
        t_arr = np.array(self._t_buf)
        rms = float(np.sqrt(np.mean(err_arr**2)))
        bias = float(err_arr.mean())
        std = float(err_arr.std())
        # Trend: mm/h
        if len(t_arr) > 10 and (t_arr[-1] - t_arr[0]) > 0:
            slope, _ = np.polyfit(t_arr, err_arr, 1)
            trend = float(slope * 3600.0)
        else:
            trend = 0.0
        q = 1.0 / (4.0 * self.spm)
        stable = abs(trend) < 0.1 and rms < 0.5
        return EncoderDriftReport(n_samples=self._n, rms_mm=rms, bias_mm=bias,
            std_mm=std, trend_mm_per_h=trend, quantization_mm=q,
            pulse_loss_count=self._pulse_loss, is_stable=stable)
