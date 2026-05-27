"""
machine_simulation.py — Makine Davranış Simülasyonu
=====================================================
Planlanan (ideal) G-code hareketi ile gerçek makine davranışı
arasındaki fiziksel farkları modelleyen simülasyon motoru.

Simüle edilen davranışlar:
  1. Spindle lag       — A ekseni eylemsizlik gecikmesi
  2. Carriage accel    — X ekseni gerçekçi ivme profili
  3. Backlash          — Vida/kılavuz boşluğu (yön değişiminde)
  4. Missed steps      — Stepper adım kaybı (Poisson modeli)
  5. Feed sync drift   — X/A senkron. kayması (uzun sarım birikimi)
  6. Tension spikes    — Turn-around noktasında gerilme artışı

Fizik modelleri:

  [Spindle Lag]
    A-ekseni motor + dişli + mandrel sistemi:
    J_total = J_motor + J_gear + J_mandrel
    τ_A = J_total / (B_viscous + K_friction/ω)
    Δφ_lag(t) = ω(t) · τ_A  [rad, steady-state lag]
    Geçici (step change): φ_actual(t) = φ_cmd(t) - ΔΦ·e^(-t/τ_A)

  [Carriage Acceleration]
    v_actual(t) = v_cmd · (1 - e^(-t/τ_X))
    τ_X = v_cmd / a_max  (% 63 ulaşma süresi)
    Konumsallaşma hatası: x_err = ∫(v_cmd - v_actual)dt ≈ v_cmd·τ_X

  [Backlash]
    Yön değişimi algılama: sign(v_x[i]) ≠ sign(v_x[i-1])
    Hata ekleme: x_actual += δ_backlash × sign(v_x[i])
    Birikimli hata (tek yön sürekli hareket): sıfır.
    Geri dönüş (turn-around): 2×δ_backlash hata.

  [Missed Steps]
    Stepper motor adım kaybı olasılığı:
    P_miss(dt) = 1 - exp(-λ·dt)
    λ = λ₀ · max(0, v/v_crit - 1)² × max(0, a/a_crit - 1)
    Tipik: λ₀=0.1/s, v_crit=0.9·v_max, a_crit=0.85·a_max
    Etki: x_err += n_missed × step_resolution_mm

  [Feed Sync Drift]
    Her geçişte kümülatif spindle lag nedeniyle:
    Δα_drift_per_pass = mean(Δφ_lag) / K_full  [rad/rad]
    n geçiş sonra: Δα_total = n × Δα_drift_per_pass
    Fiber yerleşim açısı hatası: δα = arctan(Δφ_lag × R / ΔX)

  [Tension Spikes]
    Turn-around: carriage v_x → 0 → -v_x (a_x = 2v_x/t_acc)
    Fiber gerilmesi: T = T_nom + k_fiber × Δv_pay + m_pay × a_car
    k_fiber ≈ E_f × A_f / L_pay  [N/mm]: E-cam ~70GPa, A=fiber kesit
    m_pay ≈ ρ_fiber × A_f × L_pay: titizlik terimi

  [Payout Eye Dynamics]
    İkinci derece ODE (basit harmonic oscillator + damping):
    m_eye × ẍ_e + b_eye × ẋ_e + k_eye × x_e = F_fiber(t)
    Kritik damping: b_cr = 2√(m·k)
    Tipik: ζ ≈ 0.5-0.7 (underdamped, oskilasyon mevcut)
    x_e(t): Runge-Kutta 4 ile sayısal entegrasyon

Referans: ADA268923 App.K (backlash data); Koussios (2004) s.290 (tension);
          ICCM8 Di Vita (fiber tension model)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
from scipy.integrate import solve_ivp


# ── Simulation Config ─────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SimulationConfig:
    """Simülasyon parametreleri — makine spesifik."""
    # Spindle lag
    J_total_kgm2:      float = 0.0901    # kg·m² (ADA268923: 901 kg·cm²)
    B_viscous_Nms:     float = 0.015     # N·m·s/rad — viskoz sönüm
    # Carriage backlash
    backlash_mm:       float = 0.15      # mm — lead screw boşluğu (tipik)
    # Missed steps
    lambda0_per_s:     float = 0.08      # Temel adım kaybı oranı
    v_crit_frac:       float = 0.90      # v_max'ın yüzde kaçında kritik
    a_crit_frac:       float = 0.85      # a_max'ın yüzde kaçında kritik
    step_res_x_mm:     float = 0.0125    # mm/step (80 step/mm)
    step_res_a_deg:    float = 0.0225    # °/step (44.44 step/°)
    # Fiber tension
    E_fiber_GPa:       float = 70.0      # GPa (E-cam elyaf)
    fiber_diameter_mm: float = 0.015     # mm (tipik roving)
    L_payout_mm:       float = 400.0     # mm — serbest fiber uzunluğu
    T_nominal_N:       float = 15.0      # N — nominal gerilme
    # Payout eye
    m_eye_kg:          float = 0.050     # kg — kılavuz kütlesi
    b_eye_Ns_m:        float = 2.0       # N·s/m — sönüm
    k_eye_N_m:         float = 500.0     # N/m — yay sabiti
    # Machine limits (from MachinePhysics.default())
    v_x_max_mm_s:      float = 133.3     # mm/s
    a_x_max_mm_s2:     float = 42834.0   # mm/s²
    # Random seed (-1: gerçek rastgele)
    random_seed:       int   = 42

    @property
    def tau_spindle(self) -> float:
        """Spindle zaman sabiti [s]."""
        return self.J_total_kgm2 / max(self.B_viscous_Nms, 1e-9)

    @property
    def tau_carriage(self) -> float:
        """Carriage ivmelenme zaman sabiti [s]."""
        return self.v_x_max_mm_s / max(self.a_x_max_mm_s2 / 1000.0, 1e-9)

    @property
    def k_fiber_N_per_mm(self) -> float:
        """Fiber yay sabiti [N/mm]: k = E·A/L."""
        A_mm2 = math.pi * (self.fiber_diameter_mm / 2.0)**2
        E_N_per_mm2 = self.E_fiber_GPa * 1e3  # GPa → N/mm²
        return E_N_per_mm2 * A_mm2 / self.L_payout_mm


# ── Per-Segment Simulation State ─────────────────────────────────

@dataclass(slots=True)
class SimState:
    """
    Tek segment simülasyon durumu.

    _planned: İdeal G-code değeri
    _actual:  Simüle edilen gerçek değer
    _error:   Fark (actual - planned)
    """
    # Pozisyon
    x_planned:     float
    x_actual:      float
    a_planned_deg: float
    a_actual_deg:  float
    # Hız
    vx_planned:    float
    vx_actual:     float
    omega_planned: float   # [°/s]
    omega_actual:  float
    # Fiber
    tension_N:     float
    alpha_actual_deg: float
    alpha_error_deg:  float
    # Hata bileşenleri
    backlash_err_mm:   float = 0.0
    missed_step_err_mm:float = 0.0
    spindle_lag_deg:   float = 0.0
    feed_drift_deg:    float = 0.0
    payout_eye_mm:     float = 0.0   # Göz sapması
    is_turnaround:     bool  = False
    segment_index:     int   = 0

    @property
    def x_error(self) -> float:
        return self.x_actual - self.x_planned

    @property
    def a_error_deg(self) -> float:
        return self.a_actual_deg - self.a_planned_deg

    @property
    def position_error_mm(self) -> float:
        """Kombinat pozisyon hatası."""
        return math.sqrt(self.x_error**2 + (self.a_error_deg * math.pi/180.0 * 50)**2)


@dataclass(slots=True)
class SimulationResult:
    """Tam simülasyon sonucu — tüm segmentler."""
    states:          List[SimState]
    rms_x_error:     float
    rms_a_error_deg: float
    max_tension_N:   float
    min_tension_N:   float
    n_missed_steps_x:int
    n_missed_steps_a:int
    n_turnarounds:   int
    total_backlash_err:float
    feed_drift_final_deg:float
    eye_max_deviation_mm:float

    def report(self) -> str:
        return (
            "  ═" * 34 + "\n"
            "  MAKİNE SİMÜLASYON RAPORU\n"
            "  ─" * 34 + "\n"
            f"  Segment sayısı:      {len(self.states)}\n"
            f"  RMS X hatası:        {self.rms_x_error*1000:.3f} µm\n"
            f"  RMS A hatası:        {self.rms_a_error_deg:.4f}°\n"
            f"  Max fiber gerilme:   {self.max_tension_N:.2f} N\n"
            f"  Min fiber gerilme:   {self.min_tension_N:.2f} N\n"
            f"  Kaçan adım (X):      {self.n_missed_steps_x}\n"
            f"  Kaçan adım (A):      {self.n_missed_steps_a}\n"
            f"  Turn-around:         {self.n_turnarounds}\n"
            f"  Toplam backlash:     {self.total_backlash_err:.4f} mm\n"
            f"  Feed drift birikimi: {self.feed_drift_final_deg:.4f}°\n"
            f"  Göz maks sapması:    {self.eye_max_deviation_mm:.4f} mm\n"
            "  ═" * 34
        )

    def quality_score(self) -> float:
        """
        Üretim kalite skoru [0,1].
        Düşük hata → yüksek skor.
        """
        # Her metrik için normalize edilmiş ceza
        rms_x_tol  = 0.10  # mm — kabul edilebilir X RMS
        rms_a_tol  = 0.50  # ° — kabul edilebilir A RMS
        drift_tol  = 2.0   # ° — feed drift toleransı
        eye_tol    = 1.0   # mm — göz sapma toleransı

        s_x   = max(0.0, 1.0 - self.rms_x_error / rms_x_tol)
        s_a   = max(0.0, 1.0 - self.rms_a_error_deg / rms_a_tol)
        s_d   = max(0.0, 1.0 - abs(self.feed_drift_final_deg) / drift_tol)
        s_e   = max(0.0, 1.0 - self.eye_max_deviation_mm / eye_tol)
        # Tension skor: T_min > 0.5×T_nom ve T_max < 2×T_nom
        T_ok  = (self.min_tension_N > 5.0 and self.max_tension_N < 40.0)
        s_T   = 1.0 if T_ok else 0.5

        return 0.30*s_x + 0.25*s_a + 0.20*s_d + 0.15*s_e + 0.10*s_T


# ── Machine Simulation Engine ─────────────────────────────────────

class MachineSimulationEngine:
    """
    Segment-by-segment makine simülasyon motoru.

    Her MotionSegment için gerçek makine davranışını simüle eder.

    Kullanım:
        engine = MachineSimulationEngine(config)
        result = engine.simulate(motion_segments, alpha_rad=math.radians(10.17))
    """

    def __init__(
        self,
        config: Optional[SimulationConfig] = None,
        seed:   int = 42,
    ) -> None:
        self.cfg = config or SimulationConfig()
        self._rng = np.random.default_rng(seed)
        # Birikimli durum
        self._prev_vx        = 0.0
        self._prev_omega     = 0.0
        self._spindle_state  = 0.0   # φ_actual - φ_cmd [deg]
        self._feed_drift_cum = 0.0   # kümülatif feed drift [deg]
        self._missed_x       = 0
        self._missed_a       = 0
        self._backlash_cum   = 0.0
        # Payout eye ODE state
        self._eye_x  = 0.0   # [mm]
        self._eye_v  = 0.0   # [mm/s]

    def reset(self) -> None:
        """Yeni simülasyon başlangıcında çağır."""
        self._prev_vx = 0.0; self._prev_omega = 0.0
        self._spindle_state = 0.0; self._feed_drift_cum = 0.0
        self._missed_x = 0; self._missed_a = 0
        self._backlash_cum = 0.0
        self._eye_x = 0.0; self._eye_v = 0.0

    # ── Core simulation ──────────────────────────────────────────

    def simulate_segment(
        self,
        x_planned:     float,
        a_planned_deg: float,
        vx_cmd:        float,
        omega_cmd_dps: float,   # °/s
        alpha_rad:     float,
        dt:            float,
        seg_idx:       int,
    ) -> SimState:
        """Tek segment simülasyonu."""
        cfg = self.cfg

        # ── 1. Carriage acceleration model ──────────────────────
        tau_x     = cfg.tau_carriage
        vx_actual = vx_cmd * (1.0 - math.exp(-dt / max(tau_x, 1e-6)))
        x_acc_err = vx_cmd * tau_x * (1.0 - math.exp(-dt / max(tau_x, 1e-6))) - vx_actual * dt
        x_actual  = x_planned + x_acc_err

        # ── 2. Backlash ──────────────────────────────────────────
        bl_err = 0.0
        if self._prev_vx * vx_cmd < 0 and abs(vx_cmd) > 1e-3:
            bl_err = cfg.backlash_mm * math.copysign(1.0, vx_cmd)
            self._backlash_cum += abs(bl_err)
        x_actual += bl_err

        # ── 3. Missed steps (X) ──────────────────────────────────
        miss_err_x = 0.0
        v_ratio = abs(vx_cmd) / max(cfg.v_x_max_mm_s, 1.0)
        a_ratio = abs(vx_cmd - self._prev_vx) / dt / max(cfg.a_x_max_mm_s2 / 1000.0, 1.0) if dt > 0 else 0.0
        lam = (cfg.lambda0_per_s *
               max(0.0, v_ratio - cfg.v_crit_frac)**2 *
               max(0.0, a_ratio - cfg.a_crit_frac))
        n_miss = self._rng.poisson(lam * dt)
        if n_miss > 0:
            self._missed_x += n_miss
            miss_err_x = n_miss * cfg.step_res_x_mm * math.copysign(1.0, vx_cmd)
        x_actual += miss_err_x

        # ── 4. Spindle lag ───────────────────────────────────────
        tau_A    = cfg.tau_spindle
        lag_deg  = omega_cmd_dps * tau_A   # steady-state lag [°]
        # Geçici: üstel yaklaşma
        self._spindle_state = (self._spindle_state * math.exp(-dt / max(tau_A, 1e-6)) +
                               lag_deg * (1.0 - math.exp(-dt / max(tau_A, 1e-6))))
        a_actual_deg = a_planned_deg - self._spindle_state

        # ── 5. Feed sync drift ───────────────────────────────────
        drift_per_seg = self._spindle_state * dt / max(tau_A * 10.0, 0.1)
        self._feed_drift_cum += drift_per_seg
        a_actual_deg -= self._feed_drift_cum * 0.01   # küçük birikimli etki

        # ── 6. Missed steps (A) ──────────────────────────────────
        lam_a = cfg.lambda0_per_s * max(0.0, v_ratio - cfg.v_crit_frac)**2
        n_miss_a = self._rng.poisson(lam_a * dt)
        if n_miss_a > 0:
            self._missed_a += n_miss_a
            a_actual_deg += n_miss_a * cfg.step_res_a_deg * math.copysign(1.0, omega_cmd_dps)

        # ── 7. Fiber tension ─────────────────────────────────────
        dv_x = vx_cmd - self._prev_vx
        a_carriage = dv_x / max(dt, 1e-6)
        v_payout  = abs(vx_cmd) / math.cos(alpha_rad) if abs(math.cos(alpha_rad)) > 1e-6 else 0.0
        k_fiber   = cfg.k_fiber_N_per_mm
        rho_fiber = 2500.0   # kg/m³ (cam elyaf yoğunluğu)
        A_m2      = math.pi * (cfg.fiber_diameter_mm / 2.0 / 1000.0)**2
        m_pay     = rho_fiber * A_m2 * cfg.L_payout_mm / 1000.0
        T = (cfg.T_nominal_N +
             k_fiber * abs(dv_x) * 0.001 +
             abs(m_pay * a_carriage / 1000.0))
        T = max(0.0, T)

        # ── 8. Payout eye ODE ────────────────────────────────────
        F_fiber_N = T * math.cos(alpha_rad)   # X bileşeni [N]
        F_eye_N   = F_fiber_N / (cfg.k_eye_N_m / 1000.0)  # mm cinsine çevir
        # Basit Euler: ẍ = (F - b·ẋ - k·x) / m
        m_e = cfg.m_eye_kg; b_e = cfg.b_eye_Ns_m; k_e = cfg.k_eye_N_m
        a_eye = (F_eye_N/1000.0*k_e - b_e*self._eye_v/1000.0 - k_e*self._eye_x/1000.0) / m_e
        self._eye_v = max(-500.0, min(500.0, self._eye_v + a_eye * dt))
        self._eye_x = max(-50.0, min(50.0, self._eye_x + self._eye_v * dt * 0.001))

        # ── 9. Actual alpha ──────────────────────────────────────
        x_err_mm = x_actual - x_planned
        # Δα = -ΔX·tan(α)/R (small angle perturbation)
        R_mm = 50.0
        delta_alpha_rad = -x_err_mm * math.tan(alpha_rad) / R_mm if R_mm > 0 else 0.0
        alpha_actual    = alpha_rad + delta_alpha_rad
        alpha_err_deg   = math.degrees(delta_alpha_rad)

        # Turn-around detection: hız sıfır geçişi VEYA x_planned yön değişimi
        is_ta = (self._prev_vx * vx_cmd < 0 and abs(self._prev_vx) > 1.0)

        # Update state
        self._prev_vx    = vx_cmd
        self._prev_omega = omega_cmd_dps

        return SimState(
            x_planned         = x_planned,
            x_actual          = x_actual,
            a_planned_deg     = a_planned_deg,
            a_actual_deg      = a_actual_deg,
            vx_planned        = vx_cmd,
            vx_actual         = vx_actual,
            omega_planned     = omega_cmd_dps,
            omega_actual      = omega_cmd_dps * (1 - self._spindle_state / max(lag_deg, 0.001)),
            tension_N         = T,
            alpha_actual_deg  = math.degrees(alpha_actual),
            alpha_error_deg   = alpha_err_deg,
            backlash_err_mm   = bl_err,
            missed_step_err_mm= miss_err_x,
            spindle_lag_deg   = self._spindle_state,
            feed_drift_deg    = self._feed_drift_cum,
            payout_eye_mm     = self._eye_x,
            is_turnaround     = is_ta,
            segment_index     = seg_idx,
        )

    def simulate(
        self,
        x_arr:      np.ndarray,
        a_arr:      np.ndarray,   # [°] kümülatif
        f_arr:      np.ndarray,   # [mm/min] feedrate
        alpha_rad:  float,
        mandrel_R:  float = 50.0,
    ) -> SimulationResult:
        """
        Tüm hareket profilini simüle et.

        Args:
            x_arr: X pozisyonları [mm]
            a_arr: A pozisyonları [°] kümülatif
            f_arr: Feedrate [mm/min]
            alpha_rad: Sarım açısı [rad]
        """
        self.reset()
        n = len(x_arr)
        states: List[SimState] = []

        for i in range(n):
            if i == 0:
                dt = 0.0; vx_cmd = 0.0; omega_cmd = 0.0
            else:
                dx  = x_arr[i] - x_arr[i-1]
                da  = a_arr[i] - a_arr[i-1]
                f   = f_arr[i]
                vx_cmd = f / 60.0  # mm/min → mm/s
                dt  = abs(dx) / max(abs(vx_cmd), 1e-6)
                # ω from A change
                omega_cmd = da / max(dt, 1e-6)  # °/s

            st = self.simulate_segment(
                x_planned     = x_arr[i],
                a_planned_deg = a_arr[i],
                vx_cmd        = vx_cmd,
                omega_cmd_dps = omega_cmd,
                alpha_rad     = alpha_rad,
                dt            = dt,
                seg_idx       = i,
            )
            states.append(st)

        # Aggregate metrics
        x_errors  = np.array([s.x_error for s in states])
        a_errors  = np.array([s.a_error_deg for s in states])
        tensions  = np.array([s.tension_N for s in states])
        eyes      = np.array([abs(s.payout_eye_mm) for s in states])
        turns     = sum(1 for s in states if s.is_turnaround)

        return SimulationResult(
            states               = states,
            rms_x_error          = float(np.sqrt(np.mean(x_errors**2))),
            rms_a_error_deg      = float(np.sqrt(np.mean(a_errors**2))),
            max_tension_N        = float(tensions.max()),
            min_tension_N        = float(tensions.min()),
            n_missed_steps_x     = self._missed_x,
            n_missed_steps_a     = self._missed_a,
            n_turnarounds        = turns,
            total_backlash_err   = self._backlash_cum,
            feed_drift_final_deg = self._feed_drift_cum,
            eye_max_deviation_mm = float(eyes.max()),
        )


# ── Collision Analyzer ───────────────────────────────────────────

@dataclass(slots=True)
class CollisionEvent:
    """Tek çarpışma olayı."""
    segment_index: int
    z_mm:         float
    a_deg:        float
    description:  str
    severity:     str   # "warning" | "critical"


class CollisionAnalyzer:
    """
    Toolpath çarpışma ve aşım analizi.

    Kontrol edilen durumlar:
      1. X aşımı: z > z_total veya z < 0
      2. A hızı aşımı: ω > ω_max
      3. Polar boss geçişi: r(z) < r_boss
      4. Headstock çarpışması: z < z_headstock_clearance
    """

    def __init__(
        self,
        z_total:      float,
        mandrel       = None,
        z_headstock:  float = -10.0,
        r_boss_mm:    float = 33.42,
        a_max_dps:    float = 1500.0
    ) -> None:
        self.z_max       = z_total
        self.z_min       = z_headstock
        self.r_boss      = r_boss_mm
        self.a_max_dps   = a_max_dps
        self.mandrel     = mandrel

    def analyze(
        self,
        x_arr: np.ndarray,
        a_arr: np.ndarray,
        f_arr: np.ndarray,
    ) -> List[CollisionEvent]:
        """Tüm toolpath'i tara."""
        events: List[CollisionEvent] = []
        n = len(x_arr)

        for i in range(n):
            z = x_arr[i]; a = a_arr[i]; f = f_arr[i]

            # X overtravel
            if z < self.z_min:
                events.append(CollisionEvent(i, z, a,
                    f"X={z:.2f}mm < headstock limit {self.z_min}mm",
                    "critical"))
            if z > self.z_max:
                events.append(CollisionEvent(i, z, a,
                    f"X={z:.2f}mm > z_total {self.z_max:.2f}mm",
                    "critical"))

            # A speed
            if i > 0 and f_arr[i-1] > 0:
                dx = abs(x_arr[i]-x_arr[i-1])
                if dx > 1e-6:
                    vx = f/60.0
                    dt = dx/max(vx, 1e-6)
                    da = abs(a_arr[i]-a_arr[i-1])
                    omega = da/max(dt, 1e-6)
                    if omega > self.a_max_dps * 1.05:
                        events.append(CollisionEvent(i, z, a,
                            f"A speed {omega:.1f}°/s > limit {self.a_max_dps:.0f}°/s",
                            "warning"))

            # Polar boss check
            r = self.mandrel.r(z) if hasattr(self.mandrel, 'r') else 50.0
            if r < self.r_boss - 1.0:
                events.append(CollisionEvent(i, z, a,
                    f"r(z={z:.1f})={r:.1f}mm < r_boss={self.r_boss:.1f}mm",
                    "warning"))

        return events

    def report(self, events: List[CollisionEvent]) -> str:
        if not events:
            return "  ✓ Çarpışma/aşım: YOK"
        crit = [e for e in events if e.severity=="critical"]
        warn = [e for e in events if e.severity=="warning"]
        lines = [f"  Çarpışma analizi: {len(crit)} kritik, {len(warn)} uyarı"]
        for e in events[:8]:
            lines.append(f"  [{e.severity.upper():<8}] seg={e.segment_index:4d} "
                        f"z={e.z_mm:7.2f}mm  {e.description}")
        if len(events) > 8:
            lines.append(f"  ... ve {len(events)-8} daha")
        return "\n".join(lines)
