# STL Intelligence → Winding Çekirdeği Entegrasyon Raporu

> **Durum:** Mimari entegrasyon raporu — kod YOK, implementasyon YOK.
> **Tarih:** 2026-06-16 · **Branch:** `claude/amazing-feynman-XUXBf`
> **Zincir:** STL → **Mandrel Intelligence** → Digital Twin
> **Amaç:** STL Intelligence çıktılarının mevcut winding çekirdeğiyle nasıl
> birleşeceğini, nerede veri kaybı olduğunu ve en temiz çekirdek nesne mimarisini
> netleştirmek.

Sorular: 1) MandrelProfile yeterli mi · 2) Veri kaybı · 3) Geodesic entegrasyonu ·
4) Coverage entegrasyonu (sayısal) · 5) TwinState entegrasyonu · 6) Renderer ·
7) Mimari seçenekler · 8) Nihai öneri.

---

## 1. MandrelProfile Yeterli mi?

### Mevcut yapı (`geometry_engine.py:16-24`)
```
MandrelProfile:
    z_mm: np.ndarray          # eksenel koordinatlar
    r_mm: np.ndarray          # her z'deki yarıçap
    # + metotlar: radius_at, perimeter_at, arc_length
    # + property: length_mm, max/min/avg_radius_mm
```
Yani MandrelProfile **yalnızca `(z, r)` eğrisidir.** Eksen yok (Z varsayılı),
segment yok, kutup yok, turnaround yok, güven yok.

### STL Intelligence çıktıları vs MandrelProfile kapasitesi

| STL Intelligence çıktısı | MandrelProfile taşıyabilir mi? | Not |
|---|---|---|
| `radius(z)` | ✅ Evet (`z_mm`, `r_mm`) | Tam uyum — zaten bunun için var |
| `axis` (yön + merkez) | ❌ Hayır | Profil eksen-çerçevesinde *türetilmiş*; eksenin kendisi kayıp |
| `cylinder segments` | ❌ Hayır | Segment etiketi alanı yok |
| `dome segments` | ❌ Hayır | — |
| `pole regions` | ❌ Hayır | min radius property var ama kutup *bölgesi* (z-aralığı) yok |
| `turnaround candidates` | ⚠️ Türetilebilir ama saklanmaz | `find_turnaround` ile her seferinde *yeniden* hesaplanır (cache yok) |
| `confidence score` | ❌ Hayır | — |
| `quality report` | ❌ Hayır | — |

### Karar
MandrelProfile **`radius(z)` için yeterli, ama STL zekâsının geri kalanı için
yetersiz.** İki yol var:
- **Eklenebilecek (minimal) alanlar:** opsiyonel `axis_unit`, `axis_center`,
  `source` ("stl"/"parametric"). Bunlar profilin *kökenini* taşır.
- **AYRI yapıda tutulması gerekenler:** `segments`, `pole_regions`,
  `turnaround_candidates`, `confidence`, `quality`. Bunlar profilin *yorumudur*,
  geometrinin kendisi değil → MandrelProfile'ı şişirir, downstream'i kirletir.

