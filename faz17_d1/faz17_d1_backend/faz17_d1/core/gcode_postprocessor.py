"""
core/gcode_postprocessor.py — G-code Post İşlemci
==================================================
MotionSegment listesini makineye hazır G-code'a dönüştürür.
GRBL, Mach3, Fanuc ve özel kontrolörler desteklenir.
"""
from __future__ import annotations
import datetime
from dataclasses import dataclass, field
from typing import List

from .motion_planner import MotionSegment
from .path_generator import WindingPath


@dataclass
class MachineConfig:
    """4-eksen filament sarma makinesi yapılandırması."""
    x_axis: str = "X"
    a_axis: str = "A"
    max_x_feed_mm_min: float = 5000.0
    max_a_rpm: float = 300.0
    x_home_pos_mm: float = 0.0
    preheat_dwell_s: float = 0.0
    use_inch: bool = False
    controller_type: str = "grbl"   # "grbl" | "mach3" | "fanuc" | "custom"


@dataclass
class GCodeProgram:
    lines: List[str] = field(default_factory=list)
    n_circuits: int = 0
    n_layers: int = 0
    total_length_mm: float = 0.0
    estimated_time_s: float = 0.0
    coverage_pct: float = 0.0

    def __len__(self) -> int:
        return len(self.lines)

    def as_text(self) -> str:
        return "\n".join(self.lines)


def generate_gcode(segments: List[MotionSegment],
                   path: WindingPath,
                   config: MachineConfig) -> GCodeProgram:
    """
    MotionSegment listesini G-code satırlarına dönüştür.

    Çıktı formatı:
    ; Filament Sarma CAM
    G21            (mm modu)
    G90            (mutlak koordinatlar)
    G28            (referans noktası)
    G1 X.. A.. F..
    M30
    """
    prog = GCodeProgram()
    lines = prog.lines
    p = path.params
    cfg = config

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"; Filament Sarma CAM — {now}")
    lines.append(f"; Mandrel: çap={p.profile.max_radius_mm * 2:.1f}mm "
                 f"uzunluk={p.profile.length_mm:.1f}mm")
    lines.append(f"; Sarma açısı: {p.alpha_deg:.1f}°  "
                 f"Strateji: {p.winding_strategy}")
    lines.append(f"; Katmanlar: {path.n_layers}  "
                 f"Devreler: {path.n_circuits}")
    lines.append(f"; Fiber uzunluğu: {path.total_fiber_length_mm:.0f}mm  "
                 f"Tahmini süre: {path.estimated_time_s:.0f}s")
    lines.append(f"; Kapsama: {path.coverage_pct:.1f}%")
    lines.append(f"; Kontrolör: {cfg.controller_type}")
    lines.append(";")

    # Program başlangıç kodları
    if cfg.use_inch:
        lines.append("G20")
    else:
        lines.append("G21")
    lines.append("G90")

    if cfg.controller_type in ("grbl", "mach3"):
        lines.append("G28")
    elif cfg.controller_type == "fanuc":
        lines.append("G28 G91 Z0")
        lines.append("G90")

    if cfg.preheat_dwell_s > 0:
        dwell_ms = int(cfg.preheat_dwell_s * 1000)
        if cfg.controller_type == "fanuc":
            lines.append(f"G04 P{dwell_ms}")
        else:
            lines.append(f"G4 P{dwell_ms}")

    # Başlangıç konumuna git
    if cfg.controller_type == "fanuc":
        lines.append(f"G00 {cfg.x_axis}0.000")
    else:
        lines.append(f"G0 {cfg.x_axis}0.000")

    # Hareket segmentleri
    total_dist = 0.0
    prev_feed = -1.0

    for seg in segments:
        if seg.segment_type == "RAPID":
            rapid_cmd = "G00" if cfg.controller_type == "fanuc" else "G0"
            lines.append(
                f"{rapid_cmd} {cfg.x_axis}{seg.x_end:.3f} "
                f"{cfg.a_axis}{seg.a_end:.3f}"
            )
            continue
        if seg.segment_type == "DWELL":
            dwell_val = int(seg.feed_mm_min)
            if cfg.controller_type == "fanuc":
                lines.append(f"G04 P{dwell_val}")
            else:
                lines.append(f"G4 P{dwell_val}")
            continue

        # LINEAR hareket
        feed = min(seg.feed_mm_min, cfg.max_x_feed_mm_min)
        linear_cmd = "G01" if cfg.controller_type == "fanuc" else "G1"

        dx = abs(seg.x_end - seg.x_start)
        da = abs(seg.a_end - seg.a_start)
        total_dist += dx

        if abs(feed - prev_feed) > 0.5:
            lines.append(
                f"{linear_cmd} {cfg.x_axis}{seg.x_end:.3f} "
                f"{cfg.a_axis}{seg.a_end:.3f} F{feed:.0f}"
            )
            prev_feed = feed
        else:
            lines.append(
                f"{linear_cmd} {cfg.x_axis}{seg.x_end:.3f} "
                f"{cfg.a_axis}{seg.a_end:.3f}"
            )

    # Program sonu
    lines.append(f"; Toplam X hareketi: {total_dist:.0f}mm")
    lines.append("M30")

    prog.n_circuits = path.n_circuits
    prog.n_layers = path.n_layers
    prog.total_length_mm = path.total_fiber_length_mm
    prog.estimated_time_s = path.estimated_time_s
    prog.coverage_pct = path.coverage_pct
    return prog
