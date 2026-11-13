"""Regional + size-band PV/battery cost benchmarks (2026 Italy), read from the assumptions register."""

from __future__ import annotations

from ..assumptions import value as _assumption

# PV-only benchmarks: (floor, central, ceiling) EUR/kWp by macro-region x size band.
# The one source is the assumptions register (app/assumptions.yaml, ``pv_cost_bands``).
_PV_REGISTER = _assumption("pv_cost_bands")
_PV_BENCHMARKS: dict[tuple[str, str], tuple[float, float, float]] = {
    (region, size): tuple(band)
    for region, sizes in _PV_REGISTER["bands_eur_per_kwp"].items()
    for size, band in sizes.items()
}
# Band used when no location is known: one of the regional bands, never a separate one.
NATIONAL_REGION: str = _PV_REGISTER["national_region"]

# Battery benchmarks: (floor, central, ceiling) EUR/kWh (register ``battery_cost_bands``)
_BATTERY_BENCHMARKS: dict[str, tuple[float, float, float]] = {
    size: tuple(band) for size, band in _assumption("battery_cost_bands").items()
}

# Region-free band for a typical 3-6 kWp system: the national band, not its own numbers.
MARKET_PANEL_FLOOR, MARKET_PANEL_CENTRAL, MARKET_PANEL_CEILING = _PV_BENCHMARKS[
    (NATIONAL_REGION, "medium")
]


def _get_macro_region(lat: float | None, lon: float | None) -> str | None:
    """Classify Italian coordinates into macro-regions."""
    if lat is None or lon is None:
        return None
    # Sicily: lat < 39 and lon > 12.5 (roughly)
    # Sardinia: lat 38.8-41.3 and lon 8-10
    if (lat < 39.0 and lon > 12.5) or (38.8 <= lat <= 41.3 and 8.0 <= lon <= 10.0):
        return "isole"
    if lat >= 44.0:
        return "nord"
    if lat >= 42.0:
        return "centro"
    return "sud"


def _get_size_band(kwp: float) -> str:
    """Classify system size into bands."""
    if kwp <= 3.0:
        return "small"
    if kwp <= 6.0:
        return "medium"
    if kwp <= 10.0:
        return "large"
    return "xlarge"


def _get_battery_size_band(kwh: float) -> str:
    if kwh < 5.0:
        return "small"
    if kwh <= 10.0:
        return "medium"
    return "large"


def _get_benchmarks(
    region: str | None, size_band: str,
) -> tuple[float, float, float]:
    """Return (floor, central, ceiling) for PV cost/kWp; the national band without a region."""
    key = (region, size_band)
    return _PV_BENCHMARKS[key if key in _PV_BENCHMARKS else (NATIONAL_REGION, size_band)]
