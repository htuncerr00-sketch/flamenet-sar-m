"""
core/geometry_engine.py — Mandrel Geometri Motoru
==================================================
Dönel simetrik mandrel profillerini temsil eder.
Clairaut geodezik sarma hesaplamaları için temel sınıf.
"""
from __future__ import annotations
import math
import struct
from dataclasses import dataclass
from typing import Tuple

import numpy as np


@dataclass
class MandrelProfile:
    """
    Dönel mandrel kesit profili.
    z_mm: eksenel koordinatlar (N nokta, artan sırada)
    r_mm: her z konumundaki yarıçap
    """
    z_mm: np.ndarray
    r_mm: np.ndarray

    def __post_init__(self):
        self.z_mm = np.asarray(self.z_mm, dtype=np.float64)
        self.r_mm = np.asarray(self.r_mm, dtype=np.float64)
        if self.z_mm.shape != self.r_mm.shape:
            raise ValueError("z_mm ve r_mm aynı boyutta olmalı")
        if len(self.z_mm) < 2:
            raise ValueError("Profil en az 2 nokta içermeli")

    # ── Fabrika yöntemleri ─────────────────────────────────────────────────

    @classmethod
    def cylinder(cls, length_mm: float, radius_mm: float,
                 n_points: int = 200) -> 'MandrelProfile':
        """Düz silindirik mandrel."""
        z = np.linspace(0.0, length_mm, n_points)
        r = np.full(n_points, float(radius_mm))
        return cls(z, r)

    @classmethod
    def cone(cls, length_mm: float, r_start_mm: float, r_end_mm: float,
             n_points: int = 200) -> 'MandrelProfile':
        """Konik mandrel (baştan sona doğrusal yarıçap değişimi)."""
        z = np.linspace(0.0, length_mm, n_points)
        r = np.linspace(float(r_start_mm), float(r_end_mm), n_points)
        return cls(z, r)

    @classmethod
    def dome_cylinder_dome(cls, cyl_length_mm: float, cyl_radius_mm: float,
                           dome_height_mm: float,
                           n_points: int = 500) -> 'MandrelProfile':
        """
        Yarı-küresel kapaklar + silindirik gövde.
        Profil: z=0 merkez sol kubbe, z=total_L merkez sağ kubbe.
        """
        R = float(cyl_radius_mm)
        H = float(dome_height_mm)
        L = float(cyl_length_mm)
        n3 = n_points // 3

        # Sol kubbe: z ∈ [0, H], r(z) = sqrt(2Rz - z²) (hemisfer)
        z_dl = np.linspace(0.0, H, n3)
        r_dl = np.sqrt(np.maximum(0.0, 2.0 * R * z_dl - z_dl ** 2))

        # Silindir: z ∈ [H, H+L]
        z_cy = np.linspace(H, H + L, n3)
        r_cy = np.full(n3, R)

        # Sağ kubbe: z ∈ [H+L, H+L+H] — ters hemisfer
        rem = n_points - 2 * n3
        z_dr_local = np.linspace(0.0, H, rem)
        r_dr = np.sqrt(np.maximum(0.0, 2.0 * R * (H - z_dr_local) - (H - z_dr_local) ** 2))
        z_dr = (H + L) + z_dr_local

        z = np.concatenate([z_dl, z_cy, z_dr])
        r = np.concatenate([r_dl, r_cy, r_dr])
        # Çok küçük yarıçapları düzelt (kubbe uçları)
        r = np.maximum(r, R * 0.01)
        return cls(z, r)

    @classmethod
    def from_stl(cls, stl_path: str, n_points: int = 500) -> 'MandrelProfile':
        """STL dosyasını yükle → dönel simetrik yarıçap profili çıkar."""
        from .stl_processor import parse_stl_vertices
        verts = parse_stl_vertices(stl_path)
        return cls._profile_from_vertices(verts, n_points)

    @classmethod
    def _profile_from_vertices(cls, verts: np.ndarray,
                                n_points: int = 500) -> 'MandrelProfile':
        """Ham vertex dizisinden profil oluştur (Z ekseni dönme ekseni)."""
        if len(verts) == 0:
            raise ValueError("STL dosyası vertex içermiyor")
        x, y, z = verts[:, 0], verts[:, 1], verts[:, 2]
        r = np.sqrt(x ** 2 + y ** 2)
        z_min, z_max = z.min(), z.max()
        z_bins = np.linspace(z_min, z_max, n_points)
        bin_half = (z_max - z_min) / (n_points - 1) * 1.5
        r_profile = np.zeros(n_points)
        for i, zc in enumerate(z_bins):
            mask = np.abs(z - zc) <= bin_half
            r_profile[i] = r[mask].max() if mask.any() else 0.0
        # Sıfır değerleri komşudan interpolasyon ile doldur
        nz = r_profile > 0
        if nz.any():
            r_profile = np.interp(z_bins, z_bins[nz], r_profile[nz])
        return cls(z_bins, r_profile)

    @classmethod
    def ellipsoidal_dome_cylinder_dome(
        cls,
        cyl_length_mm: float,
        cyl_radius_mm: float,
        dome_hr_ratio: float = 1.0,
        n_points: int = 500,
    ) -> 'MandrelProfile':
        """
        Elipsoidal kapaklar + silindirik gövde — silindir kavşağında sürekli.

        dome_hr_ratio = kubbe_yüksekliği / silindir_yarıçapı
          1.0 → yarı küre  (hemispherical)
          0.5 → basık elipsoid  (ASME basınçlı kap profili)
          0.707 → Netting analizi optimumu

        Profil: r(z) = R · √(1 − ((H−z)/H)²)  ⟹  kavşakta her zaman r=R
        """
        R = float(cyl_radius_mm)
        H = R * float(dome_hr_ratio)
        if H <= 0:
            H = R * 0.01
        L = float(cyl_length_mm)
        n3 = n_points // 3
        rem = n_points - 2 * n3

        # Sol kubbe: z ∈ [0, H], kutup z=0'da (r=0), ekvator z=H'de (r=R)
        z_dl = np.linspace(0.0, H, n3)
        r_dl = R * np.sqrt(np.maximum(0.0, 1.0 - ((H - z_dl) / H) ** 2))

        # Silindir: z ∈ [H, H+L]
        z_cy = np.linspace(H, H + L, n3)
        r_cy = np.full(n3, R)

        # Sağ kubbe: z ∈ [H+L, 2H+L], ekvator z=H+L'de, kutup z=2H+L'de
        z_dr_loc = np.linspace(0.0, H, rem)
        r_dr = R * np.sqrt(np.maximum(0.0, 1.0 - (z_dr_loc / H) ** 2))
        z_dr = (H + L) + z_dr_loc

        z = np.concatenate([z_dl, z_cy, z_dr])
        r = np.concatenate([r_dl, r_cy, r_dr])
        r = np.maximum(r, R * 0.005)   # kutup ucunu sıfıra bırakma
        return cls(z, r)

    # ── Geometri sorgu yöntemleri ──────────────────────────────────────────

    def radius_at(self, z_mm: float) -> float:
        """Verilen eksenel konumdaki yarıçap (mm)."""
        return float(np.interp(z_mm, self.z_mm, self.r_mm))

    def perimeter_at(self, z_mm: float) -> float:
        """Verilen eksenel konumdaki çevre uzunluğu (mm)."""
        return 2.0 * math.pi * self.radius_at(z_mm)

    def arc_length(self, z0_mm: float, z1_mm: float,
                   n_steps: int = 200) -> float:
        """Yüzey meridyeni boyunca z0 → z1 yay uzunluğu (mm)."""
        z = np.linspace(z0_mm, z1_mm, n_steps)
        r = np.interp(z, self.z_mm, self.r_mm)
        dz = np.diff(z)
        dr = np.diff(r)
        return float(np.sum(np.sqrt(dz ** 2 + dr ** 2)))

    @property
    def length_mm(self) -> float:
        return float(self.z_mm[-1] - self.z_mm[0])

    @property
    def max_radius_mm(self) -> float:
        return float(self.r_mm.max())

    @property
    def min_radius_mm(self) -> float:
        """Profildeki minimum yarıçap (mm) — kubbe açıklığı (boss) tahmini."""
        return float(self.r_mm.min())

    @property
    def avg_radius_mm(self) -> float:
        return float(self.r_mm.mean())
