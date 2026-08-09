from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class TariffBand(str, Enum):
    """Italian electricity time-of-use bands (ARERA)."""

    F1 = "F1"  # Mon-Fri 8:00-19:00
    F2 = "F2"  # Mon-Fri 7-8 & 19-23; Sat 7-23
    F3 = "F3"  # Nights 23:00-7:00, Sundays, national holidays
    MONO = "MONO"  # Single flat rate (no time-of-use)


class PricingModel(str, Enum):
    FIXED = "fixed"  # Fixed euro/kWh
    PUN_INDEXED = "indexed"  # PUN (Prezzo Unico Nazionale) + spread
    TIME_OF_USE = "tou"  # Different rate per band


class EnergyRate(BaseModel):
    """Price per kWh for a specific tariff band."""

    band: TariffBand
    rate_eur_kwh: float | None = Field(
        default=None, description="Fixed rate in euro/kWh"
    )
    pun_spread_eur_kwh: float | None = Field(
        default=None, description="Spread over PUN in euro/kWh (for indexed pricing)"
    )


class FixedFee(BaseModel):
    """Recurring fixed charge (not consumption-based)."""

    name: str = Field(description="e.g. 'commercializzazione vendita'")
    amount_eur: float
    period: Literal["monthly", "yearly", "daily"]


class ScambioSulPosto(BaseModel):
    """Net metering (Scambio Sul Posto) configuration."""

    enabled: bool = False
    contribution_type: str = Field(
        default="", description="CS (Contributo in conto Scambio) type"
    )


class TaxConfig(BaseModel):
    """Italian electricity tax rates."""

    accise_eur_kwh: float = Field(
        default=0.0227, description="Excise duty (accise) euro/kWh"
    )
    iva_percent: float = Field(
        default=10.0, description="VAT rate for residential (10%)"
    )
    addizionali_eur_kwh: float = Field(
        default=0.0114, description="Additional regional/municipal taxes euro/kWh"
    )


class ContractConditions(BaseModel):
    """Full parsed energy supply contract — output of the AI extraction pipeline."""

    id: str = Field(description="UUID")
    user_id: str = Field(description="Owner user ID")
    filename: str
    provider: str = Field(description="e.g. 'Enel', 'Eni Plenitude', 'A2A'")
    contract_name: str
    start_date: date
    end_date: date | None = None

    pricing_model: PricingModel
    energy_rates: list[EnergyRate]
    fixed_fees: list[FixedFee] = Field(default_factory=list)

    power_capacity_kw: float | None = Field(
        default=None, description="Potenza impegnata (kW)"
    )
    power_rate_eur_kw_year: float | None = Field(
        default=None, description="Annual rate per kW of committed power"
    )
    feed_in_price_eur_kwh: float | None = Field(
        default=None, description="Export remuneration euro/kWh"
    )

    annual_kwh: float | None = Field(
        default=None, description="Annual electricity consumption from bill (kWh)"
    )
    f1_kwh: float | None = Field(
        default=None, description="F1 band consumption (kWh)"
    )
    f2_kwh: float | None = Field(
        default=None, description="F2 band consumption (kWh)"
    )
    f3_kwh: float | None = Field(
        default=None, description="F3 band consumption (kWh)"
    )
    annual_cost_eur: float | None = Field(
        default=None, description="Annual electricity cost from bill (EUR)"
    )

    taxes: TaxConfig = Field(default_factory=TaxConfig)
    scambio_sul_posto: ScambioSulPosto = Field(default_factory=ScambioSulPosto)

    raw_text: str = Field(
        default="", description="Full OCR text for reference and agent access"
    )
    confidence_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="AI parsing confidence (overall)"
    )
    field_confidence: dict[str, float] = Field(
        default_factory=dict,
        description="Per-field confidence scores (0.0-1.0) for key extracted fields"
    )
    parsed_at: datetime = Field(default_factory=datetime.utcnow)
    status: Literal["parsing", "draft", "confirmed"] = "draft"
