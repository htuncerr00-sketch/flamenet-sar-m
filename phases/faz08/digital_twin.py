"""
digital_twin.py — Digital Twin + Süreç Geri Besleme Modeli
===========================================================
Planlanan (ideal) ve simüle edilen (gerçek) durum arasındaki
farkı modelleyen ve gelecekte gerçek sensörlerle beslenebilecek
dijital ikiz altyapısı.

Mimari katmanları:
  PlannedState   — G-code'dan gelen ideal program
  SimulatedState — MachineSimulationEngine'dan
  ActualState    — Gerçek sensör verileri (gelecek)
  TwinState      — Fark analizi ve kalite metriği

Sensör Arayüzü (gelecek entegrasyon hazırlığı):
  EncoderInterface   — X/A pozisyon
  LoadCellInterface  — Fiber gerilmesi
  RPMInterface       — Spindle hızı
  VisionInterface    — Fiber yerleşim açısı

Her arayüz:
  - connect()   : Sensörle bağlantı kur
  - read()      : Anlık okuma al
  - calibrate() : Kalibrasyon rutini
  - is_healthy(): Sensor sağlık durumu

Gerçek sensörler yokken: simülasyon verisi kullanılır.
Gerçek sensörler varken: karşılaştırma ve hata düzeltme.
"""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from machine_simulation import SimulationResult, SimState, MachineSimulationEngine, SimulationConfig


# ══════════════════════════════════════════════════════════════
# SENSOR INTERFACES (Future Integration)
# ══════════════════════════════════════════════════════════════

class SensorStatus(Enum):
    OFFLINE    = auto()
    SIMULATED  = auto()
    CONNECTED  = auto()
    ERROR      = auto()


@dataclass(slots=True)
class SensorReading:
    """Tek sensör okuma değeri."""
    timestamp:  float      # Unix timestamp [s]
    value:      float      # Ölçüm değeri (birim sensöre göre)
    unit:       str        # "mm", "N", "rpm", "deg"
    status:     SensorStatus
    quality:    float      # [0,1] — ölçüm kalitesi


class SensorInterface(ABC):
    """Sensör arayüzü soyut sınıfı."""

    def __init__(self, name: str, unit: str) -> None:
        self.name    = name
        self.unit    = unit
        self.status  = SensorStatus.OFFLINE
        self._sim_fn: Optional[Callable[[], float]] = None

    @abstractmethod
    def connect(self, **kwargs) -> bool:
        """Sensöre bağlan. Returns True if successful."""

    @abstractmethod
    def read(self) -> SensorReading:
        """Anlık ölçüm al."""

    @abstractmethod
    def calibrate(self) -> bool:
        """Kalibrasyon rutini."""

    def is_healthy(self) -> bool:
        return self.status in (SensorStatus.SIMULATED, SensorStatus.CONNECTED)

    def attach_simulation(self, fn: Callable[[], float]) -> None:
        """Simülasyon veri kaynağı bağla."""
        self._sim_fn  = fn
        self.status   = SensorStatus.SIMULATED

    def _sim_reading(self) -> SensorReading:
        val = self._sim_fn() if self._sim_fn else 0.0
        return SensorReading(
            timestamp = time.time(),
            value     = val,
            unit      = self.unit,
            status    = SensorStatus.SIMULATED,
            quality   = 1.0,
        )


class EncoderInterface(SensorInterface):
    """
    Lineer/rotary encoder arayüzü.

    X encoder: carriage pozisyonu [mm]
    A encoder: spindle açısı [°] kümülatif
    """
    def __init__(self, axis: str) -> None:
        super().__init__(f"{axis}_Encoder", "mm" if axis=="X" else "deg")
        self.axis = axis

    def connect(self, port: str = "COM1", **kwargs) -> bool:
        """Encoder controller'a bağlan (gelecek: serial/EtherCAT)."""
        # Gelecekte: self._serial = serial.Serial(port, 115200)
        print(f"    [SIM] {self.name}: Simülasyon modunda")
        self.status = SensorStatus.SIMULATED
        return True

    def read(self) -> SensorReading:
        return self._sim_reading()

    def calibrate(self) -> bool:
        print(f"    [SIM] {self.name}: Sıfır kalibrasyonu yapıldı")
        return True


class LoadCellInterface(SensorInterface):
    """
    Yük hücresi — fiber gerilme ölçümü [N].

    Payout gözündeki gerilmeyi ölçer.
    Tipik: HX711 ADC + 10kg load cell.
    """
    def __init__(self) -> None:
        super().__init__("LoadCell_Tension", "N")
        self._tare = 0.0

    def connect(self, **kwargs) -> bool:
        print("    [SIM] LoadCell: Simülasyon modunda")
        self.status = SensorStatus.SIMULATED
        return True

    def read(self) -> SensorReading:
        return self._sim_reading()

    def calibrate(self, known_mass_kg: float = 1.5) -> bool:
        """Bilinen kütleyle kalibrasyon."""
        print(f"    [SIM] LoadCell: {known_mass_kg}kg referansla kalibre edildi")
        return True

    def tare(self) -> None:
        """Boş gerilmeyi sıfırla."""
        self._tare = self.read().value


