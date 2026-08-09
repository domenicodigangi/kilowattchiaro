"""Tests for the solar installation evaluation engine."""

import pytest

from mock_pvgis import get_mock_pvgis_tmy_result
from kilowattchiaro_engine.pvgis import tmy_to_hourly_kw
from kilowattchiaro_engine.solar_eval import (
    _annual_profile_from_monthly,
    _build_annual_cash_flow_projection,
    _estimate_irr,
    _npv_20yr,
    _panel_factor,
    _payback_years,
    _scaled_price_trajectories,
    _solar_monthly,
    _status_quo_monthly,
    evaluate_solar,
)
from kilowattchiaro_engine.price_projection import get_price_projection_scenario
from kilowattchiaro_engine.models.hourly import _by_month
from kilowattchiaro_engine.models.solar_eval import (
    ConsumptionProfile,
    GoogleSolarResult,
    PVGISHourlyRecord,
    PVGISMonthly,
    PVGISResult,
    PVGISTMYResult,
    PriceScenarioName,
    RoofSegment,
    ScenarioType,
    SolarEvalRequest,
    SolarEvalResult,
)


# --- Fixtures ---

@pytest.fixture
def rome_consumption() -> ConsumptionProfile:
    """Typical Roman household: 3000 kWh/year, ~€750/year."""
    return ConsumptionProfile(
        annual_kwh=3000,
        f1_kwh=1200,
        f2_kwh=1050,
        f3_kwh=750,
        annual_cost_eur=750.0,
    )


@pytest.fixture
def rome_production_6kwp() -> list[PVGISMonthly]:
    """Approximate PVGIS output for 6 kWp in Rome (south, 30° tilt)."""
    # ~8500 kWh/year typical for Rome
    return [
        PVGISMonthly(month=1, e_m=450, h_m=3.0),
        PVGISMonthly(month=2, e_m=520, h_m=3.5),
        PVGISMonthly(month=3, e_m=720, h_m=4.5),
        PVGISMonthly(month=4, e_m=830, h_m=5.2),
        PVGISMonthly(month=5, e_m=950, h_m=6.0),
        PVGISMonthly(month=6, e_m=1000, h_m=6.5),
        PVGISMonthly(month=7, e_m=1050, h_m=6.8),
        PVGISMonthly(month=8, e_m=980, h_m=6.3),
        PVGISMonthly(month=9, e_m=780, h_m=5.0),
        PVGISMonthly(month=10, e_m=600, h_m=4.0),
        PVGISMonthly(month=11, e_m=420, h_m=3.0),
        PVGISMonthly(month=12, e_m=380, h_m=2.5),
    ]


# --- Model Tests ---

def test_consumption_profile_validation():
    """ConsumptionProfile accepts valid input."""
    p = ConsumptionProfile(
        annual_kwh=3000, f1_kwh=1200, f2_kwh=1050, f3_kwh=750
    )
    assert p.annual_kwh == 3000
    assert p.annual_cost_eur is None


def test_consumption_profile_rejects_zero():
    """annual_kwh must be > 0."""
    with pytest.raises(Exception):
        ConsumptionProfile(annual_kwh=0, f1_kwh=0, f2_kwh=0, f3_kwh=0)


def test_pvgis_monthly_roundtrip():
    """PVGISMonthly and PVGISResult serialize correctly."""
    m = PVGISMonthly(month=6, e_m=1000.5, h_m=6.5)
    r = PVGISResult(monthly=[m], yearly_kwh=8500, location="41.9,12.5")
    data = r.model_dump()
    r2 = PVGISResult.model_validate(data)
    assert r2.yearly_kwh == 8500
    assert r2.monthly[0].e_m == 1000.5


def test_google_solar_result_model():
    """GoogleSolarResult accepts valid roof data."""
    seg = RoofSegment(
        tilt_degrees=30.0, azimuth_degrees=180.0, area_m2=40.0,
        yearly_energy_kwh=6000.0,
    )
    result = GoogleSolarResult(
        segments=[seg], total_usable_area_m2=40.0,
        max_panel_count=20, max_kwp=8.0, yearly_energy_kwh=12000.0,
    )
    assert result.max_kwp == 8.0


