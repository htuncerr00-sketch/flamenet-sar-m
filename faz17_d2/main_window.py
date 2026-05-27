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
from backend.hardware.esp32_link import MockESP32Link, ESP32LinkBase, ConnectionState
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


class FilamentWindingApp(QMainWindow):
    """Main application window."""

    def __init__(self, headless: bool = False,
                 recipe_db_path: str = "recipes.db",
                 telemetry_db_path: str = "telemetry.db"):
        super().__init__()
        self._headless = headless
        self.setWindowTitle("Filament Winding Control")
        self.resize(1600, 1000)

        # Backend stack
        self._link: ESP32LinkBase = MockESP32Link(seed=42, rate_hz=1000)
        self._stream = TelemetryStream()
        self._safety = SafetyController()
        self._motion = MotionController(self._link, self._safety)
        self._twin = DigitalTwin(TwinParams())
        self._pm = PredictiveMaintenance()
        self._recipe_db = RecipeDB(recipe_db_path)
        self._telem_db = TelemetryDB(telemetry_db_path)

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

        # Session recording (off by default until operator starts)
        self._recording = False

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
        self._panel_live = LiveProductionPanel()
        self._panel_3d = Winding3DPanel()
        self._panel_3d.set_winding_params(WindingParams())
        self._panel_replay = ReplayPanel(self._telem_db)
        self._panel_alarms = AlarmsPanel()
        self._panel_recipe = RecipeEditor(self._recipe_db)
        self._panel_commission = CommissioningPanel(self._motion, self._link)
        self._panel_pm = PredictiveMaintenancePanel(self._pm)

        self._tabs.addTab(self._panel_live, "Live Production")
        self._tabs.addTab(self._panel_3d, "3D Visualizer")
        self._tabs.addTab(self._panel_alarms, "Alarms & Safety")
        self._tabs.addTab(self._panel_replay, "Replay")
        self._tabs.addTab(self._panel_recipe, "Recipe Editor")
        self._tabs.addTab(self._panel_commission, "Commissioning")
        self._tabs.addTab(self._panel_pm, "Predictive Maint.")

        # Menu bar
        self._build_menus()

        # Toolbar
        self._build_toolbar()

        # Status bar
        self._build_statusbar()

    def _build_menus(self):
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")
        new_session = QAction("New session", self)
        new_session.setShortcut(QKeySequence.New)
        new_session.triggered.connect(self._on_new_session)
        file_menu.addAction(new_session)

        save_layout = QAction("Save layout", self)
        save_layout.triggered.connect(self._save_layout)
        file_menu.addAction(save_layout)

        file_menu.addSeparator()
        quit_action = QAction("Exit", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # View menu — tab selection
        view_menu = menu.addMenu("&View")
        for i in range(self._tabs.count()):
            label = self._tabs.tabText(i)
            act = QAction(label, self)
            act.triggered.connect(lambda checked=False, idx=i:
                self._tabs.setCurrentIndex(idx))
            view_menu.addAction(act)

        # Help menu
        help_menu = menu.addMenu("&Help")
        about = QAction("About", self)
        about.triggered.connect(self._on_about)
        help_menu.addAction(about)

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setObjectName("MainToolBar")
        tb.setMovable(False)
        self.addToolBar(tb)
        connect_act = QAction("Connect", self)
        connect_act.triggered.connect(self._on_connect)
        tb.addAction(connect_act)
        home_act = QAction("Home", self)
        home_act.triggered.connect(self._on_home)
        tb.addAction(home_act)
        record_act = QAction("● Record", self)
        record_act.setCheckable(True)
        record_act.triggered.connect(self._on_toggle_record)
        tb.addAction(record_act)
        self._record_act = record_act
        tb.addSeparator()
        estop_act = QAction("⚠ ESTOP", self)
        estop_act.triggered.connect(self._on_estop)
        tb.addAction(estop_act)

    def _build_statusbar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._sb_conn = QLabel("Disconnected")
        self._sb_frames = QLabel("0 frames")
        self._sb_drops = QLabel("0 drops")
        self._sb_alarms = QLabel("0 alarms")
        self._sb_fps = QLabel("--")
        sb.addPermanentWidget(self._sb_conn)
        sb.addPermanentWidget(self._sb_frames)
        sb.addPermanentWidget(self._sb_drops)
        sb.addPermanentWidget(self._sb_alarms)
        sb.addPermanentWidget(self._sb_fps)

    def _connect_signals(self):
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

    @Slot()
    def _on_connect(self):
        if self._link.state == ConnectionState.CONNECTED:
            return
        self._link.connect()

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
            QMessageBox.information(self, "Session started",
                f"Recording: {session_id}")

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
                QMessageBox.information(self, "Session saved",
                    f"{meta.session_id}: {meta.n_frames} frames, "
                    f"{meta.compressed} bytes compressed "
                    f"({meta.compressed*100/max(meta.n_bytes,1):.1f}% ratio)")

    @Slot(object)
    def _on_alarm_status(self, ev):
        n = self._panel_alarms._alarm_list.event_count()
        self._sb_alarms.setText(f"{n} alarms")
        if self._safety.is_halted:
            self._panel_live.set_safety_status("fatal")

    @Slot(int)
    def _on_connection_state(self, state: int):
        names = {0: "Disconnected", 1: "Connecting", 2: "Connected", 3: "Error"}
        self._sb_conn.setText(names.get(state, "?"))

    @Slot(dict)
    def _on_stats(self, stats: dict):
        self._sb_frames.setText(f"{stats.get('received',0)} frames")
        self._sb_drops.setText(f"{stats.get('dropped',0)} drops")
        # FPS from live panel
        fps = self._panel_live.current_fps()
        self._sb_fps.setText(f"UI {fps:.0f} FPS")

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
        tab = settings.value("currentTab", 0, type=int)
        if 0 <= tab < self._tabs.count():
            self._tabs.setCurrentIndex(tab)

    def _on_about(self):
        QMessageBox.about(self, "About",
            "<h2>Filament Winding Control</h2>"
            "<p>Faz 17 — Production-Grade Desktop App</p>"
            "<p>Built on Faz 1–16 backend stack.</p>")

    def closeEvent(self, ev):
        """Async-safe shutdown."""
        try:
            # Save layout
            self._save_layout()
            # Stop PM timer
            self._pm_timer.stop()
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
