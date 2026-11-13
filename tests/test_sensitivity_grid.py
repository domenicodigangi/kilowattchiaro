"""Ticket 312: the engine owns the verdict, the what-if grid and the robustness label.

The browser only selects from what the engine returns: a verdict on every
scenario, a grid of pre-computed results over the engine's five price scenarios
x the three cost tiers, and a robustness label derived from a grid of
detrazione x price scenario x self-consumption -10 points.
"""

from __future__ import annotations

from datetime import date

import pytest

from kilowattchiaro_engine.solar_eval import evaluate_solar
from kilowattchiaro_engine.solar_eval.models import AnnualEnergyProfile
from kilowattchiaro_engine.solar_eval.sensitivity import (
    classify_robustness,
    lower_detrazione_rate,
    shift_self_consumption,
    verdict_for,
)
from kilowattchiaro_engine.models.solar_eval import (
    ConsumptionProfile,
    PriceScenarioName,
    PVGISMonthly,
    ScenarioType,
)

PRICE_SCENARIOS = list(PriceScenarioName)
TIERS = ["basso", "medio", "alto"]

# A flat 1,300 kWh/kWp year, Pisa-like
_MONTHLY_SHARE = [
    0.04,
    0.055,
    0.08,
    0.095,
    0.11,
    0.12,
    0.125,
    0.115,
    0.09,
    0.07,
    0.05,
    0.05,
]


def _monthly(kwp: float) -> list[PVGISMonthly]:
    return [
        PVGISMonthly(month=i + 1, e_d=0.0, e_m=round(1300 * kwp * share, 1), h_m=0.0)
        for i, share in enumerate(_MONTHLY_SHARE)
    ]


def _evaluate(kwp: float = 6.0, annual_kwh: float = 4000.0, **kwargs):
    return evaluate_solar(
        consumption=ConsumptionProfile(
            annual_kwh=annual_kwh,
            f1_kwh=annual_kwh * 0.4,
            f2_kwh=annual_kwh * 0.3,
            f3_kwh=annual_kwh * 0.3,
        ),
        monthly_production=_monthly(kwp),
        desired_kwp=kwp,
        **kwargs,
    )


def _scenario(result, kind: ScenarioType):
    return next(s for s in result.scenarios if s.scenario == kind)


# --- The verdict rule ------------------------------------------------------------


@pytest.mark.parametrize(
    ("npv", "payback", "savings", "expected"),
    [
        (1500.0, 9.2, 800.0, "positive"),
        (0.0, 19.0, 800.0, "caution"),  # NPV must be strictly positive
        (1500.0, None, 800.0, "caution"),  # no break-even inside the horizon
        (-200.0, None, 300.0, "caution"),
        (-200.0, None, 0.0, "negative"),
        (None, None, -50.0, "negative"),
    ],
)
def test_verdict_rule(npv, payback, savings, expected):
    assert verdict_for(npv, payback, savings) == expected


def test_every_installed_scenario_carries_the_engine_verdict():
    result = _evaluate(include_battery=True)
    status_quo = _scenario(result, ScenarioType.STATUS_QUO)
    assert status_quo.verdict is None
    for kind in (ScenarioType.SOLAR_ONLY, ScenarioType.SOLAR_BATTERY):
        scenario = _scenario(result, kind)
        assert scenario.verdict == verdict_for(
            scenario.npv_20yr_eur, scenario.payback_years, scenario.annual_savings_eur
        )


# --- The what-if grid ------------------------------------------------------------


def test_grid_covers_five_price_scenarios_by_three_cost_tiers():
    result = _evaluate(include_battery=True)
    for kind in (ScenarioType.SOLAR_ONLY, ScenarioType.SOLAR_BATTERY):
        cells = _scenario(result, kind).sensitivity
        assert {(c.price_scenario, c.price_tier) for c in cells} == {
            (p, t) for p in PRICE_SCENARIOS for t in TIERS
        }
        assert len(cells) == 15


@pytest.mark.parametrize(
    "price_scenario", [PriceScenarioName.REFERENCE, PriceScenarioName.ENERGY_CRISIS]
)
@pytest.mark.parametrize("price_tier", [None, "basso", "alto"])
def test_the_grid_cell_of_the_request_is_the_scenario_itself(
    price_scenario, price_tier
):
    result = _evaluate(
        include_battery=True, price_scenario=price_scenario, price_tier=price_tier
    )
    for kind in (ScenarioType.SOLAR_ONLY, ScenarioType.SOLAR_BATTERY):
        scenario = _scenario(result, kind)
        cell = next(
            c
            for c in scenario.sensitivity
            if c.price_scenario == price_scenario
            and c.price_tier == (price_tier or "medio")
        )
        assert cell.investment_cost_eur == scenario.investment_cost_eur
        assert cell.annual_detrazione_eur == scenario.annual_detrazione_eur
        assert cell.annual_savings_eur == scenario.annual_savings_eur
        assert cell.annual_electricity_cost_eur == scenario.annual_electricity_cost_eur
        assert cell.annual_rid_revenue_eur == scenario.annual_rid_revenue_eur
        assert cell.npv_20yr_eur == scenario.npv_20yr_eur
        assert cell.payback_years == scenario.payback_years
        assert cell.irr_percent == scenario.irr_percent
        assert cell.cumulative_cash_flow == scenario.cumulative_cash_flow
        assert cell.annual_breakdown == scenario.annual_breakdown
        assert cell.verdict == scenario.verdict
        assert cell.robustness == scenario.robustness


