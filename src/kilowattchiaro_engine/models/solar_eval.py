"""Pydantic models for solar installation evaluation."""

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class RoofSource(str, Enum):
    GOOGLE_SOLAR = "google_solar"
    MANUAL = "manual"


class GeoPoint(BaseModel):
    """Latitude/longitude point."""

    latitude: float
    longitude: float


class GeoBoundingBox(BaseModel):
    """Northeast/southwest bounding box."""

    sw: GeoPoint
    ne: GeoPoint


class RoofSegment(BaseModel):
    """A single roof segment from Google Solar API or manual input."""

    segment_index: int | None = Field(default=None, ge=0)
    tilt_degrees: float = Field(ge=0, le=90)
    azimuth_degrees: float = Field(ge=0, lt=360, description="0=North, 180=South")
    area_m2: float = Field(gt=0)
    yearly_energy_kwh: float | None = Field(
        default=None, description="Google Solar estimate for this segment"
    )
    center: GeoPoint | None = None
    bounding_box: GeoBoundingBox | None = None


class RoofInfo(BaseModel):
    """Roof characteristics for solar evaluation."""

    segments: list[RoofSegment] = Field(min_length=1)
    total_usable_area_m2: float = Field(gt=0)
    source: RoofSource = RoofSource.MANUAL


class ConsumptionProfile(BaseModel):
    """Annual electricity consumption extracted from bill or manual input.

    kWh values by ARERA tariff band.
    """

    annual_kwh: float = Field(gt=0)
    f1_kwh: float = Field(ge=0, description="Mon-Fri 8-19")
    f2_kwh: float = Field(ge=0, description="Mon-Fri 7-8/19-23, Sat 7-23")
    f3_kwh: float = Field(ge=0, description="Nights, Sundays, holidays")
    annual_cost_eur: float | None = Field(
        default=None, description="Current annual bill from parsed contract"
    )
    potenza_impegnata_kw: float = Field(
        default=3.0, ge=1.5, le=10.0,
        description="Potenza impegnata (contracted power capacity in kW)",
    )


class PriceScenarioName(str, Enum):
    """Supported electricity price projection scenarios for solar eval."""

    REFERENCE = "reference"
    HIGH_ELECTRIFICATION = "high_electrification"
    ABUNDANT_RENEWABLES = "abundant_renewables"
    STATUS_QUO = "status_quo"
    ENERGY_CRISIS = "energy_crisis"


class SolarEvalRequest(BaseModel):
    """Request payload for POST /api/solar/evaluate."""

    consumption: ConsumptionProfile
    lat: float = Field(ge=35.0, le=48.0, description="Italian latitude range")
    lon: float = Field(ge=6.0, le=19.0, description="Italian longitude range")
    roof: RoofInfo | None = Field(
        default=None, description="If None, uses Google Solar API to detect"
    )
    desired_kwp: float = Field(
        gt=0, le=20, description="Desired system size in kWp"
    )
    include_battery: bool = False
    battery_kwh: float = Field(
        default=10.0, ge=5.0, le=30.0, description="Battery capacity if included"
    )
    # Optional overrides for cost assumptions
    panel_cost_eur_per_kwp: float | None = None
    battery_cost_eur: float | None = None
    battery_cost_eur_per_kwh: float | None = Field(
        default=None, description="Per-kWh battery cost; when set, overrides battery_cost_eur"
    )
    discount_rate: float = Field(
        default=0.03, ge=0.0, le=0.15, description="Nominal discount rate for NPV"
    )
    energy_price_inflation: float = Field(
        default=0.02, ge=0.0, le=0.10,
        description="Baseline CPI input for component-based price projections",
    )
    price_scenario: PriceScenarioName = Field(
        default=PriceScenarioName.REFERENCE,
        description="Named electricity price trajectory for 20-year evaluation",
    )


class ScenarioType(str, Enum):
    STATUS_QUO = "status_quo"
    SOLAR_ONLY = "solar_only"
    SOLAR_BATTERY = "solar_battery"


class MonthlyDetail(BaseModel):
    """Monthly breakdown for a single scenario."""

    month: int = Field(ge=1, le=12)
    production_kwh: float
    self_consumption_kwh: float
    grid_import_kwh: float
    grid_export_kwh: float
    electricity_cost_eur: float
    rid_revenue_eur: float = Field(
        default=0.0, description="RID (Ritiro Dedicato) export revenue"
    )


