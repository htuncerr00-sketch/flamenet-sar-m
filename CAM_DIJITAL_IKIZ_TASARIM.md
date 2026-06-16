# CAM Dijital İkiz — Onay Öncesi Tasarım Belgesi

> **Durum:** Tasarım/onay belgesi — kod YOK, implementasyon YOK.
> **Tarih:** 2026-06-16 · **Branch:** `claude/amazing-feynman-XUXBf`
> **Karar (sabit):** Yeni backend yazılmayacak. Mevcut motorlar (`winding_twin`,
> `machine_execution`, `trajectory_builder`, `fiber_deposition`, `fiber_band`,
> `coverage_solver`, `fiber_contact_model`, `non_geodesic_engine`) **tüketilecek.**
> **Amaç:** Çizgi animasyonu değil — gerçek filament winding **dijital ikizi**.

Bu belge beş bölümden oluşur:
**A.** TwinState Entegrasyon Tasarımı · **B.** STL Axis Solver Tasarımı ·
**C.** UI Dönüşüm Planı · **D.** Risk Analizi · **E.** Sprint Kırılımı.

---

## A. TwinState Entegrasyon Tasarımı

### A.1 — Naif animasyon vs TwinState simülasyonu (fark analizi)

| Boyut | NAİF (bugün, commit `3c0d20f`) | TWIN (hedef) |
|---|---|---|
| Veri kaynağı | `path.points` (saf geometri) | `winding_twin.simulate_winding()` → `TwinState[]` |
| Zaman modeli | Yok — nokta indeksi `_anim_idx` | Gerçek zaman `t_s`, tekdüze `dt_s` ızgarası |
| Mandrel açısı | Ham `WindingPoint.a_deg` | `spindle_angle_deg` (S-eğrisi zamanlı, RPM-limitli) |
| Carriage | Hedef `x_mm` (gecikmesiz) | `carriage_x_actual_mm` (1. derece lag dahil) |
| Payout eye | **Render yok** | `eye_x_mm`, `eye_r_mm` (lead + standoff) |
| Katman büyümesi | **Yok** (tüm katman aynı çap) | `current_radius_mm` (büyüyen yüzey) |
| Kalınlık | **Yok** | `DepositionMap.thickness_mm[i,j]` |
| Coverage/overlap | **Yok** | `coverage_solver.CoverageMap` |
| Fiber konumu | `np.cos/sin` ile elle projeksiyon | Yörünge + büyümüş yüzey yarıçapı |
| Fizik | Hiç (pür trigonometri) | Lag, lead, compaction, kinematik limit |
| Determinizm | Var ama anlamsız | "Aynı girdi → bit-aynı state[]" (`winding_twin.py:185-187`) |

**Özet:** Naif sistem mandrel yüzeyine sarılan bir ipi *taklit eden* bir
çizimdir. TwinState sistemi makinenin *gerçek davranışını* (gecikme, büyüme,
yatırma, kaplama) zaman ekseninde simüle eder. İkisi arasındaki fark "animasyon
vs dijital ikiz" farkıdır.

### A.2 — TwinState → Görsel öğe eşleme tablosu (çekirdek)

`TwinState` alanları (`winding_twin.py:52-70`) ve `TwinSimulationResult`
(`:73-95`) → 3D sahne öğeleri:

| # | TwinState / Result alanı | Görsel öğe | Render mekaniği (kavramsal) |
|---|---|---|---|
| 1 | `spindle_angle_deg` | **Mandrel dönüşü** | `_mandrel_items` X ekseni etrafında `rotate(angle,1,0,0)`; göstergeyle (0°/180° şerit) dönüş görünür |
| 2 | `carriage_x_actual_mm` | **Carriage hareketi** | `_carriage_items` X boyunca `translate(dx)`; lag dahil gerçek konum |
| 3 | `eye_x_mm`, `eye_r_mm` | **Payout eye** | Carriage kolu ucunda yeni hareketli mesh; eksenel `eye_x_mm`, radyal `eye_r_mm` |
| 4 | `fiber_deposited_mm` (+ `current_radius_mm`) | **Gerçek zamanlı fiber birikimi** | Şerit, twin ilerlemesine göre büyür; nokta yarıçapı `current_radius_mm`'den |
| 5 | `current_radius_mm` | **Katman büyümesi** | Fiber + (opsiyonel) yüzey kabuğu büyüyen yarıçapta; katman arttıkça çap görünür büyür |
| 6 | `result.final_deposition.thickness_mm[i,j]` | **Kalınlık görselleştirmesi** | Bitişte `z_bins×theta_bins` grid'den shell mesh; yükseklik = `thickness_mm` |
| 7 | `coverage_solver.solve_coverage(...).count` | **Coverage heatmap** | Mandrel yüzeyine BGYR boyama; overlap (`count≥2`) ve gap (`count==0`) |
| 8 | `progress_pct`, `current_layer`, `current_circuit` | **Durum etiketi** | `_anim_lbl`: "Katman k/N · Devre · %ilerleme" |