def test_each_grid_cell_matches_a_full_engine_run_at_that_choice():
    base = _evaluate(include_battery=True)
    for price_scenario in (
        PriceScenarioName.ABUNDANT_RENEWABLES,
        PriceScenarioName.HIGH_ELECTRIFICATION,
    ):
        for tier in ("basso", "alto"):
            direct = _evaluate(
                include_battery=True, price_scenario=price_scenario, price_tier=tier
            )
            for kind in (ScenarioType.SOLAR_ONLY, ScenarioType.SOLAR_BATTERY):
                cell = next(
                    c
                    for c in _scenario(base, kind).sensitivity
                    if c.price_scenario == price_scenario and c.price_tier == tier
                )
                run = _scenario(direct, kind)
                assert (cell.npv_20yr_eur, cell.payback_years, cell.verdict) == (
                    run.npv_20yr_eur,
                    run.payback_years,
                    run.verdict,
                )
                assert cell.investment_cost_eur == run.investment_cost_eur
                assert cell.panel_cost_eur_per_kwp == direct.assumptions["panel_cost_eur_per_kwp"]
                with_battery = kind == ScenarioType.SOLAR_BATTERY
                expected_battery = direct.assumptions.get("battery_cost_eur") if with_battery else None
                expected_replacement = (
                    direct.assumptions.get("battery_replacement_eur") if with_battery else None
                )
                assert cell.battery_cost_eur == expected_battery
                assert cell.battery_replacement_eur == expected_replacement
                assert cell.cumulative_cash_flow == run.cumulative_cash_flow


def test_cheaper_tiers_and_dearer_prices_move_npv_the_expected_way():
    cells = _scenario(_evaluate(), ScenarioType.SOLAR_ONLY).sensitivity
    npv = {(c.price_scenario, c.price_tier): c.npv_20yr_eur for c in cells}
    ref = PriceScenarioName.REFERENCE
    assert npv[(ref, "basso")] > npv[(ref, "medio")] > npv[(ref, "alto")]
    assert (
        npv[(PriceScenarioName.HIGH_ELECTRIFICATION, "medio")]
        > npv[(ref, "medio")]
        > npv[(PriceScenarioName.ABUNDANT_RENEWABLES, "medio")]
    )


def test_explicit_costs_leave_only_the_price_axis():
    result = _evaluate(panel_cost_eur_per_kwp=1500.0)
    cells = _scenario(result, ScenarioType.SOLAR_ONLY).sensitivity
    assert {(c.price_scenario, c.price_tier) for c in cells} == {
        (p, None) for p in PRICE_SCENARIOS
    }


def test_grid_can_be_skipped_for_internal_callers():
    result = _evaluate(with_sensitivity=False)
    solar = _scenario(result, ScenarioType.SOLAR_ONLY)
    assert solar.sensitivity is None
    assert solar.robustness is None
    assert solar.verdict is not None


# --- Robustness ------------------------------------------------------------------


def test_lower_detrazione_rate_is_one_the_rules_can_apply_to_that_home():
    by_2026, from_2027 = date(2026, 12, 31), date(2027, 1, 1)
    assert lower_detrazione_rate(by_2026, main_home=True) == pytest.approx(0.36)
    assert lower_detrazione_rate(by_2026, main_home=False) == pytest.approx(0.30)
    # Paid from 2027 (or no date, the default): no lower rate applies to that home
    assert lower_detrazione_rate(None, main_home=True) is None
    assert lower_detrazione_rate(from_2027, main_home=True) is None
    assert lower_detrazione_rate(None, main_home=False) is None


def test_main_home_at_36_has_no_detrazione_axis():
    # Without a payment date a main home gets 36%; 30% never applies to it
    solar = _scenario(
        _evaluate(kwp=3.0, annual_kwh=2500.0, panel_cost_eur_per_kwp=1700.0),
        ScenarioType.SOLAR_ONLY,
    )
    cells = [solar.robustness] + [c.robustness for c in solar.sensitivity]
    assert all(f.input != "detrazione" for r in cells for f in r.depends_on)


def test_self_consumption_shift_moves_ten_points_of_production_to_the_grid():
    profile = AnnualEnergyProfile(
        production_kwh=8000.0,
        self_consumption_kwh=2400.0,
        grid_import_kwh=1600.0,
        grid_export_kwh=5600.0,
    )
    shifted = shift_self_consumption(profile, 0.10)
    assert shifted.production_kwh == 8000.0
    assert shifted.self_consumption_kwh == pytest.approx(1600.0)
    assert shifted.grid_import_kwh == pytest.approx(2400.0)
    assert shifted.grid_export_kwh == pytest.approx(6400.0)
    # Never below zero self-consumption
    small = AnnualEnergyProfile(
        production_kwh=8000.0,
        self_consumption_kwh=300.0,
        grid_import_kwh=3700.0,
        grid_export_kwh=7700.0,
    )
    assert shift_self_consumption(small, 0.10).self_consumption_kwh == 0.0


