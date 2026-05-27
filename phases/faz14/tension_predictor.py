"""
tension_predictor.py — EWMA+Kalman Tension Predictor + CUSUM Defect Detector
=============================================================================
İki sistem tek modülde:

1. TensionPredictor: t+100ms gerilme tahmini
   EWMA trend: T_trend(t) = α·T(t) + (1-α)·T_trend(t-1)
   Kalman öngörü: T_pred(t+dt) = T_trend + dT/dt·dt
   Güven aralığı: ±2σ_kalman

2. CUSUMDefectDetector: İstatistiksel süreç kontrolü
   CUSUM+: C+(t) = max(0, C+(t-1) + (T-μ)/σ - k)
   CUSUM-: C-(t) = max(0, C-(t-1) - (T-μ)/σ - k)
   Alarm: C+ > h veya C- > h (tipik h=5, k=0.5)
   Shewhart: |T-μ| > 3σ → ani alarm

3. WavinesDetector: Frekans domeninde fiber dalgalanma
   FFT of tension signal → spectral entropy → waviness score
"""
from __future__ import annotations
import math, time, threading
from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np

@dataclass(frozen=True, slots=True)
class TensionForecast:
    T_predicted_N:  float   # t+horizon tahmin
    T_current_N:    float
    trend_N_s:      float   # dT/dt [N/s]
    sigma_N:        float   # Tahmin belirsizliği (1σ)
    horizon_s:      float
    confidence:     float
    high_tension_risk: bool  # T_pred + 2σ > T_max?

@dataclass(frozen=True, slots=True)
class DefectAlert:
    defect_type:  str    # "tension_drift","spike","waviness","none"
    severity:     float  # [0,1]
    confidence:   float
    cusum_plus:   float
    cusum_minus:  float
    shewhart_hit: bool
    waviness:     float  # [0,1]
    timestamp:    float

class EWMAKalmanPredictor:
    """EWMA trend + Kalman 1D öngörü."""
    def __init__(self, alpha: float = 0.15, T_nom: float = 15.0,
                 sigma_proc: float = 0.5, sigma_meas: float = 0.3):
        self.alpha      = alpha
        self._T_ewma    = T_nom
        self._dT_dt     = 0.0
        self._P         = 1.0
        self._Q         = sigma_proc**2
        self._R         = sigma_meas**2
        self._t_prev    = None

    def update(self, T: float) -> None:
        now = time.monotonic()
        if self._t_prev is not None:
            dt = max(now - self._t_prev, 1e-6)
            T_prev = self._T_ewma
            self._T_ewma = self.alpha * T + (1 - self.alpha) * self._T_ewma
            self._dT_dt  = (self._T_ewma - T_prev) / dt
        else:
            self._T_ewma = T
        self._t_prev = now
        # Kalman update
        self._P += self._Q
        K = self._P / (self._P + self._R)
        self._T_ewma += K * (T - self._T_ewma)
        self._P *= (1.0 - K)

    def predict(self, horizon_s: float = 0.1) -> Tuple[float, float]:
        """Returns (T_pred, sigma_pred)."""
        T_pred = self._T_ewma + self._dT_dt * horizon_s
        sigma  = math.sqrt(self._P + self._Q * horizon_s)
        return T_pred, sigma

    @property
    def trend(self) -> float: return self._dT_dt
    @property
    def current(self) -> float: return self._T_ewma


class CUSUMController:
    """CUSUM + Shewhart kontrolü."""
    def __init__(self, mu: float = 15.0, sigma: float = 1.5,
                 k: float = 0.5, h: float = 5.0):
        self.mu    = mu; self.sigma = max(sigma, 0.1)
        self.k     = k;  self.h     = h
        self.C_pos = 0.0; self.C_neg = 0.0
        self._buf: deque = deque(maxlen=100)

    def update(self, T: float) -> Tuple[float, float, bool]:
        """Returns (C+, C-, shewhart_alarm)."""
        z = (T - self.mu) / self.sigma
        self.C_pos = max(0.0, self.C_pos + z - self.k)
        self.C_neg = max(0.0, self.C_neg - z - self.k)
        self._buf.append(T)
        # Online sigma update (adaptive)
        if len(self._buf) >= 20:
            arr = np.array(self._buf)
            self.sigma = max(0.3, float(arr.std()))
            self.mu    = float(arr.mean())
        shewhart = abs(T - self.mu) > 3.0 * self.sigma
        return self.C_pos, self.C_neg, shewhart

    @property
    def alarm(self) -> bool:
        return self.C_pos > self.h or self.C_neg > self.h


