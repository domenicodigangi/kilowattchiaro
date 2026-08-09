"""Walk-forward historical decision-point backtest engine.

Reconstructs what a solar evaluation would have predicted at each historical
decision point, using only information available at that time (no look-ahead
bias). Then computes actual outcomes using the real electricity price trajectory.

Walk-forward backtesting is standard in quantitative finance for validating
models against known outcomes.
"""

from dataclasses import dataclass
from datetime import date

from .models.solar_eval import (
    ActualMetrics,
    ConsumptionProfile,
    HistoricalBacktestResult,
    PredictedMetrics,
    PVGISMonthly,
)
from .incentive_regimes import (
    ExportMechanism,
    IncentiveRegime,
    IncentiveType,
    get_electricity_price_at_year,
    get_export_mechanism_at_date,
    get_pv_cost_at_year,
    get_regime_at_date,
)
from .solar_eval import _estimate_irr, _npv_20yr, _payback_years

# Self-consumption ratios (same as solar_eval.py defaults)
_SC_RATIO = 0.30
_SC_RATIO_BATTERY = 0.70

# Historical battery cost (EUR/kWh) — residential lithium-ion
BATTERY_COST_EUR_PER_KWH: dict[int, float] = {
    2015: 1500,
    2016: 1300,
    2017: 1100,
    2018: 900,
    2019: 800,
    2020: 700,
    2021: 650,
    2022: 700,
    2023: 600,
    2024: 550,
    2025: 500,
}

# SSP export value (approximate, EUR/kWh) — net metering credit
_SSP_EXPORT_VALUE = 0.10
# RID export price (GSE PMG)
_RID_EXPORT_VALUE = 0.046

# Present year for "actual outcome" calculations
_PRESENT_YEAR = 2025


# ---------------------------------------------------------------------------
# Knowledge-at-time
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeAtTime:
    """Parameters available to a decision-maker at a given date."""

    decision_date: date
    regime: IncentiveRegime | None
    export_mechanism: ExportMechanism
    pv_cost_eur_per_kwp: float
    electricity_price_eur_kwh: float
    expected_price_inflation: float = 0.02
    discount_rate: float = 0.03


def _build_knowledge_at_time(decision_date: date) -> KnowledgeAtTime:
    """Gather only information available at decision_date. No look-ahead."""
    return KnowledgeAtTime(
        decision_date=decision_date,
        regime=get_regime_at_date(decision_date),
        export_mechanism=get_export_mechanism_at_date(decision_date),
        pv_cost_eur_per_kwp=get_pv_cost_at_year(decision_date.year),
        electricity_price_eur_kwh=get_electricity_price_at_year(decision_date.year),
    )


# ---------------------------------------------------------------------------
# Export price helper
# ---------------------------------------------------------------------------


def _export_price(mechanism: ExportMechanism) -> float:
    """Return export revenue EUR/kWh for a given mechanism."""
    if mechanism == ExportMechanism.SSP:
        return _SSP_EXPORT_VALUE
    return _RID_EXPORT_VALUE


# ---------------------------------------------------------------------------
# Predicted metrics (using only knowledge-at-time)
# ---------------------------------------------------------------------------


def _compute_predicted_metrics(
    knowledge: KnowledgeAtTime,
    annual_production_kwh: float,
    annual_consumption_kwh: float,
    desired_kwp: float,
    include_battery: bool,
    battery_kwh: float,
) -> PredictedMetrics:
    """Compute what a decision-maker would have predicted at decision_date."""
    regime = knowledge.regime
    elec_price = knowledge.electricity_price_eur_kwh
    sc_ratio = _SC_RATIO_BATTERY if include_battery else _SC_RATIO
    inflation = knowledge.expected_price_inflation
    discount_rate = knowledge.discount_rate

    # Investment
    pv_investment = desired_kwp * knowledge.pv_cost_eur_per_kwp
    battery_investment = 0.0
    if include_battery:
        bat_year = max(2015, min(knowledge.decision_date.year, 2025))
        bat_cost_per_kwh = BATTERY_COST_EUR_PER_KWH.get(bat_year, 1500)
        battery_investment = battery_kwh * bat_cost_per_kwh
    total_investment = pv_investment + battery_investment

    # Self-consumed and exported energy
    usable = min(annual_production_kwh, annual_consumption_kwh)
    self_consumed = usable * sc_ratio
    exported = annual_production_kwh - self_consumed

    if regime is not None and regime.incentive_type == IncentiveType.FEED_IN_TARIFF:
        return _predict_feed_in_tariff(
            regime, knowledge, total_investment, annual_production_kwh,
            self_consumed, exported, elec_price, inflation, discount_rate,
        )

    # Tax deduction path (Detrazione 50% or Superbonus 110%)
    return _predict_tax_deduction(
        regime, knowledge, total_investment, self_consumed, exported,
        elec_price, inflation, discount_rate,
    )


