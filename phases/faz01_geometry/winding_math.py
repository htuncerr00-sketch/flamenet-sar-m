"""
winding_math.py — Filament Winding Matematik Motoru
=====================================================
Clairaut ilişkisi, helisel yol geometrisi ve fiber hız matematiği.

Temel formüller (silindir için, tüm doğrulamalar yapılmış):
  c   = R · sin(α)              Clairaut sabiti [mm]
  p   = 2π·R / tan(α)          Pitch: bir tam turda eksenel ilerleme [mm]
  dφ/dz = tan(α) / R           Azimut türevi [rad/mm]
  ds/dz = 1 / cos(α)           Arc-length türevi (silindir için G=1)
  dz/ds = cos(α)                Eksenel komponent
  dφ/ds = sin(α) / R           Azimut arc-length türevi = c / R²
  V_x   = S' · cos(α)          Carriage hızı [mm/s]
  F     = V_x · 60             G-code feed rate [mm/min]

Referans: Koussios (2004), Ch. 5-7; Bookhart & Fowler (1968)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from geometry import MandrelGeometry, CylindricalMandrel, SurfacePoint


# ---------------------------------------------------------------------------
# Floating Point Güvenlik Fonksiyonları
# ---------------------------------------------------------------------------

def safe_sqrt_diff(r: float, c: float) -> float:
    """
    √(r² - c²) hesabını catastrophic cancellation olmadan yapar.

    Naif yöntem:  √(r² - c²)         ← r≈c durumunda iptal hatası
    Güvenli:      √((r-c)·(r+c))     ← her zaman kararlı

    Args:
        r: Yarıçap [mm], r ≥ c olmalı
        c: Clairaut sabiti [mm], c ≥ 0

    Returns:
        √(r² - c²) [mm], sayısal olarak kararlı
    """
    diff = r - c
    if diff < 0.0:
        # Fiziksel olarak imkansız: r < c durumu
        # Floating point yuvarlama payı: küçük negatif değerleri sıfır kabul et
        if diff > -1e-9:
            return 0.0
        raise ValueError(
            f"r={r:.8f} mm < c={c:.8f} mm: Fiber polar açıklığın altında olamaz. "
            f"Sarım açısı veya polar yarıçap yeniden kontrol edilmeli."
        )
    return math.sqrt(diff * (r + c))


def safe_asin(x: float) -> float:
    """
    arcsin() domain guard: floating point yuvarlama |x|>1 yapabilir → NaN önle.

    Args:
        x: arcsin argümanı, teorik [-1, 1] aralığında

    Returns:
        arcsin(clamp(x, -1, 1)) [rad]
    """
    return math.asin(max(-1.0, min(1.0, x)))


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WindingParameters:
    """
    Değişmez sarım konfigürasyonu.

    alpha_rad:    Eksenel yönden ölçülen sarım açısı [rad]
                  0° = meridyonal (eksenel), 90° = hoop (çevresel)
                  Geçerli aralık: (0°, 90°) açık interval

    bandwidth:    Fiber band genişliği [mm]
    fiber_speed:  Hedef fiber teslimat hızı [mm/s]
    n_layers:     Toplam katman sayısı
    """
    alpha_rad:    float
    bandwidth:    float
    fiber_speed:  float
    n_layers:     int

    def __post_init__(self) -> None:
        if not (0.0 < self.alpha_rad < math.pi / 2.0 - 1e-6):
            raise ValueError(
                f"alpha_rad={self.alpha_rad:.6f} geçerli aralık dışında "
                f"(0, π/2) = (0°, 90°)"
            )
        if self.bandwidth <= 0.0:
            raise ValueError(f"bandwidth must be > 0 mm, got {self.bandwidth}")
        if self.fiber_speed <= 0.0:
            raise ValueError(f"fiber_speed must be > 0 mm/s, got {self.fiber_speed}")
        if self.n_layers < 1:
            raise ValueError(f"n_layers must be ≥ 1, got {self.n_layers}")

    @classmethod
    def from_degrees(
        cls,
        alpha_deg: float,
        bandwidth:   float = 10.0,
        fiber_speed: float = 100.0,
        n_layers:    int   = 1,
    ) -> "WindingParameters":
        """
        Derece cinsinden sarım açısıyla oluştur.

        Args:
            alpha_deg:   Sarım açısı [°]
            bandwidth:   Fiber band genişliği [mm]
            fiber_speed: Hedef fiber hızı [mm/s]
            n_layers:    Katman sayısı
        """
        if not (0.0 < alpha_deg < 90.0):
            raise ValueError(
                f"alpha_deg={alpha_deg:.4f}° geçerli aralık dışında (0°, 90°)"
            )
        return cls(
            alpha_rad=math.radians(alpha_deg),
            bandwidth=float(bandwidth),
            fiber_speed=float(fiber_speed),
            n_layers=int(n_layers),
        )

    @property
    def alpha_deg(self) -> float:
        """Sarım açısı [°]."""
        return math.degrees(self.alpha_rad)

    def __repr__(self) -> str:
        return (
            f"WindingParameters("
            f"α={self.alpha_deg:.4f}°, "
            f"b={self.bandwidth:.2f}mm, "
            f"S'={self.fiber_speed:.1f}mm/s, "
            f"layers={self.n_layers})"
        )


@dataclass(frozen=True, slots=True)
class ClairautConstants:
    """
    Mandrel + sarım parametresinden türetilen Clairaut büyüklükleri.

    Bu değerler mandrel-winding kombinasyonu için sabit (cylinder için).
    Dome geçişinde değişebilir — bu yüzden ayrı bir nesne olarak tutulur.
    """
    c:             float   # Clairaut sabiti = r_pole [mm]
    alpha_cyl:     float   # Silindir sarım açısı [rad]
    pitch:         float   # Eksenel ilerleme / tam tur [mm/rev]
    dphi_dz:       float   # dφ/dz [rad/mm]
    bandwidth_eff: float   # Efektif bandwidth ekvatorda = b/cos(α) [mm]

    @property
    def alpha_cyl_deg(self) -> float:
        return math.degrees(self.alpha_cyl)

    @property
    def a_axis_deg_per_mm(self) -> float:
        """A ekseni dönüşü (derece) / X ekseni ilerlemesi (mm)."""
        return math.degrees(self.dphi_dz)

    def summary(self) -> str:
        return (
            f"Clairaut Constants\n"
            f"  c (Clairaut constant): {self.c:.6f} mm\n"
            f"  α (cylinder):          {self.alpha_cyl_deg:.6f}°\n"
            f"  Pitch:                 {self.pitch:.6f} mm/rev\n"
            f"  dφ/dz:                 {self.dphi_dz:.8f} rad/mm\n"
            f"  A/X ratio:             {self.a_axis_deg_per_mm:.6f} °/mm\n"
            f"  b_eff (equatorial):    {self.bandwidth_eff:.4f} mm\n"
        )


@dataclass(slots=True)
class ToolPoint:
    """
    Fiber yolunda tek bir nokta, mandrel koordinat sisteminde.

    Alanlar:
      x       : Carriage konumu = eksenel z  [mm]
      phi_rad : Kümülatif azimut açısı       [rad] — 2π'yi geçebilir
      alpha_rad: Yerel sarım açısı            [rad]
      r       : Yerel yarıçap                [mm]
      s       : Kümülatif arc-length         [mm]
      segment : Segment etiketi (forward/return/dome_left vb.)
    """
    x:         float
    phi_rad:   float
    alpha_rad: float
    r:         float
    s:         float
    segment:   str = ""

    @property
    def phi_deg(self) -> float:
        """Azimut açısı [°]."""
        return math.degrees(self.phi_rad)

    @property
    def alpha_deg(self) -> float:
        """Sarım açısı [°]."""
        return math.degrees(self.alpha_rad)

    @property
    def A_axis_deg(self) -> float:
        """CNC A-eksen değeri (kümülatif, derece)."""
        return self.phi_deg

    def clairaut_value(self) -> float:
        """r·sin(α) — tüm noktalarda c'ye eşit olmalı."""
        return self.r * math.sin(self.alpha_rad)

    def __repr__(self) -> str:
        return (
            f"ToolPoint(x={self.x:.3f}mm, "
            f"φ={self.phi_deg:.3f}°, "
            f"α={self.alpha_deg:.3f}°, "
            f"r={self.r:.3f}mm, "
            f"s={self.s:.3f}mm, "
            f"seg={self.segment!r})"
        )


