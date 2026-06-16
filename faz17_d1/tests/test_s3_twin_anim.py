"""
tests/test_s3_twin_anim.py — S3 TwinState animasyon entegrasyonu
================================================================
Entegre Tasarım Paneli'nin naif cos/sin projeksiyonu yerine TwinState
tabanlı animasyon kullandığını doğrular. Offscreen Qt ile çalışır.

Çalıştırma:
    QT_QPA_PLATFORM=offscreen QT_OPENGL=software \
        python3 faz17_d1/tests/test_s3_twin_anim.py
"""
from __future__ import annotations
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

# Yol kurulumu: faz17_d2 app + backend
_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, "..", "..", "faz17_d2", "faz17_d2_app", "faz17_d2"))
sys.path.insert(0, os.path.join(_HERE, "..", "faz17_d1_backend"))

import numpy as np

_PASS = 0
_FAIL = 0


def check(cond: bool, msg: str):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  ✗ FAIL: {msg}")


def _make_twin():
    """Küçük bir digital twin sonucu üret (gerçek simulate_winding)."""
    from backend.core.geometry_engine import MandrelProfile
    from backend.core.path_generator import WindingPathParams
    from backend.core.fiber_band import FiberBand
    from backend.core.winding_twin import simulate_winding

    profile = MandrelProfile.cylinder(200.0, 50.0)
    pp = WindingPathParams(profile=profile, alpha_deg=55.0, n_layers=2,
                           tow_width_mm=8.0, overlap_pct=5.0,
                           winding_strategy="helical")
    band = FiberBand(tow_width_mm=8.0)
    twin = simulate_winding(base_profile=profile, band=band,
                            base_params=pp, n_layers=2, dt_s=1.0)
    return twin, profile


# ── T1: TwinState gerekli alanları taşıyor ───────────────────────────────────

