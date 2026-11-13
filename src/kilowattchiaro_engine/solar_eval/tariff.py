"""Household electricity tariff split into a variable and a fixed part (engine release v1).

PV avoids only the per-kWh part of a bill. Fixed charges (transport and meter
quota fissa, power quota, supplier fixed fee, with VAT) are paid with or
without panels, so avoided kWh are valued at the variable rate.

Defaults (ARERA press release, 29 September 2026): the vulnerable typical
customer (2,000 kWh/yr, 3 kW, resident) spends EUR 665.38 over 2026. Fixed
charges of about EUR 155/yr come from the same release's components: transport
and meter EUR 0.0618/kWh and retail sales EUR 0.0211/kWh at 2,000 kWh are
mostly per-point and per-kW quotas (~EUR 140/yr), plus 10% VAT. System charges
(oneri) carry no fixed part for residents. The three figures live in the
assumptions register (``app/assumptions.yaml``, ``arera_typical_bill``).
"""

from __future__ import annotations

from ..assumptions import value as _assumption

_BILL = _assumption("arera_typical_bill")  # app/assumptions.yaml
ARERA_TYPICAL_ANNUAL_SPEND_EUR: float = _BILL["annual_spend_eur"]
ARERA_TYPICAL_ANNUAL_KWH: float = _BILL["annual_kwh"]
FIXED_CHARGES_EUR_YR: float = _BILL["fixed_charges_eur_yr"]
DEFAULT_VARIABLE_RATE_EUR_KWH = (
    ARERA_TYPICAL_ANNUAL_SPEND_EUR - FIXED_CHARGES_EUR_YR
) / ARERA_TYPICAL_ANNUAL_KWH
# A typed bill below the fixed charges (or barely above) cannot be split
# reliably; never value an avoided kWh below half of the bill's average price.
_MIN_VARIABLE_SHARE = 0.5


def estimated_annual_bill_eur(annual_kwh: float) -> float:
    """Annual bill for a household with no bill on hand: ARERA variable rate plus fixed."""
    return annual_kwh * DEFAULT_VARIABLE_RATE_EUR_KWH + FIXED_CHARGES_EUR_YR


def variable_rate_eur_kwh(annual_cost_eur: float | None, annual_kwh: float) -> float:
    """EUR per avoided kWh: the bill without its fixed charges, per kWh."""
    if not annual_cost_eur or annual_kwh <= 0:
        return DEFAULT_VARIABLE_RATE_EUR_KWH
    average = annual_cost_eur / annual_kwh
    return max((annual_cost_eur - FIXED_CHARGES_EUR_YR) / annual_kwh, average * _MIN_VARIABLE_SHARE)