class RPMInterface(SensorInterface):
    """
    Spindle RPM sensörü (hall-effect veya encoder).
    """
    def __init__(self) -> None:
        super().__init__("Spindle_RPM", "rpm")

    def connect(self, **kwargs) -> bool:
        self.status = SensorStatus.SIMULATED
        return True

    def read(self) -> SensorReading:
        return self._sim_reading()

    def calibrate(self) -> bool:
        return True


class VisionInterface(SensorInterface):
    """
    Fiber yerleşim açısı — optik ölçüm (gelecek: OpenCV + kamera).

    Mandrel yüzeyi üzerindeki fiber açısını görüntü işleme ile ölçer.
    Şu an: simülasyon verisi.
    """
    def __init__(self) -> None:
        super().__init__("Vision_FiberAngle", "deg")

    def connect(self, camera_index: int = 0, **kwargs) -> bool:
        # Gelecekte: cv2.VideoCapture(camera_index)
        self.status = SensorStatus.SIMULATED
        return True

    def read(self) -> SensorReading:
        return self._sim_reading()

    def calibrate(self) -> bool:
        return True


# ══════════════════════════════════════════════════════════════
# TWIN STATE
# ══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class TwinDelta:
    """
    Planlanan vs gerçek (simüle) fark analizi.
    Tek bir zaman noktasının veya tüm pass'ın özeti.
    """
    segment_idx:      int
    dx_mm:            float   # X pozisyon hatası
    da_deg:           float   # A açı hatası (spindle lag + drift)
    dalpha_deg:       float   # Sarım açısı hatası
    dt_N:             float   # Gerilme farkı (actual - nominal)
    is_turnaround:    bool
    quality_local:    float   # [0,1] — bu segment için kalite

    @property
    def position_error_mm(self) -> float:
        return math.sqrt(self.dx_mm**2)

    @property
    def is_acceptable(self) -> bool:
        """Tolerans içinde mi?"""
        return (abs(self.dx_mm) < 0.20 and
                abs(self.da_deg) < 1.0 and
                abs(self.dalpha_deg) < 0.5)


@dataclass(slots=True)
class DigitalTwinSnapshot:
    """Belirli bir anda dijital ikiz durumu."""
    timestamp:     float
    pass_index:    int
    segment_index: int
    # Sensör okumaları
    encoder_x:     float   # mm
    encoder_a:     float   # deg
    tension_N:     float
    rpm:           float
    alpha_vision:  float   # deg (varsa)
    # Planlanan
    x_planned:     float
    a_planned:     float
    # Fark
    delta:         TwinDelta


@dataclass(slots=True)
class DigitalTwinState:
    """Tüm programın dijital ikiz durumu."""
    snapshots:       List[DigitalTwinSnapshot]
    sim_result:      SimulationResult
    mean_x_error:    float
    mean_a_error:    float
    max_tension_N:   float
    quality_score:   float
    pass_quality:    List[float]    # Her pass için kalite
    alerts:          List[str]      # Uyarı mesajları

    def full_report(self) -> str:
        lines = [
            "  ╔" + "═"*60 + "╗",
            "  ║  DIGITAL TWIN RAPORU" + " "*38 + "║",
            "  ╠" + "═"*60 + "╣",
            f"  ║  Snapshot sayısı:   {len(self.snapshots):<38d}║",
            f"  ║  Ortalama X hatası: {self.mean_x_error*1000:.2f} µm{' '*33}║",
            f"  ║  Ortalama A hatası: {self.mean_a_error:.4f}°{' '*34}║",
            f"  ║  Max gerilme:       {self.max_tension_N:.2f} N{' '*36}║",
            f"  ║  Kalite skoru:      {self.quality_score:.4f}{' '*37}║",
            "  ╠" + "═"*60 + "╣",
        ]
        if self.alerts:
            lines.append(f"  ║  UYARILAR ({len(self.alerts)}):{' '*47}║")
            for alert in self.alerts[:5]:
                lines.append(f"  ║    • {alert[:55]:<55}║")
        lines.append("  ╚" + "═"*60 + "╝")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# DIGITAL TWIN MODEL
# ══════════════════════════════════════════════════════════════