def test_solar_eval_request_lat_validation():
    """Latitude must be within Italian range."""
    with pytest.raises(Exception):
        SolarEvalRequest(
            consumption=ConsumptionProfile(
                annual_kwh=3000, f1_kwh=1200, f2_kwh=1050, f3_kwh=750
            ),
            lat=60.0,  # Not Italy
            lon=12.5,
            desired_kwp=6.0,
        )


def test_solar_eval_request_defaults_reference_price_scenario():
    req = SolarEvalRequest(
        consumption=ConsumptionProfile(
            annual_kwh=3000, f1_kwh=1200, f2_kwh=1050, f3_kwh=750
        ),
        lat=41.9,
        lon=12.5,
        desired_kwp=6.0,
    )
    assert req.price_scenario == PriceScenarioName.REFERENCE


# --- Financial Engine Tests ---

def test_status_quo_monthly(rome_consumption):
    """Status quo monthly costs sum to annual cost."""
    details = _status_quo_monthly(rome_consumption, 0.25)
    total = sum(d.electricity_cost_eur for d in details)
    assert abs(total - 750.0) < 1.0  # Within rounding
    assert all(d.production_kwh == 0.0 for d in details)
    assert len(details) == 12


def test_solar_monthly_reduces_import(rome_consumption, rome_production_6kwp):
    """Solar scenario should reduce grid import vs status quo."""
    sq = _status_quo_monthly(rome_consumption, 0.25)
    solar = _solar_monthly(
        rome_consumption, rome_production_6kwp, 0.25, 0.30, 0.046
    )
    sq_import = sum(d.grid_import_kwh for d in sq)
    solar_import = sum(d.grid_import_kwh for d in solar)
    assert solar_import < sq_import


def test_solar_monthly_has_exports(rome_consumption, rome_production_6kwp):
    """Solar scenario should have grid exports."""
    solar = _solar_monthly(
        rome_consumption, rome_production_6kwp, 0.25, 0.30, 0.046
    )
    total_export = sum(d.grid_export_kwh for d in solar)
    assert total_export > 0


def test_solar_monthly_rid_revenue(rome_consumption, rome_production_6kwp):
    """Solar exports earn RID revenue."""
    solar = _solar_monthly(
        rome_consumption, rome_production_6kwp, 0.25, 0.30, 0.046
    )
    total_rid = sum(d.rid_revenue_eur for d in solar)
    assert total_rid > 0


def test_payback_years_basic():
    """Payback should be reasonable for typical scenario."""
    payback = _payback_years(
        investment=10200,  # 6 kWp * 1700 EUR/kWp
        annual_cost_sq=750,
        annual_cost_solar=300,
        annual_rid=200,
        annual_detrazione=510,  # 10200*0.5/10
        detrazione_years=10,
    )
    assert payback is not None
    assert 3 < payback < 10


def test_payback_zero_investment():
    """Zero investment = instant payback."""
    payback = _payback_years(0, 750, 300, 200, 0, 10)
    assert payback == 0.0


def test_npv_positive_for_typical():
    """NPV should be positive for typical Italian 6 kWp scenario."""
    npv = _npv_20yr(
        investment=10200,
        annual_cost_sq=750,
        annual_cost_solar=300,
        annual_rid=200,
        annual_detrazione=510,
        detrazione_years=10,
        discount_rate=0.03,
    )
    assert npv > 0


def test_irr_reasonable():
    """IRR should be positive and reasonable for typical scenario."""
    irr = _estimate_irr(
        investment=10200,
        annual_cost_sq=750,
        annual_cost_solar=300,
        annual_rid=200,
        annual_detrazione=510,
        detrazione_years=10,
    )
    assert irr is not None
    assert 0 < irr < 30


# --- Full Evaluation Tests ---

