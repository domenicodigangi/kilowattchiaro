"""Detrazione IRPEF for residential PV (art. 16-bis TUIR, Legge di Bilancio 2025 and 2026).

The rate depends on when the work is paid and on whether the home is the
taxpayer's main residence (abitazione principale):

| Paid                 | Main home | Other homes |
|----------------------|-----------|-------------|
| by 31 December 2026  | 50%       | 36%         |
| from 1 January 2027  | 36%       | 30%         |

Without a payment date the estimate assumes payment from 2027, the cautious
side: installation lead times make a payment by year-end uncertain.

The deduction is spread over 10 equal yearly instalments and the eligible
spend is capped at EUR 96,000 per housing unit. Tax capacity (incapienza) is
not modelled. The rates, the date and the cap live in the assumptions register
(``app/assumptions.yaml``, ``detrazione``).
"""

from __future__ import annotations

from datetime import date

from .assumptions import value as _assumption

_RULES = _assumption("detrazione")  # app/assumptions.yaml
DETRAZIONE_SPEND_CAP_EUR = float(_RULES["spend_cap_eur"])
DETRAZIONE_YEARS: int = _RULES["years"]
_LAST_DAY_OF_HIGHER_RATES: date = _RULES["higher_rates_until"]


def detrazione_rate(payment_date: date | None, *, main_home: bool) -> float:
    """Deduction rate (0-1) for work paid on ``payment_date``; None means paid from 2027."""
    if payment_date is not None and payment_date <= _LAST_DAY_OF_HIGHER_RATES:
        return _RULES["main_home_higher"] if main_home else _RULES["other_homes_higher"]
    return _RULES["main_home_lower"] if main_home else _RULES["other_homes_lower"]


def detrazione_total_eur(eligible_spend_eur: float, rate: float) -> float:
    """Total deduction over the 10 years, with the spend cap applied."""
    return min(eligible_spend_eur, DETRAZIONE_SPEND_CAP_EUR) * rate


def deduction_basis_key(rate_percent: int, main_home: bool) -> str:
    """Message key naming the rule behind ``rate_percent`` (for copy next to the deduction)."""
    if main_home:
        return "deduction_basis_main_2026" if rate_percent >= 50 else "deduction_basis_main_2027"
    return "deduction_basis_other_2026" if rate_percent >= 36 else "deduction_basis_other_2027"
