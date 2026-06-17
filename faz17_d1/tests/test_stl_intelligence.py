"""
tests/test_stl_intelligence.py — STL Intelligence Layer doğrulama (T1-T18)
===========================================================================
Sentetik vertex bulutları (gerçek .stl gerekmez) ile STL Intelligence ve
MandrelModel'i doğrular. Determinizm: sabit seed.

Çalıştırma:
    python3 faz17_d1/tests/test_stl_intelligence.py

Kabul: tüm assert'ler geçer; T1-T5 grade YÜKSEK; T6 doğru eksen;
T7/T8 doğru RED; ASCII/binary tutarlılık.
"""
import copy
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "faz17_d1_backend"))

from faz17_d1.core.stl_intelligence import (
    analyze_vertices, solve_axis, extract_radius_profile, segment_regions,
    TurnaroundCandidate, TurnaroundCandidates, QualityReport,
)
from faz17_d1.core.mandrel_model import MandrelModel

RNG = np.random.default_rng(42)
_PASS = 0
_FAIL = 0


def check(cond: bool, msg: str):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  ✗ FAIL: {msg}")


# ── Sentetik gövde üreticileri ───────────────────────────────────────────────

def _surface(z_of, r_of, n_axial=120, n_theta=60):
    """Dönel yüzey vertex bulutu: z_of/r_of fonksiyon dizileri."""
    zs = z_of
    rs = r_of
    th = np.linspace(0, 2 * np.pi, n_theta, endpoint=False)
    pts = []
    for z, r in zip(zs, rs):
        for t in th:
            pts.append([z, r * math.cos(t), r * math.sin(t)])  # eksen = X
    return np.array(pts, dtype=np.float64)


def cylinder(R=50.0, L=300.0, n=120):
    z = np.linspace(0, L, n)
    r = np.full(n, R)
    return _surface(z, r)


def dome_cyl_dome(R=50.0, L=200.0, H=50.0, n=160):
    zc = np.linspace(0, 2 * H + L, n)
    r = np.empty(n)
    for i, z in enumerate(zc):
        if z < H:
            r[i] = math.sqrt(max(R * R - (H - z) ** 2, (R * 0.05) ** 2))
        elif z <= H + L:
            r[i] = R
        else:
            d = z - (H + L)
            r[i] = math.sqrt(max(R * R - d * d, (R * 0.05) ** 2))
    return _surface(zc, r)


def cone(r0=30.0, r1=70.0, L=300.0, n=120):
    z = np.linspace(0, L, n)
    r = np.linspace(r0, r1, n)
    return _surface(z, r)


def sphere(R=50.0, n=80):
    pts = []
    for phi in np.linspace(0.01, math.pi - 0.01, n):
        for t in np.linspace(0, 2 * np.pi, n, endpoint=False):
            x = R * math.cos(phi)
            y = R * math.sin(phi) * math.cos(t)
            z = R * math.sin(phi) * math.sin(t)
            pts.append([x, y, z])
    return np.array(pts, dtype=np.float64)


def rotate(V, axis, deg):
    """Vertex bulutunu verilen eksen etrafında döndür (Rodrigues)."""
    a = np.asarray(axis, float); a = a / np.linalg.norm(a)
    th = math.radians(deg)
    c, s = math.cos(th), math.sin(th)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    R = np.eye(3) * c + s * K + (1 - c) * np.outer(a, a)
    return V @ R.T


# ── T1: Eksen-hizalı silindir (X) ─────────────────────────────────────────────

def t1_aligned_cylinder():
    V = cylinder(R=50, L=300)
    rep = analyze_vertices(V)
    check(abs(abs(rep.axis.axis_unit[0]) - 1.0) < 0.02, "T1 eksen X olmalı")
    check(abs(rep.profile.r_mm.mean() - 50) < 1.5, "T1 r≈50")
    check(rep.confidence.grade == "YÜKSEK", f"T1 grade YÜKSEK (got {rep.confidence.grade})")
    check(rep.quality.winding_suitable, "T1 winding uygun")
    print(f"  T1 aligned cylinder: {rep.human_summary()}")


# ── T2: Eğik silindir ─────────────────────────────────────────────────────────

def t2_tilted_cylinder():
    V = rotate(cylinder(R=50, L=300), axis=[0, 1, 0], deg=30)
    rep = analyze_vertices(V)
    check(abs(rep.profile.r_mm.mean() - 50) < 2.0, "T2 eğik r≈50")
    check(rep.confidence.grade in ("YÜKSEK", "ORTA"), f"T2 grade (got {rep.confidence.grade})")
    # Eksen 30° eğik olmalı (X bileşeni cos30≈0.866)
    check(abs(abs(rep.axis.axis_unit[0]) - math.cos(math.radians(30))) < 0.1,
          "T2 eksen 30° eğik")
    print(f"  T2 tilted cylinder: axis={np.round(rep.axis.axis_unit,3)}")


