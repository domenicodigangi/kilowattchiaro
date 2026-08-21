"""Tests for the hourly self-consumption matching engine."""

import time

import pytest

from mock_pvgis import get_mock_pvgis_tmy_result
from kilowattchiaro_engine.pvgis import tmy_to_hourly_kw
from kilowattchiaro_engine.hourly_match import (
    CHARGE_EFFICIENCY,
    C_RATE,
    DISCHARGE_EFFICIENCY,
    match_hourly,
)
from kilowattchiaro_engine.load_profiles.archetypes import get_archetype
from kilowattchiaro_engine.models.hourly import (
    HourlyLoadProfile,
    HourlyProductionProfile,
    _MONTH_HOURS,
)


# ---------------------------------------------------------------------------
# Fixtures: typical Italian 3kWp / 3000kWh household
# ---------------------------------------------------------------------------

@pytest.fixture
def typical_production() -> HourlyProductionProfile:
    """3kWp system using mock PVGIS TMY data for Rome."""
    tmy = get_mock_pvgis_tmy_result()
    values = tmy_to_hourly_kw(tmy, kwp=3.0)
    return HourlyProductionProfile(
        values=values, lat=41.9, lon=12.5, kwp=3.0, tilt=35.0, azimuth=0.0,
    )


@pytest.fixture
def typical_load() -> HourlyLoadProfile:
    """couple_working archetype scaled to 3000 kWh/year."""
    archetype = get_archetype("couple_working")
    assert archetype is not None
    values = [v * 3000.0 for v in archetype.normalized_profile]
    return HourlyLoadProfile(values=values, source="synthetic")


# ---------------------------------------------------------------------------
# Sanity tests
# ---------------------------------------------------------------------------

class TestTypicalHousehold:
    def test_no_battery_sc_ratio(self, typical_load, typical_production):
        """Without battery: SC ratio for 3kWp/3000kWh should be ~25-35%."""
        result = match_hourly(typical_load, typical_production)
        assert 0.20 <= result.self_consumption_ratio <= 0.40

    def test_with_10kwh_battery_sc_ratio(self, typical_load, typical_production):
        """With 10kWh battery: SC ratio should rise to ~60-80%."""
        result = match_hourly(typical_load, typical_production, battery_kwh=10.0)
        assert 0.55 <= result.self_consumption_ratio <= 0.85

    def test_battery_improves_sc_ratio(self, typical_load, typical_production):
        """Battery should always improve self-consumption ratio."""
        no_batt = match_hourly(typical_load, typical_production)
        with_batt = match_hourly(typical_load, typical_production, battery_kwh=10.0)
        assert with_batt.self_consumption_ratio > no_batt.self_consumption_ratio

    def test_battery_reduces_grid_import(self, typical_load, typical_production):
        """Battery should reduce grid import."""
        no_batt = match_hourly(typical_load, typical_production)
        with_batt = match_hourly(typical_load, typical_production, battery_kwh=10.0)
        assert with_batt.annual_grid_import_kwh < no_batt.annual_grid_import_kwh


# ---------------------------------------------------------------------------
# Energy conservation
# ---------------------------------------------------------------------------

class TestEnergyConservation:
    def test_production_balance_no_battery(self, typical_load, typical_production):
        """production[h] = self_consumed_from_pv[h] + grid_export[h] (no battery)."""
        result = match_hourly(typical_load, typical_production)
        for h in range(8760):
            expected = result.self_consumption_kw[h] + result.grid_export_kw[h]
            assert abs(typical_production.values[h] - expected) < 1e-9

    def test_load_balance_no_battery(self, typical_load, typical_production):
        """load[h] = self_consumed[h] + grid_import[h] (no battery)."""
        result = match_hourly(typical_load, typical_production)
        for h in range(8760):
            expected = result.self_consumption_kw[h] + result.grid_import_kw[h]
            assert abs(typical_load.values[h] - expected) < 1e-9

    def test_load_balance_with_battery(self, typical_load, typical_production):
        """load[h] = self_consumed[h] + grid_import[h] (with battery)."""
        result = match_hourly(typical_load, typical_production, battery_kwh=10.0)
        for h in range(8760):
            expected = result.self_consumption_kw[h] + result.grid_import_kw[h]
            assert abs(typical_load.values[h] - expected) < 1e-9, (
                f"h={h}: load={typical_load.values[h]}, sc={result.self_consumption_kw[h]}, "
                f"import={result.grid_import_kw[h]}"
            )

    def test_monthly_sum_equals_annual(self, typical_load, typical_production):
        """Monthly aggregates must sum to annual totals."""
        result = match_hourly(typical_load, typical_production, battery_kwh=10.0)
        monthly_sc = sum(m["self_consumption_kwh"] for m in result.monthly_summary)
        monthly_import = sum(m["grid_import_kwh"] for m in result.monthly_summary)
        monthly_export = sum(m["grid_export_kwh"] for m in result.monthly_summary)
        assert abs(monthly_sc - result.annual_self_consumption_kwh) < 0.1
        assert abs(monthly_import - result.annual_grid_import_kwh) < 0.1
        assert abs(monthly_export - result.annual_grid_export_kwh) < 0.1


# ---------------------------------------------------------------------------
# Battery constraints
# ---------------------------------------------------------------------------

