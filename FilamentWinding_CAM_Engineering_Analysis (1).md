# FİLAMENT SARIMI CAM YAZILIMI
## Kapsamlı Mühendislik Analizi ve Proje Yol Haritası

**Hazırlayan:** Kıdemli Ar-Ge Mühendisi  
**Konu:** Profesyonel Filament Winding CAM Sistemi Geliştirme  
**Referans Literatür:** Koussios (2004) "Filament Winding: A Unified Approach", ICCM, CADMAC, Oak Ridge Y-12 Geodesic Path Program  
**Tarih:** 2026

---

## BÖLÜM 0: MÜHENDİS TANITIMI VE ALAN ANALİZİ

### 0.1 Kıdemli Ar-Ge Mühendisi Tanıtımı

Kompozit malzemeler ve filament sarım teknolojilerinde doktora derecesine sahibim. Temel uzmanlık alanlarım:

- **Diferansiyel Geometri Tabanlı Fiber Yolu Hesabı** — geodezik ve geodezik olmayan eğri parametrizasyonu, birinci temel form metrikleri (E, F, G), Clairaut integrali, geodezik eğrilik (kg)
- **Basınçlı Kap Tasarımı** — isotensoid dome profilleri, netting theory, ASME BPVC Sec. X, NASA SP-8007 standartları
- **CNC Kinematik Modelleme** — çok eksenli (2~6) makine koordinat dönüşümleri, interpolasyon algoritmaları, G-code üretim mimarileri
- **Toolpath Optimizasyonu** — fiber overlap minimizasyonu, sarım pattern Diophantine problem çözümü, üretim süresi minimizasyonu

### 0.2 Dünya Genelinde En Kritik 3 Araştırma Odak Noktası

**1. Hidrojen Depolama için Tip-IV Basınçlı Kap Optimizasyonu**
Yakıt hücreli araçlar ve H₂ altyapısı için ≥700 bar çalışma basıncına karşılık fiber hacim oranı (FVF) ve dome profil optimizasyonu. Non-geodezik sarım ile polar açıklık kısıtlaması altında isotensoid profil türetme şu an alanın en kritik problemi.

**2. In-situ Termoset Kürleme ile Islak Sarım Reçine Kontrolü**
Üretim hızını artırmak için gerçek zamanlı reçine viskozite geri bildirimi, fiber gerilme kontrolü ve kürleme sıcaklık profili entegrasyonu. Reçine transfer hızı ile mandrel dönüş hızı arasındaki çift yönlü bağlaşım kritik bir kontrol problemi.

**3. Robotik Filament Sarım için 6-Eksen Toolpath Planlaması**
Serbest formlu yüzeylerde (non-rotationally symmetric) fiber yerleşimi, çarpışma önleme, singularity-free eklem yörünge planlaması. KUKA/ABB entegrasyonu ve fiber bundle kinematiği.

---

## BÖLÜM 1: PROJE YOL HARİTASI (PROJECT ROADMAP)

```
ZAMAN ÇİZELGESİ

Faz 1 [Ay 1-2]: Silindirik Mandrel + 2 Eksen + Helical Winding + Basit G-code
   ├── Matematik motoru kurulumu (geometry.py, winding_math.py)
   ├── 2-eksen kinematik model (machine_kinematics.py)
   ├── Helical winding path generator
   └── NIST RS274/NGC uyumlu G-code üretici

Faz 2 [Ay 2-3]: Hoop Winding + Layer Sistemi + Overlap Hesapları
   ├── Hoop winding implementasyonu
   ├── Winding pattern algoritması (Diophantine çözücü)
   ├── Fiber bandwidth ve overlap hesabı
   └── Multi-layer yönetimi

Faz 3 [Ay 3-5]: Dome Geometrisi + Geodezik/Non-geodezik Path
   ├── Isotensoid dome profil hesaplayıcı
   ├── Geodezik path integratörü (Clairaut denklemi)
   ├── Non-geodezik path hesabı (λ-slippage koeffisyeni)
   └── Dome-cylinder geçiş interpolasyonu

Faz 4 [Ay 5-7]: Simülasyon + 3D Preview + Layer Visualization
   ├── 3D mandrel renderer (OpenGL/VTK tabanlı)
   ├── Fiber path animasyonu
   ├── Layer kalınlık haritası görselleştirmesi
   └── G-code simülatörü (sanal makine)

Faz 5 [Ay 7-10]: GUI + Gerçek Zamanlı Kontrol + Endüstriyel Optimizasyon
   ├── PyQt6/PySide6 arayüzü
   ├── Makine parametre konfigürasyon sistemi
   ├── Üretim süresi optimizasyonu (dinamik programlama)
   └── Export: G-code, STEP, FEA mesh

TOPLAM SÜRE: ~10 ay (paralel geliştirme ile 7-8 aya indirilebilir)
```

---

## BÖLÜM 2: TEMEL MÜHENDİSLİK PROBLEMLERİ ANALİZİ

### 2.1 Problem Hiyerarşisi (Zorluk Sırasına Göre)

