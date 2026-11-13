"""Avoided kWh are valued at the variable tariff; fixed charges are not avoided (engine v1)."""

from __future__ import annotations

from datetime import date

import pytest

from kilowattchiaro_engine.solar_eval import evaluate_solar
from kilowattchiaro_engine.solar_eval.tariff import (
    ARERA_TYPICAL_ANNUAL_KWH,
    ARERA_TYPICAL_ANNUAL_SPEND_EUR,
    DEFAULT_VARIABLE_RATE_EUR_KWH,
    FIXED_CHARGES_EUR_YR,
    variable_rate_eur_kwh,
)
from kilowattchiaro_engine.models.solar_eval import ConsumptionProfile, PVGISMonthly, ScenarioType

_PRODUCTION = [PVGISMonthly(month=m, e_m=650.0, h_m=4.0) for m in range(1, 13)]


def _evaluate(annual_cost_eur: float | None):
    consumption = ConsumptionProfile(
        annual_kwh=4000, f1_kwh=1600, f2_kwh=1200, f3_kwh=1200, annual_cost_eur=annual_cost_eur,
    )
    return evaluate_solar(consumption, _PRODUCTION, desired_kwp=6.0, payment_date=date(2026, 12, 1))


def _solar(result):
    return next(s for s in result.scenarios if s.scenario == ScenarioType.SOLAR_ONLY)


def test_defaults_come_from_arera_2026():
    # ARERA, 29 Sep 2026: vulnerable typical customer (2,000 kWh, 3 kW, resident), EUR 665.38 for 2026
    assert ARERA_TYPICAL_ANNUAL_SPEND_EUR == pytest.approx(665.38)
    assert ARERA_TYPICAL_ANNUAL_KWH == 2000
    assert DEFAULT_VARIABLE_RATE_EUR_KWH == pytest.approx((665.38 - FIXED_CHARGES_EUR_YR) / 2000)


def test_variable_rate_removes_fixed_charges_from_a_bill():
    assert variable_rate_eur_kwh(1200.0, 4000.0) == pytest.approx((1200.0 - FIXED_CHARGES_EUR_YR) / 4000)
    assert variable_rate_eur_kwh(None, 4000.0) == DEFAULT_VARIABLE_RATE_EUR_KWH


def test_avoided_kwh_value_excludes_fixed_charges():
    low, high = _evaluate(1200.0), _evaluate(1300.0)
    savings_low = _solar(low).annual_breakdown[0]["energy_savings_eur"]
    savings_high = _solar(high).annual_breakdown[0]["energy_savings_eur"]
    # Savings scale with the variable part of the bill, not with the whole bill
    expected_ratio = (1300.0 - FIXED_CHARGES_EUR_YR) / (1200.0 - FIXED_CHARGES_EUR_YR)
    assert savings_high / savings_low == pytest.approx(expected_ratio, rel=1e-3)
    assert low.assumptions["variable_electricity_rate_eur_kwh"] == pytest.approx(
        (1200.0 - FIXED_CHARGES_EUR_YR) / 4000, abs=1e-4
    )
    assert low.assumptions["fixed_charges_eur_yr"] == FIXED_CHARGES_EUR_YR


def test_without_a_bill_the_status_quo_is_arera_variable_plus_fixed():
    result = _evaluate(None)
    status_quo = next(s for s in result.scenarios if s.scenario == ScenarioType.STATUS_QUO)
    expected = 4000 * DEFAULT_VARIABLE_RATE_EUR_KWH + FIXED_CHARGES_EUR_YR
    assert status_quo.annual_electricity_cost_eur == pytest.approx(expected, abs=0.01)
    assert sum(m.electricity_cost_eur for m in status_quo.monthly_details) == pytest.approx(expected, abs=0.5)


def test_solar_households_still_pay_the_fixed_charges():
    solar = _solar(_evaluate(1200.0))
    assert solar.annual_electricity_cost_eur > FIXED_CHARGES_EUR_YR
    assert sum(m.electricity_cost_eur for m in solar.monthly_details) > FIXED_CHARGES_EUR_YR
