"""Infer hourly load profile from bill-level F1/F2/F3 consumption data.

Maps bill-level consumption (annual or per-band kWh) to a full 8760-hour
profile by matching against RSE household archetypes and scaling per-band.
"""

import logging
from datetime import datetime
from enum import Enum

from ..models.contract import TariffBand
from ..models.hourly import HourlyLoadProfile
from ..models.solar_eval import ConsumptionProfile
from ..tariff import classify_band
from .archetypes import ARCHETYPES, LoadArchetype, _DAYS_PER_MONTH, _REFERENCE_YEAR

logger = logging.getLogger(__name__)

# Named constants for tuning parameters
_RANGE_PENALTY_FACTOR = 0.1
_ANNUAL_ONLY_RANGE_WEIGHT = 10.0
_EXTREME_SCALE_THRESHOLD = 3.0
_PEAK_CAP_HEADROOM = 1.1
_PEAK_CAP_WARNING_RATIO = 0.10


class _InputMode(Enum):
    TRIORARIA = "trioraria"
    BIORARIA = "bioraria"
    ANNUAL_ONLY = "annual_only"


def _detect_input_mode(consumption: ConsumptionProfile) -> _InputMode:
    """Detect which band data is available in the bill."""
    if consumption.f1_kwh > 0 and consumption.f2_kwh > 0 and consumption.f3_kwh > 0:
        return _InputMode.TRIORARIA
    if consumption.f1_kwh > 0 and consumption.f2_kwh == 0 and consumption.f3_kwh > 0:
        return _InputMode.BIORARIA
    return _InputMode.ANNUAL_ONLY


def _build_band_map() -> list[TariffBand]:
    """Precompute tariff band for each of the 8760 hours in the reference year."""
    bands: list[TariffBand] = []
    for month_idx in range(12):
        month = month_idx + 1
        days = _DAYS_PER_MONTH[month_idx]
        for day in range(1, days + 1):
            for hour in range(24):
                dt = datetime(_REFERENCE_YEAR, month, day, hour)
                bands.append(classify_band(dt))
    return bands


# Module-level cache — computed once at import time
_BAND_MAP: list[TariffBand] = _build_band_map()


def _chi_squared(
    bill_ratios: tuple[float, ...],
    arch_ratios: tuple[float, ...],
) -> float:
    """Chi-squared distance between bill and archetype band ratios."""
    return sum(
        (b - a) ** 2 / (a + 1e-9)
        for b, a in zip(bill_ratios, arch_ratios)
    )


def _range_penalty(annual_kwh: float, arch: LoadArchetype) -> float:
    """Penalty for annual_kwh falling outside archetype's typical range."""
    low, high = arch.annual_kwh_range
    if annual_kwh < low:
        distance = (low - annual_kwh) / (high - low)
    elif annual_kwh > high:
        distance = (annual_kwh - high) / (high - low)
    else:
        return 0.0
    return distance ** 2 * _RANGE_PENALTY_FACTOR


def _select_archetype_trioraria(
    f1_kwh: float, f2_kwh: float, f3_kwh: float, annual_kwh: float,
) -> LoadArchetype:
    """Select best-fit archetype using 3-way chi-squared on F1/F2/F3 ratios."""
    if not ARCHETYPES:
        raise ValueError("No archetypes defined — cannot select archetype")
    total = f1_kwh + f2_kwh + f3_kwh
    bill_ratios = (f1_kwh / total, f2_kwh / total, f3_kwh / total)

    best_score = float("inf")
    best_arch: LoadArchetype | None = None
    for arch in ARCHETYPES:
        arch_ratios = (arch.f1_fraction, arch.f2_fraction, arch.f3_fraction)
        score = _chi_squared(bill_ratios, arch_ratios) + _range_penalty(annual_kwh, arch)
        if score < best_score:
            best_score = score
            best_arch = arch
    assert best_arch is not None
    return best_arch


def _select_archetype_bioraria(
    f1_kwh: float, f23_kwh: float, annual_kwh: float,
) -> LoadArchetype:
    """Select best-fit archetype using 2-way chi-squared on F1/F23 ratios."""
    if not ARCHETYPES:
        raise ValueError("No archetypes defined — cannot select archetype")
    total = f1_kwh + f23_kwh
    bill_ratios = (f1_kwh / total, f23_kwh / total)

    best_score = float("inf")
    best_arch: LoadArchetype | None = None
    for arch in ARCHETYPES:
        arch_ratios = (arch.f1_fraction, arch.f2_fraction + arch.f3_fraction)
        score = _chi_squared(bill_ratios, arch_ratios) + _range_penalty(annual_kwh, arch)
        if score < best_score:
            best_score = score
            best_arch = arch
    assert best_arch is not None
    return best_arch


