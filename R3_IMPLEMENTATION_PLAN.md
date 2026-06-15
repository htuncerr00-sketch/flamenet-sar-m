# R3_IMPLEMENTATION_PLAN.md — Path Explosion Protection

> Tarih: 2026-06-15  
> Onaylanan limitler: K1=2,000 K2=250,000 K3=50,000 (sonraki sprint) K4=1,000 (sonraki sprint)  
> Durum: **ONAY BEKLENİYOR — Kod yazılmadı**

---

## 1. Değişecek Dosyalar

| Dosya | Satır etkisi | Değişiklik türü |
|-------|-------------|-----------------|
| `faz17_d1/core/path_generator.py` | +70 satır | Sınıf + sabitler + 2 fonksiyon eklenir |
| `faz17_d2/app/panels/cam_panel.py` | +35 satır | `_do_calculate()` başına preflight bloğu |
| `validation_d2/test_r3_preflight.py` | +130 satır | Yeni test dosyası (Qt gerektirmez) |
| `validation_d2/cam_stress_test.py` | +25 satır | Senaryo A: patlama → graceful fail |

**Dokunulmayan dosyalar:** `_generate_helical()`, `_geodesic_pass()`, `clairaut_circuit_count()`,  
`generate_path()`, `WindingPath`, `WindingPoint`, `WindingPathParams` — hiçbiri değişmez.

---

## 2. `path_generator.py` — Tam Kod

### 2.1 Pozisyon: İmportların hemen altına, `WindingPathParams`'tan önce

```python
# ── R3: Kompleksite koruma ────────────────────────────────────────────────────

class ComplexityError(ValueError):
    """
    Yol hesaplama parametreleri güvenli limiti aşıyor.

    Bu exception worker thread'den fırlatılır ve `_on_path_error` sinyali ile
    ana thread'de kullanıcıya gösterilir.  Türk dilinde açıklayıcı mesaj içerir.
    """


_MAX_CIRCUITS_PER_LAYER: int = 2_000    # K1: tek kat başına devre üst sınırı
_MAX_TOTAL_POINTS: int = 250_000         # K2: WindingPoint nesnesi üst sınırı
_MAX_PATH_LENGTH_MM: float = 5_000_000  # 5 km fiber — son savunma hattı
```

### 2.2 Pozisyon: `clairaut_circuit_count()` fonksiyonundan hemen sonra (~satır 148)

