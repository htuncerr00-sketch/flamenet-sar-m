"""
state_estimator.py — Makine Durum Tahmincisi (Kalman Filtresi)
==============================================================
6-boyutlu durum vektörü: x = [z, v_z, φ, ω, T, T_dot]ᵀ

Fiziksel anlamlar:
  z     [mm]   : Carriage eksenel pozisyon
  v_z   [mm/s] : Carriage hızı
  φ     [°]    : Spindle açısı (kümülatif)
  ω     [°/s]  : Spindle açısal hızı
  T     [N]    : Fiber gerilmesi
  T_dot [N/s]  : Gerilme değişim hızı

Lineer Kalman filtresi (discrete-time):

  Predict:
    x̂[k|k-1] = F·x̂[k-1|k-1] + B·u[k]
    P[k|k-1]  = F·P[k-1|k-1]·Fᵀ + Q

  Update:
    K         = P[k|k-1]·Hᵀ·(H·P[k|k-1]·Hᵀ + R)⁻¹
    x̂[k|k]   = x̂[k|k-1] + K·(y[k] - H·x̂[k|k-1])
    P[k|k]    = (I - K·H)·P[k|k-1]

Proses matrisi F (dt bağımlı, tau_A makineden):
  F = [[1, dt, 0,  0,  0,  0  ],   z   → z + v_z·dt
       [0,  1, 0,  0,  0,  0  ],   v_z → v_z (acceleration via B·u)
       [0,  0, 1, dt,  0,  0  ],   φ   → φ + ω·dt
       [0,  0, 0, 1-dt/τ_A, 0, 0], ω   → ω·(1-dt/τ_A)
       [0,  0, 0,  0,  1, dt  ],   T   → T + T_dot·dt
       [0,  0, 0,  0, -k_T, 1-b_T·dt]]  T_dot → damped

Input matrix B (kontrol girdisi):
  Sütun 0: u_x (carriage force) → v_z
  Sütun 1: u_A (spindle torque) → ω via K_A/τ_A

Gözlem matrisi H:
  Encoder X      → z          (H[0,0]=1)
  Encoder A      → φ          (H[1,2]=1)
  Load cell      → T          (H[2,4]=1)
  RPM sensor     → ω→rpm·6   (H[3,3]=1/6, rpm→deg/s)

Gürültü kovaryansları:
  Q: Proses gürültüsü (motor torque ripple, fiber irregularity)
  R: Ölçüm gürültüsü (encoder quantization, load cell noise)

İnovasyon izleme (χ² testi):
  ν = y - H·x̂[k|k-1]  (innovation)
  S = H·P·Hᵀ + R       (innovation covariance)
  Mahalanobis: d² = νᵀ·S⁻¹·ν  → anomali tespiti için

Observer-based spindle estimation:
  A-ekseni encoder yoksa: φ ve ω, G-code komutlarından + τ_A modelinden tahmin.
  Bu "reduced-order observer" pattern'i faulted sensor durumunda devreye girer.

Referans:
  Kalman (1960) IEEE Trans. ASME;
  Simon (2006) Optimal State Estimation, Ch.5;
  ADA268923 App.K: machine dynamics parameters
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np


# ── State & Measurement ─────────────────────────────────────────

# State indeks sabitleri
IDX_Z, IDX_VZ, IDX_PHI, IDX_OMG, IDX_T, IDX_TDOT = 0, 1, 2, 3, 4, 5
N_STATES = 6

# Gözlem indeksleri
OBS_X, OBS_PHI, OBS_T, OBS_RPM = 0, 1, 2, 3
N_OBS = 4


@dataclass(frozen=True, slots=True)
class StateEstimate:
    """Kalman filtresi durum tahmini."""
    z_mm:        float
    vz_mm_s:     float
    phi_deg:     float
    omega_dps:   float   # °/s
    tension_N:   float
    tdot_N_s:    float

    # Belirsizlik (diagonal of P)
    sigma_z:     float
    sigma_phi:   float
    sigma_T:     float

    # Kalite
    innovation_norm: float  # Mahalanobis distance
    is_valid:    bool

    @property
    def rpm(self) -> float:
        return self.omega_dps / 6.0   # °/s → RPM

    def array(self) -> np.ndarray:
        return np.array([self.z_mm, self.vz_mm_s, self.phi_deg,
                         self.omega_dps, self.tension_N, self.tdot_N_s])

    def summary(self) -> str:
        return (
            f"z={self.z_mm:.3f}mm  v={self.vz_mm_s:.2f}mm/s  "
            f"φ={self.phi_deg:.3f}°  ω={self.omega_dps:.2f}°/s  "
            f"T={self.tension_N:.3f}N  "
            f"σ_z={self.sigma_z:.4f}  σ_φ={self.sigma_phi:.4f}  "
            f"d={self.innovation_norm:.3f}  "
            f"{'OK' if self.is_valid else 'ANOM'}"
        )


@dataclass(frozen=True, slots=True)
class SensorMeasurement:
    """Tek zaman adımı sensör ölçümü."""
    encoder_x_mm:    Optional[float]   # X encoder [mm]
    encoder_phi_deg: Optional[float]   # A encoder [°]
    tension_N:       Optional[float]   # Load cell [N]
    rpm:             Optional[float]   # Hall/encoder RPM
    timestamp:       float             # Unix timestamp

    def to_y_vector(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Ölçüm vektörü y ve geçerlilik maskesi.

        Returns:
            (y, mask)  mask[i]=True → bu ölçüm geçerli
        """
        y    = np.zeros(N_OBS)
        mask = np.zeros(N_OBS, dtype=bool)

        if self.encoder_x_mm is not None:
            y[OBS_X]   = self.encoder_x_mm;   mask[OBS_X]   = True
        if self.encoder_phi_deg is not None:
            y[OBS_PHI] = self.encoder_phi_deg; mask[OBS_PHI] = True
        if self.tension_N is not None:
            y[OBS_T]   = self.tension_N;       mask[OBS_T]   = True
        if self.rpm is not None:
            y[OBS_RPM] = self.rpm * 6.0;       mask[OBS_RPM] = True  # RPM→°/s

        return y, mask


