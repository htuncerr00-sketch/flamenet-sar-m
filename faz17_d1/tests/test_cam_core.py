"""
tests/test_cam_core.py — CAM çekirdek modülleri birim testleri
=============================================================
Çalıştır: python -m pytest faz17_d1/tests/test_cam_core.py -v
veya:      python faz17_d1/tests/test_cam_core.py
"""
from __future__ import annotations
import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                '..', 'faz17_d1_backend'))

from faz17_d1.core.geometry_engine import MandrelProfile
from faz17_d1.core.path_generator import (
    WindingPathParams, WindingPath, generate_path,
    generate_hoop_path, generate_polar_path,
    clairaut_circuit_count, find_turnaround_z_left, find_turnaround_z_right,
)
from faz17_d1.core.motion_planner import MotionSegment, plan_motion
from faz17_d1.core.gcode_postprocessor import MachineConfig, GCodeProgram, generate_gcode
from faz17_d1.core.winding_planner import WindingParams, GCodeProgram as LegacyGCodeProgram, generate_helical

PASS = 0
FAIL = 0


def _assert(condition, msg):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {msg}")
    else:
        FAIL += 1
        print(f"  FAIL  {msg}")


# ── Geometry Engine ──────────────────────────────────────────────────────────

def test_cylinder_profile():
    p = MandrelProfile.cylinder(300.0, 50.0)
    _assert(abs(p.length_mm - 300.0) < 0.01, "cylinder: uzunluk 300mm")
    _assert(abs(p.max_radius_mm - 50.0) < 0.01, "cylinder: maks yarıçap 50mm")
    _assert(abs(p.avg_radius_mm - 50.0) < 0.01, "cylinder: ort yarıçap 50mm")
    _assert(len(p.z_mm) == len(p.r_mm), "cylinder: z ve r aynı boyut")
    _assert(len(p.z_mm) >= 2, "cylinder: en az 2 nokta")


def test_cone_profile():
    p = MandrelProfile.cone(200.0, 40.0, 60.0)
    _assert(abs(p.length_mm - 200.0) < 0.01, "cone: uzunluk 200mm")
    _assert(p.max_radius_mm >= 59.9, "cone: maks yarıçap ~60mm")
    r_at_start = p.radius_at(0.0)
    r_at_end = p.radius_at(200.0)
    _assert(abs(r_at_start - 40.0) < 1.0, "cone: başlangıç yarıçapı ~40mm")
    _assert(abs(r_at_end - 60.0) < 1.0, "cone: bitiş yarıçapı ~60mm")


def test_dome_cylinder_dome():
    p = MandrelProfile.dome_cylinder_dome(200.0, 50.0, 40.0)
    _assert(p.length_mm > 200.0, "dome_cyl_dome: toplam uzunluk > silindir uzunluğu")
    _assert(p.max_radius_mm >= 49.0, "dome_cyl_dome: maks yarıçap ~50mm")
    _assert(len(p.z_mm) >= 3, "dome_cyl_dome: yeterli nokta sayısı")


def test_radius_at_interpolation():
    p = MandrelProfile.cylinder(100.0, 30.0)
    r = p.radius_at(50.0)
    _assert(abs(r - 30.0) < 0.1, "radius_at: silindir ortası yarıçap doğru")


def test_perimeter():
    p = MandrelProfile.cylinder(100.0, 25.0)
    expected = 2 * math.pi * 25.0
    _assert(abs(p.perimeter_at(50.0) - expected) < 0.1, "perimeter_at: çevre doğru")


def test_arc_length():
    p = MandrelProfile.cylinder(100.0, 30.0)
    arc = p.arc_length(0.0, 100.0)
    _assert(abs(arc - 100.0) < 0.5, "arc_length: silindir üzerinde doğrusal mesafe")


# ── Path Generator ───────────────────────────────────────────────────────────

def test_generate_path_basic():
    profile = MandrelProfile.cylinder(300.0, 50.0)
    params = WindingPathParams(
        profile=profile,
        alpha_deg=55.0,
        n_layers=2,
        tow_width_mm=6.0,
        n_steps_per_pass=30,
    )
    path = generate_path(params)
    _assert(len(path.points) > 0, "generate_path: en az 1 nokta üretildi")
    _assert(path.n_layers == 2, "generate_path: kat sayısı doğru")
    _assert(path.n_circuits > 0, "generate_path: devre sayısı > 0")
    _assert(path.total_fiber_length_mm > 0, "generate_path: fiber uzunluğu > 0")
    _assert(path.coverage_pct > 0, "generate_path: kapsama > 0")


