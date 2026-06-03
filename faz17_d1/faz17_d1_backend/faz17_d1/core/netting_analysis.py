"""
core/netting_analysis.py — Netting (Fiber-Sadece) Analizi (Faz 23 ENG-2)
==========================================================================

Netting analizi: basınçlı kabın duvar gerilmesinin tamamının fiber tarafından
taşındığı kabul edilir — matris katkısı sıfır sayılır. Bu konservatif ön
boyutlandırma yöntemi yaygın olarak filament sarma basınçlı kap tasarımında
ilk laminat şemasını üretmek için kullanılır.

================================================================================
MATEMATİKSEL MODEL
================================================================================

Silindirik kabuk, iç basınç P, çap D, sarma açısı α (eksene göre):

1. Membran gerilmeleri (ince cidar):
       σ_axial     = PD / (4t)          (eksenel)
       σ_hoop      = PD / (2t)          (çevre/teğet)

2. Fiber yönü gerilmesi (α-açılı helisel katmanda):
       σ_fiber = σ_axial · cos²α + σ_hoop · sin²α
   (gerilim dönüşümü, lokal fiber yönü)

3. Tek-açılı helisel katman dengesi (sadece helisel, hoop yok):
   Eksenel denge:  σ_fiber · cos²α · t_α = (PD / 4)
   ⟹ t_α = PD / (4 · σ_fiber_allow · cos²α)
   Çevre denge:    σ_fiber · sin²α · t_α = (PD / 2)
   ⟹ aynı zamanda gerekli   PD/2 = (PD/4) · 2 sin²α / cos²α = (PD/2) tan²α
   ⟹ tan²α = 2 → α = 54.7356°  (MAGIC ANGLE)

4. Kombine hoop + helisel katmanlar (yaygın PV laminatı):
       t_α (helisel)  = PD / (4 · σ_f · cos²α)
       t_h (hoop)     = PD / (2 · σ_f) − t_α · sin²α
   (Vasiliev 2009, Eq. 5.45; Peters 2011 Ch. 7)
   Bu açıklama: helisel katmanın hoop yönündeki katkısı düşülür.

5. Küresel kap (sphere) — netting analizi:
       σ_meridian = σ_circumferential = PD/(4t)
       t_isotropic_required = PD / (4 · σ_f_allow)
   (eşit yönde — her açıda netting çözümü mümkün, geodezik 0°-90° döngü)

6. Dome (kubbe) — kutup açıklığı r_0 ile:
   Geodezik koşul: r(z)·sin(α(z)) = r_eq·sin(α_eq) = r_0
   Sarma açısı kubbede genişler; eşdeğer kalınlık:
       t_dome(z) = t_eq · (r_eq / r(z)) · (cos α_eq / cos α(z))
   (Vasiliev 2009, Eq. 5.78)

================================================================================
VARSAYIMLAR
================================================================================

VARSAYIM-1: İnce cidar (D/t > 20). Membran teori geçerli, bending ihmal.
VARSAYIM-2: Fiber lineer elastik, sünme yok, sadece dayanım sınırı.
VARSAYIM-3: Matris hiçbir yük taşımaz (yalnız fiber sünmemiş halde tutuyor).
VARSAYIM-4: Mükemmel dolaşım — her katman tam α açısıyla deposit edilmiştir.
VARSAYIM-5: Simetrik balanslı laminat ([+α/-α]_s). Tek tarafın torsiyonel
            zorlanması ihmal.
VARSAYIM-6: Tek katman kalınlığı pratiği — gerçek üretimde diskrete sayıda
            ply gerekir; bu modül analitik (sürekli) kalınlık verir.

================================================================================
SINIRLAMALAR
================================================================================

SINIR-1: Sadece iç basınç. Eksenel kuvvet, moment, dış yük dahil değil
         (gelecek genişleme: süperpozisyon).
SINIR-2: Tsai-Wu veya laminat analizi yapılmaz — sadece netting
         (matris katkısız fiber denge).
SINIR-3: Stres konsantrasyonları (boss, geçiş bölgeleri) ihmal.
SINIR-4: Magic angle çözümü sadece tek-açılı helisel için kesin; karışık
         laminatlar için optimizasyon Mathematica/numerical gerektirir.
SINIR-5: Termal/higrotermal etkiler dahil değil.

================================================================================
DOĞRULAMA STRATEJİSİ
================================================================================

DV-1: Magic angle α = arctan(√2) ≈ 54.7356° (Vasiliev Eq. 5.39).
DV-2: Vasiliev 2009, Worked Example 5.1 sayısal eşleşmesi:
        P=20 MPa, D=200 mm, σ_f=2000 MPa, α=54.74°
        t_α = 20·200/(4·2000·cos²(54.74°)) = 1000/(8000·0.333) = 0.375 mm
DV-3: Hoop-only sınır (α=90°): t_h = PD/(2σ_f), t_α=0.
DV-4: Magic angle koşulu: tan²(α)=2 ⟹ t_h = 0 (sadece helisel yeterli).
DV-5: Mass conservation: hoop+helical fiber length = laminat mass / ρ.

================================================================================
REFERANSLAR
================================================================================

[1] Vasiliev, V.V., "Composite Pressure Vessels: Analysis, Design and
    Manufacturing", Bull Ridge Publishing, 2009, Chapter 5 (s.180-220).
[2] Peters, S.T. (ed.), "Composite Filament Windings", ASM International,
    2011, Chapter 7 (s.155-180).
[3] Jones, R.M., "Mechanics of Composite Materials", 1999, Chapter 7.6.
[4] AIAA S-080-1998, "Space Systems — Metallic Pressure Vessels, Pressurized
    Structures, and Pressure Components".
[5] Roark's Formulas for Stress and Strain, 8th ed., 2012, Chapter 13.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .material_allowables import EngineeringMaterial, LaminaProperties


# ── Sabitler ─────────────────────────────────────────────────────────────────

#: Geodezik magic angle (rad): tan²α = 2 → α = arctan(√2) ≈ 0.9553 rad
MAGIC_ANGLE_RAD: float = math.atan(math.sqrt(2.0))
#: Magic angle in degrees ≈ 54.7356°
MAGIC_ANGLE_DEG: float = math.degrees(MAGIC_ANGLE_RAD)


# ── Sonuç veri yapıları ──────────────────────────────────────────────────────

@dataclass
class NettingResult:
    """Tek netting analizi çıktısı (silindirik kabuk)."""
    t_helical_mm: float        # Helisel katman toplam kalınlığı (±α)
    t_hoop_mm: float           # Hoop katmanı toplam kalınlığı (90°)
    t_total_mm: float          # Toplam laminat kalınlığı
    alpha_deg: float           # Helisel sarma açısı
    pressure_MPa: float        # Tasarım basıncı
    diameter_mm: float         # Çap
    sigma_fiber_allow_MPa: float  # Kullanılan fiber dayanımı
    is_magic_angle: bool       # |α - 54.74°| < 0.01°
    hoop_layer_required: bool  # t_hoop > 0 ⟹ hoop katman gerekli
    notes: str = ""

    def summary(self) -> str:
        return (
            f"Netting: P={self.pressure_MPa:.2f}MPa D={self.diameter_mm:.1f}mm "
            f"α={self.alpha_deg:.2f}° | t_α={self.t_helical_mm:.3f}mm "
            f"t_h={self.t_hoop_mm:.3f}mm t_total={self.t_total_mm:.3f}mm"
        )


@dataclass
class DomeNettingResult:
    """Kubbe boyunca eşdeğer netting kalınlık profili."""
    z_mm: List[float]
    r_mm: List[float]
    alpha_deg: List[float]
    t_dome_mm: List[float]
    polar_radius_mm: float
    equator_radius_mm: float
    equator_alpha_deg: float
    t_equator_mm: float


# ── Çekirdek netting çözücüleri ──────────────────────────────────────────────

def helical_only_thickness(pressure_MPa: float, diameter_mm: float,
                            sigma_fiber_MPa: float,
                            alpha_deg: float) -> float:
    """
    Sadece helisel katmanın eksenel denge kalınlığı (hoop yok):
        t_α = PD / (4 · σ_f · cos²α)

    Eğer α = 54.7356° (magic), bu çözüm hem eksenel hem hoop dengeyi sağlar.
    Aksi halde hoop dengesi sağlanmaz ⟹ ek hoop gerekli.
    """
    if pressure_MPa <= 0 or diameter_mm <= 0 or sigma_fiber_MPa <= 0:
        raise ValueError("P, D, σ_f > 0 olmalı")
    if not (0.0 < alpha_deg < 90.0):
        raise ValueError(f"alpha_deg ∈ (0, 90): {alpha_deg}")
    alpha_rad = math.radians(alpha_deg)
    cos2 = math.cos(alpha_rad) ** 2
    if cos2 < 1e-12:
        raise ValueError("cos²α ≈ 0 (α≈90°) — sadece helisel ile çözüm yok")
    return pressure_MPa * diameter_mm / (4.0 * sigma_fiber_MPa * cos2)


def hoop_only_thickness(pressure_MPa: float, diameter_mm: float,
                         sigma_fiber_MPa: float) -> float:
    """
    Sadece çevre (90°) sarımın hoop denge kalınlığı:
        t_h = PD / (2 · σ_f)

    Eksenel yönde sıfır taşıma → eksenel kuvvet ek katmanlar gerektirir.
    """
    if pressure_MPa <= 0 or diameter_mm <= 0 or sigma_fiber_MPa <= 0:
        raise ValueError("P, D, σ_f > 0 olmalı")
    return pressure_MPa * diameter_mm / (2.0 * sigma_fiber_MPa)


def magic_angle_thickness(pressure_MPa: float, diameter_mm: float,
                           sigma_fiber_MPa: float) -> float:
    """
    Magic angle (54.7356°) tek-açılı netting kalınlığı.

    Magic angle hem eksenel hem hoop dengesini sağlar:
        cos²α = 1/3 ⟹ t_α = PD/(4σ_f · 1/3) = 3PD/(4σ_f)
    Eşdeğer hoop denklemi: sin²α = 2/3, t_α·sin²α = 2/3·3PD/(4σ_f) = PD/(2σ_f).
    """
    return helical_only_thickness(pressure_MPa, diameter_mm, sigma_fiber_MPa,
                                   MAGIC_ANGLE_DEG)


def combined_hoop_helical(pressure_MPa: float, diameter_mm: float,
                           sigma_fiber_MPa: float,
                           alpha_deg: float) -> NettingResult:
    """
    Kombine hoop + helisel katmanlı silindir için netting analizi.

    Yöntem (Vasiliev 2009, Eq. 5.45):
        Helisel eksenel taşır:
            t_α = PD / (4·σ_f·cos²α)
        Hoop, helisel açıkta kalan çevre yükünü kapatır:
            t_h = PD/(2σ_f) − t_α · sin²α
        ⟹ Magic angle'da t_h = 0
        ⟹ α < 54.74° → t_h > 0 (helisel çevre yetersiz)
        ⟹ α > 54.74° → t_h < 0 (helisel fazla çevre, hoop gerekmez,
                                  ancak eksenel yetersiz olabilir)

    Eğer t_h < 0 dönerse, helisel açı yüksektir; eksenel/teğet birlikte
    karşılanamaz — yalnız helisel kullanmak yerine α'yı düşürün veya
    salt-helisel modu kullanın.
    """
    if not (0.0 < alpha_deg < 90.0):
        raise ValueError(f"alpha_deg ∈ (0, 90): {alpha_deg}")

    alpha_rad = math.radians(alpha_deg)
    sin2 = math.sin(alpha_rad) ** 2
    cos2 = math.cos(alpha_rad) ** 2

    t_alpha = pressure_MPa * diameter_mm / (4.0 * sigma_fiber_MPa * cos2)
    t_hoop = pressure_MPa * diameter_mm / (2.0 * sigma_fiber_MPa) - t_alpha * sin2

    is_magic = abs(alpha_deg - MAGIC_ANGLE_DEG) < 0.01
    hoop_required = t_hoop > 1e-9

    # Negatif hoop fiziksel anlamlı değildir → 0'a sınırla, not bırak
    notes = ""
    if t_hoop < 0:
        notes = (f"UYARI: α={alpha_deg:.2f}° magic angle üzerinde — "
                 f"helisel hoop'u aşıyor; t_h sıfıra sınırlandı. "
                 f"Eksenel-dominant yük için α düşürün.")
        t_hoop = 0.0

    t_total = t_alpha + t_hoop

    return NettingResult(
        t_helical_mm=t_alpha,
        t_hoop_mm=t_hoop,
        t_total_mm=t_total,
        alpha_deg=alpha_deg,
        pressure_MPa=pressure_MPa,
        diameter_mm=diameter_mm,
        sigma_fiber_allow_MPa=sigma_fiber_MPa,
        is_magic_angle=is_magic,
        hoop_layer_required=hoop_required,
        notes=notes,
    )


# ── Mühendislik malzemeyle birleşik çözücü ──────────────────────────────────

def netting_from_material(pressure_MPa: float, diameter_mm: float,
                           material: EngineeringMaterial,
                           alpha_deg: Optional[float] = None,
                           basis: str = "B") -> NettingResult:
    """
    EngineeringMaterial üzerinden netting analizi.

    Fiber dayanımı için lamina X_t (boyuna çekme) kullanılır — design basis
    + çevresel knockdown uygulanmış değer.

    alpha_deg = None ⟹ magic angle (54.74°) varsayılır.
    """
    if alpha_deg is None:
        alpha_deg = MAGIC_ANGLE_DEG
    sigma_f = material.design_X_t(basis=basis)
    return combined_hoop_helical(pressure_MPa, diameter_mm, sigma_f, alpha_deg)


# ── Küresel kap (sphere) netting ─────────────────────────────────────────────

def sphere_netting_thickness(pressure_MPa: float, diameter_mm: float,
                              sigma_fiber_MPa: float) -> float:
    """
    Küresel basınçlı kap netting kalınlığı.

        σ_meridian = σ_hoop = PD/(4t)
        ⟹ t = PD/(4·σ_f)

    Bu izotropik gerilme durumudur; herhangi bir geodezik şema (örn.
    0°→90° döngüsel sarım) bu kalınlığı sağlar.
    """
    if pressure_MPa <= 0 or diameter_mm <= 0 or sigma_fiber_MPa <= 0:
        raise ValueError("P, D, σ_f > 0 olmalı")
    return pressure_MPa * diameter_mm / (4.0 * sigma_fiber_MPa)


# ── Kubbe (dome) netting ─────────────────────────────────────────────────────

def dome_thickness_profile(equator_radius_mm: float, polar_radius_mm: float,
                            equator_alpha_deg: float,
                            t_equator_mm: float,
                            z_mm: List[float],
                            r_mm: List[float]) -> DomeNettingResult:
    """
    Kubbe boyunca geodezik kalınlık profili.

    Geodezik sarım (Clairaut):
        r(z) · sin(α(z)) = r_eq · sin(α_eq) = r_0  (polar radius)

    Eşdeğer fiber genişliği koruması:
        t(z) = t_eq · (r_eq/r(z)) · (cos α_eq / cos α(z))
    (Vasiliev 2009, Eq. 5.78 — fiber yığılması nedeniyle polar bölgede
    kalınlık artar.)

    Girdi:
        equator_radius_mm  : r_eq, silindirik ekvatorda yarıçap
        polar_radius_mm    : r_0, kutup açıklığı yarıçapı (boss)
        equator_alpha_deg  : α_eq, ekvatordaki sarma açısı
        t_equator_mm       : t_eq, ekvatordaki helisel kalınlık (netting'ten)
        z_mm, r_mm         : kubbe profili (eksenel + yarıçap dizileri)
    """
    if equator_radius_mm <= 0 or polar_radius_mm <= 0:
        raise ValueError("r_eq, r_0 > 0")
    if polar_radius_mm >= equator_radius_mm:
        raise ValueError("polar < equator olmalı")
    if not (0.0 < equator_alpha_deg < 90.0):
        raise ValueError("equator_alpha_deg ∈ (0,90)")
    if len(z_mm) != len(r_mm) or len(z_mm) < 2:
        raise ValueError("z_mm/r_mm aynı uzunluk ve ≥2 nokta olmalı")

    sin_alpha_eq = math.sin(math.radians(equator_alpha_deg))
    cos_alpha_eq = math.cos(math.radians(equator_alpha_deg))
    c = equator_radius_mm * sin_alpha_eq  # Clairaut sabiti

    alpha_deg_list: List[float] = []
    t_dome_list: List[float] = []
    for r in r_mm:
        if r <= 0:
            raise ValueError(f"r > 0 olmalı, alındı: {r}")
        ratio = c / r
        ratio = min(1.0, max(-1.0, ratio))  # numeric guard
        alpha = math.asin(ratio)
        alpha_deg_list.append(math.degrees(alpha))
        cos_alpha = math.cos(alpha)
        if cos_alpha < 1e-9:
            # Lokal α≈90°: kalınlık fiziksel olarak sonsuza gider → sınırla
            t_dome_list.append(t_equator_mm * 100.0)  # büyük sembolik
        else:
            t_dome_list.append(
                t_equator_mm * (equator_radius_mm / r) *
                (cos_alpha_eq / cos_alpha)
            )

    return DomeNettingResult(
        z_mm=list(z_mm), r_mm=list(r_mm),
        alpha_deg=alpha_deg_list, t_dome_mm=t_dome_list,
        polar_radius_mm=polar_radius_mm,
        equator_radius_mm=equator_radius_mm,
        equator_alpha_deg=equator_alpha_deg,
        t_equator_mm=t_equator_mm,
    )


# ── Yardımcı: önerilen sarma açısı ───────────────────────────────────────────

def recommend_winding_angle(diameter_mm: float, length_mm: float,
                             pressure_MPa: float,
                             material: EngineeringMaterial,
                             allow_hoop_layer: bool = True) -> Tuple[float, str]:
    """
    Belirli bir basınçlı kap geometrisi için önerilen helisel sarma açısı.

    Strateji:
      • L/D > 5 (uzun kap): magic angle (54.74°) — minimum kalınlık
      • L/D < 1 (kısa/küresel): magic angle yine geçerli
      • allow_hoop_layer = False ⟹ saf magic angle
      • allow_hoop_layer = True ⟹ üretilebilirlik için 20°-25° helisel +
        ayrı hoop katmanı (boss açıklığı için düşük α tercih edilir)

    Bu sezgisel öneri — kesin değer optimize_laminate (Faz 23 ENG-7) ile
    bulunur.
    """
    if diameter_mm <= 0:
        raise ValueError("diameter_mm > 0 olmalı")
    LD = length_mm / diameter_mm if length_mm > 0 else 0.0

    if not allow_hoop_layer:
        return MAGIC_ANGLE_DEG, "Saf netting: magic angle önerildi"

    # Yardımcı modlu — pratik üretim
    if LD < 1.5:
        return MAGIC_ANGLE_DEG, f"Kısa kap L/D={LD:.2f}: magic angle"
    elif LD < 5.0:
        return 30.0, f"Orta kap L/D={LD:.2f}: 30° helisel + hoop katmanları"
    else:
        return 20.0, f"Uzun kap L/D={LD:.2f}: 20° helisel + hoop katmanları"
