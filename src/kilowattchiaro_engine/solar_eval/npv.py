"""NPV + IRR computations.

Pure-math layer over the annual cash-flow series produced in ``cash_flow``.
"""

from __future__ import annotations

from ._constants import _ANALYSIS_HORIZON_YEARS, _NPV_SENSITIVITY_RATES
from .cash_flow import _legacy_cash_flows


def _npv_sensitivity(
    investment: float,
    annual_cash_flows: list[float],
) -> dict[str, float]:
    """Compute NPV at multiple discount rates for sensitivity display."""
    result = {}
    for rate in _NPV_SENSITIVITY_RATES:
        npv = -investment
        for year, cf in enumerate(annual_cash_flows[:_ANALYSIS_HORIZON_YEARS], start=1):
            npv += cf / ((1 + rate) ** year)
        result[f"{int(rate * 100)}%"] = round(npv, 2)
    return result


def _npv_20yr(
    investment: float,
    annual_cost_sq: float,
    annual_cost_solar: float,
    annual_rid: float,
    annual_detrazione: float,
    detrazione_years: int,
    discount_rate: float,
    inflation: float = 0.0,
    annual_cash_flows: list[float] | None = None,
) -> float:
    cash_flows = annual_cash_flows or _legacy_cash_flows(
        annual_cost_sq,
        annual_cost_solar,
        annual_rid,
        annual_detrazione,
        detrazione_years,
        inflation,
        years=_ANALYSIS_HORIZON_YEARS,
    )
    npv = -investment
    for year, cash_flow in enumerate(cash_flows[:_ANALYSIS_HORIZON_YEARS], start=1):
        npv += cash_flow / ((1 + discount_rate) ** year)
    return round(npv, 2)


def _estimate_irr(
    investment: float,
    annual_cost_sq: float,
    annual_cost_solar: float,
    annual_rid: float,
    annual_detrazione: float,
    detrazione_years: int,
    years: int = _ANALYSIS_HORIZON_YEARS,
    inflation: float = 0.0,
    annual_cash_flows: list[float] | None = None,
) -> float | None:
    if investment <= 0:
        return None

    cash_flows = annual_cash_flows or _legacy_cash_flows(
        annual_cost_sq,
        annual_cost_solar,
        annual_rid,
        annual_detrazione,
        detrazione_years,
        inflation,
        years=years,
    )

    def npv_at_rate(rate: float) -> float:
        npv = -investment
        for year, cash_flow in enumerate(cash_flows[:years], start=1):
            npv += cash_flow / ((1 + rate) ** year)
        return npv

    low, high = -0.10, 1.0
    if npv_at_rate(high) > 0:
        return round(high * 100, 1)
    if npv_at_rate(low) < 0:
        return None

    for _ in range(100):
        mid = (low + high) / 2
        if npv_at_rate(mid) > 0:
            low = mid
        else:
            high = mid
        if abs(high - low) < 0.0001:
            break

    return round(((low + high) / 2) * 100, 1)
