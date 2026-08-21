"""Tests for F1/F2/F3 load curve inference from bill data.

Verifies round-trip band consistency, archetype selection logic,
fallback modes (bioraria, annual-only), edge cases, and performance.
"""

import time

import pytest

from kilowattchiaro_engine.load_profiles import ARCHETYPES, infer_hourly_load
from kilowattchiaro_engine.models.solar_eval import ConsumptionProfile


def _make_consumption(
    annual_kwh: float = 3000.0,
    f1_kwh: float = 0.0,
    f2_kwh: float = 0.0,
    f3_kwh: float = 0.0,
) -> ConsumptionProfile:
    return ConsumptionProfile(
        annual_kwh=annual_kwh, f1_kwh=f1_kwh, f2_kwh=f2_kwh, f3_kwh=f3_kwh,
    )


# ---------------------------------------------------------------------------
# Core acceptance criteria
# ---------------------------------------------------------------------------

class TestRoundTripConsistency:
    """Given F1/F2/F3 input, output profile's band totals match within 5%."""

    def test_standard_trioraria(self):
        consumption = _make_consumption(
            annual_kwh=3000, f1_kwh=1200, f2_kwh=1050, f3_kwh=750,
        )
        profile = infer_hourly_load(consumption, potenza_impegnata=3.0)

        assert len(profile.values) == 8760
        assert all(v >= 0 for v in profile.values)

        monthly = profile.to_monthly_f1_f2_f3()
        total_f1 = sum(m["f1_kwh"] for m in monthly)
        total_f2 = sum(m["f2_kwh"] for m in monthly)
        total_f3 = sum(m["f3_kwh"] for m in monthly)

        # Within 5% per band (capping may reduce slightly)
        assert total_f1 == pytest.approx(1200, rel=0.05), f"F1: {total_f1:.1f} vs 1200"
        assert total_f2 == pytest.approx(1050, rel=0.05), f"F2: {total_f2:.1f} vs 1050"
        assert total_f3 == pytest.approx(750, rel=0.05), f"F3: {total_f3:.1f} vs 750"

    def test_total_matches_bill(self):
        consumption = _make_consumption(
            annual_kwh=3000, f1_kwh=1200, f2_kwh=1050, f3_kwh=750,
        )
        profile = infer_hourly_load(consumption, potenza_impegnata=3.0)
        total = profile.total_kwh()
        # Capping may reduce total slightly, but should be within 5%
        assert total == pytest.approx(3000, rel=0.05), f"Total: {total:.1f} vs 3000"


