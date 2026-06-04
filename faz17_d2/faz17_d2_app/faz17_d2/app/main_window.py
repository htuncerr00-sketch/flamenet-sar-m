"""
app/main_window.py — MainWindow with Docking + Panel Tabs
============================================================
Orchestrates:
  - Backend lifecycle (link, stream, safety, motion, twin)
  - 6 panels in tabs/docks
  - Persistent layout (QSettings)
  - Async-safe shutdown
"""
from __future__ import annotations
import queue, sys, os, threading
from typing import Optional

from PySide6.QtCore import Qt, QSettings, QTimer, Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QMainWindow, QTabWidget, QToolBar, QStatusBar,
    QLabel, QApplication, QMessageBox)

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
from backend.hardware.esp32_link import ESP32LinkBase, ConnectionState
from backend.hardware.telemetry_stream import TelemetryStream
from backend.core.safety_controller import SafetyController
from backend.core.motion_controller import MotionController
from backend.core.digital_twin import DigitalTwin, TwinParams
from backend.core.winding_planner import WindingParams
from backend.ai.predictive_maintenance import PredictiveMaintenance
from backend.persistence.recipe_db import RecipeDB
from backend.persistence.telemetry_db import TelemetryDB

from app.themes.dark_industrial import stylesheet
from app.workers.telemetry_worker import TelemetryWorker
from app.panels.live_production import LiveProductionPanel
from app.panels.winding_3d import Winding3DPanel
from app.panels.replay import ReplayPanel
from app.panels.alarms import AlarmsPanel
from app.panels.recipe_editor import RecipeEditor
from app.panels.commissioning import CommissioningPanel
from app.panels.predictive_maintenance import PredictiveMaintenancePanel
from app.panels.cam_panel import CAMPanel
from app.panels.proje_yoneticisi import ProjeYoneticisiPanel
from app.panels.malzeme_kutuphanesi import MalzemeKutuphanesiPanel
from app.panels.tabaka_yoneticisi import TabakaYoneticisiPanel
from app.panels.katman_dizilim_paneli import KatmanDizilimPaneli
from app.panels.uretim_tasarim_paneli import UretimTasarimPaneli
from app.link_factory import LinkConfig, make_link


