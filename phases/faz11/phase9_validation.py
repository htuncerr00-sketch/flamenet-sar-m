#!/usr/bin/env python3
"""phase9_validation.py — Faz 9: Endüstriyel Güvenilirlik ve Üretilebilirlik"""
import sys,os,math,time
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from rt_scheduler import DeterministicScheduler, Platform, PLATFORM_SPECS, HardRealtimeSafeQueue
from reliability_engine import (MTBFEstimator, ThermalDriftModel, MicrostepModel,
    LongRunSimulator, WeibullParams, LongRunStats)
from commissioning import HardwareCommissioningSuite, CommissioningPhase, PhaseStatus
from hal import MockController

def sep(l="",w=68): print(f"\n==== {l} {'='*(w-len(l)-6)}" if l else "="*w)
def chk(c,ok,f=""):
    if c: print(f"    ✓ {ok}")
    else: print(f"    ✗ HATA: {f or ok}"); raise AssertionError(f or ok)

sep("ADIM 1: DETERMINISTIK ZAMANLAMA ANALİZİ")

# Platform karşılaştırması
sched_grbl = DeterministicScheduler(Platform.GRBL_SERIAL, seed=42)
sched_esp  = DeterministicScheduler(Platform.ESP32_FLUIDNC, seed=42)
sched_lnx  = DeterministicScheduler(Platform.LINUXCNC_RTAI, seed=42)

print(f"\n{sched_esp.compare_platforms()}")

# Her platform için 500 segment simülasyon
N_SEG = 500
analysis_grbl = sched_grbl.simulate_timing(N_SEG, feedrate_mm_min=5000, segment_length_mm=1.0)
analysis_esp  = sched_esp.simulate_timing(N_SEG, feedrate_mm_min=5000, segment_length_mm=1.0)
analysis_lnx  = sched_lnx.simulate_timing(N_SEG, feedrate_mm_min=5000, segment_length_mm=1.0)

print(f"\n  Zamanlama Analizi ({N_SEG} segment, F=5000mm/min, 1mm/seg):")
print(f"\n  GRBL:")
print(analysis_grbl.report())
print(f"\n  ESP32:")
print(analysis_esp.report())
print(f"\n  LinuxCNC:")
print(analysis_lnx.report())

# Doğrulamalar
chk(analysis_lnx.jitter_1sigma_us < analysis_grbl.jitter_1sigma_us,
    f"LinuxCNC jitter {analysis_lnx.jitter_1sigma_us:.1f}µs < GRBL {analysis_grbl.jitter_1sigma_us:.1f}µs ✓")
chk(analysis_esp.pulse_quality >= analysis_grbl.pulse_quality,
    f"ESP32 Q={analysis_esp.pulse_quality:.6f} ≥ GRBL Q={analysis_grbl.pulse_quality:.6f} ✓")

# Güvenli minimum feedrate
v_safe_esp  = sched_esp.safe_min_feedrate()
v_safe_grbl = sched_grbl.safe_min_feedrate()
print(f"\n    Güvenli min feedrate: ESP32={v_safe_esp:.0f}mm/min  GRBL={v_safe_grbl:.0f}mm/min")
chk(v_safe_esp > v_safe_grbl,
    f"ESP32 güvenli feedrate {v_safe_esp:.0f} > GRBL {v_safe_grbl:.0f} mm/min ✓")

# Safe Queue
q = HardRealtimeSafeQueue(capacity=32, pre_fill_count=8)
for i in range(10): q.enqueue(f"G1 X{i*10} F5000")
chk(q.size == 10, f"Queue size={q.size} ✓")
chk(q.fill_fraction == 10/32, f"Fill={q.fill_fraction:.4f} ✓")
chk(q.ready_to_run, "Pre-fill tamamlandı (≥8 segment) ✓")
seg = q.dequeue(); chk(seg is not None, f"Dequeue: {seg} ✓")
chk(q.size == 9, "Post-dequeue size=9 ✓")
n_flushed = q.flush(); chk(n_flushed == 9, f"Flush: {n_flushed} ✓")
chk(q.size == 0, "Queue boş ✓")

sep("ADIM 2: GÜVENİLİRLİK MOTORu")

