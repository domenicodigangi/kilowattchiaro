"""20-year electricity price projection engine (deterministic + Monte Carlo).

Projects each cost component independently using Italian-market-calibrated
escalation rules. Non-wholesale components follow shared deterministic rules;
only the wholesale component drives scenario differentiation.

Five named scenarios cover the plausible Italian market trajectory space:
  1. ENTSO-E Reference — moderate electrification (TYNDP 2024)
  2. High Electrification — MASE demand-doubling by 2050
  3. Abundant Renewables — PNIEC 79.2 GW solar, cannibalization effect
  4. Status Quo — flat real prices (conservative baseline)
  5. Energy Crisis — elevated average accounting for periodic spikes

Component escalation rules (shared across all scenarios):
  - Network: CPI + 0.5% real (ARERA historical trend)
  - System charges: CPI - 2% real (Conto Energia rolloff by ~2037)
  - Taxes: CPI − 1% real (slight real decline per ECCO Climate / ARERA trends)
  - Retail margin: CPI (flat real)

Monte Carlo mode:
  - Wholesale modeled as geometric Ornstein-Uhlenbeck (mean-reverting, no negatives)
  - Regulated components: deterministic + small Gaussian noise
  - Output: P10/P25/P50/P75/P90 fan chart from N stochastic simulations
"""

import math
import random as _random_module

from .models.price_components import (
    ComponentEscalationRates,
    ElectricityPriceComponents,
    FanChartYear,
    MonteCarloConfig,
    PriceProjectionResult,
    PriceScenario,
    ITALIAN_DEFAULTS_2025,
    build_fan_chart,
    _percentile,
)
from .models.solar_eval import PriceScenarioName

DEFAULT_CPI = 0.02

# (name, description, wholesale real spread over CPI)
_SCENARIO_DEFS: list[tuple[str, str, float]] = [
    (
        "ENTSO-E Reference",
        "Moderate electrification path per TYNDP 2024. "
        "Wholesale grows at CPI + 1% real.",
        0.01,
    ),
    (
        "High Electrification",
        "MASE demand-doubling by 2050, tight supply margins. "
        "Wholesale grows at CPI + 2% real.",
        0.02,
    ),
    (
        "Abundant Renewables",
        "PNIEC 79.2 GW solar by 2030, cannibalization drives "
        "wholesale down. Wholesale grows at CPI - 2% real.",
        -0.02,
    ),
    (
        "Status Quo",
        "Conservative baseline — flat real prices, no structural "
        "market change. Wholesale grows at CPI.",
        0.00,
    ),
    (
        "Energy Crisis",
        "Elevated average accounting for periodic 2022-like price "
        "spikes (~every 8 years). Wholesale grows at CPI + 1.5% real.",
        0.015,
    ),
]

_SCENARIO_ALIAS_MAP: dict[PriceScenarioName, str] = {
    PriceScenarioName.REFERENCE: "ENTSO-E Reference",
    PriceScenarioName.HIGH_ELECTRIFICATION: "High Electrification",
    PriceScenarioName.ABUNDANT_RENEWABLES: "Abundant Renewables",
    PriceScenarioName.STATUS_QUO: "Status Quo",
    PriceScenarioName.ENERGY_CRISIS: "Energy Crisis",
}


def _build_escalation(
    wholesale_real_spread: float, cpi: float
) -> ComponentEscalationRates:
    """Build escalation rates — only wholesale varies across scenarios."""
    return ComponentEscalationRates(
        wholesale_annual_pct=cpi + wholesale_real_spread,
        network_annual_pct=cpi + 0.005,  # CPI + 0.5% real
        system_charges_annual_pct=cpi - 0.02,  # CPI - 2% real (Conto Energia rolloff)
        taxes_annual_pct=cpi - 0.01,  # CPI − 1% real (slight real decline per ECCO/ARERA)
        retail_margin_annual_pct=cpi,  # flat real
    )