class ScenarioResult(BaseModel):
    """Financial result for one scenario (status quo, solar, solar+battery)."""

    scenario: ScenarioType
    label: str
    investment_cost_eur: float = 0.0
    annual_electricity_cost_eur: float
    annual_rid_revenue_eur: float = 0.0
    annual_detrazione_eur: float = Field(
        default=0.0, description="50% tax deduction annual installment (10 years)"
    )
    annual_savings_eur: float = Field(
        default=0.0, description="Savings vs status quo"
    )
    payback_years: float | None = None
    npv_20yr_eur: float | None = Field(
        default=None, description="Net present value over 20 years"
    )
    irr_percent: float | None = Field(
        default=None, description="Internal rate of return"
    )
    npv_sensitivity: dict[str, float] | None = Field(
        default=None,
        description="NPV at multiple discount rates, e.g. {'3%': 5000, '5%': 3200, '8%': 1100}",
    )
    cumulative_cash_flow: list[float] = Field(
        default_factory=list,
        description="Cumulative cash flow for years 0-20 (year 0 = -investment)",
    )
    annual_breakdown: list[dict] | None = Field(
        default=None,
        description="Per-year breakdown: energy_savings, rid, detrazione, maintenance, replacement",
    )
    self_consumption_percent: float = 0.0
    monthly_details: list[MonthlyDetail] = Field(default_factory=list)


class SolarEvalResult(BaseModel):
    """Full response from solar evaluation endpoint."""

    scenarios: list[ScenarioResult]
    annual_production_kwh: float
    system_kwp: float
    location: str = Field(description="lat,lon used for calculation")
    assumptions: dict = Field(
        default_factory=dict,
        description="Key assumptions used in calculation",
    )
    # Field name kept as ``narrative_it`` for frontend compatibility, but it
    # carries the LLM narrative in the request locale (Italian by default).
    narrative_it: str | None = Field(
        default=None,
        description="LLM-generated narrative summarising the evaluation (request locale)",
    )
    tier: Literal["free", "premium"] | None = Field(
        default=None,
        description="Access tier: free (anonymous) or premium (authenticated+approved)",
    )


class SolarRecommendationRequest(BaseModel):
    """Request payload for POST /api/solar/recommendation."""

    consumption: ConsumptionProfile
    lat: float = Field(ge=35.0, le=48.0, description="Italian latitude range")
    lon: float = Field(ge=6.0, le=19.0, description="Italian longitude range")
    roof: RoofInfo | None = Field(
        default=None,
        description="Selected roof segments or manual roof used for sizing",
    )
    include_battery: bool = False
    battery_kwh: float = Field(
        default=10.0, ge=5.0, le=30.0, description="Battery capacity if included"
    )
    panel_cost_eur_per_kwp: float | None = None
    battery_cost_eur: float | None = None
    battery_cost_eur_per_kwh: float | None = Field(
        default=None, description="Per-kWh battery cost; when set, overrides battery_cost_eur"
    )
    discount_rate: float = Field(
        default=0.03, ge=0.0, le=0.15, description="Nominal discount rate for NPV"
    )
    energy_price_inflation: float = Field(
        default=0.02, ge=0.0, le=0.10,
        description="Baseline CPI input for component-based price projections",
    )
    price_scenario: PriceScenarioName = Field(
        default=PriceScenarioName.REFERENCE,
        description="Named electricity price trajectory for 20-year evaluation",
    )
    min_kwp: float = Field(default=1.0, gt=0.0, le=20.0)
    max_kwp: float | None = Field(default=None, gt=0.0, le=20.0)
    step_kwp: float = Field(default=0.5, gt=0.0, le=5.0)


class SolarRecommendationCandidate(BaseModel):
    """One candidate system size scored by the recommendation engine."""

    system_kwp: float = Field(gt=0)
    annual_production_kwh: float = Field(ge=0)
    annual_savings_eur: float = Field(ge=0)
    payback_years: float | None = None
    npv_20yr_eur: float | None = None
    total_return_10yr_eur: float | None = None
    irr_percent: float | None = None
    self_consumption_percent: float = Field(ge=0)
    demand_coverage_percent: float = Field(ge=0)
    export_ratio_percent: float = Field(ge=0)
    include_battery: bool = False
    battery_kwh: float | None = None


class SolarRecommendationResult(BaseModel):
    """Economic recommendation for the best photovoltaic system size."""

    recommended: SolarRecommendationCandidate
    alternatives: list[SolarRecommendationCandidate] = Field(default_factory=list)
    selection_basis: str
    is_profitable: bool
    max_feasible_kwp: float = Field(gt=0)
    estimated_yield_kwh_per_kwp: float = Field(ge=0)
    candidate_count: int = Field(ge=1)
    assumptions: dict = Field(default_factory=dict)