| # | Problem | Zorluk | Matematiksel Temel | Referans |
|---|---------|--------|-------------------|----------|
| 1 | Geodezik path hesabı (silindir) | Orta | Clairaut: r·sin(α) = c | Bookhart & Fowler, 1968 |
| 2 | Helical winding G-code üretimi | Orta | α = arctan(p/2πR) | NIST RS274/NGC |
| 3 | Winding pattern / Diophantine | Zor | nd·b_eff = 2πR_eq | Koussios, 2004 |
| 4 | Non-geodezik path (λ≠0) | Zor | dα/ds = λ·κ_g | Guo et al., 2020 |
| 5 | Isotensoid dome profil | Zor | netting theory ODE sistemi | Zu et al., 2010 |
| 6 | Feed-eye kinematik dönüşüm | Çok Zor | 4x4 homogen transform. | Koussios, 2004 |
| 7 | Çarpışma tespiti + önleme | Çok Zor | Convex hull, AABB tree | - |
| 8 | Üretim süresi optimizasyonu | Çok Zor | Dinamik programlama | Koussios Ch.14 |
| 9 | Multi-axis kinematik (4-6 eksen) | Çok Zor | Jacobian, DH parametreleri | - |
| 10 | Fiber gerilme + sürtünme kontrolü | Çok Zor | Coulomb friction, FEM | ICCM6 |

### 2.2 Kritik Fiziksel Kısıtlar

**Sürtünme Koşulu (Non-Geodezik için):**
```
λ ≤ μ_static

λ = geodezik eğrilik / normal eğrilik = k_g / k_n

Eğer λ > μ → fiber kayar (SLIP) → üretim hatası
```

**Fiber Hız Sürekliliği:**
```
S'(t) = sabit [mm/s] → reçine emprenye kalitesi için kritik
S'(t) = √[(R·C')² + (Z')²]

Burada:
C' = mandrel açısal hız [rad/s]
Z' = carriage çeviri hızı [mm/s]
R  = sarım noktasındaki mandrel yarıçapı [mm]
```

**Clairaut Sabiti (Geodezik için):**
```
r(φ) · sin(α(φ)) = c = r_pole

r_pole = polar açıklık yarıçapı [mm]
α(φ)  = meridyen koordinatı φ'de sarım açısı [rad]
```

---

## BÖLÜM 3: MATEMATİK MOTORU YAPISI

### 3.1 Koordinat Sistemi Tanımı

Sistemimiz iki koordinat çerçevesi kullanır:

**Mandrel Frame (Dönen):**
```
(ρ, φ, z) — silindirik koordinat
ρ = r(z) — mandrel profil fonksiyonu
φ = azimut açısı [0, 2π]
z = eksenel koordinat [0, L]
```

**Makine Frame (Sabit):**
```
(X, Y, Z, A) — lathe-type CNC
X = cross-carriage (fiber boyu yönü, gerekirse)
Z = ana carriage (eksenel hareket)
A = mandrel dönüş açısı [derece, sürekli sayaç]
```

### 3.2 Diferansiyel Geometri Metrikleri

İnce kabuk yüzeyi için Birinci Temel Form:
```
ds² = E·dφ² + 2F·dφ·dz + G·dz²

Devrimli yüzey (shells of revolution) için F = 0:
E = r(z)²           [paralel yön metriği]
G = 1 + (dr/dz)²    [meridyen yön metriği]

Sarım açısı α:
tan(α) = √E · dφ / (√G · dz)
       = r · dφ / (√(1+(r')²) · dz)
```

### 3.3 Geodezik Path (Clairaut Denklemi)

```
Geodezik koşul: k_g = 0

Sonuç (Clairaut integrali):
r(z) · sin(α(z)) = c = sabit

Entegrasyon için:
dφ/dz = [c / r(z)] / √[G(z) · (r(z)² - c²)]

Nümerik çözüm: RK4 veya scipy.integrate.solve_ivp
Adım boyutu: Δz ≤ r_min / 100 (adaptif)
```

### 3.4 Non-Geodezik Path (Slippage Koeffisyeni)

```
Geodezik eğrilik diferansiyel denklemi:
dα/ds = λ · κ_n(s)

λ = slippage koeffisyeni [-μ, +μ]
κ_n = yüzeyin normal eğriliği sarım doğrultusunda

Silindir için:
κ_n = sin²(α) / R

→ dα/dz = λ · sin²(α) · √(1 + (r')²) / R

Çözüm: Euler veya RK4 ile nümerik integrasyon
```

### 3.5 Isotensoid Dome Profili

Netting theory temel denklemleri:
```
Gerilme dengesi (meridyen + çevre):
σ_φ · t = N_φ = p·R_φ/2
σ_θ · t = N_θ = p·R_θ·(2 - R_θ/R_φ)/2

Isotensoid koşulu: σ_fiber = sabit
→ sin²(α) / r² = sabit = 1/r_eq²

Dome profili ODE sistemi:
dz/dρ = √[(ρ² - c²) / ((r_eq² - ρ²) · (1 + dρ/dz)²)]

Sınır koşulları:
z = 0 → ρ = r_eq (silindir-dome birleşimi)
dρ/dz|_{z=0} = 0
ρ = r_pole → z = z_max (dome sonu)
```

### 3.6 Turn-Around Açısı ve Winding Pattern

```
Tek devre için meridyen açısı propagasyonu:
Δθ = ∫₀^{L_circuit} (r · sin(α) / (r² - c²)^{1/2}) · ds

Sarım pattern koşulu:
p/q = Δθ / 2π (rasyonel sayı olmalı)

Tam kaplama için:
n · b_eff = 2π · r_eq     [tek layer]
n · b_eff = 2π · r_eq / d  [d layer için]

b_eff = b / cos(α_eq)   [efektif bandwidth ekvatorda]

Diophantine çözümü:
n, d ∈ Z⁺ → gcd(n, d) = 1
```

