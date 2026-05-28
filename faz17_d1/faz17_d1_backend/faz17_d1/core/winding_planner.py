"""
core/winding_planner.py — Helical Winding Pattern Planner + G-code
====================================================================
Geriye dönük uyumlu arayüz: WindingParams + generate_helical()
Yeni motor: geometry_engine + path_generator + motion_planner + gcode_postprocessor
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List


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
        if not (0 < self.alpha_deg < 90):  errs.append(f"alpha aralık dışı (0,90): {self.alpha_deg}")
        if self.n_layers < 1:               errs.append("n_layers < 1")
        if not (3 <= self.fiber_tension_N <= 38):
            errs.append(f"gerilim [3,38]N dışında: {self.fiber_tension_N}")
        if self.feed_mm_s > 200:            errs.append(f"hız > 200 mm/s")
        if self.mandrel_R_mm <= 0:          errs.append("R <= 0")
        if self.mandrel_L_mm <= 0:          errs.append("L <= 0")
        return errs


@dataclass(slots=True)
class GCodeProgram:
    lines:    List[str] = field(default_factory=list)
    n_circuits: int = 0
    n_layers: int = 0
    total_length_mm: float = 0.0
    estimated_time_s: float = 0.0
    coverage_pct: float = 0.0

    def __len__(self) -> int: return len(self.lines)

    def as_text(self) -> str:
        return "\n".join(self.lines)


def generate_helical(params: WindingParams) -> GCodeProgram:
    """
    Geriye dönük uyumlu arayüz — yeni CAM motorunu kullanır.
    Düz silindirik mandrel için helical yol üretir.
    """
    errs = params.validate()
    if errs:
        raise ValueError(f"Geçersiz parametreler: {errs}")

    from .geometry_engine import MandrelProfile
    from .path_generator import WindingPathParams, generate_path
    from .motion_planner import plan_motion
    from .gcode_postprocessor import MachineConfig, generate_gcode

    profile = MandrelProfile.cylinder(params.mandrel_L_mm, params.mandrel_R_mm)
    path_params = WindingPathParams(
        profile=profile,
        alpha_deg=params.alpha_deg,
        n_layers=params.n_layers,
        tow_width_mm=params.tow_width_mm,
        feed_mm_s=params.feed_mm_s,
        spindle_rpm=60.0,
        winding_strategy="helical",
        carriage_min_mm=-5.0,
        carriage_max_mm=params.mandrel_L_mm + 5.0,
    )
    path = generate_path(path_params)
    segments = plan_motion(path)
    gp = generate_gcode(segments, path, MachineConfig())

    prog = GCodeProgram(
        lines=gp.lines,
        n_circuits=gp.n_circuits,
        n_layers=gp.n_layers,
        total_length_mm=gp.total_length_mm,
        estimated_time_s=gp.estimated_time_s,
        coverage_pct=gp.coverage_pct,
    )
    return prog
