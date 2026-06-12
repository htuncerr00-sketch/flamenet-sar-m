"""
validation_d2/sprint1_workflow_validation.py — Faz 25 Sprint 1 Doğrulama
=========================================================================
Proje iş akışı sertleştirme (Sprint 1) kabul testleri:

  1. _del_row düzeltmesi      — widget kimliğiyle doğru satır silinir
  2. Schema v2.0              — _empty_project tüm v2.0 alanlarını içerir
  3. _migrate_project         — v1.0 dosyası kayıpsız v2.0'a yükselir
  4. Kayıt/yükleme roundtrip  — katman dizilimi eksiksiz kaydedilir + geri yüklenir
  5. Kirli bayrak             — değişiklik → dirty, kayıt → temiz
  6. LayerSpec.from_dict      — bilinmeyen anahtarlara ('layer_type') dayanıklı

Çalıştırma:
  QT_QPA_PLATFORM=offscreen python validation_d2/sprint1_workflow_validation.py
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

from PySide6.QtWidgets import QApplication, QPushButton

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


# ════════════════════════════════════════════════════════════════════════════
# 1. _del_row düzeltmesi — widget kimliği ile doğru satır silinir
# ════════════════════════════════════════════════════════════════════════════

def test_del_row():
    print("\n--- 1. _del_row widget kimliği düzeltmesi ---")
    from app.panels.entegre_tasarim_paneli import (
        EntegreTasarimPaneli, COL_ALPHA, COL_DEL,
    )
    p = EntegreTasarimPaneli()

    # 3 satır ekle: α = 10, 20, 30
    p._add_layer_row("Sarmal", 10.0, 6.0, 1)
    p._add_layer_row("Sarmal", 20.0, 6.0, 1)
    p._add_layer_row("Sarmal", 30.0, 6.0, 1)
    tbl = p._layer_table
    check("3 satır eklendi", tbl.rowCount() == 3)

    # Ortadaki satırın (α=20) sil butonuna tıkla
    btn_mid = tbl.cellWidget(1, COL_DEL)
    btn_mid.click()
    remaining = [tbl.item(r, COL_ALPHA).text() for r in range(tbl.rowCount())]
    check("orta satır silindi (α=20 gitti)",
          remaining == ["10.0", "30.0"], f"kalan: {remaining}")

    # Eski hata senaryosu: üstteki satır silindikten sonra alttaki butonun
    # lambda'sındaki indeks bayatlamıştı. Şimdi α=30'un butonu hâlâ doğru
    # satırı (kendi satırını) silmeli.
    btn_last = tbl.cellWidget(1, COL_DEL)   # α=30 artık satır 1'de
    btn_last.click()
    remaining = [tbl.item(r, COL_ALPHA).text() for r in range(tbl.rowCount())]
    check("bayat indeks yok — α=30 doğru silindi",
          remaining == ["10.0"], f"kalan: {remaining}")

    btn_first = tbl.cellWidget(0, COL_DEL)
    btn_first.click()
    check("son satır da silindi", tbl.rowCount() == 0)
    p.deleteLater()


# ════════════════════════════════════════════════════════════════════════════
# 2 + 3. Schema v2.0 ve _migrate_project
# ════════════════════════════════════════════════════════════════════════════

def test_schema_v2_and_migration():
    print("\n--- 2. Schema v2.0 + 3. _migrate_project ---")
    from app.panels.proje_yoneticisi import (
        _empty_project, _migrate_project, _SCHEMA_VERSION,
    )

    check("şema sürümü 2.0", _SCHEMA_VERSION == "2.0")

    ep = _empty_project()
    for key in ("katman_yigini", "entegre_panel_katmanlar",
                "entegre_panel_mandrel", "sarma_parametreleri",
                "makine_profili_ismi"):
        check(f"_empty_project '{key}' içeriyor", key in ep)

    # v1.0 dosyası — v2.0 alanları YOK
    v1 = {
        "versiyon": "1.0",
        "proje_adi": "Eski Proje",
        "musteri": "Müşteri A",
        "malzeme": "carbon_t700_epoxy_pv",
        "mandrel": {"tip": "silindir", "cap_mm": 150.0, "uzunluk_mm": 400.0},
        "katmanlar": [{"tip": "sarmal", "aci_deg": 55.0, "cift_sayisi": 2,
                       "ply_kalinlik_mm": 0.3}],
        "basinc_MPa": 12.5,
    }
    m = _migrate_project(dict(v1))
    check("v1.0 → versiyon 2.0", m["versiyon"] == "2.0")
    check("v1.0 alanları korundu (proje_adi)", m["proje_adi"] == "Eski Proje")
    check("v1.0 alanları korundu (mandrel)", m["mandrel"]["cap_mm"] == 150.0)
    check("v1.0 alanları korundu (katmanlar)",
          len(m["katmanlar"]) == 1 and m["katmanlar"][0]["aci_deg"] == 55.0)
    check("v1.0 alanları korundu (basinc)", m["basinc_MPa"] == 12.5)
    check("katman_yigini varsayılanı eklendi",
          m["katman_yigini"] == {"layers": []})
    check("entegre_panel_katmanlar varsayılanı eklendi",
          m["entegre_panel_katmanlar"] == [])
    check("makine_profili_ismi varsayılanı eklendi",
          m["makine_profili_ismi"] == "")

    # Bozuk alan tipleri düzeltilir
    broken = {"versiyon": "1.0", "katman_yigini": "bozuk",
              "entegre_panel_katmanlar": {"yanlis": "tip"}}
    mb = _migrate_project(broken)
    check("bozuk katman_yigini düzeltildi",
          isinstance(mb["katman_yigini"], dict)
          and mb["katman_yigini"]["layers"] == [])
    check("bozuk entegre_panel_katmanlar düzeltildi",
          mb["entegre_panel_katmanlar"] == [])

    # Geçersiz girdi reddedilir
    try:
        _migrate_project("not a dict")
        check("geçersiz girdi ValueError fırlatır", False)
    except ValueError:
        check("geçersiz girdi ValueError fırlatır", True)


# ════════════════════════════════════════════════════════════════════════════
# 4. Kayıt/yükleme roundtrip — katman dizilimi eksiksiz
# ════════════════════════════════════════════════════════════════════════════

def test_save_load_roundtrip():
    print("\n--- 4. Kayıt/yükleme roundtrip (katman dizilimi dahil) ---")
    from app.panels.proje_yoneticisi import ProjeYoneticisiPanel
    from app.panels.katman_dizilim_paneli import KatmanDizilimPaneli
    from app.panels.entegre_tasarim_paneli import EntegreTasarimPaneli

    proje_p  = ProjeYoneticisiPanel()
    katman_p = KatmanDizilimPaneli()
    entegre_p = EntegreTasarimPaneli()

    proje_p.set_data_providers(
        katman_provider=katman_p.get_stack_dict,
        entegre_provider=entegre_p.get_design_state,
    )
    proje_p.projeYuklendi.connect(katman_p.apply_project)
    proje_p.projeYuklendi.connect(entegre_p.apply_project)

    # Manuel Dizilim paneline 3 katman ekle (backend üzerinden)
    check("KatmanDizilim backend hazır", katman_p._backend_ok)
    if katman_p._backend_ok:
        s = katman_p._stack
        s.add_layer(s.make_helical(alpha_deg=55.0, fitil_genisligi_mm=6.35))
        s.add_layer(s.make_helical(alpha_deg=-55.0, fitil_genisligi_mm=6.35))
        s.add_layer(s.make_hoop())
        katman_p._refresh_table()

    # Entegre panele 2 katman + mandrel ayarı
    entegre_p._sp_diam.setValue(120.0)
    entegre_p._sp_len.setValue(350.0)
    entegre_p._sp_friction.setValue(0.15)
    entegre_p._add_layer_row("Sarmal", 45.0, 6.0, 2)
    entegre_p._add_layer_row("Hoop", 89.5, 6.0, 1)

    # Kaydet
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "roundtrip.fwp")
        ok = proje_p._save_to_file(path)
        check("proje kaydedildi", ok)

        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        check("dosya versiyonu 2.0", data.get("versiyon") == "2.0")
        yigin_layers = data.get("katman_yigini", {}).get("layers", [])
        check("katman_yigini 3 katman içeriyor",
              len(yigin_layers) == 3, f"bulundu: {len(yigin_layers)}")
        if len(yigin_layers) == 3:
            check("katman 1 α=+55", abs(yigin_layers[0]["alpha_deg"] - 55.0) < 1e-6)
            check("katman 2 α=-55", abs(yigin_layers[1]["alpha_deg"] + 55.0) < 1e-6)
            check("katman 3 hoop", yigin_layers[2]["type"] == "hoop")
        ent_rows = data.get("entegre_panel_katmanlar", [])
        check("entegre_panel_katmanlar 2 satır", len(ent_rows) == 2,
              f"bulundu: {len(ent_rows)}")
        check("entegre mandrel çapı kaydedildi",
              abs(data.get("entegre_panel_mandrel", {}).get("cap_mm", 0)
                  - 120.0) < 1e-6)
        check("sarma sürtünme katsayısı kaydedildi",
              abs(data.get("sarma_parametreleri", {}).get("surtunme_mu", -1)
                  - 0.15) < 1e-6)

        # Panelleri sıfırla, geri yükle
        katman_p._stack.clear()
        katman_p._refresh_table()
        entegre_p.set_layer_rows([])
        entegre_p._sp_diam.setValue(100.0)
        check("paneller sıfırlandı",
              len(katman_p._stack) == 0
              and entegre_p._layer_table.rowCount() == 0)

        proje_p._load_from_file(path)
        check("katman yığını geri yüklendi (3 katman)",
              len(katman_p._stack) == 3,
              f"bulundu: {len(katman_p._stack)}")
        if len(katman_p._stack) == 3:
            check("geri yüklenen katman 1 α=+55",
                  abs(katman_p._stack[0].alpha_deg - 55.0) < 1e-6)
            check("geri yüklenen katman 3 hoop",
                  katman_p._stack[2].type.value == "hoop")
        check("entegre tablo geri yüklendi (2 satır)",
              entegre_p._layer_table.rowCount() == 2,
              f"bulundu: {entegre_p._layer_table.rowCount()}")
        check("entegre mandrel çapı geri yüklendi",
              abs(entegre_p._sp_diam.value() - 120.0) < 1e-6)
        check("sürtünme katsayısı geri yüklendi",
              abs(entegre_p._sp_friction.value() - 0.15) < 1e-6)

        # v1.0 dosyası açma — migrate + eski katmanlar yolu
        v1_path = os.path.join(td, "eski.fwp")
        with open(v1_path, "w", encoding="utf-8") as f:
            json.dump({
                "versiyon": "1.0", "proje_adi": "V1 Proje",
                "mandrel": {"tip": "silindir", "cap_mm": 200.0,
                            "uzunluk_mm": 500.0},
                "katmanlar": [{"tip": "sarmal", "aci_deg": 60.0,
                               "cift_sayisi": 1, "ply_kalinlik_mm": 0.3}],
                "basinc_MPa": 10.0,
            }, f)
        proje_p._load_from_file(v1_path)
        check("v1.0 dosyası açıldı, ad korundu",
              proje_p._proje["proje_adi"] == "V1 Proje")
        check("v1.0 → bellekte v2.0", proje_p._proje["versiyon"] == "2.0")
        check("v1.0 'katmanlar' yolundan katman yüklendi",
              len(katman_p._stack) == 1,
              f"bulundu: {len(katman_p._stack)}")

    proje_p.deleteLater()
    katman_p.deleteLater()
    entegre_p.deleteLater()


# ════════════════════════════════════════════════════════════════════════════
# 5. Kirli bayrak
# ════════════════════════════════════════════════════════════════════════════

def test_dirty_flag():
    print("\n--- 5. Kirli bayrak ---")
    from app.panels.proje_yoneticisi import ProjeYoneticisiPanel

    p = ProjeYoneticisiPanel()
    states = []
    p.degisiklikDurumu.connect(states.append)

    check("başlangıçta temiz", not p.has_unsaved_changes())

    p._fld_adi.setText("Değişen Ad")
    check("form düzenleme → kirli", p.has_unsaved_changes())
    check("degisiklikDurumu(True) yayınlandı", states and states[-1] is True)

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "dirty.fwp")
        p._save_to_file(path)
        check("kayıt → temiz", not p.has_unsaved_changes())
        check("degisiklikDurumu(False) yayınlandı", states[-1] is False)

    # Bağışıklık penceresi: yükleme hemen ardından harici sinyal kirli yapmaz
    p.mark_dirty_external()
    check("bağışıklık penceresi içinde harici sinyal yutuldu",
          not p.has_unsaved_changes())

    # Pencere dolduktan sonra harici sinyal kirli yapar
    p._dirty_grace_until = time.monotonic() - 1.0
    p.mark_dirty_external()
    check("pencere sonrası harici sinyal → kirli", p.has_unsaved_changes())
    p.deleteLater()


# ════════════════════════════════════════════════════════════════════════════
# 6. LayerSpec.from_dict dayanıklılığı
# ════════════════════════════════════════════════════════════════════════════

def test_layerspec_from_dict():
    print("\n--- 6. LayerSpec.from_dict bilinmeyen anahtar dayanıklılığı ---")
    from backend.core.manual_layer_sequencer import LayerSpec, LayerStack

    d = {"id": 0, "type": "helical", "layer_type": "helical",
         "alpha_deg": 55.0, "fitil_genisligi_mm": 6.0,
         "cakisma_pct": 5.0, "thickness_mm": 0.30,
         "feed_mm_s": 80.0, "spindle_rpm": 60.0,
         "friction_mu": 0.30, "strategy": "geodesic",
         "bilinmeyen_alan": "xyz"}
    spec = LayerSpec.from_dict(d)
    check("alias + bilinmeyen anahtar yutuldu",
          spec.alpha_deg == 55.0 and spec.type.value == "helical")

    # Tam roundtrip: to_dict → alias eklenmiş → from_dict
    stack = LayerStack()
    stack.add_layer(stack.make_helical(alpha_deg=30.0))
    sd = stack.to_dict()
    for L in sd["layers"]:
        L["layer_type"] = L["type"]   # UI tarafının eklediği alias
    stack2 = LayerStack.from_dict(sd)
    check("alias'lı yığın roundtrip kayıpsız",
          len(stack2) == 1 and abs(stack2[0].alpha_deg - 30.0) < 1e-9)


# ════════════════════════════════════════════════════════════════════════════

def main() -> int:
    print("=" * 72)
    print(" FAZ 25 — SPRINT 1 WORKFLOW HARDENING VALIDATION")
    print("=" * 72)

    test_del_row()
    test_schema_v2_and_migration()
    test_save_load_roundtrip()
    test_dirty_flag()
    test_layerspec_from_dict()

    print("\n" + "=" * 72)
    print(f" SONUÇ: {PASS} PASS / {FAIL} FAIL")
    if FAIL == 0:
        print(" ★★★ SPRINT 1 READY ★★★")
    else:
        print(" STOP — SPRINT 1 EKSİK")
    print("=" * 72)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
