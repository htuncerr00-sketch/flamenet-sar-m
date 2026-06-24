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


def extrude_ribbon(verts, faces, thickness_m: float):
    """
    Düz (sıfır kalınlık) ribbon şeridini radyal dışa doğru ötele → hacimli bant.

    Girdi ribbon yapısı (RenderFrameBuilder): verts (2m, 3) iç içe L0,R0,L1,R1,…
    faces (2*(m-1), 3) quad-şerit üçgenlemesi.

    Çıktı: alt yüzey (orijinal) + üst yüzey (radyal +thickness) + iki kenar duvarı
    (L ve R) → gerçek prepreg bandı gibi kesit hacmi.

    Panel eksen sözleşmesi: X = eksenel; radyal yön = (0, y, z) normalize.

    Dönüş: (verts2 (4m,3) float32, faces2 (N,3) int32).
    Geçersiz/boş girdide girdiyi olduğu gibi döndürür.
    """
    verts = np.asarray(verts, dtype=np.float32)
    faces = np.asarray(faces, dtype=np.int32)
    n = verts.shape[0]
    if n < 4 or faces.shape[0] == 0 or n % 2 != 0:
        return verts, faces

    # Radyal dış birim vektör (X ekseni = mandrel ekseni)
    rad = verts.astype(np.float64).copy()
    rad[:, 0] = 0.0
    rnorm = np.linalg.norm(rad, axis=1, keepdims=True)
    rhat = rad / np.maximum(rnorm, 1e-12)
    top = (verts.astype(np.float64) + float(thickness_m) * rhat).astype(np.float32)

    verts2 = np.vstack([verts, top])          # (2n, 3): [alt | üst]

    m = n // 2                                 # segment-vertex çifti sayısı
    i = np.arange(m - 1, dtype=np.int32)       # segment indeksleri

    bottom_f = faces                           # alt yüzey (orijinal sargı)
    top_f = faces[:, ::-1] + n                 # üst yüzey (ters sargı, +n ofset)

    # L kenar duvarı (çift indeksler: 2i)
    Lw = np.empty((2 * (m - 1), 3), dtype=np.int32)
    Lw[0::2] = np.column_stack([2 * i,     2 * i + 2,     2 * i + n])
    Lw[1::2] = np.column_stack([2 * i + 2, 2 * i + 2 + n, 2 * i + n])

    # R kenar duvarı (tek indeksler: 2i+1)
    Rw = np.empty((2 * (m - 1), 3), dtype=np.int32)
    Rw[0::2] = np.column_stack([2 * i + 1, 2 * i + 1 + n, 2 * i + 3])
    Rw[1::2] = np.column_stack([2 * i + 3, 2 * i + 1 + n, 2 * i + 3 + n])

    faces2 = np.vstack([bottom_f, top_f, Lw, Rw]).astype(np.int32)
    return verts2, faces2


__all__ = [
    "derive_nozzle_point",
    "sample_quadratic_bezier",
    "free_fiber_curve",
    "extrude_ribbon",
]
