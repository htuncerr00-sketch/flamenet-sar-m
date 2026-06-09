"""
tests/test_manual_layer_sequencer.py — Manuel Katman Dizilim Motoru Testleri
=============================================================================

LayerStack CRUD, pitch/build-up hesabı, slip validation,
serialization ve anti-simetrik çift önerisi.
"""
import sys
import math
import pytest

sys.path.insert(0, "faz17_d1_backend")

from faz17_d1.core.manual_layer_sequencer import (
    LayerStack, LayerSpec, LayerType, LayerValidation,
    PitchInfo, BuildUpResult,
    calculate_pitch_and_circuits, calculate_build_up,
    suggest_anti_symmetric_pair,
)
from faz17_d1.core.geometry_engine import MandrelProfile


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def profile():
    return MandrelProfile.cylinder(300.0, 50.0)


@pytest.fixture
def stack():
    return LayerStack()


# ── Grup 1: LayerStack CRUD ───────────────────────────────────────────────────

def test_group_crud_add_and_len(stack):
    assert len(stack) == 0
    spec = stack.make_helical(alpha_deg=55.0)
    idx = stack.add_layer(spec)
    assert idx == 0
    assert len(stack) == 1


def test_group_crud_remove(stack):
    spec = stack.make_helical(alpha_deg=45.0)
    stack.add_layer(spec)
    stack.add_layer(stack.make_hoop())
    assert len(stack) == 2
    removed = stack.remove_layer(0)
    assert removed.type == LayerType.HELICAL
    assert len(stack) == 1


def test_group_crud_move(stack):
    stack.add_layer(stack.make_helical(alpha_deg=55.0))
    stack.add_layer(stack.make_hoop())
    stack.move_layer(0, 1)
    assert stack[0].type == LayerType.HOOP
    assert stack[1].type == LayerType.HELICAL


def test_group_crud_clear(stack):
    stack.add_layer(stack.make_helical())
    stack.add_layer(stack.make_polar())
    stack.clear()
    assert len(stack) == 0


def test_group_crud_replace(stack):
    stack.add_layer(stack.make_helical(alpha_deg=30.0))
    new_spec = stack.make_helical(alpha_deg=60.0)
    stack.replace_layer(0, new_spec)
    assert stack[0].alpha_deg == pytest.approx(60.0)


# ── Grup 2: Fabrika metodları ─────────────────────────────────────────────────

def test_group_factory_types(stack):
    h   = stack.make_helical(alpha_deg=55.0)
    ho  = stack.make_hoop()
    p   = stack.make_polar(alpha_deg=10.0)
    sk  = stack.make_skin()
    assert h.type  == LayerType.HELICAL
    assert ho.type == LayerType.HOOP
    assert p.type  == LayerType.POLAR
    assert sk.type == LayerType.SKIN_FINISH


def test_group_factory_balanced_pair(stack):
    i1, i2 = stack.add_balanced_helical_pair(alpha_deg=45.0)
    assert stack[i1].alpha_deg == pytest.approx(+45.0)
    assert stack[i2].alpha_deg == pytest.approx(-45.0)
    assert len(stack) == 2


# ── Grup 3: Kalınlık toplamları ───────────────────────────────────────────────

def test_group_thickness_total(stack):
    stack.add_layer(stack.make_helical(thickness_mm=0.30))
    stack.add_layer(stack.make_hoop(thickness_mm=0.25))
    stack.add_layer(stack.make_helical(thickness_mm=0.30))
    assert stack.total_thickness_mm() == pytest.approx(0.85, abs=1e-9)


def test_group_thickness_helical_only(stack):
    stack.add_layer(stack.make_helical(thickness_mm=0.30))
    stack.add_layer(stack.make_hoop(thickness_mm=0.25))
    assert stack.total_helical_thickness_mm() == pytest.approx(0.30, abs=1e-9)


# ── Grup 4: Pitch hesabı ──────────────────────────────────────────────────────

