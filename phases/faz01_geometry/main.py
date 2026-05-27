#!/usr/bin/env python3
"""
main.py — End-to-End Pipeline Doğrulama
=========================================
Test senaryosu:
  Mandrel: Silindir, R=50mm, L=300mm
  Sarım:   α=30°, b=10mm, S'=100mm/s
  Eksenler: X (linear carriage) + A (rotary spindle)
  Çıktı:   GRBL uyumlu G-code

Doğrulama:
  1. Clairaut ilişkisi: c = R·sin(α) = 25.0 mm
  2. Pitch: 2πR/tan(α) = 544.139 mm/rev
  3. dφ/dz = tan(α)/R = 0.011547 rad/mm
  4. Feed rate: F = S'·cos(α)·60 = 5196.15 mm/min
  5. Tüm noktalarda r·sin(α) = c
  6. G-code syntax kontrolü
"""

import math
import sys
import os

# Modülleri bulabilmesi için path ayarla
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from geometry import CylindricalMandrel, validate_geometry_contract
from winding_math import (
    WindingParameters,
    ClairautCalculator,
    HelicalPathGenerator,
    validate_toolpath_clairaut,
)
from motion_planner import MachineConfig, MotionPlanner
from gcode_generator import GcodeConfig, GcodeGenerator


# ============================================================
# Test parametreleri
# ============================================================

R     = 50.0    # Silindir yarıçapı [mm]
L     = 300.0   # Silindir uzunluğu [mm]
ALPHA = 30.0    # Sarım açısı [°]
B     = 10.0    # Band genişliği [mm]
S_PRIME = 100.0 # Hedef fiber hızı [mm/s]

# Analitik beklentiler (elle hesaplama)
EXPECTED_C         = R * math.sin(math.radians(ALPHA))  # 25.0 mm
EXPECTED_PITCH     = 2 * math.pi * R / math.tan(math.radians(ALPHA))  # 544.1385 mm
EXPECTED_DPHI_DZ   = math.tan(math.radians(ALPHA)) / R   # 0.0115470 rad/mm
EXPECTED_F         = S_PRIME * math.cos(math.radians(ALPHA)) * 60.0  # 5196.15 mm/min
EXPECTED_PHI_AT_L  = L * math.tan(math.radians(ALPHA)) / R   # rad, forward end
EXPECTED_TURN_ANGLE = 2 * L * math.tan(math.radians(ALPHA)) / R  # rad, 1 circuit


def sep(label: str = "", width: int = 60) -> None:
    if label:
        print(f"\n{'=' * 4} {label} {'=' * (width - len(label) - 6)}")
    else:
        print("=" * width)


def check(condition: bool, msg_ok: str, msg_fail: str = "") -> None:
    if condition:
        print(f"    ✓ {msg_ok}")
    else:
        print(f"    ✗ HATA: {msg_fail or msg_ok}")
        raise AssertionError(msg_fail or msg_ok)