---

## BÖLÜM 4: TOOLPATH ENGINE YAPISI

### 4.1 Toolpath Hiyerarşisi

```
WoundBody
├── Layer[0]
│   ├── Circuit[0]   → [(φ₀,z₀), (φ₁,z₁), ..., (φₙ,zₙ)]
│   ├── Circuit[1]
│   └── ...
├── Layer[1]
└── ...

Her nokta: ToolPoint = {z, phi, alpha, r, feed_eye_pos}
```

### 4.2 Toolpath Generation Algoritması

```
ADIM 1: Mandrel Geometri Parametrizasyonu
   → r(z) profil fonksiyonu (analitik veya spline interpolasyonu)
   → r_min, r_max, L parametreleri
   → Polar açıklık: r_pole (her iki uç için)

ADIM 2: Winding Açısı ve Pattern Seçimi
   → α_cylinder = kullanıcı girişi [5°-89°]
   → c = r_pole (geodezik için)
   → λ = [0..μ] (non-geodezik deviatörü)
   → (n, p, d) çözümü: Pattern calculator

ADIM 3: Fiber Path İntegrasyonu (Her devre için)
   → Başlangıç: (φ₀, z₀) = (k·2π/n, z_start)
   → RK4 ile z artışı: dφ/dz hesabı
   → Dome bölgesi: özel ODE sistemi
   → Dome turnaround: φ → φ + Δφ_turnaround
   → Geri dönüş: z_max → z_min

ADIM 4: Devre Tekrarı
   → Her yeni devreler: φ_start += 2π·p/n
   → Toplam devre sayısı: n·d
   → Kaplama kontrolü: tüm meridyenler için

ADIM 5: Feed-Eye Pozisyon Hesabı
   → Fiber yolundan makine koordinatlarına dönüşüm
   → Çarpışma tespiti
   → A ekseni (düzeltme açısı)
```

### 4.3 Bölge Bazlı Path Stratejisi

```
Bölge         Path Tipi         ODE             Başlangıç Koşulu
─────────────────────────────────────────────────────────────────
Sol Dome       Geodezik/NG      Dome ODE        α = 90° (pole'de)
Dome-Cyl.      Transition       Interpolasyon   α_dome → α_cyl
Silindir       Geodezik/NG      Clairaut/NG     α = α_helix
Cyl.-Dome      Transition       Interpolasyon   α_cyl → α_dome
Sağ Dome       Geodezik/NG      Dome ODE        α = 90° (pole'de)
```

---

## BÖLÜM 5: MOTION PLANNER YAPISI

### 5.1 2-Eksen Lathe-Type Kinematik Model

```
Makine Eksenleri:
Z_carriage: eksenel linear hareket [mm]
A_spindle:  mandrel rotasyonu [derece, kümülatif]

Temel kinematik bağıntı:
dA/dZ = (2π · r(z)) / (b · cos(α(z)))

Sabit fiber hızı için mandrel hızı:
C'(z) = S' / r(z) / cos(α(z))   [rad/s]

Carriage hızı:
Z'(z) = S' · sin(α(z)) / √(1 + r'(z)²)  [mm/s]

Fiber tüketim hızı (sabit tutulmalı):
S'(t) = √[(r(z)·C'(t))² + (Z'(t))²] = sabit
```

### 5.2 Hız Profili Hesabı

```
Kritik kısıtlar:
1. S'(t) ≈ sabit  (reçine emprenye kalitesi)
2. C'_max ≤ makine limiti (ör: 250 rpm)
3. Z'_max ≤ makine limiti (ör: 8000 mm/min)
4. Dome bölgesinde: C' → 0 (polar alanda yavaşlama)

Polar geçiş problemi:
r(z) → r_pole (küçük değer)
→ C'(z) = S' / r(z) → ∞  (SONSUZ HIZ GEREKİR!)
→ Çözüm: S'(t)'yi polar bölgede azalt, ya da pause ekle
→ Referans: Koussios Şekil 14.10-14.12

Polar turnaround stratejileri (trade-off):
A) Sabit C', değişken S' → reçine kalitesi düşer
B) Sabit S', değişken C' → polar bölgede motor stres
C) Optimum S'(t) → dinamik programlama (Faz 5)
```

### 5.3 CNC Komut Dizisi Oluşturma

```
Her toolpoint için:
   ΔZ = z[i+1] - z[i]
   ΔA = A[i+1] - A[i]  (sürekli, kümülatif)
   
   Feed Rate: F = S' (fiber hızı, mm/min)
   
   → G1 Z{z[i+1]} A{A[i+1]} F{feed}
```

### 5.4 Feed-Eye Kinematik (3-4 Eksen için)

```
Feed-eye pozisyon vektörü:
P_eye = P_contact + L_free · t̂_fiber

t̂_fiber = fiber tangent vektörü mandrel yüzeyinde

Gerekli makine koordinatları:
X_eye = P_eye · x̂
Y_eye = P_eye · ŷ  (cross-carriage)

Fiber sarım açısı (makinede):
A_eye = arctan2(t_y, t_x)

Çarpışma tespiti:
‖P_eye - P_mandrel_surface‖ > r_clearance + r_eye
```