def test_group_pitch_helical(stack):
    spec = stack.make_helical(alpha_deg=55.0, fitil_genisligi_mm=6.0)
    pi   = calculate_pitch_and_circuits(spec, mandrel_diameter_mm=100.0)
    expected_pitch = 6.0 / math.sin(math.radians(55.0))
    assert pi.pitch_mm == pytest.approx(expected_pitch, rel=1e-3)
    assert pi.n_circuits > 0
    assert pi.effective_coverage_pct > 0


def test_group_pitch_hoop(stack):
    spec = stack.make_hoop(alpha_deg=89.5, fitil_genisligi_mm=6.0)
    pi   = calculate_pitch_and_circuits(spec, mandrel_diameter_mm=100.0)
    # Near-90° → small pitch
    assert pi.pitch_mm < 10.0
    assert pi.n_circuits >= 1


# ── Grup 5: Build-up hesabı ───────────────────────────────────────────────────

def test_group_buildup_cylinder(stack, profile):
    spec = stack.make_helical(alpha_deg=55.0, thickness_mm=0.30)
    bu   = calculate_build_up(spec, profile)
    assert bu.delta_r_mean > 0.0
    assert all(dr > 0 for dr in bu.delta_r_mm)
    assert len(bu.r_post_mm) == len(profile.z_mm)


def test_group_buildup_hoop_direct(stack, profile):
    spec = stack.make_hoop(thickness_mm=0.30)
    bu   = calculate_build_up(spec, profile)
    # Hoop → delta ~ t everywhere (no cos division)
    assert bu.delta_r_mean == pytest.approx(0.30, rel=0.05)


# ── Grup 6: Slip validation ───────────────────────────────────────────────────

def test_group_validate_geodesic_pass(stack, profile):
    stack.add_layer(stack.make_helical(alpha_deg=55.0))
    val = stack.validate_layer_slippage(0, profile)
    assert val.passes is True
    assert val.max_slip_ratio < 1.0


def test_group_validate_all(stack, profile):
    stack.add_layer(stack.make_helical(alpha_deg=55.0))
    stack.add_layer(stack.make_hoop())
    results = stack.validate_all(profile)
    assert len(results) == 2
    assert all(isinstance(v, LayerValidation) for v in results)


# ── Grup 7: Anti-simetrik çift önerisi ───────────────────────────────────────

def test_group_antisymmetric_pair(stack):
    spec  = stack.make_helical(alpha_deg=+55.0)
    pair  = suggest_anti_symmetric_pair(spec, next_id=99)
    assert pair is not None
    assert pair.alpha_deg == pytest.approx(-55.0)
    assert pair.type == LayerType.HELICAL


def test_group_antisymmetric_hoop_returns_none(stack):
    spec = stack.make_hoop()
    pair = suggest_anti_symmetric_pair(spec, next_id=1)
    assert pair is None


# ── Grup 8: Serialization ────────────────────────────────────────────────────

def test_group_to_dict_roundtrip(stack):
    stack.add_layer(stack.make_helical(alpha_deg=55.0, notes="test"))
    stack.add_layer(stack.make_hoop())
    d = stack.to_dict()
    stack2 = LayerStack.from_dict(d)
    assert len(stack2) == 2
    assert stack2[0].alpha_deg == pytest.approx(55.0)
    assert stack2[1].type == LayerType.HOOP


def test_group_to_dict_has_both_type_keys(stack):
    """to_dict() katmanları 'type' anahtarıyla üretmeli."""
    stack.add_layer(stack.make_helical(alpha_deg=45.0))
    d = stack.to_dict()
    assert "type" in d["layers"][0]
    assert d["layers"][0]["type"] == "helical"


def test_group_to_dict_version(stack):
    d = stack.to_dict()
    assert d.get("versiyon") == "1.0"


# ── Grup 9: Summary ───────────────────────────────────────────────────────────

def test_group_summary(stack):
    stack.add_layer(stack.make_helical())
    stack.add_layer(stack.make_hoop())
    s = stack.summary()
    assert "Helisel: 1" in s
    assert "Hoop: 1" in s
    assert "2 katman" in s
