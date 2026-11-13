"""The verdict, the what-if grid and the robustness label (ticket 312).

The browser shows only what the engine computes. Every installed scenario carries:

- ``verdict``: positive when NPV is above zero and the system pays back within the
  20-year horizon; caution when it saves money but NPV is not positive; else negative.
- ``sensitivity``: the scenario re-run for each discrete choice the results page
  offers, the five price scenarios x the three cost tiers, so switching between them
  needs no browser arithmetic and no new request.
- ``robustness``: whether the verdict holds over detrazione (the applied rate and the
  lower one the rules can apply to that home, if any) x the five price scenarios x
  self-consumption 10 points lower.
  "Robusto", "Dipende da: <input>" or "Fragile"; no probabilities.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from itertools import combinations

from ..models.solar_eval import (
    PriceScenarioName,
    Robustness,
    RobustnessFactor,
    Verdict,
)
from ..detrazione import detrazione_rate
from ._constants import _ANALYSIS_HORIZON_YEARS
from .models import AnnualEnergyProfile

# Self-consumption sensitivity: 10 points of annual production move from
# self-consumed to exported (the share the hourly profile could plausibly miss).
SELF_CONSUMPTION_SHIFT = 0.10
SELF_CONSUMPTION_SHIFT_POINTS = -10

# Grid key: (detrazione at the lower rate?, price scenario, self-consumption -10 pt?)
GridKey = tuple[bool, PriceScenarioName, bool]

_INPUTS = ("detrazione", "price_scenario", "self_consumption")


def verdict_for(
    npv: float | None, payback_years: float | None, annual_savings: float
) -> Verdict:
    if (
        npv is not None
        and npv > 0
        and payback_years is not None
        and payback_years <= _ANALYSIS_HORIZON_YEARS
    ):
        return "positive"
    if annual_savings > 0:
        return "caution"
    return "negative"


def lower_detrazione_rate(payment_date: date | None, *, main_home: bool) -> float | None:
    """The lower rate the rules can apply to this home: the from-2027 rate when the
    work is paid at the higher 2026 rate (50% -> 36% main home, 36% -> 30% other
    homes); None when it already gets the lower one."""
    applied = detrazione_rate(payment_date, main_home=main_home)
    later = detrazione_rate(None, main_home=main_home)
    return later if later < applied else None


def shift_self_consumption(
    profile: AnnualEnergyProfile, share_of_production: float
) -> AnnualEnergyProfile:
    """Move ``share_of_production`` of production from self-consumed to exported."""
    moved = min(
        profile.self_consumption_kwh, profile.production_kwh * share_of_production
    )
    return AnnualEnergyProfile(
        production_kwh=profile.production_kwh,
        self_consumption_kwh=profile.self_consumption_kwh - moved,
        grid_import_kwh=profile.grid_import_kwh + moved,
        grid_export_kwh=profile.grid_export_kwh + moved,
    )


def _worth_it(verdict: Verdict) -> bool:
    # The decision is "conviene or not": caution and negative sit on the same side.
    return verdict == "positive"


def classify_robustness(
    verdicts: Mapping[GridKey, Verdict],
    *,
    base_price: PriceScenarioName,
    lower_detrazione_percent: int | None,
) -> Robustness:
    """Label how firmly the base verdict (applied detrazione, ``base_price``, base
    self-consumption) holds over ``verdicts``.

    A set of inputs flips the verdict when moving exactly those inputs away from the
    base changes "worth it or not" in at least one cell. Two or more inputs that each
    flip it alone make it fragile; otherwise the label names the smallest set that does.
    """
    base = _worth_it(verdicts[(False, base_price, False)])
    inputs = [
        i for i in _INPUTS if i != "detrazione" or lower_detrazione_percent is not None
    ]
    other_prices = [p for p in PriceScenarioName if p != base_price]

    def flipping_price(moved: tuple[str, ...]) -> PriceScenarioName | None | bool:
        """The first price scenario that flips the verdict with ``moved`` departed;
        True when the set flips it without moving the price; False when it does not."""
        d = "detrazione" in moved
        s = "self_consumption" in moved
        if "price_scenario" not in moved:
            return _worth_it(verdicts[(d, base_price, s)]) != base
        for p in other_prices:
            if _worth_it(verdicts[(d, p, s)]) != base:
                return p
        return False

    def factors(
        moved: tuple[str, ...], price: PriceScenarioName | bool
    ) -> list[RobustnessFactor]:
        out = []
        for name in moved:
            if name == "detrazione":
                out.append(
                    RobustnessFactor(
                        input="detrazione", detrazione_percent=lower_detrazione_percent
                    )
                )
            elif name == "price_scenario":
                out.append(
                    RobustnessFactor(input="price_scenario", price_scenario=price)
                )
            else:
                out.append(
                    RobustnessFactor(
                        input="self_consumption",
                        self_consumption_points=SELF_CONSUMPTION_SHIFT_POINTS,
                    )
                )
        return out

    alone = [(name, flipping_price((name,))) for name in inputs]
    alone = [(name, price) for name, price in alone if price is not False]
    if len(alone) >= 2:
        return Robustness(
            level="fragile",
            depends_on=[f for name, price in alone for f in factors((name,), price)],
        )
    if len(alone) == 1:
        name, price = alone[0]
        return Robustness(level="depends", depends_on=factors((name,), price))
    for size in range(2, len(inputs) + 1):
        for moved in combinations(inputs, size):
            price = flipping_price(moved)
            if price is not False:
                return Robustness(level="depends", depends_on=factors(moved, price))
    return Robustness(level="robust")
