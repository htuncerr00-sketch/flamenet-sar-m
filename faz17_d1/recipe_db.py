"""
persistence/recipe_db.py — SQLite Recipe Database (WAL mode)
================================================================
Schema:
  recipes(id INT PK, recipe_id TEXT UNIQUE, version INT, name TEXT,
          params_json TEXT, created_at REAL, checksum TEXT)
"""
from __future__ import annotations
import hashlib, json, sqlite3, threading, time
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional


@dataclass
class Recipe:
    recipe_id:    str
    version:      int
    name:         str
    resin_system: str = ""
    fiber:        str = ""
    alpha_deg:    float = 10.17
    n_layers:     int = 8
    tension_N:    float = 15.0
    feed_mm_s:    float = 100.0
    cure_T_C:     float = 120.0
    cure_h:       float = 8.0
    Vf_target:    float = 0.55
    void_max_pct: float = 3.0
    created_at:   float = field(default_factory=time.time)
    notes:        str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    def checksum(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()[:16]

    def validate(self) -> List[str]:
        errs = []
        if not (3 <= self.tension_N <= 38): errs.append("tension out of [3,38]")
        if not (0 < self.alpha_deg < 90):   errs.append("alpha out of (0,90)")
        if self.n_layers < 1:                errs.append("n_layers < 1")
        if not (10 <= self.feed_mm_s <= 200):errs.append("feed out of [10,200]")
        if self.cure_T_C < 20 or self.cure_T_C > 200: errs.append("cure_T out of [20,200]°C")
        if not (0.30 <= self.Vf_target <= 0.75): errs.append("Vf_target out of [0.30,0.75]")
        return errs


class RecipeDB:
    """SQLite-backed recipe store, WAL mode, checksum-validated."""
    def __init__(self, path: str = "recipes.db"):
        self._path = path
        self._lock = threading.Lock()
        self._init()

    def _init(self) -> None:
        with sqlite3.connect(self._path) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("""CREATE TABLE IF NOT EXISTS recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                name TEXT NOT NULL,
                params_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                checksum TEXT NOT NULL,
                UNIQUE(recipe_id, version))""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_recipe_id ON recipes(recipe_id)")
            c.commit()

    def save(self, recipe: Recipe) -> bool:
        errs = recipe.validate()
        if errs: return False
        with self._lock:
            try:
                with sqlite3.connect(self._path) as c:
                    c.execute("""INSERT OR REPLACE INTO recipes
                        (recipe_id, version, name, params_json, created_at, checksum)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (recipe.recipe_id, recipe.version, recipe.name,
                         recipe.to_json(), recipe.created_at, recipe.checksum()))
                    c.commit()
                return True
            except Exception:
                return False

    def load(self, recipe_id: str, version: Optional[int] = None) -> Optional[Recipe]:
        with self._lock:
            with sqlite3.connect(self._path) as c:
                if version is None:
                    row = c.execute(
                        "SELECT params_json, checksum FROM recipes WHERE recipe_id=? "
                        "ORDER BY version DESC LIMIT 1",
                        (recipe_id,)).fetchone()
                else:
                    row = c.execute(
                        "SELECT params_json, checksum FROM recipes WHERE recipe_id=? AND version=?",
                        (recipe_id, version)).fetchone()
                if not row: return None
                params_json, stored_checksum = row
                # Verify checksum
                d = json.loads(params_json)
                r = Recipe(**d)
                if r.checksum() != stored_checksum:
                    return None   # corrupted
                return r

    def list_recipes(self) -> List[dict]:
        with self._lock:
            with sqlite3.connect(self._path) as c:
                rows = c.execute(
                    "SELECT recipe_id, MAX(version), name, MAX(created_at) "
                    "FROM recipes GROUP BY recipe_id").fetchall()
        return [{"recipe_id":r[0], "version":r[1], "name":r[2], "created_at":r[3]}
                for r in rows]

    def delete(self, recipe_id: str) -> int:
        with self._lock:
            with sqlite3.connect(self._path) as c:
                cur = c.execute("DELETE FROM recipes WHERE recipe_id=?", (recipe_id,))
                c.commit()
                return cur.rowcount
