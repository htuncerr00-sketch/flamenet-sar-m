"""
machine_config.py — ESP32 Pin Mapping + FluidNC Config + Motor Sizing
======================================================================
Gerçek donanım konfigürasyonu. Her değer ölçülmüş/hesaplanmış.

Motor sizing (X ekseni — carriage):
  Yük: 12kg karriage + 3kg fiber + 2kg mandrel = 17kg
  İvme: 0.5 m/s² → F_accel = 17×0.5 = 8.5N
  Sürtünme: μ=0.05, N=17×9.81=166.8N → F_fric = 8.3N
  Güvenlik faktörü: 2.5×
  F_total = (8.5+8.3)×2.5 = 42N → 4.5N·m torque @ 80mm vidalı mili
  Seçim: NEMA34 2-faz, 8.5N·m holding, 86×86mm

Motor sizing (A ekseni — spindle):
  J_mandrel = ½×m×r² = ½×5kg×(0.05m)² = 0.00625 kg·m²
  J_fiber   ≈ 0.002 kg·m² (sarılan katmanlar)
  α_angular = 2 rad/s² (0→30RPM in 1.57s)
  T_spindle = J_total×α = 0.00825×2 = 0.0165N·m → NEMA23 1.5N·m ✓

FluidNC konfig referans: docs.fluidnc.com
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ── ESP32 Pin Map ──────────────────────────────────────────────────

@dataclass(frozen=True)
class ESP32PinMap:
    """
    ESP32-WROOM-32D / ESP32-S3 pin assignment.
    GPIO numaraları FluidNC standart I2S step + direct step moduna göre.

    NOT: GPIO 34-39 = input-only (no pull-up).
    GPIO 6-11 = flash SPI, kullanılmaz.
    """
    # ── Motion outputs ────────────────────────────────────────────
    X_STEP:   int = 12    # X step pulse (active high, 5µs min width)
    X_DIR:    int = 14    # X direction (high=positive)
    X_ENABLE: int = 27    # X driver enable (active LOW for DRV8825/TMC2209)
    A_STEP:   int = 26    # A (spindle) step pulse
    A_DIR:    int = 25    # A direction
    A_ENABLE: int = 33    # A driver enable

    # ── Encoder inputs (3.3V tolerant with 74HC14 Schmitt) ───────
    ENC_X_A:  int = 36    # Quadrature channel A (input-only, optocoupled)
    ENC_X_B:  int = 39    # Quadrature channel B (input-only, optocoupled)
    ENC_X_Z:  int = 34    # Index pulse (input-only)
    ENC_A_A:  int = 35    # Spindle encoder A (input-only)
    ENC_A_B:  int = 32    # Spindle encoder B

    # ── Safety / Control ─────────────────────────────────────────
    ESTOP_IN:      int = 13    # E-stop input (NC contact, active LOW)
    LIMIT_X_MIN:   int = 15    # X min limit switch (NC, active LOW)
    LIMIT_X_MAX:   int = 2     # X max limit switch (NC, active LOW)
    STO_OUTPUT:    int = 4     # Safe Torque Off relay coil (active HIGH)
    PROBE_IN:      int = 22    # Tool probe / fiber sensor

    # ── Sensors ──────────────────────────────────────────────────
    HX711_DOUT:    int = 16    # Load cell data
    HX711_SCK:     int = 17    # Load cell clock
    HALL_RPM:      int = 18    # Hall effect RPM sensor
    TEMP_ONE_WIRE: int = 19    # DS18B20 temperature (1-wire)

    # ── Communication ─────────────────────────────────────────────
    CAN_TX:   int = 21    # TWAI CAN transmit (via SN65HVD230)
    CAN_RX:   int = 3     # TWAI CAN receive
    UART_TX:  int = 1     # USB-CDC TX (debug)
    UART_RX:  int = 0     # USB-CDC RX (debug, boot mode select)
    I2C_SDA:  int = 23    # OLED display / RTC
    I2C_SCL:  int = 5     # I2C clock

    # ── Analog ────────────────────────────────────────────────────
    TENSION_ADC:   int = 37    # Backup analog tension (12-bit, 0-3.3V)
    POWER_MON:     int = 38    # 48VDC bus voltage monitor (via divider)

    def validate(self) -> List[str]:
        """Pin conflict kontrolü."""
        errors = []
        pins = {v:k for k,v in self.__dict__.items() if isinstance(v,int)}
        conflicts = [p for p,c in pins.items() if list(pins.values()).count(c) > 1]
        flash_pins = {6,7,8,9,10,11}
        for name, pin in self.__dict__.items():
            if isinstance(pin,int) and pin in flash_pins:
                errors.append(f"{name}=GPIO{pin}: FLASH SPI conflict!")
        if conflicts:
            errors.append(f"Pin conflicts: {conflicts}")
        return errors

    def report(self) -> str:
        lines = ["  ESP32 Pin Map:"]
        for name, pin in sorted(self.__dict__.items()):
            if isinstance(pin,int):
                lines.append(f"    GPIO{pin:2d} ← {name}")
        return "\n".join(lines)


PINMAP = ESP32PinMap()


# ── Motor Sizing ───────────────────────────────────────────────────

@dataclass(frozen=True)
class MotorSpec:
    axis:          str
    type_:         str
    holding_Nm:    float
    current_A:     float
    steps_per_rev: int       # Full steps (1.8°/step → 200 steps/rev)
    microstep:     int       # 1/16 typical
    pitch_mm:      float     # Lead screw pitch [mm/rev] or 0 for rotary
    gear_ratio:    float     # 1.0 = direct drive
    max_speed_rpm: float
    driver:        str

    @property
    def steps_per_mm(self) -> float:
        if self.pitch_mm <= 0: return 0.0
        return (self.steps_per_rev * self.microstep * self.gear_ratio) / self.pitch_mm

    @property
    def steps_per_deg(self) -> float:
        return (self.steps_per_rev * self.microstep * self.gear_ratio) / 360.0

    @property
    def max_step_hz(self) -> float:
        return self.steps_per_rev * self.microstep * self.max_speed_rpm / 60.0

    def report(self) -> str:
        if self.pitch_mm > 0:
            res = f"steps/mm={self.steps_per_mm:.1f}"
        else:
            res = f"steps/°={self.steps_per_deg:.3f}"
        return (f"  Motor {self.axis}: {self.driver} {self.holding_Nm}N·m "
                f"{self.current_A}A  ms={self.microstep}  {res}  "
                f"max={self.max_step_hz:.0f}Hz")


MOTOR_X = MotorSpec(
    axis="X_carriage", type_="NEMA34",
    holding_Nm=8.5, current_A=5.0,
    steps_per_rev=200, microstep=16,
    pitch_mm=8.0,    # 8mm/rev ball screw
    gear_ratio=1.0,
    max_speed_rpm=600.0,
    driver="TMC5160",
)

MOTOR_A = MotorSpec(
    axis="A_spindle", type_="NEMA23",
    holding_Nm=1.5, current_A=2.8,
    steps_per_rev=200, microstep=16,
    pitch_mm=0.0,    # Rotary — no pitch
    gear_ratio=5.0,  # 5:1 belt reduction
    max_speed_rpm=300.0,
    driver="TMC2209",
)


# ── FluidNC Configuration ──────────────────────────────────────────

def generate_fluidnc_config() -> str:
    """
    FluidNC YAML konfigürasyon dosyası.
    machine_config.yaml olarak ESP32'nin SPIFFS'ine yazılır.
    """
    p = PINMAP
    mx = MOTOR_X; ma = MOTOR_A
    cfg = f"""
