"""
core/stl_processor.py — STL Dosya İşleyici
============================================
numpy-stl bağımlılığı olmadan binary/ASCII STL ayrıştırır.
MandrelProfile çıkartmak için geometry_engine ile birlikte kullanılır.
"""
from __future__ import annotations
import struct
from typing import Tuple

import numpy as np


def parse_stl_vertices(filepath: str) -> np.ndarray:
    """
    Binary veya ASCII STL dosyasını ayrıştır.
    Döner: (N, 3) float64 vertex dizisi.
    """
    with open(filepath, 'rb') as f:
        header = f.read(80)

    # ASCII mi binary mi?
    try:
        header_text = header.decode('ascii', errors='ignore').strip().lower()
    except Exception:
        header_text = ''

    if header_text.startswith('solid'):
        # Önce ASCII dene
        try:
            verts = _parse_ascii(filepath)
            if len(verts) > 0:
                return verts
        except Exception:
            pass

    return _parse_binary(filepath)


def _parse_binary(filepath: str) -> np.ndarray:
    """Binary STL: 80B başlık + 4B uint32 + N*(12 float + 2B attrib)."""
    with open(filepath, 'rb') as f:
        f.read(80)
        n_tri_bytes = f.read(4)
        if len(n_tri_bytes) < 4:
            return np.empty((0, 3), dtype=np.float64)
        n_tri = struct.unpack('<I', n_tri_bytes)[0]
        verts = []
        for _ in range(n_tri):
            data = f.read(50)
            if len(data) < 50:
                break
            # normal (12 B) + v1 + v2 + v3 (3×12 B) + attrib (2 B)
            for vi in range(3):
                offset = 12 + vi * 12
                v = struct.unpack_from('<fff', data, offset)
                verts.append(v)
    return np.array(verts, dtype=np.float64).reshape(-1, 3) if verts else np.empty((0, 3), dtype=np.float64)


def _parse_ascii(filepath: str) -> np.ndarray:
    """ASCII STL: 'vertex x y z' satırlarını ara."""
    verts = []
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith('vertex '):
                parts = stripped.split()
                if len(parts) == 4:
                    verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
    return np.array(verts, dtype=np.float64).reshape(-1, 3) if verts else np.empty((0, 3), dtype=np.float64)


def load_stl_profile(filepath: str, n_points: int = 500):
    """
    STL → MandrelProfile.
    Dönel simetri varsayar (Z dönme ekseni).
    """
    from .geometry_engine import MandrelProfile
    verts = parse_stl_vertices(filepath)
    return MandrelProfile._profile_from_vertices(verts, n_points)


def validate_stl_symmetry(filepath: str) -> Tuple[bool, float]:
    """
    STL dönel simetri doğrulaması.
    Döner: (is_symmetric, max_asymmetry_mm).
    Her z diliminde min_r ve max_r arasındaki farkı kontrol eder.
    """
    verts = parse_stl_vertices(filepath)
    if len(verts) == 0:
        return False, 0.0
    x, y, z = verts[:, 0], verts[:, 1], verts[:, 2]
    r = np.sqrt(x ** 2 + y ** 2)
    z_min, z_max = z.min(), z.max()
    n_bins = 50
    z_bins = np.linspace(z_min, z_max, n_bins + 1)
    max_asym = 0.0
    for i in range(n_bins):
        mask = (z >= z_bins[i]) & (z < z_bins[i + 1])
        if mask.sum() > 1:
            r_slice = r[mask]
            asym = float(r_slice.max() - r_slice.min())
            max_asym = max(max_asym, asym)
    threshold = 5.0  # 5 mm asimetri toleransı
    return max_asym <= threshold, max_asym
