"""genealogy_tracker.py — Manufacturing Genealogy Tree"""
from __future__ import annotations
import json, os, time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

@dataclass
class GenealogyNode:
    node_id:   str; node_type: str  # "raw_material"|"process"|"component"|"assembly"
    parent_ids:List[str] = field(default_factory=list)
    children:  List[str] = field(default_factory=list)
    attributes:dict      = field(default_factory=dict)
    timestamp: float     = field(default_factory=time.time)

class GenealogyTracker:
    """DAG-based manufacturing genealogy tracker."""
    def __init__(self, store_dir: str = "/mnt/user-data/outputs"):
        self._dir   = store_dir; os.makedirs(store_dir,exist_ok=True)
        self._nodes:Dict[str,GenealogyNode] = {}

    def add_node(self, node: GenealogyNode) -> None:
        self._nodes[node.node_id] = node
        for pid in node.parent_ids:
            if pid in self._nodes:
                self._nodes[pid].children.append(node.node_id)

    def trace_upstream(self, node_id: str, depth: int = 10) -> List[str]:
        """Trace all ancestors (upstream traceability)."""
        if depth <= 0 or node_id not in self._nodes: return []
        node = self._nodes[node_id]; result = [node_id]
        for pid in node.parent_ids:
            result.extend(self.trace_upstream(pid, depth-1))
        return list(dict.fromkeys(result))  # deduplicate preserving order

    def save(self, path: str) -> None:
        data = {k:asdict(v) for k,v in self._nodes.items()}
        with open(path,"w") as f: json.dump(data,f,indent=2)

    @property
    def n_nodes(self) -> int: return len(self._nodes)