class FilamentWindingApp(QMainWindow):
    """Main application window."""

    def __init__(self, headless: bool = False,
                 recipe_db_path: str = "recipes.db",
                 telemetry_db_path: str = "telemetry.db",
                 link_config: Optional[LinkConfig] = None):
        super().__init__()
        self._headless = headless
        self.setWindowTitle("Filament Sarma Kontrolü")
        self.resize(1600, 1000)

        # Backend stack — link is factory-driven (Mock or Real per config).
        # If a real link is requested but its source/driver is unavailable, we
        # degrade gracefully to mock rather than crash at startup. The reason is
        # stashed in _link_init_warning and surfaced on the status bar once it
        # exists (see _build_statusbar).
        self._link_config = link_config or LinkConfig.from_env()
        self._link_init_warning: str = ""
        try:
            self._link: ESP32LinkBase = make_link(self._link_config)
        except Exception as exc:
            self._link_init_warning = (
                f"Gerçek link başlatılamadı ({exc}); simülasyon (mock) moduna geçildi."
            )
            self._link_config.kind = "mock"
            self._link = make_link(self._link_config)
        self._stream = TelemetryStream()
        self._safety = SafetyController()
        self._motion = MotionController(self._link, self._safety)
        self._twin = DigitalTwin(TwinParams())
        self._pm = PredictiveMaintenance()
        self._recipe_db = RecipeDB(recipe_db_path)
        self._telem_db = TelemetryDB(telemetry_db_path)

        # Auto-record flag: when link transitions to CONNECTED, we start a
        # new telemetry session automatically. Cleared on disconnect.
        self._auto_record = True
        self._recording = False
        self._last_link_state = ConnectionState.DISCONNECTED

        # Link-to-stream bridge thread
        self._link_q = queue.Queue(maxsize=2000)
        self._link.subscribe(self._link_q)
        self._bridge_stop = threading.Event()
        self._bridge_thread = threading.Thread(
            target=self._link_to_stream_bridge,
            daemon=True, name="LinkBridge")
        self._bridge_thread.start()

        # Worker
        self._worker = TelemetryWorker(
            self._link, self._stream, self._safety, self._motion, self._twin)

        # UI
        self._build_ui()
        self._connect_signals()
        self._restore_layout()

        # Start systems
        self._twin.start()
        if not headless:
            self._worker.start()

    def _link_to_stream_bridge(self):
        """Move frames from link's queue → stream pub/sub.
        Runs in dedicated thread, NOT UI thread."""
        while not self._bridge_stop.is_set():
            try:
                frame = self._link_q.get(timeout=0.1)
                self._stream.feed(frame)
                if self._recording:
                    self._telem_db.record(frame)
            except queue.Empty:
                continue
            except Exception:
                continue

    def _build_ui(self):
        # Central widget: tab container
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setMovable(True)
        self.setCentralWidget(self._tabs)

        # Panels
        self._panel_proje    = ProjeYoneticisiPanel()
        self._panel_malzeme  = MalzemeKutuphanesiPanel()
        self._panel_tabaka   = TabakaYoneticisiPanel()
        self._panel_katman   = KatmanDizilimPaneli()
        self._panel_uretim   = UretimTasarimPaneli()
        self._panel_cam      = CAMPanel()
        self._panel_live     = LiveProductionPanel()
        self._panel_3d       = Winding3DPanel()
        self._panel_3d.set_winding_params(WindingParams())
        self._panel_replay   = ReplayPanel(self._telem_db)
        self._panel_alarms   = AlarmsPanel()
        self._panel_recipe   = RecipeEditor(self._recipe_db)
        self._panel_commission = CommissioningPanel(self._motion, self._link)
        self._panel_pm       = PredictiveMaintenancePanel(self._pm)

        # ── Tasarım iş akışı sekmeleri ──────────────────────────────────────
        self._tabs.addTab(self._panel_proje,   "Proje Yöneticisi")
        self._tabs.addTab(self._panel_malzeme, "Malzeme Kütüphanesi")
        self._tabs.addTab(self._panel_tabaka,  "Katman & Analiz")
        self._tabs.addTab(self._panel_katman,  "Manuel Dizilim")
        self._tabs.addTab(self._panel_uretim,  "Üretim Tasarım Merkezi")
        self._tabs.addTab(self._panel_cam,     "CAM Üretici")
        # ── Üretim & izleme sekmeleri ───────────────────────────────────────
        self._tabs.addTab(self._panel_live,        "Canlı Üretim")
        self._tabs.addTab(self._panel_3d,          "3D Görüntüleyici")
        self._tabs.addTab(self._panel_alarms,      "Alarmlar & Güvenlik")
        self._tabs.addTab(self._panel_replay,      "Tekrar Oynat")
        self._tabs.addTab(self._panel_recipe,      "Reçete Düzenleyici")
        self._tabs.addTab(self._panel_commission,  "Devreye Alma")
        self._tabs.addTab(self._panel_pm,          "Tahminsel Bakım")

        # Menu bar
        self._build_menus()

        # Toolbar
        self._build_toolbar()

        # Status bar
        self._build_statusbar()

    def _build_menus(self):
        menu = self.menuBar()

        file_menu = menu.addMenu("&Dosya")
        new_session = QAction("Yeni oturum", self)
        new_session.setShortcut(QKeySequence.New)
        new_session.triggered.connect(self._on_new_session)
        file_menu.addAction(new_session)

        save_layout = QAction("Düzeni kaydet", self)
        save_layout.triggered.connect(self._save_layout)
        file_menu.addAction(save_layout)

        file_menu.addSeparator()
        quit_action = QAction("Çıkış", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # View menu — tab selection
        view_menu = menu.addMenu("&Görünüm")
        for i in range(self._tabs.count()):
            label = self._tabs.tabText(i)
            act = QAction(label, self)
            act.triggered.connect(lambda checked=False, idx=i:
                self._tabs.setCurrentIndex(idx))
            view_menu.addAction(act)

        # Help menu
        help_menu = menu.addMenu("&Yardım")
        about = QAction("Hakkında", self)
        about.triggered.connect(self._on_about)
        help_menu.addAction(about)

    def _build_toolbar(self):
        tb = QToolBar("Ana")
        tb.setObjectName("MainToolBar")
        tb.setMovable(False)
        self.addToolBar(tb)
        connect_act = QAction("Bağlan", self)
        connect_act.triggered.connect(self._on_connect)
        tb.addAction(connect_act)
        home_act = QAction("Başlangıç Konumu", self)
        home_act.triggered.connect(self._on_home)
        tb.addAction(home_act)
        record_act = QAction("● Kayıt", self)
        record_act.setCheckable(True)
        record_act.triggered.connect(self._on_toggle_record)
        tb.addAction(record_act)
        self._record_act = record_act
        tb.addSeparator()
        estop_act = QAction("⚠ ACİL DURDUR", self)
        estop_act.triggered.connect(self._on_estop)
        tb.addAction(estop_act)

    def _build_statusbar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._sb_conn = QLabel("Bağlı değil")
        self._sb_frames = QLabel("0 kare")
        self._sb_drops = QLabel("0 kayıp")
        self._sb_alarms = QLabel("0 alarm")
        self._sb_fps = QLabel("--")
        sb.addPermanentWidget(self._sb_conn)
        sb.addPermanentWidget(self._sb_frames)
        sb.addPermanentWidget(self._sb_drops)
        sb.addPermanentWidget(self._sb_alarms)
        sb.addPermanentWidget(self._sb_fps)
        # Surface any link-init degradation (real → mock fallback) safely.
        if getattr(self, "_link_init_warning", ""):
            sb.showMessage(self._link_init_warning, 12000)

    def _connect_signals(self):
        # ── Tasarım iş akışı sinyalleri ──────────────────────────────────────
        # Proje yöneticisi → tüm tasarım panelleri
        self._panel_proje.malzemeSecildi.connect(
            self._panel_malzeme.select_material)
        self._panel_proje.malzemeSecildi.connect(
            self._panel_tabaka.set_material_key)
        self._panel_proje.malzemeSecildi.connect(
            self._panel_uretim.set_material_key)
        self._panel_proje.projeYuklendi.connect(
            self._panel_katman.apply_project)
        self._panel_proje.projeYuklendi.connect(
            self._panel_uretim.apply_project)

        # Malzeme kütüphanesi → katman yöneticileri
        self._panel_malzeme.malzemeSecildi.connect(
            self._panel_tabaka.set_material_key)
        self._panel_malzeme.malzemeSecildi.connect(
            self._panel_uretim.set_material_key)

        # Katman analizi raporu → proje yöneticisi (katman listesini güncelle)
        self._panel_tabaka.raporHazir.connect(
            self._panel_proje.apply_report)

        # Manuel dizilim paneli sinyalleri
        self._panel_katman.katmanDegisti.connect(
            self._panel_uretim.set_layer_stack)
        self._panel_katman.katmanSecildi.connect(
            self._panel_3d.highlight_layer)
        self._panel_katman.kaymaUyarisi.connect(
            self._on_kayma_uyarisi)

        # Üretim tasarım merkezi → eksen limit uyarısı → alarmlar
        self._panel_uretim.eksenSinirUyarisi.connect(
            self._on_eksen_sinir_uyarisi)

        # Worker → panels
        w = self._worker
        w.frameBatch.connect(self._panel_live.on_frame_batch)
        w.latestFrame.connect(self._panel_live.on_latest_frame)
        w.latestFrame.connect(self._panel_3d.on_latest_frame)
        w.alarmReceived.connect(self._panel_alarms.on_safety_event)
        w.alarmReceived.connect(self._on_alarm_status)
        w.motionStatusChanged.connect(self._panel_commission.on_motion_status)
        w.connectionStateChanged.connect(self._panel_live.on_connection_state)
        w.connectionStateChanged.connect(self._panel_commission.on_connection_state)
        w.connectionStateChanged.connect(self._on_connection_state)
        w.statsUpdated.connect(self._on_stats)

        # Commissioning → backend
        cp = self._panel_commission
        cp.connectRequested.connect(self._on_connect)
        cp.disconnectRequested.connect(self._on_disconnect)
        cp.homeRequested.connect(self._on_home)
        cp.jogRequested.connect(self._on_jog)
        cp.estopRequested.connect(self._on_estop)
        cp.portChangeRequested.connect(self._on_port_change)

        # Alarms → backend
        self._panel_alarms.estopRequested.connect(self._on_estop)
        self._panel_alarms.clearHaltRequested.connect(self._on_clear_halt)

        # Recipe → 3D viz update
        self._panel_recipe.recipeLoaded.connect(self._on_recipe_loaded)

        # PM bridge: feed sample data periodically
        self._pm_timer = QTimer(self)
        self._pm_timer.setInterval(1000)   # 1Hz
        self._pm_timer.timeout.connect(self._pm_tick)
        self._pm_timer.start()

        # Link diagnostics: 2 Hz refresh on commissioning panel
        self._link_diag_timer = QTimer(self)
        self._link_diag_timer.setInterval(500)
        self._link_diag_timer.timeout.connect(self._link_diag_tick)
        self._link_diag_timer.start()

    @Slot()
    def _link_diag_tick(self):
        """Push link diagnostics to commissioning panel."""
        d = {}
        if hasattr(self._link, "diagnostics"):
            diag = self._link.diagnostics
            d = {
                "frames":    diag.n_frames_ok,
                "crc":       diag.n_crc_errors,
                "sync":      diag.n_sync_errors,
                "partial":   diag.n_partial_packets,
                "watchdog":  diag.n_watchdog_trips,
                "reconnect": f"{diag.n_reconnects_ok}/{diag.n_reconnect_tries}",
                "byte_in":   f"{diag.n_bytes_in/1024:.1f} KB",
                "age":       f"{diag.age_since_last_frame_s*1000:.0f} ms"
                             if diag.age_since_last_frame_s >= 0 else "—",
            }
        else:
            # Mock link — just show frame count
            d = {"frames": getattr(self._link, "n_frames", 0)}
        if hasattr(self._panel_commission, "on_diagnostics"):
            self._panel_commission.on_diagnostics(d)

    @Slot()
    def _on_connect(self):
        if self._link.state == ConnectionState.CONNECTED:
            return
        self._link.connect()

    @Slot(str, int)
    def _on_port_change(self, port: str, baud: int):
        """Operator changed port/baud in commissioning panel.

        Strategy: if the link is a RealESP32Link and currently disconnected,
        reconfigure it in place. If it's currently a MockESP32Link, swap
        it for a RealESP32Link with the requested port. Either way, the
        operator must click Connect next.
        """
        # RealESP32Link may be a stub (source missing) — import defensively so a
        # missing hardware module never crashes the port-change handler.
        try:
            from backend.hardware.real_esp32_link import RealESP32Link
        except Exception as exc:
            self.statusBar().showMessage(
                f"Gerçek link modülü kullanılamıyor ({exc}); port değişimi "
                f"yok sayıldı.", 8000)
            return

        # Make sure we're disconnected first
        try:
            self._link.disconnect()
        except Exception:
            pass

        # Swap if needed
        if not isinstance(self._link, RealESP32Link):
            # Re-create as Real with the picked port. make_link raises a clear
            # RuntimeError if the real link is unavailable — catch it, keep the
            # current (mock) link, and log to the status bar.
            prev_kind = self._link_config.kind
            self._link_config.kind = "real"
            self._link_config.port = port
            self._link_config.baud = baud
            try:
                new_link = make_link(self._link_config)
            except Exception as exc:
                self._link_config.kind = prev_kind  # roll back config
                self.statusBar().showMessage(
                    f"Gerçek donanıma geçilemedi ({exc}); mevcut bağlantı "
                    f"korunuyor.", 8000)
                return
            self._link = new_link
            # Re-wire link → bridge queue + motion controller
            self._link_q = queue.Queue(maxsize=2000)
            self._link.subscribe(self._link_q)
            self._motion._link = self._link  # rebind motion to new link
            # Re-wire worker connection state signal (worker reads link state)
            self._worker._link = self._link
            self.statusBar().showMessage(
                f"Gerçek ESP32 link hazır: {port} @ {baud} baud "
                f"(Bağlan'a basın).", 6000)
        else:
            # Reconfigure existing real link's port path
            try:
                self._link.set_port(port, baud)
                self.statusBar().showMessage(
                    f"Port güncellendi: {port} @ {baud} baud.", 5000)
            except RuntimeError as exc:
                self.statusBar().showMessage(
                    f"Port güncellenemedi ({exc}).", 6000)

    @Slot()
    def _on_disconnect(self):
        try:
            self._link.disconnect()
        except Exception:
            pass

    @Slot()
    def _on_home(self):
        # Run in worker thread to avoid blocking UI
        threading.Thread(target=self._motion.home, daemon=True).start()

    @Slot(str, float, float)
    def _on_jog(self, axis: str, distance: float, feed: float):
        self._motion.jog(axis, distance, feed)

    @Slot()
    def _on_estop(self):
        self._motion.emergency_stop("UI_OPERATOR")
        self._panel_live.set_running_status(False)

    @Slot()
    def _on_clear_halt(self):
        self._safety.clear_halt()
        self._panel_alarms.on_halt_cleared()
        self._panel_live.set_safety_status("ok")

    @Slot(int, float)
    def _on_kayma_uyarisi(self, layer_idx: int, slip_ratio: float):
        """Manuel dizilim kayma uyarısını alarm paneline yönlendir."""
        import time as _t
        from backend.core.safety_controller import SafetyEvent, SafetyLevel
        ev = SafetyEvent(
            level=SafetyLevel.WARN,
            code="SLIP_RATIO",
            msg=f"Katman {layer_idx}: kayma oranı μ sınırını aştı",
            value=slip_ratio,
            threshold=0.5,
            timestamp=_t.time(),
        )
        self._panel_alarms.on_safety_event(ev)

    @Slot(str)
    def _on_eksen_sinir_uyarisi(self, mesaj: str):
        """G-code üretiminde eksen limit ihlalini alarm paneline yönlendir."""
        import time as _t
        from backend.core.safety_controller import SafetyEvent, SafetyLevel
        ev = SafetyEvent(
            level=SafetyLevel.WARN,
            code="AXIS_LIMIT",
            msg=f"G-code eksen limiti: {mesaj}",
            value=0.0,
            threshold=0.0,
            timestamp=_t.time(),
        )
        self._panel_alarms.on_safety_event(ev)

    @Slot()
    def _on_new_session(self):
        # Stop current recording
        if self._recording:
            self._telem_db.close_session()
        # Start new
        import time as _t
        session_id = f"S_{int(_t.time())}"
        if self._telem_db.start_session(session_id):
            self._recording = True
            self._record_act.setChecked(True)
            QMessageBox.information(self, "Oturum başladı",
                f"Kaydediliyor: {session_id}")

    @Slot(bool)
    def _on_toggle_record(self, checked: bool):
        if checked and not self._recording:
            import time as _t
            session_id = f"S_{int(_t.time())}"
            if self._telem_db.start_session(session_id):
                self._recording = True
        elif not checked and self._recording:
            meta = self._telem_db.close_session()
            self._recording = False
            if meta:
                self._panel_replay._refresh_session_list()
                QMessageBox.information(self, "Oturum kaydedildi",
                    f"{meta.session_id}: {meta.n_frames} kare, "
                    f"{meta.compressed} bayt sıkıştırılmış "
                    f"({meta.compressed*100/max(meta.n_bytes,1):.1f}% oran)")

    @Slot(object)
    def _on_alarm_status(self, ev):
        n = self._panel_alarms._alarm_list.event_count()
        self._sb_alarms.setText(f"{n} alarm")
        if self._safety.is_halted:
            self._panel_live.set_safety_status("fatal")

    @Slot(int)
    def _on_connection_state(self, state: int):
        names = {0: "Bağlı değil", 1: "Bağlanıyor", 2: "Bağlandı", 3: "Hata"}
        self._sb_conn.setText(names.get(state, "?"))

        # Auto-record: start a session when we transition to CONNECTED,
        # close when we leave CONNECTED. Operator can still control
        # recording manually via the toolbar checkbox.
        prev = self._last_link_state
        self._last_link_state = ConnectionState(state) if state in (0,1,2,3) else ConnectionState.DISCONNECTED
        if not self._auto_record:
            return
        if state == ConnectionState.CONNECTED.value and prev != ConnectionState.CONNECTED:
            self._auto_record_start()
        elif state != ConnectionState.CONNECTED.value and prev == ConnectionState.CONNECTED:
            self._auto_record_stop()

    def _auto_record_start(self) -> None:
        """Begin a telemetry session on link connect."""
        if self._recording:
            return
        import time as _t
        session_id = f"S_auto_{int(_t.time())}"
        if self._telem_db.start_session(session_id):
            self._recording = True
            if hasattr(self, "_record_act"):
                self._record_act.setChecked(True)
            self._sb_conn.setText(f"Bağlandı · kay {session_id}")

    def _auto_record_stop(self) -> None:
        """Close telemetry session on link disconnect."""
        if not self._recording:
            return
        meta = self._telem_db.close_session()
        self._recording = False
        if hasattr(self, "_record_act"):
            self._record_act.setChecked(False)
        if meta and hasattr(self, "_panel_replay"):
            try:
                self._panel_replay._refresh_session_list()
            except Exception:
                pass

    @Slot(dict)
    def _on_stats(self, stats: dict):
        self._sb_frames.setText(f"{stats.get('received',0)} kare")
        self._sb_drops.setText(f"{stats.get('dropped',0)} kayıp")
        # FPS from live panel
        fps = self._panel_live.current_fps()
        self._sb_fps.setText(f"Arayüz {fps:.0f} FPS")

    @Slot(object)
    def _on_recipe_loaded(self, r):
        # Convert recipe to WindingParams for 3D viz
        from backend.core.winding_planner import WindingParams
        params = WindingParams(
            mandrel_R_mm=50.0, mandrel_L_mm=300.0,
            alpha_deg=r.alpha_deg,
            n_layers=r.n_layers, tow_width_mm=10.0,
            fiber_tension_N=r.tension_N,
            feed_mm_s=r.feed_mm_s)
        self._panel_3d.set_winding_params(params)

    def _pm_tick(self):
        # Sample latest frame to drive PM advance
        recent = self._stream.snapshot(50)
        if not recent: return
        import math
        vib_rms = math.sqrt(sum((f.vib_x**2 + f.vib_y**2 + f.vib_z**2)
                            for f in recent) / len(recent))
        temp_C = sum(f.temp_K - 273.15 for f in recent) / len(recent)
        cur_A = sum(f.current_A for f in recent) / len(recent)
        # Advance 1/3600 hour per tick (1 second)
        self._pm.step(dt_h=1/3600, vib_rms=vib_rms,
                      temp_C=temp_C, current_A=cur_A)

    def _save_layout(self):
        settings = QSettings("FaramentWinding", "DesktopApp")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("windowState", self.saveState())
        settings.setValue("currentTab", self._tabs.currentIndex())

    def _restore_layout(self):
        settings = QSettings("FaramentWinding", "DesktopApp")
        geom = settings.value("geometry")
        if geom: self.restoreGeometry(geom)
        ws = settings.value("windowState")
        if ws: self.restoreState(ws)
        tab = settings.value("currentTab", 0, type=int)  # 0 = Proje Yöneticisi
        if 0 <= tab < self._tabs.count():
            self._tabs.setCurrentIndex(tab)

    def _on_about(self):
        QMessageBox.about(self, "Hakkında",
            "<h2>Filament Sarma Kontrolü</h2>"
            "<p>Faz 17 — Üretim Kalitesinde Masaüstü Uygulaması</p>"
            "<p>Faz 1–16 arka uç katmanı üzerine inşa edilmiştir.</p>")

    def closeEvent(self, ev):
        """Async-safe shutdown."""
        try:
            # Save layout
            self._save_layout()
            # Stop PM timer
            self._pm_timer.stop()
            # Stop link diagnostics timer
            if hasattr(self, "_link_diag_timer"):
                self._link_diag_timer.stop()
            # Close active session
            if self._recording:
                self._telem_db.close_session()
            # Stop worker
            if self._worker is not None:
                self._worker.stop()
                self._worker.wait(2000)
            # Stop replay
            self._panel_replay.shutdown()
            # Stop twin
            self._twin.stop()
            # Stop bridge thread
            self._bridge_stop.set()
            if self._bridge_thread.is_alive():
                self._bridge_thread.join(timeout=1.0)
            # Disconnect link
            try: self._link.disconnect()
            except Exception: pass
        except Exception as e:
            print(f"Shutdown error: {e}", file=sys.stderr)
        super().closeEvent(ev)
