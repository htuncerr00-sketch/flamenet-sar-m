"""
app/engine/production_engine.py — Fas 8: Canlı Üretim Telemetri Motoru
=======================================================================
G-kodu yorumlayıcı + zamanlayıcı + dijital ikiz koordinat köprüsü.

Bu modül izole ve tek parçadır: yalnızca PySide6 + backend.TelemetryFrame
bağımlılığı vardır. Mevcut mimariyi (link / stream / worker / safety) hiç
değiştirmez; üretilen sinyalleri main_window dinleyip ilgili panellere yönlendirir.

Çalışma modeli
--------------
1. ``gcode_yukle(text, ...)`` — G-kodunu satır satır ayrıştırır, her hareketi
   ``GcodeMove`` (X, Y, Z, A, F + katman/devre) olarak listeler ve her segmentin
   süresini fiber-uzunluğu/ilerleme modeline göre önceden hesaplar.
2. ``basla()`` — QTimer @ TICK_HZ ile zaman çizelgesinde ilerler. Her tikte
   anlık (X, Y, Z, A, F) interpolasyonu hesaplanır:
     • ``koordinatGuncellendi(x, y, z, a)``  → 3D dijital ikiz
     • ``durumGuncellendi(dict)``            → Canlı Üretim göstergeleri (~10 Hz)
     • ``telemetriUretildi(TelemetryFrame)`` → telemetry.db + kartlar (1 Hz, mock)
3. ``duraklat() / devam() / acilDur() / sifirla()`` — zaman çizelgesini anlık
   kilitler/sıfırlar.

Güvenlik
--------
Her tikte X taşıyıcı stroku denetlenir; ihlal varsa ``sinirIhlali`` yayınlanır
ve motor ACİL_DURDU durumuna geçer. "Real" modda gerçek telemetri kesilirse
(bağlantı kopması) yine ``sinirIhlali`` ile kritik olay üretilir.

Mod
---
• Mock  : motor zaman çizelgesinden sentetik koordinat + TelemetryFrame üretir.
• Real  : zaman çizelgesi ilerlemeyi/3D yörüngeyi sürer ama anlık X/A koordinatları
          ``gercek_telemetri(frame)`` ile gelen donanım paketlerinden okunur.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional, Dict, Tuple

from PySide6.QtCore import QObject, QTimer, Signal, Slot

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from backend.hardware.esp32_link import TelemetryFrame


# ── Durum makinesi ───────────────────────────────────────────────────────────

class ProductionState(IntEnum):
    BOSTA        = 0   # yüklendi/bekliyor
    CALISIYOR    = 1
    DURAKLATILDI = 2
    ACIL_DURDU   = 3
    TAMAMLANDI   = 4


_STATE_TR = {
    ProductionState.BOSTA:        "Hazır",
    ProductionState.CALISIYOR:    "Sarılıyor",
    ProductionState.DURAKLATILDI: "Duraklatıldı",
    ProductionState.ACIL_DURDU:   "ACİL DURDU",
    ProductionState.TAMAMLANDI:   "Tamamlandı",
}


# ── Ayrıştırılmış hareket ────────────────────────────────────────────────────

@dataclass
class GcodeMove:
    """Tek 4-eksen hareket hedefi + zaman çizelgesi penceresi."""
    x: float            # taşıyıcı (mm, mutlak)
    y: float            # radyal kafa (mm)
    z: float            # kafa yönlendirme açısı (derece)
    a: float            # iş mili kümülatif açı (derece)
    feed: float         # ilerleme (mm/dak)
    layer: int          # katman indeksi (1 tabanlı; 0 = tanımsız)
    circuit: int        # devre indeksi (1 tabanlı; 0 = tanımsız)
    t_start: float = 0.0  # kümülatif sim zamanı başlangıcı (s)
    t_end: float   = 0.0  # kümülatif sim zamanı bitişi (s)


# Tek satırdan eksen jetonlarını ayıkla: harf + sayı (örn "X12.345", "A-3.0")
_TOKEN_RE = re.compile(r"([A-Za-z])\s*([-+]?\d*\.?\d+)")
_LAYER_RE = re.compile(r"[Kk]atman\s+(\d+)")
_NCIRC_RE = re.compile(r"devre\s*=\s*(\d+)")


class ProductionEngine(QObject):
    """
    QTimer tabanlı canlı üretim motoru. UI thread içinde çalışır; her iş
    salt-hesap (bloklamasız) olduğundan arayüzü dondurmaz.
    """

    # ── Sinyaller ────────────────────────────────────────────────────────────
    koordinatGuncellendi = Signal(float, float, float, float)  # x, y, z, a
    durumGuncellendi     = Signal(dict)                        # ilerleme paketi
    telemetriUretildi    = Signal(object)                     # TelemetryFrame (1 Hz)
    sinirIhlali          = Signal(str, str, float, float)     # code, msg, value, thr
    durumAdiDegisti      = Signal(str)                        # durum adı (TR)

    TICK_HZ = 50                       # interpolasyon/koordinat tik hızı
    STATUS_HZ = 10                     # durum paketi yayını
    TELEM_HZ = 1                       # telemetry.db kayıt hızı
    REAL_TIMEOUT_S = 1.5               # real modda telemetri kesilme eşiği

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)

        self._moves: List[GcodeMove] = []
        self._total_time: float = 0.0
        self._n_layers: int = 0
        self._n_circuits: int = 0

        self._state = ProductionState.BOSTA
        self._sim_t: float = 0.0           # geçerli sim zamanı (s)
        self._wall_last: float = 0.0       # son tik duvar saati
        self._speed: float = 1.0           # zaman çizelgesi hız çarpanı

        # Geometri / güvenlik sınırları
        self._diameter_mm: float = 100.0
        self._x_lo: float = -5.0
        self._x_hi: float = 395.0
        self._limit_tripped: bool = False

        # Mod / gerçek telemetri
        self._real_mode: bool = False
        self._last_real_t: float = 0.0
        self._real_x: float = 0.0
        self._real_a: float = 0.0
        self._real_T: float = 15.0
        self._real_temp_K: float = 298.15

        # Yayın zamanlayıcıları
        self._last_status_emit: float = 0.0
        self._last_telem_emit: float = 0.0
        self._seq: int = 0

        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / self.TICK_HZ))
        self._timer.timeout.connect(self._tick)

    # ── Yapılandırma ─────────────────────────────────────────────────────────

    def set_geometry(self, diameter_mm: float) -> None:
        """Mandrel çapı — fiber uzunluğu/zaman modeli ve sentetik frame için."""
        self._diameter_mm = max(1.0, float(diameter_mm))

    def set_limits(self, x_lo: float, x_hi: float) -> None:
        """Taşıyıcı (X) strok limitleri — runtime güvenlik denetimi."""
        self._x_lo = float(x_lo)
        self._x_hi = float(x_hi)

    def set_real_mode(self, real: bool) -> None:
        """True → koordinatlar gerçek telemetriden; False → sentetik."""
        self._real_mode = bool(real)

    @property
    def state(self) -> ProductionState:
        return self._state

    @property
    def total_time_s(self) -> float:
        return self._total_time

    @property
    def move_count(self) -> int:
        return len(self._moves)

    # ── G-kodu ayrıştırma ────────────────────────────────────────────────────

    def gcode_yukle(self, text: str,
                    axis_names: Optional[Dict[str, str]] = None,
                    diameter_mm: Optional[float] = None) -> int:
        """
        G-kodu metnini ayrıştır ve zaman çizelgesi kur.

        axis_names: {"x": "X", "y": "Y", "z": "Z", "a": "A"} (varsayılan standart).
        Döndürür: ayrıştırılan hareket sayısı.
        """
        if diameter_mm is not None:
            self.set_geometry(diameter_mm)

        ax = {"x": "X", "y": "Y", "z": "Z", "a": "A", "f": "F"}
        if axis_names:
            for k in ("x", "y", "z", "a"):
                if axis_names.get(k):
                    ax[k] = str(axis_names[k]).upper()[0]
        xL, yL, zL, aL, fL = (ax["x"], ax["y"], ax["z"], ax["a"], ax["f"])

        moves: List[GcodeMove] = []
        # Modal durum
        cx = cy = cz = ca = 0.0
        cf = 1000.0
        cur_layer = 0
        layer_ncirc: Dict[int, int] = {}
        have_first = False

        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue

            # Yorum satırlarından katman/devre bilgisi çıkar
            is_comment = line[0] in (";", "(") or line.startswith("//")
            ml = _LAYER_RE.search(line)
            if ml:
                cur_layer = int(ml.group(1))
                mc = _NCIRC_RE.search(line)
                if mc:
                    layer_ncirc[cur_layer] = int(mc.group(1))
            if is_comment:
                continue

            # Yorum kuyruklarını at (satır içi ; veya ( )
            code = line.split(";", 1)[0].split("(", 1)[0].strip()
            if not code:
                continue

            tokens = {m.group(1).upper(): float(m.group(2))
                      for m in _TOKEN_RE.finditer(code)}
            if not tokens:
                continue

            # Hareket satırı mı? En az bir konumsal eksen jetonu olmalı.
            has_move = any(L in tokens for L in (xL, yL, zL, aL))
            # G28 (home) → başlangıca dön
            if "G" in tokens and int(tokens.get("G", -1)) == 28:
                cx = self._x_lo if self._x_lo > -1e8 else 0.0
                ca = 0.0
                continue
            if not has_move:
                # Yalnızca F veya modal G — modal feed güncelle, segment üretme
                if fL in tokens:
                    cf = max(1.0, tokens[fL])
                continue

            if xL in tokens: cx = tokens[xL]
            if yL in tokens: cy = tokens[yL]
            if zL in tokens: cz = tokens[zL]
            if aL in tokens: ca = tokens[aL]
            if fL in tokens: cf = max(1.0, tokens[fL])

            moves.append(GcodeMove(
                x=cx, y=cy, z=cz, a=ca, feed=cf,
                layer=cur_layer, circuit=0))
            have_first = True

        # Devre atama (katman-yerel) + zaman çizelgesi
        self._assign_circuits(moves, layer_ncirc)
        self._build_timeline(moves)

        self._moves = moves
        self._n_layers = len({m.layer for m in moves if m.layer > 0})
        self._n_circuits = max((m.circuit for m in moves), default=0)

        self._reset_runtime()
        self._set_state(ProductionState.BOSTA)
        self._emit_status(force=True)
        return len(moves)

    def _assign_circuits(self, moves: List[GcodeMove],
                         layer_ncirc: Dict[int, int]) -> None:
        """Her katman içinde hareketleri devrelere böl (yaklaşık)."""
        # Katman → bu katmana ait hareket indeksleri
        by_layer: Dict[int, List[int]] = {}
        for i, m in enumerate(moves):
            by_layer.setdefault(m.layer, []).append(i)
        for layer, idxs in by_layer.items():
            ncirc = max(1, layer_ncirc.get(layer, 1))
            per = max(1, len(idxs) / ncirc)
            for local_i, gi in enumerate(idxs):
                moves[gi].circuit = min(ncirc, int(local_i / per) + 1)

    def _build_timeline(self, moves: List[GcodeMove]) -> None:
        """
        Her segmentin süresini fiber-uzunluğu/ilerleme modeline göre hesapla.

        seg_len = sqrt(dX² + arc²);  arc = dA/360 · π · D  (yüzeye yatan fiber)
        dt      = seg_len / (F/60)
        """
        t_acc = 0.0
        px = moves[0].x if moves else 0.0
        pa = moves[0].a if moves else 0.0
        circ = math.pi * self._diameter_mm
        for i, m in enumerate(moves):
            dX = m.x - px
            dA = m.a - pa
            arc = abs(dA) / 360.0 * circ
            seg_len = math.hypot(dX, arc)
            v = max(m.feed / 60.0, 1e-3)   # mm/s
            dt = seg_len / v
            # İlk hareket: anlık konumlanma; küçük taban süre ver
            if i == 0:
                dt = 0.0
            dt = min(max(dt, 0.0), 30.0)   # tek segment için makul tavan
            m.t_start = t_acc
            t_acc += dt
            m.t_end = t_acc
            px, pa = m.x, m.a
        self._total_time = t_acc

    # ── Kontrol ──────────────────────────────────────────────────────────────

    @Slot()
    def basla(self) -> bool:
        """Sarmayı başlat (BOSTA veya TAMAMLANDI'dan)."""
        if not self._moves:
            return False
        if self._state == ProductionState.CALISIYOR:
            return True
        if self._state in (ProductionState.TAMAMLANDI,
                            ProductionState.ACIL_DURDU):
            self._reset_runtime()
        self._limit_tripped = False
        self._wall_last = time.monotonic()
        self._last_real_t = time.monotonic()
        self._set_state(ProductionState.CALISIYOR)
        if not self._timer.isActive():
            self._timer.start()
        return True

    @Slot()
    def duraklat(self) -> None:
        if self._state == ProductionState.CALISIYOR:
            self._set_state(ProductionState.DURAKLATILDI)

    @Slot()
    def devam(self) -> None:
        if self._state == ProductionState.DURAKLATILDI:
            self._wall_last = time.monotonic()
            self._last_real_t = time.monotonic()
            self._set_state(ProductionState.CALISIYOR)

    @Slot()
    def acilDur(self) -> None:
        """Acil durdur — zaman çizelgesini kilitle (sıfırlamaz)."""
        if self._state in (ProductionState.CALISIYOR,
                           ProductionState.DURAKLATILDI):
            self._set_state(ProductionState.ACIL_DURDU)
        self._timer.stop()
        self._emit_status(force=True)

    @Slot()
    def sifirla(self) -> None:
        """Başa sar — zaman çizelgesini sıfırla, BOSTA'ya dön."""
        self._timer.stop()
        self._reset_runtime()
        self._set_state(ProductionState.BOSTA)
        self._emit_status(force=True)

    def _reset_runtime(self) -> None:
        self._sim_t = 0.0
        self._seq = 0
        self._last_status_emit = 0.0
        self._last_telem_emit = 0.0
        self._limit_tripped = False

    # ── Gerçek telemetri girişi (real mod) ───────────────────────────────────

    @Slot(object)
    def gercek_telemetri(self, frame) -> None:
        """RealESP32Link paketinden anlık koordinat/sensör besle (real mod)."""
        try:
            self._real_x = float(frame.x_mm)
            self._real_a = float(frame.a_deg)
            self._real_T = float(frame.T_N)
            self._real_temp_K = float(frame.temp_K)
            self._last_real_t = time.monotonic()
        except Exception:
            pass

    # ── Tik döngüsü ──────────────────────────────────────────────────────────

    def _tick(self) -> None:
        now = time.monotonic()

        if self._state == ProductionState.CALISIYOR:
            dt = (now - self._wall_last) * self._speed
            self._wall_last = now
            self._sim_t = min(self._sim_t + dt, self._total_time)

            # Real modda telemetri kesilmesi → kritik
            if self._real_mode and (now - self._last_real_t) > self.REAL_TIMEOUT_S:
                self._trip_limit(
                    "COMMS_LOST",
                    "ESP32 telemetri kesildi (bağlantı kopması)",
                    now - self._last_real_t, self.REAL_TIMEOUT_S)
                return
        else:
            # Duraklatılmış/durmuş — duvar saatini ileri taşı ki devam doğru olsun
            self._wall_last = now

        x, y, z, a, feed = self._sample(self._sim_t)

        # Real modda görüntülenen X/A gerçek donanımdan gelir
        if self._real_mode:
            x = self._real_x
            a = self._real_a

        # ── Güvenlik: X strok denetimi ──
        if not self._limit_tripped and (x < self._x_lo - 1e-6
                                        or x > self._x_hi + 1e-6):
            self._trip_limit(
                "X_OUT_OF_RANGE",
                f"X={x:.1f} mm strok limiti dışında "
                f"[{self._x_lo:.0f}, {self._x_hi:.0f}]",
                x, self._x_hi if x > self._x_hi else self._x_lo)
            return

        # 3D dijital ikiz koordinatı (her tik)
        self.koordinatGuncellendi.emit(float(x), float(y), float(z), float(a))

        # Durum paketi (~STATUS_HZ)
        if (now - self._last_status_emit) >= (1.0 / self.STATUS_HZ):
            self._emit_status()
            self._last_status_emit = now

        # Sentetik telemetri / DB kaydı (1 Hz)
        if (now - self._last_telem_emit) >= (1.0 / self.TELEM_HZ):
            self._emit_telemetry(x, a, feed)
            self._last_telem_emit = now

        # Tamamlanma
        if (self._state == ProductionState.CALISIYOR
                and self._sim_t >= self._total_time - 1e-9):
            self._set_state(ProductionState.TAMAMLANDI)
            self._emit_status(force=True)
            self._timer.stop()

    def _sample(self, t: float) -> Tuple[float, float, float, float, float]:
        """Zaman çizelgesinin t anındaki (x, y, z, a, feed) interpolasyonu."""
        if not self._moves:
            return 0.0, 0.0, 0.0, 0.0, 0.0
        if t <= 0.0:
            m = self._moves[0]
            return m.x, m.y, m.z, m.a, m.feed
        if t >= self._total_time:
            m = self._moves[-1]
            return m.x, m.y, m.z, m.a, m.feed

        idx = self._locate(t)
        m = self._moves[idx]
        prev = self._moves[idx - 1] if idx > 0 else m
        span = max(m.t_end - m.t_start, 1e-9)
        frac = max(0.0, min(1.0, (t - m.t_start) / span))
        x = prev.x + (m.x - prev.x) * frac
        y = prev.y + (m.y - prev.y) * frac
        z = prev.z + (m.z - prev.z) * frac
        a = prev.a + (m.a - prev.a) * frac
        return x, y, z, a, m.feed

    def _locate(self, t: float) -> int:
        """t'yi içeren segment indeksini bul (ikili arama)."""
        lo, hi = 0, len(self._moves) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self._moves[mid].t_end < t:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _current_move(self) -> Optional[GcodeMove]:
        if not self._moves:
            return None
        if self._sim_t >= self._total_time:
            return self._moves[-1]
        return self._moves[self._locate(self._sim_t)]

    # ── Olay/durum yayını ────────────────────────────────────────────────────

    def _trip_limit(self, code: str, msg: str,
                    value: float, threshold: float) -> None:
        self._limit_tripped = True
        self._set_state(ProductionState.ACIL_DURDU)
        self._timer.stop()
        self.sinirIhlali.emit(code, msg, float(value), float(threshold))
        self._emit_status(force=True)

    def _set_state(self, st: ProductionState) -> None:
        if st != self._state:
            self._state = st
            self.durumAdiDegisti.emit(_STATE_TR.get(st, "?"))

    def _emit_status(self, force: bool = False) -> None:
        pct = (100.0 * self._sim_t / self._total_time
               if self._total_time > 1e-9 else 0.0)
        m = self._current_move()
        layer = m.layer if m else 0
        circuit = m.circuit if m else 0
        feed = m.feed if m else 0.0
        x, _, _, a, _ = self._sample(self._sim_t)
        if self._real_mode:
            x, a = self._real_x, self._real_a
        self.durumGuncellendi.emit({
            "durum":        _STATE_TR.get(self._state, "?"),
            "durum_kodu":   int(self._state),
            "yuzde":        round(pct, 2),
            "katman":       layer,
            "toplam_katman": self._n_layers,
            "devre":        circuit,
            "toplam_devre": self._n_circuits,
            "hiz_mm_dak":   round(feed, 1),
            "x_mm":         round(x, 2),
            "a_deg":        round(a, 2),
            "gecen_s":      round(self._sim_t, 1),
            "toplam_s":     round(self._total_time, 1),
        })

    def _emit_telemetry(self, x: float, a: float, feed: float) -> None:
        """1 Hz sentetik/snapshot TelemetryFrame — DB kaydı + (mock) kartlar."""
        rpm = feed / max(math.pi * self._diameter_mm, 1e-3)  # devir/dak ~ kaba
        if self._real_mode:
            T_N = self._real_T
            temp_K = self._real_temp_K
        else:
            # Mock: makul nominal değerler (gürültüsüz, deterministik)
            T_N = 15.0
            temp_K = 298.15
        pct = (self._sim_t / self._total_time
               if self._total_time > 1e-9 else 0.0)
        self._seq = (self._seq + 1) & 0xFFFF
        frame = TelemetryFrame(
            ts_us=int(self._sim_t * 1e6),
            seq=self._seq,
            flags=1,
            x_mm=float(x),
            a_deg=float(a),
            T_N=float(T_N),
            rpm=float(rpm),
            vib_x=0.0, vib_y=0.0, vib_z=0.0,
            temp_K=float(temp_K),
            current_A=2.5,
            alpha=float(max(0.0, min(1.0, pct))),
            quality=92.0,
        )
        self.telemetriUretildi.emit(frame)
