"""
core/path_generator.py — 4-Eksen Sarma Yolu Üreticisi
=======================================================
Clairaut geodezik koşulu kullanarak dönel mandrel üzerinde
filament sarma yolları üretir.

Temel denklem: c = r(z) · sin(α(z))  (Clairaut sabiti)
Açısal oran:   dθ/dz = c / (r(z) · √(r(z)² − c²))

Devre sayısı (Koussios 2004, araştırma doğrulamalı):
  N_circ = 2π·R·sin(α) / w_eff          [meridyen açısı α için]
  ya da eşdeğeri π·D·cos(α_hoop) / w    [çevre açısı için]

Kubbe dönüşü:
  Clairaut koşulundan: fiber r(z) < c bölgesine giremez.
  Dönüm noktası: z_turn = argmin_z { r(z) ≥ c }
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .geometry_engine import MandrelProfile


# ── R3: Kompleksite koruma — sabitler, tip ve limitler ───────────────────────

class ComplexityError(ValueError):
    """
    Yol hesaplama parametreleri güvenli limiti aşıyor.

    Worker thread'den fırlatılır; `_on_path_error` sinyali ile ana thread'e
    iletilir ve kullanıcıya `QMessageBox.critical()` ile gösterilir.
    Mesaj daima Türkçe ve eyleme dönüştürülebilir öneriler içerir.
    """


_MAX_CIRCUITS_PER_LAYER: int   = 2_000        # K1: tek kat başına devre üst sınırı
_MAX_POINTS_PER_LAYER: int     = 100_000       # K2a: tek kat başına WindingPoint üst sınırı
_MAX_TOTAL_POINTS: int         = 250_000       # K2b: tüm katmanlar toplamı WindingPoint sınırı
_MAX_PATH_LENGTH_MM: float     = 5_000_000.0  # 5 km fiber — son savunma hattı


@dataclass
class ComplexityEstimate:
    """
    `estimate_complexity()` tarafından döndürülen preflight özeti.

    Alanlar
    -------
    n_circuits_per_layer  : Clairaut formülünden tek kat devre sayısı
    n_layers              : `params.n_layers` — kat sayısı
    total_circuits        : n_circuits_per_layer × n_layers
    points_per_layer      : n_circuits_per_layer × n_steps_per_pass  [K2a ile karşılaştırılır]
    total_points          : total_circuits × n_steps_per_pass         [K2b ile karşılaştırılır]
    estimated_memory_mb   : WindingPath nesnesi için tahmini bellek (MB)
    estimated_runtime_s   : Ampirik hesaplama süresi tahmini (sn)
    estimated_fiber_length_mm : Toplam fiber uzunluğu tahmini (mm)
    """
    n_circuits_per_layer: int
    n_layers: int
    total_circuits: int
    points_per_layer: int
    total_points: int
    estimated_memory_mb: float
    estimated_runtime_s: float
    estimated_fiber_length_mm: float


@dataclass
class WindingPathParams:
    """Sarma yolu oluşturmak için tüm parametreler."""
    profile: MandrelProfile
    alpha_deg: float = 55.0          # Sarma açısı (meridyen/eksel yönden ölçülür)
    n_layers: int = 4                # Kat sayısı
    tow_width_mm: float = 6.0        # Fitil genişliği (mm)
    overlap_pct: float = 5.0         # Çakışma yüzdesi [0..50]
    feed_mm_s: float = 80.0          # İlerleme hızı (mm/s)
    spindle_rpm: float = 60.0        # İş mili devri
    winding_strategy: str = "helical"  # "helical" | "hoop" | "polar"
    carriage_min_mm: float = -5.0
    carriage_max_mm: float = 395.0
    reverse_at_ends: bool = True     # Uçlarda geri dönüş
    n_steps_per_pass: int = 150      # Geçiş başına adım sayısı
    friction_mu: float = 0.0         # Sürtünme katsayısı: 0=geodezik, >0=non-geodezik
    lambda_slip: float = 0.0         # Slippage tendency λ (non-geodezik; |λ|≤friction_mu)


@dataclass
class WindingPoint:
    """Tek bir 4-eksen sarma noktası."""
    x_mm: float      # Taşıyıcı konumu (= mandrel eksenel z konumu)
    a_deg: float     # İş mili açısı (kümülatif derece, her zaman artar)
    feed: float      # Bu noktadaki ilerleme hızı (mm/s)
    z_fiber: float   # Fiber başından itibaren birikimli uzunluk (mm)
    layer: int       # Kat indeksi (0-tabanlı)
    circuit: int     # Devre indeksi (0-tabanlı)


@dataclass
class WindingPath:
    """Hesaplanmış sarma yolu sonucu."""
    points: List[WindingPoint]
    n_circuits: int
    n_layers: int
    total_fiber_length_mm: float
    estimated_time_s: float
    coverage_pct: float
    clairaut_c: float
    params: WindingPathParams


# ── Ana üretici fonksiyonlar ─────────────────────────────────────────────────

def generate_path(params: WindingPathParams) -> WindingPath:
    """Belirtilen stratejiye göre sarma yolu üret."""
    s = params.winding_strategy.lower()
    if s == "hoop":
        return generate_hoop_path(params)
    elif s == "polar":
        return generate_polar_path(params)
    else:
        return _generate_helical(params)


def generate_hoop_path(params: WindingPathParams) -> WindingPath:
    """Çevre sarma (alpha ≈ 88°). Taşıyıcı yavaşça ilerler, iş mili hızlıca döner."""
    p = WindingPathParams(
        profile=params.profile,
        alpha_deg=88.0,
        n_layers=params.n_layers,
        tow_width_mm=params.tow_width_mm,
        overlap_pct=params.overlap_pct,
        feed_mm_s=params.feed_mm_s,
        spindle_rpm=params.spindle_rpm,
        winding_strategy="helical",
        carriage_min_mm=params.carriage_min_mm,
        carriage_max_mm=params.carriage_max_mm,
        n_steps_per_pass=params.n_steps_per_pass,
        friction_mu=params.friction_mu,
        lambda_slip=params.lambda_slip,
    )
    return _generate_helical(p)


def generate_polar_path(params: WindingPathParams) -> WindingPath:
    """Kutupsal sarma (alpha ≈ 10-15°). Fiberin kubbe etrafını sardığı düşük açılı sarma."""
    polar_alpha = min(max(params.alpha_deg, 5.0), 20.0)
    p = WindingPathParams(
        profile=params.profile,
        alpha_deg=polar_alpha,
        n_layers=params.n_layers,
        tow_width_mm=params.tow_width_mm,
        overlap_pct=params.overlap_pct,
        feed_mm_s=params.feed_mm_s,
        spindle_rpm=params.spindle_rpm,
        winding_strategy="helical",
        carriage_min_mm=params.carriage_min_mm,
        carriage_max_mm=params.carriage_max_mm,
        n_steps_per_pass=params.n_steps_per_pass,
        friction_mu=params.friction_mu,
        lambda_slip=params.lambda_slip,
    )
    return _generate_helical(p)


# ── Yardımcı fonksiyonlar ────────────────────────────────────────────────────

def clairaut_circuit_count(r_avg_mm: float, alpha_rad: float,
                            tow_width_mm: float, overlap_pct: float) -> int:
    """
    Tam kapsama için gerekli minimum devre sayısı.

    Araştırma doğrulamalı formül (Koussios 2004, CADFIL kılavuzu):
      N = 2π·R·sin(α) / w_eff        (meridyen açısı α)

    Eşdeğeri: N = π·D·cos(α_hoop) / w_eff (çevre açısı konvansiyonu)

    Parametreler
    ------------
    r_avg_mm   : Ortalama mandrel yarıçapı (mm)
    alpha_rad  : Sarma açısı (meridyen/eksel yönden, radyan)
    tow_width_mm: Fitil genişliği (mm)
    overlap_pct : Çakışma yüzdesi [0..100)
    """
    eff_width = tow_width_mm * (1.0 - overlap_pct / 100.0)
    eff_width = max(eff_width, 0.1)
    sin_a = math.sin(alpha_rad)
    n_ideal = 2.0 * math.pi * r_avg_mm * sin_a / eff_width
    return max(1, math.ceil(n_ideal))


def estimate_complexity(params: "WindingPathParams") -> ComplexityEstimate:
    """
    Yol kompleksitesini parametre düzeyinde tahmin et — iç döngü çalışmaz.

    Çağrı süresi: ~1 µs (saf matematik, `WindingPoint` oluşturulmaz).
    Hem ana thread hem worker thread'den çağrılabilir.

    Fiber uzunluğu tahmini:
        fiber_per_pass ≈ L_eff / sin(α)   (geodezik geçiş alt tahmini)
        est_fiber_mm   = total_circuits × fiber_per_pass
    """
    r_avg     = params.profile.avg_radius_mm
    alpha_rad = math.radians(max(float(params.alpha_deg), 1.0))

    n_circ       = clairaut_circuit_count(r_avg, alpha_rad,
                                          params.tow_width_mm, params.overlap_pct)
    total_circ   = n_circ * params.n_layers
    pts_per_lay  = n_circ * params.n_steps_per_pass
    total_pts    = total_circ * params.n_steps_per_pass

    mem_mb    = total_pts * 56 / 1_000_000   # WindingPoint ≈ 56 byte
    runtime_s = total_pts / 30_000            # ~30 k pt/sn ampirik oran

    L_eff            = float(params.profile.length_mm)
    fiber_per_pass   = L_eff / math.sin(alpha_rad)
    est_fiber_mm     = total_circ * fiber_per_pass

    return ComplexityEstimate(
        n_circuits_per_layer      = n_circ,
        n_layers                  = params.n_layers,
        total_circuits            = total_circ,
        points_per_layer          = pts_per_lay,
        total_points              = total_pts,
        estimated_memory_mb       = mem_mb,
        estimated_runtime_s       = runtime_s,
        estimated_fiber_length_mm = est_fiber_mm,
    )


def preflight_check(params: "WindingPathParams") -> ComplexityEstimate:
    """
    Dört basamaklı limit denetimi — herhangi bir aşımda `ComplexityError` fırlatır.

    Denetim sırası (hangi limit önce aşılırsa o raporlanır):
    1. Devre/kat limiti     (K1 = 2,000)    — fitil/açı problemi
    2. Nokta/kat limiti     (K2a = 100,000) — katman bazı limit
    3. Toplam nokta limiti  (K2b = 250,000) — proje bazı limit
    4. Fiber uzunluk limiti (5,000 m)       — son savunma hattı

    Dönen `ComplexityEstimate` nesnesi `preflight_check_stack()` tarafından
    toplam nokta hesabında yeniden kullanılır.
    """
    est = estimate_complexity(params)

    if est.n_circuits_per_layer > _MAX_CIRCUITS_PER_LAYER:
        raise ComplexityError(
            f"Devre sayısı çok yüksek: {est.n_circuits_per_layer:,} devre/kat "
            f"(limit: {_MAX_CIRCUITS_PER_LAYER:,}).\n\n"
            f"Mevcut parametreler:\n"
            f"  • Ortalama mandrel yarıçapı: {params.profile.avg_radius_mm:.0f} mm\n"
            f"  • Sarma açısı: {params.alpha_deg:.1f}°\n"
            f"  • Fitil genişliği: {params.tow_width_mm:.1f} mm\n"
            f"  • Çakışma: {params.overlap_pct:.0f}%\n\n"
            f"Öneri: fitil genişliğini artırın "
            f"({params.tow_width_mm:.1f} mm → {params.tow_width_mm * 1.5:.0f} mm deneyin) "
            f"ya da sarma açısını düşürün."
        )

    if est.points_per_layer > _MAX_POINTS_PER_LAYER:
        raise ComplexityError(
            f"Katman bazı nokta sayısı çok yüksek: {est.points_per_layer:,} nokta/kat "
            f"(limit: {_MAX_POINTS_PER_LAYER:,}).\n\n"
            f"  • {est.n_circuits_per_layer:,} devre × {params.n_steps_per_pass} adım "
            f"= {est.points_per_layer:,} nokta/kat\n\n"
            f"Öneri: fitil genişliğini artırın ya da sarma açısını düşürün."
        )

    if est.total_points > _MAX_TOTAL_POINTS:
        raise ComplexityError(
            f"Proje bazı toplam nokta sayısı çok yüksek: {est.total_points:,} nokta "
            f"(limit: {_MAX_TOTAL_POINTS:,}).\n\n"
            f"  • {est.n_circuits_per_layer:,} devre/kat × {params.n_layers} kat × "
            f"{params.n_steps_per_pass} adım = {est.total_points:,} nokta\n\n"
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


def preflight_check_stack(layer_params: List["WindingPathParams"]) -> List[ComplexityEstimate]:
    """
    Çok-katmanlı (stack dict) mod için preflight denetimi.

    Her katman için `preflight_check()` çağrılır — katman bazı limitler denetlenir.
    Ardından tüm katmanların `points_per_layer` toplamı proje bazı limitle kıyaslanır.

    Parametreler
    ------------
    layer_params : Her elemanı `n_layers=1` ile oluşturulmuş `WindingPathParams` listesi.

    Döner
    -----
    Her katmana karşılık `ComplexityEstimate` listesi.

    Fırlatır
    --------
    ComplexityError : Herhangi bir katman VEYA proje toplamı limiti aşılırsa.
    """
    estimates: List[ComplexityEstimate] = []
    total_pts = 0

    for i, p in enumerate(layer_params):
        est = preflight_check(p)   # K1 + K2a + K4 — katman başına denetim
        estimates.append(est)
        total_pts += est.points_per_layer

    if total_pts > _MAX_TOTAL_POINTS:
        n_lay = len(layer_params)
        avg   = total_pts // max(n_lay, 1)
        raise ComplexityError(
            f"Proje bazı toplam nokta sayısı çok yüksek: {total_pts:,} nokta "
            f"(limit: {_MAX_TOTAL_POINTS:,}).\n\n"
            f"  • {n_lay} katman, ortalama {avg:,} nokta/katman\n\n"
            f"Öneri: katman dizilimindeki kat sayısını azaltın "
            f"ya da fitil genişliğini artırın."
        )

    return estimates


def find_turnaround_z_left(profile: MandrelProfile,
                            clairaut_c: float) -> float:
    """
    Profilde Clairaut sabitine (r=c) uyan en soldaki z konumunu bul.

    Fiziği: Geodezik fiber r < c bölgesine giremez.
    Kubbe kalıpları için geçerli dönüm noktasını verir.
    """
    z, r = profile.z_mm, profile.r_mm
    if r[0] >= clairaut_c - 1e-6:
        return float(z[0])   # profil başından itibaren erişilebilir
    for i in range(1, len(z)):
        if r[i] >= clairaut_c - 1e-6:
            # r[i-1] < c ≤ r[i] — doğrusal interpolasyon
            dr = r[i] - r[i - 1]
            if abs(dr) < 1e-12:
                return float(z[i])
            t = (clairaut_c - r[i - 1]) / dr
            return float(z[i - 1] + t * (z[i] - z[i - 1]))
    return float(z[0])   # tüm profil c'nin altında (degenerate durum)


def find_turnaround_z_right(profile: MandrelProfile,
                             clairaut_c: float) -> float:
    """
    Profilde Clairaut sabitine (r=c) uyan en sağdaki z konumunu bul.
    """
    z, r = profile.z_mm, profile.r_mm
    if r[-1] >= clairaut_c - 1e-6:
        return float(z[-1])  # profil sonuna kadar erişilebilir
    for i in range(len(z) - 2, -1, -1):
        if r[i] >= clairaut_c - 1e-6:
            # r[i] ≥ c > r[i+1] — doğrusal interpolasyon
            dr = r[i + 1] - r[i]
            if abs(dr) < 1e-12:
                return float(z[i])
            t = (clairaut_c - r[i]) / dr
            return float(z[i] + t * (z[i + 1] - z[i]))
    return float(z[-1])  # tüm profil c'nin altında


# ── İç implementasyon ────────────────────────────────────────────────────────

def _generate_helical(params: WindingPathParams) -> WindingPath:
    """
    Geodezik / non-geodezik sarmal sarma yolu üreticisi.

    Algoritma:
    1. Clairaut sabiti c = r_max · sin(alpha)
       (maksimum yarıçap kullanılır: silindirik gövde referansı)
    2. Devre sayısı: N = 2π·R·sin(α)/w_eff  (araştırma doğrulamalı)
    3. Kubbe dönüşü: z aralığını [z_turn_left, z_turn_right] ile kırp
       (r(z) ≥ c koşulunu zorunlu kıl)
    4. Non-geodezik mod: friction_mu > 0 ise non_geodesic_engine kullan
    5. İş mili her zaman aynı yönde döner (kümülatif a_deg)
    """
    prof = params.profile
    alpha_rad = math.radians(params.alpha_deg)
    r_max = prof.max_radius_mm
    r_avg = prof.avg_radius_mm

    # Clairaut sabiti — maksimum yarıçap (silindirik gövde) referansı
    clairaut_c = r_max * math.sin(alpha_rad)
    clairaut_c = min(clairaut_c, r_max * 0.995)

    # Gerçek sarma aralığı: kubbe dönüş noktaları (r < c bölgesini atla)
    z_eff_left = find_turnaround_z_left(prof, clairaut_c)
    z_eff_right = find_turnaround_z_right(prof, clairaut_c)
    L_eff = max(z_eff_right - z_eff_left, 1.0)

    # Devre sayısı (araştırma doğrulamalı formül: sin(α) faktörü dahil)
    n_circuits_per_layer = clairaut_circuit_count(
        r_avg, alpha_rad, params.tow_width_mm, params.overlap_pct
    )

    # Non-geodezik mod
    use_nongeo = (params.friction_mu > 0.0 and
                  abs(params.lambda_slip) <= params.friction_mu)

    all_points: List[WindingPoint] = []
    a_current = 0.0
    fiber_total = 0.0

    if use_nongeo:
        try:
            from .non_geodesic_engine import NonGeodesicParams, solve_non_geodesic_path
            for layer in range(params.n_layers):
                for circ in range(n_circuits_per_layer):
                    global_pass = layer * n_circuits_per_layer + circ
                    forward = (global_pass % 2 == 0)
                    z_s = z_eff_left if forward else z_eff_right
                    z_e = z_eff_right if forward else z_eff_left
                    ng_params = NonGeodesicParams(
                        profile=prof,
                        alpha_start_deg=params.alpha_deg,
                        lambda_slip=params.lambda_slip,
                        friction_coefficient=params.friction_mu,
                        integration_step_mm=max(L_eff / params.n_steps_per_pass, 0.1),
                        z_start_mm=z_s,
                        z_end_mm=z_e,
                        n_circuits=1,
                        spindle_angle_offset_deg=a_current,
                    )
                    try:
                        report = solve_non_geodesic_path(ng_params)
                        ng_pts = report.path.points
                        for pt in ng_pts:
                            pt.layer = layer
                            pt.circuit = circ
                        if ng_pts:
                            a_current = ng_pts[-1].a_deg
                        all_points.extend(ng_pts)
                        fiber_total += report.path.total_fiber_length_mm
                    except Exception:
                        # Non-geodezik başarısız → geodezik yedek
                        pts, a_current, fs = _geodesic_pass(
                            prof, z_s, z_e, clairaut_c, a_current,
                            params.feed_mm_s, layer, circ, params.n_steps_per_pass)
                        all_points.extend(pts)
                        fiber_total += fs
        except ImportError:
            use_nongeo = False   # modül yoksa geodezik'e geri dön

    if not use_nongeo:
        for layer in range(params.n_layers):
            for circ in range(n_circuits_per_layer):
                global_pass = layer * n_circuits_per_layer + circ
                forward = (global_pass % 2 == 0)
                z_start = z_eff_left if forward else z_eff_right
                z_end = z_eff_right if forward else z_eff_left
                pts, a_current, fiber_seg = _geodesic_pass(
                    prof, z_start, z_end, clairaut_c,
                    a_current, params.feed_mm_s,
                    layer, circ, params.n_steps_per_pass
                )
                fiber_total += fiber_seg
                all_points.extend(pts)

    # Kapsama hesabı (sin(α) düzeltmeli)
    total_circuits = params.n_layers * n_circuits_per_layer
    eff_width = params.tow_width_mm * (1.0 - params.overlap_pct / 100.0)
    eff_width = max(eff_width, 0.1)
    circumference_avg = 2.0 * math.pi * r_avg
    sin_a = math.sin(alpha_rad)
    # Her devrenin çevre katkısı: eff_width / sin(α) — hoop projeksiyonu
    coverage = min(100.0,
                   total_circuits * (eff_width / max(sin_a, 1e-3)) / circumference_avg * 100.0)

    est_time_s = fiber_total / max(params.feed_mm_s, 1.0)

    return WindingPath(
        points=all_points,
        n_circuits=total_circuits,
        n_layers=params.n_layers,
        total_fiber_length_mm=fiber_total,
        estimated_time_s=est_time_s,
        coverage_pct=coverage,
        clairaut_c=clairaut_c,
        params=params,
    )


def _geodesic_pass(
    profile: MandrelProfile,
    z_start: float, z_end: float,
    clairaut_c: float,
    a_start: float,
    feed_mm_s: float,
    layer: int, circuit: int,
    n_steps: int,
) -> tuple:
    """
    Tek geçiş: z_start'tan z_end'e Clairaut geodezik.
    Döner: (points, a_final, fiber_length)
    """
    z_arr = np.linspace(z_start, z_end, n_steps)
    r_arr = np.interp(z_arr, profile.z_mm, profile.r_mm)

    # Açı entegrasyonu: dθ = c/(r·√(r²-c²)) · |dz|
    a_arr = np.zeros(n_steps)
    a_arr[0] = a_start
    c2 = clairaut_c ** 2

    for i in range(1, n_steps):
        dz = abs(z_arr[i] - z_arr[i - 1])
        r_mid = (r_arr[i] + r_arr[i - 1]) * 0.5
        r2 = r_mid ** 2
        if r2 > c2 * 1.001:
            dtheta = clairaut_c / (r_mid * math.sqrt(r2 - c2)) * dz
        else:
            # Dönüm noktası bölgesi: α→90°, teğetsel sarma
            dtheta = math.pi / 2.0 * dz / max(r_mid, 0.1)
        a_arr[i] = a_arr[i - 1] + math.degrees(dtheta)

    # Fiber uzunluğu: yüzey boyunca
    dz_arr = np.diff(z_arr)
    da_rad = np.radians(np.diff(a_arr))
    r_mid_arr = (r_arr[:-1] + r_arr[1:]) * 0.5
    ds = np.sqrt(dz_arr ** 2 + (r_mid_arr * da_rad) ** 2)
    fiber_cumul = np.concatenate([[0.0], np.cumsum(ds)])
    fiber_total = float(fiber_cumul[-1])

    points = [
        WindingPoint(
            x_mm=float(z_arr[i]),
            a_deg=float(a_arr[i]),
            feed=feed_mm_s,
            z_fiber=float(fiber_cumul[i]),
            layer=layer,
            circuit=circuit,
        )
        for i in range(n_steps)
    ]
    return points, float(a_arr[-1]), fiber_total
