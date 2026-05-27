"""
closed_loop_controller.py — Kapalı Çevrim Winding Kontrolcüsü
=============================================================
PID + Feedforward hibrit kontrol mimarisi.

Kontrol yapısı:
  u_total = u_ff + u_fb

  Feedforward (u_ff):
    Planner'dan gelen ideal G-code feedrate ve spindle hızı.
    u_ff_x = S'·cos(α)   [mm/s]
    u_ff_A = S'·sin(α)/R [°/s]

  Feedback (u_fb):
    Kalman tahmin hatası üzerinde PID.
    e_x   = z_planned - z_estimated     [mm]
    e_phi = φ_planned - φ_estimated     [°]
    e_T   = T_nominal - T_estimated     [N]

    u_fb_x = Kp_x·e_x + Ki_x·∫e_x + Kd_x·ė_x   [mm/s correction]
    u_fb_A = Kp_A·e_phi + Ki_A·∫e_phi            [°/s correction]
    u_fb_T → payout motor speed change

Anti-windup: İntegral sınırlanır (clamp) — saturasyon koruması
Incremental form: Bumpless transfer ve init problemleri önler

Backlash hysteresis modeli:
  Luenberger-Olsson backlash model:
    Yadmittance b/w dead-zone ±δ_bl:
    y_bl(t) = { u(t) - δ_bl·sign(u)   if |u - y_prev| > 2δ_bl
               { y_prev                  otherwise
  Feed override: Yön değişimi → δ_bl/v_x süresi boşluk geçişi

Spindle lag compensation:
  φ_comp = φ_cmd - φ_estimated          [°]  → anlık lag
  Feedforward düzeltme:
    u_A_corrected = u_A_ff + Kcomp·(φ_comp - φ_comp_nominal)
  Nominal lag: φ_nom = ω·τ_A (steady-state beklenen)

Dynamic feed override:
  Curvature-aware + error-aware hız düşürme:
  override = min(v_curvature_limit, v_error_limit, v_machine_limit) / v_nominal
  v_error_limit: |e_x| büyükse hızı düşür (sistem yakalayana kadar)

Referans:
  Ziegler-Nichols PID tuning (Åström & Hägglund 1995);
  Dual-input describing function backlash model (Olsson et al. 1998);
  Koussios (2004) p.290: fiber tension dynamics
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional, Tuple

import numpy as np

from state_estimator import StateEstimate


# ── PID Controller (incremental form) ───────────────────────────

@dataclass(frozen=True, slots=True)
class PIDGains:
    Kp: float; Ki: float; Kd: float
    integral_limit: float = 100.0   # Anti-windup clamp
    output_limit:   float = 500.0   # Output saturation [mm/s veya °/s]


class IncrementalPID:
    """
    Incremental (velocity) form PID.

    Bumpless transfer ve integral windup koruması sağlar.

    Δu[k] = Kp·(e[k]-e[k-1]) + Ki·dt·e[k] + Kd/dt·(e[k]-2e[k-1]+e[k-2])
    u[k]  = u[k-1] + Δu[k]

    Avantajı: Init sorunu yok, satürasyon sırasında windup yok.
    """

    def __init__(self, gains: PIDGains, dt: float) -> None:
        self.g    = gains
        self.dt   = dt
        self._e   = [0.0, 0.0, 0.0]   # e[k], e[k-1], e[k-2]
        self._u   = 0.0
        self._integral = 0.0

    def update(self, error: float) -> float:
        """
        PID güncelleme — incremental form.

        Args:
            error: e[k] = setpoint - measured

        Returns:
            Kontrol sinyali u[k]
        """
        g = self.g; dt = self.dt
        e0, e1, e2 = error, self._e[0], self._e[1]

        # Proportional increment
        delta_P = g.Kp * (e0 - e1)
        # Integral (with anti-windup clamp)
        self._integral = np.clip(
            self._integral + g.Ki * dt * e0,
            -g.integral_limit, g.integral_limit)
        # Derivative (filtered)
        delta_D = g.Kd / dt * (e0 - 2*e1 + e2) if dt > 1e-9 else 0.0

        self._u = np.clip(
            self._u + delta_P + g.Ki*dt*e0 + delta_D,
            -g.output_limit, g.output_limit)

        self._e = [e0, e1, e2]
        return self._u

    def reset(self) -> None:
        self._e = [0.0, 0.0, 0.0]
        self._u = 0.0
        self._integral = 0.0


# ── Backlash Hysteresis Model ────────────────────────────────────

class BacklashCompensator:
    """
    Luenberger-Olsson backlash hysteresis modeli ve compensation.

    İki mod:
      1. Estimatör: Gerçek boşluk miktarını tahmin et
      2. Compensatör: Yön değişiminde fazladan mesafe ekle

    Kullanım:
        bl = BacklashCompensator(backlash_mm=0.15)
        x_compensated = bl.compensate(x_commanded, v_direction)
    """

    def __init__(self, backlash_mm: float = 0.15) -> None:
        self.delta    = backlash_mm
        self._y_prev  = 0.0    # Son output
        self._in_zone = False  # Dead-zone içinde mi?

    def compensate(self, u: float, direction: int) -> float:
        """
        Backlash-compensated output.

        Args:
            u:         Komut pozisyonu [mm]
            direction: +1 (ileri) veya -1 (geri)

        Returns:
            Backlash-corrected komut [mm]
        """
        if direction > 0:
            y = max(self._y_prev, u - self.delta)
        else:
            y = min(self._y_prev, u + self.delta)
        self._y_prev = y
        return y

    def estimate_backlash(
        self,
        x_cmd_arr: np.ndarray,
        x_meas_arr: np.ndarray,
    ) -> float:
        """
        Komut ve ölçüm serisinden backlash tahmini.

        Yön değişimi noktalarında ölçüm gecikmesini analiz eder.
        Returns: Tahmini backlash [mm]
        """
        if len(x_cmd_arr) < 3:
            return self.delta

        # Yön değişimi noktaları
        v = np.diff(x_cmd_arr)
        reversals = np.where(v[:-1] * v[1:] < 0)[0] + 1

        if len(reversals) == 0:
            return self.delta

        errors_at_reversals = []
        for idx in reversals:
            if idx < len(x_meas_arr):
                errors_at_reversals.append(abs(x_cmd_arr[idx] - x_meas_arr[idx]))

        if not errors_at_reversals:
            return self.delta
        return float(np.median(errors_at_reversals))

    def update_estimate(self, new_estimate: float, alpha: float = 0.2) -> None:
        """EMA ile backlash tahminini güncelle."""
        self.delta = (1.0 - alpha) * self.delta + alpha * new_estimate


# ── Feed Override ─────────────────────────────────────────────────

@dataclass(slots=True)
class FeedOverrideState:
    """Anlık feed override durumu."""
    override_factor:     float = 1.0   # [0.05, 1.0]
    reason:              str   = "nominal"
    curvature_limit:     float = 1.0
    error_limit:         float = 1.0
    machine_limit:       float = 1.0

    @property
    def effective_feed(self) -> float:
        return min(self.curvature_limit, self.error_limit, self.machine_limit)


class DynamicFeedOverride:
    """
    Curvature-aware + error-aware dinamik feed hız override.

    Override kaynakları (min alınır):
      1. Curvature limit:  √(a_cent_max / κ_n)
      2. Error limit:      1 - K_err·|e_x|    (büyük hata → yavaşla)
      3. Machine limit:    v_x_max / v_nominal
    """

    def __init__(
        self,
        v_nominal:       float = 100.0,   # [mm/s]
        a_centripetal:   float = 5000.0,  # [mm/s²]
        v_x_max:         float = 133.3,   # [mm/s]
        K_error:         float = 2.0,     # Error-to-override gain
        min_override:    float = 0.05,
    ) -> None:
        self.v_nom   = v_nominal
        self.a_cent  = a_centripetal
        self.v_x_max = v_x_max
        self.K_err   = K_error
        self.min_ov  = min_override

    def compute(
        self,
        kappa_n:   float,      # [1/mm] — normal eğrilik
        error_x:   float,      # [mm] — konum hatası
        alpha_rad: float,      # Sarım açısı
    ) -> FeedOverrideState:
        """Override faktörünü hesapla."""
        # 1. Curvature limit
        if kappa_n > 1e-9:
            v_curv = math.sqrt(self.a_cent / kappa_n)
            curv_lim = min(1.0, v_curv / self.v_nom)
        else:
            curv_lim = 1.0

        # 2. Error limit: büyük hatada yavaşla
        err_lim = max(self.min_ov, 1.0 - self.K_err * abs(error_x) / self.v_nom)

        # 3. Machine limit
        v_x_nom = self.v_nom * math.cos(alpha_rad)
        mach_lim = min(1.0, self.v_x_max / max(v_x_nom, 1e-6))

        ov = max(self.min_ov, min(1.0, curv_lim * err_lim * mach_lim))

        reason = "nominal"
        if curv_lim < err_lim and curv_lim < mach_lim:
            reason = f"curvature(κ={kappa_n:.4f})"
        elif err_lim < mach_lim:
            reason = f"error_track(e={error_x:.3f}mm)"
        else:
            reason = "machine_limit"

        return FeedOverrideState(
            override_factor  = ov,
            reason           = reason,
            curvature_limit  = curv_lim,
            error_limit      = err_lim,
            machine_limit    = mach_lim,
        )


# ── Spindle Lag Compensator ──────────────────────────────────────

class SpindleLagCompensator:
    """
    A-ekseni lag açığı aktif olarak telafi eder.

    Steady-state lag: φ_lag_ss = ω·τ_A
    Geçici lag:       φ_lag(t) = φ_lag_ss·(1 - e^(-t/τ_A))

    Compensation stratejisi:
      1. Mevcut lag hesapla: φ_lag = φ_planned - φ_estimated
      2. Nominal lag çıkar: Δφ_excess = φ_lag - φ_lag_nominal
      3. Sürücüye düzeltici komut: u_A_comp = Kcomp·Δφ_excess/dt

    Sınırlar:
      - Aşırı kompensasyon → spindle overspeed
      - Kompensasyon miktarı ≤ u_A_max × comp_fraction
    """

    def __init__(
        self,
        tau_A_s:          float = 6.0,
        K_compensate:     float = 0.5,
        max_comp_fraction:float = 0.3,    # max(u_A) × bu faktör
        u_A_max_dps:      float = 1500.0, # °/s max spindle speed
    ) -> None:
        self.tau_A     = tau_A_s
        self.K_comp    = K_compensate
        self.max_frac  = max_comp_fraction
        self.u_A_max   = u_A_max_dps
        self._lag_hist: List[float] = []

    def compute_compensation(
        self,
        phi_planned:   float,   # [°]
        phi_estimated: float,   # [°] — Kalman tahmini
        omega_planned: float,   # [°/s] — komut açısal hızı
        dt:            float,   # [s]
    ) -> Tuple[float, float]:
        """
        Spindle lag kompensasyon hesabı.

        Returns:
            (u_A_comp, phi_lag)
            u_A_comp: Ek spindle hız komutu [°/s]
            phi_lag:  Mevcut lag [°]
        """
        phi_lag      = phi_planned - phi_estimated
        phi_lag_nom  = omega_planned * self.tau_A  # Steady-state beklenen lag

        # Fazla lag (nominal üstü)
        delta_lag    = phi_lag - phi_lag_nom

        # Oransal kompensasyon
        u_A_comp     = self.K_comp * delta_lag / max(dt, 1e-6)

        # Sınırla
        u_A_comp_max = self.u_A_max * self.max_frac
        u_A_comp     = np.clip(u_A_comp, -u_A_comp_max, u_A_comp_max)

        self._lag_hist.append(phi_lag)
        if len(self._lag_hist) > 100:
            self._lag_hist.pop(0)

        return float(u_A_comp), float(phi_lag)

    @property
    def mean_lag_deg(self) -> float:
        """Son 100 örnekte ortalama lag [°]."""
        if not self._lag_hist:
            return 0.0
        return float(np.mean(self._lag_hist))

    @property
    def lag_rms_deg(self) -> float:
        """RMS lag [°]."""
        if not self._lag_hist:
            return 0.0
        return float(np.sqrt(np.mean(np.array(self._lag_hist)**2)))


# ── Closed-Loop Controller ────────────────────────────────────────

@dataclass(slots=True)
class ControlOutput:
    """Kontrolcü çıktısı — bir zaman adımı."""
    u_x_ff:        float   # Feedforward carriage [mm/s]
    u_x_fb:        float   # Feedback correction [mm/s]
    u_A_ff:        float   # Feedforward spindle [°/s]
    u_A_comp:      float   # Spindle lag compensation [°/s]
    u_A_fb:        float   # Feedback phi correction [°/s]
    u_T:           float   # Tension correction (payout) [mm/s]
    feed_override: float   # [0.05, 1.0]
    feed_reason:   str
    phi_lag:       float   # Mevcut spindle lag [°]
    x_error:       float   # Konum hatası [mm]
    phi_error:     float   # Açı hatası [°]
    T_error:       float   # Gerilme hatası [N]
    backlash_active: bool

    @property
    def u_x_total(self) -> float:
        """Toplam carriage komut hızı [mm/s]."""
        return (self.u_x_ff + self.u_x_fb) * self.feed_override

    @property
    def u_A_total(self) -> float:
        """Toplam spindle komut hızı [°/s]."""
        return self.u_A_ff + self.u_A_comp + self.u_A_fb

    def summary(self) -> str:
        return (
            f"u_x={self.u_x_total:.2f}mm/s(FF={self.u_x_ff:.2f}+FB={self.u_x_fb:.2f}) "
            f"u_A={self.u_A_total:.2f}°/s(FF={self.u_A_ff:.2f}+comp={self.u_A_comp:.2f}) "
            f"OV={self.feed_override:.3f}[{self.feed_reason}] "
            f"lag={self.phi_lag:.3f}° "
            f"e_x={self.x_error:.4f}mm e_φ={self.phi_error:.4f}°"
        )


class ClosedLoopController:
    """
    Filament winding kapalı çevrim kontrol sistemi.

    Bileşenler:
      - PID_x:   Carriage pozisyon hatası → hız düzeltme
      - PID_phi: Spindle açı hatası → açısal hız düzeltme
      - PID_T:   Tension hatası → payout hız düzeltme
      - SpindleLagCompensator: Proaktif lag telafisi
      - BacklashCompensator:   Yön geçişinde konum düzeltme
      - DynamicFeedOverride:   Curvature + error aware hız sınırlama

    Kullanım:
        ctrl = ClosedLoopController(config)
        ctrl.reset()
        for each segment:
            out = ctrl.update(
                z_planned, phi_planned, T_nominal,
                state_estimate, kappa_n, alpha_rad, dt)
            # out.u_x_total → carriage setpoint
            # out.u_A_total → spindle setpoint
    """

    def __init__(
        self,
        x_gains:    PIDGains = None,
        phi_gains:  PIDGains = None,
        T_gains:    PIDGains = None,
        backlash_mm:float = 0.15,
        tau_A_s:    float = 6.0,
        v_nominal:  float = 100.0,
        dt:         float = 0.010,
    ) -> None:
        self.dt   = dt
        self._pid_x   = IncrementalPID(
            x_gains or PIDGains(Kp=3.0, Ki=0.5, Kd=0.2,
                                integral_limit=50.0, output_limit=30.0), dt)
        self._pid_phi = IncrementalPID(
            phi_gains or PIDGains(Kp=1.5, Ki=0.2, Kd=0.05,
                                  integral_limit=20.0, output_limit=200.0), dt)
        self._pid_T   = IncrementalPID(
            T_gains or PIDGains(Kp=0.5, Ki=0.05, Kd=0.0,
                                integral_limit=10.0, output_limit=20.0), dt)
        self._lag_comp   = SpindleLagCompensator(tau_A_s=tau_A_s)
        self._bl_comp    = BacklashCompensator(backlash_mm=backlash_mm)
        self._feed_ov    = DynamicFeedOverride(v_nominal=v_nominal)
        self._prev_dir   = 0

    def reset(self) -> None:
        """Tüm bileşenleri sıfırla."""
        self._pid_x.reset(); self._pid_phi.reset(); self._pid_T.reset()

    def update(
        self,
        z_planned:   float,    # Planlanan carriage pozisyonu [mm]
        phi_planned: float,    # Planlanan spindle açısı [°]
        T_nominal:   float,    # Nominal gerilme [N]
        alpha_rad:   float,    # Sarım açısı [rad]
        fiber_speed: float,    # Nominal fiber hızı S' [mm/s]
        state:       StateEstimate,  # Kalman tahmin
        kappa_n:     float = 0.02,   # Normal eğrilik [1/mm]
        direction:   int   = 1,      # +1 ileri, -1 geri
    ) -> ControlOutput:
        """
        Tek kontrol adımı.

        Args:
            z_planned:   Planlanan X pozisyonu [mm]
            phi_planned: Planlanan A açısı [°]
            T_nominal:   Hedef gerilme [N]
            alpha_rad:   Sarım açısı [rad]
            fiber_speed: Hedef fiber hızı [mm/s]
            state:       Kalman durum tahmini
            kappa_n:     Normal eğrilik [1/mm]
            direction:   +1 ileri, -1 geri

        Returns:
            ControlOutput
        """
        # ── Feedforward ──────────────────────────────────────────
        u_x_ff = fiber_speed * math.cos(alpha_rad)     # [mm/s]
        u_A_ff = fiber_speed * math.sin(alpha_rad) / 50.0 * (180.0/math.pi)  # [°/s]

        # ── Hatalar ──────────────────────────────────────────────
        e_x   = z_planned - state.z_mm
        e_phi = phi_planned - state.phi_deg
        e_T   = T_nominal - state.tension_N

        # ── Feedback PID ─────────────────────────────────────────
        u_x_fb   = self._pid_x.update(e_x)
        u_phi_fb = self._pid_phi.update(e_phi)
        u_T_fb   = self._pid_T.update(e_T)

        # ── Spindle lag compensation ──────────────────────────────
        u_A_comp, phi_lag = self._lag_comp.compute_compensation(
            phi_planned, state.phi_deg, u_A_ff, self.dt)

        # ── Backlash compensation ─────────────────────────────────
        bl_active = (direction != self._prev_dir and self._prev_dir != 0)
        self._prev_dir = direction
        if bl_active:
            z_planned_bl = self._bl_comp.compensate(z_planned, direction)
            e_x = z_planned_bl - state.z_mm
            u_x_fb = self._pid_x.update(e_x)

        # ── Dynamic feed override ─────────────────────────────────
        ov_state = self._feed_ov.compute(kappa_n, e_x, alpha_rad)

        return ControlOutput(
            u_x_ff          = u_x_ff,
            u_x_fb          = u_x_fb,
            u_A_ff          = u_A_ff,
            u_A_comp        = u_A_comp,
            u_A_fb          = u_phi_fb,
            u_T             = u_T_fb,
            feed_override   = ov_state.effective_feed,
            feed_reason     = ov_state.reason,
            phi_lag         = phi_lag,
            x_error         = e_x,
            phi_error       = e_phi,
            T_error         = e_T,
            backlash_active = bl_active,
        )

    def update_backlash(self, x_cmd: np.ndarray, x_meas: np.ndarray) -> float:
        """Ölçüm verisinden backlash tahmini güncelle."""
        est = self._bl_comp.estimate_backlash(x_cmd, x_meas)
        self._bl_comp.update_estimate(est)
        return self._bl_comp.delta

    @property
    def lag_stats(self) -> Tuple[float, float]:
        """(mean_lag_deg, rms_lag_deg)"""
        return self._lag_comp.mean_lag_deg, self._lag_comp.lag_rms_deg
