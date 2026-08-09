"""Estimate household consumption from lifestyle questions.

Maps household characteristics to one of 7 RSE load-profile archetypes,
then scales the archetype's typical consumption range by occupant count
and appliance modifiers.  No LLM required — pure deterministic logic.
"""

from __future__ import annotations

from .config import settings
from .models.solar_eval import (
    ConsumptionProfile,
    DaytimeOccupancy,
    HouseholdEstimationRequest,
    HouseholdEstimationResult,
    WaterHeaterType,
)
from .load_profiles.archetypes import ARCHETYPES, LoadArchetype

# Extra annual kWh for specific appliances (literature-calibrated estimates)
_RESISTANCE_WATER_HEATER_KWH = 1500  # Traditional scaldabagno elettrico
_HEAT_PUMP_WATER_HEATER_KWH = 700  # Scaldacqua a pompa di calore
_ELECTRIC_COOKING_KWH = 300

# Stepped per-occupant deltas (Besagni & Borgarello 2018; Eurostat economies of scale)
_OCCUPANT_DELTAS = [0.15, 0.10, 0.08]  # 3rd, 4th, 5th person


def _occupant_factor(occupants: int) -> float:
    """Diminishing-returns scaling relative to a 2-person baseline.

    1 person  → 0.85  (−15%)
    2 persons → 1.00  (base)
    3 persons → 1.15  (+15%)
    4 persons → 1.25  (+10%)
    5+ persons → +8% each additional
    """
    if occupants <= 0:
        return 0.85
    if occupants <= 2:
        # Below baseline: symmetric first delta
        return 1.0 - _OCCUPANT_DELTAS[0] * (2 - occupants)
    factor = 1.0
    for i in range(occupants - 2):
        delta = _OCCUPANT_DELTAS[min(i, len(_OCCUPANT_DELTAS) - 1)]
        factor += delta
    return factor


# All fields we estimate (always the full set)
_ALL_ESTIMATED_FIELDS = [
    "annual_kwh",
    "f1_kwh",
    "f2_kwh",
    "f3_kwh",
    "annual_cost_eur",
    "power_capacity_kw",
]


def _score_archetype(arch: LoadArchetype, req: HouseholdEstimationRequest) -> float:
    """Score how well an archetype matches the household characteristics.

    Higher score = better match.  Scores are relative, not absolute.
    """
    score = 0.0

    # --- Heating / cooling appliances (strongest signal) ---
    if req.has_heat_pump:
        score += 10.0 if arch.id == "heat_pump" else -3.0
    else:
        if arch.id == "heat_pump":
            score -= 8.0

    if req.has_ac and not req.has_heat_pump:
        score += 6.0 if arch.id == "ac_heavy" else 0.0

    # --- Daytime occupancy ---
    if req.daytime_occupancy == DaytimeOccupancy.FULL:
        if arch.id in ("work_from_home", "single_retiree"):
            score += 4.0
        elif arch.id == "couple_working":
            score -= 3.0
    elif req.daytime_occupancy == DaytimeOccupancy.NONE:
        if arch.id == "couple_working":
            score += 4.0
        elif arch.id in ("work_from_home", "single_retiree"):
            score -= 3.0

    # --- Occupant count ---
    if req.occupants >= 3:
        if arch.id == "family_children":
            score += 5.0
        elif arch.id in ("single_retiree", "energy_poor"):
            score -= 3.0
    elif req.occupants == 1:
        if arch.id == "single_retiree" and req.daytime_occupancy == DaytimeOccupancy.FULL:
            score += 5.0
        elif arch.id == "family_children":
            score -= 3.0
        if arch.id == "energy_poor":
            score += 2.0

    if req.occupants == 2:
        if arch.id == "couple_working" and req.daytime_occupancy == DaytimeOccupancy.NONE:
            score += 3.0

    return score


def estimate_consumption(req: HouseholdEstimationRequest) -> HouseholdEstimationResult:
    """Select the best archetype and scale consumption for the household."""
    # 1. Score all archetypes
    scored = [(arch, _score_archetype(arch, req)) for arch in ARCHETYPES]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    best_arch = scored[0][0]

    # 2. Scale annual kWh within archetype range
    lo, hi = best_arch.annual_kwh_range
    mid = (lo + hi) / 2

    # Occupant adjustment: stepped diminishing returns (Besagni 2018, Eurostat)
    annual_kwh = mid * _occupant_factor(req.occupants)

    # Appliance modifiers — water heater
    # New field takes precedence; legacy bool treated as resistance for backward compat
    wh_type = req.water_heater_type
    if wh_type == WaterHeaterType.NONE and req.has_electric_water_heater:
        wh_type = WaterHeaterType.RESISTANCE
    if wh_type == WaterHeaterType.RESISTANCE:
        annual_kwh += _RESISTANCE_WATER_HEATER_KWH
    elif wh_type == WaterHeaterType.HEAT_PUMP:
        annual_kwh += _HEAT_PUMP_WATER_HEATER_KWH
    if req.has_electric_cooking:
        annual_kwh += _ELECTRIC_COOKING_KWH

    # Clamp to a reasonable range (allow slight overshoot beyond archetype max
    # when appliance modifiers push it up, but floor at archetype min)
    annual_kwh = max(lo, min(annual_kwh, hi * 1.25))
    annual_kwh = round(annual_kwh)

    # 3. F1/F2/F3 from archetype fractions
    f1_kwh = round(annual_kwh * best_arch.f1_fraction)
    f2_kwh = round(annual_kwh * best_arch.f2_fraction)
    f3_kwh = round(annual_kwh * best_arch.f3_fraction)

    # 4. Cost estimate
    annual_cost_eur = round(annual_kwh * settings.default_electricity_price_eur_kwh)

    # 5. Potenza from archetype
    potenza_kw = best_arch.typical_potenza_kw

    consumption = ConsumptionProfile(
        annual_kwh=annual_kwh,
        f1_kwh=f1_kwh,
        f2_kwh=f2_kwh,
        f3_kwh=f3_kwh,
        annual_cost_eur=annual_cost_eur,
        potenza_impegnata_kw=potenza_kw,
    )

    return HouseholdEstimationResult(
        consumption=consumption,
        archetype_id=best_arch.id,
        archetype_label=best_arch.label,
        confidence="indicative",
        estimated_fields=_ALL_ESTIMATED_FIELDS,
    )