class TestBatteryConstraints:
    def test_battery_never_exceeds_capacity(self, typical_load, typical_production):
        """All battery_state values must be in [0, battery_kwh]."""
        batt_kwh = 10.0
        result = match_hourly(typical_load, typical_production, battery_kwh=batt_kwh)
        assert result.battery_state_kwh is not None
        for h, soc in enumerate(result.battery_state_kwh):
            assert 0.0 <= soc <= batt_kwh, f"h={h}: SOC={soc}"

    def test_battery_never_negative(self, typical_load, typical_production):
        """No negative SOC values."""
        result = match_hourly(typical_load, typical_production, battery_kwh=5.0)
        assert result.battery_state_kwh is not None
        assert all(s >= 0.0 for s in result.battery_state_kwh)

    def test_charge_rate_limit(self):
        """10kWh battery should never charge more than 5kW (C/2) in a single hour."""
        batt_kwh = 10.0
        max_rate = batt_kwh * C_RATE
        # Large surplus: 10kW production, 0 load every hour
        prod = HourlyProductionProfile(
            values=[10.0] * 8760, lat=41.9, lon=12.5, kwp=10.0, tilt=35.0, azimuth=0.0,
        )
        load = HourlyLoadProfile(values=[0.0] * 8760, source="synthetic")
        result = match_hourly(load, prod, battery_kwh=batt_kwh)
        assert result.battery_state_kwh is not None
        # Check hour-over-hour SOC increase never exceeds max_rate * CHARGE_EFF
        prev_soc = 0.0
        for h in range(8760):
            soc_increase = result.battery_state_kwh[h] - prev_soc
            # Max increase = max_rate * CHARGE_EFF (energy taken from PV capped at max_rate)
            assert soc_increase <= max_rate * CHARGE_EFFICIENCY + 1e-9, (
                f"h={h}: SOC increase={soc_increase}, max={max_rate * CHARGE_EFFICIENCY}"
            )
            prev_soc = result.battery_state_kwh[h]

    def test_no_battery_returns_none_state(self, typical_load, typical_production):
        """battery_state_kwh should be None when no battery."""
        result = match_hourly(typical_load, typical_production, battery_kwh=0.0)
        assert result.battery_state_kwh is None


# ---------------------------------------------------------------------------
# Boundary / synthetic scenarios
# ---------------------------------------------------------------------------

class TestBoundaryScenarios:
    def test_perfect_match(self):
        """When load == production every hour: SC ratio = 100%, no import/export."""
        values = [0.5] * 8760
        load = HourlyLoadProfile(values=values, source="synthetic")
        prod = HourlyProductionProfile(
            values=values, lat=41.9, lon=12.5, kwp=3.0, tilt=35.0, azimuth=0.0,
        )
        result = match_hourly(load, prod)
        assert abs(result.self_consumption_ratio - 1.0) < 1e-9
        assert result.annual_grid_import_kwh < 1e-9
        assert result.annual_grid_export_kwh < 1e-9

    def test_no_production(self):
        """Zero production: SC = 0, import = full load."""
        load_vals = [0.5] * 8760
        load = HourlyLoadProfile(values=load_vals, source="synthetic")
        prod = HourlyProductionProfile(
            values=[0.0] * 8760, lat=41.9, lon=12.5, kwp=0.0, tilt=35.0, azimuth=0.0,
        )
        result = match_hourly(load, prod)
        assert result.self_consumption_ratio == 0.0
        assert abs(result.annual_grid_import_kwh - sum(load_vals)) < 0.1
        assert result.annual_grid_export_kwh < 1e-9

    def test_no_load(self):
        """Zero load: SC = 0 kWh, all production exported."""
        prod_vals = [0.5] * 8760
        load = HourlyLoadProfile(values=[0.0] * 8760, source="synthetic")
        prod = HourlyProductionProfile(
            values=prod_vals, lat=41.9, lon=12.5, kwp=3.0, tilt=35.0, azimuth=0.0,
        )
        result = match_hourly(load, prod)
        assert result.annual_self_consumption_kwh < 1e-9
        assert result.annual_grid_import_kwh < 1e-9

    def test_daytime_production_nighttime_consumption(self):
        """Production only during day (8-16), consumption only at night (20-6).

        Without battery: SC near 0%. With battery: significantly higher.
        """
        prod_vals = []
        load_vals = []
        for _ in range(365):
            for hour in range(24):
                prod_vals.append(2.0 if 8 <= hour < 16 else 0.0)
                load_vals.append(0.5 if hour < 6 or hour >= 20 else 0.0)

        load = HourlyLoadProfile(values=load_vals, source="synthetic")
        prod = HourlyProductionProfile(
            values=prod_vals, lat=41.9, lon=12.5, kwp=3.0, tilt=35.0, azimuth=0.0,
        )

        no_batt = match_hourly(load, prod)
        assert no_batt.self_consumption_ratio < 0.05  # near 0

        with_batt = match_hourly(load, prod, battery_kwh=10.0)
        assert with_batt.self_consumption_ratio > 0.30

    def test_monthly_summary_has_12_entries(self, typical_load, typical_production):
        """Monthly summary should always have exactly 12 entries."""
        result = match_hourly(typical_load, typical_production)
        assert len(result.monthly_summary) == 12
        for i, m in enumerate(result.monthly_summary):
            assert m["month"] == i + 1


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

class TestPerformance:
    def test_performance_under_100ms(self, typical_load, typical_production):
        """match_hourly should complete in <100ms for 8760 hours."""
        # Warm-up run
        match_hourly(typical_load, typical_production, battery_kwh=10.0)
        # Timed run
        start = time.perf_counter()
        match_hourly(typical_load, typical_production, battery_kwh=10.0)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 100, f"Took {elapsed_ms:.1f}ms, expected <100ms"