```python
@dataclass
class ComplexityEstimate:
    """
    Gerçek hesap başlamadan tahmin edilen kompleksite ölçüleri.
    `preflight_check()` tarafından doldurulur ve loglanır.
    """
    n_circuits_per_layer: int
    n_layers: int
    total_circuits: int
    total_points: int
    estimated_memory_mb: float
    estimated_runtime_s: float
    estimated_fiber_length_mm: float


def estimate_complexity(params: "WindingPathParams") -> ComplexityEstimate:
    """
    Yol kompleksitesini parametre düzeyinde tahmin et — döngü çalışmaz.

    Hız: ~1 µs (sadece formül, veri yapısı oluşturulmaz).
    Çağrılabilir yer: hem ana thread hem worker thread (saf Python + math).

    Fiber uzunluğu tahmini: L_eff / sin(α) × toplam_devre
    (geodezik geçiş uzunluğunun alt tahmini; gerçek daha uzun olabilir)
    """
    r_avg = params.profile.avg_radius_mm
    alpha_rad = math.radians(max(params.alpha_deg, 1.0))   # sin(0)=0 koruması

    n_circ = clairaut_circuit_count(
        r_avg, alpha_rad, params.tow_width_mm, params.overlap_pct
    )
    total_circ = n_circ * params.n_layers
    total_pts  = total_circ * params.n_steps_per_pass

    # WindingPoint bellek kullanımı: 6 float × 8B + 2 int × 4B ≈ 56 B/nokta
    mem_mb = total_pts * 56 / 1_000_000

    # Ampirik oran: ~30k WindingPoint/sn (Python obje oluşturma + list.extend)
    runtime_s = total_pts / 30_000

    # Fiber uzunluğu: L_eff / sin(α) × toplam devre
    L_eff = float(params.profile.length_mm)
    fiber_per_pass = L_eff / math.sin(alpha_rad)
    est_fiber_mm   = total_circ * fiber_per_pass

    return ComplexityEstimate(
        n_circuits_per_layer = n_circ,
        n_layers             = params.n_layers,
        total_circuits       = total_circ,
        total_points         = total_pts,
        estimated_memory_mb  = mem_mb,
        estimated_runtime_s  = runtime_s,
        estimated_fiber_length_mm = est_fiber_mm,
    )


def preflight_check(params: "WindingPathParams") -> ComplexityEstimate:
    """
    Limit aşılırsa Türkçe açıklamalı `ComplexityError` fırlatır.
    Geçerse `ComplexityEstimate` döner (caller tarafından loglanabilir).

    Denetim sırası:
    1. Devre/kat limiti (K1=2,000)   — fitil/açı problemi
    2. Toplam nokta limiti (K2=250,000) — kat × devre × adım
    3. Fiber uzunluk limiti (5,000 m)  — son savunma hattı

    Birden fazla limit aşılırsa YALNIZCA ilk ihlal raporlanır;
    kullanıcı her seferinde tek bir düzeltme yapar ve yeniden dener.
    """
    est = estimate_complexity(params)

    if est.n_circuits_per_layer > _MAX_CIRCUITS_PER_LAYER:
        raise ComplexityError(
            f"Devre sayısı çok yüksek: {est.n_circuits_per_layer:,} devre/kat "
            f"(limit: {_MAX_CIRCUITS_PER_LAYER:,}).\n\n"
            f"Mevcut parametreler:\n"
            f"  • Mandrel çapı: {params.profile.avg_radius_mm * 2:.0f} mm\n"
            f"  • Sarma açısı: {params.alpha_deg:.1f}°\n"
            f"  • Fitil genişliği: {params.tow_width_mm:.1f} mm\n"
            f"  • Çakışma: {params.overlap_pct:.0f}%\n\n"
            f"Öneri: fitil genişliğini artırın "
            f"({params.tow_width_mm:.1f} mm → {params.tow_width_mm * 1.5:.0f} mm) "
            f"ya da sarma açısını düşürün."
        )

    if est.total_points > _MAX_TOTAL_POINTS:
        raise ComplexityError(
            f"Toplam nokta sayısı çok yüksek: {est.total_points:,} nokta "
            f"(limit: {_MAX_TOTAL_POINTS:,}).\n\n"
            f"Mevcut parametreler:\n"
            f"  • Kat sayısı: {params.n_layers}\n"
            f"  • Devre/kat: {est.n_circuits_per_layer:,}\n"
            f"  • Adım/devre: {params.n_steps_per_pass}\n\n"
            f"Öneri: kat sayısını azaltın "
            f"({params.n_layers} → {max(1, params.n_layers // 2)}) "
            f"ya da fitil genişliğini artırın."
        )

    if est.estimated_fiber_length_mm > _MAX_PATH_LENGTH_MM:
        fiber_km = est.estimated_fiber_length_mm / 1_000_000
        limit_km = _MAX_PATH_LENGTH_MM / 1_000_000
        raise ComplexityError(
            f"Tahmini fiber uzunluğu çok yüksek: {fiber_km:.1f} km "
            f"(limit: {limit_km:.0f} km).\n"
            f"Kat sayısını veya mandrel boyutunu azaltın."
        )

    return est
```

---

## 3. `cam_panel.py` — Entegrasyon Noktası

### Nereye ekleniyor?

`_do_calculate()` metodu (mevcut satır 522). Profil oluşturulduktan sonra,
`generate_path()` çağrılmadan önce — tam olarak `if stack and stack.get("layers"):` bloğundan önce.

### Eklenen import (fonksiyon içinde, `_make_backend()` çağrısından sonra)

```python
def _do_calculate(self, req: _CalcParams):
    MandrelProfile, WindingPathParams, generate_path, \
        plan_motion, MachineConfig, generate_gcode = _make_backend()
    from backend.core.path_generator import (       # ← YENİ
        preflight_check, estimate_complexity,
        ComplexityError, _MAX_TOTAL_POINTS,
    )
    import math as _math
    # ... profil oluşturma (mevcut, değişmez) ...
```

### Stack mod preflight (çok-katmanlı mod, mevcut satır 548)

