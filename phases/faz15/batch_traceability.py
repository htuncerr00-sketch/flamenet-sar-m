"""batch_traceability.py — Batch Traceability + Manufacturing Genealogy"""
from __future__ import annotations
import json, os, time, uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

@dataclass
class MaterialLot:
    lot_id:       str
    material_type:str    # "carbon_fiber","epoxy_resin","hardener"
    supplier:     str
    lot_number:   str
    coa_values:   dict   # Certificate of Analysis values
    received_date:float
    expiry_date:  float
    quantity_kg:  float
    remaining_kg: float

    @property
    def is_expired(self) -> bool: return time.time() > self.expiry_date

    def consume(self, amount_kg: float) -> bool:
        if amount_kg > self.remaining_kg: return False
        self.remaining_kg -= amount_kg; return True


@dataclass
class BatchRecord:
    batch_id:      str
    recipe_id:     str
    recipe_version:int
    start_time:    float
    end_time:      float = 0.0
    operator_id:   str   = "unknown"
    material_lots: List[str] = field(default_factory=list)
    process_params:dict  = field(default_factory=dict)
    quality_summary:dict = field(default_factory=dict)
    status:        str   = "IN_PROGRESS"  # IN_PROGRESS/PASS/FAIL/QUARANTINE
    batch_cert:    str   = ""

    def close(self, status: str, quality: dict) -> None:
        self.end_time = time.time()
        self.status   = status
        self.quality_summary = quality
        self.batch_cert = f"CERT-{self.batch_id[:8]}-{int(self.end_time)}"

    def save(self, directory: str) -> str:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"batch_{self.batch_id}.json")
        tmp  = path+".tmp"
        with open(tmp,"w") as f: json.dump(asdict(self),f,indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path); return path


class BatchTraceability:
    """Full batch traceability: material → process → product."""
    def __init__(self, store_dir: str = "/mnt/user-data/outputs/batches"):
        self._dir   = store_dir; os.makedirs(store_dir,exist_ok=True)
        self._lots:  Dict[str,MaterialLot]  = {}
        self._batch: Optional[BatchRecord]  = None
        self._history:List[BatchRecord]     = []

    def register_lot(self, lot: MaterialLot) -> None:
        self._lots[lot.lot_id] = lot

    def start_batch(self, recipe_id: str, version: int,
                    operator: str = "AUTO") -> str:
        bid = str(uuid.uuid4())[:12].upper()
        self._batch = BatchRecord(
            batch_id=bid, recipe_id=recipe_id,
            recipe_version=version, start_time=time.time(),
            operator_id=operator)
        return bid

    def add_material(self, lot_id: str, amount_kg: float) -> bool:
        if lot_id not in self._lots: return False
        lot = self._lots[lot_id]
        if lot.is_expired: print(f"    [BATCH] LOT {lot_id} EXPIRED"); return False
        if not lot.consume(amount_kg): return False
        if self._batch: self._batch.material_lots.append(lot_id)
        return True

    def record_params(self, params: dict) -> None:
        if self._batch: self._batch.process_params.update(params)

    def close_batch(self, status: str, quality: dict) -> Optional[str]:
        if not self._batch: return None
        self._batch.close(status, quality)
        path = self._batch.save(self._dir)
        self._history.append(self._batch)
        bid = self._batch.batch_id
        self._batch = None
        return path

    @property
    def current_batch_id(self) -> Optional[str]:
        return self._batch.batch_id if self._batch else None

    def get_genealogy(self, batch_id: str) -> dict:
        """Full production genealogy for a batch."""
        for b in self._history:
            if b.batch_id == batch_id:
                lots = {lid: asdict(self._lots[lid])
                        for lid in b.material_lots if lid in self._lots}
                return {"batch": asdict(b), "material_lots": lots}
        return {}

    def batch_pass_rate(self) -> float:
        if not self._history: return 0.0
        return sum(1 for b in self._history if b.status=="PASS") / len(self._history)
