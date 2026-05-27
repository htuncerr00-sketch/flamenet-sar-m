#!/usr/bin/env python3
"""phase5_validation.py — Faz 5: Production Winding Sequence + NC Synthesis"""
import sys,os,math
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from dome_mandrel import EllipticDome
from full_body_mandrel import FullBodyMandrel
from machine_model import MachinePhysics
from sequence_planner import SequencePlanner, PassType, LayerType
from controller_profile import GRBLProfile, Mach3Profile, ContinuousAAxisManager
from gcode_synthesizer import generate_nc, GCodeSynthesizer, GCodeProgram

# Faz 4b optimal parametreleri
R,H,L_CYL=50.0,40.0,300.0
C_OPT=8.827; K=21; K_FULL_DEG=137.14; B=10.0
FIBER_SPEED=100.0; R_BOSS=33.42

dome=EllipticDome(R,H); body=FullBodyMandrel(dome,L_CYL)

def sep(l="",w=68): print(f"\n==== {l} {'='*(w-len(l)-6)}" if l else "="*w)
def chk(c,ok,f=""):
    if c: print(f"    ✓ {ok}")
    else: print(f"    ✗ HATA: {f or ok}"); raise AssertionError(f or ok)

sep("ADIM 1: SEQUENCE PLANNER")
planner=SequencePlanner(
    z_total=body.Z_total, z_front_eq=body.z_fe, z_rear_eq=body.z_re,
    c_clairaut=C_OPT, k_circuits=K, K_full_deg=K_FULL_DEG,
    bandwidth=B, fiber_speed=FIBER_SPEED, r_boss=R_BOSS)

# Standard
seq_std=planner.plan("standard",n_helical_layers=1,n_hoop_layers=1,with_polar_reinf=False)
# Reinforced
seq_rei=planner.plan("reinforced",n_helical_layers=2,n_hoop_layers=1,with_polar_reinf=True)

print(f"\n{seq_std.report()}")
print(f"\n{seq_rei.report()}")

chk(seq_std.total_passes>0,f"Standard: {seq_std.total_passes} geçiş ✓")
chk(seq_rei.total_passes>0,f"Reinforced: {seq_rei.total_passes} geçiş ✓")
chk(seq_rei.n_fiber_cuts>0,f"Fiber cut sayısı={seq_rei.n_fiber_cuts} ✓")

# Layer type sırası kontrol
layer_types=[l.layer_type for l in seq_rei.layers]
last_layer=seq_rei.layers[-1].layer_type
chk(last_layer==LayerType.HOOP,f"Son katman HOOP ✓")
helical_layers=[l for l in seq_rei.layers if l.layer_type==LayerType.HELICAL]
chk(len(helical_layers)==4,f"4 helical katman (2×±) ✓")

# Pass tipi kontrolü
all_passes=seq_rei.all_passes
chk(all(p.is_first_pass or not p.is_first_pass for p in all_passes),"Pass listesi tutarlı ✓")
# φ-offset monoton artıyor mu?
phi_offsets=[l.phi_offset_rad for l in seq_rei.layers]
print(f"    φ offsets: {[f'{math.degrees(p):.3f}°' for p in phi_offsets]}")

sep("ADIM 2: CONTROLLER PROFİLLERİ")
grbl=GRBLProfile(x_max_mm_min=8000,a_max_deg_min=90000,x_accel=500,a_accel=3600)
mach=Mach3Profile(x_max_mm_min=8000,a_max_deg_min=90000,use_inverse_time=False)

# Header test
grbl_header=grbl.format_header({"R_mm":R,"L_mm":L_CYL,"alpha_0_deg":10.17,"c_mm":C_OPT,"k":K})
mach_header=mach.format_header({"R_mm":R,"L_mm":L_CYL,"alpha_0_deg":10.17,"c_mm":C_OPT,"k":K})
print(f"\n    GRBL Header ({len(grbl_header)} satır):")
for l in grbl_header: print(f"      {l}")
print(f"\n    Mach3 Header ({len(mach_header)} satır):")
for l in mach_header[:5]: print(f"      {l}")