def test_clairaut_monotone_angle():
    """İş mili açısı (a_deg) her zaman monoton artmalı."""
    profile = MandrelProfile.cylinder(300.0, 50.0)
    params = WindingPathParams(
        profile=profile,
        alpha_deg=45.0,
        n_layers=1,
        n_steps_per_pass=50,
    )
    path = generate_path(params)
    angles = [pt.a_deg for pt in path.points]
    decreases = sum(1 for i in range(1, len(angles)) if angles[i] < angles[i - 1])
    _assert(decreases == 0, "Clairaut: iş mili açısı monoton artar")


def test_hoop_path():
    profile = MandrelProfile.cylinder(100.0, 40.0)
    params = WindingPathParams(profile=profile, alpha_deg=88.0, n_layers=1, n_steps_per_pass=30)
    path = generate_hoop_path(params)
    _assert(len(path.points) > 0, "hoop_path: nokta üretildi")


def test_polar_path():
    profile = MandrelProfile.cylinder(100.0, 40.0)
    params = WindingPathParams(profile=profile, alpha_deg=10.0, n_layers=1, n_steps_per_pass=30)
    path = generate_polar_path(params)
    _assert(len(path.points) > 0, "polar_path: nokta üretildi")


def test_coverage_range():
    profile = MandrelProfile.cylinder(200.0, 50.0)
    params = WindingPathParams(
        profile=profile, alpha_deg=55.0, n_layers=4,
        tow_width_mm=6.0, overlap_pct=5.0, n_steps_per_pass=30,
    )
    path = generate_path(params)
    _assert(0 < path.coverage_pct <= 100.0, "coverage: [0, 100] aralığında")


# ── Motion Planner ───────────────────────────────────────────────────────────

def test_plan_motion_basic():
    profile = MandrelProfile.cylinder(100.0, 30.0)
    params = WindingPathParams(profile=profile, alpha_deg=55.0, n_layers=1, n_steps_per_pass=20)
    path = generate_path(params)
    segs = plan_motion(path)
    _assert(len(segs) > 0, "plan_motion: segment üretildi")
    _assert(all(isinstance(s, MotionSegment) for s in segs), "plan_motion: tüm sonuçlar MotionSegment")


def test_feed_limits():
    profile = MandrelProfile.cylinder(100.0, 30.0)
    params = WindingPathParams(profile=profile, alpha_deg=55.0, n_layers=1, n_steps_per_pass=20)
    path = generate_path(params)
    segs = plan_motion(path, max_x_feed_mm_min=5000.0)
    for s in segs:
        _assert(s.feed_mm_min <= 5000.0 + 1e-3, f"feed limit: {s.feed_mm_min:.1f} ≤ 5000")
        break  # sadece ilk segmenti kontrol et


def test_segment_types():
    profile = MandrelProfile.cylinder(100.0, 30.0)
    params = WindingPathParams(profile=profile, alpha_deg=55.0, n_layers=1, n_steps_per_pass=20)
    path = generate_path(params)
    segs = plan_motion(path)
    valid_types = {"LINEAR", "RAPID", "DWELL"}
    _assert(all(s.segment_type in valid_types for s in segs), "segment_type: geçerli değerler")


# ── G-code Post İşlemci ──────────────────────────────────────────────────────

def test_gcode_format_grbl():
    profile = MandrelProfile.cylinder(100.0, 30.0)
    params = WindingPathParams(profile=profile, alpha_deg=55.0, n_layers=1, n_steps_per_pass=20)
    path = generate_path(params)
    segs = plan_motion(path)
    cfg = MachineConfig(controller_type="grbl")
    gp = generate_gcode(segs, path, cfg)
    text = gp.as_text()
    _assert("G21" in text, "gcode grbl: G21 (mm modu) mevcut")
    _assert("G90" in text, "gcode grbl: G90 (mutlak) mevcut")
    _assert("G28" in text, "gcode grbl: G28 (referans) mevcut")
    _assert("M30" in text, "gcode grbl: M30 (program sonu) mevcut")
    _assert(any("G1" in line and "X" in line and "A" in line for line in gp.lines),
            "gcode grbl: G1 X.. A.. satırları mevcut")


