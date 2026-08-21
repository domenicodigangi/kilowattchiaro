"""Tests for Italian residential load profile archetypes.

Verifies normalization, F1/F2/F3 splits, seasonal patterns, peak constraints,
and archetype metadata for all 7 RSE/ARERA-based archetypes.
"""

import pytest

from kilowattchiaro_engine.load_profiles import ARCHETYPES, LoadArchetype, get_archetype, list_archetypes
from kilowattchiaro_engine.models.hourly import HourlyLoadProfile


def test_archetype_count():
    """Exactly 7 archetypes are defined."""
    assert len(ARCHETYPES) == 7


def test_list_archetypes():
    """list_archetypes() returns all 7 with correct metadata."""
    archetypes = list_archetypes()
    assert len(archetypes) == 7
    for a in archetypes:
        assert isinstance(a, LoadArchetype)
        assert a.id
        assert a.label
        assert a.description
        assert a.annual_kwh_range[0] < a.annual_kwh_range[1]
        assert a.typical_potenza_kw > 0


def test_get_archetype_by_id():
    """get_archetype() returns the correct archetype for known IDs."""
    a = get_archetype("couple_working")
    assert a is not None
    assert a.label == "Couple, both working"

    assert get_archetype("nonexistent") is None


@pytest.mark.parametrize("archetype", ARCHETYPES, ids=[a.id for a in ARCHETYPES])
def test_profile_has_8760_values(archetype: LoadArchetype):
    """Each profile has exactly 8760 hourly values."""
    assert len(archetype.normalized_profile) == 8760


@pytest.mark.parametrize("archetype", ARCHETYPES, ids=[a.id for a in ARCHETYPES])
def test_profile_sums_to_one(archetype: LoadArchetype):
    """Each profile sums to 1.0 (within floating-point tolerance)."""
    total = sum(archetype.normalized_profile)
    assert abs(total - 1.0) < 1e-6, f"{archetype.id}: sum={total}"


@pytest.mark.parametrize("archetype", ARCHETYPES, ids=[a.id for a in ARCHETYPES])
def test_profile_non_negative(archetype: LoadArchetype):
    """No hourly value is negative."""
    assert all(v >= 0 for v in archetype.normalized_profile), f"{archetype.id} has negative values"


@pytest.mark.parametrize("archetype", ARCHETYPES, ids=[a.id for a in ARCHETYPES])
def test_peak_within_potenza_limit(archetype: LoadArchetype):
    """At max annual_kwh, no hour exceeds 1.5x typical_potenza_kw."""
    max_val = max(archetype.normalized_profile)
    max_hourly_kw = max_val * archetype.annual_kwh_range[1]
    limit_kw = 1.5 * archetype.typical_potenza_kw
    assert max_hourly_kw <= limit_kw, (
        f"{archetype.id}: max_hourly_kw={max_hourly_kw:.3f} > limit={limit_kw:.1f}"
    )


@pytest.mark.parametrize("archetype", ARCHETYPES, ids=[a.id for a in ARCHETYPES])
def test_f1_f2_f3_fractions_match_profile(archetype: LoadArchetype):
    """Computed F1/F2/F3 split matches declared fractions within 5%."""
    # Build an HourlyLoadProfile from the normalized profile scaled to mid-range kWh
    mid_kwh = (archetype.annual_kwh_range[0] + archetype.annual_kwh_range[1]) / 2
    values_kw = [v * mid_kwh for v in archetype.normalized_profile]
    hlp = HourlyLoadProfile(
        values=values_kw,
        source="synthetic",
        potenza_impegnata_kw=archetype.typical_potenza_kw,
    )
    monthly = hlp.to_monthly_f1_f2_f3()
    total_f1 = sum(m["f1_kwh"] for m in monthly)
    total_f2 = sum(m["f2_kwh"] for m in monthly)
    total_f3 = sum(m["f3_kwh"] for m in monthly)
    total = total_f1 + total_f2 + total_f3

    computed_f1 = total_f1 / total
    computed_f2 = total_f2 / total
    computed_f3 = total_f3 / total

    assert abs(computed_f1 - archetype.f1_fraction) < 0.05, (
        f"{archetype.id}: F1 computed={computed_f1:.4f} vs declared={archetype.f1_fraction:.4f}"
    )
    assert abs(computed_f2 - archetype.f2_fraction) < 0.05, (
        f"{archetype.id}: F2 computed={computed_f2:.4f} vs declared={archetype.f2_fraction:.4f}"
    )
    assert abs(computed_f3 - archetype.f3_fraction) < 0.05, (
        f"{archetype.id}: F3 computed={computed_f3:.4f} vs declared={archetype.f3_fraction:.4f}"
    )


def test_distinct_f1_f2_f3_ratios():
    """Each archetype has a distinct F1/F2/F3 ratio vector (no two identical)."""
    ratios = [(a.f1_fraction, a.f2_fraction, a.f3_fraction) for a in ARCHETYPES]
    # Check uniqueness — no two archetypes should have the same (f1, f2, f3) tuple
    assert len(set(ratios)) == len(ratios), "Two archetypes have identical F1/F2/F3 ratios"


def test_daily_peak_patterns():
    """Profiles show expected evening peak (hours 19-21)."""
    for a in ARCHETYPES:
        # Compute average hourly value for each hour of the day
        hourly_avg = [0.0] * 24
        for i, v in enumerate(a.normalized_profile):
            hourly_avg[i % 24] += v
        # Normalize
        total = sum(hourly_avg)
        hourly_avg = [h / total for h in hourly_avg]
        # Evening peak (19-21) should be higher than overnight trough (2-5)
        evening = sum(hourly_avg[19:22]) / 3
        overnight = sum(hourly_avg[2:5]) / 3
        assert evening > overnight, (
            f"{a.id}: evening avg={evening:.4f} not > overnight avg={overnight:.4f}"
        )


def test_seasonal_variation_heat_pump():
    """Heat pump archetype: January consumption > July consumption."""
    hp = get_archetype("heat_pump")
    assert hp is not None
    jan_total = sum(hp.normalized_profile[0 : 31 * 24])
    jul_start = (31 + 28 + 31 + 30 + 31 + 30) * 24
    jul_total = sum(hp.normalized_profile[jul_start : jul_start + 31 * 24])
    assert jan_total > jul_total, f"heat_pump: Jan={jan_total:.4f} not > Jul={jul_total:.4f}"


def test_seasonal_variation_ac_heavy():
    """AC-heavy archetype: July consumption > January consumption."""
    ac = get_archetype("ac_heavy")
    assert ac is not None
    jan_total = sum(ac.normalized_profile[0 : 31 * 24])
    jul_start = (31 + 28 + 31 + 30 + 31 + 30) * 24
    jul_total = sum(ac.normalized_profile[jul_start : jul_start + 31 * 24])
    assert jul_total > jan_total, f"ac_heavy: Jul={jul_total:.4f} not > Jan={jan_total:.4f}"