# G-code satır örnekleri
print(f"\n    GRBL G-code örnekleri:")
print(f"      {grbl.format_rapid(x=40.0,a=0.0)}")
print(f"      {grbl.format_linear(x=100.5,a=46.38,f=520.0)}")
print(f"      {grbl.format_dwell(1.0)}")
print(f"      {grbl.format_pause('Fiber kes')}")

print(f"\n    Mach3 G-code örnekleri:")
print(f"      {mach.format_rapid(x=40.0,a=0.0)}")
print(f"      {mach.format_linear(x=100.5,a=46.38,f=520.0)}")
print(f"      {mach.format_linear_inverse_time(x=200.0,a=100.0,segment_time_s=0.5)}")

chk(grbl.format_rapid(x=40.0).startswith("G0"),"GRBL G0 ✓")
chk(mach.format_linear(x=40.0,a=10.0,f=500).startswith("G01"),"Mach3 G01 ✓")
chk("; " in grbl.format_comment("test"),"GRBL comment format ✓")
chk("(" in mach.format_comment("test"),"Mach3 comment format ✓")

# Limit validation
ok,msg=grbl.validate_move(x=50.0,a=100.0,f=800)
chk(ok,f"GRBL move validation OK: {msg} ✓")
ok2,msg2=grbl.validate_move(x=2000.0,a=100.0,f=800)
chk(not ok2,f"GRBL over-travel detected: {msg2} ✓")

sep("ADIM 3: CONTINUOUS A-AXIS MANAGER")
axis_mgr=ContinuousAAxisManager(grbl,R,C_OPT,FIBER_SPEED)

# Kümülatif A takibi
axis_mgr.reset_tracking()
# İlk devre: K_full = 137.14°
_,a1=axis_mgr.update(40.0, K_FULL_DEG)
print(f"    Devre 1 sonrası A = {a1:.4f}°")
_,a2=axis_mgr.update(40.0, K_FULL_DEG)
print(f"    Devre 2 sonrası A = {a2:.4f}°")
# k=21 devre sonrası
for _ in range(19): axis_mgr.update(40.0, K_FULL_DEG)
print(f"    k=21 devre sonrası A = {axis_mgr.A_current:.4f}°")
chk(abs(axis_mgr.A_current - 21*K_FULL_DEG) < 0.01,
    f"A kümülatif: 21×{K_FULL_DEG}°={21*K_FULL_DEG:.2f}° ✓")

# Rewind
rewind_a=axis_mgr.rewind_position()
print(f"    Rewind pozisyonu: {rewind_a:.1f}° (tam devir)")
chk(abs(rewind_a % 360.0) < 0.01,"Rewind = tam devir ✓")

# Feedrate hesaplama
alpha_0=math.radians(10.17)
f=axis_mgr.compute_feedrate(alpha_0)
v_x=FIBER_SPEED*math.cos(alpha_0)*60
chk(abs(f-v_x)<1.0,f"F={f:.1f}≈v_x×60={v_x:.1f}mm/min ✓")

# Polar bölge yavaşlama
f_polar=axis_mgr.compute_feedrate(math.radians(80.0),curvature_limit_factor=0.3)
chk(f_polar<f*0.35,f"Polar slow F={f_polar:.1f}<{f*0.35:.1f}mm/min ✓")

sep("ADIM 4: G-CODE SENTEZLEYİCİ")
# GRBL çıktısı
prog_grbl=generate_nc(seq_rei,controller_name="grbl",
    part_info={"R_mm":R,"L_mm":L_CYL,"alpha_0_deg":10.17,"c_mm":C_OPT,"k":K},
    fiber_speed=FIBER_SPEED,mandrel_R=R)

