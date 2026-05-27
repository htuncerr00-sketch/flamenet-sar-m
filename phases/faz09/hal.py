"""
hal.py — Hardware Abstraction Layer (HAL)
==========================================
Controller-bağımsız makine arayüzü. Tüm üst katmanlar yalnızca
ControllerInterface'e bağlıdır — fiziksel controller değiştiğinde
üst katman kodu değişmez.

Tasarım ilkeleri:
  1. Senkron API (mock/test için)  ← bu dosya
  2. Async wrapper (real hardware) ← streaming.py'de
  3. Controller Capability modeli: her controller ne yapabilir?

Desteklenen controllerlar:
  MockController    — donanım olmadan test
  GRBLFluidNC       — ESP32/FluidNC ve GRBL (Serial + WebSocket)
  Mach3Controller   — Mach3/Mach4 (DLL simülasyonu)
  LinuxCNCController — LinuxCNC HAL pins (iskelet, ileride)

Capability modeli:
  supports_inverse_time: bool    — G93 (Mach3)
  max_buffer_segments:   int     — controller iç buffer kapasitesi
  supports_rt_feedback:  bool    — gerçek zamanlı DRO okuma
  max_step_rate_hz:      int     — max step/s (donanım limiti)
  baud_rate:             int     — serial baud (GRBL)
  supports_soft_limits:  bool    — $20=1 (GRBL) veya softlimits.ini

MachineState:
  Her read_state() çağrısında döner:
  {x_actual, a_actual_deg, rpm, vx, buffer_fill, alarm, ok}

Bağlantı modeli:
  connect()  → bağlan, capabilities kontrol et
  home()     → referans al (G28 veya homing cycle)
  stream_segment() → tek segment gönder
  pause()    → feed hold (GRBL: !, Mach3: M0)
  resume()   → cycle start (GRBL: ~, Mach3: M1)
  emergency_stop() → E-stop (GRBL: ctrl-x, Mach3: M112)
  read_state() → anlık makine durumu

Referans:
  GRBL wiki: github.com/gnea/grbl/wiki
  FluidNC: docs.fluidnc.com
  ADA268923 Appendix I: pulse rate ve buffer modeli
"""

from __future__ import annotations

import math
import time
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple


# ── Machine State ────────────────────────────────────────────────

class MachineAlarm(Enum):
    NONE             = "none"
    SOFT_LIMIT_X     = "soft_limit_x"
    SOFT_LIMIT_A     = "soft_limit_a"
    HARD_LIMIT       = "hard_limit"
    SPINDLE_OVERSPEED= "spindle_overspeed"
    TENSION_HIGH     = "tension_high"
    BUFFER_OVERFLOW  = "buffer_overflow"
    WATCHDOG_TIMEOUT = "watchdog_timeout"
    EMERGENCY_STOP   = "emergency_stop"
    COMM_TIMEOUT     = "comm_timeout"


@dataclass(slots=True)
class MachineState:
    """Anlık makine durumu — read_state() çıktısı."""
    timestamp:      float
    x_actual_mm:    float   # Carriage pozisyonu
    a_actual_deg:   float   # Spindle açısı (kümülatif)
    vx_mm_s:        float   # Carriage hızı
    rpm:            float   # Spindle RPM
    tension_N:      float   # Fiber gerilmesi (load cell)
    buffer_fill:    float   # [0,1] — controller buffer doluluk oranı
    is_running:     bool    # Hareket devam ediyor mu?
    is_idle:        bool    # Makine boşta mı?
    alarm:          MachineAlarm
    ok:             bool    # Genel durum

    @classmethod
    def idle(cls) -> "MachineState":
        return cls(
            timestamp=time.time(), x_actual_mm=0.0, a_actual_deg=0.0,
            vx_mm_s=0.0, rpm=0.0, tension_N=0.0, buffer_fill=0.0,
            is_running=False, is_idle=True, alarm=MachineAlarm.NONE, ok=True)

    def summary(self) -> str:
        return (
            f"t={self.timestamp:.3f}  X={self.x_actual_mm:.3f}mm  "
            f"A={self.a_actual_deg:.2f}°  "
            f"v={self.vx_mm_s:.1f}mm/s  rpm={self.rpm:.1f}  "
            f"T={self.tension_N:.2f}N  buf={self.buffer_fill:.2f}  "
            f"{'RUN' if self.is_running else 'IDLE'}  "
            f"alarm={self.alarm.value}"
        )


