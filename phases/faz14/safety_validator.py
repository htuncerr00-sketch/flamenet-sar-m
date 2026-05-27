"""
safety_validator.py — AI Öneri Güvenlik Doğrulayıcısı
======================================================
AI katmanından gelen tüm öneriler bu sınıftan geçmeden uygulanamaz.
Physics constraints + machine limits + gradient limits.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np

@dataclass(frozen=True, slots=True)
class ValidationResult:
    approved:       bool
    v_approved:     float    # Onaylı hız (orijinal veya kırpılmış)
    T_approved:     float    # Onaylı gerilme hedefi
    curv_approved:  float    # Onaylı curvature faktörü
    reason:         str
    clipped:        bool     # Kırpma yapıldı mı?

class SafetyValidator:
    """
    AI → SafetyValidator → ControlThread pipeline'ının kritik noktası.
    Thread-safe (stateless methods — no shared mutable state).
    """
    # Hard limits (overrides all AI)
    V_ABS_MIN  = 20.0    # mm/s absolute minimum (motor stall)
    V_ABS_MAX  = 140.0   # mm/s absolute maximum
    T_ABS_MIN  = 3.0     # N (fiber slack)
    T_ABS_MAX  = 38.0    # N (fiber break margin)
    CURV_MIN   = 0.7     # Curvature factor lower bound
    CURV_MAX   = 1.3     # Curvature factor upper bound
    DELTA_V_MAX= 8.0     # mm/s max step (acceleration budget)
    DELTA_T_MAX= 2.0     # N max tension step

    def __init__(self, v_nominal: float = 100.0,
                 kappa_n_fn = None,   # callable(z) → kappa_n
                 a_centripetal: float = 5000.0):
        self.v_nom  = v_nominal
        self.a_cent = a_centripetal
        self._kappa_fn = kappa_n_fn

    def validate(self,
                 v_suggested: float, T_suggested: float,
                 curv_factor: float, v_current: float,
                 T_current: float, z_mm: float = 0.0) -> ValidationResult:
        """
        Önerilen parametreleri doğrula ve güvenli versiyonu döndür.
        Hiçbir zaman exception fırlat — her zaman bir sonuç döndür.
        """
        reasons = []
        clipped = False

        # 1. Absolute velocity bounds
        v_safe = float(np.clip(v_suggested, self.V_ABS_MIN, self.V_ABS_MAX))
        if v_safe != v_suggested:
            reasons.append(f"v_clipped[{v_suggested:.1f}→{v_safe:.1f}]"); clipped=True

        # 2. Rate limit (acceleration budget)
        dv = v_safe - v_current
        if abs(dv) > self.DELTA_V_MAX:
            v_safe = v_current + self.DELTA_V_MAX * (1.0 if dv>0 else -1.0)
            reasons.append(f"v_rate_limited"); clipped=True

        # 3. Physics: curvature speed limit
        if self._kappa_fn is not None:
            kn = self._kappa_fn(z_mm)
            if kn > 1e-9:
                v_phys = math.sqrt(self.a_cent / kn)
                if v_safe > v_phys:
                    v_safe = v_phys; reasons.append("v_curvature_limit"); clipped=True

        # 4. Tension bounds
        T_safe = float(np.clip(T_suggested, self.T_ABS_MIN, self.T_ABS_MAX))
        if T_safe != T_suggested:
            reasons.append(f"T_clipped[{T_suggested:.1f}→{T_safe:.1f}]"); clipped=True
        dT = T_safe - T_current
        if abs(dT) > self.DELTA_T_MAX:
            T_safe = T_current + self.DELTA_T_MAX * (1.0 if dT>0 else -1.0)
            reasons.append("T_rate_limited"); clipped=True

        # 5. Curvature factor bounds
        cf_safe = float(np.clip(curv_factor, self.CURV_MIN, self.CURV_MAX))
        if cf_safe != curv_factor:
            reasons.append("curv_clipped"); clipped=True

        reason_str = ",".join(reasons) if reasons else "approved"
        approved   = v_safe > 0 and T_safe > 0

        return ValidationResult(
            approved=approved, v_approved=v_safe, T_approved=T_safe,
            curv_approved=cf_safe, reason=reason_str, clipped=clipped,
        )

    def is_physics_safe(self, v: float, kappa_n: float) -> bool:
        """Curvature speed check — quick boolean."""
        if kappa_n < 1e-9: return True
        return v <= math.sqrt(self.a_cent / kappa_n) * 1.05
