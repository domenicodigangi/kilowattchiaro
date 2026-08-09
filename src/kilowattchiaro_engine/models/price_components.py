"""Electricity price component decomposition models.

Italian electricity prices are composed of five macro-components,
each with different dynamics and escalation rates:

1. Wholesale (materia energia): PUN + dispatching — market-driven, volatile
2. Network (trasporto): transport + metering — ARERA-regulated, stable
3. System charges (oneri di sistema): ASOS + ARIM — policy-driven, declining
4. Taxes (imposte): accise + addizionali — legislative, stable
5. Retail margin: supplier markup — competitive

Self-consumed kWh avoids ALL variable components (EUR 0.25-0.35/kWh saved).
Fixed costs remain regardless of solar: per-POD charge, per-kW power charge.

Reference: ARERA quarterly publications, Stimaspesa tool.
Baseline: domestico residente, 2700 kWh/yr, 3 kW commitment.
"""

from pydantic import BaseModel, Field, computed_field, field_validator


class ElectricityPriceComponents(BaseModel):
    """Italian electricity price decomposed into macro-components (EUR/kWh).

    Each field represents the per-kWh contribution to the total price (pre-IVA).
    IVA (10% residential) is applied on the sum of all components.
    """

    wholesale_eur_kwh: float = Field(
        description="Materia energia: PUN + dispatching (~53% of total)"
    )
    network_eur_kwh: float = Field(
        description="Trasporto e gestione contatore (~20% of total)"
    )
    system_charges_eur_kwh: float = Field(
        description="Oneri di sistema: ASOS + ARIM (~10% of total)"
    )
    taxes_eur_kwh: float = Field(
        description="Imposte: accise + addizionali (~10% of total)"
    )
    retail_margin_eur_kwh: float = Field(
        description="Margine fornitore (~7% of total)"
    )

    @computed_field
    @property
    def total_eur_kwh(self) -> float:
        """Sum of all components (pre-IVA)."""
        return round(
            self.wholesale_eur_kwh
            + self.network_eur_kwh
            + self.system_charges_eur_kwh
            + self.taxes_eur_kwh
            + self.retail_margin_eur_kwh,
            6,
        )

    def shares(self) -> dict[str, float]:
        """Percentage share of each component (sums to ~100%)."""
        total = self.total_eur_kwh
        if total == 0:
            return {
                "wholesale": 0.0,
                "network": 0.0,
                "system_charges": 0.0,
                "taxes": 0.0,
                "retail_margin": 0.0,
            }
        return {
            "wholesale": round(self.wholesale_eur_kwh / total * 100, 1),
            "network": round(self.network_eur_kwh / total * 100, 1),
            "system_charges": round(self.system_charges_eur_kwh / total * 100, 1),
            "taxes": round(self.taxes_eur_kwh / total * 100, 1),
            "retail_margin": round(self.retail_margin_eur_kwh / total * 100, 1),
        }


class ComponentEscalationRates(BaseModel):
    """Annual escalation rates (fraction) for each price component.

    Positive = annual increase, negative = annual decrease.
    E.g. 0.02 = 2% annual increase, -0.01 = 1% annual decrease.
    """

    wholesale_annual_pct: float = Field(
        description="Wholesale price annual change (market-driven, most volatile)"
    )
    network_annual_pct: float = Field(
        description="Network charges annual change (ARERA-regulated, stable)"
    )
    system_charges_annual_pct: float = Field(
        description="System charges annual change (declining as Conto Energia expires)"
    )
    taxes_annual_pct: float = Field(
        description="Tax rates annual change (legislative, rarely changes)"
    )
    retail_margin_annual_pct: float = Field(
        description="Retail margin annual change (competitive pressure)"
    )