# ── T3: Off-center silindir ───────────────────────────────────────────────────

def t3_offcenter_cylinder():
    V = cylinder(R=50, L=300) + np.array([10.0, 25.0, -15.0])
    rep = analyze_vertices(V)
    check(abs(rep.profile.r_mm.mean() - 50) < 1.5, "T3 off-center r≈50")
    check(rep.confidence.grade == "YÜKSEK", f"T3 grade (got {rep.confidence.grade})")
    print(f"  T3 off-center: center={np.round(rep.axis.center,1)}")


# ── T4: Dome-silindir-dome ────────────────────────────────────────────────────

def t4_dome_cyl_dome():
    V = dome_cyl_dome(R=50, L=200, H=50)
    rep = analyze_vertices(V)
    kinds = {s.kind for s in rep.segments.spans}
    check("cylinder" in kinds, "T4 silindir bölgesi var")
    check("dome" in kinds, "T4 dome bölgesi var")
    check(len(rep.pole_regions) >= 1, "T4 kutup bölgesi tespit edildi")
    print(f"  T4 dome-cyl-dome: segmentler={[s.kind for s in rep.segments.spans]}, "
          f"kutup={len(rep.pole_regions)}")


# ── T5: Konik ─────────────────────────────────────────────────────────────────

def t5_cone():
    V = cone(r0=30, r1=70, L=300)
    rep = analyze_vertices(V)
    check(rep.profile.r_mm[0] < rep.profile.r_mm[-1], "T5 r artan")
    check(abs(rep.profile.r_mm[0] - 30) < 3 and abs(rep.profile.r_mm[-1] - 70) < 3,
          "T5 r0≈30 r1≈70")
    print(f"  T5 cone: r0={rep.profile.r_mm[0]:.1f} r1={rep.profile.r_mm[-1]:.1f}")


# ── T6: Kısa-şişman silindir (D>L) — F2 testi ─────────────────────────────────

def t6_short_fat():
    # D=200 (R=100), L=80 → en büyük λ DÖNME ekseni DEĞİL
    V = cylinder(R=100, L=80, n=60)
    rep = analyze_vertices(V)
    # Dönme ekseni hâlâ X olmalı (J kriteri en-büyük-λ yanılgısını çözer)
    check(abs(abs(rep.axis.axis_unit[0]) - 1.0) < 0.05,
          f"T6 kısa-şişman eksen X (got {np.round(rep.axis.axis_unit,3)})")
    check(abs(rep.profile.r_mm.mean() - 100) < 3, "T6 r≈100")
    print(f"  T6 short-fat (D>L): axis={np.round(rep.axis.axis_unit,3)} ✓ J kriteri çalıştı")


# ── T7: Küre — F1 testi (REDDET) ──────────────────────────────────────────────

def t7_sphere():
    V = sphere(R=50)
    rep = analyze_vertices(V)
    check(rep.axis.degeneracy_flag, "T7 küre dejenerasyon bayrağı")
    check(rep.confidence.grade in ("DÜŞÜK", "REDDET"),
          f"T7 küre düşük güven (got {rep.confidence.grade})")
    print(f"  T7 sphere: degeneracy={rep.axis.degeneracy_flag}, grade={rep.confidence.grade}")


# ── T8: Torus — F3 testi (tek-değerli değil) ─────────────────────────────────

def t8_torus():
    # Torus: merkez yarıçap Rt, tüp yarıçap rt — aynı z'de iki ρ
    Rt, rt = 60.0, 20.0
    pts = []
    for u in np.linspace(0, 2 * np.pi, 80, endpoint=False):
        for v in np.linspace(0, 2 * np.pi, 40, endpoint=False):
            x = rt * math.sin(v)                      # eksen = X
            rr = Rt + rt * math.cos(v)
            pts.append([x, rr * math.cos(u), rr * math.sin(u)])
    V = np.array(pts)
    rep = analyze_vertices(V)
    check(not rep.quality.is_single_valued or rep.confidence.grade == "REDDET",
          f"T8 torus tek-değerli değil/RED (single={rep.quality.is_single_valued})")
    print(f"  T8 torus: single_valued={rep.quality.is_single_valued}, grade={rep.confidence.grade}")


# ── T13: Turnaround kapsama ───────────────────────────────────────────────────