def _predict_feed_in_tariff(
    regime: IncentiveRegime,
    knowledge: KnowledgeAtTime,
    investment: float,
    annual_production_kwh: float,
    self_consumed: float,
    exported: float,
    elec_price: float,
    inflation: float,
    discount_rate: float,
) -> PredictedMetrics:
    """Predict metrics under a Conto Energia feed-in tariff regime."""
    if regime.rate_eur_kwh is None:
        raise ValueError(f"FiT regime '{regime.name}' missing rate_eur_kwh")
    fit_rate = regime.rate_eur_kwh

    # CE I: FiT on self-consumed only. CE II-IV: FiT premium on all production.
    # CE V (omnicomprensiva): rate on exports (includes energy value).
    if regime.name == "Conto Energia I":
        # FiT only on self-consumed portion, no export mechanism
        fit_revenue = self_consumed * fit_rate
        export_revenue = 0.0
    elif regime.name == "Conto Energia V":
        # Omnicomprensiva: all-inclusive rate on exported (includes energy value)
        fit_revenue = exported * fit_rate
        export_revenue = 0.0
    else:
        # CE II, III, IV: FiT premium on ALL production + SSP on exports
        fit_revenue = annual_production_kwh * fit_rate
        # SSP credit on exports is ON TOP of the FiT premium
        export_price = _export_price(knowledge.export_mechanism)
        export_revenue = exported * export_price

    # Self-consumption savings (avoided grid purchase)
    sc_savings = self_consumed * elec_price

    # Annual cash flow = FiT revenue + SC savings + export revenue
    # Note: under FiT, no tax deduction (mutually exclusive)
    annual_cf = fit_revenue + sc_savings + export_revenue

    # FiT revenue is contractually fixed for 20 years (Conto Energia).
    # Only self-consumption savings inflate with electricity prices.
    fixed_revenue = fit_revenue + export_revenue

    npv = _npv_fit(investment, sc_savings, fixed_revenue, discount_rate, inflation, years=20)
    payback = _payback_fit(investment, sc_savings, fixed_revenue, inflation)
    irr = _irr_fit(investment, sc_savings, fixed_revenue, inflation, years=20)

    return PredictedMetrics(
        npv_20yr_eur=npv,
        payback_years=payback,
        irr_percent=irr,
        annual_savings_eur=round(annual_cf, 2),
        investment_eur=round(investment, 2),
    )


def _predict_tax_deduction(
    regime: IncentiveRegime | None,
    knowledge: KnowledgeAtTime,
    investment: float,
    self_consumed: float,
    exported: float,
    elec_price: float,
    inflation: float,
    discount_rate: float,
) -> PredictedMetrics:
    """Predict metrics under tax deduction (Detrazione 50% or Superbonus)."""
    # Self-consumption savings
    sc_savings = self_consumed * elec_price
    # Export revenue
    export_price = _export_price(knowledge.export_mechanism)
    export_revenue = exported * export_price

    # Tax deduction
    deduction_pct = 0.0
    max_amount = 96_000.0
    deduction_years = 10
    if regime is not None and regime.deduction_percent is not None:
        deduction_pct = regime.deduction_percent / 100.0
        if regime.max_amount_eur is not None:
            max_amount = regime.max_amount_eur
        deduction_years = regime.duration_years

    detrazione_total = min(investment * deduction_pct, max_amount)
    detrazione_annual = detrazione_total / deduction_years if deduction_years > 0 else 0.0

    # Map to existing helpers: sq pays for all consumption at elec_price,
    # solar pays for grid_import at elec_price (consumption - self_consumed)
    annual_cost_sq_effective = (self_consumed / _SC_RATIO if _SC_RATIO > 0 else 0) * elec_price
    annual_cost_solar_effective = (annual_cost_sq_effective - sc_savings)

    payback = _payback_years(
        investment, annual_cost_sq_effective, annual_cost_solar_effective,
        export_revenue, detrazione_annual, deduction_years, inflation,
    )
    npv = _npv_20yr(
        investment, annual_cost_sq_effective, annual_cost_solar_effective,
        export_revenue, detrazione_annual, deduction_years, discount_rate, inflation,
    )
    irr = _estimate_irr(
        investment, annual_cost_sq_effective, annual_cost_solar_effective,
        export_revenue, detrazione_annual, deduction_years, inflation=inflation,
    )
    annual_savings = round(sc_savings + export_revenue + detrazione_annual, 2)

    return PredictedMetrics(
        npv_20yr_eur=npv,
        payback_years=payback,
        irr_percent=irr,
        annual_savings_eur=annual_savings,
        investment_eur=round(investment, 2),
    )


