"""
core/path_generator.py — 4-Eksen Sarma Yolu Üreticisi
=======================================================
Clairaut geodezik koşulu kullanarak dönel mandrel üzerinde
filament sarma yolları üretir.

Temel denklem: c = r(z) · sin(α(z))  (Clairaut sabiti)
Açısal oran:   dθ/dz = c / (r(z) · √(r(z)² − c²))
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List

import numpy as np

from .geometry_engine import MandrelProfile


@dataclass
class WindingPathParams:
    """Sarma yolu oluşturmak için tüm parametreler."""
    profile: MandrelProfile
    alpha_deg: float = 55.0          # Sarma açısı (eksel yönden)
    n_layers: int = 4                # Kat sayısı
    tow_width_mm: float = 6.0        # Fitil genişliği (mm)
    overlap_pct: float = 5.0         # Çakışma yüzdesi [0..50]
    feed_mm_s: float = 80.0          # İlerleme hızı (mm/s)
    spindle_rpm: float = 60.0        # İş mili devri
    winding_strategy: str = "helical"  # "helical" | "hoop" | "polar"
    carriage_min_mm: float = -5.0
    carriage_max_mm: float = 395.0
    reverse_at_ends: bool = True     # Uçlarda geri dönüş
    n_steps_per_pass: int = 150      # Geçiş başına adım sayısı


@dataclass
class WindingPoint:
    """Tek bir 4-eksen sarma noktası."""
    x_mm: float      # Taşıyıcı konumu (= mandrel eksenel z konumu)
    a_deg: float     # İş mili açısı (kümülatif derece, her zaman artar)
    feed: float      # Bu noktadaki ilerleme hızı (mm/s)
    z_fiber: float   # Fiber başından itibaren birikimli uzunluk (mm)
    layer: int       # Kat indeksi (0-tabanlı)
    circuit: int     # Devre indeksi (0-tabanlı)


@dataclass
class WindingPath:
    """Hesaplanmış sarma yolu sonucu."""
    points: List[WindingPoint]
    n_circuits: int
    n_layers: int
    total_fiber_length_mm: float
    estimated_time_s: float
    coverage_pct: float
    clairaut_c: float
    params: WindingPathParams


# ── Ana üretici fonksiyonlar ─────────────────────────────────────────────────

def generate_path(params: WindingPathParams) -> WindingPath:
    """
    Belirtilen stratejiye göre sarma yolu üret.
    """
    s = params.winding_strategy.lower()
    if s == "hoop":
        return generate_hoop_path(params)
    elif s == "polar":
        return generate_polar_path(params)
    else:
        return _generate_helical(params)


def generate_hoop_path(params: WindingPathParams) -> WindingPath:
    """
    Çevre sarma (alpha ≈ 88°).
    Taşıyıcı yavaşça ilerler, iş mili hızlıca döner.
    """
    p = WindingPathParams(
        profile=params.profile,
        alpha_deg=88.0,
        n_layers=params.n_layers,
        tow_width_mm=params.tow_width_mm,
        overlap_pct=params.overlap_pct,
        feed_mm_s=params.feed_mm_s,
        spindle_rpm=params.spindle_rpm,
        winding_strategy="helical",
        carriage_min_mm=params.carriage_min_mm,
        carriage_max_mm=params.carriage_max_mm,
        n_steps_per_pass=params.n_steps_per_pass,
    )
    return _generate_helical(p)


def generate_polar_path(params: WindingPathParams) -> WindingPath:
    """
    Kutupsal sarma (alpha ≈ 10-15°).
    Fiberin kubbe etrafını sardığı düşük açılı sarma.
    """
    polar_alpha = min(max(params.alpha_deg, 5.0), 20.0)
    p = WindingPathParams(
        profile=params.profile,
        alpha_deg=polar_alpha,
        n_layers=params.n_layers,
        tow_width_mm=params.tow_width_mm,
        overlap_pct=params.overlap_pct,
        feed_mm_s=params.feed_mm_s,
        spindle_rpm=params.spindle_rpm,
        winding_strategy="helical",
        carriage_min_mm=params.carriage_min_mm,
        carriage_max_mm=params.carriage_max_mm,
        n_steps_per_pass=params.n_steps_per_pass,
    )
    return _generate_helical(p)


# ── İç implementasyon ────────────────────────────────────────────────────────

def _generate_helical(params: WindingPathParams) -> WindingPath:
    """
    Geodezik sarmal sarma yolu üreticisi.

    Algoritma:
    1. Clairaut sabiti c = r_avg · sin(alpha)
    2. Kat başına devre sayısı: n_circ = ceil(2π·r_avg / (w·(1−ov/100)))
    3. Her devre: ileri + geri geçiş
       dθ/dz = c / (r(z)·√(r(z)²−c²))  [Clairaut geodezik]
    4. İş mili her zaman aynı yönde döner (kümülatif a_deg)
    """
    prof = params.profile
    alpha_rad = math.radians(params.alpha_deg)
    r_avg = prof.avg_radius_mm
    r_max = prof.max_radius_mm
    L = prof.length_mm
    z0 = float(prof.z_mm[0])
    z1 = float(prof.z_mm[-1])

    # Clairaut sabiti
    clairaut_c = r_avg * math.sin(alpha_rad)
    # c > r_max durumunu engelle
    clairaut_c = min(clairaut_c, r_max * 0.995)

    # Devre sayısı: tam kapsama için gereken
    eff_width = params.tow_width_mm * (1.0 - params.overlap_pct / 100.0)
    eff_width = max(eff_width, 0.1)
    n_circuits_per_layer = max(1, math.ceil(
        2.0 * math.pi * r_avg / eff_width
    ))
    # Ama aynı zamanda eksenel pitch de gözetilmeli
    axial_pitch = params.tow_width_mm / max(math.cos(alpha_rad), 0.01)
    n_from_axial = max(1, math.ceil(L / axial_pitch))
    n_circuits_per_layer = max(n_circuits_per_layer, n_from_axial)

    all_points: List[WindingPoint] = []
    a_current = 0.0
    fiber_total = 0.0

    for layer in range(params.n_layers):
        for circ in range(n_circuits_per_layer):
            # Yön: her geçişte ters
            global_pass = layer * n_circuits_per_layer + circ
            forward = (global_pass % 2 == 0)
            z_start = z0 if forward else z1
            z_end = z1 if forward else z0

            pts, a_current, fiber_seg = _geodesic_pass(
                prof, z_start, z_end, clairaut_c,
                a_current, params.feed_mm_s,
                layer, circ, params.n_steps_per_pass
            )
            fiber_total += fiber_seg
            all_points.extend(pts)

    # Kapsama hesabı
    total_circuits = params.n_layers * n_circuits_per_layer
    circumference_avg = 2.0 * math.pi * r_avg
    coverage = min(100.0, total_circuits * eff_width / circumference_avg * 100.0)

    # Tahmini süre
    est_time_s = fiber_total / max(params.feed_mm_s, 1.0)

    return WindingPath(
        points=all_points,
        n_circuits=total_circuits,
        n_layers=params.n_layers,
        total_fiber_length_mm=fiber_total,
        estimated_time_s=est_time_s,
        coverage_pct=coverage,
        clairaut_c=clairaut_c,
        params=params,
    )


def _geodesic_pass(
    profile: MandrelProfile,
    z_start: float, z_end: float,
    clairaut_c: float,
    a_start: float,
    feed_mm_s: float,
    layer: int, circuit: int,
    n_steps: int,
) -> tuple:
    """
    Tek geçiş: z_start'tan z_end'e Clairaut geodezik.
    Döner: (points, a_final, fiber_length)
    """
    z_arr = np.linspace(z_start, z_end, n_steps)
    r_arr = np.interp(z_arr, profile.z_mm, profile.r_mm)

    # Açı entegrasyonu: dθ = c/(r·√(r²-c²)) · |dz|
    a_arr = np.zeros(n_steps)
    a_arr[0] = a_start
    c2 = clairaut_c ** 2

    for i in range(1, n_steps):
        dz = abs(z_arr[i] - z_arr[i - 1])
        r_mid = (r_arr[i] + r_arr[i - 1]) * 0.5
        r2 = r_mid ** 2
        if r2 > c2 * 1.001:
            dtheta = clairaut_c / (r_mid * math.sqrt(r2 - c2)) * dz
        else:
            # Kubbe ucu veya çok küçük r: maksimum açısal hız
            dtheta = math.pi / 2.0 * dz / max(r_mid, 0.1)
        a_arr[i] = a_arr[i - 1] + math.degrees(dtheta)

    # Fiber uzunluğu: yüzey boyunca
    dz_arr = np.diff(z_arr)
    da_rad = np.radians(np.diff(a_arr))
    r_mid_arr = (r_arr[:-1] + r_arr[1:]) * 0.5
    ds = np.sqrt(dz_arr ** 2 + (r_mid_arr * da_rad) ** 2)
    fiber_cumul = np.concatenate([[0.0], np.cumsum(ds)])
    fiber_total = float(fiber_cumul[-1])

    points = [
        WindingPoint(
            x_mm=float(z_arr[i]),
            a_deg=float(a_arr[i]),
            feed=feed_mm_s,
            z_fiber=float(fiber_cumul[i]),
            layer=layer,
            circuit=circuit,
        )
        for i in range(n_steps)
    ]
    return points, float(a_arr[-1]), fiber_total
