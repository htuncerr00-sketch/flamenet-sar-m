# STL Intelligence Layer — Onay Öncesi Tasarım Belgesi (S1)

> **Durum:** Tasarım/onay belgesi — kod YOK, implementasyon YOK.
> **Tarih:** 2026-06-16 · **Branch:** `claude/amazing-feynman-XUXBf`
> **Zincir:** STL → **Mandrel Intelligence** → Digital Twin
> **Karar:** Yeni backend yazılmayacak; mevcutlar tüketilecek. STL Intelligence
> tek gerçek yeni katman (geometriyi *anlayan* göz). cam_engine facade SONRA.

İlk geliştirme paketi (6 bileşen):
1. STL Axis Solver · 2. STL Radius(z) Extractor · 3. STL Confidence Score ·
4. STL Quality Analyzer · 5. Dome/Cylinder Segmentation · 6. Turnaround Candidate Detection

Bölümler: **A.** Veri akışı · **B.** Algoritmalar · **C.** Güven skorları ·
**D.** Başarısızlık senaryoları · **E.** Test STL senaryoları.

---

## 0. Modül Yapısı ve Çıktı Sözleşmesi

> **Tasarım ilkesi:** Nihai çıktı yine `geometry_engine.MandrelProfile` olacak;
> downstream (`path_generator`, `winding_twin`, `coverage_solver`) hiç değişmez.
> STL Intelligence sadece profilin *nasıl türetildiğini* akıllandırır ve yanına
> bir **analiz raporu** ekler.

Önerilen yeni dosya: `core/stl_intelligence.py` (tek modül; `stl_processor.py`'yi
**yeniden kullanır**, yeniden yazmaz).

Veri yapıları (kavramsal — kod değil):
```
AxisResult           : axis_unit(3,), center(3,), J_min, eig_ratios, degeneracy_flag
RadiusProfile        : z_mm(M,), r_mm(M,), n_per_bin(M,), pole_clamped(bool)
RegionSegmentation   : spans=[(z0,z1,"cylinder"|"dome"|"taper")], transitions=[z...]
TurnaroundCandidates : per_alpha={alpha: (z_left, z_right, polar_radius_c)}
ConfidenceReport     : axis, symmetry, mesh_density, radial_fit, aggregate(0..1), grade
QualityReport        : aspect_ratio, min_radius, is_single_valued, asymmetry_mm,
                       degenerate_tri_count, winding_suitable(bool), reasons[]
StlIntelligenceReport: hepsini taşır + MandrelProfile + human_summary(str, TR)
```

Orkestratör (tek giriş):
```
analyze_stl(path) -> StlIntelligenceReport
```

---

## A. Veri Akışı

```
[STL dosyası]
   │  parse_stl_vertices()  (stl_processor.py — MEVCUT, yeniden kullanılır)
   ▼
V : (N,3) vertex bulutu  ──────────────────────────────────────────────┐
   │                                                                    │
   │  (1) STL AXIS SOLVER                                               │
   │      centroid merkezle → kovaryans → eigh → 3 aday                 │
   │      simetri-maliyeti J(a) → en iyi eksen → grid rafinasyon        │
   ▼                                                                    │
AxisResult{axis_unit, center, J_min, eig_ratios, degeneracy}           │
   │                                                                    │
   │  V'yi eksen çerçevesine döndür:                                    │
   │     z' = (V−C)·a ,  ρ = ‖(V−C) − z'·a‖ ,  θ = atan2(...)           │
   ▼                                                                    │
(z', ρ, θ) silindirik koordinatlar                                     │
   │                                                                    │
   │  (2) RADIUS(z) EXTRACTOR                                           │
   │      örtüşen z-bin → bin başına p95(ρ) → düzleştir → boş doldur    │
   │      → kutup kenetle → tek-değerlilik kontrolü                     │
   ▼                                                                    │
RadiusProfile{z_mm, r_mm, n_per_bin}                                   │
   │                                                                    │
   ├─(5) DOME/CYLINDER SEGMENTATION                                     │
   │      dr/dz (düzleştirilmiş) → |dr/dz|<ε ⇒ silindir, değilse dome   │
   │      → bitişik bölge grupla → transition (diz) noktaları           │
   │      ▼ RegionSegmentation                                          │
   │                                                                    │
   ├─(6) TURNAROUND CANDIDATE DETECTION                                 │
   │      aday α'lar için c=r_max·sin(α)                                │
   │      find_turnaround_z_left/right (path_generator — MEVCUT)        │
   │      → z_left, z_right, polar_radius=c                             │
   │      ▼ TurnaroundCandidates                                        │
   │                                                                    │
   ├─(4) QUALITY ANALYZER ◄───────────────────────────────────────────┘
   │      aspect L/D, min radius, tek-değerlilik, asimetri,
   │      dejenere üçgen, winding uygunluğu
   │      ▼ QualityReport
   │
   └─(3) CONFIDENCE SCORE  (eksen + simetri + mesh yoğunluğu + radyal fit)
          ▼ ConfidenceReport{aggregate, grade}
   │
   ▼
StlIntelligenceReport  →  MandrelProfile (downstream)  +  UI rapor paneli
```

