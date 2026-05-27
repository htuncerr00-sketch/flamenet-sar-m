"""
controller_profile.py + axis_manager.py
========================================
İki modül tek dosyada:

A) ControllerProfile — Controller-agnostic G-code soyutlaması
   Desteklenen: GRBL/FluidNC, Mach3
   Abstract metodlar: format_move(), format_header(), format_footer()
   Özellikler: axis limits, feed units, decimal precision, dialect quirks

B) ContinuousAAxisManager — Kümülatif rotary eksen yönetimi
   Sorun: 360°'de sıfırlama → makine tel sarıyor, fiber kopuyor
   Çözüm: A-ekseni hiçbir zaman sıfırlanmaz, kümülatif tutulur
   Rewind: yalnızca fiber kesme sırasında (program pause'da)

A-ekseni senkronizasyonu:
  X: carriage [mm]
  A: spindle [°] kümülatif (sonsuz)

  Hız koordinasyonu:
    v_x = S'·cos(α)  [mm/s]
    ω   = S'·sin(α)/R [rad/s] → [°/s] = ω·180/π
    F_x = v_x·60     [mm/min]
    F_A = ω·180/π·60 [°/min]

    G-code F değeri: kombine vektör hızı için
      F_combined = √(F_x² + (F_A·pitch_factor)²)
    Pratikte: X hızı dominant, A zaten koordineli.
    GRBL: F = mm/min (linear axes); A = °/min (but same F number interpolates)
    Mach3: Axis-specific feed rates via G93/G94.

Rewind stratejisi:
  A_total = k × K_full × (180/π) × n_layers  [°]
  Rewind sırasında: G0 A{home_A:.4f} (rapid move, fiber kesilmiş)
  home_A = A_current - (A_current mod 360°)  → en yakın tam devir
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# CONTROLLER PROFILE
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class AxisConfig:
    """Tek eksen konfigürasyonu."""
    name:       str      # "X", "A"
    steps_per_unit: float  # step/mm veya step/°
    max_rate:   float    # mm/min veya °/min
    accel:      float    # mm/s² veya °/s²
    travel_min: float    # [mm veya °] — None = sonsuz
    travel_max: float    # [mm veya °] — None = sonsuz
    is_rotary:  bool


@dataclass(frozen=True, slots=True)
class ControllerLimits:
    """Makine kinematik limitleri (controller'dan okunur)."""
    x_max_mm_min:   float   # Max carriage speed
    a_max_deg_min:  float   # Max spindle speed  (°/min)
    x_accel_mm_s2:  float   # Carriage acceleration
    a_accel_deg_s2: float   # Spindle acceleration
    x_travel_mm:    float   # Carriage travel
    a_travel_deg:   float   # Spindle travel (360 = no limit)


class ControllerProfile(ABC):
    """
    Controller-agnostic G-code soyutlaması.

    Alt sınıflar: GRBLProfile, Mach3Profile.
    """

    name:    str
    limits:  ControllerLimits

    @abstractmethod
    def format_header(self, part_info: dict) -> List[str]:
        """Program başlık bloğu."""

    @abstractmethod
    def format_footer(self) -> List[str]:
        """Program bitiş bloğu."""

    @abstractmethod
    def format_rapid(self, x: Optional[float], a: Optional[float]) -> str:
        """Rapid move (G0)."""

    @abstractmethod
    def format_linear(
        self,
        x: Optional[float],
        a: Optional[float],
        f: float,
    ) -> str:
        """Linear interpolation (G1)."""

    @abstractmethod
    def format_dwell(self, seconds: float) -> str:
        """Bekleme (G4)."""

    @abstractmethod
    def format_pause(self, msg: str = "") -> str:
        """Program pause (M0/M1)."""

    @abstractmethod
    def format_spindle_off(self) -> str:
        """Spindle durdur."""

    @abstractmethod
    def format_comment(self, text: str) -> str:
        """Yorum satırı."""

    @abstractmethod
    def format_set_units_mm(self) -> str:
        """mm modu (G21)."""

    @abstractmethod
    def format_absolute_mode(self) -> str:
        """Absolute koordinat modu (G90)."""

    def clamp_feed(self, f_mm_min: float, f_a_deg_min: float) -> float:
        """Feed rate'i makine limitiyle kırp."""
        return min(f_mm_min, self.limits.x_max_mm_min)

    def validate_move(
        self, x: Optional[float], a: Optional[float], f: float
    ) -> Tuple[bool, str]:
        """Hareket limitlerde mi kontrol et."""
        if x is not None and x > self.limits.x_travel_mm:
            return False, f"X={x:.3f} > travel limit {self.limits.x_travel_mm}"
        if f > self.limits.x_max_mm_min * 1.05:
            return False, f"F={f:.1f} > max {self.limits.x_max_mm_min}"
        return True, ""


# ── GRBL / FluidNC Profile ────────────────────────────────────

class GRBLProfile(ControllerProfile):
    """
    GRBL / FluidNC controller profili.

    Özellikler:
      - G21 (mm), G90 (absolute), G1 X{} A{} F{}
      - F = mm/min (X dominates, A interpolated)
      - No sub-programs, no tool radius comp
      - A-axis: cumulative degrees, no auto-reset
      - Comments: (; comment) veya (comment)
      - Pause: M0 (manual, resumes on cycle start)

    Typical GRBL settings:
      $100=80 (X steps/mm)   $103=44.44 (A steps/°)
      $110=8000 (X max mm/min) $113=90000 (A max °/min)
      $120=500 (X accel mm/s²) $123=3600 (A accel °/s²)
    """

    name = "GRBL/FluidNC"

    def __init__(
        self,
        x_max_mm_min:   float = 8000.0,
        a_max_deg_min:  float = 90000.0,
        x_accel:        float = 500.0,
        a_accel:        float = 3600.0,
        x_travel:       float = 1000.0,
        decimal_places: int   = 3,
    ) -> None:
        self.limits = ControllerLimits(
            x_max_mm_min   = x_max_mm_min,
            a_max_deg_min  = a_max_deg_min,
            x_accel_mm_s2  = x_accel,
            a_accel_deg_s2 = a_accel,
            x_travel_mm    = x_travel,
            a_travel_deg   = float("inf"),  # kümülatif, sınırsız
        )
        self._dp = decimal_places

    def format_header(self, part_info: dict) -> List[str]:
        R   = part_info.get("R_mm", "?")
        L   = part_info.get("L_mm", "?")
        alp = part_info.get("alpha_0_deg", "?")
        c   = part_info.get("c_mm", "?")
        k   = part_info.get("k", "?")
        return [
            f"; === FILAMENT WINDING NC PROGRAM ===",
            f"; Controller: {self.name}",
            f"; Part: R={R}mm  L={L}mm  α₀={alp}°",
            f"; Clairaut c={c}mm  k={k} circuits",
            f"; Generated by FW-CAM v1.0",
            f"; ====================================",
            self.format_set_units_mm(),
            self.format_absolute_mode(),
            "G17",          # XY plane (not used but good practice)
        ]

    def format_footer(self) -> List[str]:
        return [
            self.format_comment("Program complete"),
            "M30",   # Program end & rewind
        ]

    def format_rapid(self, x: Optional[float] = None,
                     a: Optional[float] = None) -> str:
        parts = ["G0"]
        if x is not None: parts.append(f"X{x:.{self._dp}f}")
        if a is not None: parts.append(f"A{a:.4f}")
        return " ".join(parts)

    def format_linear(self, x: Optional[float] = None,
                      a: Optional[float] = None, f: float = 800.0) -> str:
        parts = ["G1"]
        if x is not None: parts.append(f"X{x:.{self._dp}f}")
        if a is not None: parts.append(f"A{a:.4f}")
        parts.append(f"F{f:.1f}")
        return " ".join(parts)

    def format_dwell(self, seconds: float) -> str:
        return f"G4 P{seconds:.2f}"

    def format_pause(self, msg: str = "") -> str:
        comment = f" {self.format_comment(msg)}" if msg else ""
        return f"M0{comment}"

    def format_spindle_off(self) -> str:
        return "M5"

    def format_comment(self, text: str) -> str:
        return f"; {text}"

    def format_set_units_mm(self) -> str:
        return "G21"

    def format_absolute_mode(self) -> str:
        return "G90"

    def format_grbl_settings(self) -> List[str]:
        """GRBL $ ayarları bloğu (referans için)."""
        return [
            self.format_comment("Recommended GRBL settings:"),
            self.format_comment(f"  $110={self.limits.x_max_mm_min:.0f} (X max mm/min)"),
            self.format_comment(f"  $113={self.limits.a_max_deg_min:.0f} (A max deg/min)"),
            self.format_comment(f"  $120={self.limits.x_accel_mm_s2:.0f} (X accel mm/s2)"),
            self.format_comment(f"  $123={self.limits.a_accel_deg_s2:.0f} (A accel deg/s2)"),
        ]


# ── Mach3 Profile ────────────────────────────────────────────

class Mach3Profile(ControllerProfile):
    """
    Mach3 / Mach4 controller profili.

    Özellikler:
      - Tam G-code desteği, M98/M99 sub-programs
      - G94 (feed per min), G93 (inverse time — rotary için önemli)
      - A-axis: cumulative, veya A-rollover kapatılabilir
      - Comments: (comment) parantez içinde
      - Pause: M1 (optional stop)
      - Better look-ahead and acceleration

    Inverse time mode (G93):
    G93 ile F=1/T (işlem süresi tersinin dakika cinsinden).
    Rotary ekseni mm/min ile karıştırmadan koordine eder.
    F = 1 / (segment_time_min)
    """

    name = "Mach3"

    def __init__(
        self,
        x_max_mm_min:   float = 8000.0,
        a_max_deg_min:  float = 90000.0,
        x_accel:        float = 500.0,
        a_accel:        float = 3600.0,
        x_travel:       float = 1200.0,
        use_inverse_time: bool = False,  # G93 mode for rotary sync
        decimal_places: int   = 4,
    ) -> None:
        self.limits = ControllerLimits(
            x_max_mm_min   = x_max_mm_min,
            a_max_deg_min  = a_max_deg_min,
            x_accel_mm_s2  = x_accel,
            a_accel_deg_s2 = a_accel,
            x_travel_mm    = x_travel,
            a_travel_deg   = float("inf"),
        )
        self._use_inv  = use_inverse_time
        self._dp       = decimal_places
        self._current_feed_mode = "G94"  # feed per min

    def format_header(self, part_info: dict) -> List[str]:
        R   = part_info.get("R_mm", "?")
        L   = part_info.get("L_mm", "?")
        alp = part_info.get("alpha_0_deg", "?")
        c   = part_info.get("c_mm", "?")
        k   = part_info.get("k", "?")
        lines = [
            f"( === FILAMENT WINDING NC PROGRAM === )",
            f"( Controller: {self.name} )",
            f"( Part: R={R}mm  L={L}mm  alpha0={alp}deg )",
            f"( Clairaut c={c}mm  k={k} circuits )",
            f"( FW-CAM v1.0 )",
            f"( ===================================== )",
            "%",
            f"O0001 (WINDING_PROGRAM)",
            self.format_set_units_mm(),
            self.format_absolute_mode(),
            "G94",   # Feed per minute
        ]
        if self._use_inv:
            lines.append(self.format_comment("G93 inverse time mode available"))
        return lines

    def format_footer(self) -> List[str]:
        return [
            self.format_comment("Program complete"),
            "M30",
            "%",
        ]

    def format_rapid(self, x: Optional[float] = None,
                     a: Optional[float] = None) -> str:
        parts = ["G00"]
        if x is not None: parts.append(f"X{x:.{self._dp}f}")
        if a is not None: parts.append(f"A{a:.4f}")
        return " ".join(parts)

    def format_linear(self, x: Optional[float] = None,
                      a: Optional[float] = None, f: float = 800.0) -> str:
        parts = ["G01"]
        if x is not None: parts.append(f"X{x:.{self._dp}f}")
        if a is not None: parts.append(f"A{a:.4f}")
        parts.append(f"F{f:.2f}")
        return " ".join(parts)

    def format_linear_inverse_time(
        self,
        x: Optional[float],
        a: Optional[float],
        segment_time_s: float,
    ) -> str:
        """G93 inverse time format: F = 1/T [1/min]."""
        f_inv = 60.0 / max(segment_time_s, 1e-6)
        parts = ["G93 G01"]
        if x is not None: parts.append(f"X{x:.{self._dp}f}")
        if a is not None: parts.append(f"A{a:.4f}")
        parts.append(f"F{f_inv:.4f}")
        return " ".join(parts)

    def format_dwell(self, seconds: float) -> str:
        return f"G04 P{seconds:.3f}"

    def format_pause(self, msg: str = "") -> str:
        comment = f" {self.format_comment(msg)}" if msg else ""
        return f"M01{comment}"

    def format_spindle_off(self) -> str:
        return "M05"

    def format_comment(self, text: str) -> str:
        return f"({text})"

    def format_set_units_mm(self) -> str:
        return "G21"

    def format_absolute_mode(self) -> str:
        return "G90"


# ═══════════════════════════════════════════════════════════════
# CONTINUOUS A-AXIS MANAGER
# ═══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class MotionSegment:
    """
    Tek bir makine hareket segmenti.

    x_mm:   Carriage pozisyonu [mm] — mutlak
    a_deg:  Spindle açısı [°] — kümülatif, asla sarılmaz
    f_mm_min: Besleme hızı [mm/min]
    segment_time_s: Bu segmentin süresi [s]
    pass_index: Hangi WindingPass'a ait
    point_index:Bu pass içindeki indeks
    is_rapid:   True → G0 (rapid)
    is_pause:   True → M0 öncesi son segment
    comment:    Opsiyonel yorum
    """
    x_mm:          float
    a_deg:         float
    f_mm_min:      float
    segment_time_s:float = 0.0
    pass_index:    int   = -1
    point_index:   int   = -1
    is_rapid:      bool  = False
    is_pause:      bool  = False
    comment:       str   = ""


class ContinuousAAxisManager:
    """
    Kümülatif A-ekseni yöneticisi.

    Temel ilke: A asla 360°'de sıfırlanmaz.
    G-code'da A değeri monoton artar (forward) veya azalır (return).

    Rewind:
      Fiber kesme sırasında (M0 öncesi):
        A_home = floor(A_current / 360) × 360  [en yakın tam devir]
        G0 A{A_home}  → rapid rewind (fiber kesilmiş)
      Sonraki pass: A_home'dan devam

    Spindle senkronizasyonu (A hızı):
      dA/dt = ω × (180/π)   [°/s]
      F_A   = dA/dt × 60    [°/min]
      F_X   = v_x × 60      [mm/min]
      G1 X... A... F{F_X}   → F, X ekseni mm/min cinsinden
      A ekseni controller tarafından koordineli interpolasyon ile sürülür.

    GRBL not: F= mm/min for X, but A moves in °/min internally.
    The controller converts: A_speed = F × steps_per_deg_A / steps_per_mm_X.
    Bu yanlış sonuç verir! Doğru yaklaşım:
      F = sqrt(v_x² + v_A_mm²)  burada v_A_mm = ω × R [mm/s]
    yani R-değeri üzerinden normalleştirilmiş.
    """

    def __init__(
        self,
        controller:     ControllerProfile,
        mandrel_radius: float,           # R [mm]
        c_clairaut:     float,           # c [mm]
        fiber_speed:    float = 100.0,   # S' [mm/s]
    ) -> None:
        self.ctrl   = controller
        self.R      = mandrel_radius
        self.c      = c_clairaut
        self.v_S    = fiber_speed
        self._A_current = 0.0   # Kümülatif A [°]
        self._X_current = 0.0   # Son X pozisyonu [mm]

    @property
    def A_current(self) -> float:
        return self._A_current

    def reset_tracking(self) -> None:
        """Yeni program başlangıcında çağır."""
        self._A_current = 0.0
        self._X_current = 0.0

    def update(self, x_mm: float, a_delta_deg: float) -> Tuple[float, float]:
        """
        Yeni X ve ΔA ile kümülatif A güncelle.

        Returns: (x_mm, a_cumulative_deg)
        """
        self._X_current   = x_mm
        self._A_current  += a_delta_deg
        return self._X_current, self._A_current

    def set_absolute(self, x_mm: float, a_abs_deg: float) -> Tuple[float, float]:
        """Mutlak A değeri ile güncelle (başlangıç pozisyonu için)."""
        self._X_current = x_mm
        self._A_current = a_abs_deg
        return self._X_current, self._A_current

    def compute_feedrate(
        self,
        alpha_rad: float,
        curvature_limit_factor: float = 1.0,
    ) -> float:
        """
        Verilen sarım açısı için G-code F değerini hesapla.

        Strateji: F = surface_speed × cos(α) × 60 [mm/min]
        (Carriage hızı dominant, A ekseni koordineli)

        curvature_limit_factor: [0,1] — pol yakınında azaltma
        """
        v_x    = self.v_S * math.cos(alpha_rad) * curvature_limit_factor
        f_mm   = v_x * 60.0
        return min(f_mm, self.ctrl.limits.x_max_mm_min)

    def compute_a_from_path(
        self,
        phi_start_rad:   float,
        phi_end_rad:     float,
        offset_deg:      float = 0.0,
    ) -> float:
        """
        Path'ın φ değişiminden kümülatif A değişimini hesapla.

        Filament winding: spindle tam devir sayısı = Δφ / (2π) değil,
        doğrudan Δφ_rad × (180/π) = Δφ_deg'dir.

        Fark: GeodesicPath φ kümülatif (2π'yi geçer), A da kümülatif.
        """
        delta_phi = phi_end_rad - phi_start_rad   # [rad]
        return delta_phi * (180.0 / math.pi) + offset_deg

    def rewind_position(self) -> float:
        """
        En yakın tam devire rewind pozisyonu.

        A_home = floor(A_current / 360) × 360
        """
        return math.floor(self._A_current / 360.0) * 360.0

    def generate_rewind(self, comment: str = "REWIND") -> List[MotionSegment]:
        """
        Fiber kesme sonrası rewind segmenti üret.

        Fiber kesilmiş → G0 rapid, A azalır (geri sarma).
        """
        a_home = self.rewind_position()
        seg = MotionSegment(
            x_mm          = self._X_current,
            a_deg         = a_home,
            f_mm_min      = self.ctrl.limits.x_max_mm_min,
            is_rapid      = True,
            comment       = comment,
        )
        self._A_current = a_home
        return [seg]

    def path_to_segments(
        self,
        path_points: List,   # GeodesicPoint or FullBodyPoint
        pass_index:  int,
        alpha_rad:   float,
        phi_cumulative_offset: float = 0.0,  # [rad]
    ) -> List[MotionSegment]:
        """
        GeodesicPath noktalarını MotionSegment listesine dönüştür.

        Her noktada:
          X = z_mm (eksenel = carriage)
          A = cumulative A = A_prev + Δφ×(180/π)
        """
        segments: List[MotionSegment] = []
        if not path_points:
            return segments

        prev_phi = path_points[0].phi_rad

        for i, pt in enumerate(path_points):
            # X = eksenel konum
            x_mm = pt.z_mm if hasattr(pt, "z_mm") else pt.x

            # A değişimi
            delta_phi = pt.phi_rad - prev_phi
            prev_phi  = pt.phi_rad
            self._A_current += delta_phi * (180.0 / math.pi)

            # Feedrate (curvature-aware)
            kn = getattr(pt, "kappa_n", 0.0)
            if kn > 1e-9:
                v_max_curve = math.sqrt(5000.0 / kn)
                speed_fac = min(1.0, v_max_curve / max(self.v_S, 1.0))
            else:
                speed_fac = 1.0
            f = self.compute_feedrate(alpha_rad, speed_fac)

            # Segment süresi
            if i > 0 and segments:
                dx = abs(x_mm - segments[-1].x_mm)
                t  = dx / max(f / 60.0, 1e-6)
            else:
                t = 0.0

            seg = MotionSegment(
                x_mm          = x_mm,
                a_deg         = self._A_current,
                f_mm_min      = f,
                segment_time_s= t,
                pass_index    = pass_index,
                point_index   = i,
            )
            segments.append(seg)

        return segments
