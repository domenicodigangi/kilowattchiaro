"""Tests for household-based consumption estimation."""

import pytest

from kilowattchiaro_engine.household_estimation import estimate_consumption
from kilowattchiaro_engine.models.solar_eval import DaytimeOccupancy, HouseholdEstimationRequest, WaterHeaterType


def _req(**kwargs) -> HouseholdEstimationRequest:
    defaults = {
        "occupants": 2,
        "has_heat_pump": False,
        "has_ac": False,
        "has_electric_water_heater": False,
        "has_electric_cooking": False,
        "daytime_occupancy": DaytimeOccupancy.PARTIAL,
    }
    defaults.update(kwargs)
    return HouseholdEstimationRequest(**defaults)


class TestArchetypeSelection:
    """Verify correct archetype is selected for different household types."""

    def test_heat_pump_household(self):
        result = estimate_consumption(_req(has_heat_pump=True, occupants=3))
        assert result.archetype_id == "heat_pump"

    def test_ac_heavy_household(self):
        result = estimate_consumption(_req(has_ac=True, occupants=3))
        assert result.archetype_id == "ac_heavy"

    def test_family_with_children(self):
        result = estimate_consumption(
            _req(occupants=4, daytime_occupancy=DaytimeOccupancy.NONE)
        )
        assert result.archetype_id == "family_children"

    def test_couple_working(self):
        result = estimate_consumption(
            _req(occupants=2, daytime_occupancy=DaytimeOccupancy.NONE)
        )
        assert result.archetype_id == "couple_working"

    def test_single_retiree(self):
        result = estimate_consumption(
            _req(occupants=1, daytime_occupancy=DaytimeOccupancy.FULL)
        )
        assert result.archetype_id == "single_retiree"

    def test_full_daytime_occupancy_selects_home_archetype(self):
        result = estimate_consumption(
            _req(occupants=2, daytime_occupancy=DaytimeOccupancy.FULL)
        )
        assert result.archetype_id in ("work_from_home", "single_retiree")

    def test_heat_pump_overrides_ac(self):
        """Heat pump should take priority over AC."""
        result = estimate_consumption(
            _req(has_heat_pump=True, has_ac=True, occupants=2)
        )
        assert result.archetype_id == "heat_pump"


class TestConsumptionScaling:
    """Verify consumption scales with occupants and appliances."""

    def test_more_occupants_more_consumption(self):
        r2 = estimate_consumption(_req(occupants=2))
        r4 = estimate_consumption(_req(occupants=4))
        assert r4.consumption.annual_kwh > r2.consumption.annual_kwh

    def test_electric_water_heater_adds_consumption(self):
        base = estimate_consumption(_req())
        with_heater = estimate_consumption(_req(has_electric_water_heater=True))
        assert with_heater.consumption.annual_kwh > base.consumption.annual_kwh

    def test_electric_cooking_adds_consumption(self):
        base = estimate_consumption(_req())
        with_cooking = estimate_consumption(_req(has_electric_cooking=True))
        assert with_cooking.consumption.annual_kwh > base.consumption.annual_kwh

    def test_single_occupant_lower_bound(self):
        result = estimate_consumption(_req(occupants=1))
        assert result.consumption.annual_kwh >= 800  # Minimum across archetypes

    def test_large_household_upper_bound(self):
        result = estimate_consumption(
            _req(occupants=8, has_heat_pump=True, has_electric_water_heater=True, has_electric_cooking=True)
        )
        # Should be clamped to 125% of archetype max (heat_pump: 6500 * 1.25 = 8125)
        assert result.consumption.annual_kwh <= 6500 * 1.25 + 1800 + 1  # archetype cap + appliances (1500+300) + rounding

    def test_resistance_water_heater_adds_more_than_heat_pump(self):
        """Resistance heater adds ~1500 kWh, heat pump adds ~700 kWh."""
        # Use heat_pump archetype (wide range 4000-6500) to avoid clamping
        kw = dict(occupants=2, has_heat_pump=True)
        base = estimate_consumption(_req(**kw))
        resistance = estimate_consumption(
            _req(**kw, water_heater_type=WaterHeaterType.RESISTANCE)
        )
        heat_pump_wh = estimate_consumption(
            _req(**kw, water_heater_type=WaterHeaterType.HEAT_PUMP)
        )
        assert resistance.consumption.annual_kwh > heat_pump_wh.consumption.annual_kwh
        assert heat_pump_wh.consumption.annual_kwh > base.consumption.annual_kwh
        # Resistance adds ~800 kWh more than heat pump WH
        delta = resistance.consumption.annual_kwh - heat_pump_wh.consumption.annual_kwh
        assert 700 <= delta <= 900

    def test_legacy_bool_treated_as_resistance(self):
        """has_electric_water_heater=True without water_heater_type → resistance."""
        kw = dict(occupants=2, has_heat_pump=True)
        legacy = estimate_consumption(_req(**kw, has_electric_water_heater=True))
        explicit = estimate_consumption(
            _req(**kw, water_heater_type=WaterHeaterType.RESISTANCE)
        )
        assert legacy.consumption.annual_kwh == explicit.consumption.annual_kwh

    def test_water_heater_type_overrides_legacy_bool(self):
        """water_heater_type takes precedence over has_electric_water_heater."""
        kw = dict(occupants=2, has_heat_pump=True)
        result = estimate_consumption(
            _req(**kw, has_electric_water_heater=True, water_heater_type=WaterHeaterType.HEAT_PUMP)
        )
        base = estimate_consumption(_req(**kw))
        # Should add ~700 (heat pump), not ~1500 (resistance)
        delta = result.consumption.annual_kwh - base.consumption.annual_kwh
        assert 600 <= delta <= 800

    def test_occupant_factor_stepped(self):
        """Occupant scaling uses diminishing returns: +15%, +10%, +8%..."""
        from kilowattchiaro_engine.household_estimation import _occupant_factor

        assert _occupant_factor(1) == pytest.approx(0.85)
        assert _occupant_factor(2) == pytest.approx(1.0)
        assert _occupant_factor(3) == pytest.approx(1.15)
        assert _occupant_factor(4) == pytest.approx(1.25)
        assert _occupant_factor(5) == pytest.approx(1.33)
        assert _occupant_factor(6) == pytest.approx(1.41)


class TestOutputFields:
    """Verify all output fields are populated correctly."""

    def test_f1_f2_f3_sum_to_annual(self):
        result = estimate_consumption(_req(occupants=3))
        c = result.consumption
        band_total = c.f1_kwh + c.f2_kwh + c.f3_kwh
        # Allow ±2 kWh for rounding
        assert abs(band_total - c.annual_kwh) <= 2

    def test_annual_cost_computed(self):
        result = estimate_consumption(_req())
        assert result.consumption.annual_cost_eur is not None
        assert result.consumption.annual_cost_eur > 0

    def test_potenza_set_from_archetype(self):
        result = estimate_consumption(_req())
        assert result.consumption.potenza_impegnata_kw >= 1.5

    def test_confidence_always_indicative(self):
        result = estimate_consumption(_req())
        assert result.confidence == "indicative"

    def test_all_fields_estimated(self):
        result = estimate_consumption(_req())
        assert len(result.estimated_fields) == 6
        assert "annual_kwh" in result.estimated_fields