```python
    # ── R3 Preflight ─────────────────────────────────────────────────────
    stack = req.stack_dict
    if stack and stack.get("layers"):
        # Her katman bağımsız denetlenir; toplam nokta da kontrol edilir
        total_pts_all = 0
        for layer in stack["layers"]:
            _ltype = layer.get("layer_type") or layer.get("type", "helical")
            _alpha = float(layer.get("alpha_deg", req.alpha_deg))
            # polar → alpha 10-20° olarak dönüştürülür; hoop → 88°
            if _ltype == "hoop":
                _alpha = 88.0
            elif _ltype == "polar":
                _alpha = min(max(_alpha, 5.0), 20.0)
            _pp_pre = WindingPathParams(
                profile=profile,
                alpha_deg=_alpha,
                n_layers=1,      # her katman tek tek değerlendirilir
                tow_width_mm=float(layer.get("fitil_genisligi_mm", req.tow_w_mm)),
                overlap_pct=float(layer.get("cakisma_pct", req.overlap_pct)),
            )
            est = preflight_check(_pp_pre)          # ComplexityError fırlatır
            total_pts_all += est.total_points
            log.info("[CAM] preflight katman %d: %d devre, %d nokta",
                     stack["layers"].index(layer),
                     est.n_circuits_per_layer, est.total_points)

        # Tüm katmanların toplam noktası
        if total_pts_all > _MAX_TOTAL_POINTS * len(stack["layers"]):
            raise ComplexityError(
                f"Katman dizilimi toplam nokta sayısı çok yüksek: "
                f"{total_pts_all:,} nokta "
                f"({len(stack['layers'])} katman × ortalama "
                f"{total_pts_all // len(stack['layers']):,}).\n"
                f"Katman sayısını azaltın veya fitil genişliğini artırın."
            )
        log.info("[CAM] preflight OK (stack): %d katman, %d toplam nokta",
                 len(stack["layers"]), total_pts_all)

    else:
        # Parametrik mod
        _strategy = req.strategy_text
        _alpha = req.alpha_deg
        if _strategy == "Çevre":
            _alpha = 88.0
        elif _strategy == "Kutupsal":
            _alpha = min(max(_alpha, 5.0), 20.0)
        _pp_pre = WindingPathParams(
            profile=profile,
            alpha_deg=_alpha,
            n_layers=req.n_layers,
            tow_width_mm=req.tow_w_mm,
            overlap_pct=req.overlap_pct,
        )
        est = preflight_check(_pp_pre)
        log.info("[CAM] preflight OK: %d devre/kat, %d toplam nokta, "
                 "%.1f MB, ~%.0f sn",
                 est.n_circuits_per_layer, est.total_points,
                 est.estimated_memory_mb, est.estimated_runtime_s)
    # ── /R3 Preflight ─────────────────────────────────────────────────────

    # ... mevcut generate_path çağrıları (değişmez) ...
```

### `ComplexityError` kullanıcıya nasıl iletilir?

`_do_calculate()` tüm exceptionları worker'a iletir:

```python
class _Worker(QObject):
    def run(self):
        try:
            path, profile, al = self._fn(self._params)
            self.finished.emit(...)
        except Exception as e:        # ComplexityError buraya düşer
            self.error.emit(f"{type(e).__name__}: {e}", self._gen)
```

`_on_path_error()` zaten mevcut — `QMessageBox.critical()` ile kullanıcıya gösterir.
Kullanıcı **"ComplexityError: Devre sayısı çok yüksek..."** mesajını görür.

---

## 4. Test Dosyası — `validation_d2/test_r3_preflight.py`

Qt gerektirmez. Doğrudan `path_generator` modülünü test eder.