class PVGISRequest(BaseModel):
    """Request for PVGIS production estimate."""

    lat: float
    lon: float
    peakpower_kwp: float
    tilt: float = Field(default=35.0, ge=0, le=90)
    azimuth: float = Field(
        default=0.0, ge=-180, le=180, description="PVGIS convention: 0=south"
    )
    system_loss: float = Field(default=14.0, ge=0, le=50, description="% system losses")


class PVGISMonthly(BaseModel):
    """Monthly production estimate from PVGIS."""

    month: int
    e_m: float = Field(description="Monthly energy production kWh")
    h_m: float = Field(description="Monthly irradiation kWh/m2")


class PVGISResult(BaseModel):
    """PVGIS API response (parsed)."""

    monthly: list[PVGISMonthly]
    yearly_kwh: float
    location: str


class GoogleSolarRequest(BaseModel):
    """Request for Google Solar Building Insights."""

    lat: float
    lon: float


class GoogleSolarResult(BaseModel):
    """Parsed response from Google Solar Building Insights API."""

    segments: list[RoofSegment]
    panels: list["SolarPanel"] = Field(default_factory=list)
    total_usable_area_m2: float
    max_panel_count: int
    max_kwp: float
    yearly_energy_kwh: float
    carbon_offset_kg: float | None = None
    coverage_available: bool = True


class SolarPanel(BaseModel):
    """Single panel center returned by Google Solar."""

    segment_index: int = Field(ge=0)
    center: GeoPoint
    yearly_energy_kwh: float | None = None
    orientation: str | None = None


class PredictedMetrics(BaseModel):
    """Metrics predicted at the decision point using only knowledge-at-time."""

    npv_20yr_eur: float
    payback_years: float | None
    irr_percent: float | None
    annual_savings_eur: float
    investment_eur: float


class ActualMetrics(BaseModel):
    """Metrics computed using real price trajectory from decision date to present."""

    npv_to_date_eur: float
    projected_npv_20yr: float | None = Field(
        default=None,
        description="Actual cash flows projected to 20-year horizon for fair comparison",
    )
    payback_years: float | None
    irr_percent: float | None
    annual_savings_avg_eur: float
    years_elapsed: int = Field(ge=0)


RegimeType = Literal["feed_in_tariff", "tax_deduction", "superbonus", "none"]


class HistoricalBacktestResult(BaseModel):
    """Result of a single historical decision-point backtest."""

    decision_date: date
    regime_name: str
    regime_type: RegimeType
    system_kwp: float
    investment_eur: float
    pv_cost_eur_per_kwp: float
    electricity_price_at_decision: float
    predicted: PredictedMetrics
    actual: ActualMetrics
    npv_prediction_error_eur: float
    npv_prediction_error_pct: float | None
    include_battery: bool = False


class DecisionPointValidation(BaseModel):
    """Validation result for a single historical decision point."""

    decision_date: date
    regime_name: str
    regime_type: RegimeType
    years_elapsed: int
    predicted_npv: float
    actual_npv: float
    npv_error_eur: float
    npv_error_pct: float | None
    predicted_payback: float | None
    actual_payback: float | None
    payback_error_years: float | None
    predicted_irr: float | None
    actual_irr: float | None
    irr_error_pct_points: float | None
    projected_npv_20yr: float | None = Field(
        default=None,
        description="Actual NPV projected to 20-year horizon for fair comparison",
    )
    predicted_profitable: bool
    actual_profitable: bool
    recommendation_correct: bool
    included_in_aggregates: bool = Field(
        description="False for decision points with < 5 years elapsed"
    )


class AggregateMetrics(BaseModel):
    """Aggregate accuracy metrics across qualifying decision points."""

    mean_npv_error_pct: float
    median_npv_error_pct: float
    max_npv_error_pct: float
    binary_accuracy: float
    correct_count: int
    total_count: int
    mean_payback_error_years: float | None
    median_payback_error_years: float | None
    mean_irr_error_pct_points: float | None
    median_irr_error_pct_points: float | None
    spearman_rank_correlation: float | None
    accuracy_by_regime_type: dict[RegimeType, float]


class ValidationReport(BaseModel):
    """Full validation report with per-decision-point results and aggregates."""

    scenario_label: str
    system_kwp: float
    annual_consumption_kwh: float
    decision_points: list[DecisionPointValidation]
    aggregate: AggregateMetrics
    regimes_well_modeled: list[str]
    regimes_poorly_modeled: list[str]


