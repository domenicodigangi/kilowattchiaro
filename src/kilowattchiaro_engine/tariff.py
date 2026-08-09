"""Italian electricity tariff band classification.

Implements ARERA time-of-use band rules:
  F1: Mon-Fri 8:00-19:00 (excluding national holidays)
  F2: Mon-Fri 7:00-8:00 & 19:00-23:00; Sat 7:00-23:00
  F3: All other hours (nights 23:00-7:00, Sundays, national holidays)

Also provides rate resolution logic for different pricing models.
"""

from datetime import date, datetime

from .models.contract import ContractConditions, EnergyRate, PricingModel, TariffBand

# Italian national holidays (fixed dates — same every year)
_FIXED_HOLIDAYS_MD = {
    (1, 1),   # Capodanno
    (1, 6),   # Epifania
    (4, 25),  # Festa della Liberazione
    (5, 1),   # Festa del Lavoro
    (6, 2),   # Festa della Repubblica
    (8, 15),  # Ferragosto
    (11, 1),  # Ognissanti
    (12, 8),  # Immacolata Concezione
    (12, 25), # Natale
    (12, 26), # Santo Stefano
}


def _easter_date(year: int) -> date:
    """Compute Easter Sunday using the Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def is_italian_holiday(d: date) -> bool:
    """Check if a date is an Italian national holiday."""
    if (d.month, d.day) in _FIXED_HOLIDAYS_MD:
        return True

    # Easter Monday (Pasquetta) — moveable
    easter = _easter_date(d.year)
    easter_monday = date(d.year, easter.month, easter.day)
    # Shift by 1 day
    from datetime import timedelta

    easter_monday = easter + timedelta(days=1)
    if d == easter_monday:
        return True

    return False


def classify_band(timestamp: datetime) -> TariffBand:
    """Classify an Italian timestamp into F1, F2, or F3 tariff band.

    Rules per ARERA deliberation:
      F1: Mon-Fri 8:00-19:00 (non-holiday)
      F2: Mon-Fri 7:00-8:00 & 19:00-23:00 (non-holiday); Sat 7:00-23:00 (non-holiday)
      F3: Everything else (nights, Sundays, holidays)
    """
    d = timestamp.date()
    hour = timestamp.hour
    weekday = d.weekday()  # 0=Mon, 6=Sun

    # Sundays and holidays are always F3
    if weekday == 6 or is_italian_holiday(d):
        return TariffBand.F3

    # Saturday
    if weekday == 5:
        if 7 <= hour < 23:
            return TariffBand.F2
        return TariffBand.F3

    # Monday-Friday (non-holiday)
    if 8 <= hour < 19:
        return TariffBand.F1
    if (7 <= hour < 8) or (19 <= hour < 23):
        return TariffBand.F2
    return TariffBand.F3


def get_energy_rate(contract: ContractConditions, band: TariffBand) -> float:
    """Resolve the euro/kWh rate for a given band under a contract.

    For MONO contracts, returns the single rate regardless of band.
    For FIXED contracts, returns the band-specific fixed rate.
    For PUN_INDEXED, returns the spread (caller must add PUN price).
    """
    # Find matching rate entry
    for rate in contract.energy_rates:
        if rate.band == band:
            return _resolve_rate(rate, contract.pricing_model)

    # Fallback: look for MONO rate
    for rate in contract.energy_rates:
        if rate.band == TariffBand.MONO:
            return _resolve_rate(rate, contract.pricing_model)

    return 0.0


def _resolve_rate(rate: EnergyRate, pricing_model: PricingModel) -> float:
    """Extract the numeric rate based on pricing model."""
    if pricing_model == PricingModel.PUN_INDEXED:
        # For indexed pricing, return the spread.
        # The PUN base price must be added by the caller.
        return rate.pun_spread_eur_kwh or 0.0
    return rate.rate_eur_kwh or 0.0
