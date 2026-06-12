"""
validation_d2/test_undo_redo.py — Faz 25 Sprint 2 Undo/Redo Doğrulama
======================================================================
Merkezi QUndoStack altyapısının kabul testleri:

  1. Add undo/redo            (KatmanDizilim + Entegre)
  2. Delete undo/redo         (KatmanDizilim + Entegre)
  3. Edit undo/redo           (hücre düzenleme, her iki panel)
  4. Mandrel undo/redo        (ChangeMandrelCommand)
  5. Makine ayarı undo/redo   (ChangeMachineSettingsCommand)
  6. mergeWith davranışı      (55→56→57→58 = tek Undo; eski değere dönüş
                               = komut obsolete)
  7. Çok satırlı silme        (tümünü temizle = tek Undo geri getirir)
  8. Satır sıralama           (MoveLayerCommand)
  9. Toplu yapıştırma         (ReplaceStackCommand)
 10. Stack limit              (setUndoLimit(100))
 11. Dirty state              (komut → kirli; kayıt → temiz + setClean)
 12. v2.0 proje uyumluluğu    (undo/redo sonrası kayıt/yükleme roundtrip)

Çalıştırma:
  QT_QPA_PLATFORM=offscreen python validation_d2/test_undo_redo.py
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

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QUndoStack

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


# Teardown sırasında GC edilen QUndoStack'lere bağlı sinyaller
# "already deleted" hatası vermesin diye yaşam süresini test sonuna sabitle
_KEEP_ALIVE = []


def _make_stack() -> QUndoStack:
    s = QUndoStack()
    s.setUndoLimit(100)
    _KEEP_ALIVE.append(s)
    return s


def _patch_msgbox_yes(monkey_target=QMessageBox):
    """QMessageBox.question'ı her zaman Yes dönecek şekilde yamala."""
    original = monkey_target.question
    monkey_target.question = staticmethod(
        lambda *a, **k: QMessageBox.Yes)
    return original


# ════════════════════════════════════════════════════════════════════════════
# 1-3 + 7-9. KatmanDizilimPaneli komutları
# ════════════════════════════════════════════════════════════════════════════

def test_katman_panel_commands():
    print("\n--- KatmanDizilimPaneli: add/delete/edit/move/bulk undo-redo ---")
    from app.panels.katman_dizilim_paneli import (
        KatmanDizilimPaneli, COL_ALPHA,
    )
    from app.undo_commands import ReplaceStackCommand

    stack = _make_stack()
    p = KatmanDizilimPaneli()
    p.set_undo_stack(stack)
    check("backend hazır", p._backend_ok)

    orig_q = _patch_msgbox_yes()
    try:
        # ── 1. Add (denge çifti = tek komut) ────────────────────────────────
        p._on_add_helical()      # Yes → ±45 çifti, tek komut
        check("denge çifti eklendi (2 katman)", len(p._stack) == 2)
        check("tek komut push edildi", stack.count() == 1)

        stack.undo()
        check("add undo → 0 katman", len(p._stack) == 0)
        stack.redo()
        check("add redo → 2 katman", len(p._stack) == 2)
        check("redo sonrası α işaretleri ±45",
              abs(p._stack[0].alpha_deg - 45.0) < 1e-9
              and abs(p._stack[1].alpha_deg + 45.0) < 1e-9)

        p._on_add_hoop()
        check("hoop eklendi → 3 katman", len(p._stack) == 3)

        # ── 2. Delete ───────────────────────────────────────────────────────
        p._table.selectRow(1)
        p._on_delete()           # Yes patch'li
        check("satır 1 silindi → 2 katman", len(p._stack) == 2)
        check("kalan: +45 ve hoop",
              abs(p._stack[0].alpha_deg - 45.0) < 1e-9
              and p._stack[1].type.value == "hoop")
        stack.undo()
        check("delete undo → 3 katman, −45 yerine döndü",
              len(p._stack) == 3
              and abs(p._stack[1].alpha_deg + 45.0) < 1e-9)
        stack.redo()
        check("delete redo → 2 katman", len(p._stack) == 2)

        # ── 3. Edit (tablo hücresi) ─────────────────────────────────────────
        it = p._table.item(0, COL_ALPHA)
        it.setText("60.0")       # itemChanged → EditLayerCommand
        check("edit uygulandı (α=60)",
              abs(p._stack[0].alpha_deg - 60.0) < 1e-9)
        stack.undo()
        check("edit undo (α=45)",
              abs(p._stack[0].alpha_deg - 45.0) < 1e-9)
        stack.redo()
        check("edit redo (α=60)",
              abs(p._stack[0].alpha_deg - 60.0) < 1e-9)

        # ── 8. Satır sıralama (move) ────────────────────────────────────────
        p._table.selectRow(0)
        p._on_move_down()
        check("move: helisel aşağı indi",
              p._stack[0].type.value == "hoop"
              and abs(p._stack[1].alpha_deg - 60.0) < 1e-9)
        stack.undo()
        check("move undo: sıra geri geldi",
              abs(p._stack[0].alpha_deg - 60.0) < 1e-9
              and p._stack[1].type.value == "hoop")
        stack.redo()
        check("move redo", p._stack[0].type.value == "hoop")

        # ── 7. Çok satırlı silme (tümünü temizle = tek Undo) ────────────────
        n_before = len(p._stack)
        snapshot = p._stack.to_dict()
        p._on_clear_all()        # Yes patch'li → ReplaceStackCommand
        check("tümü temizlendi", len(p._stack) == 0)
        stack.undo()
        check("tek undo TÜM katmanları geri getirdi",
              len(p._stack) == n_before,
              f"bulundu: {len(p._stack)}")
        check("geri gelen yığın içerik olarak aynı",
              p._stack.to_dict()["layers"] == snapshot["layers"])

        # ── 9. Toplu yapıştırma (ReplaceStackCommand) ───────────────────────
        from backend.core.manual_layer_sequencer import LayerStack
        paste = LayerStack()
        for a in (30.0, -30.0, 70.0, -70.0):
            paste.add_layer(paste.make_helical(alpha_deg=a))
        old_snap = p._stack.to_dict()
        stack.push(ReplaceStackCommand(
            p, old_snap, paste.to_dict(), text="toplu yapıştır"))
        check("yapıştırma: 4 katman", len(p._stack) == 4)
        stack.undo()
        check("yapıştırma undo: eski yığın döndü",
              len(p._stack) == n_before)
        stack.redo()
        check("yapıştırma redo: 4 katman", len(p._stack) == 4)
    finally:
        QMessageBox.question = orig_q

    p.deleteLater()