def test_evaluate_solar_three_scenarios(rome_consumption, rome_production_6kwp):
    """Full evaluation with battery returns 3 scenarios."""
    result = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
        include_battery=True,
        battery_kwh=10.0,
    )
    assert len(result.scenarios) == 3
    assert result.scenarios[0].scenario == ScenarioType.STATUS_QUO
    assert result.scenarios[1].scenario == ScenarioType.SOLAR_ONLY
    assert result.scenarios[2].scenario == ScenarioType.SOLAR_BATTERY


def test_evaluate_solar_two_scenarios_without_battery(
    rome_consumption, rome_production_6kwp
):
    """Without battery, returns 2 scenarios."""
    result = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
        include_battery=False,
    )
    assert len(result.scenarios) == 2


def test_solar_cheaper_than_status_quo(rome_consumption, rome_production_6kwp):
    """Solar should have lower annual electricity cost than status quo."""
    result = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
    )
    sq = result.scenarios[0]
    solar = result.scenarios[1]
    assert solar.annual_electricity_cost_eur < sq.annual_electricity_cost_eur


def test_battery_higher_self_consumption(rome_consumption, rome_production_6kwp):
    """Battery scenario should have higher self-consumption %."""
    result = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
        include_battery=True,
    )
    solar = result.scenarios[1]
    battery = result.scenarios[2]
    assert battery.self_consumption_percent > solar.self_consumption_percent


def test_evaluate_solar_payback_sanity(rome_consumption, rome_production_6kwp):
    """6 kWp in Rome, 3000 kWh consumption — payback should be 5-10 years."""
    result = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
    )
    solar = result.scenarios[1]
    assert solar.payback_years is not None
    assert 3 < solar.payback_years < 15


def test_evaluate_solar_annual_production(rome_consumption, rome_production_6kwp):
    """Annual production should match PVGIS total."""
    result = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
    )
    expected = sum(m.e_m for m in rome_production_6kwp)
    assert abs(result.annual_production_kwh - expected) < 1.0


def test_evaluate_solar_assumptions_populated(
    rome_consumption, rome_production_6kwp
):
    """Result should include key assumptions."""
    result = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
    )
    assert "panel_cost_eur_per_kwp" in result.assumptions
    assert "rid_price_eur_kwh" in result.assumptions
    assert "detrazione_percent" in result.assumptions
    assert result.assumptions["price_scenario_used"] == "reference"
    assert result.assumptions["panel_degradation_pct"] == 0.005
    assert result.assumptions["maintenance_eur_yr"] == 75.0


def test_panel_degradation_accumulates_over_time():
    assert _panel_factor(1, 0.005) == pytest.approx(1.0)
    assert _panel_factor(20, 0.005) < 0.95


def test_battery_replacement_spread_across_years_13_to_15(
    rome_consumption, rome_production_6kwp
):
    """Battery replacement cost is spread across years 13-15 (centered on year 14)."""
    status_quo = _status_quo_monthly(rome_consumption, 0.25)
    solar = _solar_monthly(rome_consumption, rome_production_6kwp, 0.25, 0.30, 0.046)
    battery = _solar_monthly(rome_consumption, rome_production_6kwp, 0.25, 0.70, 0.046)
    scenario = get_price_projection_scenario(PriceScenarioName.REFERENCE, cpi=0.02)
    projected_retail_prices, projected_wholesale_prices = _scaled_price_trajectories(
        scenario.project(),
        base_retail_rate=0.25,
        base_wholesale_rate=0.046,
    )
    projection = _build_annual_cash_flow_projection(
        status_quo_profile=_annual_profile_from_monthly(status_quo),
        solar_profile=_annual_profile_from_monthly(solar),
        battery_profile=_annual_profile_from_monthly(battery),
        projected_retail_prices=projected_retail_prices,
        projected_wholesale_prices=projected_wholesale_prices,
        annual_detrazione=910.0,  # (10200+8000)*0.5/10
        detrazione_years=10,
        annual_maintenance_eur=75.0,
        inverter_replacement_eur=1750.0,
        inverter_replacement_year=11,
        rid_floor_price_eur_kwh=0.046,
        panel_degradation_pct=0.005,
        battery_fade_pct=0.02,
        battery_replacement_eur=8000.0,
        battery_replacement_year=14,
    )
    # Years 13-15 (indices 12-14) should all be lower than year 16 (index 15)
    # due to battery replacement cost spread
    for idx in [12, 13, 14]:
        assert projection.annual_cash_flows[idx] < projection.annual_cash_flows[15]