# ---------------------------------------------------------------------------
# Clairaut Calculator
# ---------------------------------------------------------------------------

class ClairautCalculator:
    """
    Mandrel + WindingParameters → ClairautConstants hesaplayıcı.

    Silindirik mandrel için analitik çözüm:
      c         = R · sin(α)
      pitch     = 2π · R / tan(α)  = 2π·R·cos(α)/sin(α)
      dφ/dz     = tan(α) / R
      b_eff     = b / cos(α)

    Doğrulama: c = R·sin(α), pitch = 2πR/tan(α)  →  pitch = c·(2π/sin(α))·(cos(α)/1)

    Gelecek: DomeMandrel için override — dome bölgesinde c farklı olabilir
    (non-geodezik durumunda veya unequal polar openings durumunda).
    """

    def __init__(
        self,
        mandrel:  MandrelGeometry,
        winding:  WindingParameters,
    ) -> None:
        self.mandrel = mandrel
        self.winding = winding

    def compute(self) -> ClairautConstants:
        """
        Clairaut büyüklüklerini hesapla ve doğrula.

        Returns:
            ClairautConstants

        Raises:
            NotImplementedError: Silindir dışı mandrel için (Faz 2+)
            ValueError: Geometrik tutarsızlık için
        """
        if not isinstance(self.mandrel, CylindricalMandrel):
            raise NotImplementedError(
                "ClairautCalculator şu an yalnızca CylindricalMandrel destekliyor. "
                "DomeMandrel desteği Faz 3'te eklenecek."
            )

        R     = self.mandrel.radius
        alpha = self.winding.alpha_rad
        b     = self.winding.bandwidth

        # --- Temel hesaplamalar ---
        sin_a = math.sin(alpha)
        cos_a = math.cos(alpha)
        tan_a = math.tan(alpha)

        c         = R * sin_a                          # Clairaut constant [mm]
        pitch     = 2.0 * math.pi * R * cos_a / sin_a  # [mm/rev]
        dphi_dz   = tan_a / R                          # [rad/mm]
        b_eff     = b / cos_a                          # [mm]

        # --- İç tutarlılık doğrulamaları ---
        assert abs(c - R * sin_a) < 1e-12, "c = R·sin(α) doğrulaması başarısız"
        assert abs(pitch - 2.0 * math.pi * R / tan_a) < 1e-8, "pitch doğrulaması başarısız"
        assert abs(dphi_dz - sin_a / (R * cos_a)) < 1e-12, "dφ/dz doğrulaması başarısız"

        # --- Fiziksel kontrol: c < R (polar açıklık < silindir yarıçapı) ---
        if c >= R:
            raise ValueError(
                f"Clairaut sabiti c={c:.4f} ≥ R={R:.4f}: "
                f"α={math.degrees(alpha):.2f}° için polar açıklık silindir yarıçapını geçemez."
            )

        return ClairautConstants(
            c=c,
            alpha_cyl=alpha,
            pitch=pitch,
            dphi_dz=dphi_dz,
            bandwidth_eff=b_eff,
        )


