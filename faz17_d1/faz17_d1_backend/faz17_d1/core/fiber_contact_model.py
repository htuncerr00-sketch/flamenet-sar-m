"""
core/fiber_contact_model.py — Gerçek Fiber Temas Modeli
========================================================
Fiber bandının mandrel yüzeyiyle gerçek temasını modellerder:
temas yaması (patch), bant kenar pozisyonları, yüzey projeksiyonu,
teğet sürekliliği kontrolü ve kenar sürüklenme takibi.

Geometri (yüzey tanjant düzlemi)
---------------------------------
Merkez temas noktası (z_c, φ_c, r_c)'de yüzey tanjant düzlemi için
iki ortonormal vektör:
    T_m : meridyen tanjantı   = normalize(dr/dz·cosφ, dr/dz·sinφ, 1)
    T_φ : çevresel tanjant    = (-sinφ, cosφ, 0)

Fiber yönü:    T_f = cos(α)·T_m + sin(α)·T_φ
Dik yön:       T_p = -sin(α)·T_m + cos(α)·T_φ

Sol/sağ kenar:  P_edge = P_center ± (W/2)·T_p   (3B Kartezyen)

Bu doğrudan 3B vektör işlemi; silindir, konik, kubbe için geçerlidir.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .fiber_band import FiberBand
from .geometry_engine import MandrelProfile
from .path_generator import WindingPath, WindingPoint


# ── Temas yaması ──────────────────────────────────────────────────────────────

@dataclass
class ContactPatch:
    """
    Bir temas noktasındaki fiber bandının yüzey yaması.

    Tüm koordinatlar mandrel datum çerçevesindedir.
    xyz : 3B Kartezyen (mm).  (z, φ, r) : mandrel silindrik.
    """
    # Merkez
    center_xyz:     Tuple[float, float, float]
    center_z_mm:    float
    center_phi_deg: float
    center_r_mm:    float

    # Kenarlar (3B)
    left_edge_xyz:  Tuple[float, float, float]
    right_edge_xyz: Tuple[float, float, float]

    # Kenar silindrik koordinatları
    left_z_mm:    float
    left_phi_deg: float
    left_r_mm:    float
    right_z_mm:   float
    right_phi_deg: float
    right_r_mm:   float

    # Bant ölçüleri
    bandwidth_axial_mm: float   # kenar-kenar eksenel aralık
    bandwidth_circ_mm:  float   # kenar-kenar çevresel yay

    # Tanjant vektörleri (birim, 3B Kartezyen)
    surface_normal:   Tuple[float, float, float]
    tangent_fiber:    Tuple[float, float, float]
    tangent_perp:     Tuple[float, float, float]

    # Sarma açısı
    alpha_deg: float


def _meridian_tangent_unit(profile: MandrelProfile, z_mm: float,
                           phi_rad: float, dz: float = 0.5) -> Tuple[float, float, float]:
    """Meridyen tanjant birim vektörü — 3B Kartezyen."""
    r0 = profile.radius_at(z_mm - dz)
    r1 = profile.radius_at(z_mm + dz)
    rp = (r1 - r0) / (2.0 * dz)       # dr/dz
    cphi, sphi = math.cos(phi_rad), math.sin(phi_rad)
    # dP/dz = (r'·cosφ, r'·sinφ, 1) için birim
    tx = rp * cphi
    ty = rp * sphi
    tz = 1.0
    norm = math.sqrt(tx * tx + ty * ty + tz * tz)
    return (tx / norm, ty / norm, tz / norm)


def _circumferential_tangent(phi_rad: float) -> Tuple[float, float, float]:
    """Çevresel tanjant birim vektörü — 3B Kartezyen."""
    return (-math.sin(phi_rad), math.cos(phi_rad), 0.0)


def _surface_normal_unit(profile: MandrelProfile, z_mm: float,
                         phi_rad: float, dz: float = 0.5) -> Tuple[float, float, float]:
    """Yüzey dış birim normali."""
    r0 = profile.radius_at(z_mm - dz)
    r1 = profile.radius_at(z_mm + dz)
    rp = (r1 - r0) / (2.0 * dz)
    cphi, sphi = math.cos(phi_rad), math.sin(phi_rad)
    # Normal ∝ T_m × T_φ = (r'cosφ,r'sinφ,1) × (-sinφ,cosφ,0) / normlar
    # = (-cosφ, -sinφ, r')  → dış normalin işareti: radyal dışa bakmalı
    nx = -rp * cphi * 0 + cphi    # gerçek çapraz çarpım:
    # T_m = (r'cosφ, r'sinφ, 1); T_φ = (-sinφ, cosφ, 0)
    # N = T_m × T_φ = (r'sinφ·0 - 1·cosφ, 1·(-sinφ) - r'cosφ·0, r'cosφ·cosφ - r'sinφ·(-sinφ))
    #               = (-cosφ, -sinφ, r')
    # Dış normal radyal dışa bakmalı → -N yönü
    nx2 = cphi
    ny2 = sphi
    nz2 = -rp
    norm = math.sqrt(nx2 * nx2 + ny2 * ny2 + nz2 * nz2)
    if norm < 1e-12:
        return (cphi, sphi, 0.0)
    return (nx2 / norm, ny2 / norm, nz2 / norm)


def compute_contact_patch(
    z_c_mm: float,
    phi_c_deg: float,
    alpha_deg: float,
    profile: MandrelProfile,
    band_width_mm: float,
) -> ContactPatch:
    """
    Bir temas noktasındaki fiber bandının tam yüzey yamasını hesapla.

    Parametreler
    ----------
    z_c_mm       : Temas merkezi eksenel konumu.
    phi_c_deg    : Temas merkezi azimutu (iş mili açısı mod 360).
    alpha_deg    : Sarma açısı (eksen yönünden, derece).
    profile      : Mandrel geometrisi.
    band_width_mm: Fiber bant genişliği (mm).
    """
    phi_rad = math.radians(phi_c_deg % 360.0)
    alpha_rad = math.radians(max(0.01, min(alpha_deg, 89.99)))
    r_c = profile.radius_at(z_c_mm)

    cphi, sphi = math.cos(phi_rad), math.sin(phi_rad)
    center_xyz = (r_c * cphi, r_c * sphi, z_c_mm)

    # Yüzey tanjant vektörleri
    T_m = _meridian_tangent_unit(profile, z_c_mm, phi_rad)
    T_phi = _circumferential_tangent(phi_rad)
    N = _surface_normal_unit(profile, z_c_mm, phi_rad)

    # Fiber yönü ve dik yön yüzey üzerinde
    ca, sa = math.cos(alpha_rad), math.sin(alpha_rad)
    T_f = (ca * T_m[0] + sa * T_phi[0],
           ca * T_m[1] + sa * T_phi[1],
           ca * T_m[2] + sa * T_phi[2])
    T_p = (-sa * T_m[0] + ca * T_phi[0],
           -sa * T_m[1] + ca * T_phi[1],
           -sa * T_m[2] + ca * T_phi[2])

    half_w = band_width_mm * 0.5

    # Sol kenar: +T_p yönünde
    left_xyz = (center_xyz[0] + half_w * T_p[0],
                center_xyz[1] + half_w * T_p[1],
                center_xyz[2] + half_w * T_p[2])
    # Sağ kenar: -T_p yönünde
    right_xyz = (center_xyz[0] - half_w * T_p[0],
                 center_xyz[1] - half_w * T_p[1],
                 center_xyz[2] - half_w * T_p[2])

    def _to_cylindrical(xyz: Tuple[float, float, float]) -> Tuple[float, float, float]:
        x, y, z = xyz
        r = math.sqrt(x * x + y * y)
        phi = math.degrees(math.atan2(y, x))
        return z, phi, r

    lz, lphi, lr = _to_cylindrical(left_xyz)
    rz, rphi, rr = _to_cylindrical(right_xyz)

    # Eksenel ve çevresel bant ölçüleri
    bw_axial = abs(lz - rz)
    bw_circ = r_c * math.radians(abs(lphi - rphi)) if r_c > 0 else 0.0

    return ContactPatch(
        center_xyz=center_xyz,
        center_z_mm=z_c_mm, center_phi_deg=phi_c_deg, center_r_mm=r_c,
        left_edge_xyz=left_xyz,  right_edge_xyz=right_xyz,
        left_z_mm=lz,   left_phi_deg=lphi,   left_r_mm=lr,
        right_z_mm=rz,  right_phi_deg=rphi,  right_r_mm=rr,
        bandwidth_axial_mm=bw_axial,
        bandwidth_circ_mm=bw_circ,
        surface_normal=N,
        tangent_fiber=T_f,
        tangent_perp=T_p,
        alpha_deg=alpha_deg,
    )


# ── Teğet sürekliliği kontrolü ────────────────────────────────────────────────

@dataclass
class TangentDiscontinuity:
    """Ardışık iki geçiş arasındaki teğet süreksizliği."""
    z_mm: float
    phi_deg: float
    jump_deg: float     # teğet yön değişimi (derece)
    is_critical: bool   # eşiği aşıyor mu?


def check_tangent_continuity(
    path: WindingPath,
    profile: MandrelProfile,
    threshold_deg: float = 5.0,
    sample_every: int = 10,
) -> List[TangentDiscontinuity]:
    """
    Sarma yolunun devre geçişlerinde teğet sürekliliğini kontrol et.

    Ardışık iki devrenin birleştiği noktada fiber yön değişimi hesaplanır;
    `threshold_deg` üzerindeyse köprülenme/yığılma riski olarak işaretlenir.
    """
    pts = path.points
    if len(pts) < 2:
        return []

    # Devre sınırlarını bul (circuit değiştiği noktalar)
    discontinuities: List[TangentDiscontinuity] = []
    sampled = pts[::max(1, sample_every)]

    for i in range(1, len(sampled)):
        p_prev = sampled[i - 1]
        p_curr = sampled[i]
        if p_prev.circuit == p_curr.circuit:
            continue  # aynı devre içi — atla

        phi_prev = math.radians(p_prev.a_deg % 360.0)
        phi_curr = math.radians(p_curr.a_deg % 360.0)
        r_prev = profile.radius_at(p_prev.x_mm)
        r_curr = profile.radius_at(p_curr.x_mm)

        T_m_prev = _meridian_tangent_unit(profile, p_prev.x_mm, phi_prev)
        T_m_curr = _meridian_tangent_unit(profile, p_curr.x_mm, phi_curr)

        # Fiber teğet vektörü: T_f = cos(α)·T_m + sin(α)·T_φ
        # α tahmini: iki noktadan hız oranı
        dz = p_curr.x_mm - p_prev.x_mm
        da_rad = math.radians(p_curr.a_deg - p_prev.a_deg)
        r_mid = (r_prev + r_curr) * 0.5
        v_circ = r_mid * da_rad
        alpha_mid = math.atan2(abs(v_circ), abs(dz) + 1e-12)

        T_phi_prev = _circumferential_tangent(phi_prev)
        T_phi_curr = _circumferential_tangent(phi_curr)

        ca, sa = math.cos(alpha_mid), math.sin(alpha_mid)
        Tf_p = (ca * T_m_prev[0] + sa * T_phi_prev[0],
                ca * T_m_prev[1] + sa * T_phi_prev[1],
                ca * T_m_prev[2] + sa * T_phi_prev[2])
        Tf_c = (ca * T_m_curr[0] + sa * T_phi_curr[0],
                ca * T_m_curr[1] + sa * T_phi_curr[1],
                ca * T_m_curr[2] + sa * T_phi_curr[2])

        dot = sum(a * b for a, b in zip(Tf_p, Tf_c))
        jump_deg = math.degrees(math.acos(max(-1.0, min(1.0, dot))))

        if jump_deg > 0.1:
            discontinuities.append(TangentDiscontinuity(
                z_mm=(p_prev.x_mm + p_curr.x_mm) * 0.5,
                phi_deg=((p_prev.a_deg + p_curr.a_deg) * 0.5) % 360.0,
                jump_deg=jump_deg,
                is_critical=jump_deg > threshold_deg,
            ))

    return discontinuities


# ── Bant kenar takibi ─────────────────────────────────────────────────────────

@dataclass
class BandEdgeState:
    """Tek bir devre için bant kenar pozisyonları (başlangıç ve bitiş)."""
    circuit: int
    layer: int
    start_left_z_mm:  float
    start_right_z_mm: float
    end_left_z_mm:    float
    end_right_z_mm:   float
    gap_to_prev_mm:   float   # sol kenar ile bir önceki sağ kenar arası boşluk
    overlap_mm:       float   # pozitif → bindirme, negatif → boşluk
    edge_drift_mm:    float   # bu devredeki sol kenar sürüklenmesi


@dataclass
class BandEdgeReport:
    """Sarma yolunun tamamı için bant kenar takip raporu."""
    edges: List[BandEdgeState]
    total_edge_drift_mm: float       # birikimli sol kenar sürüklenmesi
    max_gap_mm: float                # en büyük kenarlar arası boşluk
    max_overlap_mm: float            # en büyük bindirme
    rms_overlap_mm: float            # bindirme tekdüzelik ölçüsü
    n_gap_circuits: int              # boşluklu devre sayısı
    n_excess_circuits: int           # aşırı bindirmeli devre sayısı
    is_uniform: bool                 # kaplama tekdüze mi?
    warnings: List[str]

    def summary(self) -> str:
        status = "TEKDÜZE ✓" if self.is_uniform else "DÜZENSİZ ✗"
        return (
            f"BantKenar: {status} | {len(self.edges)} devre | "
            f"sürüklenme={self.total_edge_drift_mm:.2f}mm | "
            f"maks_boşluk={self.max_gap_mm:.2f}mm | "
            f"maks_bindirme={self.max_overlap_mm:.2f}mm | "
            f"rms_bindirme={self.rms_overlap_mm:.2f}mm"
        )


def track_band_edges(
    path: WindingPath,
    profile: MandrelProfile,
    band: FiberBand,
    sample_every: int = 5,
    max_gap_mm: float = 0.5,
    max_excess_mm: float = 2.0,
) -> BandEdgeReport:
    """
    Sarma yolu boyunca bant kenar pozisyonlarını takip et.

    Her devre için sol ve sağ kenarlar hesaplanır; ardışık devreler
    arasındaki boşluk ve bindirme ölçülür. Kenar sürüklenmesi birikimli
    olarak izlenir (kalibrasyon hatası veya Clairaut sapması göstergesi).
    """
    pts = path.points
    if len(pts) < 2:
        return BandEdgeReport(
            edges=[], total_edge_drift_mm=0.0, max_gap_mm=0.0,
            max_overlap_mm=0.0, rms_overlap_mm=0.0, n_gap_circuits=0,
            n_excess_circuits=0, is_uniform=True,
            warnings=["Yol < 2 nokta"],
        )

    # Devrelere göre grupla
    circuits: dict = {}
    for pt in pts:
        key = (pt.layer, pt.circuit)
        circuits.setdefault(key, []).append(pt)

    W = band.tow_width_mm
    edges: List[BandEdgeState] = []
    prev_right_z: Optional[float] = None
    total_drift = 0.0

    for key in sorted(circuits.keys()):
        cpts = circuits[key][::max(1, sample_every)]
        if len(cpts) < 2:
            continue

        # İlk ve son noktada temas yaması hesapla
        p_start = cpts[0]
        p_end = cpts[-1]

        # α tahmini: ardışık noktalardan
        dz = cpts[1].x_mm - cpts[0].x_mm
        da_rad = math.radians(cpts[1].a_deg - cpts[0].a_deg)
        r_mid = profile.radius_at(cpts[0].x_mm)
        v_circ = r_mid * da_rad
        alpha_deg = math.degrees(math.atan2(abs(v_circ), abs(dz) + 1e-12))
        alpha_deg = max(1.0, min(alpha_deg, 89.0))

        patch_s = compute_contact_patch(
            p_start.x_mm, p_start.a_deg % 360.0, alpha_deg, profile, W)
        patch_e = compute_contact_patch(
            p_end.x_mm, p_end.a_deg % 360.0, alpha_deg, profile, W)

        sl_z = patch_s.left_z_mm
        sr_z = patch_s.right_z_mm
        el_z = patch_e.left_z_mm
        er_z = patch_e.right_z_mm

        gap = 0.0
        overlap = 0.0
        if prev_right_z is not None:
            gap_raw = sl_z - prev_right_z
            if gap_raw >= 0:
                gap = gap_raw
                overlap = 0.0
            else:
                gap = 0.0
                overlap = -gap_raw

        drift = sl_z - (prev_right_z + 0.0 if prev_right_z is not None else sl_z)
        total_drift += abs(drift) if prev_right_z is not None else 0.0

        edges.append(BandEdgeState(
            circuit=key[1], layer=key[0],
            start_left_z_mm=sl_z, start_right_z_mm=sr_z,
            end_left_z_mm=el_z,   end_right_z_mm=er_z,
            gap_to_prev_mm=gap, overlap_mm=overlap,
            edge_drift_mm=abs(drift) if prev_right_z is not None else 0.0,
        ))
        prev_right_z = sl_z + W  # nominal next expected right edge

    if not edges:
        return BandEdgeReport(
            edges=[], total_edge_drift_mm=0.0, max_gap_mm=0.0,
            max_overlap_mm=0.0, rms_overlap_mm=0.0, n_gap_circuits=0,
            n_excess_circuits=0, is_uniform=True, warnings=[],
        )

    gaps = [e.gap_to_prev_mm for e in edges]
    overlaps = [e.overlap_mm for e in edges]
    n_gap = sum(1 for g in gaps if g > max_gap_mm)
    n_excess = sum(1 for o in overlaps if o > max_excess_mm)

    warns: List[str] = []
    if n_gap > 0:
        warns.append(f"{n_gap} devrede kenar boşluğu > {max_gap_mm:.1f}mm (kuru fiber riski)")
    if n_excess > 0:
        warns.append(f"{n_excess} devrede aşırı bindirme > {max_excess_mm:.1f}mm (reçine yığılma)")
    if total_drift > W * 0.5:
        warns.append(
            f"Birikimli kenar sürüklenmesi {total_drift:.2f}mm > W/2 ({W/2:.2f}mm) — "
            f"kalibrasyon veya Clairaut sapmasi"
        )

    rms = float(np.sqrt(np.mean(np.array(overlaps) ** 2))) if overlaps else 0.0

    return BandEdgeReport(
        edges=edges,
        total_edge_drift_mm=total_drift,
        max_gap_mm=float(max(gaps)) if gaps else 0.0,
        max_overlap_mm=float(max(overlaps)) if overlaps else 0.0,
        rms_overlap_mm=rms,
        n_gap_circuits=n_gap,
        n_excess_circuits=n_excess,
        is_uniform=(n_gap == 0 and n_excess == 0 and total_drift <= W * 0.5),
        warnings=warns,
    )
