"""
panels/uretim_tasarim_paneli.py — Üretim Tasarım Merkezi
=========================================================
Rakip yazılımlardan (TaniqWind, CADWIND, CADFIL, ComposiCAD) en iyi
özelliklerin sentezi:

• Makine Profil Yöneticisi  (CADFIL iMove modüler yaklaşımı)
• Kayma & Sürtünme Analizi  (CADWIND non-geodezik fizik simülasyonu)
• Maliyet & Süre Tahmini    (ComposiCAD maliyet kırılımı)
• Dışa Aktarma Merkezi      (CADFIL çoklu FEA/NC çıktısı)

Sinyal sözleşmesi
-----------------
  raporuGonder  = Signal(dict)  — özet raporu proje yöneticisine
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import (
    Qt, QThread, QTimer, QObject, Signal, Slot,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QGroupBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QDoubleSpinBox, QSpinBox, QComboBox, QPushButton, QLineEdit,
    QListWidget, QListWidgetItem, QTextEdit, QFileDialog,
    QCheckBox, QSplitter, QHeaderView, QMessageBox, QFrame,
    QSizePolicy, QProgressBar,
)

from ..themes.dark_industrial import COLOR

_PROFILES_PATH = Path.home() / ".filament_cam_machine_profiles.json"

# ── Makine profil veri modeli ─────────────────────────────────────────────────

@dataclass
class MachineProfile:
    isim: str = "Varsayılan"
    kontrolor_tipi: str = "grbl"          # grbl | mach3 | fanuc | custom
    x_eksen: str = "X"
    a_eksen: str = "A"
    max_x_ilerleme_mm_dak: float = 5000.0
    max_a_rpm: float = 300.0
    x_baslangic_mm: float = 0.0
    geri_cekme_mm: float = 10.0
    on_isitma_s: float = 0.0
    inch_modu: bool = False
    yorum_prefix: str = ";"
    program_bitis: str = "M30"
    hizlanma_pct: float = 10.0           # ilk/son devre % hız düşümü
    kubbe_hiz_pct: float = 60.0          # kubbe bölgelerinde % hız düşümü
    # ── 4-eksen alanları (Fas 4) — JSON geriye-uyumlu ──────────────────────
    y_eksen: str = "Y"                   # radyal sarım kafası (eye) ekseni
    z_eksen: str = "Z"                   # sarım kafası yönlendirme ekseni
    max_x_strok_mm: float = 1000.0       # taşıyıcı eksenel strok limiti
    max_y_mm: float = 500.0              # radyal kafa erişim limiti
    eye_standoff_mm: float = 0.0         # kafa-yüzey nominal mesafesi

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "MachineProfile":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})


def _load_profiles() -> List[MachineProfile]:
    if not _PROFILES_PATH.exists():
        return [MachineProfile()]
    try:
        with open(_PROFILES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [MachineProfile.from_dict(d) for d in data]
    except Exception:
        return [MachineProfile()]


def _save_profiles(profiles: List[MachineProfile]) -> None:
    try:
        with open(_PROFILES_PATH, "w", encoding="utf-8") as f:
            json.dump([p.to_dict() for p in profiles], f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ── Arkaplan analiz işçisi ────────────────────────────────────────────────────

class _AnalysisWorker(QObject):
    finished = Signal(dict)
    error    = Signal(str)

    def __init__(self, stack_dict: Dict, mandrel: Dict, material_key: str,
                 mu: float, fiber_cost: float, resin_cost: float,
                 labor_rate: float, vf: float,
                 tex: float = 800.0, overhead_pct: float = 20.0,
                 resin_density: float = 1.2, accel_pct: float = 10.0):
        super().__init__()
        self._stack         = stack_dict
        self._mandrel       = mandrel
        self._matkey        = material_key
        self._mu            = mu
        self._fiber_c       = fiber_cost
        self._resin_c       = resin_cost
        self._labor         = labor_rate
        self._vf            = vf
        self._tex           = tex           # g/km (tow linear density)
        self._overhead_pct  = overhead_pct
        self._resin_density = resin_density  # g/cm³
        self._accel_pct     = accel_pct      # % of stroke used for ramp-up

    @Slot()
    def run(self):
        try:
            result = self._compute()
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))

    def _compute(self) -> Dict:
        D_mm = self._mandrel.get("cap_mm", 100.0)
        L_mm = self._mandrel.get("uzunluk_mm", 300.0)
        R_mm = D_mm / 2.0
        mu   = self._mu
        vf   = max(0.01, min(0.99, self._vf))

        layers = self._stack.get("layers", [])

        # ── Kayma analizi ────────────────────────────────────────────────────
        slip_rows: List[Dict] = []
        for idx, lyr in enumerate(layers):
            alpha_deg = lyr.get("alpha_deg", 45.0)
            alpha_abs = abs(alpha_deg)
            t = lyr.get("layer_type", "helical")

            if t in ("hoop", "skin_finish"):
                slip = 0.0; verdict = "ok"
            elif t == "polar":
                # Polar: açı çok küçük → kayma riski yüksek
                slip_ratio = math.sin(math.radians(max(1.0, alpha_abs))) * 0.5
                slip = round(slip_ratio, 4)
                verdict = "ok" if slip <= mu * 0.7 else ("uyari" if slip <= mu else "kritik")
            else:
                # Helical – Clairaut geodezik kontrolü
                # λ ≈ tan(Δα) × r / ds (simplified)
                # Conservative: slip ~ sin²(α) / (2μ) for geodesic deviation
                if lyr.get("strategy", "geodesic") == "non_geodesic":
                    raw = abs(math.sin(math.radians(alpha_abs))) * 0.4
                else:
                    raw = 0.0
                slip = round(min(raw, 1.0), 4)
                verdict = "ok" if slip <= mu * 0.5 else ("uyari" if slip <= mu else "kritik")

            slip_rows.append({
                "idx": idx,
                "etiket": lyr.get("label", f"Katman {idx+1}"),
                "tip": t,
                "alpha_deg": alpha_deg,
                "slip_ratio": slip,
                "mu": mu,
                "verdict": verdict,
            })

        # ── Maliyet & süre hesabı ─────────────────────────────────────────────
        # Fiber yoğunluğu (carbon/epoxy tipik)
        try:
            from backend.core.material_allowables import ENGINEERING_MATERIALS
            mat = ENGINEERING_MATERIALS.get(self._matkey)
            rho_kg_m3 = getattr(mat, "density_kg_m3", 1580.0) if mat else 1580.0
        except Exception:
            rho_kg_m3 = 1580.0

        total_fiber_m  = 0.0
        total_time_s   = 0.0
        total_t_mm     = 0.0

        TIP_TR = {"helical": "Sarmal", "hoop": "Çevre", "polar": "Kutupsal",
                  "transition": "Geçiş", "skin_finish": "Kaplama"}
        layer_breakdown: List[Dict] = []

        for idx, lyr in enumerate(layers):
            alpha_deg  = abs(lyr.get("alpha_deg", 45.0))
            alpha_rad  = math.radians(max(1.0, alpha_deg))
            fw_mm      = lyr.get("fitil_genisligi_mm", 6.0)
            t_ply      = lyr.get("thickness_mm", 0.3)
            overlap    = lyr.get("cakisma_pct", 5.0) / 100.0
            feed_mm_s  = max(1.0, lyr.get("feed_mm_s", 80.0))
            tip        = lyr.get("layer_type", "helical")

            sin_a     = max(math.sin(alpha_rad), 0.01)
            pitch_mm  = fw_mm / sin_a * (1 - overlap)
            n_circ    = max(1, math.ceil(math.pi * D_mm / max(pitch_mm, 0.01)))

            if tip == "hoop":
                circuit_len_mm = math.pi * D_mm + 2 * fw_mm
                n_circ = max(1, math.ceil(L_mm / max(fw_mm * (1 - overlap), 0.01)))
            else:
                circuit_len_mm = math.sqrt((math.pi * D_mm) ** 2 + L_mm ** 2)

            fiber_len_mm = n_circ * circuit_len_mm * 2   # ×2 ileri+geri
            fiber_m      = fiber_len_mm / 1000.0

            # Hızlanma gecikme süresi — trapez profili (ivmelenme + frenleme)
            accel_dist_mm = L_mm * max(0.0, self._accel_pct) / 100.0
            n_passes      = 2 * n_circ   # her devre: ileri + geri
            t_accel_layer = n_passes * 2.0 * accel_dist_mm / feed_mm_s
            t_wind_layer  = fiber_len_mm / feed_mm_s
            layer_time_s  = t_wind_layer + t_accel_layer

            total_fiber_m  += fiber_m
            total_time_s   += layer_time_s
            total_t_mm     += t_ply

            # Tex tabanlı katman kütlesi: tex [g/km] × uzunluk [m] / 1e6 = kg
            m_f_layer = fiber_m * self._tex / 1e6

            layer_breakdown.append({
                "idx":        idx,
                "etiket":     lyr.get("label", f"Katman {idx+1}"),
                "tip_tr":     TIP_TR.get(tip, tip),
                "alpha_deg":  lyr.get("alpha_deg", 45.0),
                "n_circuits": n_circ,
                "fiber_m":    round(fiber_m, 2),
                "time_s":     round(layer_time_s, 1),
                "m_fiber_kg": round(m_f_layer, 4),
                "fiber_usd":  round(m_f_layer * self._fiber_c, 2),
            })

        # ── Tex tabanlı toplam fiber kütlesi ──────────────────────────────────
        # tex = g/km → m_fiber [kg] = total_fiber_m [m] × tex / 1_000_000
        m_fiber_kg = total_fiber_m * self._tex / 1e6

        # Reçine kütlesi — ρ ağırlıklı Vf dönüşümü
        rho_resin_kg_m3 = self._resin_density * 1000.0   # g/cm³ → kg/m³
        m_resin_kg = m_fiber_kg * (1.0 - vf) / vf * (rho_resin_kg_m3 / rho_kg_m3)
        m_total_kg = m_fiber_kg + m_resin_kg

        fiber_usd   = m_fiber_kg * self._fiber_c
        resin_usd   = m_resin_kg * self._resin_c
        labor_usd   = (total_time_s / 3600.0) * self._labor
        overhead_f  = max(0.0, self._overhead_pct) / 100.0
        overhead    = (fiber_usd + resin_usd + labor_usd) * overhead_f
        total_usd   = fiber_usd + resin_usd + labor_usd + overhead

        cost = {
            "total_fiber_m":   round(total_fiber_m, 1),
            "m_fiber_kg":      round(m_fiber_kg, 3),
            "m_resin_kg":      round(m_resin_kg, 3),
            "m_total_kg":      round(m_total_kg, 3),
            "time_s":          round(total_time_s, 1),
            "fiber_usd":       round(fiber_usd, 2),
            "resin_usd":       round(resin_usd, 2),
            "labor_usd":       round(labor_usd, 2),
            "overhead_usd":    round(overhead, 2),
            "total_usd":       round(total_usd, 2),
            "overhead_pct":    self._overhead_pct,
            "tex":             self._tex,
            "layer_breakdown": layer_breakdown,
        }

        return {"slip_rows": slip_rows, "cost": cost}


# ── Panel ─────────────────────────────────────────────────────────────────────

class UretimTasarimPaneli(QWidget):
    """
    Üretim Tasarım Merkezi — 4 sekmeli en-iyi-özellik sentez paneli.

    Dış API:
        set_layer_stack(stack)              — LayerStack nesnesinden yükle
        set_mandrel_parameters(D, L, P)     — mandrel boyutlarını güncelle
        set_material_key(key)               — malzeme seç
        apply_project(proje: dict)          — proje dict'inden yükle
    """

    raporuGonder       = Signal(dict)   # özet → proje yöneticisi
    eksenSinirUyarisi  = Signal(str)     # G-code eksen limit ihlali → alarmlar

    _DEBOUNCE_MS = 400

    def __init__(self, parent=None):
        super().__init__(parent)

        self._mandrel: Dict[str, Any] = {
            "cap_mm": 100.0, "uzunluk_mm": 300.0,
            "basinc_MPa": 10.0,
        }
        self._material_key: str = "carbon_t700_epoxy_pv"
        self._stack_dict:   Dict = {"layers": []}
        self._profiles: List[MachineProfile] = _load_profiles()
        self._active_profile_idx: int = 0
        self._last_analysis: Optional[Dict] = None
        self._last_axis_warnings: List[str] = []   # son G-code üretim limit ihlalleri

        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[_AnalysisWorker] = None
        self._pending_recalc: bool = False   # latest-wins: new data arrived while worker ran
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self._DEBOUNCE_MS)
        self._debounce.timeout.connect(self._run_analysis)

        self._build_ui()

    # ── UI inşa ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        hdr = QHBoxLayout()
        ttl = QLabel("Üretim Tasarım Merkezi")
        ttl.setProperty("role", "header")
        hdr.addWidget(ttl)
        hdr.addStretch()
        self._status_lbl = QLabel("Hazır")
        self._status_lbl.setProperty("role", "caption")
        hdr.addWidget(self._status_lbl)
        root.addLayout(hdr)

        self._tabs = QTabWidget()
        root.addWidget(self._tabs)

        self._tabs.addTab(self._build_machine_tab(),   "Makine Profilleri")
        self._tabs.addTab(self._build_slip_tab(),      "Kayma Analizi")
        self._tabs.addTab(self._build_cost_tab(),      "Maliyet & Süre")
        self._tabs.addTab(self._build_export_tab(),    "Dışa Aktarma")
        # All tabs built — now safe to populate lists
        self._refresh_profile_list()

    # ── Sekme 0: Makine Profilleri ────────────────────────────────────────────

    def _build_machine_tab(self) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # Sol: profil listesi
        left = QGroupBox("Makine Profilleri")
        ll = QVBoxLayout(left)
        self._profile_list = QListWidget()
        self._profile_list.setMaximumWidth(200)
        ll.addWidget(self._profile_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Ekle")
        add_btn.clicked.connect(self._on_add_profile)
        btn_row.addWidget(add_btn)
        del_btn = QPushButton("Sil")
        del_btn.clicked.connect(self._on_del_profile)
        btn_row.addWidget(del_btn)
        ll.addLayout(btn_row)
        save_btn = QPushButton("Kaydet")
        save_btn.clicked.connect(self._on_save_profiles)
        ll.addWidget(save_btn)
        layout.addWidget(left)

        # Sağ: profil editörü
        right = QGroupBox("Profil Ayarları")
        rl = QGridLayout(right)
        rl.setSpacing(6)
        row = 0

        def _lbl(txt): return QLabel(txt)

        rl.addWidget(_lbl("İsim:"), row, 0)
        self._pf_name = QLineEdit()
        rl.addWidget(self._pf_name, row, 1, 1, 2); row += 1

        rl.addWidget(_lbl("Kontrolör:"), row, 0)
        self._pf_ctrl = QComboBox()
        self._pf_ctrl.addItems(["grbl", "mach3", "fanuc", "custom"])
        rl.addWidget(self._pf_ctrl, row, 1, 1, 2); row += 1

        rl.addWidget(_lbl("X ekseni adı:"), row, 0)
        self._pf_xax = QLineEdit("X")
        self._pf_xax.setMaximumWidth(60)
        rl.addWidget(self._pf_xax, row, 1)
        rl.addWidget(_lbl("A ekseni adı:"), row, 2)
        self._pf_aax = QLineEdit("A")
        self._pf_aax.setMaximumWidth(60)
        rl.addWidget(self._pf_aax, row, 3); row += 1

        rl.addWidget(_lbl("Max X ilerleme (mm/dak):"), row, 0)
        self._pf_xfeed = QDoubleSpinBox()
        self._pf_xfeed.setRange(10, 30000); self._pf_xfeed.setValue(5000)
        rl.addWidget(self._pf_xfeed, row, 1, 1, 3); row += 1

        rl.addWidget(_lbl("Max A (RPM):"), row, 0)
        self._pf_arpm = QDoubleSpinBox()
        self._pf_arpm.setRange(1, 3000); self._pf_arpm.setValue(300)
        rl.addWidget(self._pf_arpm, row, 1, 1, 3); row += 1

        rl.addWidget(_lbl("X başlangıç (mm):"), row, 0)
        self._pf_xhome = QDoubleSpinBox()
        self._pf_xhome.setRange(-500, 500); self._pf_xhome.setValue(0)
        rl.addWidget(self._pf_xhome, row, 1, 1, 3); row += 1

        rl.addWidget(_lbl("Geri çekilme (mm):"), row, 0)
        self._pf_retract = QDoubleSpinBox()
        self._pf_retract.setRange(0, 200); self._pf_retract.setValue(10)
        rl.addWidget(self._pf_retract, row, 1, 1, 3); row += 1

        rl.addWidget(_lbl("Ön ısıtma (s):"), row, 0)
        self._pf_preheat = QDoubleSpinBox()
        self._pf_preheat.setRange(0, 600); self._pf_preheat.setValue(0)
        rl.addWidget(self._pf_preheat, row, 1, 1, 3); row += 1

        rl.addWidget(_lbl("Hızlanma bölgesi (%):"), row, 0)
        self._pf_accel = QDoubleSpinBox()
        self._pf_accel.setRange(1, 50); self._pf_accel.setValue(10)
        rl.addWidget(self._pf_accel, row, 1, 1, 3); row += 1

        rl.addWidget(_lbl("Kubbe hız oranı (%):"), row, 0)
        self._pf_dspeed = QDoubleSpinBox()
        self._pf_dspeed.setRange(10, 100); self._pf_dspeed.setValue(60)
        rl.addWidget(self._pf_dspeed, row, 1, 1, 3); row += 1

        self._pf_inch = QCheckBox("İnç modu")
        rl.addWidget(self._pf_inch, row, 0, 1, 4); row += 1

        apply_btn = QPushButton("Profili Uygula")
        apply_btn.clicked.connect(self._on_apply_profile_edit)
        rl.addWidget(apply_btn, row, 0, 1, 4); row += 1
        rl.setRowStretch(row, 1)

        layout.addWidget(right, stretch=1)

        self._profile_list.currentRowChanged.connect(self._on_profile_selected)
        return w

    # ── Sekme 1: Kayma Analizi ────────────────────────────────────────────────

    def _build_slip_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(QLabel("Sürtünme katsayısı μ:"))
        self._mu_spin = QDoubleSpinBox()
        self._mu_spin.setRange(0.01, 1.0)
        self._mu_spin.setSingleStep(0.05)
        self._mu_spin.setValue(0.3)
        self._mu_spin.setDecimals(3)
        self._mu_spin.setToolTip(
            "Fiber/mandrel statik sürtünme katsayısı.\n"
            "Kuru karbon/epoksi: 0.10–0.25\n"
            "Islak sarma: 0.30–0.50")
        top.addWidget(self._mu_spin)
        top.addWidget(QLabel("(CADWIND tipi sürtünme kontrolü)"))
        top.addStretch()
        analyze_btn = QPushButton("Analiz Et")
        analyze_btn.clicked.connect(self._schedule_analysis)
        top.addWidget(analyze_btn)
        layout.addLayout(top)

        # Tablo
        self._slip_table = QTableWidget(0, 6)
        self._slip_table.setHorizontalHeaderLabels(
            ["Katman", "Tür", "Açı (°)", "Kayma Oranı", "Güvenlik", "Durum"])
        self._slip_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._slip_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._slip_table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self._slip_table, stretch=1)

        # Alt özet
        bot = QHBoxLayout()
        self._slip_verdict_lbl = QLabel("Analiz bekleniyor...")
        self._slip_verdict_lbl.setProperty("role", "body")
        bot.addWidget(self._slip_verdict_lbl)
        bot.addStretch()
        self._slip_bar = QProgressBar()
        self._slip_bar.setRange(0, 100)
        self._slip_bar.setValue(0)
        self._slip_bar.setMaximumWidth(200)
        self._slip_bar.setFormat("Max kayma: %v%")
        bot.addWidget(self._slip_bar)
        layout.addLayout(bot)

        return w

    # ── Sekme 2: Maliyet & Süre ───────────────────────────────────────────────

    def _build_cost_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        # ── Sol: girdi parametreleri ──────────────────────────────────────────
        left = QGroupBox("Maliyet & Sarma Parametreleri")
        ll = QGridLayout(left)
        ll.setSpacing(5)
        row = 0

        def _dspin(lo, hi, val, dec=2, suf="", step=None):
            s = QDoubleSpinBox()
            s.setRange(lo, hi); s.setValue(val); s.setDecimals(dec)
            if suf:  s.setSuffix(f" {suf}")
            if step: s.setSingleStep(step)
            return s

        # Fiber parametreleri
        ll.addWidget(QLabel("Fiber tex değeri:"), row, 0)
        self._tex_spin = _dspin(1, 30000, 800, dec=0, suf="tex", step=100)
        self._tex_spin.setToolTip(
            "Fitil doğrusal yoğunluğu (g/km).\n"
            "Karbon CF: 200–3000 tex  |  Cam: 1200–4800 tex")
        ll.addWidget(self._tex_spin, row, 1); row += 1

        ll.addWidget(QLabel("Fiber maliyeti:"), row, 0)
        self._fiber_cost = _dspin(0.1, 1000, 25.0, suf="$/kg")
        ll.addWidget(self._fiber_cost, row, 1); row += 1

        ll.addWidget(QLabel("Reçine yoğunluğu:"), row, 0)
        self._resin_density_spin = _dspin(0.5, 2.5, 1.2, dec=2, suf="g/cm³")
        self._resin_density_spin.setToolTip("Epoksi: 1.15–1.25 | Poliester: 1.10–1.20")
        ll.addWidget(self._resin_density_spin, row, 1); row += 1

        ll.addWidget(QLabel("Reçine maliyeti:"), row, 0)
        self._resin_cost = _dspin(0.1, 500, 8.0, suf="$/kg")
        ll.addWidget(self._resin_cost, row, 1); row += 1

        ll.addWidget(QLabel("Fiber hacim oranı Vf:"), row, 0)
        self._vf_spin = _dspin(0.20, 0.75, 0.55, dec=3)
        ll.addWidget(self._vf_spin, row, 1); row += 1

        ll.addWidget(QLabel("İşçilik ücreti:"), row, 0)
        self._labor_cost = _dspin(1, 500, 45.0, dec=1, suf="$/saat")
        ll.addWidget(self._labor_cost, row, 1); row += 1

        ll.addWidget(QLabel("Genel gider oranı:"), row, 0)
        self._overhead_spin = _dspin(0, 100, 20.0, dec=1, suf="%")
        self._overhead_spin.setToolTip("Hammadde + işçilik toplamına uygulanır")
        ll.addWidget(self._overhead_spin, row, 1); row += 1

        ll.addWidget(QLabel("Hızlanma bölgesi:"), row, 0)
        self._accel_cost_spin = _dspin(0, 50, 10.0, dec=1, suf="% strok")
        self._accel_cost_spin.setToolTip(
            "Trapez hız profili: strokun bu kadar %'si ivmelenme/frenlemeye ayrılır")
        ll.addWidget(self._accel_cost_spin, row, 1); row += 1

        ll.setRowStretch(row, 1)
        calc_btn = QPushButton("Hesapla")
        calc_btn.clicked.connect(self._schedule_analysis)
        ll.addWidget(calc_btn, row, 0, 1, 2)
        top_row.addWidget(left)

        # Canlı güncelleme bağlantıları
        for sp in (self._tex_spin, self._fiber_cost, self._resin_density_spin,
                   self._resin_cost, self._vf_spin, self._labor_cost,
                   self._overhead_spin, self._accel_cost_spin):
            sp.valueChanged.connect(self._schedule_analysis)

        # ── Sağ: özet sonuçlar ────────────────────────────────────────────────
        right = QGroupBox("Tahmin Özeti")
        rl = QGridLayout(right)
        rl.setSpacing(4)
        row = 0

        self._cost_fields: Dict[str, QLabel] = {}
        items = [
            ("fiber_len",   "Toplam fiber uzunluğu",    "m"),
            ("fiber_mass",  "Fiber kütlesi",             "kg"),
            ("resin_mass",  "Reçine kütlesi",            "kg"),
            ("total_mass",  "Toplam parça kütlesi",      "kg"),
            ("time_str",    "Toplam sarma süresi",        ""),
            ("fiber_usd",   "Fiber maliyeti",            "$"),
            ("resin_usd",   "Reçine maliyeti",           "$"),
            ("labor_usd",   "İşçilik maliyeti",          "$"),
            ("overhead",    "Genel gider",               "$"),
            ("total_usd",   "Toplam maliyet",            "$"),
        ]
        for key, label, unit in items:
            lbl = QLabel(label + ":")
            rl.addWidget(lbl, row, 0)
            val = QLabel("—")
            val.setProperty("role", "body")
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            rl.addWidget(val, row, 1)
            if unit:
                rl.addWidget(QLabel(unit), row, 2)
            self._cost_fields[key] = val
            row += 1

        rl.setColumnStretch(1, 1)
        rl.setRowStretch(row, 1)
        top_row.addWidget(right, stretch=1)
        outer.addLayout(top_row)

        # ── Alt: katman kırılım tablosu ───────────────────────────────────────
        brk_grp = QGroupBox("Katman Kırılımı")
        brk_lay = QVBoxLayout(brk_grp)
        self._breakdown_table = QTableWidget(0, 9)
        self._breakdown_table.setHorizontalHeaderLabels([
            "#", "Etiket", "Tür", "Açı (°)", "Devre",
            "Fiber (m)", "Süre (dak)", "Kütle (kg)", "Maliyet ($)"
        ])
        self._breakdown_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._breakdown_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._breakdown_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._breakdown_table.setMaximumHeight(180)
        brk_lay.addWidget(self._breakdown_table)
        outer.addWidget(brk_grp)
        return w

    # ── Sekme 3: Dışa Aktarma ────────────────────────────────────────────────

    def _build_export_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        top = QGroupBox("Dışa Aktarma Seçenekleri")
        tl = QGridLayout(top)
        row = 0

        tl.addWidget(QLabel("Format:"), row, 0)
        self._export_fmt = QComboBox()
        self._export_fmt.addItems([
            "G-code (.nc)",
            "Katman Çizelgesi (.csv)",
            "ABAQUS Giriş (.inp)",
            "NASTRAN Güverte (.bdf)",
            "Kapsamlı Rapor (.txt)",
            "HTML Rapor (.html)",
        ])
        self._export_fmt.currentIndexChanged.connect(self._on_export_fmt_changed)
        tl.addWidget(self._export_fmt, row, 1, 1, 3); row += 1

        tl.addWidget(QLabel("Makine profili:"), row, 0)
        self._export_profile_cb = QComboBox()
        tl.addWidget(self._export_profile_cb, row, 1, 1, 3); row += 1

        tl.addWidget(QLabel("Hedef dosya:"), row, 0)
        self._export_path = QLineEdit()
        self._export_path.setPlaceholderText("Dosya seçin veya doğrudan girin…")
        tl.addWidget(self._export_path, row, 1, 1, 2)
        browse_btn = QPushButton("…")
        browse_btn.setMaximumWidth(36)
        browse_btn.clicked.connect(self._on_browse_export)
        tl.addWidget(browse_btn, row, 3); row += 1

        self._export_include_header = QCheckBox("Dosya başlığı ekle")
        self._export_include_header.setChecked(True)
        tl.addWidget(self._export_include_header, row, 0, 1, 2)
        self._export_comments = QCheckBox("Satır açıklamaları")
        self._export_comments.setChecked(True)
        tl.addWidget(self._export_comments, row, 2, 1, 2); row += 1

        export_btn = QPushButton("Dışa Aktar")
        export_btn.clicked.connect(self._on_export)
        tl.addWidget(export_btn, row, 0, 1, 2)
        pdf_btn = QPushButton("PDF Olarak Kaydet")
        pdf_btn.setToolTip("HTML raporu oluşturur ve PDF dosyası olarak kaydeder")
        pdf_btn.clicked.connect(self._on_save_pdf)
        tl.addWidget(pdf_btn, row, 2, 1, 2); row += 1
        layout.addWidget(top)

        preview_grp = QGroupBox("Önizleme (ilk 30 satır)")
        pl = QVBoxLayout(preview_grp)
        self._export_preview = QTextEdit()
        self._export_preview.setReadOnly(True)
        self._export_preview.setFont(self._monospace_font())
        pl.addWidget(self._export_preview)
        layout.addWidget(preview_grp, stretch=1)

        return w

    def _monospace_font(self):
        from PySide6.QtGui import QFont
        f = QFont("Courier New", 9)
        f.setStyleHint(f.StyleHint.Monospace)
        return f

    # ── Profil listesi ────────────────────────────────────────────────────────

    def _refresh_profile_list(self):
        self._profile_list.clear()
        for p in self._profiles:
            self._profile_list.addItem(p.isim)
        if self._profiles:
            idx = min(self._active_profile_idx, len(self._profiles) - 1)
            self._profile_list.setCurrentRow(idx)
            self._load_profile_to_form(self._profiles[idx])
        self._refresh_export_profile_combo()

    def _refresh_export_profile_combo(self):
        self._export_profile_cb.clear()
        for p in self._profiles:
            self._export_profile_cb.addItem(p.isim)

    def _load_profile_to_form(self, p: MachineProfile):
        self._pf_name.setText(p.isim)
        idx = self._pf_ctrl.findText(p.kontrolor_tipi)
        if idx >= 0: self._pf_ctrl.setCurrentIndex(idx)
        self._pf_xax.setText(p.x_eksen)
        self._pf_aax.setText(p.a_eksen)
        self._pf_xfeed.setValue(p.max_x_ilerleme_mm_dak)
        self._pf_arpm.setValue(p.max_a_rpm)
        self._pf_xhome.setValue(p.x_baslangic_mm)
        self._pf_retract.setValue(p.geri_cekme_mm)
        self._pf_preheat.setValue(p.on_isitma_s)
        self._pf_accel.setValue(p.hizlanma_pct)
        self._pf_dspeed.setValue(p.kubbe_hiz_pct)
        self._pf_inch.setChecked(p.inch_modu)

    def _form_to_profile(self) -> MachineProfile:
        return MachineProfile(
            isim=self._pf_name.text() or "Profil",
            kontrolor_tipi=self._pf_ctrl.currentText(),
            x_eksen=self._pf_xax.text() or "X",
            a_eksen=self._pf_aax.text() or "A",
            max_x_ilerleme_mm_dak=self._pf_xfeed.value(),
            max_a_rpm=self._pf_arpm.value(),
            x_baslangic_mm=self._pf_xhome.value(),
            geri_cekme_mm=self._pf_retract.value(),
            on_isitma_s=self._pf_preheat.value(),
            hizlanma_pct=self._pf_accel.value(),
            kubbe_hiz_pct=self._pf_dspeed.value(),
            inch_modu=self._pf_inch.isChecked(),
        )

    @Slot(int)
    def _on_profile_selected(self, row: int):
        if 0 <= row < len(self._profiles):
            self._active_profile_idx = row
            self._load_profile_to_form(self._profiles[row])

    @Slot()
    def _on_add_profile(self):
        p = MachineProfile(isim=f"Profil {len(self._profiles)+1}")
        self._profiles.append(p)
        self._refresh_profile_list()
        self._profile_list.setCurrentRow(len(self._profiles) - 1)

    @Slot()
    def _on_del_profile(self):
        row = self._profile_list.currentRow()
        if row < 0 or len(self._profiles) <= 1:
            return
        del self._profiles[row]
        self._active_profile_idx = max(0, row - 1)
        self._refresh_profile_list()

    @Slot()
    def _on_save_profiles(self):
        p = self._form_to_profile()
        row = self._profile_list.currentRow()
        if 0 <= row < len(self._profiles):
            self._profiles[row] = p
        _save_profiles(self._profiles)
        self._refresh_profile_list()
        self._status_lbl.setText("Profiller kaydedildi.")

    @Slot()
    def _on_apply_profile_edit(self):
        p = self._form_to_profile()
        row = self._profile_list.currentRow()
        if 0 <= row < len(self._profiles):
            self._profiles[row] = p
            self._profile_list.item(row).setText(p.isim)
            self._refresh_export_profile_combo()
            self._status_lbl.setText(f"'{p.isim}' güncellendi.")

    # ── Analiz tetikleyici ────────────────────────────────────────────────────

    def _schedule_analysis(self):
        self._debounce.start()

    def _run_analysis(self):
        if self._worker_thread is not None and self._worker_thread.isRunning():
            # Worker meşgul: en son snapshot'ı işaretle, biter bitmez yeniden çalış.
            self._pending_recalc = True
            return

        self._pending_recalc = False
        self._status_lbl.setText("Analiz çalışıyor…")
        mu   = self._mu_spin.value()
        fc   = self._fiber_cost.value()
        rc   = self._resin_cost.value()
        lc   = self._labor_cost.value()
        vf   = self._vf_spin.value()

        self._worker = _AnalysisWorker(
            self._stack_dict, self._mandrel,
            self._material_key, mu, fc, rc, lc, vf,
            tex=self._tex_spin.value(),
            overhead_pct=self._overhead_spin.value(),
            resin_density=self._resin_density_spin.value(),
            accel_pct=self._accel_cost_spin.value(),
        )
        self._worker_thread = QThread(self)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_analysis_done)
        self._worker.error.connect(self._on_analysis_error)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker_thread.start()

    @Slot(dict)
    def _on_analysis_done(self, result: Dict):
        self._last_analysis = result
        cost = result.get("cost", {})
        self._update_slip_tab(result.get("slip_rows", []))
        self._update_cost_tab(cost)
        self._update_breakdown_table(cost.get("layer_breakdown", []))
        self._update_export_preview()
        self._status_lbl.setText("Analiz tamamlandı.")
        if self._pending_recalc:
            self._run_analysis()

    @Slot(str)
    def _on_analysis_error(self, msg: str):
        self._status_lbl.setText(f"Hata: {msg[:80]}")
        # Hata durumunda da bekleyen yeniden hesaplamayı temizle.
        self._pending_recalc = False

    # ── Kayma sekmesini güncelle ──────────────────────────────────────────────

    def _update_slip_tab(self, rows: List[Dict]):
        self._slip_table.setRowCount(len(rows))
        if not rows:
            self._slip_verdict_lbl.setText("Katman yok.")
            return

        colors = {"ok": "#5cb85c", "uyari": "#f0ad4e", "kritik": "#d9534f"}
        max_slip = 0.0
        n_kritik = 0

        for r, row in enumerate(rows):
            tip_tr = {"helical": "Sarmal", "hoop": "Çevre",
                      "polar": "Kutupsal", "transition": "Geçiş",
                      "skin_finish": "Kaplama"}.get(row["tip"], row["tip"])
            vals = [
                row["etiket"],
                tip_tr,
                f"{row['alpha_deg']:+.1f}",
                f"{row['slip_ratio']:.4f}",
                f"μ={row['mu']:.3f}",
                row["verdict"].upper(),
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                item.setTextAlignment(Qt.AlignCenter)
                if c == 5:
                    clr = colors.get(row["verdict"], "#aaa")
                    from PySide6.QtGui import QColor
                    item.setForeground(QColor(clr))
                self._slip_table.setItem(r, c, item)

            max_slip = max(max_slip, row["slip_ratio"])
            if row["verdict"] == "kritik": n_kritik += 1

        mu = self._mu_spin.value()
        pct = min(100, int(max_slip / max(mu, 0.01) * 100))
        self._slip_bar.setValue(pct)

        if n_kritik > 0:
            self._slip_verdict_lbl.setText(
                f"⚠ {n_kritik} kritik katman — μ={mu:.3f} ile kayma riski!")
            self._slip_verdict_lbl.setStyleSheet("color:#d9534f;")
        else:
            self._slip_verdict_lbl.setText(
                f"✓ Tüm katmanlar güvenli (μ={mu:.3f}, max kayma={max_slip:.4f})")
            self._slip_verdict_lbl.setStyleSheet("color:#5cb85c;")

    # ── Maliyet sekmesini güncelle ────────────────────────────────────────────

    def _update_cost_tab(self, cost: Dict):
        t_s = cost.get("time_s", 0.0)
        h, rem = divmod(int(t_s), 3600)
        m, s   = divmod(rem, 60)
        time_str = f"{h}s {m:02d}d {s:02d}sn"

        self._cost_fields["fiber_len"].setText(f"{cost.get('total_fiber_m', 0):.1f}")
        self._cost_fields["fiber_mass"].setText(f"{cost.get('m_fiber_kg', 0):.3f}")
        self._cost_fields["resin_mass"].setText(f"{cost.get('m_resin_kg', 0):.3f}")
        self._cost_fields["total_mass"].setText(f"{cost.get('m_total_kg', 0):.3f}")
        self._cost_fields["time_str"].setText(time_str)
        self._cost_fields["fiber_usd"].setText(f"{cost.get('fiber_usd', 0):.2f}")
        self._cost_fields["resin_usd"].setText(f"{cost.get('resin_usd', 0):.2f}")
        self._cost_fields["labor_usd"].setText(f"{cost.get('labor_usd', 0):.2f}")
        ovh_pct = cost.get("overhead_pct", 20.0)
        self._cost_fields["overhead"].setText(
            f"{cost.get('overhead_usd', 0):.2f}  (%{ovh_pct:.0f})")
        self._cost_fields["total_usd"].setText(f"{cost.get('total_usd', 0):.2f}")

    # ── Katman kırılım tablosunu güncelle ─────────────────────────────────────

    def _update_breakdown_table(self, breakdown: List[Dict]):
        self._breakdown_table.setRowCount(len(breakdown))
        for r, row in enumerate(breakdown):
            t_s = row.get("time_s", 0.0)
            t_min = t_s / 60.0
            vals = [
                str(row.get("idx", r) + 1),
                row.get("etiket", ""),
                row.get("tip_tr", ""),
                f"{row.get('alpha_deg', 0):+.1f}",
                str(row.get("n_circuits", 0)),
                f"{row.get('fiber_m', 0):.1f}",
                f"{t_min:.1f}",
                f"{row.get('m_fiber_kg', 0):.4f}",
                f"{row.get('fiber_usd', 0):.2f}",
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                item.setTextAlignment(Qt.AlignCenter)
                self._breakdown_table.setItem(r, c, item)

    # ── Dışa aktarma ──────────────────────────────────────────────────────────

    def _on_export_fmt_changed(self, idx: int):
        fmts = [".nc", ".csv", ".inp", ".bdf", ".txt", ".html"]
        ext = fmts[idx] if idx < len(fmts) else ".txt"
        path = self._export_path.text()
        if path and "." in os.path.basename(path):
            base, _ = os.path.splitext(path)
            self._export_path.setText(base + ext)
        self._update_export_preview()

    @Slot()
    def _on_browse_export(self):
        fmt_idx = self._export_fmt.currentIndex()
        filters = [
            "G-code (*.nc *.gcode *.txt)",
            "CSV (*.csv)",
            "ABAQUS (*.inp)",
            "NASTRAN (*.bdf)",
            "Metin Raporu (*.txt)",
            "HTML Raporu (*.html)",
        ]
        flt = filters[fmt_idx] if fmt_idx < len(filters) else "Tüm dosyalar (*)"
        path, _ = QFileDialog.getSaveFileName(self, "Dışa Aktar", "", flt)
        if path:
            self._export_path.setText(path)

    def _update_export_preview(self):
        content = self._generate_export_content(preview_only=True)
        lines = content.split("\n")[:30]
        self._export_preview.setPlainText("\n".join(lines))

    @Slot()
    def _on_export(self):
        path = self._export_path.text().strip()
        if not path:
            QMessageBox.warning(self, "Hata", "Lütfen hedef dosya seçin.")
            return
        self._last_axis_warnings = []
        content = self._generate_export_content(preview_only=False)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self._status_lbl.setText(f"Dışa aktarıldı: {os.path.basename(path)}")
        except Exception as exc:
            QMessageBox.critical(self, "Dışa Aktarma Hatası", str(exc))
            return
        # G-code ihracında eksen limit ihlali varsa ana pencereye uyarı fırlat
        if self._export_fmt.currentIndex() == 0 and self._last_axis_warnings:
            n = len(self._last_axis_warnings)
            ilk = self._last_axis_warnings[0]
            self.eksenSinirUyarisi.emit(
                f"{n} eksen limit ihlali — ör: {ilk}")

    def _get_active_profile(self) -> MachineProfile:
        idx = self._export_profile_cb.currentIndex()
        if 0 <= idx < len(self._profiles):
            return self._profiles[idx]
        return MachineProfile()

    def _generate_export_content(self, preview_only: bool = False) -> str:
        fmt_idx = self._export_fmt.currentIndex()
        fns = [
            self._gen_gcode,
            self._gen_csv,
            self._gen_abaqus,
            self._gen_nastran,
            self._gen_report,
            self._gen_html_report,
        ]
        fn = fns[fmt_idx] if fmt_idx < len(fns) else self._gen_report
        return fn(preview_only)

    # ── 4-eksen G-code post-processor (Fas 4) ────────────────────────────────
    #
    # Eksenler:
    #   X — taşıyıcı araba (mandrel ekseni boyunca doğrusal, mm)
    #   Y — sarım kafası (eye) radyal yaklaşma/uzaklaşma (mm)
    #   Z — kafa yönlendirme açısı (derece; yerel lif açısına kilitli)
    #   A — iş mili (mandrel) sürekli dönüş (kümülatif derece)
    #
    # Clairaut senkronizasyonu: kubbede R_local küçüldükçe α_local artar;
    # ilerleme hızı (F) ve iş mili devri (A oranı) dinamik düşürülür.

    _GCODE_LINE_CAP = 20000   # güvenlik: aşırı büyük dosya koruması

    def _gcode_header(self, p: MachineProfile, meta: Dict, cmt: bool) -> List[str]:
        """Makine lehçesine göre başlık bloğu."""
        from datetime import datetime
        c   = p.yorum_prefix if p.kontrolor_tipi in ("grbl", "custom") else "("
        cc  = "" if p.kontrolor_tipi in ("grbl", "custom") else ")"
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        unit = "G20" if p.inch_modu else "G21"

        def com(txt): return f"{c} {txt}{(' ' + cc) if cc else ''}"

        head: List[str] = []
        kind = p.kontrolor_tipi

        # Fanuc program başlangıç jetonları YORUMLARDAN ÖNCE gelmeli (% ilk satır)
        if kind == "fanuc":
            head += ["%", "O0001"]

        if cmt:
            head += [
                com(f"Filament Sarma CAM — {now}"),
                com(f"Mandrel D={meta['D']:.1f}mm L={meta['L']:.1f}mm "
                    f"kubbe H={meta['H']:.1f}mm"),
                com(f"Kontrolor: {p.kontrolor_tipi}  eksenler: "
                    f"{p.x_eksen}/{p.y_eksen}/{p.z_eksen}/{p.a_eksen}"),
                com(f"Profil: {p.isim}  katman={meta['n_layers']} "
                    f"devre={meta['n_circuits']}"),
            ]

        if kind in ("grbl", "custom"):
            head += [unit, "G90", "G94", "G28"]
        elif kind == "mach3":
            head += [f"{unit} G90 G94", "G40 G49", "G28"]
        elif kind == "fanuc":
            head += [f"{unit} G90 G94", "G28 U0. W0."]
        else:
            head += [unit, "G90", "G94", "G28"]

        if p.on_isitma_s > 0:
            head.append(f"G4 P{int(p.on_isitma_s * 1000)}" +
                        (f"  {com('On isitma bekleme')}" if cmt else ""))
        return head

    def _gcode_footer(self, p: MachineProfile) -> List[str]:
        kind = p.kontrolor_tipi
        if kind == "fanuc":
            return ["G28 U0. W0.", "M5", p.program_bitis or "M30", "%"]
        if kind == "mach3":
            return ["G28", "M5", p.program_bitis or "M30"]
        return ["M5", p.program_bitis or "M30"]

    @staticmethod
    def _fmt_move(p: MachineProfile, nline: Optional[int],
                  x: float, y: float, z: float, a: float, f: float) -> str:
        """Tek 4-eksen G1 hareket satırı (lehçeye göre N-numara opsiyonlu)."""
        prefix = f"N{nline} " if nline is not None else ""
        return (f"{prefix}G1 {p.x_eksen}{x:.3f} {p.y_eksen}{y:.3f} "
                f"{p.z_eksen}{z:.3f} {p.a_eksen}{a:.3f} F{f:.0f}")

    def _gen_gcode(self, _preview: bool = False) -> str:
        p      = self._get_active_profile()
        cmt    = self._export_comments.isChecked()
        hdr    = self._export_include_header.isChecked()
        layers = self._stack_dict.get("layers", [])

        D = self._mandrel.get("cap_mm", 100.0)
        L = self._mandrel.get("uzunluk_mm", 300.0)
        H = float(self._mandrel.get("kubbe_yukseklik_mm", 0.0) or 0.0)

        # Geometri profili (geometry_engine veya saf-Python fallback)
        z_prof, r_prof, R_nom = self._build_profile_for_fea()
        # Yola yeterli ama hafif istasyon kümesi
        n_stat = 12 if _preview else 28
        z_st, r_st = self._downsample(z_prof, r_prof, target=n_stat)
        n_st = len(z_st)
        z0, z1 = z_st[0], z_st[-1]

        warnings: List[str] = []
        line_no = 10
        use_n = p.kontrolor_tipi in ("mach3", "fanuc")
        comch = p.yorum_prefix if p.kontrolor_tipi in ("grbl", "custom") else "("
        comcc = "" if p.kontrolor_tipi in ("grbl", "custom") else ")"

        def comment(txt: str) -> str:
            pre = f"N{line_no} " if use_n else ""
            return f"{pre}{comch} {txt}{(' ' + comcc) if comcc else ''}"

        # Strok limitleri (mutlak makine koordinatı)
        x_lo = p.x_baslangic_mm
        x_hi = p.x_baslangic_mm + p.max_x_strok_mm
        feed_cap = p.max_x_ilerleme_mm_dak

        # Toplam devre sayısı önceden hesap (başlık için)
        total_circuits = 0
        for lyr in layers:
            a_nom = abs(lyr.get("alpha_deg", 45.0))
            fw    = lyr.get("fitil_genisligi_mm", 6.0)
            ov    = lyr.get("cakisma_pct", 5.0) / 100.0
            sin_a = max(math.sin(math.radians(a_nom)), 1e-3)
            pitch = fw / sin_a * (1 - ov)
            total_circuits += max(1, math.ceil(math.pi * D / max(pitch, 1e-3)))

        meta = {"D": D, "L": L, "H": H, "n_layers": len(layers),
                "n_circuits": total_circuits}

        body: List[str] = []

        def emit(s: str):
            nonlocal line_no
            body.append(s)
            if use_n:
                line_no += 10

        a_pos = 0.0   # kümülatif iş mili açısı (her zaman artar)
        truncated = False

        for li, lyr in enumerate(layers):
            if len(body) > self._GCODE_LINE_CAP:
                truncated = True
                break

            a_nom = abs(lyr.get("alpha_deg", 45.0))
            fw    = lyr.get("fitil_genisligi_mm", 6.0)
            ov    = lyr.get("cakisma_pct", 5.0) / 100.0
            tip   = lyr.get("layer_type", "helical")
            feed_nom = max(1.0, lyr.get("feed_mm_s", 80.0)) * 60.0  # mm/dak

            # Hoop için efektif nominal açı ~89.5°
            a_eff = 89.5 if tip == "hoop" else a_nom
            c0 = R_nom * math.sin(math.radians(a_eff))

            sin_a = max(math.sin(math.radians(a_eff)), 1e-3)
            pitch = fw / sin_a * (1 - ov)
            n_circ = max(1, math.ceil(math.pi * D / max(pitch, 1e-3)))

            # Nominal devir kontrolü (rpm = ilerleme / hatve)
            rpm_nom = feed_nom / max(pitch, 1e-3)
            if rpm_nom > p.max_a_rpm:
                msg = (f"Katman {li+1} ({tip}): nominal devir {rpm_nom:.0f} > "
                       f"max {p.max_a_rpm:.0f} RPM")
                warnings.append(msg)
                emit(comment(f"WARNING: Axis Limit Exceeded — {msg}"))

            if cmt:
                emit(comment(f"--- Katman {li+1} {lyr.get('label','')} "
                             f"({tip}) a_nom={a_nom:.1f} devre={n_circ} ---"))

            # Devre faz kayması (kapsama için her devre çevresel ofset)
            phase_per_circuit = 360.0 / max(n_circ, 1)

            for circ in range(n_circ):
                if len(body) > self._GCODE_LINE_CAP:
                    truncated = True
                    break

                # İleri (z0→z1) ve geri (z1→z0) tarama: turnaround dahil
                for direction in (+1, -1):
                    stations = range(n_st) if direction > 0 else range(n_st - 1, -1, -1)
                    for k in stations:
                        zk = z_st[k]
                        rk = max(r_st[k], 1e-3)

                        # Clairaut yerel açı
                        a_loc = self._alpha_local_deg(a_eff, R_nom, rk)

                        # X = taşıyıcı (mutlak makine koordinatı)
                        x_pos = p.x_baslangic_mm + (zk - z0)
                        # Y = radyal kafa (yüzeyi takip + standoff)
                        y_pos = rk + p.eye_standoff_mm
                        # Z = kafa yönlendirme = yerel lif açısı
                        z_pos = a_loc

                        # Kubbe hız ölçeği: R_local/R_nom, kubbe_hiz_pct ile tabanlı
                        scale = max(p.kubbe_hiz_pct / 100.0, rk / max(R_nom, 1e-3))
                        scale = min(1.0, scale)
                        feed = min(feed_nom * scale, feed_cap)

                        # A = iş mili kümülatif açı; dθ = tan(α_loc)/r · dz
                        if k == (0 if direction > 0 else n_st - 1) and circ == 0:
                            dz = 0.0
                        else:
                            # bir önceki istasyona göre eksenel adım
                            kp = k - direction
                            dz = abs(zk - z_st[kp]) if 0 <= kp < n_st else 0.0
                        tan_a = math.tan(math.radians(min(a_loc, 89.0)))
                        d_theta = math.degrees(tan_a / rk * dz)
                        # A her zaman artar — mil tek yönde döner (yön X'te değişir)
                        a_pos += d_theta

                        # ── Sınır denetimleri ──
                        if x_pos < x_lo - 1e-6 or x_pos > x_hi + 1e-6:
                            msg = (f"Katman {li+1} devre {circ+1}: X={x_pos:.1f} "
                                   f"strok dışı [{x_lo:.0f},{x_hi:.0f}]")
                            warnings.append(msg)
                            emit(comment(f"WARNING: Axis Limit Exceeded — {msg}"))
                        if y_pos > p.max_y_mm + 1e-6:
                            msg = (f"Katman {li+1}: Y={y_pos:.1f} radyal limit "
                                   f"{p.max_y_mm:.0f} aşıldı")
                            warnings.append(msg)
                            emit(comment(f"WARNING: Axis Limit Exceeded — {msg}"))

                        emit(self._fmt_move(p, line_no if use_n else None,
                                            x_pos, y_pos, z_pos, a_pos, feed))

                # Devre sonunda çevresel faz ofseti (kapsama)
                a_pos += phase_per_circuit

        if truncated:
            body.append(comment(
                f"... ({self._GCODE_LINE_CAP}+ satir — dosya guvenlik siniri)"))

        # Uyarıları sakla (export sinyali için)
        self._last_axis_warnings = warnings

        lines: List[str] = []
        if hdr:
            lines += self._gcode_header(p, meta, cmt)
        if cmt and warnings:
            lines.append(comment(f"TOPLAM {len(warnings)} EKSEN LIMIT UYARISI"))
        lines += body
        lines += self._gcode_footer(p)
        return "\n".join(lines)

    def _gen_csv(self, _preview: bool = False) -> str:
        lines = ["Katman No,Etiket,Tür,Açı (°),Fitil Genişliği (mm),"
                 "Çakışma (%),Kalınlık (mm),İlerleme (mm/s)"]
        for i, lyr in enumerate(self._stack_dict.get("layers", []), start=1):
            lines.append(",".join([
                str(i),
                lyr.get("label", f"L{i}"),
                lyr.get("layer_type", "helical"),
                f"{lyr.get('alpha_deg', 45.0):.2f}",
                f"{lyr.get('fitil_genisligi_mm', 6.0):.2f}",
                f"{lyr.get('cakisma_pct', 5.0):.1f}",
                f"{lyr.get('thickness_mm', 0.3):.3f}",
                f"{lyr.get('feed_mm_s', 80.0):.1f}",
            ]))
        return "\n".join(lines)

    # ── ABAQUS — Clairaut tabanlı element-bazlı açı alanı ────────────────────

    def _build_profile_for_fea(self):
        """
        Mandrel için (z_mm, r_mm, R_nominal) profil verisi döndür.

        Öncelik geometry_engine.MandrelProfile motorudur; içe aktarılamazsa
        saf-Python analitik fallback (kubbe için hemisfer, gövde için sabit R)
        kullanılır. Böylece UI paneli numpy'a sıkı bağımlı kalmaz.
        """
        D = self._mandrel.get("cap_mm", 100.0)
        L = self._mandrel.get("uzunluk_mm", 300.0)
        R = D / 2.0
        H = float(self._mandrel.get("kubbe_yukseklik_mm", 0.0) or 0.0)

        # 1) Gerçek geometri motoru
        try:
            from backend.core.geometry_engine import MandrelProfile
            if H > 1e-6:
                prof = MandrelProfile.dome_cylinder_dome(L, R, H, n_points=90)
            else:
                prof = MandrelProfile.cylinder(L, R, n_points=30)
            z = [float(v) for v in prof.z_mm]
            r = [float(v) for v in prof.r_mm]
            if z and r:
                return z, r, R
        except Exception:
            pass

        # 2) Saf-Python fallback (lineer/analitik interpolasyon)
        z, r = [], []
        if H > 1e-6:
            n = 30
            for k in range(n):                      # sol kubbe (hemisfer)
                zz = H * k / (n - 1)
                r.append(max(math.sqrt(max(0.0, 2 * R * zz - zz * zz)), R * 0.01))
                z.append(zz)
            for k in range(n):                      # silindir gövde
                z.append(H + L * k / (n - 1)); r.append(R)
            for k in range(n):                      # sağ kubbe
                zl = H * k / (n - 1)
                rr = math.sqrt(max(0.0, 2 * R * (H - zl) - (H - zl) ** 2))
                z.append(H + L + zl); r.append(max(rr, R * 0.01))
        else:
            n = 24
            for k in range(n):
                z.append(L * k / (n - 1)); r.append(R)
        return z, r, R

    @staticmethod
    def _downsample(z, r, target: int = 20):
        """Yoğun profili FEA mesh'i için ~target halkaya indir (uçlar korunur)."""
        n = len(z)
        if n <= target:
            return list(z), list(r)
        step = (n - 1) / (target - 1)
        idx = sorted({int(round(i * step)) for i in range(target)})
        if idx[-1] != n - 1:
            idx.append(n - 1)
        return [z[i] for i in idx], [r[i] for i in idx]

    @staticmethod
    def _alpha_local_deg(alpha_nominal_deg: float,
                         R_nominal: float, R_local: float) -> float:
        """
        Clairaut: c0 = R_nom·sin(α_nom);  α_local = arcsin(c0 / R_local).

        Polar açıklıkta (c0/R_local ≥ 1) sinüs tanım kümesi aşılır →
        güvenli sınır 90° (hoop yönelimi) atanır. Hata fırlatılmaz.
        """
        if R_local <= 1e-6 or R_nominal <= 1e-6:
            return 90.0
        c0 = R_nominal * math.sin(math.radians(abs(alpha_nominal_deg)))
        ratio = c0 / R_local
        if ratio >= 1.0:
            return 90.0
        return math.degrees(math.asin(ratio))

    def _gen_abaqus(self, _preview: bool = False) -> str:
        from datetime import datetime
        layers = self._stack_dict.get("layers", [])

        z_prof, r_prof, R_nom = self._build_profile_for_fea()
        z_rings, r_rings = self._downsample(z_prof, r_prof, target=20)
        n_rings = len(z_rings)
        n_seg = 4 if _preview else 8        # önizlemede hafif mesh
        H = float(self._mandrel.get("kubbe_yukseklik_mm", 0.0) or 0.0)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        lines = [
            "** ABAQUS Giriş Dosyası — Filament Sarma (Clairaut kubbe açı alanı)",
            f"** Üretildi: {now}",
            f"** Mandrel: D={self._mandrel.get('cap_mm', 0):.1f}mm  "
            f"L={self._mandrel.get('uzunluk_mm', 0):.1f}mm  kubbe H={H:.1f}mm",
            "** Açı modeli (Clairaut): a_local = arcsin(R_cyl*sin(a_nom)/R_local)",
            "**   polar açıklıkta (oran>=1) güvenli sınır 90 derece (hoop) atanir.",
            "**   Her ESET_R{j} ekseni halkasi kendi yerel acisini tasir.",
            "**",
            "*Heading",
            " Filament Sarma Kompozit Kabuk — eleman bazli lif acisi",
            "**",
            "*Part, name=MANDREL",
        ]

        # ── Düğümler (dönel yüzey: halka × segment) ──────────────────────────
        lines.append("*Node")
        node_ids: List[List[int]] = []
        nid = 0
        for ri in range(n_rings):
            zz = z_rings[ri]; rr = r_rings[ri]
            row: List[int] = []
            for si in range(n_seg):
                theta = 2.0 * math.pi * si / n_seg
                nid += 1
                x = rr * math.cos(theta); y = rr * math.sin(theta)
                lines.append(f"{nid}, {x:.4f}, {y:.4f}, {zz:.4f}")
                row.append(nid)
            node_ids.append(row)

        # ── S4R kabuk elemanları (eksenel bant başına grup) ──────────────────
        lines.append("*Element, type=S4R")
        eid = 0
        band_elems: List[List[int]] = []
        for bi in range(n_rings - 1):
            belems: List[int] = []
            for si in range(n_seg):
                s2 = (si + 1) % n_seg
                n1 = node_ids[bi][si]
                n2 = node_ids[bi][s2]
                n3 = node_ids[bi + 1][s2]
                n4 = node_ids[bi + 1][si]
                eid += 1
                lines.append(f"{eid}, {n1}, {n2}, {n3}, {n4}")
                belems.append(eid)
            band_elems.append(belems)

        # ── Bant element setleri ─────────────────────────────────────────────
        for bi, belems in enumerate(band_elems, start=1):
            lines.append(f"*Elset, elset=ESET_R{bi}")
            for k in range(0, len(belems), 16):
                lines.append(", ".join(str(e) for e in belems[k:k + 16]))

        lines += [
            "*End Part",
            "**",
            "*Assembly, name=Assembly",
            "*Instance, name=MANDREL-1, part=MANDREL",
            "*End Instance",
            "*End Assembly",
            "**",
            "** Malzeme tanımları (her katman için ortotropik lamina)",
        ]
        for i, _lyr in enumerate(layers, start=1):
            lines += [
                f"*Material, name=PLY_{i}",
                "*Elastic, type=LAMINA",
                " 120000.,  8000.,  0.25,  5000.,  5000.,  4000.",
            ]

        # ── Eleman bazlı yönelim + kompozit kesit (ayrık açı alanı) ──────────
        lines += [
            "**",
            "** Eleman bazli yonelim ve laminat — kubbe aci sapmasi dahil.",
            "** Silindirik sistem (Z ekseni); kompozit katman acilari yereldir.",
        ]
        for bi in range(1, n_rings):
            R_local = 0.5 * (r_rings[bi - 1] + r_rings[bi])
            lines += [
                f"*Orientation, name=Ori_R{bi}, system=CYLINDRICAL",
                " 0., 0., 0., 0., 0., 1.",
                " 3, 0.",
                f"*Shell Section, elset=ESET_R{bi}, composite, orientation=Ori_R{bi}",
            ]
            for i, lyr in enumerate(layers, start=1):
                a_nom = abs(lyr.get("alpha_deg", 45.0))
                t     = lyr.get("thickness_mm", 0.3)
                a_loc = self._alpha_local_deg(a_nom, R_nom, R_local)
                lines.append(f" {t:.4f}, 3, PLY_{i}, {a_loc:.2f}, PLY_{i}")

        lines += [
            "**",
            "** Not: silindir govdede a_local≈a_nom; kubbeye dogru R_local",
            "**      kuculur, Clairaut geregi a_local artar (→ polar yakininda 90).",
        ]
        return "\n".join(lines)

    def _gen_nastran(self, _preview: bool = False) -> str:
        from datetime import datetime
        lines = [
            "$ NASTRAN Güverte Dosyası — Filament Sarma",
            f"$ Üretildi: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "$",
            "SOL 101",
            "CEND",
            "ECHO=NONE",
            "BEGIN BULK",
            "$",
            "$ Katman malzeme özellikleri (MAT8 — Ortotropik)",
        ]
        for i, lyr in enumerate(self._stack_dict.get("layers", []), start=1):
            mid = 100 + i
            lines.append(f"MAT8, {mid}, 120000., 8000., 0.25, 5000., 5000., 4000.")
        lines += [
            "$",
            "$ PCOMP — Tabakalı laminat özelliği",
            "PCOMP, 1,",
        ]
        for i, lyr in enumerate(self._stack_dict.get("layers", []), start=1):
            mid   = 100 + i
            angle = lyr.get("alpha_deg", 45.0)
            t     = lyr.get("thickness_mm", 0.3)
            lines.append(f"      {mid}, {t:.4f}, {angle:.2f}, YES,")
        lines += ["$", "ENDDATA"]
        return "\n".join(lines)

    def _gen_report(self, _preview: bool = False) -> str:
        from datetime import datetime
        d = self._mandrel
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [
            "=" * 60,
            "FILAMENT SARMA CAM — KAPSAMLI TASARIM RAPORU",
            f"Oluşturulma tarihi: {now}",
            "=" * 60,
            "",
            "MANDREL GEOMETRİSİ",
            f"  Çap           : {d.get('cap_mm', 0):.1f} mm",
            f"  Uzunluk       : {d.get('uzunluk_mm', 0):.1f} mm",
            f"  Çalışma basıncı: {d.get('basinc_MPa', 0):.2f} MPa",
            "",
            "KATMAN ÇİZELGESİ",
            f"  {'No':<4} {'Etiket':<20} {'Tür':<12} {'Açı':<8} {'Kalınlık':<10}",
            "-" * 56,
        ]
        for i, lyr in enumerate(self._stack_dict.get("layers", []), start=1):
            tip_tr = {"helical": "Sarmal", "hoop": "Çevre",
                      "polar": "Kutupsal"}.get(lyr.get("layer_type", ""), "Diğer")
            lines.append(
                f"  {i:<4} {lyr.get('label',''):<20} {tip_tr:<12} "
                f"{lyr.get('alpha_deg', 0):>+7.1f}° "
                f"{lyr.get('thickness_mm', 0):>8.3f} mm"
            )

        if self._last_analysis:
            cost = self._last_analysis.get("cost", {})
            t_s  = cost.get("time_s", 0)
            h, rem = divmod(int(t_s), 3600)
            m, s   = divmod(rem, 60)
            lines += [
                "",
                "ÜRETİM TAHMİNİ",
                f"  Fiber uzunluğu  : {cost.get('total_fiber_m', 0):.1f} m",
                f"  Fiber kütlesi   : {cost.get('m_fiber_kg', 0):.3f} kg",
                f"  Reçine kütlesi  : {cost.get('m_resin_kg', 0):.3f} kg",
                f"  Toplam kütle    : {cost.get('m_total_kg', 0):.3f} kg",
                f"  Sarma süresi    : {h}s {m:02d}d {s:02d}sn",
                f"  Toplam maliyet  : ${cost.get('total_usd', 0):.2f}",
                "",
                "MALİYET KIRILIMI",
                f"  Fiber           : ${cost.get('fiber_usd', 0):.2f}",
                f"  Reçine          : ${cost.get('resin_usd', 0):.2f}",
                f"  İşçilik         : ${cost.get('labor_usd', 0):.2f}",
                f"  Genel gider     : ${cost.get('overhead_usd', 0):.2f}",
            ]

            slip_rows = self._last_analysis.get("slip_rows", [])
            if slip_rows:
                lines += ["", "KAYMA ANALİZİ"]
                for row in slip_rows:
                    lines.append(
                        f"  {row['etiket']:<24} kayma={row['slip_ratio']:.4f} "
                        f"μ={row['mu']:.3f} → {row['verdict'].upper()}"
                    )

        lines += ["", "=" * 60, "Rapor sonu."]
        return "\n".join(lines)

    # ── HTML Üretim Reçetesi ──────────────────────────────────────────────────

    def _gen_html_report(self, _preview: bool = False) -> str:
        """
        Tam HTML Mühendislik Üretim Reçetesi.
        QTextDocument tarafından renderlanabilir; PDF'e de dönüştürülebilir.
        """
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        d    = self._mandrel
        D_mm = d.get("cap_mm", 0.0)
        L_mm = d.get("uzunluk_mm", 0.0)
        H_mm = float(d.get("kubbe_yukseklik_mm", 0.0) or 0.0)
        P    = d.get("basinc_MPa", 0.0)

        cost = (self._last_analysis or {}).get("cost", {})
        slip = (self._last_analysis or {}).get("slip_rows", [])
        breakdown = cost.get("layer_breakdown", [])
        layers = self._stack_dict.get("layers", [])

        # ── Zaman biçimlendirme ───────────────────────────────────────────────
        t_s = cost.get("time_s", 0.0)
        h, rem = divmod(int(t_s), 3600)
        mn, sc = divmod(rem, 60)
        time_str = f"{h}s {mn:02d}d {sc:02d}sn"

        slip_map = {r["idx"]: r for r in slip}

        # ── CSS ───────────────────────────────────────────────────────────────
        css = """
body  { font-family: Arial, sans-serif; font-size: 10pt; color: #1a1a1a;
        margin: 18px; }
h1    { background: #2c3e50; color: #ffffff; padding: 10px 16px;
        margin: 0 0 14px; font-size: 14pt; }
h1 small { font-size: 8pt; font-weight: normal; }
h2    { color: #2c3e50; border-bottom: 2px solid #3498db;
        padding-bottom: 3px; margin: 14px 0 6px; font-size: 11pt; }
table { width: 100%; border-collapse: collapse; margin: 6px 0;
        font-size: 9pt; }
th    { background: #34495e; color: #ffffff; padding: 5px 8px;
        text-align: left; }
td    { padding: 4px 8px; border-bottom: 1px solid #e0e0e0; }
.even td { background: #f5f5f5; }
.ok   { color: #27ae60; font-weight: bold; }
.uyari{ color: #e67e22; font-weight: bold; }
.kritik { color: #e74c3c; font-weight: bold; }
.summary { background: #ecf0f1; border-left: 4px solid #3498db;
           padding: 7px 12px; margin: 8px 0; }
.total-row td { font-weight: bold; background: #dde8f4; }
.footer { margin-top: 20px; font-size: 8pt; color: #95a5a6;
          border-top: 1px solid #cccccc; padding-top: 6px; }
"""

        # ── Proje özet tablosu ────────────────────────────────────────────────
        kubbe_str = f"{H_mm:.1f} mm" if H_mm > 0 else "Yok (saf silindir)"
        proj_rows = f"""
<tr><td>Dış çap (D)</td><td><b>{D_mm:.1f} mm</b></td>
    <td>Operasyon basıncı</td><td><b>{P:.2f} MPa</b></td></tr>
<tr class="even"><td>Mandrel uzunluğu (L)</td><td><b>{L_mm:.1f} mm</b></td>
    <td>Kubbe yüksekliği (H)</td><td><b>{kubbe_str}</b></td></tr>
<tr><td>Malzeme</td><td colspan="3"><b>{self._material_key}</b></td></tr>
<tr class="even"><td>Fiber tex</td><td><b>{cost.get('tex', 800.0):.0f} tex (g/km)</b></td>
    <td>Fiber hacim oranı Vf</td><td><b>{self._vf_spin.value():.3f}</b></td></tr>
"""

        # ── Katman tablosu ────────────────────────────────────────────────────
        layer_rows_html = ""
        for i, row in enumerate(breakdown):
            idx = row.get("idx", i)
            sr  = slip_map.get(idx, {})
            verdict = sr.get("verdict", "")
            verdict_cls   = {"ok": "ok", "uyari": "uyari", "kritik": "kritik"}.get(verdict, "")
            verdict_label = {"ok": "OK", "uyari": "UYARI", "kritik": "KRİTİK"}.get(verdict, "—")
            t_s_layer = row.get("time_s", 0.0)
            t_m_layer = t_s_layer / 60.0
            row_cls = "even" if i % 2 == 1 else ""
            layer_rows_html += (
                f'<tr class="{row_cls}">'
                f"<td>{idx+1}</td>"
                f"<td>{row.get('etiket','')}</td>"
                f"<td>{row.get('tip_tr','')}</td>"
                f"<td style='text-align:center'>{row.get('alpha_deg',0):+.1f}°</td>"
                f"<td style='text-align:center'>{row.get('n_circuits',0)}</td>"
                f"<td style='text-align:right'>{row.get('fiber_m',0):.1f} m</td>"
                f"<td style='text-align:right'>{t_m_layer:.1f} dak</td>"
                f"<td style='text-align:right'>{row.get('m_fiber_kg',0):.4f} kg</td>"
                f"<td style='text-align:right'>${row.get('fiber_usd',0):.2f}</td>"
                f'<td class="{verdict_cls}" style="text-align:center">{verdict_label}</td>'
                f"</tr>\n"
            )

        # ── Finansal özet tablosu ─────────────────────────────────────────────
        ovh_pct = cost.get("overhead_pct", 20.0)
        fin_rows = f"""
<tr><td>Fiber maliyeti</td><td style='text-align:right'>
    ${cost.get('fiber_usd',0):.2f}</td>
    <td>Fiber kütlesi</td><td style='text-align:right'>
    {cost.get('m_fiber_kg',0):.3f} kg</td></tr>
<tr class="even"><td>Reçine maliyeti</td><td style='text-align:right'>
    ${cost.get('resin_usd',0):.2f}</td>
    <td>Reçine kütlesi</td><td style='text-align:right'>
    {cost.get('m_resin_kg',0):.3f} kg</td></tr>
<tr><td>İşçilik maliyeti</td><td style='text-align:right'>
    ${cost.get('labor_usd',0):.2f}</td>
    <td>Toplam parça kütlesi</td><td style='text-align:right'>
    {cost.get('m_total_kg',0):.3f} kg</td></tr>
<tr class="even"><td>Genel gider (%{ovh_pct:.0f})</td><td style='text-align:right'>
    ${cost.get('overhead_usd',0):.2f}</td>
    <td>Toplam fiber uzunluğu</td><td style='text-align:right'>
    {cost.get('total_fiber_m',0):.1f} m</td></tr>
<tr class="total-row"><td colspan="2">Toplam Maliyet</td>
    <td colspan="2" style='text-align:right; font-size:12pt'>
    <b>${cost.get('total_usd',0):.2f}</b></td></tr>
"""

        # ── Kayma özet ────────────────────────────────────────────────────────
        n_warn    = sum(1 for r in slip if r.get("verdict") == "uyari")
        n_kritik  = sum(1 for r in slip if r.get("verdict") == "kritik")
        if n_kritik > 0:
            slip_summary = (f'<span class="kritik">⚠ {n_kritik} KRİTİK katman — '
                            f'derhal gözden geçirin!</span>')
        elif n_warn > 0:
            slip_summary = (f'<span class="uyari">⚠ {n_warn} uyarı — '
                            f'katman açılarını kontrol edin.</span>')
        else:
            slip_summary = '<span class="ok">✓ Tüm katmanlar kayma açısından güvenli.</span>'

        # ── Makine profili ────────────────────────────────────────────────────
        prof = self._get_active_profile()
        mach_info = (f"{prof.isim} | {prof.kontrolor_tipi.upper()} | "
                     f"Max X={prof.max_x_ilerleme_mm_dak:.0f} mm/dak | "
                     f"Max A={prof.max_a_rpm:.0f} RPM")

        html = f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<title>Mühendislik Üretim Reçetesi — {now}</title>
