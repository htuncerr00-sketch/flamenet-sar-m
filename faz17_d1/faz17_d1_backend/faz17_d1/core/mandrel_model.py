"""
core/mandrel_model.py — MandrelModel Kompozit Çekirdek Nesnesi
==============================================================
STL_CEKIRDEK_ENTEGRASYON_RAPORU §8 + MANDREL_MODEL_TASARIM.md onayı.

Seçenek C: MandrelProfile SADE geometri olarak kalır (downstream sözleşmesi);
MandrelModel onu *sarar* ve STL zekâsını (özellikle confidence) pipeline boyunca
kayıpsız taşır.

İlke: Model "akıllı" değil **derli toplu**. İş mantığı stl_intelligence'de;
model yalnız veri konteyneri + erişim/kapı + serialization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np

from .geometry_engine import MandrelProfile
from .stl_intelligence import (
    AxisResult, ConfidenceReport, PoleRegion, QualityReport,
    RegionSegmentation, Segment, StlIntelligenceReport,
    TurnaroundCandidate, TurnaroundCandidates,
)


@dataclass
class MandrelModel:
    """
    Mandrel kompozit kökü: sade geometri + STL zekâsı.

    Backend (geodesic/coverage/twin) bu nesneyi GÖRMEZ — yalnız `as_profile()`
    sınırından beslenir. Zengin alanlar UI/render tarafından tüketilir;
    `confidence` her aşamada kapı olarak okunur.
    """
    # ── Zorunlu çekirdek ──────────────────────────────────────────────
    profile: MandrelProfile
    source_type: str = "parametric"           # "stl" | "parametric"

    # ── STL zekâsı (opsiyonel) ────────────────────────────────────────
    axis: Optional[AxisResult] = None
    confidence: Optional[ConfidenceReport] = None
    quality: Optional[QualityReport] = None
    segments: Optional[RegionSegmentation] = None
    pole_regions: Optional[List[PoleRegion]] = None
    turnaround_candidates: Optional[TurnaroundCandidates] = None

    # ── Metadata ──────────────────────────────────────────────────────
    source_path: Optional[str] = None
    units: str = "mm"
    model_version: int = 1
    created_at: Optional[str] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc).isoformat()

    # ── Davranış (erişim/kapı — iş mantığı DEĞİL) ─────────────────────

    def as_profile(self) -> MandrelProfile:
        """Downstream'e sade MandrelProfile ver (backward compatibility)."""
        return self.profile

    def is_winding_ready(self) -> bool:
        """
        Confidence/quality kapısı: simülasyona/coverage'a girmeli mi?
        Parametrik profil her zaman hazırdır (zekâ yok ama geometri kesin).
        """
        if self.source_type == "parametric":
            return True
        if self.confidence is not None and self.confidence.grade == "REDDET":
            return False
        if self.quality is not None and not self.quality.winding_suitable:
            return False
        return True

    def cylinder_segments(self) -> List[Segment]:
        return self.segments.by_kind("cylinder") if self.segments else []

    def dome_segments(self) -> List[Segment]:
        return self.segments.by_kind("dome") if self.segments else []

    # ── Fabrikalar ────────────────────────────────────────────────────

    @classmethod
    def from_stl(cls, report: StlIntelligenceReport) -> "MandrelModel":
        """STL Intelligence raporundan kompozit kur (kayıpsız)."""
        return cls(
            profile=report.profile, source_type="stl",
            axis=report.axis, confidence=report.confidence,
            quality=report.quality, segments=report.segments,
            pole_regions=report.pole_regions,
            turnaround_candidates=report.turnaround_candidates,
            source_path=report.source_path, units=report.units)

    @classmethod
    def from_parametric(cls, profile: MandrelProfile) -> "MandrelModel":
        """Parametrik profili (cylinder/cone/dome) kompozite sar."""
        return cls(profile=profile, source_type="parametric")

    # ── Serialization (MANDREL_MODEL_TASARIM.md §3) ───────────────────

    def to_dict(self) -> dict:
        """JSON-uyumlu sözlük (np dizileri liste olur). Ham vertex SAKLANMAZ."""
        d: dict = {
            "model_version": self.model_version,
            "source_type": self.source_type,
            "source_path": self.source_path,
            "units": self.units,
            "created_at": self.created_at,
            "profile": {
                "z_mm": self.profile.z_mm.tolist(),
                "r_mm": self.profile.r_mm.tolist(),
            },
        }
        if self.axis is not None:
            d["axis"] = {
                "axis_unit": np.asarray(self.axis.axis_unit).tolist(),
                "center": np.asarray(self.axis.center).tolist(),
                "j_min": self.axis.j_min,
                "eig_ratios": list(self.axis.eig_ratios),
                "degeneracy_flag": self.axis.degeneracy_flag,
            }
        if self.confidence is not None:
            c = self.confidence
            d["confidence"] = {
                "axis": c.axis, "symmetry": c.symmetry,
                "mesh_density": c.mesh_density, "radial_fit": c.radial_fit,
                "aggregate": c.aggregate, "grade": c.grade,
            }
        if self.quality is not None:
            q = self.quality
            d["quality"] = {
                "aspect_ratio": q.aspect_ratio, "min_radius_mm": q.min_radius_mm,
                "is_single_valued": q.is_single_valued, "asymmetry_mm": q.asymmetry_mm,
                "degenerate_tri_count": q.degenerate_tri_count,
                "mesh_density": q.mesh_density, "winding_suitable": q.winding_suitable,
                "reasons": list(q.reasons),
            }
        if self.segments is not None:
            d["segments"] = {
                "spans": [{"z_start_mm": s.z_start_mm, "z_end_mm": s.z_end_mm,
                           "kind": s.kind, "r_mean_mm": s.r_mean_mm,
                           "dr_dz_mean": s.dr_dz_mean} for s in self.segments.spans],
                "transitions": list(self.segments.transitions),
            }
        if self.pole_regions is not None:
            d["pole_regions"] = [
                {"side": p.side, "z_start_mm": p.z_start_mm, "z_end_mm": p.z_end_mm,
                 "min_radius_mm": p.min_radius_mm} for p in self.pole_regions]
        if self.turnaround_candidates is not None:
            d["turnaround_candidates"] = {
                "by_alpha": {
                    str(a): {"alpha_deg": t.alpha_deg, "z_left_mm": t.z_left_mm,
                             "z_right_mm": t.z_right_mm,
                             "polar_radius_c_mm": t.polar_radius_c_mm,
                             "reachable": t.reachable}
                    for a, t in self.turnaround_candidates.by_alpha.items()},
            }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MandrelModel":
        """to_dict çıktısından geri yükle (şema-toleranslı)."""
        profile = MandrelProfile(
            z_mm=np.asarray(d["profile"]["z_mm"], dtype=np.float64),
            r_mm=np.asarray(d["profile"]["r_mm"], dtype=np.float64))

        axis = None
        if "axis" in d:
            a = d["axis"]
            axis = AxisResult(
                axis_unit=np.asarray(a["axis_unit"], dtype=np.float64),
                center=np.asarray(a["center"], dtype=np.float64),
                j_min=a["j_min"], eig_ratios=tuple(a["eig_ratios"]),
                degeneracy_flag=a["degeneracy_flag"])

        confidence = None
        if "confidence" in d:
            c = d["confidence"]
            confidence = ConfidenceReport(
                axis=c["axis"], symmetry=c["symmetry"],
                mesh_density=c["mesh_density"], radial_fit=c["radial_fit"],
                aggregate=c["aggregate"], grade=c["grade"])

        quality = None
        if "quality" in d:
            q = d["quality"]
            quality = QualityReport(
                aspect_ratio=q["aspect_ratio"], min_radius_mm=q["min_radius_mm"],
                is_single_valued=q["is_single_valued"], asymmetry_mm=q["asymmetry_mm"],
                degenerate_tri_count=q["degenerate_tri_count"],
                mesh_density=q["mesh_density"], winding_suitable=q["winding_suitable"],
                reasons=list(q.get("reasons", [])))

        segments = None
        if "segments" in d:
            spans = [Segment(z_start_mm=s["z_start_mm"], z_end_mm=s["z_end_mm"],
                             kind=s["kind"], r_mean_mm=s["r_mean_mm"],
                             dr_dz_mean=s["dr_dz_mean"]) for s in d["segments"]["spans"]]
            segments = RegionSegmentation(
                spans=spans, transitions=list(d["segments"]["transitions"]))

        pole_regions = None
        if "pole_regions" in d:
            pole_regions = [PoleRegion(side=p["side"], z_start_mm=p["z_start_mm"],
                                       z_end_mm=p["z_end_mm"],
                                       min_radius_mm=p["min_radius_mm"])
                            for p in d["pole_regions"]]

        turnaround = None
        if "turnaround_candidates" in d:
            by_alpha = {}
            for a_str, t in d["turnaround_candidates"]["by_alpha"].items():
                by_alpha[float(a_str)] = TurnaroundCandidate(
                    alpha_deg=t["alpha_deg"], z_left_mm=t["z_left_mm"],
                    z_right_mm=t["z_right_mm"], polar_radius_c_mm=t["polar_radius_c_mm"],
                    reachable=t["reachable"])
            turnaround = TurnaroundCandidates(by_alpha=by_alpha)

        return cls(
            profile=profile, source_type=d.get("source_type", "parametric"),
            axis=axis, confidence=confidence, quality=quality, segments=segments,
            pole_regions=pole_regions, turnaround_candidates=turnaround,
            source_path=d.get("source_path"), units=d.get("units", "mm"),
            model_version=d.get("model_version", 1), created_at=d.get("created_at"))