---

## BÖLÜM 6: G-CODE MİMARİSİ

### 6.1 Hedef Format

```
NIST RS274/NGC (Grbl, LinuxCNC, Marlin uyumlu)
Makine: 2-eksen (Z + A) lathe-type
Genişleme: 4-eksen (Z + A + X + B)
```

### 6.2 G-code Yapısı

```gcode
;============================================
; FilamentCAM v1.0
; Mandrel: D={diameter}mm L={length}mm
; Winding: α={angle}° Band={bandwidth}mm
; Layers: {n_layers} Pattern: {p}/{q}
; Generated: {timestamp}
;============================================

; === BAŞLANGIÇ BLOĞU ===
G21          ; Metrik birimler
G90          ; Absolute konumlandırma
G94          ; Feed rate: mm/min
M5           ; Spindle stop (güvenlik)
G28          ; Home position
M3 S{rpm}    ; Spindle start (yönlü)

; === SETUP ===
G0 Z{z_start} A0    ; Başlangıç pozisyonuna git

; === WINDING LOOP ===
; Layer 1 - Circuit 1
G1 Z10.500 A15.320 F500
G1 Z21.000 A30.640 F500
...
; Dome turnaround
G1 Z{z_dome} A{A_turnaround} F{F_slow}
...
; Return pass
G1 Z10.500 A{A_return} F500
...

; === BİTİŞ BLOĞU ===
M5           ; Spindle stop
G28          ; Home
M30          ; Program end
```

### 6.3 G-code Katmanları (Abstraction Layer)

```
Seviye 3: WoundBody (fiziksel fiber yolu)
      ↓
Seviye 2: ToolPath (makine koordinatları)
      ↓
Seviye 1: MotionCommands (G1, G0, M3 vb.)
      ↓
Seviye 0: GcodeText (ham string output)
```

### 6.4 Kritik G-code Parametreleri

```
Helical sarım (silindir, α sabit):
   Bir tam tur için Z ilerlemesi:
   p = 2πR · tan(α) / cos(α)   [mm/tur]
   
   G-code:
   G1 Z{p·n_turns} A{360·n_turns} F{feed}
   
Hoop sarım (α ≈ 90°):
   Z ilerlemesi ≈ b (fiber genişliği)
   
   G-code (her katman):
   G1 Z{z + b} A{A + 360·n_wraps} F{feed_slow}
```

---

## BÖLÜM 7: SİMÜLASYON SİSTEMİ YAPISI

### 7.1 Simülasyon Katmanları

```
Layer 4: Fiziksel Doğrulama
   - Fiber kayma kontrolü (λ ≤ μ)
   - Gerilme analizi (netting theory)
   - Layer kalınlığı hesabı

Layer 3: Kaplama Analizi
   - Coverage map (kaplama haritası)
   - Overlap bölgeleri
   - Boşluk (gap) tespiti

Layer 2: Makine Simülasyonu
   - G-code yorumlayıcı
   - Eksen limiti kontrolü
   - Çarpışma tespiti

Layer 1: 3D Görselleştirme
   - Mandrel mesh render
   - Fiber yolu animasyonu
   - Layer-by-layer preview
```

### 7.2 Kaplama Haritası Algoritması

```python
# Konsept (gerçek implementasyon için):
coverage_map = np.zeros((N_phi, N_z))

for each circuit in toolpath:
    for each point (phi, z) in circuit:
        band_start = phi - b_eff/2/r(z)
        band_end   = phi + b_eff/2/r(z)
        coverage_map[band_start:band_end, z_idx] += 1

# Tek kaplama → coverage ≥ 1 her yerde
# İki kaplama → coverage ≥ 2 her yerde
```

### 7.3 3D Render Yaklaşımı

```
Teknoloji seçimi (trade-off):

PyOpenGL:
  (+) Hızlı, doğrudan GPU erişimi
  (+) Büyük mesh desteği
  (-) Düşük seviye, karmaşık setup
  → Üretim simülasyonu için UYGUN

VTK + PyVista:
  (+) Bilimsel görselleştirme için hazır araçlar
  (+) Layer kalınlık renk haritası
  (-) Bağımlılık ağırlığı
  → Analiz görselleştirmesi için UYGUN

Three.js (WebGL):
  (+) Browser tabanlı, paylaşılabilir
  (+) İnteraktif 3D
  (-) Python entegrasyonu karmaşık
  → Demo/raporlama için UYGUN

ÖNERİLEN: PyVista (Faz 4) → Three.js (Faz 5)
```

---

## BÖLÜM 8: SİSTEM MİMARİSİ

### 8.1 Modüler Mimari Şeması

```
┌─────────────────────────────────────────────────────────────────┐
│                      FILAMENT CAM SYSTEM                        │
├─────────────────────────────────────────────────────────────────┤
│                        UI LAYER                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  ui.py       │  │ config_ui.py │  │  simulation_ui.py    │  │
│  │ (PyQt6 GUI)  │  │ (Makine Kfg) │  │  (3D Preview)        │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
├─────────┼─────────────────┼────────────────────-─┼─────────────┤
│                     CORE ENGINE LAYER                           │
│  ┌──────┴───────┐  ┌──────┴───────┐  ┌──────────┴───────────┐  │
│  │toolpath_     │  │motion_       │  │ simulator.py          │  │
│  │generator.py  │  │planner.py    │  │ visualization.py      │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────────────────┘  │
│         │                 │                                      │
│  ┌──────┴───────┐  ┌──────┴───────┐                            │
│  │winding_      │  │gcode_        │                            │
│  │math.py       │  │generator.py  │                            │
│  └──────┬───────┘  └──────────────┘                            │
├─────────┼──────────────────────────────────────────────────────┤
│                     FOUNDATION LAYER                            │
│  ┌──────┴───────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ geometry.py  │  │machine_      │  │ machine_config.py    │  │
│  │ (Diff.Geom.) │  │kinematics.py │  │ (YAML/JSON config)   │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 8.2 Veri Akışı

```
KULLANICI GİRİŞİ
   ↓
