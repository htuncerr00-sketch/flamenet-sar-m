"""
core/winding_planner.py — Helical Winding Pattern Planner + G-code
====================================================================
Clairaut geodesic: c = R × sin(α)
G-code generation: G91 X.. A.. F.. coordinated motion.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True, slots=True)
class WindingParams:
    mandrel_R_mm:     float = 50.0
    mandrel_L_mm:     float = 300.0
    alpha_deg:        float = 10.17
    n_layers:         int   = 8
    tow_width_mm:     float = 10.0
    fiber_tension_N:  float = 15.0
    feed_mm_s:        float = 100.0

    @property
    def clairaut_c_mm(self) -> float:
        return self.mandrel_R_mm * math.sin(math.radians(self.alpha_deg))

    @property
    def feed_mm_min(self) -> float:
        return self.feed_mm_s * 60.0

    def validate(self) -> List[str]:
        errs = []
        if not (0 < self.alpha_deg < 90):  errs.append(f"alpha out of (0,90): {self.alpha_deg}")
        if self.n_layers < 1:               errs.append(f"n_layers < 1")
        if not (3 <= self.fiber_tension_N <= 38):
            errs.append(f"tension out of [3,38]N: {self.fiber_tension_N}")
        if self.feed_mm_s > 200:            errs.append(f"feed > 200 mm/s")
        if self.mandrel_R_mm <= 0:          errs.append(f"R <= 0")
        if self.mandrel_L_mm <= 0:          errs.append(f"L <= 0")
        return errs


@dataclass(slots=True)
class GCodeProgram:
    lines:    List[str] = field(default_factory=list)
    n_circuits: int = 0
    total_length_mm: float = 0.0
    estimated_time_s: float = 0.0

    def __len__(self) -> int: return len(self.lines)


def generate_helical(params: WindingParams) -> GCodeProgram:
    """Generate G-code for helical winding pattern."""
    errs = params.validate()
    if errs:
        raise ValueError(f"Invalid params: {errs}")
    p = params
    prog = GCodeProgram()
    prog.lines.append(f"; Faz17 winding program — generated")
    prog.lines.append(f"; mandrel R={p.mandrel_R_mm}mm L={p.mandrel_L_mm}mm")
    prog.lines.append(f"; alpha={p.alpha_deg}° layers={p.n_layers}")
    prog.lines.append(f"; c={p.clairaut_c_mm:.3f}mm (Clairaut)")
    prog.lines.append(f"G21 G91")          # mm, relative
    prog.lines.append(f"F{p.feed_mm_min:.0f}")
    pitch = p.tow_width_mm / math.cos(math.radians(p.alpha_deg))
    a_per_pass = 360.0 / math.tan(math.radians(p.alpha_deg))   # degrees per L
    feed_passes = int(p.mandrel_L_mm / pitch) + 1
    total_len = 0.0
    for layer in range(p.n_layers):
        direction = 1 if layer % 2 == 0 else -1
        prog.lines.append(f"; Layer {layer+1}/{p.n_layers}  dir={direction}")
        for circ in range(feed_passes):
            dx = pitch * direction
            da = p.mandrel_L_mm / feed_passes * a_per_pass * direction
            prog.lines.append(f"G1 X{dx:.3f} A{da:.3f}")
            total_len += abs(dx)
            prog.n_circuits += 1
    prog.lines.append(f"; total circuits: {prog.n_circuits}")
    prog.lines.append("M30")
    prog.total_length_mm = total_len
    prog.estimated_time_s = total_len / p.feed_mm_s
    return prog
