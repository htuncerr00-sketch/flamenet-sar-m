"""
core/render_frame_builder.py — TwinSimulationResult → RenderFrame dönüştürücü (S4.2.3)
========================================================================================
RenderFrameBuilder: TwinSimulationResult + MandrelProfile verilerini alır,
her animasyon karesi için RenderFrame üretir.

Tasarım kuralları
-----------------
1. Fizik hesabı YOK — simulate_winding, solve_coverage, generate_path ÇAĞRILMAZ.
2. Tüm veri TwinSimulationResult'tan (önceden hesaplanmış) gelir.
3. __init__: vektörel ön-hesaplar (batch numpy, tek seferlik).
4. build(k): yalnız veri derleme + hafif koordinat dönüşümü → RenderFrame.
5. Shell: katman başına (unique radius) önceden hesaplanır; her karede yeniden üretilmez.
6. Heatmap: period=HEATMAP_PERIOD karede bir dirty=True; veri final_deposition'dan.
7. Qt/pyqtgraph bağımlılığı YOK → headless test edilebilir.
8. Koordinat birimi: METRE (mm / 1000).

Panel eksen sözleşmesi (entegre_tasarim_paneli.py ile uyumlu)
-------------------------------------------------------------
  panel-X = eksenel konum  (carriage taşıyıcı)
  panel-Y = radyal cos(A)
  panel-Z = radyal sin(A)
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

import numpy as np

from .render_frame import (
    RenderFrame,
    RenderSceneTopology,
    _ro,
    make_empty_frame,
)

# Heatmap renk LUT: kaplama sayısına göre RGBA
# 0 → mavi, 1 → yeşil, 2 → turuncu, 3+ → kırmızı
_HEATMAP_COLORS = np.array([
    [0.20, 0.40, 0.80, 0.70],   # 0: mavi (boş)
    [0.20, 0.75, 0.25, 0.85],   # 1: yeşil (tek kat)
    [0.95, 0.55, 0.10, 0.90],   # 2: turuncu (iki kat)
    [0.90, 0.15, 0.15, 0.95],   # 3+: kırmızı (çok kat)
], dtype=np.float32)

HEATMAP_PERIOD = 10   # Her kaç karede bir heatmap_dirty=True


def _cyl_shell_verts(
    z0_m: float, L_m: float, r_m: float,
    nz: int, nth: int,
) -> np.ndarray:
    """
    Silindirik kabuk yüzey koordinatları (Nz × Nth, 3) float32, metre.

    Eksen-X = eksenel (z0_m..z0_m+L_m), panel eksen sözleşmesine uygun.
    """
    z_vals = np.linspace(z0_m, z0_m + L_m, nz, dtype=np.float32)
    th_vals = np.linspace(0.0, 2.0 * math.pi, nth, endpoint=False, dtype=np.float32)

    # Yayın: (nz, nth)
    z_g = np.broadcast_to(z_vals[:, np.newaxis], (nz, nth))
    cos_g = r_m * np.cos(th_vals)[np.newaxis, :]
    sin_g = r_m * np.sin(th_vals)[np.newaxis, :]

    verts = np.empty((nz * nth, 3), dtype=np.float32)
    verts[:, 0] = z_g.ravel()
    verts[:, 1] = np.broadcast_to(cos_g, (nz, nth)).ravel()
    verts[:, 2] = np.broadcast_to(sin_g, (nz, nth)).ravel()
    return verts


def _shell_faces(nz: int, nth: int) -> np.ndarray:
    """
    Silindirik kabuk yüzey üçgenleri (2*(Nz-1)*Nth, 3) int32.
    """
    faces = np.empty((2 * (nz - 1) * nth, 3), dtype=np.int32)
    row = 0
    for iz in range(nz - 1):
        for it in range(nth):
            it_next = (it + 1) % nth
            a = iz * nth + it
            b = iz * nth + it_next
            c = (iz + 1) * nth + it
            d = (iz + 1) * nth + it_next
            faces[row]     = [a, b, c]
            faces[row + 1] = [c, b, d]
            row += 2
    return faces


def _heatmap_topology(
    z0_mm: float, z1_mm: float,
    profile_z_mm: np.ndarray, profile_r_mm: np.ndarray,
    nz: int, nth: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Isı haritası için profil-şekilli statik mesh (verts, faces).

    verts: (nz*(nth+1), 3) float32 — nth+1 sütun (sarma kapanır)
    faces: (2*(nz-1)*nth, 3) int32
    """
    z_vals = np.linspace(z0_mm, z1_mm, nz, dtype=np.float64)
    r_vals = np.interp(z_vals, profile_z_mm, profile_r_mm) / 1000.0  # → metre

    th_vals = np.linspace(0.0, 2.0 * math.pi, nth + 1, dtype=np.float32)

    n_col = nth + 1
    verts = np.empty((nz * n_col, 3), dtype=np.float32)
    for iz, (z_mm, r_m) in enumerate(zip(z_vals, r_vals)):
        x_m = (z_mm - float(profile_z_mm[0])) / 1000.0
        row_start = iz * n_col
        verts[row_start: row_start + n_col, 0] = x_m
        verts[row_start: row_start + n_col, 1] = (r_m * np.cos(th_vals)).astype(np.float32)
        verts[row_start: row_start + n_col, 2] = (r_m * np.sin(th_vals)).astype(np.float32)

    faces = np.empty((2 * (nz - 1) * nth, 3), dtype=np.int32)
    row = 0
    for iz in range(nz - 1):
        for it in range(nth):
            a = iz * n_col + it
            b = iz * n_col + it + 1
            c = (iz + 1) * n_col + it
            d = (iz + 1) * n_col + it + 1
            faces[row]     = [a, b, c]
            faces[row + 1] = [c, b, d]
            row += 2
    return verts.astype(np.float32), faces


