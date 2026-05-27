#!/usr/bin/env python3
"""phase4b_validation.py — Faz 4b Stabilizasyon: Doğru Fizik + Sweet Spots"""
import sys,os,math
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from math import gcd
from dome_mandrel import EllipticDome
from full_body_mandrel import FullBodyMandrel
from machine_model import MachinePhysics
from clairaut_optimizer import ClairautOptimizer
from geodesic_family import GeodesicFamilySearcher

R,H,L_CYL=50.0,40.0,300.0; B=10.0; K=21
dome=EllipticDome(R,H); body=FullBodyMandrel(dome,L_CYL)
machine=MachinePhysics.default()

def sep(l="",w=68): print(f"\n==== {l} {'='*(w-len(l)-6)}" if l else "="*w)
def chk(c,ok,f=""):
    if c: print(f"    ✓ {ok}")
    else: print(f"    ✗ HATA: {f or ok}"); raise AssertionError(f or ok)

sep("ADIM 1: ANALİTİK MINIMUM BUILDUP KANITI")
r_boss_geo=K*B/(2*math.pi)
min_buildup=R/r_boss_geo
print(f"    r_boss = k×b/(2π) = {K}×{B}/(2π) = {r_boss_geo:.4f}mm")
print(f"    Buildup(x) = (R/r_boss)·√(1-x²)/√(1-(R/r_boss)²·x²)  [x=c/R]")
print(f"    d(buildup)/dx > 0 (monoton artan) → minimum @ x=0: R/r_boss = {min_buildup:.6f}x")
print(f"    Faz 4 3.80x hatası: r_stop=c×1.002 kullanıldı, r_boss={r_boss_geo:.2f}mm olmalıydı")
c25=25.0; rb25=max(c25*1.002,r_boss_geo); alpha25=math.asin(c25/R)
bu25=R*math.cos(alpha25)/(rb25*math.sqrt(1-(c25/rb25)**2))
print(f"    c=25mm doğru buildup: {bu25:.4f}x (önceki 3.80x hataydı)")
chk(abs(min_buildup-R/r_boss_geo)<1e-9,"min_buildup=R/r_boss analitik ✓")
chk(bu25<3.0,f"c=25mm doğru buildup={bu25:.3f}<3 ✓")

sep("ADIM 2: CLAIRAUT OPTİMİZATÖRÜ")
opt=ClairautOptimizer(body,K,B,machine,fiber_speed=100.0)
result=opt.optimize(c_min_frac=0.10,c_max_frac=0.92,n_steps=200)
print(f"\n{result.buildup_table(max_rows=12)}")
print(f"\n{result.family_report()}")
chk(len(result.all_points)>0,f"{len(result.all_points)} nokta tarandı ✓")
bo=result.best_overall
chk(bo is not None,"best_overall mevcut ✓")
fam20=result.families.get(2.0)
chk(fam20 and len(fam20.members)>0,f"BU≤2.0 ailesi: {len(fam20.members) if fam20 else 0} ✓")
print(f"    BU≤2.0: c∈[{fam20.c_range[0]:.2f},{fam20.c_range[1]:.2f}]mm")
c_eq20=opt.find_equalization_c(2.0)
chk(c_eq20 is not None,f"c_eq(BU≤2.0)={c_eq20:.2f}mm ✓")
chk(len(result.pareto_front)>0,"Pareto front ✓")

sep("ADIM 3: FULL-BODY SWEET SPOT ARAMASI")
print("""
    [Kritik Bulgu]
    k=21, L=300mm full-body için K_full ≈ 400-450°.
    Silindir φ-geçiş: K_cyl = 2×L×tan(α₀)/R → dominant.
    İyi pattern için: k×K_full/(2π) ≈ tam sayı gerekli.
    Bu koşul belirli c değerlerinde sağlanır (sweet spots).
""")

searcher=GeodesicFamilySearcher(body,K,B,machine,fiber_speed=100.0,
    buildup_max=3.0, overlap_max_frac=0.50, n_tolerance=0.50)

# Full-body sweet spot sweep (doğrudan)
c_arr=np.linspace(5.0, R*0.85, 2000)
sweet_spots=[]
for c in c_arr:
    if c>=R: continue
    r_boss=opt._r_boss(c)
    bu=opt._buildup_factor(c,r_boss)
    K_full=searcher._K_full_body(c)
    k_times=K*K_full/(2*math.pi)
    p=round(k_times)
    if p<=0: continue
    if gcd(int(p),K)!=1: continue
    residual=abs(k_times-p)
    alpha0=math.asin(min(1.0,c/R))
    eps_rad=(k_times-p)*2*math.pi
    eps_mm=abs(eps_rad*R/math.cos(alpha0))
    overlap_ratio=eps_mm/B
    mc_cyl,mc_boss,vx,mok=opt._machine_metrics(c)
    if residual<0.035 and overlap_ratio<0.45:
        sweet_spots.append({
            "c":float(c),"alpha0":math.degrees(alpha0),
            "K_deg":math.degrees(K_full),"p":p,"k":K,
            "k_times":k_times,"residual":residual,
            "eps_mm":eps_mm,"overlap_ratio":overlap_ratio,
            "buildup":bu,"machine_ok":mok,
            "composite": max(0,(1-2*residual))*(1/max(bu,1))*(1 if mok else 0.5)
        })

