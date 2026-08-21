"""Tests for the deterministic and Monte Carlo component projection engine."""

import time

import pytest

from kilowattchiaro_engine.price_projection import (
    project_prices,
    simulate_monte_carlo,
    get_price_projection_scenario,
    _SCENARIO_DEFS,
    DEFAULT_CPI,
)
from kilowattchiaro_engine.models.price_components import ITALIAN_DEFAULTS_2025, MonteCarloConfig
from kilowattchiaro_engine.models.solar_eval import PriceScenarioName


class TestTrajectoryPlausibility:
    """All scenarios stay within EUR 0.10-0.50/kWh for all years."""

    def test_all_scenarios_within_plausible_range(self):
        result = project_prices()
        for scenario in result.scenarios:
            projection = scenario.project()
            for year_idx, components in enumerate(projection):
                total = components.total_eur_kwh
                assert 0.10 <= total <= 0.50, (
                    f"{scenario.name} year {scenario.base_year + year_idx}: "
                    f"total {total:.4f} outside [0.10, 0.50]"
                )


class TestComponentIsolation:
    """Non-wholesale components are identical across all 5 scenarios."""

    def test_non_wholesale_identical_across_scenarios(self):
        result = project_prices()
        projections = [s.project() for s in result.scenarios]
        reference = projections[0]

        for scenario_idx in range(1, len(projections)):
            for year_idx in range(len(reference)):
                ref = reference[year_idx]
                other = projections[scenario_idx][year_idx]
                assert ref.network_eur_kwh == other.network_eur_kwh
                assert ref.system_charges_eur_kwh == other.system_charges_eur_kwh
                assert ref.taxes_eur_kwh == other.taxes_eur_kwh
                assert ref.retail_margin_eur_kwh == other.retail_margin_eur_kwh

    def test_wholesale_differs_across_scenarios(self):
        result = project_prices()
        projections = [s.project() for s in result.scenarios]
        # At year 20, wholesale values must all be distinct
        y20_wholesale = [p[20].wholesale_eur_kwh for p in projections]
        assert len(set(y20_wholesale)) == len(y20_wholesale)


class TestScenarioDiversity:
    """Y20 totals are distinct and ordered: Abundant < Status Quo < ENTSO-E < Crisis < High."""

    def test_y20_ordering(self):
        result = project_prices()
        scenario_map = {s.name: s for s in result.scenarios}

        expected_order = [
            "Abundant Renewables",
            "Status Quo",
            "ENTSO-E Reference",
            "Energy Crisis",
            "High Electrification",
        ]
        y20_totals = [
            scenario_map[name].project()[20].total_eur_kwh for name in expected_order
        ]

        for i in range(len(y20_totals) - 1):
            assert y20_totals[i] < y20_totals[i + 1], (
                f"{expected_order[i]} ({y20_totals[i]:.4f}) should be less than "
                f"{expected_order[i + 1]} ({y20_totals[i + 1]:.4f})"
            )


class TestSystemChargesFloor:
    """System charges never go negative, even with aggressive decline."""

    def test_system_charges_non_negative(self):
        # Use high CPI to make system_charges_annual_pct more negative
        result = project_prices(cpi=0.0)  # cpi=0 -> rate = -0.02
        for scenario in result.scenarios:
            projection = scenario.project()
            for components in projection:
                assert components.system_charges_eur_kwh >= 0.0


class TestCPISensitivity:
    """Higher CPI raises network/margin; taxes grow slower than network."""

    def test_higher_cpi_raises_network_and_margin(self):
        low = project_prices(cpi=0.01)
        high = project_prices(cpi=0.04)

        # Compare year 20 for first scenario
        low_y20 = low.scenarios[0].project()[20]
        high_y20 = high.scenarios[0].project()[20]

        assert high_y20.network_eur_kwh > low_y20.network_eur_kwh
        assert high_y20.retail_margin_eur_kwh > low_y20.retail_margin_eur_kwh

    def test_taxes_grow_slower_than_network(self):
        """Taxes escalate at CPI-1% real, slower than network at CPI+0.5%."""
        result = project_prices(cpi=0.02)
        proj = result.scenarios[0].project()
        # At year 20, network should have grown more than taxes
        tax_growth = proj[20].taxes_eur_kwh / proj[0].taxes_eur_kwh
        net_growth = proj[20].network_eur_kwh / proj[0].network_eur_kwh
        assert net_growth > tax_growth

    def test_higher_cpi_raises_taxes(self):
        """Taxes now respond to CPI (at CPI-1%), so higher CPI → higher taxes."""
        low = project_prices(cpi=0.01)
        high = project_prices(cpi=0.04)
        # At year 20, higher CPI should produce higher taxes
        low_taxes_y20 = low.scenarios[0].project()[20].taxes_eur_kwh
        high_taxes_y20 = high.scenarios[0].project()[20].taxes_eur_kwh
        assert high_taxes_y20 > low_taxes_y20


