"""
production_recovery.py — Üretim Kurtarma Sistemi
=================================================
Power failure, GRBL disconnect, missed-step resync.
Crash-safe snapshot → resume on restart.
"""
from __future__ import annotations
import json, math, os, time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Dict, List, Optional

class RecoveryDecision(Enum):
    RESUME="resume"; RESTART_LAYER="restart_layer"
    RESTART_ALL="restart_all"; OPERATOR_NEEDED="operator_needed"

@dataclass
class WindingSnapshot:
    """Atom olarak diske yazılan anlık durum — fsync garantili."""
    timestamp:      float
    layer_index:    int
    pass_index:     int
    segment_index:  int
    x_mm:           float
    a_deg:          float
    tension_N:      float
    feed_override:  float
    phi_drift_deg:  float
    thermal_err_mm: float
    n_circuits_done:int
    planner_c:      float   # Clairaut c [mm]
    planner_k:      int     # Circuits per layer
    valid:          bool = True
    resume_possible:bool = True
    reason:         str  = ""

    def save(self, path:str) -> None:
        tmp = path+".tmp"
        with open(tmp,"w") as f:
            json.dump(asdict(self),f,indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)  # Atomic rename

    @classmethod
    def load(cls, path:str) -> Optional["WindingSnapshot"]:
        try:
            with open(path) as f: d=json.load(f)
            return cls(**d)
        except Exception: return None

    def to_summary(self) -> str:
        return (f"Snapshot @ {time.strftime('%H:%M:%S',time.localtime(self.timestamp))}  "
                f"Layer={self.layer_index} Pass={self.pass_index} Seg={self.segment_index}  "
                f"X={self.x_mm:.2f}mm A={self.a_deg:.2f}° T={self.tension_N:.2f}N  "
                f"{'RESUME ✓' if self.resume_possible else 'NO RESUME ✗'}")

class ProductionRecovery:
    """
    Kurtarma stratejileri ve snapshot yönetimi.
    """
    SNAPSHOT_PATH = "/mnt/user-data/outputs/winding_snapshot.json"
    CRASH_DUMP_PATH = "/mnt/user-data/outputs/crash_dump.bin"

    def __init__(self):
        self._snapshots: List[WindingSnapshot] = []
        self._last_snap: Optional[WindingSnapshot] = None

    def take_snapshot(self, layer:int, pass_:int, seg:int, x:float, a:float,
                      T:float, override:float=1.0, drift:float=0.0,
                      thermal:float=0.0, n_circ:int=0, c:float=8.83, k:int=21) -> WindingSnapshot:
        snap = WindingSnapshot(timestamp=time.time(),layer_index=layer,pass_index=pass_,
            segment_index=seg,x_mm=x,a_deg=a,tension_N=T,feed_override=override,
            phi_drift_deg=drift,thermal_err_mm=thermal,n_circuits_done=n_circ,
            planner_c=c,planner_k=k)
        self._snapshots.append(snap); self._last_snap=snap
        snap.save(self.SNAPSHOT_PATH)
        return snap

    def load_last_snapshot(self) -> Optional[WindingSnapshot]:
        return WindingSnapshot.load(self.SNAPSHOT_PATH)

    def analyze_recovery(self, snap:WindingSnapshot) -> RecoveryDecision:
        if not snap or not snap.valid:
            return RecoveryDecision.RESTART_ALL
        # Güvenli kurtarma koşulları
        x_ok  = 0.0 <= snap.x_mm <= 400.0
        a_ok  = snap.a_deg >= 0.0
        T_ok  = 1.0 <= snap.tension_N <= 50.0
        drft  = abs(snap.phi_drift_deg) < 20.0
        if x_ok and a_ok and T_ok and drft and snap.resume_possible:
            return RecoveryDecision.RESUME
        if x_ok and a_ok:
            return RecoveryDecision.RESTART_LAYER
        return RecoveryDecision.OPERATOR_NEEDED

    def build_resume_gcode(self, snap:WindingSnapshot, controller_name:str="grbl") -> List[str]:
        """Resume G-code: güvenli pozisyona git, gerilim kontrol et, devam et."""
        cmt = ";" if "grbl" in controller_name.lower() else "("
        cme = "" if "grbl" in controller_name.lower() else ")"
        def C(t): return f"{cmt} {t}{cme}"
        lines = [
            C("=== POWER RECOVERY RESUME ==="),
            C(f"Snapshot: Layer={snap.layer_index} Pass={snap.pass_index}"),
            C(f"X={snap.x_mm:.3f}mm A={snap.a_deg:.3f}deg T={snap.tension_N:.2f}N"),
            "G21 G90",
            C("Safe approach: slow-move to resume position"),
            f"G1 X{max(0.0,snap.x_mm-5.0):.3f} F300",
            C("Pause for operator tension check"),
            "M0",
            f"G1 X{snap.x_mm:.3f} A{snap.a_deg:.3f} F{int(3000*snap.feed_override)}",
            C("=== RESUME COMPLETE ==="),
        ]
        return lines

    def missed_step_resync(self, x_encoder:float, x_commanded:float, tolerance_mm:float=0.5) -> dict:
        """Encoder vs commanded karşılaştırma ve resync kararı."""
        err = abs(x_encoder - x_commanded)
        needs_resync = err > tolerance_mm
        return {"error_mm":err,"needs_resync":needs_resync,
                "action":"RESYNC → hız azalt + Kalman reset" if needs_resync else "OK"}

    def emergency_retract(self, current_x:float, home_x:float=0.0) -> List[str]:
        """Güvenli geri çekme G-code."""
        return [
            "; === EMERGENCY RETRACT ===",
            "M0 ; Wait for operator",
            f"G0 X{home_x:.3f} ; Rapid home",
            "; === RETRACT COMPLETE ===",
        ]

    @property
    def last_snapshot(self) -> Optional[WindingSnapshot]:
        return self._last_snap

    def snapshot_count(self) -> int:
        return len(self._snapshots)
