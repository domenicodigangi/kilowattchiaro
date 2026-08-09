"""Historical Italian solar incentive regimes, PV cost curve, and electricity prices.

Pure data module — no external dependencies, no API calls, no database.
Sources: GSE Ministerial Decrees, Agenzia delle Entrate, IRENA, ARERA/Eurostat.
"""

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class IncentiveType(str, Enum):
    FEED_IN_TARIFF = "feed_in_tariff"
    TAX_DEDUCTION = "tax_deduction"
    SUPERBONUS = "superbonus"


class ExportMechanism(str, Enum):
    SSP = "SSP"  # Scambio Sul Posto (net metering)
    RID = "RID"  # Ritiro Dedicato (dedicated withdrawal)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class IncentiveRegime(BaseModel):
    """A single Italian solar incentive regime with its rules and rates."""

    name: str
    start_date: date
    end_date: date | None = Field(default=None, description="None = still active")
    incentive_type: IncentiveType
    rate_eur_kwh: float | None = Field(
        default=None, description="Feed-in tariff rate (EUR/kWh)"
    )
    deduction_percent: float | None = Field(
        default=None, description="Tax deduction percentage"
    )
    max_amount_eur: float | None = Field(
        default=None, description="Deduction cap (e.g. EUR 96K)"
    )
    duration_years: int = Field(gt=0, description="Incentive duration in years")
    compatible_export: list[ExportMechanism] = Field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# Static data: 7 incentive regimes (chronological order)
# ---------------------------------------------------------------------------

INCENTIVE_REGIMES: list[IncentiveRegime] = [
    # 1. Conto Energia I — DM 28/07/2005
    IncentiveRegime(
        name="Conto Energia I",
        start_date=date(2005, 9, 19),
        end_date=date(2007, 2, 28),
        incentive_type=IncentiveType.FEED_IN_TARIFF,
        rate_eur_kwh=0.445,
        duration_years=20,
        compatible_export=[],
        notes="Self-consumed only, DM 28/07/2005. Rate for 1-20 kWp residential.",
    ),
    # 2. Conto Energia II — DM 19/02/2007
    IncentiveRegime(
        name="Conto Energia II",
        start_date=date(2007, 4, 1),
        end_date=date(2011, 6, 30),
        incentive_type=IncentiveType.FEED_IN_TARIFF,
        rate_eur_kwh=0.480,
        duration_years=20,
        compatible_export=[ExportMechanism.SSP],
        notes="Avg 1-3 kWp residential not integrated, DM 19/02/2007.",
    ),
    # 3. Conto Energia III — DM 06/08/2010
    IncentiveRegime(
        name="Conto Energia III",
        start_date=date(2011, 1, 1),
        end_date=date(2011, 5, 31),
        incentive_type=IncentiveType.FEED_IN_TARIFF,
        rate_eur_kwh=0.402,
        duration_years=20,
        compatible_export=[ExportMechanism.SSP],
        notes="DM 06/08/2010. Overlaps CE II (both active Jan-May 2011).",
    ),
    # 4. Conto Energia IV — DM 05/05/2011
    IncentiveRegime(
        name="Conto Energia IV",
        start_date=date(2011, 6, 1),
        end_date=date(2012, 8, 26),
        incentive_type=IncentiveType.FEED_IN_TARIFF,
        rate_eur_kwh=0.274,
        duration_years=20,
        compatible_export=[ExportMechanism.SSP],
        notes="Avg declining rate, DM 05/05/2011. Rate is H2 2011 avg for 1-3 kWp.",
    ),
    # 5. Conto Energia V — DM 05/07/2012
    IncentiveRegime(
        name="Conto Energia V",
        start_date=date(2012, 8, 27),
        end_date=date(2013, 7, 6),
        incentive_type=IncentiveType.FEED_IN_TARIFF,
        rate_eur_kwh=0.208,
        duration_years=20,
        compatible_export=[],
        notes="Tariffa omnicomprensiva (includes energy value), DM 05/07/2012.",
    ),
    # 6. Detrazione 50% — Bonus Casa (ongoing)
    IncentiveRegime(
        name="Detrazione 50%",
        start_date=date(2013, 7, 7),
        end_date=None,
        incentive_type=IncentiveType.TAX_DEDUCTION,
        deduction_percent=50.0,
        max_amount_eur=96_000.0,
        duration_years=10,
        compatible_export=[ExportMechanism.SSP, ExportMechanism.RID],
        notes="Bonus Casa IRPEF deduction over 10 years. Max EUR 96K eligible spend.",
    ),
    # 7. Superbonus 110% — DL 34/2020 (110% period only)
    IncentiveRegime(
        name="Superbonus 110%",
        start_date=date(2020, 7, 1),
        end_date=date(2023, 12, 31),
        incentive_type=IncentiveType.SUPERBONUS,
        deduction_percent=110.0,
        max_amount_eur=48_000.0,
        duration_years=10,
        compatible_export=[ExportMechanism.SSP, ExportMechanism.RID],
        notes="DL 34/2020. Only 110% period; trailing 70%/65% excluded per scope.",
    ),
]

