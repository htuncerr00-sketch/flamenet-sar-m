"""
app/autosave_manager.py — Faz 25 Sprint 3: Auto-Save + Crash Recovery
======================================================================
Bileşenler:
  AutoSaveVersion : tek otomatik kayıt sürümünün meta verisi
  AutoSaveManager : 30 s debounce + 5 sürüm rotasyonu + oturum kilidi
  RecoveryWizard  : açılışta sürüm seçimli kurtarma sihirbazı

Tasarım (bkz. SPRINT3_DESIGN.md §1-2):
  * Veri kaynağı bir callback'tir (ProjeYoneticisi._read_form) — tam v2.0
    proje dict'i yazılır: katman dizilimi + mandrel + CAM + makine ayarları.
  * Yazım atomiktir (.tmp → os.replace); yarım dosya asla sürüm sayılmaz.
  * Oturum kilidi (session.lock) temiz kapanışta silinir; açılışta hâlâ
    duruyorsa önceki oturum çökmüş demektir → RecoveryWizard.
  * Tüm işlemler UI thread'de, <5 ms — telemetri/güvenlik yollarından
    tamamen bağımsız (AD-008 etkilenmez).
"""
from __future__ import annotations

import json
import os
import re
import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton,
)

_DEFAULT_BASE_DIR = Path.home() / ".flamenet_sar"
_AUTOSAVE_SUBDIR = "autosave"
_LOCK_NAME = "session.lock"
_TS_FORMAT = "%Y%m%d_%H%M%S"
_FNAME_RE = re.compile(r"^(?P<slug>.+)_(?P<ts>\d{8}_\d{6})\.fwp\.bak$")


def _slugify(name: str) -> str:
    """Proje adını güvenli dosya adı parçasına çevir."""
    s = re.sub(r"[^\w\-]+", "_", name.strip(), flags=re.UNICODE)
    return s.strip("_") or "proje"


@dataclass
class AutoSaveVersion:
    """Diskteki tek otomatik kayıt sürümü."""
    path: str
    timestamp: datetime.datetime
    proje_adi: str
    n_katman: int

    @property
    def label(self) -> str:
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        return f"🕒 {ts} — {self.proje_adi} ({self.n_katman} katman)"


class AutoSaveManager(QObject):
    """Debounce'lu otomatik kayıt + sürüm rotasyonu + çökme kilidi."""

    autosaved = Signal(str)   # yazılan dosya yolu

    def __init__(self,
                 data_provider: Callable[[], Dict[str, Any]],
                 dirty_provider: Callable[[], bool],
                 base_dir: Optional[str] = None,
                 max_versions: int = 5,
                 debounce_ms: int = 30_000,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._data_provider = data_provider
        self._dirty_provider = dirty_provider
        self._base = Path(base_dir) if base_dir else _DEFAULT_BASE_DIR
        self._dir = self._base / _AUTOSAVE_SUBDIR
        self._max_versions = max(1, int(max_versions))

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(debounce_ms))
        self._timer.timeout.connect(self._on_timeout)

    # ── Debounce / yazım ─────────────────────────────────────────────────────

    def notify_change(self) -> None:
        """Değişiklik bildirimi — 30 s'lik tek atımlık sayaç başlat.

        Sayaç zaten çalışıyorsa yeniden BAŞLATILMAZ: sürekli düzenlemede
        bile en geç debounce süresinde bir yedek alınır.
        """
        if not self._timer.isActive():
            self._timer.start()

    def _on_timeout(self) -> None:
        try:
            if self._dirty_provider():
                self.force_save()
        except Exception:
            pass  # otomatik kayıt asla uygulamayı düşürmez

    def force_save(self) -> Optional[str]:
        """Hemen bir sürüm yaz (rotasyon uygulanır). Yol döner, hata → None."""
        try:
            data = self._data_provider()
            if not isinstance(data, dict):
                return None
            self._dir.mkdir(parents=True, exist_ok=True)
            slug = _slugify(str(data.get("proje_adi", "proje")))
            ts = datetime.datetime.now().strftime(_TS_FORMAT)
            path = self._dir / f"{slug}_{ts}.fwp.bak"
            tmp = self._dir / f".{slug}_{ts}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)   # atomik — yarım dosya kalmaz
            self._rotate()
            self.autosaved.emit(str(path))
            return str(path)
        except Exception:
            return None

    def _rotate(self) -> None:
        """En yeni `max_versions` sürümü tut, fazlasını sil."""
        versions = self.list_versions()
        for v in versions[self._max_versions:]:
            try:
                os.remove(v.path)
            except OSError:
                pass

    # ── Sürüm listesi / yükleme ──────────────────────────────────────────────

    def list_versions(self) -> List[AutoSaveVersion]:
        """Geçerli sürümleri en yeniden eskiye listele.

        Bozuk JSON ve yarım .tmp dosyaları sessizce atlanır.
        """
        out: List[AutoSaveVersion] = []
        if not self._dir.is_dir():
            return out
        for p in self._dir.iterdir():
            m = _FNAME_RE.match(p.name)
            if not m:
                continue
            try:
                ts = datetime.datetime.strptime(m.group("ts"), _TS_FORMAT)
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    continue
                n_katman = len(
                    (data.get("katman_yigini") or {}).get("layers", [])
                ) or len(data.get("katmanlar", []))
                out.append(AutoSaveVersion(
                    path=str(p), timestamp=ts,
                    proje_adi=str(data.get("proje_adi", "?")),
                    n_katman=n_katman))
            except Exception:
                continue
        # Aynı saniyedeki yazımlar için dosya adı ikincil anahtar
        out.sort(key=lambda v: (v.timestamp, v.path), reverse=True)
        return out

    def load_version(self, path: str) -> Optional[Dict[str, Any]]:
        """Sürüm dosyasını oku (migrasyon ÇAĞIRANIN sorumluluğunda —
        ProjeYoneticisi.load_project_dict her zaman migrate eder)."""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    # ── Oturum kilidi (çökme tespiti) ────────────────────────────────────────

    @property
    def _lock_path(self) -> Path:
        return self._base / _LOCK_NAME

    def has_stale_lock(self) -> bool:
        """Önceki oturumdan kalmış kilit var mı (= çökme tespit edildi)."""
        return self._lock_path.exists()

    def acquire_lock(self) -> None:
        try:
            self._base.mkdir(parents=True, exist_ok=True)
            self._lock_path.write_text(
                datetime.datetime.now().isoformat(timespec="seconds"),
                encoding="utf-8")
        except Exception:
            pass

    def release_lock(self) -> None:
        try:
            self._lock_path.unlink(missing_ok=True)
        except Exception:
            pass

    def stop(self) -> None:
        self._timer.stop()