**Akış kuralı:** Her aşama bir sonrakine **yapı** verir; Quality + Confidence
yatay olarak tüm aşamaların metriklerini toplar. Eksen düşük güvenliyse akış
durmaz ama rapor **"düşük güven — manuel eksen öner"** bayrağıyla işaretlenir
(sessiz yanlış sonuç YASAK).

---

## B. Kullanılacak Algoritmalar (6 Bileşen)

### B.1 — STL Axis Solver

```
Girdi : V (N,3)
1. C = mean(V, axis=0);  X = V − C
2. M = Xᵀ X / N                      (3×3 kovaryans)
3. λ, E = numpy.linalg.eigh(M)       (λ artan; E sütunları özvektör)
4. Adaylar: a ∈ {E[:,0], E[:,1], E[:,2]}
5. Her aday için SİMETRİ MALİYETİ:
      z' = X·a ;  ρ = ‖X − outer(z',a)‖   (eksene dik uzaklık)
      z'-yi K bine böl (K≈80); her binde robust Var(ρ)  [p10–p90 trimmed]
      J(a) = Σ_bin Var_trim(ρ) / mean(ρ)²    (boyutsuz, ölçek-bağımsız)
6. a* = argmin J(a)                  ← EN BÜYÜK λ DEĞİL, en düşük J
7. RAFİNASYON (numpy-only, scipy yok):
      a* etrafında küçük açısal ızgara (±5°, kaba 1° → ince 0.1°)
      her komşu yön için J hesapla, J azaldıkça in → yakınsa kilitle
8. Dejenerasyon: d = (λ2−λ1)/(λ3+ε) ve (λ3−λ2)/(λ3+ε)
      iki oran da küçükse (~küresel) ⇒ degeneracy_flag = True
Çıktı : AxisResult{a*, C, J_min, eig_ratios, degeneracy_flag}
```
**Neden bu yöntem:** PCA hızlı tohum verir ama "en büyük λ = eksen" kısa-şişman
gövdede yanlıştır; J(a) doğrudan *body-of-revolution* tanımını (her z'de eşit
yarıçap) ölçer → 3 adayı objektif kıyaslar. Rafinasyon scipy gerektirmez.

### B.2 — STL Radius(z) Extractor

```
Girdi : X=V−C, a* (eksen)
1. z' = X·a* ;  ρ = ‖X − outer(z',a*)‖
2. z aralığı [z'min, z'max] → M eşit bin (M≈300)
3. ÖRTÜŞEN binleme: bin_half = 1.5 × (binaralığı)  → süreklilik
4. Her bin merkezi z_c:
      slice = ρ[ |z'−z_c| ≤ bin_half ]
      r[c] = percentile(slice, 95)        ← p95: dış yüzey, outlier'a dayanıklı
      n_per_bin[c] = len(slice)
5. DÜZLEŞTİRME: moving-median (pencere ~5) → faset gürültüsü temizlenir
6. BOŞ BİN: n_per_bin==0 olanlar np.interp ile komşulardan
7. KUTUP KENETLEME: r = max(r, r_max·ε) (ε≈0.01) → r=0 tekilliği önle
8. TEK-DEĞERLİLİK: z' monoton bin merkezleri; r çok-değerli mi? (torus testi)
      aynı z'de iki ayrı ρ kümesi (iç+dış kabuk değil, gerçek re-entrant) ⇒ flag
Çıktı : RadiusProfile{z_mm=z_c, r_mm=r, n_per_bin, pole_clamped}
```
**Neden p95:** `max(ρ)` tek aykırı vertex'e aşırı duyarlı; `mean(ρ)` seyrek
mesh'te yüzeyi küçük gösterir. p95 dış yüzeyi yakalar, gürültüye dayanıklıdır.