class DigitalTwinModel:
    """
    Dijital ikiz modeli — planlanan vs simüle edilen karşılaştırma.

    Kullanım:
        twin = DigitalTwinModel(sim_engine, sensors)
        twin.initialize(sequence)
        state = twin.run(x_arr, a_arr, f_arr, alpha_rad)
        print(state.full_report())
    """

    # Uyarı eşikleri
    X_ERROR_WARN_MM  = 0.15   # mm — X hatası uyarısı
    A_ERROR_WARN_DEG = 0.8    # ° — A hatası uyarısı
    TENSION_HIGH_N   = 30.0   # N — yüksek gerilme
    TENSION_LOW_N    = 3.0    # N — düşük gerilme (fibber gevşedi)
    ALPHA_ERROR_DEG  = 0.5    # ° — sarım açısı toleransı

    def __init__(
        self,
        sim_engine: MachineSimulationEngine,
        sensors:    Optional[Dict[str, SensorInterface]] = None,
    ) -> None:
        self.engine  = sim_engine
        self.sensors = sensors or {}

    def attach_sensor(self, name: str, sensor: SensorInterface) -> None:
        self.sensors[name] = sensor

    def run(
        self,
        x_arr:      np.ndarray,
        a_arr:      np.ndarray,
        f_arr:      np.ndarray,
        alpha_rad:  float,
        pass_index: int = 0,
    ) -> DigitalTwinState:
        """
        Tüm hareket profilini dijital ikiz ile çalıştır.

        Adımlar:
          1. MachineSimulationEngine → SimulationResult
          2. Her segment → TwinDelta (planned vs actual)
          3. Sensör okumaları (gerçek veya simüle)
          4. Uyarı üretimi
          5. Kalite skoru
        """
        # ── Simülasyon ──────────────────────────────────────────
        sim_result = self.engine.simulate(x_arr, a_arr, f_arr, alpha_rad)

        # ── Snapshot listesi ─────────────────────────────────────
        snapshots: List[DigitalTwinSnapshot] = []
        nominal_T = self.engine.cfg.T_nominal_N

        for i, st in enumerate(sim_result.states):
            delta = TwinDelta(
                segment_idx   = i,
                dx_mm         = st.x_error,
                da_deg        = st.a_error_deg,
                dalpha_deg    = st.alpha_error_deg,
                dt_N          = st.tension_N - nominal_T,
                is_turnaround = st.is_turnaround,
                quality_local = self._local_quality(st),
            )

            # Sensör okumaları (sim veya gerçek)
            enc_x = self._read_sensor("encoder_x", st.x_actual)
            enc_a = self._read_sensor("encoder_a", st.a_actual_deg)
            tens  = self._read_sensor("tension",   st.tension_N)
            rpm   = self._read_sensor("rpm",       abs(st.omega_actual) / 6.0)
            alpha_vis = self._read_sensor("vision", st.alpha_actual_deg)

            snap = DigitalTwinSnapshot(
                timestamp     = float(i) * 0.01,
                pass_index    = pass_index,
                segment_index = i,
                encoder_x     = enc_x,
                encoder_a     = enc_a,
                tension_N     = tens,
                rpm           = rpm,
                alpha_vision  = alpha_vis,
                x_planned     = st.x_planned,
                a_planned     = st.a_planned_deg,
                delta         = delta,
            )
            snapshots.append(snap)

        # ── Aggregate ────────────────────────────────────────────
        x_errors = np.array([s.delta.dx_mm for s in snapshots])
        a_errors = np.array([s.delta.da_deg for s in snapshots])
        tensions = np.array([s.tension_N for s in snapshots])

        # ── Alerts ───────────────────────────────────────────────
        alerts = []
        rms_x = float(np.sqrt(np.mean(x_errors**2)))
        rms_a = float(np.sqrt(np.mean(a_errors**2)))
        if rms_x > self.X_ERROR_WARN_MM:
            alerts.append(f"X RMS hatası {rms_x*1000:.1f}µm > {self.X_ERROR_WARN_MM*1000:.0f}µm")
        if rms_a > self.A_ERROR_WARN_DEG:
            alerts.append(f"A RMS hatası {rms_a:.3f}° > {self.A_ERROR_WARN_DEG:.1f}°")
        if sim_result.max_tension_N > self.TENSION_HIGH_N:
            alerts.append(f"Max gerilme {sim_result.max_tension_N:.1f}N > {self.TENSION_HIGH_N}N")
        if sim_result.min_tension_N < self.TENSION_LOW_N:
            alerts.append(f"Min gerilme {sim_result.min_tension_N:.1f}N < {self.TENSION_LOW_N}N (fiber gevşedi?)")
        if sim_result.n_missed_steps_x > 0:
            alerts.append(f"X ekseni {sim_result.n_missed_steps_x} adım kaybetti")
        if abs(sim_result.feed_drift_final_deg) > 2.0:
            alerts.append(f"Feed drift {sim_result.feed_drift_final_deg:.3f}° > 2° — rewind öner")

        return DigitalTwinState(
            snapshots      = snapshots,
            sim_result     = sim_result,
            mean_x_error   = rms_x,
            mean_a_error   = rms_a,
            max_tension_N  = float(tensions.max()),
            quality_score  = sim_result.quality_score(),
            pass_quality   = [s.delta.quality_local for s in snapshots],
            alerts         = alerts,
        )

    def _local_quality(self, st: SimState) -> float:
        """Segment başına kalite [0,1]."""
        q_x = max(0.0, 1.0 - abs(st.x_error) / 0.20)
        q_a = max(0.0, 1.0 - abs(st.a_error_deg) / 1.0)
        q_T = max(0.0, 1.0 - abs(st.tension_N - self.engine.cfg.T_nominal_N) / 20.0)
        return 0.40*q_x + 0.35*q_a + 0.25*q_T

    def _read_sensor(self, name: str, sim_value: float) -> float:
        """Sensörden oku veya simülasyon değeri kullan."""
        if name in self.sensors and self.sensors[name].is_healthy():
            reading = self.sensors[name].read()
            return reading.value
        return sim_value


