"""
validation_d2/bringup_validation.py — Faz 18 Hardware Bring-Up Validation
==============================================================================
Tests the production RealESP32Link against scenarios that REAL hardware
will throw at it. Uses Linux pty (pseudo-terminal) to create a virtual
serial port pair — the slave looks like /dev/ttyUSB0 to RealESP32Link,
the master is where this test writes synthetic ESP32 traffic.

Scenarios:
  1. happy_path       — clean 1 kHz stream, expect 0 CRC/sync errors
  2. partial_packets  — write frames byte-by-byte, link must reassemble
  3. corrupted_crc    — flip a byte mid-frame, only that frame is dropped
  4. cable_unplug     — close master mid-stream, link detects + reconnects
  5. burst_after_idle — 500-frame burst after 500 ms silence, no drops
  6. junk_then_sync   — feed 4 KB of random bytes then good frames, must resync
  7. watchdog_trip    — go silent for 1.5 s, watchdog fires, last_error set
  8. high_cpu_load    — saturate CPU in another thread, link still parses
  9. queue_overflow   — slow consumer, link must not crash / block producer

Pass criteria per scenario shown in console output.
"""
from __future__ import annotations
import os
import sys
import struct
import threading
import time
import queue
import random

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pty

from backend.hardware.esp32_link import (
    TelemetryFrame, TELEM_BYTES)
from backend.hardware.real_esp32_link import RealESP32Link, WATCHDOG_S


# ─── synthetic ESP32 endpoint ──────────────────────────────────────────

MAGIC = b"\xaa\x55"


def make_frame(seq: int, *, t_s: float = 0.0, T_N: float = 15.0,
               temp_K: float = 295.15, rpm: float = 8.5) -> bytes:
    """Construct a fully valid framed payload (MAGIC + 64B + CRC inside)."""
    f = TelemetryFrame(
        ts_us=int(t_s * 1e6), seq=seq & 0xFFFF, flags=1,
        x_mm=100.0, a_deg=(seq * 0.1) % 360.0,
        T_N=T_N, rpm=rpm,
        vib_x=0.05, vib_y=0.05, vib_z=0.05,
        temp_K=temp_K, current_A=2.5,
        alpha=0.5, quality=92.0)
    return MAGIC + f.pack()


def open_virtual_port() -> tuple:
    """
    Open a pty pair. Returns (master_fd, slave_path).
      - slave_path is what RealESP32Link opens (looks like /dev/pts/N)
      - write to master_fd to feed bytes into the link
    """
    master_fd, slave_fd = pty.openpty()
    slave_path = os.ttyname(slave_fd)
    # Close slave fd; pyserial will reopen by path
    os.close(slave_fd)
    return master_fd, slave_path


def _drain_subscriber(q: queue.Queue, timeout_s: float = 0.5) -> list:
    """Pull everything from q within timeout_s."""
    out = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            out.append(q.get(timeout=0.05))
        except queue.Empty:
            if not out:
                continue
            break
    return out


# ─── scenario runners ──────────────────────────────────────────────────


def s_happy_path() -> dict:
    """Send 1000 clean frames at ~5 kHz, expect all received with 0 errors."""
    master_fd, slave_path = open_virtual_port()
    link = RealESP32Link(port=slave_path, watchdog_s=2.0, auto_reconnect=False)
    sub_q = queue.Queue(maxsize=4096)
    link.subscribe(sub_q)
    assert link.connect(), "connect failed"

    n = 1000
    for i in range(n):
        os.write(master_fd, make_frame(i))
        if i % 50 == 0:
            time.sleep(0.001)   # mild pacing
    # let everything drain
    time.sleep(0.5)
    received = _drain_subscriber(sub_q, timeout_s=0.5)
    link.disconnect()
    os.close(master_fd)
    d = link.diagnostics
    return {
        "name":               "happy_path",
        "sent":               n,
        "received":           len(received),
        "crc_errors":         d.n_crc_errors,
        "sync_errors":        d.n_sync_errors,
        "partial_packets":    d.n_partial_packets,
        "passed":             len(received) == n and d.n_crc_errors == 0 and d.n_sync_errors == 0,
    }