# ---------------------------------------------------------------------------
# FiT-specific financial helpers
# ---------------------------------------------------------------------------


def _npv_fit(
    investment: float,
    sc_savings_base: float,
    fixed_revenue: float,
    discount_rate: float,
    inflation: float,
    years: int = 20,
) -> float:
    """Compute 20-year NPV for a FiT regime: fixed FiT revenue + inflating SC savings."""
    npv = -investment
    for year in range(1, years + 1):
        inflated_sc_savings = sc_savings_base * (1 + inflation) ** year
        cf = inflated_sc_savings + fixed_revenue
        npv += cf / ((1 + discount_rate) ** year)
    return round(npv, 2)


def _payback_fit(
    investment: float,
    sc_savings_base: float,
    fixed_revenue: float,
    inflation: float,
) -> float | None:
    """Compute simple payback years for FiT regime (annual FiT revenue + SC savings vs investment)."""
    if investment <= 0:
        return 0.0
    cumulative = 0.0
    for year in range(1, 26):
        inflated_sc_savings = sc_savings_base * (1 + inflation) ** year
        cf = inflated_sc_savings + fixed_revenue
        cumulative += cf
        if cumulative >= investment:
            overshoot = cumulative - investment
            fraction = 1.0 - (overshoot / cf) if cf > 0 else 0.0
            return round(year - 1 + fraction, 1)
    return None


def _irr_fit(
    investment: float,
    sc_savings_base: float,
    fixed_revenue: float,
    inflation: float,
    years: int = 20,
) -> float | None:
    """Estimate IRR for FiT regime using bisection on 20-year cash flows."""
    if investment <= 0:
        return None

    def npv_at_rate(r: float) -> float:
        npv = -investment
        for year in range(1, years + 1):
            inflated_sc_savings = sc_savings_base * (1 + inflation) ** year
            cf = inflated_sc_savings + fixed_revenue
            npv += cf / ((1 + r) ** year)
        return npv

    low, high = -0.10, 1.0
    if npv_at_rate(high) > 0:
        return round(high * 100, 1)
    if npv_at_rate(low) < 0:
        return None

    for _ in range(100):
        mid = (low + high) / 2
        if npv_at_rate(mid) > 0:
            low = mid
        else:
            high = mid
        if abs(high - low) < 0.0001:
            break

    return round(((low + high) / 2) * 100, 1)


# ---------------------------------------------------------------------------
# Actual metrics (using real price trajectory)
# ---------------------------------------------------------------------------


