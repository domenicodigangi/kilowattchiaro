"""Cash-flow projection — degradation factors + 20-year projection + cumulative.

Stateless. Inputs are fully parameterised so callers can swap retail price
trajectories, panel-degradation curves, replacement schedules.
"""

from __future__ import annotations

from ._constants import _ANALYSIS_HORIZON_YEARS
from .models import AnnualBreakdownEntry, AnnualCashFlowProjection, AnnualEnergyProfile


def _panel_factor(year: int, panel_degradation_pct: float) -> float:
    return (1 - panel_degradation_pct) ** max(0, year - 1)


def _battery_factor(
    year: int,
    battery_fade_pct: float,
    battery_replacement_year: int | None,
) -> float:
    if battery_replacement_year is None or year <= battery_replacement_year:
        age = year
    else:
        age = year - battery_replacement_year
    return (1 - battery_fade_pct) ** max(0, age - 1)


def _build_annual_cash_flow_projection(
    *,
    status_quo_profile: AnnualEnergyProfile,
    solar_profile: AnnualEnergyProfile,
    projected_retail_prices: list[float],
    projected_wholesale_prices: list[float],
    annual_detrazione: float,
    detrazione_years: int,
    annual_maintenance_eur: float,
    inverter_replacement_eur: float,
    inverter_replacement_year: int,
    rid_floor_price_eur_kwh: float,
    panel_degradation_pct: float,
    battery_profile: AnnualEnergyProfile | None = None,
    battery_fade_pct: float = 0.0,
    battery_replacement_eur: float = 0.0,
    battery_replacement_year: int | None = None,
) -> AnnualCashFlowProjection:
    annual_cash_flows: list[float] = []
    annual_import_costs: list[float] = []
    annual_export_revenues: list[float] = []
    annual_breakdown: list[AnnualBreakdownEntry] = []

    solar_self_consumption = solar_profile.self_consumption_kwh
    solar_export = solar_profile.grid_export_kwh
    battery_self_consumption_uplift = 0.0
    battery_export_reduction = 0.0
    if battery_profile is not None:
        battery_self_consumption_uplift = max(
            0.0,
            battery_profile.self_consumption_kwh - solar_profile.self_consumption_kwh,
        )
        battery_export_reduction = max(
            0.0,
            solar_profile.grid_export_kwh - battery_profile.grid_export_kwh,
        )

    # Spread replacement costs across 3 years centered on the replacement year
    inverter_spread_start = inverter_replacement_year - 1
    inverter_spread_end = inverter_replacement_year + 1
    inverter_annual_cost = inverter_replacement_eur / 3

    battery_spread_start = (battery_replacement_year - 1) if battery_replacement_year else 0
    battery_spread_end = (battery_replacement_year + 1) if battery_replacement_year else 0
    battery_annual_cost = battery_replacement_eur / 3 if battery_replacement_year else 0.0

    for year in range(1, _ANALYSIS_HORIZON_YEARS + 1):
        panel_factor = _panel_factor(year, panel_degradation_pct)
        self_consumption_kwh = solar_self_consumption * panel_factor
        export_kwh = solar_export * panel_factor

        if battery_profile is not None:
            battery_factor = _battery_factor(
                year,
                battery_fade_pct,
                battery_replacement_year,
            )
            self_consumption_kwh += (
                battery_self_consumption_uplift * panel_factor * battery_factor
            )
            export_kwh = max(
                0.0,
                export_kwh - battery_export_reduction * panel_factor * battery_factor,
            )

        grid_import_kwh = max(
            0.0,
            status_quo_profile.grid_import_kwh - self_consumption_kwh,
        )
        retail_price = projected_retail_prices[year]
        rid_price = max(rid_floor_price_eur_kwh, projected_wholesale_prices[year])
        grid_cost = retail_price * grid_import_kwh
        rid_revenue = rid_price * export_kwh
        status_quo_cost = retail_price * status_quo_profile.grid_import_kwh

        energy_savings = status_quo_cost - grid_cost
        detrazione_yr = annual_detrazione if year <= detrazione_years else 0.0
        replacement = 0.0
        if inverter_spread_start <= year <= inverter_spread_end:
            replacement += inverter_annual_cost
        if (
            battery_profile is not None
            and battery_replacement_year is not None
            and battery_spread_start <= year <= battery_spread_end
        ):
            replacement += battery_annual_cost

        cash_flow = energy_savings + rid_revenue - annual_maintenance_eur + detrazione_yr - replacement

        annual_cash_flows.append(round(cash_flow, 2))
        annual_import_costs.append(round(grid_cost, 2))
        annual_export_revenues.append(round(rid_revenue, 2))
        annual_breakdown.append(AnnualBreakdownEntry(
            year=year,
            energy_savings_eur=round(energy_savings, 2),
            rid_revenue_eur=round(rid_revenue, 2),
            detrazione_eur=round(detrazione_yr, 2),
            maintenance_eur=round(-annual_maintenance_eur, 2),
            replacement_eur=round(-replacement, 2),
            total_eur=round(cash_flow, 2),
        ))

    return AnnualCashFlowProjection(
        annual_cash_flows=annual_cash_flows,
        annual_import_costs=annual_import_costs,
        annual_export_revenues=annual_export_revenues,
        annual_breakdown=annual_breakdown,
    )


def _cumulative_cash_flow(
    investment: float,
    annual_cash_flows: list[float],
) -> list[float]:
    """Build cumulative cash flow list for years 0-20 (year 0 = -investment)."""
    result = [-investment]
    cumulative = -investment
    for cf in annual_cash_flows[:20]:
        cumulative += cf
        result.append(round(cumulative, 2))
    return result


def _legacy_cash_flows(
    annual_cost_sq: float,
    annual_cost_solar: float,
    annual_rid: float,
    annual_detrazione: float,
    detrazione_years: int,
    inflation: float,
    *,
    years: int,
) -> list[float]:
    """Pre-projection cash-flow shape kept for backward-compat in payback / NPV / IRR
    when the caller has not threaded a full ``annual_cash_flows`` list through."""
    cash_flows: list[float] = []
    for year in range(1, years + 1):
        inflated_sq = annual_cost_sq * (1 + inflation) ** year
        cash_flow = inflated_sq - annual_cost_solar + annual_rid
        if year <= detrazione_years:
            cash_flow += annual_detrazione
        cash_flows.append(round(cash_flow, 2))
    return cash_flows