machine_config.py  ← makine parametreleri (R_max, L_max, feed_max, μ)
   ↓
geometry.py        ← mandrel profili r(z), dome tipi, r_pole
   ↓
winding_math.py    ← α, pattern (n,p,d), b_eff, turn-around açısı
   ↓
toolpath_generator.py  ← [(φ₀,z₀,α₀), (φ₁,z₁,α₁), ...] her devre
   ↓
motion_planner.py  ← [(Z₀,A₀,F₀), (Z₁,A₁,F₁), ...] makine komutları
   ↓
gcode_generator.py ← "G1 Z10.5 A15.3 F500\n..."
   ↓
simulator.py       ← doğrulama + kaplama analizi
   ↓
visualization.py   ← 3D render
   ↓
ÇIKTI: .nc dosyası + kaplama raporu + 3D preview
```

### 8.3 Temel Veri Yapıları

```python
# Tip tanımları (dataclass)
@dataclass
class MandrelGeometry:
    radius_func: Callable[[float], float]  # r(z)
    length: float           # [mm]
    r_pole_left: float      # [mm]
    r_pole_right: float     # [mm]
    dome_type: str          # 'geodesic' | 'isotensoid' | 'elliptic' | 'custom'
    dome_data: np.ndarray   # (z, r) profil noktaları

@dataclass
class WindingParameters:
    angle: float            # [rad], silindir bölgesi
    bandwidth: float        # [mm]
    thickness: float        # [mm]
    slippage_lambda: float  # [-μ, +μ]
    n_layers: int
    pattern: WindingPattern

@dataclass
class WindingPattern:
    n: int    # equator'da fiber sayısı
    p: int    # her devre azimut kayması (turlar)
    d: int    # layer sayısı
    k: int    # devre sayısı (n·d)
    overlap: float  # [mm]

@dataclass
class ToolPoint:
    z: float          # [mm]
    phi: float        # [rad]
    alpha: float      # [rad]
    r: float          # [mm]
    feed_eye: np.ndarray  # [X, Y] makine koordinatları

@dataclass  
class MachineCommand:
    Z: float    # [mm]
    A: float    # [deg, cumulative]
    F: float    # [mm/min]
    spindle: float  # [rpm]
```

---

## BÖLÜM 9: MODÜL LİSTESİ VE SORUMLULUKLAR

### 9.1 Foundation Layer Modülleri

**`geometry.py`**
```
Sorumluluk: Diferansiyel geometri hesapları
─────────────────────────────────────────
+ ShellOfRevolution sınıfı
  - r(z), dr/dz, d²r/dz² hesabı
  - E, G metrik katsayıları (F=0 için)
  - Normal eğrilik κ_n hesabı
  - Geodezik eğrilik κ_g hesabı
  - Yüzey alanı hesabı (arclength integrali)

+ DomeProfile sınıfı
  - geodesic_dome(r_eq, r_pole) → z(r) profili
  - isotensoid_dome(r_eq, r_pole, p_design) → ODE çözümü
  - elliptic_dome(r_eq, H) → analitik form
  - custom_dome(points) → spline fit

+ MandrelSurface sınıfı
  - Tam mandrel: sol_dome + silindir + sağ_dome
  - Nokta sorgulama: surface_point(z, phi)
  - Normal vektör: surface_normal(z, phi)
```

**`machine_config.py`**
```
Sorumluluk: Makine parametrelerinin yönetimi
─────────────────────────────────────────────
+ MachineConfig sınıfı
  - YAML/JSON okuma/yazma
  - Parametre doğrulama (limit kontrolleri)
  - Makine profilleri: 2-eksen, 4-eksen, 6-eksen

Örnek YAML:
  axes:
    Z: {max_vel: 8000, max_acc: 500}  # mm/min, mm/s²
    A: {max_vel: 250, max_acc: 100}   # rpm, rpm/s
  geometry:
    chuck_distance: 1300  # mm
    max_diameter: 250     # mm
    max_length: 750       # mm
  winding:
    fiber_speed_max: 500  # mm/s
    friction_coeff: 0.35  # μ (cam-fiber)
```

### 9.2 Core Engine Modülleri

**`winding_math.py`**
```
Sorumluluk: Tüm sarım matematiği
─────────────────────────────────
+ ClairautIntegrator
  - geodesic_path(mandrel, c, z_start, direction) → [(z,phi)]
  - desmek: Adaptif RK4, hata toleransı 1e-6

+ NonGeodesicIntegrator  
  - nongeodesic_path(mandrel, alpha0, lambda, z_start) → [(z,phi,alpha)]
  - Slippage koşulu λ ≤ μ kontrolü

