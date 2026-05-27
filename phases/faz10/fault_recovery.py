"""
fault_recovery.py — Hata Kurtarma Yöneticisi
==============================================
Makine çalışması sırasında oluşan hataları tespit edip kurtarma
prosedürlerini devreye sokar.

Hata türleri ve kurtarma stratejileri:

  MissedStep (adım kayması):
    Tespit: |x_encoder - x_commanded| > threshold (sürekli artan)
    Kurtarma:
      1. Feed hold (hızı azalt)
      2. Position re-sync: x_estimated = x_encoder (Kalman reset)
      3. Backlash güncelleme
      4. Hızı %75'e düşür

  TensionFault (gerilme hatası):
    HIGH: T > T_max → Pause → Payout motor yavaşlat → Resume
    LOW:  T < T_min → Pause → Operatör kontrol (fiber kopmuş olabilir)
    SPIKE: |dT/dt| > threshold → Geçici feed override düşürme

  OscillationDamping (rezonans sönümleme):
    Tespit: FFT peak > threshold (ResonanceDetector'dan)
    Kurtarma: Feed override → resonant frequency'de hız yasak
    Implementation: Notch filter veya speed exclusion zone

  DrfitAccumulation (kümülatif kayma):
    Tespit: |phi_drift| > threshold (k devre sonrası)
    Kurtarma: Rewind → position re-sync → devam
    Bu sadece fiber cut noktasında uygulanır (güvenlik)

  CommTimeout (iletişim kesintisi):
    Tespit: Son controller response > timeout_ms
    Kurtarma: E-stop → reconnect → position query → resume

Durum makinesi:
  NORMAL → FAULT_DETECTED → RECOVERY_IN_PROGRESS → NORMAL
  NORMAL → FAULT_DETECTED → RECOVERY_FAILED → EMERGENCY_STOP

Kurtarma kararları her zaman güvenlik sınırları içinde kalır.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np


# ── Fault Types ──────────────────────────────────────────────────

class FaultType(Enum):
    MISSED_STEP     = "missed_step"
    TENSION_HIGH    = "tension_high"
    TENSION_LOW     = "tension_low"
    TENSION_SPIKE   = "tension_spike"
    OSCILLATION     = "oscillation"
    DRIFT_ACCUM     = "drift_accumulation"
    COMM_TIMEOUT    = "comm_timeout"
    ENCODER_ANOMALY = "encoder_anomaly"
    NONE            = "none"


class RecoveryStatus(Enum):
    NORMAL              = "normal"
    FAULT_DETECTED      = "fault_detected"
    RECOVERY_IN_PROGRESS= "recovery_in_progress"
    RECOVERY_SUCCESS    = "recovery_success"
    RECOVERY_FAILED     = "recovery_failed"
    EMERGENCY_STOP      = "emergency_stop"


# ── Fault Events ─────────────────────────────────────────────────

@dataclass(slots=True)
class FaultEvent:
    """Tek hata olayı."""
    timestamp:       float
    fault_type:      FaultType
    severity:        float      # [0,1]
    measured_value:  float
    threshold:       float
    description:     str
    action_taken:    str
    recovery_status: RecoveryStatus
    segment_index:   int

    def __str__(self) -> str:
        t = time.strftime("%H:%M:%S.%f", time.localtime(self.timestamp))[:12]
        return (
            f"[{t}] {self.fault_type.value:<18} "
            f"sev={self.severity:.2f}  val={self.measured_value:.4f}  "
            f"thr={self.threshold:.4f}  → {self.action_taken}"
        )


@dataclass(slots=True)
class RecoveryAction:
    """Kurtarma eylemi."""
    feed_override_factor: float   # [0.05, 1.0]
    pause_duration_s:     float   # 0 = anında devam
    reset_kalman:         bool    # Kalman estimatör sıfırlama
    resync_position:      bool    # Position re-sync
    reduce_speed_percent: float   # Kalıcı hız düşürme [0,100]
    operator_confirm:     bool    # Operatör onayı gerekli
    message:              str


# ── Individual Fault Detectors ───────────────────────────────────

class MissedStepDetector:
    """
    Kümülatif adım kayması tespiti.

    x_encoder - x_commanded farkı sürekli artıyorsa → missed steps.
    Tek seferlik büyük fark → encoder jump (farklı problem).
    """
    def __init__(
        self,
        threshold_mm:   float = 0.5,      # Tek ölçüm eşiği
        cumulative_mm:  float = 2.0,      # Kümülatif eşik
        window:         int   = 20,
    ) -> None:
        self.thr     = threshold_mm
        self.cum_thr = cumulative_mm
        self._errors: List[float] = []
        self._window = window

    def update(self, x_cmd: float, x_enc: float) -> Optional[FaultEvent]:
        err = abs(x_enc - x_cmd)
        self._errors.append(err)
        if len(self._errors) > self._window:
            self._errors.pop(0)

        cumulative = sum(self._errors)

        if err > self.thr or cumulative > self.cum_thr:
            sev = min(1.0, max(err/self.thr, cumulative/self.cum_thr) / 3.0)
            return FaultEvent(
                timestamp       = time.time(),
                fault_type      = FaultType.MISSED_STEP,
                severity        = sev,
                measured_value  = err,
                threshold       = self.thr,
                description     = f"X hata={err:.4f}mm, kümülatif={cumulative:.3f}mm",
                action_taken    = "feed_reduce" if sev < 0.5 else "resync+feed_reduce",
                recovery_status = RecoveryStatus.FAULT_DETECTED,
                segment_index   = 0,
            )
        return None


class TensionFaultDetector:
    """
    Fiber gerilme hatası tespiti.

    HIGH:  T > T_max → fiber kopma riski
    LOW:   T < T_min → fiber gevşedi (kopmuş olabilir)
    SPIKE: |ΔT/Δt| > spike_rate → anlık sıçrama
    """
    def __init__(
        self,
        T_nominal:  float = 15.0,
        T_max:      float = 40.0,
        T_min:      float = 3.0,
        spike_rate: float = 50.0,   # N/s
        dt:         float = 0.010,
    ) -> None:
        self.T_nom      = T_nominal
        self.T_max      = T_max
        self.T_min      = T_min
        self.spike_rate = spike_rate
        self.dt         = dt
        self._T_prev    = T_nominal

    def update(self, T: float) -> Optional[FaultEvent]:
        dT_dt = (T - self._T_prev) / max(self.dt, 1e-6)
        self._T_prev = T

        if T > self.T_max:
            return FaultEvent(
                timestamp=time.time(), fault_type=FaultType.TENSION_HIGH,
                severity=min(1.0,(T-self.T_max)/(self.T_max*0.2)),
                measured_value=T, threshold=self.T_max,
                description=f"Gerilme {T:.2f}N > {self.T_max}N",
                action_taken="pause+payout_slow",
                recovery_status=RecoveryStatus.FAULT_DETECTED, segment_index=0)

        if T < self.T_min and T > 0.1:
            return FaultEvent(
                timestamp=time.time(), fault_type=FaultType.TENSION_LOW,
                severity=1.0,
                measured_value=T, threshold=self.T_min,
                description=f"Gerilme {T:.2f}N < {self.T_min}N (fiber koptu?)",
                action_taken="estop_operator_check",
                recovery_status=RecoveryStatus.FAULT_DETECTED, segment_index=0)

        if abs(dT_dt) > self.spike_rate:
            return FaultEvent(
                timestamp=time.time(), fault_type=FaultType.TENSION_SPIKE,
                severity=min(1.0, abs(dT_dt)/self.spike_rate/3),
                measured_value=abs(dT_dt), threshold=self.spike_rate,
                description=f"Gerilme spike: {dT_dt:+.1f}N/s",
                action_taken="feed_override_reduce",
                recovery_status=RecoveryStatus.FAULT_DETECTED, segment_index=0)
        return None


class OscillationDamper:
    """
    Rezonans tespiti ve aktif sönümleme.

    Sönümleme stratejisi:
      f_res tespit edildi → speed exclusion zone:
      v_min_safe < v_res < v_max_safe
      v_res = f_res / κ_typ  (κ: tipik eğrilik)

    Notch benzeri override: rezonans frekansına yakın hızda çalışmayı engelle.
    """
    def __init__(
        self,
        f_res:            float = 0.0,   # Hz (0 = tespit edilmedi)
        amp_res:          float = 0.0,   # N
        damping_threshold:float = 0.3,   # N — bu üstünde damp uygula
    ) -> None:
        self.f_res  = f_res
        self.amp    = amp_res
        self.thr    = damping_threshold
        self._active = f_res > 0.5 and amp_res > damping_threshold

    def get_safe_feed_override(
        self,
        v_current: float,
        kappa_n:   float = 0.02,
    ) -> Tuple[float, str]:
        """
        Rezonans kaçınma için güvenli feed override.

        Returns: (override, reason)
        """
        if not self._active or self.f_res < 0.1:
            return 1.0, "no_resonance"

        # Rezonans hız bölgesi
        if kappa_n > 1e-9:
            v_res = math.sqrt(self.f_res * 2 * math.pi / kappa_n)
        else:
            v_res = 50.0

        if abs(v_current - v_res) < v_res * 0.15:
            # Rezonans bölgesinde → kaç!
            if v_current < v_res:
                return min(1.0, 0.8 * v_res / max(v_current, 1.0)), "avoid_resonance_down"
            else:
                return min(1.0, 1.2 * v_res / max(v_current, 1.0)), "avoid_resonance_up"
        return 1.0, "outside_resonance_zone"

    def update_resonance(self, f_res: float, amp: float) -> None:
        self.f_res   = f_res
        self.amp     = amp
        self._active = f_res > 0.5 and amp > self.thr


# ── Fault Recovery Manager ────────────────────────────────────────

class FaultRecoveryManager:
    """
    Merkezi hata kurtarma yöneticisi.

    Tüm hata detektörlerini koordine eder.
    Kurtarma eylemlerini oluşturur.
    Durum makinesi: NORMAL → FAULT → RECOVERY → NORMAL/ESTOP.

    Kullanım:
        frm = FaultRecoveryManager(T_nom=15.0, T_max=40.0)
        frm.update_resonance(f_res=2.5, amp=0.8)
        action = frm.process(x_cmd, x_enc, T, segment_idx)
        if action:
            apply_recovery(action)
    """

    MAX_RECOVERY_ATTEMPTS = 3

    def __init__(
        self,
        T_nominal:      float = 15.0,
        T_max:          float = 40.0,
        T_min:          float = 3.0,
        missed_step_thr:float = 0.5,
        dt:             float = 0.010,
        on_fault:       Optional[Callable[[FaultEvent], None]] = None,
    ) -> None:
        self._missed_det  = MissedStepDetector(missed_step_thr)
        self._tension_det = TensionFaultDetector(T_nominal, T_max, T_min, dt=dt)
        self._osc_damp    = OscillationDamper()
        self._on_fault    = on_fault or (lambda e: None)

        self._status      = RecoveryStatus.NORMAL
        self._events:     List[FaultEvent] = []
        self._recovery_n  = 0    # Arka arkaya kurtarma girişimi
        self._feed_mult   = 1.0  # Kalıcı hız çarpanı (missed steps sonrası)

    # ── Public API ───────────────────────────────────────────────

    def process(
        self,
        x_cmd:       float,
        x_enc:       float,
        tension_N:   float,
        segment_idx: int = 0,
        v_current:   float = 100.0,
        kappa_n:     float = 0.02,
    ) -> Optional[RecoveryAction]:
        """
        Tek adım: tüm detektörleri çalıştır ve kurtarma eylemi üret.

        Returns:
            RecoveryAction veya None (normal)
        """
        fault: Optional[FaultEvent] = None

        # Detektörler sırayla (öncelik sırası)
        fault = fault or self._missed_det.update(x_cmd, x_enc)
        fault = fault or self._tension_det.update(tension_N)

        if fault is None:
            if self._status == RecoveryStatus.RECOVERY_SUCCESS:
                self._status = RecoveryStatus.NORMAL
                self._recovery_n = 0
            return None

        # Hata kaydı
        fault.segment_index = segment_idx
        self._events.append(fault)
        self._on_fault(fault)

        # Kurtarma limitine ulaşıldı mı?
        if self._recovery_n >= self.MAX_RECOVERY_ATTEMPTS:
            self._status = RecoveryStatus.EMERGENCY_STOP
            return RecoveryAction(
                feed_override_factor = 0.0,
                pause_duration_s     = 0.0,
                reset_kalman         = True,
                resync_position      = True,
                reduce_speed_percent = 100.0,
                operator_confirm     = True,
                message              = f"MAX KURTARMA ({self._recovery_n}) → E-STOP",
            )

        self._status = RecoveryStatus.RECOVERY_IN_PROGRESS
        self._recovery_n += 1
        action = self._build_recovery(fault, v_current, kappa_n)
        return action

    def update_resonance(self, f_res: float, amp: float) -> None:
        """Rezonans bilgisini güncelle."""
        self._osc_damp.update_resonance(f_res, amp)

    def get_oscillation_override(self, v: float, kappa_n: float) -> Tuple[float, str]:
        """Rezonans kaçınma override faktörü."""
        return self._osc_damp.get_safe_feed_override(v, kappa_n)

    @property
    def status(self) -> RecoveryStatus:
        return self._status

    @property
    def is_emergency(self) -> bool:
        return self._status == RecoveryStatus.EMERGENCY_STOP

    @property
    def feed_multiplier(self) -> float:
        return self._feed_mult

    @property
    def events(self) -> List[FaultEvent]:
        return list(self._events)

    def event_report(self) -> str:
        if not self._events:
            return "  Hata geçmişi: temiz"
        lines = [f"  Hata geçmişi ({len(self._events)} olay):"]
        for e in self._events[-10:]:
            lines.append(f"    {e}")
        return "\n".join(lines)

    def mark_recovery_success(self) -> None:
        """Kurtarma başarılı olduğunu işaretle."""
        self._status    = RecoveryStatus.RECOVERY_SUCCESS
        self._recovery_n = 0

    # ── Recovery action builder ──────────────────────────────────

    def _build_recovery(
        self,
        fault:     FaultEvent,
        v_current: float,
        kappa_n:   float,
    ) -> RecoveryAction:
        """Hata türüne göre kurtarma eylemi."""
        ft = fault.fault_type
        sev= fault.severity

        if ft == FaultType.MISSED_STEP:
            if sev < 0.3:
                self._feed_mult = max(0.7, self._feed_mult * 0.9)
                return RecoveryAction(
                    feed_override_factor = self._feed_mult,
                    pause_duration_s     = 0.0,
                    reset_kalman         = False,
                    resync_position      = False,
                    reduce_speed_percent = 10.0,
                    operator_confirm     = False,
                    message              = f"Missed step (minor): feed→{self._feed_mult:.2f}",
                )
            else:
                self._feed_mult = max(0.5, self._feed_mult * 0.75)
                return RecoveryAction(
                    feed_override_factor = self._feed_mult,
                    pause_duration_s     = 0.5,
                    reset_kalman         = True,
                    resync_position      = True,
                    reduce_speed_percent = 25.0,
                    operator_confirm     = False,
                    message              = f"Missed step (major): resync+feed→{self._feed_mult:.2f}",
                )

        elif ft == FaultType.TENSION_HIGH:
            return RecoveryAction(
                feed_override_factor = 0.6,
                pause_duration_s     = 1.0,
                reset_kalman         = False,
                resync_position      = False,
                reduce_speed_percent = 20.0,
                operator_confirm     = False,
                message              = "Yüksek gerilme: yavaşla+pause",
            )

        elif ft == FaultType.TENSION_LOW:
            return RecoveryAction(
                feed_override_factor = 0.0,
                pause_duration_s     = 0.0,
                reset_kalman         = False,
                resync_position      = False,
                reduce_speed_percent = 100.0,
                operator_confirm     = True,
                message              = "Düşük gerilme: operatör kontrol gerekli",
            )

        elif ft == FaultType.TENSION_SPIKE:
            return RecoveryAction(
                feed_override_factor = max(0.4, 1.0 - sev * 0.5),
                pause_duration_s     = 0.2,
                reset_kalman         = False,
                resync_position      = False,
                reduce_speed_percent = 0.0,
                operator_confirm     = False,
                message              = f"Tension spike: kısa pause+yavaşlama",
            )

        # Genel kurtarma
        return RecoveryAction(
            feed_override_factor = max(0.3, 1.0 - sev * 0.7),
            pause_duration_s     = 0.5,
            reset_kalman         = sev > 0.7,
            resync_position      = sev > 0.7,
            reduce_speed_percent = sev * 30.0,
            operator_confirm     = sev > 0.9,
            message              = f"Genel kurtarma [{ft.value}]",
        )