def test_gcode_format_fanuc():
    profile = MandrelProfile.cylinder(100.0, 30.0)
    params = WindingPathParams(profile=profile, alpha_deg=55.0, n_layers=1, n_steps_per_pass=20)
    path = generate_path(params)
    segs = plan_motion(path)
    cfg = MachineConfig(controller_type="fanuc")
    gp = generate_gcode(segs, path, cfg)
    text = gp.as_text()
    _assert("G21" in text, "gcode fanuc: G21 mevcut")
    _assert("G01" in text or "G1" in text, "gcode fanuc: linear move mevcut")


def test_gcode_statistics():
    profile = MandrelProfile.cylinder(200.0, 50.0)
    params = WindingPathParams(profile=profile, alpha_deg=55.0, n_layers=2, n_steps_per_pass=30)
    path = generate_path(params)
    segs = plan_motion(path)
    cfg = MachineConfig()
    gp = generate_gcode(segs, path, cfg)
    _assert(gp.n_circuits > 0, "gcode stats: devre sayısı > 0")
    _assert(gp.n_layers == 2, "gcode stats: kat sayısı doğru")
    _assert(gp.total_length_mm > 0, "gcode stats: fiber uzunluğu > 0")
    _assert(gp.estimated_time_s > 0, "gcode stats: tahmini süre > 0")
    _assert(0 < gp.coverage_pct <= 100, "gcode stats: kapsama [0, 100]")


# ── Geriye dönük uyumluluk ───────────────────────────────────────────────────

def test_legacy_generate_helical():
    params = WindingParams(
        mandrel_R_mm=50.0,
        mandrel_L_mm=300.0,
        alpha_deg=55.0,
        n_layers=2,
        tow_width_mm=6.0,
        fiber_tension_N=15.0,
        feed_mm_s=80.0,
    )
    prog = generate_helical(params)
    _assert(isinstance(prog, LegacyGCodeProgram), "legacy: GCodeProgram döndürüldü")
    _assert(len(prog.lines) > 5, "legacy: en az 5 satır G-code")
    _assert("G21" in prog.as_text(), "legacy: G21 mevcut")
    _assert("M30" in prog.as_text(), "legacy: M30 mevcut")
    _assert(prog.n_circuits > 0, "legacy: devre sayısı > 0")


def test_legacy_invalid_params():
    params = WindingParams(alpha_deg=0.0)
    try:
        generate_helical(params)
        _assert(False, "legacy: geçersiz parametre ValueError fırlatmalı")
    except ValueError:
        _assert(True, "legacy: geçersiz parametre ValueError fırlattı")


# ── Elipsoidal kubbe profili ──────────────────────────────────────────────────

def test_ellipsoidal_dome_cylinder_dome():
    p = MandrelProfile.ellipsoidal_dome_cylinder_dome(200.0, 50.0, dome_hr_ratio=0.7)
    _assert(p.max_radius_mm >= 49.9, "ellips_dome: maks yarıçap ~50mm")
    _assert(p.min_radius_mm < 5.0, "ellips_dome: kutup yarıçapı küçük")
    _assert(p.length_mm > 200.0, "ellips_dome: toplam uzunluk > silindir uzunluğu")
    # Kavşak sürekliliği: z=dome_height'ta r≈R
    H = 50.0 * 0.7
    r_at_junction = p.radius_at(H)
    _assert(abs(r_at_junction - 50.0) < 1.0, "ellips_dome: kavşakta r≈50mm")


def test_ellipsoidal_hemisphere():
    p = MandrelProfile.ellipsoidal_dome_cylinder_dome(100.0, 40.0, dome_hr_ratio=1.0)
    # dome_hr_ratio=1 → yarı küre, H=R=40mm
    r_at_equator = p.radius_at(40.0)
    _assert(abs(r_at_equator - 40.0) < 1.0, "ellips_hemisphere: kavşakta r≈R")


