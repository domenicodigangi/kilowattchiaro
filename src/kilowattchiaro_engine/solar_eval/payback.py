"""Payback-period computation."""

from __future__ import annotations

from ._constants import _PAYBACK_HORIZON_YEARS
from .cash_flow import _legacy_cash_flows


def _payback_years(
    investment: float,
    annual_cost_sq: float,
    annual_cost_solar: float,
    annual_rid: float,
    annual_detrazione: float,
    detrazione_years: int,
    inflation: float = 0.0,
    annual_cash_flows: list[float] | None = None,
) -> float | None:
    if investment <= 0:
        return 0.0

    cash_flows = annual_cash_flows or _legacy_cash_flows(
        annual_cost_sq,
        annual_cost_solar,
        annual_rid,
        annual_detrazione,
        detrazione_years,
        inflation,
        years=_PAYBACK_HORIZON_YEARS,
    )

    cumulative = 0.0
    for year, cash_flow in enumerate(cash_flows, start=1):
        cumulative += cash_flow
        if cumulative >= investment:
            overshoot = cumulative - investment
            fraction = 1.0 - (overshoot / cash_flow) if cash_flow > 0 else 0.0
            return round(year - 1 + fraction, 1)
    return None
