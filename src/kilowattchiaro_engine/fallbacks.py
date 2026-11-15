"""Fallback codes: which path and which defaults a /solar evaluation used (ticket 316).

Every evaluation response carries ``fallback_codes`` (sorted, from the closed
vocabulary below) and ``full_fidelity``. The server also counts them anonymously
per day (``app.storage.anon_fallback_counts``); the first reliability KPI is the
share of evaluations on the full-fidelity path.

Two kinds of code:

- **Path codes** mean the engine did not run its full model: the default 30/70
  self-consumption ratio instead of the hourly match, or the 30 deg south
  placeholder instead of a known roof orientation. Any of them makes
  ``full_fidelity`` false.
- **Input defaults** mean a value the household did not give was assumed. They
  are reported (the results page labels them, ticket 313) but leave the path at
  full fidelity.

Add a code here, in ``FallbackCode`` (``app/models/solar_eval.py``) and in
``docs/methodology.md`` together; a test checks the first two agree.
"""

from __future__ import annotations

from collections.abc import Iterable

# --- Path codes -----------------------------------------------------------------
# PVGIS TMY unavailable (fetch failed, or no irradiance components)
SC_RATIO_NO_TMY = "sc_ratio_no_tmy"
# TMY present but the hourly match raised
SC_RATIO_HOURLY_FAILED = "sc_ratio_hourly_failed"
# No roof, or a roof without a detected or entered orientation: 30 deg south
ROOF_DEFAULT_SOUTH30 = "roof_default_south30"

# --- Input defaults ---------------------------------------------------------------
# No annual bill cost: the rate comes from the ARERA reference bill
TARIFF_ALL_IN_DEFAULT = "tariff_all_in_default"
# F1/F2/F3 split unknown: the hourly load uses the average household shape
BAND_SPLIT_DEFAULT = "band_split_default"
# Annual kWh is a typical value (preset or average), not the household's own
CONSUMPTION_TYPICAL = "consumption_typical"
# No payment date: the detrazione rule for payments from 2027 is assumed
DETRAZIONE_DATE_ASSUMED = "detrazione_date_assumed"
# No own price: the investment is the regional market band
COST_MARKET_BAND = "cost_market_band"
# Google Solar imagery below HIGH quality behind the roof segments
IMAGERY_NOT_HIGH = "imagery_not_high"
# PVGIS gave no year-to-year SD, so no production range is shown
PRODUCTION_RANGE_UNAVAILABLE = "production_range_unavailable"

PATH_CODES = frozenset({SC_RATIO_NO_TMY, SC_RATIO_HOURLY_FAILED, ROOF_DEFAULT_SOUTH30})
INPUT_CODES = frozenset(
    {
        TARIFF_ALL_IN_DEFAULT,
        BAND_SPLIT_DEFAULT,
        CONSUMPTION_TYPICAL,
        DETRAZIONE_DATE_ASSUMED,
        COST_MARKET_BAND,
        IMAGERY_NOT_HIGH,
        PRODUCTION_RANGE_UNAVAILABLE,
    }
)
ALL_CODES = PATH_CODES | INPUT_CODES


def finalise(codes: Iterable[str]) -> tuple[list[str], bool]:
    """Sorted unique codes and whether the evaluation stayed on the full-fidelity path."""
    unique = set(codes)
    unknown = unique - ALL_CODES
    if unknown:
        raise ValueError(f"Unknown fallback codes: {sorted(unknown)}")
    return sorted(unique), not (unique & PATH_CODES)
