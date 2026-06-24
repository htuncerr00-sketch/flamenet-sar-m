"""
app/renderers/fiber_geometry.py — Serbest fiber geometri yardımcıları (S6.15.1)
=================================================================================
Nozul ucu türetme + eğri (quadratic bezier) örnekleme. Saf numpy; Qt/pyqtgraph
bağımlılığı YOK — headless test edilebilir.

Neden gerekli
-------------
Builder'ın ``eye_xyz`` alanı GERÇEK payout dinamiğinden gelir: standoff=150mm,
lead=standoff·tan(α) ⇒ nozul mandrelden ~150mm dışarıda + ~214mm yanda. Bu fizik
için doğru ama GÖRSEL için kopuk (CAD hissi). Render için nozulu temas noktasından
makul bir standoff ile TÜRETİRİZ; böylece serbest fiber daima sarım kafasına bağlanır.

Panel eksen sözleşmesi: X = eksenel (mandrel ekseni), (Y, Z) = radyal düzlem.
Birim: METRE.
"""
from __future__ import annotations

import numpy as np


def derive_nozzle_point(
    eye_xyz,
    contact_xyz,
    standoff_factor: float = 0.7,
    min_standoff_m: float = 0.030,
    lead_factor: float = 0.45,
):
    """
    Temas noktasından görsel olarak makul bir nozul ucu türet.

    Nozul = temas + standoff·radial_hat + lead·axial_hat

    - radial_hat : temas noktasının radyal dış yönü (X ekseni etrafında).
    - standoff   : max(standoff_factor · r_yüzey, min_standoff_m).
                   Mandrel yarıçapıyla ölçeklenir; çok küçük mandrelde taban değer.
    - lead       : eksenel öncülük; yönü gerçek ``eye_xyz``'nin eksenel tarafından alınır.

    Dönüş: (3,) float32 — panel dünya koordinatı (metre).
    """
    c = np.asarray(contact_xyz, dtype=np.float64).reshape(3)
    e = np.asarray(eye_xyz, dtype=np.float64).reshape(3)

    # Radyal yön: X ekseni mandrel ekseni → radyal bileşen (0, y, z)
    radial = np.array([0.0, c[1], c[2]], dtype=np.float64)
    r_surf = float(np.linalg.norm(radial))
    if r_surf < 1e-9:
        radial_hat = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        r_surf = 0.0
    else:
        radial_hat = radial / r_surf

    standoff = max(standoff_factor * r_surf, min_standoff_m)

    dx = e[0] - c[0]
    lead_dir = 1.0 if dx >= 0.0 else -1.0
    axial = np.array([lead_dir * lead_factor * standoff, 0.0, 0.0], dtype=np.float64)

    nozzle = c + standoff * radial_hat + axial
    return nozzle.astype(np.float32)


def sample_quadratic_bezier(p0, p1, p2, n: int = 18):
    """
    Quadratic bezier eğrisini n nokta ile örnekle.

    B(t) = (1-t)²·p0 + 2(1-t)t·p1 + t²·p2,  t ∈ [0, 1]

    Dönüş: (n, 3) float32.
    """
    p0 = np.asarray(p0, dtype=np.float64).reshape(3)
    p1 = np.asarray(p1, dtype=np.float64).reshape(3)
    p2 = np.asarray(p2, dtype=np.float64).reshape(3)
    n = max(2, int(n))
    t = np.linspace(0.0, 1.0, n, dtype=np.float64).reshape(n, 1)
    one_m = 1.0 - t
    pts = (one_m * one_m) * p0 + (2.0 * one_m * t) * p1 + (t * t) * p2
    return pts.astype(np.float32)


def free_fiber_curve(eye_xyz, contact_xyz, n: int = 18, bow_factor: float = 0.16):
    """
    Nozul → temas arası eğri serbest fiber şeridini örnekle.

    1. Nozul ucu temas noktasından türetilir (``derive_nozzle_point``).
    2. Kontrol noktası, kiriş orta noktasının radyal dışa hafif bombelenmesidir
       (gergin fiberin nozul kılavuzundan yüzeye yumuşak inişi).

    Dönüş: (n, 3) float32 — line_strip için hazır.
    """
    nozzle = derive_nozzle_point(eye_xyz, contact_xyz).astype(np.float64)
    contact = np.asarray(contact_xyz, dtype=np.float64).reshape(3)

    radial = np.array([0.0, contact[1], contact[2]], dtype=np.float64)
    r_surf = float(np.linalg.norm(radial))
    radial_hat = (radial / r_surf) if r_surf > 1e-9 else np.array([0.0, 1.0, 0.0])

    mid = 0.5 * (nozzle + contact)
    chord_len = float(np.linalg.norm(nozzle - contact))
    ctrl = mid + bow_factor * chord_len * radial_hat

    return sample_quadratic_bezier(nozzle, ctrl, contact, n=n)


__all__ = ["derive_nozzle_point", "sample_quadratic_bezier", "free_fiber_curve"]
