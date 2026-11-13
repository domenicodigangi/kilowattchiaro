"""Top-level evaluate_solar orchestrator.

The function body is structured as 8 linear phases (see the Mermaid
diagram in ``__init__.py`` for the visual graph). Phase markers below
keep the long but linear pipeline scannable:

    Phase 1 — Resolve costs (panel/battery cost, investments, detrazione)
    Phase 2 — Compute self-consumption (hourly path; monthly fallback)
    Phase 3 — Build status-quo + price context
    Phase 4 — Build solar cash-flow projection
    Phase 5 — Assemble status-quo + solar ScenarioResults
    Phase 6 — Battery branch: projection + ScenarioResult (conditional)
    Phase 7 — Assemble assumptions dict
    Phase 8 — Return SolarEvalResult

Phases 5 and 6 dispatch to ``_build_scenario_result`` so the NPV / IRR /
payback assembly is written once and reused for both scenarios.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from ..assumptions import value as _assumption
from ..config import settings
from ..models.hourly import HourlyProductionProfile
from ..models.solar_eval import (
    ConsumptionProfile,
    MonthlyDetail,
    PVGISMonthly,
    PVGISTMYResult,
    PriceScenarioName,
    PriceTier,
    ScenarioResult,
    ScenarioType,
    SensitivityCell,
    SolarEvalResult,
)
from ..detrazione import DETRAZIONE_YEARS, detrazione_rate, detrazione_total_eur
from ..hourly_match import match_hourly
from ..load_profiles import infer_hourly_load
from ..price_projection import get_price_projection_scenario
from ..pv_production import SegmentProduction, hourly_production_kw
from ..quote.stacked import battery_band, investment_range, pv_band
from ..version import ENGINE_VERSION
from ._constants import _ANALYSIS_HORIZON_YEARS
from .cash_flow import (
    BATTERY_CELL_PACK_SHARE,
    _build_annual_cash_flow_projection,
    _cumulative_cash_flow,
)
from .models import AnnualCashFlowProjection
from .npv import _estimate_irr, _npv_20yr, _npv_sensitivity
from .payback import _payback_years
from .sensitivity import (
    SELF_CONSUMPTION_SHIFT,
    classify_robustness,
    lower_detrazione_rate,
    shift_self_consumption,
    verdict_for,
)
from .tariff import estimated_annual_bill_eur, variable_rate_eur_kwh
from .scenario import (
    _annual_profile_from_monthly,
    _breakdown_to_dicts,
    _monthly_from_hourly_match,
    _scaled_price_trajectories,
    _solar_monthly,
    _status_quo_monthly,
)

logger = logging.getLogger(__name__)

# Export (RID) valuation. GSE pays the hourly zonal price, so exported PV earns
# a solar-weighted price, below the average. GME CNOR day-ahead 2025: zonal
# mean EUR 116.8/MWh, weighted by the Pisa PV profile EUR 98.9/MWh; that is
# 0.72 of the engine's wholesale component (EUR 0.137/kWh, which also carries
# dispatching). RID revenue is taxed as IRPEF "redditi diversi" (~23%).
SOLAR_CAPTURE_FACTOR: float = _assumption("export_capture_factor")  # app/assumptions.yaml
EXPORT_IRPEF_RATE = 0.23

# Price tiers are the floor, central and ceiling of the market band for the size evaluated.
_PRICE_TIER_BAND_INDEX = {"basso": 0, "medio": 1, "alto": 2}


def _projected_prices(name: PriceScenarioName, cpi: float):
    return get_price_projection_scenario(
        scenario_name=name, years=_ANALYSIS_HORIZON_YEARS, cpi=cpi
    ).project()


def _export_price(projected_prices) -> float:
    """Year-0 EUR/kWh earned on exported kWh, before the RID floor."""
    return (
        projected_prices[0].wholesale_eur_kwh
        * SOLAR_CAPTURE_FACTOR
        * (1 - EXPORT_IRPEF_RATE)
    )



def _economics(
    *,
    investment: float,
    projection: AnnualCashFlowProjection,
    sq_annual_cost: float,
    detrazione_annual: float,
    detrazione_years: int,
    inflation: float,
    discount_rate: float,
) -> dict:
    """Every figure the results page shows for one projection, and the verdict on it."""
    annual_cost = round(projection.annual_import_costs[0], 2)
    rid_annual = round(projection.annual_export_revenues[0], 2)
    annual_savings = round(projection.annual_cash_flows[0], 2)
    common = dict(
        investment=investment,
        annual_cost_sq=sq_annual_cost,
        annual_cost_solar=annual_cost,
        annual_rid=rid_annual,
        annual_detrazione=detrazione_annual,
        detrazione_years=detrazione_years,
        inflation=inflation,
        annual_cash_flows=projection.annual_cash_flows,
    )
    payback = _payback_years(**common)
    npv = _npv_20yr(**common, discount_rate=discount_rate)
    return {
        "investment_cost_eur": investment,
        "annual_electricity_cost_eur": annual_cost,
        "annual_rid_revenue_eur": rid_annual,
        "annual_detrazione_eur": detrazione_annual,
        "annual_savings_eur": annual_savings,
        "payback_years": payback,
        "npv_20yr_eur": npv,
        "irr_percent": _estimate_irr(**common),
        "cumulative_cash_flow": _cumulative_cash_flow(
            investment, projection.annual_cash_flows
        ),
        "annual_breakdown": _breakdown_to_dicts(projection.annual_breakdown),
        "verdict": verdict_for(npv, payback, annual_savings),
    }


def _build_scenario_result(
    *,
    scenario: ScenarioType,
    label: str,
    investment: float,
    projection: AnnualCashFlowProjection,
    sq_annual_cost: float,
    detrazione_annual: float,
    detrazione_years: int,
    inflation: float,
    discount_rate: float,
    monthly_details: list[MonthlyDetail],
    sc_pct: float,
) -> ScenarioResult:
    figures = _economics(
        investment=investment,
        projection=projection,
        sq_annual_cost=sq_annual_cost,
        detrazione_annual=detrazione_annual,
        detrazione_years=detrazione_years,
        inflation=inflation,
        discount_rate=discount_rate,
    )
    return ScenarioResult(
        scenario=scenario,
        label=label,
        **figures,
        npv_sensitivity=_npv_sensitivity(investment, projection.annual_cash_flows),
        self_consumption_percent=sc_pct,
        monthly_details=monthly_details,
    )


@dataclass(frozen=True)
class _CostChoice:
    tier: PriceTier | None
    panel_cost_eur_per_kwp: float
    battery_cost_eur: float


def _cost_choices(
    *,
    region: str | None,
    kwp: float,
    battery_kwh: float,
    explicit: _CostChoice | None,
) -> list[_CostChoice]:
    """The cost tiers the grid covers; only the request's own costs when it fixed them."""
    if explicit is not None:
        return [explicit]
    pv = pv_band(region, kwp)
    battery = battery_band(battery_kwh)
    return [
        _CostChoice(
            tier=tier,
            panel_cost_eur_per_kwp=pv[index],
            battery_cost_eur=round(battery[index] * battery_kwh, 2),
        )
        for tier, index in _PRICE_TIER_BAND_INDEX.items()
    ]


