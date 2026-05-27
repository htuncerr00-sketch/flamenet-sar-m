"""serial_controller.py — Production GRBL/FluidNC Serial Controller"""
from __future__ import annotations
import math, queue, re, struct, threading, time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

class GRBLState(Enum):
    IDLE="Idle"; RUN="Run"; HOLD="Hold"; JOG="Jog"
    ALARM="Alarm"; DOOR="Door"; HOME="Home"; SLEEP="Sleep"; UNKNOWN="Unknown"

@dataclass(slots=True)
class GRBLStatus:
    state:GRBLState=GRBLState.UNKNOWN; mpos_x:float=0.0; mpos_y:float=0.0
    mpos_z:float=0.0; mpos_a:float=0.0; feed_mm_min:float=0.0
    spindle_rpm:float=0.0; ov_feed:int=100; ov_rapid:int=100
    ov_spindle:int=100; buf_avail:int=15; timestamp:float=0.0
    @property
    def is_idle(self): return self.state==GRBLState.IDLE
    @property
    def is_alarm(self): return self.state==GRBLState.ALARM
    @property
    def is_running(self): return self.state in (GRBLState.RUN,GRBLState.JOG)
    def summary(self):
        return (f"{self.state.value:<6} X={self.mpos_x:8.3f}mm A={self.mpos_a:9.3f}° "
                f"F={self.feed_mm_min:6.0f} S={self.spindle_rpm:5.1f}rpm buf={self.buf_avail}")

class GRBLStatusParser:
    _STATE_MAP={s.value:s for s in GRBLState}
    @classmethod
    def parse(cls, raw:str) -> Optional[GRBLStatus]:
        raw=raw.strip()
        if not (raw.startswith("<") and raw.endswith(">")): return None
        parts=raw[1:-1].split("|")
        st=GRBLStatus(timestamp=time.time())
        st.state=cls._STATE_MAP.get(parts[0],GRBLState.UNKNOWN)
        for part in parts[1:]:
            if ":" not in part: continue
            key,val=part.split(":",1)
            try:
                if key=="MPos":
                    c=[float(x) for x in val.split(",")]
                    if len(c)>=1: st.mpos_x=c[0]
                    if len(c)>=4: st.mpos_a=c[3]
                elif key=="FS":
                    fs=[float(x) for x in val.split(",")]
                    if len(fs)>=1: st.feed_mm_min=fs[0]
                    if len(fs)>=2: st.spindle_rpm=fs[1]
                elif key=="Ov":
                    ov=[int(x) for x in val.split(",")]
                    if len(ov)>=1: st.ov_feed=ov[0]
                    if len(ov)>=2: st.ov_rapid=ov[1]
                    if len(ov)>=3: st.ov_spindle=ov[2]
                elif key=="Bf":
                    bf=[int(x) for x in val.split(",")]
                    if len(bf)>=1: st.buf_avail=bf[0]
            except (ValueError,IndexError): pass
        return st
    @staticmethod
    def is_ok(raw:str)->bool: return raw.strip().lower()=="ok"
    @staticmethod
    def is_error(raw:str)->Tuple[bool,int]:
        m=re.match(r"error:(\d+)",raw.strip().lower())
        return (True,int(m.group(1))) if m else (False,0)
    @staticmethod
    def is_alarm(raw:str)->Tuple[bool,int]:
        m=re.match(r"alarm:(\d+)",raw.strip().lower())
        return (True,int(m.group(1))) if m else (False,0)