# SSP closure date (closed to new applications)
_SSP_CLOSURE_DATE = date(2025, 5, 1)


# ---------------------------------------------------------------------------
# Lookup functions
# ---------------------------------------------------------------------------


def get_regime_at_date(decision_date: date) -> IncentiveRegime | None:
    """Return the best incentive regime available at a given decision date.

    Iterates in reverse chronological order so that Superbonus (superior
    incentive) takes priority over Detrazione 50% during their overlap period.
    Returns None for dates before any incentive existed.
    """
    for regime in reversed(INCENTIVE_REGIMES):
        if regime.start_date <= decision_date and (
            regime.end_date is None or decision_date <= regime.end_date
        ):
            return regime
    return None


def get_export_mechanism_at_date(decision_date: date) -> ExportMechanism:
    """Return the default export mechanism for new installations at a given date.

    SSP (Scambio Sul Posto) was available until May 2025; RID (Ritiro Dedicato)
    is the default for new installations from that date onward.
    """
    if decision_date < _SSP_CLOSURE_DATE:
        return ExportMechanism.SSP
    return ExportMechanism.RID


# ---------------------------------------------------------------------------
# Historical PV cost curve (EUR/kWp, residential 3-6 kWp)
# ---------------------------------------------------------------------------

PV_COST_EUR_PER_KWP: dict[int, float] = {
    2005: 6000,
    2006: 5800,
    2007: 5500,
    2008: 5200,
    2009: 4800,
    2010: 4200,
    2011: 3500,
    2012: 2800,
    2013: 2500,
    2014: 2400,
    2015: 2300,
    2016: 2200,
    2017: 2100,
    2018: 2000,
    2019: 1900,
    2020: 1800,
    2021: 1900,
    2022: 2100,
    2023: 2200,
    2024: 2200,
    2025: 2200,
}

_PV_COST_MIN_YEAR = min(PV_COST_EUR_PER_KWP)
_PV_COST_MAX_YEAR = max(PV_COST_EUR_PER_KWP)


def get_pv_cost_at_year(year: int) -> float:
    """Return residential PV installed cost (EUR/kWp) for a given year.

    Clamps to boundary values for years outside 2005-2025.
    Sources: IRENA Renewable Power Generation Costs, GSE Rapporto Statistico.
    """
    clamped = max(_PV_COST_MIN_YEAR, min(year, _PV_COST_MAX_YEAR))
    return PV_COST_EUR_PER_KWP[clamped]


# ---------------------------------------------------------------------------
# Historical electricity price curve (EUR/kWh, residential avg incl. taxes)
# ---------------------------------------------------------------------------

ELECTRICITY_PRICE_EUR_PER_KWH: dict[int, float] = {
    2005: 0.17,
    2006: 0.18,
    2007: 0.19,
    2008: 0.20,
    2009: 0.19,
    2010: 0.19,
    2011: 0.20,
    2012: 0.23,
    2013: 0.23,
    2014: 0.24,
    2015: 0.23,
    2016: 0.21,
    2017: 0.21,
    2018: 0.22,
    2019: 0.22,
    2020: 0.20,
    2021: 0.22,
    2022: 0.34,
    2023: 0.30,
    2024: 0.28,
    2025: 0.28,
}

_ELEC_PRICE_MIN_YEAR = min(ELECTRICITY_PRICE_EUR_PER_KWH)
_ELEC_PRICE_MAX_YEAR = max(ELECTRICITY_PRICE_EUR_PER_KWH)


def get_electricity_price_at_year(year: int) -> float:
    """Return avg residential electricity price (EUR/kWh) for a given year.

    Clamps to boundary values for years outside 2005-2025.
    Sources: Eurostat HICP energy, ARERA annual reports.
    """
    clamped = max(_ELEC_PRICE_MIN_YEAR, min(year, _ELEC_PRICE_MAX_YEAR))
    return ELECTRICITY_PRICE_EUR_PER_KWH[clamped]
