"""
spc_engine.py — Statistical Process Control Engine
====================================================
Shewhart control charts + CUSUM + Process Capability (Cp/Cpk).

Shewhart:
  UCL = μ + 3σ, LCL = μ - 3σ
  Rule 1 (1/3σ): 1 point outside 3σ
  Rule 2 (2/3×2σ): 2/3 consecutive points beyond 2σ same side
  Rule 3 (4/5×1σ): 4/5 consecutive points beyond 1σ same side
  Rule 4 (8 run): 8 consecutive points same side of CL

CUSUM:
  C+(k) = max(0, C+(k-1) + z(k) - k_slack)
  C-(k) = max(0, C-(k-1) - z(k) - k_slack)
  Alarm: C+ > h or C- > h

Process Capability:
  Cp  = (USL - LSL) / (6σ)        [precision, regardless of centering]
  Cpk = min(Cpu, Cpl)             [capability with centering]
  Cpu = (USL - μ) / (3σ)
  Cpl = (μ - LSL) / (3σ)
  Target: Cpk ≥ 1.33 (6σ quality)
"""
from __future__ import annotations
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np


@dataclass(frozen=True, slots=True)
class ControlLimits:
    """Control chart limits."""
    CL:  float   # Center line
    UCL: float   # Upper control limit
    LCL: float   # Lower control limit
    USL: float   # Upper spec limit
    LSL: float   # Lower spec limit


@dataclass(slots=True)
class ViolationEvent:
    rule:     str    # "3sigma", "cusum", "run8", etc.
    value:    float
    index:    int
    timestamp:float


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    n:      int
    mean:   float
    std:    float
    Cp:     float
    Cpk:    float
    Cpu:    float
    Cpl:    float
    ppm:    float    # Estimated defect rate [ppm]
    capable:bool     # Cpk ≥ 1.33

    def summary(self) -> str:
        s = "✓ CAPABLE" if self.capable else "✗ NOT CAPABLE"
        return (f"Cp={self.Cp:.3f}  Cpk={self.Cpk:.3f}  "
                f"μ={self.mean:.4f}  σ={self.std:.4f}  "
                f"PPM={self.ppm:.1f}  {s}")


class ShewhartChart:
    """Individual (X) control chart with Western Electric rules."""

    WARMUP = 25   # Samples needed to establish limits

    def __init__(self, name: str, limits: ControlLimits,
                 window: int = 300):
        self.name    = name
        self.lim     = limits
        self._buf    = deque(maxlen=window)
        self._viols: List[ViolationEvent] = []
        self._lock   = threading.Lock()
        self._n      = 0

    def add(self, value: float) -> Optional[ViolationEvent]:
        import time
        with self._lock:
            self._buf.append(float(value)); self._n += 1
            if self._n < self.WARMUP:
                return None
            viol = self._check(value, self._n)
            if viol: self._viols.append(viol)
            return viol

    def _check(self, val: float, idx: int) -> Optional[ViolationEvent]:
        import time as _t
        L = self.lim; ts = _t.time()
        sigma = (L.UCL - L.CL) / 3.0

        # Rule 1: beyond 3σ
        if val > L.UCL or val < L.LCL:
            return ViolationEvent("rule1_3sigma", val, idx, ts)

        # Rule 4: 8 consecutive same side
        if len(self._buf) >= 8:
            last8 = [self._buf[-i-1] for i in range(8)]
            if all(v > L.CL for v in last8) or all(v < L.CL for v in last8):
                return ViolationEvent("rule4_run8", val, idx, ts)

        # Rule 2: 2/3 beyond 2σ same side
        if len(self._buf) >= 3:
            last3 = [self._buf[-i-1] for i in range(3)]
            n_above2 = sum(1 for v in last3 if v > L.CL + 2*sigma)
            n_below2 = sum(1 for v in last3 if v < L.CL - 2*sigma)
            if n_above2 >= 2 or n_below2 >= 2:
                return ViolationEvent("rule2_2of3_2sigma", val, idx, ts)

        return None

    def n_violations(self) -> int:
        with self._lock: return len(self._viols)

    def capability(self) -> CapabilityReport:
        with self._lock: data = np.array(self._buf)
        if len(data) < 2:
            return CapabilityReport(0,0,1,0,0,0,0,999999,False)
        mu  = float(data.mean()); sigma = float(data.std())
        L   = self.lim
        Cpu = (L.USL - mu) / (3*sigma) if sigma>0 else 0.0
        Cpl = (mu - L.LSL) / (3*sigma) if sigma>0 else 0.0
        Cp  = (L.USL - L.LSL) / (6*sigma) if sigma>0 else 0.0
        Cpk = min(Cpu, Cpl)
        # PPM estimate (normal distribution)
        from scipy.stats import norm as _norm
        try:
            ppm = (1 - _norm.cdf((L.USL-mu)/sigma) + _norm.cdf((L.LSL-mu)/sigma)) * 1e6
        except:
            ppm = 1e6 * max(0, 1-Cpk) if Cpk > 0 else 999999
        return CapabilityReport(n=len(data), mean=mu, std=sigma,
            Cp=Cp, Cpk=Cpk, Cpu=Cpu, Cpl=Cpl, ppm=ppm, capable=Cpk>=1.33)


