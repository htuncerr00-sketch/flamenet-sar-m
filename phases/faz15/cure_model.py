"""
cure_model.py — Composite Cure Kinetics + Heat Transfer
=========================================================
Fizik modeli: Kamal autocatalytic + Arrhenius + ısı dengesi

Kamal Modeli:
  dα/dt = (k₁(T) + k₂(T)·αᵐ) · (1-α)ⁿ
  k_i(T) = A_i · exp(-Ea_i / (R·T))   [Arrhenius]

  α  : cure degree [0=uncured, 1=fully cured]
  m,n: reaction order (tipik: m=0.8, n=1.6)
  A_i: pre-exponential factor [1/s]
  Ea_i: activation energy [J/mol]
  R  = 8.314 J/(mol·K)

Isı dengesi (1D FD, composite plaka):
  ρ·Cp·∂T/∂t = k_th·∂²T/∂x² + ρ·H_r·dα/dt

  H_r: toplam reaksiyon ısısı [J/kg]
  k_th: termal iletkenlik [W/(m·K)]
  ρ: yoğunluk [kg/m³]

Gelasyon noktası (αg): α > αg → viskozite → ∞ → akış durur
Vitrifikasyon (Tg):   Tg artar α ile → proses penceresi kapanır

Referans: Kamal & Sourour (1976) Polym. Eng. Sci.;
          Lee & Lee (1994) JCompMat cure model survey.
"""
from __future__ import annotations
import math, time, threading
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import numpy as np

R_GAS = 8.314   # J/(mol·K)

# ── Resin System Parameters ───────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ResinSystem:
    """Karakterize edilmiş reçine sistemi parametreleri."""
    name:     str
    # Kamal parameters
    A1:       float   # [1/s] pre-exponential k1
    A2:       float   # [1/s] pre-exponential k2
    Ea1:      float   # [J/mol] activation energy k1
    Ea2:      float   # [J/mol] activation energy k2
    m:        float   # reaction order m
    n:        float   # reaction order n
    # Thermal
    H_rxn:    float   # [J/kg] total heat of reaction
    rho:      float   # [kg/m³] density
    Cp:       float   # [J/(kg·K)] specific heat
    k_th:     float   # [W/(m·K)] thermal conductivity
    # Gelation / vitrification
    alpha_gel:float   # gelation degree (~0.60)
    Tg_0:     float   # [°C] Tg at α=0 (uncured)
    Tg_inf:   float   # [°C] Tg at α=1 (fully cured)
    # Safety
    T_max_safe:float  # [°C] decomposition onset
    T_process: float  # [°C] nominal cure temperature

# EPOXY/CARBON typical (Hexion EPON 828 + Ancamine 2049)
EPOXY_SYSTEM = ResinSystem(
    name       = "Epoxy_EPON828",
    A1         = 1.2e5, A2 = 3.8e7,
    Ea1        = 55_000.0, Ea2 = 72_000.0,
    m=0.85, n=1.60,
    H_rxn      = 420_000.0,    # J/kg
    rho        = 1250.0,       # kg/m³
    Cp         = 1500.0,       # J/(kg·K)
    k_th       = 0.20,         # W/(m·K) [neat resin]
    alpha_gel  = 0.62,
    Tg_0       = -20.0,        # °C uncured
    Tg_inf     = 180.0,        # °C fully cured
    T_max_safe = 250.0,        # °C thermal decomp onset
    T_process  = 120.0,        # °C nominal isothermal
)

# VINYL ESTER (İçme suyu basınçlı kapları)
VINYLESTER_SYSTEM = ResinSystem(
    name       = "VinylEster_Derakane510",
    A1         = 8.5e4, A2 = 2.1e7,
    Ea1        = 52_000.0, Ea2 = 68_000.0,
    m=0.76, n=1.45,
    H_rxn      = 370_000.0,
    rho        = 1200.0,
    Cp         = 1400.0,
    k_th       = 0.18,
    alpha_gel  = 0.58,
    Tg_0       = -25.0,
    Tg_inf     = 150.0,
    T_max_safe = 220.0,
    T_process  = 90.0,
)