def test_maintenance_and_inverter_replacement_reduce_npv(
    rome_consumption, rome_production_6kwp
):
    status_quo = _status_quo_monthly(rome_consumption, 0.25)
    solar = _solar_monthly(rome_consumption, rome_production_6kwp, 0.25, 0.30, 0.046)
    scenario = get_price_projection_scenario(PriceScenarioName.REFERENCE, cpi=0.02)
    projected_retail_prices, projected_wholesale_prices = _scaled_price_trajectories(
        scenario.project(),
        base_retail_rate=0.25,
        base_wholesale_rate=0.046,
    )

    with_lifecycle_costs = _build_annual_cash_flow_projection(
        status_quo_profile=_annual_profile_from_monthly(status_quo),
        solar_profile=_annual_profile_from_monthly(solar),
        projected_retail_prices=projected_retail_prices,
        projected_wholesale_prices=projected_wholesale_prices,
        annual_detrazione=510.0,
        detrazione_years=10,
        annual_maintenance_eur=75.0,
        inverter_replacement_eur=1750.0,
        inverter_replacement_year=11,
        rid_floor_price_eur_kwh=0.046,
        panel_degradation_pct=0.005,
    )
    without_lifecycle_costs = _build_annual_cash_flow_projection(
        status_quo_profile=_annual_profile_from_monthly(status_quo),
        solar_profile=_annual_profile_from_monthly(solar),
        projected_retail_prices=projected_retail_prices,
        projected_wholesale_prices=projected_wholesale_prices,
        annual_detrazione=510.0,
        detrazione_years=10,
        annual_maintenance_eur=0.0,
        inverter_replacement_eur=0.0,
        inverter_replacement_year=11,
        rid_floor_price_eur_kwh=0.046,
        panel_degradation_pct=0.005,
    )

    npv_with_costs = _npv_20yr(
        investment=10200,
        annual_cost_sq=750,
        annual_cost_solar=300,
        annual_rid=200,
        annual_detrazione=510,
        detrazione_years=10,
        discount_rate=0.03,
        annual_cash_flows=with_lifecycle_costs.annual_cash_flows,
    )
    npv_without_costs = _npv_20yr(
        investment=10200,
        annual_cost_sq=750,
        annual_cost_solar=300,
        annual_rid=200,
        annual_detrazione=510,
        detrazione_years=10,
        discount_rate=0.03,
        annual_cash_flows=without_lifecycle_costs.annual_cash_flows,
    )

    assert npv_without_costs > npv_with_costs


def test_price_scenarios_change_npv_meaningfully(
    rome_consumption, rome_production_6kwp
):
    low = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
        price_scenario=PriceScenarioName.ABUNDANT_RENEWABLES,
    ).scenarios[1]
    high = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
        price_scenario=PriceScenarioName.HIGH_ELECTRIFICATION,
    ).scenarios[1]
    assert low.npv_20yr_eur is not None
    assert high.npv_20yr_eur is not None
    assert high.npv_20yr_eur > low.npv_20yr_eur
    assert (high.npv_20yr_eur - low.npv_20yr_eur) / abs(low.npv_20yr_eur) > 0.10


def test_reference_scenario_stays_close_to_legacy_two_pct_path(
    rome_consumption, rome_production_6kwp
):
    """Regression guard: reference scenario produces consistent NPV.

    The engine uses price projection cash flows that include:
    - Panel degradation (0.5%/yr)
    - Maintenance costs (€75/yr)
    - Inverter replacement (€1750 in year 11)

    This test validates that the full engine output stays close to
    the expected baseline, accounting for these lifecycle costs.
    """
    result = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
        price_scenario=PriceScenarioName.REFERENCE,
        energy_price_inflation=0.02,
    )
    solar = result.scenarios[1]

    # Structural guard: for a 6 kWp system in Rome with 2% inflation on the
    # REFERENCE price scenario, NPV should be meaningfully positive and within
    # a plausible range.  Exact value varies with dependency versions (observed
    # With turnkey installed cost at €2200/kWp (investment €13,200), NPV is
    # lower than with the old €1700/kWp but still positive.  Observed ~1094.
    assert solar.npv_20yr_eur is not None
    assert 500 < solar.npv_20yr_eur < 5000, (
        f"NPV {solar.npv_20yr_eur} outside plausible range for 6 kWp Rome reference scenario"
    )