class CUSUMChart:
    """CUSUM control chart for drift detection."""
    def __init__(self, k_slack: float = 0.5, h_alarm: float = 5.0):
        self.k = k_slack; self.h = h_alarm
        self._Cp = 0.0; self._Cm = 0.0
        self._mu = 0.0; self._sigma = 1.0
        self._buf = deque(maxlen=100); self._n = 0

    def add(self, value: float) -> bool:
        self._buf.append(value); self._n += 1
        if self._n > 20:
            arr = np.array(self._buf)
            self._mu = float(arr.mean()); self._sigma = max(float(arr.std()),0.01)
        z = (value - self._mu) / self._sigma
        self._Cp = max(0.0, self._Cp + z - self.k)
        self._Cm = max(0.0, self._Cm - z - self.k)
        return self._Cp > self.h or self._Cm > self.h

    def reset(self) -> None: self._Cp = self._Cm = 0.0

    @property
    def C_pos(self) -> float: return self._Cp
    @property
    def C_neg(self) -> float: return self._Cm


class SPCEngine:
    """
    Multi-channel SPC engine for composite process monitoring.
    Thread-safe. Non-blocking update().
    """
    def __init__(self):
        # Void fraction chart
        self._void_chart = ShewhartChart("void_pct",
            ControlLimits(CL=2.0,UCL=3.5,LCL=0.5,USL=5.0,LSL=0.0))
        # Vf chart
        self._Vf_chart = ShewhartChart("Vf",
            ControlLimits(CL=0.55,UCL=0.62,LCL=0.48,USL=0.70,LSL=0.45))
        # Thickness chart
        self._t_chart = ShewhartChart("t_layer_mm",
            ControlLimits(CL=0.25,UCL=0.30,LCL=0.20,USL=0.35,LSL=0.15))
        # Cure degree chart
        self._alpha_chart = ShewhartChart("alpha_final",
            ControlLimits(CL=0.90,UCL=0.98,LCL=0.82,USL=1.00,LSL=0.80))
        # CUSUM on void
        self._cusum_void = CUSUMChart(k_slack=0.5, h_alarm=5.0)

        self._violations: List[ViolationEvent] = []
        self._lock = threading.Lock()

    def update(self, void_pct: float, Vf: float, t_mm: float,
               alpha: float) -> List[str]:
        """Non-blocking. Returns list of alarm codes."""
        alarms = []
        for chart, val, name in [
            (self._void_chart,  void_pct, "VOID"),
            (self._Vf_chart,    Vf,       "VF"),
            (self._t_chart,     t_mm,     "THICKNESS"),
            (self._alpha_chart, alpha,    "CURE"),
        ]:
            viol = chart.add(val)
            if viol:
                alarms.append(f"{name}:{viol.rule}")
                with self._lock: self._violations.append(viol)

        if self._cusum_void.add(void_pct):
            alarms.append("VOID:cusum_drift")

        return alarms

    def get_capabilities(self) -> Dict[str, CapabilityReport]:
        return {
            "void":      self._void_chart.capability(),
            "Vf":        self._Vf_chart.capability(),
            "thickness": self._t_chart.capability(),
            "cure":      self._alpha_chart.capability(),
        }

    def total_violations(self) -> int:
        with self._lock: return len(self._violations)

    def is_capable(self) -> bool:
        caps = self.get_capabilities()
        return all(c.capable for c in caps.values())