# ═══════════════════════════════════════════════════════════════════════════
# Kurtarma Sihirbazı
# ═══════════════════════════════════════════════════════════════════════════

class RecoveryWizard(QDialog):
    """Çökme sonrası açılış sihirbazı — kullanıcı sürüm seçer.

    exec() == Accepted ise `selected_data` geri yüklenecek proje dict'idir.
    "Yoksay" sürümleri silmez; Sürüm Geçmişi panelinden erişilebilir kalır.
    """

    def __init__(self, manager: AutoSaveManager, parent=None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._versions: List[AutoSaveVersion] = manager.list_versions()
        self.selected_data: Optional[Dict[str, Any]] = None
        self.selected_path: Optional[str] = None

        self.setWindowTitle("Çökme Kurtarma")
        self.setMinimumSize(560, 360)
        self.setModal(True)

        v = QVBoxLayout(self)
        v.setSpacing(10)

        lbl = QLabel(
            "⚠ Önceki oturum düzgün kapanmadı.\n"
            "Otomatik kaydedilen sürümlerden birini geri yükleyebilirsiniz:")
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #FFB050; font-size: 13px; padding: 4px;")
        v.addWidget(lbl)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget { background: #1A1A2E; color: #E8E8E8; "
            "border: 1px solid #3A3A5C; font-size: 12px; }"
            "QListWidget::item { padding: 6px; }"
            "QListWidget::item:selected { background: #2A3A6A; }")
        for ver in self._versions:
            item = QListWidgetItem(ver.label)
            item.setData(Qt.UserRole, ver.path)
            self._list.addItem(item)
        if self._versions:
            self._list.setCurrentRow(0)   # en yeni sürüm varsayılan
        self._list.itemDoubleClicked.connect(lambda _it: self._on_restore())
        v.addWidget(self._list, stretch=1)

        h = QHBoxLayout()
        h.addStretch()
        btn_skip = QPushButton("Yoksay — Yeni Oturum")
        btn_skip.setStyleSheet(
            "QPushButton { background: #252540; color: #C0C0E0; "
            "padding: 8px 14px; border: 1px solid #3A3A5C; "
            "border-radius: 3px; }")
        btn_skip.clicked.connect(self.reject)
        h.addWidget(btn_skip)

        self._btn_restore = QPushButton("Seçili Sürümü Geri Yükle")
        self._btn_restore.setStyleSheet(
            "QPushButton { background: #1A6B3C; color: white; "
            "padding: 8px 14px; border: none; border-radius: 3px; "
            "font-weight: bold; }"
            "QPushButton:hover { background: #2A8B4C; }")
        self._btn_restore.setEnabled(bool(self._versions))
        self._btn_restore.clicked.connect(self._on_restore)
        h.addWidget(self._btn_restore)
        v.addLayout(h)

    def _on_restore(self) -> None:
        item = self._list.currentItem()
        if item is None:
            self.reject()
            return
        path = item.data(Qt.UserRole)
        data = self._manager.load_version(path)
        if data is None:
            self.reject()
            return
        self.selected_data = data
        self.selected_path = path
        self.accept()


__all__ = ["AutoSaveVersion", "AutoSaveManager", "RecoveryWizard"]