### A.3 — Eşlemelerin detayı (istenen 7 bağlantı)

**(1) `spindle_angle_deg` → mandrel dönüşü.**
`set_simulation_state` mandrel öğelerini `resetTransform()` + `rotate(angle)` ile
günceller. Naiften fark: açı artık ham path değil, RPM-limitli S-eğrisi zamanlı
gerçek açı → dönüş hızı katman sınırlarında zıplamaz (eski 45.819 RPM hatası
backend'de zaten giderilmiş, `winding_twin.py:Sprint 4B notu`).

**(2) `carriage_x_actual_mm` → carriage hareketi.**
`carriage_x_mm` (hedef) DEĞİL, `carriage_x_actual_mm` (lag = v·τ kadar geride)
kullanılır → carriage'ın gerçek fiziksel gecikmesi görünür. `np.clip(0, L)`.

**(3) `eye_x_mm` / `eye_r_mm` → payout eye.**
Bugün hiç yok. Carriage kolunun ucuna yeni bir küçük mesh (göz/makara) eklenir;
eksenel `eye_x_mm`, radyal `eye_r_mm` (standoff dahil) ile konumlanır. Fiber
çizgisi bu gözden temas noktasına (`contact_z_mm`, `contact_r_mm`) uzanır →
"fiber gözden mandrele iniyor" görünümü.

**(4) `fiber_deposited_mm` → gerçek zamanlı birikim.**
Şerit, twin zamanına göre büyür. Naiften fark: büyüme nokta-indeksiyle değil,
`fiber_deposited_mm` / toplam ile orantılı; her segment `current_radius_mm`
yüzeyine oturur (alttaki katmanlar üstündekini iter).

**(5) `current_radius_mm` → katman büyümesi.**
Her zaman adımında yüzey yarıçapı. Fiber bu yarıçapta yatar; istenirse mandrel
üstüne yarı saydam "büyüyen kabuk" eklenir. Katman 1 → r₀, katman N → `final_radius_mm`.

**(6) `DepositionMap.thickness_mm` → kalınlık görselleştirmesi.**
`final_deposition` `z_bins (Nz)` × `theta_bins (Nθ)` grid'i taşır. Her hücre
`thickness_mm[i,j]` → mandrel yüzeyinden radyal yükseklik. Renderda: silindirik
yüzey mesh'i, her köşe `r = profile.radius_at(z) + thickness_mm[i,j]` → gerçek
kalınlık topografyası (kalın bölgeler dışa kabarık).

**(7) `coverage_solver` → heatmap.**
`solve_coverage(path, band, profile, n_z, n_theta)` → `CoverageMap.count[i,j]`.
Renk skalası: 0=mavi (boşluk), 1=yeşil (tam), 2=sarı (çift), ≥3=kırmızı (aşırı
overlap). `overlap_heatmap()` doğrudan kullanılabilir. Mandrel yüzeyine vertex
rengi olarak işlenir.

---

## B. STL Axis Solver Tasarımı (Yeni En Yüksek Öncelik)

### B.0 — Mevcut durum ve sorun

Bugün `geometry_engine._profile_from_vertices` (`:93-111`) **dönme eksenini Z
varsayıyor** (`r=√(x²+y²)`) ve kesit olarak **slice başına `max(r)`** alıyor.
Sonuç: STL farklı yönelimde ise profil tamamen yanlış; tek aykırı vertex
yarıçapı şişirir. Hedef: ekseni **veriden bul**, kesiti **robust** çıkar.

### B.1 — STL ekseni nasıl bulunacak? (3 aşamalı)

```
Aşama 1 — TOHUM (PCA):
  • Vertex bulutunu centroid'e merkezle (C = mean(V)).
  • Kovaryans matrisi M = (V-C)ᵀ(V-C) / N.
  • numpy.linalg.eigh(M) → 3 özdeğer λ1≤λ2≤λ3, 3 özvektör e1,e2,e3.
  • 3 eksen ADAYI: {e1, e2, e3}. (Hangisinin gerçek eksen olduğu Aşama 2'de.)

Aşama 2 — SEÇİM (dönel-simetri kriteri):
  • Gerçek dönme ekseni: her z-diliminde tüm yüzey noktalarının eksene
    UZAKLIĞININ (ρ) en az saçıldığı eksendir (tanım: body of revolution).
  • Her aday eksen a için "simetri maliyeti" J(a):
        - noktaları a'ya projekte et → z' = (V-C)·a
        - ρ = ‖(V-C) - z'·a‖   (eksene dik uzaklık)
        - z'-yi K bine böl; her binde Var(ρ) hesapla
        - J(a) = Σ_bin (yüzey-noktaları için) Var(ρ)   ← küçük = iyi
  • 3 PCA adayından J en küçük olanı seç. (En büyük λ'yı KÖRÜ KÖRÜNE seçme!)

Aşama 3 — RAFİNASYON (numpy-only, scipy YOK):
  • Seçilen eksen etrafında küçük açısal ızgara araması (±birkaç derece,
    kaba→ince), J(a)'yı minimize et. (scipy mevcut değil — coarse grid descent.)
  • Yakınsama: J değişimi < eşik → eksen kilitli.
Çıktı: axis_unit (yön), C (eksen üzerindeki nokta), J_min (güven metriği).
```

### B.2 — PCA yeterli mi? → **Hayır, tek başına değil. Tohum olarak evet.**

PCA üç zayıflığa sahip:

1. **En-büyük-eksen yanılgısı.** Uzun ince mandrel (L>D) için dönme ekseni
   *en büyük* λ'dır; ama **kısa-şişman** gövde (D>L, ör. kısa basınç tankı) için
   dönme ekseni *en küçük* λ'dır. "Largest eigenvalue = axis" varsayımı bu yüzden
   güvenilmez. → Aşama 2 (simetri kriteri) tüm 3 adayı test ederek bunu çözer.
2. **Yön belirsizliği.** Özvektör işareti keyfi (±). Eksen için sorun değil
   (dönme ekseni yönsüz), ama z'-yönü için: domların geometrisi / vertex z-aralığı
   ile sabitlenir.
3. **Konum eksikliği.** PCA yalnız *yön* verir. Dönel cisimde centroid eksen
   üstündedir → C+yön yeterli. (Gövdede asimetrik çıkıntı varsa centroid kayar →
   Aşama 3 rafinasyonu konumu da iyileştirir.)

**Sonuç:** PCA = hızlı, güvenilir **tohum**. Tek karar verici değil; simetri
kriteri (Aşama 2-3) nihai kararı verir.

### B.3 — Simetrik gövdelerde hata riskleri

| Risk | Neden | Azaltma |
|---|---|---|
| **Dejenere özdeğerler** (λ1≈λ2 veya λ2≈λ3) | Küre, eşkenar/kübik en-boy: eksen özuzayda keyfi → PCA yönü kararsız | Dejenerasyonu tespit et (`(λ2-λ1)/λ3 < ε`); simetri-kriteri aramasına düş; güven düşükse kullanıcıdan eksen ipucu iste |
| **D≈L (küresel en-boy)** | En büyük/en küçük λ ayırt edilemez | 3 adayın hepsini J ile test et; J farkı küçükse "belirsiz" uyarısı |
| **Mükemmel küre** | Sonsuz simetri ekseni | Tespit et (tüm ρ ~eşit); kullanıcıya "küre — eksen tanımsız" bildir |
| **Eksen orijinden geçmiyor** | Off-center STL | Centroid merkezleme + Aşama 3 konum rafinasyonu |
| **Asimetrik özellikler** (boss, flat, delik) | Montaj çıkıntıları ρ varyansını şişirir | J hesabında robust istatistik (percentile-trimmed variance); outlier maskeleme |
| **Eğik (tilted) STL** | Eksen hiçbir koordinat eksenine paralel değil | PCA zaten yönü bulur; sorun değil |
| **İçi boş / kabuk mesh** | İç+dış yüzey iki ρ değeri | Kesitte yüksek-percentile (dış yüzey) al (§B.4) |

**Anahtar ilke:** Simetri ne kadar yüksekse eksen yönü o kadar belirsizleşir.
Bu yüzden bir **güven skoru** (J_min + λ-dejenerasyon) döndürülür ve düşükse UI
kullanıcıyı uyarır / manuel eksen seçimine izin verir. Sessizce yanlış eksen
seçmek YASAK.

### B.4 — STL → Radius(z): en güvenilir yöntem

Eksen bulunup bulut eksen çerçevesine döndürüldükten sonra (z' = eksen
projeksiyonu, ρ = dik uzaklık):

```
Her z'-dilimi için:
  ❌ max(ρ)   : tek aykırı vertex'e aşırı duyarlı (mevcut naif yöntem)
  ❌ mean(ρ)  : seyrek/iç vertex'lerle yüzeyi olduğundan küçük gösterir
  ✅ p95(ρ)   : YÜKSEK PERCENTILE — dış yüzeyi yakalar, outlier'a dayanıklı
```

Adımlar (önerilen, en güvenilir):
1. **Örtüşen binleme:** `bin_half ≈ 1.5 × spacing` → süreklilik, boş bin azalır.
2. **Robust yarıçap:** her binde `ρ`'nun p90–p95 değeri (kabuk/iç vertex'i ele).
3. **Düzleştirme:** moving-median veya Savitzky-Golay → faset gürültüsünü temizle
   (numpy ile uygulanabilir; scipy gerekmez).