# ── Kalman Config ────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class KalmanConfig:
    """Kalman filtresi ve observer parametreleri."""
    dt:           float = 0.010     # [s] — örnekleme süresi
    tau_A_s:      float = 6.0      # Spindle zaman sabiti [s]
    m_carriage_kg:float = 11.33    # Carriage kütlesi [kg]
    K_A:          float = 1.0      # Spindle giriş kazancı (normalized)
    k_fiber:      float = 0.035    # Fiber yay sabiti [N/mm]
    b_tension:    float = 2.0      # Gerilme sönüm [1/s]
    # Proses gürültüsü (Q diagonal)
    q_z:          float = 0.01     # mm² — pozisyon gürültüsü
    q_vz:         float = 1.0      # (mm/s)² — hız gürültüsü
    q_phi:        float = 0.1      # deg² — açı gürültüsü
    q_omega:      float = 10.0     # (°/s)² — açısal hız gürültüsü
    q_T:          float = 0.5      # N² — gerilme gürültüsü
    q_Tdot:       float = 5.0      # (N/s)² — gerilme hız gürültüsü
    # Ölçüm gürültüsü (R diagonal)
    r_x:          float = 0.001    # mm² — encoder X
    r_phi:        float = 0.01     # deg² — encoder A
    r_T:          float = 0.1      # N² — load cell
    r_rpm:        float = 1.0      # (°/s)² — rpm sensor
    # Anomali tespiti
    chi2_threshold:float = 12.0   # χ² eşiği (4 dof, p<0.02)


# ── Kalman Filter ────────────────────────────────────────────────