# ── Controller Capabilities ──────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ControllerCapabilities:
    """Controller'ın desteklediği özellikler."""
    name:                  str
    supports_inverse_time: bool    # G93 inverse time mode
    max_buffer_segments:   int     # İç G-code buffer kapasitesi
    supports_rt_feedback:  bool    # DRO / real-time okuma
    max_step_rate_hz:      int     # Max step/s
    baud_rate:             int     # Serial baud rate (0 = N/A)
    supports_soft_limits:  bool    # Yazılım limit kontrolü
    supports_homing:       bool    # Otomatik homing cycle
    supports_websocket:    bool    # WebSocket bağlantı
    min_segment_time_ms:   float   # Min segment süresi (timing)
    position_resolution_mm:float  # Min pozisyon çözünürlüğü

    def report(self) -> str:
        return (
            f"  Controller: {self.name}\n"
            f"  Buffer: {self.max_buffer_segments} seg  "
            f"MaxStep: {self.max_step_rate_hz}Hz  "
            f"Baud: {self.baud_rate}\n"
            f"  InvTime: {'✓' if self.supports_inverse_time else '✗'}  "
            f"RTFeedback: {'✓' if self.supports_rt_feedback else '✗'}  "
            f"SoftLimits: {'✓' if self.supports_soft_limits else '✗'}  "
            f"Homing: {'✓' if self.supports_homing else '✗'}  "
            f"WS: {'✓' if self.supports_websocket else '✗'}\n"
            f"  MinSegTime: {self.min_segment_time_ms:.1f}ms  "
            f"PosRes: {self.position_resolution_mm*1000:.3f}µm"
        )


GRBL_CAPABILITIES = ControllerCapabilities(
    name                  = "GRBL/FluidNC",
    supports_inverse_time = False,   # GRBL: G94 only
    max_buffer_segments   = 15,      # GRBL RX buffer: 127 bytes ≈ 15 short cmds
    supports_rt_feedback  = True,    # Status query '?'
    max_step_rate_hz      = 40000,   # ESP32 FluidNC: 40kHz
    baud_rate             = 115200,
    supports_soft_limits  = True,    # $20=1
    supports_homing       = True,    # $H
    supports_websocket    = True,    # FluidNC WebSocket
    min_segment_time_ms   = 0.5,
    position_resolution_mm= 1/80.0,  # 80 step/mm
)

MACH3_CAPABILITIES = ControllerCapabilities(
    name                  = "Mach3",
    supports_inverse_time = True,    # G93
    max_buffer_segments   = 50,      # Mach3 lookahead buffer
    supports_rt_feedback  = True,    # DRO via OEM API
    max_step_rate_hz      = 100000,  # Mach3 kernel: 100kHz (PC parallel port)
    baud_rate             = 0,       # No serial, kernel mode
    supports_soft_limits  = True,    # Soft limits in config
    supports_homing       = True,    # Ref all home
    supports_websocket    = False,
    min_segment_time_ms   = 1.0,
    position_resolution_mm= 1/80.0,
)

MOCK_CAPABILITIES = ControllerCapabilities(
    name                  = "MockController",
    supports_inverse_time = True,
    max_buffer_segments   = 256,
    supports_rt_feedback  = True,
    max_step_rate_hz      = 1000000,
    baud_rate             = 0,
    supports_soft_limits  = True,
    supports_homing       = True,
    supports_websocket    = True,
    min_segment_time_ms   = 0.0,
    position_resolution_mm= 0.0001,
)


# ── Abstract Interface ───────────────────────────────────────────

