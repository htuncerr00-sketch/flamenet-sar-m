"""
core/render_frame.py — Tek kare render verisi (S4.2.1)
=======================================================
RenderFrame: TwinState'ten türetilen, renderer'lara aktarılan salt-okunur veri.

Tasarım kuralları
-----------------
1. frozen=True → renderer alan ataması yapamaz.
2. Her ndarray.flags.writeable = False → renderer içerik yazamaz.
3. Qt / pyqtgraph bağımlılığı YOK — backend'de yaşar, headless test edilebilir.
4. Fizik / geometri hesabı YOK — yalnız RenderFrameBuilder bu veriyi üretir.
5. Dünya koordinat birimi: METRE (mm/1000).

Statik sahne verisi (frame başına taşınmayan) için: RenderSceneTopology.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# Yardımcı: ndarray'leri read-only kilitle (view ya da kopyasız çalışır)
# ─────────────────────────────────────────────────────────────────────────────

def _ro(arr: np.ndarray) -> np.ndarray:
    """Array'i (veya view'ını) read-only yap. Yeni allocation yok."""
    if arr is None:
        return arr
    if arr.flags.writeable:
        arr.flags.writeable = False
    return arr


def _freeze_frame(frame: "RenderFrame") -> None:
    """Oluşturulduktan sonra tüm ndarray alanlarını read-only yap."""
    for name in _ARRAY_FIELDS:
        v = getattr(frame, name, None)
        if isinstance(v, np.ndarray):
            _ro(v)


_ARRAY_FIELDS = (
    "eye_xyz",
    "contact_xyz",
    "ribbon_verts",
    "ribbon_faces",
    "shell_verts",
    "shell_colors",
    "heatmap_vc",
)


# ─────────────────────────────────────────────────────────────────────────────
# RenderSceneTopology — Tüm oturumda statik olan veri
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RenderSceneTopology:
    """
    Animasyon boyunca değişmeyen mesh topology'si.

    RenderFrameBuilder.__init__'te bir kez oluşturulur.
    Renderer'lara setup() içinde verilir; frame başına kopyalanmaz.
    """
    # Shell
    shell_faces:     np.ndarray   # (2*(Nz-1)*Nth, 3) int32 — sabit
    shell_n_verts:   int          # Nz * Nth

    # Heatmap
    heatmap_verts:   np.ndarray   # (Nz_h*(Nth_h+1), 3) float32 — sabit
    heatmap_faces:   np.ndarray   # (2*(Nz_h-1)*Nth_h, 3) int32 — sabit

    # LOD parametreleri
    shell_nz:  int
    shell_nth: int
    heatmap_nz:  int
    heatmap_nth: int
    ribbon_max_seg: int

    def __post_init__(self):
        # Topology dizileri de read-only olsun
        for name in ("shell_faces", "heatmap_verts", "heatmap_faces"):
            v = object.__getattribute__(self, name)
            if isinstance(v, np.ndarray) and v.flags.writeable:
                v.flags.writeable = False


# ─────────────────────────────────────────────────────────────────────────────
# RenderFrame — Tek animasyon karesi
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RenderFrame:
    """
    Bir animasyon karesinin render verisi. Tamamen salt-okunur.

    Koordinat birimi: METRE  (mm/1000 dönüşümü RenderFrameBuilder'da)

    Ribbon
    ------
    ribbon_verts : (2*k, 3) float32  — L[0],R[0], L[1],R[1], … (k=aktif segment)
    ribbon_faces : (2*(k-1), 3) int32 — (k-1) quad = 2*(k-1) üçgen

    Shell
    -----
    shell_verts  : (Nz*Nth, 3) float32  — profil şekilli kabuk
    shell_colors : (Nz*Nth, 4) float32  — katman derinliği renk LUT

    Heatmap
    -------
    heatmap_vc    : (Nz_h*(Nth_h+1), 4) float32 — per-vertex renk (değişen)
    heatmap_dirty : bool — True ise renderer güncelleme yapmalı, False ise atla
    """

    # ── Makine kinematiği ────────────────────────────────────────────────────
    spindle_angle_deg: float       # A ekseni kümülatif
    carriage_x_mm:     float       # Gerçek taşıyıcı konumu (dinamik gecikmeli)

    # ── Payout + temas noktası (dünya koordinatları, metre) ─────────────────
    eye_xyz:     np.ndarray        # (3,) float32
    contact_xyz: np.ndarray        # (3,) float32

    # ── Tow ribbon ──────────────────────────────────────────────────────────
    ribbon_verts: np.ndarray       # (2*k, 3) float32 — k aktif segment
    ribbon_faces: np.ndarray       # (2*(k-1), 3) int32

    # ── Fiber kabuk (katman büyümesi) ────────────────────────────────────────
    shell_verts:  np.ndarray       # (Nz*Nth, 3) float32
    shell_colors: np.ndarray       # (Nz*Nth, 4) float32

    # ── Kaplama ısı haritası ─────────────────────────────────────────────────
    heatmap_vc:    np.ndarray      # (Nz_h*(Nth_h+1), 4) float32
    heatmap_dirty: bool            # False → renderer bu frame'de setMeshData atlar

    # ── Metadata (UI + LOD yönetimi) ─────────────────────────────────────────
    frame_idx:          int
    progress_pct:       float      # [0, 100]
    current_radius_mm:  float
    fiber_deposited_mm: float
    layer:              int
    circuit:            int

    def __post_init__(self):
        # Tüm array alanlarını read-only kilitle (frozen=True sonrası)
        _freeze_frame(self)

    # ── Kullanışlı erişimciler ───────────────────────────────────────────────

    @property
    def ribbon_n_segments(self) -> int:
        """Aktif ribbon segment sayısı."""
        n = len(self.ribbon_verts)
        return n // 2 if n >= 2 else 0

    @property
    def is_shell_visible(self) -> bool:
        """Kabuk çizilmeli mi? (Başlangıç yarıçapından büyükse)"""
        return self.current_radius_mm > 0

    @property
    def carriage_x_m(self) -> float:
        return self.carriage_x_mm / 1000.0


# ─────────────────────────────────────────────────────────────────────────────
# Fabrika: boş / dejenere kare (renderer'lar için güvenli başlangıç)
# ─────────────────────────────────────────────────────────────────────────────

def make_empty_frame(
    topology: "Optional[RenderSceneTopology]" = None,
) -> RenderFrame:
    """
    Tüm array'leri boş/sıfır olan başlangıç karesi üretir.
    Renderer setup'ında kullanılır.
    """
    n_shell = topology.shell_n_verts if topology else 0
    n_hm    = (topology.heatmap_nz * (topology.heatmap_nth + 1)
               if topology else 0)

    frame = RenderFrame(
        spindle_angle_deg=0.0,
        carriage_x_mm=0.0,
        eye_xyz     =_ro(np.zeros(3, dtype=np.float32)),
        contact_xyz =_ro(np.zeros(3, dtype=np.float32)),
        ribbon_verts=_ro(np.zeros((0, 3), dtype=np.float32)),
        ribbon_faces=_ro(np.zeros((0, 3), dtype=np.int32)),
        shell_verts =_ro(np.zeros((max(n_shell, 1), 3), dtype=np.float32)),
        shell_colors=_ro(np.zeros((max(n_shell, 1), 4), dtype=np.float32)),
        heatmap_vc  =_ro(np.zeros((max(n_hm, 1), 4),   dtype=np.float32)),
        heatmap_dirty=False,
        frame_idx=0, progress_pct=0.0,
        current_radius_mm=0.0, fiber_deposited_mm=0.0,
        layer=0, circuit=0,
    )
    return frame


__all__ = [
    "RenderFrame",
    "RenderSceneTopology",
    "make_empty_frame",
]
