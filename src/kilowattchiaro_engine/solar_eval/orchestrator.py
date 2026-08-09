"""Top-level evaluate_solar orchestrator.

The function body is structured as 8 linear phases (see the Mermaid
diagram in ``__init__.py`` for the visual graph). Phase markers below
keep the long but linear pipeline scannable:

    Phase 1 — Resolve costs (panel/battery cost, investments, detrazione)
    Phase 2 — Compute self-consumption (hourly path; monthly fallback)
    Phase 3 — Build status-quo + price context
    Phase 4 — Build solar cash-flow projection
    Phase 5 — Assemble status-quo + solar ScenarioResults
    Phase 6 — Battery branch: projection + ScenarioResult (conditional)
    Phase 7 — Assemble assumptions dict
    Phase 8 — Return SolarEvalResult

Phases 5 and 6 dispatch to ``_build_scenario_result`` so the NPV / IRR /
payback assembly is written once and reused for both scenarios.
"""

from __future__ import annotations

import logging

from ..config import settings
from ..pvgis import tmy_to_hourly_kw
from ..models.hourly import HourlyProductionProfile
from ..models.solar_eval import (
    ConsumptionProfile,
    MonthlyDetail,
    PVGISMonthly,
    PVGISTMYResult,
    PriceScenarioName,
    ScenarioResult,
    ScenarioType,
    SolarEvalResult,
)
from ..hourly_match import match_hourly
from ..load_profiles import infer_hourly_load
from ..price_projection import get_price_projection_scenario
from ._constants import _ANALYSIS_HORIZON_YEARS, _DEFAULT_ALL_IN_RATE_EUR_KWH
from .cash_flow import (
    _build_annual_cash_flow_projection,
    _cumulative_cash_flow,
)
from .models import AnnualCashFlowProjection
from .npv import _estimate_irr, _npv_20yr, _npv_sensitivity
from .payback import _payback_years
from .scenario import (
    _annual_profile_from_monthly,
    _breakdown_to_dicts,
    _monthly_from_hourly_match,
    _scaled_price_trajectories,
    _solar_monthly,
    _status_quo_monthly,
)

logger = logging.getLogger(__name__)


def _build_scenario_result(
    *,
    scenario: ScenarioType,
    label: str,
    investment: float,
    projection: AnnualCashFlowProjection,
    sq_annual_cost: float,
    detrazione_annual: float,
    detrazione_years: int,
    inflation: float,
    discount_rate: float,
    monthly_details: list[MonthlyDetail],
    sc_pct: float,
) -> ScenarioResult:
    annual_cost = round(projection.annual_import_costs[0], 2)
    rid_annual = round(projection.annual_export_revenues[0], 2)
    annual_savings = round(projection.annual_cash_flows[0], 2)
    payback = _payback_years(
        investment,
        sq_annual_cost,
        annual_cost,
        rid_annual,
        detrazione_annual,
        detrazione_years,
        inflation=inflation,
        annual_cash_flows=projection.annual_cash_flows,
    )
    npv = _npv_20yr(
        investment,
        sq_annual_cost,
        annual_cost,
        rid_annual,
        detrazione_annual,
        detrazione_years,
        discount_rate,
        inflation=inflation,
        annual_cash_flows=projection.annual_cash_flows,
    )
    irr = _estimate_irr(
        investment,
        sq_annual_cost,
        annual_cost,
        rid_annual,
        detrazione_annual,
        detrazione_years,
        inflation=inflation,
        annual_cash_flows=projection.annual_cash_flows,
    )
    return ScenarioResult(
        scenario=scenario,
        label=label,
        investment_cost_eur=investment,
        annual_electricity_cost_eur=annual_cost,
        annual_rid_revenue_eur=rid_annual,
        annual_detrazione_eur=detrazione_annual,
        annual_savings_eur=annual_savings,
        payback_years=payback,
        npv_20yr_eur=npv,
        irr_percent=irr,
        npv_sensitivity=_npv_sensitivity(investment, projection.annual_cash_flows),
        cumulative_cash_flow=_cumulative_cash_flow(investment, projection.annual_cash_flows),
        annual_breakdown=_breakdown_to_dicts(projection.annual_breakdown),
        self_consumption_percent=sc_pct,
        monthly_details=monthly_details,
    )


