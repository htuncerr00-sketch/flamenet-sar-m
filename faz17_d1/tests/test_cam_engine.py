"""
tests/test_cam_engine.py — cam_engine facade doğrulaması (S2)
=============================================================
cam_engine'nin 5 fonksiyonunu backend'e doğrudan erişmeden test eder.
Qt olmadan çalışır (headless).

Çalıştırma:
    python3 faz17_d1/tests/test_cam_engine.py
"""
from __future__ import annotations
import os
import sys

# Backend import yolu
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "faz17_d1_backend"))
# cam_engine import yolu (faz17_d2 app)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "faz17_d2", "faz17_d2_app", "faz17_d2"))
# backend symlink'i simüle et
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "faz17_d1_backend"))
# cam_engine'nin `from backend.core.*` yapması için backend paketini bul
import importlib, types

# cam_engine `from backend.core.*` diye import eder; backend = faz17_d1
# Bunu test ortamında şöyle çözeriz:
if "backend" not in sys.modules:
    import faz17_d1
    import faz17_d1.core
    import faz17_d1.core.geometry_engine
    import faz17_d1.core.path_generator
    import faz17_d1.core.motion_planner
    import faz17_d1.core.gcode_postprocessor
    import faz17_d1.core.mandrel_model
    import faz17_d1.core.stl_intelligence
    import faz17_d1.core.winding_twin
    import faz17_d1.core.coverage_solver
    import faz17_d1.core.fiber_band
    for name in list(sys.modules):
        if name.startswith("faz17_d1"):
            alias = name.replace("faz17_d1", "backend", 1)
            sys.modules.setdefault(alias, sys.modules[name])

from app.cam_engine import CamRequest
import app.cam_engine as eng

_PASS = 0
_FAIL = 0


def check(cond: bool, msg: str):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  ✗ FAIL: {msg}")


# ── T1: CamRequest varsayılan değerleri ──────────────────────────────────────

def t1_cam_request_defaults():
    req = CamRequest()
    check(req.mandrel_type == "Silindir", "T1 mandrel_type varsayılan Silindir")
    check(req.diameter_mm == 100.0, "T1 diameter varsayılan 100")
    check(req.strategy == "Sarmal", "T1 strategy varsayılan Sarmal")
    check(req.twin_dt_s == 1.0, "T1 twin_dt_s varsayılan 1.0")
    print("  T1 CamRequest defaults: OK")


# ── T2: load_mandrel — Silindir ───────────────────────────────────────────────

def t2_load_mandrel_cylinder():
    req = CamRequest(diameter_mm=100.0, length_mm=300.0)
    model = eng.load_mandrel(req)
    check(model.source_type == "parametric", "T2 source_type parametrik")
    check(model.is_winding_ready(), "T2 parametrik winding-ready")
    prof = model.as_profile()
    import numpy as np
    check(abs(prof.r_mm.max() - 50.0) < 0.1, "T2 r_max=50")
    check(abs(prof.z_mm[-1] - 300.0) < 0.1, "T2 L=300")
    print(f"  T2 load_mandrel cylinder: r_max={prof.r_mm.max():.1f} L={prof.z_mm[-1]:.1f}")


# ── T3: load_mandrel — Konik ─────────────────────────────────────────────────

def t3_load_mandrel_cone():
    req = CamRequest(mandrel_type="Konik", diameter_mm=60.0,
                     length_mm=200.0, cone_angle_deg=10.0)
    model = eng.load_mandrel(req)
    prof = model.as_profile()
    check(prof.r_mm[0] < prof.r_mm[-1], "T3 konik r artan")
    check(model.source_type == "parametric", "T3 source_type parametrik")
    print(f"  T3 load_mandrel cone: r0={prof.r_mm[0]:.1f} r1={prof.r_mm[-1]:.1f}")


# ── T4: load_mandrel — Kubbeli Silindir ──────────────────────────────────────

def t4_load_mandrel_dome():
    req = CamRequest(mandrel_type="Kubbeli Silindir",
                     diameter_mm=100.0, length_mm=300.0, dome_h_mm=50.0)
    model = eng.load_mandrel(req)
    prof = model.as_profile()
    check(len(prof.z_mm) > 100, "T4 dome profil yeterli nokta")
    check(model.source_type == "parametric", "T4 source_type parametrik")
    print(f"  T4 load_mandrel dome: n={len(prof.z_mm)}, r_max={prof.r_mm.max():.1f}")


# ── T5: compute_path — Sarmal ────────────────────────────────────────────────

def t5_compute_path_helical():
    req = CamRequest(diameter_mm=100.0, length_mm=300.0,
                     alpha_deg=55.0, n_layers=2, tow_w_mm=6.0,
                     strategy="Sarmal")
    model = eng.load_mandrel(req)
    path, all_layers = eng.compute_path(req, model)
    check(len(path.points) > 0, "T5 yol noktaları var")
    check(path.n_circuits > 0, "T5 devre sayısı > 0")
    check(all_layers is None, "T5 tek-açı modda all_layers=None")
    print(f"  T5 compute_path helical: {len(path.points)} nokta, "
          f"{path.n_circuits} devre, {path.coverage_pct:.1f}% kapsama")