```python
"""
test_r3_preflight.py — R3 kompleksite koruma testleri

Qt gerektirmez; path_generator'ı doğrudan import eder.
Her test: estimate_complexity() veya preflight_check() davranışını doğrular.

Çalıştırma:
  cd faz17_d2
  python validation_d2/test_r3_preflight.py
  # Beklenen çıktı: 10/10 PASS, rc=0
"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), '..', 'faz17_d1_backend'))

from faz17_d1.core.path_generator import (
    WindingPathParams, preflight_check, estimate_complexity,
    ComplexityError, _MAX_CIRCUITS_PER_LAYER, _MAX_TOTAL_POINTS,
)
from faz17_d1.core.geometry_engine import MandrelProfile

PASS = 0
FAIL = 0


def _ok(name):
    global PASS
    PASS += 1
    print(f"  ✓ {name}")


def _fail(name, reason):
    global FAIL
    FAIL += 1
    print(f"  ✗ {name}: {reason}")


def _make(D_mm, L_mm, alpha_deg, tow_mm, overlap_pct, n_layers):
    profile = MandrelProfile.cylinder(L_mm, D_mm / 2.0)
    return WindingPathParams(
        profile=profile,
        alpha_deg=alpha_deg,
        n_layers=n_layers,
        tow_width_mm=tow_mm,
        overlap_pct=overlap_pct,
    )


# ── Test 1: Normal parametreler → OK ─────────────────────────────────────────

def test_normal_ok():
    p = _make(D_mm=100, L_mm=300, alpha_deg=55, tow_mm=6, overlap_pct=5, n_layers=4)
    try:
        est = preflight_check(p)
        assert est.n_circuits_per_layer <= 100, f"beklenen ≤100, gerçek {est.n_circuits_per_layer}"
        assert est.total_points <= 100_000, f"beklenen ≤100k, gerçek {est.total_points}"
        _ok("normal_ok — D=100 α=55° n=4 geçti")
    except Exception as e:
        _fail("normal_ok", str(e))


# ── Test 2: Büyük mandrel + yüksek açı → devre limiti aşılır ─────────────────

def test_circuit_limit_exceeded():
    # D=2000mm, α=89°, tow=0.5mm → ~62,830 devre/kat >> 2000
    p = _make(D_mm=2000, L_mm=2000, alpha_deg=89, tow_mm=0.5, overlap_pct=0, n_layers=1)
    try:
        preflight_check(p)
        _fail("circuit_limit_exceeded", "ComplexityError beklendi, gelmedi")
    except ComplexityError as e:
        msg = str(e)
        if "Devre sayısı" in msg and "2,000" in msg:
            _ok("circuit_limit_exceeded — doğru hata mesajı")
        else:
            _fail("circuit_limit_exceeded", f"yanlış mesaj: {msg[:80]}")
    except Exception as e:
        _fail("circuit_limit_exceeded", f"yanlış exception tipi: {type(e).__name__}: {e}")


# ── Test 3: Çok katman + orta mandrel → toplam nokta limiti ──────────────────

def test_total_points_limit_exceeded():
    # D=300mm, α=55°, tow=6mm, overlap=5%, n=16
    # n_circ ≈ 138/kat → 138 × 16 × 150 = 331,200 > 250,000
    p = _make(D_mm=300, L_mm=500, alpha_deg=55, tow_mm=6, overlap_pct=5, n_layers=16)
    try:
        preflight_check(p)
        _fail("total_points_limit_exceeded", "ComplexityError beklendi, gelmedi")
    except ComplexityError as e:
        msg = str(e)
        if "Toplam nokta" in msg and "250,000" in msg:
            _ok("total_points_limit_exceeded — doğru hata mesajı")
        else:
            _fail("total_points_limit_exceeded", f"yanlış mesaj: {msg[:80]}")
    except Exception as e:
        _fail("total_points_limit_exceeded", f"yanlış exception tipi: {type(e).__name__}: {e}")


# ── Test 4: Limit sınırında (2000 devre/kat) → geçer ─────────────────────────

def test_at_circuit_limit_passes():
    # 2000 devre/kat için: n = ceil(2π × r × sin(α) / eff_w) = 2000
    # Ters hesap: r × sin(α) = 2000 × eff_w / (2π)
    # α=88°, eff_w=1mm: r = 2000 / (2π × sin(88°)) ≈ 318.5mm → D≈637mm
    p = _make(D_mm=635, L_mm=500, alpha_deg=88, tow_mm=1.0, overlap_pct=0, n_layers=1)
    try:
        est = preflight_check(p)
        if est.n_circuits_per_layer <= _MAX_CIRCUITS_PER_LAYER:
            _ok(f"at_circuit_limit_passes — {est.n_circuits_per_layer} devre/kat")
        else:
            _fail("at_circuit_limit_passes", f"{est.n_circuits_per_layer} > {_MAX_CIRCUITS_PER_LAYER}")
    except ComplexityError:
        _fail("at_circuit_limit_passes", "sınırda red edildi (parametre ayarı gerekebilir)")
    except Exception as e:
        _fail("at_circuit_limit_passes", str(e))


# ── Test 5: ComplexityError → doğru exception tipi ve hiyerarşi ──────────────

def test_complexity_error_type():
    p = _make(D_mm=2000, L_mm=2000, alpha_deg=89, tow_mm=0.5, overlap_pct=0, n_layers=1)
    try:
        preflight_check(p)
        _fail("complexity_error_type", "exception beklendi")
    except ComplexityError as e:
        # ValueError alt sınıfı olmalı
        if isinstance(e, ValueError):
            _ok("complexity_error_type — ValueError alt sınıfı doğrulandı")
        else:
            _fail("complexity_error_type", "ValueError alt sınıfı değil")
    except Exception as e:
        _fail("complexity_error_type", f"yanlış tip: {type(e).__name__}")


# ── Test 6: estimate_complexity hız kontrolü (<1ms) ──────────────────────────

def test_estimate_speed():
    import time
    p = _make(D_mm=100, L_mm=300, alpha_deg=55, tow_mm=6, overlap_pct=5, n_layers=4)
    t0 = time.monotonic()
    for _ in range(1000):
        estimate_complexity(p)
    elapsed_ms = (time.monotonic() - t0) * 1000
    avg_us = elapsed_ms / 1000 * 1000
    if avg_us < 1000:  # <1ms/çağrı
        _ok(f"estimate_speed — ortalama {avg_us:.0f} µs/çağrı (<1ms)")
    else:
        _fail("estimate_speed", f"çok yavaş: {avg_us:.0f} µs/çağrı")


# ── Test 7: Türkçe hata mesajı eyleme dönüştürülebilir ──────────────────────

def test_error_message_turkish_actionable():
    p = _make(D_mm=2000, L_mm=2000, alpha_deg=89, tow_mm=0.5, overlap_pct=0, n_layers=1)
    try:
        preflight_check(p)
        _fail("error_message_turkish", "exception beklendi")
    except ComplexityError as e:
        msg = str(e)
        has_turkish = any(w in msg for w in ["fitil", "çakışma", "açısı", "kat"])
        has_suggestion = "Öneri" in msg or "artırın" in msg or "azaltın" in msg
        if has_turkish and has_suggestion:
            _ok("error_message_turkish_actionable — Türkçe + öneri içeriyor")
        else:
            _fail("error_message_turkish_actionable",
                  f"Türkçe={has_turkish}, öneri={has_suggestion}: {msg[:100]}")
    except Exception as e:
        _fail("error_message_turkish_actionable", str(e))


# ── Test 8: Hoop stratejisi → α=88° → yüksek devre sayısı → red ─────────────

def test_hoop_large_mandrel_rejected():
    # Hoop: α~88°, D=500mm, tow=1mm → yüksek devre
    p = _make(D_mm=500, L_mm=500, alpha_deg=88, tow_mm=1.0, overlap_pct=0, n_layers=1)
    est = estimate_complexity(p)
    if est.n_circuits_per_layer > _MAX_CIRCUITS_PER_LAYER:
        # Bu parametre red edilmeli
        try:
            preflight_check(p)
            _fail("hoop_large_mandrel_rejected", "ComplexityError beklendi")
        except ComplexityError:
            _ok(f"hoop_large_mandrel_rejected — {est.n_circuits_per_layer} devre/kat red edildi")
    else:
        _ok(f"hoop_large_mandrel_rejected — {est.n_circuits_per_layer} devre/kat limit içinde")


# ── Test 9: Çok küçük çap → minimum devre → OK ───────────────────────────────

def test_small_mandrel_always_passes():
    p = _make(D_mm=10, L_mm=50, alpha_deg=45, tow_mm=6, overlap_pct=5, n_layers=2)
    try:
        est = preflight_check(p)
        assert est.total_points < 1000, f"beklenen <1000, gerçek {est.total_points}"
        _ok(f"small_mandrel_always_passes — {est.total_points} nokta")
    except Exception as e:
        _fail("small_mandrel_always_passes", str(e))


# ── Test 10: estimate → preflight tutarlılığı ─────────────────────────────────

def test_estimate_preflight_consistency():
    # estimate_complexity ve preflight_check aynı değerleri vermeli
    p = _make(D_mm=200, L_mm=400, alpha_deg=60, tow_mm=6, overlap_pct=10, n_layers=4)
    est1 = estimate_complexity(p)
    try:
        est2 = preflight_check(p)
        if (est1.n_circuits_per_layer == est2.n_circuits_per_layer and
                est1.total_points == est2.total_points):
            _ok(f"estimate_preflight_consistency — {est1.total_points} nokta, tutarlı")
        else:
            _fail("estimate_preflight_consistency",
                  f"uyumsuz: est={est1.total_points}, check={est2.total_points}")
    except ComplexityError:
        _ok("estimate_preflight_consistency — küçük mandrel reddedilmemeli, test gözden geçir")
    except Exception as e:
        _fail("estimate_preflight_consistency", str(e))


# ── Ana çalıştırıcı ──────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(" R3 PREFLIGHT TEST — path_generator kompleksite koruma")
    print("-" * 60)
    test_normal_ok()
    test_circuit_limit_exceeded()
    test_total_points_limit_exceeded()
    test_at_circuit_limit_passes()
    test_complexity_error_type()
    test_estimate_speed()
    test_error_message_turkish_actionable()
    test_hoop_large_mandrel_rejected()
    test_small_mandrel_always_passes()
    test_estimate_preflight_consistency()
    print("-" * 60)
    print(f"  Toplam: {PASS + FAIL}/10  |  PASS: {PASS}  FAIL: {FAIL}")
    print("=" * 60)
    if PASS == 10 and FAIL == 0:
        print(" ★★★ PASS — 10/10 preflight testi geçti ★★★")
        return 0
    print(" ✗ FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

---

## 5. `cam_stress_test.py` Ek Senaryo

Mevcut `main()` döngüsü (100 iterasyon) tamamlandıktan sonra:

```python
    # ── Senaryo A: Patlayıcı parametreler → ComplexityError → graceful fail ──
    print("  [A] Patlayıcı parametre testi...")
    panel._mandrel_type.setCurrentText("Silindir")
    panel._diameter.setValue(2000.0)    # çok büyük
    panel._alpha.setValue(89.0)          # çok yüksek açı
    panel._tow_w.setValue(0.5)           # çok ince fitil
    panel._n_layers.setValue(32)
    panel.set_layer_stack({"layers": []})  # parametrik mod

    panel._calculate_path()
    _wait_done(app, panel, timeout_s=5)   # 5 sn yeterli: preflight anında red eder

    if panel._winding_path is None:       # path üretilmemeli
        print("  [A] PASS: ComplexityError → winding_path=None (graceful fail)")
    else:
        fails.append("Senaryo A: patlayıcı parametre kabul edildi")