def t1_twinstate_fields():
    twin, _ = _make_twin()
    check(len(twin.states) > 0, "T1 twin durumları var")
    s = twin.states[len(twin.states) // 2]
    for fld in ("spindle_angle_deg", "carriage_x_actual_mm",
                "current_radius_mm", "eye_x_mm", "eye_r_mm"):
        check(hasattr(s, fld), f"T1 TwinState.{fld} mevcut")
    print(f"  T1 TwinState fields: {len(twin.states)} durum, "
          f"alanlar tam ✓")


# ── T2: Katman büyümesi — current_radius_mm artıyor ──────────────────────────

def t2_layer_growth():
    twin, _ = _make_twin()
    r_first = twin.states[0].current_radius_mm
    r_last = twin.states[-1].current_radius_mm
    check(r_last >= r_first, f"T2 yarıçap büyüdü ({r_first:.1f}→{r_last:.1f})")
    check(twin.final_radius_mm > twin.base_radius_mm,
          "T2 final yarıçap > taban yarıçap")
    print(f"  T2 layer growth: r {twin.base_radius_mm:.1f}→{twin.final_radius_mm:.1f}mm")


# ── T3: Panel _setup_animation TwinState'ten dizi kuruyor ────────────────────

def t3_setup_animation():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from app.panels.entegre_tasarim_paneli import EntegreTasarimPaneli

    twin, profile = _make_twin()
    panel = EntegreTasarimPaneli()
    panel._setup_animation(twin, profile)

    check(panel._anim_xyz is not None, "T3 _anim_xyz kuruldu")
    check(panel._anim_eye is not None, "T3 _anim_eye kuruldu (payout gözü)")
    check(panel._anim_r_mm is not None, "T3 _anim_r_mm kuruldu (yarıçap)")
    n = len(panel._anim_xyz)
    check(panel._anim_xyz.shape == (n, 3), "T3 temas noktaları (N,3)")
    check(panel._anim_eye.shape == (n, 3), "T3 göz koordinatları (N,3)")
    check(len(panel._anim_a_deg) == n, "T3 açı dizisi N uzunluk")
    check(panel._btn_play.isEnabled(), "T3 Oynat butonu aktif")
    print(f"  T3 setup_animation: {n} kare, temas+göz+yarıçap dizileri ✓")


# ── T4: _apply_anim_frame sahneyi günceller (eye + delivery item) ────────────

def t4_apply_frame():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from app.panels.entegre_tasarim_paneli import EntegreTasarimPaneli

    twin, profile = _make_twin()
    panel = EntegreTasarimPaneli()
    panel._setup_animation(twin, profile)
    n = len(panel._anim_xyz)

    # İlk, orta, son kareleri uygula
    for idx in (0, n // 2, n - 1):
        panel._apply_anim_frame(idx)

    gl = panel._gl
    check(gl._eye_item is not None, "T4 payout gözü 3D item oluştu")
    check(gl._delivery_item is not None, "T4 teslim fiberi 3D item oluştu")
    check(gl._ribbon_item is not None, "T4 büyüyen tow ribbon (mesh) oluştu")
    print("  T4 apply_frame: göz + teslim fiberi + ribbon sahneye eklendi ✓")


# ── T5: Mandrel x∈[0,L] sınırları — temas noktaları aralık içinde ────────────

def t5_contact_bounds():
    twin, profile = _make_twin()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from app.panels.entegre_tasarim_paneli import EntegreTasarimPaneli

    panel = EntegreTasarimPaneli()
    panel._setup_animation(twin, profile)
    xs = panel._anim_xyz[:, 0]   # metre
    L_m = (profile.z_mm[-1] - profile.z_mm[0]) / 1000.0
    # Küçük tolerans (lag offset)
    check(xs.min() >= -0.05, f"T5 temas x alt sınır ({xs.min():.3f})")
    check(xs.max() <= L_m + 0.05, f"T5 temas x üst sınır ({xs.max():.3f} ≤ {L_m:.3f})")
    print(f"  T5 contact bounds: x∈[{xs.min():.3f},{xs.max():.3f}]m, L={L_m:.3f}m")


# ── T6: twin=None → animasyon nazikçe devre dışı ─────────────────────────────

def t6_none_twin():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from app.panels.entegre_tasarim_paneli import EntegreTasarimPaneli

    _, profile = _make_twin()
    panel = EntegreTasarimPaneli()
    panel._setup_animation(None, profile)
    check(panel._anim_xyz is None, "T6 twin=None → anim dizisi yok")
    check(not panel._btn_play.isEnabled(), "T6 twin=None → Oynat pasif")
    print("  T6 none twin: animasyon nazikçe devre dışı ✓")


# ── T7: S4 ribbon kenarları — fiziksel genişlik ──────────────────────────────

def t7_ribbon_edges():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from app.panels.entegre_tasarim_paneli import EntegreTasarimPaneli

    twin, profile = _make_twin()
    panel = EntegreTasarimPaneli()
    panel._anim_tow_w_mm = 8.0
    panel._setup_animation(twin, profile)

    check(panel._anim_rib_L is not None, "T7 ribbon sol kenar kuruldu")
    check(panel._anim_rib_R is not None, "T7 ribbon sağ kenar kuruldu")
    n = len(panel._anim_xyz)
    check(panel._anim_rib_L.shape == (n, 3), "T7 sol kenar (N,3)")
    check(panel._anim_rib_R.shape == (n, 3), "T7 sağ kenar (N,3)")

    # Bant genişliği fiziksel: |L-R| ≈ tow_w (metre), birkaç orta noktada
    L = panel._anim_rib_L.astype(np.float64)
    R = panel._anim_rib_R.astype(np.float64)
    widths = np.linalg.norm(L - R, axis=1)   # metre
    mid = widths[n // 4: 3 * n // 4]
    expected_m = 8.0 / 1000.0
    err = np.abs(np.median(mid) - expected_m) / expected_m
    check(err < 0.05, f"T7 bant genişliği ≈8mm (medyan={np.median(mid)*1000:.2f}mm)")
    print(f"  T7 ribbon edges: genişlik medyan={np.median(mid)*1000:.2f}mm "
          f"(hedef 8.00mm), hata=%{err*100:.1f}")


# ── T8: S4 ribbon mesh sahneye eklendi (GLMeshItem) ──────────────────────────

def t8_ribbon_mesh():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from app.panels.entegre_tasarim_paneli import EntegreTasarimPaneli

    twin, profile = _make_twin()
    panel = EntegreTasarimPaneli()
    panel._setup_animation(twin, profile)
    n = len(panel._anim_xyz)
    panel._apply_anim_frame(n // 2)

    gl = panel._gl
    check(gl._ribbon_item is not None, "T8 tow ribbon mesh oluştu")
    # İnce çizgi artık birincil değil (ribbon devraldı)
    check(gl._anim_fiber_item is None, "T8 ince çizgi kullanılmıyor (ribbon devrede)")
    print("  T8 ribbon mesh: GLMeshItem sahnede, ince çizgi devre dışı ✓")


# ── T9: Ribbon genişlik yönü merkez çizgisine dik ─────────────────────────────

def t9_ribbon_perpendicular():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from app.panels.entegre_tasarim_paneli import EntegreTasarimPaneli

    twin, profile = _make_twin()
    panel = EntegreTasarimPaneli()
    panel._setup_animation(twin, profile)

    P = panel._anim_xyz.astype(np.float64)
    L = panel._anim_rib_L.astype(np.float64)
    R = panel._anim_rib_R.astype(np.float64)
    n = len(P)
    # Genişlik vektörü w = L-R; merkez tanjantı t = P[i+1]-P[i]; w·t ≈ 0
    i = n // 2
    w = L[i] - R[i]
    t = P[i + 1] - P[i]
    w /= (np.linalg.norm(w) + 1e-12)
    t /= (np.linalg.norm(t) + 1e-12)
    dot = abs(float(np.dot(w, t)))
    check(dot < 0.15, f"T9 genişlik yönü tanjanta ~dik (|w·t|={dot:.3f})")
    print(f"  T9 ribbon perpendicular: |w·t|={dot:.3f} (≈0 ⇒ dik) ✓")


def main():
    print("=" * 70)
    print("S3+S4 — TwinState animasyon + tow ribbon doğrulama")
    print("=" * 70)
    for fn in [t1_twinstate_fields, t2_layer_growth, t3_setup_animation,
               t4_apply_frame, t5_contact_bounds, t6_none_twin,
               t7_ribbon_edges, t8_ribbon_mesh, t9_ribbon_perpendicular]:
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
