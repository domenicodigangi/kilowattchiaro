"""Module-private constants shared across the solar_eval submodules."""

from __future__ import annotations

_MONTHLY_CONSUMPTION_SHARE = [
    0.100,
    0.090,
    0.085,
    0.075,
    0.070,
    0.065,
    0.065,
    0.065,
    0.075,
    0.080,
    0.095,
    0.135,
]

_DEFAULT_ALL_IN_RATE_EUR_KWH = 0.25
_ANALYSIS_HORIZON_YEARS = 20
_PAYBACK_HORIZON_YEARS = 25
_NPV_SENSITIVITY_RATES = [0.03, 0.05, 0.08]