4. **Boş bin doldurma:** komşulardan `np.interp`.
5. **Kutup işleme:** dom uçlarında `ρ→0`, vertex seyrek; min yarıçap kenetle
   (`r ≥ r_max·ε`), tam 0 yapma (Clairaut tekilliğini önler).
6. **Tek-değerlilik kontrolü:** profil z'de tek-değerli mi? (torus/re-entrant
   şekil → body-of-revolution değil → reddet + uyar).
Çıktı: düzgün, monoton-olmayan ama tek-değerli `r(z)` → mevcut `MandrelProfile`.

> Tasarım ilkesi: çıktı **aynı `MandrelProfile` dataclass'ı**. Downstream
> (`path_generator`, `winding_twin`, `coverage_solver`) hiç etkilenmez — sadece
> profilin nasıl türetildiği iyileşir.

### B.5 — Dome + silindir birlikte nasıl desteklenir?

**Önemli içgörü:** `r(z)` eğrisi dome'u (uçlarda r azalır) ve silindiri
(ortada r sabit) **tek bir eğride doğal olarak** taşır — kesit çıkarımında özel
durum gerekmez. Dome desteği bir geometri problemi değil, bir **bölge
sınıflandırma + sarma** problemidir:

1. **Bölge sınıflandırma (türevle):** `dr/dz ≈ 0` → silindirik gövde;
   `|dr/dz|` büyük → dome. Bu segmentasyon yalnız bilgi/görsel içindir.
