"""
defect_predictor.py — Çok Kanallı Sarım Kusur Tespit Motoru
============================================================
7 kusur türü: bridging, gap, overlap, tension_collapse,
oscillation, spindle_slip, payout_resonance.

Algoritmalar:
  CUSUM:    kümülatif sum chart, drift tespiti
  Shewhart: anlık 3σ aşımı
  Spectral: FFT magnitude > threshold → resonance/oscillation
  Density:  yerel kaplama yoğunluğu anomalisi (coverage map)

Bütün hesaplamalar: NumPy-only, deterministik, <1ms inference.

Confidence thresholds:
  < 0.50 → ignore
  0.50-0.70 → INFO log
  0.70-0.90 → WARNING + advisory slow
  > 0.90 → CRITICAL + pause recommendation (SafetyValidator'a geçer)

AI kuralı: DefectPredictor ASLA motion command üretemez.
           Sadece DefectAlert döndürür.
"""
from __future__ import annotations
import math, time, threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple
import numpy as np


class DefectType(Enum):
    NONE            = "none"
    BRIDGING        = "bridging"
    GAP             = "gap"
    OVERLAP         = "overlap"
    TENSION_COLLAPSE= "tension_collapse"
    OSCILLATION     = "oscillation"
    SPINDLE_SLIP    = "spindle_slip"
    PAYOUT_RESONANCE= "payout_resonance"


@dataclass(frozen=True, slots=True)
class DefectAlert:
    defect_type:   DefectType
    severity:      float          # [0,1]
    confidence:    float          # [0,1]
    location_mm:   float          # z pozisyonu [mm]
    probable_cause:str
    timestamp:     float
    # Teşhis verileri
    cusum_val:     float
    shewhart_z:    float          # |T-μ|/σ
    spectral_peak: float          # [Hz]
    density_ratio: float          # rho_actual/rho_expected

    @property
    def level(self) -> str:
        if self.confidence < 0.50: return "ignore"
        if self.confidence < 0.70: return "info"
        if self.confidence < 0.90: return "warning"
        return "critical"

    @property
    def advisory(self) -> str:
        if self.level == "warning":
            return f"Yavaşla (%10): {self.defect_type.value}"
        if self.level == "critical":
            return f"Dur/yavaşla (%30): {self.defect_type.value}"
        return ""


# ── CUSUM Controller ──────────────────────────────────────────────

class CUSUMChannel:
    """Single-metric CUSUM chart."""
    def __init__(self, k: float = 0.5, h: float = 5.0,
                 mu_init: float = 0.0, sigma_init: float = 1.0):
        self.k    = k; self.h = h
        self.mu   = mu_init; self.sigma = max(sigma_init, 0.1)
        self.C_pos= 0.0; self.C_neg = 0.0
        self._n   = 0; self._buf = deque(maxlen=50)

    def update(self, val: float) -> Tuple[float, float]:
        self._buf.append(val); self._n += 1
        # Adaptive μ,σ after warmup
        if self._n > 20:
            arr = np.array(self._buf)
            self.mu    = float(arr.mean())
            self.sigma = max(0.1, float(arr.std()))
        z = (val - self.mu) / self.sigma
        self.C_pos = max(0.0, self.C_pos + z - self.k)
        self.C_neg = max(0.0, self.C_neg - z - self.k)
        return self.C_pos, self.C_neg

    @property
    def alarm(self) -> bool: return self.C_pos > self.h or self.C_neg > self.h
    def reset(self): self.C_pos = self.C_neg = 0.0


# ── Spectral Analyzer ─────────────────────────────────────────────

