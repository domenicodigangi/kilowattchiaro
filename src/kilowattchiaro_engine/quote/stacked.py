"""Stacked PV + battery benchmark for bundled and retrofit quotes.

A PV+battery quote with no PV/battery split is judged against the existing PV band
(region x size, EUR/kWp) times the quoted kWp plus the existing battery band (EUR/kWh)
times the quoted kWh. A battery-only retrofit is judged on EUR/kWh against the battery
band alone. No benchmark constant lives here: both bands come from ``benchmarks``.
"""

from __future__ import annotations

from typing import Literal

from ..models.installer_quote import InstallerQuote
from .benchmarks import (
    _BATTERY_BENCHMARKS,
    _get_battery_size_band,
    _get_benchmarks,
    _get_size_band,
)

BenchmarkBasis = Literal["pv_only", "pv_battery_stacked", "battery_only"]

PV_ONLY: BenchmarkBasis = "pv_only"
PV_BATTERY_STACKED: BenchmarkBasis = "pv_battery_stacked"
BATTERY_ONLY: BenchmarkBasis = "battery_only"

Band = tuple[float, float, float]


def pv_band(region: str | None, kwp: float) -> Band:
    """(floor, central, ceiling) EUR/kWp for a PV system of this size in this region."""
    return _get_benchmarks(region, _get_size_band(kwp))


def battery_band(kwh: float) -> Band:
    """(floor, central, ceiling) EUR/kWh for a battery of this size."""
    return _BATTERY_BENCHMARKS[_get_battery_size_band(kwh)]


def stacked_bundle_band(region: str | None, kwp: float, kwh: float) -> Band:
    """(floor, central, ceiling) total EUR for ``kwp`` of PV plus ``kwh`` of battery."""
    pv = pv_band(region, kwp)
    bat = battery_band(kwh)
    return tuple(round(p * kwp + b * kwh) for p, b in zip(pv, bat))  # type: ignore[return-value]


def investment_range(region: str | None, kwp: float, kwh: float | None) -> Band:
    """(floor, central, ceiling) total EUR for a PV system, with a battery when ``kwh`` is set."""
    if kwh:
        return stacked_bundle_band(region, kwp, kwh)
    return tuple(round(p * kwp) for p in pv_band(region, kwp))  # type: ignore[return-value]


def _has_battery(quote: InstallerQuote) -> bool:
    return quote.battery_kwh is not None and quote.battery_kwh > 0


def _has_panel_evidence(quote: InstallerQuote) -> bool:
    return bool(
        quote.num_panels or quote.panel_watt_peak or quote.panel_brand or quote.panel_model
    )


def benchmark_basis(quote: InstallerQuote) -> BenchmarkBasis:
    """Which benchmark can fairly judge this quote's total.

    ``pv_battery_stacked``: kWp, kWh and a total, no explicit PV EUR/kWp.
    ``battery_only``: a battery and a total, no kWp and no sign of panels (a retrofit).
    ``pv_only``: everything else (the legacy path; the caller may still need to withhold).
    """
    if not (_has_battery(quote) and quote.total_cost_eur and quote.cost_per_kwp_eur is None):
        return PV_ONLY
    if quote.system_kwp and quote.system_kwp > 0:
        return PV_BATTERY_STACKED
    if not _has_panel_evidence(quote):
        return BATTERY_ONLY
    return PV_ONLY
