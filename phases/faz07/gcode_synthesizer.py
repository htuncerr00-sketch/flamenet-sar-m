"""
gcode_synthesizer.py — G-code Sentezleyici
==========================================
WindingSequence + MotionSegment listesini eksiksiz NC dosyasına dönüştürür.

Desteklenen diyalektler: GRBL/FluidNC, Mach3
Yapı (her program için):
  1. Header (controller info, part info, settings)
  2. Home move (G0 X0 A0)
  3. Per-layer:
       a. Layer comment
       b. Per-pass:
            i.  Approach move (G0 rapid to start position)
            ii. Winding moves (G1 coordinated X+A)
            iii.Pass end (pause / tension relief)
       c. Layer end (fiber cut comment, rewind if needed)
  4. Footer (M30)

Feedrate strateji (GRBL 2-eksen sarım):
  X ve A aynı anda G1 ile hareket eder.
  F değeri: carriage mm/min cinsinden.
  A ekseni: controller steps_per_deg ile koordineli sürer.

  ÖNEMLI: GRBL default'ta F tek bir sayı — tüm eksenler için geçerli.
  Mixed unit problem (mm/min X vs °/min A):
  Çözüm 1 (basit): F=X_speed_mm_min, A koordineli dolaylı.
  Çözüm 2 (kesin): GRBL'ün rotary axis mode'u (ya da FluidNC kinematics).
  Bu implementasyon: Çözüm 1 (pratik, yaygın yaklaşım).

  Endüstriyel not: Gerçek sarım makineleri genellikle özel motion controller
  kullanır (Galil, Delta Tau) ve G-code bu kadar basit değildir.
  Bu implementasyon: GRBL tabanlı DIY winder için uygundur.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Iterator, List, Optional

from sequence_planner import (
    WindingSequence, WindingPass, LayerGroup,
    PassType, LayerType,
)
from controller_profile import (
    ControllerProfile, GRBLProfile, Mach3Profile,
    ContinuousAAxisManager, MotionSegment,
)
from unified_geodesic_planner import FullBodyPath, FullBodyPoint


# ── G-code Program ──────────────────────────────────────────────

@dataclass(slots=True)
class GCodeProgram:
    """Üretilen NC programı."""
    controller_name: str
    lines:           List[str] = field(default_factory=list)
    n_moves:         int       = 0
    n_pauses:        int       = 0
    n_rewinds:       int       = 0
    total_x_travel:  float     = 0.0
    estimated_time_s:float     = 0.0

    def add(self, line: str) -> None:
        self.lines.append(line)

    def to_string(self) -> str:
        return "\n".join(self.lines)

    def save(self, filepath: str) -> None:
        with open(filepath, "w") as f:
            f.write(self.to_string())
        print(f"    NC dosyası kaydedildi: {filepath} ({len(self.lines)} satır)")

    def stats(self) -> str:
        return (
            f"  NC Program İstatistikleri:\n"
            f"    Satır sayısı:   {len(self.lines)}\n"
            f"    Move sayısı:    {self.n_moves}\n"
            f"    Pause (M0/M1):  {self.n_pauses}\n"
            f"    Rewind:         {self.n_rewinds}\n"
            f"    X travel:       {self.total_x_travel:.1f} mm\n"
            f"    Tahmini süre:   {self.estimated_time_s/60:.1f} dak\n"
        )


# ── Geodesic → Motion Segments adapter ──────────────────────────

def path_to_motion_segments(
    path:       FullBodyPath,
    axis_mgr:   ContinuousAAxisManager,
    pass_obj:   WindingPass,
    n_subsample:int = 5,
) -> List[MotionSegment]:
    """
    FullBodyPath noktalarını MotionSegment listesine dönüştür.

    n_subsample: Kaç noktada bir sample al (G-code satırı yoğunluğu).
    Daha az satır → daha hızlı controller, daha az hassasiyet.
    Önerilen: n_subsample=3-5 (150rpm makine için yeterli).
    """
    if not path.points:
        return []

    pts = path.points
    # Subsample
    if n_subsample > 1:
        indices = list(range(0, len(pts), n_subsample))
        if (len(pts)-1) not in indices:
            indices.append(len(pts)-1)
        pts = [path.points[i] for i in indices]

    alpha_rad = math.radians(abs(pass_obj.alpha_deg))
    return axis_mgr.path_to_segments(pts, pass_obj.pass_index, alpha_rad)


def hoop_pass_segments(
    pass_obj:   WindingPass,
    axis_mgr:   ContinuousAAxisManager,
    n_pts:      int = 8,
) -> List[MotionSegment]:
    """
    Hoop geçişi için basit lineer segmentler.

    Hoop: X lineer (z_start → z_end), A tam devir.
    """
    z0    = pass_obj.z_start_mm
    z1    = pass_obj.z_end_mm
    f     = pass_obj.feedrate_mm_min
    # Hoop: 1 tam devir (360°) per strip
    da    = 360.0

    x_arr = [z0 + (z1-z0)*i/(n_pts-1) for i in range(n_pts)]
    segs  = []
    for i, x in enumerate(x_arr):
        a_seg = da * i / (n_pts - 1)
        _, a_cum = axis_mgr.update(x, a_seg if i > 0 else 0.0)
        if i > 0:  # first segment: no move (starting position)
            seg = MotionSegment(
                x_mm=x, a_deg=a_cum, f_mm_min=f,
                pass_index=pass_obj.pass_index, point_index=i,
            )
            segs.append(seg)
    return segs


# ── G-code Synthesizer ──────────────────────────────────────────

class GCodeSynthesizer:
    """
    WindingSequence + Path listesinden NC programı üretir.

    Kullanım:
        synth = GCodeSynthesizer(
            controller=GRBLProfile(),
            sequence=seq,
            paths=paths,         # FullBodyPath listesi (pass başına)
            part_info={"R_mm":50, "L_mm":300, ...}
        )
        program = synth.synthesize()
        program.save("winding.nc")
    """

    # Polar bölge için yavaş feedrate faktörü
    POLAR_SLOW_FACTOR = 0.40

    # Approach move Z offset (start position before dome equator)
    APPROACH_OFFSET_MM = 2.0

    def __init__(
        self,
        controller:     ControllerProfile,
        sequence:       WindingSequence,
        paths:          List[Optional[FullBodyPath]],  # per-pass, None=skip
        part_info:      dict,
        n_subsample:    int   = 4,
        z_home:         float = -5.0,   # Safe home position (off-part)
        fiber_speed:    float = 100.0,
        mandrel_radius: float = 50.0,
    ) -> None:
        self.ctrl         = controller
        self.seq          = sequence
        self.paths        = paths
        self.part_info    = part_info
        self.n_sub        = n_subsample
        self.z_home       = z_home
        self.axis_mgr     = ContinuousAAxisManager(
            controller, mandrel_radius, sequence.c_clairaut, fiber_speed)

    def synthesize(self) -> GCodeProgram:
        """Tam NC programını sentezle."""
        ctrl    = self.ctrl
        prog    = GCodeProgram(controller_name=ctrl.name)
        c       = ctrl.format_comment

        # ── Header ───────────────────────────────────────────────
        for line in ctrl.format_header(self.part_info):
            prog.add(line)
        prog.add("")

        # ── Home ─────────────────────────────────────────────────
        prog.add(c("=== PROGRAM BAŞLANGIÇ ==="))
        prog.add(ctrl.format_comment(f"Sequence: {self.seq.strategy}"))
        prog.add(ctrl.format_comment(
            f"α₀={self.seq.alpha_0_deg:.2f}°  c={self.seq.c_clairaut:.3f}mm  "
            f"k={self.seq.k_circuits}"))
        prog.add(ctrl.format_rapid(x=self.z_home, a=0.0))
        prog.add(ctrl.format_dwell(1.0))
        prog.add("")

        self.axis_mgr.reset_tracking()
        path_iter = iter(self.paths)
        pass_idx  = 0

        for layer in self.seq.layers:
            prog.add(c(f"─── KATMAN {layer.layer_index}: "
                       f"{layer.layer_type.name}  α={layer.alpha_deg:+.2f}°  "
                       f"φ_offset={layer.phi_offset_deg:.3f}° ───"))

            for pass_obj in layer.passes:
                path = next(path_iter, None)
                self._synthesize_pass(prog, pass_obj, path, layer)
                pass_idx += 1

            # Layer transition (fiber cut between layers)
            if layer is not self.seq.layers[-1]:
                prog.add(c("  ≡ FİBER KESME ≡"))
                prog.add(ctrl.format_pause("Fiber kes, yeni bobin hazırla"))
                prog.n_pauses += 1
                # Rewind
                rewind_segs = self.axis_mgr.generate_rewind("REWIND")
                if rewind_segs:
                    seg = rewind_segs[0]
                    prog.add(ctrl.format_rapid(x=None, a=seg.a_deg))
                    prog.n_rewinds += 1
                prog.add("")

        # ── Footer ───────────────────────────────────────────────
        prog.add(c("=== PROGRAM SONU ==="))
        prog.add(ctrl.format_rapid(x=self.z_home, a=self.axis_mgr.A_current))
        for line in ctrl.format_footer():
            prog.add(line)

        return prog

    def _synthesize_pass(
        self,
        prog:     GCodeProgram,
        pass_obj: WindingPass,
        path:     Optional[FullBodyPath],
        layer:    LayerGroup,
    ) -> None:
        """Tek bir WindingPass için G-code blok üret."""
        ctrl = self.ctrl; c = ctrl.format_comment

        prog.add(c(f"  PASS {pass_obj.pass_index}: "
                   f"{pass_obj.pass_type.name}  "
                   f"φ₀={pass_obj.phi_start_deg:.2f}°  "
                   f"{pass_obj.comment}"))

        # ── Approach ──────────────────────────────────────────────
        x_approach = pass_obj.z_start_mm - self.APPROACH_OFFSET_MM
        prog.add(ctrl.format_rapid(x=x_approach, a=self.axis_mgr.A_current))

        # ── Winding moves ─────────────────────────────────────────
        if pass_obj.pass_type in (PassType.HELICAL_FORWARD,
                                   PassType.HELICAL_RETURN):
            segs = self._helical_segments(pass_obj, path)
        elif pass_obj.pass_type in (PassType.HOOP, PassType.POLAR_REINF):
            segs = hoop_pass_segments(pass_obj, self.axis_mgr)
        else:
            segs = []

        for seg in segs:
            if seg.is_rapid:
                prog.add(ctrl.format_rapid(seg.x_mm, seg.a_deg))
            else:
                prog.add(ctrl.format_linear(seg.x_mm, seg.a_deg, seg.f_mm_min))
            prog.n_moves += 1
            prog.total_x_travel += abs(seg.x_mm - (prog.total_x_travel))  # approx
            prog.estimated_time_s += seg.segment_time_s

        # ── Pass end ──────────────────────────────────────────────
        if pass_obj.tension_release:
            prog.add(c("    tension release slow-out"))
            f_slow = pass_obj.feedrate_mm_min * 0.3
            x_out  = pass_obj.z_start_mm
            prog.add(ctrl.format_linear(x=x_out, a=self.axis_mgr.A_current,
                                        f=f_slow))

        prog.add("")

    def _helical_segments(
        self,
        pass_obj: WindingPass,
        path:     Optional[FullBodyPath],
    ) -> List[MotionSegment]:
        """Helical geçiş için MotionSegment listesi."""
        if path is not None and path.points:
            return path_to_motion_segments(
                path, self.axis_mgr, pass_obj, self.n_sub)
        else:
            # Fallback: basit lineer yaklaşım (path yoksa)
            return self._simple_helical_segments(pass_obj)

    def _simple_helical_segments(
        self,
        pass_obj: WindingPass,
    ) -> List[MotionSegment]:
        """
        Path yoksa analitik approximation.

        X: z_start → z_end lineer
        A: Δφ = (z_end - z_start) × tan(α) / R × (180/π) kümülatif
        """
        alpha = math.radians(abs(pass_obj.alpha_deg))
        z0, z1 = pass_obj.z_start_mm, pass_obj.z_end_mm
        n    = max(5, int(abs(z1-z0) / 10.0))   # 10mm aralık
        segs = []

        for i in range(1, n+1):
            t = i / n
            x = z0 + t * (z1 - z0)
            dz = (z1 - z0) / n
            dA = abs(dz) * math.tan(alpha) / 50.0 * (180.0 / math.pi)
            self.axis_mgr.update(x, dA if z1 > z0 else -dA)

            f  = self.axis_mgr.compute_feedrate(alpha)
            t_seg = abs(dz) / max(f / 60.0, 1e-6)
            seg = MotionSegment(
                x_mm=x, a_deg=self.axis_mgr.A_current,
                f_mm_min=f, segment_time_s=t_seg,
                pass_index=pass_obj.pass_index, point_index=i,
            )
            segs.append(seg)

        return segs


# ── Quick generator ───────────────────────────────────────────────

def generate_nc(
    sequence:       WindingSequence,
    controller_name:str  = "grbl",
    part_info:      dict = None,
    output_path:    str  = None,
    fiber_speed:    float = 100.0,
    mandrel_R:      float = 50.0,
    n_subsample:    int   = 4,
) -> GCodeProgram:
    """
    Hızlı NC üretim fonksiyonu (paths olmadan — basit mod).

    Tam trajectory ile kullanım için GCodeSynthesizer'ı doğrudan kullan.
    """
    if controller_name.lower() in ("grbl", "fluidnc", "grbl/fluidnc"):
        ctrl = GRBLProfile()
    elif controller_name.lower() in ("mach3", "mach4"):
        ctrl = Mach3Profile()
    else:
        raise ValueError(f"Bilinmeyen controller: {controller_name}")

    if part_info is None:
        part_info = {
            "R_mm":       mandrel_R,
            "L_mm":       "?",
            "alpha_0_deg":sequence.alpha_0_deg,
            "c_mm":       sequence.c_clairaut,
            "k":          sequence.k_circuits,
        }

    # paths = None per pass (simple mode)
    paths = [None] * sequence.total_passes

    synth   = GCodeSynthesizer(
        ctrl, sequence, paths, part_info,
        n_subsample=n_subsample,
        fiber_speed=fiber_speed,
        mandrel_radius=mandrel_R,
    )
    program = synth.synthesize()

    if output_path:
        program.save(output_path)

    return program
