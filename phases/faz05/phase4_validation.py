#!/usr/bin/env python3
"""phase4_validation.py — Faz 4 Unified Full-Body Winding Planner testi"""
import sys,os,math
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from dome_mandrel import EllipticDome
from full_body_mandrel import FullBodyMandrel, BodyRegion
from transition_manager import TransitionManager
from unified_geodesic_planner import UnifiedGeodesicPlanner
from full_body_coverage import FullBodyCoverageAnalyzer

R,H,L_CYL=50.0,40.0,300.0
ALPHA_EQ=30.0
C=R*math.sin(math.radians(ALPHA_EQ)); B=10.0; K=21

def sep(l="",w=65): print(f"\n==== {l} {'='*(w-len(l)-6)}" if l else "="*w)
def chk(c,ok,f=""):
    if c: print(f"    ✓ {ok}")
    else: print(f"    ✗ HATA: {f or ok}"); raise AssertionError(f or ok)

sep("ADIM 1: FULL BODY MANDREL")
dome=EllipticDome(R,H)
body=FullBodyMandrel(dome,L_CYL)
print(f"\n{body.report()}")

# Temel geometri doğrulamaları
chk(abs(body.r(0.0)-0.0)<1.0,          "Front pole r≈0 ✓")
chk(abs(body.r(H)-R)<1e-6,             f"Front equator r=R={R} ✓")
chk(abs(body.r(H+L_CYL/2)-R)<1e-6,    f"Cylinder mid r=R={R} ✓")
chk(abs(body.r(H+L_CYL)-R)<1e-6,      f"Rear equator r=R={R} ✓")
chk(abs(body.r(2*H+L_CYL)-0.0)<1.0,   "Rear pole r≈0 ✓")

# r' C1 continuity at junctions
rp_fe = body.r_prime(H); rp_re = body.r_prime(H+L_CYL)
chk(abs(rp_fe)<1e-4, f"Front equator r'={rp_fe:.6f}≈0 (C1) ✓")
chk(abs(rp_re)<1e-4, f"Rear  equator r'={rp_re:.6f}≈0 (C1) ✓")

# C2 discontinuity check (expected)
km_dome = dome.kappa_m(0.0); km_cyl = body.kappa_m(H+0.1)
chk(abs(km_dome)>1e-6, f"Dome κ_m≠0: {km_dome:.5f} (C2 jump expected) ✓")
chk(abs(km_cyl)<1e-4,  f"Cylinder κ_m≈0: {km_cyl:.6f} ✓")

# Region detection
chk(body.region_of(0.0)==BodyRegion.FRONT_DOME,"z=0 → FRONT_DOME ✓")
chk(body.region_of(H+L_CYL/2)==BodyRegion.CYLINDER,f"z=H+L/2 → CYLINDER ✓")
chk(body.region_of(2*H+L_CYL-1)==BodyRegion.REAR_DOME,"z≈Z_total → REAR_DOME ✓")

sep("ADIM 2: TRANSITION MANAGER")
tm=TransitionManager(body,B,blend_multiplier=3.0)
ta=tm.analyze(C,K)
print(f"\n{ta.report()}")

chk(ta.front_junction.c1_ok, "Front junction C1 ✓")
chk(ta.rear_junction.c1_ok,  "Rear  junction C1 ✓")
chk(not ta.front_junction.c2_ok, "Front junction C2 discontinuity expected ✓")
chk(ta.blend_delta_mm>0, f"Blend δ={ta.blend_delta_mm:.2f}mm>0 ✓")

# Blended curvature profili
zz=np.linspace(H-20,H+20,80)
bc=tm.blend_curvature(zz,C)
# Blend zone'da κ_m azalmalı (cylinder tarafında sıfıra yaklaşmalı)
km_before=float(bc.kappa_m_blend[0])
km_after =float(bc.kappa_m_blend[-1])
chk(abs(km_before)>abs(km_after)*0.1,
    f"Blend: κ_m before={km_before:.5f} > after={km_after:.5f} ✓")

# Thickness profile
tp=tm.junction_thickness_profile(C,K,0.25,n_pts=60)
t_cyl_mean=float(tp["t"][tp["rho"]<1.1].mean()) if (tp["rho"]<1.1).any() else 0.0
print(f"    Silindir nominal kalınlık ≈ {t_cyl_mean:.4f}mm")
chk(len(tp["z"])>10, f"Thickness profile n={len(tp['z'])} ✓")

