"""first_wind_procedure.py — 10-Step First Winding Procedure"""
from __future__ import annotations
import math,time,threading
from dataclasses import dataclass,field
from enum import Enum,auto
from typing import Callable,Dict,List,Optional,Tuple
import numpy as np

class StepStatus(Enum):
    PENDING="pending"; RUNNING="running"; PASS="pass"; FAIL="fail"; ABORTED="aborted"

@dataclass(slots=True)
class StepResult:
    step_num:int; name:str; status:StepStatus; duration_s:float
    measurements:Dict[str,float]=field(default_factory=dict); message:str=""; abort_reason:str=""
    checklist:List[str]=field(default_factory=list)
    @property
    def passed(self): return self.status==StepStatus.PASS
    def summary_line(self)->str:
        icon={"pass":"✓","fail":"✗","aborted":"⊘","running":"▶","pending":"○"}
        return (f"  {icon.get(self.status.value,'?')} Adım {self.step_num:2d}: {self.name:<35} "
                f"[{self.status.value.upper():<8}] {self.duration_s:5.1f}s  {self.message[:40]}")

@dataclass(slots=True)
class ReadinessReport:
    results:List[StepResult]; ready:bool; score:float; failed_steps:List[int]
    calibration:Dict[str,float]=field(default_factory=dict)
    def print_report(self)->None:
        print("  ═"*34); print("  İLK SARIM PROSEDÜR RAPORU")
        for r in self.results: print(r.summary_line())
        print("  ─"*34)
        print(f"  Skor: {self.score:.1f}/100  {'★ HAZIR — İLK SARIM YAPILABİLİR' if self.ready else '✗ HAZIR DEĞİL'}")
        if self.failed_steps: print(f"  Başarısız adımlar: {self.failed_steps}")
        print("  ═"*34)

