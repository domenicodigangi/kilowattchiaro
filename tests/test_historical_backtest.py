"""Tests for historical decision-point backtest engine.

Covers: no look-ahead bias, regime selection per date, cost curve application,
acceptance criteria (NPV ordering), battery edge cases, and prediction error tracking.
"""

import math
from datetime import date

import pytest

from kilowattchiaro_engine.historical_backtest import (
    BATTERY_COST_EUR_PER_KWH,
    STANDARD_DECISION_DATES,
    KnowledgeAtTime,
    _build_knowledge_at_time,
    _compute_actual_metrics,
    _compute_predicted_metrics,
    _npv_fit,
    run_historical_backtest,
    run_standard_backtest,
)
from kilowattchiaro_engine.incentive_regimes import (
    ExportMechanism,
    IncentiveType,
    get_electricity_price_at_year,
    get_pv_cost_at_year,
)
from kilowattchiaro_engine.models.solar_eval import ConsumptionProfile, PVGISMonthly


# --- Fixtures ---


@pytest.fixture
def rome_production_6kwp() -> list[PVGISMonthly]:
    """Approximate PVGIS output for 6 kWp in Rome (~8680 kWh/year)."""
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


@pytest.fixture
def default_consumption() -> ConsumptionProfile:
    """Reference scenario: 3000 kWh/yr Roman household."""
    return ConsumptionProfile(
        annual_kwh=3000, f1_kwh=1200, f2_kwh=900, f3_kwh=900,
    )


# ---------------------------------------------------------------------------
# No look-ahead bias
# ---------------------------------------------------------------------------


class TestNoLookAheadBias:
    def test_2018_uses_2018_electricity_price(self):
        """2018 knowledge must use 2018 price, NOT 2022 crisis price."""
        knowledge = _build_knowledge_at_time(date(2018, 7, 1))
        assert knowledge.electricity_price_eur_kwh == get_electricity_price_at_year(2018)
        assert knowledge.electricity_price_eur_kwh == pytest.approx(0.22)
        # Must NOT be the 2022 crisis price
        assert knowledge.electricity_price_eur_kwh != pytest.approx(0.34)

    def test_2015_uses_detrazione_not_superbonus(self):
        """2015 knowledge uses Detrazione 50%, not Superbonus (which starts 2020)."""
        knowledge = _build_knowledge_at_time(date(2015, 7, 1))
        assert knowledge.regime is not None
        assert knowledge.regime.name == "Detrazione 50%"
        assert knowledge.regime.incentive_type != IncentiveType.SUPERBONUS

    def test_2010_uses_conto_energia_ii(self):
        """2010 knowledge uses CE II feed-in tariff."""
        knowledge = _build_knowledge_at_time(date(2010, 7, 1))
        assert knowledge.regime is not None
        assert knowledge.regime.name == "Conto Energia II"
        assert knowledge.regime.incentive_type == IncentiveType.FEED_IN_TARIFF

    def test_2008_does_not_know_2012_cost(self):
        """2008 PV cost is 5200, not the 2012 cost of 2800."""
        knowledge = _build_knowledge_at_time(date(2008, 7, 1))
        assert knowledge.pv_cost_eur_per_kwp == 5200
        assert knowledge.pv_cost_eur_per_kwp != 2800


# ---------------------------------------------------------------------------
# Regime selection per date
# ---------------------------------------------------------------------------


class TestRegimeSelection:
    def test_2008_conto_energia_ii(self):
        k = _build_knowledge_at_time(date(2008, 7, 1))
        assert k.regime is not None
        assert k.regime.name == "Conto Energia II"

    def test_2012_conto_energia_iv(self):
        k = _build_knowledge_at_time(date(2012, 1, 1))
        assert k.regime is not None
        assert "Conto Energia IV" in k.regime.name

    def test_2013_10_detrazione(self):
        k = _build_knowledge_at_time(date(2013, 10, 1))
        assert k.regime is not None
        assert k.regime.name == "Detrazione 50%"

    def test_2020_10_superbonus(self):
        k = _build_knowledge_at_time(date(2020, 10, 1))
        assert k.regime is not None
        assert k.regime.name == "Superbonus 110%"

    def test_2025_detrazione(self):
        k = _build_knowledge_at_time(date(2025, 1, 1))
        assert k.regime is not None
        assert k.regime.name == "Detrazione 50%"


# ---------------------------------------------------------------------------
# Cost curve application
# ---------------------------------------------------------------------------


class TestCostCurve:
    def test_2008_pv_cost(self):
        assert get_pv_cost_at_year(2008) == 5200

    def test_2012_pv_cost(self):
        assert get_pv_cost_at_year(2012) == 2800

    def test_2020_pv_cost(self):
        assert get_pv_cost_at_year(2020) == 1800

    def test_investment_uses_cost_curve(self, rome_production_6kwp, default_consumption):
        """Backtest investment should use historical PV cost, not current."""
        result_2008 = run_historical_backtest(
            date(2008, 7, 1), rome_production_6kwp, default_consumption, 6.0,
        )
        result_2020 = run_historical_backtest(
            date(2020, 10, 1), rome_production_6kwp, default_consumption, 6.0,
        )
        assert result_2008.investment_eur > result_2020.investment_eur
        assert result_2008.pv_cost_eur_per_kwp == 5200
        assert result_2020.pv_cost_eur_per_kwp == 1800