<style>{css}</style>
</head>
<body>
<h1>Mühendislik Üretim Reçetesi
<br><small>Filament Sarma CAM Platformu &nbsp;|&nbsp; {now}</small>
</h1>

<h2>Proje Özet Bilgileri</h2>
<table>
<tr><th>Parametre</th><th>Değer</th><th>Parametre</th><th>Değer</th></tr>
{proj_rows}
</table>

<h2>Makine Profili</h2>
<div class="summary">{mach_info}</div>

<h2>Katman Katman Tasarım Tablosu</h2>
<table>
<tr>
  <th>#</th><th>Etiket</th><th>Tür</th><th>Açı (°)</th><th>Devre</th>
  <th>Fiber (m)</th><th>Süre (dak)</th><th>Fiber Kütlesi</th>
  <th>Fiber Maliyeti</th><th>Kayma Durumu</th>
</tr>
{layer_rows_html}
</table>
<div class="summary">Kayma Analizi: {slip_summary}</div>

<h2>Finansal ve Süre Özeti</h2>
<table>
<tr><th>Maliyet Kalemi</th><th>Tutar</th><th>Malzeme / Süre</th><th>Miktar</th></tr>
{fin_rows}
</table>
<div class="summary">
  Toplam Sarma Süresi: <b>{time_str}</b>
  &nbsp;&nbsp;|&nbsp;&nbsp;
  Toplam Fiber: <b>{cost.get('total_fiber_m',0):.1f} m</b>
  &nbsp;&nbsp;|&nbsp;&nbsp;
  Makine: {prof.isim}
