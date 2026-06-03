"""
core/material_allowables.py — Lamina Mukavemet ve Mühendislik Özellikleri (Faz 23 ENG-1)
==========================================================================================

Bu modül `material_database.MaterialSpec` üzerine kompozit mühendislik
katmanını ekler. Lamina (tek tabaka) düzeyinde elastik sabitler, mukavemet
limitleri, istatistiksel allowables (A/B-basis), Tsai-Wu etkileşim sabitleri
ve çevresel knockdown faktörlerini içerir.

Bu modül diğer ENG-* modüllerinin (netting, CLT, failure, burst,
safety factor, sizing) tek doğruluk kaynağıdır.

================================================================================
MATEMATİKSEL MODEL
================================================================================

1. Lamina elastik özellikleri (ortotropik, plane stress):
       E_1, E_2, G_12, ν_12
   Burada 1 = fiber yönü, 2 = enine yön, 12 = düzlem içi kayma.
   Karşılıklı Poisson oranı: ν_21 = ν_12 · E_2/E_1.

2. Lamina mukavemet limitleri (lokal koordinatlarda):
       X_t  = boyuna çekme dayanımı   (fiber yönü, çekme)
       X_c  = boyuna basma dayanımı   (fiber yönü, basma)
       Y_t  = enine çekme dayanımı    (matris hakim, çekme)
       Y_c  = enine basma dayanımı    (matris hakim, basma)
       S    = düzlem içi kayma dayanımı
   Tüm değerler MPa cinsinden.

3. Tsai-Wu etkileşim sabitleri:
       F_1   = 1/X_t − 1/X_c
       F_2   = 1/Y_t − 1/Y_c
       F_11  = 1/(X_t · X_c)
       F_22  = 1/(Y_t · Y_c)
       F_66  = 1/S²
       F_12  = f_norm · sqrt(F_11 · F_22),   f_norm ∈ [-1, 0]
   Standart varsayım: f_norm = -0.5 (Tsai & Hahn 1980, deneysel ortalama).

4. Statistical allowables (MIL-HDBK-17 / CMH-17 yaklaşımı):
       A-basis = μ · (1 − k_A · CV)
       B-basis = μ · (1 − k_B · CV)
   Normal dağılım için tipik değerler:
       k_A = 2.326  (99% güvenirlik, %95 güven)
       k_B = 1.282  (90% güvenirlik, %95 güven)
   CV = standart sapma / ortalama, kompozitler için tipik %5-10.

5. Çevresel knockdown faktörleri:
       σ_design = σ_allowable · K_hotwet · K_fatigue · K_aging · K_uv
   Tipik değerler (CMH-17 Vol 2, Chapter 2):
       K_hotwet  ≈ 0.80   (sıcak/ıslak, T < T_g − 50°C)
       K_fatigue ≈ 0.55   (10⁶ döngü)
       K_aging   ≈ 0.85   (20 yıl uzun süreli)
       K_uv      ≈ 0.90   (UV maruziyeti)
   Toplam: K_total = ∏ K_i

================================================================================
VARSAYIMLAR
================================================================================

VARSAYIM-1: Lamina düzlem-gerilme (plane stress) durumundadır — kalın laminat
            etkileri (out-of-plane shear, σ_3) ihmal edilir. Filament sarma
            duvar kalınlığı/çap < 0.05 için makul.

VARSAYIM-2: Tüm yönlerde lineer elastik davranış. Matris kırılması veya fiber
            kopması sonrası progresif hasar bu modülde modellenmemiştir
            (sadece İlk-Ply-Failure analizi için yeterli).

VARSAYIM-3: F_12 normalleştirilmiş etkileşim katsayısı f_norm = -0.5
            (Tsai & Hahn 1980 önerisi). Daha hassas analiz için biaxial
            test gereklidir.

VARSAYIM-4: Knockdown faktörleri çarpımsaldır ve birbirinden bağımsızdır.
            Gerçek uygulamada bazı etkileşimler mevcuttur (örn. nemli ortamda
            yorulma hızlanması) ancak konservatif tarafta kalmak için bağımsız
            varsayılır.

VARSAYIM-5: A/B-basis hesabı normal dağılım varsayar. Weibull veya log-normal
            dağılım için farklı çarpanlar gerekir; bu modül normal varsayımı
            kullanır (en yaygın havacılık pratiği).

================================================================================
SINIRLAMALAR
================================================================================

SINIR-1:  Sadece tek yönlü (UD) lamina özellikleri tanımlanmıştır. Dokuma
          (woven), örgü (braided) veya kısa fiber kompozitler kapsam dışıdır.

SINIR-2:  Sıcaklık bağımlılığı tek bir T_g değeri ile basitleştirilmiştir.
          Tam sıcaklık-bağımlı eğri için ayrı bir veri seti gerekir.

SINIR-3:  Higro-termal şişme (CTE_1, CTE_2, β_1, β_2) bu sürümde dahil
          edilmemiştir — Faz 23+ için ayrı modül planlanmıştır.

SINIR-4:  Çevre etki katsayıları konservatif "tipik" değerlerdir; üretici
          datasheet'i veya ASTM testleri ile değiştirilmelidir.

================================================================================
DOĞRULAMA STRATEJİSİ
================================================================================

DV-1: Yayınlanmış referans değerlere karşı tip kontrolü
        - T300/5208      → Tsai & Hahn 1980, Table 1.2
        - T700S/Epoxy    → CMH-17 Vol 2 Rev. G, Carbon fiber datasheet
        - IM7/8552       → Daniel & Ishai 2006, Table 2.2
        - E-Glass/Epoxy  → Daniel & Ishai 2006, Table 2.2
        - Kevlar-49/Ep.  → Daniel & Ishai 2006, Table 2.2

DV-2: Tsai-Wu etkileşim sabitleri elle hesaplanan değerlerle karşılaştırılır.
DV-3: A/B-basis hesabı CMH-17 örnek problem ile karşılaştırılır.
DV-4: ν_21 = ν_12·E_2/E_1 simetri ilişkisi otomatik kontrol edilir.
DV-5: Knockdown çarpımı bağımsız çarpan sırasına göre değişmez.

================================================================================
REFERANSLAR
================================================================================

[1] Daniel, I.M. & Ishai, O., "Engineering Mechanics of Composite Materials",
    2nd ed., Oxford University Press, 2006, Chapter 2.
[2] Tsai, S.W. & Hahn, H.T., "Introduction to Composite Materials",
    Technomic, 1980, Chapter 1 & 7.
[3] Jones, R.M., "Mechanics of Composite Materials", 2nd ed., Taylor &
    Francis, 1999, Chapter 2.
[4] CMH-17 (Composite Materials Handbook), Vol. 2 Rev. G, "Polymer Matrix
    Composites — Materials Properties", SAE International, 2012.
[5] MIL-HDBK-17, "Polymer Matrix Composites — Guidelines for Characterization
    of Structural Materials", DoD, 2002.
[6] Vasiliev, V.V., "Composite Pressure Vessels", Bull Ridge Publishing,
    2009, Chapter 2.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .material_database import MaterialSpec, get_material


# ── İstatistiksel allowables sabitleri ───────────────────────────────────────

K_A_BASIS_NORMAL: float = 2.326   # 99% güvenirlik, normal dağılım (CMH-17)
K_B_BASIS_NORMAL: float = 1.282   # 90% güvenirlik, normal dağılım (CMH-17)


# ── Lamina mekanik özellikleri ───────────────────────────────────────────────

@dataclass
class LaminaProperties:
    """
    Tek yönlü (UD) lamina için mühendislik sabitleri ve mukavemet limitleri.

    Tüm değerler MPa (mukavemet) veya GPa (modül) cinsindendir.
    Koordinat sistemi: 1 = fiber yönü, 2 = enine, 12 = düzlem içi kayma.
    """
    name: str

    # Elastik sabitler (plane stress, ortotropik)
    E_1_GPa: float           # fiber yönü modül
    E_2_GPa: float           # enine modül
    G_12_GPa: float          # düzlem içi kayma modülü
    nu_12: float             # büyük Poisson oranı (1→2 yönü)

    # Mukavemet limitleri (lamina, mean değerler, MPa)
    X_t_MPa: float           # boyuna çekme dayanımı
    X_c_MPa: float           # boyuna basma dayanımı (pozitif olarak gir)
    Y_t_MPa: float           # enine çekme dayanımı
    Y_c_MPa: float           # enine basma dayanımı (pozitif olarak gir)
    S_MPa: float             # düzlem içi kayma dayanımı

    # İstatistiksel parametre
    coefficient_of_variation: float = 0.07   # CV (tipik kompozitler %5-10)

    # Tsai-Wu etkileşim katsayısı (normalleştirilmiş F_12)
    f_12_normalized: float = -0.5            # Tsai & Hahn 1980 önerisi

    # Termal sınırlar
    glass_transition_T_C: float = 120.0      # T_g (°C)

    # Yoğunluk (g/cm³) — kütle hesabı için (referans)
    ply_density_g_cm3: float = 1.58

    # Plyye özgü kalınlık (mm) — bilgilendirici
    nominal_ply_thickness_mm: float = 0.125

    def __post_init__(self) -> None:
        # Pozitif giriş zorunlu (basma dayanımları pozitif girilir)
        positive_fields = {
            "E_1_GPa": self.E_1_GPa, "E_2_GPa": self.E_2_GPa,
            "G_12_GPa": self.G_12_GPa,
            "X_t_MPa": self.X_t_MPa, "X_c_MPa": self.X_c_MPa,
            "Y_t_MPa": self.Y_t_MPa, "Y_c_MPa": self.Y_c_MPa,
            "S_MPa": self.S_MPa,
        }
        for fname, val in positive_fields.items():
            if val <= 0:
                raise ValueError(f"{fname} pozitif olmalı: {val}")
        if not (-0.5 <= self.nu_12 <= 0.5):
            raise ValueError(f"nu_12 fiziksel aralık [-0.5, 0.5]: {self.nu_12}")
        if not (0.0 <= self.coefficient_of_variation <= 0.5):
            raise ValueError(f"CV ∈ [0, 0.5]: {self.coefficient_of_variation}")
        if not (-1.0 <= self.f_12_normalized <= 0.0):
            raise ValueError(
                f"f_12_normalized ∈ [-1, 0] (Tsai-Wu pozitif belirli koşul): "
                f"{self.f_12_normalized}"
            )

    # ── Temel türetilmiş özellikler ────────────────────────────────────────

    @property
    def nu_21(self) -> float:
        """Karşılıklı Poisson oranı: ν_21 = ν_12 · E_2/E_1 (simetri ilişkisi)."""
        return self.nu_12 * self.E_2_GPa / self.E_1_GPa

    # ── A/B-basis statistical allowables ───────────────────────────────────

    @staticmethod
    def _basis_value(mean: float, cv: float, k: float) -> float:
        """μ · (1 − k·CV), negatif olmayacak şekilde sınırla."""
        return max(0.0, mean * (1.0 - k * cv))

    def A_basis(self, mean_strength_MPa: float) -> float:
        """A-basis allowable (99/95 güvenirlik)."""
        return self._basis_value(mean_strength_MPa, self.coefficient_of_variation,
                                 K_A_BASIS_NORMAL)

    def B_basis(self, mean_strength_MPa: float) -> float:
        """B-basis allowable (90/95 güvenirlik)."""
        return self._basis_value(mean_strength_MPa, self.coefficient_of_variation,
                                 K_B_BASIS_NORMAL)

    def X_t_A_basis(self) -> float: return self.A_basis(self.X_t_MPa)
    def X_t_B_basis(self) -> float: return self.B_basis(self.X_t_MPa)
    def X_c_A_basis(self) -> float: return self.A_basis(self.X_c_MPa)
    def X_c_B_basis(self) -> float: return self.B_basis(self.X_c_MPa)
    def Y_t_A_basis(self) -> float: return self.A_basis(self.Y_t_MPa)
    def Y_t_B_basis(self) -> float: return self.B_basis(self.Y_t_MPa)
    def Y_c_A_basis(self) -> float: return self.A_basis(self.Y_c_MPa)
    def Y_c_B_basis(self) -> float: return self.B_basis(self.Y_c_MPa)
    def S_A_basis(self) -> float: return self.A_basis(self.S_MPa)
    def S_B_basis(self) -> float: return self.B_basis(self.S_MPa)

    # ── Tsai-Wu katsayıları ────────────────────────────────────────────────

    def F_1(self) -> float:
        """F_1 = 1/X_t − 1/X_c (1/MPa)."""
        return 1.0 / self.X_t_MPa - 1.0 / self.X_c_MPa

    def F_2(self) -> float:
        """F_2 = 1/Y_t − 1/Y_c (1/MPa)."""
        return 1.0 / self.Y_t_MPa - 1.0 / self.Y_c_MPa

    def F_11(self) -> float:
        """F_11 = 1/(X_t · X_c) (1/MPa²)."""
        return 1.0 / (self.X_t_MPa * self.X_c_MPa)

    def F_22(self) -> float:
        """F_22 = 1/(Y_t · Y_c) (1/MPa²)."""
        return 1.0 / (self.Y_t_MPa * self.Y_c_MPa)

    def F_66(self) -> float:
        """F_66 = 1/S² (1/MPa²)."""
        return 1.0 / (self.S_MPa * self.S_MPa)

    def F_12(self) -> float:
        """
        F_12 = f_norm · sqrt(F_11 · F_22)  (1/MPa²)

        f_norm ∈ [-1, 0]; -0.5 standart pratik.
        Pozitif-belirli koşul: F_11·F_22 − F_12² > 0 ⟹ |f_norm| < 1.
        """
        return self.f_12_normalized * math.sqrt(self.F_11() * self.F_22())

    def tsai_wu_coefficients(self) -> Dict[str, float]:
        """Tüm Tsai-Wu katsayılarını tek sözlükte döndür."""
        return {
            "F_1": self.F_1(), "F_2": self.F_2(),
            "F_11": self.F_11(), "F_22": self.F_22(),
            "F_66": self.F_66(), "F_12": self.F_12(),
        }

    # ── Bilgilendirici özet ────────────────────────────────────────────────

    def summary(self) -> str:
        return (
            f"Lamina '{self.name}': "
            f"E_1={self.E_1_GPa:.0f}GPa | E_2={self.E_2_GPa:.1f}GPa | "
            f"G_12={self.G_12_GPa:.1f}GPa | ν_12={self.nu_12:.2f} | "
            f"X_t={self.X_t_MPa:.0f}MPa | X_c={self.X_c_MPa:.0f}MPa | "
            f"Y_t={self.Y_t_MPa:.0f}MPa | Y_c={self.Y_c_MPa:.0f}MPa | "
            f"S={self.S_MPa:.0f}MPa | CV={self.coefficient_of_variation*100:.1f}%"
        )


# ── Çevresel knockdown faktörleri ────────────────────────────────────────────

@dataclass
class EnvironmentalKnockdown:
    """
    Çoklu knockdown faktörlerini tek bir konteynerde toplar.

    Tüm faktörler ∈ (0, 1]. K_total = ∏ K_i.

    Tipik değerler (CMH-17 Vol 2, Chapter 2):
        K_hotwet  = 0.80  (T < T_g − 50°C, doymuş nem)
        K_fatigue = 0.55  (R=0.1, 10⁶ döngü)
        K_aging   = 0.85  (20 yıl saklama)
        K_uv      = 0.90  (UV maruziyet)
        K_creep   = 0.85  (uzun süreli yükleme)
    """
    K_hotwet: float = 1.0
    K_fatigue: float = 1.0
    K_aging: float = 1.0
    K_uv: float = 1.0
    K_creep: float = 1.0
    K_impact: float = 1.0    # BVID sonrası dayanım kaybı
    notes: str = ""

    def __post_init__(self) -> None:
        for fname in ("K_hotwet", "K_fatigue", "K_aging", "K_uv",
                      "K_creep", "K_impact"):
            v = getattr(self, fname)
            if not (0.0 < v <= 1.0):
                raise ValueError(f"{fname} ∈ (0, 1]: {v}")

    @property
    def K_total(self) -> float:
        """Tüm knockdown faktörlerinin çarpımı (≤ 1)."""
        return (self.K_hotwet * self.K_fatigue * self.K_aging *
                self.K_uv * self.K_creep * self.K_impact)

    def apply(self, mean_strength_MPa: float) -> float:
        """Verilen mukavemete tüm knockdownları uygula."""
        return mean_strength_MPa * self.K_total

    @classmethod
    def benign_room_temp(cls) -> "EnvironmentalKnockdown":
        """Oda sıcaklığı, kısa süreli statik yükleme — tüm K=1."""
        return cls(notes="oda sıcaklığı, kısa süreli statik")

    @classmethod
    def standard_pressure_vessel(cls) -> "EnvironmentalKnockdown":
        """Standart yer üstü basınçlı kap — orta düzey çevresel düşürme."""
        return cls(
            K_hotwet=0.85, K_fatigue=0.70, K_aging=0.90, K_uv=0.95,
            K_creep=0.90, K_impact=1.0,
            notes="standart yer üstü PV (20 yıl, 10⁵ döngü)",
        )

    @classmethod
    def aerospace_long_term(cls) -> "EnvironmentalKnockdown":
        """Havacılık tipi uzun süre — agresif knockdown."""
        return cls(
            K_hotwet=0.80, K_fatigue=0.55, K_aging=0.85, K_uv=0.85,
            K_creep=0.85, K_impact=0.85,
            notes="havacılık uzun süreli (30 yıl, 10⁶ döngü, BVID)",
        )


# ── Birleşik mühendislik malzeme tanımı ──────────────────────────────────────

@dataclass
class EngineeringMaterial:
    """
    MaterialSpec (kütle/maliyet) + LaminaProperties (mekanik) birleşimi.

    Bu sınıf ENG-2..ENG-7 modüllerinin ortak girdi yapısıdır.
    Backward-compat: `base` üzerinden tüm eski MaterialSpec API'si erişilebilir.
    """
    name: str
    base: MaterialSpec            # mevcut kütle/maliyet katmanı
    lamina: LaminaProperties      # yeni mukavemet katmanı
    environment: EnvironmentalKnockdown = field(
        default_factory=EnvironmentalKnockdown.benign_room_temp
    )

    def design_X_t(self, basis: str = "B") -> float:
        """Tasarım boyuna çekme dayanımı (basis + knockdown uygulanmış)."""
        return self._design_strength(self.lamina.X_t_MPa, basis)

    def design_X_c(self, basis: str = "B") -> float:
        return self._design_strength(self.lamina.X_c_MPa, basis)

    def design_Y_t(self, basis: str = "B") -> float:
        return self._design_strength(self.lamina.Y_t_MPa, basis)

    def design_Y_c(self, basis: str = "B") -> float:
        return self._design_strength(self.lamina.Y_c_MPa, basis)

    def design_S(self, basis: str = "B") -> float:
        return self._design_strength(self.lamina.S_MPa, basis)

    def _design_strength(self, mean_MPa: float, basis: str) -> float:
        basis = basis.upper()
        if basis == "A":
            stat = self.lamina.A_basis(mean_MPa)
        elif basis == "B":
            stat = self.lamina.B_basis(mean_MPa)
        elif basis == "MEAN":
            stat = mean_MPa
        else:
            raise ValueError(f"basis ∈ {{'A','B','MEAN'}}: {basis!r}")
        return stat * self.environment.K_total

    def summary(self) -> str:
        return (
            f"Mühendislik Malzeme '{self.name}'\n"
            f"  Base: {self.base.summary()}\n"
            f"  Lamina: {self.lamina.summary()}\n"
            f"  Ortam: K_total={self.environment.K_total:.3f} "
            f"({self.environment.notes or 'oda sıcaklığı'})"
        )


# ── Literatür-tabanlı lamina kataloğu ────────────────────────────────────────
#
# Tüm değerler aşağıdaki kaynaklardan alınmıştır:
#   • T300/5208     → Tsai & Hahn 1980, Table 1.2 (klasik referans)
#   • T700S/Epoxy   → CMH-17 Vol 2 Rev. G, ortalama UD lamina
#   • IM7/8552      → Daniel & Ishai 2006, Table 2.2 (s.36)
#   • E-Glass/Epoxy → Daniel & Ishai 2006, Table 2.2 (s.36)
#   • Kevlar-49/Ep. → Daniel & Ishai 2006, Table 2.2 (s.36)

def _lam_t300_5208() -> LaminaProperties:
    """T300/5208 — Tsai & Hahn 1980, Table 1.2. Klasik akademik referans."""
    return LaminaProperties(
        name="T300/5208 (Tsai-Hahn 1980)",
        E_1_GPa=181.0, E_2_GPa=10.3, G_12_GPa=7.17, nu_12=0.28,
        X_t_MPa=1500.0, X_c_MPa=1500.0,
        Y_t_MPa=40.0, Y_c_MPa=246.0, S_MPa=68.0,
        coefficient_of_variation=0.07,
        glass_transition_T_C=180.0,
        ply_density_g_cm3=1.60,
        nominal_ply_thickness_mm=0.125,
    )


def _lam_t700s_epoxy() -> LaminaProperties:
    """T700S/Epoxy — CMH-17 Vol 2 Rev G, ortalama UD lamina (~Vf=0.60)."""
    return LaminaProperties(
        name="T700S/Epoxy (CMH-17)",
        E_1_GPa=135.0, E_2_GPa=8.5, G_12_GPa=4.5, nu_12=0.30,
        X_t_MPa=2550.0, X_c_MPa=1470.0,
        Y_t_MPa=51.0, Y_c_MPa=206.0, S_MPa=93.0,
        coefficient_of_variation=0.07,
        glass_transition_T_C=120.0,
        ply_density_g_cm3=1.58,
        nominal_ply_thickness_mm=0.150,
    )


def _lam_im7_8552() -> LaminaProperties:
    """IM7/8552 — Daniel & Ishai 2006, Table 2.2 (s.36)."""
    return LaminaProperties(
        name="IM7/8552 (Daniel-Ishai)",
        E_1_GPa=165.0, E_2_GPa=8.4, G_12_GPa=5.6, nu_12=0.34,
        X_t_MPa=2800.0, X_c_MPa=1700.0,
        Y_t_MPa=60.0, Y_c_MPa=240.0, S_MPa=90.0,
        coefficient_of_variation=0.06,
        glass_transition_T_C=190.0,
        ply_density_g_cm3=1.57,
        nominal_ply_thickness_mm=0.131,
    )


def _lam_eglass_epoxy() -> LaminaProperties:
    """E-Glass/Epoxy — Daniel & Ishai 2006, Table 2.2 (s.36)."""
    return LaminaProperties(
        name="E-Glass/Epoxy (Daniel-Ishai)",
        E_1_GPa=39.0, E_2_GPa=8.6, G_12_GPa=3.8, nu_12=0.28,
        X_t_MPa=1080.0, X_c_MPa=620.0,
        Y_t_MPa=39.0, Y_c_MPa=128.0, S_MPa=89.0,
        coefficient_of_variation=0.08,
        glass_transition_T_C=110.0,
        ply_density_g_cm3=1.97,
        nominal_ply_thickness_mm=0.200,
    )


def _lam_kevlar49_epoxy() -> LaminaProperties:
    """Kevlar-49/Epoxy — Daniel & Ishai 2006, Table 2.2 (s.36)."""
    return LaminaProperties(
        name="Kevlar-49/Epoxy (Daniel-Ishai)",
        E_1_GPa=76.0, E_2_GPa=5.5, G_12_GPa=2.3, nu_12=0.34,
        X_t_MPa=1400.0, X_c_MPa=235.0,
        Y_t_MPa=12.0, Y_c_MPa=53.0, S_MPa=34.0,
        coefficient_of_variation=0.08,
        glass_transition_T_C=150.0,
        ply_density_g_cm3=1.38,
        nominal_ply_thickness_mm=0.150,
    )


_LAMINA_CATALOG: Dict[str, callable] = {
    "t300_5208":      _lam_t300_5208,
    "t700s_epoxy":    _lam_t700s_epoxy,
    "im7_8552":       _lam_im7_8552,
    "eglass_epoxy":   _lam_eglass_epoxy,
    "kevlar49_epoxy": _lam_kevlar49_epoxy,
}


def available_laminas() -> List[str]:
    """Mevcut lamina özellik veri seti adlarını döndürür."""
    return list(_LAMINA_CATALOG.keys())


def get_lamina(name: str) -> LaminaProperties:
    """Ada göre literatür-tabanlı lamina özellikleri döndürür."""
    if name not in _LAMINA_CATALOG:
        raise KeyError(
            f"Bilinmeyen lamina: '{name}'. Mevcut: {list(_LAMINA_CATALOG.keys())}"
        )
    return _LAMINA_CATALOG[name]()


# ── Hazır mühendislik malzeme profilleri ─────────────────────────────────────

def carbon_t700_epoxy_pv() -> EngineeringMaterial:
    """T700S/Epoxy mühendislik profili — standart basınçlı kap."""
    return EngineeringMaterial(
        name="T700S/Epoxy-PV",
        base=get_material("carbon_t700_standard_epoxy"),
        lamina=_lam_t700s_epoxy(),
        environment=EnvironmentalKnockdown.standard_pressure_vessel(),
    )


def carbon_im7_aerospace() -> EngineeringMaterial:
    """IM7/8552 mühendislik profili — havacılık uzun süreli."""
    return EngineeringMaterial(
        name="IM7/8552-Aero",
        base=get_material("carbon_im7_epoxy"),
        lamina=_lam_im7_8552(),
        environment=EnvironmentalKnockdown.aerospace_long_term(),
    )


def eglass_epoxy_pv() -> EngineeringMaterial:
    """E-Glass/Epoxy mühendislik profili — ekonomik PV."""
    return EngineeringMaterial(
        name="E-Glass/Epoxy-PV",
        base=get_material("eglass_epoxy"),
        lamina=_lam_eglass_epoxy(),
        environment=EnvironmentalKnockdown.standard_pressure_vessel(),
    )


def t300_5208_reference() -> EngineeringMaterial:
    """T300/5208 — Tsai-Hahn klasik akademik referans (doğrulama amaçlı)."""
    return EngineeringMaterial(
        name="T300/5208-Ref",
        base=get_material("carbon_t700_standard_epoxy"),  # kütle referansı
        lamina=_lam_t300_5208(),
        environment=EnvironmentalKnockdown.benign_room_temp(),
    )


_ENG_MATERIAL_CATALOG: Dict[str, callable] = {
    "carbon_t700_epoxy_pv":  carbon_t700_epoxy_pv,
    "carbon_im7_aerospace":  carbon_im7_aerospace,
    "eglass_epoxy_pv":       eglass_epoxy_pv,
    "t300_5208_reference":   t300_5208_reference,
}


def available_engineering_materials() -> List[str]:
    return list(_ENG_MATERIAL_CATALOG.keys())


def get_engineering_material(name: str) -> EngineeringMaterial:
    if name not in _ENG_MATERIAL_CATALOG:
        raise KeyError(
            f"Bilinmeyen mühendislik malzeme: '{name}'. "
            f"Mevcut: {list(_ENG_MATERIAL_CATALOG.keys())}"
        )
    return _ENG_MATERIAL_CATALOG[name]()


# ── Yardımcı doğrulama fonksiyonları ─────────────────────────────────────────

def tsai_wu_positive_definite(lam: LaminaProperties) -> bool:
    """
    Tsai-Wu kuadratik formun pozitif-belirli olduğunu doğrular.

    Koşul: F_11·F_22 − F_12² > 0
    (Aksi halde dayanım yüzeyi açık-uçlu olur, fiziksel değildir.)
    """
    return (lam.F_11() * lam.F_22() - lam.F_12() ** 2) > 0.0


def check_reciprocal_poisson(lam: LaminaProperties,
                              tol: float = 1e-9) -> bool:
    """
    ν_21 = ν_12 · E_2/E_1 simetri ilişkisinin sağlandığını kontrol et.

    Bu otomatik bir tutarlılık testidir; her zaman True dönmelidir.
    """
    expected = lam.nu_12 * lam.E_2_GPa / lam.E_1_GPa
    return abs(lam.nu_21 - expected) < tol
