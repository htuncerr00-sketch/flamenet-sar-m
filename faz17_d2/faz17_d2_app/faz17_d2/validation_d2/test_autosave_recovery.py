"""
validation_d2/test_autosave_recovery.py — Faz 25 Sprint 3 Doğrulama
====================================================================
Auto-save + Crash Recovery + Project History kabul testleri
(senaryolar SPRINT3_DESIGN.md §3 R1–R12):

  R1  Debounce yazımı            R7  CAM reçeteleri kurtarma
  R2  5 sürüm rotasyonu          R8  Makine ayarları kurtarma
  R3  Temiz projede yazım yok    R9  Kurtarma sihirbazı seçimi
  R4  Oturum kilidi / çökme      R10 v1.0 autosave migrasyonu
  R5  Katman dizilimi kurtarma   R11 Bozuk dosya toleransı
  R6  Mandrel kurtarma           R12 Geri dönüş öncesi güvenlik yedeği

Çalıştırma:
  QT_QPA_PLATFORM=offscreen python validation_d2/test_autosave_recovery.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS ✓  {name}")
    else:
        FAIL += 1
        print(f"  FAIL ✗  {name}  {detail}")


def _wait_ms(ms: int) -> None:
    """Qt event loop'unu çalıştırarak bekle (QTimer'lar işlesin)."""
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        _app.processEvents()
        time.sleep(0.005)


def _sample_project(n_katman: int = 2, adi: str = "Test Projesi") -> dict:
    return {
        "versiyon": "2.0", "proje_adi": adi,
        "mandrel": {"tip": "silindir", "cap_mm": 200.0, "uzunluk_mm": 500.0},
        "katmanlar": [],
        "katman_yigini": {"layers": [
            {"id": i, "type": "helical", "alpha_deg": 55.0 * (-1) ** i,
             "fitil_genisligi_mm": 6.0, "cakisma_pct": 5.0,
             "thickness_mm": 0.3, "feed_mm_s": 80.0, "spindle_rpm": 60.0,
             "friction_mu": 0.3, "strategy": "geodesic",
             "label": "", "notes": ""}
            for i in range(n_katman)
        ]},
        "entegre_panel_katmanlar": [],
        "entegre_panel_mandrel": {},
        "sarma_parametreleri": {},
        "makine_profili_ismi": "",
    }


# ════════════════════════════════════════════════════════════════════════════
# R1–R4 + R11: AutoSaveManager mekanikleri
# ════════════════════════════════════════════════════════════════════════════

def test_manager_mechanics():
    print("\n--- R1-R4 + R11: AutoSaveManager mekanikleri ---")
    from app.autosave_manager import AutoSaveManager

    with tempfile.TemporaryDirectory() as td:
        data = {"box": _sample_project()}
        dirty = {"v": True}
        mgr = AutoSaveManager(
            data_provider=lambda: data["box"],
            dirty_provider=lambda: dirty["v"],
            base_dir=td, max_versions=5, debounce_ms=80)

        # R1: debounce — notify → süre dolunca yazım
        mgr.notify_change()
        check("R1: sayaç başladı", mgr._timer.isActive())
        _wait_ms(250)
        versions = mgr.list_versions()
        check("R1: debounce sonrası 1 sürüm yazıldı", len(versions) == 1,
              f"bulundu: {len(versions)}")
        if versions:
            with open(versions[0].path, encoding="utf-8") as f:
                saved = json.load(f)
            check("R1: yazılan dosya geçerli v2.0 JSON",
                  saved.get("versiyon") == "2.0")
            check("R1: sürüm etiketi proje adı + katman içerir",
                  "Test Projesi" in versions[0].label
                  and "2 katman" in versions[0].label)

        # R3: temiz projede yazım olmaz
        dirty["v"] = False
        mgr.notify_change()
        _wait_ms(200)
        check("R3: temiz projede yeni sürüm yazılmadı",
              len(mgr.list_versions()) == 1)
        dirty["v"] = True

        # R2: rotasyon — 7 yazım daha → toplam 5 kalır
        paths = []
        for i in range(7):
            data["box"] = _sample_project(n_katman=i + 1)
            p = mgr.force_save()
            paths.append(p)
            time.sleep(1.05)  # dosya adı zaman damgası saniye çözünürlüklü
        versions = mgr.list_versions()
        check("R2: rotasyon 5 sürümde sınırladı", len(versions) == 5,
              f"bulundu: {len(versions)}")
        check("R2: en yeni sürüm korundu (7 katman)",
              versions and versions[0].n_katman == 7)
        check("R2: zaman sırası en yeniden eskiye",
              all(versions[i].timestamp >= versions[i + 1].timestamp
                  for i in range(len(versions) - 1)))

        # R11: bozuk dosya + yarım .tmp sürüm listesine girmez
        bad = os.path.join(td, "autosave", "bozuk_20260612_120000.fwp.bak")
        with open(bad, "w") as f:
            f.write("{ bozuk json")
        tmp = os.path.join(td, "autosave", ".yarim_20260612_120001.tmp")
        with open(tmp, "w") as f:
            f.write('{"yarim": true')
        check("R11: bozuk/yarım dosyalar listede yok",
              len(mgr.list_versions()) == 5)

        # R4: oturum kilidi yaşam döngüsü
        check("R4: başlangıçta stale kilit yok", not mgr.has_stale_lock())
        mgr.acquire_lock()
        check("R4: kilit alındı", mgr.has_stale_lock())
        mgr.release_lock()
        check("R4: temiz kapanış kilidi sildi", not mgr.has_stale_lock())

        # R4: çökme simülasyonu — kilit bırakılır, YENİ oturum tespit eder
        mgr.acquire_lock()   # ... ve release edilmeden "çöker"
        mgr2 = AutoSaveManager(
            data_provider=lambda: data["box"],
            dirty_provider=lambda: True, base_dir=td)
        check("R4: yeni oturum çökmeyi tespit etti (stale kilit)",
              mgr2.has_stale_lock())
        check("R4: çökme sonrası sürümler erişilebilir",
              len(mgr2.list_versions()) == 5)
        mgr2.release_lock()


# ════════════════════════════════════════════════════════════════════════════
# R5–R8: Çökme sonrası eksiksiz kurtarma (panel düzeyi)
# ════════════════════════════════════════════════════════════════════════════

def test_full_recovery():
    print("\n--- R5-R8: Çökme sonrası eksiksiz kurtarma ---")
    from app.autosave_manager import AutoSaveManager
    from app.panels.proje_yoneticisi import ProjeYoneticisiPanel
    from app.panels.katman_dizilim_paneli import KatmanDizilimPaneli
    from app.panels.entegre_tasarim_paneli import EntegreTasarimPaneli

    with tempfile.TemporaryDirectory() as td:
        # ── "Çöken" oturum: tam tasarım durumu kur, autosave al ─────────────
        proje_p  = ProjeYoneticisiPanel()
        katman_p = KatmanDizilimPaneli()
        entegre_p = EntegreTasarimPaneli()
        proje_p.set_data_providers(
            katman_provider=katman_p.get_stack_dict,
            entegre_provider=entegre_p.get_design_state)

        s = katman_p._stack
        s.add_layer(s.make_helical(alpha_deg=63.0, fitil_genisligi_mm=7.5))
        s.add_layer(s.make_helical(alpha_deg=-63.0, fitil_genisligi_mm=7.5))
        s.add_layer(s.make_hoop())
        katman_p._refresh_table()

        entegre_p._cb_type.setCurrentText("Kubbeli Silindir")
        entegre_p._sp_diam.setValue(180.0)
        entegre_p._sp_len.setValue(420.0)
        entegre_p._sp_dome.setValue(45.0)
        entegre_p._sp_feed.setValue(95.0)
        entegre_p._sp_rpm.setValue(72.0)
        entegre_p._sp_friction.setValue(0.22)
        entegre_p._sp_overlap.setValue(8.0)
        entegre_p._sp_tow.setValue(5.5)
        entegre_p._add_layer_row("Sarmal", 63.0, 5.5, 2)
        entegre_p._add_layer_row("Hoop", 89.5, 5.5, 1)

        mgr = AutoSaveManager(
            data_provider=proje_p._read_form,
            dirty_provider=lambda: True, base_dir=td)
        mgr.acquire_lock()
        saved_path = mgr.force_save()
        check("autosave alındı", saved_path is not None)
        # Oturum burada "çöker" — release_lock ÇAĞRILMAZ
        proje_p.deleteLater(); katman_p.deleteLater(); entegre_p.deleteLater()
        _app.processEvents()

        # ── Yeni oturum: kurtar ──────────────────────────────────────────────
        proje2  = ProjeYoneticisiPanel()
        katman2 = KatmanDizilimPaneli()
        entegre2 = EntegreTasarimPaneli()
        proje2.projeYuklendi.connect(katman2.apply_project)
        proje2.projeYuklendi.connect(entegre2.apply_project)

        mgr2 = AutoSaveManager(
            data_provider=proje2._read_form,
            dirty_provider=lambda: True, base_dir=td)
        check("çökme tespit edildi", mgr2.has_stale_lock())
        versions = mgr2.list_versions()
        check("kurtarılacak sürüm listelendi", len(versions) >= 1)

        data = mgr2.load_version(versions[0].path)
        proje2.load_project_dict(data)

        # R5: katman dizilimi eksiksiz
        check("R5: 3 katman geri geldi", len(katman2._stack) == 3,
              f"bulundu: {len(katman2._stack)}")
        if len(katman2._stack) == 3:
            check("R5: α=+63 / −63 / hoop sırası",
                  abs(katman2._stack[0].alpha_deg - 63.0) < 1e-9
                  and abs(katman2._stack[1].alpha_deg + 63.0) < 1e-9
                  and katman2._stack[2].type.value == "hoop")
            check("R5: fitil genişliği korundu (7.5)",
                  abs(katman2._stack[0].fitil_genisligi_mm - 7.5) < 1e-9)

        # R6: mandrel parametreleri eksiksiz
        check("R6: mandrel tipi", entegre2._cb_type.currentText()
              == "Kubbeli Silindir")
        check("R6: çap/uzunluk/kubbe",
              abs(entegre2._sp_diam.value() - 180.0) < 1e-9
              and abs(entegre2._sp_len.value() - 420.0) < 1e-9
              and abs(entegre2._sp_dome.value() - 45.0) < 1e-9)

        # R7: CAM reçetesi (entegre katman tablosu) eksiksiz
        rows = entegre2.get_layer_rows()
        check("R7: 2 CAM katman satırı geri geldi", len(rows) == 2,
              f"bulundu: {len(rows)}")
        if len(rows) == 2:
            check("R7: satır içerikleri (63°×2 + hoop)",
                  abs(rows[0]["alpha_deg"] - 63.0) < 1e-6
                  and rows[0]["n_kat"] == 2
                  and rows[1]["tip"] == "Hoop")

        # R8: makine ayarları eksiksiz
        check("R8: feed/rpm/μ/çakışma/fitil",
              abs(entegre2._sp_feed.value() - 95.0) < 1e-9
              and abs(entegre2._sp_rpm.value() - 72.0) < 1e-9
              and abs(entegre2._sp_friction.value() - 0.22) < 1e-9
              and abs(entegre2._sp_overlap.value() - 8.0) < 1e-9
              and abs(entegre2._sp_tow.value() - 5.5) < 1e-9)

        mgr2.release_lock()
        proje2.deleteLater(); katman2.deleteLater(); entegre2.deleteLater()
        _app.processEvents()


# ════════════════════════════════════════════════════════════════════════════
# R9: Kurtarma sihirbazı + R12: geri dönüş güvenlik yedeği
# ════════════════════════════════════════════════════════════════════════════

def test_wizard_and_history():
    print("\n--- R9 + R12: Sihirbaz + Sürüm Geçmişi ---")
    from app.autosave_manager import AutoSaveManager, RecoveryWizard
    from app.panels.proje_yoneticisi import ProjeYoneticisiPanel
    from PySide6.QtCore import Qt

    with tempfile.TemporaryDirectory() as td:
        box = {"d": _sample_project(n_katman=1, adi="Surum A")}
        mgr = AutoSaveManager(
            data_provider=lambda: box["d"],
            dirty_provider=lambda: True, base_dir=td)
        mgr.force_save()
        time.sleep(1.05)
        box["d"] = _sample_project(n_katman=4, adi="Surum B")
        mgr.force_save()

        # R9: sihirbaz sürümleri listeler, en yenisi seçili
        wiz = RecoveryWizard(mgr)
        check("R9: sihirbaz 2 sürüm listeledi", wiz._list.count() == 2)
        check("R9: en yeni sürüm varsayılan seçili",
              wiz._list.currentRow() == 0
              and "Surum B" in wiz._list.currentItem().text())
        wiz._on_restore()
        check("R9: seçim geri yükleme verisini doldurdu",
              wiz.selected_data is not None
              and wiz.selected_data["proje_adi"] == "Surum B"
              and len(wiz.selected_data["katman_yigini"]["layers"]) == 4)

        # Eski sürüm seçimi de çalışır
        wiz2 = RecoveryWizard(mgr)
        wiz2._list.setCurrentRow(1)
        wiz2._on_restore()
        check("R9: kullanıcı ESKİ sürümü de seçebilir",
              wiz2.selected_data is not None
              and wiz2.selected_data["proje_adi"] == "Surum A")

        # R12: Sürüm Geçmişi paneli + geri dönüş öncesi güvenlik yedeği
        p = ProjeYoneticisiPanel()
        p.set_autosave_manager(mgr)
        check("R12: geçmiş paneli sürümleri listeledi",
              p._history_list.count() == 2)

        from PySide6.QtWidgets import QMessageBox
        orig_q = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        try:
            n_before = len(mgr.list_versions())
            p._history_list.setCurrentRow(1)   # eski sürüm (Surum A)
            time.sleep(1.05)                   # yeni yedek farklı ts alsın
            p._on_revert_to_version()
            check("R12: geri dönüş uygulandı (Surum A)",
                  p._proje.get("proje_adi") == "Surum A")
            check("R12: dönüş öncesi güvenlik yedeği alındı",
                  len(mgr.list_versions()) == n_before + 1)
            check("R12: geri dönüş projeyi kirli işaretledi",
                  p.has_unsaved_changes())
        finally:
            QMessageBox.question = orig_q
        p.deleteLater()
        _app.processEvents()


# ════════════════════════════════════════════════════════════════════════════
# R10: v1.0 autosave dosyası migrasyonla açılır
# ════════════════════════════════════════════════════════════════════════════

def test_v1_autosave_migration():
    print("\n--- R10: v1.0 autosave migrasyonu ---")
    from app.autosave_manager import AutoSaveManager
    from app.panels.proje_yoneticisi import ProjeYoneticisiPanel
    from app.panels.katman_dizilim_paneli import KatmanDizilimPaneli

    with tempfile.TemporaryDirectory() as td:
        adir = os.path.join(td, "autosave")
        os.makedirs(adir)
        v1 = {
            "versiyon": "1.0", "proje_adi": "Eski V1",
            "mandrel": {"tip": "silindir", "cap_mm": 150.0,
                        "uzunluk_mm": 350.0},
            "katmanlar": [{"tip": "sarmal", "aci_deg": 48.0,
                           "cift_sayisi": 1, "ply_kalinlik_mm": 0.3}],
            "basinc_MPa": 9.0,
        }
        with open(os.path.join(adir, "eski_20260610_090000.fwp.bak"),
                  "w", encoding="utf-8") as f:
            json.dump(v1, f)

        mgr = AutoSaveManager(
            data_provider=dict, dirty_provider=lambda: False, base_dir=td)
        versions = mgr.list_versions()
        check("R10: v1.0 dosyası listelendi (katmanlar'dan sayım)",
              len(versions) == 1 and versions[0].n_katman == 1)

        p = ProjeYoneticisiPanel()
        k = KatmanDizilimPaneli()
        p.projeYuklendi.connect(k.apply_project)
        data = mgr.load_version(versions[0].path)
        p.load_project_dict(data)
        check("R10: migrasyon uygulandı (bellekte v2.0)",
              p._proje["versiyon"] == "2.0")
        check("R10: v1.0 katmanı geri yüklendi",
              len(k._stack) == 1
              and abs(k._stack[0].alpha_deg - 48.0) < 1e-9)
        p.deleteLater(); k.deleteLater()
        _app.processEvents()


# ════════════════════════════════════════════════════════════════════════════

def main() -> int:
    print("=" * 72)
    print(" FAZ 25 — SPRINT 3 AUTO-SAVE + CRASH RECOVERY VALIDATION")
    print("=" * 72)

    test_manager_mechanics()
    test_full_recovery()
    test_wizard_and_history()
    test_v1_autosave_migration()

    print("\n" + "=" * 72)
    print(f" SONUÇ: {PASS} PASS / {FAIL} FAIL")
    if FAIL == 0:
        print(" ★★★ SPRINT 3 READY ★★★")
    else:
        print(" STOP — SPRINT 3 EKSİK")
    print("=" * 72)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
