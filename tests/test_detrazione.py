"""Detrazione (art. 16-bis TUIR) by payment date and residence — engine release v1."""

from datetime import date

import pytest

from kilowattchiaro_engine.detrazione import (
    DETRAZIONE_SPEND_CAP_EUR,
    detrazione_rate,
    detrazione_total_eur,
)
from kilowattchiaro_engine.solar_eval import evaluate_solar
from kilowattchiaro_engine.models.solar_eval import ConsumptionProfile, PVGISMonthly, ScenarioType

_PAID_2026 = date(2026, 12, 31)
_PAID_2027 = date(2027, 1, 1)


@pytest.mark.parametrize(
    ("payment_date", "main_home", "expected"),
    [
        (_PAID_2026, True, 0.50),
        (_PAID_2026, False, 0.36),
        (_PAID_2027, True, 0.36),
        (_PAID_2027, False, 0.30),
    ],
)
def test_four_cases_give_50_36_36_30(payment_date, main_home, expected):
    assert detrazione_rate(payment_date, main_home=main_home) == expected


def test_total_is_capped_on_eligible_spend_not_on_the_deduction():
    # 96,000 EUR is a cap on spend; at 36% the deduction tops out at 34,560 EUR.
    assert DETRAZIONE_SPEND_CAP_EUR == 96_000.0
    assert detrazione_total_eur(200_000.0, 0.36) == pytest.approx(34_560.0)
    assert detrazione_total_eur(10_000.0, 0.50) == pytest.approx(5_000.0)


def _evaluate(payment_date: date | None, main_home: bool):
    consumption = ConsumptionProfile(annual_kwh=4000, f1_kwh=1600, f2_kwh=1200, f3_kwh=1200)
    production = [PVGISMonthly(month=m, e_m=650.0, h_m=4.0) for m in range(1, 13)]
    return evaluate_solar(
        consumption,
        production,
        desired_kwp=6.0,
        include_battery=True,
        panel_cost_eur_per_kwp=1500.0,
        battery_cost_eur=6000.0,
        payment_date=payment_date,
        main_home=main_home,
    )


@pytest.mark.parametrize(
    ("payment_date", "main_home", "expected_pct"),
    [
        (_PAID_2026, True, 50),
        (_PAID_2026, False, 36),
        (_PAID_2027, True, 36),
        (_PAID_2027, False, 30),
    ],
)
def test_evaluate_solar_applies_the_rule_for_payment_date_and_residence(
    payment_date, main_home, expected_pct
):
    result = _evaluate(payment_date, main_home)
    by_type = {s.scenario: s for s in result.scenarios}

    assert result.assumptions["detrazione_percent"] == expected_pct
    assert result.assumptions["main_home"] is main_home
    assert by_type[ScenarioType.SOLAR_ONLY].annual_detrazione_eur == pytest.approx(
        9_000.0 * expected_pct / 100 / 10
    )
    assert by_type[ScenarioType.SOLAR_BATTERY].annual_detrazione_eur == pytest.approx(
        15_000.0 * expected_pct / 100 / 10
    )


def test_a_missing_payment_date_is_a_2027_payment():
    assert detrazione_rate(None, main_home=True) == 0.36
    assert detrazione_rate(None, main_home=False) == 0.30


@pytest.mark.parametrize(("main_home", "expected_pct"), [(True, 36), (False, 30)])
def test_evaluation_without_payment_date_applies_the_2027_rule(main_home, expected_pct):
    result = _evaluate(None, main_home)
    by_type = {s.scenario: s for s in result.scenarios}

    assert result.assumptions["detrazione_percent"] == expected_pct
    assert by_type[ScenarioType.SOLAR_ONLY].annual_detrazione_eur == pytest.approx(
        9_000.0 * expected_pct / 100 / 10
    )