class SpectralAnalyzer:
    """FFT-based resonance and oscillation detector."""
    WINDOW = 256
    def __init__(self, dt: float = 0.01, n_peaks: int = 3):
        self._buf  = deque(maxlen=self.WINDOW)
        self.dt    = dt
        self.n_pk  = n_peaks

    def add(self, x: float) -> None: self._buf.append(float(x))

    def analyze(self) -> Tuple[float, float, float]:
        """Returns (peak_freq_hz, peak_amp, spectral_entropy)."""
        if len(self._buf) < self.WINDOW // 2:
            return 0.0, 0.0, 0.0
        arr  = np.array(self._buf, dtype=float); arr -= arr.mean()
        win  = np.hanning(len(arr))
        fft  = np.abs(np.fft.rfft(arr * win))
        freq = np.fft.rfftfreq(len(arr), d=self.dt)
        fft[0] = 0.0  # DC
        # Peaks in 0.5-50Hz
        mask = (freq >= 0.5) & (freq <= 50.0)
        if not mask.any(): return 0.0, 0.0, 0.0
        f_m  = freq[mask]; a_m = fft[mask]
        pk   = int(np.argmax(a_m))
        peak_f   = float(f_m[pk])
        peak_amp = float(a_m[pk]) * 2.0 / self.WINDOW
        # Spectral entropy
        p    = a_m / (a_m.sum() + 1e-12)
        entr = float(-np.sum(p * np.log(p + 1e-12)))
        return peak_f, peak_amp, entr


# ── Defect Predictor ──────────────────────────────────────────────