# ════════════════════════════════════════════════════════════════════════════
# Entegre panel: satır + hücre + parametre komutları
# ════════════════════════════════════════════════════════════════════════════

def test_entegre_panel_commands():
    print("\n--- EntegreTasarimPaneli: add/delete/edit/param undo-redo ---")
    from app.panels.entegre_tasarim_paneli import (
        EntegreTasarimPaneli, COL_ALPHA, COL_DEL,
    )

    stack = _make_stack()
    p = EntegreTasarimPaneli()
    p.set_undo_stack(stack)
    tbl = p._layer_table

    # ── Add ──────────────────────────────────────────────────────────────
    p._on_add_helical()
    p._on_add_hoop()
    check("2 satır eklendi", tbl.rowCount() == 2)
    stack.undo()
    check("add undo → 1 satır", tbl.rowCount() == 1)
    stack.redo()
    check("add redo → 2 satır", tbl.rowCount() == 2)

    # ── Delete (buton üzerinden) ─────────────────────────────────────────
    first_alpha = tbl.item(0, COL_ALPHA).text()
    tbl.cellWidget(0, COL_DEL).click()
    check("satır 0 silindi", tbl.rowCount() == 1)
    stack.undo()
    check("delete undo: satır + içerik geri geldi",
          tbl.rowCount() == 2
          and tbl.item(0, COL_ALPHA).text() == first_alpha)
    stack.redo()
    check("delete redo", tbl.rowCount() == 1)
    stack.undo()  # 2 satıra dön

    # ── Edit (hücre) ─────────────────────────────────────────────────────
    it = tbl.item(0, COL_ALPHA)
    old_txt = it.text()
    it.setText("33.0")
    check("hücre düzenlendi", tbl.item(0, COL_ALPHA).text() == "33.0")
    stack.undo()
    check("hücre undo", tbl.item(0, COL_ALPHA).text() == old_txt)
    stack.redo()
    check("hücre redo", tbl.item(0, COL_ALPHA).text() == "33.0")

    # ── 4. Mandrel undo/redo ─────────────────────────────────────────────
    idx0 = stack.index()
    p._sp_diam.setValue(150.0)
    check("mandrel çap komutu push edildi", stack.index() == idx0 + 1)
    check("çap=150", abs(p._sp_diam.value() - 150.0) < 1e-9)
    stack.undo()
    check("mandrel undo (çap=100)",
          abs(p._sp_diam.value() - 100.0) < 1e-9)
    stack.redo()
    check("mandrel redo (çap=150)",
          abs(p._sp_diam.value() - 150.0) < 1e-9)

    # Mandrel tipi değişimi (combo)
    p._cb_type.setCurrentText("Konik")
    check("tip=Konik", p._cb_type.currentText() == "Konik")
    check("konik alanı görünür kılındı", not p._sp_cone.isHidden())
    stack.undo()
    check("tip undo → Silindir", p._cb_type.currentText() == "Silindir")
    stack.redo()
    check("tip redo → Konik", p._cb_type.currentText() == "Konik")
    stack.undo()

    # ── 5. Makine ayarı undo/redo ────────────────────────────────────────
    p._sp_feed.setValue(120.0)
    check("ilerleme=120", abs(p._sp_feed.value() - 120.0) < 1e-9)
    stack.undo()
    check("makine ayarı undo (80)", abs(p._sp_feed.value() - 80.0) < 1e-9)
    stack.redo()
    check("makine ayarı redo (120)", abs(p._sp_feed.value() - 120.0) < 1e-9)

    # ── 6. mergeWith: 55→56→57→58 = TEK undo ────────────────────────────
    n_before = stack.count()
    p._sp_alpha.setValue(56.0)
    p._sp_alpha.setValue(57.0)
    p._sp_alpha.setValue(58.0)
    check("ardışık düzenleme tek komuta birleşti",
          stack.count() == n_before + 1,
          f"count {stack.count()} != {n_before + 1}")
    stack.undo()
    check("tek undo tüm düzenlemeyi geri aldı (α=55)",
          abs(p._sp_alpha.value() - 55.0) < 1e-9)
    stack.redo()
    check("redo → α=58", abs(p._sp_alpha.value() - 58.0) < 1e-9)

    # mergeWith obsolete: eski değere dönüş komutu düşürür
    n2 = stack.count()
    idx2 = stack.index()
    p._sp_rpm.setValue(70.0)
    p._sp_rpm.setValue(60.0)   # eski değere dönüş → obsolete
    check("eski değere dönüş komutu stack'ten düşürdü",
          stack.index() == idx2 and stack.count() == n2,
          f"index {stack.index()}/{idx2}, count {stack.count()}/{n2}")

    # Hücre mergeWith
    n3 = stack.count()
    it = tbl.item(0, COL_ALPHA)
    it.setText("40.0")
    it.setText("41.0")
    it.setText("42.0")
    check("hücre ardışık düzenleme tek komut",
          stack.count() == n3 + 1)
    stack.undo()
    check("hücre tek undo → 33.0", it.text() == "33.0")

    p.deleteLater()