def _build_actual_cashflows(
    knowledge: KnowledgeAtTime,
    investment: float,
    annual_production_kwh: float,
    annual_consumption_kwh: float,
    include_battery: bool,
    years_elapsed: int,
) -> list[float]:
    """Build year-by-year actual cash flows using real electricity prices."""
    decision_year = knowledge.decision_date.year
    regime = knowledge.regime
    sc_ratio = _SC_RATIO_BATTERY if include_battery else _SC_RATIO

    usable = min(annual_production_kwh, annual_consumption_kwh)
    self_consumed = usable * sc_ratio
    exported = annual_production_kwh - self_consumed

    # FiT revenue is locked for the regime duration
    fit_rate = 0.0
    fit_duration = 0
    is_fit = (
        regime is not None
        and regime.incentive_type == IncentiveType.FEED_IN_TARIFF
    )
    if is_fit:
        if regime is None or regime.rate_eur_kwh is None:
            raise ValueError("FiT regime missing or has no rate_eur_kwh")
        fit_rate = regime.rate_eur_kwh
        fit_duration = regime.duration_years

    # Tax deduction params
    detrazione_annual = 0.0
    deduction_years = 0
    if regime is not None and regime.deduction_percent is not None:
        deduction_pct = regime.deduction_percent / 100.0
        max_amount = regime.max_amount_eur or 96_000.0
        deduction_years = regime.duration_years
        detrazione_total = min(investment * deduction_pct, max_amount)
        detrazione_annual = detrazione_total / deduction_years if deduction_years > 0 else 0.0

    cash_flows: list[float] = []
    for i in range(1, years_elapsed + 1):
        year = decision_year + i
        actual_price = get_electricity_price_at_year(year)

        # Self-consumption savings at actual price
        sc_savings = self_consumed * actual_price

        # FiT revenue (locked rate, limited to duration)
        fit_rev = 0.0
        if is_fit and i <= fit_duration:
            if regime.name == "Conto Energia I":  # type: ignore[union-attr]
                fit_rev = self_consumed * fit_rate
            elif regime.name == "Conto Energia V":  # type: ignore[union-attr]
                fit_rev = exported * fit_rate
            else:
                fit_rev = annual_production_kwh * fit_rate

        # Export revenue
        export_rev = 0.0
        if not is_fit:
            export_mech = get_export_mechanism_at_date(date(year, 1, 1))
            export_rev = exported * _export_price(export_mech)
        elif is_fit and regime is not None and i <= fit_duration:
            # CE II, III, IV allowed SSP export credits on top of FiT premium.
            # CE I had no SSP; CE V omnicomprensiva includes energy value.
            if regime.name in ("Conto Energia II", "Conto Energia III", "Conto Energia IV"):
                export_mech = get_export_mechanism_at_date(date(year, 1, 1))
                export_rev = exported * _export_price(export_mech)

        # Tax deduction
        detr = detrazione_annual if i <= deduction_years else 0.0

        cash_flows.append(sc_savings + fit_rev + export_rev + detr)

    return cash_flows


def _irr_from_cashflows(
    investment: float,
    cash_flows: list[float],
) -> float | None:
    """Estimate IRR via bisection from a precomputed cash flow series."""
    if investment <= 0 or len(cash_flows) < 2:
        return None

    def npv_at_rate(r: float) -> float:
        npv = -investment
        for i, cf in enumerate(cash_flows, 1):
            npv += cf / ((1 + r) ** i)
        return npv

    low, high = -0.10, 1.0
    if npv_at_rate(high) > 0:
        return round(high * 100, 1)
    if npv_at_rate(low) < 0:
        return None

    for _ in range(100):
        mid = (low + high) / 2
        if npv_at_rate(mid) > 0:
            low = mid
        else:
            high = mid
        if abs(high - low) < 0.0001:
            break

    return round(((low + high) / 2) * 100, 1)