# FluidNC machine_config.yaml
# Filament Winding Machine — Faz 15
# Generated by machine_config.py

name: FilamentWinderFAZ15
board: generic_esp32

axes:
  shared_stepper_disable_pin: NO_PIN

  x:
    steps_per_mm: {mx.steps_per_mm:.1f}
    max_rate_mm_per_min: {mx.max_speed_rpm * mx.pitch_mm:.0f}
    acceleration: 500
    max_travel_mm: 400
    soft_limits: true
    homing:
      cycle: 1
      positive_direction: false
      mpos: 0
      feed_mm_per_min: 200
      seek_mm_per_min: 1000
      settle_ms: 250
      seek_scaler: 1.1
      feed_scaler: 1.1
    motor0:
      limit_neg_pin: gpio.{p.LIMIT_X_MIN}:low
      limit_pos_pin: gpio.{p.LIMIT_X_MAX}:low
      stepstick:
        step_pin:    gpio.{p.X_STEP}
        direction_pin: gpio.{p.X_DIR}
        disable_pin: gpio.{p.X_ENABLE}:low
        ms1_pin: NO_PIN
        ms2_pin: NO_PIN

  a:
    steps_per_mm: {ma.steps_per_deg:.4f}  # degrees
    max_rate_mm_per_min: {ma.max_speed_rpm * 360.0 / 60.0 * ma.steps_per_deg:.0f}
    acceleration: 5000
    max_travel_mm: 999999  # unlimited rotation
    soft_limits: false
    homing:
      cycle: 0
    motor0:
      stepstick:
        step_pin:    gpio.{p.A_STEP}
        direction_pin: gpio.{p.A_DIR}
        disable_pin: gpio.{p.A_ENABLE}:low