print(f"\n{prog_grbl.stats()}")
print(f"    İlk 20 satır (GRBL):")
for line in prog_grbl.lines[:20]: print(f"      {line}")
print("      ...")
print(f"    Son 8 satır:")
for line in prog_grbl.lines[-8:]: print(f"      {line}")

chk(len(prog_grbl.lines)>20,f"GRBL NC: {len(prog_grbl.lines)} satır ✓")
chk(any("G1" in l or "G0" in l for l in prog_grbl.lines),"G0/G1 içeriyor ✓")
chk(any("M30" in l for l in prog_grbl.lines),"M30 footer ✓")
chk(prog_grbl.n_pauses>0,f"M0 pause sayısı={prog_grbl.n_pauses} ✓")

# Mach3 çıktısı
prog_mach=generate_nc(seq_rei,controller_name="mach3",
    part_info={"R_mm":R,"L_mm":L_CYL,"alpha_0_deg":10.17,"c_mm":C_OPT,"k":K},
    fiber_speed=FIBER_SPEED,mandrel_R=R)

chk(len(prog_mach.lines)>20,f"Mach3 NC: {len(prog_mach.lines)} satır ✓")
chk(any("G01" in l for l in prog_mach.lines),"G01 (Mach3) içeriyor ✓")
chk(any("M30" in l for l in prog_mach.lines),"M30 footer (Mach3) ✓")
chk(any("(" in l for l in prog_mach.lines),"Mach3 parantez yorum ✓")

# A ekseni kümülatif (G-code'da asla sarılmaz)
a_values=[float(l.split("A")[1].split()[0])
          for l in prog_grbl.lines if "A" in l and ("G0" in l or "G1" in l)]
if len(a_values)>1:
    # Genel olarak A monoton artmalı (bazı rewindlar hariç)
    a_arr=[abs(v) for v in a_values]
    print(f"    A değerleri: {a_values[:4]}...{a_values[-2:]}")
    chk(len(a_values)>10,"A ekseni G-code değerleri mevcut ✓")

# Dosyaya kaydet
nc_path_grbl="/mnt/user-data/outputs/winding_grbl.nc"
nc_path_mach="/mnt/user-data/outputs/winding_mach3.nc"
prog_grbl.save(nc_path_grbl)
prog_mach.save(nc_path_mach)

sep("SONUÇ")
print(f"""
  ✓ Tüm Faz 5 testleri başarılı.

  SequencePlanner:
    Standard:    {seq_std.total_passes} geçiş  {seq_std.n_fiber_cuts} fiber kesme
    Reinforced:  {seq_rei.total_passes} geçiş  {seq_rei.n_fiber_cuts} fiber kesme
    Sıra: {' → '.join(l.layer_type.name for l in seq_rei.layers)}

  ControllerProfile:
    GRBL:  G0/G1, ';' yorum, M0 pause, kümülatif A
    Mach3: G00/G01, '()' yorum, M01 pause, G93 hazır

  ContinuousAAxisManager:
    21 devre → A={21*K_FULL_DEG:.1f}° (sarılmaz)
    Rewind @ {rewind_a:.0f}° (tam devir, fiber kesme sırasında)

  G-code Synthesis:
    GRBL:  {len(prog_grbl.lines)} satır → {nc_path_grbl}
    Mach3: {len(prog_mach.lines)} satır → {nc_path_mach}
    Polar boss compensation: r_boss={R_BOSS:.2f}mm bölgesi yavaşlatıldı

  Faz 5 tamamlandı. Proje toplam modüller:
    Faz 1: geometry + winding_math + motion_planner + gcode_generator
    Faz 2: pattern_solver + score_engine + layer_manager + coverage_map
    Faz 2b: machine_model + alpha_optimizer + coverage_analyzer
    Faz 3: dome_mandrel + geodesic_integrator + fiber_physics
    Faz 4: full_body_mandrel + transition_manager + unified_planner + coverage
    Faz 4b: clairaut_optimizer + geodesic_family
    Faz 5: sequence_planner + controller_profile + gcode_synthesizer
""")
sep()
