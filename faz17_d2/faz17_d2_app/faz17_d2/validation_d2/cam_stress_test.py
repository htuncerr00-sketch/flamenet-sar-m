"""
validation_d2/cam_stress_test.py — CAM Pipeline Hang Düzeltme Stress Testi
=========================================================================
RCA R1/R2/R4/R8 düzeltmelerini doğrular:

  R1 — worker thread'de hiçbir Qt widget erişimi yok (düz _CalcParams)
  R2 — 30 sn watchdog: hiçbir hesap kalıcı "Hesaplanıyor…"da takılmaz
  R4 — thread yaşam döngüsü deleteLater + cleanup ile güvenli (sızıntı yok)
  R8 — stack_dict propagation izlenir (boş yığın → parametrik moda dönüş loglanır)

100 ardışık "Yolu Hesapla" + "G-code Üret" döngüsü; parametrik / çok-katmanlı /
farklı mandrel tipleri (Silindir, Konik, Kubbeli) karıştırılır.

rc=0  → 100/100 yol + 100/100 gcode, hang yok, thread temiz.
rc=1  → en az bir döngü takıldı / başarısız.
"""
import os
import sys
import time
import logging

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# Offscreen'de modal dialog'lar testi bloklamasın diye sustur
QMessageBox.critical = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.information = staticmethod(lambda *a, **k: None)

from app.panels.cam_panel import CAMPanel

N = 100
WAIT_S = 35  # watchdog 30 sn → 35 sn üst sınır


def _wait_done(app, panel, timeout_s=WAIT_S) -> bool:
    """Hesap tamamlanana kadar olay döngüsünü pompala.

    Tamamlanma göstergesi: thread.finished → _on_thread_cleanup
    self._worker_thread'i None yapar (bu noktada _on_path_done zaten işlendi).
    """
    t0 = time.time()
    while panel._worker_thread is not None:
        app.processEvents()
        if time.time() - t0 > timeout_s:
            return False
        time.sleep(0.003)
    app.processEvents()
    return True


def _make_stack(i: int) -> dict:
    return {
        "versiyon": "1.0", "default_friction_mu": 0.3, "next_id": 3,
        "layers": [
            {"id": 0, "type": "helical", "alpha_deg": 45.0 + (i % 20),
             "fitil_genisligi_mm": 6.0, "cakisma_pct": 5.0, "thickness_mm": 0.30,
             "feed_mm_s": 80.0, "spindle_rpm": 60.0, "friction_mu": 0.0,
             "strategy": "geodesic", "label": "Sarmal", "notes": ""},
            {"id": 1, "type": "hoop", "alpha_deg": 89.0,
             "fitil_genisligi_mm": 6.0, "cakisma_pct": 5.0, "thickness_mm": 0.30,
             "feed_mm_s": 80.0, "spindle_rpm": 60.0, "friction_mu": 0.0,
             "strategy": "geodesic", "label": "Çevre", "notes": ""},
        ],
    }


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    panel = CAMPanel()

    n_path = 0
    n_gcode = 0
    fails = []
    mandrel_types = ["Silindir", "Konik", "Kubbeli Silindir"]

    t_start = time.time()
    for i in range(N):
        mt = mandrel_types[i % 3]
        panel._mandrel_type.setCurrentText(mt)
        panel._diameter.setValue(60.0 + (i % 40))
        panel._length.setValue(250.0 + (i % 100))
        panel._alpha.setValue(35.0 + (i % 40))
        panel._n_layers.setValue(1 + (i % 4))
        panel._tow_w.setValue(6.0)
        panel._overlap.setValue(5.0)

        multi = (i % 2 == 0)
        if multi:
            panel.set_layer_stack(_make_stack(i))
        else:
            panel.set_layer_stack({"layers": []})  # parametrik moda dön (R8)

        panel._calculate_path()
        if not _wait_done(app, panel):
            fails.append(f"#{i} ({mt}, {'çok' if multi else 'param'}): HANG / timeout")
            # nesil ilerlet, bir sonrakine devam et
            continue
        if panel._winding_path is None:
            fails.append(f"#{i}: yol üretilmedi")
            continue
        n_path += 1

        panel._generate_gcode()
        app.processEvents()
        gp = panel._gcode_program
        if gp is not None and getattr(gp, "lines", None):
            n_gcode += 1
        else:
            fails.append(f"#{i}: gcode üretilmedi")

    elapsed = time.time() - t_start

    print("=" * 64)
    print(f" CAM PIPELINE STRESS TEST — {N} döngü, {elapsed:.1f} sn")
    print("-" * 64)
    print(f"   yol hesaplandı     : {n_path}/{N}")
    print(f"   gcode üretildi     : {n_gcode}/{N}")
    print(f"   kalan worker thread: {panel._worker_thread}")
    print(f"   kalan watchdog     : {panel._watchdog}")
    if fails:
        print("   HATALAR:")
        for f in fails[:20]:
            print("     -", f)
    print("=" * 64)

    ok = (n_path == N and n_gcode == N and not fails
          and panel._worker_thread is None and panel._watchdog is None)
    if ok:
        print(" ★★★ PASS — 100/100 hesap + gcode, hang yok, thread/watchdog temiz ★★★")
        return 0
    print(" ✗ FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
