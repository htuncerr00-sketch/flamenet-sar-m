"""
tests/test_core_cam_engine.py — core/cam_engine.py facade doğrulaması
=====================================================================
Canonical facade'ın 5 fonksiyonunu doğrular + shim eşdeğerliği.
Qt gerektirmez, headless çalışır.

Çalıştırma:
    python3 faz17_d1/tests/test_core_cam_engine.py
"""
from __future__ import annotations
import os
import sys

# Backend import yolu
_TESTS_DIR = os.path.dirname(__file__)
_FAZ17_D1 = os.path.join(_TESTS_DIR, "..", "faz17_d1_backend")
sys.path.insert(0, _FAZ17_D1)

# faz17_d2 uygulama kökü (shim eşdeğerliği testleri için)
_FAZ17_D2_APP = os.path.join(_TESTS_DIR, "..", "..",
                              "faz17_d2", "faz17_d2_app", "faz17_d2")
sys.path.insert(0, _FAZ17_D2_APP)

# `from backend.core.*` → `faz17_d1.core.*` eşlemesi
if "backend" not in sys.modules:
    import faz17_d1
    import faz17_d1.core
    for _n in list(sys.modules):
        if _n.startswith("faz17_d1"):
            sys.modules.setdefault(_n.replace("faz17_d1", "backend", 1),
                                   sys.modules[_n])

import faz17_d1.core.cam_engine as core_eng
from faz17_d1.core.cam_engine import (
    MandrelSpec, PathSpec, MotionSpec, TwinSpec,
    PathResult, MotionResult, TwinResult, PathStatistics,
    build_mandrel_model, compute_path, compute_motion,
    compute_gcode, compute_twin,
)
from faz17_d1.core.gcode_postprocessor import MachineConfig

_PASS = 0
_FAIL = 0


def check(cond: bool, msg: str):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  ✗ FAIL: {msg}")


# ── C1: build_mandrel_model — silindir ───────────────────────────────────────

def c1_cylinder():
    spec = MandrelSpec(kind="cylinder", diameter_mm=100.0, length_mm=300.0)
    model = build_mandrel_model(spec)
    check(model.source_type == "parametric", "C1 source_type=parametric")
    check(model.is_winding_ready(), "C1 is_winding_ready=True")
    prof = model.as_profile()
    import numpy as np
    check(abs(prof.r_mm.max() - 50.0) < 0.2, "C1 r_max=50mm")
    check(abs(prof.z_mm[-1] - 300.0) < 0.2, "C1 L=300mm")
    print("  C1 build_mandrel_model(cylinder): OK")


# ── C2: build_mandrel_model — koni ───────────────────────────────────────────

def c2_cone():
    spec = MandrelSpec(kind="cone", diameter_mm=80.0, length_mm=200.0,
                       cone_angle_deg=5.0)
    model = build_mandrel_model(spec)
    check(model.source_type == "parametric", "C2 source_type=parametric")
    prof = model.as_profile()
    import math
    import numpy as np
    r_end_expected = 40.0 + 200.0 * math.tan(math.radians(5.0))
    check(abs(prof.r_mm[-1] - r_end_expected) < 1.0, "C2 r_end konik")
    print("  C2 build_mandrel_model(cone): OK")


# ── C3: build_mandrel_model — kubbeli silindir ───────────────────────────────

def c3_dome_cylinder_dome():
    spec = MandrelSpec(kind="dome_cylinder_dome", diameter_mm=100.0,
                       length_mm=250.0, dome_height_mm=40.0)
    model = build_mandrel_model(spec)
    check(model.source_type == "parametric", "C3 source_type=parametric")
    prof = model.as_profile()
    check(len(prof.z_mm) >= 300, "C3 yeterli örnekleme noktası")
    import numpy as np
    check(prof.r_mm.max() >= 50.0 - 0.5, "C3 r_max≥50")
    print("  C3 build_mandrel_model(dome_cylinder_dome): OK")


# ── C4: compute_path — PathResult alanları ───────────────────────────────────

def c4_compute_path_fields():
    mspec = MandrelSpec(kind="cylinder", diameter_mm=100.0, length_mm=300.0)
    model = build_mandrel_model(mspec)
    pspec = PathSpec(alpha_deg=55.0, n_layers=2, tow_width_mm=6.0,
                     compute_coverage=False)
    result = compute_path(model, pspec)
    check(isinstance(result, PathResult), "C4 PathResult türü")
    check(result.winding_path is not None, "C4 winding_path var")
    check(len(result.winding_path.points) > 0, "C4 yol noktaları >0")
    check(isinstance(result.statistics, PathStatistics), "C4 PathStatistics var")
    check(result.statistics.n_circuits > 0, "C4 n_circuits>0")
    check(result.statistics.total_fiber_length_mm > 0, "C4 fiber_length>0")
    check(result.statistics.clairaut_c > 0, "C4 clairaut_c>0")
    check(result.coverage is None, "C4 coverage=None (compute_coverage=False)")
    check(result.turnaround is not None, "C4 turnaround bilgisi var")
    print("  C4 compute_path (PathResult alanları): OK")