class KalmanStateEstimator:
    """
    6-boyutlu makine durum Kalman filtresi.

    Kullanım:
        est = KalmanStateEstimator(config)
        est.initialize(z0=40.0, phi0=0.0)
        for meas in measurements:
            state = est.update(meas, u_x=vx_cmd, u_A=omega_cmd)
            print(state.summary())
    """

    def __init__(self, config: KalmanConfig = None) -> None:
        self.cfg  = config or KalmanConfig()
        self._x   = np.zeros(N_STATES)
        self._P   = np.eye(N_STATES) * 100.0   # Large initial uncertainty
        self._F   = self._build_F()
        self._B   = self._build_B()
        self._H   = self._build_H()
        self._Q   = self._build_Q()
        self._R   = self._build_R()
        self._step = 0
        self._innovations: List[float] = []

    # ── Initialization ───────────────────────────────────────────

    def initialize(
        self,
        z_mm:      float = 0.0,
        vz_mm_s:   float = 0.0,
        phi_deg:   float = 0.0,
        omega_dps: float = 0.0,
        tension_N: float = 15.0,
    ) -> None:
        """İlk durum tahmini."""
        self._x = np.array([z_mm, vz_mm_s, phi_deg, omega_dps, tension_N, 0.0])
        self._P = np.diag([1.0, 5.0, 1.0, 25.0, 1.0, 10.0])
        self._step = 0

    # ── Main update step ─────────────────────────────────────────

    def update(
        self,
        measurement: SensorMeasurement,
        u_x:        float = 0.0,   # Carriage komutu (feedrate mm/s)
        u_A:        float = 0.0,   # Spindle komutu (°/s hedef)
    ) -> StateEstimate:
        """
        Predict + Update adımı.

        Args:
            measurement: Sensör ölçümleri
            u_x: Carriage komut hızı [mm/s]
            u_A: Spindle komut açısal hızı [°/s]

        Returns:
            StateEstimate — posterior tahmin
        """
        u = np.array([u_x, u_A])

        # ── Predict ─────────────────────────────────────────────
        x_pred = self._F @ self._x + self._B @ u
        P_pred = self._F @ self._P @ self._F.T + self._Q

        # ── Partial observation (missing sensors handled) ────────
        y, mask = measurement.to_y_vector()

        # Geçerli ölçümleri filtrele
        valid_idx = np.where(mask)[0]

        if len(valid_idx) == 0:
            # Hiç ölçüm yok — sadece predict
            self._x = x_pred
            self._P = P_pred
            innov_norm = 0.0
        else:
            H_eff = self._H[valid_idx, :]
            R_eff = self._R[np.ix_(valid_idx, valid_idx)]
            y_eff = y[valid_idx]

            # Innovation
            innov   = y_eff - H_eff @ x_pred
            S       = H_eff @ P_pred @ H_eff.T + R_eff

            # Kalman gain
            try:
                K = P_pred @ H_eff.T @ np.linalg.inv(S)
            except np.linalg.LinAlgError:
                K = np.zeros((N_STATES, len(valid_idx)))

            # Posterior
            self._x = x_pred + K @ innov
            I_KH    = np.eye(N_STATES) - K @ H_eff
            self._P = I_KH @ P_pred @ I_KH.T + K @ R_eff @ K.T  # Joseph form

            # Mahalanobis distance
            try:
                innov_norm = float(innov.T @ np.linalg.solve(S, innov))
            except np.linalg.LinAlgError:
                innov_norm = 0.0
            self._innovations.append(innov_norm)

        # ── Gerilme fiziksel sınırlar ────────────────────────────
        self._x[IDX_T]    = max(0.0, self._x[IDX_T])
        self._x[IDX_TDOT] = np.clip(self._x[IDX_TDOT], -200.0, 200.0)

        # ── Anomali tespiti ──────────────────────────────────────
        is_valid = (
            len(valid_idx) == 0 or
            innov_norm < self.cfg.chi2_threshold
        )

        self._step += 1
        P_diag = np.diag(self._P)

        return StateEstimate(
            z_mm         = float(self._x[IDX_Z]),
            vz_mm_s      = float(self._x[IDX_VZ]),
            phi_deg      = float(self._x[IDX_PHI]),
            omega_dps    = float(self._x[IDX_OMG]),
            tension_N    = float(self._x[IDX_T]),
            tdot_N_s     = float(self._x[IDX_TDOT]),
            sigma_z      = float(np.sqrt(max(0, P_diag[IDX_Z]))),
            sigma_phi    = float(np.sqrt(max(0, P_diag[IDX_PHI]))),
            sigma_T      = float(np.sqrt(max(0, P_diag[IDX_T]))),
            innovation_norm = innov_norm if len(valid_idx) > 0 else 0.0,
            is_valid     = is_valid,
        )

    # ── Observer-based spindle (reduced-order) ───────────────────

    def estimate_spindle_without_encoder(
        self,
        u_A_cmd: float,    # [°/s] komut
        dt:      float,    # [s]
    ) -> Tuple[float, float]:
        """
        A-ekseni encoder olmadan φ ve ω tahmini.

        Luenberger observer:
          ω̂[k+1] = (1 - dt/τ_A)·ω̂[k] + K_A·u_A·dt/τ_A
          φ̂[k+1] = φ̂[k] + ω̂·dt

        Returns: (phi_est_deg, omega_est_dps)
        """
        tau_A = self.cfg.tau_A_s
        omega_new = (self._x[IDX_OMG] * (1.0 - dt/tau_A) +
                     self.cfg.K_A * u_A_cmd * dt / tau_A)
        phi_new   = self._x[IDX_PHI] + omega_new * dt
        self._x[IDX_OMG] = omega_new
        self._x[IDX_PHI] = phi_new
        return float(phi_new), float(omega_new)

    # ── Covariance adaptation ────────────────────────────────────

    def adapt_noise(self, innovation_window: int = 50) -> None:
        """
        Son N innovation'dan Q güncelleme (adaptive Kalman).

        Eğer innovation sürekli büyükse → proses gürültüsü artır.
        """
        if len(self._innovations) < innovation_window:
            return
        recent = np.array(self._innovations[-innovation_window:])
        mean_inn = float(np.mean(recent))
        if mean_inn > self.cfg.chi2_threshold * 1.5:
            # Sistemik hata → Q'yu artır
            self._Q *= 1.05
        elif mean_inn < self.cfg.chi2_threshold * 0.3:
            # Çok küçük → Q'yu azalt (daha güvenilir proses)
            self._Q *= 0.98
        self._Q = np.clip(self._Q, 1e-9, 1e6)

    @property
    def current_state(self) -> np.ndarray:
        return self._x.copy()

    @property
    def covariance(self) -> np.ndarray:
        return self._P.copy()

    # ── Matrix builders ──────────────────────────────────────────

    def _build_F(self) -> np.ndarray:
        """State transition matrix F (6×6)."""
        dt    = self.cfg.dt
        tau_A = self.cfg.tau_A_s
        b_T   = self.cfg.b_tension

        F = np.eye(N_STATES)
        F[IDX_Z,   IDX_VZ]  = dt          # z += vz·dt
        F[IDX_PHI, IDX_OMG] = dt          # φ += ω·dt
        F[IDX_OMG, IDX_OMG] = 1.0 - dt/tau_A  # ω lag
        F[IDX_T,   IDX_TDOT]= dt          # T += T_dot·dt
        F[IDX_TDOT,IDX_TDOT]= max(0.0, 1.0 - b_T*dt)  # T_dot damping
        return F

    def _build_B(self) -> np.ndarray:
        """Input matrix B (6×2): [u_x, u_A]."""
        dt    = self.cfg.dt
        tau_A = self.cfg.tau_A_s
        m     = self.cfg.m_carriage_kg
        K_A   = self.cfg.K_A

        B = np.zeros((N_STATES, 2))
        B[IDX_VZ,  0] = dt / m       # u_x → v_z (simplified: F=m·a → a=u_x/m)
        B[IDX_OMG, 1] = K_A * dt / tau_A  # u_A → ω
        return B

    def _build_H(self) -> np.ndarray:
        """Observation matrix H (4×6)."""
        H = np.zeros((N_OBS, N_STATES))
        H[OBS_X,   IDX_Z]   = 1.0    # Encoder X → z
        H[OBS_PHI, IDX_PHI] = 1.0    # Encoder A → φ
        H[OBS_T,   IDX_T]   = 1.0    # Load cell → T
        H[OBS_RPM, IDX_OMG] = 1.0    # RPM → ω (already in °/s)
        return H

    def _build_Q(self) -> np.ndarray:
        """Process noise covariance Q (6×6)."""
        c = self.cfg
        return np.diag([c.q_z, c.q_vz, c.q_phi, c.q_omega, c.q_T, c.q_Tdot])

    def _build_R(self) -> np.ndarray:
        """Measurement noise covariance R (4×4)."""
        c = self.cfg
        return np.diag([c.r_x, c.r_phi, c.r_T, c.r_rpm])
