"""Italian residential load profile archetypes.

Based on RSE Besagni et al. (2020) 7-cluster centroids from Italian monitoring
campaign (MDPI Buildings), ARERA TIC standard profiles, and MICENE project data.

Each archetype is a normalized 8760-hour profile (sums to 1.0) generated from
parameterized daily shapes x seasonal factors x weekday/weekend split.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from ..models.contract import TariffBand
from ..tariff import classify_band


class LoadArchetype(BaseModel):
    """A representative Italian household load profile archetype."""

    id: str
    label: str
    description: str
    annual_kwh_range: tuple[float, float] = Field(
        description="Typical (min, max) annual consumption"
    )
    typical_potenza_kw: float = Field(description="Typical committed power (kW)")
    f1_fraction: float = Field(description="Expected F1 share (0-1)")
    f2_fraction: float = Field(description="Expected F2 share (0-1)")
    f3_fraction: float = Field(description="Expected F3 share (0-1)")
    normalized_profile: list[float] = Field(
        description="8760 values summing to 1.0"
    )


# Reference year for profile generation (non-leap, matching HourlyLoadProfile convention)
_REFERENCE_YEAR = 2025

# Days per month for non-leap year
_DAYS_PER_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _generate_profile(
    weekday_shape: list[float],
    weekend_shape: list[float],
    seasonal_factors: list[float],
    base_load: float,
) -> list[float]:
    """Generate a normalized 8760-hour profile from daily shapes and seasonal factors.

    Args:
        weekday_shape: 24 hourly weights for Mon-Fri
        weekend_shape: 24 hourly weights for Sat-Sun
        seasonal_factors: 12 monthly multipliers
        base_load: minimum hourly fraction (always-on appliances)

    Returns:
        8760 float values summing to 1.0
    """
    values: list[float] = []
    for month_idx in range(12):
        factor = seasonal_factors[month_idx]
        days = _DAYS_PER_MONTH[month_idx]
        for day in range(1, days + 1):
            dt = datetime(_REFERENCE_YEAR, month_idx + 1, day)
            weekday = dt.weekday()  # 0=Mon, 6=Sun
            shape = weekday_shape if weekday < 5 else weekend_shape
            for hour in range(24):
                values.append((base_load + shape[hour]) * factor)

    # Normalize to sum to 1.0
    total = sum(values)
    return [v / total for v in values]


def _compute_band_fractions(profile: list[float]) -> tuple[float, float, float]:
    """Compute F1/F2/F3 fractions from a normalized 8760 profile."""
    f1 = f2 = f3 = 0.0
    idx = 0
    for month_idx in range(12):
        month = month_idx + 1
        days = _DAYS_PER_MONTH[month_idx]
        for day in range(1, days + 1):
            for hour in range(24):
                dt = datetime(_REFERENCE_YEAR, month, day, hour)
                band = classify_band(dt)
                if band == TariffBand.F1:
                    f1 += profile[idx]
                elif band == TariffBand.F2:
                    f2 += profile[idx]
                else:
                    f3 += profile[idx]
                idx += 1
    return round(f1, 4), round(f2, 4), round(f3, 4)


# ---------------------------------------------------------------------------
# Archetype daily shapes (24-hour weekday and weekend patterns)
# Values are relative weights (not normalized — normalization happens globally)
# Based on RSE Besagni 2020 cluster centroids and ARERA TIC profiles
# ---------------------------------------------------------------------------

# 1. Single retiree: low baseload, gentle morning + evening peaks
_SINGLE_RETIREE_WD = [
    0.05, 0.04, 0.04, 0.04, 0.04, 0.05, 0.08, 0.15, 0.18, 0.16,
    0.14, 0.16, 0.18, 0.14, 0.12, 0.11, 0.12, 0.14, 0.18, 0.20,
    0.22, 0.18, 0.12, 0.07,
]
_SINGLE_RETIREE_WE = [
    0.05, 0.04, 0.04, 0.04, 0.04, 0.05, 0.07, 0.12, 0.16, 0.15,
    0.14, 0.16, 0.18, 0.14, 0.12, 0.11, 0.12, 0.14, 0.18, 0.20,
    0.22, 0.18, 0.12, 0.07,
]
_SINGLE_RETIREE_SEASONAL = [
    1.15, 1.10, 1.00, 0.90, 0.85, 0.85, 0.90, 0.90, 0.90, 1.00, 1.10, 1.15,
]

# 2. Couple, both working: very low daytime, strong evening peak
_COUPLE_WORKING_WD = [
    0.06, 0.05, 0.05, 0.05, 0.05, 0.06, 0.12, 0.10, 0.05, 0.04,
    0.04, 0.04, 0.05, 0.04, 0.04, 0.04, 0.05, 0.10, 0.22, 0.35,
    0.38, 0.30, 0.18, 0.10,
]
_COUPLE_WORKING_WE = [
    0.06, 0.05, 0.05, 0.05, 0.05, 0.05, 0.06, 0.10, 0.14, 0.16,
    0.15, 0.16, 0.18, 0.16, 0.14, 0.12, 0.12, 0.14, 0.20, 0.30,
    0.32, 0.26, 0.16, 0.08,
]
_COUPLE_WORKING_SEASONAL = [
    1.10, 1.05, 1.00, 0.92, 0.88, 0.90, 0.95, 0.92, 0.92, 1.00, 1.08, 1.12,
]

# 3. Family with children: highest evening peak, moderate lunch, kids home afternoon
_FAMILY_CHILDREN_WD = [
    0.08, 0.06, 0.06, 0.06, 0.06, 0.07, 0.14, 0.16, 0.10, 0.08,
    0.08, 0.10, 0.16, 0.14, 0.12, 0.14, 0.16, 0.20, 0.30, 0.42,
    0.45, 0.35, 0.20, 0.12,
]
_FAMILY_CHILDREN_WE = [
    0.08, 0.06, 0.06, 0.06, 0.06, 0.06, 0.08, 0.12, 0.16, 0.18,
    0.18, 0.20, 0.22, 0.18, 0.16, 0.16, 0.18, 0.20, 0.28, 0.38,
    0.40, 0.32, 0.20, 0.10,
]
_FAMILY_CHILDREN_SEASONAL = [
    1.12, 1.08, 1.00, 0.92, 0.88, 0.92, 0.98, 0.95, 0.92, 1.00, 1.08, 1.14,
]

# 4. Work-from-home: elevated daytime plateau, flatter profile
_WFH_WD = [
    0.06, 0.05, 0.05, 0.05, 0.05, 0.06, 0.10, 0.14, 0.18, 0.20,
    0.20, 0.18, 0.20, 0.18, 0.18, 0.18, 0.18, 0.16, 0.20, 0.25,
    0.24, 0.20, 0.14, 0.08,
]
_WFH_WE = [
    0.06, 0.05, 0.05, 0.05, 0.05, 0.06, 0.08, 0.12, 0.15, 0.16,
    0.16, 0.17, 0.18, 0.16, 0.14, 0.14, 0.14, 0.16, 0.20, 0.24,
    0.24, 0.20, 0.14, 0.08,
]
_WFH_SEASONAL = [
    1.10, 1.05, 1.00, 0.92, 0.88, 0.90, 0.95, 0.92, 0.92, 1.00, 1.08, 1.12,
]

# 5. Heat pump household: winter-heavy, heating cycles throughout day
_HEAT_PUMP_WD = [
    0.10, 0.08, 0.08, 0.08, 0.08, 0.10, 0.16, 0.20, 0.18, 0.16,
    0.14, 0.14, 0.16, 0.14, 0.14, 0.14, 0.16, 0.18, 0.24, 0.28,
    0.26, 0.22, 0.16, 0.12,
]
_HEAT_PUMP_WE = [
    0.10, 0.08, 0.08, 0.08, 0.08, 0.10, 0.14, 0.18, 0.18, 0.17,
    0.16, 0.16, 0.18, 0.16, 0.15, 0.15, 0.16, 0.18, 0.22, 0.26,
    0.24, 0.20, 0.16, 0.12,
]
_HEAT_PUMP_SEASONAL = [
    1.80, 1.65, 1.30, 0.80, 0.55, 0.50, 0.50, 0.50, 0.55, 0.85, 1.40, 1.80,
]

# 6. AC-heavy household: summer peaks, afternoon cooling load
_AC_HEAVY_WD = [
    0.06, 0.05, 0.05, 0.05, 0.05, 0.06, 0.10, 0.12, 0.10, 0.10,
    0.10, 0.12, 0.14, 0.16, 0.18, 0.18, 0.16, 0.14, 0.20, 0.28,
    0.30, 0.24, 0.16, 0.08,
]
_AC_HEAVY_WE = [
    0.06, 0.05, 0.05, 0.05, 0.05, 0.06, 0.08, 0.12, 0.14, 0.14,
    0.14, 0.16, 0.18, 0.20, 0.22, 0.22, 0.20, 0.18, 0.22, 0.28,
    0.28, 0.22, 0.14, 0.08,
]
_AC_HEAVY_SEASONAL = [
    0.80, 0.78, 0.82, 0.88, 1.00, 1.40, 1.70, 1.65, 1.20, 0.88, 0.78, 0.80,
]

# 7. Energy-poor household: very low flat base, minimal peaks
_ENERGY_POOR_WD = [
    0.04, 0.03, 0.03, 0.03, 0.03, 0.04, 0.06, 0.08, 0.06, 0.05,
    0.05, 0.06, 0.08, 0.06, 0.05, 0.05, 0.06, 0.08, 0.12, 0.14,
    0.14, 0.10, 0.06, 0.04,
]
_ENERGY_POOR_WE = [
    0.04, 0.03, 0.03, 0.03, 0.03, 0.04, 0.05, 0.07, 0.08, 0.08,
    0.07, 0.08, 0.10, 0.08, 0.06, 0.06, 0.06, 0.08, 0.12, 0.14,
    0.14, 0.10, 0.06, 0.04,
]
_ENERGY_POOR_SEASONAL = [
    1.05, 1.02, 1.00, 0.98, 0.96, 0.96, 0.98, 0.98, 0.97, 1.00, 1.03, 1.06,
]


# ---------------------------------------------------------------------------
# Build archetype definitions
# ---------------------------------------------------------------------------

_ARCHETYPE_CONFIGS: list[dict] = [
    {
        "id": "single_retiree",
        "label": "Single retiree",
        "description": "Low baseload (~110W), gentle morning and evening peaks, minimal weekday/weekend difference",
        "annual_kwh_range": (1200.0, 1800.0),
        "typical_potenza_kw": 3.0,
        "weekday_shape": _SINGLE_RETIREE_WD,
        "weekend_shape": _SINGLE_RETIREE_WE,
        "seasonal_factors": _SINGLE_RETIREE_SEASONAL,
        "base_load": 0.04,
    },
    {
        "id": "couple_working",
        "label": "Couple, both working",
        "description": "Very low daytime consumption (at work), strong 19-22 evening peak (~400W)",
        "annual_kwh_range": (2000.0, 2800.0),
        "typical_potenza_kw": 3.0,
        "weekday_shape": _COUPLE_WORKING_WD,
        "weekend_shape": _COUPLE_WORKING_WE,
        "seasonal_factors": _COUPLE_WORKING_SEASONAL,
        "base_load": 0.04,
    },
    {
        "id": "family_children",
        "label": "Family with children",
        "description": "Highest evening peak (~600W), moderate lunch peak, children home in afternoon",
        "annual_kwh_range": (2800.0, 4000.0),
        "typical_potenza_kw": 4.5,
        "weekday_shape": _FAMILY_CHILDREN_WD,
        "weekend_shape": _FAMILY_CHILDREN_WE,
        "seasonal_factors": _FAMILY_CHILDREN_SEASONAL,
        "base_load": 0.05,
    },
    {
        "id": "work_from_home",
        "label": "Work-from-home",
        "description": "Elevated 9-18 daytime plateau, flatter profile, less evening peak contrast",
        "annual_kwh_range": (2500.0, 3500.0),
        "typical_potenza_kw": 3.0,
        "weekday_shape": _WFH_WD,
        "weekend_shape": _WFH_WE,
        "seasonal_factors": _WFH_SEASONAL,
        "base_load": 0.04,
    },
    {
        "id": "heat_pump",
        "label": "Heat pump household",
        "description": "Strong winter seasonal factor (2x), moderate base, heating cycles throughout day",
        "annual_kwh_range": (4000.0, 6500.0),
        "typical_potenza_kw": 6.0,
        "weekday_shape": _HEAT_PUMP_WD,
        "weekend_shape": _HEAT_PUMP_WE,
        "seasonal_factors": _HEAT_PUMP_SEASONAL,
        "base_load": 0.06,
    },
    {
        "id": "ac_heavy",
        "label": "AC-heavy household",
        "description": "Strong summer seasonal factor (1.8x), afternoon peaks 13-17, southern Italy bias",
        "annual_kwh_range": (3000.0, 5000.0),
        "typical_potenza_kw": 4.5,
        "weekday_shape": _AC_HEAVY_WD,
        "weekend_shape": _AC_HEAVY_WE,
        "seasonal_factors": _AC_HEAVY_SEASONAL,
        "base_load": 0.04,
    },
    {
        "id": "energy_poor",
        "label": "Energy-poor household",
        "description": "Very low flat baseload (~50W), minimal peaks, nearly no seasonal variation",
        "annual_kwh_range": (800.0, 1200.0),
        "typical_potenza_kw": 1.5,
        "weekday_shape": _ENERGY_POOR_WD,
        "weekend_shape": _ENERGY_POOR_WE,
        "seasonal_factors": _ENERGY_POOR_SEASONAL,
        "base_load": 0.03,
    },
]


def _build_archetypes() -> list[LoadArchetype]:
    """Build all archetype objects with generated profiles and computed band fractions."""
    archetypes = []
    for cfg in _ARCHETYPE_CONFIGS:
        profile = _generate_profile(
            weekday_shape=cfg["weekday_shape"],
            weekend_shape=cfg["weekend_shape"],
            seasonal_factors=cfg["seasonal_factors"],
            base_load=cfg["base_load"],
        )
        f1, f2, f3 = _compute_band_fractions(profile)
        archetypes.append(
            LoadArchetype(
                id=cfg["id"],
                label=cfg["label"],
                description=cfg["description"],
                annual_kwh_range=cfg["annual_kwh_range"],
                typical_potenza_kw=cfg["typical_potenza_kw"],
                f1_fraction=f1,
                f2_fraction=f2,
                f3_fraction=f3,
                normalized_profile=profile,
            )
        )
    return archetypes


# Module-level singleton — computed once at import time
ARCHETYPES: list[LoadArchetype] = _build_archetypes()

# Index by ID for fast lookup
_ARCHETYPES_BY_ID: dict[str, LoadArchetype] = {a.id: a for a in ARCHETYPES}


def get_archetype(archetype_id: str) -> LoadArchetype | None:
    """Get an archetype by its ID. Returns None if not found."""
    return _ARCHETYPES_BY_ID.get(archetype_id)


def list_archetypes() -> list[LoadArchetype]:
    """Return all available archetypes."""
    return ARCHETYPES