# ════════════════════════════════════════════════════════════════════════════
# 10. Stack limit
# ════════════════════════════════════════════════════════════════════════════

def test_stack_limit():
    print("\n--- Stack limit (setUndoLimit(100)) ---")
    from app.panels.entegre_tasarim_paneli import EntegreTasarimPaneli
    from app.undo_commands import AddRowCommand

    stack = _make_stack()
    check("undoLimit == 100", stack.undoLimit() == 100)

    p = EntegreTasarimPaneli()
    p.set_undo_stack(stack)

    # 120 ayrı add komutu (merge edilmez — AddRowCommand id() = -1)
    for i in range(120):
        stack.push(AddRowCommand(p, {
            "tip": "Sarmal", "alpha_deg": float(i), "fitil_mm": 6.0,
            "n_kat": 1}))
    check("120 satır eklendi", p._layer_table.rowCount() == 120)
    check("stack 100 komutta sınırlandı", stack.count() == 100,
          f"count={stack.count()}")

    while stack.canUndo():
        stack.undo()
    check("dibe kadar undo → ilk 20 satır kalır (limit dışı)",
          p._layer_table.rowCount() == 20,
          f"kalan={p._layer_table.rowCount()}")
    p.deleteLater()


# ════════════════════════════════════════════════════════════════════════════
# 11. Dirty state + save sonrası clean state
# ════════════════════════════════════════════════════════════════════════════