# ── T6: compute_path — Çevre ─────────────────────────────────────────────────

def t6_compute_path_hoop():
    req = CamRequest(diameter_mm=100.0, length_mm=200.0,
                     n_layers=1, tow_w_mm=6.0, strategy="Çevre")
    model = eng.load_mandrel(req)
    path, _ = eng.compute_path(req, model)
    check(len(path.points) > 0, "T6 çevre yol noktaları var")
    # Hoop alpha ≈ 88° → yüksek α
    if path.points:
        check(path.params.alpha_deg >= 85.0, f"T6 hoop alpha≈88 (got {path.params.alpha_deg})")
    print(f"  T6 compute_path hoop: {len(path.points)} nokta, alpha={path.params.alpha_deg}°")


# ── T7: compute_gcode ────────────────────────────────────────────────────────

def t7_compute_gcode():
    from faz17_d1.core.gcode_postprocessor import MachineConfig
    req = CamRequest(diameter_mm=100.0, length_mm=300.0,
                     alpha_deg=55.0, n_layers=1, tow_w_mm=6.0)
    model = eng.load_mandrel(req)
    path, all_layers = eng.compute_path(req, model)
    cfg = MachineConfig(controller_type="grbl", x_axis="X", a_axis="A")
    gp = eng.compute_gcode(path, cfg, all_layers)
    check(len(gp.lines) > 5, "T7 G-code satır sayısı > 5")
    check(any("G1" in ln for ln in gp.lines), "T7 G1 hareketi var")
    check(any("G21" in ln for ln in gp.lines), "T7 G21 başlık var")
    check(gp.n_circuits > 0, "T7 devre sayısı > 0")
    print(f"  T7 compute_gcode: {len(gp.lines)} satır, {gp.n_circuits} devre")


# ── T8: MandrelModel — is_winding_ready kapısı ───────────────────────────────

def t8_winding_ready_gate():
    # STL olmadan → parametrik → daima hazır
    req = CamRequest(mandrel_type="Silindir", diameter_mm=100.0, length_mm=300.0)
    model = eng.load_mandrel(req)
    check(model.is_winding_ready(), "T8 parametrik winding-ready")
    check(model.confidence is None, "T8 parametrik confidence=None")
    print("  T8 winding_ready gate: parametrik=HAZIR, confidence=None ✓")


# ── T9: compute_coverage ─────────────────────────────────────────────────────

def t9_compute_coverage():
    req = CamRequest(diameter_mm=100.0, length_mm=200.0,
                     alpha_deg=55.0, n_layers=1, tow_w_mm=6.0)
    model = eng.load_mandrel(req)
    path, _ = eng.compute_path(req, model)
    cmap = eng.compute_coverage(path, model)
    check(0.0 < cmap.coverage_pct <= 100.0, f"T9 kapsama {cmap.coverage_pct:.1f}% geçerli")
    check(cmap.uniformity_index() >= 0.0, "T9 tekdüzelik geçerli")
    print(f"  T9 compute_coverage: {cmap.coverage_pct:.1f}% kaplama, "
          f"tekdüzelik={cmap.uniformity_index():.3f}")


# ── T10: compute_twin ────────────────────────────────────────────────────────

def t10_compute_twin():
    req = CamRequest(diameter_mm=100.0, length_mm=200.0,
                     alpha_deg=55.0, n_layers=2, tow_w_mm=6.0,
                     twin_dt_s=2.0)
    model = eng.load_mandrel(req)
    result = eng.compute_twin(model, req)
    check(result.total_time_s > 0, "T10 toplam süre > 0")
    check(len(result.states) > 0, "T10 durum listesi dolu")
    if result.states:
        s = result.states[-1]
        check(s.progress_pct >= 0, "T10 ilerleme % geçerli")
        check(s.fiber_deposited_mm > 0, "T10 fiber yatırıldı")
    print(f"  T10 compute_twin: {len(result.states)} durum, "
          f"süre={result.total_time_s:.1f}s, "
          f"fiber={result.total_fiber_length_mm/1000:.1f}m")


def main():
    print("=" * 70)
    print("cam_engine facade — S2 doğrulama")
    print("=" * 70)
    for fn in [
        t1_cam_request_defaults,
        t2_load_mandrel_cylinder,
        t3_load_mandrel_cone,
        t4_load_mandrel_dome,
        t5_compute_path_helical,
        t6_compute_path_hoop,
        t7_compute_gcode,
        t8_winding_ready_gate,
        t9_compute_coverage,
        t10_compute_twin,
    ]:
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