def _project_npv_to_20yr(
    knowledge: KnowledgeAtTime,
    npv_to_date: float,
    cash_flows: list[float],
    years_elapsed: int,
    discount_rate: float,
    annual_production_kwh: float,
    annual_consumption_kwh: float,
    include_battery: bool,
    investment: float,
) -> float | None:
    """Project actual NPV to a full 20-year horizon for fair comparison with predictions.

    For years beyond the elapsed period, projects future cash flows:
    - Tax deduction regimes: continue last-year pattern with 2% inflation on savings,
      stopping tax deduction after year 10
    - FiT regimes: continue fixed FiT revenue + self-consumption savings with 2% inflation,
      FiT ending at year 20
    Uses the same discount rate as predicted NPV.
    """
    if years_elapsed >= 20:
        return round(npv_to_date, 2)
    if years_elapsed == 0:
        return None

    regime = knowledge.regime
    decision_year = knowledge.decision_date.year
    sc_ratio = _SC_RATIO_BATTERY if include_battery else _SC_RATIO
    inflation = 0.02  # standard projection inflation

    usable = min(annual_production_kwh, annual_consumption_kwh)
    self_consumed = usable * sc_ratio
    exported = annual_production_kwh - self_consumed

    is_fit = (
        regime is not None
        and regime.incentive_type == IncentiveType.FEED_IN_TARIFF
    )

    # Get base values from the last actual year for projection
    last_actual_price = get_electricity_price_at_year(
        min(decision_year + years_elapsed, _PRESENT_YEAR)
    )

    projected_npv = npv_to_date
    for i in range(years_elapsed + 1, 21):
        # Years since last actual year (for inflation projection)
        years_beyond = i - years_elapsed

        if is_fit:
            # FiT revenue: fixed rate, no inflation, ends at regime duration
            fit_rate = regime.rate_eur_kwh if regime and regime.rate_eur_kwh else 0.0
            fit_duration = regime.duration_years if regime else 0
            fit_rev = 0.0
            if i <= fit_duration:
                if regime.name == "Conto Energia I":  # type: ignore[union-attr]
                    fit_rev = self_consumed * fit_rate
                elif regime.name == "Conto Energia V":  # type: ignore[union-attr]
                    fit_rev = exported * fit_rate
                else:
                    fit_rev = annual_production_kwh * fit_rate

            # SSP export for CE II/III/IV during FiT period
            export_rev = 0.0
            if i <= fit_duration and regime is not None and regime.name in (
                "Conto Energia II", "Conto Energia III", "Conto Energia IV"
            ):
                export_rev = exported * _SSP_EXPORT_VALUE

            # Self-consumption savings: inflate from last actual price
            sc_savings = self_consumed * last_actual_price * (1 + inflation) ** years_beyond

            cf = fit_rev + export_rev + sc_savings
        else:
            # Tax deduction / Superbonus path
            # Self-consumption savings: inflate from last actual price
            sc_savings = self_consumed * last_actual_price * (1 + inflation) ** years_beyond

            # Export revenue: inflate modestly
            export_rev = exported * _SSP_EXPORT_VALUE * (1 + inflation) ** years_beyond

            # Tax deduction: stops after year 10 from decision
            detr = 0.0
            if regime is not None and regime.deduction_percent is not None and i <= regime.duration_years:
                deduction_pct = regime.deduction_percent / 100.0
                max_amount = regime.max_amount_eur or 96_000.0
                detrazione_total = min(investment * deduction_pct, max_amount)
                detr = detrazione_total / regime.duration_years

            cf = sc_savings + export_rev + detr

        projected_npv += cf / ((1 + discount_rate) ** i)

    return round(projected_npv, 2)


