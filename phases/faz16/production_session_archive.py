"""
production_session_archive.py — Crash-Safe Production Session Archive
======================================================================
Her üretim oturumu atomik olarak kaydedilir.
Power-loss → son geçerli session dosyası her zaman kurtarılabilir.
"""
from __future__ import annotations
import hashlib, json, os, time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

@dataclass
class ProductionSession:
    session_id:     str
    batch_id:       str
    recipe_id:      str
    operator_id:    str
    start_time:     float
    end_time:       float = 0.0
    n_layers:       int   = 0
    total_arc_km:   float = 0.0
    rms_x_mm:       float = 0.0
    rms_T_N:        float = 0.0
    twin_accuracy:  float = 0.0
    quality_mean:   float = 0.0
    void_pct_mean:  float = 0.0
    Vf_mean:        float = 0.0
    alpha_final:    float = 0.0
    status:         str   = "IN_PROGRESS"
    fault_count:    int   = 0
    telemetry_path: str   = ""
    checksum:       str   = ""
    notes:          str   = ""

    def compute_checksum(self) -> str:
        from dataclasses import asdict
        d = asdict(self); d.pop("checksum","")
        return hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest()[:16]

    def close(self, status: str, metrics: dict) -> None:
        self.end_time = time.time()
        self.status   = status
        for k,v in metrics.items():
            if hasattr(self,k): setattr(self,k,v)
        d = asdict(self); d.pop("checksum","")
        self.checksum = hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest()[:16]

    def duration_h(self) -> float:
        t = self.end_time or time.time()
        return (t - self.start_time) / 3600.0

    def save(self, directory: str) -> str:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"session_{self.session_id}.json")
        tmp  = path + ".tmp"
        with open(tmp,"w") as f:
            json.dump(asdict(self),f,indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path: str) -> Optional["ProductionSession"]:
        try:
            with open(path) as f: d = json.load(f)
            s = cls(**d)
            # Verify checksum
            stored = s.checksum
            d2 = asdict(s); d2.pop("checksum","")
            expected = hashlib.sha256(json.dumps(d2,sort_keys=True).encode()).hexdigest()[:16]
            if stored and stored != expected:
                print(f"    [SESSION] Checksum mismatch: {path}"); return None
            return s
        except Exception as e:
            print(f"    [SESSION] Load error: {e}"); return None


class ProductionSessionArchive:
    """Manages production session lifecycle and archive."""
    def __init__(self, store_dir: str = "/mnt/user-data/outputs/sessions"):
        self._dir  = store_dir; os.makedirs(store_dir, exist_ok=True)
        self._active: Optional[ProductionSession] = None
        self._history: List[ProductionSession] = []

    def start_session(self, session_id: str, batch_id: str,
                      recipe_id: str, operator: str="AUTO") -> ProductionSession:
        s = ProductionSession(session_id=session_id, batch_id=batch_id,
                              recipe_id=recipe_id, operator_id=operator,
                              start_time=time.time())
        self._active = s
        s.save(self._dir)   # Initial save
        return s

    def checkpoint(self, metrics: dict) -> Optional[str]:
        """Periodic checkpoint — safe to call frequently."""
        if not self._active: return None
        for k,v in metrics.items():
            if hasattr(self._active,k): setattr(self._active,k,v)
        return self._active.save(self._dir)

    def close_session(self, status: str, metrics: dict) -> Optional[str]:
        if not self._active: return None
        self._active.close(status, metrics)
        path = self._active.save(self._dir)
        self._history.append(self._active)
        self._active = None
        return path

    def load_incomplete(self) -> List[ProductionSession]:
        """Find incomplete sessions (power-loss recovery)."""
        incomplete = []
        for f in os.listdir(self._dir):
            if not f.endswith(".json"): continue
            s = ProductionSession.load(os.path.join(self._dir,f))
            if s and s.status == "IN_PROGRESS": incomplete.append(s)
        return incomplete

    def session_count(self) -> int: return len(self._history)
    def pass_rate(self) -> float:
        if not self._history: return 0.0
        return sum(1 for s in self._history if s.status=="PASS") / len(self._history)