def _attach_sensitivity(
    scenario: ScenarioResult,
    *,
    project: Callable[..., AnnualCashFlowProjection],
    trajectories: dict[PriceScenarioName, tuple[list[float], list[float]]],
    choices: list[_CostChoice],
    base_key: tuple[PriceScenarioName, PriceTier | None],
    kwp: float,
    with_battery: bool,
    detrazione: float,
    lower_rate: float | None,
    sq_annual_cost: float,
    inflation: float,
    discount_rate: float,
) -> None:
    """Fill ``scenario.sensitivity`` (price scenario x cost tier) and its robustness label.

    Each cell runs the same projection as the scenario itself; robustness also runs the
    lower detrazione rate and self-consumption 10 points lower, keeping only verdicts.
    """
    rates = [(False, detrazione)] + (
        [(True, lower_rate)] if lower_rate is not None else []
    )
    cells: list[SensitivityCell] = []
    for choice in choices:
        solar_investment = round(kwp * choice.panel_cost_eur_per_kwp, 2)
        investment = solar_investment + (
            round(choice.battery_cost_eur, 2) if with_battery else 0.0
        )
        verdicts: dict[tuple[bool, PriceScenarioName, bool], str] = {}
        figures_by_price: dict[PriceScenarioName, dict] = {}
        for lowered, rate in rates:
            detrazione_annual = round(
                detrazione_total_eur(investment, rate) / DETRAZIONE_YEARS, 2
            )
            for shifted in (False, True):
                for price, (retail, wholesale) in trajectories.items():
                    projection = project(
                        retail=retail,
                        wholesale=wholesale,
                        annual_detrazione=detrazione_annual,
                        battery_cost_eur=choice.battery_cost_eur,
                        shift_self_consumption=shifted,
                    )
                    figures = _economics(
                        investment=investment,
                        projection=projection,
                        sq_annual_cost=sq_annual_cost,
                        detrazione_annual=detrazione_annual,
                        detrazione_years=DETRAZIONE_YEARS,
                        inflation=inflation,
                        discount_rate=discount_rate,
                    )
                    verdicts[(lowered, price, shifted)] = figures["verdict"]
                    if not lowered and not shifted:
                        figures_by_price[price] = figures
        lower_pct = round(lower_rate * 100) if lower_rate is not None else None
        for price, figures in figures_by_price.items():
            cells.append(
                SensitivityCell(
                    price_scenario=price,
                    price_tier=choice.tier,
                    panel_cost_eur_per_kwp=choice.panel_cost_eur_per_kwp,
                    battery_cost_eur=choice.battery_cost_eur if with_battery else None,
                    battery_replacement_eur=(
                        round(choice.battery_cost_eur * BATTERY_CELL_PACK_SHARE, 2)
                        if with_battery
                        else None
                    ),
                    **figures,
                    robustness=classify_robustness(
                        verdicts, base_price=price, lower_detrazione_percent=lower_pct
                    ),
                )
            )
    scenario.sensitivity = cells
    base = next(c for c in cells if (c.price_scenario, c.price_tier) == base_key)
    scenario.robustness = base.robustness