+ IsotensoidSolver
  - dome_profile(r_eq, r_pole, q, r) → (z, r) array
  - netting_theory_ode(y, z, params)

+ PatternCalculator
  - find_pattern(r_eq, b, n_layers, alpha_eq) → [WindingPattern]
  - turn_around_angle(mandrel, c, winding_type) → Δθ
  - diophantine_solve(n_target, tolerance) → (n, p, d)
  - bandwidth_check(pattern, mandrel) → overlap [mm]
```

**`toolpath_generator.py`**
```
Sorumluluk: Fiber yolu koordinat dizisi üretimi
───────────────────────────────────────────────
+ HelicalPathGenerator   → silindir + helisel
+ HoopPathGenerator      → hoop winding
+ PolarPathGenerator     → polar winding (α ≈ 0°)
+ FullBodyPathGenerator  → dome + silindir + dome
  - integrate_circuit(start_phi, winding_params) → [ToolPoint]
  - generate_all_circuits(pattern, winding_params) → [[ToolPoint]]
  - dome_turnaround(entry_point) → turnaround_sequence
```

**`motion_planner.py`**
```
Sorumluluk: ToolPoint → MachineCommand dönüşümü
───────────────────────────────────────────────
+ KinematicTransformer
  - toolpoint_to_machine(tp, machine_config) → MachineCommand
  - compute_feed_rate(tp, target_fiber_speed) → F [mm/min]
  - spindle_speed_profile(toolpoints) → [rpm]

+ CollisionDetector
  - check_feed_eye_collision(eye_pos, mandrel) → bool
  - compute_safe_standoff(z, r) → X_eye_min

+ VelocityProfiler
  - smooth_velocity(commands, acc_limit) → [MachineCommand]
  - polar_deceleration(commands, r_pole, v_min) → [MachineCommand]
```

**`gcode_generator.py`**
```
Sorumluluk: MachineCommand → G-code text
─────────────────────────────────────────
+ GcodeFormatter
  - header(params) → str
  - footer() → str
  - linear_move(cmd) → "G1 Z{} A{} F{}\n"
  - rapid_move(cmd) → "G0 Z{} A{}\n"
  - spindle_cmd(rpm) → "M3 S{}\n"

+ GcodeOptimizer
  - remove_redundant(commands) → [MachineCommand]
  - arc_fit(linear_seq) → mixed G1/G2/G3  (Faz 5)
  - segment_merge(commands, tolerance) → [MachineCommand]

+ GcodeValidator
  - syntax_check(gcode_text) → [errors]
  - limit_check(commands, machine_config) → [warnings]
```

### 9.3 Simülasyon ve Görselleştirme Modülleri

**`simulator.py`**
```
Sorumluluk: Sanal makine ve fiziksel doğrulama
───────────────────────────────────────────────
+ GcodeSimulator
  - parse(gcode_text) → [MachineCommand]
  - execute(commands) → SimulationState
  - replay(speed=1.0) → generator

+ PhysicsValidator
  - check_slippage(toolpoints, mu) → [SlipEvent]
  - compute_coverage(toolpoints, bandwidth) → coverage_map
  - compute_thickness(toolpoints) → thickness_map
  - check_polar_overlap(toolpoints) → overlap_map
```

**`visualization.py`**
```
Sorumluluk: 3D görselleştirme
───────────────────────────────
+ MandrelRenderer (PyVista)
  - render_surface(mandrel) → mesh
  - render_fiber_path(toolpoints) → tube
  - render_coverage_map(coverage) → heatmap_on_surface
  - render_layer(layer_idx) → specific_layer

+ AnimationController
  - animate_winding(toolpoints, speed) → animation
  - export_gif(filename)
```

---

## BÖLÜM 10: KRİTİK TRADE-OFF ANALİZLERİ

### 10.1 Geodezik vs. Non-Geodezik

| Özellik | Geodezik (λ=0) | Non-Geodezik (λ≠0) |
|---------|---------------|-------------------|
| Hesap karmaşıklığı | Düşük (analitik) | Yüksek (ODE) |
| Fiber stabilite | Garantili | μ'ya bağlı |
| Tasarım özgürlüğü | Kısıtlı | Geniş |
| Polar açıklık | Sabit (c=r_pole) | Değiştirilebilir |
| Üretim riski | Düşük | Orta |
| Standart uyumu | ASTM D2585, ISO | - |
| **Önerilen kullanım** | **Faz 1-2** | **Faz 3+** |

### 10.2 Dome Profili Karşılaştırması

| Dome Tipi | Hesap | Dayanım | Üretim | Kullanım |
|-----------|-------|---------|--------|----------|
| Geodezik (küresel) | Kolay | İyi | Kolay | Genel |
| Isotensoid | ODE çözümü | Çok iyi | Orta | Basınçlı kap |
| Eliptik | Analitik | Orta | Kolay | Düşük basınç |
| Helical modif. | ODE+opt. | İyi | Orta | Eşitsiz açıklık |

### 10.3 Pattern Hesap Yöntemi Karşılaştırması

| Yöntem | Hız | Doğruluk | Esneklik |
|--------|-----|----------|---------|
| Diophantine çözümü | Yavaş | Mükemmel | Kısıtlı |
| Sürekli yaklaşım | Hızlı | İyi | Esnek |
| Optimizasyon tabanlı | Orta | Optimal | Esnek |
| **Önerim** | **Önce Sürekli, sonra Diophantine** |||

### 10.4 G-code Interpolasyon Yöntemi

| Yöntem | Dosya Boyutu | Hassasiyet | Desteklenen CNC |
|--------|-------------|-----------|----------------|
| G1 (linear, sık adım) | Büyük | Yüksek | Tümü |
| G1 (seyrek adım) | Küçük | Düşük | Tümü |
| G2/G3 (dairesel arç) | Küçük | Yüksek | Çoğu |
| NURBS (G05.2) | Çok küçük | Çok yüksek | Yalnız Fanuc/Siemens |
| **Önerim** | **G1 + adaptif adım boyutu** |||

---

## BÖLÜM 11: EN ZOR MÜHENDİSLİK PROBLEMLERİ

### 11.1 #1 Kritik Problem: Polar Bölge Singularitesi

```
PROBLEM:
Clairaut integrali: sin(α) = c/r(z)
r(z) → r_pole = c → sin(α) → 1 → α → 90°
Aynı anda: dφ/dz → ∞  (sayısal patlama!)