2. **Dome'da sarma (zaten backend'de var):** Clairaut sabiti `c = r_max·sin(α)`
   ve `find_turnaround_z_left/right` (`path_generator.py:336-374`) fiberin
   `r(z) ≥ c` olduğu bölgede kalmasını, dome'da (r<c) dönmesini zaten sağlıyor.
   Yani radius(z) doğruysa dome winding **otomatik** çalışır.
3. **Kutup deliği (polar opening):** dome tepesinde `r = c` → fiber buraya teğet
   sarılır; `c`'yi kutup açıklığı yarıçapına göre seçmek kutup deliğini belirler.
   (Gelecek faz: kutup takviyesi.)
4. **Görsel:** dome bölgesi profile mesh'inde otomatik konik/küresel görünür;
   ribbon renderer yüzey normalini `fiber_contact_model._surface_normal_unit`
   ile alır → dome'da da doğru oturur.

**Sonuç:** Dome+silindir için ekstra geometri motoru gerekmez. Tek gereksinim:
güvenilir `r(z)` (B.4) + zaten var olan Clairaut turnaround mantığı.

---

## C. UI Dönüşüm Planı

> Dosya: `faz17_d2/.../app/panels/entegre_tasarim_paneli.py` + yeni `app/cam_engine.py`

### C.1 — KALDIRILACAK UI kodları