class PriceScenario(BaseModel):
    """Named price scenario with per-component escalation over N years.

    Each scenario represents a plausible trajectory for electricity prices,
    allowing the solar eval engine to compute NPV/IRR under different assumptions.
    """

    name: str = Field(description="Scenario name, e.g. 'Base', 'High Energy'")
    description: str = Field(description="Human-readable scenario description")
    base_year: int = Field(description="Starting year for projection")
    base_components: ElectricityPriceComponents
    escalation: ComponentEscalationRates
    years: int = Field(default=20, ge=1, le=30, description="Projection horizon")

    def project(self) -> list[ElectricityPriceComponents]:
        """Generate year-by-year price components for the projection horizon.

        Returns a list of length `years + 1` (base year at index 0, final year at index `years`).
        Each component is escalated independently at its own annual rate.
        """
        result = [self.base_components]
        current = self.base_components

        for _ in range(self.years):
            current = ElectricityPriceComponents(
                wholesale_eur_kwh=round(
                    current.wholesale_eur_kwh
                    * (1 + self.escalation.wholesale_annual_pct),
                    6,
                ),
                network_eur_kwh=round(
                    current.network_eur_kwh
                    * (1 + self.escalation.network_annual_pct),
                    6,
                ),
                system_charges_eur_kwh=round(
                    max(
                        0.0,
                        current.system_charges_eur_kwh
                        * (1 + self.escalation.system_charges_annual_pct),
                    ),
                    6,
                ),
                taxes_eur_kwh=round(
                    current.taxes_eur_kwh
                    * (1 + self.escalation.taxes_annual_pct),
                    6,
                ),
                retail_margin_eur_kwh=round(
                    current.retail_margin_eur_kwh
                    * (1 + self.escalation.retail_margin_annual_pct),
                    6,
                ),
            )
            result.append(current)

        return result


class MonteCarloConfig(BaseModel):
    """Configuration for Monte Carlo wholesale price simulation."""

    n_simulations: int = Field(default=1000, ge=100, le=10000)
    seed: int | None = Field(default=None, description="Random seed for reproducibility")
    theta: float = Field(default=0.5, description="OU mean reversion speed (1/yr)")
    sigma: float = Field(default=0.30, description="OU annual volatility (fraction)")
    regulated_sigma: float = Field(
        default=0.005,
        description="Gaussian noise for regulated components (fraction/yr)",
    )


class FanChartYear(BaseModel):
    """Percentile bands for a single year's total price projection."""

    year: int
    p10_eur_kwh: float = Field(description="10th percentile (optimistic)")
    p25_eur_kwh: float = Field(description="25th percentile")
    p50_eur_kwh: float = Field(description="50th percentile (median)")
    p75_eur_kwh: float = Field(description="75th percentile")
    p90_eur_kwh: float = Field(description="90th percentile (pessimistic)")


class PriceProjectionResult(BaseModel):
    """Multi-scenario price projection with uncertainty bands.

    Contains the full scenario projections and derived fan chart data
    for visualization (P10/P25/P50/P75/P90 bands per year).
    """

    scenarios: list[PriceScenario]
    fan_chart: list[FanChartYear] = Field(
        description="Per-year percentile bands across all scenarios"
    )
    base_year: int
    projection_years: int

    @field_validator("fan_chart")
    @classmethod
    def check_fan_chart_ordering(cls, v: list[FanChartYear]) -> list[FanChartYear]:
        """Ensure percentiles are ordered: P10 <= P25 <= P50 <= P75 <= P90."""
        for entry in v:
            if not (
                entry.p10_eur_kwh
                <= entry.p25_eur_kwh
                <= entry.p50_eur_kwh
                <= entry.p75_eur_kwh
                <= entry.p90_eur_kwh
            ):
                raise ValueError(
                    f"Year {entry.year}: percentiles not ordered "
                    f"(P10={entry.p10_eur_kwh}, P25={entry.p25_eur_kwh}, "
                    f"P50={entry.p50_eur_kwh}, P75={entry.p75_eur_kwh}, "
                    f"P90={entry.p90_eur_kwh})"
                )
        return v


# ---------------------------------------------------------------------------
# Italian defaults (ARERA Q1 2026, domestico residente, 2700 kWh/yr, 3 kW)
# Values validated March 2026 against ARERA / GSE / D.Lgs. 43/2025 (DEC-022 in docs/decisions.md)
# ---------------------------------------------------------------------------

ITALIAN_DEFAULTS_2025 = ElectricityPriceComponents(
    wholesale_eur_kwh=0.137,  # ~49% — PUN avg + dispatching (ARERA Q1 2026)
    network_eur_kwh=0.062,  # ~22% — trasporto + gestione contatore (ARERA Q1 2026)
    system_charges_eur_kwh=0.030,  # ~11% — ASOS + ARIM (ARERA Q1 2026)
    taxes_eur_kwh=0.028,  # ~10% — accise (0.0227) + addizionali (0.0114), reduced Q1 2026
    retail_margin_eur_kwh=0.022,  # ~8%  — supplier markup
)
# total = 0.279 EUR/kWh pre-IVA


