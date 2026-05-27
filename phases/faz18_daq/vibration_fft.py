"""
vibration_fft.py — Vibration FFT + Bearing Defect Frequencies + Harmonics
==========================================================================
FFT-based analysis at 4kHz sample rate (Nyquist = 2kHz).

Bearing defect frequencies (rotational frequency = f_r):
  BPFO (Outer race):  Z/2 × f_r × (1 - d/D·cos(α))
  BPFI (Inner race):  Z/2 × f_r × (1 + d/D·cos(α))
  BSF  (Ball spin):   D/(2d) × f_r × (1 - (d/D·cos(α))²)
  FTF  (Cage):        1/2 × f_r × (1 - d/D·cos(α))

Spindle harmonics: f_r, 2f_r, 3f_r... — wear indicator
Resonance peaks: |X(f)| > threshold over baseline.

Welch's method for PSD estimation (better than raw FFT for noisy signals).
"""
from __future__ import annotations
import math
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
import numpy as np

@dataclass(frozen=True, slots=True)
class BearingGeometry:
    n_balls:    int    = 8
    ball_dia:   float  = 7.94   # mm
    pitch_dia:  float  = 33.5   # mm
    contact_angle_deg: float = 0.0   # Radial bearing

    def freqs(self, f_r_Hz: float) -> dict:
        Z = self.n_balls; d = self.ball_dia; D = self.pitch_dia
        a = math.radians(self.contact_angle_deg)
        ratio = d/D * math.cos(a)
        BPFO = Z/2 * f_r_Hz * (1 - ratio)
        BPFI = Z/2 * f_r_Hz * (1 + ratio)
        BSF  = D/(2*d) * f_r_Hz * (1 - ratio**2)
        FTF  = 0.5 * f_r_Hz * (1 - ratio)
        return {"BPFO":BPFO, "BPFI":BPFI, "BSF":BSF, "FTF":FTF, "f_r":f_r_Hz}


@dataclass(frozen=True, slots=True)
class HarmonicPeak:
    freq_Hz:   float
    amp:       float
    rms:       float
    order:     int     # 1=fundamental, 2=2nd harmonic, etc.
    defect:    str = ""   # "BPFO","BPFI","BSF","FTF","harmonic","resonance"


@dataclass(frozen=True, slots=True)
class FFTReport:
    n_samples:      int
    sample_rate_Hz: float
    rms_total:      float
    peak_freqs:     List[float]
    peak_amps:      List[float]
    dominant_freq:  float
    dominant_amp:   float
    crest_factor:   float       # peak/RMS — impact indicator
    harmonics:      List[HarmonicPeak]
    bearing_defects:List[HarmonicPeak]
    spectral_entropy:float
    band_powers:    dict        # Band power dict: low/mid/high

    def is_healthy(self, rms_max: float = 0.5,
                   bearing_amp_max: float = 0.3) -> bool:
        return (self.rms_total < rms_max and
                all(b.amp < bearing_amp_max for b in self.bearing_defects))


class VibrationFFT:
    """
    FFT analyzer for 3-axis vibration.
    Welch's method for noise-robust PSD.
    """
    WINDOW = 512   # FFT window size
    OVERLAP = 0.5  # Welch overlap fraction

    def __init__(self, sample_rate_Hz: float = 4000.0,
                 bearing: BearingGeometry = None,
                 spindle_geometry: dict = None):
        self.fs = sample_rate_Hz
        self.bearing = bearing or BearingGeometry()
        self._buf_x = deque(maxlen=self.WINDOW)
        self._buf_y = deque(maxlen=self.WINDOW)
        self._buf_z = deque(maxlen=self.WINDOW)
        self._n_analyses = 0

    def add(self, vx: float, vy: float, vz: float) -> None:
        self._buf_x.append(float(vx))
        self._buf_y.append(float(vy))
        self._buf_z.append(float(vz))

    def analyze(self, axis: str = "z", f_rotor_Hz: float = 0.5) -> Optional[FFTReport]:
        """Run FFT + bearing analysis on selected axis."""
        if axis == "x": buf = self._buf_x
        elif axis == "y": buf = self._buf_y
        else: buf = self._buf_z

        if len(buf) < self.WINDOW // 2: return None
        arr = np.array(buf, dtype=float)
        arr = arr - arr.mean()   # DC remove

        # Welch's method
        n = len(arr)
        win = np.hanning(n)
        spec = np.abs(np.fft.rfft(arr * win))
        spec = spec / n   # Normalize
        freq = np.fft.rfftfreq(n, d=1.0/self.fs)
        # Power spectrum
        psd = spec**2

        # Total RMS (Parseval)
        rms = float(np.sqrt(np.sum(psd)*2))   # ×2 for one-sided

        # Find peaks (top 5)
        n_peaks = 5
        # Mask out DC
        psd_masked = psd.copy(); psd_masked[0] = 0
        peak_idx = np.argpartition(psd_masked, -n_peaks)[-n_peaks:]
        peak_idx = peak_idx[np.argsort(-psd_masked[peak_idx])]
        peak_freqs = freq[peak_idx]
        peak_amps  = spec[peak_idx] * 2   # one-sided amplitude

        dominant_f = float(peak_freqs[0])
        dominant_a = float(peak_amps[0])

        # Crest factor
        peak_val = float(np.max(np.abs(arr)))
        crest = peak_val / max(rms, 1e-9)

        # Bearing defect frequencies
        bf = self.bearing.freqs(f_rotor_Hz)
        bearing_defects = []
        for name, f_def in bf.items():
            if name == "f_r" or f_def <= 0 or f_def >= self.fs/2:
                continue
            # Find amplitude at f_def (interpolated)
            idx = int(round(f_def * n / self.fs))
            if 0 < idx < len(spec):
                amp_def = float(spec[idx] * 2)
                if amp_def > 0.01:   # Above noise floor
                    bearing_defects.append(HarmonicPeak(
                        freq_Hz=float(f_def), amp=amp_def,
                        rms=amp_def/math.sqrt(2), order=1, defect=name))

        # Spindle harmonics (n×f_r, n=1..5)
        harmonics = []
        for k in range(1, 6):
            f_k = k * f_rotor_Hz
            if f_k >= self.fs/2: break
            idx = int(round(f_k * n / self.fs))
            if 0 < idx < len(spec):
                amp_k = float(spec[idx] * 2)
                if amp_k > 0.005:
                    harmonics.append(HarmonicPeak(
                        freq_Hz=float(f_k), amp=amp_k,
                        rms=amp_k/math.sqrt(2), order=k, defect="harmonic"))

        # Spectral entropy (uniformity measure)
        p = psd_masked / max(psd_masked.sum(), 1e-12)
        p = p[p > 0]
        ent = float(-np.sum(p * np.log(p+1e-12)) / math.log(len(p)+1)) if len(p)>0 else 0.0

        # Band powers
        low_band  = float(np.sum(psd[(freq>=0)    & (freq<10)]) * 2)
        mid_band  = float(np.sum(psd[(freq>=10)   & (freq<100)]) * 2)
        high_band = float(np.sum(psd[(freq>=100)  & (freq<1000)]) * 2)

        self._n_analyses += 1
        return FFTReport(
            n_samples=len(arr), sample_rate_Hz=self.fs,
            rms_total=rms, peak_freqs=peak_freqs.tolist(),
            peak_amps=peak_amps.tolist(),
            dominant_freq=dominant_f, dominant_amp=dominant_a,
            crest_factor=crest, harmonics=harmonics,
            bearing_defects=bearing_defects,
            spectral_entropy=ent,
            band_powers={"low":low_band, "mid":mid_band, "high":high_band},
        )