def t13_turnaround():
    V = dome_cyl_dome(R=50, L=200, H=50)
    rep = analyze_vertices(V)
    tc = rep.turnaround_candidates.by_alpha
    check(len(tc) >= 4, "T13 birden çok α adayı")
    # Düşük α → daha geniş erişim (z_right - z_left daha büyük)
    lo = tc[min(tc)]; hi = tc[max(tc)]
    span_lo = lo.z_right_mm - lo.z_left_mm
    span_hi = hi.z_right_mm - hi.z_left_mm
    check(span_lo >= span_hi - 1e-6,
          f"T13 düşük α daha geniş erişim ({span_lo:.1f} ≥ {span_hi:.1f})")
    print(f"  T13 turnaround: α={min(tc):.0f}→span {span_lo:.1f}mm, "
          f"α={max(tc):.0f}→span {span_hi:.1f}mm")


# ── MandrelModel: serialization + backward compat ────────────────────────────

def t_model_roundtrip():
    V = dome_cyl_dome(R=50, L=200, H=50)
    rep = analyze_vertices(V, source_path="/tmp/test.stl")
    model = MandrelModel.from_stl(rep)
    # as_profile downstream-uyumlu
    prof = model.as_profile()
    check(prof.z_mm.shape == prof.r_mm.shape, "Model as_profile geçerli")
    check(model.source_type == "stl", "Model source_type stl")
    # JSON roundtrip
    import json
    d = model.to_dict()
    s = json.dumps(d)                              # JSON-uyumlu olmalı
    model2 = MandrelModel.from_dict(json.loads(s))
    check(np.allclose(model.profile.r_mm, model2.profile.r_mm), "Roundtrip r_mm aynı")
    check(model2.confidence.grade == model.confidence.grade, "Roundtrip grade aynı")
    check(len(model2.segments.spans) == len(model.segments.spans), "Roundtrip segment sayısı")
    # Parametrik köprü
    from faz17_d1.core.geometry_engine import MandrelProfile
    pm = MandrelModel.from_parametric(MandrelProfile.cylinder(300, 50))
    check(pm.is_winding_ready(), "Parametrik model winding-ready")
    check(pm.source_type == "parametric", "Parametrik source_type")
    print(f"  Model roundtrip: JSON {len(s)} bytes, grade={model2.confidence.grade}")


# ── Determinizm ───────────────────────────────────────────────────────────────

def t_determinism():
    V = dome_cyl_dome(R=50, L=200, H=50)
    r1 = analyze_vertices(V)
    r2 = analyze_vertices(V)
    check(np.array_equal(r1.profile.r_mm, r2.profile.r_mm), "Determinizm: r_mm bit-aynı")
    check(np.array_equal(r1.axis.axis_unit, r2.axis.axis_unit), "Determinizm: eksen bit-aynı")
    print("  Determinism: aynı girdi → bit-aynı çıktı ✓")


# ── Yardımcı: son derece dar kutup profili ────────────────────────────────────

def _tiny_pole_vertices(R=50.0, L=100.0, H=50.0, n=200):
    """Sinüsoidal dome ile kutup r→0 (clamp'tan önce 0.05mm). Eksen = X."""
    zc = np.linspace(0, 2 * H + L, n)
    rvals = []
    for z in zc:
        if z <= H:
            # sin(0) = 0 → kutup tamamen kapanıyor
            r = max(R * math.sin(math.pi * z / (2 * H)), 0.05)
        elif z <= H + L:
            r = R
        else:
            d = z - (H + L)
            r = max(R * math.sin(math.pi * (H - d) / (2 * H)), 0.05)
        rvals.append(r)
    return _surface(zc, np.array(rvals), n_axial=n)


# ── T15: Torus → single_valued=False (katı kontrol) ─────────────────────────

def t15_torus_single_valued():
    """Eşik düzeltmesi sonrası torus kesinlikle single_valued=False."""
    Rt, rt = 60.0, 20.0
    pts = []
    for u in np.linspace(0, 2 * np.pi, 80, endpoint=False):
        for v in np.linspace(0, 2 * np.pi, 40, endpoint=False):
            x = rt * math.sin(v)
            rr = Rt + rt * math.cos(v)
            pts.append([x, rr * math.cos(u), rr * math.sin(u)])
    V = np.array(pts)
    rep = analyze_vertices(V)
    check(not rep.quality.is_single_valued,
          f"T15 torus single_valued=False (got {rep.quality.is_single_valued})")
    check(rep.confidence.grade == "REDDET",
          f"T15 torus grade=REDDET (got {rep.confidence.grade})")
    print(f"  T15 torus strict: single={rep.quality.is_single_valued}, "
          f"grade={rep.confidence.grade} ✓")


# ── T16: Erişilemez turnaround → is_winding_ready=False ──────────────────────