### B.3 — STL Confidence Score (bkz. §C — skorlama orada)

Dört alt-skoru toplar: `axis`, `symmetry`, `mesh_density`, `radial_fit`.

### B.4 — STL Quality Analyzer

```
Girdi : V, RadiusProfile, AxisResult
Kontroller:
  • aspect_ratio = L / (2·r_max)               (çok küçük/çok büyük uyarı)
  • min_radius   = min(r_mm)                    (kutup deliği < eşik ⇒ winding zor)
  • is_single_valued (B.2'den)                 (False ⇒ body-of-revolution değil)
  • asymmetry_mm = max_bin( p95(ρ) − p50(ρ) )  (yüksek ⇒ asimetrik/eğri)
  • degenerate_tri_count: sıfır-alanlı üçgen / NaN normal sayısı
  • mesh_density: ortalama n_per_bin           (düşük ⇒ kaba mesh)
  • vertex_count: çok düşükse reddet
Verdict:
  winding_suitable = (is_single_valued) AND (min_radius ≥ eşik)
                     AND (asymmetry düşük) AND (mesh yeterli)
  reasons[] : başarısız her kriter için TR açıklama
Çıktı : QualityReport
```

### B.5 — Dome / Cylinder Segmentation

```
Girdi : RadiusProfile
1. dr/dz = np.gradient(r_mm, z_mm) ; düzleştir (moving-median)
2. Sınıflandır (her bin):
      |dr/dz| < ε_cyl (≈0.02)        → "cylinder"
      dr/dz büyük & profil ucuna yakın → "dome"
      ara                            → "taper" (konik geçiş)
3. Bitişik aynı-etiket binleri tek SPAN olarak grupla
4. TRANSITION (diz) noktaları: etiket değişim z'leri
      + eğrilik tepe noktaları (d²r/dz² ekstremumları)
Çıktı : RegionSegmentation{spans[(z0,z1,type)], transitions[z...]}
```
**Not:** Bu segmentasyon **bilgi + görsel** içindir (dome'u UI'da işaretlemek).
Sarma fiziği için zorunlu değil — Clairaut turnaround (B.6) zaten r(z)'den çalışır.

### B.6 — Turnaround Candidate Detection

```
Girdi : RadiusProfile (→ geçici MandrelProfile), aday α listesi
Her α için:
  c = r_max · sin(α)                              (Clairaut sabiti)
  c = min(c, r_max·0.995)                          (tekillik koruması)
  z_left  = find_turnaround_z_left(profile, c)     (path_generator — MEVCUT)
  z_right = find_turnaround_z_right(profile, c)     (path_generator — MEVCUT)
  polar_radius = c                                  (kutup açıklığı yarıçapı)
  reachable = (z_right > z_left)                    (α bu gövdede sarılabilir mi?)
Çıktı : TurnaroundCandidates{α: (z_left, z_right, polar_radius, reachable)}
```
**Anlamı:** Fiber geodezik olarak `r(z) ≥ c` bölgesinde kalır; dome'da (r<c)
döner. Bu, hangi α'ların gövdeye uyduğunu ve kutup deliği yarıçapını önceden
gösterir → UI kullanıcıya "bu α için sarılabilir bölge: [z_left, z_right]" der.

---

## C. Güven Skorları

> Hepsi 0..1; 1 = mükemmel. Sessiz yanlış sonucu önlemek için **agregat eşiği**
> altında UI uyarı verir / manuel eksen ister.

