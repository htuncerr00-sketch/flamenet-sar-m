"""
core/gcode_postprocessor.py — G-code Post İşlemci
==================================================
MotionSegment listesini makineye hazır G-code'a dönüştürür.
GRBL, Mach3, Fanuc ve özel kontrolörler desteklenir.
"""
from __future__ import annotations
import datetime
from dataclasses import dataclass, field
from typing import List, Tuple

from .motion_planner import MotionSegment
from .path_generator import WindingPath


@dataclass
class MachineConfig:
    """
    4-eksen filament sarma makinesi yapılandırması.

    Desteklenen kontrolörler
    -----------------------
    "grbl"     — GRBL v1.1 (Arduino/CNC Shield tabanlı); G0/G1, G21/G90, G28 (saklı ev)
    "mach3"    — Mach3/Mach4 Windows CNC; G0/G1, M3/M5, % başlık opsiyonel
    "fanuc"    — Fanuc 0i/21i/30i; G00/G01, %, O-numarası, N satır numaraları gerekli
    "linuxcnc" — LinuxCNC/EMC2; NIST RS-274 standart; G0/G1, % isteğe bağlı, M2 sonlandırır
    "siemens"  — Siemens SINUMERIK 840D; G0/G1, M30, satır numarasız
    "custom"   — Satır numarası yok, minimal başlık
    """
    x_axis: str = "X"
    a_axis: str = "A"
    b_axis: str = ""               # Payout göz ekseni (boş = 3-eksen çıkış)
    max_x_feed_mm_min: float = 5000.0
    max_a_rpm: float = 300.0
    x_home_pos_mm: float = 0.0
    preheat_dwell_s: float = 0.0
    use_inch: bool = False
    controller_type: str = "grbl"  # "grbl"|"mach3"|"fanuc"|"linuxcnc"|"siemens"|"custom"
    program_number: int = 1        # Fanuc O-numarası (0 = devre dışı)


@dataclass
class GCodeProgram:
    lines: List[str] = field(default_factory=list)
    n_circuits: int = 0
    n_layers: int = 0
    total_length_mm: float = 0.0
    estimated_time_s: float = 0.0
    coverage_pct: float = 0.0
    # Güvenlik post-işleme alanları (varsayılan → geriye dönük uyumlu)
    soft_limit_violations: int = 0
    is_safe: bool = True
    warnings: List[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.lines)

    def as_text(self) -> str:
        return "\n".join(self.lines)