def _select_archetype_annual_only(
    annual_kwh: float, potenza_impegnata: float,
) -> LoadArchetype:
    """Select archetype by potenza_impegnata proximity + annual_kwh range overlap."""
    if not ARCHETYPES:
        raise ValueError("No archetypes defined — cannot select archetype")
    best_score = float("inf")
    best_arch: LoadArchetype | None = None
    for arch in ARCHETYPES:
        potenza_dist = abs(arch.typical_potenza_kw - potenza_impegnata)
        low, high = arch.annual_kwh_range
        if annual_kwh < low:
            range_dist = (low - annual_kwh) / annual_kwh
        elif annual_kwh > high:
            range_dist = (annual_kwh - high) / annual_kwh
        else:
            range_dist = 0.0
        score = potenza_dist + range_dist * _ANNUAL_ONLY_RANGE_WEIGHT
        if score < best_score:
            best_score = score
            best_arch = arch
    assert best_arch is not None
    return best_arch


def infer_hourly_load(
    consumption: ConsumptionProfile,
    potenza_impegnata: float = 3.0,
) -> HourlyLoadProfile:
    """Infer a full 8760-hour load profile from bill-level consumption data.

    Algorithm:
    1. Detect input mode (trioraria/bioraria/annual-only)
    2. Select best-fit archetype by chi-squared on band ratios
    3. Scale archetype profile to match bill's actual kWh
    4. Per-band scaling to match F1/F2/F3 totals exactly
    5. Cap peaks at potenza_impegnata * 1.1
    """
    mode = _detect_input_mode(consumption)

    # Step 1: Select archetype and determine total kWh
    if mode == _InputMode.TRIORARIA:
        total_kwh = consumption.f1_kwh + consumption.f2_kwh + consumption.f3_kwh
        arch = _select_archetype_trioraria(
            consumption.f1_kwh, consumption.f2_kwh, consumption.f3_kwh,
            consumption.annual_kwh,
        )
    elif mode == _InputMode.BIORARIA:
        total_kwh = consumption.f1_kwh + consumption.f3_kwh  # f3_kwh holds F23
        arch = _select_archetype_bioraria(
            consumption.f1_kwh, consumption.f3_kwh, consumption.annual_kwh,
        )
    else:
        total_kwh = consumption.annual_kwh
        arch = _select_archetype_annual_only(consumption.annual_kwh, potenza_impegnata)

    logger.info("Selected archetype '%s' for mode=%s", arch.id, mode.value)

    # Step 2: Scale profile to match total kWh
    raw_values = [v * total_kwh for v in arch.normalized_profile]

    # Step 3: Per-band scaling (trioraria and bioraria only)
    if mode == _InputMode.TRIORARIA:
        raw_sums = {TariffBand.F1: 0.0, TariffBand.F2: 0.0, TariffBand.F3: 0.0}
        for v, b in zip(raw_values, _BAND_MAP):
            raw_sums[b] += v

        scale = {
            TariffBand.F1: consumption.f1_kwh / raw_sums[TariffBand.F1] if raw_sums[TariffBand.F1] > 0 else 1.0,
            TariffBand.F2: consumption.f2_kwh / raw_sums[TariffBand.F2] if raw_sums[TariffBand.F2] > 0 else 1.0,
            TariffBand.F3: consumption.f3_kwh / raw_sums[TariffBand.F3] if raw_sums[TariffBand.F3] > 0 else 1.0,
        }
        values = [v * scale[b] for v, b in zip(raw_values, _BAND_MAP)]

        for s in scale.values():
            if s > _EXTREME_SCALE_THRESHOLD:
                logger.warning("Extreme per-band scale factor: %.2f", s)

    elif mode == _InputMode.BIORARIA:
        raw_f1 = 0.0
        raw_f23 = 0.0
        for v, b in zip(raw_values, _BAND_MAP):
            if b == TariffBand.F1:
                raw_f1 += v
            else:
                raw_f23 += v

        scale_f1 = consumption.f1_kwh / raw_f1 if raw_f1 > 0 else 1.0
        scale_f23 = consumption.f3_kwh / raw_f23 if raw_f23 > 0 else 1.0

        values = [
            v * (scale_f1 if b == TariffBand.F1 else scale_f23)
            for v, b in zip(raw_values, _BAND_MAP)
        ]

        for s in (scale_f1, scale_f23):
            if s > _EXTREME_SCALE_THRESHOLD:
                logger.warning("Extreme per-band scale factor: %.2f", s)
    else:
        values = raw_values

    # Step 4: Cap peaks at potenza_impegnata * headroom
    cap = potenza_impegnata * _PEAK_CAP_HEADROOM
    capped_energy = 0.0
    for i, v in enumerate(values):
        if v > cap:
            capped_energy += v - cap
            values[i] = cap

    if capped_energy > total_kwh * _PEAK_CAP_WARNING_RATIO:
        logger.warning(
            "Peak capping removed %.1f kWh (%.1f%% of total) — "
            "potenza_impegnata=%.1f kW may be too low",
            capped_energy, capped_energy / total_kwh * 100, potenza_impegnata,
        )

    return HourlyLoadProfile(
        values=values,
        source="bill_inference",
        profile_type="residential",
        potenza_impegnata_kw=potenza_impegnata,
        year=_REFERENCE_YEAR,
        selected_archetype=arch.id,
    )