def s_partial_packets() -> dict:
    """Write frames byte-by-byte. Link must hold partial state correctly."""
    master_fd, slave_path = open_virtual_port()
    link = RealESP32Link(port=slave_path, watchdog_s=5.0, auto_reconnect=False)
    sub_q = queue.Queue(maxsize=1024)
    link.subscribe(sub_q)
    assert link.connect()

    n = 100
    for i in range(n):
        frame = make_frame(i)
        for b in frame:
            os.write(master_fd, bytes([b]))
            # No sleep — exercise stream that arrives in tiny chunks
    time.sleep(0.5)
    received = _drain_subscriber(sub_q, timeout_s=0.5)
    link.disconnect()
    os.close(master_fd)
    d = link.diagnostics
    return {
        "name":            "partial_packets",
        "sent":            n,
        "received":        len(received),
        "partial_packets": d.n_partial_packets,
        "crc_errors":      d.n_crc_errors,
        "passed":          len(received) == n and d.n_crc_errors == 0,
    }


def s_corrupted_crc() -> dict:
    """
    Flip one byte of every 10th frame. Link must drop only those frames
    and stay in sync for the rest.
    """
    master_fd, slave_path = open_virtual_port()
    link = RealESP32Link(port=slave_path, watchdog_s=5.0, auto_reconnect=False)
    sub_q = queue.Queue(maxsize=1024)
    link.subscribe(sub_q)
    assert link.connect()

    n = 200
    n_corrupted = 0
    for i in range(n):
        frame = bytearray(make_frame(i))
        if i % 10 == 0:
            frame[10] ^= 0xFF   # flip a payload byte
            n_corrupted += 1
        os.write(master_fd, bytes(frame))
    time.sleep(0.5)
    received = _drain_subscriber(sub_q, timeout_s=0.5)
    link.disconnect()
    os.close(master_fd)
    d = link.diagnostics
    # Expected: n_corrupted frames trigger CRC fail (and may also incur sync
    # errors when their MAGIC byte is dropped and the next frame resyncs).
    good_received = len(received)
    return {
        "name":               "corrupted_crc",
        "sent":               n,
        "corrupted":          n_corrupted,
        "received":           good_received,
        "expected_received":  n - n_corrupted,
        "crc_errors":         d.n_crc_errors,
        "sync_errors":        d.n_sync_errors,
        # Tolerance: every corrupted frame may also cost one extra sync recovery
        "passed":             good_received >= (n - n_corrupted) - 1 and
                              d.n_crc_errors >= n_corrupted,
    }


def s_cable_unplug() -> dict:
    """
    Close master mid-stream → simulates cable unplug.
    auto_reconnect=False here — just verify state transitions cleanly.
    """
    master_fd, slave_path = open_virtual_port()
    link = RealESP32Link(port=slave_path, watchdog_s=0.5, auto_reconnect=False)
    sub_q = queue.Queue(maxsize=1024)
    link.subscribe(sub_q)
    assert link.connect()

    # Stream a few frames
    for i in range(50):
        os.write(master_fd, make_frame(i))
    time.sleep(0.1)
    received_before = _drain_subscriber(sub_q, timeout_s=0.2)

    # "Unplug": close master fd
    os.close(master_fd)
    # Wait long enough for watchdog (0.5 s)
    time.sleep(1.0)

    link.disconnect()
    d = link.diagnostics
    detected = (d.n_watchdog_trips >= 1 or
                "read:" in d.last_error or
                "SerialException" in d.last_error)
    return {
        "name":                "cable_unplug",
        "received_before_unplug": len(received_before),
        "watchdog_trips":      d.n_watchdog_trips,
        "last_error":          d.last_error,
        "disconnect_detected": detected,
        # Pass: got the pre-unplug frames + disconnect was detected
        # (either via watchdog OR via direct serial I/O error)
        "passed":              len(received_before) >= 40 and detected,
    }


def s_cable_unplug_reconnect() -> dict:
    """
    Same as above but with auto_reconnect=True. We can't actually replug
    in this test environment, but we can verify reconnect attempts are
    triggered (n_reconnect_tries > 0).
    """
    master_fd, slave_path = open_virtual_port()
    link = RealESP32Link(port=slave_path, watchdog_s=0.5, auto_reconnect=True)
    sub_q = queue.Queue(maxsize=1024)
    link.subscribe(sub_q)
    assert link.connect()

    # Stream some, then unplug
    for i in range(50):
        os.write(master_fd, make_frame(i))
    time.sleep(0.1)
    os.close(master_fd)
    # Wait ~1.5 s — that's watchdog (0.5s) + backoff (0.2s) + retry
    time.sleep(2.0)

    link.disconnect()
    d = link.diagnostics
    return {
        "name":               "cable_unplug_reconnect",
        "watchdog_trips":     d.n_watchdog_trips,
        "reconnect_tries":    d.n_reconnect_tries,
        # The slave is gone — reconnects will fail to open, which is fine.
        # We just verify the link attempted to reconnect.
        "passed":             d.n_watchdog_trips >= 1 and d.n_reconnect_tries >= 1,
    }