def test_min_radius_mm():
    cyl = MandrelProfile.cylinder(100.0, 30.0)
    _assert(abs(cyl.min_radius_mm - 30.0) < 0.1, "min_radius_mm: silindir")
    dome = MandrelProfile.ellipsoidal_dome_cylinder_dome(100.0, 30.0, 0.5)
    _assert(dome.min_radius_mm < 5.0, "min_radius_mm: kubbe ucu küçük")


# ── Kubbe dönüş noktası ───────────────────────────────────────────────────────

def test_turnaround_cylinder():
    """Silindir için tüm profil erişilebilir → dönüm noktaları profil uçlarında."""
    p = MandrelProfile.cylinder(300.0, 50.0)
    c = 50.0 * math.sin(math.radians(55.0))  # < 50 → tüm profil erişilebilir
    z_left  = find_turnaround_z_left(p, c)
    z_right = find_turnaround_z_right(p, c)
    _assert(abs(z_left - 0.0) < 0.1, "turnaround_cyl: sol dönüm z=0'da")
    _assert(abs(z_right - 300.0) < 0.1, "turnaround_cyl: sağ dönüm z=L'de")


def test_turnaround_dome():
    """Kubbe profili: dönüm noktaları silindir iç kısmında olmalı."""
    p = MandrelProfile.ellipsoidal_dome_cylinder_dome(200.0, 50.0, 0.7)
    alpha_rad = math.radians(55.0)
    c = 50.0 * math.sin(alpha_rad)  # ≈ 40.96mm
    z_left  = find_turnaround_z_left(p, c)
    z_right = find_turnaround_z_right(p, c)
    H = 50.0 * 0.7   # dome height = 35mm
    total_L = p.length_mm
    _assert(z_left > 0.0, "turnaround_dome: sol dönüm profil başından sonra")
    _assert(z_left < H + 5.0, "turnaround_dome: sol dönüm kubbe bölgesinde")
    _assert(z_right < total_L, "turnaround_dome: sağ dönüm profil sonundan önce")
    _assert(z_right > total_L - H - 5.0, "turnaround_dome: sağ dönüm sağ kubbe bölgesinde")


# ── Devre sayısı formülü ──────────────────────────────────────────────────────

def test_clairaut_circuit_count_cylinder():
    """N = ceil(2π·R·sin(α)/w) — araştırma doğrulamalı formül."""
    R, alpha_deg, w = 50.0, 55.0, 5.0
    alpha_rad = math.radians(alpha_deg)
    n = clairaut_circuit_count(R, alpha_rad, w, overlap_pct=0.0)
    n_ideal = 2 * math.pi * R * math.sin(alpha_rad) / w
    _assert(n >= math.ceil(n_ideal) - 1, "circuit_count: alt sınır tutarlı")
    _assert(n <= math.ceil(n_ideal) + 1, "circuit_count: üst sınır tutarlı")
    _assert(n < 100, "circuit_count: aşırı büyük değil (sin faktörü çalışıyor)")


def test_clairaut_circuit_count_less_than_circumferential():
    """sin(α) faktörüyle devre sayısı eskiden büyük değerden az olmalı."""
    R, alpha_deg, w = 50.0, 55.0, 5.0
    alpha_rad = math.radians(alpha_deg)
    n_new = clairaut_circuit_count(R, alpha_rad, w, overlap_pct=0.0)
    n_old = math.ceil(2.0 * math.pi * R / w)   # eski formül (sin yoktu)
    _assert(n_new <= n_old, f"circuit_count: yeni({n_new}) ≤ eski({n_old})")


# ── Kubbe sarma yolu ──────────────────────────────────────────────────────────

def test_path_dome_turnaround():
    """Kubbe profil sarma: tüm noktalar r ≥ c koşulunu sağlamalı."""
    profile = MandrelProfile.ellipsoidal_dome_cylinder_dome(100.0, 40.0, 0.7)
    params = WindingPathParams(
        profile=profile, alpha_deg=55.0, n_layers=1,
        tow_width_mm=5.0, n_steps_per_pass=30,
    )
    path = generate_path(params)
    alpha_rad = math.radians(params.alpha_deg)
    c = profile.max_radius_mm * math.sin(alpha_rad)
    violations = sum(
        1 for pt in path.points
        if profile.radius_at(pt.x_mm) < c - 2.0   # 2mm tolerans
    )
    _assert(violations == 0,
            f"dome_turnaround: r < c ihlali yok (c={c:.1f}mm) — ihlal={violations}")


