"""
core/payout_dynamics.py — Gerçek Payout Göz Dinamiği
======================================================
Payout gözünün dinamik davranışını modeller: taşıyıcı ataleti, göz ivme
limitleri, gecikme (lag) telafisi ve dinamik temas noktası hareketi.

statik payout_kinematics.py geometriyi verir; bu modül ZAMAN davranışını
ekler — ideal yörüngeyi takip edemeyen gerçek bir tahrik sistemi.

Model
-----
Taşıyıcı, ikinci-derece izleyici + ivme doygunluğu ile modellenir:
    e   = x_desired − x_actual
    a_c = kp·e − kd·v          (kritik sönümlü PD)
    a   = clamp(a_c, ±a_max)   (ivme doygunluğu / atalet etkisi)
    v  += a·dt ;  x += v·dt

İvme limiti F_max/m'den gelir:  a_max = F_max / m.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .geometry_engine import MandrelProfile


@dataclass
class PayoutDynamicsConfig:
    """
    Payout gözü / taşıyıcı dinamik parametreleri.

    carriage_mass_kg   : Taşıyıcı + göz kütlesi (atalet).
    max_drive_force_N  : Tahrik motoru maksimum kuvveti.
    eye_lag_time_const_s : Gecikme zaman sabiti τ (gözün gerçek tepki gecikmesi).
    standoff_mm        : Göz-yüzey radyal mesafesi.
    max_eye_accel_mm_s2 : Açık ivme tavanı (None ise F/m'den hesaplanır).
    """
    carriage_mass_kg: float = 5.0
    max_drive_force_N: float = 2000.0
    eye_lag_time_const_s: float = 0.03
    standoff_mm: float = 150.0
    max_eye_accel_mm_s2: Optional[float] = None

    def __post_init__(self) -> None:
        if self.carriage_mass_kg <= 0:
            raise ValueError("carriage_mass_kg > 0 olmalı")
        if self.eye_lag_time_const_s <= 0:
            raise ValueError("eye_lag_time_const_s > 0 olmalı")

    @property
    def accel_limit_mm_s2(self) -> float:
        """Etkin ivme tavanı (mm/s²). a = F/m, m·s⁻² → mm·s⁻² için ×1000."""
        if self.max_eye_accel_mm_s2 is not None:
            return self.max_eye_accel_mm_s2
        # F[N] / m[kg] = a[m/s²] → mm/s² için ×1000
        return self.max_drive_force_N / self.carriage_mass_kg * 1000.0


@dataclass
class EyeResponseResult:
    """Göz dinamik tepki simülasyon sonucu."""
    t_s: np.ndarray              # Zaman dizisi
    x_desired_mm: np.ndarray     # İstenen taşıyıcı konumu
    x_actual_mm: np.ndarray      # Gerçek taşıyıcı konumu (dinamik)
    v_actual_mm_s: np.ndarray    # Gerçek hız
    a_actual_mm_s2: np.ndarray   # Gerçek ivme
    lag_error_mm: np.ndarray     # x_desired − x_actual

    @property
    def max_lag_error_mm(self) -> float:
        return float(np.max(np.abs(self.lag_error_mm)))

    @property
    def rms_lag_error_mm(self) -> float:
        return float(np.sqrt(np.mean(self.lag_error_mm ** 2)))

    @property
    def max_accel_mm_s2(self) -> float:
        return float(np.max(np.abs(self.a_actual_mm_s2)))

    @property
    def n_accel_saturated(self) -> int:
        """İvme tavanına dayanan adım sayısı."""
        return int(self._sat_count)

    _sat_count: int = 0

    def summary(self) -> str:
        return (
            f"EyeResponse: maks_gecikme={self.max_lag_error_mm:.3f}mm "
            f"rms={self.rms_lag_error_mm:.3f}mm "
            f"maks_ivme={self.max_accel_mm_s2:.0f}mm/s² "
            f"doygunluk={self.n_accel_saturated} adım"
        )


def simulate_eye_response(
    t_s: np.ndarray,
    x_desired_mm: np.ndarray,
    config: PayoutDynamicsConfig,
    natural_freq_hz: float = 8.0,
) -> EyeResponseResult:
    """
    İstenen taşıyıcı yörüngesine göz dinamik tepkisini simüle et.

    Kritik sönümlü ikinci-derece izleyici + ivme doygunluğu kullanır.

    Parametreler
    ----------
    t_s          : (N,) zaman dizisi (saniye, artan).
    x_desired_mm : (N,) istenen taşıyıcı konumu.
    config       : Dinamik yapılandırma.
    natural_freq_hz : İzleyici doğal frekansı (yanıt hızı).
    """
    n = len(t_s)
    x_act = np.zeros(n)
    v_act = np.zeros(n)
    a_act = np.zeros(n)
    lag = np.zeros(n)

    wn = 2.0 * math.pi * natural_freq_hz
    kp = wn ** 2
    kd = 2.0 * wn  # kritik sönüm (ζ = 1)

    a_max = config.accel_limit_mm_s2
    x_act[0] = x_desired_mm[0]
    sat_count = 0

    for i in range(1, n):
        dt = t_s[i] - t_s[i - 1]
        if dt <= 0:
            x_act[i] = x_act[i - 1]
            v_act[i] = v_act[i - 1]
            continue

        e = x_desired_mm[i] - x_act[i - 1]
        a_cmd = kp * e - kd * v_act[i - 1]

        # İvme doygunluğu (atalet / kuvvet limiti)
        if abs(a_cmd) > a_max:
            a_cmd = math.copysign(a_max, a_cmd)
            sat_count += 1

        v_new = v_act[i - 1] + a_cmd * dt
        x_new = x_act[i - 1] + v_new * dt

        a_act[i] = a_cmd
        v_act[i] = v_new
        x_act[i] = x_new
        lag[i] = x_desired_mm[i] - x_new

    result = EyeResponseResult(
        t_s=t_s,
        x_desired_mm=x_desired_mm,
        x_actual_mm=x_act,
        v_actual_mm_s=v_act,
        a_actual_mm_s2=a_act,
        lag_error_mm=lag,
    )
    result._sat_count = sat_count
    return result


def compute_carriage_lead_safe(alpha_deg: float, standoff_mm: float) -> float:
    """
    Statik taşıyıcı öncülük mesafesi: lead = standoff · tan(α).

    payout_kinematics.compute_carriage_lead'in profilden bağımsız, hafif
    sürümü — digital twin döngüsünde her adımda çağrılmak için.
    """
    alpha_rad = math.radians(max(0.1, min(alpha_deg, 89.9)))
    return standoff_mm * math.tan(alpha_rad)


def compute_lag_compensation_mm(
    velocity_mm_s: float,
    config: PayoutDynamicsConfig,
) -> float:
    """
    İleri-besleme gecikme telafisi: gözü hız×τ kadar önde konumla.

    İstenen konum = ideal_konum + v·τ  →  gecikmeyi sıfıra yaklaştırır.
    """
    return velocity_mm_s * config.eye_lag_time_const_s


@dataclass
class ContactPointMotion:
    """Zaman içinde dinamik temas noktası hareketi."""
    t_s: np.ndarray
    contact_z_mm: np.ndarray       # Temas noktası eksenel konumu
    contact_velocity_mm_s: np.ndarray  # Temas noktası hızı
    max_contact_velocity_mm_s: float

    def summary(self) -> str:
        return (
            f"ContactMotion: maks_temas_hızı="
            f"{self.max_contact_velocity_mm_s:.1f}mm/s "
            f"({len(self.t_s)} adım)"
        )


def compute_contact_point_motion(
    t_s: np.ndarray,
    eye_x_mm: np.ndarray,
    profile: MandrelProfile,
    config: PayoutDynamicsConfig,
) -> ContactPointMotion:
    """
    Göz hareketine bağlı dinamik temas noktası hareketini hesapla.

    Basit model: temas noktası, gözün eksenel konumunu standoff geometrisi
    ile takip eder. Düz yüzeyde temas ≈ göz ekseninin altındadır; eğimli
    profilde teğet noktası kayar. Burada birinci derece yaklaşımla temas_z
    göz_z'yi takip eder ve hızı sayısal türevle bulunur.
    """
    n = len(t_s)
    contact_z = np.array(eye_x_mm, dtype=np.float64)
    contact_v = np.zeros(n)

    for i in range(1, n):
        dt = t_s[i] - t_s[i - 1]
        if dt > 0:
            contact_v[i] = (contact_z[i] - contact_z[i - 1]) / dt

    return ContactPointMotion(
        t_s=t_s,
        contact_z_mm=contact_z,
        contact_velocity_mm_s=contact_v,
        max_contact_velocity_mm_s=float(np.max(np.abs(contact_v))) if n else 0.0,
    )