def approx_equal(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


# ============================================================
# ADIM 1: Mandrel Geometrisi
# ============================================================

def step1_geometry() -> CylindricalMandrel:
    sep("ADIM 1: MANDREL GEOMETRİSİ")

    mandrel = CylindricalMandrel(radius=R, length=L)
    print(f"    {mandrel}")

    # Sözleşme doğrulaması
    validate_geometry_contract(mandrel, n_test_points=20)
    check(True, "Geometry contract: r≥0, G≥1, √G=√(1+r'²) tüm noktalarda")

    # Silindir özellik kontrolleri
    sp = mandrel.surface_point(z=0.0)
    check(approx_equal(sp.r, R),        f"r(0) = {R} mm")
    check(approx_equal(sp.r_prime, 0.0), "r'(0) = 0 (silindir)")
    check(approx_equal(sp.G, 1.0),       "G(0) = 1.0 (Öklid metrik)")
    check(approx_equal(sp.sqrt_G, 1.0),  "√G(0) = 1.0")

    sp_mid = mandrel.surface_point(z=L / 2)
    check(approx_equal(sp_mid.r, R), f"r(L/2) = {R} mm (sabit yarıçap)")

    check(approx_equal(mandrel.arc_length_element(L / 2), 1.0),
          "ds/dz = 1.0 (silindir için G=1)")

    print(f"\n    {mandrel.summary().strip()}")
    return mandrel


# ============================================================
# ADIM 2: Sarım Parametreleri
# ============================================================

def step2_winding_params() -> WindingParameters:
    sep("ADIM 2: SARIM PARAMETRELERİ")

    winding = WindingParameters.from_degrees(
        alpha_deg   = ALPHA,
        bandwidth   = B,
        fiber_speed = S_PRIME,
        n_layers    = 1,
    )
    print(f"    {winding}")

    check(approx_equal(winding.alpha_deg, ALPHA, tol=1e-10),
          f"α = {winding.alpha_deg:.6f}°")
    check(approx_equal(winding.alpha_rad, math.radians(ALPHA), tol=1e-15),
          f"α [rad] = {winding.alpha_rad:.10f}")

    # Geçersiz parametreler reddediliyor mu?
    try:
        WindingParameters.from_degrees(alpha_deg=0.0)
        raise AssertionError("α=0° kabul edilmemeli")
    except ValueError:
        check(True, "α=0° ValueError fırlatıyor (doğru)")

    try:
        WindingParameters.from_degrees(alpha_deg=90.0)
        raise AssertionError("α=90° kabul edilmemeli")
    except ValueError:
        check(True, "α=90° ValueError fırlatıyor (doğru)")

    return winding


# ============================================================
# ADIM 3: Clairaut Sabitleri
# ============================================================

def step3_clairaut(
    mandrel: CylindricalMandrel,
    winding: WindingParameters,
) -> tuple:
    sep("ADIM 3: CLAIRAUT SABİTLERİ")

    calculator = ClairautCalculator(mandrel, winding)
    constants  = calculator.compute()

    print(f"\n{constants.summary()}")

    # --- Temel doğrulamalar ---
    check(approx_equal(constants.c, EXPECTED_C, tol=1e-12),
          f"c = R·sin(α) = {EXPECTED_C:.8f} mm  ✓  (hesaplanan: {constants.c:.8f})")

    check(approx_equal(constants.pitch, EXPECTED_PITCH, tol=1e-6),
          f"pitch = 2πR/tan(α) = {EXPECTED_PITCH:.6f} mm  ✓  (hesaplanan: {constants.pitch:.6f})")

    check(approx_equal(constants.dphi_dz, EXPECTED_DPHI_DZ, tol=1e-12),
          f"dφ/dz = tan(α)/R = {EXPECTED_DPHI_DZ:.10f} rad/mm  ✓")

    # Tutarlılık: pitch × dphi_dz = 2π
    product = constants.pitch * constants.dphi_dz
    check(approx_equal(product, 2 * math.pi, tol=1e-10),
          f"pitch × dφ/dz = 2π = {2*math.pi:.10f}  ✓  (hesaplanan: {product:.10f})")

    # b_eff = b / cos(α)
    expected_beff = B / math.cos(math.radians(ALPHA))
    check(approx_equal(constants.bandwidth_eff, expected_beff, tol=1e-12),
          f"b_eff = b/cos(α) = {expected_beff:.6f} mm  ✓")

    return constants, calculator


# ============================================================
# ADIM 4: Toolpath Üretimi
# ============================================================

def step4_toolpath(
    mandrel:   CylindricalMandrel,
    winding:   WindingParameters,
    constants,
) -> list:
    sep("ADIM 4: TOOLPATH ÜRETİMİ")

    generator = HelicalPathGenerator(mandrel, winding, constants)

    # Nokta sayısı: maksimum 2° açı adımına göre
    n_pts = generator.n_points_from_angular_step(max_da_deg=2.0)
    print(f"    Nokta sayısı (her geçiş): {n_pts}  (max ΔA = 2°/adım)")

    # Tek devre üret
    toolpath = generator.generate_circuit(
        phi_offset        = 0.0,
        n_points_per_pass = n_pts,
    )

    total_pts    = len(toolpath)
    fwd_pts      = n_pts
    ret_pts      = total_pts - fwd_pts
    turn_angle_d = generator.turn_around_angle_deg()

    print(f"    Toplam nokta:     {total_pts}")
    print(f"    İleri geçiş:      {fwd_pts} nokta (z: 0→{L} mm)")
    print(f"    Geri geçiş:       {ret_pts} nokta (z: {L}→0 mm)")
    print(f"    Turn-around açısı: {turn_angle_d:.4f}°  "
          f"(= {turn_angle_d/360:.4f} devir)")

    # --- Doğrulama: İlk nokta ---
    tp0 = toolpath[0]
    check(approx_equal(tp0.x, 0.0),                "İlk nokta X = 0.0 mm")
    check(approx_equal(tp0.phi_rad, 0.0),           "İlk nokta φ = 0.0 rad")
    check(approx_equal(tp0.alpha_deg, ALPHA, 1e-10),"İlk nokta α = 30.0°")
    check(approx_equal(tp0.r, R, 1e-10),            f"İlk nokta r = {R} mm")
    check(tp0.segment == "forward",                  "İlk nokta segment = 'forward'")

    # --- Doğrulama: İleri geçiş son noktası (turn-around) ---
    tp_fwd_end = toolpath[n_pts - 1]
    check(approx_equal(tp_fwd_end.x, L, tol=1e-10),
          f"İleri geçiş sonu X = L = {L} mm")
    check(approx_equal(tp_fwd_end.phi_rad, EXPECTED_PHI_AT_L, tol=1e-10),
          f"İleri geçiş sonu φ = {math.degrees(EXPECTED_PHI_AT_L):.4f}°")

    # --- Doğrulama: Turn-around açısı ---
    turn_rad = generator.turn_around_angle_rad()
    check(approx_equal(turn_rad, EXPECTED_TURN_ANGLE, tol=1e-10),
          f"Turn-around = 2L·tan(α)/R = {math.degrees(EXPECTED_TURN_ANGLE):.4f}°")

    # --- Doğrulama: Son nokta (geri geçiş sonu) ---
    tp_last = toolpath[-1]
    check(approx_equal(tp_last.x, 0.0, tol=1e-10), "Son nokta X = 0.0 mm (geri döndü)")
    check(tp_last.segment == "return",              "Son nokta segment = 'return'")
    check(approx_equal(tp_last.phi_rad, EXPECTED_TURN_ANGLE, tol=1e-10),
          f"Son nokta φ = {math.degrees(EXPECTED_TURN_ANGLE):.4f}°")

    # --- Doğrulama: φ monotonik artış ---
    for i in range(1, len(toolpath)):
        if toolpath[i].phi_rad < toolpath[i-1].phi_rad - 1e-10:
            raise AssertionError(f"φ azaldı: nokta {i-1}→{i}: "
                                 f"{toolpath[i-1].phi_deg:.4f}° → {toolpath[i].phi_deg:.4f}°")
    check(True, "φ (azimut) tüm noktalarda monotonik artıyor")

    # --- Doğrulama: Clairaut ilişkisi tüm noktalarda ---
    validate_toolpath_clairaut(toolpath, constants, tol=1e-9)
    check(True, f"Clairaut r·sin(α)=c={EXPECTED_C:.6f} mm — tüm {len(toolpath)} noktada doğrulandı")

    # --- Doğrulama: Arc-length tutarlılığı ---
    tp_fwd_end2 = toolpath[n_pts - 1]
    expected_s  = L / math.cos(math.radians(ALPHA))   # arc-length ileri geçiş
    check(approx_equal(tp_fwd_end2.s, expected_s, tol=1e-8),
          f"Arc-length ileri geçiş = L/cos(α) = {expected_s:.4f} mm")

    # --- İlk ve son 3 noktayı göster ---
    print("\n    İlk 3 nokta:")
    for tp in toolpath[:3]:
        print(f"      {tp}")
    print("    ...")
    print("    Son 3 nokta:")
    for tp in toolpath[-3:]:
        print(f"      {tp}")

    return toolpath


# ============================================================
# ADIM 5: Hareket Planlaması
# ============================================================

def step5_motion(
    toolpath: list,
    winding:  WindingParameters,
    constants,
) -> tuple:
    sep("ADIM 5: HAREKET PLANLAMASI")

    machine = MachineConfig.default_2axis()
    print(f"\n{machine.summary()}")

    planner  = MotionPlanner(machine)
    commands = planner.plan(toolpath)

    print(f"    Üretilen komut sayısı: {len(commands)}")

    # --- Doğrulama: Feed rate ---
    check(approx_equal(commands[0].F, EXPECTED_F, tol=0.1),
          f"Feed rate F = S'·cos(α)·60 = {EXPECTED_F:.2f} mm/min  "
          f"(hesaplanan: {commands[0].F:.2f})")

    # --- Doğrulama: İlk komut konumları ---
    check(approx_equal(commands[0].X, 0.0, tol=1e-4), "İlk komut X = 0.0 mm")
    check(approx_equal(commands[0].A, 0.0, tol=1e-4), "İlk komut A = 0.0°")

    # --- Doğrulama: X = phi_deg orantısı ---
    # X artar → A artar, oran = a_axis_deg_per_mm
    if len(commands) > 1:
        dX = commands[1].X - commands[0].X
        dA = commands[1].A - commands[0].A
        if abs(dX) > 1e-6:
            ratio = dA / dX
            check(
                approx_equal(ratio, constants.a_axis_deg_per_mm, tol=1e-3),
                f"dA/dX = {ratio:.6f} °/mm  "
                f"(beklenen: {constants.a_axis_deg_per_mm:.6f} °/mm, "
                f"fark {abs(ratio - constants.a_axis_deg_per_mm):.2e} — G-code yuvarlamasından kaynaklanır)",
            )

    # --- Özet ---
    summary = planner.trajectory_summary(commands)
    print(f"\n    Hareket Özeti:")
    print(f"      X aralığı:   {summary['x_range_mm'][0]:.2f} → {summary['x_range_mm'][1]:.2f} mm")
    print(f"      A aralığı:   {summary['a_range_deg'][0]:.2f} → {summary['a_range_deg'][1]:.2f}°")
    print(f"      Toplam dönüş:{summary['total_a_rev']:.4f} devir")
    print(f"      Feed rate:   {summary['feed_mm_min'][0]:.1f} mm/min (sabit)")

    # --- Spindle hız kontrolü ---
    rpms = planner.compute_spindle_rpm(commands, winding.alpha_rad, R)
    expected_rpm = (S_PRIME * math.sin(winding.alpha_rad) / R) * 60.0 / (2.0 * math.pi)
    check(approx_equal(rpms[0], expected_rpm, tol=0.01),
          f"Spindle hızı = {expected_rpm:.4f} rpm  (hesaplanan: {rpms[0]:.4f})")
    print(f"    Spindle hızı: {rpms[0]:.4f} rpm  "
          f"({'< limit ✓' if rpms[0] < machine.max_a_rpm else '> limit ✗'})")

    return machine, commands


# ============================================================
# ADIM 6: G-code Üretimi
# ============================================================

def step6_gcode(
    commands:  list,
    machine:   MachineConfig,
    winding:   WindingParameters,
    constants,
) -> str:
    sep("ADIM 6: G-CODE ÜRETİMİ")

    gcode_config = GcodeConfig(
        pos_decimals     = 4,
        feed_decimals    = 1,
        firmware         = "GRBL",
        use_absolute     = True,
        units_mm         = True,
        home_sequence    = True,
        include_comments = True,
    )

    gen   = GcodeGenerator(config=gcode_config)
    gcode = gen.generate(
        commands  = commands,
        machine   = machine,
        winding   = winding,
        constants = constants,
        job_name  = f"cylinder_R{int(R)}_L{int(L)}_a{int(ALPHA)}",
    )

    n_lines    = gen.line_count(gcode)
    n_g1       = gen.command_count(gcode)
    est_time_m = gen.estimate_time_min(commands)

    print(f"    G-code satır sayısı:  {n_lines}")
    print(f"    G1 komut sayısı:      {n_g1}")
    print(f"    Tahmini süre:         {est_time_m:.3f} dakika = {est_time_m*60:.1f} saniye")

    # --- Syntax kontrolleri ---
    lines = gcode.split("\n")
    g21_found = any(l.strip().startswith("G21") for l in lines)
    g90_found = any(l.strip().startswith("G90") for l in lines)
    g28_found = any(l.strip().startswith("G28") for l in lines)
    m30_found = any(l.strip().startswith("M30") for l in lines)
    g1_found  = any(l.strip().startswith("G1") for l in lines)

    check(g21_found,  "G21 (mm birim) mevcut")
    check(g90_found,  "G90 (mutlak modu) mevcut")
    check(g28_found,  "G28 (home) mevcut")
    check(m30_found,  "M30 (program sonu) mevcut")
    check(g1_found,   "G1 (lineer hareket) komutları mevcut")

    # --- İlk G1 satırı formatı kontrolü ---
    g1_lines = [l for l in lines if l.strip().startswith("G1")]
    if g1_lines:
        first_g1 = g1_lines[0]
        check("X" in first_g1, f"G1 satırında X var: {first_g1.strip()}")
        check("A" in first_g1, f"G1 satırında A var: {first_g1.strip()}")
        check("F" in first_g1, f"G1 satırında F var: {first_g1.strip()}")

    return gcode


# ============================================================
# G-code Preview
# ============================================================

def print_gcode_preview(gcode: str, n_header: int = 30, n_footer: int = 10) -> None:
    sep("G-CODE PREVIEW")
    lines = gcode.split("\n")

    print(f"\n--- İlk {n_header} Satır ---")
    for i, line in enumerate(lines[:n_header]):
        print(f"  {i+1:4d}  {line}")

    if len(lines) > n_header + n_footer:
        print(f"\n  ... ({len(lines) - n_header - n_footer} satır gizlendi) ...\n")

    print(f"--- Son {n_footer} Satır ---")
    for i, line in enumerate(lines[-n_footer:], start=len(lines)-n_footer):
        print(f"  {i+1:4d}  {line}")


# ============================================================
# Ana Akış
# ============================================================

def main() -> None:
    sep(f"FİLAMENT SARIM CAM — End-to-End Doğrulama")
    print(f"  Test: Silindir R={R}mm, L={L}mm, α={ALPHA}°, S'={S_PRIME}mm/s")
    sep()

    print("\n  Analitik Beklentiler (elle hesaplama):")
    print(f"    c = R·sin(α)     = {EXPECTED_C:.8f} mm")
    print(f"    p = 2πR/tan(α)   = {EXPECTED_PITCH:.6f} mm/rev")
    print(f"    dφ/dz = tan(α)/R = {EXPECTED_DPHI_DZ:.10f} rad/mm")
    print(f"    F = S'·cos(α)·60 = {EXPECTED_F:.4f} mm/min")
    print(f"    Δφ (1 devre)     = {math.degrees(EXPECTED_TURN_ANGLE):.4f}°")

    # Tüm adımları çalıştır
    mandrel   = step1_geometry()
    winding   = step2_winding_params()
    constants, _ = step3_clairaut(mandrel, winding)
    toolpath  = step4_toolpath(mandrel, winding, constants)
    machine, commands = step5_motion(toolpath, winding, constants)
    gcode     = step6_gcode(commands, machine, winding, constants)

    # G-code kaydet
    output_path = "/mnt/user-data/outputs/helical_R50_L300_a30.nc"
    with open(output_path, "w") as f:
        f.write(gcode)
    print(f"\n  G-code kaydedildi: {output_path}")

    # Preview
    print_gcode_preview(gcode, n_header=35, n_footer=8)

    # Özet
    sep("SONUÇ")
    print(f"""
  ✓ Tüm adımlar başarıyla tamamlandı.

  Pipeline:
    geometry.py       → CylindricalMandrel(R={R}mm, L={L}mm)
    winding_math.py   → Clairaut(c={EXPECTED_C:.3f}mm, p={EXPECTED_PITCH:.2f}mm)
    motion_planner.py → {len(commands)} komut, F={EXPECTED_F:.1f}mm/min
    gcode_generator.py → GRBL uyumlu .nc dosyası

  Sonraki adım:
    Faz 2: Hoop winding + layer sistemi + overlap hesabı
    Faz 3: Dome geometry + geodesic path (ClairautIntegrator RK45)
    """)
    sep()


if __name__ == "__main__":
    main()
