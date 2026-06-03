"""
tests/test_engineering_layer.py — Faz 23 ENG-* Mühendislik Katmanı Birim Testleri
==================================================================================

Bu test paketi Faz 23 mühendislik katmanının (ENG-1..ENG-7) tüm modüllerini
doğrular. Her test grubu:
  • Literatür-tabanlı referans değerlere karşı sayısal kontrol
  • Matematik denkliklerinin (Tsai-Wu, CLT, vb.) tutarlılığı
  • Sınır durumları ve hata yolları
  • Backward-compat (mevcut CAM modüllerinin kırılmaması)
"""
from __future__ import annotations

import math
import sys
import os
import traceback
from typing import Callable, List, Tuple

# Yol ayarı (backend modüllerine erişim)
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.join(_HERE, "..", "faz17_d1_backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


_PASS = 0
_FAIL = 0
_FAILURES: List[str] = []


def _assert(condition: bool, msg: str) -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
    else:
        _FAIL += 1
        _FAILURES.append(msg)
        print(f"  ✗ FAIL: {msg}")


def _assert_close(actual: float, expected: float, rel_tol: float, abs_tol: float,
                  msg: str) -> None:
    err = abs(actual - expected)
    ok = (err <= abs_tol) or (expected != 0 and err / abs(expected) <= rel_tol)
    if not ok:
        msg = f"{msg} | actual={actual:.6g}, expected={expected:.6g}, err={err:.6g}"
    _assert(ok, msg)


# ════════════════════════════════════════════════════════════════════════════
# GROUP 1 — LaminaProperties: dataclass + validation
# ════════════════════════════════════════════════════════════════════════════

def test_group_1_lamina_basic() -> None:
    print("\n[GROUP 1] LaminaProperties — temel doğrulama")
    from faz17_d1.core.material_allowables import LaminaProperties

    # 1.1: Geçerli lamina oluşturma
    lam = LaminaProperties(
        name="test", E_1_GPa=180.0, E_2_GPa=10.0, G_12_GPa=7.0, nu_12=0.28,
        X_t_MPa=1500, X_c_MPa=1500, Y_t_MPa=40, Y_c_MPa=246, S_MPa=68,
    )
    _assert(lam.name == "test", "1.1: ad atanmalı")

    # 1.2-1.6: Pozitif olmayan alanlar reddedilmeli
    for field_name, kwargs in [
        ("E_1_GPa", dict(E_1_GPa=0)),
        ("E_2_GPa", dict(E_2_GPa=-1)),
        ("G_12_GPa", dict(G_12_GPa=0)),
        ("X_t_MPa", dict(X_t_MPa=-100)),
        ("Y_c_MPa", dict(Y_c_MPa=0)),
    ]:
        try:
            base = dict(name="t", E_1_GPa=180, E_2_GPa=10, G_12_GPa=7, nu_12=0.28,
                        X_t_MPa=1500, X_c_MPa=1500, Y_t_MPa=40, Y_c_MPa=246, S_MPa=68)
            base.update(kwargs)
            LaminaProperties(**base)
            _assert(False, f"1.x: {field_name}<=0 reddedilmeli")
        except ValueError:
            _assert(True, f"1.x: {field_name}<=0 reddedildi")

    # 1.7: Aşırı Poisson reddedilmeli
    try:
        LaminaProperties(
            name="t", E_1_GPa=180, E_2_GPa=10, G_12_GPa=7, nu_12=0.9,
            X_t_MPa=1500, X_c_MPa=1500, Y_t_MPa=40, Y_c_MPa=246, S_MPa=68,
        )
        _assert(False, "1.7: nu_12=0.9 reddedilmeli")
    except ValueError:
        _assert(True, "1.7: aşırı Poisson reddedildi")

    # 1.8: CV >0.5 reddedilmeli
    try:
        LaminaProperties(
            name="t", E_1_GPa=180, E_2_GPa=10, G_12_GPa=7, nu_12=0.28,
            X_t_MPa=1500, X_c_MPa=1500, Y_t_MPa=40, Y_c_MPa=246, S_MPa=68,
            coefficient_of_variation=0.6,
        )
        _assert(False, "1.8: CV=0.6 reddedilmeli")
    except ValueError:
        _assert(True, "1.8: aşırı CV reddedildi")

    # 1.9: f_12_normalized pozitif reddedilmeli
    try:
        LaminaProperties(
            name="t", E_1_GPa=180, E_2_GPa=10, G_12_GPa=7, nu_12=0.28,
            X_t_MPa=1500, X_c_MPa=1500, Y_t_MPa=40, Y_c_MPa=246, S_MPa=68,
            f_12_normalized=0.5,
        )
        _assert(False, "1.9: f_norm=+0.5 reddedilmeli")
    except ValueError:
        _assert(True, "1.9: pozitif f_norm reddedildi")


# ════════════════════════════════════════════════════════════════════════════
# GROUP 2 — Lamina katalog: literatür değerleri
# ════════════════════════════════════════════════════════════════════════════

def test_group_2_lamina_catalog() -> None:
    print("\n[GROUP 2] Lamina kataloğu — literatür referansları")
    from faz17_d1.core.material_allowables import (
        get_lamina, available_laminas,
    )

    # 2.1: 5 lamina mevcut olmalı
    names = available_laminas()
    _assert(len(names) >= 5, f"2.1: ≥5 lamina kataloğunda var (got {len(names)})")

    # 2.2: T300/5208 — Tsai & Hahn 1980, Table 1.2
    lam = get_lamina("t300_5208")
    _assert_close(lam.E_1_GPa, 181.0, 1e-3, 0.5, "2.2a: T300 E_1=181 GPa")
    _assert_close(lam.E_2_GPa, 10.3, 1e-3, 0.1, "2.2b: T300 E_2=10.3 GPa")
    _assert_close(lam.G_12_GPa, 7.17, 1e-3, 0.05, "2.2c: T300 G_12=7.17 GPa")
    _assert_close(lam.nu_12, 0.28, 1e-3, 0.005, "2.2d: T300 ν_12=0.28")
    _assert_close(lam.X_t_MPa, 1500.0, 1e-3, 5.0, "2.2e: T300 X_t=1500 MPa")
    _assert_close(lam.Y_c_MPa, 246.0, 1e-3, 5.0, "2.2f: T300 Y_c=246 MPa")

    # 2.3: T700S/Epoxy — CMH-17 nominal
    lam = get_lamina("t700s_epoxy")
    _assert(120.0 <= lam.E_1_GPa <= 160.0, "2.3a: T700S E_1 ~140 GPa aralığında")
    _assert(2000.0 <= lam.X_t_MPa <= 3000.0, "2.3b: T700S X_t ~2500 MPa aralığında")
    _assert_close(lam.nu_12, 0.30, 1e-3, 0.05, "2.3c: T700S ν_12~0.30")

    # 2.4: IM7/8552 — Daniel & Ishai Table 2.2
    lam = get_lamina("im7_8552")
    _assert_close(lam.E_1_GPa, 165.0, 1e-3, 1.0, "2.4a: IM7/8552 E_1=165 GPa")
    _assert_close(lam.X_t_MPa, 2800.0, 1e-3, 10.0, "2.4b: IM7/8552 X_t=2800 MPa")
    _assert_close(lam.nu_12, 0.34, 1e-3, 0.01, "2.4c: IM7/8552 ν_12=0.34")

    # 2.5: E-Glass/Epoxy — Daniel & Ishai
    lam = get_lamina("eglass_epoxy")
    _assert_close(lam.E_1_GPa, 39.0, 1e-3, 0.5, "2.5a: E-Glass E_1=39 GPa")
    _assert_close(lam.X_t_MPa, 1080.0, 1e-3, 5.0, "2.5b: E-Glass X_t=1080 MPa")

    # 2.6: Kevlar — Daniel & Ishai
    lam = get_lamina("kevlar49_epoxy")
    _assert_close(lam.E_1_GPa, 76.0, 1e-3, 0.5, "2.6a: Kevlar E_1=76 GPa")
    _assert_close(lam.X_c_MPa, 235.0, 1e-3, 5.0, "2.6b: Kevlar X_c=235 MPa (düşük)")
    _assert(lam.X_c_MPa < lam.X_t_MPa,
            "2.6c: Kevlar fiziksel doğru — basma << çekme")

    # 2.7: Bilinmeyen lamina KeyError
    try:
        get_lamina("uydurma_lamina")
        _assert(False, "2.7: bilinmeyen lamina KeyError")
    except KeyError:
        _assert(True, "2.7: KeyError dönüyor")


# ════════════════════════════════════════════════════════════════════════════
# GROUP 3 — Reciprocal Poisson simetri ilişkisi
# ════════════════════════════════════════════════════════════════════════════

def test_group_3_reciprocal_poisson() -> None:
    print("\n[GROUP 3] Karşılıklı Poisson oranı simetri ilişkisi")
    from faz17_d1.core.material_allowables import (
        get_lamina, available_laminas, check_reciprocal_poisson,
    )

    # 3.1-3.5: Tüm kataloğ laminaları simetri sağlamalı
    for i, name in enumerate(available_laminas()):
        lam = get_lamina(name)
        _assert(check_reciprocal_poisson(lam),
                f"3.{i+1}: '{name}' — ν_21 = ν_12·E_2/E_1 simetrisi")

    # 3.6: Manuel hesaplama doğrulama
    lam = get_lamina("t300_5208")
    expected_nu21 = 0.28 * 10.3 / 181.0
    _assert_close(lam.nu_21, expected_nu21, 1e-6, 1e-6,
                  "3.6: T300 ν_21 manuel hesap")


# ════════════════════════════════════════════════════════════════════════════
# GROUP 4 — Tsai-Wu katsayıları
# ════════════════════════════════════════════════════════════════════════════

def test_group_4_tsai_wu() -> None:
    print("\n[GROUP 4] Tsai-Wu katsayıları")
    from faz17_d1.core.material_allowables import (
        get_lamina, tsai_wu_positive_definite,
    )

    # 4.1-4.6: T300/5208 katsayıları elle hesaplanmış değerlerle uyuşmalı
    # (Tsai & Hahn 1980, Table 7.1 hesaplama örneği)
    lam = get_lamina("t300_5208")
    F1 = 1.0 / 1500 - 1.0 / 1500
    F2 = 1.0 / 40 - 1.0 / 246
    F11 = 1.0 / (1500 * 1500)
    F22 = 1.0 / (40 * 246)
    F66 = 1.0 / (68 ** 2)
    F12 = -0.5 * math.sqrt(F11 * F22)

    _assert_close(lam.F_1(), F1, 1e-6, 1e-12, "4.1: F_1 = 1/X_t - 1/X_c")
    _assert_close(lam.F_2(), F2, 1e-6, 1e-12, "4.2: F_2 = 1/Y_t - 1/Y_c")
    _assert_close(lam.F_11(), F11, 1e-6, 1e-15, "4.3: F_11 = 1/(X_t·X_c)")
    _assert_close(lam.F_22(), F22, 1e-6, 1e-12, "4.4: F_22 = 1/(Y_t·Y_c)")
    _assert_close(lam.F_66(), F66, 1e-6, 1e-12, "4.5: F_66 = 1/S²")
    _assert_close(lam.F_12(), F12, 1e-6, 1e-12, "4.6: F_12 = f_norm·√(F_11·F_22)")

    # 4.7: T300 simetrik (X_t=X_c) için F_1=0
    _assert(abs(lam.F_1()) < 1e-12, "4.7: X_t=X_c için F_1=0")

    # 4.8: Pozitif belirli kuadratik form (tüm laminalar)
    from faz17_d1.core.material_allowables import available_laminas
    for name in available_laminas():
        l = get_lamina(name)
        _assert(tsai_wu_positive_definite(l),
                f"4.8.{name}: pozitif belirli (F_11·F_22 - F_12² > 0)")

    # 4.9: tsai_wu_coefficients sözlük formatı
    coefs = lam.tsai_wu_coefficients()
    for k in ("F_1", "F_2", "F_11", "F_22", "F_66", "F_12"):
        _assert(k in coefs, f"4.9.{k}: katsayı sözlükte mevcut")

    # 4.10: f_12_normalized = 0 → F_12 = 0
    from faz17_d1.core.material_allowables import LaminaProperties
    lam_no = LaminaProperties(
        name="no_F12", E_1_GPa=180, E_2_GPa=10, G_12_GPa=7, nu_12=0.28,
        X_t_MPa=1500, X_c_MPa=1500, Y_t_MPa=40, Y_c_MPa=246, S_MPa=68,
        f_12_normalized=0.0,
    )
    _assert(abs(lam_no.F_12()) < 1e-15, "4.10: f_norm=0 → F_12=0")


# ════════════════════════════════════════════════════════════════════════════
# GROUP 5 — A/B-basis istatistiksel allowables
# ════════════════════════════════════════════════════════════════════════════

def test_group_5_basis_allowables() -> None:
    print("\n[GROUP 5] A/B-basis istatistiksel allowables")
    from faz17_d1.core.material_allowables import (
        get_lamina, K_A_BASIS_NORMAL, K_B_BASIS_NORMAL, LaminaProperties,
    )

    # 5.1: A-basis < B-basis < mean (CV>0)
    lam = get_lamina("t700s_epoxy")
    mean = lam.X_t_MPa
    A = lam.A_basis(mean)
    B = lam.B_basis(mean)
    _assert(0 < A < B < mean, f"5.1: 0 < A({A:.0f}) < B({B:.0f}) < mean({mean:.0f})")

    # 5.2: A-basis manuel formül kontrolü
    cv = lam.coefficient_of_variation
    expected_A = mean * (1.0 - K_A_BASIS_NORMAL * cv)
    _assert_close(lam.X_t_A_basis(), expected_A, 1e-6, 1e-3,
                  "5.2: X_t A-basis = μ(1 - 2.326·CV)")

    # 5.3: B-basis manuel formül kontrolü
    expected_B = mean * (1.0 - K_B_BASIS_NORMAL * cv)
    _assert_close(lam.X_t_B_basis(), expected_B, 1e-6, 1e-3,
                  "5.3: X_t B-basis = μ(1 - 1.282·CV)")

    # 5.4: CV=0 → A-basis = B-basis = mean
    lam0 = LaminaProperties(
        name="cv0", E_1_GPa=180, E_2_GPa=10, G_12_GPa=7, nu_12=0.28,
        X_t_MPa=1500, X_c_MPa=1500, Y_t_MPa=40, Y_c_MPa=246, S_MPa=68,
        coefficient_of_variation=0.0,
    )
    _assert_close(lam0.X_t_A_basis(), 1500.0, 1e-9, 1e-6,
                  "5.4a: CV=0 → A-basis = mean")
    _assert_close(lam0.X_t_B_basis(), 1500.0, 1e-9, 1e-6,
                  "5.4b: CV=0 → B-basis = mean")

    # 5.5: Aşırı CV → A-basis negatif olamaz (sıfırla sınırla)
    lam_high = LaminaProperties(
        name="high_cv", E_1_GPa=180, E_2_GPa=10, G_12_GPa=7, nu_12=0.28,
        X_t_MPa=100, X_c_MPa=1500, Y_t_MPa=40, Y_c_MPa=246, S_MPa=68,
        coefficient_of_variation=0.5,  # 2.326*0.5 = 1.163 > 1
    )
    _assert(lam_high.X_t_A_basis() >= 0.0, "5.5: A-basis >= 0 garantisi")

    # 5.6: Tüm 5 dayanım için A/B-basis erişilebilir
    lam = get_lamina("im7_8552")
    accessors = [
        lam.X_t_A_basis, lam.X_t_B_basis,
        lam.X_c_A_basis, lam.X_c_B_basis,
        lam.Y_t_A_basis, lam.Y_t_B_basis,
        lam.Y_c_A_basis, lam.Y_c_B_basis,
        lam.S_A_basis, lam.S_B_basis,
    ]
    for i, fn in enumerate(accessors):
        v = fn()
        _assert(v > 0, f"5.6.{i}: {fn.__name__} pozitif değer döndürdü")

    # 5.7: A-basis k çarpan değeri (normal dağılım sabitleri CMH-17 ile uyumlu)
    _assert_close(K_A_BASIS_NORMAL, 2.326, 1e-3, 1e-3,
                  "5.7a: k_A=2.326 (CMH-17 normal)")
    _assert_close(K_B_BASIS_NORMAL, 1.282, 1e-3, 1e-3,
                  "5.7b: k_B=1.282 (CMH-17 normal)")


# ════════════════════════════════════════════════════════════════════════════
# GROUP 6 — Çevresel knockdown faktörleri
# ════════════════════════════════════════════════════════════════════════════

def test_group_6_knockdown() -> None:
    print("\n[GROUP 6] Çevresel knockdown faktörleri")
    from faz17_d1.core.material_allowables import EnvironmentalKnockdown

    # 6.1: Varsayılan = identitite (K_total = 1)
    k = EnvironmentalKnockdown()
    _assert_close(k.K_total, 1.0, 1e-12, 1e-12,
                  "6.1: varsayılan knockdown K_total=1")

    # 6.2: Sınır dışı → ValueError
    for bad in [0.0, -0.5, 1.5, 2.0]:
        try:
            EnvironmentalKnockdown(K_hotwet=bad)
            _assert(False, f"6.2.{bad}: K={bad} reddedilmeli")
        except ValueError:
            _assert(True, f"6.2.{bad}: K={bad} reddedildi")

    # 6.3: standard_pressure_vessel preset değerleri
    k = EnvironmentalKnockdown.standard_pressure_vessel()
    _assert(k.K_total < 1.0, "6.3a: PV knockdown < 1")
    _assert(k.K_total > 0.3, f"6.3b: PV knockdown >0.3 makul (got {k.K_total:.3f})")

    # 6.4: aerospace_long_term preset daha agresif
    k_aero = EnvironmentalKnockdown.aerospace_long_term()
    _assert(k_aero.K_total < k.K_total,
            f"6.4: aerospace ({k_aero.K_total:.3f}) < PV ({k.K_total:.3f})")

    # 6.5: apply() metodu doğru çarpıyor
    k = EnvironmentalKnockdown(K_hotwet=0.8, K_fatigue=0.5)
    expected = 1000.0 * 0.8 * 0.5
    _assert_close(k.apply(1000.0), expected, 1e-9, 1e-6,
                  "6.5: apply() = mean·K_total")

    # 6.6: Çarpım sırası bağımsızlığı
    k1 = EnvironmentalKnockdown(K_hotwet=0.8, K_fatigue=0.7, K_aging=0.9)
    k2 = EnvironmentalKnockdown(K_aging=0.9, K_fatigue=0.7, K_hotwet=0.8)
    _assert_close(k1.K_total, k2.K_total, 1e-12, 1e-12,
                  "6.6: çarpım sırası bağımsız")


# ════════════════════════════════════════════════════════════════════════════
# GROUP 7 — EngineeringMaterial entegrasyonu
# ════════════════════════════════════════════════════════════════════════════

def test_group_7_engineering_material() -> None:
    print("\n[GROUP 7] EngineeringMaterial — entegrasyon")
    from faz17_d1.core.material_allowables import (
        get_engineering_material, available_engineering_materials,
        EnvironmentalKnockdown,
    )

    # 7.1: Katalog
    names = available_engineering_materials()
    _assert(len(names) >= 4, f"7.1: ≥4 mühendislik malzeme (got {len(names)})")

    # 7.2: Tüm presetler oluşturulabilir
    for name in names:
        em = get_engineering_material(name)
        _assert(em.name == em.name, f"7.2.{name}: oluşturulabilir")
        _assert(em.lamina.X_t_MPa > 0, f"7.2.{name}.X_t: pozitif lamina")
        _assert(em.base.fiber.tex_g_km > 0, f"7.2.{name}.base: kütle base mevcut")

    # 7.3: Backward-compat — base.fiber_mass_kg eski API
    em = get_engineering_material("carbon_t700_epoxy_pv")
    m = em.base.fiber_mass_kg(1_000_000.0)  # 1 km fiber
    _assert(m > 0, f"7.3: base.fiber_mass_kg eski API erişilebilir (m={m:.4f} kg)")

    # 7.4: Design strength: B-basis + knockdown
    em = get_engineering_material("carbon_t700_epoxy_pv")
    X_t_mean = em.lamina.X_t_MPa
    X_t_B = em.lamina.X_t_B_basis()
    K = em.environment.K_total
    expected = X_t_B * K
    _assert_close(em.design_X_t(basis="B"), expected, 1e-6, 1e-3,
                  "7.4: design_X_t = X_t_B_basis · K_total")

    # 7.5: Design A < Design B < Design MEAN
    XA = em.design_X_t(basis="A")
    XB = em.design_X_t(basis="B")
    XM = em.design_X_t(basis="MEAN")
    _assert(XA < XB < XM, f"7.5: A({XA:.0f}) < B({XB:.0f}) < MEAN({XM:.0f})")

    # 7.6: Geçersiz basis
    try:
        em.design_X_t(basis="C")
        _assert(False, "7.6: 'C' basis reddedilmeli")
    except ValueError:
        _assert(True, "7.6: bilinmeyen basis reddedildi")

    # 7.7: Knockdown yok ortam → design = basis only
    from faz17_d1.core.material_allowables import EngineeringMaterial
    from faz17_d1.core.material_database import get_material
    from faz17_d1.core.material_allowables import get_lamina
    em_no = EngineeringMaterial(
        name="no_knockdown",
        base=get_material("carbon_t700_standard_epoxy"),
        lamina=get_lamina("t700s_epoxy"),
        environment=EnvironmentalKnockdown.benign_room_temp(),
    )
    _assert_close(em_no.design_X_t(basis="B"), em_no.lamina.X_t_B_basis(),
                  1e-6, 1e-3, "7.7: K_total=1 → design = B-basis")

    # 7.8: Bilinmeyen mühendislik malzeme KeyError
    try:
        get_engineering_material("uydurma")
        _assert(False, "7.8: bilinmeyen malzeme KeyError")
    except KeyError:
        _assert(True, "7.8: KeyError")


# ════════════════════════════════════════════════════════════════════════════
# GROUP 8 — Backward-compat: mevcut CAM modülleri kırılmamalı
# ════════════════════════════════════════════════════════════════════════════

def test_group_8_backward_compat() -> None:
    print("\n[GROUP 8] Backward-compat — eski material_database API'si")
    from faz17_d1.core.material_database import (
        get_material, available_materials, carbon_t700_standard_epoxy,
        eglass_epoxy, MaterialSpec, FiberSpec, ResinSpec, TowSpec,
    )

    # 8.1: Eski katalog dokunulmamış
    mats = available_materials()
    _assert("carbon_t700_standard_epoxy" in mats, "8.1: eski catalog adı korundu")
    _assert("eglass_epoxy" in mats, "8.1b: e-glass adı korundu")

    # 8.2: MaterialSpec API'si değişmemiş
    m = carbon_t700_standard_epoxy()
    _assert(hasattr(m, "fiber") and hasattr(m, "resin") and hasattr(m, "tow"),
            "8.2: MaterialSpec yapısı sabit")
    _assert(hasattr(m, "fiber_mass_kg"), "8.2b: fiber_mass_kg metodu var")
    _assert(hasattr(m, "composite_density_g_cm3"), "8.2c: density property var")

    # 8.3: Kütle hesabı eski formül
    m_kg = m.fiber_mass_kg(1_000_000.0)  # 1 km × 800 g/km = 800 g = 0.8 kg
    _assert_close(m_kg, 0.800, 1e-3, 1e-4, "8.3: 1 km × 800 g/km = 0.8 kg")

    # 8.4: Density kural karışımı
    rho = m.composite_density_g_cm3
    _assert(1.4 < rho < 1.7, f"8.4: T700/Epoksi ρ ~1.5 g/cm³ (got {rho:.3f})")

    # 8.5: Yeni material_allowables modülünü import etmek eski modülü etkilemez
    import faz17_d1.core.material_allowables  # noqa
    m2 = carbon_t700_standard_epoxy()
    _assert(m2.fiber.tex_g_km == m.fiber.tex_g_km,
            "8.5: yeni modül import sonrası eski katalog tutarlı")


# ════════════════════════════════════════════════════════════════════════════
# Runner
# ════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════
# GROUP 9 — ENG-2: Netting analysis — magic angle ve temel denklemler
# ════════════════════════════════════════════════════════════════════════════

def test_group_9_magic_angle() -> None:
    print("\n[GROUP 9] Netting — magic angle ve temel denklemler")
    from faz17_d1.core.netting_analysis import (
        MAGIC_ANGLE_DEG, MAGIC_ANGLE_RAD,
        helical_only_thickness, hoop_only_thickness,
        magic_angle_thickness, combined_hoop_helical,
    )

    # 9.1: Magic angle = arctan(√2) = 54.7356°
    _assert_close(MAGIC_ANGLE_DEG, 54.7356, 1e-4, 1e-3,
                  "9.1: Magic angle 54.7356°")
    _assert_close(MAGIC_ANGLE_RAD, math.atan(math.sqrt(2.0)), 1e-12, 1e-12,
                  "9.1b: Magic angle = arctan(√2)")
    _assert_close(math.tan(MAGIC_ANGLE_RAD)**2, 2.0, 1e-12, 1e-9,
                  "9.1c: tan²(α_magic) = 2")

    # 9.2: Vasiliev 2009 Worked Example: P=20MPa, D=200mm, σ_f=2000MPa
    #      Magic angle: t_α = 3PD/(4σ_f) = 3·20·200/(4·2000) = 1.5 mm
    t = magic_angle_thickness(20.0, 200.0, 2000.0)
    _assert_close(t, 1.5, 1e-6, 1e-6,
                  "9.2: Magic angle Vasiliev (P=20, D=200, σ=2000) → 1.5 mm")

    # 9.3: helical_only at α=0° → cos²=1, t = PD/(4σ_f) (en ince)
    # ama α=0 sınır, çok küçük değer kontrol et
    t_low = helical_only_thickness(10.0, 100.0, 1000.0, 1.0)
    # ≈ PD/(4σ) için çok küçük α
    _assert(t_low > 0, f"9.3: küçük α için t_low > 0 (got {t_low:.6f})")

    # 9.4: hoop_only: t = PD/(2σ_f)
    t_h = hoop_only_thickness(10.0, 100.0, 1000.0)
    _assert_close(t_h, 0.5, 1e-9, 1e-9, "9.4: hoop_only t = PD/(2σ_f) = 0.5")

    # 9.5: Magic angle altı tek-helisel ile hoop denge sağlanmaz
    # combined ile α<magic → t_hoop > 0
    res = combined_hoop_helical(10.0, 100.0, 1000.0, 30.0)
    _assert(res.t_hoop_mm > 0,
            f"9.5: α=30° < magic ⟹ t_hoop > 0 (got {res.t_hoop_mm:.4f})")
    _assert(not res.is_magic_angle, "9.5b: 30° magic flag False")

    # 9.6: Magic angle → t_hoop ≈ 0
    res = combined_hoop_helical(10.0, 100.0, 1000.0, MAGIC_ANGLE_DEG)
    _assert(abs(res.t_hoop_mm) < 1e-6,
            f"9.6: magic angle ⟹ t_hoop≈0 (got {res.t_hoop_mm:.6g})")
    _assert(res.is_magic_angle, "9.6b: is_magic_angle True")

    # 9.7: α > magic → helisel hoop'u aşıyor, t_hoop sıfıra sınırlı
    res = combined_hoop_helical(10.0, 100.0, 1000.0, 70.0)
    _assert(res.t_hoop_mm == 0.0, "9.7: α>magic → t_hoop sıfıra sınırlı")
    _assert("UYARI" in res.notes, "9.7b: kullanıcıya uyarı verildi")

    # 9.8: Hatalı girdiler
    try:
        helical_only_thickness(-1, 100, 1000, 30)
        _assert(False, "9.8a: P<0 reddedilmeli")
    except ValueError:
        _assert(True, "9.8a: P<0 reddedildi")
    try:
        helical_only_thickness(10, 100, 1000, 95)
        _assert(False, "9.8b: α>90° reddedilmeli")
    except ValueError:
        _assert(True, "9.8b: α>90° reddedildi")
    try:
        helical_only_thickness(10, 100, 1000, 0)
        _assert(False, "9.8c: α=0 reddedilmeli")
    except ValueError:
        _assert(True, "9.8c: α=0 reddedildi")


# ════════════════════════════════════════════════════════════════════════════
# GROUP 10 — ENG-2: Sphere ve mühendislik malzeme entegrasyonu
# ════════════════════════════════════════════════════════════════════════════

def test_group_10_sphere_and_material() -> None:
    print("\n[GROUP 10] Netting — sphere + material entegrasyonu")
    from faz17_d1.core.netting_analysis import (
        sphere_netting_thickness, netting_from_material, MAGIC_ANGLE_DEG,
        recommend_winding_angle,
    )
    from faz17_d1.core.material_allowables import get_engineering_material

    # 10.1: Sphere t = PD/(4σ_f); P=10, D=100, σ=1000 → t = 0.25 mm
    t = sphere_netting_thickness(10.0, 100.0, 1000.0)
    _assert_close(t, 0.25, 1e-9, 1e-9, "10.1: Sphere t = PD/(4σ_f) = 0.25 mm")

    # 10.2: Sphere kalınlığı = silindir magic angle kalınlığının 1/6'sı
    # cyl_magic t = 3PD/(4σ), sphere t = PD/(4σ) ⟹ ratio = 3
    from faz17_d1.core.netting_analysis import magic_angle_thickness
    t_cyl = magic_angle_thickness(10.0, 100.0, 1000.0)
    _assert_close(t_cyl / t, 3.0, 1e-9, 1e-9,
                  "10.2: cyl_magic / sphere = 3 (geometri sabit)")

    # 10.3: netting_from_material varsayılan magic angle
    mat = get_engineering_material("carbon_t700_epoxy_pv")
    res = netting_from_material(10.0, 200.0, mat)
    _assert(res.is_magic_angle, "10.3a: varsayılan magic angle")
    _assert(res.t_helical_mm > 0, "10.3b: t_helical pozitif")
    _assert(abs(res.t_hoop_mm) < 1e-6, "10.3c: magic ⟹ t_hoop≈0")

    # 10.4: Basis A < B → daha düşük dayanım → daha kalın laminat
    res_A = netting_from_material(10.0, 200.0, mat, basis="A")
    res_B = netting_from_material(10.0, 200.0, mat, basis="B")
    _assert(res_A.t_helical_mm > res_B.t_helical_mm,
            f"10.4: A-basis t({res_A.t_helical_mm:.3f}) > B-basis t({res_B.t_helical_mm:.3f})")

    # 10.5: Yüksek basınç → daha kalın
    res1 = netting_from_material(5.0, 200.0, mat)
    res2 = netting_from_material(50.0, 200.0, mat)
    _assert(res2.t_helical_mm > res1.t_helical_mm,
            "10.5: P artarsa t artar (lineer)")
    _assert_close(res2.t_helical_mm / res1.t_helical_mm, 10.0, 1e-6, 1e-3,
                  "10.5b: t ~ P (lineer)")

    # 10.6: Çap artarsa kalınlık artar (lineer)
    res_d1 = netting_from_material(10.0, 100.0, mat)
    res_d2 = netting_from_material(10.0, 300.0, mat)
    _assert_close(res_d2.t_helical_mm / res_d1.t_helical_mm, 3.0, 1e-6, 1e-3,
                  "10.6: t ~ D (lineer)")

    # 10.7: recommend_winding_angle: kısa kap magic angle
    a, note = recommend_winding_angle(200.0, 100.0, 10.0, mat)
    _assert_close(a, MAGIC_ANGLE_DEG, 1e-6, 1e-3,
                  f"10.7: L/D=0.5 ⟹ magic angle (got {a:.2f}°, note='{note}')")

    # 10.8: Uzun kap düşük açı + hoop
    a2, note2 = recommend_winding_angle(200.0, 2000.0, 10.0, mat)
    _assert(a2 < MAGIC_ANGLE_DEG,
            f"10.8: L/D=10 ⟹ düşük α (got {a2:.1f}°)")


# ════════════════════════════════════════════════════════════════════════════
# GROUP 11 — ENG-2: Dome (kubbe) kalınlık profili
# ════════════════════════════════════════════════════════════════════════════

def test_group_11_dome() -> None:
    print("\n[GROUP 11] Netting — kubbe (dome) kalınlık profili")
    from faz17_d1.core.netting_analysis import dome_thickness_profile

    # Basit yarı-elipsoid kubbe profili: r(z) = r_eq·√(1 − (z/h)²)
    # α_eq seçimi: r·sin(α) = c koşulu yol boyunca geçerli olmalı ⟹
    # c = r_eq·sin(α_eq) ≤ min(r) ⟹ α_eq ≤ arcsin(r_min/r_eq).
    r_eq = 100.0
    h = 80.0
    r_polar = 15.0
    z_pts = [0.0, 20.0, 40.0, 60.0, 75.0]  # ekvatordan kutba doğru
    r_pts: List[float] = []
    for z in z_pts:
        ratio = z / h
        r_pts.append(r_eq * math.sqrt(max(1e-6, 1.0 - ratio * ratio)))
    # min(r_pts) ≈ 34.8 → α_eq ≤ arcsin(34.8/100) ≈ 20.4°; 15° güvenli
    alpha_eq_test = 15.0

    # 11.1: r_polar < r_eq doğrulama → r_pts[-1] yakın olmalı (ama tam 15 değil)
    res = dome_thickness_profile(
        equator_radius_mm=r_eq, polar_radius_mm=r_polar,
        equator_alpha_deg=alpha_eq_test, t_equator_mm=1.0,
        z_mm=z_pts, r_mm=r_pts,
    )
    _assert(len(res.t_dome_mm) == len(z_pts),
            "11.1: çıktı uzunluğu girdi ile aynı")

    # 11.2: Ekvatorda kalınlık t_eq'a eşit
    _assert_close(res.t_dome_mm[0], 1.0, 1e-6, 1e-4,
                  f"11.2: ekvator t={res.t_dome_mm[0]:.4f} (beklenen 1.0)")

    # 11.3: Kutba yaklaştıkça kalınlık artar (fiber yığılması)
    _assert(res.t_dome_mm[-1] > res.t_dome_mm[0],
            f"11.3: t_kutup({res.t_dome_mm[-1]:.3f}) > t_ekvator({res.t_dome_mm[0]:.3f})")

    # 11.4: Sarma açısı kutba yaklaştıkça artar (Clairaut: r·sin α = sabit)
    _assert(res.alpha_deg[-1] > res.alpha_deg[0],
            f"11.4: α kutuba doğru artıyor ({res.alpha_deg[0]:.1f}° → {res.alpha_deg[-1]:.1f}°)")

    # 11.5: Clairaut sabiti boyunca korunur
    c0 = r_pts[0] * math.sin(math.radians(res.alpha_deg[0]))
    for i in range(len(r_pts)):
        c_i = r_pts[i] * math.sin(math.radians(res.alpha_deg[i]))
        _assert_close(c_i, c0, 1e-6, 1e-3,
                      f"11.5.{i}: Clairaut r·sin α sabit")

    # 11.6: Geçersiz girdi
    try:
        dome_thickness_profile(100, 200, 30, 1.0, [0, 1], [100, 90])  # polar > eq
        _assert(False, "11.6: polar > eq reddedilmeli")
    except ValueError:
        _assert(True, "11.6: polar > eq reddedildi")
    try:
        dome_thickness_profile(100, 10, 95, 1.0, [0], [100])  # α=95
        _assert(False, "11.6b: α>90 reddedilmeli")
    except ValueError:
        _assert(True, "11.6b: α>90 reddedildi")


# ════════════════════════════════════════════════════════════════════════════
# GROUP 12 — ENG-3: CLT — Q matrisi ve rotasyon
# ════════════════════════════════════════════════════════════════════════════

def test_group_12_Q_matrix() -> None:
    print("\n[GROUP 12] CLT — Q ve Q̄ matrisleri")
    import numpy as np
    from faz17_d1.core.clt_engine import lamina_Q_matrix, rotate_Q
    from faz17_d1.core.material_allowables import get_lamina

    lam = get_lamina("t300_5208")
    Q = lamina_Q_matrix(lam)

    # 12.1: Q ortotropik (Q[0,2]=Q[1,2]=0)
    _assert(abs(Q[0, 2]) < 1e-9 and abs(Q[1, 2]) < 1e-9,
            "12.1: lokal Q ortotropik (Q_16=Q_26=0)")

    # 12.2: Q simetrik
    _assert(np.allclose(Q, Q.T), "12.2: Q simetrik")

    # 12.3: Jones 1999, T300/5208 Q_11 ≈ 182 GPa, Q_22 ≈ 10.3 GPa (Vf≈0.6)
    # Manuel: Q_11 = E_1/(1-ν12·ν21), ν21=0.28·10.3/181 = 0.01594
    # Q_11 = 181000 / (1 - 0.28·0.01594) = 181000 / 0.99554 = 181812 MPa
    expected_Q11 = 181000.0 / (1.0 - 0.28 * (0.28 * 10.3 / 181.0))
    _assert_close(Q[0, 0], expected_Q11, 1e-4, 10.0,
                  f"12.3: Q_11 ≈ {expected_Q11:.0f} MPa")

    # 12.4: 0° rotasyon = identite
    Qb0 = rotate_Q(Q, 0.0)
    _assert(np.allclose(Qb0, Q, atol=1e-6),
            "12.4: 0° rotasyon = Q")

    # 12.5: 90° rotasyon: Q̄_11(90°) = Q_22(0°)
    Qb90 = rotate_Q(Q, 90.0)
    _assert_close(Qb90[0, 0], Q[1, 1], 1e-6, 1e-3,
                  "12.5: Q̄_11(90°) = Q_22(0°)")
    _assert_close(Qb90[1, 1], Q[0, 0], 1e-6, 1e-3,
                  "12.5b: Q̄_22(90°) = Q_11(0°)")

    # 12.6: 180° rotasyon = identite (simetri)
    Qb180 = rotate_Q(Q, 180.0)
    _assert(np.allclose(Qb180, Q, atol=1.0),  # büyük modüller, mutlak tol
            "12.6: 180° rotasyon ≈ Q (Q simetri ile)")

    # 12.7: 45° rotasyon Q̄_16 ≠ 0 (kuplaj)
    Qb45 = rotate_Q(Q, 45.0)
    _assert(abs(Qb45[0, 2]) > 1.0,
            f"12.7: Q̄_16(45°) ≠ 0 (got {Qb45[0,2]:.2f})")

    # 12.8: ±θ için Q̄_11 aynı (simetri), Q̄_16 ters işaret
    Qp = rotate_Q(Q, 30.0)
    Qm = rotate_Q(Q, -30.0)
    _assert_close(Qp[0, 0], Qm[0, 0], 1e-9, 1e-3,
                  "12.8a: Q̄_11(+θ) = Q̄_11(-θ)")
    _assert_close(Qp[0, 2], -Qm[0, 2], 1e-9, 1e-3,
                  "12.8b: Q̄_16(+θ) = -Q̄_16(-θ)")


# ════════════════════════════════════════════════════════════════════════════
# GROUP 13 — ENG-3: LaminateStackup ve ABD matrisi
# ════════════════════════════════════════════════════════════════════════════

def test_group_13_ABD() -> None:
    print("\n[GROUP 13] CLT — laminat yığını + ABD matrisi")
    import numpy as np
    from faz17_d1.core.clt_engine import (
        Ply, LaminateStackup, compute_ABD,
        make_symmetric_balanced_stackup, make_helical_hoop_stackup,
    )
    from faz17_d1.core.material_allowables import get_lamina

    lam = get_lamina("t300_5208")

    # 13.1: Boş yığın hatası
    try:
        compute_ABD(LaminateStackup(plies=[]))
        _assert(False, "13.1: boş yığın hata")
    except ValueError:
        _assert(True, "13.1: boş yığın reddedildi")

    # 13.2: Tek 0° ply için A = Q · h
    stack = LaminateStackup(plies=[Ply(0.0, 0.5, lam)])
    abd = compute_ABD(stack)
    from faz17_d1.core.clt_engine import lamina_Q_matrix
    Q = lamina_Q_matrix(lam)
    expected_A = Q * 0.5
    _assert(np.allclose(abd.A, expected_A, atol=1e-3),
            "13.2: tek ply A = Q·h")

    # 13.3: Simetrik laminat için B ≈ 0
    stack_sym = make_symmetric_balanced_stackup(
        [0.0, 45.0, -45.0, 90.0], 0.125, lam,
    )
    _assert(stack_sym.is_symmetric(), "13.3a: stackup simetrik")
    abd = compute_ABD(stack_sym)
    _assert(abd.is_symmetric_numerically(),
            f"13.3b: simetrik laminat → B ≈ 0 (max B = {np.max(np.abs(abd.B)):.2e})")

    # 13.4: Quasi-iso [0/45/-45/90]_s → A_11 ≈ A_22, A_16=A_26=0
    A = abd.A
    _assert_close(A[0, 0], A[1, 1], 1e-3, 100.0,
                  f"13.4a: quasi-iso A_11≈A_22 ({A[0,0]:.0f} vs {A[1,1]:.0f})")
    _assert(abs(A[0, 2]) < 1e-3 * abs(A[0, 0]),
            f"13.4b: quasi-iso A_16≈0 ({A[0,2]:.2e})")
    _assert(abs(A[1, 2]) < 1e-3 * abs(A[1, 1]),
            f"13.4c: quasi-iso A_26≈0 ({A[1,2]:.2e})")

    # 13.5: Balanced laminat [+30/-30] → A_16 = A_26 = 0
    stack_bal = make_symmetric_balanced_stackup([30.0, -30.0], 0.125, lam)
    _assert(stack_bal.is_balanced(), "13.5a: ±30 balanced")
    abd_bal = compute_ABD(stack_bal)
    _assert(abs(abd_bal.A[0, 2]) < 1e-3 * abs(abd_bal.A[0, 0]),
            f"13.5b: balanced A_16≈0 ({abd_bal.A[0,2]:.2e})")

    # 13.6: A pozitif belirli (özdeğerler > 0)
    eigs = np.linalg.eigvalsh(abd_bal.A)
    _assert(all(e > 0 for e in eigs),
            f"13.6: A pozitif belirli (eigs={eigs})")

    # 13.7: D matrisi pozitif belirli
    eigs_D = np.linalg.eigvalsh(abd_bal.D)
    _assert(all(e > 0 for e in eigs_D),
            f"13.7: D pozitif belirli (eigs={eigs_D})")

    # 13.8: Toplam kalınlık doğru
    _assert_close(stack_sym.total_thickness_mm(), 0.125 * 8, 1e-9, 1e-6,
                  "13.8: toplam kalınlık 8·0.125=1.0 mm")

    # 13.9: z-koordinatları orta düzleme göre simetrik
    z = stack_sym.z_coords()
    _assert_close(z[0], -0.5, 1e-9, 1e-9, "13.9a: z_alt = -h/2")
    _assert_close(z[-1], 0.5, 1e-9, 1e-9, "13.9b: z_üst = +h/2")

    # 13.10: helical+hoop yığını
    stack_hh = make_helical_hoop_stackup(
        alpha_deg=54.74, n_helical_pairs=4, n_hoop=2,
        thickness_per_ply_mm=0.15, lamina=lam,
    )
    _assert(stack_hh.is_symmetric(),
            "13.10a: helical+hoop yığını simetrik")
    n_total = 4 * 2 + 2  # half
    n_full = n_total * 2
    _assert(len(stack_hh.plies) == n_full,
            f"13.10b: ply sayısı = {n_full} (got {len(stack_hh.plies)})")


# ════════════════════════════════════════════════════════════════════════════
# GROUP 14 — ENG-3: Membran çözümü ve efektif sabitler
# ════════════════════════════════════════════════════════════════════════════

def test_group_14_membrane() -> None:
    print("\n[GROUP 14] CLT — membran çözümü ve efektif sabitler")
    import numpy as np
    from faz17_d1.core.clt_engine import (
        Ply, LaminateStackup, compute_ABD, solve_membrane,
        laminate_effective_constants, make_symmetric_balanced_stackup,
    )
    from faz17_d1.core.material_allowables import get_lamina

    lam = get_lamina("t300_5208")

    # 14.1: Tek 0° ply, N_x yükü → ε_x = N_x / (E_1·h) (uniaxial)
    stack = LaminateStackup(plies=[Ply(0.0, 1.0, lam)])
    abd = compute_ABD(stack)
    N_x = 1000.0  # N/mm
    resp = solve_membrane(abd, N_x, 0.0, 0.0)
    # Beklenen: ε_x ≈ N_x / (E_1·h) = 1000/(181000·1.0) = 0.005525
    # (küçük Poisson düzeltmesi olmadan yaklaşık)
    eps_x_approx = N_x / (181000.0 * 1.0)
    _assert(0.5 * eps_x_approx < resp.epsilon0[0] < 2.0 * eps_x_approx,
            f"14.1: ε_x ~ N_x/(E·h) ({resp.epsilon0[0]:.5e})")

    # 14.2: 90° ply altında aynı N_x → ε_x büyük (zayıf yön)
    stack90 = LaminateStackup(plies=[Ply(90.0, 1.0, lam)])
    abd90 = compute_ABD(stack90)
    resp90 = solve_membrane(abd90, N_x, 0.0, 0.0)
    _assert(resp90.epsilon0[0] > 10.0 * resp.epsilon0[0],
            f"14.2: 90° ε_x >> 0° ε_x (ratio={resp90.epsilon0[0]/resp.epsilon0[0]:.1f})")

    # 14.3: Quasi-iso laminat efektif E ≈ izotropik
    stack_qi = make_symmetric_balanced_stackup(
        [0.0, 45.0, -45.0, 90.0], 0.125, lam,
    )
    abd_qi = compute_ABD(stack_qi)
    eff = laminate_effective_constants(abd_qi)
    _assert(eff["E_x_MPa"] > 0, "14.3a: E_x_eff > 0")
    _assert_close(eff["E_x_MPa"], eff["E_y_MPa"], 1e-3, 100.0,
                  f"14.3b: quasi-iso E_x≈E_y "
                  f"({eff['E_x_MPa']:.0f} vs {eff['E_y_MPa']:.0f})")
    _assert(0 < eff["nu_xy"] < 0.5,
            f"14.3c: 0 < ν_xy < 0.5 ({eff['nu_xy']:.3f})")
    # Daniel & Ishai Table 4.3: quasi-iso T300/5208 E ≈ 68-70 GPa
    _assert(50000.0 < eff["E_x_MPa"] < 80000.0,
            f"14.3d: quasi-iso E ~70 GPa (got {eff['E_x_MPa']/1000:.1f} GPa)")

    # 14.4: Tek 0° ply efektif E_x ≈ E_1
    stack_uni = LaminateStackup(plies=[Ply(0.0, 1.0, lam)])
    abd_uni = compute_ABD(stack_uni)
    eff_uni = laminate_effective_constants(abd_uni)
    _assert_close(eff_uni["E_x_MPa"], 181000.0, 0.01, 1000.0,
                  f"14.4: 0° E_x ≈ 181 GPa ({eff_uni['E_x_MPa']/1000:.1f})")

    # 14.5: Tek 0° ply efektif ν_xy ≈ ν_12
    _assert_close(eff_uni["nu_xy"], 0.28, 0.01, 0.005,
                  f"14.5: ν_xy ≈ ν_12 ({eff_uni['nu_xy']:.4f})")

    # 14.6: Membran response shape doğru
    _assert(resp.epsilon0.shape == (3,), "14.6a: ε⁰ shape (3,)")
    _assert(resp.ply_stress_global.shape == (1, 3),
            "14.6b: ply_stress_global shape (N,3)")
    _assert(resp.ply_stress_local.shape == (1, 3),
            "14.6c: ply_stress_local shape (N,3)")

    # 14.7: 0° ply için lokal σ_1 ≈ N_x / h (saf eksenel)
    # σ_local[0] = σ_x (0° ply için 1-ekseni = x-ekseni)
    _assert_close(resp.ply_stress_local[0, 0], N_x / 1.0, 0.01, 10.0,
                  f"14.7: 0° lokal σ_1 ≈ N_x/h ({resp.ply_stress_local[0,0]:.1f})")


# ════════════════════════════════════════════════════════════════════════════
# GROUP 15 — ENG-4: Tsai-Wu, max stress, Tsai-Hill — tek nokta hasar
# ════════════════════════════════════════════════════════════════════════════

def test_group_15_failure_indices() -> None:
    print("\n[GROUP 15] Failure criterion — tek nokta indeksleri")
    from faz17_d1.core.failure_criterion import (
        tsai_wu_FI, tsai_wu_strength_ratio, max_stress_FI, tsai_hill_FI,
    )
    from faz17_d1.core.material_allowables import get_lamina

    lam = get_lamina("t300_5208")

    # 15.1: Saf σ_1 = X_t → Tsai-Wu FI ≈ 1 (X_t=X_c için kuadratik)
    FI = tsai_wu_FI(lam, lam.X_t_MPa, 0.0, 0.0)
    _assert_close(FI, 1.0, 1e-3, 1e-3,
                  f"15.1: σ_1=X_t → Tsai-Wu FI=1 (got {FI:.4f})")

    # 15.2: Saf σ_2 = Y_t → FI ≈ 1
    FI = tsai_wu_FI(lam, 0.0, lam.Y_t_MPa, 0.0)
    _assert_close(FI, 1.0, 1e-3, 1e-3,
                  f"15.2: σ_2=Y_t → FI=1 (got {FI:.4f})")

    # 15.3: Saf τ_12 = S → FI ≈ 1 (sadece F_66·S² = 1)
    FI = tsai_wu_FI(lam, 0.0, 0.0, lam.S_MPa)
    _assert_close(FI, 1.0, 1e-9, 1e-9,
                  f"15.3: τ_12=S → FI=1 (got {FI:.6f})")

    # 15.4: σ_1 = X_t / 2 → max-stress FI = 0.5
    FI_max = max_stress_FI(lam, lam.X_t_MPa / 2.0, 0.0, 0.0)
    _assert_close(FI_max, 0.5, 1e-9, 1e-9,
                  f"15.4: σ_1=X_t/2 → max-stress FI=0.5 (got {FI_max:.4f})")

    # 15.5: Saf basma σ_1 = -X_c → max-stress FI = 1
    FI_max = max_stress_FI(lam, -lam.X_c_MPa, 0.0, 0.0)
    _assert_close(FI_max, 1.0, 1e-9, 1e-9,
                  f"15.5: σ_1=-X_c → max-stress FI=1 (got {FI_max:.4f})")

    # 15.6: SR ile FI tutarlılığı: SR ≈ X_t/σ_1 saf uniax
    sigma_app = lam.X_t_MPa / 4.0
    SR = tsai_wu_strength_ratio(lam, sigma_app, 0.0, 0.0)
    _assert_close(SR, 4.0, 1e-2, 0.05,
                  f"15.6: SR(X_t/4) ≈ 4 (got {SR:.4f})")

    # 15.7: Sıfır yük → SR sonsuz veya çok büyük
    SR0 = tsai_wu_strength_ratio(lam, 0.0, 0.0, 0.0)
    _assert(SR0 == float("inf") or SR0 > 1e10,
            f"15.7: sıfır yük → SR ∞ (got {SR0})")

    # 15.8: Tsai-Hill saf σ_1 = X_t → FI = 1
    FI_TH = tsai_hill_FI(lam, lam.X_t_MPa, 0.0, 0.0)
    _assert_close(FI_TH, 1.0, 1e-6, 1e-6,
                  f"15.8: Tsai-Hill σ_1=X_t → FI=1 (got {FI_TH:.4f})")

    # 15.9: Tsai-Hill σ_2 = Y_t → FI = 1
    FI_TH = tsai_hill_FI(lam, 0.0, lam.Y_t_MPa, 0.0)
    _assert_close(FI_TH, 1.0, 1e-6, 1e-6,
                  f"15.9: Tsai-Hill σ_2=Y_t → FI=1 (got {FI_TH:.4f})")

    # 15.10: SR·σ uygulandığında FI=1 (Tsai-Wu kuadratik tutarlılık)
    s1, s2, t12 = 400.0, -100.0, 30.0
    SR = tsai_wu_strength_ratio(lam, s1, s2, t12)
    FI_at_SR = tsai_wu_FI(lam, SR * s1, SR * s2, SR * t12)
    _assert_close(FI_at_SR, 1.0, 1e-4, 1e-4,
                  f"15.10: FI(SR·σ) = 1 tutarlı (got {FI_at_SR:.6f})")

    # 15.11: Bilinmeyen kriter ValueError
    from faz17_d1.core.failure_criterion import first_ply_failure
    from faz17_d1.core.clt_engine import (
        Ply, LaminateStackup, compute_ABD,
    )
    stack = LaminateStackup(plies=[Ply(0.0, 1.0, lam)])
    abd = compute_ABD(stack)
    try:
        first_ply_failure(abd, 100, 0, 0, criterion="bogus")
        _assert(False, "15.11: bilinmeyen criterion reddedilmeli")
    except ValueError:
        _assert(True, "15.11: bilinmeyen criterion reddedildi")


# ════════════════════════════════════════════════════════════════════════════
# GROUP 16 — ENG-4: First-Ply-Failure (FPF) laminat çözümü
# ════════════════════════════════════════════════════════════════════════════

def test_group_16_FPF() -> None:
    print("\n[GROUP 16] FPF — laminat hasar analizi")
    from faz17_d1.core.failure_criterion import (
        first_ply_failure, find_failure_load,
    )
    from faz17_d1.core.clt_engine import (
        Ply, LaminateStackup, compute_ABD, make_symmetric_balanced_stackup,
        make_helical_hoop_stackup,
    )
    from faz17_d1.core.material_allowables import get_lamina

    lam = get_lamina("t300_5208")

    # 16.1: Tek 0° ply, N_x = X_t·h → FPF (FI=1)
    h = 1.0
    stack = LaminateStackup(plies=[Ply(0.0, h, lam)])
    abd = compute_ABD(stack)
    N_x_fail = lam.X_t_MPa * h
    res = first_ply_failure(abd, N_x_fail, 0.0, 0.0, criterion="tsai_wu")
    _assert_close(res.max_FI, 1.0, 1e-2, 1e-2,
                  f"16.1: 0° ply N_x=X_t·h → FI=1 (got {res.max_FI:.4f})")

    # 16.2: Yarı yük → FI ≈ 0.25 (kuadratik)
    res_half = first_ply_failure(abd, N_x_fail / 2.0, 0.0, 0.0)
    _assert(res_half.max_FI < res.max_FI,
            f"16.2a: yarı yük FI < tam yük FI")
    _assert_close(res_half.max_FI, 0.25, 0.05, 0.05,
                  f"16.2b: yarı yük FI ≈ 0.25 (kuadratik, got {res_half.max_FI:.4f})")

    # 16.3: SR tutarlılık (FPF)
    _assert_close(res_half.min_SR, 2.0, 1e-2, 0.05,
                  f"16.3: SR ≈ 2 (got {res_half.min_SR:.4f})")

    # 16.4: Güvenli → is_safe=True
    _assert(res_half.is_safe, "16.4a: yarı yük güvenli")
    _assert(not res.is_safe or abs(res.max_FI - 1.0) < 0.01,
            "16.4b: tam yük sınır (FI≈1)")

    # 16.5: Quasi-iso laminat — eksenel yük altında min_SR kritik ply
    stack_qi = make_symmetric_balanced_stackup(
        [0.0, 45.0, -45.0, 90.0], 0.125, lam,
    )
    abd_qi = compute_ABD(stack_qi)
    res_qi = first_ply_failure(abd_qi, 1000.0, 0.0, 0.0)
    _assert(0 <= res_qi.critical_ply_index < len(stack_qi.plies),
            "16.5a: kritik ply indeksi geçerli")
    _assert(res_qi.min_SR > 0,
            f"16.5b: SR > 0 (got {res_qi.min_SR:.4f})")

    # 16.6: find_failure_load == res.min_SR (Tsai-Wu)
    sr = find_failure_load(abd_qi, 1000.0, 0.0, 0.0)
    _assert_close(sr, res_qi.min_SR, 1e-9, 1e-9,
                  "16.6: find_failure_load == res.min_SR")

    # 16.7: Helisel+hoop tipik PV laminat — basınç temsili N
    # Silindirik kabuk N_x = PD/4, N_y = PD/2; basit P/D ile temsil et
    P = 10.0
    D = 200.0
    # h hesaplaması: önce kalın bir laminat (8 helical pair + 4 hoop) → kalın
    stack_hh = make_helical_hoop_stackup(
        alpha_deg=54.74, n_helical_pairs=4, n_hoop=2,
        thickness_per_ply_mm=0.2, lamina=lam,
    )
    abd_hh = compute_ABD(stack_hh)
    h_total = stack_hh.total_thickness_mm()
    N_x = P * D / 4.0  # N/mm (eksenel)
    N_y = P * D / 2.0  # N/mm (hoop)
    res_hh = first_ply_failure(abd_hh, N_x, N_y, 0.0)
    _assert(res_hh.min_SR > 0,
            f"16.7a: helical+hoop SR>0 (got {res_hh.min_SR:.3f})")
    _assert(len(res_hh.failure_indices) == len(stack_hh.plies),
            "16.7b: FI listesi tüm plyleri kapsar")

    # 16.8: Tsai-Hill ve max_stress kriterler de çalışır
    res_th = first_ply_failure(abd_qi, 1000.0, 0.0, 0.0, criterion="tsai_hill")
    res_ms = first_ply_failure(abd_qi, 1000.0, 0.0, 0.0, criterion="max_stress")
    _assert(res_th.criterion == "tsai_hill", "16.8a: criterion ad")
    _assert(res_ms.criterion == "max_stress", "16.8b: criterion ad")
    _assert(res_ms.max_FI > 0, "16.8c: max_stress FI>0")
    _assert(res_th.max_FI > 0, "16.8d: tsai_hill FI>0")


GROUPS: List[Tuple[str, Callable[[], None]]] = [
    ("ENG-1: LaminaProperties validation", test_group_1_lamina_basic),
    ("ENG-1: Lamina catalog", test_group_2_lamina_catalog),
    ("ENG-1: Reciprocal Poisson", test_group_3_reciprocal_poisson),
    ("ENG-1: Tsai-Wu coefficients", test_group_4_tsai_wu),
    ("ENG-1: Basis allowables", test_group_5_basis_allowables),
    ("ENG-1: Knockdown factors", test_group_6_knockdown),
    ("ENG-1: EngineeringMaterial", test_group_7_engineering_material),
    ("ENG-1: Backward-compat", test_group_8_backward_compat),
    ("ENG-2: Magic angle", test_group_9_magic_angle),
    ("ENG-2: Sphere + material", test_group_10_sphere_and_material),
    ("ENG-2: Dome profile", test_group_11_dome),
    ("ENG-3: Q matrix + rotation", test_group_12_Q_matrix),
    ("ENG-3: Laminate ABD", test_group_13_ABD),
    ("ENG-3: Membrane solver", test_group_14_membrane),
    ("ENG-4: Failure indices", test_group_15_failure_indices),
    ("ENG-4: FPF analysis", test_group_16_FPF),
]


def main() -> int:
    global _PASS, _FAIL, _FAILURES
    print("═" * 72)
    print("  FAZ 23 — ENGINEERING LAYER TEST SUITE")
    print("═" * 72)

    for name, fn in GROUPS:
        try:
            fn()
        except Exception as e:
            _FAIL += 1
            _FAILURES.append(f"{name}: UNCAUGHT {type(e).__name__}: {e}")
            print(f"  ✗ UNCAUGHT EXCEPTION in {name}: {e}")
            traceback.print_exc()

    print("\n" + "═" * 72)
    print(f"  TOPLAM: {_PASS} PASS / {_FAIL} FAIL")
    print("═" * 72)
    if _FAIL > 0:
        print("\nBaşarısız assertion'lar:")
        for f in _FAILURES:
            print(f"  • {f}")
        return 1
    print("  ✓ TÜM TESTLER GEÇTİ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
