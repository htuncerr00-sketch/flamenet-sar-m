# Filament Winding CAD/CAM/Mühendislik — Gap Analysis & Yol Haritası

> **Audit kapsamı:** Faz 17 D1 (Python backend), Faz 17 D2 (PySide6 UI),
> Faz 18 (RealESP32Link), Faz 19A/B (firmware), tüm yardımcı modüller.
> Doküman tarihi: 2026-06-03. Hedef: ticari sınıf üretim hazırlığı.
>
> **2026-06-03 stratejik güncelleme:** Platform CAM-only'den
> CAD/CAM/Mühendislik hibridine evrim. Mühendislik tabakası analizi
> ayrı dokümanda: **`ENGINEERING_LAYER_ANALYSIS.md`** (12 mühendislik
> yeteneği detaylı). Bu doküman CAM tabakası analizi sürdürür; yol
> haritası mühendislik tabakası önceliği ile revize edildi.

---

## 1. Yönetici Özeti

Mevcut platform **silindirik/konik mandrel + geodezik sarım** için
endüstriyel sınıfa yakın olgunluktadır. Temel mimari sağlam: protokol
donmuş, donanım soyutlaması temiz, dijital ikiz ve makine yürütme katmanı
tam doğrulanmış. **Üç stratejik boşluk** ticarileşmeyi engelliyor:

1. **Non-geodezik / izotensoid yol mühendisliği** yok (basınçlı kap
   tasarımı için kritik).
2. **Kubbe boss / kutupsal açıklık geometrisi ve katman düşürme
   (ply-dropoff)** yok (gerçek kompozit vessel imkânsız).
3. **Endüstriyel kontrolör post-processor yelpazesi** dar (yalnızca
   GRBL/Mach3/Fanuc; Siemens 840D, Heidenhain iTNC, KEBA yok).

Yan boşluklar: AS9100/ASME PED uyumluluk çerçevesi, kür modeli + artık
gerilim, tansiyon dinamiği, malzeme sertifika izlenebilirliği.

---

## 2. Teknik Olgunluk Matrisi (A / B / C / D)

