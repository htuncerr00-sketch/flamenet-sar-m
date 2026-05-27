"""
industrial_watchdog.py — Endüstriyel Watchdog (10ms döngü, deadlock-free)
"""
from __future__ import annotations
import queue, threading, time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Tuple

class Priority(Enum):
    FATAL=0; CRITICAL=1; WARNING=2; INFO=3

class WatchdogEvent(Enum):
    WATCHDOG_TIMEOUT="watchdog_timeout"; SPINDLE_OVERSPEED="spindle_overspeed"
    TENSION_HIGH="tension_high"; TENSION_LOW="tension_low"; SOFT_LIMIT_X="soft_limit_x"
    ENCODER_MISMATCH="encoder_mismatch"; MISSED_STEPS="missed_steps"
    FIBER_BREAK="fiber_break"; THERMAL_RUNAWAY="thermal_runaway"; OK="ok"

@dataclass(slots=True)
class WatchdogAlert:
    event:WatchdogEvent; priority:Priority; value:float; threshold:float
    message:str; timestamp:float; action:str

class IndustrialWatchdog:
    """
    10ms safety loop — non-blocking, deadlock-free.
    Callbacks called OUTSIDE any lock.
    """
    POLL_MS = 10.0
    def __init__(self,
        tension_max_N:float=40.0, tension_min_N:float=2.0,
        rpm_max:float=260.0, x_max_mm:float=390.0, x_min_mm:float=-10.0,
        watchdog_timeout_s:float=0.3,
        on_alert:Optional[Callable[[WatchdogAlert],None]]=None,
        on_estop:Optional[Callable[[],None]]=None):

        self.cfg = dict(T_max=tension_max_N,T_min=tension_min_N,rpm_max=rpm_max,
            x_max=x_max_mm,x_min=x_min_mm,wd_timeout=watchdog_timeout_s)
        self._on_alert = on_alert or (lambda a:None)
        self._on_estop = on_estop or (lambda:None)

        # State — written by multiple threads, read atomically
        self._x_mm         = 0.0
        self._rpm           = 0.0
        self._tension_N     = 15.0
        self._temp_C        = 20.0
        self._encoder_x     = 0.0
        self._fiber_ok      = True
        self._last_hb       = time.monotonic()

        self._estop_active  = False
        self._alerts:List[WatchdogAlert] = []
        self._alert_q:queue.Queue = queue.Queue(maxsize=64)

        self._lock   = threading.Lock()   # Only for _alerts list & state snapshot
        self._stop   = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Fiber interlock
        self._fiber_attached = True
        self._rewind_locked  = True

    # ── Public update methods (called from SensorThread) ─────────
    def update(self, x_mm:float=None, rpm:float=None, tension_N:float=None,
               temp_C:float=None, encoder_x:float=None, fiber_ok:bool=None):
        """Non-blocking state update. Called from any thread."""
        if x_mm       is not None: self._x_mm       = x_mm
        if rpm         is not None: self._rpm         = rpm
        if tension_N   is not None: self._tension_N   = tension_N
        if temp_C      is not None: self._temp_C      = temp_C
        if encoder_x   is not None: self._encoder_x   = encoder_x
        if fiber_ok    is not None: self._fiber_ok    = fiber_ok

    def heartbeat(self): self._last_hb = time.monotonic()

    # ── Fiber interlock ───────────────────────────────────────────
    def set_fiber_attached(self, v:bool):
        self._fiber_attached = v; self._rewind_locked = v
        state = "BAĞLI→kilitli" if v else "KESİLDİ→serbest"
        print(f"    [WD] Fiber: {state}")

    def request_rewind(self) -> bool:
        locked = self._rewind_locked
        if locked:
            self._fire(WatchdogAlert(WatchdogEvent.FIBER_BREAK,Priority.CRITICAL,
                0.0,0.0,"Fiber bağlıyken rewind REDDEDİLDİ",time.time(),"blocked"))
        return not locked

    def confirm_fiber_cut(self): self.set_fiber_attached(False)

    # ── Start/stop ───────────────────────────────────────────────
    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop,daemon=True,name="Watchdog")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join(timeout=0.3)

    def clear_estop(self): self._estop_active = False

    @property
    def is_estop(self): return self._estop_active

    # ── Monitor loop (10ms, non-blocking) ────────────────────────
    def _loop(self):
        while not self._stop.is_set():
            t0 = time.monotonic()
            if not self._estop_active:
                self._check_watchdog(); self._check_tension(); self._check_rpm()
                self._check_position(); self._check_fiber()
            # Drain alert queue — callbacks called here, outside any lock
            while True:
                try:
                    alert = self._alert_q.get_nowait()
                    self._on_alert(alert)
                    if alert.priority == Priority.FATAL:
                        self._estop_active = True
                        self._on_estop()
                except queue.Empty: break
            elapsed = (time.monotonic()-t0)*1000
            sleep_s = max(0.0,(self.POLL_MS - elapsed)/1000)
            time.sleep(sleep_s)

    def _fire(self, alert:WatchdogAlert):
        with self._lock: self._alerts.append(alert)
        try: self._alert_q.put_nowait(alert)
        except queue.Full: pass  # Never block in fire

    def _check_watchdog(self):
        dt = time.monotonic()-self._last_hb
        if dt > self.cfg["wd_timeout"]:
            self._fire(WatchdogAlert(WatchdogEvent.WATCHDOG_TIMEOUT,Priority.FATAL,
                dt*1000,self.cfg["wd_timeout"]*1000,
                f"Heartbeat timeout {dt*1000:.0f}ms",time.time(),"E-STOP"))

    def _check_tension(self):
        T = self._tension_N
        if T > self.cfg["T_max"]:
            self._fire(WatchdogAlert(WatchdogEvent.TENSION_HIGH,Priority.FATAL,
                T,self.cfg["T_max"],f"T={T:.2f}>{self.cfg['T_max']}N",time.time(),"E-STOP"))
        elif T < self.cfg["T_min"] and T > 0.1:
            self._fire(WatchdogAlert(WatchdogEvent.TENSION_LOW,Priority.CRITICAL,
                T,self.cfg["T_min"],f"T={T:.2f}<{self.cfg['T_min']}N",time.time(),"PAUSE"))

    def _check_rpm(self):
        if self._rpm > self.cfg["rpm_max"]*1.05:
            self._fire(WatchdogAlert(WatchdogEvent.SPINDLE_OVERSPEED,Priority.FATAL,
                self._rpm,self.cfg["rpm_max"],
                f"RPM={self._rpm:.1f}>{self.cfg['rpm_max']}",time.time(),"E-STOP"))

    def _check_position(self):
        x = self._x_mm
        if x < self.cfg["x_min"]:
            self._fire(WatchdogAlert(WatchdogEvent.SOFT_LIMIT_X,Priority.FATAL,
                x,self.cfg["x_min"],f"X={x:.2f}<{self.cfg['x_min']}mm",time.time(),"E-STOP"))
        if x > self.cfg["x_max"]:
            self._fire(WatchdogAlert(WatchdogEvent.SOFT_LIMIT_X,Priority.FATAL,
                x,self.cfg["x_max"],f"X={x:.2f}>{self.cfg['x_max']}mm",time.time(),"E-STOP"))

    def _check_fiber(self):
        if not self._fiber_ok:
            self._fire(WatchdogAlert(WatchdogEvent.FIBER_BREAK,Priority.FATAL,
                0.0,0.0,"Fiber break tespit edildi",time.time(),"E-STOP"))

    def alerts(self,n:int=20) -> List[WatchdogAlert]:
        with self._lock: return list(self._alerts[-n:])

    def report(self) -> str:
        al = self.alerts()
        if not al: return "  [WD] Olay yok — normal"
        return "\n".join(f"  [{a.priority.name}] {a.event.value}: {a.message}" for a in al[-6:])