def _compute_actual_metrics(
    knowledge: KnowledgeAtTime,
    investment: float,
    annual_production_kwh: float,
    annual_consumption_kwh: float,
    desired_kwp: float,
    include_battery: bool,
) -> ActualMetrics:
    """Compute actual NPV, payback, IRR, and avg savings from real cash flow history."""
    decision_year = knowledge.decision_date.year
    discount_rate = knowledge.discount_rate
    years_elapsed = min(20, _PRESENT_YEAR - decision_year)

    cash_flows = _build_actual_cashflows(
        knowledge, investment, annual_production_kwh,
        annual_consumption_kwh, include_battery, years_elapsed,
    )

    # Derive NPV, payback, and total savings from the cash flow series
    npv = -investment
    cumulative = 0.0
    payback: float | None = None
    total_savings = 0.0

    for i, annual_cf in enumerate(cash_flows, 1):
        total_savings += annual_cf
        npv += annual_cf / ((1 + discount_rate) ** i)
        cumulative += annual_cf

        if payback is None and cumulative >= investment:
            overshoot = cumulative - investment
            fraction = 1.0 - (overshoot / annual_cf) if annual_cf > 0 else 0.0
            payback = round(i - 1 + fraction, 1)

    avg_savings = total_savings / years_elapsed if years_elapsed > 0 else 0.0
    irr = _irr_from_cashflows(investment, cash_flows)

    # Project actual cash flows to 20-year horizon for fair comparison
    projected_npv_20yr = _project_npv_to_20yr(
        knowledge, npv, cash_flows, years_elapsed, discount_rate,
        annual_production_kwh, annual_consumption_kwh, include_battery, investment,
    )

    return ActualMetrics(
        npv_to_date_eur=round(npv, 2),
        projected_npv_20yr=projected_npv_20yr,
        payback_years=payback,
        irr_percent=irr,
        annual_savings_avg_eur=round(avg_savings, 2),
        years_elapsed=years_elapsed,
    )


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def run_historical_backtest(
    decision_date: date,
    monthly_production: list[PVGISMonthly],
    consumption: ConsumptionProfile,
    desired_kwp: float,
    include_battery: bool = False,
    battery_kwh: float = 10.0,
) -> HistoricalBacktestResult:
    """Run a single historical decision-point backtest.

    Args:
        decision_date: The date a homeowner considers installing solar.
        monthly_production: PVGIS monthly production (time-invariant TMY).
        consumption: Annual consumption profile.
        desired_kwp: System size in kWp.
        include_battery: Whether to include battery in the scenario.
        battery_kwh: Battery capacity (only used if include_battery=True).

    Returns:
        HistoricalBacktestResult comparing predicted vs actual metrics.
    """
    knowledge = _build_knowledge_at_time(decision_date)
    annual_production = sum(m.e_m for m in monthly_production)

    # Investment
    pv_investment = desired_kwp * knowledge.pv_cost_eur_per_kwp
    battery_investment = 0.0
    if include_battery:
        bat_year = max(2015, min(decision_date.year, 2025))
        bat_cost_per_kwh = BATTERY_COST_EUR_PER_KWH.get(bat_year, 1500)
        battery_investment = battery_kwh * bat_cost_per_kwh
    total_investment = pv_investment + battery_investment

    predicted = _compute_predicted_metrics(
        knowledge, annual_production, consumption.annual_kwh,
        desired_kwp, include_battery, battery_kwh,
    )

    actual = _compute_actual_metrics(
        knowledge, total_investment, annual_production,
        consumption.annual_kwh, desired_kwp, include_battery,
    )

    # Prediction error
    npv_error = actual.npv_to_date_eur - predicted.npv_20yr_eur
    npv_error_pct = (
        round(npv_error / abs(predicted.npv_20yr_eur) * 100, 1)
        if predicted.npv_20yr_eur != 0
        else None
    )

    regime = knowledge.regime
    regime_name = regime.name if regime else "None"
    regime_type = regime.incentive_type.value if regime else "none"

    return HistoricalBacktestResult(
        decision_date=decision_date,
        regime_name=regime_name,
        regime_type=regime_type,
        system_kwp=desired_kwp,
        investment_eur=round(total_investment, 2),
        pv_cost_eur_per_kwp=knowledge.pv_cost_eur_per_kwp,
        electricity_price_at_decision=knowledge.electricity_price_eur_kwh,
        predicted=predicted,
        actual=actual,
        npv_prediction_error_eur=round(npv_error, 2),
        npv_prediction_error_pct=npv_error_pct,
        include_battery=include_battery,
    )


# ---------------------------------------------------------------------------
# Standard decision points
# ---------------------------------------------------------------------------

STANDARD_DECISION_DATES: list[date] = [
    date(2008, 7, 1),   # CE II era, high costs, generous FiT
    date(2010, 7, 1),   # CE II/III transition, costs dropping fast
    date(2012, 1, 1),   # CE IV, still profitable FiT
    date(2013, 10, 1),  # Post-CE, transition to Detrazione 50%
    date(2015, 7, 1),   # Detrazione 50%, moderate prices
    date(2018, 7, 1),   # Detrazione 50%, low PV costs, battery emerging
    date(2020, 10, 1),  # Superbonus 110%, ultra-low effective cost
    date(2023, 7, 1),   # Detrazione 50%, post-crisis elevated prices
    date(2025, 1, 1),   # Current: Detrazione 50% + RID (SSP closing)
]


def run_standard_backtest(
    monthly_production: list[PVGISMonthly],
    consumption: ConsumptionProfile | None = None,
    desired_kwp: float = 6.0,
) -> list[HistoricalBacktestResult]:
    """Run backtest for all standard decision points.

    Default: Rome, 6kWp, 3000 kWh/yr (reference scenario from issue).
    """
    if consumption is None:
        consumption = ConsumptionProfile(
            annual_kwh=3000, f1_kwh=1200, f2_kwh=900, f3_kwh=900,
        )

    return [
        run_historical_backtest(
            decision_date=d,
            monthly_production=monthly_production,
            consumption=consumption,
            desired_kwp=desired_kwp,
        )
        for d in STANDARD_DECISION_DATES
    ]