class ControllerInterface(ABC):
    """
    Hardware Abstraction Layer — abstract base.

    Tüm üst katman kodu bu interface üzerinden çalışır.
    """

    @property
    @abstractmethod
    def capabilities(self) -> ControllerCapabilities:
        """Bu controller'ın capability seti."""

    @abstractmethod
    def connect(self, **kwargs) -> bool:
        """Bağlan. Returns True if successful."""

    @abstractmethod
    def disconnect(self) -> None:
        """Bağlantıyı kapat."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Bağlı mı?"""

    @abstractmethod
    def home(self, axes: str = "XA") -> bool:
        """Referans hareketi. axes: "X", "A", "XA"."""

    @abstractmethod
    def stream_segment(self, gcode_line: str) -> bool:
        """
        Tek G-code satırı gönder.
        Returns False if buffer full or error.
        """

    @abstractmethod
    def stream_segments(self, lines: List[str]) -> int:
        """
        Birden fazla satır gönder.
        Returns: kaç satır başarıyla gönderildi.
        """

    @abstractmethod
    def pause(self) -> bool:
        """Feed hold. Returns True if accepted."""

    @abstractmethod
    def resume(self) -> bool:
        """Cycle start (feed hold'dan devam)."""

    @abstractmethod
    def emergency_stop(self) -> bool:
        """
        Emergency stop — fiber kesilmiş olsun veya olmasın.
        GRBL: Ctrl-X (soft reset)
        Mach3: M112
        """

    @abstractmethod
    def read_state(self) -> MachineState:
        """Anlık makine durumunu oku."""

    @abstractmethod
    def send_raw(self, cmd: str) -> Optional[str]:
        """Ham komut gönder (debug/kalibrasyon için)."""

    def wait_idle(self, timeout_s: float = 30.0, poll_hz: float = 10.0) -> bool:
        """Makine idle olana kadar bekle."""
        deadline = time.monotonic() + timeout_s
        interval = 1.0 / poll_hz
        while time.monotonic() < deadline:
            state = self.read_state()
            if state.is_idle and not state.is_running:
                return True
            if state.alarm != MachineAlarm.NONE:
                return False
            time.sleep(interval)
        return False

    def check_alive(self) -> bool:
        """Bağlantı ve makine sağlığı kontrolü."""
        if not self.is_connected:
            return False
        state = self.read_state()
        return state.ok and state.alarm == MachineAlarm.NONE


# ── Mock Controller ──────────────────────────────────────────────