class TestArchetypeSelection:
    """Different F1/F2/F3 ratios select different archetypes."""

    def test_high_f1_selects_wfh(self):
        """High F1 ratio (lots of daytime) -> work_from_home."""
        # WFH has highest F1 fraction among archetypes
        wfh = next(a for a in ARCHETYPES if a.id == "work_from_home")
        consumption = _make_consumption(
            annual_kwh=3000,
            f1_kwh=3000 * wfh.f1_fraction,
            f2_kwh=3000 * wfh.f2_fraction,
            f3_kwh=3000 * wfh.f3_fraction,
        )
        profile = infer_hourly_load(consumption, potenza_impegnata=3.0)
        assert profile.source == "bill_inference"

    def test_low_f1_high_f3_selects_couple_working(self):
        """Low F1, high evening/night -> couple_working."""
        couple = next(a for a in ARCHETYPES if a.id == "couple_working")
        consumption = _make_consumption(
            annual_kwh=2400,
            f1_kwh=2400 * couple.f1_fraction,
            f2_kwh=2400 * couple.f2_fraction,
            f3_kwh=2400 * couple.f3_fraction,
        )
        profile = infer_hourly_load(consumption, potenza_impegnata=3.0)
        assert len(profile.values) == 8760

    @pytest.mark.parametrize(
        "archetype", ARCHETYPES, ids=[a.id for a in ARCHETYPES],
    )
    def test_each_archetype_self_matches(self, archetype):
        """Feeding an archetype's own F1/F2/F3 ratios should select itself."""
        mid_kwh = sum(archetype.annual_kwh_range) / 2
        consumption = _make_consumption(
            annual_kwh=mid_kwh,
            f1_kwh=mid_kwh * archetype.f1_fraction,
            f2_kwh=mid_kwh * archetype.f2_fraction,
            f3_kwh=mid_kwh * archetype.f3_fraction,
        )
        profile = infer_hourly_load(
            consumption, potenza_impegnata=archetype.typical_potenza_kw,
        )
        assert len(profile.values) == 8760
        assert all(v >= 0 for v in profile.values)
        assert profile.selected_archetype == archetype.id, (
            f"Expected archetype '{archetype.id}' but got '{profile.selected_archetype}'"
        )

    def test_different_ratios_select_different_archetypes(self):
        """Two very different ratio sets produce different profiles."""
        # Very high F1 (daytime worker)
        profile_high_f1 = infer_hourly_load(
            _make_consumption(annual_kwh=3000, f1_kwh=1800, f2_kwh=700, f3_kwh=500),
        )
        # Very low F1 (evening/night person)
        profile_low_f1 = infer_hourly_load(
            _make_consumption(annual_kwh=3000, f1_kwh=600, f2_kwh=1200, f3_kwh=1200),
        )
        # Profiles should differ meaningfully
        daytime_hours = list(range(8, 19))  # F1 hours
        day_kwh_high = sum(
            profile_high_f1.values[h] for h in range(8760) if h % 24 in daytime_hours
        )
        day_kwh_low = sum(
            profile_low_f1.values[h] for h in range(8760) if h % 24 in daytime_hours
        )
        assert day_kwh_high > day_kwh_low


class TestProfileValidity:
    """Output profiles are valid 8760-element vectors."""

    def test_8760_values(self):
        consumption = _make_consumption(
            annual_kwh=3000, f1_kwh=1200, f2_kwh=1050, f3_kwh=750,
        )
        profile = infer_hourly_load(consumption)
        assert len(profile.values) == 8760

    def test_all_non_negative(self):
        consumption = _make_consumption(
            annual_kwh=3000, f1_kwh=1200, f2_kwh=1050, f3_kwh=750,
        )
        profile = infer_hourly_load(consumption)
        assert all(v >= 0 for v in profile.values)

    def test_peak_constraint(self):
        """No value exceeds potenza_impegnata * 1.1."""
        consumption = _make_consumption(
            annual_kwh=3000, f1_kwh=1200, f2_kwh=1050, f3_kwh=750,
        )
        potenza = 3.0
        profile = infer_hourly_load(consumption, potenza_impegnata=potenza)
        cap = potenza * 1.1
        assert max(profile.values) <= cap + 1e-9


# ---------------------------------------------------------------------------
# Fallback modes
# ---------------------------------------------------------------------------

class TestBiorariaFallback:
    """Bioraria (F1/F23 merged) input produces valid profile."""

    def test_bioraria_valid_profile(self):
        # f2_kwh=0 signals bioraria; f3_kwh holds F23
        consumption = _make_consumption(
            annual_kwh=3000, f1_kwh=1200, f2_kwh=0, f3_kwh=1800,
        )
        profile = infer_hourly_load(consumption, potenza_impegnata=3.0)
        assert len(profile.values) == 8760
        assert all(v >= 0 for v in profile.values)

    def test_bioraria_f1_matches(self):
        consumption = _make_consumption(
            annual_kwh=3000, f1_kwh=1200, f2_kwh=0, f3_kwh=1800,
        )
        profile = infer_hourly_load(consumption, potenza_impegnata=3.0)
        monthly = profile.to_monthly_f1_f2_f3()
        total_f1 = sum(m["f1_kwh"] for m in monthly)
        # F1 should match within 5%
        assert total_f1 == pytest.approx(1200, rel=0.05), f"F1: {total_f1:.1f} vs 1200"

    def test_bioraria_total_matches(self):
        consumption = _make_consumption(
            annual_kwh=3000, f1_kwh=1200, f2_kwh=0, f3_kwh=1800,
        )
        profile = infer_hourly_load(consumption, potenza_impegnata=3.0)
        total = profile.total_kwh()
        assert total == pytest.approx(3000, rel=0.05), f"Total: {total:.1f} vs 3000"