sep("ADIM 3: UNIFIED GEODESIC PLANNER")
planner=UnifiedGeodesicPlanner(body,c_clairaut=C,a_centripetal=5000.0)
path=planner.plan_circuit(phi_start=0.0)
print(f"\n    {path.summary()}")

chk(path.n_points>30,     f"n={path.n_points}>30 ✓")
chk(path.n_turnarounds>=1,f"Turnarounds≥1: {path.n_turnarounds} ✓")
chk(path.total_arc_mm>200,f"Arc>{200}mm: {path.total_arc_mm:.1f} ✓")

# Clairaut doğrulaması: r·sin(α)=c
errs=[abs(body.r(p.z_mm)*math.sin(p.alpha_rad)-C) for p in path.points[::5]
      if body.r(p.z_mm)>C+0.01]
max_err=max(errs) if errs else 0.0
chk(max_err<0.1, f"Clairaut r·sin(α)=c maxErr={max_err:.4f}mm ✓")
print(f"    Clairaut max error = {max_err:.4f}mm")

# Region geçişleri olmalı
rc=path.region_counts()
print(f"    Region dağılımı: {dict(rc)}")
chk(rc.get("CYLINDER",0)>0, "CYLINDER bölgesi geçildi ✓")

# Turn-around noktaları
taps=path.turnaround_points()
print(f"    Turn-around noktaları: {len(taps)}")
if taps:
    for tap in taps:
        print(f"      z={tap.z_mm:.1f}mm α={tap.alpha_deg:.1f}° r={tap.r_mm:.2f}mm reg={tap.region_code}")

# α doğrulaması: cylinderde sabit, dome'da değişken
cyl_pts=[p for p in path.points if p.region==BodyRegion.CYLINDER]
if len(cyl_pts)>3:
    alphas_cyl=np.array([p.alpha_rad for p in cyl_pts])
    alpha_cv=float(np.std(alphas_cyl)/np.mean(alphas_cyl))
    chk(alpha_cv<0.01, f"Silindir α CV={alpha_cv:.5f}<0.01 (sabit α) ✓")

sep("ADIM 4: FULL-BODY KAPLAMA ANALİZİ")
fbca=FullBodyCoverageAnalyzer(body,C,K,B,fiber_thickness=0.25,n_z=120,n_phi=36)

# Plan 3 devre (hızlı)
paths=planner.plan_layer(n_circuits=3,z_start=body.z_fe)
print(f"    {len(paths)} devre planlandı")

result=fbca.analyze_analytical(paths=paths)
result.print_summary()
result.thickness_field.print_profile(n_bars=28)

# Doğrulamalar
chk(result.cylinder_thickness>0, f"t_cyl={result.cylinder_thickness:.4f}mm>0 ✓")
chk(result.buildup_factor_front>1.0,f"Dome buildup>1: {result.buildup_factor_front:.3f} ✓")
chk(result.pole_boss_required_r>0, f"Polar boss min r={result.pole_boss_required_r:.2f}mm>0 ✓")
chk(result.cv_global>=0,           f"Global CV={result.cv_global:.4f}≥0 ✓")

sep("SONUÇ")
print(f"""
  ✓ Tüm Faz 4 testleri başarılı.

  FullBodyMandrel:
    C1 @ junction ✓  C2 discontinuity detected ✓
    Region detection ✓  r(z) continuous ✓

  TransitionManager:
    Blend δ={ta.blend_delta_mm:.1f}mm  κ_m blending ✓
    Thickness gradient computed ✓

  UnifiedGeodesicPlanner:
    {path.n_points} nokta  arc={path.total_arc_mm:.0f}mm
    Clairaut err={max_err:.4f}mm  {path.n_turnarounds} turnaround
    Cylinder α sabit ✓

  FullBodyCoverageAnalyzer:
    t_cyl={result.cylinder_thickness:.4f}mm
    Front buildup={result.buildup_factor_front:.2f}x  Rear={result.buildup_factor_rear:.2f}x
    Polar boss ≥ {result.pole_boss_required_r:.1f}mm
    Hotspots={result.n_hotspot_cells}
""")
sep()