| # | Alt sistem | Sınıf | Kanıt |
|---|---|:---:|---|
| 1 | Geometri motoru (silindir/koni/kubbe) | **A** | `geometry_engine.py` + dome_transition.py; testler geçiyor |
| 2 | STL işleme | **B** | Çalışır; ama bin-tabanlı simetri kontrolü kaba |
| 3 | Geodezik motor (Clairaut doğrulama) | **A** | `geodesic_validator.py`; Clairaut sapma metriği + lift-off + slip risk |
| 4 | Non-geodezik motor | **D** | Yalnızca `allow_non_geodesic` bayrağı; yol üretimi yok |
| 5 | Fiber band & temas modeli | **B** | Statik; viskoelastik sıkıştırma yok |
| 6 | Fiber gerilimi (statik) | **B** | `fiber_tension.py` slip oranı + temas basıncı OK |
| 7 | Tansiyon dinamiği (spool inerti, motor) | **D** | Yok |
| 8 | Payout kinematik (göz → temas) | **A** | `payout_kinematics.py` lead/standoff doğru |
| 9 | Payout dinamiği (taşıyıcı kütle + lag) | **B** | PD kontrolör var; coulomb sürtünme yok |
| 10 | Fiber katenari / sarkma | **D** | Yok |
| 11 | Kaplama analizi (gap/overlap) | **B** | `coverage_solver.py`; dönüş bölgesi yığılma yok |
| 12 | Katman düşürme (dome turnaround) | **D** | Yok — yığılma tahmin edilmiyor |
| 13 | Laminat / kat planlama | **A** | `laminate_builder.py`; simetrik ±α + hoop |
| 14 | Kalınlık tahmini (Clairaut kapsama) | **B** | Eksenel kapsama OK; kür çekmesi yok |
| 15 | Reçete optimize edici | **A** | `recipe_optimizer.py` — 6 modül + 182 test |
| 16 | Malzeme veritabanı | **B** | 6 katalog; sıcaklığa bağlı değil; toplu varyasyon yok |
| 17 | Üretilebilirlik raporu | **B** | `manufacturability.py`; uyumluluk yok |
| 18 | Üretim raporu | **B** | `production_report.py`; OEE/yield yok |
| 19 | Maliyet tahmini | **B** | Pappus + işçilik + genel gider; NRE/aşınma yok |
| 20 | Hareket planlama | **A** | `motion_planner.py` + endüstriyel; jerk yok |
| 21 | Makine kinematiği (4-eksen FK/IK) | **A** | `machine_kinematics.py` |
| 22 | Makine kalibrasyonu | **B** | Statik; sıcaklık ofseti yok |
| 23 | Makine zarfı / limitler | **B** | Hız + ivme; jerk + titreşim yok |
| 24 | Makine yürütme (lag, backlash) | **A** | `machine_execution.py` — 146 test |
| 25 | Dijital ikiz | **A** | `digital_twin.py` — 205 test |
| 26 | Yürütme ikizi | **A** | `execution_twin.py` |
| 27 | G-code post-processor (GRBL/Mach3/Fanuc) | **B** | 3 kontrolör; Siemens/Heidenhain/KEBA yok |
| 28 | Tool offset / makro / alt-program | **D** | Yok |
| 29 | Simülasyon oynatımı | **A** | `simulation_playback.py` |
| 30 | Göz yönlendirme çözücü | **B** | Çalışır; aperture spreading yok |
| 31 | Yol üreticisi (geodezik) | **A** | `path_generator.py` |
| 32 | Geçiş bölgesi yol yeniden yönlendirme | **D** | Yok |
| 33 | Süreç parametreleri | **B** | Statik; sıcaklığa bağlı yok |
| 34 | Güvenlik kontrolcüsü | **B** | Sınır kontrolü; SIL sertifikasyon yolu yok |
| 35 | Hareket kontrolcüsü | **B** | Komut → bağlantı; tansiyon geri besleme yok |
| 36 | Görselleştirme | **B** | 3D mesh + fiber path; band-trace yok |
| 37 | CAM operatör iş akışı | **B** | Çalışır; onay kapıları + DRC yok |
| 38 | İzotensoid / netting analizi | **D** | Yok — basınçlı kap için kritik |
| 39 | ASME Section X / BPVC stres doğrulama | **D** | Yok |
| 40 | Kür modeli + artık gerilim | **D** | Yok |
| 41 | Malzeme sertifika & izlenebilirlik (AS9100) | **D** | Yok |
| 42 | Reçete versiyonlama / değişiklik geçmişi | **D** | Yok |
| 43 | Uç-uça CAM iş akışı entegrasyon testleri | **D** | UI testleri var; işlevsel akış yok |

**Özet:** A=15, B=18, C=0, D=10. **D-grubu hepsi yüksek-değer endüstriyel
boşluklar.**

---

## 3. Eksik Özellikler Matrisi (alana göre)

### Süreç fiziği
- Non-geodezik yol üretimi (sürtünme-slip kuplajı)
- İzotensoid / netting analizi (basınçlı kap optimizasyonu)
- Katman düşürme — dönüş bölgesinde fiber yığılma
- Kür kinetiği (DiBenedetto + Arrhenius) + artık gerilim
- Tansiyon dinamiği (spool inerti, motor zaman sabiti)
- Fiber katenari sarkma + dinamik lead düzeltmesi
- Viskoelastik reçine sıkıştırma + sıcaklık bağımlılığı

### Geometri & yol
- Kutupsal boss / kutupsal açıklık geometrisi (D-tipi mandrel)
- Eksantrik kubbe (asimetrik basınçlı kap)
- Aletli torus geçişleri
- Non-rotational mandrel desteği (gelecek için arch)
- Step-α geçişinde yumuşatma (mevcut kod kesik geçiş üretir)

### Üretim & uyumluluk
- AS9100 §8.5.2: malzeme sertifika + lot/parti izlenebilirliği
- ASME Section X (filament wound RP vessels) stres marjı kontrolü
- Tasarım çekme limiti (burst pressure × safety factor)
- NDT (UT, eddy current) strateji tanımı
- Reçete versiyonlama + audit trail
- Imzalama / onay kapıları (CAM iş akışı içinde)

### Makine kontrolü
- Endüstriyel post-processor portföyü: SINUMERIK 840D, Heidenhain
  iTNC640, KEBA KeMotion
- Tool/work offset (G54-G59), makro (P/M99), alt-program çağrısı
- 3. derece (jerk-sınırlı) S-curve hareket
- Tansiyon geri beslemeli adaptif feed-rate
- Kalibrasyon sihirbazı (ısıl drift + backlash karakterizasyon)