| Kod birimi | Gerekçe |
|---|---|
| `_setup_animation(path, profile)` — naif `cos/sin` önhesabı | Yerini `simulate_winding` çıktısı alır |
| `_anim_xyz`, `_anim_a_deg`, `_anim_x_mm` attribute'ları | `self._twin: TwinSimulationResult` ile değişir |
| `_anim_tick` içindeki ham açı/x sürüşü | TwinState index'i ile değişir |
| Fiber büyümesinin nokta-indeks mantığı | `fiber_deposited_mm` / `current_radius_mm` tabanlı olur |

### C.2 — KORUNACAK UI kodları (veri kaynağı değişir, iskelet aynı)

| Kod birimi | Korunma nedeni |
|---|---|
| `_anim_timer` (QTimer 30 FPS) | Oynatım saati aynı |
| `_btn_play/_pause/_stop_anim/_reset_anim`, `_anim_slider`, `_cb_speed`, `_anim_lbl` | Kontroller aynı; etiket formatı güncellenir |
| `_on_anim_play/_pause/_stop/_reset/_seek` | İskelet korunur; iç veri kaynağı TwinState |
| `_MachineGLView._rebuild_scene`, `_build_static_frame`, `_build_carriage_at_zero` | Makine geometrisi aynı |
| `_MachineGLView.set_simulation_state` / `clear_simulation_state` | Metot korunur; **imza genişler** (eye + radius) |
| `update_fiber_paths` (statik hesap-sonrası render) | Hızlı önizleme için kalır |
| R1/R2/R3/R4 altyapısı (`_collect_params`, worker, watchdog, preflight) | Thread-güvenliği ve donma koruması aynen geçerli |

### C.3 — EKLENECEK UI birimleri

| Yeni birim | Kaynak | Sprint |
|---|---|---|
| `app/cam_engine.py` facade (`CamRequest`, `compute_twin/coverage/gcode`, `load_mandrel`) | Backend sarmalayıcı | S1 |
| Payout eye mesh (carriage ucu) | `eye_x_mm`, `eye_r_mm` | S3 |
| `set_simulation_state` genişletilmiş imza (spindle, carriage_actual, eye, radius, fiber) | TwinState | S3 |
| RibbonRenderer (GLMeshItem) | layered paths + FiberBand + yüzey normali | S4 |
| CoverageHeatmapRenderer | `CoverageMap` | S5 |
| ShellRenderer (kalınlık/büyüme) | `current_radius_mm` + `DepositionMap.thickness_mm` | S6 |

### C.4 — Yeni veri akışı

```
"Simüle Et" → _collect_params() (R1: ana thread, düz veri)
            → QThread worker: cam_engine.compute_twin(req)
                 → preflight_check_stack(...)   (R3: donma koruması)
                 → simulate_winding(profile, band, params, n_layers, dt_s)
            → finished(TwinSimulationResult) → self._twin = result
_anim_tick (30 FPS):
   t += dt_play · speed
   st = self._twin.state_at(t)
   gl.set_simulation_state(st.spindle_angle_deg, st.carriage_x_actual_mm,
                           st.eye_x_mm, st.eye_r_mm, st.current_radius_mm,
                           st.fiber_deposited_mm)
   _anim_lbl ← f"Katman {st.current_layer} · %{st.progress_pct:.0f}"
   bitiş → coverage heatmap + thickness shell
```

---

## D. Risk Analizi