# ══════════════════════════════════════════════════════════════
# PROCESS FEEDBACK MODEL
# ══════════════════════════════════════════════════════════════

class ProcessFeedbackModel:
    """
    Gelecek sensör entegrasyonu için süreç geri besleme modeli.

    Şu an: simülasyon verisiyle çalışır.
    Gelecekte: encoder, load-cell, RPM, vision entegrasyonu.

    Adaptive control (gelecek):
      Feedforward: G-code'dan temel hareket
      Feedback: Encoder hatası → hız düzeltmesi
      PID: Tension hatası → payout motor düzeltmesi
    """

    def __init__(self) -> None:
        self.sensors: Dict[str, SensorInterface] = {}
        self._feedback_enabled = False
        self._correction_log:  List[dict] = []

    def register_sensors(self) -> Dict[str, SensorInterface]:
        """Sensörleri oluştur ve kaydet (simülasyon modunda)."""
        enc_x = EncoderInterface("X")
        enc_a = EncoderInterface("A")
        lc    = LoadCellInterface()
        rpm   = RPMInterface()
        vis   = VisionInterface()

        for s in [enc_x, enc_a, lc, rpm, vis]:
            s.connect()
        self.sensors = {
            "encoder_x": enc_x,
            "encoder_a": enc_a,
            "tension":   lc,
            "rpm":       rpm,
            "vision":    vis,
        }
        return self.sensors

    def attach_simulated_data(
        self,
        sim_states: List[SimState],
    ) -> None:
        """Simülasyon verilerini sensörlere bağla."""
        idx = [0]

        def next_val(attr):
            def fn():
                i = min(idx[0], len(sim_states)-1)
                v = getattr(sim_states[i], attr, 0.0)
                idx[0] = min(idx[0]+1, len(sim_states)-1)
                return v
            return fn

        if "encoder_x" in self.sensors:
            self.sensors["encoder_x"].attach_simulation(next_val("x_actual"))
        if "encoder_a" in self.sensors:
            self.sensors["encoder_a"].attach_simulation(next_val("a_actual_deg"))
        if "tension" in self.sensors:
            self.sensors["tension"].attach_simulation(next_val("tension_N"))
        if "rpm" in self.sensors:
            self.sensors["rpm"].attach_simulation(
                lambda: abs(next_val("omega_actual")()) / 6.0)

    def sensor_health_report(self) -> str:
        lines = ["  Sensör Durum Raporu:"]
        for name, sensor in self.sensors.items():
            icon = "✓" if sensor.is_healthy() else "✗"
            lines.append(
                f"    {icon} {sensor.name:<20} "
                f"[{sensor.status.name:<10}]  "
                f"unit={sensor.unit}")
        return "\n".join(lines)

    def enable_feedback(self, enabled: bool = True) -> None:
        self._feedback_enabled = enabled
        print(f"    Feedback control: {'AKTİF' if enabled else 'PASİF'}")

    def compute_correction(
        self,
        delta_x: float,
        delta_a: float,
        delta_T: float,
    ) -> dict:
        """
        Geri besleme düzeltmesi hesapla.

        Gelecek PID kontrolü için iskelet:
          Kp_x, Ki_x, Kd_x: X eksen PID
          Kp_a, Ki_a, Kd_a: A eksen PID
          Kp_T: Tension proportional
        """
        if not self._feedback_enabled:
            return {"dvx": 0.0, "domega": 0.0, "dT": 0.0}

        Kp_x = 2.0; Kp_a = 0.5; Kp_T = 0.3
        correction = {
            "dvx":    -Kp_x * delta_x,
            "domega": -Kp_a * delta_a,
            "dT":     -Kp_T * delta_T,
        }
        self._correction_log.append(correction)
        return correction