# ---------------------------------------------------------------------------
# Acceptance criteria (from issue)
# ---------------------------------------------------------------------------


class TestAcceptanceCriteria:
    def test_2012_ce_iv_npv_positive(self, rome_production_6kwp, default_consumption):
        """Predicted NPV for 2012 (Conto Energia IV) is significantly positive."""
        result = run_historical_backtest(
            date(2012, 1, 1), rome_production_6kwp, default_consumption, 6.0,
        )
        assert result.regime_name == "Conto Energia IV"
        assert result.predicted.npv_20yr_eur > 5000, (
            f"CE IV predicted NPV should be significantly positive, got {result.predicted.npv_20yr_eur}"
        )

    def test_2013_npv_lower_than_2012(self, rome_production_6kwp, default_consumption):
        """Predicted NPV for 2013 (transition to Detrazione) is lower than 2012 (CE IV)."""
        result_2012 = run_historical_backtest(
            date(2012, 1, 1), rome_production_6kwp, default_consumption, 6.0,
        )
        result_2013 = run_historical_backtest(
            date(2013, 10, 1), rome_production_6kwp, default_consumption, 6.0,
        )
        assert result_2012.predicted.npv_20yr_eur > result_2013.predicted.npv_20yr_eur, (
            f"CE IV NPV ({result_2012.predicted.npv_20yr_eur}) should exceed "
            f"Detrazione NPV ({result_2013.predicted.npv_20yr_eur})"
        )

    def test_2015_actual_profitable(self, rome_production_6kwp, default_consumption):
        """Actual outcome for 2015 (Detrazione 50% + actual prices) shows profitability."""
        result = run_historical_backtest(
            date(2015, 7, 1), rome_production_6kwp, default_consumption, 6.0,
        )
        assert result.actual.npv_to_date_eur > 0, (
            f"2015 actual NPV should be positive, got {result.actual.npv_to_date_eur}"
        )

    def test_prediction_error_tracked(self, rome_production_6kwp, default_consumption):
        """Every result has prediction vs actual error."""
        results = run_standard_backtest(rome_production_6kwp, default_consumption)
        for r in results:
            assert r.npv_prediction_error_eur is not None
            assert math.isfinite(r.npv_prediction_error_eur)

    def test_each_decision_point_uses_historical_info(
        self, rome_production_6kwp, default_consumption
    ):
        """Each decision point result carries correct historical parameters."""
        results = run_standard_backtest(rome_production_6kwp, default_consumption)
        for r in results:
            expected_cost = get_pv_cost_at_year(r.decision_date.year)
            assert r.pv_cost_eur_per_kwp == expected_cost
            expected_price = get_electricity_price_at_year(r.decision_date.year)
            assert r.electricity_price_at_decision == expected_price


# ---------------------------------------------------------------------------
# Standard backtest runner
# ---------------------------------------------------------------------------


class TestStandardBacktest:
    def test_returns_9_results(self, rome_production_6kwp, default_consumption):
        """Standard backtest covers all 9 decision points."""
        results = run_standard_backtest(rome_production_6kwp, default_consumption)
        assert len(results) == len(STANDARD_DECISION_DATES)
        assert len(results) == 9

    def test_default_consumption(self, rome_production_6kwp):
        """run_standard_backtest works with default consumption (None)."""
        results = run_standard_backtest(rome_production_6kwp)
        assert len(results) == 9
        assert all(r.system_kwp == 6.0 for r in results)

    def test_results_have_all_fields(self, rome_production_6kwp, default_consumption):
        """Every result has required fields populated."""
        results = run_standard_backtest(rome_production_6kwp, default_consumption)
        for r in results:
            assert r.decision_date in STANDARD_DECISION_DATES
            assert r.regime_name != ""
            assert r.investment_eur > 0
            assert r.predicted.investment_eur > 0
            assert r.actual.years_elapsed >= 0


# ---------------------------------------------------------------------------
# Battery edge cases
# ---------------------------------------------------------------------------


