from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class InstallerQuote(BaseModel):
    """Structured data extracted from an Italian solar installer quote (preventivo)."""

    id: str = Field(description="UUID")
    user_id: str = Field(description="Owner user ID")

    # Core financials
    total_cost_eur: float | None = Field(
        default=None, description="Total quoted price including IVA (EUR)"
    )
    cost_per_kwp_eur: float | None = Field(
        default=None, description="Derived: total_cost_eur / system_kwp"
    )
    iva_rate: float | None = Field(
        default=None, description="IVA rate as decimal (e.g. 0.10 for 10%)"
    )
    chiavi_in_mano: bool | None = Field(
        default=None, description="Whether the quote is turnkey (all-inclusive)"
    )
    price_scope: Literal["pv_only", "pv_and_battery", "mixed_bundle", "unknown"] = Field(
        default="unknown",
        exclude=True,
        description=(
            "What the total pays for; mixed_bundle when it also covers substantial "
            "non-solar products or work (no price verdict then). Set by the quote agent; "
            "not persisted"
        ),
    )
    detrazione_percent: float | None = Field(
        default=None, description="Tax deduction percentage (typically 50)"
    )

    # System overview
    system_kwp: float | None = Field(
        default=None, description="Total system power (kWp)"
    )
    num_panels: int | None = Field(default=None, description="Number of panels")

    # Panel details
    panel_brand: str | None = Field(default=None, description="Panel manufacturer")
    panel_model: str | None = Field(default=None, description="Panel model name")
    panel_watt_peak: int | None = Field(
        default=None, description="Peak wattage per panel (Wp)"
    )
    panel_technology: str | None = Field(
        default=None,
        description="Panel technology (monocristallino, policristallino, half-cut, bifacciale)",
    )
    panel_efficiency_percent: float | None = Field(
        default=None, description="Panel efficiency percentage"
    )
    panel_dimensions_mm: dict[str, int] | None = Field(
        default=None, description="Panel dimensions {l, w, h} in mm"
    )

    # Inverter details
    inverter_brand: str | None = Field(
        default=None, description="Inverter manufacturer"
    )
    inverter_model: str | None = Field(default=None, description="Inverter model name")
    inverter_power_kw: float | None = Field(
        default=None, description="Inverter AC power output (kW)"
    )
    inverter_type: str | None = Field(
        default=None,
        description="Inverter type (stringa, micro, ibrido, centralizzato)",
    )
    inverter_num_mppt: int | None = Field(
        default=None, description="Number of MPPT trackers"
    )
    inverter_efficiency_percent: float | None = Field(
        default=None, description="Inverter efficiency percentage"
    )

    # Battery details (nullable — not all quotes include storage)
    battery_brand: str | None = Field(
        default=None, description="Battery manufacturer"
    )
    battery_model: str | None = Field(default=None, description="Battery model name")
    battery_kwh: float | None = Field(
        default=None, description="Usable battery capacity (kWh)"
    )
    battery_chemistry: str | None = Field(
        default=None, description="Battery chemistry (LFP, NMC)"
    )
    battery_cycles: int | None = Field(
        default=None, description="Rated battery cycle count"
    )

    # Other components (flexible jsonb)
    mounting_structure: dict[str, Any] | None = Field(
        default=None, description="Mounting structure info {type, material}"
    )
    monitoring_system: dict[str, Any] | None = Field(
        default=None, description="Monitoring system info {brand, model}"
    )
    additional_components: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Other components [{name, brand, model, notes}]",
    )

    # Installer claims
    estimated_annual_kwh: float | None = Field(
        default=None, description="Installer's claimed annual production (kWh)"
    )
    payback_years_claimed: float | None = Field(
        default=None, description="Installer's claimed payback period (years)"
    )
    ssp_revenue_claimed_eur: float | None = Field(
        default=None,
        description="Installer's claimed SSP/RID annual revenue (EUR)",
    )

    # Warranties
    warranty_years_panels_product: int | None = Field(
        default=None, description="Panel product warranty (years)"
    )
    warranty_years_panels_performance: int | None = Field(
        default=None, description="Panel performance warranty (years)"
    )
    warranty_years_inverter: int | None = Field(
        default=None, description="Inverter warranty (years)"
    )
    warranty_years_battery: int | None = Field(
        default=None, description="Battery warranty (years)"
    )
    warranty_years_workmanship: int | None = Field(
        default=None, description="Installation workmanship warranty (years)"
    )

    # Metadata
    installer_name: str | None = Field(default=None, description="Installer company name")
    installer_piva: str | None = Field(
        default=None, description="Installer P.IVA (VAT number, 11 digits)"
    )
    installer_rea: str | None = Field(
        default=None, description="REA/CCIAA registration number"
    )
    quote_date: date | None = Field(default=None, description="Date on the quote")
    quote_validity_days: int | None = Field(
        default=None, description="Quote validity period in days"
    )

    # Regulatory / compliance signals (extracted from quote text)
    has_dico_commitment: bool | None = Field(
        default=None,
        description="Whether quote commits to releasing Dichiarazione di Conformità (DM 37/08)",
    )
    has_withdrawal_notice: bool | None = Field(
        default=None,
        description="Whether right of withdrawal (diritto di recesso, 14/30 days) is mentioned",
    )
    has_legal_guarantee: bool | None = Field(
        default=None,
        description="Whether 24-month garanzia di conformità (D.Lgs. 206/2005) is stated",
    )
    payment_method: str | None = Field(
        default=None,
        description="Payment method mentioned (bonifico parlante, finanziamento, etc.)",
    )
    included_services: list[str] = Field(
        default_factory=list,
        description="Services included (e.g. grid connection, GSE registration, ENEA)",
    )
    raw_text_flags: list[str] = Field(
        default_factory=list,
        description="Regulatory text patterns detected in OCR (e.g. cessione_credito, costo_zero)",
    )

    # Parsing infrastructure (mirrors ConsumptionImport pattern)
    confidence_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Overall extraction confidence"
    )
    parse_quality: Literal["full", "partial", "insufficient"] = Field(
        default="insufficient",
        description="full=cost+kwp+panel info, partial=cost or kwp, insufficient=neither",
    )
    field_confidence: dict[str, float] = Field(
        default_factory=dict,
        description="Per-field confidence scores (0.0-1.0)",
    )
    inferred_fields: list[str] = Field(
        default_factory=list,
        description="Fields inferred via heuristics rather than direct LLM extraction",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal parse warnings or missing-field notes",
    )
    status: Literal["parsing", "ready", "failed"] = "parsing"
    parsed_at: datetime = Field(default_factory=datetime.utcnow)