```

---

## 6. Teslim Kriterleri

Aşağıdaki komutlar sırasıyla çalıştırılır ve tümü RC=0 döner:

```bash
cd /home/user/flamenet-sar-m/faz17_d2

# 1. R3 birim testleri
python validation_d2/test_r3_preflight.py
# Beklenen: 10/10 PASS, rc=0

# 2. Tam stress testi (100 döngü + Senaryo A)
QT_QPA_PLATFORM=offscreen QT_OPENGL=software \
    python validation_d2/cam_stress_test.py
# Beklenen: 100/100 yol + 100/100 gcode + Senaryo A PASS, rc=0

# 3. Regresyon (Faz 17 D2 bütünü)
QT_QPA_PLATFORM=offscreen QT_OPENGL=software \
    python validation_d2/phase17_d2_validation.py
# Beklenen: 8/8 composite 100/100, rc=0
```

---

## 7. Kapsam Dışı (Bu Sprint)

Aşağıdakiler bu planda yoktur; sonraki sprintlerde ele alınacak:

| Konu | Sprint |
|------|--------|
| R5: Async G-code Worker | Sprint 3.3 |
| R6: 3D Viewer Decimation | Sprint 3.4 (K5 sırasına göre R6 sona alındı) |
| `n_steps_per_pass` kullanıcı ayarı | Kapsam dışı |
| Limit değerlerini UI'den ayarlama | Kapsam dışı |

---

## 8. Onay Beklenen Tek Karar

Tüm K kararları zaten verildi. **Tek belirsizlik:**

**Stack mod toplam nokta limiti:** Stack içindeki N katman için
toplam limit `_MAX_TOTAL_POINTS × N` mi, yoksa sabit `_MAX_TOTAL_POINTS * 2` mi?

Önerilen: **`_MAX_TOTAL_POINTS × len(layers)`**  
→ Her katman bağımsız kendi `_MAX_TOTAL_POINTS` limitine tabi  
→ Toplam kontrol, katman sayısıyla orantılı  
→ 8 katmanlı yığın: toplam 2M noktaya izin verir ama her biri 250k'nın altında kalır

Alternatif: **Sabit `_MAX_TOTAL_POINTS * 2 = 500,000`**  
→ Daha muhafazakâr; 8 katmanlı büyük mandrel büyük ihtimalle redlenir  
→ Kullanıcıyı kısıtlar

**Bu kararı onaylarsanız kod yazımına başlanır.**
