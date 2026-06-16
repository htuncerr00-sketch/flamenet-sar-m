# TwinState Entegrasyon Raporu — Mevcut Animasyonun Gerçek Dijital İkize Bağlanması

> **Durum:** Yalnızca teknik entegrasyon analizi — bu belgede kod yazılmadı / implementasyon yapılmadı.
> **Tarih:** 2026-06-16
> **Branch:** `claude/amazing-feynman-XUXBf`
> **Karar:** Yeni backend YOK. Mevcut `winding_twin` motoru merkez. UI ona bağlanacak.

---

## 0. Amaç

CAM Tasarım Merkezi'ndeki (`entegre_tasarim_paneli.py`) **kendi-yazımı naif
animasyon** (path noktalarından elle `cos/sin` projeksiyonu) **kaldırılacak** ve
yerine `winding_twin.simulate_winding()` motorunun ürettiği doğrulanmış
**`TwinState` akışı** bağlanacak.

Bu rapor üç soruyu net cevaplıyor:
1. **Hangi backend modülleri kullanılacak** (§2)
2. **Hangi UI kodları kaldırılacak** (§3)
3. **Hangi animasyon kodları TwinState ile değiştirilecek** (§4 — alan-alan eşleme)

---

## 1. Merkez Motor: `winding_twin.simulate_winding`

İmza (doğrulandı, `winding_twin.py:157`):

```python
simulate_winding(
    base_profile: MandrelProfile,
    band: FiberBand,
    base_params: WindingPathParams,
    n_layers: int,
    machine: Optional[MachineEnvelope] = None,    # None → varsayılan
    tension: Optional[FiberTensionModel] = None,  # None → varsayılan
    payout: Optional[PayoutDynamicsConfig] = None,# None → varsayılan
    dt_s: float = 1.0,
    hold_angle: bool = True,
    deposition_grid: Tuple[int, int] = (100, 240),
) -> TwinSimulationResult
```

Yani facade'ın motoru sürmek için sağlaması gereken **zorunlu** girdiler yalnızca:
`base_profile`, `band` (FiberBand), `base_params` (WindingPathParams), `n_layers`.
Diğer üçü (machine/tension/payout) `None` bırakılıp varsayılanları kullanılabilir.

**Çıktı `TwinSimulationResult`** (`winding_twin.py:73`) içeriği:
- `states: List[TwinState]` — zaman-adımlı durum akışı (animasyonun kalbi)
- `final_deposition: DepositionMap` — `thickness_mm[i,j]` kalınlık grid'i (§ hedef 6)
- `base_radius_mm → final_radius_mm` — toplam katman büyümesi (§ hedef 5)
- `dt_s`, `total_time_s`, `layer_time_ranges_s`, `max_spindle_rpm`, `max_lag_error_mm`
- `state_at(t) -> TwinState` — O(1) zaman → durum (`round(t/dt_s)`)

---

## 2. Kullanılacak Backend Modülleri (Envanter)

> Hepsi **korunur, değiştirilmez, sadece tüketilir.** Hiçbiri yeniden yazılmaz.

| Modül | Facade'da kullanım | UI'da görünür sonuç | Kullanıcı hedefi |
|---|---|---|---|
| `winding_twin.py` | `simulate_winding()` → `TwinSimulationResult` | Tüm 4-eksen oynatım | 1,2,3,4,5 |
| `trajectory_builder.py` | `simulate_winding` içinde `build_timeline` (dolaylı) | Tekdüze dt zaman ızgarası | 3 |
| `machine_execution.py` | İleride kinematik-limitli yürütme (S3 opsiyon) | RPM/hız sınırı doğrulaması | 3 |
| `fiber_deposition.py` | `result.final_deposition` (`DepositionMap`) | Kalınlık shell + birikim | 2,6 |
| `fiber_band.py` | `FiberBand(tow_width, tow_thickness, ...)` girdi | Tow genişlik/kalınlık fiziği | 2 |
| `coverage_solver.py` | `solve_coverage(path, band, profile)` → `CoverageMap` | Z-θ overlap heatmap | 7 (S5) |
| `fiber_contact_model.py` | `_surface_normal_unit`, `compute_contact_patch` | Ribbon yüzey normali + temas | 2,4 (S4) |
| `non_geodesic_engine.py` | `friction_mu>0` yolunda (path_generator üzerinden) | Non-geodesic yörünge | 6 |
| `geometry_engine.py` | `MandrelProfile` (parametrik + STL) | Mandrel geometrisi | 1 |
| `path_generator.py` | `WindingPathParams`, `generate_path` (G-code yolu) | Yol + preflight (R3) | — |
| `motion_planner.py` + `gcode_postprocessor.py` | G-code zinciri (mevcut) | G-code çıktı sekmesi | — |

