"""
reliability_engine.py — Endüstriyel Güvenilirlik Motoru
=========================================================
Uzun süreli ve tekrarlanabilir üretim için fizik tabanlı modeller.

Modeller:
  1. Weibull güvenilirlik (MTBF)
  2. Termal sürüklenme (termal genleşme + motor ısınma)
  3. Mikrostep doğrusalsızlığı
  4. Bobin eylemsizlik değişimi
  5. Gerilme yorgunluğu (fiber tension fatigue)
  6. Uzun süreli kümülatif hata modeli

Weibull güvenilirlik modeli:
  F(t) = 1 - exp(-(t/η)^β)     [arıza olasılığı]
  R(t) = exp(-(t/η)^β)          [güvenilirlik]
  h(t) = (β/η)·(t/η)^(β-1)    [arıza hızı]

  β < 1: infant mortality (ilk çalışma döneminde arıza riski)
  β = 1: sabit arıza hızı (üstel dağılım)
  β > 1: wear-out (ömür sonuna yaklaşma)

  Filament winding makinesi: β ≈ 1.5 (mixed failure modes)
  η ≈ 8760h (1 yıl MTBF hedefi)

Termal genleşme:
  ΔL = α × L × ΔT
  α_steel = 11.7e-6 /°C
  Örnek: L=300mm, ΔT=20°C → ΔL = 0.070mm (önemli!)
  Kompensasyon: Closed-loop encoder feedback eliminer çoğunu.

Motor ısınma (termal modeli):
  P_heat = I²R (Joule ısıtma)
  ΔT(t)  = R_thermal × P × (1 - e^(-t/τ_thermal))
  τ_thermal ≈ 300s (5 dakika termal sabit)
  Etki: torque ≈ -0.2%/°C → 20°C ısınma → -4% tork

Mikrostep doğrusalsızlığı:
  Adım motorlarda sinüsoidal akım dalgalanması:
  x_actual(n) = x_ideal(n) + A_nl × sin(2π×n/N_microstep)
  A_nl ≈ 0.05 × step_size (tipik %5 doğrusalsızlık)
  İki tam adım periyodunda sıfırlanır.

Torsiyonel uyum:
  φ_actual = φ_cmd - T_load/K_torsion
  K_torsion ≈ 10 N·m/rad (alüminyum mill spindle)
  Tipik hata: T=2N·m → φ_err = 0.2 rad = 11.5° (!!)

Referans:
  Weibull (1951); mil-HDBK-217F (reliability prediction);
  ADA268923 App.K (motor thermal); Prechtl (2004) thermal compensation
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


# ── Weibull Reliability ────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class WeibullParams:
    """Weibull dağılım parametreleri."""
    beta:  float   # Şekil parametresi
    eta_h: float   # Ölçek parametresi [saat]

    def reliability(self, t_h: float) -> float:
        """R(t) = exp(-(t/η)^β)"""
        return math.exp(-(t_h / self.eta_h) ** self.beta)

    def failure_rate(self, t_h: float) -> float:
        """h(t) = (β/η)·(t/η)^(β-1)  [1/h]"""
        if t_h <= 0:
            return 0.0
        return (self.beta / self.eta_h) * (t_h / self.eta_h) ** (self.beta - 1)

    def mean_life_h(self) -> float:
        """MTTF = η·Γ(1 + 1/β)"""
        import math
        return self.eta_h * math.gamma(1.0 + 1.0 / self.beta)

    def bx_life(self, x_percent: float) -> float:
        """B_x yaşam süresi: %x arıza olasılığı için zaman."""
        return self.eta_h * (-math.log(1.0 - x_percent / 100.0)) ** (1.0 / self.beta)


class MTBFEstimator:
    """
    Makine MTBF tahmini — Weibull analizi.

    Bileşenler:
      - Lead screw bearing:  β=2.0, η=5000h (wear-out)
      - Stepper motor:       β=1.2, η=20000h
      - Driver electronics:  β=0.8, η=15000h (infant mortality)
      - Belt/coupling:       β=1.5, η=8000h
      - Frame/structure:     β=3.0, η=50000h
    """

    COMPONENTS = {
        "lead_screw_bearing": WeibullParams(beta=2.0, eta_h=5000),
        "stepper_motor_X":    WeibullParams(beta=1.2, eta_h=20000),
        "stepper_motor_A":    WeibullParams(beta=1.2, eta_h=20000),
        "driver_electronics": WeibullParams(beta=0.8, eta_h=15000),
        "belt_coupling":      WeibullParams(beta=1.5, eta_h=8000),
        "frame_structure":    WeibullParams(beta=3.0, eta_h=50000),
        "fiber_guide":        WeibullParams(beta=2.0, eta_h=3000),
    }

    def system_reliability(self, t_h: float) -> float:
        """
        Seri sistem güvenilirliği: R_sys = Π R_i(t)
        En zayıf halka belirler.
        """
        R = 1.0
        for comp, params in self.COMPONENTS.items():
            R *= params.reliability(t_h)
        return R

    def system_mtbf(self) -> float:
        """Sistem MTBF — sayısal integrasyon."""
        t_arr = np.linspace(0, 50000, 5000)
        R_arr = np.array([self.system_reliability(t) for t in t_arr])
        # MTTF = ∫ R(t) dt
        return float(np.trapezoid(R_arr, t_arr) if hasattr(np,"trapezoid") else np.sum((R_arr[:-1]+R_arr[1:])*np.diff(t_arr)/2))

    def failure_analysis(self, t_h: float = 8760.0) -> str:
        """t_h saatte her bileşen için güvenilirlik raporu."""
        lines = [
            f"  MTBF Analizi @ t={t_h:.0f}h:",
            f"  {'Bileşen':<24} {'β':>5} {'η':>7} {'R(t)':>8} {'h(t)':>10} {'B10':>8}",
            "  " + "─" * 65,
        ]
        for name, params in self.COMPONENTS.items():
            R = params.reliability(t_h)
            h = params.failure_rate(t_h)
            b10 = params.bx_life(10.0)
            lines.append(
                f"  {name:<24} {params.beta:>5.1f} {params.eta_h:>7.0f}h "
                f"{R:>8.4f} {h*1000:>9.4f}‰/h {b10:>7.0f}h"
            )
        R_sys = self.system_reliability(t_h)
        mtbf  = self.system_mtbf()
        lines += [
            "  " + "─" * 65,
            f"  SİSTEM: R({t_h:.0f}h)={R_sys:.4f}  MTBF={mtbf:.0f}h  "
            f"{'✓ KABUL' if R_sys>0.9 else '⚠ DÜŞÜK'}",
        ]
        return "\n".join(lines)


# ── Thermal Models ─────────────────────────────────────────────────

@dataclass(slots=True)
class ThermalState:
    """Termal durum değişkenleri."""
    t_ambient_C:       float = 20.0   # Ortam sıcaklığı
    t_motor_X_C:       float = 20.0   # X motoru sıcaklığı
    t_motor_A_C:       float = 20.0   # A motoru sıcaklığı
    t_lead_screw_C:    float = 20.0   # Vida sıcaklığı
    t_frame_C:         float = 20.0   # Gövde sıcaklığı
    elapsed_h:         float = 0.0    # Geçen süre [saat]


class ThermalDriftModel:
    """
    Makine termal sürüklenme modeli.

    Kaynaklar:
      1. Ortam sıcaklık değişimi (sabah-akşam ΔT=10°C)
      2. Motor ısınma (Joule heating)
      3. Vida ısınma (sürtünme)
      4. Gövde termal gradyanı

    Toplam pozisyon hatası:
      Δx_thermal = Σ α_i × L_i × ΔT_i  [mm]
    """

    ALPHA_STEEL  = 11.7e-6   # /°C — çelik
    ALPHA_ALUM   = 23.1e-6   # /°C — alüminyum
    L_SCREW_MM   = 685.8     # Vida uzunluğu [mm]
    L_FRAME_MM   = 800.0     # Gövde referans uzunluğu [mm]

    # Motor termal modeli
    R_THERMAL_C_W = 5.0      # °C/W — motor termal direnç
    TAU_THERMAL_S = 300.0    # s — motor ısınma zaman sabiti
    P_MOTOR_W     = 8.0      # W — tipik motor gücü
    # Torque drift: -0.2%/°C
    TORQUE_DRIFT  = -0.002   # /°C

    def __init__(self, state: Optional[ThermalState] = None) -> None:
        self.state = state or ThermalState()

    def update(self, dt_s: float) -> ThermalState:
        """
        Termal durumu dt_s saniye ilerlet.

        Motor ısınma: üstel yaklaşma
        Vida: linear interpolasyon (basit model)
        """
        st   = self.state
        tau  = self.TAU_THERMAL_S
        T_ss = st.t_ambient_C + self.R_THERMAL_C_W * self.P_MOTOR_W

        # Motor sıcaklıkları
        st.t_motor_X_C += (T_ss - st.t_motor_X_C) * dt_s / tau
        st.t_motor_A_C += (T_ss - st.t_motor_A_C) * dt_s / tau

        # Vida ısınma (sürtünme)
        P_friction = 0.3 * self.P_MOTOR_W
        T_ss_screw = st.t_ambient_C + self.R_THERMAL_C_W * P_friction
        st.t_lead_screw_C += (T_ss_screw - st.t_lead_screw_C) * dt_s / (tau * 2)

        # Gövde (yavaş ısınma)
        st.t_frame_C += (st.t_ambient_C + 2.0 - st.t_frame_C) * dt_s / (tau * 10)
        st.elapsed_h += dt_s / 3600.0

        return st

    def positional_error_mm(self) -> float:
        """
        Termal genleşmeden kaynaklanan toplam pozisyon hatası [mm].
        """
        st = self.state
        # Vida termal genleşmesi
        ΔT_screw  = st.t_lead_screw_C - 20.0
        Δx_screw  = self.ALPHA_STEEL * self.L_SCREW_MM * ΔT_screw

        # Gövde termal genleşmesi
        ΔT_frame  = st.t_frame_C - 20.0
        Δx_frame  = self.ALPHA_ALUM * self.L_FRAME_MM * ΔT_frame

        return Δx_screw + Δx_frame

    def torque_reduction_factor(self) -> float:
        """Motor tork düşüşü faktörü [0,1]."""
        ΔT = max(self.state.t_motor_X_C, self.state.t_motor_A_C) - 20.0
        return max(0.7, 1.0 + self.TORQUE_DRIFT * ΔT)

    def report(self) -> str:
        st = self.state
        Δx = self.positional_error_mm()
        τ  = self.torque_reduction_factor()
        return (
            f"  Termal Durum @ {st.elapsed_h:.2f}h:\n"
            f"    Motor_X={st.t_motor_X_C:.1f}°C  Motor_A={st.t_motor_A_C:.1f}°C\n"
            f"    Vida={st.t_lead_screw_C:.1f}°C  Gövde={st.t_frame_C:.1f}°C\n"
            f"    Pozisyon hatası: {Δx:.4f}mm  "
            f"Tork faktörü: {τ:.4f} ({(1-τ)*100:.1f}% düşüş)"
        )


# ── Microstep Nonlinearity ─────────────────────────────────────────

class MicrostepModel:
    """
    Adım motoru mikrostep doğrusalsızlık modeli.

    Sinüsoidal akım dalgalanması:
      x_actual(n) = x_ideal(n) + A_nl × sin(2πn/N_ms)
      A_nl ≈ 0.05 × full_step_size

    Bu, mikrostep sayısına göre farklı periyot ile tekrar eder.
    Tipik N_ms = 16 (1/16 mikrostep).
    """

    def __init__(
        self,
        steps_per_mm:     float = 80.0,
        microstep_factor: int   = 16,
        nonlinearity_pct: float = 5.0,
    ) -> None:
        self.spm   = steps_per_mm
        self.N_ms  = microstep_factor
        self.A_nl  = (nonlinearity_pct / 100.0) / steps_per_mm  # mm amplitude

    def positional_error_mm(self, x_mm: float) -> float:
        """
        Verilen pozisyonda mikrostep hata [mm].

        n = x_mm × steps_per_mm (step numarası)
        """
        n = x_mm * self.spm
        return self.A_nl * math.sin(2.0 * math.pi * n / self.N_ms)

    def rms_error_mm(self) -> float:
        """RMS pozisyon hatası = A_nl / √2"""
        return self.A_nl / math.sqrt(2.0)

    def worst_case_mm(self) -> float:
        """Maksimum hata: A_nl"""
        return self.A_nl

    def resonant_speed_mm_min(self, base_period_s: float = 0.001) -> float:
        """
        Mikrostep rezonans hızı.

        f_ms_resonance = v / (N_ms × step_size)
        Rezonans: motor step frekansı = doğal frekansa eşit
        """
        step_size_mm = 1.0 / self.spm
        return self.N_ms * step_size_mm / base_period_s * 60.0


# ── Long-Run Drift Model ───────────────────────────────────────────

@dataclass(slots=True)
class LongRunStats:
    """Uzun süreli çalışma istatistikleri."""
    duration_h:           float
    n_circuits:           int
    n_passes:             int
    cumulative_phi_drift: float   # [°] — toplam spindle kayması
    cumulative_x_drift:   float   # [mm] — toplam pozisyon kayması
    thermal_error_mm:     float   # Termal genleşme hatası
    microstep_rms_mm:     float   # Mikrostep RMS hatası
    missed_steps_x:       int
    missed_steps_a:       int
    tension_cv:           float   # Gerilme varyasyon katsayısı
    winding_consistency:  float   # [0,1] — kaplama tutarlılığı
    failure_probability:  float   # Weibull R(t) tamamlayıcısı

    def print_report(self) -> None:
        print("  ─" * 34)
        print(f"  UZUN SÜRELİ ÇALIŞMA RAPORU")
        print(f"  Süre: {self.duration_h:.2f}h  Devreler: {self.n_circuits}  Geçişler: {self.n_passes}")
        print(f"  Kümülatif φ kayması: {self.cumulative_phi_drift:.4f}°")
        print(f"  Kümülatif X kayması: {self.cumulative_x_drift:.4f}mm")
        print(f"  Termal hata:         {self.thermal_error_mm:.4f}mm")
        print(f"  Mikrostep RMS:       {self.microstep_rms_mm:.5f}mm")
        print(f"  Kaçan adım X/A:      {self.missed_steps_x}/{self.missed_steps_a}")
        print(f"  Gerilme CV:          {self.tension_cv:.4f}")
        print(f"  Sarım tutarlılığı:   {self.winding_consistency:.4f}")
        print(f"  Arıza olasılığı:     {self.failure_probability:.4f}")
        ok = (self.cumulative_phi_drift < 10.0 and
              self.thermal_error_mm < 0.2 and
              self.winding_consistency > 0.8)
        print(f"  SONUÇ: {'✓ KABUL EDİLEBİLİR' if ok else '✗ MÜDAHALE GEREKLİ'}")
        print("  ─" * 34)


class LongRunSimulator:
    """
    6 saatlik sürekli sarım simülasyonu (sıkıştırılmış).

    Her geçiş için:
      - Termal durum güncelleme
      - Kümülatif drift hesaplama
      - Mikrostep hatası
      - Missed step olasılığı (Weibull tabanlı)
      - Gerilme varyasyonu
    """

    def __init__(
        self,
        n_circuits_per_layer:  int   = 21,
        n_layers:              int   = 4,
        fiber_speed_mm_s:      float = 100.0,
        mandrel_L_mm:          float = 300.0,
        drift_deg_circuit:     float = 1.22,
        drift_x_mm_h:          float = 0.05,
        seed:                  int   = 42,
    ) -> None:
        self.k        = n_circuits_per_layer
        self.n_lay    = n_layers
        self.v        = fiber_speed_mm_s
        self.L        = mandrel_L_mm
        self.drift_φ  = drift_deg_circuit
        self.drift_x  = drift_x_mm_h
        self._rng     = np.random.default_rng(seed)
        self._thermal = ThermalDriftModel()
        self._mtbf    = MTBFEstimator()
        self._ms_model= MicrostepModel()

    def simulate(self, duration_h: float = 6.0) -> LongRunStats:
        """
        duration_h saatlik üretim simülasyonu.

        Adım sayısı: n_passes = duration_h × (v/L×60×60) × k × n_layers
        """
        # Zaman başına geçiş sayısı
        t_circuit_s  = 2 * self.L / self.v   # İleri + geri [s]
        total_circuits = int(duration_h * 3600 / t_circuit_s)
        total_passes   = total_circuits * self.n_lay
        dt_step        = duration_h * 3600 / max(total_passes, 1)

        # Termal simülasyon
        thermal_errors = []
        torque_factors = []
        for _ in range(min(total_passes, 500)):
            self._thermal.update(dt_step)
            thermal_errors.append(self._thermal.positional_error_mm())
            torque_factors.append(self._thermal.torque_reduction_factor())
        thermal_err = float(thermal_errors[-1]) if thermal_errors else 0.0

        # Kümülatif drift
        cum_phi = self.drift_φ * total_circuits
        cum_x   = self.drift_x * duration_h

        # Missed steps (Weibull tabanlı)
        # Düşük tork → daha fazla step kaybı
        mean_torque = float(np.mean(torque_factors)) if torque_factors else 1.0
        lambda_miss = 0.05 * max(0.0, 1.0 - mean_torque) * total_passes
        miss_x = self._rng.poisson(lambda_miss)
        miss_a = self._rng.poisson(lambda_miss * 0.7)

        # Gerilme varyasyonu (zamanla artan)
        T_base = 15.0
        T_noise_std = 0.5 + duration_h * 0.1   # Yavaşça artan
        T_arr  = T_base + self._rng.normal(0, T_noise_std, min(total_passes, 1000))
        T_cv   = float(np.std(T_arr) / np.mean(T_arr))

        # Sarım tutarlılığı
        phi_cv  = abs(cum_phi) / max(total_circuits * 5, 1)   # Normalize
        x_cv    = abs(cum_x) / max(duration_h * 0.5, 1e-6)
        consistency = max(0.0, 1.0 - 0.3*phi_cv - 0.3*x_cv - 0.4*T_cv)

        # Arıza olasılığı
        fail_prob = 1.0 - self._mtbf.system_reliability(duration_h)

        # Mikrostep RMS
        ms_rms = self._ms_model.rms_error_mm()

        return LongRunStats(
            duration_h           = duration_h,
            n_circuits           = total_circuits,
            n_passes             = total_passes,
            cumulative_phi_drift = cum_phi,
            cumulative_x_drift   = cum_x,
            thermal_error_mm     = thermal_err,
            microstep_rms_mm     = ms_rms,
            missed_steps_x       = miss_x,
            missed_steps_a       = miss_a,
            tension_cv           = T_cv,
            winding_consistency  = consistency,
            failure_probability  = fail_prob,
        )
