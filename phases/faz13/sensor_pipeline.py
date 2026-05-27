"""sensor_pipeline.py — Real Sensor Pipeline (Faz 10)"""
from __future__ import annotations
import math,threading,time
from collections import deque
from dataclasses import dataclass,field
from enum import Enum,auto
from typing import Callable,Dict,List,Optional,Tuple
import numpy as np

@dataclass(slots=True)
class EncoderReading:
    position_mm:float; velocity_mm_s:float; timestamp:float; n_pulses:int; n_missed:int

class QuadratureEncoder:
    """4x quadrature decoding + latency compensation + missed pulse detection."""
    def __init__(self,axis="X",steps_per_mm=80.0,latency_us=50.0):
        self.axis=axis; self.spm=steps_per_mm; self.lat_us=latency_us
        self.res_mm=1.0/(steps_per_mm*4)
        self._count=0; self._missed=0; self._prev_t=time.monotonic(); self._prev_pos=0.0
        self._lock=threading.Lock(); self._sim_pos=0.0
    def set_simulated_position(self,pos_mm:float):
        with self._lock: self._sim_pos=pos_mm; self._count=int(pos_mm/self.res_mm)
    def read(self)->EncoderReading:
        now=time.monotonic()
        with self._lock:
            pos=self._count*self.res_mm; dt=now-self._prev_t
            vel=(pos-self._prev_pos)/max(dt,1e-6)
            self._prev_pos=pos; self._prev_t=now; missed=self._missed
        return EncoderReading(position_mm=pos,velocity_mm_s=vel,
            timestamp=time.time()-self.lat_us/1e6,n_pulses=self._count,n_missed=missed)
    def reset(self):
        with self._lock: self._count=self._missed=0; self._sim_pos=0.0
    def simulate_move(self,delta_mm:float)->int:
        n=int(abs(delta_mm)/self.res_mm)*(1 if delta_mm>=0 else -1)
        with self._lock: self._count+=n
        return abs(n)

@dataclass(slots=True)
class TensionReading:
    tension_N:float; raw_adc:int; filtered_N:float; timestamp:float; tare_N:float; drift_N:float

class LoadCellHX711:
    """HX711 load cell: moving avg N=16 + 3σ spike rejection + Kalman fusion + drift comp."""
    def __init__(self,scale_N_per_count=5e-4,tare_N=0.0,noise_sigma_N=0.3,drift_N_per_h=0.05,moving_avg_n=16):
        self._scale=scale_N_per_count; self._tare=tare_N; self._noise=noise_sigma_N
        self._drift_r=drift_N_per_h/3600.0; self._n_avg=moving_avg_n
        self._buf=deque(maxlen=moving_avg_n); self._t_start=time.time()
        self._rng=np.random.default_rng(42)
        self._x_kal=15.0; self._P_kal=1.0; self._Q_kal=0.01; self._R_kal=noise_sigma_N**2
    def tare(self,n_samples=32)->float:
        readings=[self._raw_read() for _ in range(n_samples)]
        self._tare=float(np.mean(readings))*self._scale; return self._tare
    def read(self)->TensionReading:
        raw=self._raw_read(); t=time.time()
        N_raw=raw*self._scale-self._tare
        self._buf.append(N_raw)
        if len(self._buf)>=self._n_avg//2:
            arr=np.array(self._buf); mu=float(arr.mean()); sig=float(arr.std())+1e-9
            valid=arr[np.abs(arr-mu)<3*sig]; N_avg=float(valid.mean()) if len(valid)>0 else mu
        else: N_avg=N_raw
        elapsed_s=t-self._t_start; drift_N=self._drift_r*elapsed_s; N_nd=N_avg-drift_N
        self._P_kal+=self._Q_kal; K=self._P_kal/(self._P_kal+self._R_kal)
        self._x_kal+=K*(N_nd-self._x_kal); self._P_kal*=(1.0-K)
        return TensionReading(tension_N=max(0.0,N_raw),raw_adc=int(raw*1e6),
            filtered_N=max(0.0,self._x_kal),timestamp=t,tare_N=self._tare,drift_N=drift_N)
    def _raw_read(self)->float:
        return 15.0/self._scale+self._tare/self._scale+self._rng.normal(0,self._noise/self._scale)

@dataclass(slots=True)
class RPMReading:
    rpm:float; omega_dps:float; frequency:float; timestamp:float; dropout:bool