| # | Risk | Etki | Azaltma |
|---|---|---|---|
| D1 | `states[]` patlaması (100 katman × küçük dt_s) | Bellek/FPS | `dt_s` ölçekle; `deposition_grid` düşür; R3 preflight `compute_twin`'e de |
| D2 | `set_simulation_state` imza değişimi mevcut çağıranları kırar | Regresyon | `update_fiber_paths` statik yolunu koru; yeni paramlar default'lu |
| D3 | İki "gerçek" (saf `generate_path` büyümesiz vs `winding_twin` büyümeli) | Tutarsız çap | UI kaynağı **yalnız** `winding_twin`; `generate_path` sadece G-code yolu |
| D4 | STL ekseni yanlış (simetrik/dejenere gövde) | Yanlış profil | Güven skoru + UI uyarı + manuel eksen seçeneği; sessiz yanlış YASAK |
| D5 | STL kutup tekilliği (`r→0`) | Clairaut `c/√(r²−c²)` patlar | Min yarıçap kenetle; turnaround mantığı zaten r≥c koruyor |
| D6 | Ribbon mesh çokgen patlaması | FPS | LOD: ≤8 katman tam ribbon, üstü seçili katman; diğerleri çizgi |
| D7 | Determinizm kaybı | Replay/test kırılır | Oynatım hızı yalnız görsel `t`'yi ölçekler; simülasyon `dt_s` sabit |
| D8 | scipy yokluğu | STL rafinasyon optimizasyonu | numpy-only coarse grid descent (scipy gerektirmez) |
| D9 | `winding_twin` çağrı süresi (UI donması) | UX | Zaten QThread worker + watchdog (R2/R4) içinde |

---

## E. Sprint Kırılımı

| Sprint | Başlık | Kapsam | Kabul kriteri |
|---|---|---|---|
| **S1** | CAM Facade + envanter + entegrasyon planı | `app/cam_engine.py` (`CamRequest`, `compute_twin/coverage/gcode`, `load_mandrel`); UI yalnız facade'a konuşur; bu belge onaylı | Mevcut UI çalışır; tüm backend çağrıları tek dosyada; regresyon 8/8 + R3 12/12 korunur |
| **S2** | **STL Axis Solver (EN YÜKSEK ÖNCELİK)** | `core/stl_axis_solver.py`: PCA tohum + simetri-kriteri seçim + numpy coarse-grid rafinasyon; robust p95 `radius(z)`; güven skoru; `from_stl` bunu kullanır | Eğik + kısa-şişman + dome'lu test STL'leri doğru `r(z)`; dejenere gövdede uyarı; birim testi |
| **S3** | TwinState tabanlı gerçek simülasyon | §C.1 naif animasyon kaldır; §A.2 eşlemesini bağla; `set_simulation_state` genişlet (spindle/carriage_actual/eye/radius/fiber) | Mandrel `spindle_angle_deg`, carriage `carriage_x_actual_mm`, eye görünür, çap büyür; donma yok |
| **S4** | Ribbon / Tow Mesh Renderer | `GLLinePlotItem`→`GLMeshItem` şerit; fiziksel tow width+thickness; yüzey normali (`fiber_contact_model`); D6 LOD | Tow genişliği kamera zoom'undan bağımsız fiziksel; FPS ≥ kabul |
| **S5** | Coverage Heatmap | `solve_coverage`→mandrel yüzeyine BGYR; overlap (`count≥2`) + gap (`count==0`) | Z-θ heatmap + overlap/gap görünür |
| **S6** | Layer Growth Visualization | `current_radius_mm` + `DepositionMap.thickness_mm`→büyüyen kabuk/shell; kalınlık topografyası | Katman ekledikçe çap+kalınlık görsel büyür; thickness grid render |

---

## Onay

Bu belge onaylanırsa kodlama **S1 (cam_engine facade)** ile başlar; ardından
**S2 STL Axis Solver** (en yüksek öncelik). Onaya kadar kod yazılmaz.

**İlke:** *Çizgi animasyonu yapan CAM değil — gerçek filament winding dijital
ikizi. Beyin (backend motorları) hazır; göz (STL Axis Solver) ve kas (TwinState
render) takılıyor.*