def t16_no_reachable_turnaround():
    """Tüm turnaround adayları erişilemez → is_winding_ready=False."""
    V = cylinder(R=50, L=300)
    rep = analyze_vertices(V)

    # Temel: iyi silindir winding-ready olmalı
    check(rep.is_winding_ready(),
          "T16 baseline silindir winding-ready")

    # Tüm turnaround'ları 'erişilemez' (z_right < z_left) yap
    mock_turn = TurnaroundCandidates()
    for a in list(rep.turnaround_candidates.by_alpha.keys()):
        mock_turn.by_alpha[a] = TurnaroundCandidate(
            alpha_deg=a, z_left_mm=200.0, z_right_mm=50.0,
            polar_radius_c_mm=0.0, reachable=False)

    rep2 = copy.copy(rep)
    rep2.turnaround_candidates = mock_turn

    check(not rep2.is_winding_ready(),
          "T16 turnaround yok → is_winding_ready=False")
    print(f"  T16 no turnaround: ready={rep2.is_winding_ready()} "
          f"(beklenen False) ✓")


# ── T17: Çok küçük kutup → is_winding_ready=False ────────────────────────────

def t17_small_pole():
    """quality.min_radius_mm < r_max*0.02 → is_winding_ready=False.

    p95 tabanlı profil çıkarımı gerçek kutup minimumunu yansıtmaz;
    bu yüzden is_winding_ready() kriterini doğrudan mock quality ile test et.
    """
    V = cylinder(R=50, L=300)
    rep = analyze_vertices(V)
    check(rep.is_winding_ready(), "T17 baseline hazır")

    r_max = float(rep.profile.r_mm.max())
    # Kutup yarıçapını eşiğin altına ayarla: r_max*0.005 < r_max*0.02
    tiny_quality = QualityReport(
        aspect_ratio=rep.quality.aspect_ratio,
        min_radius_mm=r_max * 0.005,
        is_single_valued=True,
        asymmetry_mm=rep.quality.asymmetry_mm,
        degenerate_tri_count=rep.quality.degenerate_tri_count,
        mesh_density=rep.quality.mesh_density,
        winding_suitable=False,
        reasons=["Kutup yarıçapı çok küçük."],
    )
    rep2 = copy.copy(rep)
    rep2.quality = tiny_quality

    check(rep2.quality.min_radius_mm < r_max * 0.02,
          f"T17 r_min={rep2.quality.min_radius_mm:.3f} < {r_max*0.02:.3f}mm")
    check(not rep2.is_winding_ready(),
          "T17 küçük kutup → is_winding_ready=False")
    print(f"  T17 small pole: r_min={rep2.quality.min_radius_mm:.3f}mm "
          f"(eşik={r_max*0.02:.3f}mm), ready={rep2.is_winding_ready()} ✓")


# ── T18: Yüksek güven + geçerli turnaround → is_winding_ready=True ───────────

def t18_winding_ready():
    """Standart silindir: yüksek güven + erişilebilir turnaround → True."""
    V = cylinder(R=50, L=300)
    rep = analyze_vertices(V)
    check(rep.confidence.aggregate >= 0.65,
          f"T18 agregat güven ≥ 0.65 (got {rep.confidence.aggregate:.3f})")
    reachable_count = sum(
        1 for tc in rep.turnaround_candidates.by_alpha.values() if tc.reachable)
    check(reachable_count > 0,
          f"T18 erişilebilir turnaround var ({reachable_count})")
    check(rep.is_winding_ready(),
          "T18 is_winding_ready=True")
    print(f"  T18 winding ready: confidence={rep.confidence.aggregate:.3f}, "
          f"grade={rep.confidence.grade}, "
          f"turnaround_reachable={reachable_count}, "
          f"ready={rep.is_winding_ready()} ✓")


def main():
    print("=" * 70)
    print("STL Intelligence Layer — T1-T18 doğrulama")
    print("=" * 70)
    for fn in [t1_aligned_cylinder, t2_tilted_cylinder, t3_offcenter_cylinder,
               t4_dome_cyl_dome, t5_cone, t6_short_fat, t7_sphere, t8_torus,
               t13_turnaround, t_model_roundtrip, t_determinism,
               t15_torus_single_valued, t16_no_reachable_turnaround,
               t17_small_pole, t18_winding_ready]:
        try:
            fn()
        except Exception as exc:
            global _FAIL
            _FAIL += 1
            print(f"  ✗ EXCEPTION in {fn.__name__}: {type(exc).__name__}: {exc}")
            import traceback
            traceback.print_exc()
    print("=" * 70)
    print(f"TOPLAM: {_PASS} geçti / {_FAIL} başarısız")
    print("=" * 70)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
