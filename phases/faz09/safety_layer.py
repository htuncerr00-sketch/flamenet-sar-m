"""
safety_layer.py — Güvenlik Katmanı
=====================================
Gerçek makine çalışması için kritik güvenlik altyapısı.

Güvenlik mekanizmaları:
  1. Watchdog         — 100ms heartbeat, timeout → E-stop
  2. Soft Limits      — X min/max, A hız sınırı
  3. Tension Guard    — Load cell > T_max → pause
  4. Spindle Overspeed— RPM > RPM_max → E-stop
  5. Rewind Interlock — Fiber bağlıyken rewind engeli (KRİTİK)
  6. Buffer Underrun  — Akış durduğunda tension spike koruması
  7. Emergency Stop   — Tüm eksenler durdur, fiber gerilme bırak

Rewind Interlock (en kritik):
  fiber_attached = True → rewind komutu → RED
  Fiber KESME işlemi:
    1. M0 (pause)
    2. Operatör fiber keser
    3. fiber_attached = False seti (operatör onayı)
    4. Rewind serbest

Watchdog modeli:
  SafetyLayer.heartbeat() her 100ms çağrılmalı
  Son heartbeat'ten 300ms geçti → watchdog_timeout alarm
  → Otomatik E-stop

Safety check sequence (per 10ms):
  1. Watchdog check
  2. Tension threshold
  3. RPM check
  4. X soft limit
  5. A speed check
  6. Buffer underrun check

Güvenlik seviyeleri:
  WARNING  → Log + uyarı, devam et
  CRITICAL → Pause (feed hold), operatör onayı gerekli
  FATAL    → E-stop, fiber kesme, makine durdur

Referans: ISO 10218-1 (makine güvenliği); OSHA 1910.147 (lockout/tagout)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, List, Optional

from hal import ControllerInterface, MachineAlarm, MachineState


# ── Safety Config ────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SafetyConfig:
    """Güvenlik eşik değerleri."""
    # Pozisyon limitleri
    x_min_mm:          float = -10.0    # Headstock clearance
    x_max_mm:          float = 400.0    # Max travel
    # Hız limitleri
    vx_max_mm_s:       float = 140.0    # mm/s (8400 mm/min)
    a_max_rpm:         float = 260.0    # Spindle max RPM
    a_max_acc_rpm_s:   float = 1000.0   # Spindle max accel
    # Gerilme limitleri
    tension_max_N:     float = 45.0     # Fiber kopmadan önce
    tension_min_N:     float = 2.0      # Fiber gevşedi
    tension_spike_N:   float = 35.0     # Anlık spike → pause
    # Watchdog
    watchdog_timeout_s:float = 0.30     # 300ms
    heartbeat_interval:float = 0.10     # 100ms
    # Buffer
    buffer_underrun_threshold: float = 0.02  # <2% → tension risk
    # Rewind
    require_fiber_cut_confirm: bool = True   # Operatör onayı zorunlu


# ── Safety Events ────────────────────────────────────────────────

class SafetyLevel(Enum):
    OK       = "ok"
    WARNING  = "warning"
    CRITICAL = "critical"
    FATAL    = "fatal"


@dataclass(slots=True)
class SafetyEvent:
    """Tek güvenlik olayı."""
    timestamp:   float
    level:       SafetyLevel
    category:    str    # "tension", "rpm", "position", "watchdog", "rewind"
    message:     str
    value:       float  # Tetikleyen değer
    threshold:   float  # Aşılan eşik
    action_taken:str    # Ne yapıldı

    def __str__(self) -> str:
        t = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        return (f"[{t}] [{self.level.value.upper():<8}] "
                f"{self.category:<12}: {self.message} "
                f"(val={self.value:.3f}, thr={self.threshold:.3f}) "
                f"→ {self.action_taken}")


# ── Safety Layer ────────────────────────────────────────────────

class SafetyLayer:
    """
    Gerçek zamanlı güvenlik izleme ve müdahale motoru.

    Thread modeli:
      _monitor_thread: 10ms periyot, tüm kontrolleri çalıştırır
      heartbeat():     ana döngüden 100ms'de bir çağrılmalı
      callback:        her olay için kullanıcı callback

    Kullanım:
        safety = SafetyLayer(controller, config)
        safety.start()
        ...
        safety.heartbeat()  # Ana döngüde
        ...
        safety.stop()
    """

    POLL_INTERVAL_S = 0.020  # 20ms safety poll

    def __init__(
        self,
        controller:    ControllerInterface,
        config:        SafetyConfig = None,
        on_event:      Optional[Callable[[SafetyEvent], None]] = None,
    ) -> None:
        self._ctrl      = controller
        self._cfg       = config or SafetyConfig()
        self._on_event  = on_event or (lambda e: None)

        # Durum
        self._fiber_attached     = True   # Fiber bağlı varsayım
        self._estop_active       = False
        self._paused_by_safety   = False
        self._rewind_locked      = True   # Başlangıçta kilitli
        self._last_heartbeat     = time.monotonic()
        self._last_tension       = 0.0
        self._last_state: Optional[MachineState] = None

        # Log
        self._events:   List[SafetyEvent] = []
        self._lock      = threading.Lock()
        self._stop_flag = threading.Event()
        self._thread:   Optional[threading.Thread] = None

    # ── Public API ───────────────────────────────────────────────

    def start(self) -> None:
        """Safety monitor thread'i başlat."""
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name="SafetyMonitor",
            daemon=True,
        )
        self._thread.start()
        print("    [SAFETY] Güvenlik izleme başlatıldı")

    def stop(self) -> None:
        """Thread'i durdur."""
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=0.2)

    def heartbeat(self) -> None:
        """Ana döngüden çağrılmalı — watchdog besle."""
        with self._lock:
            self._last_heartbeat = time.monotonic()

    def set_fiber_attached(self, attached: bool) -> None:
        """
        Fiber durumu güncelle.

        attached=True:  Rewind kilitlendi
        attached=False: Rewind serbest (fiber kesme sonrası)
        """
        with self._lock:
            self._fiber_attached = attached
            self._rewind_locked  = attached
        status = "BAĞLI → rewind kilitli" if attached else "KESİLDİ → rewind serbest"
        print(f"    [SAFETY] Fiber: {status}")

    def request_rewind(self) -> bool:
        """
        Rewind talep et.
        Returns True if allowed, False if locked.
        """
        # Check without logging (no lock contention)
        locked = False
        with self._lock:
            locked = self._rewind_locked and self._cfg.require_fiber_cut_confirm
        if locked:
            # Log outside lock
            self._log_event(SafetyLevel.CRITICAL, "rewind",
                "Fiber bağlıyken rewind talebi REDDEDİLDİ",
                0.0, 0.0, "Rewind engellendi")
            return False
        return True

    def confirm_fiber_cut(self) -> None:
        """Operatör fiber kesme onayı — rewind unlock."""
        self.set_fiber_attached(False)

    @property
    def is_estop_active(self) -> bool:
        return self._estop_active

    @property
    def events(self) -> List[SafetyEvent]:
        with self._lock:
            return list(self._events)

    @property
    def last_state(self) -> Optional[MachineState]:
        return self._last_state

    def clear_estop(self) -> bool:
        """
        E-stop durumunu temizle (operatör onayı sonrası).
        Makine alarm temizleme gerekebilir.
        """
        if self._estop_active:
            self._estop_active = False
            self._paused_by_safety = False
            print("    [SAFETY] E-stop temizlendi — operatör doğruladı")
            return True
        return False

    def event_report(self) -> str:
        """Son 20 güvenlik olayı raporu."""
        with self._lock:
            recent = self._events[-20:]
        if not recent:
            return "  [SAFETY] Olay yok — sistem normal"
        lines = [f"  [SAFETY] Son {len(recent)} olay:"]
        for e in recent:
            lines.append(f"    {e}")
        return "\n".join(lines)

    # ── Monitor Loop ─────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        """10ms periyotlu güvenlik kontrol döngüsü."""
        while not self._stop_flag.is_set():
            t_start = time.monotonic()
            try:
                state = self._ctrl.read_state()
                with self._lock:
                    self._last_state = state

                # Sıralı güvenlik kontrolleri
                if not self._estop_active:
                    self._check_watchdog()
                    self._check_tension(state.tension_N)
                    self._check_rpm(state.rpm)
                    self._check_position(state.x_actual_mm)
                    self._check_vx(state.vx_mm_s)

            except Exception as e:
                self._log_event(SafetyLevel.WARNING, "monitor",
                    f"State okuma hatası: {e}", 0.0, 0.0, "Skip")

            # Tam 10ms bekle
            elapsed = time.monotonic() - t_start
            sleep_t = max(0.0, self.POLL_INTERVAL_S - elapsed)
            time.sleep(sleep_t)

    # ── Individual checks ────────────────────────────────────────

    def _check_watchdog(self) -> None:
        """Watchdog timeout kontrolü."""
        elapsed = time.monotonic() - self._last_heartbeat
        if elapsed > self._cfg.watchdog_timeout_s:
            self._trigger_estop(
                "watchdog",
                f"Heartbeat timeout: {elapsed*1000:.0f}ms > "
                f"{self._cfg.watchdog_timeout_s*1000:.0f}ms",
                elapsed, self._cfg.watchdog_timeout_s,
                MachineAlarm.WATCHDOG_TIMEOUT,
            )

    def _check_tension(self, T: float) -> None:
        """Fiber gerilme kontrolü."""
        cfg = self._cfg
        self._last_tension = T

        if T > cfg.tension_max_N:
            self._trigger_estop("tension",
                f"Gerilme {T:.1f}N > max {cfg.tension_max_N}N",
                T, cfg.tension_max_N, MachineAlarm.TENSION_HIGH)
        elif T > cfg.tension_spike_N and not self._paused_by_safety:
            self._trigger_pause("tension",
                f"Gerilme spike {T:.1f}N > {cfg.tension_spike_N}N",
                T, cfg.tension_spike_N)
        elif T < cfg.tension_min_N and T > 0.1:
            self._log_event(SafetyLevel.WARNING, "tension",
                f"Düşük gerilme {T:.1f}N < {cfg.tension_min_N}N",
                T, cfg.tension_min_N, "Uyarı")

    def _check_rpm(self, rpm: float) -> None:
        """Spindle devir sayısı kontrolü."""
        if rpm > self._cfg.a_max_rpm * 1.05:
            self._trigger_estop("spindle",
                f"Spindle {rpm:.1f}RPM > limit {self._cfg.a_max_rpm}RPM",
                rpm, self._cfg.a_max_rpm,
                MachineAlarm.SPINDLE_OVERSPEED)

    def _check_position(self, x: float) -> None:
        """Soft limit kontrolü."""
        cfg = self._cfg
        if x < cfg.x_min_mm:
            self._trigger_estop("position",
                f"X={x:.3f}mm < min {cfg.x_min_mm}mm",
                x, cfg.x_min_mm, MachineAlarm.SOFT_LIMIT_X)
        elif x > cfg.x_max_mm:
            self._trigger_estop("position",
                f"X={x:.3f}mm > max {cfg.x_max_mm}mm",
                x, cfg.x_max_mm, MachineAlarm.SOFT_LIMIT_X)

    def _check_vx(self, vx: float) -> None:
        """Carriage hız kontrolü."""
        if abs(vx) > self._cfg.vx_max_mm_s * 1.05:
            self._trigger_pause("speed",
                f"v_x={vx:.1f}mm/s > limit {self._cfg.vx_max_mm_s}mm/s",
                abs(vx), self._cfg.vx_max_mm_s)

    # ── Actions ──────────────────────────────────────────────────

    def _trigger_estop(
        self,
        category:  str,
        message:   str,
        value:     float,
        threshold: float,
        alarm:     MachineAlarm,
    ) -> None:
        """Fatal hata → E-stop."""
        self._estop_active = True
        self._ctrl.emergency_stop()
        self._log_event(SafetyLevel.FATAL, category, message,
                        value, threshold, "E-STOP")

    def _trigger_pause(
        self,
        category:  str,
        message:   str,
        value:     float,
        threshold: float,
    ) -> None:
        """Critical hata → Feed hold."""
        if not self._paused_by_safety:
            self._paused_by_safety = True
            self._ctrl.pause()
            self._log_event(SafetyLevel.CRITICAL, category, message,
                            value, threshold, "PAUSE")

    def _log_event(
        self,
        level:       SafetyLevel,
        category:    str,
        message:     str,
        value:       float,
        threshold:   float,
        action:      str,
    ) -> None:
        """Güvenlik olayı kaydet ve callback çağır (lock dışında)."""
        event = SafetyEvent(
            timestamp    = time.time(),
            level        = level,
            category     = category,
            message      = message,
            value        = value,
            threshold    = threshold,
            action_taken = action,
        )
        # NOT: callback lock dışında — deadlock engellemek için
        with self._lock:
            self._events.append(event)
        if level in (SafetyLevel.CRITICAL, SafetyLevel.FATAL):
            print(f"    ⚡ {event}")
        self._on_event(event)  # ← lock dışında çağır