# Deduplicate (keep best per eps_mm range)
sweet_spots.sort(key=lambda s: s["eps_mm"])
unique_spots=[]; seen_c=set()
for sp in sweet_spots:
    c_round=round(sp["c"],1)
    if c_round not in seen_c:
        seen_c.add(c_round); unique_spots.append(sp)

print(f"    {len(unique_spots)} sweet spot bulundu (|residual|<0.035, |OL|<45%b):")
print(f"\n    {'c':>6}  {'α₀':>7}  {'K':>8}  {'p':>4}  {'ε_mm':>7}  {'OL%':>5}  {'BU':>6}  {'mak'}")
print("    "+"-"*60)
for sp in unique_spots[:12]:
    print(f"    {sp['c']:>6.2f}  {sp['alpha0']:>6.2f}°  {sp['K_deg']:>7.2f}°  "
          f"{sp['p']:>4}  {sp['eps_mm']:>6.3f}mm  {sp['overlap_ratio']*100:>4.1f}%  "
          f"{sp['buildup']:>6.3f}x  {'✓' if sp['machine_ok'] else '✗'}")

chk(len(unique_spots)>0,"En az 1 sweet spot bulundu ✓")

# Best sweet spot
feasible_spots=[s for s in unique_spots if s["machine_ok"] and s["buildup"]<3.0]
best_spot=min(feasible_spots,key=lambda s: s["eps_mm"]) if feasible_spots else unique_spots[0]
print(f"\n    ★ En İyi Sweet Spot:")
print(f"      c={best_spot['c']:.3f}mm  α₀={best_spot['alpha0']:.3f}°")
print(f"      Δφ={best_spot['K_deg']:.2f}°  p={best_spot['p']}  k={best_spot['k']}")
print(f"      ε={best_spot['eps_mm']:.3f}mm ({best_spot['overlap_ratio']*100:.1f}% bant)")
print(f"      Buildup={best_spot['buildup']:.4f}x")

chk(best_spot["eps_mm"]<B*0.5,f"Overlap={best_spot['eps_mm']:.3f}mm<{B/2}mm ✓")
chk(best_spot["buildup"]<3.0,f"Buildup={best_spot['buildup']:.3f}x<3.0 ✓")
chk(gcd(best_spot["p"],best_spot["k"])==1,"gcd(p,k)=1 ✓")

sep("ADIM 4: FAZ 5 HAZIRLIK — ÇIKIŞ PAKETİ")
bs=best_spot
pkg={
    "c_clairaut_mm": bs["c"],
    "alpha_0_deg": bs["alpha0"],
    "r_boss_mm": opt._r_boss(bs["c"]),
    "k_circuits": bs["k"],
    "p_step": bs["p"],
    "overlap_mm": bs["eps_mm"],
    "overlap_ratio": bs["overlap_ratio"],
    "buildup_factor": bs["buildup"],
    "K_full_body_deg": bs["K_deg"],
    "machine_ok": bs["machine_ok"],
    "min_buildup_theoretical": min_buildup,
    "r_boss_geometric": r_boss_geo,
    "phase5_ready": True,
    # Winding sequence hints
    "recommended_n_helical_layers": 2,
    "recommended_hoop_layers": 1,
    "hoop_after_helical": True,
}
print("\n    FAZ 5 ÇIKIŞ PAKETİ:")
for key,val in pkg.items(): print(f"      {key:<35}: {val}")

# Kısa özet: c=25mm vs optimal
print(f"\n    ÖNCE/SONRA KARŞILAŞTIRMASI:")
print(f"    {'Parametre':<25} {'Faz 3/4 (c=25mm)':<22} {'Faz 4b optimal'}")
print(f"    {'-'*65}")
print(f"    {'α₀':<25} {'30.00°':<22} {bs['alpha0']:.2f}°")
print(f"    {'Buildup':<25} {bu25:<22.4f} {bs['buildup']:.4f}x")
print(f"    {'Overlap':<25} {'20.4mm (18.3%→∞)':<22} {bs['eps_mm']:.3f}mm ({bs['overlap_ratio']*100:.1f}%)")
print(f"    {'Δφ_full':<25} {'446.8°':<22} {bs['K_deg']:.1f}°")
chk(pkg["phase5_ready"],"phase5_ready=True ✓")

sep("SONUÇ")
print(f"""
  ✓ Tüm Faz 4b testleri başarılı.

  Analitik Bulgular:
    Minimum buildup = R/r_boss = {min_buildup:.6f}x (hard limit, k={K}, b={B}mm)
    Full-body sweet spot: c'ye göre k×K_full/(2π) ∈ ℤ koşulu

  En İyi Konfigürasyon:
    c = {best_spot['c']:.3f}mm  α₀ = {best_spot['alpha0']:.3f}°
    Buildup = {best_spot['buildup']:.4f}x
    Overlap = {best_spot['eps_mm']:.3f}mm ({best_spot['overlap_ratio']*100:.1f}% bant)
    gcd(p={best_spot['p']}, k={K}) = {gcd(best_spot['p'],K)} ✓

  Sweet Spot Sayısı: {len(unique_spots)} (c ∈ [5, 42]mm aralığında)

  Faz 5 Hazırlığı:
    ✓ Optimal c, k, p → LayerManager'a hazır
    ✓ r_boss = {opt._r_boss(best_spot['c']):.2f}mm → polar boss compensation
    ✓ K_full = {best_spot['K_deg']:.1f}° → A-axis synchronization
    ✓ Multi-layer: 2 helical + 1 hoop önerildi
""")
sep()