FİZİKSEL ANLAMI:
Fiber polar açıklığa yaklaştığında,
eksenel ilerleme sıfırlanır ama açısal hız sonsuza gider.
CNC açısından: A ekseni sonsuz hıza çıkamaz → ÇARPIŞMA

ÇÖZÜM STRATEJİLERİ:
A) Koordinat değişimi: φ parametrizasyonu yerine arc-length s kullanımı
   ds değişmez → dφ/ds ve dz/ds ayrı hesaplanır
   
B) Singularity-free integrasyon: 
   Near-pole bölge (r < 1.05·r_pole) için özel muamele
   Makine hızını sınırla: C'_max → S'_polar < S'_nominal
   
C) Dome turnaround bölgesi için ayrı ODE:
   r yerine açı θ (meridyen açısı) parametresi kullan
   dz/dθ = r·cos(θ) ... → sıfır olmayan payda
   
UYGULAMA: Bu sorunu Faz 3'te çözmeden dome geçişi YAPILMAZ.
```

### 11.2 #2 Kritik Problem: Fiber Hız Sabitliği vs. Mandrel Hızı

```
PROBLEM:
S'(t) = r(z)·C'(t) · 1/cos(α(z)) = sabit istenirse
→ C'(t) = S' · cos(α(z)) / r(z)

Dome'da r(z) küçülüyor → C'(t) büyüyor → motor limiti aşılıyor!

ÇÖZÜM:
1. S'(t)'yi polar bölgede orantılı azalt
   → Reçine emprenye kalitesi etkilenir (kabul edilebilir mi?)
   
2. Makine limiti: max_fiber_speed = min(S'_nominal, r_pole·C'_max)
   → Tasarım kısıtı olarak makine_config.yaml'a gir

ÖNERME: Faz 1'de bu kısıtı basitleştir:
   Silindirik bölge → S' sabit
   Polar bölge → C' sabit (S' değişir)
```

### 11.3 #3 Kritik Problem: Winding Pattern Belirsizliği

```
PROBLEM:
Winding pattern (n, p, d) bulma = Diophantine problem
Δθ · p ≡ 2π (mod 2π) değil, daha karmaşık:

Δθ irrasyonel olabilir → mükemmel pattern bulunamaz!
Gerçekte: |Δθ·p - 2π·q| < ε  (tolerans gerekli)

ÇÖZÜM:
1. Turn-around açısını (Δθ) say.sal integrasyon ile hesapla
2. Continued fraction expansion ile en iyi p/q yaklaşımı bul
3. Fark: ε = |Δθ·p - 2π·q| → overlap miktarı
4. Kabul kriteri: overlap ≤ b/4 (quarter-bandwidth)

Koussios'tan (2004): "K(λ) fonksiyonu için küçük bir λ 
aralığında lineer bir yaklaşım yeterlidir"
→ Bu yaklaşımı Pattern Calculator'a uygula.
```

---

## BÖLÜM 12: GELİŞTİRME FAZ PLANI (DETAYLI)

### FAZ 1: Temel Çekirdek (2 Ay)

**Hedef:** Silindirik mandrel, sabit açılı helical sarım, çalışan G-code üretimi

**Önce Geliştir:**
```
Hafta 1-2: geometry.py
   - ShellOfRevolution (silindir: r(z) = R = sabit)
   - Metrik hesapları E, G
   - Arc-length integrali (scipy)

Hafta 3-4: winding_math.py (sadece Clairaut)
   - geodesic_path() — silindir için analitik (Clairaut basit hâli)
   - turn_around_angle() — silindir için: Δθ = 2π·R·tan(α)/b

Hafta 5-6: motion_planner.py (2-eksen)
   - toolpoint_to_machine() — (z, φ) → (Z, A)
   - compute_feed_rate()

Hafta 7-8: gcode_generator.py
   - header/footer
   - linear_move G1 üretimi
   - Doğrulama: Grbl simülatöründe test

FAZ 1 BAŞARISI: Çalışan, silindir üzerine helical sarım G-code'u
```

### FAZ 2: Pattern Sistemi (1 Ay)

```
Hafta 9-10: PatternCalculator
   - Diophantine çözücü (brute-force → sonra optimize)
   - bandwidth_check
   - Multi-layer destesi

Hafta 11-12: Hoop Winding
   - HoopPathGenerator
   - Layer yönetim sistemi