def test_dirty_clean_sync():
    print("\n--- Dirty/clean senkronu ---")
    from app.panels.proje_yoneticisi import ProjeYoneticisiPanel
    from app.panels.entegre_tasarim_paneli import EntegreTasarimPaneli

    stack = _make_stack()
    proje_p = ProjeYoneticisiPanel()
    p = EntegreTasarimPaneli()
    p.set_undo_stack(stack)

    # MainWindow kablolamasının aynısı
    def on_index_changed(_i):
        if not stack.isClean():
            proje_p.mark_dirty_external()
    stack.indexChanged.connect(on_index_changed)
    proje_p.degisiklikDurumu.connect(
        lambda dirty: (not dirty) and stack.setClean())

    proje_p._dirty_grace_until = 0.0   # bağışıklık penceresini kapat
    check("başlangıç: temiz", not proje_p.has_unsaved_changes()
          and stack.isClean())

    p._on_add_helical()
    check("komut push → proje kirli", proje_p.has_unsaved_changes())
    check("stack temiz değil", not stack.isClean())

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "s2.fwp")
        ok = proje_p._save_to_file(path)
        check("kayıt başarılı", ok)
        check("kayıt → proje temiz", not proje_p.has_unsaved_changes())
        check("kayıt → undo stack clean noktası senkron", stack.isClean())

        # Kayıttan sonra yeni komut → tekrar kirli
        proje_p._dirty_grace_until = 0.0
        p._on_add_hoop()
        check("kayıt sonrası komut → tekrar kirli",
              proje_p.has_unsaved_changes() and not stack.isClean())

        # Undo ile temiz noktaya dönüş — bayrak SİLİNMEZ (muhafazakâr)
        stack.undo()
        check("undo → stack yine clean noktasında", stack.isClean())
        check("kirli bayrak muhafazakâr olarak korunur",
              proje_p.has_unsaved_changes())

    stack.indexChanged.disconnect(on_index_changed)
    proje_p.deleteLater()
    p.deleteLater()
    _app.processEvents()


# ════════════════════════════════════════════════════════════════════════════
# 12. v2.0 proje uyumluluğu (undo/redo sonrası roundtrip)
# ════════════════════════════════════════════════════════════════════════════

def test_v2_project_compat():
    print("\n--- v2.0 proje uyumluluğu (undo/redo sonrası roundtrip) ---")
    from app.panels.proje_yoneticisi import ProjeYoneticisiPanel
    from app.panels.katman_dizilim_paneli import KatmanDizilimPaneli
    from app.panels.entegre_tasarim_paneli import EntegreTasarimPaneli
    from app.undo_commands import AddLayerCommand

    stack = _make_stack()
    proje_p  = ProjeYoneticisiPanel()
    katman_p = KatmanDizilimPaneli()
    entegre_p = EntegreTasarimPaneli()
    katman_p.set_undo_stack(stack)
    entegre_p.set_undo_stack(stack)
    proje_p.set_data_providers(
        katman_provider=katman_p.get_stack_dict,
        entegre_provider=entegre_p.get_design_state,
    )
    proje_p.projeYuklendi.connect(katman_p.apply_project)
    proje_p.projeYuklendi.connect(entegre_p.apply_project)
    proje_p.projeYuklendi.connect(lambda *_: stack.clear())

    # Komutlarla 3 katman ekle, 1'ini undo et → 2 katman kalsın
    s = katman_p._stack
    for a in (55.0, -55.0, 89.5):
        spec = (s.make_hoop() if a > 80 else s.make_helical(alpha_deg=a))
        stack.push(AddLayerCommand(katman_p, [spec.to_dict()]))
    stack.undo()   # hoop geri alındı
    check("undo sonrası 2 katman", len(katman_p._stack) == 2)

    entegre_p._on_add_helical()

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "v2.fwp")
        check("kayıt", proje_p._save_to_file(path))
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        check("v2.0 şema", data["versiyon"] == "2.0")
        check("undo edilmiş katman KAYDA GİRMEDİ (2 katman)",
              len(data["katman_yigini"]["layers"]) == 2)
        check("entegre satırı kayıtta",
              len(data["entegre_panel_katmanlar"]) == 1)

        # Yükle: stack temizlenir, katmanlar geri gelir
        katman_p._cmd_set_stack({"layers": []})
        proje_p._load_from_file(path)
        check("yükleme sonrası 2 katman", len(katman_p._stack) == 2)
        check("yükleme undo yığınını temizledi",
              stack.count() == 0 and stack.isClean())
        check("yükleme sonrası redo edilemez", not stack.canRedo())

    proje_p.deleteLater()
    katman_p.deleteLater()
    entegre_p.deleteLater()
    _app.processEvents()


# ════════════════════════════════════════════════════════════════════════════

def main() -> int:
    print("=" * 72)
    print(" FAZ 25 — SPRINT 2 UNDO/REDO VALIDATION")
    print("=" * 72)

    test_katman_panel_commands()
    test_entegre_panel_commands()
    test_stack_limit()
    test_dirty_clean_sync()
    test_v2_project_compat()

    print("\n" + "=" * 72)
    print(f" SONUÇ: {PASS} PASS / {FAIL} FAIL")
    if FAIL == 0:
        print(" ★★★ SPRINT 2 READY ★★★")
    else:
        print(" STOP — SPRINT 2 EKSİK")
    print("=" * 72)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