**Dolaylı bağımlılıklar** (simulate_winding içinde otomatik, facade'ın bilmesi gerekmez):
`layer_buildup.generate_layered_paths`, `industrial_motion.plan_industrial_motion`,
`machine_envelope.MachineEnvelope`, `fiber_tension.FiberTensionModel`,
`payout_dynamics.PayoutDynamicsConfig`.

---

## 3. Kaldırılacak / Değiştirilecek UI Kodları

> Dosya: `faz17_d2/faz17_d2_app/faz17_d2/app/panels/entegre_tasarim_paneli.py`
> (son commit `3c0d20f` ile yazdığım 4-eksen animasyon dahil)

### 3.1 — Tamamen KALDIRILACAK (naif geometri projeksiyonu)

| Kod birimi | Ne yapıyor (bugün) | Neden kaldırılıyor |
|---|---|---|
| `_setup_animation(path, profile)` | Path noktalarından elle `_anim_xyz`, `_anim_a_deg`, `_anim_x_mm` hesaplıyor (`np.interp` + `cos/sin`) | Yerini `simulate_winding` çıktısı alacak; geometri TwinState'ten gelecek |
| `_anim_xyz`, `_anim_a_deg`, `_anim_x_mm` attribute'ları | Naif önceden-hesaplanmış diziler | `self._twin: TwinSimulationResult` ile değişecek |
| `_anim_tick` içindeki `set_simulation_state(a_deg, x_mm, pts)` çağrısı | İndeks-temelli ham açı/x ile sürüyor | TwinState alanlarıyla sürülecek (§4) |
| `_path_to_3d_fast` içindeki **animasyon** kullanımı | Fiber büyümesini path noktası indexiyle üretiyor | Fiber artık `current_radius_mm` büyümeli yüzeyde, twin progress ile |

### 3.2 — KORUNACAK (animasyon kabuğu / UI iskeleti)

> Bunlar yeniden kullanılır; sadece **veri kaynağı** değişir.

| Kod birimi | Korunma nedeni |
|---|---|
| `_anim_timer` (QTimer 30 FPS) | Oynatım saati aynı kalır |
| `_btn_play/_pause/_stop_anim/_reset_anim`, `_anim_slider`, `_cb_speed`, `_anim_lbl` | UI kontrolleri aynı; `_anim_lbl` formatı X/A yerine layer/progress gösterecek |
| `_on_anim_play/_pause/_stop/_reset/_seek` | İskelet korunur; içleri TwinState index'ine göre güncellenecek |
| `_MachineGLView._rebuild_scene` | Mandrel + statik çerçeve + carriage kurulumu aynı |
| `_build_static_frame`, `_build_carriage_at_zero` | Makine geometrisi aynı |
| `_MachineGLView.set_simulation_state` / `clear_simulation_state` | **İmzası genişletilecek** (payout eye + radius ekle), ama metot korunur |

### 3.3 — `_MachineGLView`'a EKLENECEK (yeni render hedefleri)

| Yeni birim | Kaynak veri | Hedef |
|---|---|---|
| Payout eye mesh (carriage kolu ucunda hareketli işaretçi) | `TwinState.eye_x_mm`, `eye_r_mm` | 3 |
| `RibbonRenderer` (GLMeshItem şerit) | layered paths + `FiberBand` genişlik/kalınlık + yüzey normali | 2 (S4) |
| `ShellRenderer` (büyüyen kabuk) | `current_radius_mm` / `DepositionMap.thickness_mm` | 5,6 (S6) |
| `CoverageHeatmapRenderer` (yüzey boyama) | `CoverageMap.count` / `overlap_heatmap()` | 7 (S5) |

---

## 4. Animasyon Alan Eşlemesi: Bugünkü Kaynak → TwinState

> Bu tablo "hangi animasyon kodu TwinState ile değişecek" sorusunun tam cevabı.
> Sol = mevcut naif kaynak; sağ = onu **değiştirecek** TwinState alanı.

| # | Görsel öğe | BUGÜN (naif kaynak) | YENİ (TwinState alanı) | Not |
|---|---|---|---|---|
| 1 | **Mandrel dönüşü** | `_anim_a_deg[i]` (ham path `a_deg`) → `mandrel.rotate(a_deg, 1,0,0)` | `state.spindle_angle_deg` | Aynı eksen; ama artık S-eğrisi zamanlı, RPM-limitli gerçek açı |
| 2 | **Carriage X** | `_anim_x_mm[i]` (path hedef x) → `carriage.translate` | `state.carriage_x_actual_mm` | **lag dahil gerçek konum** (hedef `carriage_x_mm` değil) |
| 3 | **Payout eye** | *(bugün render YOK)* | `state.eye_x_mm`, `state.eye_r_mm` | Carriage kolunun ucuna yeni hareketli mesh |
| 4 | **Fiber birikimi** | `_anim_xyz[:i]` (indeks-temelli polyline) | `state.fiber_deposited_mm` + `current_radius_mm` ile büyüyen şerit | Fiber artık büyümüş yüzeye oturur |
| 5 | **Katman büyümesi** | *(YOK — tüm katmanlar aynı çap)* | `state.current_radius_mm` | Çap zamanla büyür; fiber dış yüzeyde |
| 6 | **Kalınlık birikimi** | *(YOK)* | `result.final_deposition.thickness_mm[i,j]` | Bitişte shell render (S6) |
| 7 | **Coverage heatmap** | *(YOK)* | `solve_coverage(...).count` (BGYR) | Bitişte yüzey boyama (S5) |
| 8 | **İlerleme etiketi** | `"X: .. A: .."` (ham) | `state.progress_pct`, `current_layer`, `current_circuit` | `_anim_lbl` metni güncellenecek |
| 9 | **Oynatım indexi** | `_anim_idx` + manuel step | `result.state_at(t)` veya `idx = round(t/dt_s)` | Hız çarpanı `t` ölçekler |

### 4.1 — Yeni oynatım döngüsü (kavramsal, kod değil)

```
"Simüle Et" → _collect_params() (R1: ana thread, düz veri)
            → QThread worker: cam_engine.compute_twin(req)
                 → simulate_winding(profile, band, params, n_layers, dt_s)
                 → TwinSimulationResult
            → finished signal → self._twin = result; oynatım hazır

_anim_tick (30 FPS):
   t += dt_play * speed
   state = self._twin.state_at(t)          # O(1)
   gl.set_simulation_state(
       spindle_angle_deg = state.spindle_angle_deg,   # → mandrel döner
       carriage_x_mm     = state.carriage_x_actual_mm,# → carriage kayar
       eye_x_mm          = state.eye_x_mm,            # → payout eye
       eye_r_mm          = state.eye_r_mm,
       radius_mm         = state.current_radius_mm,   # → katman büyümesi
       fiber_deposited   = state.fiber_deposited_mm)  # → fiber birikir
   _anim_lbl: f"Katman {state.current_layer} | %{state.progress_pct:.0f}"
   t ≥ total_time_s → dur; coverage + shell göster
```

---

## 5. cam_engine Facade — Sözleşme (S1)

> Tek giriş noktası. UI worker yalnızca buraya konuşur; backend importlarını UI bilmez.

```
@dataclass CamRequest:        # düz veri (R1 uyumlu — Qt yok)
    profile: MandrelProfile
    band: FiberBand           # tow_width_mm, tow_thickness_mm, ...
    params: WindingPathParams
    n_layers: int
    dt_s: float = 1.0

compute_twin(req)     -> TwinSimulationResult     (winding_twin.simulate_winding)
compute_coverage(req) -> CoverageMap              (coverage_solver.solve_coverage)
compute_gcode(req)    -> GCodeProgram             (motion_planner + postprocessor)
load_mandrel(stl/params) -> MandrelProfile        (geometry_engine / S2 axis solver)
```

**R3 (preflight) kuralı:** `compute_twin` çağrısından önce `preflight_check_stack`
çalıştırılır; 100-katman senaryosu güvenle reddedilir (donma yok).

---

## 6. Güncellenmiş Sprint Sırası

| Sprint | Başlık | Kapsam | Kabul kriteri |
|---|---|---|---|
| **S1** | CAM Facade + Envanter + Entegrasyon planı | `app/cam_engine.py` (`compute_twin/coverage/gcode/load_mandrel`); bu rapor onaylı | UI yalnız facade'a konuşur; backend importları tek dosyada |
| **S2** | **STL Axis Solver (EN YÜKSEK ÖNCELİK)** | `core/stl_axis_solver.py`: PCA eksen otodetect + robust p95 kesit → `radius(z)`; `from_stl` bunu kullanır | Eğik yönelimli test STL'i doğru `radius(z)` verir; birim testi |
| **S3** | TwinState tabanlı gerçek simülasyon | §3 naif animasyonu kaldır; §4 eşlemesini bağla; mandrel/carriage/payout/fiber/büyüme | Mandrel `spindle_angle_deg`, carriage `carriage_x_actual_mm`, eye `eye_x/r_mm`, çap büyür |
| **S4** | Ribbon / Tow Mesh Renderer | `GLLinePlotItem` → `GLMeshItem` şerit; fiziksel tow width + kalınlık; yüzey normali (`fiber_contact_model`) | Tow genişliği kamera zoom'undan bağımsız fiziksel |
| **S5** | Coverage Heatmap | `solve_coverage` → mandrel yüzeyine BGYR; overlap/gap bölgeleri | Z-θ heatmap + overlap görünür |
| **S6** | Layer Growth Visualization | `current_radius_mm` + `DepositionMap.thickness_mm` → büyüyen kabuk/shell | Katman ekledikçe çap+kalınlık görsel büyür |

---

## 7. Riskler

1. **`states[]` boyutu:** 100 katman × küçük `dt_s` → büyük dizi. `dt_s` ve
   `deposition_grid` ölçeklenmeli; R3 preflight `compute_twin`'e de uygulanmalı (S3).
2. **`set_simulation_state` imza genişlemesi:** Yeni parametreler (eye, radius)
   eklenince mevcut çağıranlar güncellenmeli — geriye dönük `update_fiber_paths`
   (statik hesap-sonrası render) korunmalı.
3. **İki "gerçek" tutarsızlığı:** Saf `generate_path` büyüme modellemez; UI artık
   `winding_twin`'i kaynak almalı. `generate_path` yalnız G-code yolu için kalır.
4. **Determinizm:** `simulate_winding` "aynı girdi → bit-aynı" garantisi (`:185-187`)
   replay/test için korunmalı; oynatım hızı yalnız görsel `t`'yi ölçekler, simülasyonu değil.

---

## 8. Sonuç

- **Backend yazılmayacak.** Merkez: `winding_twin` + 6 yardımcı motor (§2).
- **Kaldırılacak:** §3.1 naif geometri projeksiyonu (`_setup_animation`,
  `_anim_xyz/_anim_a_deg/_anim_x_mm` ham hesabı).
- **Değiştirilecek:** §4 tablosu — her görsel öğe ilgili `TwinState` alanına bağlanır.
- **Sıra:** S1 facade → **S2 STL Axis Solver (en yüksek öncelik)** → S3 TwinState
  simülasyon → S4 ribbon → S5 coverage → S6 layer growth.

> Onay verilirse **S1 (cam_engine facade)** ile başlanır; S2 STL Axis Solver
> hemen ardından gelir.
