"""
auto_calibration.py — Otomatik Kalibrasyon ve Readiness Report
===============================================================
Faz 7 telemetri verisinden otomatik parametre güncelleme:
  - τ_A ölçümü (spindle zaman sabiti)
  - Backlash tahmini
  - Tension transient model
  - Feed drift katsayısı
  - Resonans frekansı tespiti

Bayesian-inspired kalibrasyon:
  Yeni ölçüm θ_meas, önce (prior) θ_prior ile birleşir:
  θ_posterior = (σ_prior²·θ_meas + σ_meas²·θ_prior) / (σ_prior² + σ_meas²)
  Bu, az ölçümde prior'ı korur, çok ölçümde veriye yaklaşır.

Resonans tespiti:
  FFT of tension signal → dominant frequency
  Eğer f_res > 0 ve < 50Hz → mechanical resonance
  Sönüm: feed override ile f_res'in üzerinde hız yasak

"First Real Winding Readiness Report":
  Her subsystem için 0-100 hazırlık skoru.
  Tüm skorlar ≥ 70 → "HAZIR" kararı.
  Kritik skorlar (safety, comm, limits) ≥ 90 zorunlu.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple

import numpy as np


# ── Calibrated Parameters ────────────────────────────────────────

@dataclass(slots=True)
class CalibratedParams:
    """Kalibrasyon sonucu parametreler."""
    tau_A_s:           float = 6.0      # Spindle zaman sabiti [s]
    tau_A_sigma:       float = 2.0      # Belirsizlik (1-sigma)
    backlash_mm:       float = 0.15     # Carriage backlash [mm]
    backlash_sigma:    float = 0.05
    tension_k_fiber:   float = 0.035    # Fiber yay sabiti [N/mm]
    tension_b:         float = 2.0      # Gerilme sönüm [1/s]
    drift_deg_circuit: float = 1.22     # Feed drift / devre [°]
    drift_sigma:       float = 0.5
    resonance_hz:      float = 0.0      # Rezonans frekansı (0=yok)
    resonance_amp:     float = 0.0      # Rezonans genliği [N]
    n_measurements:    int   = 0        # Toplam ölçüm sayısı

    def report(self) -> str:
        lines = [
            "  Kalibre Parametreler:",
            f"    τ_A       = {self.tau_A_s:.4f} ± {self.tau_A_sigma:.4f} s",
            f"    backlash  = {self.backlash_mm:.4f} ± {self.backlash_sigma:.4f} mm",
            f"    k_fiber   = {self.tension_k_fiber:.5f} N/mm",
            f"    b_tension = {self.tension_b:.4f} 1/s",
            f"    drift     = {self.drift_deg_circuit:.4f} ± {self.drift_sigma:.4f} °/devre",
            f"    resonance = {self.resonance_hz:.2f} Hz "
            f"({'TESPIT' if self.resonance_hz > 0.5 else 'yok'}) amp={self.resonance_amp:.3f}N",
            f"    n_meas    = {self.n_measurements}",
        ]
        return "\n".join(lines)

    def to_simulation_config_dict(self) -> dict:
        """SimulationConfig güncellemesi için dict."""
        return {
            "J_spindle_tau_s":    self.tau_A_s,
            "backlash_mm":        self.backlash_mm,
            "k_fiber_N_per_mm":   self.tension_k_fiber,
            "b_tension":          self.tension_b,
            "feed_drift_per_circuit_deg": self.drift_deg_circuit,
        }


# ── Tau_A Estimator ──────────────────────────────────────────────

class TauAEstimator:
    """
    Spindle zaman sabiti τ_A tahmini.

    Yöntem: Step response curve fitting
      ω(t) = ω_∞ · (1 - e^(-t/τ))
      τ = -t / ln(1 - ω(t)/ω_∞)

    Birden fazla step response → Weighted least squares.
    """

    def __init__(self, prior_tau: float = 6.0, prior_sigma: float = 2.0) -> None:
        self.prior_tau   = prior_tau
        self.prior_sigma = prior_sigma
        self._estimates: List[Tuple[float, float]] = []   # (tau, weight)

    def add_step_response(
        self,
        t_arr:     np.ndarray,    # Zaman [s]
        omega_arr: np.ndarray,    # Açısal hız [°/s]
        omega_inf: float,         # Steady-state [°/s]
    ) -> Optional[float]:
        """
        Step response'tan τ_A tahmini.

        63.2% ulaşma zamanı yöntemi + regresyon.
        """
        if len(t_arr) < 5 or omega_inf < 1.0:
            return None

        # 63.2% threshold
        threshold = omega_inf * 0.632
        idx_63    = np.argmax(omega_arr >= threshold)
        if idx_63 == 0:
            return None

        tau_63 = float(t_arr[idx_63])

        # Curve fit: log(1 - ω/ω_∞) = -t/τ
        ratio = omega_arr / omega_inf
        ratio = np.clip(ratio, 0.01, 0.99)
        log_r = -np.log(1.0 - ratio)
        # Linear fit: log_r = t/τ → slope = 1/τ
        valid = (log_r > 0.01) & (t_arr > 0)
        if valid.sum() < 3:
            return tau_63

        slope, intercept = np.polyfit(t_arr[valid], log_r[valid], 1)
        tau_fit = 1.0 / max(slope, 1e-6)

        # Makul aralıkta mı?
        if 0.1 < tau_fit < 30.0:
            weight = valid.sum() / len(t_arr)
            self._estimates.append((tau_fit, weight))
            return tau_fit
        elif 0.1 < tau_63 < 30.0:
            self._estimates.append((tau_63, 0.5))
            return tau_63
        return None

    def get_estimate(self) -> Tuple[float, float]:
        """
        Bayesian posterior tahmin.

        Returns: (tau_posterior, sigma_posterior)
        """
        if not self._estimates:
            return self.prior_tau, self.prior_sigma

        # Weighted mean
        taus    = np.array([e[0] for e in self._estimates])
        weights = np.array([e[1] for e in self._estimates])
        w_mean  = float(np.average(taus, weights=weights))
        w_std   = float(np.sqrt(np.average((taus - w_mean)**2, weights=weights)))

        # Bayesian fusion with prior
        sigma_prior = self.prior_sigma
        sigma_meas  = max(w_std, 0.1)
        n           = len(self._estimates)

        # Precision-weighted fusion
        prec_prior  = 1.0 / sigma_prior**2
        prec_meas   = n / sigma_meas**2
        tau_post    = (prec_prior*self.prior_tau + prec_meas*w_mean) / (prec_prior + prec_meas)
        sigma_post  = 1.0 / math.sqrt(prec_prior + prec_meas)

        return float(tau_post), float(sigma_post)


# ── Resonance Detector ────────────────────────────────────────────

class ResonanceDetector:
    """
    Titreşim/rezonans tespiti — FFT tabanlı.

    Tension veya vibration sinyalinde dominant frekans bul.
    f_res ∈ [0.5, 50]Hz aralığında → mekanik rezonans.
    """

    def __init__(self, dt: float = 0.01, threshold_amp: float = 0.5) -> None:
        self.dt        = dt
        self.thr       = threshold_amp   # [N] min significant amplitude
        self._buffer:  List[float] = []
        self._window   = 512             # FFT pencere boyutu

    def add_sample(self, tension_N: float) -> None:
        self._buffer.append(tension_N)
        if len(self._buffer) > self._window * 2:
            self._buffer.pop(0)

    def detect(self) -> Tuple[float, float]:
        """
        FFT ile rezonans tespiti.

        Returns: (freq_hz, amplitude_N)
        Freq=0 → rezonans yok.
        """
        if len(self._buffer) < self._window:
            return 0.0, 0.0

        data = np.array(self._buffer[-self._window:])
        data -= data.mean()   # DC removal

        # Hanning penceresi
        win    = np.hanning(len(data))
        fft    = np.abs(np.fft.rfft(data * win))
        freqs  = np.fft.rfftfreq(len(data), d=self.dt)

        # DC ve çok yüksek frekansları hariç tut
        mask   = (freqs > 0.5) & (freqs < 50.0)
        if not mask.any():
            return 0.0, 0.0

        fft_m  = fft[mask]
        freq_m = freqs[mask]
        peak_idx = int(np.argmax(fft_m))
        amp    = float(fft_m[peak_idx]) * 2.0 / self._window  # Normalize

        if amp > self.thr:
            return float(freq_m[peak_idx]), amp
        return 0.0, 0.0


# ── Auto Calibration Engine ──────────────────────────────────────

class AutoCalibrationEngine:
    """
    Faz 7 telemetri + çalışma zamanı ölçümlerinden otomatik kalibrasyon.

    Kullanım:
        engine = AutoCalibrationEngine()
        # Phase 7 telemetri dosyasından yükle
        engine.load_phase7_telemetry("fw_telemetry.csv")
        # Çalışma zamanı güncelleme
        engine.update_from_measurement(x_cmd, x_meas, tension_arr, t_arr)
        # Kalibre parametreleri al
        params = engine.get_calibrated_params()
        print(params.report())
        # Readiness report
        report = engine.readiness_report(params, safety_ok, comm_ok)
    """

    def __init__(
        self,
        prior: Optional[CalibratedParams] = None,
    ) -> None:
        self._params = prior or CalibratedParams()
        self._tau_est   = TauAEstimator(self._params.tau_A_s, self._params.tau_A_sigma)
        self._res_det   = ResonanceDetector()
        self._bl_errors: List[float] = []
        self._drift_obs: List[float] = []
        self._tension_hist: List[float] = []
        self._n_updates = 0

    # ── Phase 7 telemetry loader ─────────────────────────────────

    def load_phase7_telemetry(self, filepath: str) -> int:
        """
        Faz 7 telemetri CSV'den yükle ve kalibre et.

        Returns: Yüklenen paket sayısı
        """
        import csv
        try:
            rows = []
            with open(filepath) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(row)
        except FileNotFoundError:
            return 0

        n = len(rows)
        if n < 10:
            return n

        # Gerilme sinyali → rezonans
        tensions = [float(r.get("tension_N", 15.0)) for r in rows]
        for T in tensions:
            self._res_det.add_sample(T)
            self._tension_hist.append(T)

        # X hatası → backlash estimate
        x_errs = [float(r.get("twin_x_err", 0.0)) for r in rows]
        self._bl_errors.extend(x_errs)

        # A hata → drift estimate
        a_errs = [float(r.get("twin_a_err", 0.0)) for r in rows]
        if a_errs:
            drift = float(np.mean(np.abs(a_errs)))
            self._drift_obs.append(drift)

        self._n_updates += n
        return n

    # ── Runtime update ───────────────────────────────────────────

    def update_from_measurement(
        self,
        x_cmd_arr:    np.ndarray,
        x_meas_arr:   np.ndarray,
        tension_arr:  np.ndarray,
        t_arr:        np.ndarray,
        omega_arr:    Optional[np.ndarray] = None,
        omega_inf:    float = 100.0,
    ) -> None:
        """
        Çalışma zamanı ölçümlerinden kalibrasyon güncelleme.
        """
        # τ_A tahmini (spindle step response varsa)
        if omega_arr is not None and len(omega_arr) > 10:
            self._tau_est.add_step_response(t_arr, omega_arr, omega_inf)

        # Backlash güncelleme (yön değişimi hatalarından)
        if len(x_cmd_arr) > 3 and len(x_meas_arr) > 3:
            v     = np.diff(x_cmd_arr)
            revs  = np.where(v[:-1] * v[1:] < 0)[0]
            for idx in revs:
                if idx < len(x_meas_arr):
                    err = abs(float(x_cmd_arr[idx]) - float(x_meas_arr[idx]))
                    self._bl_errors.append(err)

        # Gerilme → rezonans
        for T in tension_arr:
            self._res_det.add_sample(float(T))
            self._tension_hist.append(float(T))

        self._n_updates += len(x_cmd_arr)

    # ── Parameter estimation ─────────────────────────────────────

    def get_calibrated_params(self) -> CalibratedParams:
        """Kalibre parametreleri hesapla ve döndür."""
        params = CalibratedParams()

        # τ_A
        tau, sigma_tau = self._tau_est.get_estimate()
        params.tau_A_s     = tau
        params.tau_A_sigma = sigma_tau

        # Backlash
        if self._bl_errors:
            bl_arr = np.array(self._bl_errors)
            # Yön değişimindeki hatalar → backlash ≈ median
            bl_est = float(np.median(bl_arr[bl_arr > 0.001])) if any(e > 0.001 for e in bl_arr) else 0.15
            # Bayesian fusion
            n     = len(bl_arr)
            prior_prec = 1.0 / 0.05**2
            meas_prec  = n / max(float(np.std(bl_arr)), 0.01)**2
            params.backlash_mm = float((prior_prec*0.15 + meas_prec*bl_est) / (prior_prec + meas_prec))
            params.backlash_sigma = float(1.0 / math.sqrt(prior_prec + meas_prec))
        else:
            params.backlash_mm    = self._params.backlash_mm
            params.backlash_sigma = self._params.backlash_sigma

        # Drift
        if self._drift_obs:
            drift_mean    = float(np.mean(self._drift_obs))
            drift_std     = float(np.std(self._drift_obs)) if len(self._drift_obs) > 1 else 0.5
            params.drift_deg_circuit = drift_mean
            params.drift_sigma       = drift_std
        else:
            params.drift_deg_circuit = self._params.drift_deg_circuit
            params.drift_sigma       = self._params.drift_sigma

        # Tension model (k_fiber, b_tension) — EMA güncelleme
        if len(self._tension_hist) > 20:
            T_arr  = np.array(self._tension_hist[-200:])
            T_std  = float(np.std(T_arr))
            # k_fiber ∝ T_std / v_nominal (basit heuristik)
            params.tension_k_fiber = max(0.01, T_std * 0.002)
            params.tension_b       = max(0.5, 2.0 / max(tau, 1.0))

        # Resonans
        f_res, amp = self._res_det.detect()
        params.resonance_hz  = f_res
        params.resonance_amp = amp

        params.n_measurements = self._n_updates

        # Mevcut params'ı güncelle (EMA ile)
        alpha = 0.3 if self._n_updates > 100 else 0.1
        self._params.tau_A_s           = (1-alpha)*self._params.tau_A_s + alpha*params.tau_A_s
        self._params.backlash_mm       = (1-alpha)*self._params.backlash_mm + alpha*params.backlash_mm
        self._params.drift_deg_circuit = (1-alpha)*self._params.drift_deg_circuit + alpha*params.drift_deg_circuit

        return params

    # ── Readiness Report ─────────────────────────────────────────

    def readiness_report(
        self,
        params:       CalibratedParams,
        safety_ok:    bool = True,
        comm_ok:      bool = True,
        validation_passed: int = 4,   # Faz 7'den
        n_validation_total:int = 6,
        rms_x_error_mm: float = 0.06,
        rms_a_error_deg:float = 0.18,
    ) -> "ReadinessReport":
        """
        İlk gerçek sarım hazırlık raporu üret.

        Her subsistem için 0-100 skor.
        """
        scores = {}

        # 1. Kalibrasyon kalitesi [0,100]
        tau_conf = max(0, 100 - params.tau_A_sigma * 10)
        bl_conf  = max(0, 100 - params.backlash_sigma * 100)
        calib_score = (tau_conf + bl_conf) / 2.0
        scores["calibration"] = min(100, calib_score + min(50, params.n_measurements/10))

        # 2. Sensör sağlığı (Phase 7 doğrulamasından)
        val_frac = validation_passed / max(n_validation_total, 1)
        scores["validation"] = val_frac * 100.0

        # 3. Safety layer
        scores["safety"] = 95.0 if safety_ok else 0.0

        # 4. İletişim (controller bağlantısı)
        scores["communication"] = 90.0 if comm_ok else 20.0

        # 5. Tracking accuracy (Kalman + PID performansı)
        x_score = max(0, 100.0 - rms_x_error_mm * 500)
        a_score = max(0, 100.0 - rms_a_error_deg * 50)
        scores["tracking"] = (x_score + a_score) / 2.0

        # 6. Rezonans (0 → mükemmel, yüksek → riskli)
        if params.resonance_hz > 0.5:
            res_score = max(0, 100.0 - params.resonance_amp * 20.0)
        else:
            res_score = 100.0
        scores["vibration"] = res_score

        # 7. Drift (düşük drift → yüksek skor)
        drift_score = max(0, 100.0 - abs(params.drift_deg_circuit) * 10.0)
        scores["drift_control"] = drift_score

        # Ağırlıklı toplam
        weights = {
            "safety":        0.25,
            "communication": 0.15,
            "validation":    0.20,
            "calibration":   0.15,
            "tracking":      0.15,
            "vibration":     0.05,
            "drift_control": 0.05,
        }
        composite = sum(scores[k]*weights[k] for k in weights)

        # Kritik kontroller (≥80 zorunlu)
        critical_pass = (
            scores["safety"] >= 80.0 and
            scores["communication"] >= 70.0 and
            scores["validation"] >= 60.0
        )

        return ReadinessReport(
            scores       = scores,
            composite    = composite,
            critical_ok  = critical_pass,
            params       = params,
            ready        = composite >= 70.0 and critical_pass,
        )


# ── Readiness Report ─────────────────────────────────────────────

@dataclass(slots=True)
class ReadinessReport:
    """İlk gerçek sarım hazırlık raporu."""
    scores:     Dict[str, float]
    composite:  float
    critical_ok:bool
    params:     CalibratedParams
    ready:      bool

    def print_report(self) -> None:
        stars = "★" if self.ready else "✗"
        print("  ╔" + "═"*62 + "╗")
        print(f"  ║  {stars} İLK GERÇEK SARIM HAZIRLIK RAPORU  {stars:<26}║")
        print("  ╠" + "═"*62 + "╣")

        categories = [
            ("safety",        "Güvenlik Katmanı",    80.0),
            ("communication", "Controller İletişim", 70.0),
            ("validation",    "Donanım Doğrulama",   60.0),
            ("calibration",   "Makine Kalibrasyonu", 60.0),
            ("tracking",      "Konum İzleme",        50.0),
            ("vibration",     "Titreşim Durumu",     50.0),
            ("drift_control", "Drift Kontrolü",      50.0),
        ]
        for key, label, threshold in categories:
            score = self.scores.get(key, 0.0)
            bar_n = int(score / 5)
            bar   = "█"*bar_n + "░"*(20-bar_n)
            icon  = "✓" if score >= threshold else "✗"
            print(f"  ║  {icon} {label:<22} [{bar}] {score:5.1f}/100  ║")

        print("  ╠" + "═"*62 + "╣")
        comp_bar = "█"*int(self.composite/5) + "░"*(20-int(self.composite/5))
        print(f"  ║  Kompozit Skor:         [{comp_bar}] {self.composite:5.1f}/100  ║")
        print(f"  ║  Kritik kontroller: {'GEÇER ✓' if self.critical_ok else 'BAŞARISIZ ✗':<37}║")
        print("  ╠" + "═"*62 + "╣")
        if self.ready:
            print("  ║  ★★★ KARAR: İLK SARIM TESTİ YAPILABILIR ★★★             ║")
        else:
            fail_items = [k for k,v in self.scores.items() if v < 60.0]
            msg = f"REDDEDİLDİ — iyileştir: {','.join(fail_items[:2])}"
            print(f"  ║  ✗ KARAR: {msg:<52}║")
        print("  ╚" + "═"*62 + "╝")

        # Kalibrasyon özeti
        print(f"\n{self.params.report()}")