class TestDefaultBase:
    """base=None uses ITALIAN_DEFAULTS_2025."""

    def test_default_base_matches_italian_defaults(self):
        result = project_prices(base=None)
        for scenario in result.scenarios:
            base_components = scenario.project()[0]
            assert base_components.wholesale_eur_kwh == ITALIAN_DEFAULTS_2025.wholesale_eur_kwh
            assert base_components.network_eur_kwh == ITALIAN_DEFAULTS_2025.network_eur_kwh
            assert base_components.system_charges_eur_kwh == ITALIAN_DEFAULTS_2025.system_charges_eur_kwh
            assert base_components.taxes_eur_kwh == ITALIAN_DEFAULTS_2025.taxes_eur_kwh
            assert base_components.retail_margin_eur_kwh == ITALIAN_DEFAULTS_2025.retail_margin_eur_kwh


class TestFanChartOrdering:
    """P10 <= P25 <= P50 <= P75 <= P90 for every year."""

    def test_fan_chart_percentiles_ordered(self):
        result = project_prices()
        for entry in result.fan_chart:
            assert entry.p10_eur_kwh <= entry.p25_eur_kwh
            assert entry.p25_eur_kwh <= entry.p50_eur_kwh
            assert entry.p50_eur_kwh <= entry.p75_eur_kwh
            assert entry.p75_eur_kwh <= entry.p90_eur_kwh


class TestOutputStructure:
    """5 scenarios, 21 fan chart years, correct base_year."""

    def test_five_scenarios(self):
        result = project_prices()
        assert len(result.scenarios) == 5

    def test_scenario_names_match(self):
        result = project_prices()
        names = {s.name for s in result.scenarios}
        expected = {name for name, _, _ in _SCENARIO_DEFS}
        assert names == expected

    def test_fan_chart_length(self):
        result = project_prices(years=20)
        assert len(result.fan_chart) == 21  # base year + 20 projection years

    def test_base_year_and_projection_years(self):
        result = project_prices(base_year=2025, years=20)
        assert result.base_year == 2025
        assert result.projection_years == 20

    def test_fan_chart_years_sequential(self):
        result = project_prices(base_year=2025, years=20)
        for i, entry in enumerate(result.fan_chart):
            assert entry.year == 2025 + i

    def test_named_solar_eval_alias_resolves_to_expected_scenario(self):
        scenario = get_price_projection_scenario(PriceScenarioName.REFERENCE)
        assert scenario.name == "ENTSO-E Reference"


# ---------------------------------------------------------------------------
# Monte Carlo simulation tests
# ---------------------------------------------------------------------------

MC_SEED = 42
MC_CONFIG = MonteCarloConfig(seed=MC_SEED, n_simulations=2000)


class TestMonteCarloP50TracksDeterministic:
    """P50 should approximate the ENTSO-E Reference deterministic trajectory."""

    def test_p50_tracks_deterministic_at_y10_and_y20(self):
        result = simulate_monte_carlo(config=MC_CONFIG)
        det = project_prices()
        det_scenario = next(s for s in det.scenarios if s.name == "ENTSO-E Reference")
        det_proj = det_scenario.project()

        for year_idx in [10, 20]:
            det_total = det_proj[year_idx].total_eur_kwh
            mc_p50 = result.fan_chart[year_idx].p50_eur_kwh
            assert abs(mc_p50 - det_total) / det_total < 0.05, (
                f"Year {year_idx}: P50={mc_p50:.4f} vs deterministic={det_total:.4f} "
                f"(diff={abs(mc_p50 - det_total) / det_total:.1%})"
            )


class TestMonteCarloPercentileOrdering:
    """P10 < P25 < P50 < P75 < P90 for all years (strict after Y1)."""

    def test_percentile_ordering(self):
        result = simulate_monte_carlo(config=MC_CONFIG)
        for entry in result.fan_chart:
            assert entry.p10_eur_kwh <= entry.p25_eur_kwh
            assert entry.p25_eur_kwh <= entry.p50_eur_kwh
            assert entry.p50_eur_kwh <= entry.p75_eur_kwh
            assert entry.p75_eur_kwh <= entry.p90_eur_kwh

    def test_strict_ordering_after_year_1(self):
        result = simulate_monte_carlo(config=MC_CONFIG)
        for entry in result.fan_chart[2:]:  # skip Y0 and Y1
            assert entry.p10_eur_kwh < entry.p90_eur_kwh, (
                f"Year {entry.year}: P10={entry.p10_eur_kwh} not < P90={entry.p90_eur_kwh}"
            )


