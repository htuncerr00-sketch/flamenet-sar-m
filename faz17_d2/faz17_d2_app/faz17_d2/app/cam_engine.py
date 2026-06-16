"""
app/cam_engine.py — CAM Arka Uç Cephesi (S2)
=============================================
Tüm backend CAM çağrılarını tek bir noktada toplar.

Kural:
    UI (cam_panel, entegre_tasarim_paneli, …)
        → cam_engine fonksiyonları
            → backend.core.*

cam_panel doğrudan backend importu YAPMAZ; yalnızca bu modülden çağırır.
cam_engine'nin kendisi ise lazy import kullanır (Qt uygulaması import sırasıyla
oynamasın diye; worker thread'den de güvenle çağrılabilir).

5 ana fonksiyon
---------------
load_mandrel  : CamRequest → MandrelModel (parametrik veya STL)
compute_path  : CamRequest + MandrelModel → (WindingPath, katman_listesi | None)
compute_gcode : WindingPath + MachineConfig + katman_listesi → GCodeProgram
compute_twin  : MandrelModel + CamRequest → TwinSimulationResult
compute_coverage: WindingPath + MandrelModel → CoverageMap
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

log = logging.getLogger("faz17_d2.cam_engine")


# ═══════════════════════════════════════════════════════════════════════════════
# CamRequest — UI'dan backend'e geçen DÜZ veri yapısı
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CamRequest:
    """
    Sarma yolu hesaplamak için gereken tüm parametreler.

    Kural: Qt nesnesi BARINDIRMAZ (QThread'den geçirilebilir olmalı).
    cam_panel._collect_params() bu yapıyı döndürür.
    """
    # Mandrel geometrisi
    mandrel_type: str = "Silindir"          # "Silindir"|"Konik"|"Kubbeli Silindir"|"STL'den"
    diameter_mm: float = 100.0
    length_mm: float = 300.0
    cone_angle_deg: float = 5.0
    dome_h_mm: float = 50.0
    stl_path: Optional[str] = None

    # Sarma parametreleri
    alpha_deg: float = 55.0
    n_layers: int = 4
    tow_w_mm: float = 6.0
    overlap_pct: float = 5.0
    strategy: str = "Sarmal"               # "Sarmal"|"Çevre"|"Kutupsal"
    feed_mm_s: float = 80.0
    rpm: float = 60.0
    x_min: float = -5.0
    x_max: float = 395.0
    stack_dict: Optional[dict] = None

    # Digital twin örnekleme adımı
    twin_dt_s: float = 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# Re-export: backend tipleri (cam_panel tek import noktası olarak cam_engine kullanır)
# ═══════════════════════════════════════════════════════════════════════════════

def _import_gcode_config():
    """MachineConfig + GCodeProgram lazy import (Qt app import sırasına müdahale etmez)."""
    from backend.core.gcode_postprocessor import MachineConfig, GCodeProgram
    return MachineConfig, GCodeProgram


# cam_panel'in `from app.cam_engine import MachineConfig` kullanabilmesi için
# modül yüklenince MachineConfig'i de bu isim alanına koy.
def __getattr__(name):
    if name == "MachineConfig":
        MachineConfig, _ = _import_gcode_config()
        return MachineConfig
    raise AttributeError(f"module 'cam_engine' has no attribute {name!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# Fonksiyon 1 — load_mandrel
# ═══════════════════════════════════════════════════════════════════════════════

def load_mandrel(req: CamRequest):
    """
    CamRequest'ten MandrelModel oluştur.

    STL seçiliyse stl_intelligence.analyze_stl() çağrılır → tam rapor + güven skoru.
    Parametrik seçeneklerde MandrelModel.from_parametric() kullanılır.

    Dönüş: MandrelModel (her zaman; never raises on bad confidence — caller karar verir)
    """
    from backend.core.geometry_engine import MandrelProfile
    from backend.core.mandrel_model import MandrelModel

    mtype = req.mandrel_type
    r_mm = req.diameter_mm / 2.0
    l_mm = req.length_mm

    if mtype == "STL'den":
        if not req.stl_path:
            raise ValueError("STL yolu belirtilmedi.")
        log.info("[cam_engine] STL yükleniyor: %s", req.stl_path)
        from backend.core.stl_intelligence import analyze_stl
        report = analyze_stl(req.stl_path)
        model = MandrelModel.from_stl(report)
        log.info("[cam_engine] STL yüklendi: grade=%s winding_ready=%s",
                 model.confidence.grade if model.confidence else "—",
                 model.is_winding_ready())
    else:
        if mtype == "Silindir":
            profile = MandrelProfile.cylinder(l_mm, r_mm)
        elif mtype == "Konik":
            r_end = r_mm + l_mm * math.tan(math.radians(req.cone_angle_deg))
            profile = MandrelProfile.cone(l_mm, r_mm, r_end)
        elif mtype == "Kubbeli Silindir":
            profile = MandrelProfile.dome_cylinder_dome(l_mm, r_mm, req.dome_h_mm)
        else:
            raise ValueError(f"Bilinmeyen mandrel tipi: {mtype!r}")
        model = MandrelModel.from_parametric(profile)
        log.info("[cam_engine] Parametrik mandrel: %s r=%.1f L=%.1f",
                 mtype, r_mm, l_mm)

    return model


# ═══════════════════════════════════════════════════════════════════════════════
# Fonksiyon 2 — compute_path
# ═══════════════════════════════════════════════════════════════════════════════

def compute_path(req: CamRequest, model) -> Tuple[object, Optional[List]]:
    """
    Sarma yolu üret.

    Dönüş: (WindingPath, all_layer_paths | None)
      - all_layer_paths  : Çok-katman modunda [(layer_dict, WindingPath), …]
      - None             : Tek-açı parametrik modda

    STL modeli için is_winding_ready() False ise uyarı loglanır;
    caller UI'da kullanıcıya gösterir.
    """
    from backend.core.path_generator import (
        WindingPathParams, generate_path,
        preflight_check, preflight_check_stack, ComplexityError,
    )

    profile = model.as_profile()

    if not model.is_winding_ready():
        log.warning("[cam_engine] Mandrel winding-ready değil — yine de devam ediliyor.")

    # ── Çok-katmanlı mod (Manuel Dizilim'den yığın) ───────────────────────────
    stack = req.stack_dict
    if stack and stack.get("layers"):
        _layer_params = []
        for _layer in stack["layers"]:
            _ltype = _layer.get("layer_type") or _layer.get("type", "helical")
            _alpha = float(_layer.get("alpha_deg", req.alpha_deg))
            if _ltype == "hoop":
                _alpha = 88.0
            elif _ltype == "polar":
                _alpha = min(max(_alpha, 5.0), 20.0)
            _layer_params.append(WindingPathParams(
                profile=profile,
                alpha_deg=_alpha,
                n_layers=1,
                tow_width_mm=float(_layer.get("fitil_genisligi_mm", req.tow_w_mm)),
                overlap_pct=float(_layer.get("cakisma_pct", req.overlap_pct)),
            ))
        _ests = preflight_check_stack(_layer_params)   # ComplexityError fırlatabilir
        log.info("[cam_engine] preflight OK (stack %d katman): %d toplam nokta",
                 len(_ests), sum(e.points_per_layer for e in _ests))

        all_layer_paths = []
        for layer in stack["layers"]:
            ltype = layer.get("layer_type") or layer.get("type", "helical")
            if ltype == "hoop":
                strategy = "hoop"
            elif ltype == "polar":
                strategy = "polar"
            else:
                strategy = "helical"
            pp = WindingPathParams(
                profile=profile,
                alpha_deg=float(layer.get("alpha_deg", 55.0)),
                n_layers=1,
                tow_width_mm=float(layer.get("fitil_genisligi_mm", req.tow_w_mm)),
                overlap_pct=float(layer.get("cakisma_pct", req.overlap_pct)),
                feed_mm_s=float(layer.get("feed_mm_s", req.feed_mm_s)),
                spindle_rpm=float(layer.get("spindle_rpm", req.rpm)),
                winding_strategy=strategy,
                carriage_min_mm=req.x_min,
                carriage_max_mm=req.x_max,
            )
            p = generate_path(pp)
            all_layer_paths.append((layer, p))

        if not all_layer_paths:
            raise RuntimeError("Katman yığınından yol üretilemedi.")
        first_path = all_layer_paths[0][1]
        log.info("[cam_engine] path OK (çok-katman): %d katman, ilk yol %d nokta",
                 len(all_layer_paths), len(first_path.points))
        return first_path, all_layer_paths

    # ── Tek-açı parametrik mod ────────────────────────────────────────────────
    strategy_map = {"Sarmal": "helical", "Çevre": "hoop", "Kutupsal": "polar"}
    strategy = strategy_map.get(req.strategy, "helical")

    # Strateji'ye göre gerçek alpha
    salpha = req.alpha_deg
    if req.strategy == "Çevre":
        salpha = 88.0
    elif req.strategy == "Kutupsal":
        salpha = min(max(req.alpha_deg, 5.0), 20.0)

    pp_pre = WindingPathParams(
        profile=profile,
        alpha_deg=salpha,
        n_layers=req.n_layers,
        tow_width_mm=req.tow_w_mm,
        overlap_pct=req.overlap_pct,
    )
    est = preflight_check(pp_pre)   # ComplexityError fırlatabilir
    log.info("[cam_engine] preflight OK: %d devre/kat, %d nokta/kat, %.1f MB",
             est.n_circuits_per_layer, est.points_per_layer, est.estimated_memory_mb)

    path_params = WindingPathParams(
        profile=profile,
        alpha_deg=salpha,
        n_layers=req.n_layers,
        tow_width_mm=req.tow_w_mm,
        overlap_pct=req.overlap_pct,
        feed_mm_s=req.feed_mm_s,
        spindle_rpm=req.rpm,
        winding_strategy=strategy,
        carriage_min_mm=req.x_min,
        carriage_max_mm=req.x_max,
    )
    path = generate_path(path_params)
    log.info("[cam_engine] path OK (parametrik): %d nokta, %d devre",
             len(path.points), path.n_circuits)
    return path, None


# ═══════════════════════════════════════════════════════════════════════════════
# Fonksiyon 3 — compute_gcode
# ═══════════════════════════════════════════════════════════════════════════════

def compute_gcode(path, config, all_layer_paths: Optional[List] = None):
    """
    WindingPath → G-code.

    Parametreler
    ------------
    path             : Ana WindingPath (tek-açı veya çok-katmanın ilk yolu)
    config           : MachineConfig (kontrolör tipi, eksen adları, hız limitleri)
    all_layer_paths  : Çok-katman modunda [(layer_dict, WindingPath), …]

    Dönüş: GCodeProgram
    """
    from backend.core.motion_planner import plan_motion
    from backend.core.gcode_postprocessor import generate_gcode, GCodeProgram

    if all_layer_paths:
        # Çok-katmanlı: her katman için ayrı G-code, sıralı birleştir
        all_lines: List[str] = []
        total_len = 0.0
        total_time = 0.0
        total_circuits = 0
        coverage = 0.0

        for i, (layer_dict, wpath) in enumerate(all_layer_paths):
            ltype = layer_dict.get("layer_type") or layer_dict.get("type", "?")
            alpha = layer_dict.get("alpha_deg", 0.0)
            lbl = layer_dict.get("label", f"Katman {i + 1}")
            all_lines.append(
                f"; === Katman {i + 1}: {lbl} ({ltype} α={alpha:+.1f}°) ===")
            segs = plan_motion(wpath)
            gp_layer = generate_gcode(segs, wpath, config)

            if i == 0:
                # İlk katman: tam başlık + gövde (M30 hariç)
                body = [ln for ln in gp_layer.lines if ln.strip() != "M30"]
                all_lines.extend(body)
            else:
                # Sonraki katmanlar: başlık/bitiş satırları atla
                _skip = {"G21", "G90", "G28", "M30"}
                _skip_pfx = (
                    "; Filament", "; Mandrel", "; Sarma",
                    "; Toplam", "; Tahmini",
                )
                body = [
                    ln for ln in gp_layer.lines
                    if ln.strip() not in _skip
                    and not any(ln.startswith(p) for p in _skip_pfx)
                ]
                all_lines.extend(body)

            total_len += gp_layer.total_length_mm
            total_time += gp_layer.estimated_time_s
            total_circuits += gp_layer.n_circuits
            coverage = max(coverage, gp_layer.coverage_pct)

        all_lines.append("M30  ; Program sonu")

        combined = GCodeProgram(
            lines=all_lines,
            n_circuits=total_circuits,
            n_layers=len(all_layer_paths),
            total_length_mm=total_len,
            estimated_time_s=total_time,
            coverage_pct=coverage,
        )
        log.info("[cam_engine] gcode OK (çok-katman): %d satır, %d devre",
                 len(combined.lines), combined.n_circuits)
        return combined

    # Tek-açı modu
    segments = plan_motion(path)
    gp = generate_gcode(segments, path, config)
    log.info("[cam_engine] gcode OK (parametrik): %d satır, %d devre",
             len(gp.lines), gp.n_circuits)
    return gp


# ═══════════════════════════════════════════════════════════════════════════════
# Fonksiyon 4 — compute_twin
# ═══════════════════════════════════════════════════════════════════════════════

def compute_twin(model, req: CamRequest):
    """
    Digital twin simülasyonu.

    Dönüş: TwinSimulationResult
    Her TwinState.spindle_angle_deg, .carriage_x_actual_mm, .eye_x_mm,
    .eye_r_mm, .current_radius_mm, .fiber_deposited_mm ile S3 animasyonu besler.
    """
    from backend.core.winding_twin import simulate_winding
    from backend.core.fiber_band import FiberBand
    from backend.core.path_generator import WindingPathParams

    profile = model.as_profile()

    strategy_map = {"Sarmal": "helical", "Çevre": "hoop", "Kutupsal": "polar"}
    strategy = strategy_map.get(req.strategy, "helical")

    salpha = req.alpha_deg
    if req.strategy == "Çevre":
        salpha = 88.0
    elif req.strategy == "Kutupsal":
        salpha = min(max(req.alpha_deg, 5.0), 20.0)

    base_params = WindingPathParams(
        profile=profile,
        alpha_deg=salpha,
        n_layers=req.n_layers,
        tow_width_mm=req.tow_w_mm,
        overlap_pct=req.overlap_pct,
        feed_mm_s=req.feed_mm_s,
        spindle_rpm=req.rpm,
        winding_strategy=strategy,
        carriage_min_mm=req.x_min,
        carriage_max_mm=req.x_max,
    )
    band = FiberBand(tow_width_mm=req.tow_w_mm)

    log.info("[cam_engine] compute_twin başlıyor: %d katman, dt=%.1fs",
             req.n_layers, req.twin_dt_s)
    result = simulate_winding(
        base_profile=profile,
        band=band,
        base_params=base_params,
        n_layers=req.n_layers,
        dt_s=req.twin_dt_s,
    )
    log.info("[cam_engine] compute_twin OK: %d durum, süre=%.1fs",
             len(result.states), result.total_time_s)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Fonksiyon 5 — compute_coverage
# ═══════════════════════════════════════════════════════════════════════════════

def compute_coverage(path, model):
    """
    Kaplama haritası hesapla.

    Dönüş: CoverageMap (.coverage_pct, .gap_pct, .overlap_pct, .uniformity_index())
    """
    from backend.core.coverage_solver import solve_coverage
    from backend.core.fiber_band import FiberBand

    profile = model.as_profile()
    tow_w = float(path.params.tow_width_mm) if hasattr(path, "params") else 6.0
    band = FiberBand(tow_width_mm=tow_w)

    log.info("[cam_engine] compute_coverage başlıyor: %d nokta", len(path.points))
    cmap = solve_coverage(path, band, profile)
    log.info("[cam_engine] compute_coverage OK: %.1f%% kaplama, %.1f%% boşluk",
             cmap.coverage_pct, cmap.gap_pct)
    return cmap