### Veri & yaşam döngüsü
- SQLite-tabanlı reçete deposu (yalnız telemetri var, recipe DB sığ)
- CAM çıktısı için CI-imzalı paketleme (G-code + .recipe + .twin → .wp)
- Uç-uça entegrasyon test paketi (mandrel → recipe → G-code → ikiz)

---

## 4. Risk Matrisi

| Risk | Şiddet | Olasılık | Etki | Azaltma |
|---|:---:|:---:|---|---|
| Wire protokolü bozulursa tüm zincir kırılır | **K** | D | Tarih okuma + replay yok | Sözleşme donmuş; sadece rezerve bayrak kullan |
| Non-geodezik koda sürtünme katsayısı yanlış girilirse fiber kayar | **K** | O | Hatalı yol → kalıptan düşme | Malzeme bazlı μ tablosu + sınır kontrolü |
| ASME marj hesabı yanlışsa basınçlı kap patlar | **K** | D | Can/mal kaybı | Bağımsız mühendis doğrulaması + kayıt |
| İzotensoid çözücüsü doğrultu yanlış seçerse kalın laminat | **Y** | O | Aşırı maliyet + ağırlık | Netting çözücüsünü ASME örneklerine karşı test et |
| Post-processor bir kontrolöre yanlış lehçe üretirse makine çarpar | **K** | O | Donanım hasarı | Her dialekt için "dry-run" simülatör test paketi |
| Backwards compat bozulursa mevcut testler kırılır | **Y** | O | Regresyon | Önce wrapper API; sonra refactor |
| Determinizm bozulursa replay anlamsız olur | **Y** | D | Hata ayıklama imkansız | seed=42, deterministik testler |
| Kür modeli yanlış parametreyle artık gerilimi düşük tahmin | **O** | O | Yanlış üretim parametreleri | Üretici verisinden kalibrasyon zorunlu |
| Optimizer arama uzayı patlarsa CAM hizmeti donar | **O** | O | Üretim aksaması | Zaman bütçesi + uzay sınırı |

Şiddet: K=Kritik, Y=Yüksek, O=Orta. Olasılık: D=Düşük, O=Orta, Y=Yüksek.

---

## 5. En Yüksek Değerli 20 Eksik Yetenek (Üretim Etkisine Göre)

### Tier S — Basınçlı kap üretimi için zorunlu

| # | Yetenek | Modül | Etki |
|---|---|---|---|
| 1 | **Non-geodezik yol motoru** (sürtünme-slip kuplajı + λ slippage tendency parametresi) | `non_geodesic_engine.py` (yeni) | Eksantrik kubbe, küçük açıklık, ısıl izolasyon için zorunlu |
| 2 | **Kubbe boss / kutupsal açıklık geometrisi** | `geometry_engine.py` ext. | Gerçek basınçlı kap profili olmadan başlanamaz |
| 3 | **Katman düşürme (dome turnaround pile-up)** | `path_generator.py` + `thickness_predictor.py` ext. | Kubbe kalınlığı silindirden 2-4× kalın olur; modellenmezse kalıp yetmez |
| 4 | **İzotensoid / netting analizi** | `isotensoid_solver.py` (yeni) | Optimal laminat tasarımı için endüstri standardı |
| 5 | **ASME Section X stres doğrulama (hoop + axial burst)** | `pressure_vessel_check.py` (yeni) | Sertifikalandırılabilir vessel tasarımı için zorunlu |

### Tier A — Üretim kalitesi & doğruluğu

| # | Yetenek | Etki |
|---|---|---|
| 6 | Sürtünme katsayısı malzeme tablosu (μ_fiber-resin matrisi) | Slip risk hesaplaması güvenli olur |
| 7 | SINUMERIK 840D post-processor | Alman makine parkı için zorunlu |
| 8 | Heidenhain iTNC640 post-processor | Aero-uzay sektör |
| 9 | Tool offset + makro (G54-G59, %P/M99) | Endüstriyel kontrolör entegrasyonu |
| 10 | Tansiyon dinamiği (spool inerti + motor τ) | Komut tansiyonu ile gerçek arası fark < %5 |
| 11 | Fiber katenari + dinamik lead düzeltmesi | Band yerleşim doğruluğu < 0.5 mm |
| 12 | Jerk-sınırlı (S-curve) hareket profili | Yüzey kalitesi + makine ömrü |
| 13 | Adaptif feed-rate (tansiyon geri beslemeli) | Kapalı çevrim süreç kontrolü |
| 14 | Göz aperture spreading kısıtı | Fiber-eye sıkışması engellenir |
| 15 | Kür kinetiği + artık gerilim modeli | Şekil bozulması tahmini |