def get_price_projection_scenario(
    scenario_name: PriceScenarioName = PriceScenarioName.REFERENCE,
    *,
    base: ElectricityPriceComponents | None = None,
    years: int = 20,
    cpi: float = DEFAULT_CPI,
    base_year: int = 2025,
) -> PriceScenario:
    """Return a single deterministic scenario for the named solar-eval alias."""
    scenario_lookup = _SCENARIO_ALIAS_MAP[scenario_name]
    result = project_prices(base=base, years=years, cpi=cpi, base_year=base_year)
    for scenario in result.scenarios:
        if scenario.name == scenario_lookup:
            return scenario

    raise ValueError(
        f"Scenario alias '{scenario_name.value}' did not resolve to '{scenario_lookup}'"
    )


def simulate_monte_carlo(
    base: ElectricityPriceComponents | None = None,
    scenario: str = "ENTSO-E Reference",
    n_simulations: int = 1000,
    years: int = 20,
    cpi: float = DEFAULT_CPI,
    base_year: int = 2025,
    config: MonteCarloConfig | None = None,
) -> PriceProjectionResult:
    """Run Monte Carlo simulation for wholesale price uncertainty.

    Wholesale component follows a geometric Ornstein-Uhlenbeck process
    (mean-reverting, prevents negative prices). Regulated components use
    deterministic escalation with small Gaussian noise.

    Args:
        base: Starting price components. Defaults to ITALIAN_DEFAULTS_2025.
        scenario: Name of the scenario whose deterministic trajectory sets
            the OU mean-reversion target. Must match a name in _SCENARIO_DEFS.
        n_simulations: Number of stochastic paths (default 1000).
        years: Projection horizon (default 20).
        cpi: Annual CPI assumption (default 0.02).
        base_year: First year of the projection.
        config: Monte Carlo parameters. Defaults to MonteCarloConfig().

    Returns:
        PriceProjectionResult with the matched deterministic scenario
        and a stochastic fan chart.
    """
    base = base or ITALIAN_DEFAULTS_2025
    config = config or MonteCarloConfig()
    rng = _random_module.Random(config.seed)

    # Find the matching scenario definition
    scenario_def = None
    for name, desc, spread in _SCENARIO_DEFS:
        if name == scenario:
            scenario_def = (name, desc, spread)
            break
    if scenario_def is None:
        raise ValueError(
            f"Unknown scenario '{scenario}'. "
            f"Available: {[n for n, _, _ in _SCENARIO_DEFS]}"
        )

    s_name, s_desc, s_spread = scenario_def
    escalation = _build_escalation(s_spread, cpi)

    # Build the deterministic scenario (for return value + mean-reversion target)
    det_scenario = PriceScenario(
        name=s_name,
        description=s_desc,
        base_year=base_year,
        base_components=base,
        escalation=escalation,
        years=years,
    )
    det_projection = det_scenario.project()

    # Extract deterministic wholesale targets and regulated component values
    det_wholesale = [c.wholesale_eur_kwh for c in det_projection]
    det_network = [c.network_eur_kwh for c in det_projection]
    det_system = [c.system_charges_eur_kwh for c in det_projection]
    det_taxes = [c.taxes_eur_kwh for c in det_projection]
    det_margin = [c.retail_margin_eur_kwh for c in det_projection]

    theta = config.theta
    sigma = config.sigma
    reg_sigma = config.regulated_sigma
    dt = 1.0 / 12  # monthly sub-steps
    sqrt_dt = math.sqrt(dt)

    # Cap wholesale at 5x base to prevent runaway paths
    wholesale_cap = base.wholesale_eur_kwh * 5.0
    wholesale_floor = 0.01

    # Simulate: collect total prices per year per simulation
    # totals_by_year[year_idx] = list of total prices across all simulations
    totals_by_year: list[list[float]] = [[] for _ in range(years + 1)]

    for _ in range(n_simulations):
        wholesale = base.wholesale_eur_kwh

        for year_idx in range(years + 1):
            # Regulated components with small noise (except year 0 = base)
            if year_idx == 0:
                net = det_network[0]
                sys_c = det_system[0]
                tax = det_taxes[0]
                margin = det_margin[0]
            else:
                net = det_network[year_idx] * (1 + rng.gauss(0, reg_sigma))
                sys_c = max(0.0, det_system[year_idx] * (1 + rng.gauss(0, reg_sigma)))
                tax = det_taxes[year_idx] * (1 + rng.gauss(0, reg_sigma))
                margin = det_margin[year_idx] * (1 + rng.gauss(0, reg_sigma))

            total = wholesale + net + sys_c + tax + margin
            totals_by_year[year_idx].append(total)

            # Evolve wholesale through 12 monthly sub-steps to next year
            if year_idx < years:
                mu_t = det_wholesale[year_idx + 1]
                for _ in range(12):
                    z = rng.gauss(0, 1)
                    # Geometric OU: work in log-space to prevent negatives
                    log_p = math.log(wholesale)
                    log_mu = math.log(max(mu_t, wholesale_floor))
                    log_p += theta * (log_mu - log_p) * dt + sigma * sqrt_dt * z
                    wholesale = math.exp(log_p)
                wholesale = max(wholesale_floor, min(wholesale, wholesale_cap))

    # Compute percentile bands
    fan_chart: list[FanChartYear] = []
    for year_idx in range(years + 1):
        values = sorted(totals_by_year[year_idx])
        n = len(values)
        fan_chart.append(
            FanChartYear(
                year=base_year + year_idx,
                p10_eur_kwh=round(_percentile(values, n, 0.10), 6),
                p25_eur_kwh=round(_percentile(values, n, 0.25), 6),
                p50_eur_kwh=round(_percentile(values, n, 0.50), 6),
                p75_eur_kwh=round(_percentile(values, n, 0.75), 6),
                p90_eur_kwh=round(_percentile(values, n, 0.90), 6),
            )
        )

    return PriceProjectionResult(
        scenarios=[det_scenario],
        fan_chart=fan_chart,
        base_year=base_year,
        projection_years=years,
    )


