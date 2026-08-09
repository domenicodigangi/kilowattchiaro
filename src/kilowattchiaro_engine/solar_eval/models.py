"""Internal dataclasses used across the solar_eval submodules.

These are NOT public API — they're aggregated values passed between
the cash-flow projection, scenario assembly, and orchestrator. Public
result types live in ``app.models.solar_eval``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnnualEnergyProfile:
    production_kwh: float
    self_consumption_kwh: float
    grid_import_kwh: float
    grid_export_kwh: float


@dataclass(frozen=True)
class AnnualBreakdownEntry:
    year: int
    energy_savings_eur: float
    rid_revenue_eur: float
    detrazione_eur: float
    maintenance_eur: float
    replacement_eur: float
    total_eur: float


@dataclass(frozen=True)
class AnnualCashFlowProjection:
    annual_cash_flows: list[float]
    annual_import_costs: list[float]
    annual_export_revenues: list[float]
    annual_breakdown: list[AnnualBreakdownEntry]