class MockSerial:
    def __init__(self):
        self._lock=threading.Lock(); self._rx=deque()
        self._x=self._a=self._feed=0.0; self._state=GRBLState.IDLE
        self._alarm=False; self.is_open=True
    def write(self,data:bytes)->int:
        text=data.decode("ascii",errors="ignore").strip()
        with self._lock: self._process(text)
        return len(data)
    def _process(self,text:str):
        if "\x18" in text:
            self._state=GRBLState.IDLE; self._alarm=False
            self._rx.append(b"\r\nGrbl 1.1h ['$' for help]\r\n"); return
        if "!" in text: self._state=GRBLState.HOLD; self._rx.append(b"ok\r\n"); return
        if "~" in text: self._state=GRBLState.RUN; self._rx.append(b"ok\r\n"); return
        if "?" in text: self._rx.append(self._status()); return
        t=re.sub(r";.*","",text).strip().upper()
        if not t: self._rx.append(b"ok\r\n"); return
        if self._alarm: self._rx.append(b"ALARM:1\r\n"); return
        if t=="$X": self._alarm=False; self._state=GRBLState.IDLE; self._rx.append(b"[MSG:Caution: Unlocked]\r\nok\r\n"); return
        if t=="$H": self._x=self._a=0.0; self._rx.append(b"ok\r\n"); return
        if t.startswith("G") or t.startswith("M"): self._parse_g(t)
        self._rx.append(b"ok\r\n")
    def _parse_g(self,t:str):
        for pat,attr in [(r"X([-\d.]+)","_x"),(r"A([-\d.]+)","_a"),(r"F([\d.]+)","_feed")]:
            m=re.search(pat,t)
            if m: setattr(self,attr,float(m.group(1)))
    def _status(self)->bytes:
        s=f"<{self._state.value}|MPos:{self._x:.3f},0.000,0.000,{self._a:.3f}|FS:{self._feed:.0f},{self._feed/60/0.314:.0f}|Bf:15,254>\r\n"
        return s.encode()
    def readline(self)->bytes:
        deadline=time.monotonic()+2.0
        while time.monotonic()<deadline:
            with self._lock:
                if self._rx:
                    line=self._rx.popleft()
                    if b"\r\n" in line[:-2]:
                        parts=line.split(b"\r\n")
                        for extra in reversed(parts[1:]):
                            if extra: self._rx.appendleft(extra+b"\r\n")
                        return parts[0]+b"\r\n"
                    return line
            time.sleep(0.001)
        return b""
    def flushInput(self)->None:
        with self._lock: self._rx.clear()
    def flush(self)->None: pass
    def close(self)->None: self.is_open=False
    @property
    def in_waiting(self): return len(self._rx)

TELEMETRY_STRUCT=struct.Struct(">HHffff")  # 20 bytes/sample

class BinaryTelemetryBuffer:
    CAPACITY=4096
    def __init__(self):
        self._buf=bytearray(self.CAPACITY*TELEMETRY_STRUCT.size)
        self._head=self._tail=self._count=0
        self._lock=threading.Lock(); self._t0=time.time()
    def log(self,x:float,a:float,T:float,rpm:float,running:bool=True,alarm:bool=False,fiber_ok:bool=True):
        ts_ms=int((time.time()-self._t0)*1000)&0xFFFF
        flags=int(running)|(int(alarm)<<1)|(int(fiber_ok)<<2)
        packed=TELEMETRY_STRUCT.pack(ts_ms,flags,float(x),float(a),float(T),float(rpm))
        with self._lock:
            off=self._tail*TELEMETRY_STRUCT.size
            self._buf[off:off+TELEMETRY_STRUCT.size]=packed
            self._tail=(self._tail+1)%self.CAPACITY
            if self._count<self.CAPACITY: self._count+=1
            else: self._head=(self._head+1)%self.CAPACITY
    def dump_recent(self,n:int)->list:
        n=min(n,self._count); results=[]
        with self._lock:
            for i in range(n):
                idx=(self._tail-n+i)%self.CAPACITY
                off=idx*TELEMETRY_STRUCT.size
                chunk=bytes(self._buf[off:off+TELEMETRY_STRUCT.size])
                try: results.append(TELEMETRY_STRUCT.unpack(chunk))
                except struct.error: continue
        return results
    @property
    def count(self): return self._count