# ── Fanuc G-code formatı ──────────────────────────────────────────────────────

def test_fanuc_percent_markers():
    """Fanuc G-code'u % başlık ve sonlandırıcıyla açılmalı/kapanmalı."""
    profile = MandrelProfile.cylinder(100.0, 30.0)
    params  = WindingPathParams(profile=profile, alpha_deg=55.0, n_layers=1, n_steps_per_pass=20)
    path    = generate_path(params)
    segs    = plan_motion(path)
    cfg     = MachineConfig(controller_type="fanuc", program_number=42)
    gp      = generate_gcode(segs, path, cfg)
    lines   = gp.lines
    _assert(lines[0] == "%", "fanuc: ilk satır '%'")
    _assert(lines[-1] == "%", "fanuc: son satır '%'")
    _assert(any("O0042" in l for l in lines), "fanuc: O-numarası O0042 mevcut")


def test_fanuc_line_numbers():
    """Fanuc G-code satırları N-prefix içermeli."""
    profile = MandrelProfile.cylinder(100.0, 30.0)
    params  = WindingPathParams(profile=profile, alpha_deg=55.0, n_layers=1, n_steps_per_pass=10)
    path    = generate_path(params)
    segs    = plan_motion(path)
    cfg     = MachineConfig(controller_type="fanuc")
    gp      = generate_gcode(segs, path, cfg)
    n_prefixed = sum(1 for l in gp.lines if l.startswith("N") and len(l) > 4)
    _assert(n_prefixed > 3, f"fanuc: N-prefixli satır sayısı > 3 ({n_prefixed})")


def test_linuxcnc_m2_end():
    """LinuxCNC G-code'u M2 ile bitmeli (M30 değil)."""
    profile = MandrelProfile.cylinder(100.0, 30.0)
    params  = WindingPathParams(profile=profile, alpha_deg=55.0, n_layers=1, n_steps_per_pass=10)
    path    = generate_path(params)
    segs    = plan_motion(path)
    cfg     = MachineConfig(controller_type="linuxcnc")
    gp      = generate_gcode(segs, path, cfg)
    text    = gp.as_text()
    _assert("M2" in text, "linuxcnc: M2 mevcut")
    _assert("M30" not in text, "linuxcnc: M30 yok")


# ── Özet ─────────────────────────────────────────────────────────────────────

def main():
    print("\n=== CAM Çekirdek Birim Testleri ===\n")
    test_cylinder_profile()
    test_cone_profile()
    test_dome_cylinder_dome()
    test_radius_at_interpolation()
    test_perimeter()
    test_arc_length()
    test_generate_path_basic()
    test_clairaut_monotone_angle()
    test_hoop_path()
    test_polar_path()
    test_coverage_range()
    test_plan_motion_basic()
    test_feed_limits()
    test_segment_types()
    test_gcode_format_grbl()
    test_gcode_format_fanuc()
    test_gcode_statistics()
    test_legacy_generate_helical()
    test_legacy_invalid_params()
    # Yeni testler (Faz 11+)
    test_ellipsoidal_dome_cylinder_dome()
    test_ellipsoidal_hemisphere()
    test_min_radius_mm()
    test_turnaround_cylinder()
    test_turnaround_dome()
    test_clairaut_circuit_count_cylinder()
    test_clairaut_circuit_count_less_than_circumferential()
    test_path_dome_turnaround()
    test_fanuc_percent_markers()
    test_fanuc_line_numbers()
    test_linuxcnc_m2_end()

    total = PASS + FAIL
    print(f"\n{'─' * 40}")
    print(f"TOPLAM  {total}  |  BAŞARILI {PASS}  |  BAŞARISIZ {FAIL}")
    if FAIL == 0:
        print("★★★ TÜM TESTLER BAŞARILI ★★★")
    else:
        print(f"DURDUR — {FAIL} test başarısız")
    return FAIL


if __name__ == "__main__":
    sys.exit(main())