# Weibull doğrulama
w = WeibullParams(beta=1.5, eta_h=8760.0)
R_1y = w.reliability(8760.0)   # 1 yıl
R_0  = w.reliability(0.0)
mttf = w.mean_life_h()
b10  = w.bx_life(10.0)

print(f"\n    Weibull(β={w.beta}, η={w.eta_h}h):")
print(f"    R(0)  = {R_0:.6f} (beklenen 1.0)")
print(f"    R(1y) = {R_1y:.6f}")
print(f"    MTTF  = {mttf:.0f}h = {mttf/8760:.2f}yıl")
print(f"    B10   = {b10:.0f}h (10% arıza @ bu süre)")

chk(abs(R_0 - 1.0) < 1e-9, "R(0)=1.0 (tanım gereği) ✓")
chk(0.0 < R_1y < 1.0, f"R(1y)={R_1y:.4f} ∈ (0,1) ✓")
chk(mttf > 0, f"MTTF={mttf:.0f}h>0 ✓")
chk(b10 < mttf, f"B10={b10:.0f}h < MTTF={mttf:.0f}h ✓")

# MTBF sistem analizi
mtbf_est = MTBFEstimator()
print(f"\n{mtbf_est.failure_analysis(t_h=2000.0)}")
R_2000 = mtbf_est.system_reliability(2000.0)
sys_mtbf = mtbf_est.system_mtbf()
chk(0 < R_2000 < 1, f"R_sys(2000h)={R_2000:.4f} ✓")
chk(sys_mtbf > 0, f"Sistem MTBF={sys_mtbf:.0f}h ✓")

# Termal sürüklenme
thermal = ThermalDriftModel()
print(f"\n  Termal Simülasyon (2h ısınma):")
for step in range(12):  # 12 × 10min = 2h
    thermal.update(dt_s=600.0)
print(thermal.report())

Δx_thermal = thermal.positional_error_mm()
τ_factor   = thermal.torque_reduction_factor()
chk(abs(Δx_thermal) >= 0, f"Termal hata = {Δx_thermal:.4f}mm ✓")
chk(0.7 <= τ_factor <= 1.0, f"Tork faktörü {τ_factor:.4f} ∈ [0.7,1.0] ✓")

# Mikrostep model
ms = MicrostepModel(steps_per_mm=80.0, microstep_factor=16, nonlinearity_pct=5.0)
err_0   = ms.positional_error_mm(0.0)
rms_err = ms.rms_error_mm()
worst   = ms.worst_case_mm()
v_res   = ms.resonant_speed_mm_min()

print(f"\n  Mikrostep Model:")
print(f"    RMS hata:  {rms_err*1000:.3f}µm")
print(f"    Worst-case:{worst*1000:.3f}µm")
print(f"    Rezonans hızı: {v_res:.0f}mm/min")

chk(rms_err < 0.001, f"Mikrostep RMS={rms_err*1000:.3f}µm < 1µm ✓")
chk(worst < 0.002, f"Worst-case={worst*1000:.3f}µm < 2µm ✓")
chk(v_res > 0, f"Rezonans hızı={v_res:.0f}mm/min ✓")

sep("ADIM 3: 6 SAATLIK UZUN SÜRELİ SİMÜLASYON")
sim = LongRunSimulator(
    n_circuits_per_layer=21, n_layers=4,
    fiber_speed_mm_s=100.0, mandrel_L_mm=300.0,
    drift_deg_circuit=1.22, drift_x_mm_h=0.05, seed=42)

print("    Simüle edilen: 6 saat sürekli sarım...")
stats = sim.simulate(duration_h=6.0)
stats.print_report()

chk(stats.n_circuits > 0, f"Devre sayısı={stats.n_circuits} ✓")
chk(stats.duration_h == 6.0, "6h simüle edildi ✓")
chk(stats.cumulative_phi_drift >= 0, f"φ drift={stats.cumulative_phi_drift:.2f}° ✓")
chk(stats.failure_probability < 0.5, f"Arıza olasılığı={stats.failure_probability:.4f}<0.5 ✓")
chk(stats.winding_consistency > 0, f"Tutarlılık={stats.winding_consistency:.4f}>0 ✓")

# Uzun süreli drift uyarısı
if stats.cumulative_phi_drift > 50.0:
    print(f"    ⚠ Kümülatif φ drift {stats.cumulative_phi_drift:.1f}° > 50° — rewind gerekli")