def evaluate_solar(
    consumption: ConsumptionProfile,
    monthly_production: list[PVGISMonthly],
    desired_kwp: float,
    include_battery: bool = False,
    battery_kwh: float = 10.0,
    panel_cost_eur_per_kwp: float | None = None,
    battery_cost_eur: float | None = None,
    battery_cost_eur_per_kwh: float | None = None,
    discount_rate: float = 0.03,
    energy_price_inflation: float = 0.02,
    price_scenario: PriceScenarioName = PriceScenarioName.REFERENCE,
    tmy_data: PVGISTMYResult | None = None,
) -> SolarEvalResult:
    """Run financial evaluation for status quo, solar, and solar+battery."""
    if consumption.annual_kwh <= 0:
        raise ValueError("annual_kwh must be positive for evaluation")

    # === Phase 1 — Resolve costs (panel, battery, investments, detrazione) ===
    panel_cost = panel_cost_eur_per_kwp if panel_cost_eur_per_kwp is not None else settings.default_panel_cost_eur_per_kwp
    if battery_cost_eur_per_kwh is not None:
        bat_cost = round(battery_cost_eur_per_kwh * battery_kwh, 2)
    elif battery_cost_eur is not None:
        bat_cost = battery_cost_eur
    else:
        bat_cost = settings.default_battery_cost_eur
    rid_floor = settings.default_rid_price_eur_kwh
    maintenance_eur_yr = settings.default_maintenance_eur_yr
    inverter_replacement_eur = settings.default_inverter_replacement_eur
    inverter_replacement_year = settings.default_inverter_replacement_year
    battery_replacement_year = settings.default_battery_replacement_year
    panel_degradation_pct = settings.default_panel_degradation_pct
    battery_fade_pct = settings.default_battery_fade_pct

    if consumption.annual_cost_eur and consumption.annual_kwh > 0:
        all_in_rate = consumption.annual_cost_eur / consumption.annual_kwh
    else:
        all_in_rate = _DEFAULT_ALL_IN_RATE_EUR_KWH

    annual_production = sum(m.e_m for m in monthly_production)
    solar_investment = round(desired_kwp * panel_cost, 2)
    battery_investment = round(bat_cost, 2) if include_battery else 0.0

    solar_detrazione_total = min(solar_investment * 0.50, 48_000.0)
    solar_detrazione_annual = round(solar_detrazione_total / 10, 2)
    battery_detrazione_total = min(
        (solar_investment + battery_investment) * 0.50, 48_000.0,
    )
    battery_detrazione_annual = round(battery_detrazione_total / 10, 2)

    # === Phase 2 — Compute self-consumption (hourly path; monthly fallback) ===
    use_hourly = tmy_data is not None
    hourly_match_bat = None
    if use_hourly:
        try:
            hourly_prod_values = tmy_to_hourly_kw(tmy_data, desired_kwp)
            hourly_load = infer_hourly_load(
                consumption,
                potenza_impegnata=consumption.potenza_impegnata_kw,
            )
            hourly_prod = HourlyProductionProfile(
                values=hourly_prod_values,
                lat=0,
                lon=0,
                kwp=desired_kwp,
                tilt=0,
                azimuth=0,
            )
            hourly_match_solar = match_hourly(hourly_load, hourly_prod, battery_kwh=0)
            sc_ratio = hourly_match_solar.self_consumption_ratio
            solar_monthly = _monthly_from_hourly_match(
                hourly_match_solar,
                all_in_rate,
                rid_floor,
            )
            solar_sc_pct = round(
                hourly_match_solar.annual_self_consumption_kwh
                / consumption.annual_kwh
                * 100,
                1,
            ) if consumption.annual_kwh > 0 else 0.0

            if include_battery:
                hourly_match_bat = match_hourly(
                    hourly_load,
                    hourly_prod,
                    battery_kwh=battery_kwh,
                )
                sc_ratio_bat = hourly_match_bat.self_consumption_ratio
            else:
                sc_ratio_bat = settings.default_self_consumption_ratio_battery
        except Exception:
            logger.warning(
                "Hourly matching failed, falling back to monthly path",
                exc_info=True,
            )
            use_hourly = False

    if not use_hourly:
        sc_ratio = settings.default_self_consumption_ratio
        sc_ratio_bat = settings.default_self_consumption_ratio_battery
        solar_monthly = _solar_monthly(
            consumption,
            monthly_production,
            all_in_rate,
            sc_ratio,
            rid_floor,
        )
        solar_sc_pct = round(
            sc_ratio * min(annual_production, consumption.annual_kwh)
            / consumption.annual_kwh
            * 100,
            1,
        ) if consumption.annual_kwh > 0 else 0.0

    # === Phase 3 — Build status-quo + price context ===
    sq_monthly = _status_quo_monthly(consumption, all_in_rate)
    sq_annual_cost = round(
        consumption.annual_cost_eur or consumption.annual_kwh * all_in_rate,
        2,
    )
    status_quo_profile = _annual_profile_from_monthly(sq_monthly)
    solar_profile = _annual_profile_from_monthly(solar_monthly)

    price_projection_scenario = get_price_projection_scenario(
        scenario_name=price_scenario,
        years=_ANALYSIS_HORIZON_YEARS,
        cpi=energy_price_inflation,
    )
    projected_prices = price_projection_scenario.project()
    projected_retail_prices, projected_wholesale_prices = _scaled_price_trajectories(
        projected_prices,
        base_retail_rate=all_in_rate,
        base_wholesale_rate=rid_floor,
    )

    # === Phase 4 — Build solar cash-flow projection ===
    solar_projection = _build_annual_cash_flow_projection(
        status_quo_profile=status_quo_profile,
        solar_profile=solar_profile,
        projected_retail_prices=projected_retail_prices,
        projected_wholesale_prices=projected_wholesale_prices,
        annual_detrazione=solar_detrazione_annual,
        detrazione_years=10,
        annual_maintenance_eur=maintenance_eur_yr,
        inverter_replacement_eur=inverter_replacement_eur,
        inverter_replacement_year=inverter_replacement_year,
        rid_floor_price_eur_kwh=rid_floor,
        panel_degradation_pct=panel_degradation_pct,
    )

    # === Phase 5 — Assemble status-quo + solar ScenarioResults ===
    status_quo = ScenarioResult(
        scenario=ScenarioType.STATUS_QUO,
        label="Nessun impianto (situazione attuale)",
        annual_electricity_cost_eur=sq_annual_cost,
        monthly_details=sq_monthly,
    )

    solar_only = _build_scenario_result(
        scenario=ScenarioType.SOLAR_ONLY,
        label=f"Fotovoltaico {desired_kwp} kWp",
        investment=solar_investment,
        projection=solar_projection,
        sq_annual_cost=sq_annual_cost,
        detrazione_annual=solar_detrazione_annual,
        detrazione_years=10,
        inflation=energy_price_inflation,
        discount_rate=discount_rate,
        monthly_details=solar_monthly,
        sc_pct=solar_sc_pct,
    )

    scenarios = [status_quo, solar_only]

    # === Phase 6 — Battery branch: projection + ScenarioResult (conditional) ===
    if include_battery:
        total_investment = solar_investment + battery_investment
        if use_hourly and hourly_match_bat is not None:
            bat_monthly = _monthly_from_hourly_match(
                hourly_match_bat,
                all_in_rate,
                rid_floor,
            )
            bat_sc_pct = round(
                hourly_match_bat.annual_self_consumption_kwh
                / consumption.annual_kwh
                * 100,
                1,
            ) if consumption.annual_kwh > 0 else 0.0
        else:
            bat_monthly = _solar_monthly(
                consumption,
                monthly_production,
                all_in_rate,
                sc_ratio_bat,
                rid_floor,
            )
            bat_sc_pct = round(
                sc_ratio_bat * min(annual_production, consumption.annual_kwh)
                / consumption.annual_kwh
                * 100,
                1,
            ) if consumption.annual_kwh > 0 else 0.0

        battery_profile = _annual_profile_from_monthly(bat_monthly)
        battery_projection = _build_annual_cash_flow_projection(
            status_quo_profile=status_quo_profile,
            solar_profile=solar_profile,
            projected_retail_prices=projected_retail_prices,
            projected_wholesale_prices=projected_wholesale_prices,
            annual_detrazione=battery_detrazione_annual,
            detrazione_years=10,
            annual_maintenance_eur=maintenance_eur_yr,
            inverter_replacement_eur=inverter_replacement_eur,
            inverter_replacement_year=inverter_replacement_year,
            rid_floor_price_eur_kwh=rid_floor,
            panel_degradation_pct=panel_degradation_pct,
            battery_profile=battery_profile,
            battery_fade_pct=battery_fade_pct,
            battery_replacement_eur=bat_cost,
            battery_replacement_year=battery_replacement_year,
        )

        solar_battery = _build_scenario_result(
            scenario=ScenarioType.SOLAR_BATTERY,
            label=f"Fotovoltaico {desired_kwp} kWp + batteria {battery_kwh} kWh",
            investment=total_investment,
            projection=battery_projection,
            sq_annual_cost=sq_annual_cost,
            detrazione_annual=battery_detrazione_annual,
            detrazione_years=10,
            inflation=energy_price_inflation,
            discount_rate=discount_rate,
            monthly_details=bat_monthly,
            sc_pct=bat_sc_pct,
        )
        scenarios.append(solar_battery)

    # === Phase 7 — Assemble assumptions dict ===
    assumptions = {
        "panel_cost_eur_per_kwp": panel_cost,
        "battery_cost_eur": bat_cost if include_battery else None,
        "rid_price_eur_kwh": rid_floor,
        "self_consumption_ratio": sc_ratio,
        "self_consumption_ratio_battery": sc_ratio_bat if include_battery else None,
        "all_in_electricity_rate_eur_kwh": round(all_in_rate, 4),
        "discount_rate": discount_rate,
        "energy_price_inflation": energy_price_inflation,
        "price_scenario_used": price_scenario.value,
        "detrazione_percent": 50,
        "detrazione_years": 10,
        "analysis_horizon_years": _ANALYSIS_HORIZON_YEARS,
        "panel_degradation_pct": panel_degradation_pct,
        "battery_fade_pct": battery_fade_pct if include_battery else None,
        "maintenance_eur_yr": maintenance_eur_yr,
        "inverter_replacement_eur": inverter_replacement_eur,
        "inverter_replacement_year": inverter_replacement_year,
        "battery_replacement_year": battery_replacement_year if include_battery else None,
        "self_consumption_source": "hourly_match" if use_hourly else "default_ratio",
    }

    # === Phase 8 — Return SolarEvalResult ===
    return SolarEvalResult(
        scenarios=scenarios,
        annual_production_kwh=round(annual_production, 1),
        system_kwp=desired_kwp,
        location="",
        assumptions={k: v for k, v in assumptions.items() if v is not None},
    )
