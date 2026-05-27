"""
export_pipeline.py — 3D Görselleştirme Export Sistemi
=======================================================
Toolpath ve simülasyon verilerini dışsal görselleştirme
araçları için çeşitli formatlara aktarır.

Export türleri:
  1. Toolpath JSON     — 3D yol noktaları (Three.js/Blender)
  2. Thickness Heatmap — 2D kalınlık ısı haritası (CSV + ASCII)
  3. A-Axis Timeline   — Kümülatif A profili (CSV + ASCII bar)
  4. Layer Buildup     — z boyunca kalınlık birikimi (CSV)
  5. Error Map         — Simülasyon hata dağılımı (CSV)
  6. Simulation Report — Tam metin raporu (TXT)

Format seçimleri:
  JSON: Three.js / web görselleştirme için
  CSV:  MATLAB / Python analiz için
  TXT:  İnsan okunabilir rapor
  ASCII: Terminal önizleme

Koordinat sistemi:
  JSON toolpath: Kartezyen (x, y, z) — mandrel yüzeyinde
    x = r(z)·cos(φ)   [mm]
    y = r(z)·sin(φ)   [mm]
    z = z              [mm] (eksenel)
"""

from __future__ import annotations

import csv
import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from machine_simulation import SimulationResult, SimState
from digital_twin import DigitalTwinState


# ── Toolpath Point ───────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ToolpathPoint3D:
    """Kartezyen 3D toolpath noktası."""
    x_cart: float   # [mm] — Cartesian X
    y_cart: float   # [mm] — Cartesian Y
    z_ax:   float   # [mm] — Axial (mandrel axis)
    phi:    float   # [rad] — Azimut
    r:      float   # [mm] — Mandrel radius at this z
    alpha:  float   # [rad] — Winding angle
    thickness:float # [mm] — Local thickness
    tension:  float # [N]  — Fiber tension
    pass_idx: int   # Pass index
    seg_idx:  int   # Segment index


def cylindrical_to_cartesian(
    z_mm:   float,
    phi_rad:float,
    r_fn:   callable,
) -> tuple:
    """(z, φ) → (x_cart, y_cart, z_ax)."""
    r = r_fn(z_mm)
    return (r * math.cos(phi_rad), r * math.sin(phi_rad), z_mm)


# ── Export Pipeline ──────────────────────────────────────────────

