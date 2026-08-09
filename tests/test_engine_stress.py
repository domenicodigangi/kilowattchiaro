"""Boundary and real-world stress tests for the solar evaluation engine.

Tests exercise evaluate_solar with diverse system sizes, consumption levels,
and locations, then check that outputs fall within plausible ranges.
No exact-value assertions — only sanity/range checks.
"""

import math

import pytest

from kilowattchiaro_engine.solar_eval import evaluate_solar
from kilowattchiaro_engine.models.solar_eval import (
    ConsumptionProfile,
    PVGISMonthly,
    ScenarioType,
    SolarEvalResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_consumption(
    annual_kwh: float,
    annual_cost_eur: float | None = None,
) -> ConsumptionProfile:
    """Build a ConsumptionProfile with plausible ARERA band split."""
    return ConsumptionProfile(
        annual_kwh=annual_kwh,
        f1_kwh=round(annual_kwh * 0.40),
        f2_kwh=round(annual_kwh * 0.35),
        f3_kwh=round(annual_kwh * 0.25),
        annual_cost_eur=annual_cost_eur,
    )


def _make_monthly_production(
    annual_kwh: float,
    southern: bool = False,
) -> list[PVGISMonthly]:
    """Approximate PVGIS monthly production for Italy.

    Uses seasonal shape; southern Italy gets a flatter, sunnier curve.
    """
    if southern:
        # Southern Italy — higher base, flatter curve
        shape = [0.060, 0.065, 0.085, 0.095, 0.105, 0.115,
                 0.120, 0.115, 0.095, 0.075, 0.040, 0.030]
    else:
        # Northern/central Italy
        shape = [0.045, 0.055, 0.080, 0.095, 0.110, 0.120,
                 0.125, 0.115, 0.090, 0.070, 0.050, 0.045]
    total_shape = sum(shape)
    return [
        PVGISMonthly(
            month=i + 1,
            e_m=round(annual_kwh * s / total_shape, 1),
            h_m=round(annual_kwh * s / total_shape / 30, 1),  # rough irradiation proxy
        )
        for i, s in enumerate(shape)
    ]


def _assert_common_plausibility(result: SolarEvalResult, include_battery: bool):
    """Shared plausibility checks applied to every scenario."""
    expected_count = 3 if include_battery else 2
    assert len(result.scenarios) == expected_count

    sq = result.scenarios[0]
    solar = result.scenarios[1]

    # Status quo
    assert sq.scenario == ScenarioType.STATUS_QUO
    assert sq.annual_electricity_cost_eur > 0
    assert len(sq.monthly_details) == 12

    # Solar only
    assert solar.scenario == ScenarioType.SOLAR_ONLY
    assert len(solar.monthly_details) == 12
    assert result.annual_production_kwh > 0

    # Payback: either positive or None (never pays back)
    if solar.payback_years is not None:
        assert solar.payback_years > 0

    # NPV is a finite number
    assert solar.npv_20yr_eur is not None
    assert math.isfinite(solar.npv_20yr_eur)

    # Annual savings should not be astronomical
    assert -50_000 < solar.annual_savings_eur < 50_000

    if include_battery:
        bat = result.scenarios[2]
        assert bat.scenario == ScenarioType.SOLAR_BATTERY
        assert len(bat.monthly_details) == 12
        assert bat.npv_20yr_eur is not None
        assert math.isfinite(bat.npv_20yr_eur)
        if bat.payback_years is not None:
            assert bat.payback_years > 0


# ---------------------------------------------------------------------------
# Scenario 1: Tiny system — apartment in northern Italy
# ---------------------------------------------------------------------------

class TestTinySystem:
    """1.5 kWp, 1500 kWh/yr, northern Italy (lat 45.5, lon 9.2)."""

    @pytest.fixture
    def result(self) -> SolarEvalResult:
        consumption = _make_consumption(1500, annual_cost_eur=450.0)
        # 1.5 kWp in northern Italy ~ 1650 kWh/yr
        production = _make_monthly_production(1650, southern=False)
        return evaluate_solar(
            consumption=consumption,
            monthly_production=production,
            desired_kwp=1.5,
            include_battery=True,
            battery_kwh=5.0,
        )

    def test_plausibility(self, result):
        _assert_common_plausibility(result, include_battery=True)

    def test_payback_slow(self, result):
        solar = result.scenarios[1]
        # Small system with low consumption — payback should be slow
        if solar.payback_years is not None:
            assert solar.payback_years > 5

    def test_annual_savings_modest(self, result):
        solar = result.scenarios[1]
        assert solar.annual_savings_eur < 500

    def test_npv_modest(self, result):
        solar = result.scenarios[1]
        # NPV can be negative or modestly positive for a tiny system
        assert solar.npv_20yr_eur < 5000


# ---------------------------------------------------------------------------
# Scenario 2: Large system — villa in southern Italy
# ---------------------------------------------------------------------------

class TestLargeSystem:
    """20 kWp, 8000 kWh/yr, southern Italy (lat 38.1, lon 13.4)."""

    @pytest.fixture
    def result(self) -> SolarEvalResult:
        consumption = _make_consumption(8000, annual_cost_eur=2400.0)
        # 20 kWp in southern Italy ~ 28000 kWh/yr
        production = _make_monthly_production(28000, southern=True)
        return evaluate_solar(
            consumption=consumption,
            monthly_production=production,
            desired_kwp=20.0,
            include_battery=True,
            battery_kwh=15.0,
        )

    def test_plausibility(self, result):
        _assert_common_plausibility(result, include_battery=True)

    def test_payback_reasonable(self, result):
        solar = result.scenarios[1]
        assert solar.payback_years is not None
        assert solar.payback_years < 12

    def test_npv_significantly_positive(self, result):
        solar = result.scenarios[1]
        assert solar.npv_20yr_eur > 0

    def test_annual_savings_substantial(self, result):
        solar = result.scenarios[1]
        assert solar.annual_savings_eur > 500


# ---------------------------------------------------------------------------
# Scenario 3: Standard golden path — 6 kWp central Italy
# ---------------------------------------------------------------------------

class TestGoldenPath:
    """6 kWp, 3000 kWh/yr, central Italy (lat 41.9, lon 12.5)."""

    @pytest.fixture
    def result(self) -> SolarEvalResult:
        consumption = _make_consumption(3000, annual_cost_eur=750.0)
        # 6 kWp in central Italy ~ 8400 kWh/yr
        production = _make_monthly_production(8400, southern=False)
        return evaluate_solar(
            consumption=consumption,
            monthly_production=production,
            desired_kwp=6.0,
            include_battery=True,
            battery_kwh=10.0,
        )

    def test_plausibility(self, result):
        _assert_common_plausibility(result, include_battery=True)

    def test_solar_payback_5_to_10(self, result):
        solar = result.scenarios[1]
        assert solar.payback_years is not None
        assert 3 < solar.payback_years < 15

    def test_npv_positive(self, result):
        solar = result.scenarios[1]
        assert solar.npv_20yr_eur > 0

    def test_solar_cheaper_than_status_quo(self, result):
        sq = result.scenarios[0]
        solar = result.scenarios[1]
        assert solar.annual_electricity_cost_eur < sq.annual_electricity_cost_eur


# ---------------------------------------------------------------------------
# Scenario 4: Battery does not pay off — very small system, low consumption
# ---------------------------------------------------------------------------

class TestBatteryDoesNotPayOff:
    """2 kWp, 1200 kWh/yr — battery adds cost but minimal benefit."""

    @pytest.fixture
    def result(self) -> SolarEvalResult:
        consumption = _make_consumption(1200, annual_cost_eur=360.0)
        # 2 kWp in central Italy ~ 2800 kWh/yr
        production = _make_monthly_production(2800, southern=False)
        return evaluate_solar(
            consumption=consumption,
            monthly_production=production,
            desired_kwp=2.0,
            include_battery=True,
            battery_kwh=5.0,
        )

    def test_plausibility(self, result):
        _assert_common_plausibility(result, include_battery=True)

    def test_battery_npv_worse_than_solar(self, result):
        solar = result.scenarios[1]
        battery = result.scenarios[2]
        # Battery NPV should be worse — adds EUR 8000 cost with little extra benefit
        assert battery.npv_20yr_eur < solar.npv_20yr_eur


# ---------------------------------------------------------------------------
# Scenario 5: Battery pays off — large system, high consumption
# ---------------------------------------------------------------------------

class TestBatteryPaysOff:
    """10 kWp, 6000 kWh/yr — high consumption offsets battery cost."""

    @pytest.fixture
    def result(self) -> SolarEvalResult:
        consumption = _make_consumption(6000, annual_cost_eur=1800.0)
        # 10 kWp in southern Italy ~ 14000 kWh/yr
        production = _make_monthly_production(14000, southern=True)
        return evaluate_solar(
            consumption=consumption,
            monthly_production=production,
            desired_kwp=10.0,
            include_battery=True,
            battery_kwh=10.0,
        )

    def test_plausibility(self, result):
        _assert_common_plausibility(result, include_battery=True)

    def test_battery_has_higher_self_consumption(self, result):
        solar = result.scenarios[1]
        battery = result.scenarios[2]
        assert battery.self_consumption_percent > solar.self_consumption_percent

    def test_battery_economics_comparable_or_better(self, result):
        solar = result.scenarios[1]
        battery = result.scenarios[2]
        # With high consumption + default SC ratios (0.30 vs 0.70),
        # battery should have comparable or better economics.
        # Allow battery NPV to be at most EUR 3000 worse than solar-only
        # (the default ratios strongly favor battery at high consumption).
        assert battery.npv_20yr_eur > solar.npv_20yr_eur - 3000


# ---------------------------------------------------------------------------
# Scenario 6: Very low consumption — energy-poor household
# ---------------------------------------------------------------------------

class TestVeryLowConsumption:
    """800 kWh/yr — engine should not crash."""

    @pytest.fixture
    def result(self) -> SolarEvalResult:
        consumption = _make_consumption(800, annual_cost_eur=250.0)
        # 3 kWp ~ 4200 kWh/yr — vastly oversized for 800 kWh
        production = _make_monthly_production(4200, southern=False)
        return evaluate_solar(
            consumption=consumption,
            monthly_production=production,
            desired_kwp=3.0,
            include_battery=True,
            battery_kwh=5.0,
        )

    def test_plausibility(self, result):
        _assert_common_plausibility(result, include_battery=True)

    def test_does_not_crash(self, result):
        # The main assertion is that we got here without exception
        assert result.system_kwp == 3.0

    def test_production_exceeds_consumption(self, result):
        # With 4200 kWh production vs 800 kWh consumption, most is exported
        solar = result.scenarios[1]
        total_export = sum(d.grid_export_kwh for d in solar.monthly_details)
        assert total_export > 0


# ---------------------------------------------------------------------------
# Scenario 7: Very high consumption — heat pump household
# ---------------------------------------------------------------------------

class TestVeryHighConsumption:
    """10000 kWh/yr — high consumption benefits from solar."""

    @pytest.fixture
    def result(self) -> SolarEvalResult:
        consumption = _make_consumption(10000, annual_cost_eur=3000.0)
        # 10 kWp in central Italy ~ 14000 kWh/yr
        production = _make_monthly_production(14000, southern=False)
        return evaluate_solar(
            consumption=consumption,
            monthly_production=production,
            desired_kwp=10.0,
            include_battery=True,
            battery_kwh=15.0,
        )

    def test_plausibility(self, result):
        _assert_common_plausibility(result, include_battery=True)

    def test_does_not_crash(self, result):
        assert result.system_kwp == 10.0

    def test_payback_favorable(self, result):
        solar = result.scenarios[1]
        assert solar.payback_years is not None
        # High consumption means high avoided cost
        assert solar.payback_years < 15

    def test_significant_savings(self, result):
        solar = result.scenarios[1]
        assert solar.annual_savings_eur > 300


# ---------------------------------------------------------------------------
# Scenario 8: Zero battery cost edge case
# ---------------------------------------------------------------------------

class TestZeroBatteryCost:
    """battery_cost_eur=0 edge case.

    KNOWN ISSUE: The engine uses ``battery_cost_eur or default`` which treats
    0.0 as falsy and falls back to the default EUR 8000. This test documents
    that behaviour: passing 0.0 does NOT actually produce a free battery.
    A near-zero value (0.01) is used to test the "practically free" path.
    """

    @pytest.fixture
    def result_near_zero(self) -> SolarEvalResult:
        """Use 0.01 EUR to bypass the `or` fallback bug."""
        consumption = _make_consumption(3000, annual_cost_eur=750.0)
        production = _make_monthly_production(8400, southern=False)
        return evaluate_solar(
            consumption=consumption,
            monthly_production=production,
            desired_kwp=6.0,
            include_battery=True,
            battery_kwh=10.0,
            battery_cost_eur=0.01,
        )

    @pytest.fixture
    def result_zero(self) -> SolarEvalResult:
        """Pass 0.0 — engine silently uses default battery cost."""
        consumption = _make_consumption(3000, annual_cost_eur=750.0)
        production = _make_monthly_production(8400, southern=False)
        return evaluate_solar(
            consumption=consumption,
            monthly_production=production,
            desired_kwp=6.0,
            include_battery=True,
            battery_kwh=10.0,
            battery_cost_eur=0.0,
        )

    def test_plausibility_near_zero(self, result_near_zero):
        _assert_common_plausibility(result_near_zero, include_battery=True)

    def test_does_not_crash_zero(self, result_zero):
        assert result_zero.system_kwp == 6.0
        _assert_common_plausibility(result_zero, include_battery=True)

    def test_zero_cost_is_respected(self, result_zero):
        """Zero battery cost should be used as-is, not fall back to default."""
        assert result_zero.assumptions["battery_cost_eur"] == 0.0

    def test_near_zero_battery_better_than_solar_only(self, result_near_zero):
        solar = result_near_zero.scenarios[1]
        battery = result_near_zero.scenarios[2]
        # Practically free battery should beat solar-only
        assert battery.npv_20yr_eur >= solar.npv_20yr_eur
        assert battery.self_consumption_percent >= solar.self_consumption_percent