def _tmy_location(tmy: PVGISTMYResult) -> tuple[float, float]:
    if tmy.lat is not None and tmy.lon is not None:
        return tmy.lat, tmy.lon
    lat, lon = (float(v) for v in tmy.location.split(","))
    return lat, lon


def _attach_price_range(
    scenario: ScenarioResult, region: str | None, kwp: float, kwh: float | None
) -> None:
    floor, central, ceiling = investment_range(region, kwp, kwh)
    scenario.investment_range_eur = (floor, ceiling)
    scenario.investment_central_eur = central


def evaluate_solar(
    consumption: ConsumptionProfile,
    monthly_production: list[PVGISMonthly],
    desired_kwp: float,
    include_battery: bool = False,
    battery_kwh: float = 10.0,
    panel_cost_eur_per_kwp: float | None = None,
    battery_cost_eur: float | None = None,
    battery_cost_eur_per_kwh: float | None = None,
    discount_rate: float = 0.03,
    energy_price_inflation: float = 0.02,
    price_scenario: PriceScenarioName = PriceScenarioName.REFERENCE,
    tmy_data: PVGISTMYResult | None = None,
    region: str | None = None,
    payment_date: date | None = None,
    main_home: bool = True,
    roof_segments: list[SegmentProduction] | None = None,
    price_tier: PriceTier | None = None,
    with_sensitivity: bool = True,
) -> SolarEvalResult:
    """Run financial evaluation for status quo, solar, and solar+battery.

    Without an explicit panel or battery cost, the modelled investment uses the central
    of the market band for ``region`` (nord/centro/sud/isole; national when None): the
    PV band central x kWp plus the battery band central x kWh. ``price_tier`` picks the
    floor (basso) or ceiling (alto) of those same bands instead.

    Without ``payment_date`` the detrazione assumes payment from 2027.

    ``roof_segments`` carries each segment's tilt, azimuth, kWp and PVcalc
    monthly yield; the hourly production sums their tilted-plane profiles.
    Without it the roof is one 30-degree south segment yielding
    ``monthly_production``.

    Each installed scenario carries the engine verdict; with ``with_sensitivity`` it
    also carries the price scenario x cost tier grid and the robustness label
    (``sensitivity.py``). Callers that only rank candidates skip the grid.
    """
    if consumption.annual_kwh <= 0:
        raise ValueError("annual_kwh must be positive for evaluation")

    # === Phase 1 — Resolve costs (panel, battery, investments, detrazione) ===
    band_index = _PRICE_TIER_BAND_INDEX[price_tier or "medio"]
    panel_cost = (
        panel_cost_eur_per_kwp
        if panel_cost_eur_per_kwp is not None
        else pv_band(region, desired_kwp)[band_index]
    )
    if battery_cost_eur_per_kwh is not None:
        bat_cost = round(battery_cost_eur_per_kwh * battery_kwh, 2)
    elif battery_cost_eur is not None:
        bat_cost = battery_cost_eur
    else:
        bat_cost = round(battery_band(battery_kwh)[band_index] * battery_kwh, 2)
    rid_floor = settings.default_rid_price_eur_kwh
    battery_replacement_eur = round(bat_cost * BATTERY_CELL_PACK_SHARE, 2)

    projected_prices = _projected_prices(price_scenario, energy_price_inflation)
    export_price = _export_price(projected_prices)
    export_value = max(export_price, rid_floor)  # year-0 EUR/kWh for monthly detail
    maintenance_eur_yr = settings.default_maintenance_eur_yr
    inverter_replacement_eur = settings.default_inverter_replacement_eur
    inverter_replacement_year = settings.default_inverter_replacement_year
    battery_replacement_year = settings.default_battery_replacement_year
    panel_degradation_pct = settings.default_panel_degradation_pct
    battery_fade_pct = settings.default_battery_fade_pct

    # Avoided kWh are worth the variable rate only; fixed charges stay on the bill.
    sq_annual_cost = round(
        consumption.annual_cost_eur or estimated_annual_bill_eur(consumption.annual_kwh),
        2,
    )
    all_in_rate = sq_annual_cost / consumption.annual_kwh
    variable_rate = variable_rate_eur_kwh(consumption.annual_cost_eur, consumption.annual_kwh)
    fixed_charges = max(0.0, sq_annual_cost - variable_rate * consumption.annual_kwh)

    annual_production = sum(m.e_m for m in monthly_production)
    solar_investment = round(desired_kwp * panel_cost, 2)
    battery_investment = round(bat_cost, 2) if include_battery else 0.0

    detrazione = detrazione_rate(payment_date, main_home=main_home)
    solar_detrazione_annual = round(
        detrazione_total_eur(solar_investment, detrazione) / DETRAZIONE_YEARS, 2
    )
    battery_detrazione_annual = round(
        detrazione_total_eur(solar_investment + battery_investment, detrazione)
        / DETRAZIONE_YEARS,
        2,
    )

    # === Phase 2 — Compute self-consumption (hourly path; monthly fallback) ===
    use_hourly = tmy_data is not None and tmy_data.has_components
    hourly_match_bat = None
    if use_hourly:
        try:
            segments = roof_segments or [
                SegmentProduction(
                    kwp=desired_kwp,
                    tilt_deg=30.0,
                    azimuth_pvgis_deg=0.0,
                    monthly_kwh=tuple(m.e_m for m in sorted(monthly_production, key=lambda m: m.month)),
                )
            ]
            tmy_lat, tmy_lon = _tmy_location(tmy_data)
            hourly_prod_values = hourly_production_kw(tmy_data, tmy_lat, tmy_lon, segments)
            hourly_load = infer_hourly_load(
                consumption,
                potenza_impegnata=consumption.potenza_impegnata_kw,
            )
            hourly_prod = HourlyProductionProfile(
                values=hourly_prod_values,
                lat=tmy_lat,
                lon=tmy_lon,
                kwp=desired_kwp,
                tilt=segments[0].tilt_deg,
                azimuth=segments[0].azimuth_pvgis_deg,
            )
            hourly_match_solar = match_hourly(hourly_load, hourly_prod, battery_kwh=0)
            sc_ratio = hourly_match_solar.self_consumption_ratio
            solar_monthly = _monthly_from_hourly_match(
                hourly_match_solar,
                variable_rate,
                export_value,
                fixed_charges,
            )
            solar_sc_pct = round(
                hourly_match_solar.annual_self_consumption_kwh
                / consumption.annual_kwh
                * 100,
                1,
            ) if consumption.annual_kwh > 0 else 0.0

            if include_battery:
                hourly_match_bat = match_hourly(
                    hourly_load,
                    hourly_prod,
                    battery_kwh=battery_kwh,
                )
                sc_ratio_bat = hourly_match_bat.self_consumption_ratio
            else:
                sc_ratio_bat = settings.default_self_consumption_ratio_battery
        except Exception:
            logger.warning(
                "Hourly matching failed, falling back to monthly path",
                exc_info=True,
            )
            use_hourly = False

    if not use_hourly:
        sc_ratio = settings.default_self_consumption_ratio
        sc_ratio_bat = settings.default_self_consumption_ratio_battery
        solar_monthly = _solar_monthly(
            consumption,
            monthly_production,
            variable_rate,
            sc_ratio,
            export_value,
            fixed_charges,
        )
        solar_sc_pct = round(
            sc_ratio * min(annual_production, consumption.annual_kwh)
            / consumption.annual_kwh
            * 100,
            1,
        ) if consumption.annual_kwh > 0 else 0.0

    # === Phase 3 — Build status-quo + price context ===
    sq_monthly = _status_quo_monthly(consumption, variable_rate, fixed_charges)
    status_quo_profile = _annual_profile_from_monthly(sq_monthly)
    solar_profile = _annual_profile_from_monthly(solar_monthly)

    projected_retail_prices, projected_wholesale_prices = _scaled_price_trajectories(
        projected_prices,
        base_retail_rate=variable_rate,
        base_wholesale_rate=export_price,
    )

    # === Phase 4 — Build solar cash-flow projection ===
    solar_projection = _build_annual_cash_flow_projection(
        status_quo_profile=status_quo_profile,
        solar_profile=solar_profile,
        projected_retail_prices=projected_retail_prices,
        projected_wholesale_prices=projected_wholesale_prices,
        annual_detrazione=solar_detrazione_annual,
        detrazione_years=DETRAZIONE_YEARS,
        annual_maintenance_eur=maintenance_eur_yr,
        inverter_replacement_eur=inverter_replacement_eur,
        inverter_replacement_year=inverter_replacement_year,
        rid_floor_price_eur_kwh=rid_floor,
        panel_degradation_pct=panel_degradation_pct,
        cost_inflation=energy_price_inflation,
        fixed_charges_eur=fixed_charges,
    )

    # === Phase 5 — Assemble status-quo + solar ScenarioResults ===
    status_quo = ScenarioResult(
        scenario=ScenarioType.STATUS_QUO,
        label="Nessun impianto (situazione attuale)",
        annual_electricity_cost_eur=sq_annual_cost,
        monthly_details=sq_monthly,
    )

    solar_only = _build_scenario_result(
        scenario=ScenarioType.SOLAR_ONLY,
        label=f"Fotovoltaico {desired_kwp} kWp",
        investment=solar_investment,
        projection=solar_projection,
        sq_annual_cost=sq_annual_cost,
        detrazione_annual=solar_detrazione_annual,
        detrazione_years=DETRAZIONE_YEARS,
        inflation=energy_price_inflation,
        discount_rate=discount_rate,
        monthly_details=solar_monthly,
        sc_pct=solar_sc_pct,
    )

    scenarios = [status_quo, solar_only]
    _attach_price_range(solar_only, region, desired_kwp, None)

    # === Phase 6 — Battery branch: projection + ScenarioResult (conditional) ===
    if include_battery:
        total_investment = solar_investment + battery_investment
        if use_hourly and hourly_match_bat is not None:
            bat_monthly = _monthly_from_hourly_match(
                hourly_match_bat,
                variable_rate,
                export_value,
                fixed_charges,
            )
            bat_sc_pct = round(
                hourly_match_bat.annual_self_consumption_kwh
                / consumption.annual_kwh
                * 100,
                1,
            ) if consumption.annual_kwh > 0 else 0.0
        else:
            bat_monthly = _solar_monthly(
                consumption,
                monthly_production,
                variable_rate,
                sc_ratio_bat,
                export_value,
                fixed_charges,
            )
            bat_sc_pct = round(
                sc_ratio_bat * min(annual_production, consumption.annual_kwh)
                / consumption.annual_kwh
                * 100,
                1,
            ) if consumption.annual_kwh > 0 else 0.0

        battery_profile = _annual_profile_from_monthly(bat_monthly)
        battery_projection = _build_annual_cash_flow_projection(
            status_quo_profile=status_quo_profile,
            solar_profile=solar_profile,
            projected_retail_prices=projected_retail_prices,
            projected_wholesale_prices=projected_wholesale_prices,
            annual_detrazione=battery_detrazione_annual,
            detrazione_years=DETRAZIONE_YEARS,
            annual_maintenance_eur=maintenance_eur_yr,
            inverter_replacement_eur=inverter_replacement_eur,
            inverter_replacement_year=inverter_replacement_year,
            rid_floor_price_eur_kwh=rid_floor,
            panel_degradation_pct=panel_degradation_pct,
            battery_profile=battery_profile,
            battery_fade_pct=battery_fade_pct,
            battery_replacement_eur=battery_replacement_eur,
            battery_replacement_year=battery_replacement_year,
            cost_inflation=energy_price_inflation,
            fixed_charges_eur=fixed_charges,
        )

        solar_battery = _build_scenario_result(
            scenario=ScenarioType.SOLAR_BATTERY,
            label=f"Fotovoltaico {desired_kwp} kWp + batteria {battery_kwh} kWh",
            investment=total_investment,
            projection=battery_projection,
            sq_annual_cost=sq_annual_cost,
            detrazione_annual=battery_detrazione_annual,
            detrazione_years=DETRAZIONE_YEARS,
            inflation=energy_price_inflation,
            discount_rate=discount_rate,
            monthly_details=bat_monthly,
            sc_pct=bat_sc_pct,
        )
        _attach_price_range(solar_battery, region, desired_kwp, battery_kwh)
        scenarios.append(solar_battery)

    # === Phase 6b — What-if grid and robustness label (sensitivity.py) ===
    if with_sensitivity:
        trajectories = {}
        for name in PriceScenarioName:
            prices = _projected_prices(name, energy_price_inflation)
            trajectories[name] = _scaled_price_trajectories(
                prices,
                base_retail_rate=variable_rate,
                base_wholesale_rate=_export_price(prices),
            )
        explicit_costs = (
            panel_cost_eur_per_kwp is not None
            or battery_cost_eur is not None
            or battery_cost_eur_per_kwh is not None
        )
        choices = _cost_choices(
            region=region,
            kwp=desired_kwp,
            battery_kwh=battery_kwh,
            explicit=_CostChoice(price_tier, panel_cost, bat_cost)
            if explicit_costs
            else None,
        )
        base_key = (
            price_scenario,
            price_tier if explicit_costs else (price_tier or "medio"),
        )
        shifted_solar_profile = shift_self_consumption(
            solar_profile, SELF_CONSUMPTION_SHIFT
        )

        def project_solar(
            *,
            retail,
            wholesale,
            annual_detrazione,
            battery_cost_eur,
            shift_self_consumption,
        ):
            return _build_annual_cash_flow_projection(
                status_quo_profile=status_quo_profile,
                solar_profile=shifted_solar_profile
                if shift_self_consumption
                else solar_profile,
                projected_retail_prices=retail,
                projected_wholesale_prices=wholesale,
                annual_detrazione=annual_detrazione,
                detrazione_years=DETRAZIONE_YEARS,
                annual_maintenance_eur=maintenance_eur_yr,
                inverter_replacement_eur=inverter_replacement_eur,
                inverter_replacement_year=inverter_replacement_year,
                rid_floor_price_eur_kwh=rid_floor,
                panel_degradation_pct=panel_degradation_pct,
                cost_inflation=energy_price_inflation,
                fixed_charges_eur=fixed_charges,
            )

        grid_common = dict(
            trajectories=trajectories,
            choices=choices,
            base_key=base_key,
            kwp=desired_kwp,
            detrazione=detrazione,
            lower_rate=lower_detrazione_rate(payment_date, main_home=main_home),
            sq_annual_cost=sq_annual_cost,
            inflation=energy_price_inflation,
            discount_rate=discount_rate,
        )
        _attach_sensitivity(
            solar_only, project=project_solar, with_battery=False, **grid_common
        )

        if include_battery:
            shifted_battery_profile = shift_self_consumption(
                battery_profile, SELF_CONSUMPTION_SHIFT
            )

            def project_battery(
                *,
                retail,
                wholesale,
                annual_detrazione,
                battery_cost_eur,
                shift_self_consumption,
            ):
                return _build_annual_cash_flow_projection(
                    status_quo_profile=status_quo_profile,
                    solar_profile=shifted_solar_profile
                    if shift_self_consumption
                    else solar_profile,
                    projected_retail_prices=retail,
                    projected_wholesale_prices=wholesale,
                    annual_detrazione=annual_detrazione,
                    detrazione_years=DETRAZIONE_YEARS,
                    annual_maintenance_eur=maintenance_eur_yr,
                    inverter_replacement_eur=inverter_replacement_eur,
                    inverter_replacement_year=inverter_replacement_year,
                    rid_floor_price_eur_kwh=rid_floor,
                    panel_degradation_pct=panel_degradation_pct,
                    battery_profile=shifted_battery_profile
                    if shift_self_consumption
                    else battery_profile,
                    battery_fade_pct=battery_fade_pct,
                    battery_replacement_eur=round(
                        battery_cost_eur * BATTERY_CELL_PACK_SHARE, 2
                    ),
                    battery_replacement_year=battery_replacement_year,
                    cost_inflation=energy_price_inflation,
                    fixed_charges_eur=fixed_charges,
                )

            _attach_sensitivity(
                solar_battery, project=project_battery, with_battery=True, **grid_common
            )

    # === Phase 7 — Assemble assumptions dict ===
    assumptions = {
        "panel_cost_eur_per_kwp": panel_cost,
        "battery_cost_eur": bat_cost if include_battery else None,
        "rid_price_eur_kwh": round(export_value, 4),
        "rid_floor_eur_kwh": rid_floor,
        "self_consumption_ratio": sc_ratio,
        "self_consumption_ratio_battery": sc_ratio_bat if include_battery else None,
        "all_in_electricity_rate_eur_kwh": round(all_in_rate, 4),
        "variable_electricity_rate_eur_kwh": round(variable_rate, 4),
        "fixed_charges_eur_yr": round(fixed_charges, 2),
        "discount_rate": discount_rate,
        # Applied to nominal cash flows (prices escalate with CPI), so it is a
        # nominal rate; npv_sensitivity shows 5% nominal alongside.
        "discount_rate_basis": "nominal",
        "pv_cost_band_eur_per_kwp": list(pv_band(region, desired_kwp)),
        # Always present: the browser's price tiers read both bands
        "battery_cost_band_eur_per_kwh": list(battery_band(battery_kwh)),
        "energy_price_inflation": energy_price_inflation,
        "price_scenario_used": price_scenario.value,
        "detrazione_percent": round(detrazione * 100),
        "detrazione_years": DETRAZIONE_YEARS,
        "main_home": main_home,
        "analysis_horizon_years": _ANALYSIS_HORIZON_YEARS,
        "panel_degradation_pct": panel_degradation_pct,
        "battery_fade_pct": battery_fade_pct if include_battery else None,
        "maintenance_eur_yr": maintenance_eur_yr,
        "inverter_replacement_eur": inverter_replacement_eur,
        "inverter_replacement_year": inverter_replacement_year,
        "battery_replacement_year": battery_replacement_year if include_battery else None,
        "battery_replacement_eur": battery_replacement_eur if include_battery else None,
        "self_consumption_source": "hourly_match" if use_hourly else "default_ratio",
        "production_model": "tilted_plane_hourly" if use_hourly else "pvgis_monthly",
    }

    # === Phase 8 — Return SolarEvalResult ===
    return SolarEvalResult(
        scenarios=scenarios,
        annual_production_kwh=round(annual_production, 1),
        system_kwp=desired_kwp,
        location="",
        engine_version=ENGINE_VERSION,
        assumptions={k: v for k, v in assumptions.items() if v is not None},
    )
