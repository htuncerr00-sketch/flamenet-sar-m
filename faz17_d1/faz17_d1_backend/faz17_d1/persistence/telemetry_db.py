"""
persistence/telemetry_db.py — Binary Telemetry Sessions Store
================================================================
Each session = (session_id, start_time, end_time, frame_count, blob)
Blob stored as compressed binary (telemetry V2 packed frames).
"""
from __future__ import annotations
import sqlite3, threading, time, zlib
from dataclasses import dataclass
from typing import List, Optional
from ..hardware.esp32_link import TelemetryFrame, TELEM_BYTES


@dataclass
class SessionMeta:
    session_id: str
    start_time: float
    end_time:   float
    n_frames:   int
    n_bytes:    int   # uncompressed size
    compressed: int   # compressed size


class TelemetryDB:
    """Binary blob store for telemetry sessions."""
    FLUSH_INTERVAL = 1000   # frames

    def __init__(self, path: str = "telemetry.db"):
        self._path = path
        self._lock = threading.Lock()
        self._current_session: Optional[str] = None
        self._current_buf: bytearray = bytearray()
        self._current_n: int = 0
        self._init()

    def _init(self) -> None:
        with sqlite3.connect(self._path) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=FULL")   # fsync on commit
            c.execute("""CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                n_frames INTEGER NOT NULL,
                n_bytes INTEGER NOT NULL,
                blob BLOB NOT NULL)""")
            c.commit()

    def start_session(self, session_id: str) -> bool:
        with self._lock:
            if self._current_session is not None:
                return False
            self._current_session = session_id
            self._current_buf = bytearray()
            self._current_n = 0
            self._t0 = time.time()
        return True

    def record(self, frame: TelemetryFrame) -> None:
        if self._current_session is None: return
        with self._lock:
            self._current_buf += frame.pack()
            self._current_n += 1

    def flush(self) -> None:
        """Force commit to disk (call periodically)."""
        if self._current_session is None: return
        with self._lock:
            sid = self._current_session
            n   = self._current_n
            t0  = self._t0
            buf = bytes(self._current_buf)
        # No DB action yet — buffer in memory until close_session

    def close_session(self) -> Optional[SessionMeta]:
        """Compress and commit. Returns SessionMeta on success."""
        with self._lock:
            if self._current_session is None: return None
            sid = self._current_session
            buf = bytes(self._current_buf)
            n_frames = self._current_n
            t0 = self._t0
            t1 = time.time()
            self._current_session = None
            self._current_buf = bytearray()
            self._current_n = 0
        # Compress and write OUTSIDE the recording lock
        compressed = zlib.compress(buf, level=6)
        try:
            with sqlite3.connect(self._path) as c:
                c.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?)",
                    (sid, t0, t1, n_frames, len(buf), compressed))
                c.commit()
            return SessionMeta(sid, t0, t1, n_frames, len(buf), len(compressed))
        except Exception:
            return None

    def load_session(self, session_id: str) -> List[TelemetryFrame]:
        with self._lock:
            with sqlite3.connect(self._path) as c:
                row = c.execute(
                    "SELECT n_bytes, blob FROM sessions WHERE session_id=?",
                    (session_id,)).fetchone()
        if not row: return []
        n_bytes, blob = row
        raw = zlib.decompress(blob)
        frames: List[TelemetryFrame] = []
        for i in range(0, len(raw), TELEM_BYTES):
            f = TelemetryFrame.unpack(raw[i:i+TELEM_BYTES])
            if f: frames.append(f)
        return frames

    def list_sessions(self) -> List[dict]:
        with self._lock:
            with sqlite3.connect(self._path) as c:
                rows = c.execute(
                    "SELECT session_id, start_time, end_time, n_frames, "
                    "n_bytes FROM sessions ORDER BY start_time DESC").fetchall()
        return [{"session_id":r[0], "start_time":r[1], "end_time":r[2],
                 "n_frames":r[3], "n_bytes":r[4]} for r in rows]