### C.1 — Alt skorlar

| Skor | Formül (kavramsal) | Yüksek = |
|---|---|---|
| `axis` | `1 − clip(J_min / J_ref)` ; dejenerasyonda ceza | Eksen net |
| `symmetry` | `1 − mean_bin( (p95(ρ)−p50(ρ)) / p50(ρ) )` | Dönel simetrik |
| `mesh_density` | `clip(mean(n_per_bin) / n_target)` | Yoğun mesh |
| `radial_fit` | `1 − RMS(r − smooth(r)) / mean(r)` | Pürüzsüz profil |

### C.2 — Agregat ve sınıf

```
aggregate = w1·axis + w2·symmetry + w3·mesh_density + w4·radial_fit
            (örn. w = 0.40, 0.30, 0.15, 0.15)
grade:
   aggregate ≥ 0.85  → "YÜKSEK"   (otomatik kabul)
   0.65–0.85         → "ORTA"     (kabul + uyarı: kontrol et)
   0.45–0.65         → "DÜŞÜK"    (manuel eksen öner, profil şüpheli)
   < 0.45            → "REDDET"   (winding için uygun değil)
ek kural: degeneracy_flag=True  ⇒ grade en fazla "DÜŞÜK"
          is_single_valued=False ⇒ grade = "REDDET"
```

### C.3 — Rapor çıktısı (UI'a)
`ConfidenceReport.human_summary` (TR): örn.
*"Eksen güveni %92, simetri %88, mesh yoğunluğu orta. Genel: YÜKSEK. Profil
winding için uygun. Kutup yarıçapı α=15° için 13.0 mm."*

---

## D. Başarısızlık Senaryoları

| # | Senaryo | Belirti | Tespit | Yanıt |
|---|---|---|---|---|
| F1 | **Mükemmel küre** | Eksen tanımsız (∞ simetri ekseni) | `degeneracy_flag` + tüm ρ ~eşit | grade=REDDET; "küre — dönme ekseni tanımsız" |
| F2 | **Kısa-şişman gövde** (D>L) | PCA en-büyük-λ yanlış eksen verir | J(a) 3 adayı kıyaslar, doğruyu seçer | Otomatik düzeltilir; J farkı küçükse ORTA uyarı |
| F3 | **Torus / re-entrant** | r(z) çok-değerli | tek-değerlilik kontrolü (B.2-8) | `is_single_valued=False` ⇒ REDDET |
| F4 | **Off-center eksen** | √(x²+y²) yanlış (orijin dışı) | centroid merkezleme + rafinasyon | Otomatik düzeltilir |
| F5 | **Eğik (tilted) STL** | Hiçbir koordinat eksenine paralel değil | PCA yönü bulur | Otomatik düzeltilir (sorun değil) |
| F6 | **Seyrek mesh** | Bin başına az vertex, boşluklar | `n_per_bin` düşük | `mesh_density` düşer; boş bin interp; ORTA/DÜŞÜK uyarı |
| F7 | **Asimetrik çıkıntı** (boss/flat) | ρ varyansı şişer | trimmed variance + `asymmetry_mm` | Robust istatistik maskeleler; yüksekse uyarı |
| F8 | **Dejenere/bozuk üçgen** | Sıfır-alan, NaN normal | `degenerate_tri_count` | Sayılır; vertex'ler yine de kullanılır; raporlanır |
| F9 | **Yanlış birim/ölçek** (m/inç) | r_max anormal (çok küçük/büyük) | aspect + mutlak boyut sağduyu kontrolü | Uyarı: "birim şüpheli — mm mi?"; ölçek önerisi |
| F10 | **Kutup tekilliği** (r→0) | Clairaut `c/√(r²−c²)` patlar | min_radius eşiği + kutup kenetleme | r kenetlenir; turnaround zaten r≥c korur |
| F11 | **Çok-gövdeli STL** (2 ayrı parça) | İki vertex kümesi | z'-bin dağılımında boşluk/çift mod | Uyarı: "birden fazla gövde algılandı"; en büyüğü seç |
| F12 | **Açık kabuk (non-watertight)** | İç+dış yüzey ρ ikiliği | p95 dış yüzeyi alır | Genelde tolere; aşırıysa quality uyarı |
| F13 | **Boş/bozuk dosya** | parse 0 vertex | `vertex_count` kontrolü | REDDET; "STL okunamadı / boş" |