def _deposition_to_heatmap_vc(
    dep,                    # DepositionMap | CoverageMap | None
    topology: RenderSceneTopology,
) -> np.ndarray:
    """
    Deposition/Coverage haritasını heatmap vertex renk dizisine çevir.

    Çıktı: (nz_h*(nth_h+1), 4) float32.
    """
    nz_h, nth_h = topology.heatmap_nz, topology.heatmap_nth
    n_hm = nz_h * (nth_h + 1)
    vc = np.zeros((n_hm, 4), dtype=np.float32)
    vc[:, :] = _HEATMAP_COLORS[0]   # varsayılan: mavi

    if dep is None:
        return vc

    # DepositionMap veya CoverageMap — ortak arayüz: z_bins, count alanı
    z_bins = getattr(dep, 'z_bins', None)
    count_2d = getattr(dep, 'coverage_count', None)
    if count_2d is None:
        count_2d = getattr(dep, 'count', None)
    if z_bins is None or count_2d is None:
        return vc

    z_bins = np.asarray(z_bins, dtype=np.float64)
    count_2d = np.asarray(count_2d, dtype=np.int32)    # (Nz_dep, Ntheta_dep)

    # LOD çözünürlüğüne downsample (ortalama)
    nz_dep, nth_dep = count_2d.shape

    # Her heatmap z satırı için deposition'da karşılık gelen satırı bul
    hm_z = np.linspace(float(z_bins[0]), float(z_bins[-1]), nz_h, dtype=np.float64)

    for iz_h in range(nz_h):
        iz_d = int(np.clip(
            np.searchsorted(z_bins, hm_z[iz_h]) - 1,
            0, nz_dep - 1,
        ))
        dep_row = count_2d[iz_d]           # (Ntheta_dep,)

        # Theta yeniden örnekle: nth_dep → nth_h sütunları
        th_idx = (np.arange(nth_h) * nth_dep / nth_h).astype(int)
        dep_row_lod = dep_row[th_idx]      # (nth_h,)

        row_start = iz_h * (nth_h + 1)
        for it in range(nth_h + 1):
            c = int(min(dep_row_lod[it % nth_h], 3))
            vc[row_start + it] = _HEATMAP_COLORS[c]

    return vc


# ─────────────────────────────────────────────────────────────────────────────
# RenderFrameBuilder
# ─────────────────────────────────────────────────────────────────────────────