def build_default_scenarios(
    base_year: int = 2025,
    years: int = 20,
    base_components: ElectricityPriceComponents | None = None,
) -> list[PriceScenario]:
    """Build three standard price scenarios with Italian defaults.

    Returns [base, high_energy, low_energy] scenarios.
    """
    base = base_components or ITALIAN_DEFAULTS_2025

    return [
        PriceScenario(
            name="Base",
            description=(
                "Central scenario: moderate wholesale growth, stable regulated "
                "components, gradual decline of system charges as Conto Energia "
                "obligations expire."
            ),
            base_year=base_year,
            base_components=base,
            escalation=ComponentEscalationRates(
                wholesale_annual_pct=0.02,
                network_annual_pct=0.015,
                system_charges_annual_pct=-0.01,
                taxes_annual_pct=0.01,
                retail_margin_annual_pct=0.005,
            ),
            years=years,
        ),
        PriceScenario(
            name="High Energy",
            description=(
                "Pessimistic scenario: elevated wholesale prices driven by gas "
                "dependency and geopolitical risk, faster network investment, "
                "stable system charges."
            ),
            base_year=base_year,
            base_components=base,
            escalation=ComponentEscalationRates(
                wholesale_annual_pct=0.04,
                network_annual_pct=0.02,
                system_charges_annual_pct=0.005,
                taxes_annual_pct=0.015,
                retail_margin_annual_pct=0.01,
            ),
            years=years,
        ),
        PriceScenario(
            name="Low Energy",
            description=(
                "Optimistic scenario: accelerated renewables penetration drives "
                "down wholesale prices, system charges decline faster, competitive "
                "retail market compresses margins."
            ),
            base_year=base_year,
            base_components=base,
            escalation=ComponentEscalationRates(
                wholesale_annual_pct=0.005,
                network_annual_pct=0.01,
                system_charges_annual_pct=-0.03,
                taxes_annual_pct=0.005,
                retail_margin_annual_pct=-0.005,
            ),
            years=years,
        ),
    ]


def build_fan_chart(scenarios: list[PriceScenario]) -> list[FanChartYear]:
    """Build fan chart data from a set of price scenarios.

    For each projection year, collects all scenario totals, sorts them,
    and interpolates to produce P10/P25/P50/P75/P90 bands.

    Requires all scenarios to share the same base_year and years.
    """
    if not scenarios:
        return []

    base_year = scenarios[0].base_year
    years = scenarios[0].years

    # Project all scenarios
    projections = [s.project() for s in scenarios]

    fan_chart: list[FanChartYear] = []
    for year_idx in range(years + 1):
        totals = sorted(p[year_idx].total_eur_kwh for p in projections)
        n = len(totals)

        fan_chart.append(
            FanChartYear(
                year=base_year + year_idx,
                p10_eur_kwh=round(_percentile(totals, n, 0.10), 6),
                p25_eur_kwh=round(_percentile(totals, n, 0.25), 6),
                p50_eur_kwh=round(_percentile(totals, n, 0.50), 6),
                p75_eur_kwh=round(_percentile(totals, n, 0.75), 6),
                p90_eur_kwh=round(_percentile(totals, n, 0.90), 6),
            )
        )

    return fan_chart


def build_projection(
    scenarios: list[PriceScenario] | None = None,
    base_year: int = 2025,
    years: int = 20,
) -> PriceProjectionResult:
    """Build a complete price projection result with fan chart.

    If no scenarios provided, uses the three default Italian scenarios.
    """
    if scenarios is None:
        scenarios = build_default_scenarios(base_year=base_year, years=years)

    fan_chart = build_fan_chart(scenarios)

    return PriceProjectionResult(
        scenarios=scenarios,
        fan_chart=fan_chart,
        base_year=base_year,
        projection_years=years,
    )


def _percentile(sorted_values: list[float], n: int, p: float) -> float:
    """Linear interpolation percentile on a sorted list."""
    if n == 1:
        return sorted_values[0]

    # Fractional index (0-based)
    idx = p * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo])
