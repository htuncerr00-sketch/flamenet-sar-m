"""
machine_model.py — Makine Fizik Modeli
=======================================
Filament sarım makinesi gerçek dinamiklerini modelleyen sınıflar.

Makine bileşen hiyerarşisi:
  ┌─ Carriage (X ekseni)
  │   ├─ Motor: tork, eylemsizlik, adım çözünürlüğü
  │   ├─ Lead Screw: adım adımı, verim
  │   └─ Kizma + kılavuz: sürtünme, kütle
  │
  └─ Spindle (A ekseni)
      ├─ Motor: tork, eylemsizlik
      ├─ Tahrik (belt/pulley): dişli oranı
      └─ Mandrel: dolma eylemsizliği (winding ilerledikçe artar)

Temel fizik:
  Carriage ivmesi (Newton II):
    F_net = F_motor - F_friction - F_gravity_component
    a_x   = F_net / m_total   [mm/s²]

  Spindle ivmesi (dönme hareketi):
    τ_net = τ_motor - τ_friction - τ_fiber_tension
    α_A   = τ_net / J_total   [rad/s²]

  Adım hızı (pulse rate) limiti:
    v_x [mm/s] = f_pulse [Hz] / steps_per_mm
    Limit: f_pulse ≤ f_max_Hz

  Turn-around analizi:
    Carriage v_x → 0 → -v_x süresinde:
    t_ta = 2 · v_x / a_x_max   [s]
    Bu sürede spindle: φ_drift = ω · t_ta + 0.5·α·t_ta²
    → Drift compensation G-code'a eklenmeli

Referans:
  ADA268923 (University of Texas, 1994): Appendix I-K, Table 7.1
  Koussios (2004): Tablo 12.1 — lathe winder dinamik limitleri
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Unit Conversions (SI → makine birimleri)
# ---------------------------------------------------------------------------

def oz_in_to_Nm(oz_in: float) -> float:
    """oz·in → N·m"""
    return oz_in * 0.007062

def oz_in2_to_kgcm2(oz_in2: float) -> float:
    """oz·in² → kg·cm²"""
    return oz_in2 * 0.018290

def lb_in2_to_kgcm2(lb_in2: float) -> float:
    """lb·in² → kg·cm²"""
    return lb_in2 * 0.29264

def kgcm2_to_kgm2(kgcm2: float) -> float:
    """kg·cm² → kg·m²"""
    return kgcm2 * 1e-4


# ---------------------------------------------------------------------------
# Motor Specification
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MotorSpec:
    """
    Adım motor spesifikasyonu.

    Referans: ADA268923 Table 7.1
      Motor 1 (S83-135, spindle): 400 oz·in = 2.82 N·m, 10.24 oz·in²
      Motor 2 (S57-51, carriage):  65 oz·in = 0.46 N·m,  0.48 oz·in²
    """
    name:              str
    static_torque_Nm:  float   # [N·m]
    rotor_inertia_kgm2:float   # [kg·m²]
    steps_per_rev:     int     # [step/rev] — sürücü microstepping dahil
    max_pulse_hz:      float   # [Hz] — kontrolör max step hızı
    holding_torque_Nm: float   # [N·m] — durma torku (genellikle %70 static)
    voltage_V:         float   # [V] — nominal voltaj

    @property
    def step_angle_deg(self) -> float:
        """Adım açısı [°]."""
        return 360.0 / self.steps_per_rev

    @property
    def max_angular_velocity_rads(self) -> float:
        """Maksimum açısal hız (pulse hızından) [rad/s]."""
        return self.max_pulse_hz / self.steps_per_rev * 2.0 * math.pi

    @classmethod
    def spindle_motor(cls) -> "MotorSpec":
        """
        ADA268923 S83-135 motor — spindle sürücüsü.
        25,000 step/rev (0.0144°/step) microstepping.
        """
        return cls(
            name               = "S83-135 (Spindle)",
            static_torque_Nm   = oz_in_to_Nm(400.0),  # 2.824 N·m
            rotor_inertia_kgm2 = kgcm2_to_kgm2(oz_in2_to_kgcm2(10.24)),  # 1.87e-4 kg·m²
            steps_per_rev      = 25000,
            max_pulse_hz       = 50000,   # tipik steppper controller limit
            holding_torque_Nm  = oz_in_to_Nm(280.0),   # 70%
            voltage_V          = 24.0,
        )

    @classmethod
    def carriage_motor(cls) -> "MotorSpec":
        """
        ADA268923 S57-51 motor — carriage sürücüsü.
        """
        return cls(
            name               = "S57-51 (Carriage)",
            static_torque_Nm   = oz_in_to_Nm(65.0),   # 0.459 N·m
            rotor_inertia_kgm2 = kgcm2_to_kgm2(oz_in2_to_kgcm2(0.48)),  # 8.77e-6 kg·m²
            steps_per_rev      = 25000,
            max_pulse_hz       = 50000,
            holding_torque_Nm  = oz_in_to_Nm(45.0),
            voltage_V          = 24.0,
        )


# ---------------------------------------------------------------------------
# Drive Train
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class LeadScrewSpec:
    """
    Vida-ray tahrik sistemi.

    Referans: ADA268923 Appendix I
      pitch = 2 threads/in → 12.7 mm/rev
      radius = 0.75 in = 19.05 mm
      length = 27 in = 685.8 mm
      density ρ = 7800 kg/m³ (çelik)
    """
    pitch_mm:     float   # [mm/rev]
    radius_mm:    float   # [mm]
    length_mm:    float   # [mm]
    density_kgm3: float   # [kg/m³]
    efficiency:   float   # [0..1] — mekanik verim

    @property
    def inertia_kgm2(self) -> float:
        """Vida eylemsizliği [kg·m²]."""
        # Içi dolu silindir: J = 0.5·m·r²
        r_m  = self.radius_mm / 1000.0
        l_m  = self.length_mm / 1000.0
        mass = self.density_kgm3 * math.pi * r_m**2 * l_m
        return 0.5 * mass * r_m**2

    @property
    def mm_per_step(self) -> float:
        """Tek motor adımında carriage hareketi [mm/step]."""
        return self.pitch_mm / 25000  # 25000 step/rev

    @classmethod
    def default(cls) -> "LeadScrewSpec":
        return cls(
            pitch_mm     = 12.7,    # 2 threads/in
            radius_mm    = 19.05,   # 0.75 in
            length_mm    = 685.8,   # 27 in
            density_kgm3 = 7800.0,
            efficiency   = 0.85,
        )


@dataclass(frozen=True, slots=True)
class SpindleDriveSpec:
    """
    Spindle tahrik sistemi (belt-pulley veya direct).
    """
    gear_ratio:    float   # motor_turns / spindle_turns
    efficiency:    float   # [0..1]
    belt_inertia_kgm2: float = 0.0   # küçük, genellikle ihmal edilir

    @classmethod
    def direct(cls) -> "SpindleDriveSpec":
        return cls(gear_ratio=1.0, efficiency=0.95)

    @classmethod
    def belt_2to1(cls) -> "SpindleDriveSpec":
        return cls(gear_ratio=2.0, efficiency=0.92)


# ---------------------------------------------------------------------------
# Mandrel Inertia (Winding İlerledikçe Değişir)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MandrelInertia:
    """
    Mandrel + sarılı fiber ağırlığı eylemsizliği.

    Boş mandrel (alüminyum silindir):
      J_empty = 0.5 · m_mandrel · R²

    Sarılı fiber katmanı eklendikçe:
      J_wound = J_empty + ΔJ_fiber
      ΔJ_fiber ≈ Σ 0.5 · m_fiber_layer · R_eff²

    ADA268923'ten: J_total = 307.88 lb·in² = 901 kg·cm² = 0.0901 kg·m²
    """
    empty_kgm2:   float   # Boş mandrel [kg·m²]
    max_wound_kgm2: float # Tam dolu mandrel [kg·m²]
    current_fill: float = 0.0   # [0..1] — mevcut doluluk oranı

    @property
    def current_kgm2(self) -> float:
        """Mevcut eylemsizlik [kg·m²]."""
        return self.empty_kgm2 + self.current_fill * (self.max_wound_kgm2 - self.empty_kgm2)

    @classmethod
    def from_ada268923(cls) -> "MandrelInertia":
        """ADA268923 verilerinden — toplam J ≈ 901 kg·cm²."""
        J_total_kgcm2 = lb_in2_to_kgcm2(307.88)  # 90.08 kg·cm²
        return cls(
            empty_kgm2    = kgcm2_to_kgm2(J_total_kgcm2 * 0.6),  # boş ≈ %60
            max_wound_kgm2= kgcm2_to_kgm2(J_total_kgcm2 * 1.5),  # dolu ≈ %150
        )


# ---------------------------------------------------------------------------
# Machine Physics Engine
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MachinePhysics:
    """
    Tam makine fizik modeli.

    Sağlanan hesaplamalar:
      - Carriage maksimum ivmesi (motor torku ve karriage kütlesinden)
      - Spindle maksimum açısal ivmesi (motor torku ve eylemsizlikten)
      - Turn-around süresi ve fiber hız düşüşü
      - Adım hızı limitleri (pulse rate)
      - Adım çözünürlüğü (minimum hareketi)
      - Sarım süresince eylemsizlik değişimi

    Kullanım:
        phys = MachinePhysics.default()
        print(phys.carriage_max_acc_mm_s2())    # [mm/s²]
        print(phys.spindle_max_acc_rad_s2())    # [rad/s²]
        ta = phys.turn_around_time_s(fiber_speed=100, alpha_deg=30)
        print(phys.fiber_speed_dip_percent(ta, fiber_speed=100, alpha_deg=30))
    """
    spindle_motor:   MotorSpec
    carriage_motor:  MotorSpec
    lead_screw:      LeadScrewSpec
    spindle_drive:   SpindleDriveSpec
    mandrel_inertia: MandrelInertia
    carriage_mass_kg:float         # toplam carriage kütlesi [kg]
    carriage_friction_N: float     # sürtünme kuvveti [N]
    fiber_tension_N: float = 15.0  # tipik sarım gerilmesi [N]

    # -----------------------------------------------------------------------
    # Carriage (X ekseni)
    # -----------------------------------------------------------------------

    def carriage_max_acc_mm_s2(self) -> float:
        """
        Carriage maksimum ivmesi [mm/s²].

        F_net = η · T_motor / r_screw - F_friction
        a     = F_net / m_carriage
        r_screw = pitch / (2π)  [m]
        """
        pitch_m  = self.lead_screw.pitch_mm / 1000.0
        r_screw  = pitch_m / (2.0 * math.pi)   # [m/rad]
        F_max    = (self.carriage_motor.static_torque_Nm *
                    self.lead_screw.efficiency / r_screw)
        F_net    = F_max - self.carriage_friction_N
        a        = max(0.0, F_net / self.carriage_mass_kg)
        return a * 1000.0   # m/s² → mm/s²

    def carriage_max_speed_mm_s(self) -> float:
        """
        Carriage maksimum hızı (adım hızı limitinden) [mm/s].

        v_max = f_max_pulse · pitch / steps_per_rev
        """
        return (self.carriage_motor.max_pulse_hz *
                self.lead_screw.pitch_mm /
                self.carriage_motor.steps_per_rev)

    def carriage_step_resolution_mm(self) -> float:
        """Carriage minimum adım mesafesi [mm]."""
        return self.lead_screw.mm_per_step

    def carriage_acc_torque_Nm(self, a_mm_s2: float) -> float:
        """
        Verilen ivme için gerekli motor torku [N·m].

        T_acc = m · a · r_screw / η + T_friction
        """
        pitch_m  = self.lead_screw.pitch_mm / 1000.0
        r_screw  = pitch_m / (2.0 * math.pi)
        a_m_s2   = a_mm_s2 / 1000.0
        T_acc    = (self.carriage_mass_kg * a_m_s2 * r_screw /
                    self.lead_screw.efficiency)
        T_fric   = self.carriage_friction_N * r_screw
        return T_acc + T_fric

    # -----------------------------------------------------------------------
    # Spindle (A ekseni)
    # -----------------------------------------------------------------------

    def spindle_max_acc_rad_s2(self, fill: float = 0.0) -> float:
        """
        Spindle maksimum açısal ivmesi [rad/s²].

        α_max = η · T_motor · gear_ratio / J_total
        J_total = J_motor·gear_ratio² + J_mandrel(fill)
        """
        self.mandrel_inertia.current_fill = fill
        J_motor   = (self.spindle_motor.rotor_inertia_kgm2 *
                     self.spindle_drive.gear_ratio**2)
        J_mandrel = self.mandrel_inertia.current_kgm2
        J_total   = J_motor + J_mandrel + self.spindle_drive.belt_inertia_kgm2

        T_available = (self.spindle_motor.static_torque_Nm *
                       self.spindle_drive.gear_ratio *
                       self.spindle_drive.efficiency)
        return T_available / J_total

    def spindle_max_speed_rpm(self) -> float:
        """Spindle maksimum dönüş hızı (pulse limitinden) [rpm]."""
        rad_per_s = (self.spindle_motor.max_pulse_hz /
                     self.spindle_motor.steps_per_rev * 2.0 * math.pi /
                     self.spindle_drive.gear_ratio)
        return rad_per_s * 60.0 / (2.0 * math.pi)

    def spindle_step_resolution_deg(self) -> float:
        """Spindle minimum adım açısı [°]."""
        return (self.spindle_motor.step_angle_deg /
                self.spindle_drive.gear_ratio)

    # -----------------------------------------------------------------------
    # Turn-Around Dynamics
    # -----------------------------------------------------------------------

    def turn_around_time_s(
        self,
        fiber_speed_mm_s: float,
        alpha_deg:        float,
    ) -> float:
        """
        Turn-around süresi [s].

        Carriage v_x → 0 → v_x (ters yön) için gereken süre.
        v_x = S' · cos(α)
        t_ta = 2 · v_x / a_x_max

        Referans: ADA268923 Appendix I — t_acc = 0.05s örneği

        Args:
            fiber_speed_mm_s: Hedef fiber hızı S' [mm/s]
            alpha_deg:        Sarım açısı [°]

        Returns:
            Turn-around süresi [s]
        """
        v_x   = fiber_speed_mm_s * math.cos(math.radians(alpha_deg))
        a_max = self.carriage_max_acc_mm_s2()
        if a_max <= 0:
            return float("inf")
        return 2.0 * v_x / a_max

    def fiber_speed_dip_percent(
        self,
        fiber_speed_mm_s: float,
        alpha_deg:        float,
    ) -> float:
        """
        Turn-around süresinde fiber hızı ortalama düşüşü [%].

        Turn-around sırasında carriage durur (v_x=0), ama spindle devam eder.
        Bu sürede fiber hızı: S'(t) = R · ω = S' · sin(α) (yalnızca dönel)
        Yani fiber hızı: S'_ta = S' · sin(α)  (cos bileşeni kaybolur)

        Düşüş oranı: (S' - S'·sin(α)) / S' = 1 - sin(α)

        NOT: Bu yalnızca silindir için geçerli.
        Dome bölgesinde α→90° → sin(α)→1 → dip küçülür.
        """
        sin_a = math.sin(math.radians(alpha_deg))
        return (1.0 - sin_a) * 100.0   # [%]

    def fiber_speed_dip_duration_fraction(
        self,
        fiber_speed_mm_s: float,
        alpha_deg:        float,
        mandrel_length_mm:float,
    ) -> float:
        """
        Turn-around süresinin toplam devre süresine oranı [0..1].

        Bir devre süresi ≈ 2L / v_x  [s]
        Turn-around süresi = t_ta [s]
        Oran = t_ta / t_circuit
        """
        v_x       = fiber_speed_mm_s * math.cos(math.radians(alpha_deg))
        if v_x <= 0:
            return 1.0
        t_circuit = 2.0 * mandrel_length_mm / v_x   # [s]
        t_ta      = self.turn_around_time_s(fiber_speed_mm_s, alpha_deg)
        return min(1.0, t_ta / t_circuit)

    # -----------------------------------------------------------------------
    # Detailed Kinematic Report
    # -----------------------------------------------------------------------

    def kinematic_report(
        self,
        fiber_speed_mm_s: float,
        alpha_deg:        float,
        mandrel_radius_mm:float,
        mandrel_length_mm:float,
    ) -> str:
        """Tam kinematik analiz raporu."""
        v_x         = fiber_speed_mm_s * math.cos(math.radians(alpha_deg))
        rpm         = (fiber_speed_mm_s * math.sin(math.radians(alpha_deg)) /
                       (2.0 * math.pi * mandrel_radius_mm) * 60.0)
        a_x_max     = self.carriage_max_acc_mm_s2()
        a_A_max     = math.degrees(self.spindle_max_acc_rad_s2())   # [°/s²]
        t_ta        = self.turn_around_time_s(fiber_speed_mm_s, alpha_deg)
        dip         = self.fiber_speed_dip_percent(fiber_speed_mm_s, alpha_deg)
        dip_frac    = self.fiber_speed_dip_duration_fraction(
            fiber_speed_mm_s, alpha_deg, mandrel_length_mm)

        # Tork kontrolleri
        T_req_x = self.carriage_acc_torque_Nm(a_x_max)
        rpm_limit = self.spindle_max_speed_rpm()
        speed_limit_x = self.carriage_max_speed_mm_s()

        lines = [
            "=" * 58,
            "MAKİNE KİNEMATİK ANALİZİ",
            "=" * 58,
            f"  Parametreler: S'={fiber_speed_mm_s:.1f}mm/s, α={alpha_deg:.2f}°",
            f"  R={mandrel_radius_mm:.1f}mm, L={mandrel_length_mm:.1f}mm",
            "-" * 58,
            "  CARRIAGE (X EKSENİ):",
            f"    v_x = S'·cos(α) = {v_x:.2f} mm/s   [limit: {speed_limit_x:.1f}]",
            f"    {'✓' if v_x < speed_limit_x else '✗'} Hız limiti",
            f"    a_max (fiziksel) = {a_x_max:.1f} mm/s²",
            f"    T_req (turn-around) = {T_req_x:.4f} N·m   [motor: {self.carriage_motor.static_torque_Nm:.4f}]",
            f"    Adım çözünürlüğü = {self.carriage_step_resolution_mm()*1000:.3f} µm",
            "-" * 58,
            "  SPİNDLE (A EKSENİ):",
            f"    rpm = {rpm:.4f}   [limit: {rpm_limit:.1f}]",
            f"    {'✓' if rpm < rpm_limit else '✗'} RPM limiti",
            f"    α_max = {a_A_max:.1f} °/s²",
            f"    Açı çözünürlüğü = {self.spindle_step_resolution_deg():.5f}°",
            "-" * 58,
            "  TURN-AROUND DİNAMİKLERİ:",
            f"    t_turnaround = {t_ta*1000:.2f} ms",
            f"    Fiber hız düşüşü = {dip:.1f}%  (cosine bileşeni kaybı)",
            f"    Dip süresi / devre = {dip_frac*100:.2f}%",
            f"    {'✓ KABUL' if dip_frac < 0.05 else '⚠ DİKKAT' if dip_frac < 0.15 else '✗ KRİTİK'}"
            f" — {'Hızlanma süresi kısa' if dip_frac < 0.05 else 'Reçine kalitesi etkilenebilir'}",
            "=" * 58,
        ]
        return "\n".join(lines)

    # -----------------------------------------------------------------------
    # Factory method
    # -----------------------------------------------------------------------

    @classmethod
    def default(cls) -> "MachinePhysics":
        """
        Özbek et al. (2020) makinesi — ana referans model.

        Doğrulanmış değerler (Özbek 2020 Table/Fig):
          X max:  8000 mm/min = 133.3 mm/s   → $110=8000
          A max:  250 rpm = 90000 deg/min     → $113=90000
          X res:  80 step/mm                  → $100=80
          A res:  44.44 step/deg              → $103=44.44

        Motor modellemesi:
          pitch=5mm, 400 step/rev (half-step 200-step motor)
          80 step/mm = 400/5 ✓
          f_max = 133.3 mm/s × 80 step/mm = 10667 Hz ✓
        """
        # Carriage motoru: pitch=5mm, 400step/rev → 80step/mm
        carriage = MotorSpec(
            name               = "Carriage-NEMA23 (Özbek 2020)",
            static_torque_Nm   = oz_in_to_Nm(65.0),   # 0.459 N·m
            rotor_inertia_kgm2 = kgcm2_to_kgm2(oz_in2_to_kgcm2(0.48)),
            steps_per_rev      = 400,         # half-step 200-step motor
            max_pulse_hz       = 11000,       # → 137.5 mm/s max (8000mm/min = 10667Hz)
            holding_torque_Nm  = oz_in_to_Nm(45.0),
            voltage_V          = 24.0,
        )
        # Spindle motoru: 44.44 step/deg → 16000 step/rev
        spindle = MotorSpec(
            name               = "Spindle-NEMA23 (Özbek 2020)",
            static_torque_Nm   = oz_in_to_Nm(400.0),  # 2.82 N·m
            rotor_inertia_kgm2 = kgcm2_to_kgm2(oz_in2_to_kgcm2(10.24)),
            steps_per_rev      = 16000,       # 44.44 step/deg × 360 ≈ 16000
            max_pulse_hz       = 67000,       # → 250rpm: 250×360/60×44.44×(1/360)×16000
            holding_torque_Nm  = oz_in_to_Nm(280.0),
            voltage_V          = 24.0,
        )
        screw = LeadScrewSpec(
            pitch_mm     = 5.0,    # 80 step/mm × 5mm/rev = 400 step/rev ✓
            radius_mm    = 8.0,
            length_mm    = 800.0,
            density_kgm3 = 7800.0,
            efficiency   = 0.85,
        )
        return cls(
            spindle_motor       = spindle,
            carriage_motor      = carriage,
            lead_screw          = screw,
            spindle_drive       = SpindleDriveSpec.direct(),
            mandrel_inertia     = MandrelInertia.from_ada268923(),
            carriage_mass_kg    = 11.33,
            carriage_friction_N = 5.0,
            fiber_tension_N     = 15.0,
        )

    @classmethod
    def ada268923(cls) -> "MachinePhysics":
        """ADA268923 UT-Austin araştırma makinesi (1994)."""
        return cls(
            spindle_motor    = MotorSpec.spindle_motor(),
            carriage_motor   = MotorSpec.carriage_motor(),
            lead_screw       = LeadScrewSpec.default(),
            spindle_drive    = SpindleDriveSpec.direct(),
            mandrel_inertia  = MandrelInertia.from_ada268923(),
            carriage_mass_kg = 11.33,
            carriage_friction_N = 5.0,
            fiber_tension_N  = 15.0,
        )


# ---------------------------------------------------------------------------
# Machine-Aware Constraint Checker
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MachineConstraintResult:
    """Tek bir (α, fiber_speed) kombinasyonu için kısıt sonuçları."""
    alpha_deg:         float
    fiber_speed_mm_s:  float
    v_x_mm_s:          float
    rpm:               float
    a_x_mm_s2:         float    # hesaplanan turn-around ivmesi
    a_x_limit:         float    # makine limiti
    t_ta_ms:           float    # turn-around süresi [ms]
    dip_percent:       float    # fiber hız düşüşü [%]
    dip_fraction:      float    # dip_süresi / devre_süresi
    x_speed_ok:        bool
    x_accel_ok:        bool
    spindle_rpm_ok:    bool
    pulse_rate_x_hz:   float
    pulse_rate_a_hz:   float
    pulse_rate_ok:     bool
    overall_ok:        bool


def check_machine_constraints(
    physics:           MachinePhysics,
    alpha_deg:         float,
    fiber_speed_mm_s:  float,
    mandrel_radius_mm: float,
    mandrel_length_mm: float,
) -> MachineConstraintResult:
    """
    Verilen sarım açısı ve fiber hızı için tüm makine kısıtlarını kontrol et.
    """
    a_rad  = math.radians(alpha_deg)
    v_x    = fiber_speed_mm_s * math.cos(a_rad)
    v_circ = fiber_speed_mm_s * math.sin(a_rad)
    rpm    = v_circ / (2.0 * math.pi * mandrel_radius_mm) * 60.0

    # Makine limitleri
    v_x_lim   = physics.carriage_max_speed_mm_s()
    rpm_lim   = physics.spindle_max_speed_rpm()
    a_x_lim   = physics.carriage_max_acc_mm_s2()

    # Gerçekçi ivme (turn-around)
    a_x_actual = 2.0 * v_x / 0.05 if v_x > 0 else 0.0  # t_acc = 0.05s (ADA268923)

    # Adım hızları
    f_x = v_x / (physics.lead_screw.pitch_mm / physics.carriage_motor.steps_per_rev)
    f_a = (v_circ / mandrel_radius_mm * physics.spindle_drive.gear_ratio /
           (2.0 * math.pi) * physics.spindle_motor.steps_per_rev)

    t_ta  = physics.turn_around_time_s(fiber_speed_mm_s, alpha_deg)
    dip   = physics.fiber_speed_dip_percent(fiber_speed_mm_s, alpha_deg)
    dip_f = physics.fiber_speed_dip_duration_fraction(
        fiber_speed_mm_s, alpha_deg, mandrel_length_mm)

    x_speed_ok  = v_x <= v_x_lim * 1.02
    x_accel_ok  = a_x_actual <= a_x_lim * 1.05
    rpm_ok      = rpm <= rpm_lim * 1.02
    f_ok        = (f_x <= physics.carriage_motor.max_pulse_hz and
                   f_a <= physics.spindle_motor.max_pulse_hz)
    overall     = x_speed_ok and x_accel_ok and rpm_ok and f_ok

    return MachineConstraintResult(
        alpha_deg        = alpha_deg,
        fiber_speed_mm_s = fiber_speed_mm_s,
        v_x_mm_s         = v_x,
        rpm              = rpm,
        a_x_mm_s2        = a_x_actual,
        a_x_limit        = a_x_lim,
        t_ta_ms          = t_ta * 1000.0,
        dip_percent      = dip,
        dip_fraction     = dip_f,
        x_speed_ok       = x_speed_ok,
        x_accel_ok       = x_accel_ok,
        spindle_rpm_ok   = rpm_ok,
        pulse_rate_x_hz  = f_x,
        pulse_rate_a_hz  = f_a,
        pulse_rate_ok    = f_ok,
        overall_ok       = overall,
    )
