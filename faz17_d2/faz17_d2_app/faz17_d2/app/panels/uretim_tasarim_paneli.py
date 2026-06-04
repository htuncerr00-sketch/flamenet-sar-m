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
                 labor_rate: float, vf: float):
        super().__init__()
        self._stack   = stack_dict
        self._mandrel = mandrel
        self._matkey  = material_key
        self._mu      = mu
        self._fiber_c = fiber_cost
        self._resin_c = resin_cost
        self._labor   = labor_rate
        self._vf      = vf

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
        # Materyal yoğunluğu (carbon/epoxy tipik)
        try:
            from backend.core.material_allowables import ENGINEERING_MATERIALS
            mat = ENGINEERING_MATERIALS.get(self._matkey)
            if mat:
                rho_kg_m3 = getattr(mat, "density_kg_m3", 1580.0)
            else:
                rho_kg_m3 = 1580.0
        except Exception:
            rho_kg_m3 = 1580.0

        total_fiber_m = 0.0
        total_time_s  = 0.0
        total_t_mm    = 0.0

        for lyr in layers:
            alpha_deg = abs(lyr.get("alpha_deg", 45.0))
            alpha_rad = math.radians(max(1.0, alpha_deg))
            fw_mm = lyr.get("fitil_genisligi_mm", 6.0)
            t_ply = lyr.get("thickness_mm", 0.3)
            overlap = lyr.get("cakisma_pct", 5.0) / 100.0
            feed_mm_s = max(1.0, lyr.get("feed_mm_s", 80.0))
            tip = lyr.get("layer_type", "helical")

            sin_a = max(math.sin(alpha_rad), 0.01)
            pitch_mm = fw_mm / sin_a * (1 - overlap)
            n_circuits = max(1, math.ceil(math.pi * D_mm / max(pitch_mm, 0.01)))

            if tip == "hoop":
                circuit_len_mm = math.pi * D_mm + 2 * fw_mm
                n_circuits = max(1, math.ceil(L_mm / max(fw_mm * (1 - overlap), 0.01)))
            else:
                circuit_len_mm = math.sqrt((math.pi * D_mm) ** 2 + L_mm ** 2)

            fiber_len_mm = n_circuits * circuit_len_mm * 2  # ×2 ileri+geri
            fiber_m = fiber_len_mm / 1000.0
            total_fiber_m  += fiber_m
            total_time_s   += fiber_len_mm / feed_mm_s
            total_t_mm     += t_ply

        total_fiber_mm = total_fiber_m * 1000.0

        # Kütle (silindir mandrel)
        surface_area_mm2 = 2 * math.pi * R_mm * L_mm
        vol_fiber_mm3 = surface_area_mm2 * total_t_mm * vf
        m_fiber_kg = vol_fiber_mm3 * 1e-9 * rho_kg_m3
        m_resin_kg = m_fiber_kg * (1 - vf) / vf * (1.2 / rho_kg_m3 * 1000)
        m_total_kg = m_fiber_kg + m_resin_kg

        # Tex varsayımı: 800 tex (6 g/km) tipik CF
        tex = 800.0
        m_fiber_tex = total_fiber_mm / 1e6 * tex  # kg
        m_fiber_kg = max(m_fiber_kg, m_fiber_tex)

        fiber_usd  = m_fiber_kg  * self._fiber_c
        resin_usd  = m_resin_kg  * self._resin_c
        labor_usd  = (total_time_s / 3600.0) * self._labor
        overhead   = (fiber_usd + resin_usd + labor_usd) * 0.20
        total_usd  = fiber_usd + resin_usd + labor_usd + overhead

        cost = {
            "total_fiber_m": round(total_fiber_m, 1),
            "m_fiber_kg": round(m_fiber_kg, 3),
            "m_resin_kg": round(m_resin_kg, 3),
            "m_total_kg": round(m_total_kg, 3),
            "time_s": round(total_time_s, 1),
            "fiber_usd": round(fiber_usd, 2),
            "resin_usd": round(resin_usd, 2),
            "labor_usd": round(labor_usd, 2),
            "overhead_usd": round(overhead, 2),
            "total_usd": round(total_usd, 2),
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

    raporuGonder = Signal(dict)   # özet → proje yöneticisi

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
        layout = QHBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        left = QGroupBox("Maliyet Parametreleri")
        ll = QGridLayout(left)
        ll.setSpacing(6)
        row = 0

        def _dspin(lo, hi, val, suf=""):
            s = QDoubleSpinBox()
            s.setRange(lo, hi); s.setValue(val)
            if suf: s.setSuffix(f" {suf}")
            return s

        ll.addWidget(QLabel("Fiber maliyeti:"), row, 0)
        self._fiber_cost = _dspin(0.1, 1000, 25.0, "$/kg")
        ll.addWidget(self._fiber_cost, row, 1); row += 1

        ll.addWidget(QLabel("Reçine maliyeti:"), row, 0)
        self._resin_cost = _dspin(0.1, 500, 8.0, "$/kg")
        ll.addWidget(self._resin_cost, row, 1); row += 1

        ll.addWidget(QLabel("İşçilik ücreti:"), row, 0)
        self._labor_cost = _dspin(1, 500, 45.0, "$/saat")
        ll.addWidget(self._labor_cost, row, 1); row += 1

        ll.addWidget(QLabel("Fiber hacim oranı Vf:"), row, 0)
        self._vf_spin = _dspin(0.20, 0.75, 0.55)
        ll.addWidget(self._vf_spin, row, 1); row += 1

        ll.setRowStretch(row, 1)
        calc_btn = QPushButton("Hesapla")
        calc_btn.clicked.connect(self._schedule_analysis)
        ll.addWidget(calc_btn, row, 0, 1, 2)
        layout.addWidget(left)

        right = QGroupBox("Tahmin Sonuçları")
        rl = QGridLayout(right)
        rl.setSpacing(4)
        row = 0

        self._cost_fields: Dict[str, QLabel] = {}
        items = [
            ("fiber_len",   "Toplam fiber uzunluğu",  "m"),
            ("fiber_mass",  "Fiber kütlesi",           "kg"),
            ("resin_mass",  "Reçine kütlesi",          "kg"),
            ("total_mass",  "Toplam parça kütlesi",    "kg"),
            ("time_str",    "Sarma süresi",             ""),
            ("fiber_usd",   "Fiber maliyeti",          "$"),
            ("resin_usd",   "Reçine maliyeti",         "$"),
            ("labor_usd",   "İşçilik maliyeti",        "$"),
            ("overhead",    "Genel gider (%20)",       "$"),
            ("total_usd",   "Toplam maliyet",          "$"),
        ]
        for key, label, unit in items:
            rl.addWidget(QLabel(label + ":"), row, 0)
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
        layout.addWidget(right, stretch=1)
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
        tl.addWidget(export_btn, row, 0, 1, 4); row += 1
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
            self._material_key, mu, fc, rc, lc, vf)
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
        self._update_slip_tab(result.get("slip_rows", []))
        self._update_cost_tab(result.get("cost", {}))
        self._update_export_preview()
        self._status_lbl.setText("Analiz tamamlandı.")
        # latest-wins: worker çalışırken yeni veri geldiyse en güncel snapshot'la yeniden çalış.
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
        def _s(sec): return f"{cost.get(sec, 0.0)}"

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
        self._cost_fields["overhead"].setText(f"{cost.get('overhead_usd', 0):.2f}")
        self._cost_fields["total_usd"].setText(f"{cost.get('total_usd', 0):.2f}")

    # ── Dışa aktarma ──────────────────────────────────────────────────────────

    def _on_export_fmt_changed(self, idx: int):
        fmts = [".nc", ".csv", ".inp", ".bdf", ".txt"]
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
        content = self._generate_export_content(preview_only=False)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self._status_lbl.setText(f"Dışa aktarıldı: {os.path.basename(path)}")
        except Exception as exc:
            QMessageBox.critical(self, "Dışa Aktarma Hatası", str(exc))

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
        ]
        fn = fns[fmt_idx] if fmt_idx < len(fns) else self._gen_report
        return fn(preview_only)

    def _gen_gcode(self, _preview: bool = False) -> str:
        from datetime import datetime
        p    = self._get_active_profile()
        now  = datetime.now().strftime("%Y-%m-%d %H:%M")
        hdr  = self._export_include_header.isChecked()
        cmt  = self._export_comments.isChecked()
        c    = p.yorum_prefix
        x    = p.x_eksen
        a    = p.a_eksen
        feed = int(min(p.max_x_ilerleme_mm_dak, 2000))
        D    = self._mandrel.get("cap_mm", 100.0)
        L    = self._mandrel.get("uzunluk_mm", 300.0)

        lines = []
        if hdr:
            lines += [
                f"{c} Filament Sarma CAM — {now}",
                f"{c} Mandrel: D={D:.1f}mm  L={L:.1f}mm",
                f"{c} Kontrolör: {p.kontrolor_tipi}  X:{x}  A:{a}",
                f"{c} Profil: {p.isim}",
                "",
            ]
        if p.kontrolor_tipi in ("grbl", "custom"):
            lines += ["G21", "G90", "G28"]
        else:
            lines += ["G71", "G90", "G91.1"]

        if p.on_isitma_s > 0:
            lines.append(f"G4 P{int(p.on_isitma_s * 1000)}" +
                         (f"  {c} Ön ısıtma bekleme" if cmt else ""))

        layers = self._stack_dict.get("layers", [])
        x_pos = p.x_baslangic_mm
        a_pos = 0.0

        for lyr in layers:
            alpha = abs(lyr.get("alpha_deg", 45.0))
            fw    = lyr.get("fitil_genisligi_mm", 6.0)
            tip   = lyr.get("layer_type", "helical")

            if cmt:
                lines.append(f"{c} --- {lyr.get('label','Katman')} ({tip}) a={alpha:.1f}° ---")

            sin_a  = max(math.sin(math.radians(alpha)), 0.01)
            pitch  = fw / sin_a
            n_pass = max(1, math.ceil(math.pi * D / max(pitch, 0.01)))

            for _ in range(n_pass * 2):
                x_end = L if x_pos < L / 2 else 0.0
                dz = abs(x_end - x_pos)
                da = math.degrees(dz / max((D / 2), 1.0) * math.tan(math.radians(alpha)))
                a_end = a_pos + da
                x_pos = x_end
                a_pos = a_end
                lines.append(f"G1 {x}{x_pos:.3f} {a}{a_pos:.3f} F{feed}")

        lines += ["", p.program_bitis]
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