> **İlke:** MandrelProfile = saf geometri primitifi (downstream sözleşmesi).
> Zekâ (yorum/analiz) ayrı yapıda. (§7-8'de bunu `MandrelModel` ile çözüyoruz.)

---

## 2. Veri Kaybı Analizi

Mevcut boru hattında STL'den çıkan zenginlik **MandrelProfile darboğazında**
kayboluyor. İz sürümü:

```
STL analizi (zengin)
   │  axis, segments, poles, turnaround, confidence, quality
   ▼
MandrelProfile (z_mm, r_mm)        ◄── DARBOĞAZ: yalnız (z,r) geçer
   │
   ├─► path_generator  ──► axis YOK (Z varsayar), segment YOK
   ├─► coverage_solver ──► confidence YOK (kör güvenir)
   ├─► winding_twin    ──► dome/pole YOK (turnaround'u yeniden hesaplar)
   └─► UI render       ──► quality/confidence YOK (kullanıcı uyarılmaz)
```

| Bilgi | Nerede üretilir | Nerede kaybolur | Sonuç |
|---|---|---|---|
| **dome bilgisi** | STL segmentation | MandrelProfile'a girmez | Dome görsel işaretlenemez; turnaround her modülde yeniden türetilir |
| **pole bilgisi** | STL segmentation | MandrelProfile'a girmez | Kutup tekilliği (r→0) downstream'de sürpriz; UI kutbu gösteremez |
| **quality bilgisi** | Quality Analyzer | Profil'e girmez | "Bu STL winding'e uygun değil" uyarısı kaybolur → kötü STL sessizce işlenir |
| **confidence bilgisi** | Confidence Score | Profil'e girmez | coverage/twin yanlış profile **kör güvenir** → §4'teki sessiz hatalar |
| **axis bilgisi** | Axis Solver | Profil türetilirken tüketilir, saklanmaz | 3D'de gerçek eksen çizilemez; STL yeniden hizalanamaz |

**En tehlikeli kayıp = confidence.** Çünkü düşük güvenli (yanlış) bir profil,
hiçbir downstream modül bunu bilmeden, "kesin doğru" gibi işlenir.

---

## 3. Geodesic Entegrasyonu

Mevcut geodesic çekirdek (`path_generator.py`): `_generate_helical` →
`c = r_max·sin(α)` → `find_turnaround_z_left/right` → `_geodesic_pass`
(`dθ = c/(r_mid·√(r²−c²))·dz`). Tamamı **`MandrelProfile.r_mm`'den** beslenir.

### Birleşme akışı (önerilen)
```
StlIntelligence.analyze_stl(path)
   → AxisResult + RadiusProfile + Segments + TurnaroundCandidates + Confidence
   │
   │  (a) RadiusProfile → MandrelProfile(z_mm, r_mm)   [downstream sözleşmesi]
   │  (b) confidence < eşik ?  → UI uyarı / dur (geodesic'e kör girme)
   ▼
geodesic çekirdek (DEĞİŞMEZ):
   c = r_max·sin(α)                       ← r_max artık DOĞRU (gerçek eksen)
   z_left  = find_turnaround_z_left(prof, c)
   z_right = find_turnaround_z_right(prof, c)
   _geodesic_pass(...)                    ← r(z) artık DOĞRU
```

### İki kazanç (kod değişmeden)
1. **r(z) doğruluğu:** Axis Solver doğru ekseni bulduğu için `r_mm` gerçek
   yarıçaptır → Clairaut sabiti `c` ve turnaround doğru çıkar.
2. **Turnaround önbelleği (opsiyonel):** STL Intelligence `TurnaroundCandidates`
   zaten aday α'lar için `z_left/z_right` hesapladı. Geodesic çekirdek bunları
   *yeniden* hesaplamak yerine cache'ten okuyabilir (performans + tutarlılık).
   `find_turnaround` imzası aynı kalır; sadece sonuç paylaşılır.

> Geodesic motoru **hiç değişmez.** Sadece girdisi (r(z)) doğrulanır ve
> turnaround sonucu paylaşılır. STL Intelligence geodesic'in *önüne* takılır.

---

## 4. Coverage Entegrasyonu (Sayısal)

`coverage_solver.solve_coverage` profili üç yerde kullanır
(`coverage_solver.py:210, 237, 243`):
- `r_c = interp(z_c, profile.z_mm, profile.r_mm)` — lokal yarıçap
- `dth_off = cos_a / r_c · t` — **bant açısal genişliği ∝ 1/r**
- ızgara uzanımı `z0,z1 = profile.z_mm[0..-1]` — hücre eşlemesi

Kaplama tam-açı koşulu: bir z'de tam kaplama için
`N · W / (2πr) ≥ 1  ⇒  N ≥ 2πr/W`.

### Senaryo örnek tabanı: silindir, gerçek R=50 mm, tow W=6 mm
Gerçek tam-kaplama devre sayısı `N* = 2π·50/6 = 52.4 → 53`.

#### (a) Yanlış radius(z) — %10 YÜKSEK (r'=55)
- Path generator devre sayısı: `N = 2π·55/6 = 57.6 → 58` (5 fazla devre).
- Gerçek mandrelde (R=50) 58 devre × 6 mm: açısal kaplama
  `58·6/(2π·50) = 1.108 → %110.8` → **overlap, ~%10 malzeme israfı**, kalınlık
  hatası (fazla katman birikir).
- Coverage solver kendi içinde r'=55 ile tutarlı çalışır → "tam kaplama" der;
  gerçekteki overlap'ı *fazla kalınlık* olarak değil "tamam" olarak raporlar.

#### (b) Yanlış radius(z) — %10 DÜŞÜK (r'=45)  ← EN TEHLİKELİ
- Path generator: `N = 2π·45/6 = 47.1 → 48` devre (5 EKSİK).
- Gerçek mandrelde (R=50): `48·6/(2π·50) = 0.917 → %91.7` → **%8.3 kuru bant
  boşluğu** (yapısal kusur, sızdırma riski).
- AMA solver r'=45 ile hesaplar: `48·6/(2π·45) = 1.019 → %102` → **"tam kaplama
  ✓" raporlar.** Gerçekte boşluk var. **Sessiz yapısal kusur** — confidence
  skoru olmadan yakalanamaz.

#### (c) Yanlış eksen — 5° eğim
- Eğik eksenden ölçülen ρ, dilim içinde saçılır; p95 dış değeri seçer →
  silindir **fıçı (barrel)** gibi görünür: uçlarda r şişer
  (`√(R² + (L/2·sinβ)²)`; R=50, L/2=150, β=5° → `√(2500+170.8)=51.7 mm`,
  orta 50 mm). Yani sahte ~%3–4 çap dalgalanması + asimetri.
- Sonuç: `r(z)` sabit değil sanılır → orta ve uçta farklı devre yoğunluğu;
  ızgara z-uzanımı da eğik eksende yanlış → hücre eşlemesi kayar → **coverage
  haritası gerçek yüzeyle hizalanmaz** (heatmap yanıltıcı).

#### (d) Yanlış turnaround
- `c = r_max·sin(α)`. r_max %10 yüksek (55) → α=55° için `c = 55·sin55° = 45.05`
  (doğru: `50·sin55° = 40.96`).
- Daha yüksek c → turnaround içe kayar: fiber yalnız `r ≥ 45` bölgesinde sarılır;
  gerçekte `r ≥ 41` sarılabilirdi. Dome omzunda r'nin 41→45 arası geçtiği
  eksenel şerit (tipik dome'da **~10–20 mm/uç**) **boş kalır** → omuz boşluğu.
- Ters yön (c çok düşük): fiber `r < c` bölgesine komut edilir →
  `dθ = c/(r√(r²−c²))` paydası → 0 → açı patlar → fiziksel olmayan yol.

### Özet tablo (gerçek R=50, W=6)
| Hata | Komut edilen N | Gerçek kaplama | Solver raporu | Tehlike |
|---|---|---|---|---|
| r %10 yüksek | 58 | %110.8 (overlap) | "tam" | İsraf + kalınlık hatası |
| r %10 düşük | 48 | %91.7 (boşluk) | "%102 tam" | **Sessiz yapısal kusur** |
| eksen 5° | değişken | hizasız | yanıltıcı heatmap | Yanlış analiz |
| turnaround içe | — | dome omzu boş | "tam" | Omuz boşluğu |

> **Sonuç:** Coverage kalitesi doğrudan `r(z)` ve eksen doğruluğuna bağlı.
> Confidence skoru taşınmazsa, %10'luk bir radius hatası bile **"tam kaplama"
> yalanıyla** sonuçlanabilir. Bu yüzden confidence MUTLAKA pipeline'da kalmalı.

---

## 5. TwinState Entegrasyonu (Tam Tablo)

`TwinState` alanları `simulate_winding` tarafından **profil + band + params**'tan
türetilir. STL Intelligence profili (ve dolaylı turnaround/segment) beslediği için
her geometrik alan zincirleme STL'ye dayanır.

| TwinState alanı | Doğrudan kaynak | STL Intelligence katkısı | Etki gücü |
|---|---|---|---|
| `spindle_angle_deg` | Clairaut `c, r(z)` | axis + radius(z) → c doğru | Yüksek |
| `spindle_rpm` | açı/zaman türevi | radius(z) (çevre) | Orta |
| `carriage_x_mm` | yörünge x | z-uzanımı + turnaround → x aralığı | Yüksek |
| `carriage_x_actual_mm` | x − lag | yukarıdaki + hız | Yüksek |
| `carriage_v_mm_s` | x türevi | turnaround (uçta yön dönüşü) | Orta |
| `eye_x_mm` | x + lead | turnaround | Orta |
| `eye_r_mm` | `radius_at(z) + standoff` | **radius(z)** | Yüksek |
| `contact_z_mm` | x | z-uzanımı | Orta |
| `contact_r_mm` | `radius_at(z)` | **radius(z)** | Yüksek |
| `current_layer` | katman sayacı | — (paramdan) | Yok |
| `current_circuit` | devre sayacı | circuit count ∝ radius(z) | Orta |
| `fiber_deposited_mm` | yörünge yay uzunluğu | radius(z) (yay) | Yüksek |
| `current_radius_mm` | base + katman büyümesi | **radius(z) / avg_radius** | Yüksek |
| `lag_error_mm` | v·τ | dolaylı (hız) | Düşük |
| `progress_pct` | t/total | — | Yok |

**Result düzeyi:**
| TwinSimulationResult | STL Intelligence katkısı |
|---|---|
| `final_deposition (DepositionMap)` | radius(z) (taban yüzey) + dome (kalınlık dağılımı) |
| `base_radius_mm → final_radius_mm` | **radius(z).avg** taban; büyüme band+katmandan |
| `layer_time_ranges_s` | turnaround (pass uzunluğu) |

> **Özet:** `axis` ve `radius(z)` TwinState'in *tüm geometrik* alanlarını
> kökten belirler. `dome/pole/turnaround` carriage x-aralığını ve dome
> davranışını belirler. `confidence/quality` TwinState'i **beslemez** ama
> "simülasyona girmeli mi?" kapısını tutar (düşük güven → twin'e kör girme).

---

## 6. Renderer Entegrasyonu

STL Intelligence'ın her render özelliğine sağlaması gereken veri:

| Render özelliği | Gerekli STL Intelligence verisi | Neden |
|---|---|---|
| **Ribbon / Tow Mesh** | `axis` (çerçeve), `radius(z)` (oturduğu yüzey), yüzey normali (r(z) gradyanı → `fiber_contact_model`), `pole_regions` (kutupta şerit daralması) | Şerit gerçek yüzeye fiziksel genişlik/kalınlıkla otursun |
| **Thickness** | `radius(z)` (taban yarıçap), `DepositionMap` grid (z×θ), `dome/cylinder segments` (bölgesel kalınlık beklentisi) | Kalınlık topografyası taban yüzey üstüne bindirilir |
| **Coverage Heatmap** | `axis` + `radius(z)` (boyanan yüzey mesh'i), `CoverageMap`, `pole_regions` (kutupta yoğunlaşma normal) | Heatmap gerçek yüzeyle hizalı olsun (§4c hizasızlık riski) |
| **Layer Growth** | `radius(z)` taban + ply kalınlık → `r_eff = r(z) + n·ply`, `pole_regions` (kutupta büyüme sınırı) | Katman ekledikçe doğru yüzeyde büyüsün; kutupta r→0 patlamasın |

**Ortak gereksinim:** Tüm render'lar `axis` + `radius(z)`'e dayanır; bu yüzden
bu ikisi render katmanına **doğrulanmış** (confidence ≥ eşik) ulaşmalı. Düşük
güvenli profil render edilirse kullanıcı yanlış geometriyi "gerçek" sanır.

---

## 7. Mimari Seçenekler (A / B / C)

### Seçenek A — MandrelProfile'ı genişlet
> Tüm STL zekâsını (axis, segments, poles, turnaround, confidence, quality)
> MandrelProfile alanlarına ekle.

| Avantaj | Dezavantaj |
|---|---|
| Tek nesne, tek import | **Downstream sözleşmesi kirlenir** — path_generator/coverage/twin sade (z,r) bekliyor |
| Geçirmesi kolay | Parametrik profillerde (cylinder/cone) STL alanları anlamsız/boş |
| | `__post_init__` ve serileştirme karmaşıklaşır; replay/test kırılma riski |
| | "Geometri" ile "yorum" tek tipte → sorumluluk karışır (SRP ihlali) |

### Seçenek B — StlIntelligenceReport ayrı, MandrelProfile sade
> MandrelProfile değişmez; STL zekâsı bağımsız raporda durur.

| Avantaj | Dezavantaj |
|---|---|
| Downstream **hiç değişmez** (sözleşme korunur) | İki nesneyi **el ile birlikte taşımak** gerekir (profil + rapor) |
| Sorumluluk ayrımı net (geometri vs yorum) | Hangi raporun hangi profile ait olduğu gevşek bağ (eşleşme hatası riski) |
| Parametrik profil rapor üretmez — temiz | UI/twin iki ayrı parametre geçirir → imza şişer |

### Seçenek C — Yeni `MandrelModel` çekirdek nesnesi
> Kompozisyon: `MandrelModel` *içinde* sade `MandrelProfile` + `StlIntelligenceReport`
> + segments + turnaround cache barındırır.

```
MandrelModel:
    profile: MandrelProfile          # sade geometri (downstream'e verilir)
    axis: Optional[AxisResult]
    segments: Optional[RegionSegmentation]
    turnaround: Optional[TurnaroundCandidates]
    confidence: Optional[ConfidenceReport]
    quality: Optional[QualityReport]
    source: str                      # "stl" | "parametric"
    def as_profile() -> MandrelProfile      # downstream'e sade profil
    def is_winding_ready() -> bool          # confidence/quality kapısı
```

| Avantaj | Dezavantaj |
|---|---|
| **Tek zengin nesne** UI/twin/render'da dolaşır; eşleşme hatası yok | Yeni tip → bir kez tüm UI çağrı noktaları MandrelModel'e geçmeli |
| MandrelProfile **sade kalır** (downstream sözleşmesi korunur) | Hafif sarmalama maliyeti (ama ihmal edilebilir) |
| `as_profile()` ile geriye uyum; `is_winding_ready()` ile kapı | İlk kurulumda biraz refactor |
| Parametrik profil de MandrelModel(source="parametric") olarak temsil edilir | |
| Confidence/quality artık pipeline'da **kaybolmaz** (§2 çözülür) | |

---

## 8. Nihai Öneri

### Öneri: **Seçenek C — `MandrelModel`** (B'nin ayrımı üzerine kompozisyonla inşa)

**Gerekçe:**
1. **Downstream sözleşmesi korunur (B'nin gücü):** `MandrelModel.as_profile()`
   her zaman sade `MandrelProfile` verir → path_generator/coverage/twin hiç
   değişmez, wire/replay/test güvende.
2. **Veri kaybı çözülür (§2):** axis/segments/poles/turnaround/**confidence**/
   quality tek nesnede yaşar; pipeline boyunca kaybolmaz → §4'teki "sessiz
   %102 kaplama" yalanı imkânsızlaşır (`is_winding_ready()` kapısı).
3. **Tek nesne taşınır (A'nın ergonomisi, B'nin temizliği olmadan kirlenmeden):**
   UI/twin/render `MandrelModel` alır; geometri gerektiğinde `as_profile()`.
4. **Sorumluluk ayrımı (SRP):** `MandrelProfile` = saf geometri primitifi;
   `MandrelModel` = geometri + zekâ kompozit kökü.
5. **Parametrik + STL birleşik:** cylinder/cone da `MandrelModel(source="parametric",
   confidence=tam)` olur → tek tip, dallanma yok.

**Uygulama ilkesi (ileride, onaylanınca):**
- `MandrelProfile`'a **DOKUNMA** (frozen geometri).
- `core/stl_intelligence.py` → `StlIntelligenceReport` üretir.
- `MandrelModel` ince kompozit; `from_stl()` ve `from_parametric()` fabrikaları.
- UI/twin/render kademeli olarak `MandrelModel` alır; geçişte `as_profile()`
  ile eski çağrılar çalışmaya devam eder (kırılma yok).
- `is_winding_ready()` düşükse: coverage/twin'e girmeden UI uyarısı.

### Reddedilenler
- **A** reddedildi: downstream sözleşmesini kirletir, SRP ihlali, replay riski.
- **B** reddedildi (tek başına): doğru ayrım ama iki-nesne taşıma ergonomisi
  zayıf, eşleşme hatası riski. (C zaten B'nin ayrımını içeriyor — üstüne kompozit.)

> **Tek cümle:** `MandrelProfile` saf geometri olarak kalsın; onu *saran*
> `MandrelModel` STL zekâsını (özellikle confidence) pipeline boyunca taşısın —
> böylece geodesic/coverage/twin/render hep **doğrulanmış** geometriyle çalışır,
> sessiz hata üretmez.

---

## Onay

Bu rapor onaylanırsa STL Intelligence Layer implementasyonu, çıktıları
`MandrelModel` kompoziti üzerinden taşıyacak şekilde planlanır;
`MandrelProfile` sade ve downstream-uyumlu kalır. Kodlama yalnızca onaydan
sonra başlar.
