"""
production_dataset_archive.py — Crash-Safe Production Dataset Archive
========================================================================
Long-term storage of telemetry, fingerprints, faults, maintenance.
Atomic JSON + binary telemetry. Replay-compatible.
"""
from __future__ import annotations
import hashlib, json, os, time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

@dataclass
class DatasetEntry:
    session_id:    str
    batch_id:      str
    start_time:    float
    end_time:      float = 0.0
    n_samples:     int   = 0
    telemetry_path:str   = ""
    fingerprint:   dict  = field(default_factory=dict)
    fault_log:     list  = field(default_factory=list)
    maintenance:   list  = field(default_factory=list)
    health_score:  float = 0.0
    checksum:      str   = ""

    def compute_checksum(self) -> str:
        d = asdict(self); d.pop("checksum","")
        return hashlib.sha256(json.dumps(d,sort_keys=True,default=str).encode()).hexdigest()[:16]

    def save(self, directory: str) -> str:
        self.checksum = self.compute_checksum()
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"dataset_{self.session_id}.json")
        tmp = path+".tmp"
        with open(tmp,"w") as f:
            json.dump(asdict(self), f, indent=2, default=str)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path: str) -> Optional["DatasetEntry"]:
        try:
            with open(path) as f: d = json.load(f)
            e = cls(**d)
            stored = e.checksum
            e.checksum = ""
            expected = e.compute_checksum()
            e.checksum = stored
            if stored and stored != expected: return None
            return e
        except: return None


class ProductionDatasetArchive:
    def __init__(self, store_dir: str = "/mnt/user-data/outputs/datasets"):
        self._dir = store_dir; os.makedirs(store_dir, exist_ok=True)
        self._entries: List[DatasetEntry] = []

    def archive(self, entry: DatasetEntry) -> str:
        path = entry.save(self._dir)
        self._entries.append(entry)
        return path

    def list_sessions(self) -> List[str]:
        return sorted([f for f in os.listdir(self._dir) if f.endswith(".json")])

    def load_session(self, session_id: str) -> Optional[DatasetEntry]:
        path = os.path.join(self._dir, f"dataset_{session_id}.json")
        return DatasetEntry.load(path) if os.path.exists(path) else None

    @property
    def n_archived(self) -> int: return len(self._entries)