class MockController(ControllerInterface):
    """
    Donanım olmadan tam test sağlayan simülasyon controller.

    - G-code satırlarını parse eder ve dahili durumu günceller
    - Gerçekçi timing simülasyonu (segment süresi hesaplama)
    - Alarm tetikleyici API (test için)
    - Buffer doluluk simülasyonu
    """

    def __init__(
        self,
        x_travel_mm:    float = 400.0,
        a_max_rpm:      float = 250.0,
        simulate_lag:   float = 0.0,    # Yapay gecikme [s]
        random_seed:    int   = 42,
    ) -> None:
        self._connected      = False
        self._x              = 0.0
        self._a              = 0.0
        self._vx             = 0.0
        self._rpm            = 0.0
        self._tension        = 15.0
        self._buffer:        List[str] = []
        self._alarm          = MachineAlarm.NONE
        self._running        = False
        self._x_travel       = x_travel_mm
        self._a_max_rpm      = a_max_rpm
        self._lag            = simulate_lag
        self._cmd_count      = 0
        self._lock           = threading.Lock()
        self._last_cmd_time  = time.monotonic()

    @property
    def capabilities(self) -> ControllerCapabilities:
        return MOCK_CAPABILITIES

    def connect(self, **kwargs) -> bool:
        self._connected = True
        print("    [MOCK] Controller connected (simülasyon modu)")
        return True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def home(self, axes: str = "XA") -> bool:
        if not self._connected: return False
        self._x = 0.0
        self._a = 0.0
        print(f"    [MOCK] Home: {axes} axes → X=0 A=0")
        return True

    def stream_segment(self, gcode_line: str) -> bool:
        if not self._connected: return False
        if len(self._buffer) >= self.capabilities.max_buffer_segments:
            return False  # Buffer full

        with self._lock:
            self._buffer.append(gcode_line)
            self._process_buffer()
        if self._lag > 0:
            time.sleep(self._lag)
        return True

    def stream_segments(self, lines: List[str]) -> int:
        count = 0
        for line in lines:
            if self.stream_segment(line):
                count += 1
            else:
                break
        return count

    def _process_buffer(self) -> None:
        """Buffer'daki ilk komutu işle (parse + state update)."""
        if not self._buffer:
            return
        cmd = self._buffer.pop(0).strip().upper()
        self._parse_gcode(cmd)
        self._cmd_count += 1
        self._running = len(self._buffer) > 0

    def _parse_gcode(self, cmd: str) -> None:
        """Basit G-code parser — X, A, F değerlerini çıkar."""
        import re
        self._running = True
        try:
            x_m = re.search(r'X([-\d.]+)', cmd)
            a_m = re.search(r'A([-\d.]+)', cmd)
            f_m = re.search(r'F([\d.]+)', cmd)
            if x_m: self._x  = float(x_m.group(1))
            if a_m: self._a  = float(a_m.group(1))
            if f_m:
                f_val = float(f_m.group(1))
                self._vx  = f_val / 60.0  # mm/min → mm/s
                if self._x > 0:
                    self._rpm = self._vx * math.sin(math.radians(10.17)) / (2*math.pi*50) * 60
            # M0/M1: pause
            if 'M0' in cmd or 'M1' in cmd:
                self._running = False
        except Exception:
            pass
        self._running = False  # processed

    def pause(self) -> bool:
        self._running = False
        return True

    def resume(self) -> bool:
        self._running = bool(self._buffer)
        return True

    def emergency_stop(self) -> bool:
        with self._lock:
            self._buffer.clear()
            self._vx   = 0.0
            self._rpm  = 0.0
            self._running = False
            self._alarm = MachineAlarm.EMERGENCY_STOP
        print("    [MOCK] ⚡ EMERGENCY STOP")
        return True

    def read_state(self) -> MachineState:
        with self._lock:
            buf_fill = len(self._buffer) / self.capabilities.max_buffer_segments
        return MachineState(
            timestamp    = time.time(),
            x_actual_mm  = self._x,
            a_actual_deg = self._a,
            vx_mm_s      = self._vx,
            rpm          = self._rpm,
            tension_N    = self._tension,
            buffer_fill  = buf_fill,
            is_running   = self._running,
            is_idle      = not self._running,
            alarm        = self._alarm,
            ok           = self._alarm == MachineAlarm.NONE,
        )

    def send_raw(self, cmd: str) -> Optional[str]:
        self._parse_gcode(cmd.upper())
        return "ok"

    def inject_alarm(self, alarm: MachineAlarm) -> None:
        """Test amaçlı alarm tetikle."""
        self._alarm = alarm

    def clear_alarm(self) -> None:
        self._alarm = MachineAlarm.NONE

    def set_tension(self, N: float) -> None:
        """Simüle gerilme değeri set et."""
        self._tension = N


# ── GRBL / FluidNC Controller (stub — gerçek serial için genişletilir) ──