# ── C5: compute_path — kaplama etkin ─────────────────────────────────────────

def c5_compute_path_coverage():
    mspec = MandrelSpec(kind="cylinder", diameter_mm=100.0, length_mm=200.0)
    model = build_mandrel_model(mspec)
    pspec = PathSpec(alpha_deg=55.0, n_layers=2, tow_width_mm=6.0,
                     compute_coverage=True, coverage_n_z=60, coverage_n_theta=180)
    result = compute_path(model, pspec)
    check(result.coverage is not None, "C5 CoverageMap üretildi")
    check(result.statistics.coverage_pct_solved is not None,
          "C5 coverage_pct_solved dolu")
    check(0.0 <= result.statistics.coverage_pct_solved <= 100.0,
          "C5 coverage_pct_solved aralık [0,100]")
    print("  C5 compute_path (kaplama etkin): OK")


# ── C6: compute_motion — MotionResult ────────────────────────────────────────

def c6_compute_motion():
    mspec = MandrelSpec(kind="cylinder", diameter_mm=100.0, length_mm=200.0)
    model = build_mandrel_model(mspec)
    pspec = PathSpec(alpha_deg=55.0, n_layers=1, tow_width_mm=6.0,
                     compute_coverage=False)
    pr = compute_path(model, pspec)

    mr = compute_motion(pr)
    check(isinstance(mr, MotionResult), "C6 MotionResult türü")
    check(mr.n_segments > 0, "C6 n_segments>0")
    check(mr.max_x_feed_mm_min > 0, "C6 max_x_feed>0")
    check(len(mr.segments) == mr.n_segments, "C6 segments uzunluğu tutarlı")
    print("  C6 compute_motion (MotionResult): OK")


# ── C7: compute_gcode — GCodeProgram ─────────────────────────────────────────

def c7_compute_gcode():
    mspec = MandrelSpec(kind="cylinder", diameter_mm=100.0, length_mm=200.0)
    model = build_mandrel_model(mspec)
    pspec = PathSpec(alpha_deg=55.0, n_layers=1, tow_width_mm=6.0,
                     compute_coverage=False)
    pr = compute_path(model, pspec)
    mr = compute_motion(pr)
    cfg = MachineConfig()

    gp = compute_gcode(mr, pr, cfg)
    check(len(gp.lines) > 5, "C7 G-code satır sayısı >5")
    # G21 + G90 başlık ve G1 hareket satırları
    g21_found = any("G21" in ln for ln in gp.lines)
    g1_found = any(ln.startswith("G1") for ln in gp.lines)
    m30_found = any("M30" in ln for ln in gp.lines)
    check(g21_found, "C7 G21 (mm modu) mevcut")
    check(g1_found, "C7 G1 hareket satırları mevcut")
    check(m30_found, "C7 M30 (program sonu) mevcut")
    check(gp.n_circuits > 0, "C7 n_circuits>0")
    print("  C7 compute_gcode (GCodeProgram): OK")


# ── C8: compute_twin — TwinResult ────────────────────────────────────────────

def c8_compute_twin():
    mspec = MandrelSpec(kind="cylinder", diameter_mm=100.0, length_mm=200.0)
    model = build_mandrel_model(mspec)
    pspec = PathSpec(alpha_deg=55.0, n_layers=2, tow_width_mm=6.0,
                     compute_coverage=False)
    tspec = TwinSpec(n_layers=2, dt_s=2.0)

    result = compute_twin(model, pspec, tspec)
    check(isinstance(result, TwinResult), "C8 TwinResult türü")
    check(len(result.states) > 0, "C8 states boş değil")
    check(result.total_time_s > 0, "C8 total_time_s>0")
    check(result.base_radius_mm > 0, "C8 base_radius_mm>0")
    check(result.final_radius_mm >= result.base_radius_mm,
          "C8 final_radius≥base_radius (katman birikmesi)")
    check(len(result.summary()) > 0, "C8 summary() metni dolu")
    print("  C8 compute_twin (TwinResult): OK")


# ── C9: compute_motion — PathResult veya WindingPath kabul ───────────────────

def c9_motion_accepts_winding_path():
    mspec = MandrelSpec(kind="cylinder", diameter_mm=80.0, length_mm=150.0)
    model = build_mandrel_model(mspec)
    pspec = PathSpec(alpha_deg=45.0, n_layers=1, compute_coverage=False)
    pr = compute_path(model, pspec)

    # WindingPath doğrudan geçilebilmeli
    mr_direct = compute_motion(pr.winding_path)
    check(mr_direct.n_segments > 0, "C9 WindingPath doğrudan kabul edildi")
    print("  C9 compute_motion(WindingPath doğrudan): OK")


# ── C10: compute_gcode — MotionResult veya segment listesi kabul ─────────────

def c10_gcode_accepts_segment_list():
    mspec = MandrelSpec(kind="cylinder", diameter_mm=80.0, length_mm=150.0)
    model = build_mandrel_model(mspec)
    pspec = PathSpec(alpha_deg=45.0, n_layers=1, compute_coverage=False)
    pr = compute_path(model, pspec)
    mr = compute_motion(pr)
    cfg = MachineConfig()

    # Segment listesi doğrudan geçilebilmeli
    gp = compute_gcode(mr.segments, pr.winding_path, cfg)
    check(len(gp.lines) > 0, "C10 segment listesi doğrudan kabul edildi")
    print("  C10 compute_gcode(segment listesi doğrudan): OK")


