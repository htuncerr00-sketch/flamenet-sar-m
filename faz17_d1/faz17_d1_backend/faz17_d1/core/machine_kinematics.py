"""
core/machine_kinematics.py — Tam 4-Eksen Makine Kinematiği
============================================================
Gerçek filament sarma makinesinin 4-eksen kinematiğini uygular.

Eksenler
--------
    X (carriage)      : eksenel taşıyıcı, mm
    A (spindle)       : iş mili dönüşü, derece (kümülatif)
    Y (eye standoff)  : payout göz radyal mesafesi (yüzeye), mm
    B (eye steering)  : payout göz yönelim açısı (radyalden), derece

Fizik modeli
------------
Serbest fiber, gözden temas noktasına meridyen (r-z) düzleminde gider:
göz, temas noktasının radyal olarak `standoff` üstünde ve eksenel olarak
`lead` önündedir. Serbest fiber vektörü:

    fiber = temas_xyz − göz_xyz = (−standoff·cosφ, −standoff·sinφ, −lead·yön)
    |fiber| = √(standoff² + lead²)

SARMA AÇISI statik geometriyle DEĞİL, EKSEN SENKRONİZASYONU ile belirlenir:
temas noktasının yüzey hızının çevresel ve eksenel bileşenlerinin oranı.

    v_eksenel   = dz_c/dt              (taşıyıcı hızı)
    v_çevresel  = r_c · dφ_c/dt        (iş mili yüzey hızı)
    α (sarma açısı) = atan2(v_çevresel, v_eksenel)

LEAD TAZMİNATI: fiber, temas noktasının gerisinde α açısıyla yatar; göz
standoff kadar yukarıda olduğundan eksenel olarak öne alınmalıdır:

    lead = standoff · tan(α)
    B (steering) = atan2(lead, standoff) = α   (birinci derece)

Bu model forward(inverse(x)) ≈ x sağlar (round-trip tutarlı).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .geometry_engine import MandrelProfile
from .machine_calibration import MachineCalibration, default_calibration
from .path_generator import WindingPath, WindingPoint


# ── Eksen durumu ───────────────────────────────────────────────────────────────

@dataclass
class AxisState:
    """4-eksen makine komut durumu (mandrel datum çerçevesinde geometri)."""
    carriage_x_mm: float        # X — makine taşıyıcı konumu
    spindle_deg: float          # A — iş mili açısı (kümülatif)
    eye_standoff_mm: float      # Y — göz radyal mesafesi (yüzeye)
    eye_steer_deg: float        # B — göz yönelim açısı (radyalden)


@dataclass
class FiberContactGeometry:
    """Bir eksen durumundaki tam fiber-temas geometrisi."""
    contact_xyz: Tuple[float, float, float]   # temas noktası (mandrel datum)
    eye_xyz: Tuple[float, float, float]       # göz merkezi
    fiber_vector: Tuple[float, float, float]  # göz → temas
    fiber_free_length_mm: float               # serbest fiber uzunluğu
    contact_z_mm: float                       # temas eksenel konumu
    contact_r_mm: float                       # temas yarıçapı
    contact_phi_deg: float                    # temas azimutu (= iş mili açısı mod 360)
    contact_angle_deg: float                  # achieved sarma açısı (steering'den)
    lead_mm: float                            # eksenel öncülük
    surface_normal: Tuple[float, float, float]  # yüzey dış normali (birim)
    is_valid: bool
    issue: str = ""


# ── Yardımcılar ────────────────────────────────────────────────────────────────

def _surface_normal(profile: MandrelProfile, z_mm: float, phi_rad: float,
                    dz: float = 0.5) -> Tuple[float, float, float]:
    """
    Dönel yüzeyin dış birim normali (z, φ) noktasında.

    Meridyen eğimi r'(z) merkezi farkla; normal ∝ (cosφ, sinφ, −r').
    """
    r0 = profile.radius_at(z_mm - dz)
    r1 = profile.radius_at(z_mm + dz)
    rp = (r1 - r0) / (2.0 * dz)
    nx = math.cos(phi_rad)
    ny = math.sin(phi_rad)
    nz = -rp
    norm = math.sqrt(nx * nx + ny * ny + nz * nz)
    if norm < 1e-12:
        return (math.cos(phi_rad), math.sin(phi_rad), 0.0)
    return (nx / norm, ny / norm, nz / norm)


# ── Ters kinematik (CAM → eksen komutları) ────────────────────────────────────

def inverse_kinematics(
    contact_z_mm: float,
    contact_phi_deg: float,
    alpha_deg: float,
    profile: MandrelProfile,
    calib: Optional[MachineCalibration] = None,
    travel_dir: float = 1.0,
    standoff_mm: Optional[float] = None,
) -> Tuple[AxisState, FiberContactGeometry]:
    """
    İstenen temas noktası + sarma açısı için 4-eksen komutlarını hesapla.

    Parametreler
    ----------
    contact_z_mm    : Hedef temas noktası eksenel konumu (mandrel datum).
    contact_phi_deg : Hedef temas azimutu = iş mili açısı (kümülatif).
    alpha_deg       : İstenen sarma açısı (eksen yönünden).
    profile         : Mandrel geometrisi.
    calib           : Makine kalibrasyonu (None → varsayılan).
    travel_dir      : Taşıyıcı seyahat yönü (+1 / −1) — lead işareti.
    standoff_mm     : Göz standoff (None → calib.eye_base_standoff_mm).
    """
    if calib is None:
        calib = default_calibration()
    if standoff_mm is None:
        standoff_mm = calib.eye_base_standoff_mm

    r_c = profile.radius_at(contact_z_mm)
    alpha_rad = math.radians(max(0.0, min(alpha_deg, 89.5)))
    lead = standoff_mm * math.tan(alpha_rad)

    z_eye = contact_z_mm + travel_dir * lead
    steer_deg = math.degrees(math.atan2(lead, standoff_mm)) * (1.0 if travel_dir >= 0 else -1.0)

    axis = AxisState(
        carriage_x_mm=calib.mandrel_to_machine_z(z_eye),
        spindle_deg=contact_phi_deg,
        eye_standoff_mm=standoff_mm,
        eye_steer_deg=steer_deg,
    )

    geom = _build_geometry(
        contact_z_mm, contact_phi_deg, r_c, standoff_mm, lead,
        travel_dir, alpha_deg, profile,
    )
    return axis, geom


# ── İleri kinematik (eksen komutları → temas geometrisi) ──────────────────────

def forward_kinematics(
    axis: AxisState,
    profile: MandrelProfile,
    calib: Optional[MachineCalibration] = None,
    travel_dir: float = 1.0,
) -> FiberContactGeometry:
    """
    Verilen 4-eksen durumundan gerçekleşen fiber-temas geometrisini hesapla.

    ters_kinematik'in tersidir: forward(inverse(x)) ≈ x.
    """
    if calib is None:
        calib = default_calibration()

    standoff = axis.eye_standoff_mm
    z_eye = calib.machine_to_mandrel_z(axis.carriage_x_mm)
    steer_rad = math.radians(axis.eye_steer_deg)
    lead = standoff * math.tan(abs(steer_rad))
    dir_sign = 1.0 if axis.eye_steer_deg >= 0 else -1.0

    z_c = z_eye - dir_sign * lead
    r_c = profile.radius_at(z_c)
    alpha_deg = abs(axis.eye_steer_deg)  # steering ↔ sarma açısı (birinci derece)

    return _build_geometry(
        z_c, axis.spindle_deg, r_c, standoff, lead,
        dir_sign, alpha_deg, profile,
    )


def _build_geometry(
    contact_z_mm: float, contact_phi_deg: float, r_c: float,
    standoff_mm: float, lead_mm: float, travel_dir: float,
    alpha_deg: float, profile: MandrelProfile,
) -> FiberContactGeometry:
    """Temas + göz konumlarından tam 3B geometriyi kur."""
    phi = math.radians(contact_phi_deg % 360.0)
    cphi, sphi = math.cos(phi), math.sin(phi)

    contact_xyz = (r_c * cphi, r_c * sphi, contact_z_mm)
    r_eye = r_c + standoff_mm
    z_eye = contact_z_mm + travel_dir * lead_mm
    eye_xyz = (r_eye * cphi, r_eye * sphi, z_eye)

    fiber_vec = (
        contact_xyz[0] - eye_xyz[0],
        contact_xyz[1] - eye_xyz[1],
        contact_xyz[2] - eye_xyz[2],
    )
    fiber_len = math.sqrt(fiber_vec[0] ** 2 + fiber_vec[1] ** 2 + fiber_vec[2] ** 2)

    normal = _surface_normal(profile, contact_z_mm, phi)

    is_valid = r_eye > r_c + 1e-6
    issue = "" if is_valid else f"Göz mandrel içinde (r_göz={r_eye:.1f} <= r_temas={r_c:.1f})"

    return FiberContactGeometry(
        contact_xyz=contact_xyz,
        eye_xyz=eye_xyz,
        fiber_vector=fiber_vec,
        fiber_free_length_mm=fiber_len,
        contact_z_mm=contact_z_mm,
        contact_r_mm=r_c,
        contact_phi_deg=contact_phi_deg % 360.0,
        contact_angle_deg=alpha_deg,
        lead_mm=lead_mm,
        surface_normal=normal,
        is_valid=is_valid,
        issue=issue,
    )


# ── Eksen senkronizasyonu (sarma açısı = hız oranı) ───────────────────────────

@dataclass
class AxisSyncState:
    """Bir an için eksen hız senkronizasyon durumu."""
    carriage_v_mm_s: float       # eksenel hız (X)
    spindle_v_deg_s: float       # iş mili açısal hız (A)
    surface_v_circ_mm_s: float   # çevresel yüzey hızı = r·ω
    winding_angle_deg: float     # achieved sarma açısı = atan2(çevresel, eksenel)
    sync_ratio: float            # spindle_deg_s / carriage_mm_s (eksen kuplajı)


def compute_axis_synchronization(
    carriage_v_mm_s: float,
    spindle_v_deg_s: float,
    contact_r_mm: float,
) -> AxisSyncState:
    """
    Eksen hızlarından gerçekleşen sarma açısını hesapla.

    Sarma açısı eksen senkronizasyonunun sonucudur (statik geometri değil):
        v_çevresel = r · ω = r · (deg_s · π/180)
        α = atan2(v_çevresel, |v_eksenel|)
    """
    omega_rad_s = math.radians(spindle_v_deg_s)
    v_circ = contact_r_mm * omega_rad_s
    v_axial = carriage_v_mm_s
    alpha = math.degrees(math.atan2(abs(v_circ), abs(v_axial) + 1e-12))
    sync_ratio = spindle_v_deg_s / carriage_v_mm_s if abs(carriage_v_mm_s) > 1e-9 else float("inf")
    return AxisSyncState(
        carriage_v_mm_s=carriage_v_mm_s,
        spindle_v_deg_s=spindle_v_deg_s,
        surface_v_circ_mm_s=v_circ,
        winding_angle_deg=alpha,
        sync_ratio=sync_ratio,
    )


def required_spindle_speed_deg_s(
    carriage_v_mm_s: float,
    alpha_deg: float,
    contact_r_mm: float,
) -> float:
    """
    Verilen taşıyıcı hızı + sarma açısı için gereken iş mili hızı.

    v_çevresel = v_eksenel · tan(α);  ω = v_çevresel / r;  deg_s = ω·180/π
    """
    alpha_rad = math.radians(max(0.0, min(alpha_deg, 89.5)))
    v_circ = abs(carriage_v_mm_s) * math.tan(alpha_rad)
    omega = v_circ / max(contact_r_mm, 1e-6)
    return math.degrees(omega)


# ── Yol boyunca kinematik analizi ─────────────────────────────────────────────

@dataclass
class KinematicsReport:
    """Sarma yolu için 4-eksen kinematik analiz raporu."""
    n_points: int
    n_valid: int
    n_invalid: int
    max_contact_angle_deg: float
    min_contact_angle_deg: float
    max_lead_mm: float
    max_fiber_length_mm: float
    min_fiber_length_mm: float
    max_steer_deg: float
    mean_sync_ratio: float
    invalid_points: List[Tuple[float, str]]
    is_valid: bool
    warnings: List[str]

    def summary(self) -> str:
        status = "GEÇERLİ ✓" if self.is_valid else "GEÇERSİZ ✗"
        return (
            f"Kinematik: {status} ({self.n_valid}/{self.n_points} nokta) | "
            f"sarma_açısı=[{self.min_contact_angle_deg:.1f},{self.max_contact_angle_deg:.1f}]° | "
            f"maks_lead={self.max_lead_mm:.1f}mm | "
            f"maks_steer={self.max_steer_deg:.1f}° | "
            f"fiber_uzunluk=[{self.min_fiber_length_mm:.1f},{self.max_fiber_length_mm:.1f}]mm"
        )


def analyze_path_kinematics(
    path: WindingPath,
    profile: MandrelProfile,
    calib: Optional[MachineCalibration] = None,
    standoff_mm: Optional[float] = None,
    sample_every: int = 5,
) -> KinematicsReport:
    """
    Sarma yolu boyunca tam 4-eksen kinematik analizi.

    Her örneklenen noktada ters kinematik çözülür; sarma açısı ardışık
    noktaların eksen-senkronizasyonundan (hız oranı) hesaplanır.
    """
    if calib is None:
        calib = default_calibration()

    pts = path.points
    if len(pts) < 2:
        return KinematicsReport(
            n_points=0, n_valid=0, n_invalid=0,
            max_contact_angle_deg=0.0, min_contact_angle_deg=0.0,
            max_lead_mm=0.0, max_fiber_length_mm=0.0, min_fiber_length_mm=0.0,
            max_steer_deg=0.0, mean_sync_ratio=0.0,
            invalid_points=[], is_valid=False,
            warnings=["Yol < 2 nokta"],
        )

    sampled = pts[::sample_every] if len(pts) > sample_every else pts
    angles: List[float] = []
    leads: List[float] = []
    fiber_lens: List[float] = []
    steers: List[float] = []
    sync_ratios: List[float] = []
    invalid: List[Tuple[float, str]] = []
    n_valid = 0

    for i in range(len(sampled)):
        p = sampled[i]
        # Sarma açısını eksen senkronizasyonundan al (ardışık nokta hız oranı)
        if i + 1 < len(sampled):
            q = sampled[i + 1]
            dz = q.x_mm - p.x_mm
            da = q.a_deg - p.a_deg
            r_c = profile.radius_at(p.x_mm)
            # Ölçeklenmiş hız oranı (zaman sabiti düşse de oran korunur)
            sync = compute_axis_synchronization(dz, da, r_c)
            alpha = sync.winding_angle_deg
            travel_dir = 1.0 if dz >= 0 else -1.0
            sync_ratios.append(sync.sync_ratio if math.isfinite(sync.sync_ratio) else 0.0)
        else:
            alpha = angles[-1] if angles else 55.0
            travel_dir = 1.0

        axis, geom = inverse_kinematics(
            p.x_mm, p.a_deg, alpha, profile, calib,
            travel_dir=travel_dir, standoff_mm=standoff_mm,
        )
        angles.append(geom.contact_angle_deg)
        leads.append(geom.lead_mm)
        fiber_lens.append(geom.fiber_free_length_mm)
        steers.append(abs(axis.eye_steer_deg))
        if geom.is_valid:
            n_valid += 1
        else:
            invalid.append((p.x_mm, geom.issue))

    n_invalid = len(sampled) - n_valid
    warnings: List[str] = []
    if n_invalid > 0:
        warnings.append(f"{n_invalid} noktada göz-mandrel geometrisi geçersiz")
    if max(steers) > 60.0:
        warnings.append(f"Yüksek steering açısı (maks {max(steers):.1f}°) — göz büküm sınırı riski")

    return KinematicsReport(
        n_points=len(sampled),
        n_valid=n_valid,
        n_invalid=n_invalid,
        max_contact_angle_deg=max(angles) if angles else 0.0,
        min_contact_angle_deg=min(angles) if angles else 0.0,
        max_lead_mm=max(leads) if leads else 0.0,
        max_fiber_length_mm=max(fiber_lens) if fiber_lens else 0.0,
        min_fiber_length_mm=min(fiber_lens) if fiber_lens else 0.0,
        max_steer_deg=max(steers) if steers else 0.0,
        mean_sync_ratio=float(np.mean(sync_ratios)) if sync_ratios else 0.0,
        invalid_points=invalid,
        is_valid=(n_invalid == 0),
        warnings=warnings,
    )