```

### FAZ 3: Dome ve Tam Geometri (2.5 Ay)

```
Ay 3: Dome profilleri
   - isotensoid_dome() ODE çözümü
   - elliptic_dome() analitik

Ay 4: Geodezik path (genel mandrel)
   - Adaptif RK4 integratörü
   - Singularite yönetimi (kritik!)
   - Dome-cylinder geçişi

Ay 4.5: Non-geodezik path
   - λ-koeffisyentli path
   - Slippage kontrol
```

### FAZ 4: Simülasyon (2 Ay)

```
Ay 5: Coverage map + fiziksel doğrulama
Ay 6: 3D görselleştirme (PyVista)
      G-code sanal makine
```

### FAZ 5: GUI + Optimizasyon (3 Ay)

```
Ay 7-8: PyQt6 arayüzü
Ay 9-10: Üretim süresi optimizasyonu (dinamik prog.)
         Multi-axis (4-eksen) genişletme
```

---

## BÖLÜM 13: BAŞLANGIÇ ÖNERİSİ — İLK KOD YAZILMADAN ÖNCE

### Matematiksel Doğrulama Testleri (Sıfır Kod, Önce Analiz)

Faz 1 kodlamaya başlamadan şu matematiksel kontrolleri yap:

**Test 1 — Clairaut (Elle Hesap):**
```
Silindir: R = 50mm, α = 30°, r_pole = 25mm
c = R·sin(α) = 50·0.5 = 25mm ✓ (r_pole'e eşit, doğru)

Bir tur için Z ilerlemesi:
p = 2π·R·tan(α) = 2π·50·0.577 = 181.4 mm/tur

Makine: 1 tur = A360°, Z=181.4mm
G-code: G1 Z181.4 A360.0 F{S'=200mm/s → F=12000mm/min}
```

**Test 2 — Band Sayısı (Elle Hesap):**
```
R = 50mm, b = 10mm, α_eq = 30°
b_eff = b / cos(30°) = 10/0.866 = 11.55mm
n = 2π·R / b_eff = 314.16 / 11.55 ≈ 27.2 ≈ 27 band

Pattern: Δθ = 2π · (b / 2πR / cos(α)) ≈ 13.33°
p/q yaklaşımı: 13.33° ≈ 4/108 → p=4, n=27, d=1 → 27 devre
```

**Test 3 — Polar Açıklık Kontrolü:**
```
r_pole = c = 25mm > 0 → geodezik sarım mümkün
Minimum sarım açısı: α_min = arcsin(r_pole/R) = 30° ✓
```

Bu hesapları onayladıktan sonra Faz 1 kodlamaya geçilmeli.

---

## BÖLÜM 14: LİTERATÜR VE STANDARTLAR

### 14.1 Temel Referanslar

| Kaynak | İçerik | Kullanım Alanı |
|--------|--------|----------------|
| Koussios (2004) | Geodezik/NG path, kinematik | Matematik motoru, tümü |
| Bookhart & Fowler (1968) | Bilgisayar tabanlı geodezik | Nümerik çözüm yöntemleri |
| Guo et al. (2020) | Eşitsiz açıklıklı NG pattern | Faz 3 pattern hesabı |
| ICCM6 - CADMAC (1987) | Yazılım mimarisi referansı | Genel mimari |
| Özbek et al. (2020) | 2-eksen lathe makine | Faz 1 makine kinematiği |
| reilley.net/winder/ | Açık kaynak referans | Mimari yaklaşım analizi |

### 14.2 İlgili Standartlar

| Standart | Konu |
|----------|------|
| ASTM D2585 | Filament winding test metotları |
| ASTM D2290 | Hoop gerilme (split disk) |
| ISO 7866 | Kompozit silindirik basınçlı kaplar |
| ASME BPVC Section X | Fiber güçlendirilmiş basınçlı kaplar |
| NIST RS274/NGC | G-code formatı (hedef standart) |
| ISO 841 | CNC eksen isimlendirmesi |

---

## ÖZET: ÖNCE GELİŞTİRİLMESİ GEREKENLER

```
KRITIK YOL (CRITICAL PATH):

1. geometry.py → ShellOfRevolution (Faz 1 engeli)
   ↓
2. winding_math.py → ClairautIntegrator (Faz 1 engeli)
   ↓
3. motion_planner.py → 2-eksen kinematik (Faz 1 engeli)
   ↓
4. gcode_generator.py → G1 üretimi (Faz 1 çıktısı)
   ↓
5. PatternCalculator (Faz 2 engeli)
   ↓
6. geometry.py → DomeProfile (Faz 3 engeli)
   ↓
7. Singularite yönetimi (Faz 3'ün EN ZOR adımı)
   ↓
8. simulator.py → Coverage map (Faz 4)
   ↓
9. visualization.py (Faz 4)
   ↓
10. GUI + Optimizasyon (Faz 5)

NOT: 7. adım (singularite) atlanırsa Faz 3 çalışmaz.
Bu nedenle polar geçiş matematiksel çözümü,
dome kodlamasından ÖNCE teorik olarak tamamlanmalıdır.
```

---

*Bu doküman, projenin mühendislik altyapısını tanımlamaktadır. Herhangi bir modülün detaylı implementasyonuna geçmeden önce, ilgili bölümdeki matematiksel formüllerin ve test hesaplarının onaylanması gerekmektedir.*