class VisualizationExportPipeline:
    """
    3D görselleştirme export motoru.

    Kullanım:
        pipe = VisualizationExportPipeline(output_dir="/mnt/user-data/outputs")
        pipe.export_toolpath_json(paths, mandrel, "toolpath.json")
        pipe.export_thickness_heatmap(coverage_result, "thickness.csv")
        pipe.export_a_timeline(x_arr, a_arr, "a_timeline.csv")
        pipe.export_simulation_report(twin_state, "sim_report.txt")
    """

    def __init__(self, output_dir: str = "/mnt/user-data/outputs") -> None:
        self.out_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def _path(self, filename: str) -> str:
        return os.path.join(self.out_dir, filename)

    # ── 1. Toolpath JSON ─────────────────────────────────────────

    def export_toolpath_json(
        self,
        x_arr:      np.ndarray,
        a_arr_deg:  np.ndarray,
        f_arr:      np.ndarray,
        r_fn:       callable,           # r(z) → float
        alpha_rad:  float,
        sim_states: Optional[List[SimState]] = None,
        filename:   str = "toolpath.json",
        subsample:  int = 3,
    ) -> str:
        """
        Toolpath'i Three.js uyumlu JSON formatına aktar.

        JSON yapısı:
        {
          "metadata": {...},
          "planned": [{"x":..., "y":..., "z":..., "phi":..., ...}],
          "actual":  [{"x":..., "y":..., "z":..., ...}]  // simüle
        }
        """
        planned_pts = []
        actual_pts  = []
        n = len(x_arr)

        for i in range(0, n, subsample):
            z   = float(x_arr[i])
            phi = float(math.radians(a_arr_deg[i]))
            r   = r_fn(z)
            xc  = r * math.cos(phi)
            yc  = r * math.sin(phi)
            f   = float(f_arr[i])
            planned_pts.append({
                "x": round(xc, 4), "y": round(yc, 4), "z": round(z, 4),
                "phi_deg": round(math.degrees(phi) % 360.0, 3),
                "r": round(r, 4), "f": round(f, 2),
            })

            if sim_states and i < len(sim_states):
                st = sim_states[i]
                phi_a = math.radians(st.a_actual_deg)
                r_a   = r_fn(st.x_actual)
                actual_pts.append({
                    "x": round(r_a*math.cos(phi_a), 4),
                    "y": round(r_a*math.sin(phi_a), 4),
                    "z": round(st.x_actual, 4),
                    "tension_N": round(st.tension_N, 3),
                    "x_error_mm": round(st.x_error, 4),
                    "is_turnaround": bool(st.is_turnaround),
                })

        data = {
            "metadata": {
                "type": "FilamentWindingToolpath",
                "n_planned": len(planned_pts),
                "n_actual":  len(actual_pts),
                "alpha_deg": round(math.degrees(alpha_rad), 4),
                "units":     "mm",
                "coord_system": "mandrel_surface_cartesian",
            },
            "planned": planned_pts,
            "actual":  actual_pts,
        }

        fpath = self._path(filename)
        with open(fpath, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        print(f"    Toolpath JSON: {fpath} ({len(planned_pts)} nokta)")
        return fpath

    # ── 2. Thickness Heatmap ─────────────────────────────────────

    def export_thickness_heatmap(
        self,
        z_arr:      np.ndarray,
        rho_arr:    np.ndarray,
        t_arr:      np.ndarray,
        filename:   str = "thickness_heatmap.csv",
        print_ascii:bool = True,
    ) -> str:
        """
        z boyunca ρ ve t değerlerini CSV ve ASCII olarak aktar.
        """
        fpath = self._path(filename)
        with open(fpath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["z_mm", "rho", "thickness_mm"])
            for z, rho, t in zip(z_arr, rho_arr, t_arr):
                writer.writerow([f"{z:.3f}", f"{rho:.5f}", f"{t:.5f}"])
        print(f"    Thickness CSV: {fpath} ({len(z_arr)} zon)")

        if print_ascii:
            self._ascii_heatmap(z_arr, rho_arr, t_arr)
        return fpath

    def _ascii_heatmap(
        self,
        z_arr:   np.ndarray,
        rho_arr: np.ndarray,
        t_arr:   np.ndarray,
        n_bars:  int = 30,
        bar_w:   int = 30,
    ) -> None:
        """ASCII ısı haritası (terminal görselleştirme)."""
        max_t = max(t_arr.max(), 1e-9)
        n = len(z_arr)
        step = max(1, n // n_bars)

        print(f"\n  Thickness Heatmap (z: 0→{z_arr[-1]:.0f}mm)")
        print(f"  {'z':>7}  {'ρ':>6}  {'t[mm]':>7}  {'':>30}")
        print("  " + "─" * 55)

        # Color chars
        chars = " ░▒▓█"

        for i in range(0, n, step):
            z = z_arr[i]; rho = rho_arr[i]; t = t_arr[i]
            # Color level
            level = min(4, int(rho / 2.0 * 4))
            bar_n = int(t / max_t * bar_w)
            bar   = chars[level] * bar_n + "·" * (bar_w - bar_n)
            warn  = " ⚠" if rho > 2.0 else ""
            print(f"  {z:>7.1f}  {rho:>6.3f}  {t:>7.4f}  │{bar}│{warn}")

    # ── 3. A-Axis Timeline ───────────────────────────────────────

    def export_a_timeline(
        self,
        x_arr:     np.ndarray,
        a_arr_deg: np.ndarray,
        f_arr:     np.ndarray,
        sim_a:     Optional[np.ndarray] = None,
        filename:  str = "a_axis_timeline.csv",
        print_ascii:bool = True,
    ) -> str:
        """
        Kümülatif A profili — planlanan vs simüle edilen.
        """
        fpath = self._path(filename)
        with open(fpath, "w", newline="") as f:
            writer = csv.writer(f)
            header = ["seg", "x_mm", "a_planned_deg", "f_mm_min"]
            if sim_a is not None: header.append("a_actual_deg")
            writer.writerow(header)
            for i in range(len(x_arr)):
                row = [i, f"{x_arr[i]:.3f}", f"{a_arr_deg[i]:.4f}", f"{f_arr[i]:.1f}"]
                if sim_a is not None: row.append(f"{sim_a[i]:.4f}")
                writer.writerow(row)
        print(f"    A-Timeline CSV: {fpath} ({len(x_arr)} segment)")

        if print_ascii:
            self._ascii_a_timeline(x_arr, a_arr_deg, sim_a)
        return fpath

    def _ascii_a_timeline(
        self,
        x_arr:     np.ndarray,
        a_arr_deg: np.ndarray,
        sim_a:     Optional[np.ndarray],
        n_bars:    int = 25,
    ) -> None:
        """ASCII A-ekseni zaman çizelgesi."""
        n    = len(x_arr)
        step = max(1, n // n_bars)
        a_max = float(a_arr_deg.max()) if len(a_arr_deg) > 0 else 1.0
        bar_w = 35

        print(f"\n  A-Axis Timeline (kümülatif, {a_max:.0f}°)")
        print(f"  {'seg':>5}  {'x_mm':>7}  {'A_plan':>9}  {'A_sim':>9}  {'':>{bar_w}}")
        print("  " + "─" * (bar_w + 38))

        for i in range(0, n, step):
            a_p = float(a_arr_deg[i])
            a_s = float(sim_a[i]) if sim_a is not None else a_p
            bar_p = int(a_p / a_max * bar_w)
            bar_s = int(a_s / a_max * bar_w)
            diff  = abs(a_p - a_s)

            if sim_a is not None:
                bar = "─"*min(bar_p, bar_s) + "≡"*abs(bar_p-bar_s) + " "*(bar_w-max(bar_p,bar_s))
                diff_str = f" Δ={diff:.3f}°"
            else:
                bar = "█"*bar_p + "░"*(bar_w-bar_p)
                diff_str = ""

            print(f"  {i:>5}  {x_arr[i]:>7.1f}  {a_p:>9.2f}  "
                  f"{a_s:>9.2f}  │{bar}│{diff_str}")

    # ── 4. Simulation Report ─────────────────────────────────────

    def export_simulation_report(
        self,
        twin_state: DigitalTwinState,
        sim_config_str: str = "",
        filename: str = "simulation_report.txt",
    ) -> str:
        """Tam simülasyon raporu TXT dosyası."""
        fpath = self._path(filename)
        lines = [
            "=" * 65,
            "FILAMENT WINDING - DIGITAL TWIN SİMÜLASYON RAPORU",
            "=" * 65,
            "",
            twin_state.full_report(),
            "",
            twin_state.sim_result.report(),
            "",
            f"Kalite skoru: {twin_state.quality_score:.4f}",
            f"Uyarı sayısı: {len(twin_state.alerts)}",
        ]
        for a in twin_state.alerts:
            lines.append(f"  ⚠ {a}")

        if sim_config_str:
            lines += ["", "Simülasyon Konfigürasyonu:", sim_config_str]

        lines += ["", "=" * 65, "Rapor sonu.", "=" * 65]

        with open(fpath, "w") as f:
            f.write("\n".join(lines))
        print(f"    Simülasyon raporu: {fpath}")
        return fpath

    # ── 5. Error Map ─────────────────────────────────────────────

    def export_error_map(
        self,
        states:   List[SimState],
        filename: str = "error_map.csv",
    ) -> str:
        """Tüm segmentler için hata haritası CSV."""
        fpath = self._path(filename)
        with open(fpath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "seg", "x_planned", "x_actual", "x_err_mm",
                "a_planned_deg", "a_actual_deg", "a_err_deg",
                "tension_N", "alpha_err_deg",
                "backlash_mm", "missed_step_mm", "spindle_lag_deg",
                "is_turnaround",
            ])
            for st in states:
                writer.writerow([
                    st.segment_index,
                    f"{st.x_planned:.4f}", f"{st.x_actual:.4f}",
                    f"{st.x_error:.6f}",
                    f"{st.a_planned_deg:.4f}", f"{st.a_actual_deg:.4f}",
                    f"{st.a_error_deg:.6f}",
                    f"{st.tension_N:.4f}",
                    f"{st.alpha_error_deg:.5f}",
                    f"{st.backlash_err_mm:.6f}",
                    f"{st.missed_step_err_mm:.6f}",
                    f"{st.spindle_lag_deg:.6f}",
                    int(st.is_turnaround),
                ])
        print(f"    Error Map CSV: {fpath} ({len(states)} segment)")
        return fpath

    # ── 6. Batch export ─────────────────────────────────────────

    def export_all(
        self,
        x_arr:      np.ndarray,
        a_arr_deg:  np.ndarray,
        f_arr:      np.ndarray,
        twin_state: DigitalTwinState,
        r_fn:       callable,
        alpha_rad:  float,
        z_arr:      Optional[np.ndarray] = None,
        rho_arr:    Optional[np.ndarray] = None,
        t_arr:      Optional[np.ndarray] = None,
        prefix:     str = "fw_",
    ) -> Dict[str, str]:
        """Tüm export'ları tek seferde çalıştır."""
        sim_states = twin_state.sim_result.states
        sim_a      = np.array([s.a_actual_deg for s in sim_states])

        files = {}
        files["toolpath"] = self.export_toolpath_json(
            x_arr, a_arr_deg, f_arr, r_fn, alpha_rad,
            sim_states, f"{prefix}toolpath.json", subsample=2)

        files["a_timeline"] = self.export_a_timeline(
            x_arr, a_arr_deg, f_arr, sim_a, f"{prefix}a_timeline.csv")

        files["error_map"] = self.export_error_map(
            sim_states, f"{prefix}error_map.csv")

        if z_arr is not None and rho_arr is not None and t_arr is not None:
            files["thickness"] = self.export_thickness_heatmap(
                z_arr, rho_arr, t_arr, f"{prefix}thickness.csv")

        files["report"] = self.export_simulation_report(
            twin_state, filename=f"{prefix}sim_report.txt")

        print(f"\n    ✓ {len(files)} export dosyası oluşturuldu:")
        for k, v in files.items():
            print(f"      {k:<12}: {os.path.basename(v)}")
        return files