class SpectralWavinessDetector:
    """FFT tabanlı fiber dalgalanma skoru."""
    WINDOW = 128
    def __init__(self, dt: float = 0.01):
        self._buf = deque(maxlen=self.WINDOW)
        self._dt  = dt

    def add(self, T: float) -> None: self._buf.append(T)

    def waviness(self) -> float:
        """Spectral entropy → waviness [0,1]."""
        if len(self._buf) < self.WINDOW // 2:
            return 0.0
        arr  = np.array(self._buf, dtype=float)
        arr -= arr.mean()
        win  = np.hanning(len(arr))
        fft  = np.abs(np.fft.rfft(arr * win))
        fft  = fft[1:]  # DC remove
        p    = fft / (fft.sum() + 1e-12)
        entr = -float(np.sum(p * np.log(p + 1e-12)))
        max_e = math.log(len(p))
        return float(np.clip(entr / max_e, 0, 1)) if max_e > 0 else 0.0


class TensionPredictor:
    """
    Unified tension intelligence: predict + detect defects.
    Thread-safe, non-blocking reads.
    """
    def __init__(self, T_nominal: float = 15.0, T_max: float = 40.0,
                 dt: float = 0.01, horizon_s: float = 0.1):
        self.T_nom    = T_nominal
        self.T_max    = T_max
        self._horizon = horizon_s
        self._kalman  = EWMAKalmanPredictor(T_nom=T_nominal)
        self._cusum   = CUSUMController(mu=T_nominal)
        self._wav     = SpectralWavinessDetector(dt=dt)
        self._lock    = threading.Lock()
        self._last_fc: Optional[TensionForecast] = None
        self._last_al: Optional[DefectAlert]     = None
        self._n       = 0

    def update(self, T: float) -> Tuple[TensionForecast, DefectAlert]:
        """Tek ölçüm ile tüm modelleri güncelle. Thread-safe."""
        with self._lock:
            self._kalman.update(T)
            C_pos, C_neg, shaw = self._cusum.update(T)
            self._wav.add(T)
            self._n += 1

            T_pred, sigma = self._kalman.predict(self._horizon)
            trend         = self._kalman.trend
            conf          = min(1.0, self._n / 30.0)

            fc = TensionForecast(
                T_predicted_N  = float(np.clip(T_pred, 0, self.T_max * 1.5)),
                T_current_N    = T,
                trend_N_s      = trend,
                sigma_N        = sigma,
                horizon_s      = self._horizon,
                confidence     = conf,
                high_tension_risk = (T_pred + 2*sigma) > (self.T_max - 5.0),
            )

            wav_score = self._wav.waviness()
            has_drift = self._cusum.alarm
            has_spike = shaw
            has_wave  = wav_score > 0.6

            if has_spike and has_drift:
                dtype, sev = "tension_drift+spike", 0.9
            elif has_spike:
                dtype, sev = "spike", 0.7
            elif has_drift:
                dtype, sev = "tension_drift", 0.5
            elif has_wave:
                dtype, sev = "waviness", 0.4
            else:
                dtype, sev = "none", 0.0

            al = DefectAlert(defect_type=dtype, severity=sev, confidence=conf,
                cusum_plus=C_pos, cusum_minus=C_neg,
                shewhart_hit=shaw, waviness=wav_score, timestamp=time.time())

            self._last_fc = fc; self._last_al = al
            return fc, al

    def get_latest(self) -> Tuple[Optional[TensionForecast], Optional[DefectAlert]]:
        with self._lock: return self._last_fc, self._last_al
