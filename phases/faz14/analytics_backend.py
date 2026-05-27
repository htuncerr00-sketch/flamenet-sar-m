"""
analytics_backend.py — Production Analytics Backend (SQLite + WebSocket + CSV/JSON)
====================================================================================
Thread-safe, non-blocking, ring-buffer backed.
"""
from __future__ import annotations
import csv, json, os, sqlite3, threading, time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional
import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    t REAL, x_mm REAL, a_deg REAL, rpm REAL,
    tension_N REAL, quality REAL, feed_mult REAL,
    defect TEXT, phi_err_deg REAL
);
CREATE TABLE IF NOT EXISTS defects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    t REAL, defect_type TEXT, severity REAL,
    confidence REAL, z_mm REAL, cause TEXT
);
CREATE TABLE IF NOT EXISTS tuning_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    t REAL, committed INTEGER, quality_before REAL,
    quality_after REAL, reason TEXT, theta TEXT
);
"""

@dataclass(slots=True)
class TelemetryRecord:
    t: float; x_mm: float; a_deg: float; rpm: float
    tension_N: float; quality: float; feed_mult: float
    defect: str; phi_err_deg: float

class AnalyticsBackend:
    """
    Production analytics: SQLite write-ahead + JSON/CSV export + WebSocket stub.
    Write queue: non-blocking enqueue, background flush every 5s.
    """
    RING_SIZE   = 10000
    FLUSH_EVERY = 500    # records
    FLUSH_EVERY_S= 5.0   # seconds

    def __init__(self, db_path: str = "/mnt/user-data/outputs/winding_analytics.db",
                 out_dir: str = "/mnt/user-data/outputs"):
        self._db_path  = db_path
        self._out_dir  = out_dir; os.makedirs(out_dir, exist_ok=True)
        self._ring:    deque = deque(maxlen=self.RING_SIZE)
        self._write_q: deque = deque(maxlen=2000)
        self._lock     = threading.Lock()
        self._stop     = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._n_written = 0
        self._last_flush= time.monotonic()
        self._ws_clients: list = []   # WebSocket stubs
        self._init_db()

    def _init_db(self) -> None:
        try:
            con = sqlite3.connect(self._db_path, check_same_thread=False)
            con.executescript(SCHEMA); con.commit(); con.close()
        except Exception as e:
            print(f"    [DB] Init warn: {e}")

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._flush_loop,
                                         daemon=True, name="Analytics")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread: self._thread.join(timeout=2.0)
        self._flush_to_db()

    # ── Non-blocking record ───────────────────────────────────────

    def record(self, rec: TelemetryRecord) -> None:
        """Non-blocking. Drops on contention."""
        acq = self._lock.acquire(blocking=False)
        if not acq: return
        try:
            self._ring.append(rec)
            self._write_q.append(rec)
        finally:
            self._lock.release()

    def log_defect(self, t: float, dtype: str, sev: float,
                   conf: float, z_mm: float, cause: str) -> None:
        try:
            con = sqlite3.connect(self._db_path, check_same_thread=False)
            con.execute("INSERT INTO defects VALUES(NULL,?,?,?,?,?,?)",
                        (t, dtype, sev, conf, z_mm, cause))
            con.commit(); con.close()
        except: pass

    def log_tuning(self, t: float, committed: bool, q_before: float,
                   q_after: float, reason: str, theta: list) -> None:
        try:
            con = sqlite3.connect(self._db_path, check_same_thread=False)
            con.execute("INSERT INTO tuning_log VALUES(NULL,?,?,?,?,?,?)",
                        (t, int(committed), q_before, q_after, reason, str(theta)))
            con.commit(); con.close()
        except: pass

    # ── Flush loop ────────────────────────────────────────────────

    def _flush_loop(self) -> None:
        while not self._stop.is_set():
            dt = time.monotonic() - self._last_flush
            if (len(self._write_q) >= self.FLUSH_EVERY or dt > self.FLUSH_EVERY_S):
                self._flush_to_db()
            self._stop.wait(timeout=1.0)

    def _flush_to_db(self) -> None:
        with self._lock:
            batch = list(self._write_q); self._write_q.clear()
        if not batch: return
        try:
            con = sqlite3.connect(self._db_path, check_same_thread=False)
            con.executemany(
                "INSERT INTO telemetry VALUES(NULL,?,?,?,?,?,?,?,?,?)",
                [(r.t,r.x_mm,r.a_deg,r.rpm,r.tension_N,r.quality,
                  r.feed_mult,r.defect,r.phi_err_deg) for r in batch])
            con.commit(); con.close()
            self._n_written += len(batch)
            self._last_flush = time.monotonic()
        except Exception as e:
            print(f"    [DB] Flush warn: {e}")

    # ── Query / Export ────────────────────────────────────────────

    def get_live_summary(self) -> dict:
        with self._lock:
            if not self._ring: return {"status":"empty"}
            recent = list(self._ring)[-20:]
        T_arr = np.array([r.tension_N for r in recent])
        q_arr = np.array([r.quality for r in recent])
        last  = recent[-1]
        return {"t":round(last.t,3),"x":round(last.x_mm,3),
                "a":round(last.a_deg,2),"rpm":round(last.rpm,2),
                "T_mean":round(float(T_arr.mean()),3),
                "q_mean":round(float(q_arr.mean()),2),
                "n_total":len(self._ring),"n_db":self._n_written}

    def get_quality_timeline(self, n: int = 200) -> dict:
        with self._lock: recent = list(self._ring)[-n:]
        return {"t":[r.t for r in recent], "q":[r.quality for r in recent],
                "T":[r.tension_N for r in recent]}

    def tension_histogram(self, n_bins: int = 20) -> dict:
        with self._lock:
            T_arr = np.array([r.tension_N for r in self._ring])
        if len(T_arr) < 2: return {}
        hist, edges = np.histogram(T_arr, bins=n_bins)
        return {"counts":hist.tolist(), "edges":edges.tolist(),
                "mean":float(T_arr.mean()), "std":float(T_arr.std())}

    def export_csv(self, path: str) -> int:
        with self._lock: records = list(self._ring)
        with open(path,"w",newline="") as f:
            w = csv.writer(f)
            w.writerow(["t","x_mm","a_deg","rpm","tension_N","quality","feed_mult","defect","phi_err"])
            for r in records:
                w.writerow([f"{r.t:.3f}",f"{r.x_mm:.4f}",f"{r.a_deg:.3f}",
                            f"{r.rpm:.2f}",f"{r.tension_N:.3f}",f"{r.quality:.2f}",
                            f"{r.feed_mult:.4f}",r.defect,f"{r.phi_err_deg:.4f}"])
        return len(records)

    def export_json(self, path: str, last_n: int = 500) -> int:
        with self._lock: records = list(self._ring)[-last_n:]
        data = [{"t":r.t,"x":r.x_mm,"a":r.a_deg,"rpm":r.rpm,"T":r.tension_N,
                 "q":r.quality,"f":r.feed_mult,"d":r.defect} for r in records]
        with open(path,"w") as f: json.dump(data, f, separators=(",",":"))
        return len(records)

    def broadcast_ws(self, payload: dict) -> None:
        """WebSocket broadcast stub (real: asyncio + websockets)."""
        pass

    @property
    def n_records(self) -> int:
        with self._lock: return len(self._ring)