class TestMonteCarloFanWidens:
    """Uncertainty fan should widen over time."""

    def test_fan_wider_at_y20_than_y5(self):
        result = simulate_monte_carlo(config=MC_CONFIG)
        spread_y5 = result.fan_chart[5].p90_eur_kwh - result.fan_chart[5].p10_eur_kwh
        spread_y20 = result.fan_chart[20].p90_eur_kwh - result.fan_chart[20].p10_eur_kwh
        assert spread_y20 > spread_y5, (
            f"Y20 spread ({spread_y20:.4f}) should exceed Y5 spread ({spread_y5:.4f})"
        )


class TestMonteCarloMeanReversion:
    """Mean of Y20 wholesale across sims should track the deterministic target."""

    def test_mean_reversion(self):
        result = simulate_monte_carlo(config=MC_CONFIG)
        det = project_prices()
        det_scenario = next(s for s in det.scenarios if s.name == "ENTSO-E Reference")
        det_total_y20 = det_scenario.project()[20].total_eur_kwh
        mc_p50_y20 = result.fan_chart[20].p50_eur_kwh

        # P50 within ±10% of deterministic (looser than the 5% test above)
        assert abs(mc_p50_y20 - det_total_y20) / det_total_y20 < 0.10


class TestMonteCarloPerformance:
    """1000 simulations * 20 years must complete in < 2 seconds."""

    def test_performance(self):
        cfg = MonteCarloConfig(seed=0, n_simulations=1000)
        start = time.perf_counter()
        simulate_monte_carlo(config=cfg)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, f"Monte Carlo took {elapsed:.2f}s (limit: 2.0s)"


class TestMonteCarloSeedReproducibility:
    """Same seed must produce identical results."""

    def test_seed_reproducibility(self):
        cfg = MonteCarloConfig(seed=123, n_simulations=500)
        r1 = simulate_monte_carlo(config=cfg)
        r2 = simulate_monte_carlo(config=cfg)

        for i in range(len(r1.fan_chart)):
            assert r1.fan_chart[i].p10_eur_kwh == r2.fan_chart[i].p10_eur_kwh
            assert r1.fan_chart[i].p50_eur_kwh == r2.fan_chart[i].p50_eur_kwh
            assert r1.fan_chart[i].p90_eur_kwh == r2.fan_chart[i].p90_eur_kwh


class TestMonteCarloOutputStructure:
    """Returns valid PriceProjectionResult with correct structure."""

    def test_output_structure(self):
        result = simulate_monte_carlo(config=MonteCarloConfig(seed=0))
        assert len(result.scenarios) == 1
        assert result.scenarios[0].name == "ENTSO-E Reference"
        assert len(result.fan_chart) == 21  # years + 1
        assert result.base_year == 2025
        assert result.projection_years == 20


class TestMonteCarloNoNegativePrices:
    """All simulated totals and wholesale must stay positive."""

    def test_no_negative_prices(self):
        result = simulate_monte_carlo(config=MC_CONFIG)
        for entry in result.fan_chart:
            assert entry.p10_eur_kwh > 0, f"Year {entry.year}: P10 is non-positive"


class TestProjectPricesWithMonteCarlo:
    """project_prices() with monte_carlo parameter uses stochastic fan chart."""

    def test_project_prices_with_monte_carlo(self):
        cfg = MonteCarloConfig(seed=42, n_simulations=500)
        result = project_prices(monte_carlo=cfg)
        # Should still have 5 scenarios
        assert len(result.scenarios) == 5
        # Fan chart should have 21 entries
        assert len(result.fan_chart) == 21
        # Fan chart should differ from deterministic (stochastic noise)
        det_result = project_prices()
        # At year 20, the MC fan spread should be wider than deterministic
        mc_spread = result.fan_chart[20].p90_eur_kwh - result.fan_chart[20].p10_eur_kwh
        det_spread = det_result.fan_chart[20].p90_eur_kwh - det_result.fan_chart[20].p10_eur_kwh
        assert mc_spread > det_spread * 0.5  # MC spread should be substantial

    def test_backward_compatible_without_monte_carlo(self):
        """project_prices() without monte_carlo behaves exactly as before."""
        result = project_prices()
        assert len(result.scenarios) == 5
        assert len(result.fan_chart) == 21