class GRBLFluidNCController(ControllerInterface):
    """
    GRBL / FluidNC serial controller.

    Bağlantı: Serial (pyserial) veya WebSocket (ESP32 FluidNC)
    Protokol: ASCII G-code, response: "ok" veya "error:N"
    Status: "?" → "<Idle|MPos:x,y,z,a|FS:f,s|WCO:x,y,z>"

    NOT: Gerçek pyserial/websockets bağımlılığı opsiyonel.
    Yoksa MockController kullanılır.
    """

    def __init__(
        self,
        port:          str   = "COM3",
        baud:          int   = 115200,
        use_websocket: bool  = False,
        ws_url:        str   = "ws://192.168.1.100:81",
        timeout:       float = 5.0,
    ) -> None:
        self._port   = port
        self._baud   = baud
        self._use_ws = use_websocket
        self._ws_url = ws_url
        self._timeout= timeout
        self._serial = None
        self._ws     = None
        self._x = self._a = self._vx = self._rpm = 0.0
        self._alarm  = MachineAlarm.NONE
        self._buffer_fill = 0.0
        self._lock   = threading.Lock()

    @property
    def capabilities(self) -> ControllerCapabilities:
        return GRBL_CAPABILITIES

    def connect(self, **kwargs) -> bool:
        try:
            import serial  # pyserial
            self._serial = serial.Serial(self._port, self._baud, timeout=self._timeout)
            time.sleep(2.0)  # GRBL reset time
            self._serial.write(b"\r\n\r\n")
            time.sleep(0.1)
            self._serial.flushInput()
            print(f"    [GRBL] Connected: {self._port} @ {self._baud}")
            return True
        except ImportError:
            print("    [GRBL] pyserial yok — MockController kullanın")
            return False
        except Exception as e:
            print(f"    [GRBL] Bağlantı hatası: {e}")
            return False

    def disconnect(self) -> None:
        if self._serial:
            self._serial.close()
            self._serial = None

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def home(self, axes: str = "XA") -> bool:
        resp = self.send_raw("$H")
        return resp is not None and "ok" in resp.lower()

    def stream_segment(self, gcode_line: str) -> bool:
        if not self.is_connected: return False
        try:
            cmd = (gcode_line.strip() + "\n").encode()
            self._serial.write(cmd)
            resp = self._serial.readline().decode().strip()
            if "error" in resp.lower():
                return False
            return True
        except Exception:
            return False

    def stream_segments(self, lines: List[str]) -> int:
        count = 0
        for line in lines:
            if self.stream_segment(line): count += 1
            else: break
        return count

    def pause(self) -> bool:
        if not self.is_connected: return False
        self._serial.write(b"!")  # GRBL feed hold
        return True

    def resume(self) -> bool:
        if not self.is_connected: return False
        self._serial.write(b"~")  # GRBL cycle start
        return True

    def emergency_stop(self) -> bool:
        if not self.is_connected: return False
        self._serial.write(b"\x18")  # GRBL soft reset
        self._alarm = MachineAlarm.EMERGENCY_STOP
        return True

    def read_state(self) -> MachineState:
        if not self.is_connected:
            return MachineState.idle()
        try:
            self._serial.write(b"?")
            resp = self._serial.readline().decode().strip()
            return self._parse_status(resp)
        except Exception:
            return MachineState.idle()

    def _parse_status(self, resp: str) -> MachineState:
        """
        GRBL status string parser.
        Format: <Idle|MPos:0.000,0.000,0.000|FS:0,0>
        """
        import re
        state_name = "Idle"
        x_val = self._x; a_val = self._a; f_val = 0.0; s_val = 0.0
        try:
            m_state = re.match(r'<(\w+)[|>]', resp)
            if m_state: state_name = m_state.group(1)
            m_pos = re.search(r'MPos:([-\d.]+),([-\d.]+),([-\d.]+)(?:,([-\d.]+))?', resp)
            if m_pos:
                x_val = float(m_pos.group(1))
                a_str = m_pos.group(4)
                if a_str: a_val = float(a_str)
            m_fs = re.search(r'FS:([\d.]+),([\d.]+)', resp)
            if m_fs: f_val = float(m_fs.group(1)); s_val = float(m_fs.group(2))
        except Exception:
            pass
        self._x = x_val; self._a = a_val
        return MachineState(
            timestamp    = time.time(),
            x_actual_mm  = x_val,
            a_actual_deg = a_val,
            vx_mm_s      = f_val / 60.0,
            rpm          = s_val,
            tension_N    = 0.0,   # load cell ayrı
            buffer_fill  = 0.5,   # GRBL buffer doluluk bilinmiyor
            is_running   = state_name in ("Run", "Hold"),
            is_idle      = state_name == "Idle",
            alarm        = self._alarm,
            ok           = self._alarm == MachineAlarm.NONE,
        )

    def send_raw(self, cmd: str) -> Optional[str]:
        if not self.is_connected: return None
        try:
            self._serial.write((cmd.strip() + "\n").encode())
            return self._serial.readline().decode().strip()
        except Exception:
            return None
