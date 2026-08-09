"""Scenario-assembly helpers — monthly detail builders, energy profiles.

Pure functions translating between PVGIS production / hourly-match results
and per-month ``MonthlyDetail`` records, plus serialization helpers.
"""

from __future__ import annotations

from ..models.hourly import HourlySelfConsumptionResult
from ..models.solar_eval import (
    ConsumptionProfile,
    MonthlyDetail,
    PVGISMonthly,
)
from ._constants import _MONTHLY_CONSUMPTION_SHARE
from .models import AnnualBreakdownEntry, AnnualEnergyProfile


def _annual_profile_from_monthly(details: list[MonthlyDetail]) -> AnnualEnergyProfile:
    return AnnualEnergyProfile(
        production_kwh=sum(d.production_kwh for d in details),
        self_consumption_kwh=sum(d.self_consumption_kwh for d in details),
        grid_import_kwh=sum(d.grid_import_kwh for d in details),
        grid_export_kwh=sum(d.grid_export_kwh for d in details),
    )


def _scaled_price_trajectories(
    projected_prices,
    *,
    base_retail_rate: float,
    base_wholesale_rate: float,
) -> tuple[list[float], list[float]]:
    base_total = projected_prices[0].total_eur_kwh or 1.0
    base_wholesale = projected_prices[0].wholesale_eur_kwh or 1.0
    retail_prices = [base_retail_rate]
    wholesale_prices = [base_wholesale_rate]

    for components in projected_prices[1:]:
        retail_prices.append(
            round(base_retail_rate * components.total_eur_kwh / base_total, 6),
        )
        wholesale_prices.append(
            round(base_wholesale_rate * components.wholesale_eur_kwh / base_wholesale, 6),
        )

    return retail_prices, wholesale_prices


def _monthly_from_hourly_match(
    match_result: HourlySelfConsumptionResult,
    all_in_rate: float,
    rid_price: float,
) -> list[MonthlyDetail]:
    return [
        MonthlyDetail(
            month=ms["month"],
            production_kwh=round(ms["production_kwh"], 1),
            self_consumption_kwh=round(ms["self_consumption_kwh"], 1),
            grid_import_kwh=round(ms["grid_import_kwh"], 1),
            grid_export_kwh=round(ms["grid_export_kwh"], 1),
            electricity_cost_eur=round(ms["grid_import_kwh"] * all_in_rate, 2),
            rid_revenue_eur=round(ms["grid_export_kwh"] * rid_price, 2),
        )
        for ms in match_result.monthly_summary
    ]


def _status_quo_monthly(
    consumption: ConsumptionProfile,
    all_in_rate: float,
) -> list[MonthlyDetail]:
    details = []
    for i, share in enumerate(_MONTHLY_CONSUMPTION_SHARE):
        month_kwh = consumption.annual_kwh * share
        details.append(
            MonthlyDetail(
                month=i + 1,
                production_kwh=0.0,
                self_consumption_kwh=0.0,
                grid_import_kwh=round(month_kwh, 1),
                grid_export_kwh=0.0,
                electricity_cost_eur=round(month_kwh * all_in_rate, 2),
            )
        )
    return details


def _solar_monthly(
    consumption: ConsumptionProfile,
    monthly_production: list[PVGISMonthly],
    all_in_rate: float,
    self_consumption_ratio: float,
    rid_price: float,
) -> list[MonthlyDetail]:
    details = []
    prod_by_month = {m.month: m.e_m for m in monthly_production}

    for i, share in enumerate(_MONTHLY_CONSUMPTION_SHARE):
        month = i + 1
        month_consumption = consumption.annual_kwh * share
        month_production = prod_by_month.get(month, 0.0)

        usable_production = min(month_production, month_consumption)
        self_consumed = usable_production * self_consumption_ratio
        excess = max(month_production - month_consumption, 0.0)
        not_self_consumed = usable_production - self_consumed
        total_export = excess + not_self_consumed
        grid_import = max(month_consumption - self_consumed, 0.0)

        details.append(
            MonthlyDetail(
                month=month,
                production_kwh=round(month_production, 1),
                self_consumption_kwh=round(self_consumed, 1),
                grid_import_kwh=round(grid_import, 1),
                grid_export_kwh=round(total_export, 1),
                electricity_cost_eur=round(grid_import * all_in_rate, 2),
                rid_revenue_eur=round(total_export * rid_price, 2),
            )
        )
    return details


def _breakdown_to_dicts(breakdown: list[AnnualBreakdownEntry]) -> list[dict]:
    """Convert breakdown dataclass entries to plain dicts for JSON serialization."""
    return [
        {
            "year": e.year,
            "energy_savings_eur": e.energy_savings_eur,
            "rid_revenue_eur": e.rid_revenue_eur,
            "detrazione_eur": e.detrazione_eur,
            "maintenance_eur": e.maintenance_eur,
            "replacement_eur": e.replacement_eur,
            "total_eur": e.total_eur,
        }
        for e in breakdown
    ]