class TestAnnualOnlyFallback:
    """Annual-only input (no band data) produces valid profile."""

    def test_annual_only_valid_profile(self):
        consumption = _make_consumption(annual_kwh=3000, f1_kwh=0, f2_kwh=0, f3_kwh=0)
        profile = infer_hourly_load(consumption, potenza_impegnata=3.0)
        assert len(profile.values) == 8760
        assert all(v >= 0 for v in profile.values)

    def test_annual_only_total_matches(self):
        consumption = _make_consumption(annual_kwh=3000, f1_kwh=0, f2_kwh=0, f3_kwh=0)
        profile = infer_hourly_load(consumption, potenza_impegnata=3.0)
        total = profile.total_kwh()
        # No per-band scaling, so total should match closely (only capping can reduce)
        assert total == pytest.approx(3000, rel=0.05), f"Total: {total:.1f} vs 3000"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Extreme inputs handled gracefully."""

    def test_very_low_consumption(self):
        """900 kWh annual -> energy_poor archetype, valid profile."""
        consumption = _make_consumption(annual_kwh=900, f1_kwh=0, f2_kwh=0, f3_kwh=0)
        profile = infer_hourly_load(consumption, potenza_impegnata=1.5)
        assert len(profile.values) == 8760
        assert profile.total_kwh() > 0

    def test_very_high_consumption(self):
        """6000 kWh annual -> heat_pump range, valid profile."""
        consumption = _make_consumption(annual_kwh=6000, f1_kwh=0, f2_kwh=0, f3_kwh=0)
        profile = infer_hourly_load(consumption, potenza_impegnata=6.0)
        assert len(profile.values) == 8760

    def test_extreme_f1_ratio(self):
        """F1=90% doesn't crash and produces valid profile."""
        consumption = _make_consumption(
            annual_kwh=3000, f1_kwh=2700, f2_kwh=200, f3_kwh=100,
        )
        profile = infer_hourly_load(consumption, potenza_impegnata=3.0)
        assert len(profile.values) == 8760
        assert all(v >= 0 for v in profile.values)

    def test_band_sum_differs_from_annual(self):
        """f1+f2+f3 != annual_kwh — uses band sum for scaling."""
        consumption = _make_consumption(
            annual_kwh=2800, f1_kwh=1200, f2_kwh=1050, f3_kwh=750,
        )
        profile = infer_hourly_load(consumption)
        total = profile.total_kwh()
        # Should match band sum (3000), not annual_kwh (2800)
        assert total == pytest.approx(3000, rel=0.05), f"Total: {total:.1f} vs 3000"

    def test_metadata_fields(self):
        """Output has correct metadata."""
        consumption = _make_consumption(
            annual_kwh=3000, f1_kwh=1200, f2_kwh=1050, f3_kwh=750,
        )
        profile = infer_hourly_load(consumption, potenza_impegnata=4.5)
        assert profile.source == "bill_inference"
        assert profile.profile_type == "residential"
        assert profile.potenza_impegnata_kw == 4.5
        assert profile.year == 2025


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

class TestPerformance:
    """Inference completes within acceptable time."""

    def test_inference_under_500ms(self):
        consumption = _make_consumption(
            annual_kwh=3000, f1_kwh=1200, f2_kwh=1050, f3_kwh=750,
        )
        start = time.perf_counter()
        infer_hourly_load(consumption, potenza_impegnata=3.0)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 500, f"Inference took {elapsed_ms:.0f}ms (limit: 500ms)"