**Genel ilke:** Hiçbir başarısızlık sessiz geçmez. Her senaryo ya otomatik
düzeltilir (F2,F4,F5) ya da `ConfidenceReport`/`QualityReport` üzerinden
kullanıcıya açık TR mesajla bildirilir.

---

## E. Test STL Senaryoları

> Gerçek `.stl` dosyaları gerekmez — testler **sentetik vertex bulutları**
> üretebilir (dönel yüzey örnekleme) ve hem ASCII hem binary serileştirmeyi
> doğrular. Determinizm: sabit seed.

| # | Test geometrisi | Üretim | Beklenen çıktı |
|---|---|---|---|
| T1 | **Eksen-hizalı silindir** (Z) | r=const, z∈[0,L] | axis≈Z, r(z)≈const, grade=YÜKSEK, segment=tek "cylinder" |
| T2 | **Eğik silindir** (rastgele R döndür) | T1'i rastgele rotasyonla | axis = döndürülmüş eksen, r(z) T1 ile aynı, grade=YÜKSEK |
| T3 | **Off-center silindir** | T1 + sabit kaydırma | center kaydırmayı yakalar, r(z) doğru |
| T4 | **Dome-silindir-dome** | analitik hemisferik uçlar + gövde | segment=[dome,cylinder,dome], transitions doğru z'de |
| T5 | **Konik (taper)** | r doğrusal artar | segment="taper", r(z) doğrusal, grade=YÜKSEK |
| T6 | **Kısa-şişman silindir** (D>L) | r büyük, L küçük | **F2 testi**: J doğru ekseni seçer (en büyük λ değil) |
| T7 | **Küre** | r=√(R²−z²) tam küre | **F1 testi**: degeneracy_flag, grade=REDDET |
| T8 | **Torus** | re-entrant profil | **F3 testi**: is_single_valued=False, REDDET |
| T9 | **Seyrek mesh silindir** | T1 ama çok az vertex | **F6 testi**: mesh_density düşük, boş bin interp, grade≤ORTA |
| T10 | **Asimetrik bosslu silindir** | T1 + yan çıkıntı | **F7 testi**: asymmetry_mm yüksek, robust p95 gövdeyi korur |
| T11 | **ASCII vs binary aynı gövde** | T4'ü iki formatta yaz | İki parse **aynı** vertex → aynı profil (byte-tutarlı) |
| T12 | **Elipsoidal dome** | dome_hr_ratio≠1 | r(z) elips, segment dome, turnaround α'ları makul |
| T13 | **Turnaround kapsama** | T4 üzerinde α∈{10,30,55,80} | her α için z_left/z_right; düşük α daha geniş erişim; polar_radius=c |
| T14 | **Yanlış ölçek** (m cinsi, r=0.05) | T1 metre birimiyle | **F9 testi**: birim şüphesi uyarısı |

**Kabul kriteri (S2 implementasyonu için):** T1–T5,T11–T13 grade=YÜKSEK ve
profil hatası < %2; T6 doğru eksen; T7,T8,T14 doğru uyarı/RED; tüm testler
deterministik (sabit seed → bit-aynı).

---

## Onay

Bu rapor onaylanırsa **STL Intelligence Layer** implementasyonu (`core/stl_intelligence.py`)
6 bileşenle başlar; `stl_processor.parse_stl_vertices` ve `path_generator`
turnaround fonksiyonları **yeniden kullanılır**, çıktı `MandrelProfile`
**downstream-uyumlu** kalır. cam_engine facade ve TwinState render sonra gelir.

**Zincir:** STL → **Mandrel Intelligence** (bu katman) → Digital Twin.
**İlke:** Sistem STL yüklenince ekseni *bulur*, geometriyi *anlar*, winding
uygunluğunu *raporlar* — sessiz yanlış sonuç üretmez.