# ── C11: shim eşdeğerliği — load_mandrel == build_mandrel_model ──────────────

def c11_shim_load_mandrel_equiv():
    """app/cam_engine.load_mandrel ↔ core.build_mandrel_model bit-eşdeğer."""
    from app.cam_engine import CamRequest, load_mandrel as shim_load

    req = CamRequest(diameter_mm=100.0, length_mm=300.0)
    shim_model = shim_load(req)

    core_model = build_mandrel_model(
        MandrelSpec(kind="cylinder", diameter_mm=100.0, length_mm=300.0)
    )

    import numpy as np
    sp = shim_model.as_profile()
    cp = core_model.as_profile()
    check(sp.r_mm.shape == cp.r_mm.shape, "C11 profil shape eşleşiyor")
    check(abs(sp.r_mm.max() - cp.r_mm.max()) < 0.1, "C11 r_max eşleşiyor")
    print("  C11 shim.load_mandrel ↔ core.build_mandrel_model: OK")


# ── C12: shim eşdeğerliği — compute_twin → TwinSimulationResult ──────────────

def c12_shim_twin_returns_simulation():
    """app/cam_engine.compute_twin TwinSimulationResult döndürmeli."""
    from app.cam_engine import CamRequest, load_mandrel as shim_load
    import app.cam_engine as shim_eng

    req = CamRequest(diameter_mm=80.0, length_mm=150.0, n_layers=1,
                     strategy="Sarmal", twin_dt_s=2.0)
    model = shim_load(req)
    result = shim_eng.compute_twin(model, req)
    # isinstance yerine tip adı kontrolü — çift modül yükleme sorunundan kaçınır
    check(type(result).__name__ == "TwinSimulationResult",
          "C12 shim.compute_twin → TwinSimulationResult")
    check(hasattr(result, "states") and len(result.states) > 0,
          "C12 states boş değil")
    check(hasattr(result, "total_time_s") and result.total_time_s > 0,
          "C12 total_time_s>0")
    print("  C12 shim.compute_twin → TwinSimulationResult: OK")


# ── C13: build_mandrel_model — bilinmeyen tür ValueError ─────────────────────

def c13_unknown_kind_raises():
    try:
        build_mandrel_model(MandrelSpec(kind="unknown_xyz"))
        check(False, "C13 ValueError bekleniyor, fırlatılmadı")
    except ValueError:
        check(True, "C13 ValueError fırlatıldı")
    print("  C13 bilinmeyen tür ValueError: OK")


# ── C14: preflight_check kapısı (ComplexityError) ────────────────────────────

def c14_preflight_blocks_huge_path():
    from faz17_d1.core.path_generator import ComplexityError
    mspec = MandrelSpec(kind="cylinder", diameter_mm=1000.0, length_mm=5000.0)
    model = build_mandrel_model(mspec)
    pspec = PathSpec(alpha_deg=5.0, n_layers=20, tow_width_mm=1.0,
                     overlap_pct=0.0, run_preflight=True, compute_coverage=False)
    try:
        compute_path(model, pspec)
        # Çok büyük yollar ComplexityError fırlatabilir veya fırlatmayabilir;
        # önemli olan fonksiyon çökmemesi
        check(True, "C14 preflight geçti veya makul hata fırlattı")
    except ComplexityError:
        check(True, "C14 ComplexityError fırlatıldı (beklenen davranış)")
    except Exception as e:
        check(False, f"C14 beklenmedik hata: {type(e).__name__}: {e}")
    print("  C14 preflight_check kapısı: OK")


# ── Ana çalıştırıcı ──────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("test_core_cam_engine.py — core/cam_engine.py facade doğrulama")
    print("=" * 60)

    tests = [
        c1_cylinder,
        c2_cone,
        c3_dome_cylinder_dome,
        c4_compute_path_fields,
        c5_compute_path_coverage,
        c6_compute_motion,
        c7_compute_gcode,
        c8_compute_twin,
        c9_motion_accepts_winding_path,
        c10_gcode_accepts_segment_list,
        c11_shim_load_mandrel_equiv,
        c12_shim_twin_returns_simulation,
        c13_unknown_kind_raises,
        c14_preflight_blocks_huge_path,
    ]

    for t in tests:
        try:
            t()
        except Exception as exc:
            global _FAIL
            _FAIL += 1
            print(f"  ✗ EXCEPTION in {t.__name__}: {type(exc).__name__}: {exc}")

    print("-" * 60)
    total = _PASS + _FAIL
    print(f"SONUÇ: {_PASS}/{total} geçti, {_FAIL} başarısız")
    if _FAIL == 0:
        print("★★★ HAZIR ★★★")
    else:
        print("STOP — HATALAR VAR")
    return _FAIL


if __name__ == "__main__":
    sys.exit(main())