class HistoricalBacktestRequest(BaseModel):
    """Request payload for POST /api/solar/historical-backtest."""

    lat: float = Field(ge=35.0, le=48.0, description="Italian latitude range")
    lon: float = Field(ge=6.0, le=19.0, description="Italian longitude range")
    desired_kwp: float = Field(default=6.0, gt=0, le=20)
    annual_consumption_kwh: float = Field(default=3000, gt=0)
    include_battery: bool = False
    battery_kwh: float = Field(default=10.0, ge=5.0, le=30.0)


class PVGISHourlyRecord(BaseModel):
    """Single hourly record from PVGIS TMY response."""

    hour_index: int = Field(ge=0, lt=8760, description="0-based hour of year")
    ghi_wm2: float = Field(ge=0, description="Global Horizontal Irradiance W/m2")
    temperature_c: float = Field(description="Ambient temperature in Celsius")
    wind_speed_ms: float = Field(ge=0, default=0.0, description="Wind speed m/s")


class PVGISTMYResult(BaseModel):
    """Parsed PVGIS TMY response - 8760 hourly records."""

    records: list[PVGISHourlyRecord] = Field(min_length=8760, max_length=8760)
    location: str = Field(description="lat,lon used for query")


# ---------------------------------------------------------------------------
# Household-based consumption estimation
# ---------------------------------------------------------------------------


class DaytimeOccupancy(str, Enum):
    """How many household members are home during daytime hours."""

    NONE = "none"  # all away (working/school)
    PARTIAL = "partial"  # some home
    FULL = "full"  # all home (retiree, WFH, etc.)


class WaterHeaterType(str, Enum):
    """Type of electric water heater."""

    NONE = "none"
    RESISTANCE = "resistance"  # Traditional scaldabagno elettrico (~1500 kWh/yr)
    HEAT_PUMP = "heat_pump"  # Scaldacqua a pompa di calore (~700 kWh/yr)


class HouseholdEstimationRequest(BaseModel):
    """Household characteristics for consumption estimation."""

    occupants: int = Field(ge=1, le=10, description="Number of household members")
    has_heat_pump: bool = Field(default=False, description="Heating via heat pump")
    has_ac: bool = Field(default=False, description="Air conditioning present")
    has_electric_water_heater: bool = Field(
        default=False,
        description="Electric water heater — deprecated, use water_heater_type",
    )
    water_heater_type: WaterHeaterType = Field(
        default=WaterHeaterType.NONE,
        description="Type of electric water heater (none, resistance, heat_pump)",
    )
    has_electric_cooking: bool = Field(
        default=False, description="Electric/induction cooking"
    )
    daytime_occupancy: DaytimeOccupancy = Field(
        default=DaytimeOccupancy.PARTIAL,
        description="How many members are home during working hours",
    )


class HouseholdEstimationResult(BaseModel):
    """Estimated consumption derived from household profile."""

    consumption: ConsumptionProfile
    archetype_id: str = Field(description="Selected RSE archetype ID")
    archetype_label: str = Field(description="Human-readable archetype name")
    confidence: Literal["indicative"] = Field(
        default="indicative",
        description="Always indicative — this is an estimate from household traits",
    )
    estimated_fields: list[str] = Field(
        description="All consumption fields that were estimated (always all of them)",
    )


class CondoWaitlistRequest(BaseModel):
    """Signup for the condominio/CER support waitlist.

    Collected under Art. 6.1.a consent — ``consent`` must be an explicit
    opt-in (the endpoint rejects False) and ``consent_version`` records which
    consent text was shown. ``source`` distinguishes capture surfaces.
    """

    email: EmailStr
    consent: bool = Field(
        description="Explicit opt-in to be contacted when condo support ships.",
    )
    consent_version: str = Field(default="", max_length=16)
    source: str = Field(default="wizard_gate", max_length=32)


class EmailResultsRequest(BaseModel):
    """Request to send the solar evaluation summary to a user's email.

    Numeric fields are bounded to constrain the abuse surface — the endpoint
    renders these values into a fixed HTML template, so without bounds a caller
    could embed arbitrarily large numbers into spam emails.
    """

    email: EmailStr
    system_kwp: float = Field(ge=0, le=100)
    annual_production_kwh: float = Field(ge=0, le=200_000)
    annual_savings_eur: float = Field(ge=0, le=100_000)
    payback_years: float | None = Field(default=None, ge=0, le=100)
    followup_consent: bool = Field(
        default=False,
        description="Opt-in (unticked by default) to future contact from "
        "KiloWattChiaro. Never implies sharing with installers.",
    )
    consent_version: str = Field(default="", max_length=16)