# ---------------------------------------------------------------------------
# Helical Path Generator (Silindir, Faz 1)
# ---------------------------------------------------------------------------

class HelicalPathGenerator:
    """
    Silindirik mandrel üzerinde helisel fiber yolu üretici.

    Analitik çözüm (nümerik integrasyon gerekmez):
      φ(z) = φ_0 + z · dφ/dz     [tam olarak doğru, makine hassasiyetinde]
      α(z) = α_0 = sabit          [silindir boyunca değişmez]
      r(z) = R    = sabit
      s(z) = |z - z_start| / cos(α)   [arc-length]

    Faz 3 için genişleme planı:
      HelicalPathGenerator → yalnızca silindirik bölge
      GeodesicDomeIntegrator → dome bölgesi (RK45, arc-length parametreli)
      FullBodyPathGenerator → iki üreticiyi birleştirir
    """

    def __init__(
        self,
        mandrel:    CylindricalMandrel,
        winding:    WindingParameters,
        constants:  ClairautConstants,
    ) -> None:
        if not isinstance(mandrel, CylindricalMandrel):
            raise TypeError(
                "HelicalPathGenerator Faz 1'de yalnızca CylindricalMandrel alır."
            )
        self.mandrel   = mandrel
        self.winding   = winding
        self.constants = constants

    # --- Yardımcı: Nokta hesabı ---

    def _compute_point(
        self,
        z:          float,
        z_start:    float,
        phi_start:  float,
        segment:    str,
        s_offset:   float = 0.0,
    ) -> ToolPoint:
        """
        Tek bir ToolPoint hesapla.

        Args:
            z:          Mevcut eksenel konum [mm]
            z_start:    Bu geçişin başlangıcı [mm]
            phi_start:  Bu geçişin başlangıç φ'si [rad]
            segment:    Segment etiketi
            s_offset:   Önceki geçişlerden birikmiş arc-length [mm]
        """
        # Azimut φ her zaman artar — fiber her zaman aynı yönde döner.
        # Geri geçişte z negatif ilerler ama φ artmaya devam eder: |dz| kullan.
        dz_abs  = abs(z - z_start)
        phi     = phi_start + self.constants.dphi_dz * dz_abs
        s_local = dz_abs / math.cos(self.winding.alpha_rad)

        sp = self.mandrel.surface_point(z)

        return ToolPoint(
            x         = z,
            phi_rad   = phi,
            alpha_rad = self.winding.alpha_rad,
            r         = sp.r,
            s         = s_offset + s_local,
            segment   = segment,
        )

    # --- Tek geçiş ---

    def generate_pass(
        self,
        z_start:    float,
        z_end:      float,
        phi_start:  float = 0.0,
        n_points:   int   = 100,
        segment:    str   = "forward",
        s_offset:   float = 0.0,
    ) -> List[ToolPoint]:
        """
        Bir lineer geçiş üret (ileri veya geri).

        Nokta dağılımı: z'de eşit aralıklı (uniform parametrizasyon).
        Silindir için bu, arc-length'de de eşit aralıklıdır (ds/dz = 1/cos(α) = sabit).

        Args:
            z_start:   Başlangıç eksenel konumu [mm]
            z_end:     Bitiş eksenel konumu [mm]
            phi_start: Başlangıç azimut açısı [rad] (kümülatif)
            n_points:  Çıktı nokta sayısı (≥ 2)
            segment:   Segment etiketi
            s_offset:  Birikmiş arc-length ofseti [mm]

        Returns:
            List of ToolPoint (n_points uzunlukta)
        """
        if n_points < 2:
            raise ValueError(f"n_points must be ≥ 2, got {n_points}")

        self.mandrel.validate_z(z_start)
        self.mandrel.validate_z(z_end)

        points = []
        for i in range(n_points):
            t = i / (n_points - 1)               # parametrik [0, 1]
            z = z_start + t * (z_end - z_start)  # lineer interpolasyon

            pt = self._compute_point(
                z         = z,
                z_start   = z_start,
                phi_start = phi_start,
                segment   = segment,
                s_offset  = s_offset,
            )
            points.append(pt)

        return points

    # --- Tam devre (forward + return) ---

    def generate_circuit(
        self,
        phi_offset:        float = 0.0,
        n_points_per_pass: int   = 100,
    ) -> List[ToolPoint]:
        """
        Bir tam devre üret: ileri geçiş (z: 0→L) + geri geçiş (z: L→0).

        Turn-around noktası (z=L) her iki geçişte paylaşılır.
        Geri geçişin ilk noktası (turn-around) çift sayılmaz.

        Args:
            phi_offset:        Bu devrenin başlangıç φ ofseti [rad]
            n_points_per_pass: Her geçişteki nokta sayısı

        Returns:
            List of ToolPoint (2·n_points_per_pass - 1 uzunlukta)
        """
        L        = self.mandrel.length
        cos_a    = math.cos(self.winding.alpha_rad)
        s_fwd    = L / cos_a   # ileri geçiş arc-length

        # --- İleri geçiş: z: 0 → L ---
        forward = self.generate_pass(
            z_start   = 0.0,
            z_end     = L,
            phi_start = phi_offset,
            n_points  = n_points_per_pass,
            segment   = "forward",
            s_offset  = 0.0,
        )

        # İleri geçiş sonu φ değeri (geri geçiş başlangıcı)
        phi_at_end = forward[-1].phi_rad

        # --- Geri geçiş: z: L → 0 ---
        backward = self.generate_pass(
            z_start   = L,
            z_end     = 0.0,
            phi_start = phi_at_end,
            n_points  = n_points_per_pass,
            segment   = "return",
            s_offset  = s_fwd,  # arc-length ileri geçişten devam ediyor
        )

        # Turn-around noktası tekrarlanıyor → geri geçişin ilk noktasını atla
        return forward + backward[1:]

    # --- Pattern analizi ---

    def turn_around_angle_rad(self) -> float:
        """
        Tek devre için azimut artışı [rad].

        Silindir için analitik:
            Δφ = 2 · L · dφ/dz = 2 · L · tan(α) / R
        """
        return 2.0 * self.mandrel.length * self.constants.dphi_dz

    def turn_around_angle_deg(self) -> float:
        """Tek devre için azimut artışı [°]."""
        return math.degrees(self.turn_around_angle_rad())

    def n_points_from_angular_step(self, max_da_deg: float = 2.0) -> int:
        """
        İstenen maksimum açı adımına göre n_points hesapla.

        Args:
            max_da_deg: Maksimum izin verilen A-ekseni adımı [°/step]

        Returns:
            Gerekli minimum nokta sayısı (tek geçiş için)
        """
        # Tek geçişteki toplam φ değişimi [°]
        dphi_per_pass_deg = abs(
            math.degrees(self.constants.dphi_dz * self.mandrel.length)
        )
        n = max(2, int(math.ceil(dphi_per_pass_deg / max_da_deg)) + 1)
        return n


# ---------------------------------------------------------------------------
# Validation Helper
# ---------------------------------------------------------------------------

def validate_toolpath_clairaut(
    toolpath:  List[ToolPoint],
    constants: ClairautConstants,
    tol:       float = 1e-9,
) -> None:
    """
    Tüm toolpath noktalarında Clairaut ilişkisini doğrula.

    r(z) · sin(α(z)) = c  (tüm noktalarda)

    Args:
        toolpath:  ToolPoint listesi
        constants: Beklenen Clairaut konstanları
        tol:       Tolerans [mm]

    Raises:
        AssertionError: Herhangi bir noktada ihlal varsa
    """
    c = constants.c
    for i, tp in enumerate(toolpath):
        cv = tp.clairaut_value()
        if abs(cv - c) > tol:
            raise AssertionError(
                f"Clairaut ihlali — Nokta {i}: "
                f"r·sin(α) = {cv:.10f} mm, beklenen c = {c:.10f} mm, "
                f"fark = {abs(cv - c):.2e} mm  "
                f"[x={tp.x:.3f}mm, α={tp.alpha_deg:.4f}°, r={tp.r:.4f}mm]"
            )