# ── Cure State ────────────────────────────────────────────────────

@dataclass(slots=True)
class CureState:
    alpha:     float = 0.0       # Cure degree [0,1]
    dadt:      float = 0.0       # Cure rate [1/s]
    T_K:       float = 298.15    # Temperature [K]
    T_exo_K:   float = 0.0       # Exotherm contribution [K]
    Tg_C:      float = -20.0     # Glass transition temp [°C]
    gelated:   bool  = False
    vitrified: bool  = False
    t_s:       float = 0.0       # Process time [s]

    @property
    def T_C(self) -> float: return self.T_K - 273.15

    @property
    def pct(self) -> float: return self.alpha * 100.0


# ── Kamal Cure Model ──────────────────────────────────────────────

class KamalCureModel:
    """
    Kamal autocatalytic cure kinetics with Arrhenius temperature dependence.
    Thread-safe: immutable parameters + stateless compute methods.

    dα/dt = (k1(T) + k2(T)·αᵐ)·(1-α)ⁿ
    """

    def __init__(self, resin: ResinSystem = None):
        self.r = resin or EPOXY_SYSTEM
        # DiBenedetto Tg model: Tg(α) = Tg0 + (Tginf-Tg0)·λα/(1-(1-λ)α)
        self._lambda_db = 0.38   # DiBenedetto λ

    def k1(self, T_K: float) -> float:
        """Arrhenius rate constant k1 [1/s]."""
        return self.r.A1 * math.exp(-self.r.Ea1 / (R_GAS * max(T_K, 200.0)))

    def k2(self, T_K: float) -> float:
        """Arrhenius rate constant k2 [1/s]."""
        return self.r.A2 * math.exp(-self.r.Ea2 / (R_GAS * max(T_K, 200.0)))

    def dadt(self, alpha: float, T_K: float) -> float:
        """
        Kamal cure rate [1/s].
        alpha ∈ [0, α_gel]: returns 0 if gelated (flow stop).
        """
        alpha = float(np.clip(alpha, 0.0, 0.9999))
        if alpha >= self.r.alpha_gel:
            return 0.0   # Gelated → no further flow, but cure may continue
        k1v = self.k1(T_K); k2v = self.k2(T_K)
        rate = (k1v + k2v * alpha**self.r.m) * (1.0 - alpha)**self.r.n
        return max(0.0, rate)

    def dadt_post_gel(self, alpha: float, T_K: float) -> float:
        """Cure rate after gelation (structural development, no flow)."""
        alpha = float(np.clip(alpha, self.r.alpha_gel, 0.9999))
        k1v = self.k1(T_K); k2v = self.k2(T_K)
        return max(0.0, (k1v + k2v * alpha**self.r.m) * (1.0 - alpha)**self.r.n)

    def Tg(self, alpha: float) -> float:
        """DiBenedetto glass transition temperature [°C]."""
        lam = self._lambda_db
        a   = float(np.clip(alpha, 0.0, 1.0))
        denom = 1.0 - (1.0 - lam) * a
        return (self.r.Tg_0 +
                (self.r.Tg_inf - self.r.Tg_0) * lam * a / max(denom, 1e-6))

    def exotherm_power(self, alpha: float, T_K: float) -> float:
        """Heat generation rate [W/kg] = H_rxn × dα/dt."""
        rate = self.dadt(alpha, T_K) + self.dadt_post_gel(alpha, T_K)
        return self.r.H_rxn * rate

    def integrate(
        self,
        T_profile: np.ndarray,   # [K] — temperature vs time
        dt_s:      float = 1.0,  # Time step [s]
        alpha0:    float = 0.0,
        rng:       Optional[np.random.Generator] = None,
        noise_std: float = 0.001,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        RK4 integration of Kamal model.
        Returns (alpha_arr, dadt_arr).
        Optional measurement noise for Monte Carlo.
        """
        n = len(T_profile)
        alpha_arr = np.zeros(n); dadt_arr = np.zeros(n)
        alpha = float(np.clip(alpha0, 0.0, 0.9999))

        for i, T_K in enumerate(T_profile):
            # RK4
            f  = lambda a: (self.dadt(a, T_K) + self.dadt_post_gel(a, T_K)
                            if a >= self.r.alpha_gel else self.dadt(a, T_K))
            k1 = f(alpha)
            k2 = f(alpha + 0.5*dt_s*k1)
            k3 = f(alpha + 0.5*dt_s*k2)
            k4 = f(alpha + dt_s*k3)
            dalpha = dt_s * (k1 + 2*k2 + 2*k3 + k4) / 6.0

            if rng is not None and noise_std > 0:
                dalpha += float(rng.normal(0, noise_std * dt_s))

            alpha = float(np.clip(alpha + dalpha, 0.0, 1.0))
            alpha_arr[i] = alpha
            dadt_arr[i]  = k1
        return alpha_arr, dadt_arr

    def gelation_time(self, T_K: float) -> float:
        """Time to gelation at isothermal T [s]. Numerical estimate."""
        alpha, dt = 0.0, 0.5
        for t in range(100_000):
            rate = self.dadt(alpha, T_K)
            if rate <= 0: return float(t * dt)
            alpha += rate * dt
            if alpha >= self.r.alpha_gel: return float(t * dt)
        return float("inf")


# ── 1D Heat Transfer ──────────────────────────────────────────────

class HeatTransferModel:
    """
    Simplified 1D FD heat transfer through laminate thickness.
    ρ·Cp·∂T/∂t = k·∂²T/∂x² + ρ·H_r·dα/dt

    N layers, explicit FD (stability: dt ≤ dx²·ρ·Cp/(2k))
    """

    def __init__(self, resin: ResinSystem, n_layers: int = 5,
                 thickness_mm: float = 5.0):
        self.r   = resin
        self.N   = n_layers
        self.dx  = thickness_mm / 1000.0 / n_layers   # [m]
        # Stability criterion
        self.dt_max = (self.dx**2 * resin.rho * resin.Cp) / (2 * resin.k_th)
        self._T = None   # Temperature field [K]

    def initialize(self, T_K: float) -> None:
        self._T = np.full(self.N, T_K)

    def step(self, dt_s: float, alpha_arr: np.ndarray,
             T_oven_K: float, h_conv: float = 15.0) -> np.ndarray:
        """
        One explicit FD step.
        Boundary: convective BC at both surfaces.
        Returns T_field [K].
        """
        if self._T is None:
            self._T = np.full(self.N, T_oven_K)

        # Stability check (use sub-stepping if needed)
        n_sub = max(1, int(dt_s / self.dt_max) + 1)
        dt_sub = dt_s / n_sub
        T = self._T.copy()

        for _ in range(n_sub):
            T_new = T.copy()
            for i in range(1, self.N-1):
                diff   = self.r.k_th * (T[i+1] - 2*T[i] + T[i-1]) / self.dx**2
                exo    = self.r.rho * self.r.H_rxn * max(0.0, alpha_arr[i] if i<len(alpha_arr) else 0.0)
                T_new[i] = T[i] + dt_sub / (self.r.rho * self.r.Cp) * (diff + exo)
            # Convective BC
            T_new[0]    = T[0] + dt_sub/(self.r.rho*self.r.Cp) * (
                h_conv*(T_oven_K-T[0])/self.dx + self.r.k_th*(T[1]-T[0])/self.dx**2)
            T_new[-1]   = T_new[-2]   # Insulated (mandrel side)
            T = np.clip(T_new, 200.0, 800.0)

        self._T = T
        return T

    @property
    def T_field(self) -> Optional[np.ndarray]: return self._T

    @property
    def T_center_K(self) -> float:
        if self._T is None: return 298.15
        return float(self._T[self.N//2])

    @property
    def T_max_K(self) -> float:
        if self._T is None: return 298.15
        return float(self._T.max())

    @property
    def T_gradient_K_m(self) -> float:
        """Through-thickness temperature gradient [K/m]."""
        if self._T is None or self.N < 2: return 0.0
        return float((self._T[-1] - self._T[0]) / (self.N * self.dx))