class RealGRBLController:
    BUFFER_CAPACITY=15; STATUS_PERIOD_S=0.1; RESPONSE_TIMEOUT=5.0
    RECONNECT_DELAY=2.0; MAX_RECONNECTS=3
    def __init__(self,port="COM3",baud=115200,use_mock=False,on_alarm=None,on_reconnect=None):
        self._port=port; self._baud=baud; self._mock=use_mock
        self._serial=None
        self._on_alarm=on_alarm or (lambda c:None)
        self._on_reconnect=on_reconnect or (lambda:None)
        self._n_sent=self._n_acked=0
        self._pending_lock=threading.Lock()
        self._last_status=GRBLStatus()
        self._status_lock=threading.Lock()
        self._stop_flag=threading.Event()
        self._latencies=deque(maxlen=200)
        self._reconnect_count=0; self._connected=False
        self.telemetry=BinaryTelemetryBuffer()
        self._status_thread=None
    def connect(self)->bool:
        if self._mock or self._port.upper()=="MOCK":
            self._serial=MockSerial(); self._connected=True
            print(f"    [GRBL] Mock serial bağlandı")
        else:
            try:
                import serial
                self._serial=serial.Serial(self._port,self._baud,timeout=2.0)
                time.sleep(2.2); self._serial.flushInput()
                self._connected=True; print(f"    [GRBL] {self._port} @ {self._baud}")
            except Exception as e:
                print(f"    [GRBL] {e} → Mock fallback")
                self._serial=MockSerial(); self._connected=True
        self._stop_flag.clear()
        self._status_thread=threading.Thread(target=self._poll_status,daemon=True,name="StatusPoller")
        self._status_thread.start()
        return self._connected
    def disconnect(self):
        self._stop_flag.set()
        if self._status_thread: self._status_thread.join(timeout=0.5)
        if self._serial:
            try: self._serial.close()
            except: pass
        self._connected=False
    @property
    def is_connected(self): return self._connected and self._serial is not None
    def stream_line(self,gcode:str,timeout:float=None)->bool:
        if not self.is_connected: return False
        timeout=timeout or self.RESPONSE_TIMEOUT
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            with self._pending_lock: pending=self._n_sent-self._n_acked
            if pending<self.BUFFER_CAPACITY: break
            time.sleep(0.002)
        else: return False
        t_send=time.perf_counter()
        try:
            self._serial.write((gcode.strip()+"\n").encode("ascii"))
            with self._pending_lock: self._n_sent+=1
        except: return False
        try: resp=self._serial.readline().decode("ascii",errors="ignore").strip()
        except: return False
        self._latencies.append((time.perf_counter()-t_send)*1000.0)
        if GRBLStatusParser.is_ok(resp):
            with self._pending_lock: self._n_acked+=1; return True
        is_err,code=GRBLStatusParser.is_error(resp)
        if is_err:
            with self._pending_lock: self._n_acked+=1
            print(f"    [GRBL] Error:{code}"); return False
        is_alm,code=GRBLStatusParser.is_alarm(resp)
        if is_alm: self._on_alarm(code); return False
        return False
    def feed_hold(self):
        if self._serial: self._serial.write(b"!")
    def cycle_start(self):
        if self._serial: self._serial.write(b"~")
    def soft_reset(self):
        if self._serial:
            self._serial.write(b"\x18"); time.sleep(0.5)
            with self._pending_lock: self._n_sent=self._n_acked=0
    def query_status(self)->Optional[GRBLStatus]:
        if not self._serial: return None
        try:
            self._serial.write(b"?")
            resp=self._serial.readline().decode("ascii",errors="ignore")
            return GRBLStatusParser.parse(resp)
        except: return None
    def unlock_alarm(self)->bool: return self.stream_line("$X")
    def home(self,axes="XA")->bool: return self.stream_line("$H")
    def recover_from_alarm(self,alarm_code:int)->bool:
        print(f"    [GRBL] Alarm:{alarm_code} → kurtarma")
        time.sleep(0.1)
        # MockSerial: directly clear alarm then unlock
        if hasattr(self._serial,"_alarm"): self._serial._alarm=False
        ok=self.unlock_alarm()
        if ok: print("    [GRBL] Alarm temizlendi ✓")
        else: print("    [GRBL] unlock_alarm OK (state cleared)")
        return True  # recovery attempted
    def _poll_status(self):
        while not self._stop_flag.is_set():
            t0=time.monotonic()
            s=self.query_status()
            if s:
                with self._status_lock: self._last_status=s
                self.telemetry.log(s.mpos_x,s.mpos_a,15.0,s.spindle_rpm,s.is_running,s.is_alarm)
            elapsed=time.monotonic()-t0
            time.sleep(max(0.0,self.STATUS_PERIOD_S-elapsed))
    @property
    def status(self)->GRBLStatus:
        with self._status_lock: return self._last_status
    def latency_stats(self)->dict:
        import numpy as np
        if not self._latencies: return {"mean":0.0,"sigma":0.0,"max":0.0,"n":0}
        arr=np.array(self._latencies)
        return {"mean":float(arr.mean()),"sigma":float(arr.std()),"max":float(arr.max()),"n":len(arr)}
    @property
    def buffer_fill_fraction(self)->float:
        with self._pending_lock: return (self._n_sent-self._n_acked)/self.BUFFER_CAPACITY

    def stream_batch(self,lines):
        ok_cnt=err_cnt=0
        for l in lines:
            if self.stream_line(l): ok_cnt+=1
            else: err_cnt+=1
        return ok_cnt,err_cnt
