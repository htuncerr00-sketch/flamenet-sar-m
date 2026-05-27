"""
geometry.py — Mandrel Geometry Engine
======================================
Devrimli yüzey mandrel geometrisini tanımlar.

Koordinat sistemi:
  z   : eksenel koordinat [mm], 0'dan mandrel uzunluğuna kadar
  r   : yerel yarıçap [mm]  →  r(z) profil fonksiyonu
  r'  : dr/dz  [boyutsuz]
  G   : Birinci Temel Form metrik katsayısı = 1 + r'²  [boyutsuz]

Gelecekte eklenecek: DomeMandrel (isotensoid, elliptic), SphericalCap,
                     CustomProfileMandrel (spline-based)
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SurfacePoint:
    """
    Bir mandrel yüzey noktasında geometrik büyüklükler.
    Tüm türevler bu nesne üzerinden erişilir; dış kod r, r', G'yi
    ayrı ayrı sorgulamak zorunda kalmaz.
    """
    z:       float   # eksenel konum [mm]
    r:       float   # yarıçap [mm]
    r_prime: float   # dr/dz  [boyutsuz]
    G:       float   # metrik katsayı G = 1 + r'²  [boyutsuz]
    sqrt_G:  float   # √G  [boyutsuz]

    def __post_init__(self) -> None:
        # Tutarlılık kontrolü (frozen olduğu için sadece init'te çalışır)
        if self.r < 0:
            raise ValueError(f"r cannot be negative: r={self.r:.6f} mm at z={self.z:.3f} mm")
        if self.G < 1.0 - 1e-12:
            raise ValueError(f"G = 1+r'² must be ≥ 1, got G={self.G:.6f}")


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class MandrelGeometry(ABC):
    """
    Tüm mandrel geometrilerinin soyut temel sınıfı.

    Alt sınıflar şunları implemente etmelidir:
      - length      : float property
      - r_pole_left : float property  (sol polar açıklık yarıçapı)
      - r_pole_right: float property  (sağ polar açıklık yarıçapı)
      - surface_point(z): SurfacePoint

    Clairaut sabiti: c = r_pole  (winding_math.py tarafından hesaplanır)

    Dome desteği için domain genişlemesi:
      Silindir: r(z) = R = const  →  r' = 0, G = 1
      Dome:     r(z) = f(z)       →  r' = f'(z), G = 1 + f'(z)²
    """

    # --- Soyut arayüz ---

    @property
    @abstractmethod
    def length(self) -> float:
        """Mandrel toplam uzunluğu [mm]."""
        ...

    @property
    @abstractmethod
    def r_pole_left(self) -> float:
        """Sol polar açıklık yarıçapı [mm]. Saf silindir için 0."""
        ...

    @property
    @abstractmethod
    def r_pole_right(self) -> float:
        """Sağ polar açıklık yarıçapı [mm]. Saf silindir için 0."""
        ...

    @abstractmethod
    def surface_point(self, z: float) -> SurfacePoint:
        """z konumunda yüzey geometrisini hesapla."""
        ...

    # --- Ortak yardımcı metodlar ---

    def validate_z(self, z: float) -> None:
        """z geçerli mandrel sınırları içinde değilse ValueError fırlat."""
        if not (-1e-9 <= z <= self.length + 1e-9):
            raise ValueError(
                f"z={z:.4f} mm mandrel sınırları dışında "
                f"[0, {self.length:.4f} mm]"
            )

    def r_max(self) -> float:
        """Maksimum yarıçap (equatorial radius) [mm].
        
        Silindir için R'dir. Dome'lu yapılar için alt sınıf override edebilir.
        """
        return self.surface_point(self.length / 2.0).r

    def arc_length_element(self, z: float) -> float:
        """Arc-length infinitezimal: ds/dz = √G = √(1 + r'²).
        
        Kullanım: ds = arc_length_element(z) · dz
        """
        return self.surface_point(z).sqrt_G

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"L={self.length:.2f}mm, "
            f"r_pole_L={self.r_pole_left:.2f}mm, "
            f"r_pole_R={self.r_pole_right:.2f}mm)"
        )


# ---------------------------------------------------------------------------
# Cylindrical Mandrel (Faz 1)
# ---------------------------------------------------------------------------

class CylindricalMandrel(MandrelGeometry):
    """
    Sabit yarıçaplı silindirik mandrel: r(z) = R = const.

    Analitik özellikler:
      r(z)    = R           (sabit)
      r'(z)   = 0           (sıfır türev)
      G(z)    = 1           (Öklid metrik)
      √G(z)   = 1
      ds/dz   = 1           (arc-length = axial distance)

    Clairaut: c = R·sin(α)  (winding_math.py tarafından hesaplanır)
    Pitch:    p = 2πR/tan(α)

    Faz 2+ için ekleme:
      - Dome transition sınırı (z_dome_left, z_dome_right)
      - Chuck bölgesi maskeleme
    """

    def __init__(self, radius: float, length: float) -> None:
        """
        Args:
            radius: Silindir yarıçapı [mm]. Pozitif olmalı.
            length: Silindir uzunluğu [mm]. Pozitif olmalı.

        Raises:
            ValueError: Geçersiz parametreler için.
        """
        if radius <= 0.0:
            raise ValueError(f"radius must be > 0 mm, got {radius:.4f} mm")
        if length <= 0.0:
            raise ValueError(f"length must be > 0 mm, got {length:.4f} mm")

        self._radius: float = float(radius)
        self._length: float = float(length)

        # Silindir için sabit SurfacePoint: tekrar hesaplamaktan kaçın
        self._cached_point = SurfacePoint(
            z=0.0,          # z placeholder (her noktada aynı değerler)
            r=self._radius,
            r_prime=0.0,
            G=1.0,
            sqrt_G=1.0,
        )

    # --- Properties ---

    @property
    def radius(self) -> float:
        """Silindir yarıçapı [mm]."""
        return self._radius

    @property
    def length(self) -> float:
        return self._length

    @property
    def r_pole_left(self) -> float:
        """Silindir için polar açıklık yok → 0."""
        return 0.0

    @property
    def r_pole_right(self) -> float:
        return 0.0

    # --- Core method ---

    def surface_point(self, z: float) -> SurfacePoint:
        """
        z konumunda yüzey geometrisi.

        Silindir için tüm geometrik büyüklükler z'den bağımsız (sabit).
        Cache kullanımı: Yeni bir SurfacePoint oluşturmak yerine z alanını
        güncelleyemeyiz (frozen dataclass), bu yüzden yeni bir nesne oluşturuyoruz.
        Performans kritikse alt sınıf tuple döndürecek şekilde override edilebilir.

        Args:
            z: Eksenel konum [mm]

        Returns:
            SurfacePoint with r=R, r'=0, G=1 at given z
        """
        self.validate_z(z)
        # Not: frozen dataclass olduğu için replace() ile kopyalıyoruz
        return SurfacePoint(
            z=z,
            r=self._radius,
            r_prime=0.0,
            G=1.0,
            sqrt_G=1.0,
        )

    # --- Convenience methods ---

    def r_max(self) -> float:
        """Maksimum yarıçap = R."""
        return self._radius

    def arc_length_element(self, z: float) -> float:
        """Silindir için ds/dz = 1 (√G = 1)."""
        return 1.0  # Override: G=1 garantili, hesaplama gerekmez

    def total_arc_length(self) -> float:
        """Toplam mandrel arc-length = L (silindir için)."""
        return self._length

    def __repr__(self) -> str:
        return (
            f"CylindricalMandrel("
            f"radius={self._radius:.4f} mm, "
            f"length={self._length:.4f} mm)"
        )

    def summary(self) -> str:
        """İnsan okunabilir özet."""
        circumference = 2.0 * math.pi * self._radius
        return (
            f"Cylindrical Mandrel\n"
            f"  Radius:        {self._radius:.4f} mm\n"
            f"  Length:        {self._length:.4f} mm\n"
            f"  Circumference: {circumference:.4f} mm\n"
            f"  Aspect ratio:  {self._length / self._radius:.4f}\n"
            f"  r(z):          constant = {self._radius:.4f} mm\n"
            f"  G(z):          constant = 1.0000\n"
        )


# ---------------------------------------------------------------------------
# Validation helper (standalone, no import dependencies)
# ---------------------------------------------------------------------------

def validate_geometry_contract(mandrel: MandrelGeometry,
                                n_test_points: int = 10) -> None:
    """
    MandrelGeometry sözleşmesini doğrula.

    Kontrol edilen koşullar:
      1. surface_point() tüm geçerli z değerleri için çalışır
      2. r ≥ 0 her yerde
      3. G ≥ 1 her yerde
      4. sqrt_G = √G doğruluğu

    Args:
        mandrel: Test edilecek mandrel
        n_test_points: Kaç test noktası kullanılacağı
    """
    L = mandrel.length
    for i in range(n_test_points + 1):
        z = i * L / n_test_points
        sp = mandrel.surface_point(z)

        assert sp.r >= 0.0, f"r < 0 at z={z:.3f}"
        assert sp.G >= 1.0 - 1e-10, f"G < 1 at z={z:.3f}: G={sp.G:.8f}"
        assert abs(sp.sqrt_G - math.sqrt(sp.G)) < 1e-10, \
            f"sqrt_G mismatch at z={z:.3f}: {sp.sqrt_G} vs {math.sqrt(sp.G)}"
        assert abs(sp.G - (1.0 + sp.r_prime**2)) < 1e-10, \
            f"G = 1 + r'² mismatch at z={z:.3f}"