### Tier B — Üretim hazırlığı

| # | Yetenek | Etki |
|---|---|---|
| 16 | AS9100 §8.5.2 malzeme sertifika & lot izlenebilirliği | Aerospace sertifikasyonu için zorunlu |
| 17 | Reçete versiyonlama + audit trail | Değişiklik kontrolü; FDA/EASA |
| 18 | Uç-uça CAM entegrasyon test paketi | Regresyon güvenliği |
| 19 | Burst-test referans kütüphanesi (DOT, ISO 11119) | Sertifika hazırlığı |
| 20 | KEBA KeMotion post-processor + dry-run test paketi | Endüstriyel sarma makine parkı |

---

## 6. Geliştirme Yol Haritası — REVİZE EDİLDİ (2026-06-03)

**Stratejik pivot**: Mühendislik tabakası önce; sonra CAM uzantıları.
Detay için `ENGINEERING_LAYER_ANALYSIS.md` §6 ve §8.

```
✓ Faz 22 Madde 1 (TAMAMLANDI):
    non_geodesic_engine.py + non_geodesic_validator.py
    + test_non_geodesic.py (85/85 PASS)
    Koussios çözücü + Wells bağımsız doğrulayıcı

⏸ Faz 22 Madde 2 (ERTELENDİ → Faz 24):
    geometry_engine boss + polar opening
    Gerekçe: mühendislik tabakası boss boyutlandırma kriterlerini
    sağlayacak; "manken boss" yerine "tasarımı yansıtan boss"
    inşa etmek daha doğru.

⏸ Faz 22 Madde 3 (ERTELENDİ → Faz 24):
    thickness_predictor turnaround dropoff
    Gerekçe: dome mühendisliği (DRS §3.7) ile birlikte gelir.

▶ Faz 23 — MÜHENDİSLİK MVE (yeni; ~1.5-2 hafta):
    ENG-1 material_allowables.py     (A/B-basis + Tsai-Wu coeffs)
    ENG-2 netting_analysis.py        (silindir kapalı-form)
    ENG-3 clt_engine.py              (Q, Q̄, [A] matrisi)
    ENG-4 failure_criterion.py       (Tsai-Wu + max stress)
    ENG-5 burst_pressure.py          (netting + CLT yaklaşımları)
    ENG-6 safety_factor.py           (ASME/ISO/AIAA kodları)
    ENG-7 pressure_vessel_sizing.py  (silindir orkestratörü)
    Hedef: silindirik basınçlı kap tasarım (input P → output schedule)

▶ Faz 24 — GEOMETRİ + KUBBE MÜHENDİSLİĞİ (~2 hafta):
    CAM-1 geometry_engine boss + polar opening (ex Faz 22 Madde 2)
    CAM-2 thickness_predictor turnaround dropoff (ex Faz 22 Madde 3)
    ENG-8 dome_engineering.py        (DRS §3.7)
    ENG-9 isotensoid_dome.py         (optimal kubbe şekli)
    ENG-10 boss_design.py             (bolt + interface)
    ENG-11 netting_analysis dome uzantısı

▶ Faz 25 — TAM WORKFLOW + ENTEGRASYON (~1.5 hafta):
    ENG-12 hoop_helical_optimizer.py
    ENG-13 pressure_vessel_workflow.py
    ENG-14 engineering_to_cam_bridge.py
    ENG-15 progressive_damage.py
    ENG-16 design_report_generator.py

▶ Faz 26 — ENDÜSTRİYEL POST-PROCESSORS (eski Faz 24, ~2 hafta):
    SINUMERIK + Heidenhain + KEBA + macro/offset + dry-run

▶ Faz 27 — DİNAMİK SÜREÇ (eski Faz 25, ~1-2 hafta):
    tension_dynamics + catenary + S-curve + adaptive feed

▶ Faz 28 — KÜR & UYUMLULUK (eski Faz 26-27 birleştirilmiş, ~2 hafta):
    cure_kinetics + residual_stress + AS9100 + ASME_X + audit trail

TOPLAM: ~10-12 hafta tam ticarileşmeye
```

---

## 7. Mimari Yol Haritası