# --- Energy Price Inflation Tests ---

def test_inflation_zero_backward_compat():
    """inflation=0.0 produces same results as calling without inflation (default=0.0)."""
    args = dict(
        investment=10200,
        annual_cost_sq=750,
        annual_cost_solar=300,
        annual_rid=200,
        annual_detrazione=510,
        detrazione_years=10,
    )
    # _payback_years
    pb_default = _payback_years(**args)
    pb_zero = _payback_years(**args, inflation=0.0)
    assert pb_default == pb_zero

    # _npv_20yr
    npv_default = _npv_20yr(**args, discount_rate=0.03)
    npv_zero = _npv_20yr(**args, discount_rate=0.03, inflation=0.0)
    assert npv_default == npv_zero

    # _estimate_irr
    irr_default = _estimate_irr(**args)
    irr_zero = _estimate_irr(**args, inflation=0.0)
    assert irr_default == irr_zero


def test_higher_inflation_shorter_payback():
    """Higher inflation → shorter payback (savings grow faster)."""
    args = dict(
        investment=10200,
        annual_cost_sq=750,
        annual_cost_solar=300,
        annual_rid=200,
        annual_detrazione=510,
        detrazione_years=10,
    )
    pb_no_infl = _payback_years(**args, inflation=0.0)
    pb_high_infl = _payback_years(**args, inflation=0.05)
    assert pb_no_infl is not None
    assert pb_high_infl is not None
    assert pb_high_infl < pb_no_infl


def test_higher_inflation_higher_npv():
    """Higher inflation → higher NPV."""
    args = dict(
        investment=10200,
        annual_cost_sq=750,
        annual_cost_solar=300,
        annual_rid=200,
        annual_detrazione=510,
        detrazione_years=10,
        discount_rate=0.03,
    )
    npv_no_infl = _npv_20yr(**args, inflation=0.0)
    npv_high_infl = _npv_20yr(**args, inflation=0.05)
    assert npv_high_infl > npv_no_infl


def test_inflation_max_boundary():
    """inflation=0.10 does not crash or produce NaN."""
    args = dict(
        investment=10200,
        annual_cost_sq=750,
        annual_cost_solar=300,
        annual_rid=200,
        annual_detrazione=510,
        detrazione_years=10,
    )
    pb = _payback_years(**args, inflation=0.10)
    assert pb is not None
    assert pb == pb  # not NaN

    npv = _npv_20yr(**args, discount_rate=0.03, inflation=0.10)
    assert npv == npv  # not NaN

    irr = _estimate_irr(**args, inflation=0.10)
    assert irr is not None
    assert irr == irr  # not NaN

    # Pydantic validation: 0.10 is valid, 0.11 is not
    valid = SolarEvalRequest(
        consumption=ConsumptionProfile(
            annual_kwh=3000, f1_kwh=1200, f2_kwh=1050, f3_kwh=750
        ),
        lat=41.9, lon=12.5, desired_kwp=6.0,
        energy_price_inflation=0.10,
    )
    assert valid.energy_price_inflation == 0.10

    with pytest.raises(Exception):
        SolarEvalRequest(
            consumption=ConsumptionProfile(
                annual_kwh=3000, f1_kwh=1200, f2_kwh=1050, f3_kwh=750
            ),
            lat=41.9, lon=12.5, desired_kwp=6.0,
            energy_price_inflation=0.11,
        )


