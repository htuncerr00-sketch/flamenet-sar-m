"""
hardware/real_esp32_link.py — Production-Grade Real ESP32 Serial Link
==========================================================================
Hardware bring-up replacement for the skeleton RealESP32Link.

Same API as MockESP32Link (ESP32LinkBase abstract). Adds:

  - Watchdog: if no valid frame in WATCHDOG_S, raise alarm + attempt reconnect
  - Auto-reconnect: on serial error or cable unplug, retry with backoff
  - Bounded resync buffer: prevents unbounded growth on cable noise
  - Partial packet handling: holds incomplete fragments across reads
  - Burst handling: reads in 4 KB chunks, drains full ring per iteration
  - Frame metrics: partial packets, sync recoveries, watchdog trips,
    reconnect attempts, byte throughput, last-frame-age
  - Short read timeout: disconnect responds within 100 ms

Wire protocol (matches Mock + D1 telem V2):
  MAGIC (2B) = 0xAA 0x55
  PAYLOAD (64B) — packed TelemetryFrame (TELEM_FMT)
  Frame footer = last 2B of payload = CRC-16/CCITT of first 62B
"""
from __future__ import annotations
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, List

from .esp32_link import (
    ESP32LinkBase,
    ConnectionState,
    TelemetryFrame,
    TELEM_BYTES,
)


# ── tunable constants ─────────────────────────────────────────────────
WATCHDOG_S          = 1.0      # no valid frame for this long → trip
READ_CHUNK          = 4096     # serial read size per iteration (burst friendly)
READ_TIMEOUT_S      = 0.05     # short → disconnect responsive
MAX_BUFFER          = 16384    # bounded resync buffer (≈ 16 KB)
RECONNECT_BACKOFF_S = (0.2, 0.5, 1.0, 2.0, 2.0, 2.0)   # capped at 2s
LISTENER_PUT_LIMIT  = 64       # if any listener queue is full this many times,
                               #   stop trying it (avoids hot busy-loop)


@dataclass(slots=True)
class LinkDiagnostics:
    """Detailed counters surfaced to the UI / commissioning panel."""
    n_frames_ok:        int = 0
    n_crc_errors:       int = 0
    n_sync_errors:      int = 0      # bytes discarded looking for magic
    n_partial_packets:  int = 0      # frames waiting for next read
    n_resync_drops:     int = 0      # buffer overrun → forced flush
    n_watchdog_trips:   int = 0      # gaps > WATCHDOG_S
    n_reconnect_tries:  int = 0
    n_reconnects_ok:    int = 0
    n_bytes_in:         int = 0
    n_bytes_out:        int = 0
    last_frame_t_s:     float = 0.0   # monotonic timestamp of last good frame
    last_error:         str = ""

    @property
    def age_since_last_frame_s(self) -> float:
        if self.last_frame_t_s == 0.0:
            return -1.0
        return time.monotonic() - self.last_frame_t_s