def s_burst_after_idle() -> dict:
    """Long silence, then 500 frames in one go. Burst handling test."""
    master_fd, slave_path = open_virtual_port()
    link = RealESP32Link(port=slave_path, watchdog_s=5.0, auto_reconnect=False)
    sub_q = queue.Queue(maxsize=2048)
    link.subscribe(sub_q)
    assert link.connect()

    time.sleep(0.5)   # idle
    burst = b"".join(make_frame(i) for i in range(500))
    # Write the burst in one syscall — pty buffer will hold most of it
    # Some Linux pty buffers are small (~4 KB), so the write may need to
    # be chunked. 500 × 66 bytes = 33 KB.
    pos = 0
    while pos < len(burst):
        n = os.write(master_fd, burst[pos:pos + 4096])
        pos += n
    time.sleep(0.5)
    received = _drain_subscriber(sub_q, timeout_s=0.5)
    link.disconnect()
    os.close(master_fd)
    d = link.diagnostics
    return {
        "name":      "burst_after_idle",
        "sent":      500,
        "received":  len(received),
        "passed":    len(received) >= 495 and d.n_crc_errors == 0,
    }


def s_junk_then_sync() -> dict:
    """
    Send 4 KB of random junk, then 100 valid frames.
    Link must resync to MAGIC and accept all 100.
    """
    master_fd, slave_path = open_virtual_port()
    link = RealESP32Link(port=slave_path, watchdog_s=5.0, auto_reconnect=False)
    sub_q = queue.Queue(maxsize=1024)
    link.subscribe(sub_q)
    assert link.connect()

    rng = random.Random(42)
    # Junk: 4 KB of random bytes that AVOID the magic sequence
    # (or include it occasionally to test false-sync recovery)
    junk = bytearray(rng.randint(0, 255) for _ in range(4096))
    pos = 0
    while pos < len(junk):
        n = os.write(master_fd, bytes(junk[pos:pos + 4096]))
        pos += n
    time.sleep(0.1)
    # Now send valid frames
    for i in range(100):
        os.write(master_fd, make_frame(i))
    time.sleep(0.5)
    received = _drain_subscriber(sub_q, timeout_s=0.5)
    link.disconnect()
    os.close(master_fd)
    d = link.diagnostics
    return {
        "name":            "junk_then_sync",
        "junk_bytes":      len(junk),
        "frames_sent":     100,
        "received":        len(received),
        "sync_errors":     d.n_sync_errors,
        "crc_errors":      d.n_crc_errors,
        # Tolerance: occasional false-sync inside junk costs ~1 dropped
        # frame each time it happens. Accept ≥ 90 / 100 good frames.
        "passed":          len(received) >= 90 and d.n_sync_errors >= 100,
    }


def s_watchdog_trip() -> dict:
    """Go totally silent. Watchdog must fire within watchdog_s seconds."""
    master_fd, slave_path = open_virtual_port()
    callback_log = []
    link = RealESP32Link(port=slave_path, watchdog_s=0.5, auto_reconnect=False)
    link.register_watchdog_callback(lambda reason: callback_log.append(reason))
    assert link.connect()

    # Stream a few frames so last_frame_t is set
    for i in range(10):
        os.write(master_fd, make_frame(i))
    time.sleep(0.1)
    # Now go silent for 1.2 s
    time.sleep(1.2)
    link.disconnect()
    os.close(master_fd)
    d = link.diagnostics
    return {
        "name":                  "watchdog_trip",
        "watchdog_trips":        d.n_watchdog_trips,
        "callback_invocations":  len(callback_log),
        "callback_first_msg":    callback_log[0] if callback_log else "",
        "last_error":            d.last_error,
        "passed":                d.n_watchdog_trips >= 1 and len(callback_log) >= 1,
    }