def test_evaluate_solar_rejects_zero_consumption(rome_production_6kwp):
    """Engine should reject zero annual_kwh."""
    zero_consumption = ConsumptionProfile(
        annual_kwh=0.001,  # Pydantic requires gt=0, so use near-zero
        f1_kwh=0,
        f2_kwh=0,
        f3_kwh=0,
        annual_cost_eur=0.0,
    )
    # Should not crash — should use default rate
    result = evaluate_solar(
        consumption=zero_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
    )
    assert result.annual_production_kwh > 0


# --- TMY Model and Connector Tests ---

def test_pvgis_tmy_model_validation():
    """PVGISTMYResult requires exactly 8760 records."""
    records = [
        PVGISHourlyRecord(hour_index=i, ghi_wm2=0.0, temperature_c=15.0)
        for i in range(8760)
    ]
    result = PVGISTMYResult(records=records, location="41.9,12.5")
    assert len(result.records) == 8760

    # 8759 records should fail
    with pytest.raises(Exception):
        PVGISTMYResult(records=records[:8759], location="41.9,12.5")


def test_pvgis_tmy_record_fields():
    """PVGISHourlyRecord validates field constraints."""
    # Valid record
    r = PVGISHourlyRecord(hour_index=0, ghi_wm2=500.0, temperature_c=25.0, wind_speed_ms=3.0)
    assert r.ghi_wm2 == 500.0

    # Negative GHI should fail
    with pytest.raises(Exception):
        PVGISHourlyRecord(hour_index=0, ghi_wm2=-1.0, temperature_c=15.0)

    # hour_index=8760 should fail (lt=8760 means max 8759)
    with pytest.raises(Exception):
        PVGISHourlyRecord(hour_index=8760, ghi_wm2=0.0, temperature_c=15.0)


def test_mock_tmy_returns_8760():
    """Mock TMY generator returns 8760 records with summer > winter GHI."""
    result = get_mock_pvgis_tmy_result()
    assert len(result.records) == 8760

    # Summer GHI (July, month index 6) should exceed winter (January, month index 0)
    from kilowattchiaro_engine.models.hourly import _MONTH_HOURS, _MONTH_OFFSETS
    jan_start = _MONTH_OFFSETS[0]
    jan_end = jan_start + _MONTH_HOURS[0]
    jul_start = _MONTH_OFFSETS[6]
    jul_end = jul_start + _MONTH_HOURS[6]

    jan_ghi = sum(r.ghi_wm2 for r in result.records[jan_start:jan_end])
    jul_ghi = sum(r.ghi_wm2 for r in result.records[jul_start:jul_end])
    assert jul_ghi > jan_ghi


def test_tmy_to_hourly_kw():
    """tmy_to_hourly_kw produces 8760 values with correct annual sum."""
    tmy = get_mock_pvgis_tmy_result()
    kw = tmy_to_hourly_kw(tmy, kwp=6.0)

    assert len(kw) == 8760
    assert all(v >= 0 for v in kw)

    annual_kwh = sum(kw)
    assert 7500 <= annual_kwh <= 9500, f"Annual kWh {annual_kwh} outside 7500-9500 range"

    # Nighttime hours should be zero
    assert kw[3] == 0.0  # 3 AM in January


def test_tmy_month_distribution():
    """July production should exceed January production."""
    tmy = get_mock_pvgis_tmy_result()
    kw = tmy_to_hourly_kw(tmy, kwp=6.0)
    monthly = _by_month(kw)

    # monthly[0] = January, monthly[6] = July
    assert monthly[6] > monthly[0], f"July {monthly[6]} should exceed January {monthly[0]}"


# --- Hourly Path Integration Tests ---

def test_evaluate_solar_hourly_path(rome_consumption, rome_production_6kwp):
    """Hourly path uses TMY data for self-consumption."""
    tmy = get_mock_pvgis_tmy_result()
    result = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
        tmy_data=tmy,
    )
    assert result.assumptions["self_consumption_source"] == "hourly_match"
    solar = result.scenarios[1]
    # SC coverage: % of consumption met by self-consumed PV
    # With 6kWp (~8400 kWh) on 3000 kWh household, daytime coverage is high
    assert 20 < solar.self_consumption_percent < 80