class TestBatteryScenarios:
    def test_pre_2018_battery_poor_economics(
        self, rome_production_6kwp, default_consumption
    ):
        """Battery scenario for pre-2018 should have worse NPV than solar-only."""
        result_no_bat = run_historical_backtest(
            date(2015, 7, 1), rome_production_6kwp, default_consumption, 6.0,
            include_battery=False,
        )
        result_bat = run_historical_backtest(
            date(2015, 7, 1), rome_production_6kwp, default_consumption, 6.0,
            include_battery=True, battery_kwh=10.0,
        )
        # Pre-2018 battery cost is very high — worse NPV
        assert result_bat.predicted.npv_20yr_eur < result_no_bat.predicted.npv_20yr_eur

    def test_battery_increases_investment(
        self, rome_production_6kwp, default_consumption
    ):
        """Battery scenario should have higher investment."""
        result_no_bat = run_historical_backtest(
            date(2020, 10, 1), rome_production_6kwp, default_consumption, 6.0,
            include_battery=False,
        )
        result_bat = run_historical_backtest(
            date(2020, 10, 1), rome_production_6kwp, default_consumption, 6.0,
            include_battery=True, battery_kwh=10.0,
        )
        assert result_bat.investment_eur > result_no_bat.investment_eur

    def test_battery_cost_curve(self):
        """Battery cost should decrease over time (except supply chain uptick)."""
        assert BATTERY_COST_EUR_PER_KWH[2018] > BATTERY_COST_EUR_PER_KWH[2025]
        assert BATTERY_COST_EUR_PER_KWH[2015] > BATTERY_COST_EUR_PER_KWH[2020]


# ---------------------------------------------------------------------------
# Single backtest result structure
# ---------------------------------------------------------------------------


class TestSingleBacktest:
    def test_2020_superbonus_result(self, rome_production_6kwp, default_consumption):
        """Superbonus 110% at 2020 should show extreme returns."""
        result = run_historical_backtest(
            date(2020, 10, 1), rome_production_6kwp, default_consumption, 6.0,
        )
        assert result.regime_name == "Superbonus 110%"
        assert result.regime_type == "superbonus"
        # 110% deduction means effective cost is negative
        assert result.predicted.npv_20yr_eur > 0

    def test_2008_conto_energia_ii(self, rome_production_6kwp, default_consumption):
        """CE II (2008) should have high FiT rate despite high PV cost."""
        result = run_historical_backtest(
            date(2008, 7, 1), rome_production_6kwp, default_consumption, 6.0,
        )
        assert result.regime_name == "Conto Energia II"
        assert result.regime_type == "feed_in_tariff"
        assert result.pv_cost_eur_per_kwp == 5200
        # CE II rate was EUR 0.48/kWh — very generous
        assert result.predicted.npv_20yr_eur > 0

    def test_prediction_error_sign(self, rome_production_6kwp, default_consumption):
        """Prediction error = actual - predicted. Can be positive or negative."""
        result = run_historical_backtest(
            date(2015, 7, 1), rome_production_6kwp, default_consumption, 6.0,
        )
        expected_error = result.actual.npv_to_date_eur - result.predicted.npv_20yr_eur
        assert abs(result.npv_prediction_error_eur - expected_error) < 0.1

    def test_recent_date_short_actual_period(
        self, rome_production_6kwp, default_consumption
    ):
        """2025 decision point should have 0 years elapsed."""
        result = run_historical_backtest(
            date(2025, 1, 1), rome_production_6kwp, default_consumption, 6.0,
        )
        assert result.actual.years_elapsed == 0

    def test_2008_long_actual_period(self, rome_production_6kwp, default_consumption):
        """2008 should have 17 years of actual data (capped at 20)."""
        result = run_historical_backtest(
            date(2008, 7, 1), rome_production_6kwp, default_consumption, 6.0,
        )
        assert result.actual.years_elapsed == 17  # 2025 - 2008


# ---------------------------------------------------------------------------
# Export mechanism
# ---------------------------------------------------------------------------


class TestExportMechanism:
    def test_pre_2025_uses_ssp(self):
        k = _build_knowledge_at_time(date(2020, 1, 1))
        assert k.export_mechanism == ExportMechanism.SSP

    def test_2025_uses_rid(self):
        k = _build_knowledge_at_time(date(2025, 6, 1))
        assert k.export_mechanism == ExportMechanism.RID


# ---------------------------------------------------------------------------
# FiT non-inflation
# ---------------------------------------------------------------------------


class TestFiTNonInflation:
    def test_fixed_revenue_does_not_inflate(self):
        """FiT revenue (fixed_revenue) must not grow year-over-year in NPV calc."""
        investment = 10_000
        fixed_revenue = 1000.0  # locked FiT revenue
        sc_savings_base = 0.0  # no SC savings
        inflation = 0.02
        discount_rate = 0.03

        # With zero sc_savings_base, NPV should equal discounted fixed annuity - investment
        npv = _npv_fit(investment, sc_savings_base, fixed_revenue, discount_rate, inflation)
        expected = -investment + sum(
            fixed_revenue / (1 + discount_rate) ** y for y in range(1, 21)
        )
        assert npv == pytest.approx(expected, abs=0.1)

    def test_only_sc_savings_inflates(self):
        """Only the SC savings component should grow with inflation."""
        investment = 10_000
        fixed_revenue = 800.0
        sc_savings_base = 200.0
        inflation = 0.02
        discount_rate = 0.03

        npv = _npv_fit(investment, sc_savings_base, fixed_revenue, discount_rate, inflation)
        expected = -investment + sum(
            (fixed_revenue + sc_savings_base * (1 + inflation) ** y) / (1 + discount_rate) ** y
            for y in range(1, 21)
        )
        assert npv == pytest.approx(expected, abs=0.1)
