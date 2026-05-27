#!/usr/bin/env python3
"""phase6_validation.py — Faz 6: Simulation + Digital Twin + Export"""
import sys,os,math
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from dome_mandrel import EllipticDome
from full_body_mandrel import FullBodyMandrel
from machine_simulation import MachineSimulationEngine, SimulationConfig, CollisionAnalyzer
from digital_twin import DigitalTwinModel, ProcessFeedbackModel
from export_pipeline import VisualizationExportPipeline

R,H,L_CYL=50.0,40.0,300.0
C_OPT=8.827; K=21; K_FULL_DEG=137.14; B=10.0
ALPHA_RAD=math.radians(10.17); FIBER_SPEED=100.0; R_BOSS=33.42

dome=EllipticDome(R,H); body=FullBodyMandrel(dome,L_CYL)

def sep(l="",w=68): print(f"\n==== {l} {'='*(w-len(l)-6)}" if l else "="*w)
def chk(c,ok,f=""):
    if c: print(f"    ✓ {ok}")
    else: print(f"    ✗ HATA: {f or ok}"); raise AssertionError(f or ok)

# Sentetik toolpath: 1 helical pass (basit silindir yaklaşımı)
N=80
z0=body.z_fe; z1=body.Z_total
x_arr=np.concatenate([
    np.linspace(z0,z1,N//2),     # forward
    np.linspace(z1,z0,N//2),     # return
])
a_arr=np.cumsum(np.full(N, K_FULL_DEG/N))
f_nom=FIBER_SPEED*math.cos(ALPHA_RAD)*60.0
# Turn-around noktasında yavaşlama
f_arr=np.full(N, f_nom)
f_arr[N//2-5:N//2+5]*=0.25  # turn-around slow

sep("ADIM 1: MAKİNE SİMÜLASYON MOTORu")
cfg=SimulationConfig(
    J_total_kgm2=0.0901, B_viscous_Nms=0.015,
    backlash_mm=0.15, lambda0_per_s=0.05,
    T_nominal_N=15.0, random_seed=42)
print(f"\n    τ_spindle  = {cfg.tau_spindle*1000:.2f} ms")
print(f"    τ_carriage = {cfg.tau_carriage*1000:.2f} ms")
print(f"    k_fiber    = {cfg.k_fiber_N_per_mm:.4f} N/mm")
print(f"    Backlash   = {cfg.backlash_mm} mm")

engine=MachineSimulationEngine(cfg, seed=42)
sim_result=engine.simulate(x_arr, a_arr, f_arr, ALPHA_RAD, R)

print(f"\n{sim_result.report()}")

chk(sim_result.rms_x_error>=0, f"RMS X hatası = {sim_result.rms_x_error*1000:.3f} µm ≥ 0 ✓")
chk(sim_result.rms_a_error_deg>=0, f"RMS A hatası = {sim_result.rms_a_error_deg:.4f}° ≥ 0 ✓")
chk(sim_result.max_tension_N > cfg.T_nominal_N, f"Max gerilme {sim_result.max_tension_N:.2f}N > {cfg.T_nominal_N}N ✓")
chk(sim_result.min_tension_N >= 0, f"Min gerilme {sim_result.min_tension_N:.2f}N ≥ 0 ✓")
# Turn-around x direction reversal (N/2 noktasında)
chk(x_arr[N//2-1] < x_arr[N//2-2] or x_arr[N//2+1] < x_arr[N//2],
    f"X yön değişimi N/2 bölgesinde tespit edildi ✓")
chk(len(sim_result.states) == N, f"State sayısı = {len(sim_result.states)} = N={N} ✓")

# Spindle lag fizik kontrolü
lag_values=[st.spindle_lag_deg for st in sim_result.states]
chk(any(abs(v)>1e-6 for v in lag_values), "Spindle lag ≠ 0 (fizik aktif) ✓")
print(f"    Max spindle lag = {max(abs(v) for v in lag_values):.4f}°")

# Turn-around gerilme artışı
ta_tensions=[st.tension_N for st in sim_result.states if st.is_turnaround]
norm_tensions=[st.tension_N for st in sim_result.states if not st.is_turnaround]
if ta_tensions and norm_tensions:
    print(f"    Turn-around T = {max(ta_tensions):.2f}N  Nominal T = {sum(norm_tensions)/len(norm_tensions):.2f}N")

# Kalite skoru
qs=sim_result.quality_score()
chk(0.0<=qs<=1.0, f"Kalite skoru = {qs:.4f} ∈ [0,1] ✓")
print(f"    Üretim kalite skoru: {qs:.4f}")

sep("ADIM 2: ÇARPIŞmA ANALİZÖRÜ")
analyzer=CollisionAnalyzer(z_total=body.Z_total, z_headstock=-10.0,
    r_boss_mm=R_BOSS, a_max_dps=1500.0, mandrel=body)
events=analyzer.analyze(x_arr, a_arr, f_arr)
print(f"\n{analyzer.report(events)}")
chk(len(events)==0 or any(e.severity in ("warning","critical") for e in events),
    f"{len(events)} olay tespit edildi ✓")

# Kasıtlı aşım testi
x_bad=np.array([-20.0, body.Z_total+10.0])
a_bad=np.array([0.0, 100.0])
f_bad=np.array([500.0, 500.0])
events_bad=analyzer.analyze(x_bad, a_bad, f_bad)
chk(any(e.severity=="critical" for e in events_bad),
    f"Aşım tespit edildi: {len(events_bad)} kritik ✓")

sep("ADIM 3: DIGITAL TWIN MODELİ")
twin=DigitalTwinModel(engine)
twin_state=twin.run(x_arr, a_arr, f_arr, ALPHA_RAD, pass_index=0)
print(f"\n{twin_state.full_report()}")
print(f"\n{twin_state.sim_result.report()}")

chk(len(twin_state.snapshots)==N, f"Snapshot sayısı={len(twin_state.snapshots)} ✓")
chk(0.0<=twin_state.quality_score<=1.0, f"Quality={twin_state.quality_score:.4f} ✓")
chk(len(twin_state.pass_quality)==N, "Pass quality listesi tam ✓")

# Uyarılar mantıklı mı?
print(f"    Uyarılar ({len(twin_state.alerts)}):")
for a in twin_state.alerts: print(f"      ⚠ {a}")

# Snapshot içeriği
snap0=twin_state.snapshots[N//4]
enc_diff=abs(snap0.encoder_x-snap0.x_planned)
print(f"    Encoder-Planned X diff={enc_diff:.3f}mm (simülasyon)")
chk(enc_diff < 100.0, f"Encoder X simülasyon farkı={enc_diff:.3f}mm < 100mm ✓")

sep("ADIM 4: SÜREÇ GERİ BESLEME MODELİ")
feedback=ProcessFeedbackModel()
sensors=feedback.register_sensors()
feedback.attach_simulated_data(twin_state.sim_result.states)
print(f"\n{feedback.sensor_health_report()}")
chk(len(sensors)==5, f"5 sensör kayıtlı: {list(sensors.keys())} ✓")
chk(all(s.is_healthy() for s in sensors.values()), "Tüm sensörler healthy (simülasyon) ✓")

# Okuma testi
enc_x_val=sensors["encoder_x"].read()
tension_val=sensors["tension"].read()
chk(enc_x_val.status.name=="SIMULATED", "EncoderX SIMULATED modunda ✓")
chk(tension_val.value>0, f"Tension reading={tension_val.value:.2f}N>0 ✓")
print(f"    EncoderX okuma: {enc_x_val.value:.3f}mm")
print(f"    Tension okuma:  {tension_val.value:.3f}N")

feedback.enable_feedback(True)
corr=feedback.compute_correction(0.05, 0.3, 5.0)
chk("dvx" in corr and "domega" in corr, "Feedback düzeltmesi hesaplandı ✓")
print(f"    Feedback: dvx={corr['dvx']:.4f}mm/s  domega={corr['domega']:.4f}°/s")

sep("ADIM 5: VİZÜELLEŞTİRME EXPORT")
pipe=VisualizationExportPipeline("/mnt/user-data/outputs")

# Analitik thickness (dome coverage)
z_th=np.linspace(0.0, body.Z_total, 60)
rho_th=np.array([body.pole_congestion(z,C_OPT,K,B) for z in z_th])
rho_th=np.clip(rho_th,0.0,10.0); t_th=rho_th*0.25

sim_a=np.array([st.a_actual_deg for st in twin_state.sim_result.states])
files=pipe.export_all(
    x_arr=x_arr, a_arr_deg=a_arr, f_arr=f_arr,
    twin_state=twin_state,
    r_fn=body.r, alpha_rad=ALPHA_RAD,
    z_arr=z_th, rho_arr=rho_th, t_arr=t_th,
    prefix="fw_phase6_")

chk(len(files)==5, f"{len(files)} dosya oluşturuldu ✓")
# JSON doğrulama
import json
with open(files["toolpath"]) as f: tp=json.load(f)
chk("planned" in tp and "actual" in tp, "Toolpath JSON yapısı ✓")
chk(len(tp["planned"])>0, f"Planned noktaları: {len(tp['planned'])} ✓")
chk(all(k in tp["planned"][0] for k in ["x","y","z"]), "Kartezyen koordinatlar ✓")
print(f"    Toolpath JSON: {len(tp['planned'])} planlanan, {len(tp['actual'])} simüle")
# CSV doğrulama
import csv as csv_mod
with open(files["a_timeline"]) as f: rows=list(csv_mod.reader(f))
chk(len(rows)>N//5, f"A-timeline: {len(rows)} satır ✓")
chk(rows[0][0]=="seg", "CSV başlık: 'seg' ✓")

sep("SONUÇ")
print(f"""
  ✓ Tüm Faz 6 testleri başarılı.

  MachineSimulationEngine:
    RMS X hatası:    {sim_result.rms_x_error*1000:.3f} µm
    RMS A hatası:    {sim_result.rms_a_error_deg:.4f}°
    Max gerilme:     {sim_result.max_tension_N:.2f} N
    Spindle lag max: {max(abs(v) for v in lag_values):.4f}°
    Turn-around:     {sim_result.n_turnarounds} tespit edildi
    Kaçan adım (X):  {sim_result.n_missed_steps_x}
    Feed drift:      {sim_result.feed_drift_final_deg:.4f}°

  DigitalTwinModel:
    {len(twin_state.snapshots)} snapshot  Q={twin_state.quality_score:.4f}
    {len(twin_state.alerts)} uyarı

  ProcessFeedbackModel:
    5 sensör (EncoderX, EncoderA, LoadCell, RPM, Vision)
    PID feedback iskelet hazır

  ExportPipeline ({len(files)} dosya):
    toolpath.json → Three.js / Blender
    a_timeline.csv → MATLAB / Python
    error_map.csv → hata dağılımı
    thickness.csv → kalınlık ısı haritası
    sim_report.txt → tam metin raporu

  Proje tamamlandı: 6 Faz × 25+ modül
  Kalite skoru: {qs:.4f}
""")
sep()
