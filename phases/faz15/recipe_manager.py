"""recipe_manager.py — Recipe Management (Offline-Safe, Versioned)"""
from __future__ import annotations
import hashlib, json, os, time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

@dataclass
class ProcessRecipe:
    recipe_id:    str
    version:      int
    name:         str
    resin_system: str
    T_cure_C:     float
    T_ramp_C_min: float
    wind_speed_mm_s:float
    tension_N:    float
    alpha_wind_deg:float
    n_layers:     int
    Vf_target:    float
    void_max_pct: float
    cure_time_min:float
    post_cure_T_C:float
    created_by:   str
    created_at:   float = field(default_factory=time.time)
    checksum:     str = ""

    def compute_checksum(self) -> str:
        d = asdict(self); d.pop("checksum","")
        return hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest()[:16]

    def validate(self) -> bool:
        c = (20 <= self.T_cure_C <= 200 and
             0.1 <= self.T_ramp_C_min <= 10 and
             10 <= self.wind_speed_mm_s <= 200 and
             2 <= self.tension_N <= 40 and
             0 <= self.alpha_wind_deg <= 85 and
             1 <= self.n_layers <= 50 and
             0.30 <= self.Vf_target <= 0.75 and
             self.void_max_pct <= 5.0)
        return c

    def save(self, directory: str) -> str:
        self.checksum = self.compute_checksum()
        path = os.path.join(directory, f"{self.recipe_id}_v{self.version}.json")
        os.makedirs(directory, exist_ok=True)
        tmp = path+".tmp"
        with open(tmp,"w") as f: json.dump(asdict(self),f,indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path); return path

    @classmethod
    def load(cls, path: str) -> Optional["ProcessRecipe"]:
        try:
            with open(path) as f: d = json.load(f)
            r = cls(**d)
            expected = r.compute_checksum()
            if r.checksum and r.checksum != expected:
                print(f"    [RECIPE] Checksum mismatch: {path}")
                return None
            return r
        except Exception as e:
            print(f"    [RECIPE] Load error: {e}"); return None


class RecipeManager:
    """Offline-safe recipe store with versioning."""
    def __init__(self, store_dir: str = "/mnt/user-data/outputs/recipes"):
        self._dir  = store_dir; os.makedirs(store_dir, exist_ok=True)
        self._cache:Dict[str,ProcessRecipe] = {}

    def save(self, recipe: ProcessRecipe) -> bool:
        if not recipe.validate():
            print("    [RECIPE] Validation FAIL"); return False
        path = recipe.save(self._dir)
        self._cache[recipe.recipe_id] = recipe
        print(f"    [RECIPE] Saved: {path}"); return True

    def load(self, recipe_id: str, version: int = None) -> Optional[ProcessRecipe]:
        if recipe_id in self._cache: return self._cache[recipe_id]
        prefix = f"{recipe_id}_v"
        files  = [f for f in os.listdir(self._dir) if f.startswith(prefix)]
        if not files: return None
        if version:
            target = f"{recipe_id}_v{version}.json"
        else:
            target = sorted(files)[-1]   # Latest version
        r = ProcessRecipe.load(os.path.join(self._dir, target))
        if r: self._cache[recipe_id] = r
        return r

    def list_recipes(self) -> List[str]:
        ids = set()
        for f in os.listdir(self._dir):
            if f.endswith(".json") and "_v" in f:
                ids.add(f.split("_v")[0])
        return sorted(ids)

    def validate_process_window(self, recipe: ProcessRecipe,
                                 actual_T_C: float, actual_tension_N: float,
                                 actual_alpha: float) -> dict:
        T_ok   = abs(actual_T_C - recipe.T_cure_C) < 5.0
        tns_ok = abs(actual_tension_N - recipe.tension_N) < recipe.tension_N * 0.1
        alp_ok = actual_alpha < 0.95
        return {"T_ok":T_ok,"tension_ok":tns_ok,"alpha_ok":alp_ok,
                "all_ok":T_ok and tns_ok and alp_ok}