class DefectPredictor:
    """
    7-channel defect detection engine.
    Thread-safe. Non-blocking update().
    """
    # Defect thresholds
    TENSION_COLLAPSE_FRAC = 0.40  # T < 40% nominal → collapse
    SLIP_ENCODER_ERR_MM   = 1.0   # |x_enc - x_cmd| > 1mm → slip
    RESONANCE_AMP_N       = 0.8   # peak tension FFT amplitude [N]
    BRIDGING_RHO_LOW      = 0.80  # rho < 0.80 → bridging (fiber lifts)
    GAP_RHO_LOW           = 0.90  # rho < 0.90 → gap
    OVERLAP_RHO_HIGH      = 1.25  # rho > 1.25 → overlap

    def __init__(self, T_nominal: float = 15.0, dt: float = 0.01,
                 bandwidth_mm: float = 10.0):
        self.T_nom = T_nominal
        self.b     = bandwidth_mm
        # CUSUM channels
        self._cusum_T    = CUSUMChannel(mu_init=T_nominal, sigma_init=1.5)
        self._cusum_rho  = CUSUMChannel(mu_init=1.0, sigma_init=0.05)
        self._cusum_enc  = CUSUMChannel(mu_init=0.0, sigma_init=0.1)
        # Spectral
        self._spec_T     = SpectralAnalyzer(dt=dt)  # tension oscillation
        self._spec_vib   = SpectralAnalyzer(dt=dt)  # vibration/IMU
        # State
        self._lock       = threading.Lock()
        self._n          = 0
        self._last_alert: Optional[DefectAlert] = None

    def update(self,
               T_N: float,          # Load cell tension [N]
               rho_local: float,    # Local coverage density
               x_enc_mm: float,     # Encoder X [mm]
               x_cmd_mm: float,     # Commanded X [mm]
               vib_rms: float = 0.0,# Vibration RMS [g]
               z_mm: float = 0.0,   # Current z position
               ) -> Optional[DefectAlert]:
        """
        Tek ölçüm ile tüm kanalları güncelle.
        Returns DefectAlert if detected, None otherwise.
        Non-blocking (acquires lock briefly).
        """
        with self._lock:
            self._n += 1

            # CUSUM update
            c_T_p, c_T_n  = self._cusum_T.update(T_N)
            c_r_p, c_r_n  = self._cusum_rho.update(rho_local)
            enc_err        = abs(x_enc_mm - x_cmd_mm)
            c_e_p, c_e_n  = self._cusum_enc.update(enc_err)

            # Spectral
            self._spec_T.add(T_N - self.T_nom)
            self._spec_vib.add(vib_rms)
            f_T, amp_T, _ = self._spec_T.analyze()
            f_v, amp_v, _ = self._spec_vib.analyze()

            # Shewhart z-scores
            mu_T = self._cusum_T.mu; sig_T = self._cusum_T.sigma
            z_T  = abs(T_N - mu_T) / max(sig_T, 0.1)

            # ── Defect classification ─────────────────────────────
            # Priority: most severe first
            defect  = DefectType.NONE
            sev     = 0.0
            conf    = 0.0
            cause   = ""

            # 1. Tension collapse
            if T_N < self.T_nom * self.TENSION_COLLAPSE_FRAC:
                defect = DefectType.TENSION_COLLAPSE
                sev    = 1.0
                conf   = 0.97
                cause  = f"T={T_N:.2f}N<{self.T_nom*self.TENSION_COLLAPSE_FRAC:.1f}N"

            # 2. Payout resonance (high-amplitude tension oscillation)
            elif amp_T > self.RESONANCE_AMP_N and 0.5 < f_T < 20.0:
                defect = DefectType.PAYOUT_RESONANCE
                sev    = min(1.0, amp_T / (self.RESONANCE_AMP_N * 2))
                conf   = min(0.95, 0.60 + 0.35 * (amp_T / self.RESONANCE_AMP_N - 1))
                cause  = f"T_resonance f={f_T:.1f}Hz amp={amp_T:.3f}N"

            # 3. Oscillation (vibration + tension drift)
            elif amp_v > 0.3 and self._cusum_T.alarm:
                defect = DefectType.OSCILLATION
                sev    = min(1.0, amp_v / 0.5)
                conf   = 0.70 + 0.20 * min(1.0, amp_v / 0.5)
                cause  = f"vib={amp_v:.3f}g + tension drift"

            # 4. Spindle slip (encoder mismatch)
            elif enc_err > self.SLIP_ENCODER_ERR_MM and self._cusum_enc.alarm:
                defect = DefectType.SPINDLE_SLIP
                sev    = min(1.0, enc_err / (self.SLIP_ENCODER_ERR_MM * 3))
                conf   = min(0.95, 0.65 + 0.30 * (enc_err - self.SLIP_ENCODER_ERR_MM))
                cause  = f"enc_err={enc_err:.3f}mm"

            # 5. Bridging (very low density)
            elif rho_local < self.BRIDGING_RHO_LOW:
                defect = DefectType.BRIDGING
                sev    = (self.BRIDGING_RHO_LOW - rho_local) / self.BRIDGING_RHO_LOW
                conf   = 0.70 + 0.25 * sev
                cause  = f"rho={rho_local:.3f}<{self.BRIDGING_RHO_LOW}"

            # 6. Gap (moderate low density)
            elif rho_local < self.GAP_RHO_LOW:
                defect = DefectType.GAP
                sev    = (self.GAP_RHO_LOW - rho_local) / self.GAP_RHO_LOW * 0.7
                conf   = 0.60 + 0.25 * sev
                cause  = f"rho={rho_local:.3f}<{self.GAP_RHO_LOW}"

            # 7. Overlap (high density)
            elif rho_local > self.OVERLAP_RHO_HIGH:
                defect = DefectType.OVERLAP
                sev    = min(1.0, (rho_local - self.OVERLAP_RHO_HIGH) / 0.25)
                conf   = 0.60 + 0.25 * sev
                cause  = f"rho={rho_local:.3f}>{self.OVERLAP_RHO_HIGH}"

            # ── Confidence warmup penalty ─────────────────────────
            warmup_factor = min(1.0, self._n / 50.0)
            conf *= warmup_factor

            if defect == DefectType.NONE or conf < 0.50:
                self._last_alert = None
                return None

            alert = DefectAlert(
                defect_type   = defect,
                severity      = sev,
                confidence    = conf,
                location_mm   = z_mm,
                probable_cause= cause,
                timestamp     = time.time(),
                cusum_val     = max(c_T_p, c_T_n),
                shewhart_z    = z_T,
                spectral_peak = f_T if amp_T > 0 else f_v,
                density_ratio = rho_local,
            )
            self._last_alert = alert
            return alert

    def get_latest(self) -> Optional[DefectAlert]:
        with self._lock: return self._last_alert

    def reset_cusum(self) -> None:
        """New layer başlangıcında CUSUM sıfırla."""
        with self._lock:
            self._cusum_T.reset()
            self._cusum_rho.reset()
            self._cusum_enc.reset()