control:
  safety_door_pin:  gpio.{p.ESTOP_IN}:low:pu  # E-stop (active-low, pull-up)
  feed_hold_pin:    NO_PIN
  cycle_start_pin:  NO_PIN
  reset_pin:        NO_PIN

probe:
  pin: gpio.{p.PROBE_IN}:low:pu

uart1:
  txd_pin: gpio.{p.UART_TX}
  rxd_pin: gpio.{p.UART_RX}
  baud:    921600
  mode:    8N1

spi:
  miso_pin: gpio.19  # Not used for TMC UART
  mosi_pin: gpio.23
  sck_pin:  gpio.18

coolant:
  mist_pin:  NO_PIN
  flood_pin: NO_PIN

user_outputs:
  analog0_pin: gpio.{p.STO_OUTPUT}  # STO relay output

# Spindle (used for tension reference output via DAC)
spindle:
  type: none

# Timing
stepping:
  engine:     RMT    # ESP32 RMT peripheral — jitter <1µs
  idle_ms:    255
  dir_delay_us:  10
  pulse_us:      5
  disable_delay_us: 0
"""
    return cfg.strip()


# ── Electrical Architecture ────────────────────────────────────────

@dataclass(frozen=True)
class ElectricalSpec:
    """Elektrik dolabı ve güç dağıtım spesifikasyonu."""
    # Power supply
    AC_input:       str = "220VAC / 50Hz / 16A"
    EMC_filter:     str = "Schaffner FN2090-16-06 (16A, 220VAC)"
    RCD:            str = "30mA Type-A RCCB"
    MCB_main:       str = "16A C-curve MCB"
    PSU_48V:        str = "Mean Well RSP-320-48 (48V/6.7A/320W)"
    PSU_24V:        str = "Mean Well MDR-60-24 (24V/2.5A, DIN rail)"
    PSU_5V:         str = "Mean Well MDR-20-5 (5V/3A)"

    # Safety relay
    safety_relay:   str = "Pilz PNOZ X3.1 (Cat.3/PLd, dual-channel)"
    safety_category:str = "Category 3, PLd (IEC 62061)"
    safety_reaction:str = "<10ms (relay drop-out)"

    # Motor drivers
    driver_X:       str = "Leadshine DM860H (80V/8A, TMC5160 SPI)"
    driver_A:       str = "Leadshine DM542 (50V/4A, TMC2209 UART)"
    driver_bus:     str = "48VDC bus, star topology from PSU"

    # Encoder
    encoder_type:   str = "US Digital E6-500-N-S (500 CPR, 4x→2000 PPR)"
    encoder_power:  str = "5VDC from encoder breakout (74HC14 Schmitt input)"
    encoder_shield: str = "Shielded cable, shield grounded at controller end only"

    # Grounding
    ground_strategy:str = "Star grounding: PE bus → single point → chassis"
    cable_routing:  str = "Power/signal cables separated by 100mm minimum"
    ferrite_beads:  str = "Fair-Rite 0431167281 on encoder cables"

    # Cabinet
    cabinet:        str = "IP54 steel enclosure 400×600×200mm"
    cooling:        str = "Rittal SK 3105 fan unit (45m³/h)"
    temp_monitor:   str = "Cabinet thermostat: trip at 55°C"

    def report(self) -> str:
        lines = ["  Electrical Architecture:"]
        for k,v in self.__dict__.items():
            lines.append(f"    {k:<20}: {v}")
        return "\n".join(lines)


ELECTRICAL = ElectricalSpec()