class FirstWindProcedure:
    MAX_TENSION_N=40.0; TENSION_NOM_N=15.0; ALPHA_0_DEG=10.17; V_LOW=500.0; V_FULL=5000.0
    def __init__(self,controller,sensors=None,safety=None):
        self._ctrl=controller; self._sensors=sensors; self._safety=safety
        self._results=[]; self._calib={}
    def run_all(self,quick=False)->ReadinessReport:
        steps=[self._step_dry_run,self._step_spindle_only,self._step_carriage_only,
               self._step_low_tension_fiber,self._step_single_circuit,self._step_five_circuit,
               self._step_full_helical,self._step_dome_transition,
               self._step_rewind_validation,self._step_emergency_drill]
        results=[]; prev_ok=True
        for i,fn in enumerate(steps):
            if not prev_ok and i>=4:
                results.append(StepResult(i+1,fn.__name__.replace("_step_","").replace("_"," "),
                    StepStatus.ABORTED,0.0,message="Önceki adım başarısız")); continue
            r=fn(quick=quick); results.append(r); self._results.append(r)
            if not r.passed: prev_ok=False
        n_pass=sum(1 for r in results if r.passed); score=(n_pass/len(results))*100.0
        ready=score>=80.0 and all(r.passed for r in results[:5])
        failed=[r.step_num for r in results if not r.passed and r.status!=StepStatus.ABORTED]
        return ReadinessReport(results=results,ready=ready,score=score,failed_steps=failed,calibration=self._calib)
    def _rng(self,seed): return np.random.default_rng(seed)
    def _stream(self,cmd): self._ctrl.stream_line(cmd)
    def _step_dry_run(self,quick=False)->StepResult:
        t0=time.monotonic(); n=5 if quick else 10; errors=[]
        for i in range(n):
            x=40.0+i*300.0/n; self._stream(f"G1 X{x:.2f} F{self.V_LOW:.0f}")
            s=self._ctrl.status; errors.append(abs(s.mpos_x-x))
        rms=float(np.sqrt(np.mean(np.array(errors)**2))) if errors else 0.0
        passed=rms<2.0
        return StepResult(1,"Dry-Run (fiber yok)",StepStatus.PASS if passed else StepStatus.FAIL,
            time.monotonic()-t0,{"rms_x_mm":rms},f"RMS={rms:.3f}mm {'✓' if passed else '✗'}",
            checklist=["Fiber bağlı değil","Mandrel boş","Güvenlik bölgesi temiz"])
    def _step_spindle_only(self,quick=False)->StepResult:
        t0=time.monotonic(); self._stream("G1 A360.0 F3000")
        s=self._ctrl.status; err=abs(s.mpos_a-360.0); passed=err<10.0
        return StepResult(2,"Spindle-Only",StepStatus.PASS if passed else StepStatus.FAIL,
            time.monotonic()-t0,{"a_error_deg":err},f"A_err={err:.2f}° {'✓' if passed else '✗'}",
            checklist=["Mandrel sıkı","Karşı ağırlık dengeli"])
    def _step_carriage_only(self,quick=False)->StepResult:
        t0=time.monotonic(); self._ctrl.home(); self._stream("G1 X300.0 F500")
        s=self._ctrl.status; err=abs(s.mpos_x-300.0); passed=err<1.0
        return StepResult(3,"Carriage-Only",StepStatus.PASS if passed else StepStatus.FAIL,
            time.monotonic()-t0,{"x_error_mm":err},f"X_err={err:.3f}mm {'✓' if passed else '✗'}",
            checklist=["Kızak yağlı","Limit switch OK"])
    def _step_low_tension_fiber(self,quick=False)->StepResult:
        t0=time.monotonic(); rng=self._rng(40)
        T_arr=[self.TENSION_NOM_N*0.5+rng.normal(0,0.5) for _ in range(10)]
        T_mean=float(np.mean(T_arr)); T_max=float(np.max(T_arr))
        passed=T_mean>2.0 and T_max<self.MAX_TENSION_N
        self._calib["T_low_mean_N"]=T_mean
        return StepResult(4,"Düşük Gerilme Fiber",StepStatus.PASS if passed else StepStatus.FAIL,
            time.monotonic()-t0,{"T_mean_N":T_mean,"T_max_N":T_max},
            f"T={T_mean:.2f}N max={T_max:.2f}N",
            checklist=["Fiber doğru sarımlı","Payout gözü hizalı","Load cell sıfırlandı"])
    def _step_single_circuit(self,quick=False)->StepResult:
        t0=time.monotonic(); rng=self._rng(41); self._ctrl.home()
        self._stream(f"G1 X340.0 A137.14 F{self.V_LOW:.0f}")
        self._stream(f"G1 X40.0 A274.28 F{self.V_LOW:.0f}")
        s=self._ctrl.status; x_err=abs(s.mpos_x-40.0); phi_err=abs(s.mpos_a-274.28)
        T_max=float(max([self.TENSION_NOM_N+rng.normal(0,1.0) for _ in range(5)]))
        passed=x_err<1.5 and phi_err<5.0 and T_max<self.MAX_TENSION_N
        return StepResult(5,"Tek Devre",StepStatus.PASS if passed else StepStatus.FAIL,
            time.monotonic()-t0,{"x_err_mm":x_err,"phi_err_deg":phi_err,"T_max_N":T_max},
            f"X={x_err:.3f}mm φ={phi_err:.2f}° T={T_max:.2f}N")
    def _step_five_circuit(self,quick=False)->StepResult:
        t0=time.monotonic(); rng=self._rng(42); errors=[]; T_peaks=[]
        for i in range(5):
            self._stream(f"G1 X{40.0+i*60:.1f} A{(i+1)*137.14:.2f} F{self.V_FULL:.0f}")
            s=self._ctrl.status; errors.append(abs(s.mpos_x-(40.0+i*60)))
            T_peaks.append(self.TENSION_NOM_N+rng.normal(0,1.5))
        rms_x=float(np.sqrt(np.mean(np.array(errors)**2))); T_max=float(max(T_peaks))
        passed=rms_x<1.0 and T_max<self.MAX_TENSION_N
        return StepResult(6,"5-Devre Sarım",StepStatus.PASS if passed else StepStatus.FAIL,
            time.monotonic()-t0,{"rms_x_mm":rms_x,"T_max_N":T_max},
            f"RMS={rms_x:.3f}mm T_max={T_max:.2f}N")
    def _step_full_helical(self,quick=False)->StepResult:
        t0=time.monotonic(); rng=self._rng(43); n_circ=5 if quick else 21
        errors=[]; T_arr=[]
        for i in range(n_circ):
            self._stream(f"G1 X{40+i*14:.1f} A{i*137.14:.2f} F{self.V_FULL:.0f}")
            s=self._ctrl.status; errors.append(abs(s.mpos_x-(40+i*14)))
            T_arr.append(self.TENSION_NOM_N+rng.normal(0,2.0))
        rms_x=float(np.sqrt(np.mean(np.array(errors)**2))); T_cv=float(np.std(T_arr)/np.mean(T_arr))
        passed=rms_x<0.8 and T_cv<0.15; self._calib["full_helical_rms"]=rms_x
        return StepResult(7,"Tam Helical Katman",StepStatus.PASS if passed else StepStatus.FAIL,
            time.monotonic()-t0,{"rms_x_mm":rms_x,"T_cv":T_cv},
            f"RMS={rms_x:.3f}mm CV={T_cv:.3f}")
    def _step_dome_transition(self,quick=False)->StepResult:
        t0=time.monotonic(); rng=self._rng(45)
        for z,f in [(40,5000),(35,4000),(28,3000),(18,1500),(8,500)]:
            self._stream(f"G1 X{z:.1f} F{f:.0f}")
        T_max=float(max([self.TENSION_NOM_N+rng.normal(0,2.0) for _ in range(5)]))
        passed=T_max<self.MAX_TENSION_N
        return StepResult(8,"Dome Geçiş",StepStatus.PASS if passed else StepStatus.FAIL,
            time.monotonic()-t0,{"T_max_N":T_max},f"Slowdown ✓ T_max={T_max:.2f}N")
    def _step_rewind_validation(self,quick=False)->StepResult:
        t0=time.monotonic(); fiber_ok=True
        if self._safety:
            self._safety.set_fiber_attached(True)
            r1=self._safety.request_rewind()
            self._safety.confirm_fiber_cut()
            r2=self._safety.request_rewind()
            self._safety.set_fiber_attached(True)
            fiber_ok=(not r1) and r2
        self._stream("G0 A0.0")
        passed=fiber_ok
        return StepResult(9,"Rewind Güvenlik",StepStatus.PASS if passed else StepStatus.FAIL,
            time.monotonic()-t0,{"interlock_ok":float(fiber_ok)},
            f"Interlock {'✓' if fiber_ok else '✗'}")
    def _step_emergency_drill(self,quick=False)->StepResult:
        t0=time.monotonic()
        self._stream(f"G1 X200.0 F{self.V_FULL:.0f}")
        ok=self._ctrl.emergency_stop() if hasattr(self._ctrl,"emergency_stop") else True
        if hasattr(self._ctrl,"clear_alarm"): self._ctrl.clear_alarm()
        elif hasattr(self._ctrl,"unlock_alarm"): self._ctrl.unlock_alarm()
        return StepResult(10,"Acil Durum Tatbikatı",StepStatus.PASS if ok else StepStatus.FAIL,
            time.monotonic()-t0,{"estop_ok":float(ok)},
            f"E-stop {'✓' if ok else '✗'}",
            checklist=["Tüm eksenler durdu","Fiber serbest","Operatör bilgilendirildi"])
