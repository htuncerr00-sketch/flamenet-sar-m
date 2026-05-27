"""
persistence/session_archive.py — Atomic JSON session metadata
==================================================================
Crash-safe: write-tmp + fsync + atomic rename.
"""
from __future__ import annotations
import hashlib, json, os, time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class SessionMeta:
    session_id:    str
    batch_id:      str
    recipe_id:     str
    operator_id:   str
    start_time:    float
    end_time:      float = 0.0
    status:        str = "IN_PROGRESS"
    quality_mean:  float = 0.0
    rms_x_mm:      float = 0.0
    twin_accuracy: float = 0.0
    n_alerts:      int = 0
    notes:         str = ""
    checksum:      str = ""

    def compute_checksum(self) -> str:
        d = asdict(self); d.pop("checksum", "")
        return hashlib.sha256(
            json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]

    def save_atomic(self, directory: str) -> str:
        os.makedirs(directory, exist_ok=True)
        self.checksum = self.compute_checksum()
        path = os.path.join(directory, f"session_{self.session_id}.json")
        tmp  = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(asdict(self), f, indent=2, default=str)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path: str) -> Optional["SessionMeta"]:
        try:
            with open(path) as f:
                d = json.load(f)
            s = cls(**d)
            stored = s.checksum
            s.checksum = ""
            expected = s.compute_checksum()
            s.checksum = stored
            if stored and stored != expected:
                return None
            return s
        except Exception:
            return None


class SessionArchive:
    def __init__(self, directory: str = "sessions"):
        self._dir = directory
        os.makedirs(directory, exist_ok=True)

    def list_incomplete(self) -> List[SessionMeta]:
        out = []
        for f in os.listdir(self._dir):
            if not f.endswith(".json"): continue
            s = SessionMeta.load(os.path.join(self._dir, f))
            if s and s.status == "IN_PROGRESS":
                out.append(s)
        return out

    def list_all(self) -> List[SessionMeta]:
        out = []
        for f in os.listdir(self._dir):
            if not f.endswith(".json"): continue
            s = SessionMeta.load(os.path.join(self._dir, f))
            if s: out.append(s)
        return sorted(out, key=lambda x: -x.start_time)