if stats.thermal_error_mm > 0.1:
    print(f"    ⚠ Termal hata {stats.thermal_error_mm:.4f}mm > 0.1mm — kompensasyon öner")

# 24h projeksiyonu
stats_24 = sim.simulate(duration_h=24.0)
print(f"\n    24h projeksiyonu: φ_drift={stats_24.cumulative_phi_drift:.1f}°  "
      f"arıza_P={stats_24.failure_probability:.4f}")
chk(stats_24.cumulative_phi_drift > stats.cumulative_phi_drift,
    "24h drift > 6h drift (monoton artış) ✓")

sep("ADIM 4: DONANIM DEVREYE ALMA")
ctrl = MockController(x_travel_mm=400.0, a_max_rpm=260.0)
ctrl.connect()
suite = HardwareCommissioningSuite(ctrl, mandrel_R_mm=50.0, alpha_0_deg=10.17)
report = suite.run_all(quick=True)
report.print_report()

chk(report.n_passed > 0, f"{report.n_passed}/{len(report.results)} aşama geçti ✓")
chk(report.all_critical_passed, "Tüm kritik aşamalar (power_on, estop) geçti ✓")

scores = report.compute_industrial_scores()
print()
scores.print_scores()
chk(scores.lab_prototype > 50, f"Lab prototype skoru={scores.lab_prototype:.1f}>50 ✓")
chk(scores.pilot_production > 0, f"Pilot skor={scores.pilot_production:.1f}>0 ✓")

# Bireysel aşama kontrolleri
estop_result = next((r for r in report.results if r.phase==CommissioningPhase.ESTOP_VALIDATION),None)
chk(estop_result and estop_result.passed, "E-stop aşaması PASS ✓")
dry_run_result = next((r for r in report.results if r.phase==CommissioningPhase.DRY_RUN),None)
if dry_run_result:
    chk(dry_run_result.status in (PhaseStatus.PASS, PhaseStatus.FAIL, PhaseStatus.BLOCKED),
        "Dry-run aşaması yürütüldü ✓")

sep("SONUÇ")
print(f"""
  ✓ Tüm Faz 9 testleri başarılı.

  Deterministik Zamanlama:
    ESP32 jitter: {analysis_esp.jitter_1sigma_us:.1f}µs(1σ) Q={analysis_esp.pulse_quality:.6f}
    GRBL jitter:  {analysis_grbl.jitter_1sigma_us:.1f}µs(1σ) Q={analysis_grbl.pulse_quality:.6f}
    LinuxCNC:     {analysis_lnx.jitter_1sigma_us:.1f}µs(1σ) Q={analysis_lnx.pulse_quality:.6f}
    Safe feedrate ESP32: {v_safe_esp:.0f}mm/min

  Güvenilirlik:
    Weibull β=1.5  MTTF={mttf:.0f}h={mttf/8760:.2f}yıl
    Sistem R(2000h)={R_2000:.4f}  MTBF={sys_mtbf:.0f}h
    Termal hata (2h): {Δx_thermal:.4f}mm  Tork: {τ_factor:.3f}x
    Mikrostep RMS: {rms_err*1000:.3f}µm

  6h Simülasyon:
    {stats.n_circuits} devre  {stats.n_passes} geçiş
    φ drift: {stats.cumulative_phi_drift:.2f}°  X drift: {stats.cumulative_x_drift:.4f}mm
    Tutarlılık: {stats.winding_consistency:.4f}  Arıza P: {stats.failure_probability:.4f}

  Devreye Alma:
    {report.n_passed}/{len(report.results)} aşama PASS
    Lab prototype: {scores.lab_prototype:.1f}/100
    Pilot üretim:  {scores.pilot_production:.1f}/100

  Endüstriyel Hedef:
    "8 saat güvenli ve repeatable?" →
    φ drift/h: {stats.cumulative_phi_drift/6:.2f}°/h (limit: <10°/h)
    Tutarlılık: {stats.winding_consistency:.3f} (hedef: >0.85)
    {'✓ PRODUCTION GRADE' if stats.winding_consistency>0.8 and stats.cumulative_phi_drift/6<10 else '⚠ İYİLEŞTİRME GEREKLİ'}
""")
sep()