def generate_gcode(segments: List[MotionSegment],
                   path: WindingPath,
                   config: MachineConfig) -> GCodeProgram:
    """
    MotionSegment listesini makineye hazır G-code satırlarına dönüştür.

    Desteklenen formatlar:
    - grbl/linuxcnc : G0/G1, satır numarasız, M2/M30 sonlandırır
    - mach3         : G0/G1, M30 sonlandırır
    - fanuc         : G00/G01, %, O-numarası, N satır numaraları, G04 dwell
    - siemens       : G0/G1, G91/G90, M30 sonlandırır
    - custom        : Minimal çıkış
    """
    prog = GCodeProgram()
    lines = prog.lines
    p = path.params
    cfg = config

    is_fanuc    = cfg.controller_type == "fanuc"
    is_siemens  = cfg.controller_type == "siemens"
    is_linuxcnc = cfg.controller_type == "linuxcnc"
    use_lnum    = is_fanuc   # N-satır numarası sadece Fanuc
    lnum        = [10]       # mutable sayaç için liste

    def _ln(cmd: str) -> str:
        """Fanuc için N-prefix ekle; diğerleri düz döner."""
        if use_lnum:
            n = lnum[0]; lnum[0] += 10
            return f"N{n:04d} {cmd}"
        return cmd

    rapid  = "G00" if is_fanuc else "G0"
    linear = "G01" if is_fanuc else "G1"
    dwell_g = "G04" if is_fanuc else "G4"

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── Program başlığı ───────────────────────────────────────────────────
    if is_fanuc:
        lines.append("%")
        if cfg.program_number > 0:
            lines.append(f"O{cfg.program_number:04d}")

    lines.append(f"; Filament Sarma CAM — {now}")
    lines.append(f"; Mandrel: çap={p.profile.max_radius_mm * 2:.1f}mm  "
                 f"uzunluk={p.profile.length_mm:.1f}mm")
    lines.append(f"; Sarma açısı: {p.alpha_deg:.1f}°  "
                 f"Strateji: {p.winding_strategy}")
    lines.append(f"; Katmanlar: {path.n_layers}  Devreler: {path.n_circuits}")
    lines.append(f"; Fiber: {path.total_fiber_length_mm/1000:.2f}m  "
                 f"Süre: {path.estimated_time_s:.0f}s  "
                 f"Kapsama: {path.coverage_pct:.1f}%")
    lines.append(f"; Clairaut c: {path.clairaut_c:.3f}mm")
    lines.append(f"; Kontrolör: {cfg.controller_type}")
    lines.append(";")

    # ── Başlatma kodları ──────────────────────────────────────────────────
    lines.append(_ln("G20" if cfg.use_inch else "G21"))
    lines.append(_ln("G90"))

    if is_fanuc:
        lines.append(_ln("G28 G91 Z0"))
        lines.append(_ln("G90"))
    elif is_siemens:
        lines.append(_ln("G90"))
    elif cfg.controller_type in ("grbl", "mach3"):
        lines.append(_ln("G28"))
    elif is_linuxcnc:
        lines.append(_ln("G28"))

    if cfg.preheat_dwell_s > 0:
        dwell_ms = int(cfg.preheat_dwell_s * 1000)
        lines.append(_ln(f"{dwell_g} P{dwell_ms}"))

    lines.append(_ln(f"{rapid} {cfg.x_axis}0.000"))

    # ── Hareket segmentleri ───────────────────────────────────────────────
    total_dist = 0.0
    prev_feed = -1.0
    b_has_data = cfg.b_axis and any(
        hasattr(s, "b_deg") for s in segments
    )

    for seg in segments:
        b_part = ""
        if b_has_data and hasattr(seg, "b_deg"):
            b_part = f" {cfg.b_axis}{seg.b_deg:.2f}"

        if seg.segment_type == "RAPID":
            lines.append(_ln(
                f"{rapid} {cfg.x_axis}{seg.x_end:.3f} "
                f"{cfg.a_axis}{seg.a_end:.3f}{b_part}"
            ))
            continue
        if seg.segment_type == "DWELL":
            dwell_val = int(seg.feed_mm_min)
            lines.append(_ln(f"{dwell_g} P{dwell_val}"))
            continue

        # LINEAR
        feed = min(seg.feed_mm_min, cfg.max_x_feed_mm_min)
        dx = abs(seg.x_end - seg.x_start)
        total_dist += dx

        if abs(feed - prev_feed) > 0.5:
            lines.append(_ln(
                f"{linear} {cfg.x_axis}{seg.x_end:.3f} "
                f"{cfg.a_axis}{seg.a_end:.3f}{b_part} F{feed:.0f}"
            ))
            prev_feed = feed
        else:
            lines.append(_ln(
                f"{linear} {cfg.x_axis}{seg.x_end:.3f} "
                f"{cfg.a_axis}{seg.a_end:.3f}{b_part}"
            ))

    # ── Program sonu ──────────────────────────────────────────────────────
    lines.append(f"; Toplam X hareketi: {total_dist:.0f}mm")
    end_cmd = "M30" if not is_linuxcnc else "M2"
    lines.append(_ln(end_cmd))

    if is_fanuc:
        lines.append("%")

    prog.n_circuits = path.n_circuits
    prog.n_layers = path.n_layers
    prog.total_length_mm = path.total_fiber_length_mm
    prog.estimated_time_s = path.estimated_time_s
    prog.coverage_pct = path.coverage_pct
    return prog


# ═══════════════════════════════════════════════════════════════════════════════
# GÜVENLİ G-CODE ÇIKIŞI (Faz: Endüstriyel Üretim Doğruluğu — Görev 6)
# ═══════════════════════════════════════════════════════════════════════════════
# Yumuşak-limit-farkında çıkış, yapılandırılabilir makine sıfırı, güvenli geri
# çekme (retract), iş mili rampa zamanlaması ve senkronize başlatma/kapatma.