</div>

<div class="footer">
Oluşturan: Filament Sarma CAM Platformu &nbsp;|&nbsp;
Tarih: {now} &nbsp;|&nbsp;
Katman sayısı: {len(layers)} &nbsp;|&nbsp;
Mandrel: D={D_mm:.0f}×L={L_mm:.0f} mm
</div>
</body>
</html>"""
        return html

    # ── PDF kaydet ────────────────────────────────────────────────────────────

    @Slot()
    def _on_save_pdf(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "PDF Olarak Kaydet", "", "PDF Dosyası (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"

        html = self._gen_html_report(preview_only=False)
        try:
            from PySide6.QtGui import QTextDocument
            from PySide6.QtPrintSupport import QPrinter
            from PySide6.QtGui import QPageSize

            doc = QTextDocument()
            doc.setHtml(html)

            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(path)
            try:
                printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            except Exception:
                pass
            doc.print_(printer)
            self._status_lbl.setText(f"PDF kaydedildi: {os.path.basename(path)}")
        except ImportError:
            # QtPrintSupport yoksa HTML dosyasına düş
            html_path = path.replace(".pdf", "_rapor.html")
            try:
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html)
                QMessageBox.information(
                    self, "PDF Desteği Eksik",
                    f"PySide6.QtPrintSupport bulunamadı.\n"
                    f"HTML raporu kaydedildi:\n{html_path}")
                self._status_lbl.setText(f"HTML kaydedildi: {os.path.basename(html_path)}")
            except Exception as exc:
                QMessageBox.critical(self, "Kaydetme Hatası", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "PDF Hatası", str(exc))

    # ── Dış API ───────────────────────────────────────────────────────────────

    def set_layer_stack(self, stack) -> None:
        """LayerStack nesnesinden yükle (manual_layer_sequencer'dan)."""
        try:
            self._stack_dict = stack.to_dict() if hasattr(stack, "to_dict") else {}
        except Exception:
            self._stack_dict = {}
        self._schedule_analysis()

    def set_mandrel_parameters(self, D_mm: float, L_mm: float,
                               P_MPa: float = 10.0,
                               kubbe_yukseklik_mm: float = 0.0) -> None:
        self._mandrel = {
            "cap_mm": D_mm, "uzunluk_mm": L_mm, "basinc_MPa": P_MPa,
            "kubbe_yukseklik_mm": kubbe_yukseklik_mm,
        }
        self._schedule_analysis()

    def set_material_key(self, key: str) -> None:
        self._material_key = key
        self._schedule_analysis()

    def apply_project(self, proje: Dict[str, Any]) -> None:
        m = proje.get("mandrel", {})
        self._mandrel = {
            "cap_mm": m.get("cap_mm", 100.0),
            "uzunluk_mm": m.get("uzunluk_mm", 300.0),
            "basinc_MPa": proje.get("basinc_MPa", 10.0),
            "kubbe_yukseklik_mm": m.get("kubbe_yukseklik_mm", 0.0),
            "tip": m.get("tip", "silindir"),
        }
        self._material_key = proje.get("malzeme", self._material_key)
        layers = proje.get("katmanlar", [])
        self._stack_dict = {"layers": [
            {
                "label": f"L{i+1}",
                "layer_type": l.get("tip", "helical"),
                "alpha_deg": l.get("aci_deg", 45.0),
                "fitil_genisligi_mm": 6.0,
                "cakisma_pct": 5.0,
                "thickness_mm": l.get("ply_kalinlik_mm", 0.3),
                "feed_mm_s": 80.0,
                "strategy": "geodesic",
            }
            for i, l in enumerate(layers)
        ]}
        self._schedule_analysis()
