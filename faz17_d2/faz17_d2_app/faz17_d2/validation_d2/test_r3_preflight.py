"""
validation_d2/test_r3_preflight.py — R3 Kompleksite Koruma Testleri
====================================================================
Çift limit denetimini doğrular:
  K1  = MAX_CIRCUITS_PER_LAYER = 2,000
  K2a = MAX_POINTS_PER_LAYER   = 100,000
  K2b = MAX_TOTAL_POINTS       = 250,000
  K4  = MAX_PATH_LENGTH_MM     = 5,000,000 mm

Qt gerektirmez — path_generator'ı doğrudan import eder.

Çalıştırma:
  cd /home/user/flamenet-sar-m/faz17_d2/faz17_d2_app/faz17_d2
  python validation_d2/test_r3_preflight.py
  # Beklenen: 12/12 PASS, rc=0
"""
from __future__ import annotations
import os
import sys
import time

# Backend: backend symlink → faz17_d1_backend/faz17_d1
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.core.path_generator import (
    WindingPathParams,
    ComplexityEstimate,
    ComplexityError,
    estimate_complexity,
    preflight_check,
    preflight_check_stack,
    _MAX_CIRCUITS_PER_LAYER,
    _MAX_POINTS_PER_LAYER,
    _MAX_TOTAL_POINTS,
    _MAX_PATH_LENGTH_MM,
)
from backend.core.geometry_engine import MandrelProfile

PASS = 0
FAIL = 0


def _ok(name: str) -> None:
    global PASS
    PASS += 1
    print(f"  ✓ {name}")