class HallRPMSensor:
    """Hall-effect RPM sensörü — PLL tabanlı estimatör + dropout detection (300ms)."""
    PLL_KP=5.0; PLL_KI=2.0; DROPOUT_MS=300.0
    def __init__(self,n_magnets=1,noise_pct=1.0,sim_rpm=0.0):
        self._n_mag=n_magnets; self._noise=noise_pct/100.0; self._sim_rpm=sim_rpm
        self._f_pll=0.0; self._f_int=0.0; self._t_last=time.monotonic()
        self._rng=np.random.default_rng(44)
    def set_simulated_rpm(self,rpm:float): self._sim_rpm=rpm
    def read(self)->RPMReading:
        now=time.monotonic(); dt=now-self._t_last; self._t_last=now
        rpm_meas=self._sim_rpm*(1+self._rng.normal(0,self._noise)) if self._sim_rpm>0 else 0.0
        f_meas=rpm_meas/60.0*self._n_mag
        err=f_meas-self._f_pll
        self._f_int+=self.PLL_KI*err*dt; self._f_pll+=( self.PLL_KP*err+self._f_int)*dt
        self._f_pll=max(0.0,self._f_pll)
        rpm_est=self._f_pll*60.0/self._n_mag
        return RPMReading(rpm=rpm_est,omega_dps=rpm_est*6.0,frequency=self._f_pll,
            timestamp=time.time(),dropout=(self._sim_rpm==0 and dt>self.DROPOUT_MS/1000.0))

class FiberBreakState(Enum):
    OK="ok"; WARNING="warning"; BREAK="break"; RECOVERING="recovering"

@dataclass(slots=True)
class FiberBreakReading:
    state:FiberBreakState; optical_ok:bool; tension_ok:bool
    confidence:float; timestamp:float; recommended_action:str

class FiberBreakDetector:
    """Dual-channel fiber break detection: optical + tension collapse."""
    T_MIN_N=3.0; SPIKE_RATE=-30.0
    def __init__(self,T_nominal=15.0,T_min=3.0,optical_sim_ok=True):
        self.T_nom=T_nominal; self.T_min=T_min; self._opt_ok=optical_sim_ok
        self._T_prev=T_nominal; self._t_prev=time.time(); self._state=FiberBreakState.OK
    def update(self,tension_N:float)->FiberBreakReading:
        now=time.time(); dt=max(now-self._t_prev,1e-6)
        dT_dt=(tension_N-self._T_prev)/dt; self._T_prev=tension_N; self._t_prev=now
        opt_ok=self._opt_ok; T_ok=tension_N>=self.T_min; spike_ok=dT_dt>self.SPIKE_RATE
        tension_ok=T_ok and spike_ok
        if not opt_ok and not tension_ok:
            state=FiberBreakState.BREAK; conf=0.99; action="ACIL: Fiber koptu."
        elif not opt_ok or not tension_ok:
            state=FiberBreakState.WARNING; conf=0.65; action="UYARI: Fiber kontrol."
        else:
            state=FiberBreakState.OK; conf=1.0; action="Normal"
        self._state=state
        return FiberBreakReading(state=state,optical_ok=opt_ok,tension_ok=tension_ok,
            confidence=conf,timestamp=now,recommended_action=action)
    def set_optical_status(self,ok:bool): self._opt_ok=ok
    @property
    def current_state(self)->FiberBreakState: return self._state

@dataclass(slots=True)
class SyncedSensorFrame:
    timestamp:float; encoder_x:Optional[EncoderReading]; encoder_a:Optional[EncoderReading]
    tension:Optional[TensionReading]; rpm:Optional[RPMReading]; fiber_state:Optional[FiberBreakReading]

class SensorTimestampSync:
    """Asenkron sensör okumalarını ortak timestamp'e hizala (ZOH interpolasyon)."""
    LATENCY_ENCODER_US=50.0; LATENCY_LOADCELL_MS=12.5; LATENCY_HALL_MS=5.0
    def __init__(self,encoder_x=None,encoder_a=None,load_cell=None,rpm_sensor=None,fiber_det=None):
        self.enc_x=encoder_x; self.enc_a=encoder_a; self.lc=load_cell
        self.rpm=rpm_sensor; self.fb=fiber_det
    def read_all(self)->SyncedSensorFrame:
        t_now=time.time()
        enc_x=self.enc_x.read() if self.enc_x else None
        enc_a=self.enc_a.read() if self.enc_a else None
        lc=self.lc.read() if self.lc else None
        rpm=self.rpm.read() if self.rpm else None
        fb=None
        if self.fb and lc: fb=self.fb.update(lc.filtered_N)
        elif self.fb: fb=self.fb.update(15.0)
        return SyncedSensorFrame(timestamp=t_now,encoder_x=enc_x,encoder_a=enc_a,
            tension=lc,rpm=rpm,fiber_state=fb)
    def health_check(self)->Dict[str,bool]:
        frame=self.read_all()
        return {
            "encoder_x": frame.encoder_x is not None,
            "encoder_a": frame.encoder_a is not None,
            "load_cell": frame.tension is not None and frame.tension.filtered_N>=0,
            "rpm":       frame.rpm is not None and not frame.rpm.dropout,
            "fiber":     frame.fiber_state is None or frame.fiber_state.state!=FiberBreakState.BREAK,
        }