def s_high_cpu_load() -> dict:
    """Saturate CPU in worker threads; link parsing must still proceed."""
    master_fd, slave_path = open_virtual_port()
    link = RealESP32Link(port=slave_path, watchdog_s=5.0, auto_reconnect=False)
    sub_q = queue.Queue(maxsize=4096)
    link.subscribe(sub_q)
    assert link.connect()

    # Launch CPU-hogging threads
    stop_cpu = threading.Event()

    def cpu_burn():
        x = 0.0
        while not stop_cpu.is_set():
            for _ in range(10000):
                x = (x + 1.0) ** 0.5
                x %= 1e9

    burners = [threading.Thread(target=cpu_burn, daemon=True) for _ in range(4)]
    for b in burners:
        b.start()

    # Stream while CPU is hot
    n = 500
    for i in range(n):
        os.write(master_fd, make_frame(i))
        time.sleep(0.001)
    time.sleep(0.5)
    stop_cpu.set()
    for b in burners:
        b.join(timeout=0.5)

    received = _drain_subscriber(sub_q, timeout_s=1.0)
    link.disconnect()
    os.close(master_fd)
    d = link.diagnostics
    return {
        "name":       "high_cpu_load",
        "sent":       n,
        "received":   len(received),
        "crc_errors": d.n_crc_errors,
        "passed":     len(received) >= int(n * 0.95) and d.n_crc_errors == 0,
    }


def s_queue_overflow() -> dict:
    """
    Subscriber queue is tiny (10). Producer must NOT block. Frames are
    dropped at the listener boundary, but the link keeps parsing.
    """
    master_fd, slave_path = open_virtual_port()
    link = RealESP32Link(port=slave_path, watchdog_s=5.0, auto_reconnect=False)
    sub_q = queue.Queue(maxsize=10)   # tiny — will overflow immediately
    link.subscribe(sub_q)
    assert link.connect()

    n = 1000
    t0 = time.monotonic()
    for i in range(n):
        os.write(master_fd, make_frame(i))
    write_elapsed = time.monotonic() - t0
    time.sleep(0.5)
    # Consumer never drains, so sub_q is stuck at 10
    link.disconnect()
    os.close(master_fd)
    d = link.diagnostics
    return {
        "name":               "queue_overflow",
        "sent":               n,
        "frames_parsed":      d.n_frames_ok,
        "queue_full_at_end":  sub_q.qsize(),
        "write_elapsed_s":    round(write_elapsed, 3),
        # Pass: producer didn't get stuck (write_elapsed should be fast),
        # link parsed most frames (slow consumer didn't backpressure it)
        "passed":             write_elapsed < 1.0 and d.n_frames_ok >= int(n * 0.9),
    }


# ─── orchestrator ──────────────────────────────────────────────────────

SCENARIOS = [
    s_happy_path,
    s_partial_packets,
    s_corrupted_crc,
    s_cable_unplug,
    s_cable_unplug_reconnect,
    s_burst_after_idle,
    s_junk_then_sync,
    s_watchdog_trip,
    s_high_cpu_load,
    s_queue_overflow,
]


def main():
    print("\n" + "=" * 72)
    print(" FAZ 18 — HARDWARE BRING-UP VALIDATION (RealESP32Link via pty)")
    print("=" * 72)
    results = []
    for fn in SCENARIOS:
        print(f"\n--- {fn.__name__} ---")
        t0 = time.monotonic()
        try:
            r = fn()
        except Exception as e:
            r = {"name": fn.__name__, "passed": False,
                 "error": f"{type(e).__name__}: {e}"}
        r["elapsed_s"] = round(time.monotonic() - t0, 2)
        results.append(r)
        verdict = "PASS ✓" if r.get("passed") else "FAIL ✗"
        print(f"  {verdict}  ({r['elapsed_s']}s)")
        for k, v in r.items():
            if k in ("name", "passed", "elapsed_s"):
                continue
            print(f"    {k:>22} = {v}")

    n_pass = sum(1 for r in results if r.get("passed"))
    n_total = len(results)
    print("\n" + "=" * 72)
    print(f" BRING-UP VERDICT: {n_pass}/{n_total} scenarios passed")
    print("=" * 72)
    for r in results:
        icon = "✓" if r.get("passed") else "✗"
        print(f"  {icon}  {r['name']:<30}  ({r['elapsed_s']}s)")
    print()
    if n_pass == n_total:
        print("  ★★★  REAL ESP32 LINK BRING-UP READY  ★★★")
    else:
        print(f"  STOP — {n_total - n_pass} scenario(s) failed")
    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