class RenderFrameBuilder:
    """
    TwinSimulationResult → RenderFrame dönüştürücü.

    Kullanım
    --------
    >>> builder = RenderFrameBuilder(twin, profile, topology, tow_width_mm=6.0)
    >>> frame = builder.build(k)          # k: durum indeksi

    Ön hesaplamalar (init'te, tek seferlik)
    ----------------------------------------
    - Tüm TwinState alanları numpy dizilerine alınır.
    - contact_xyz ve eye_xyz batch olarak dünya koordinatlarına çevrilir.
    - Ribbon sol/sağ kenarları batch olarak hesaplanır.
    - Shell vertex dizileri her katman için önceden hesaplanır.
    - Heatmap vertex renkleri final_deposition'dan üretilir.
    """

    def __init__(
        self,
        twin,                               # TwinSimulationResult
        profile,                            # MandrelProfile
        topology: RenderSceneTopology,
        tow_width_mm: float = 6.0,
        deposition=None,                    # DepositionMap | CoverageMap | None
    ) -> None:
        self._topo = topology
        self._tow_half_m = float(tow_width_mm) * 0.5 / 1000.0
        self._n_states = len(twin.states)

        profile_z = np.asarray(profile.z_mm, dtype=np.float64)
        profile_r = np.asarray(profile.r_mm, dtype=np.float64)
        self._z0_mm = float(profile_z[0])
        self._L_m = (float(profile_z[-1]) - float(profile_z[0])) / 1000.0

        states = twin.states

        # ── Skalerleri diziye topla ──────────────────────────────────────────
        self._spindle_deg = np.array([s.spindle_angle_deg for s in states], np.float32)
        self._carriage_mm = np.array([s.carriage_x_actual_mm for s in states], np.float32)
        self._radius_mm   = np.array([s.current_radius_mm for s in states], np.float32)
        self._eye_x_mm    = np.array([s.eye_x_mm for s in states], np.float32)
        self._eye_r_mm    = np.array([s.eye_r_mm for s in states], np.float32)
        self._layer       = np.array([s.current_layer for s in states], np.int32)
        self._circuit     = np.array([s.current_circuit for s in states], np.int32)
        self._fiber_dep   = np.array([s.fiber_deposited_mm for s in states], np.float32)
        self._progress    = np.array([s.progress_pct for s in states], np.float32)

        # ── Batch dünya koordinat projeksiyonu ──────────────────────────────
        a_rad = np.radians(self._spindle_deg.astype(np.float64))
        cos_a = np.cos(a_rad).astype(np.float32)
        sin_a = np.sin(a_rad).astype(np.float32)

        cx = (self._carriage_mm - self._z0_mm) / 1000.0
        r_m = self._radius_mm / 1000.0
        self._contact_xyz = np.column_stack([
            cx, r_m * cos_a, r_m * sin_a,
        ]).astype(np.float32)           # (N, 3)

        ex = (self._eye_x_mm - self._z0_mm) / 1000.0
        er_m = self._eye_r_mm / 1000.0
        self._eye_xyz = np.column_stack([
            ex, er_m * cos_a, er_m * sin_a,
        ]).astype(np.float32)           # (N, 3)

        # ── Ribbon kenarları: batch hesapla ──────────────────────────────────
        self._ribbon_L, self._ribbon_R = self._batch_ribbon_edges(
            self._contact_xyz, self._spindle_deg,
            self._carriage_mm, profile,
        )

        # ── Shell: her katman için vertex dizisi ─────────────────────────────
        nz_s  = topology.shell_nz
        nth_s = topology.shell_nth
        unique_layers = sorted(set(self._layer.tolist()))
        base_r_mm = float(twin.base_radius_mm)
        final_r_mm = float(twin.final_radius_mm)
        n_layers = max(int(twin.n_layers), 1)

        self._shell_by_layer: dict[int, np.ndarray] = {}
        for lyr in unique_layers:
            t = lyr / max(n_layers - 1, 1)
            r_lyr_mm = base_r_mm + t * (final_r_mm - base_r_mm)
            self._shell_by_layer[lyr] = _cyl_shell_verts(
                0.0, self._L_m, r_lyr_mm / 1000.0, nz_s, nth_s,
            )

        # Shell renk LUT: katman derinliğine göre RGBA (sarı → turuncu)
        self._shell_color_by_layer: dict[int, np.ndarray] = {}
        n_shell = topology.shell_n_verts
        for lyr in unique_layers:
            t = lyr / max(n_layers - 1, 1)
            r = 1.0
            g = float(0.78 - 0.4 * t)
            b = float(0.18 - 0.18 * t)
            colors = np.tile(
                np.array([r, g, b, 0.35], dtype=np.float32),
                (n_shell, 1),
            )
            self._shell_color_by_layer[lyr] = colors

        # ── Heatmap vertex renkleri (sabit) ──────────────────────────────────
        dep = deposition if deposition is not None else getattr(twin, 'final_deposition', None)
        self._heatmap_vc_base = _deposition_to_heatmap_vc(dep, topology)

        # ── Ribbon ön-alloc tamponu ──────────────────────────────────────────
        max_seg = topology.ribbon_max_seg
        self._rib_verts_buf = np.zeros((2 * max_seg, 3), dtype=np.float32)
        self._rib_faces_buf = np.zeros((2 * max(max_seg - 1, 0), 3), dtype=np.int32)
        self._ribbon_max_seg = max_seg

    # ── Ön hesap: ribbon kenarları ───────────────────────────────────────────

    def _batch_ribbon_edges(
        self,
        contact_xyz: np.ndarray,       # (N, 3) float32
        spindle_deg: np.ndarray,        # (N,) float32
        carriage_mm: np.ndarray,        # (N,) float32
        profile,                        # MandrelProfile
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """
        Tüm kare dizisi için ribbon sol/sağ kenarlarını batch hesapla.

        Her temas noktası için: kenar = merkez ± (w/2)·ŵ
        ŵ = normalize(N × t),  N = yüzey normali,  t = merkez tanjantı
        """
        try:
            from .fiber_contact_model import _surface_normal_unit
        except Exception:
            return None, None

        n = len(contact_xyz)
        if n < 2:
            return None, None

        P = contact_xyz.astype(np.float64)

        # Merkez çizgisi tanjantı (ileri fark; son nokta geri fark)
        t_vec = np.empty_like(P)
        t_vec[:-1] = P[1:] - P[:-1]
        t_vec[-1]  = P[-1] - P[-2]
        tn = np.linalg.norm(t_vec, axis=1, keepdims=True)
        t_vec /= np.maximum(tn, 1e-12)

        # Yüzey normali — panel çerçevesine remap (Xr,Yr,Zax)→(Zax,Xr,Yr)
        a_rad = np.radians(spindle_deg.astype(np.float64))
        N_arr = np.empty((n, 3), dtype=np.float64)
        try:
            for i in range(n):
                nm = _surface_normal_unit(profile, float(carriage_mm[i]), float(a_rad[i]))
                N_arr[i, 0] = nm[2]   # eksenel → panel X
                N_arr[i, 1] = nm[0]   # radyal x → panel Y
                N_arr[i, 2] = nm[1]   # radyal y → panel Z
        except Exception:
            return None, None

        w = np.cross(N_arr, t_vec)
        wn = np.linalg.norm(w, axis=1, keepdims=True)
        w /= np.maximum(wn, 1e-12)

        left  = (P + self._tow_half_m * w).astype(np.float32)
        right = (P - self._tow_half_m * w).astype(np.float32)
        return left, right

    # ── Ana build ────────────────────────────────────────────────────────────

    def build(self, k: int) -> RenderFrame:
        """
        k-inci TwinState'i RenderFrame'e dönüştür.

        k < 0 ya da k >= n_states için ilk/son durum kullanılır.
        """
        n = self._n_states
        k = max(0, min(k, n - 1))

        eye_xyz     = self._eye_xyz[k].copy()
        contact_xyz = self._contact_xyz[k].copy()

        # ── Ribbon (birikimli, k-inci kareye kadar) ──────────────────────────
        k1     = k + 1
        max_s  = self._ribbon_max_seg
        seg_k  = min(k1, max_s)          # kaç segment gösterilecek

        if seg_k >= 2 and self._ribbon_L is not None:
            start = max(0, k1 - max_s)
            L = self._ribbon_L[start: start + seg_k]
            R = self._ribbon_R[start: start + seg_k]
            m = len(L)

            # Ön-alloc tamponuna yaz
            self._rib_verts_buf[:2 * m:2] = L
            self._rib_verts_buf[1:2 * m:2] = R

            # Faces: quad şerit üçgenlemesi
            if m >= 2:
                for i in range(m - 1):
                    self._rib_faces_buf[2 * i]     = [2*i,   2*i+2, 2*i+1]
                    self._rib_faces_buf[2 * i + 1] = [2*i+1, 2*i+2, 2*i+3]
                ribbon_verts = self._rib_verts_buf[:2 * m].copy()
                ribbon_faces = self._rib_faces_buf[:2 * (m - 1)].copy()
            else:
                ribbon_verts = np.zeros((0, 3), dtype=np.float32)
                ribbon_faces = np.zeros((0, 3), dtype=np.int32)
        else:
            ribbon_verts = np.zeros((0, 3), dtype=np.float32)
            ribbon_faces = np.zeros((0, 3), dtype=np.int32)

        # ── Shell ────────────────────────────────────────────────────────────
        lyr = int(self._layer[k])
        shell_verts  = self._shell_by_layer.get(lyr)
        shell_colors = self._shell_color_by_layer.get(lyr)
        if shell_verts is None:
            shell_verts  = np.zeros((self._topo.shell_n_verts, 3), dtype=np.float32)
            shell_colors = np.zeros((self._topo.shell_n_verts, 4), dtype=np.float32)
        else:
            shell_verts  = shell_verts.copy()
            shell_colors = shell_colors.copy()

        # ── Heatmap ──────────────────────────────────────────────────────────
        heatmap_vc    = self._heatmap_vc_base.copy()
        heatmap_dirty = (k % HEATMAP_PERIOD == 0)

        return RenderFrame(
            spindle_angle_deg=float(self._spindle_deg[k]),
            carriage_x_mm=float(self._carriage_mm[k]),
            eye_xyz=eye_xyz,
            contact_xyz=contact_xyz,
            ribbon_verts=ribbon_verts,
            ribbon_faces=ribbon_faces,
            shell_verts=shell_verts,
            shell_colors=shell_colors,
            heatmap_vc=heatmap_vc,
            heatmap_dirty=heatmap_dirty,
            frame_idx=k,
            progress_pct=float(self._progress[k]),
            current_radius_mm=float(self._radius_mm[k]),
            fiber_deposited_mm=float(self._fiber_dep[k]),
            layer=lyr,
            circuit=int(self._circuit[k]),
        )

    def make_topology(self) -> RenderSceneTopology:
        """Mevcut topology'yi döndür (değişmez)."""
        return self._topo

    @property
    def n_states(self) -> int:
        return self._n_states

    # ── Fabrika: topology oluştur ────────────────────────────────────────────

    @classmethod
    def build_topology(
        cls,
        profile,                    # MandrelProfile
        shell_nz: int = 20,
        shell_nth: int = 32,
        heatmap_nz: int = 16,
        heatmap_nth: int = 24,
        ribbon_max_seg: int = 300,
    ) -> RenderSceneTopology:
        """
        MandrelProfile'den RenderSceneTopology üret.

        Renderer'ların setup() sırasında bir kez çağrılır.
        """
        profile_z = np.asarray(profile.z_mm, dtype=np.float64)
        profile_r = np.asarray(profile.r_mm, dtype=np.float64)
        z0 = float(profile_z[0])
        z1 = float(profile_z[-1])

        s_faces = _shell_faces(shell_nz, shell_nth)

        hm_verts, hm_faces = _heatmap_topology(
            z0, z1, profile_z, profile_r, heatmap_nz, heatmap_nth,
        )

        return RenderSceneTopology(
            shell_faces=s_faces,
            shell_n_verts=shell_nz * shell_nth,
            heatmap_verts=hm_verts,
            heatmap_faces=hm_faces,
            shell_nz=shell_nz,
            shell_nth=shell_nth,
            heatmap_nz=heatmap_nz,
            heatmap_nth=heatmap_nth,
            ribbon_max_seg=ribbon_max_seg,
        )


__all__ = [
    "RenderFrameBuilder",
    "HEATMAP_PERIOD",
]