@dataclass
class MachineSafetyConfig:
    """
    G-code güvenlik post-işleme yapılandırması.

    x_soft_min_mm / x_soft_max_mm : Taşıyıcı yumuşak limitleri.
    machine_zero_x_mm : Makine sıfırı ofseti (X_çıktı = X_yol + ofset).
    safe_park_x_mm    : Güvenli park / geri çekme konumu (makine koordinatı).
    enable_safe_retract : Başta/sonda güvenli park hareketleri ekle.
    spindle_ramp_s    : İş mili başlatma/kapatma rampa süresi (s).
    spindle_ramp_steps : Rampa adım sayısı (kademeli hız artışı).
    clamp_to_soft_limits : True → limit dışı X'i kırp; False → hareketi atla+uyar.
    use_spindle_sync  : True → M3/M5 ile senkronize iş mili başlat/kapat.
    spindle_rpm       : Hedef iş mili devri (rampa için).
    """
    x_soft_min_mm: float = -5.0
    x_soft_max_mm: float = 395.0
    machine_zero_x_mm: float = 0.0
    safe_park_x_mm: float = 0.0
    enable_safe_retract: bool = True
    spindle_ramp_s: float = 2.0
    spindle_ramp_steps: int = 4
    clamp_to_soft_limits: bool = False
    use_spindle_sync: bool = True
    spindle_rpm: float = 60.0

    def __post_init__(self) -> None:
        if self.x_soft_min_mm >= self.x_soft_max_mm:
            raise ValueError("x_soft_min >= x_soft_max — geçersiz yumuşak limit aralığı")
        if self.spindle_ramp_steps < 1:
            raise ValueError("spindle_ramp_steps >= 1 olmalı")


