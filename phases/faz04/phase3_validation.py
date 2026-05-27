#!/usr/bin/env python3
import sys,os,math
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from dome_mandrel import EllipticDome, SphericalDome
from geodesic_integrator import GeodesicPathIntegrator
from fiber_physics_and_constraints import FiberPhysics, CurvatureAwareFeedrateAdapter, ProcessConstraints, DomeCoverageAnalyzer
from machine_model import MachinePhysics

R,H_ELL=50.0,40.0; ALPHA_EQ=30.0
C=R*math.sin(math.radians(ALPHA_EQ)); B=10.0; K=21

def sep(l="",w=65): print(f"\n==== {l} {'='*(w-len(l)-6)}" if l else "="*w)
def chk(c,ok,f=""):
    if c: print(f"    ✓ {ok}")
    else: print(f"    ✗ HATA: {f or ok}"); raise AssertionError(f or ok)

sep("ADIM 1: DOME MANDREL GEOMETRİSİ")
sph=SphericalDome(R); ell=EllipticDome(R,H_ELL)
print(f"\n  [Küresel Dome R={R}mm]\n{sph.report()}")

for z in [0.0,R*0.3,R*0.6,R*0.85]:
    km=sph.kappa_m(z); kc=sph.kappa_c(z)
    chk(abs(km-1/R)<1e-6,f"Küre z={z:.1f}: κ_m={km:.6f}=1/R ✓")
    chk(abs(kc-1/R)<1e-6,f"Küre z={z:.1f}: κ_c={kc:.6f}=1/R ✓")

print(f"\n  [Eliptik Dome R={R}mm H={H_ELL}mm]\n{ell.report()}")
km0=ell.kappa_m(0.0); kc0=ell.kappa_c(0.0)
chk(abs(km0-R/H_ELL**2)<1e-6,f"Eliptik z=0: κ_m={km0:.6f}=R/H²={R/H_ELL**2:.6f} ✓")
chk(abs(kc0-1/R)<1e-6,f"Eliptik z=0: κ_c={kc0:.6f}=1/R ✓")

for z in [0.0,R*0.3,R*0.6]:
    r=sph.r(z); G=sph.G(z); Ge=R**2/r**2
    chk(abs(G-Ge)<1e-6,f"Küre G(z={z:.0f})=R²/r²={G:.6f} ✓")

for z in [0.0,H_ELL*0.4,H_ELL*0.7]:
    is_b,kn,_=ell.bridging_check(z,math.radians(ALPHA_EQ))
    chk(not is_b,f"Eliptik z={z:.1f}: κ_n={kn:.5f}>0 ✓")

sep("ADIM 2: GEODEZİK YOL İNTEGRATÖRÜ")
print(f"    c=R·sin({ALPHA_EQ}°)={C:.4f}mm")
gi=GeodesicPathIntegrator(ell,c_clairaut=C,a_centripetal_max=5000.0)
path=gi.integrate_circuit(phi_start=0.0,z_start=0.0,s_max_half=2500.0)
print(f"\n    {path.summary()}")

chk(path.n_points>20,f"Path n={path.n_points}>20 ✓")
chk(path.total_arc_length_mm>0,f"Arc={path.total_arc_length_mm:.1f}mm ✓")
chk(path.z_turnaround>0,f"z_ta={path.z_turnaround:.2f}mm>0 ✓")
chk(path.phi_total_rad>0,f"Δφ={path.phi_total_deg:.2f}°>0 ✓")

errs=[abs(ell.r(p.z_mm)*math.sin(p.alpha_rad)-C) for p in path.points[::5]]
max_err=max(errs)
chk(max_err<1e-3,f"Clairaut r·sin(α)=c maxErr={max_err:.2e}mm ✓")
print(f"    Clairaut max error = {max_err:.2e} mm")

ta=next((p for p in path.points if p.is_turnaround),None)
if ta: chk(ta.alpha_deg>80.0,f"Turn-around α={ta.alpha_deg:.2f}°>80° ✓")

sep("ADIM 3: FİBER FİZİĞİ")
phys=FiberPhysics(ell,mu_friction=0.2,tension_N=15.0)
stab=phys.analyze_path(path,is_geodesic=True)
print(f"\n{stab.report()}")
chk(stab.overall_stable,"Fiber stabilite STABLE ✓")
chk(stab.max_slip_tendency==0.0,"Geodezik: slip=0 ✓")

cong=phys.polar_congestion_analysis(C,K,B)
print(f"    Congestion onset: z={cong['congestion_onset_z']:.1f}mm")
print(f"    Polar boss min r = {cong['polar_boss_min_r']:.2f}mm")
finite_rho=cong['rho'][np.isfinite(cong['rho'])]
print(f"    ρ max (finite) = {float(finite_rho.max()):.2f}")
chk(cong["polar_boss_min_r"]>0,f"Polar boss min r>0 ✓")

sep("ADIM 4: EĞRİLİK-UYARLAMALI HIZ PROFİLİ")
machine=MachinePhysics.default()
adapter=CurvatureAwareFeedrateAdapter(ell,machine,C,v_target=100.0,a_centripetal=200.0,polar_slowdown_factor=0.3)
fr=adapter.compute_profile(path)
print(f"\n{fr.summary()}")
chk(len(fr.v_arr)>0,f"Hız profili n={len(fr.v_arr)} ✓")
chk(fr.v_min>0,f"v_min={fr.v_min:.2f}>0 ✓")
chk(fr.v_min<99.9,f"Curvature-aware hız düşüşü v_min={fr.v_min:.2f}<100 ✓")
viols=fr.constraint_violations(machine.carriage_max_speed_mm_s(),machine.spindle_max_speed_rpm())
print(f"    Hız ihlali:{viols['v_exceed']}  RPM ihlali:{viols['rpm_exceed']}")

sep("ADIM 5: SÜREÇ KISITLARI")
pc=ProcessConstraints(ell,machine,phys)
rpt=pc.evaluate(path,fr,K,B)
print(f"\n{rpt.report()}")
chk(rpt.max_kappa_n>0,f"Max κ_n>0: {rpt.max_kappa_n:.5f} ✓")
chk(rpt.bridging_zones==0,"Bridging=0 ✓")
chk(rpt.pole_boss_min_r_mm>0,f"Polar boss min r={rpt.pole_boss_min_r_mm:.2f}mm ✓")

sep("ADIM 6: DOME KAPLAMA ANALİZİ")
ca=DomeCoverageAnalyzer(ell,C,K,B,fiber_thickness=0.25)
cr=ca.analyze(n_zones=60)
print(f"\n{cr.summary()}")
cr.print_thickness_profile(n_bars=18)
chk(len(cr.z_arr)>5,f"Zon sayısı={len(cr.z_arr)}>5 ✓")
chk(cr.pole_rho_max>1.0,f"Pol ρ_max={cr.pole_rho_max:.2f}>1 ✓")
chk(cr.rho_arr[-1]>=cr.rho_arr[0],f"Pol yoğunluğu ≥ ekvatör ✓")

sep("SONUÇ")
print(f"""
  ✓ Tüm Faz 3 testleri başarılı.
    Clairaut maxErr={max_err:.2e}mm
    Turn-around α={ta.alpha_deg:.2f}° (pol)
    Geodezik λ=0, bridging=0
    Polar boss min r={rpt.pole_boss_min_r_mm:.2f}mm
    Pol hız düşüşü v_min={fr.v_min:.1f}mm/s
""")
sep()