Mevcut tüm modüller `core/` altında düz dizilim. **Önerilen yeniden
düzenleme** (geriye dönük uyumlu, yeni dizinleri import köprüsü ile aç):

```
faz17_d1/core/
├── geometry/          (yeni: profile + dome + boss + stl)
├── physics/           (yeni: fiber_*, payout_*, tension_*)
├── path/              (yeni: path_generator + non_geodesic_engine + dome_transition)
├── laminate/          (yeni: layer_*, laminate_builder, thickness_predictor)
├── process/           (yeni: cure_kinetics, residual_stress, isotensoid_solver)
├── machine/           (yeni: machine_*, eye_orientation_solver)
├── motion/            (yeni: motion_planner, industrial_motion, s_curve_motion)
├── postprocessors/    (yeni: grbl, mach3, fanuc, sinumerik, heidenhain, keba)
├── compliance/        (yeni: asme_x, as9100, pressure_vessel_check)
├── recipe/            (yeni: recipe_optimizer, cost_estimator, production_estimator)
├── twin/              (yeni: digital_twin, winding_twin, execution_twin, machine_execution)
└── validation/        (mevcut: manufacturability, manufacturing_report, geodesic_validator)
```

**Geçiş stratejisi:** her yeni alt-paket bir `__init__.py` ile mevcut
düz modülleri yeniden ihraç eder. Tek seferlik refactor yerine modül
modül taşıma + bridge ile geriye uyumluluk garanti edilir.

---

## 8. Önerilen İlk Madde

**Madde 1: Non-geodezik yol motoru** çünkü:

1. **Foundation kalitesinde**: 4 (izotensoid) ve 5 (ASME) bu motora bağlı.
2. **Risk azaltır**: friction-slip coupling parametrik; doğru kalibre
   edilirse 6, 10, 13 maddelerin temeli.
3. **Yalıtık**: mevcut geodezik yol üretimini bozmaz; yeni modül +
   `WindingPathParams.strategy="non_geodesic"` ile opsiyonel devreye.
4. **Test edilebilir**: λ = (dα/ds)·tan(β) slippage tendency parametresi
   ile sürtünme limitlerine karşı doğrulanır (Koussios 2004 referansı).

Tasarım taslağı:

```python
@dataclass
class NonGeodesicParams:
    profile: MandrelProfile
    alpha_start_deg: float
    friction_coefficient: float       # μ_fiber-resin (malzemeden)
    lambda_max: float = 0.95           # slip emniyet katsayısı
    integration_step_mm: float = 0.5
    direction_reversal_z: Optional[float] = None  # kubbe dönüş

def solve_non_geodesic_path(p: NonGeodesicParams) -> WindingPath:
    # Diferansiyel: dα/ds = λ·μ·(cos α / r) · sin(meridian_angle)
    #              Clairaut serbestliği λ ile parametrize
    # 4. derece Runge-Kutta entegrasyonu
    # |λ| < 1 ⇒ slip-emniyetli; |λ| > 1 ⇒ kayma; |λ| = 0 ⇒ geodezik
    ...
```

---

## 9. Doğrulama Çerçevesi (Tüm Yeni Modüller İçin)

Her yeni modül için minimum gereksinimler:
- **Birim test paketi**: ≥ 30 assertion, deterministik (`seed=42`),
  matematiksel doğruluk + fiziksel sınır kontrolleri.
- **Entegrasyon testi**: en az 1 uç-uça senaryo (modül → mevcut zincir).
- **Regresyon koruması**: mevcut tüm testler geçer.
- **Dokümantasyon**: modül başlığı + matematiksel model + referans
  yayın (yalnız adı + yıl + yazar — URL/DOI değil).
- **Sayısal rapor**: "★★★ READY ★★★" formatı; ölçülmüş sayılar ile.

---

## 10. Sonraki Adım — REVİZE EDİLDİ

**Faz 22 Madde 1 TAMAMLANDI** (non_geodesic_engine.py + Wells doğrulayıcı +
85/85 test).

**Faz 22 Madde 2 ve 3 ERTELENDİ → Faz 24** — mühendislik tabakası önce
gelmeli (gerekçe: `ENGINEERING_LAYER_ANALYSIS.md` §8).

**Faz 23 ENG-MVE başlatılması önerilir.** İlk modül:
`material_allowables.py` (A/B-basis allowables + Tsai-Wu coefficients +
çevre knockdown faktörleri). `material_database` modülünün geri uyumlu
uzantısı; sıfır regresyon.