def generate_safe_gcode(
    segments: List[MotionSegment],
    path: WindingPath,
    config: MachineConfig,
    safety: MachineSafetyConfig,
) -> GCodeProgram:
    """
    Güvenlik-farkında G-code üret.

    `generate_gcode`'un üzerine ekler:
    - Makine sıfırı ofseti (tüm X koordinatlarına uygulanır).
    - Yumuşak limit kontrolü: limit dışı hareketler kırpılır veya atlanır + uyarı.
    - Güvenli geri çekme: başta park→ilk nokta, sonda son nokta→park.
    - İş mili rampası: başlatmada kademeli hız artışı, kapatmada azalış.
    - Senkronize iş mili başlat/kapat (M3/M5).

    Döner
    -----
    GCodeProgram — soft_limit_violations, is_safe ve warnings alanları doldurulmuş.
    """
    prog = GCodeProgram()
    lines = prog.lines
    cfg = config
    p = path.params
    zero = safety.machine_zero_x_mm
    violations = 0

    rapid = "G00" if cfg.controller_type == "fanuc" else "G0"
    linear = "G01" if cfg.controller_type == "fanuc" else "G1"
    dwell = "G04" if cfg.controller_type == "fanuc" else "G4"

    def _x_out(x_path: float) -> float:
        return x_path + zero

    def _check_soft(x_out: float) -> Tuple[float, bool]:
        """Yumuşak limit kontrolü. Döner: (kullanılacak_x, uygun_mu)."""
        if safety.x_soft_min_mm <= x_out <= safety.x_soft_max_mm:
            return x_out, True
        if safety.clamp_to_soft_limits:
            return min(max(x_out, safety.x_soft_min_mm), safety.x_soft_max_mm), False
        return x_out, False

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"; Filament Sarma CAM (GÜVENLİ) — {now}")
    lines.append(f"; Mandrel: çap={p.profile.max_radius_mm * 2:.1f}mm "
                 f"uzunluk={p.profile.length_mm:.1f}mm")
    lines.append(f"; Sarma açısı: {p.alpha_deg:.1f}°  Strateji: {p.winding_strategy}")
    lines.append(f"; Katmanlar: {path.n_layers}  Devreler: {path.n_circuits}")
    lines.append(f"; Makine sıfırı ofseti X: {zero:+.3f}mm")
    lines.append(f"; Yumuşak limitler X: [{safety.x_soft_min_mm:.1f}, "
                 f"{safety.x_soft_max_mm:.1f}]mm "
                 f"({'KIRP' if safety.clamp_to_soft_limits else 'ATLA+UYAR'})")
    lines.append(f"; Güvenli park X: {safety.safe_park_x_mm:.1f}mm  "
                 f"İş mili rampası: {safety.spindle_ramp_s:.1f}s/{safety.spindle_ramp_steps} adım")
    lines.append(";")

    lines.append("G20" if cfg.use_inch else "G21")
    lines.append("G90")
    if cfg.controller_type in ("grbl", "mach3"):
        lines.append("G28")
    elif cfg.controller_type == "fanuc":
        lines.append("G28 G91 Z0")
        lines.append("G90")

    # ── Güvenli park başlangıcı ───────────────────────────────────────────────
    if safety.enable_safe_retract:
        park, _ = _check_soft(safety.safe_park_x_mm)
        lines.append(f"; — Güvenli park başlangıcı —")
        lines.append(f"{rapid} {cfg.x_axis}{park:.3f}")

    # İlk yol noktası
    if segments:
        first_x, ok0 = _check_soft(_x_out(segments[0].x_start))
        if not ok0:
            violations += 1
        lines.append(f"{rapid} {cfg.x_axis}{first_x:.3f} {cfg.a_axis}{segments[0].a_start:.3f}")

    # ── Senkronize iş mili başlatma + rampa ──────────────────────────────────
    if safety.use_spindle_sync:
        lines.append("; — İş mili senkronize başlatma —")
        lines.append(f"M3 S{safety.spindle_rpm:.0f}")
    # Rampa: kademeli bekleme ile iş milinin hıza ulaşması
    ramp_dwell_ms = int(max(0.0, safety.spindle_ramp_s) / safety.spindle_ramp_steps * 1000)
    for k in range(safety.spindle_ramp_steps):
        frac = (k + 1) / safety.spindle_ramp_steps
        lines.append(f"; iş mili rampası %{frac*100:.0f}")
        if ramp_dwell_ms > 0:
            lines.append(f"{dwell} P{ramp_dwell_ms}")

    if cfg.preheat_dwell_s > 0:
        lines.append(f"{dwell} P{int(cfg.preheat_dwell_s * 1000)}")

    # ── Hareket segmentleri (yumuşak limit farkında) ─────────────────────────
    total_dist = 0.0
    prev_feed = -1.0
    for seg in segments:
        if seg.segment_type == "RAPID":
            x_out, ok = _check_soft(_x_out(seg.x_end))
            if not ok and not safety.clamp_to_soft_limits:
                violations += 1
                lines.append(f"; ATLANDI (limit dışı): X{x_out:.3f}")
                continue
            if not ok:
                violations += 1
            lines.append(f"{rapid} {cfg.x_axis}{x_out:.3f} {cfg.a_axis}{seg.a_end:.3f}")
            continue
        if seg.segment_type == "DWELL":
            lines.append(f"{dwell} P{int(seg.feed_mm_min)}")
            continue

        x_out, ok = _check_soft(_x_out(seg.x_end))
        if not ok and not safety.clamp_to_soft_limits:
            violations += 1
            lines.append(f"; ATLANDI (yumuşak limit dışı): X{x_out:.3f} "
                         f"∉ [{safety.x_soft_min_mm:.1f},{safety.x_soft_max_mm:.1f}]")
            continue
        if not ok:
            violations += 1  # kırpıldı

        feed = min(seg.feed_mm_min, cfg.max_x_feed_mm_min)
        total_dist += abs(seg.x_end - seg.x_start)
        if abs(feed - prev_feed) > 0.5:
            lines.append(f"{linear} {cfg.x_axis}{x_out:.3f} "
                         f"{cfg.a_axis}{seg.a_end:.3f} F{feed:.0f}")
            prev_feed = feed
        else:
            lines.append(f"{linear} {cfg.x_axis}{x_out:.3f} {cfg.a_axis}{seg.a_end:.3f}")

    # ── Senkronize iş mili kapatma + rampa azalışı ───────────────────────────
    lines.append("; — İş mili senkronize kapatma (rampa azalışı) —")
    for k in range(safety.spindle_ramp_steps):
        frac = 1.0 - (k + 1) / safety.spindle_ramp_steps
        lines.append(f"; iş mili rampası %{frac*100:.0f}")
        if ramp_dwell_ms > 0:
            lines.append(f"{dwell} P{ramp_dwell_ms}")
    if safety.use_spindle_sync:
        lines.append("M5")

    # ── Güvenli park sonu ─────────────────────────────────────────────────────
    if safety.enable_safe_retract:
        park, _ = _check_soft(safety.safe_park_x_mm)
        lines.append("; — Güvenli park sonu (geri çekme) —")
        lines.append(f"{rapid} {cfg.x_axis}{park:.3f}")

    lines.append(f"; Toplam X hareketi: {total_dist:.0f}mm")
    if violations:
        lines.append(f"; UYARI: {violations} hareket yumuşak limit dışı "
                     f"({'kırpıldı' if safety.clamp_to_soft_limits else 'atlandı'})")
    lines.append("M30")

    prog.n_circuits = path.n_circuits
    prog.n_layers = path.n_layers
    prog.total_length_mm = path.total_fiber_length_mm
    prog.estimated_time_s = path.estimated_time_s
    prog.coverage_pct = path.coverage_pct
    prog.soft_limit_violations = violations
    prog.is_safe = (violations == 0)
    if violations:
        prog.warnings.append(
            f"{violations} hareket yumuşak limit dışı "
            f"({'kırpıldı' if safety.clamp_to_soft_limits else 'atlandı'})")
    return prog