def project_prices(
    base: ElectricityPriceComponents | None = None,
    years: int = 20,
    cpi: float = DEFAULT_CPI,
    base_year: int = 2025,
    monte_carlo: MonteCarloConfig | None = None,
) -> PriceProjectionResult:
    """Build 5-scenario Italian electricity price projection.

    Args:
        base: Starting price components. Defaults to ITALIAN_DEFAULTS_2025.
        years: Projection horizon (default 20).
        cpi: Annual CPI assumption (default 0.02 = 2%).
        base_year: First year of the projection.
        monte_carlo: If provided, generates a stochastic fan chart using
            Monte Carlo simulation (OU process) instead of deterministic
            interpolation. The 5 scenario lines remain unchanged.

    Returns:
        PriceProjectionResult with 5 scenarios and fan chart.
    """
    base = base or ITALIAN_DEFAULTS_2025

    scenarios = [
        PriceScenario(
            name=name,
            description=desc,
            base_year=base_year,
            base_components=base,
            escalation=_build_escalation(spread, cpi),
            years=years,
        )
        for name, desc, spread in _SCENARIO_DEFS
    ]

    if monte_carlo is not None:
        mc_result = simulate_monte_carlo(
            base=base,
            scenario="ENTSO-E Reference",
            n_simulations=monte_carlo.n_simulations,
            years=years,
            cpi=cpi,
            base_year=base_year,
            config=monte_carlo,
        )
        fan_chart = mc_result.fan_chart
    else:
        fan_chart = build_fan_chart(scenarios)

    return PriceProjectionResult(
        scenarios=scenarios,
        fan_chart=fan_chart,
        base_year=base_year,
        projection_years=years,
    )
