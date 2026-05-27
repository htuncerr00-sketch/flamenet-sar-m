"""
motion_planner.py — CNC Hareket Planlayıcı
==========================================
ToolPoint (fiber yolu, mandrel frame) → MachineCommand (makine eksenleri) dönüşümü.

Koordinat Eşlemesi:
  ToolPoint.x        [mm]  →  MachineCommand.X  [mm]    (carriage linear)
  ToolPoint.phi_deg  [°]   →  MachineCommand.A  [°]     (spindle rotary, kümülatif)

Feed Rate Hesabı (fizik temelli):
  Hedef: Fiber teslimat hızı S' [mm/s] = sabit

  Fiber hız vektörü (silindir):
    V_x    = S' · cos(α)   [mm/s]  carriage (X ekseni) hız bileşeni
    V_circ = S' · sin(α)   [mm/s]  çevresel hız bileşeni = R · ω

  Kontrol:
    ‖V‖ = √(V_x² + V_circ²) = S' · √(cos²α + sin²α) = S'  ✓

  G-code feed rate:
    F = V_x · 60  [mm/min]   ← carriage ekseni hızı

  Spindle devir hızı (rpm):
    ω = V_circ / R = S' · sin(α) / R  [rad/s]
    rpm = ω · (60 / 2π)

Referans: Koussios (2004), Ch. 11; Özbek et al. (2020)
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from winding_math import ToolPoint, ClairautConstants, WindingParameters


# ---------------------------------------------------------------------------
# Machine Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MachineConfig:
    """
    CNC makinesi kinematik limitleri ve yapılandırması.

    Makine referansı: Özbek et al. (2020) 2-eksen lathe-type filament winding
      Carriage: Nema 23 BLDC + leadscrew
      Spindle:  Nema 23 BLDC + belt-pulley

    Birimler:
      max_x:             mm       (carriage travel)
      max_x_speed:       mm/min   (carriage linear feed)
      max_a_speed:       deg/min  (spindle rotary speed)
      fiber_speed_target: mm/s    (hedef fiber hızı)
      x_steps_per_mm:    step/mm  (carriage encoder/step resolution)
      a_steps_per_deg:   step/°   (spindle encoder/step resolution)
    """
    max_x:              float   # [mm]
    max_x_speed:        float   # [mm/min]
    max_a_speed:        float   # [deg/min]
    fiber_speed_target: float   # [mm/s]
    x_steps_per_mm:     float   # [step/mm]
    a_steps_per_deg:    float   # [step/°]

    def __post_init__(self) -> None:
        checks = [
            (self.max_x,              "max_x"),
            (self.max_x_speed,        "max_x_speed"),
            (self.max_a_speed,        "max_a_speed"),
            (self.fiber_speed_target, "fiber_speed_target"),
            (self.x_steps_per_mm,     "x_steps_per_mm"),
            (self.a_steps_per_deg,    "a_steps_per_deg"),
        ]
        for val, name in checks:
            if val <= 0.0:
                raise ValueError(f"{name} must be > 0, got {val}")

    @property
    def x_resolution_mm(self) -> float:
        """X ekseni minimum adım çözünürlüğü [mm]."""
        return 1.0 / self.x_steps_per_mm

    @property
    def a_resolution_deg(self) -> float:
        """A ekseni minimum adım çözünürlüğü [°]."""
        return 1.0 / self.a_steps_per_deg

    @property
    def max_a_rpm(self) -> float:
        """Maksimum spindle hızı [rpm]."""
        return self.max_a_speed / 360.0

    @classmethod
    def default_2axis(cls) -> "MachineConfig":
        """
        Özbek et al. (2020) makinesi varsayılan yapılandırması.
        Özellikler: 750mm x 250mm max, 8000mm/min linear, 250rpm spindle
        """
        return cls(
            max_x=750.0,
            max_x_speed=8000.0,    # mm/min
            max_a_speed=90000.0,   # 250 rpm = 250 * 360 deg/min
            fiber_speed_target=100.0,  # mm/s (ıslak sarım için konservatif)
            x_steps_per_mm=80.0,
            a_steps_per_deg=44.44,     # 16000 step/rev ÷ 360
        )

    def summary(self) -> str:
        return (
            f"Machine Configuration\n"
            f"  Max X travel:       {self.max_x:.1f} mm\n"
            f"  Max X speed:        {self.max_x_speed:.1f} mm/min = {self.max_x_speed/60:.2f} mm/s\n"
            f"  Max A speed:        {self.max_a_speed:.1f} deg/min = {self.max_a_rpm:.1f} rpm\n"
            f"  Fiber speed target: {self.fiber_speed_target:.1f} mm/s\n"
            f"  X resolution:       {self.x_resolution_mm*1000:.3f} µm\n"
            f"  A resolution:       {self.a_resolution_deg:.4f}°\n"
        )


# ---------------------------------------------------------------------------
# Machine Command
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MachineCommand:
    """
    Tek bir CNC hareket komutu.

    G-code karşılığı: G1 X{X} A{A} F{F}

    X: Mutlak carriage konumu [mm]
    A: Kümülatif spindle açısı [°] — 360°'yi geçebilir
    F: Carriage (X) feed rate [mm/min]
    """
    X:       float
    A:       float
    F:       float
    comment: str = ""

    @property
    def a_rpm_at_feed(self) -> float:
        """Bu komuttaki A ekseni hızı [rpm] — sonsuz F için sıfır döner."""
        # deg/min / 360 = rpm; ancak bu static bir hesap değil, ΔA/ΔX·F gerekir
        # Bu property yalnızca tek nokta bilgisiyle rpm veremez; planner'da hesaplanır
        return 0.0

    def __repr__(self) -> str:
        return (
            f"MachineCommand("
            f"X={self.X:.4f}mm, "
            f"A={self.A:.4f}°, "
            f"F={self.F:.1f}mm/min)"
        )


# ---------------------------------------------------------------------------
# Velocity Profile Helper
# ---------------------------------------------------------------------------

class FeedRateComputer:
    """
    Fiber hızını → carriage (X) feed rate'e dönüştürür.

    Formül:
        F [mm/min] = S' · cos(α) · 60

    Bu formül şunları garanti eder:
      - X ekseni hızı = S'·cos(α) [mm/s]
      - Çevresel hız = S'·sin(α) [mm/s]
      - Fiber hızı   = S'         [mm/s] (Pisagor üçgeni)

    Kısıtlama: F ≤ max_x_speed (makine limiti)
    Uyarı verilir ama F max'a kırpılır (küçültülür).
    """

    def __init__(self, machine: MachineConfig) -> None:
        self.machine = machine

    def compute(self, alpha_rad: float) -> float:
        """
        Carriage feed rate hesapla.

        Args:
            alpha_rad: Yerel sarım açısı [rad]

        Returns:
            Feed rate [mm/min], makine limitine kırpılmış
        """
        S_prime = self.machine.fiber_speed_target   # [mm/s]
        V_x     = S_prime * math.cos(alpha_rad)     # [mm/s]
        F       = V_x * 60.0                        # [mm/min]

        if F > self.machine.max_x_speed:
            warnings.warn(
                f"Hesaplanan F={F:.1f} mm/min, makine limitini aşıyor "
                f"({self.machine.max_x_speed:.1f} mm/min). "
                f"Gerçek fiber hızı S'={self.machine.fiber_speed_target:.1f} mm/s "
                f"yerine {self.machine.max_x_speed/60/math.cos(alpha_rad):.1f} mm/s "
                f"olacak.",
                stacklevel=2,
            )
            F = self.machine.max_x_speed

        return round(F, 2)

    def spindle_rpm_from_feed(self, F_mm_per_min: float, alpha_rad: float, R_mm: float) -> float:
        """
        Feed rate'ten spindle rpm hesapla.

        V_x     = F / 60              [mm/s]
        V_circ  = V_x · tan(α)        [mm/s]  (= S'·sin(α) when F = S'·cos(α)·60)
        ω       = V_circ / R          [rad/s]
        rpm     = ω · 60 / (2π)

        Args:
            F_mm_per_min: Carriage feed rate [mm/min]
            alpha_rad:    Sarım açısı [rad]
            R_mm:         Mandrel yarıçapı [mm]

        Returns:
            Spindle speed [rpm]
        """
        V_x    = F_mm_per_min / 60.0
        V_circ = V_x * math.tan(alpha_rad)
        omega  = V_circ / R_mm
        return omega * 60.0 / (2.0 * math.pi)


# ---------------------------------------------------------------------------
# Motion Planner
# ---------------------------------------------------------------------------

class MotionPlanner:
    """
    Fiber toolpath'i → CNC makine komutlarına dönüştürür.

    İşlem adımları:
      1. Her ToolPoint için X ve A koordinatlarını ata
      2. Feed rate hesapla (sabit fiber hız hedefi)
      3. A-ekseni hız limitini kontrol et
      4. Makine koordinat sınırlarını doğrula
      5. MachineCommand listesi döndür
    """

    def __init__(self, machine: MachineConfig) -> None:
        self.machine       = machine
        self.feed_computer = FeedRateComputer(machine)

    def plan(self, toolpath: List[ToolPoint]) -> List[MachineCommand]:
        """
        Toolpath'i machine commands'a çevir.

        Args:
            toolpath: ToolPoint listesi (sıralı, boş olmayan)

        Returns:
            MachineCommand listesi (toolpath ile bire bir eşleşen)

        Raises:
            ValueError: Toolpath boş veya sınır dışı koordinatlar var
        """
        if not toolpath:
            raise ValueError("Toolpath boş olamaz.")

        commands: List[MachineCommand] = []

        for i, tp in enumerate(toolpath):
            # --- Koordinat dönüşümü ---
            X = tp.x             # [mm]
            A = tp.A_axis_deg    # [°, kümülatif]

            # --- X eksen sınır kontrolü ---
            if X < -1e-6 or X > self.machine.max_x + 1e-6:
                raise ValueError(
                    f"ToolPoint[{i}] X={X:.4f} mm makine sınırlarını aşıyor "
                    f"[0, {self.machine.max_x:.1f} mm]"
                )

            # --- Feed rate ---
            F = self.feed_computer.compute(tp.alpha_rad)

            # --- Segment yorumu ---
            comment = tp.segment if tp.segment else ""

            commands.append(MachineCommand(
                X       = round(X, 4),
                A       = round(A, 4),
                F       = F,
                comment = comment,
            ))

        # --- A-eksen hız doğrulaması ---
        self._check_a_speed(commands)

        return commands

    def _check_a_speed(self, commands: List[MachineCommand]) -> None:
        """
        A-ekseni açısal hızını kontrol et.

        deg/min = (ΔA [deg]) / (ΔX [mm] / F [mm/min])
                = ΔA · F / ΔX

        Kısıtlama: deg/min ≤ max_a_speed
        """
        max_seen = 0.0

        for i in range(1, len(commands)):
            dX = abs(commands[i].X - commands[i - 1].X)
            dA = abs(commands[i].A - commands[i - 1].A)
            F  = commands[i].F

            if dX < 1e-8:
                continue  # Saf rotasyon (lathe pause) — bu geçiş yoktur Faz 1'de

            # dt = dX / F  [min]
            dt       = dX / F          # [min]
            a_speed  = dA / dt         # [deg/min]
            max_seen = max(max_seen, a_speed)

            if a_speed > self.machine.max_a_speed * 1.05:
                warnings.warn(
                    f"Komut[{i}]: A-ekseni hızı {a_speed:.1f} deg/min "
                    f"({a_speed/360:.1f} rpm) makine limitini aşıyor "
                    f"({self.machine.max_a_speed:.1f} deg/min = "
                    f"{self.machine.max_a_rpm:.1f} rpm). "
                    f"Fiber hızını veya sarım açısını azaltın.",
                    stacklevel=3,
                )

    def compute_spindle_rpm(
        self,
        commands:   List[MachineCommand],
        alpha_rad:  float,
        radius_mm:  float,
    ) -> List[float]:
        """
        Her komut için spindle rpm değerini hesapla (analiz için).

        Args:
            commands:   MachineCommand listesi
            alpha_rad:  Sarım açısı [rad] (silindir için sabit)
            radius_mm:  Mandrel yarıçapı [mm]

        Returns:
            Her komut için rpm değerleri
        """
        rpms = []
        for cmd in commands:
            rpm = self.feed_computer.spindle_rpm_from_feed(
                cmd.F, alpha_rad, radius_mm
            )
            rpms.append(round(rpm, 4))
        return rpms

    def trajectory_summary(self, commands: List[MachineCommand]) -> dict:
        """Hareket özeti için istatistikler."""
        if not commands:
            return {}

        x_vals  = [c.X for c in commands]
        a_vals  = [c.A for c in commands]
        f_vals  = [c.F for c in commands]

        total_x  = abs(x_vals[-1] - x_vals[0])
        total_da = abs(a_vals[-1] - a_vals[0])

        return {
            "n_commands":   len(commands),
            "x_range_mm":   (min(x_vals), max(x_vals)),
            "a_range_deg":  (min(a_vals), max(a_vals)),
            "total_a_deg":  total_da,
            "total_a_rev":  total_da / 360.0,
            "feed_mm_min":  (min(f_vals), max(f_vals)),
        }
