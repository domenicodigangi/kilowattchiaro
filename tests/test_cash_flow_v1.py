"""Cash-flow corrections in engine release v1: battery replacement, maintenance, export price."""

from __future__ import annotations

from datetime import date

import pytest

from kilowattchiaro_engine.solar_eval import _build_annual_cash_flow_projection, evaluate_solar
from kilowattchiaro_engine.solar_eval.cash_flow import (
    BATTERY_CELL_PACK_SHARE,
    BATTERY_LIFE_YEARS,
)
from kilowattchiaro_engine.solar_eval.orchestrator import EXPORT_IRPEF_RATE, SOLAR_CAPTURE_FACTOR
from kilowattchiaro_engine.solar_eval.models import AnnualEnergyProfile
from kilowattchiaro_engine.models.price_components import ITALIAN_DEFAULTS_2025
from kilowattchiaro_engine.models.solar_eval import ConsumptionProfile, PVGISMonthly, ScenarioType

_PROFILE = AnnualEnergyProfile(
    production_kwh=8000, self_consumption_kwh=2000, grid_import_kwh=2000, grid_export_kwh=6000
)
_SQ = AnnualEnergyProfile(production_kwh=0, self_consumption_kwh=0, grid_import_kwh=4000, grid_export_kwh=0)
_BATTERY = AnnualEnergyProfile(
    production_kwh=8000, self_consumption_kwh=3200, grid_import_kwh=800, grid_export_kwh=4600
)


def _projection(**overrides):
    kwargs = dict(
        status_quo_profile=_SQ,
        solar_profile=_PROFILE,
        projected_retail_prices=[0.25] * 21,
        projected_wholesale_prices=[0.08] * 21,
        annual_detrazione=0.0,
        detrazione_years=10,
        annual_maintenance_eur=75.0,
        inverter_replacement_eur=0.0,
        inverter_replacement_year=11,
        rid_floor_price_eur_kwh=0.0475,
        panel_degradation_pct=0.0,
        battery_profile=_BATTERY,
        battery_fade_pct=0.0,
        battery_replacement_eur=4400.0,
        battery_replacement_year=14,
    )
    kwargs.update(overrides)
    return _build_annual_cash_flow_projection(**kwargs)


class TestBatteryReplacement:
    def test_cell_pack_share_and_life(self):
        assert BATTERY_CELL_PACK_SHARE == pytest.approx(0.55)
        assert BATTERY_LIFE_YEARS == 15

    def test_replacement_is_spread_over_years_13_to_15(self):
        breakdown = _projection().annual_breakdown
        for entry in breakdown:
            if entry.year in (13, 14, 15):
                assert entry.replacement_eur == pytest.approx(-4400.0 / 3, abs=0.01)

    def test_year_20_credits_the_unused_life_of_the_replacement(self):
        # Installed in year 14, used 6 of 15 years by the end of year 20: 9/15 left.
        year_20 = _projection().annual_breakdown[19]
        assert year_20.replacement_eur == pytest.approx(4400.0 * 9 / 15, abs=0.01)

    def test_evaluate_solar_charges_the_cell_pack_not_the_turnkey_price(self):
        consumption = ConsumptionProfile(annual_kwh=4000, f1_kwh=1600, f2_kwh=1200, f3_kwh=1200)
        production = [PVGISMonthly(month=m, e_m=650.0, h_m=4.0) for m in range(1, 13)]
        result = evaluate_solar(
            consumption,
            production,
            desired_kwp=6.0,
            include_battery=True,
            battery_cost_eur=8000.0,
            payment_date=date(2026, 12, 1),
        )
        battery = next(s for s in result.scenarios if s.scenario == ScenarioType.SOLAR_BATTERY)
        solar = next(s for s in result.scenarios if s.scenario == ScenarioType.SOLAR_ONLY)
        battery_only = [
            b["replacement_eur"] - s["replacement_eur"]
            for b, s in zip(battery.annual_breakdown, solar.annual_breakdown)
        ]
        assert sum(battery_only[12:15]) == pytest.approx(-8000.0 * 0.55, abs=0.05)
        assert battery_only[19] == pytest.approx(8000.0 * 0.55 * 9 / 15, abs=0.05)
        assert result.assumptions["battery_replacement_eur"] == pytest.approx(4400.0)


class TestMaintenanceEscalation:
    def test_maintenance_grows_with_cpi(self):
        breakdown = _projection(cost_inflation=0.02).annual_breakdown
        assert breakdown[0].maintenance_eur == pytest.approx(-75.0)
        assert breakdown[19].maintenance_eur == pytest.approx(-75.0 * 1.02**19, abs=0.01)


class TestExportPrice:
    def test_constants(self):
        assert SOLAR_CAPTURE_FACTOR == pytest.approx(0.72)
        assert EXPORT_IRPEF_RATE == pytest.approx(0.23)

    def test_export_is_valued_at_the_capture_price_after_tax_not_at_the_floor(self):
        consumption = ConsumptionProfile(annual_kwh=4000, f1_kwh=1600, f2_kwh=1200, f3_kwh=1200)
        production = [PVGISMonthly(month=m, e_m=650.0, h_m=4.0) for m in range(1, 13)]
        result = evaluate_solar(consumption, production, desired_kwp=6.0, payment_date=date(2026, 12, 1))
        expected = ITALIAN_DEFAULTS_2025.wholesale_eur_kwh * 0.72 * (1 - 0.23)
        assert result.assumptions["rid_price_eur_kwh"] == pytest.approx(expected, abs=1e-4)
        assert result.assumptions["rid_price_eur_kwh"] > 0.0475

        solar = next(s for s in result.scenarios if s.scenario == ScenarioType.SOLAR_ONLY)
        export_kwh = sum(m.grid_export_kwh for m in solar.monthly_details)
        year_1_price = solar.annual_breakdown[0]["rid_revenue_eur"] / export_kwh
        assert year_1_price > expected  # year 1 is one wholesale escalation step above year 0
        assert year_1_price < expected * 1.05