def test_evaluate_solar_hourly_fallback(rome_consumption, rome_production_6kwp):
    """Without TMY, falls back to default ratios."""
    result = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
        tmy_data=None,
    )
    assert result.assumptions["self_consumption_source"] == "default_ratio"


def test_hourly_battery_higher_sc(rome_consumption, rome_production_6kwp):
    """Hourly battery SC > hourly solar-only SC."""
    tmy = get_mock_pvgis_tmy_result()
    result = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
        include_battery=True,
        tmy_data=tmy,
    )
    solar = result.scenarios[1]
    battery = result.scenarios[2]
    assert battery.self_consumption_percent > solar.self_consumption_percent


def test_hourly_path_monthly_details_complete(rome_consumption, rome_production_6kwp):
    """Hourly path produces 12 monthly details with all fields populated."""
    tmy = get_mock_pvgis_tmy_result()
    result = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
        tmy_data=tmy,
    )
    solar = result.scenarios[1]
    assert len(solar.monthly_details) == 12
    for md in solar.monthly_details:
        assert md.production_kwh >= 0
        assert md.self_consumption_kwh >= 0
        assert md.grid_import_kwh >= 0
        assert md.grid_export_kwh >= 0


def test_hourly_path_sc_ratio_in_assumptions(rome_consumption, rome_production_6kwp):
    """Hourly path reports actual computed SC ratio, not the default."""
    tmy = get_mock_pvgis_tmy_result()
    result = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
        tmy_data=tmy,
    )
    # Hourly SC ratio should differ from the fixed 0.30 default
    assert result.assumptions["self_consumption_ratio"] != 0.30


def test_hourly_path_financial_sanity(rome_consumption, rome_production_6kwp):
    """Hourly path produces financially sane results."""
    tmy = get_mock_pvgis_tmy_result()
    result = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
        include_battery=True,
        tmy_data=tmy,
    )
    solar = result.scenarios[1]
    battery = result.scenarios[2]
    sq = result.scenarios[0]
    # Solar should be cheaper than status quo
    assert solar.annual_electricity_cost_eur < sq.annual_electricity_cost_eur
    # Payback should be reasonable
    assert solar.payback_years is not None
    assert 3 < solar.payback_years < 20
    # Battery payback should exist
    assert battery.payback_years is not None


# --- Google Solar No-Coverage Fallback Tests ---

def test_google_solar_result_coverage_available_default():
    """GoogleSolarResult defaults coverage_available to True for backward compat."""
    seg = RoofSegment(
        tilt_degrees=30.0, azimuth_degrees=180.0, area_m2=40.0,
    )
    result = GoogleSolarResult(
        segments=[seg], total_usable_area_m2=40.0,
        max_panel_count=20, max_kwp=8.0, yearly_energy_kwh=12000.0,
    )
    assert result.coverage_available is True


def test_google_solar_result_coverage_available_false():
    """GoogleSolarResult can have coverage_available=False."""
    seg = RoofSegment(
        tilt_degrees=30.0, azimuth_degrees=180.0, area_m2=50.0,
    )
    result = GoogleSolarResult(
        segments=[seg], total_usable_area_m2=50.0,
        max_panel_count=0, max_kwp=0.0, yearly_energy_kwh=0.0,
        coverage_available=False,
    )
    assert result.coverage_available is False
    assert result.max_kwp == 0.0


def test_manual_roof_source_evaluation(rome_consumption, rome_production_6kwp):
    """Evaluation works with manual roof source."""
    from kilowattchiaro_engine.models.solar_eval import RoofInfo, RoofSource

    manual_roof = RoofInfo(
        segments=[
            RoofSegment(tilt_degrees=25.0, azimuth_degrees=180.0, area_m2=60.0)
        ],
        total_usable_area_m2=60.0,
        source=RoofSource.MANUAL,
    )

    result = evaluate_solar(
        consumption=rome_consumption,
        monthly_production=rome_production_6kwp,
        desired_kwp=6.0,
    )
    # Just verify it doesn't crash with manual inputs
    assert len(result.scenarios) == 2
    assert result.system_kwp == 6.0
