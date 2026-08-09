"""Validation framework with accuracy metrics for the solar eval engine.

Runs the historical backtest across all standard decision points and computes
aggregate accuracy metrics: NPV error, binary recommendation accuracy, payback
error, IRR error, and Spearman rank correlation (direction accuracy).

This is a developer/quality tool, not user-facing.
"""

from statistics import median

from .models.solar_eval import (
    AggregateMetrics,
    ConsumptionProfile,
    DecisionPointValidation,
    PVGISMonthly,
    ValidationReport,
)
from .historical_backtest import run_standard_backtest

# Decision points with fewer than this many years elapsed are excluded
# from aggregate metrics (apples-to-oranges: predicted 20yr vs partial actual).
_MIN_YEARS_FOR_AGGREGATES = 5
_WELL_MODELED_ERROR_THRESHOLD_PCT = 20.0


def _spearman_rank_correlation(
    predicted: list[float], actual: list[float],
) -> float | None:
    """Compute Spearman rank correlation without scipy dependency."""
    if len(predicted) != len(actual):
        raise ValueError(
            f"predicted and actual must have equal length, got {len(predicted)} and {len(actual)}"
        )
    n = len(predicted)
    if n < 3:
        return None

    def _rank(values: list[float]) -> list[float]:
        sorted_indices = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and values[sorted_indices[j + 1]] == values[sorted_indices[j]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                ranks[sorted_indices[k]] = avg_rank
            i = j + 1
        return ranks

    r_pred = _rank(predicted)
    r_actual = _rank(actual)
    d_sq = sum((r_pred[i] - r_actual[i]) ** 2 for i in range(n))
    return round(1 - 6 * d_sq / (n * (n * n - 1)), 4)


def run_validation_suite(
    monthly_production: list[PVGISMonthly],
    consumption: ConsumptionProfile | None = None,
    desired_kwp: float = 6.0,
    scenario_label: str = "Reference",
) -> ValidationReport:
    """Run backtest at all standard decision points and compute accuracy metrics.

    Args:
        monthly_production: PVGIS monthly production data (time-invariant TMY).
        consumption: Annual consumption profile. Defaults to 3000 kWh/yr.
        desired_kwp: System size in kWp.
        scenario_label: Human-readable label for the report.

    Returns:
        ValidationReport with per-decision-point results and aggregate metrics.
    """
    if consumption is None:
        consumption = ConsumptionProfile(
            annual_kwh=3000, f1_kwh=1200, f2_kwh=900, f3_kwh=900,
        )

    backtest_results = run_standard_backtest(
        monthly_production, consumption, desired_kwp,
    )

    # Build per-decision-point validations
    decision_points: list[DecisionPointValidation] = []
    for result in backtest_results:
        years = result.actual.years_elapsed
        included = years >= _MIN_YEARS_FOR_AGGREGATES

        predicted_npv = result.predicted.npv_20yr_eur
        actual_npv = result.actual.npv_to_date_eur
        projected_npv = result.actual.projected_npv_20yr

        predicted_profitable = predicted_npv > 0
        # Use projected 20yr NPV for profitability when available (fair horizon comparison)
        comparison_npv = projected_npv if projected_npv is not None else actual_npv
        actual_profitable = comparison_npv > 0

        # Payback error
        payback_error: float | None = None
        if result.predicted.payback_years is not None and result.actual.payback_years is not None:
            payback_error = round(
                result.predicted.payback_years - result.actual.payback_years, 2,
            )

        # IRR error (percentage points)
        irr_error: float | None = None
        if result.predicted.irr_percent is not None and result.actual.irr_percent is not None:
            irr_error = round(
                result.predicted.irr_percent - result.actual.irr_percent, 2,
            )

        decision_points.append(DecisionPointValidation(
            decision_date=result.decision_date,
            regime_name=result.regime_name,
            regime_type=result.regime_type,
            years_elapsed=years,
            predicted_npv=predicted_npv,
            actual_npv=actual_npv,
            projected_npv_20yr=projected_npv,
            npv_error_eur=result.npv_prediction_error_eur,
            npv_error_pct=result.npv_prediction_error_pct,
            predicted_payback=result.predicted.payback_years,
            actual_payback=result.actual.payback_years,
            payback_error_years=payback_error,
            predicted_irr=result.predicted.irr_percent,
            actual_irr=result.actual.irr_percent,
            irr_error_pct_points=irr_error,
            predicted_profitable=predicted_profitable,
            actual_profitable=actual_profitable,
            recommendation_correct=predicted_profitable == actual_profitable,
            included_in_aggregates=included,
        ))

    # Compute aggregates from qualifying points only
    qualifying = [dp for dp in decision_points if dp.included_in_aggregates]
    aggregate = _compute_aggregates(qualifying)

    # Classify regimes
    regime_npv_errors: dict[str, list[float]] = {}
    for dp in qualifying:
        if dp.npv_error_pct is not None:
            regime_npv_errors.setdefault(dp.regime_name, []).append(abs(dp.npv_error_pct))

    well_modeled: list[str] = []
    poorly_modeled: list[str] = []
    for regime_name, errors in regime_npv_errors.items():
        mean_err = sum(errors) / len(errors)
        if mean_err < _WELL_MODELED_ERROR_THRESHOLD_PCT:
            well_modeled.append(regime_name)
        else:
            poorly_modeled.append(regime_name)

    return ValidationReport(
        scenario_label=scenario_label,
        system_kwp=desired_kwp,
        annual_consumption_kwh=consumption.annual_kwh,
        decision_points=decision_points,
        aggregate=aggregate,
        regimes_well_modeled=sorted(well_modeled),
        regimes_poorly_modeled=sorted(poorly_modeled),
    )


def _compute_aggregates(
    qualifying: list[DecisionPointValidation],
) -> AggregateMetrics:
    """Aggregate per-decision-point validation into summary metrics.

    Computes NPV error stats, binary accuracy, payback/IRR error,
    Spearman rank correlation, and per-regime-type accuracy.
    """
    total = len(qualifying)

    # NPV error
    npv_errors = [abs(dp.npv_error_pct) for dp in qualifying if dp.npv_error_pct is not None]
    mean_npv = sum(npv_errors) / len(npv_errors) if npv_errors else 0.0
    med_npv = median(npv_errors) if npv_errors else 0.0
    max_npv = max(npv_errors) if npv_errors else 0.0

    # Binary accuracy
    correct = sum(1 for dp in qualifying if dp.recommendation_correct)
    accuracy = correct / total if total > 0 else 0.0

    # Payback error
    payback_errors = [
        abs(dp.payback_error_years) for dp in qualifying if dp.payback_error_years is not None
    ]
    mean_payback = round(sum(payback_errors) / len(payback_errors), 2) if payback_errors else None
    med_payback = round(median(payback_errors), 2) if payback_errors else None

    # IRR error
    irr_errors = [
        abs(dp.irr_error_pct_points) for dp in qualifying if dp.irr_error_pct_points is not None
    ]
    mean_irr = round(sum(irr_errors) / len(irr_errors), 2) if irr_errors else None
    med_irr = round(median(irr_errors), 2) if irr_errors else None

    # Spearman rank correlation — use projected 20yr NPV when available for fair comparison
    pred_npvs = [dp.predicted_npv for dp in qualifying]
    actual_npvs = [
        dp.projected_npv_20yr if dp.projected_npv_20yr is not None else dp.actual_npv
        for dp in qualifying
    ]
    spearman = _spearman_rank_correlation(pred_npvs, actual_npvs)

    # Per-regime binary accuracy
    regime_groups: dict[str, list[bool]] = {}
    for dp in qualifying:
        regime_groups.setdefault(dp.regime_type, []).append(dp.recommendation_correct)
    accuracy_by_regime = {
        rt: round(sum(1 for c in vals if c) / len(vals), 4)
        for rt, vals in regime_groups.items()
    }

    return AggregateMetrics(
        mean_npv_error_pct=round(mean_npv, 2),
        median_npv_error_pct=round(med_npv, 2),
        max_npv_error_pct=round(max_npv, 2),
        binary_accuracy=round(accuracy, 4),
        correct_count=correct,
        total_count=total,
        mean_payback_error_years=mean_payback,
        median_payback_error_years=med_payback,
        mean_irr_error_pct_points=mean_irr,
        median_irr_error_pct_points=med_irr,
        spearman_rank_correlation=spearman,
        accuracy_by_regime_type=accuracy_by_regime,
    )