class RealESP32Link(ESP32LinkBase):
    """
    Real serial link to ESP32. Drop-in replacement for MockESP32Link.

    Public API (same as MockESP32Link):
      connect() -> bool
      disconnect() -> None
      send_command(bytes) -> bool
      subscribe(queue), unsubscribe(queue)
      state, n_frames, n_crc_errors, n_sync_errors

    Extras for bring-up:
      diagnostics : LinkDiagnostics
      enable_auto_reconnect : bool (default True)
    """
    MAGIC = b"\xaa\x55"

    def __init__(self,
                 port: str = "/dev/ttyUSB0",
                 baud: int = 921600,
                 watchdog_s: float = WATCHDOG_S,
                 auto_reconnect: bool = True) -> None:
        super().__init__()
        self._port_path = port
        self._baud = baud
        self._watchdog_s = float(watchdog_s)
        self._auto_reconnect = bool(auto_reconnect)
        self._port = None   # pyserial Serial object; lazy
        self._diag = LinkDiagnostics()
        # Watchdog event for upper layers to subscribe to
        self._watchdog_callbacks: List = []
        # Listener-fullness counter, per-queue, capped
        self._listener_full_count = {}

    # ───────────────────────── public API ──────────────────────────────

    @property
    def diagnostics(self) -> LinkDiagnostics:
        return self._diag

    @property
    def port_path(self) -> str:
        return self._port_path

    @property
    def baud(self) -> int:
        return self._baud

    def set_port(self, port: str, baud: int = 921600) -> None:
        """Change port path while disconnected. Raises if connected."""
        if self._state == ConnectionState.CONNECTED:
            raise RuntimeError("Disconnect before changing port")
        self._port_path = port
        self._baud = baud

    def register_watchdog_callback(self, cb) -> None:
        """Callback(reason: str) when watchdog trips. Fired outside lock."""
        with self._lock:
            self._watchdog_callbacks.append(cb)

    def connect(self) -> bool:
        """Open serial port, start reader thread. Returns True on success."""
        if self._state == ConnectionState.CONNECTED:
            return True
        self._state = ConnectionState.CONNECTING
        if not self._open_port():
            self._state = ConnectionState.ERROR
            return False
        self._stop.clear()
        self._diag.last_frame_t_s = time.monotonic()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="RealESP32Link")
        self._thread.start()
        self._state = ConnectionState.CONNECTED
        return True

    def disconnect(self) -> None:
        """Stop reader thread, close port. Idempotent. Returns within ~150 ms."""
        self._stop.set()
        # Auto-reconnect must NOT kick in during explicit disconnect
        self._auto_reconnect_request = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._close_port()
        self._state = ConnectionState.DISCONNECTED

    def send_command(self, cmd: bytes) -> bool:
        """Write bytes to ESP32. False on any error."""
        if self._port is None or self._state != ConnectionState.CONNECTED:
            return False
        try:
            n = self._port.write(cmd)
            self._diag.n_bytes_out += n if n else 0
            return True
        except Exception as e:
            self._diag.last_error = f"send: {type(e).__name__}: {e}"
            self._state = ConnectionState.ERROR
            return False

    # ───────────────────────── helpers ─────────────────────────────────

    def _open_port(self) -> bool:
        try:
            import serial  # noqa: F401 — local optional dep
            self._port = serial.Serial(
                self._port_path,
                self._baud,
                timeout=READ_TIMEOUT_S,
                write_timeout=0.5,
                # Linux: disable DTR pulse (resets some ESP32 dev boards)
                rtscts=False,
                dsrdtr=False,
            )
            # Drain any boot-message backlog
            try:
                self._port.reset_input_buffer()
                self._port.reset_output_buffer()
            except Exception:
                pass
            return True
        except Exception as e:
            self._diag.last_error = f"open: {type(e).__name__}: {e}"
            self._port = None
            return False

    def _close_port(self) -> None:
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                pass
            self._port = None

    def _fire_watchdog(self, reason: str) -> None:
        """Notify upper layers. Called outside lock."""
        self._diag.n_watchdog_trips += 1
        self._diag.last_error = reason
        with self._lock:
            cbs = list(self._watchdog_callbacks)
        for cb in cbs:
            try:
                cb(reason)
            except Exception:
                pass

    # ───────────────────────── reader thread ───────────────────────────

    def _loop(self) -> None:
        """
        Reader loop:
          - read up to READ_CHUNK bytes
          - resync to MAGIC
          - parse 64B frames + CRC
          - dispatch
          - check watchdog
          - on serial error or watchdog trip → try to reconnect
        """
        buf = bytearray()
        last_watchdog_check = time.monotonic()
        reconnect_attempt = 0

        while not self._stop.is_set():
            # --- read ---
            try:
                chunk = self._port.read(READ_CHUNK) if self._port else b""
            except Exception as e:
                self._diag.last_error = f"read: {type(e).__name__}: {e}"
                if not self._handle_disconnect(reconnect_attempt):
                    return
                reconnect_attempt += 1
                buf.clear()
                last_watchdog_check = time.monotonic()
                continue

            if chunk:
                self._diag.n_bytes_in += len(chunk)
                buf += chunk
            # else: read returned nothing within timeout — that's normal

            # --- bounded buffer: cap to MAX_BUFFER bytes (forced resync) ---
            if len(buf) > MAX_BUFFER:
                excess = len(buf) - MAX_BUFFER
                del buf[:excess]
                self._diag.n_resync_drops += 1
                self._diag.n_sync_errors += excess

            # --- drain all complete frames in buffer ---
            self._parse_frames(buf)

            # --- watchdog check ---
            now = time.monotonic()
            if now - last_watchdog_check > 0.2:   # check every 200 ms
                last_watchdog_check = now
                age = now - (self._diag.last_frame_t_s or now)
                if age > self._watchdog_s:
                    self._fire_watchdog(
                        f"watchdog: no frame for {age:.2f}s")
                    if self._auto_reconnect:
                        if not self._handle_disconnect(reconnect_attempt):
                            return
                        reconnect_attempt += 1
                        buf.clear()
                        # Reset last_frame so reconnected link gets a fresh
                        # window before tripping again
                        self._diag.last_frame_t_s = time.monotonic()
                else:
                    reconnect_attempt = 0  # healthy → forget past failures

    def _parse_frames(self, buf: bytearray) -> None:
        """
        Drain all complete frames from buf in place.
        Leaves any partial trailing data for the next read.
        """
        FRAME_TOTAL = len(self.MAGIC) + TELEM_BYTES   # 2 + 64 = 66
        i = 0
        n = len(buf)
        while i + FRAME_TOTAL <= n:
            # Look for MAGIC
            if buf[i] != self.MAGIC[0] or buf[i + 1] != self.MAGIC[1]:
                # Need to resync. Find next MAGIC[0] candidate.
                j = buf.find(self.MAGIC[0:1], i + 1, n)
                if j < 0:
                    # No candidate at all — drop everything we scanned
                    self._diag.n_sync_errors += (n - i)
                    i = n
                    break
                self._diag.n_sync_errors += (j - i)
                i = j
                continue
            # Have MAGIC at i — try to decode 64B payload
            payload = bytes(buf[i + 2 : i + FRAME_TOTAL])
            frame = TelemetryFrame.unpack(payload)
            if frame is None:
                # CRC fail — drop only the 0xAA byte (it might have been
                # noise that coincidentally matched), advance and resync
                self._diag.n_crc_errors += 1
                i += 1
                continue
            # Good frame
            self._diag.n_frames_ok += 1
            self._diag.last_frame_t_s = time.monotonic()
            # Mirror to base class counters for API compat
            self._n_frames = self._diag.n_frames_ok
            self._n_crc_err = self._diag.n_crc_errors
            self._n_sync_err = self._diag.n_sync_errors
            self._dispatch(frame)
            i += FRAME_TOTAL

        # Keep anything from i onwards as partial (incomplete frame)
        if i > 0:
            partial = n - i
            if partial > 0 and partial < FRAME_TOTAL:
                self._diag.n_partial_packets += 1
            del buf[:i]

    def _handle_disconnect(self, attempt: int) -> bool:
        """
        Called when the port errored or watchdog tripped.
        If auto_reconnect: try to re-open with backoff.
        Returns True if we should keep the reader loop running, False to exit.
        """
        self._close_port()

        if not self._auto_reconnect or self._stop.is_set():
            self._state = ConnectionState.ERROR
            return False

        # Backoff
        backoff = RECONNECT_BACKOFF_S[
            min(attempt, len(RECONNECT_BACKOFF_S) - 1)]
        # Sleep in small steps so disconnect() doesn't block too long
        deadline = time.monotonic() + backoff
        while time.monotonic() < deadline:
            if self._stop.is_set():
                self._state = ConnectionState.DISCONNECTED
                return False
            time.sleep(0.05)

        self._diag.n_reconnect_tries += 1
        self._state = ConnectionState.CONNECTING
        if self._open_port():
            self._diag.n_reconnects_ok += 1
            self._state = ConnectionState.CONNECTED
            return True
        # Open failed — stay in CONNECTING/ERROR and try again next iteration
        return True