R, HE, AR, SQ, EC = PRICE_SCENARIOS


def _grid(default: str, **flips: str) -> dict:
    """Verdicts over (detrazione lower?, price scenario, self-consumption -10?)."""
    grid = {
        (d, p, s): default
        for d in (False, True)
        for p in PRICE_SCENARIOS
        for s in (False, True)
    }
    for key, verdict in flips.items():
        d, p, s = key.split("_")
        grid[(d == "low", PriceScenarioName(p), s == "low")] = verdict
    return grid


def _set(grid: dict, verdict: str, *, d=None, p=None, s=None) -> dict:
    for key in grid:
        if (
            (d is None or key[0] == d)
            and (p is None or key[1] == p)
            and (s is None or key[2] == s)
        ):
            grid[key] = verdict
    return grid


def test_robust_when_nothing_in_the_grid_flips_the_verdict():
    label = classify_robustness(
        _grid("positive"), base_price=R, lower_detrazione_percent=30
    )
    assert label.level == "robust"
    assert label.depends_on == []


def test_depends_on_detrazione_when_only_the_lower_rate_flips_it():
    grid = _set(_grid("positive"), "caution", d=True)
    label = classify_robustness(grid, base_price=R, lower_detrazione_percent=36)
    assert label.level == "depends"
    assert [f.model_dump(exclude_none=True) for f in label.depends_on] == [
        {"input": "detrazione", "detrazione_percent": 36}
    ]


def test_depends_on_the_price_scenario_that_flips_it():
    grid = _set(_grid("positive"), "caution", p=AR)
    label = classify_robustness(grid, base_price=R, lower_detrazione_percent=30)
    assert label.level == "depends"
    assert [f.model_dump(exclude_none=True) for f in label.depends_on] == [
        {"input": "price_scenario", "price_scenario": AR}
    ]


def test_fragile_when_two_inputs_each_flip_it_alone():
    grid = _set(_set(_grid("positive"), "caution", d=True), "caution", s=True)
    label = classify_robustness(grid, base_price=R, lower_detrazione_percent=36)
    assert label.level == "fragile"
    assert [f.input for f in label.depends_on] == ["detrazione", "self_consumption"]


def test_a_flip_only_from_a_combination_names_both_inputs():
    grid = _grid("positive")
    grid[(True, R, True)] = "caution"
    label = classify_robustness(grid, base_price=R, lower_detrazione_percent=36)
    assert label.level == "depends"
    assert [f.input for f in label.depends_on] == ["detrazione", "self_consumption"]
    assert label.depends_on[1].self_consumption_points == -10


def test_a_negative_verdict_that_turns_positive_also_counts():
    grid = _set(_grid("caution"), "positive", p=HE)
    label = classify_robustness(grid, base_price=R, lower_detrazione_percent=30)
    assert label.level == "depends"
    assert label.depends_on[0].price_scenario == HE


def test_caution_and_negative_are_the_same_side_of_the_decision():
    grid = _set(_grid("caution"), "negative", s=True)
    assert (
        classify_robustness(grid, base_price=R, lower_detrazione_percent=30).level
        == "robust"
    )


def test_robustness_is_relative_to_the_selected_price_scenario():
    # Positive only under the high-electrification trajectory
    grid = _set(_grid("caution"), "positive", p=HE)
    label = classify_robustness(grid, base_price=HE, lower_detrazione_percent=30)
    assert label.level == "depends"
    # Every other price scenario flips it: one input, named by the first that does
    assert label.depends_on[0].input == "price_scenario"


def test_no_lower_detrazione_rate_means_no_detrazione_axis():
    grid = {key: v for key, v in _grid("positive").items() if key[0] is False}
    assert (
        classify_robustness(grid, base_price=R, lower_detrazione_percent=None).level
        == "robust"
    )


def test_engine_label_names_the_lower_detrazione_rate_on_a_marginal_case():
    # Paid in 2026 at 50%: holds unless the rate drops to 36% and prices also fall
    solar = _scenario(
        _evaluate(
            kwp=3.0,
            annual_kwh=2500.0,
            payment_date=date(2026, 11, 30),
            panel_cost_eur_per_kwp=2000.0,
        ),
        ScenarioType.SOLAR_ONLY,
    )
    assert solar.verdict == "positive"
    assert solar.robustness.level == "depends"
    assert {"input": "detrazione", "detrazione_percent": 36} in [
        f.model_dump(exclude_none=True) for f in solar.robustness.depends_on
    ]


def test_engine_label_on_a_comfortable_case_is_robust():
    solar = _scenario(
        _evaluate(
            kwp=6.0,
            annual_kwh=4000.0,
            payment_date=date(2026, 11, 30),
            panel_cost_eur_per_kwp=1600.0,
        ),
        ScenarioType.SOLAR_ONLY,
    )
    assert (solar.verdict, solar.robustness.level) == ("positive", "robust")