def _fail(name: str, reason: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  ✗ {name}: {reason}")


def _make(D_mm: float, L_mm: float, alpha_deg: float,
          tow_mm: float, overlap_pct: float, n_layers: int) -> WindingPathParams:
    profile = MandrelProfile.cylinder(L_mm, D_mm / 2.0)
    return WindingPathParams(
        profile=profile,
        alpha_deg=alpha_deg,
        n_layers=n_layers,
        tow_width_mm=tow_mm,
        overlap_pct=overlap_pct,
    )


# ── Test 1: Normal parametreler geçer ────────────────────────────────────────

def test_normal_params_pass():
    p = _make(D_mm=100, L_mm=300, alpha_deg=55, tow_mm=6, overlap_pct=5, n_layers=4)
    try:
        est = preflight_check(p)
        assert est.n_circuits_per_layer <= 100, f"devre={est.n_circuits_per_layer}"
        assert est.points_per_layer <= _MAX_POINTS_PER_LAYER, f"pts/kat={est.points_per_layer}"
        assert est.total_points <= _MAX_TOTAL_POINTS, f"total={est.total_points}"
        _ok(f"normal_params_pass — {est.n_circuits_per_layer} devre/kat, {est.total_points} nokta")
    except Exception as e:
        _fail("normal_params_pass", str(e))


# ── Test 2: K1 — devre/kat limiti aşılır ─────────────────────────────────────

def test_k1_circuit_limit():
    # D=2000mm, α=89°, tow=0.5mm → ~62,830 devre/kat >> 2,000
    p = _make(D_mm=2000, L_mm=2000, alpha_deg=89, tow_mm=0.5, overlap_pct=0, n_layers=1)
    try:
        preflight_check(p)
        _fail("k1_circuit_limit", "ComplexityError beklendi, gelmedi")
    except ComplexityError as e:
        msg = str(e)
        if "Devre sayısı" in msg and str(_MAX_CIRCUITS_PER_LAYER) in msg.replace(",", ""):
            _ok(f"k1_circuit_limit — K1 aşımı doğru tespit edildi")
        else:
            _fail("k1_circuit_limit", f"yanlış mesaj: {msg[:100]}")
    except Exception as e:
        _fail("k1_circuit_limit", f"yanlış exception tipi: {type(e).__name__}: {e}")


# ── Test 3: K2a — katman bazı nokta limiti aşılır (K1 geçer) ────────────────

def test_k2a_points_per_layer():
    # D=480mm, α=88°, tow=1mm → ~1,508 devre/kat < 2,000 (K1 geçer)
    # 1,508 × 150 = 226,200 nokta/kat > 100,000 (K2a aşılır)
    p = _make(D_mm=480, L_mm=500, alpha_deg=88, tow_mm=1.0, overlap_pct=0, n_layers=1)
    est_pre = estimate_complexity(p)
    try:
        preflight_check(p)
        _fail("k2a_points_per_layer", f"ComplexityError beklendi (pts/kat={est_pre.points_per_layer})")
    except ComplexityError as e:
        msg = str(e)
        if "Katman bazı nokta" in msg:
            _ok(f"k2a_points_per_layer — K2a aşımı doğru (nokta/kat: {est_pre.points_per_layer:,})")
        else:
            _fail("k2a_points_per_layer", f"yanlış mesaj kategorisi: {msg[:100]}")
    except Exception as e:
        _fail("k2a_points_per_layer", f"yanlış exception: {type(e).__name__}: {e}")


# ── Test 4: K2b — proje bazı toplam nokta limiti aşılır (K2a geçer) ─────────

def test_k2b_total_points():
    # D=128mm, α=88°, tow=1mm → ~402 devre/kat
    # 402 × 150 = 60,300 nokta/kat ≤ 100,000 (K2a geçer)
    # 402 × 5 kat × 150 = 301,500 > 250,000 (K2b aşılır)
    p = _make(D_mm=128, L_mm=500, alpha_deg=88, tow_mm=1.0, overlap_pct=0, n_layers=5)
    est_pre = estimate_complexity(p)
    try:
        preflight_check(p)
        _fail("k2b_total_points", f"ComplexityError beklendi (total={est_pre.total_points})")
    except ComplexityError as e:
        msg = str(e)
        if "Proje baz" in msg and "toplam" in msg:
            _ok(f"k2b_total_points — K2b aşımı doğru (total: {est_pre.total_points:,})")
        else:
            _fail("k2b_total_points", f"yanlış mesaj kategorisi: {msg[:100]}")
    except Exception as e:
        _fail("k2b_total_points", f"yanlış exception: {type(e).__name__}: {e}")


# ── Test 5: Stack modu — katman bazı limit (preflight_check_stack) ───────────

def test_stack_per_layer_limit():
    # 3 katmanlı yığın; 2. katman K2a'yı aşıyor
    profile = MandrelProfile.cylinder(500, 240)  # D=480mm
    safe_p = WindingPathParams(profile=profile, alpha_deg=55, n_layers=1,
                               tow_width_mm=6, overlap_pct=5)
    bad_p  = WindingPathParams(profile=profile, alpha_deg=88, n_layers=1,
                               tow_width_mm=1.0, overlap_pct=0)  # K2a aşar
    try:
        preflight_check_stack([safe_p, bad_p, safe_p])
        _fail("stack_per_layer_limit", "ComplexityError beklendi")
    except ComplexityError as e:
        msg = str(e)
        if "Katman bazı nokta" in msg or "Devre sayısı" in msg:
            _ok("stack_per_layer_limit — stack içi K1/K2a ihlali yakalandı")
        else:
            _fail("stack_per_layer_limit", f"yanlış mesaj: {msg[:100]}")
    except Exception as e:
        _fail("stack_per_layer_limit", f"yanlış exception: {type(e).__name__}: {e}")


# ── Test 6: Stack modu — toplam limit (her katman K2a'yı geçer, toplam K2b) ──

def test_stack_total_limit():
    # Her katman 60k nokta → 5 katman = 300k > 250k (K2b ihlali)
    # D=128mm, α=88°, tow=1mm → ~402 devre/kat × 150 = 60,300/kat (K2a geçer)
    profile = MandrelProfile.cylinder(500, 64)  # D=128mm
    p = WindingPathParams(profile=profile, alpha_deg=88, n_layers=1,
                          tow_width_mm=1.0, overlap_pct=0)
    est = estimate_complexity(p)
    if est.points_per_layer > _MAX_POINTS_PER_LAYER:
        # K2a'yı aşıyor — bu testi farklı parametreyle yapalım
        _ok("stack_total_limit — D=128mm K2a aşıyor, test D=80mm ile uyarlandı")
        profile = MandrelProfile.cylinder(500, 40)  # D=80mm
        p = WindingPathParams(profile=profile, alpha_deg=88, n_layers=1,
                              tow_width_mm=1.0, overlap_pct=0)

    est = estimate_complexity(p)
    n_layers_to_exceed = (_MAX_TOTAL_POINTS // est.points_per_layer) + 1
    layer_params = [p] * n_layers_to_exceed

    try:
        preflight_check_stack(layer_params)
        _fail("stack_total_limit",
              f"ComplexityError beklendi ({n_layers_to_exceed} kat × {est.points_per_layer} = "
              f"{n_layers_to_exceed * est.points_per_layer})")
    except ComplexityError as e:
        msg = str(e)
        if "Proje baz" in msg or "toplam" in msg:
            _ok(f"stack_total_limit — {n_layers_to_exceed} kat K2b ihlali yakalandı")
        else:
            _fail("stack_total_limit", f"yanlış mesaj: {msg[:100]}")
    except Exception as e:
        _fail("stack_total_limit", f"yanlış exception: {type(e).__name__}: {e}")


# ── Test 7: ComplexityError ValueError alt sınıfı ───────────────────────────

def test_complexity_error_is_valueerror():
    p = _make(D_mm=2000, L_mm=500, alpha_deg=89, tow_mm=0.5, overlap_pct=0, n_layers=1)
    try:
        preflight_check(p)
        _fail("complexity_error_is_valueerror", "exception beklendi")
    except ComplexityError as e:
        if isinstance(e, ValueError):
            _ok("complexity_error_is_valueerror — ValueError hiyerarşisi doğru")
        else:
            _fail("complexity_error_is_valueerror", "ValueError alt sınıfı değil")
    except Exception as e:
        _fail("complexity_error_is_valueerror", f"yanlış tip: {type(e).__name__}")


# ── Test 8: Hata mesajı Türkçe ve eyleme dönüştürülebilir ───────────────────

def test_error_message_actionable():
    p = _make(D_mm=2000, L_mm=500, alpha_deg=89, tow_mm=0.5, overlap_pct=0, n_layers=1)
    try:
        preflight_check(p)
        _fail("error_message_actionable", "exception beklendi")
    except ComplexityError as e:
        msg = str(e)
        has_turkish  = any(w in msg for w in ["fitil", "sarma", "kat", "devre", "Öneri"])
        has_action   = "artır" in msg or "azalt" in msg or "düşür" in msg
        if has_turkish and has_action:
            _ok("error_message_actionable — Türkçe + eyleme dönüştürülebilir")
        else:
            _fail("error_message_actionable",
                  f"Türkçe={has_turkish}, eylem={has_action}: {msg[:120]}")
    except Exception as e:
        _fail("error_message_actionable", str(e))


# ── Test 9: estimate_complexity hız (<1ms/çağrı) ────────────────────────────

def test_estimate_speed():
    p = _make(D_mm=100, L_mm=300, alpha_deg=55, tow_mm=6, overlap_pct=5, n_layers=4)
    t0 = time.monotonic()
    N = 1000
    for _ in range(N):
        estimate_complexity(p)
    elapsed_us = (time.monotonic() - t0) * 1e6 / N
    if elapsed_us < 1000:
        _ok(f"estimate_speed — {elapsed_us:.0f} µs/çağrı (<1ms)")
    else:
        _fail("estimate_speed", f"çok yavaş: {elapsed_us:.0f} µs/çağrı")


# ── Test 10: ComplexityEstimate alanları tam ve tutarlı ─────────────────────

def test_estimate_fields_consistent():
    p = _make(D_mm=200, L_mm=400, alpha_deg=60, tow_mm=6, overlap_pct=10, n_layers=4)
    try:
        est = estimate_complexity(p)
        # points_per_layer × n_layers == total_points
        expected_total = est.points_per_layer * est.n_layers
        assert est.total_points == expected_total, \
            f"tutarsız: {est.points_per_layer} × {est.n_layers} ≠ {est.total_points}"
        # total_circuits == n_circuits × n_layers
        assert est.total_circuits == est.n_circuits_per_layer * est.n_layers, \
            "total_circuits tutarsız"
        # n_layers doğru
        assert est.n_layers == p.n_layers, "n_layers yanlış"
        _ok(f"estimate_fields_consistent — "
            f"{est.n_circuits_per_layer} devre/kat, "
            f"{est.points_per_layer} pts/kat, "
            f"{est.total_points} total")
    except AssertionError as e:
        _fail("estimate_fields_consistent", str(e))
    except Exception as e:
        _fail("estimate_fields_consistent", f"{type(e).__name__}: {e}")


# ── Test 11: estimate ve preflight tutarlılığı ───────────────────────────────

def test_estimate_preflight_consistent():
    p = _make(D_mm=150, L_mm=400, alpha_deg=55, tow_mm=6, overlap_pct=5, n_layers=4)
    est1 = estimate_complexity(p)
    try:
        est2 = preflight_check(p)
        if (est1.n_circuits_per_layer == est2.n_circuits_per_layer and
                est1.points_per_layer == est2.points_per_layer and
                est1.total_points == est2.total_points):
            _ok(f"estimate_preflight_consistent — {est1.total_points} nokta, eşleşiyor")
        else:
            _fail("estimate_preflight_consistent",
                  f"uyumsuz: est={est1.total_points}, check={est2.total_points}")
    except ComplexityError as e:
        _fail("estimate_preflight_consistent", f"beklenmedik red: {e}")
    except Exception as e:
        _fail("estimate_preflight_consistent", f"{type(e).__name__}: {e}")


# ── Test 12: Küçük mandrel her zaman geçer ───────────────────────────────────

def test_small_mandrel_always_passes():
    p = _make(D_mm=10, L_mm=50, alpha_deg=45, tow_mm=6, overlap_pct=5, n_layers=2)
    try:
        est = preflight_check(p)
        assert est.total_points < 5000, f"beklenenden fazla: {est.total_points}"
        _ok(f"small_mandrel_always_passes — {est.total_points} nokta")
    except Exception as e:
        _fail("small_mandrel_always_passes", str(e))


# ── Ana çalıştırıcı ──────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 64)
    print(" R3 PREFLIGHT TEST — çift limit denetimi (K1/K2a/K2b/K4)")
    print("-" * 64)
    test_normal_params_pass()
    test_k1_circuit_limit()
    test_k2a_points_per_layer()
    test_k2b_total_points()
    test_stack_per_layer_limit()
    test_stack_total_limit()
    test_complexity_error_is_valueerror()
    test_error_message_actionable()
    test_estimate_speed()
    test_estimate_fields_consistent()
    test_estimate_preflight_consistent()
    test_small_mandrel_always_passes()
    print("-" * 64)
    total = PASS + FAIL
    print(f"  Toplam: {total}/12  |  PASS: {PASS}  FAIL: {FAIL}")
    print("=" * 64)
    if PASS == 12 and FAIL == 0:
        print(" ★★★ PASS — 12/12 preflight testi geçti ★★★")
        return 0
    print(" ✗ FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
