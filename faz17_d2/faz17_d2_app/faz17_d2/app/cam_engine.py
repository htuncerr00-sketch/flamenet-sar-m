"""
app/cam_engine.py — CAM Köprüsü (Shim)
=======================================
Bu modül artık yalnızca ince bir köprüdür.
Kanonik implementasyon: faz17_d1/core/cam_engine.py

CamRequest API'si ve tüm fonksiyon imzaları/dönüş tipleri değişmez;
cam_panel, entegre_tasarim_paneli ve diğer UI bileşenleri kırılmaz.

Köprü tablosu:
    load_mandrel(req)          → core.build_mandrel_model(MandrelSpec)
    compute_path(req, model)   → stack_dict varsa doğrudan backend;
                                 yoksa core.compute_path(model, PathSpec)
    compute_gcode(…)           → doğrudan backend (plan_motion + generate_gcode)
    compute_twin(model, req)   → core.compute_twin(…).simulation
    compute_coverage(…)        → doğrudan backend (solve_coverage)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

log = logging.getLogger("faz17_d2.cam_engine")


# ═══════════════════════════════════════════════════════════════════════════════
# CamRequest — UI'dan backend'e geçen DÜZ veri yapısı (KORUNUYOR)
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
# Re-export: MachineConfig (cam_panel `from app.cam_engine import MachineConfig`)
# ═══════════════════════════════════════════════════════════════════════════════

def __getattr__(name):
    if name == "MachineConfig":
        from backend.core.gcode_postprocessor import MachineConfig
        return MachineConfig
    raise AttributeError(f"module 'cam_engine' has no attribute {name!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# İç yardımcılar — CamRequest ↔ core spec çevirisi
# ═══════════════════════════════════════════════════════════════════════════════

_TYPE_TO_KIND: dict = {
    "Silindir": "cylinder",
    "Konik": "cone",
    "Kubbeli Silindir": "dome_cylinder_dome",
    "STL'den": "stl",
}

_STRATEGY_TR_EN: dict = {
    "Sarmal": "helical",
    "Çevre": "hoop",
    "Kutupsal": "polar",
}


def _effective_alpha(req: CamRequest) -> float:
    """Stratejiye göre gerçek sarma açısını hesapla."""
    if req.strategy == "Çevre":
        return 88.0
    if req.strategy == "Kutupsal":
        return min(max(req.alpha_deg, 5.0), 20.0)
    return req.alpha_deg


# ═══════════════════════════════════════════════════════════════════════════════
# Fonksiyon 1 — load_mandrel  →  core.build_mandrel_model
# ═══════════════════════════════════════════════════════════════════════════════

def load_mandrel(req: CamRequest):
    """
    CamRequest → MandrelModel.

    core.build_mandrel_model'e delege eder; tüm geometri mantığı orada.
    """
    from backend.core.cam_engine import build_mandrel_model, MandrelSpec

    kind = _TYPE_TO_KIND.get(req.mandrel_type, "cylinder")
    spec = MandrelSpec(
        kind=kind,
        diameter_mm=req.diameter_mm,
        length_mm=req.length_mm,
        cone_angle_deg=req.cone_angle_deg,
        dome_height_mm=req.dome_h_mm,
        stl_path=req.stl_path,
        analyze_stl=True,
    )
    model = build_mandrel_model(spec)
    log.info("[cam_engine] load_mandrel OK: %s → %s", req.mandrel_type, model.source_type)
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
        _ests = preflight_check_stack(_layer_params)
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

    # ── Tek-açı parametrik mod  →  core.compute_path ─────────────────────────
    from backend.core.cam_engine import compute_path as core_compute_path, PathSpec

    salpha = _effective_alpha(req)
    strategy = _STRATEGY_TR_EN.get(req.strategy, "helical")

    spec = PathSpec(
        alpha_deg=salpha,
        n_layers=req.n_layers,
        tow_width_mm=req.tow_w_mm,
        overlap_pct=req.overlap_pct,
        feed_mm_s=req.feed_mm_s,
        spindle_rpm=req.rpm,
        strategy=strategy,
        run_preflight=True,
        compute_coverage=False,
    )
    result = core_compute_path(model, spec)
    log.info("[cam_engine] path OK (parametrik): %d nokta, %d devre",
             len(result.winding_path.points), result.winding_path.n_circuits)
    return result.winding_path, None


# ═══════════════════════════════════════════════════════════════════════════════
# Fonksiyon 3 — compute_gcode
# ═══════════════════════════════════════════════════════════════════════════════

def compute_gcode(path, config, all_layer_paths: Optional[List] = None):
    """
    WindingPath → G-code.

    Parametreler
    ------------
    path             : Ana WindingPath
    config           : MachineConfig
    all_layer_paths  : Çok-katman modunda [(layer_dict, WindingPath), …]

    Dönüş: GCodeProgram
    """
    from backend.core.motion_planner import plan_motion
    from backend.core.gcode_postprocessor import generate_gcode, GCodeProgram

    if all_layer_paths:
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
                body = [ln for ln in gp_layer.lines if ln.strip() != "M30"]
                all_lines.extend(body)
            else:
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

    segments = plan_motion(path)
    gp = generate_gcode(segments, path, config)
    log.info("[cam_engine] gcode OK (parametrik): %d satır, %d devre",
             len(gp.lines), gp.n_circuits)
    return gp


# ═══════════════════════════════════════════════════════════════════════════════
# Fonksiyon 4 — compute_twin  →  core.compute_twin
# ═══════════════════════════════════════════════════════════════════════════════

def compute_twin(model, req: CamRequest):
    """
    Digital twin simülasyonu.

    core.compute_twin'e delege eder; TwinResult.simulation sarılır.
    Dönüş: TwinSimulationResult (eski sözleşme korunur).
    """
    from backend.core.cam_engine import compute_twin as core_twin, PathSpec, TwinSpec

    salpha = _effective_alpha(req)
    strategy = _STRATEGY_TR_EN.get(req.strategy, "helical")

    path_spec = PathSpec(
        alpha_deg=salpha,
        n_layers=req.n_layers,
        tow_width_mm=req.tow_w_mm,
        overlap_pct=req.overlap_pct,
        feed_mm_s=req.feed_mm_s,
        spindle_rpm=req.rpm,
        strategy=strategy,
        compute_coverage=False,
    )
    twin_spec = TwinSpec(n_layers=req.n_layers, dt_s=req.twin_dt_s)

    result = core_twin(model, path_spec, twin_spec)
    log.info("[cam_engine] compute_twin OK: %d durum, süre=%.1fs",
             len(result.states), result.total_time_s)
    return result.simulation  # TwinResult → TwinSimulationResult (eski sözleşme)


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
