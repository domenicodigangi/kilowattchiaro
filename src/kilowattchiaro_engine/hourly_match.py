"""Hourly self-consumption matching engine.

Greedy hour-by-hour algorithm that matches consumption against production
to compute actual self-consumption ratios, replacing hardcoded 30%/70% assumptions.

Calibrations (engine release v1):

- **Hourly-resolution de-rating.** Averaging over one hour hides the
  minute-scale appliance spikes and cloud transients, so it *overestimates*
  direct PV use (Quoilin et al. 2016; Beck et al. 2016; Luthander et al.
  2015: +3 to +8 pp of PV-only self-consumption against 1-minute data). On the
  owner-home fixture (``tests/fixtures/ground_truth/owner_home``) hourly
  averaging overstates direct use by 9.2% relative to 5-minute data. Direct
  use is de-rated by 10%; the mismatch it represents goes to the grid or, when
  present, the battery.
- **Battery realism** (HTW Berlin *Stromspeicher-Inspektion* conventions):
  usable depth of discharge 90% of nominal capacity, round-trip efficiency
  90%, C/2 charge and discharge, a hybrid-inverter AC output cap of
  ``min(kWp, 6 kW)`` (single-phase connection), ~20 W of continuous BMS and
  inverter standby (~175 kWh a year), and a cyclic state of charge so the
  year ends where it starts.

Self-consumption here means avoided grid import: load served by PV or by the
battery, minus any standby energy the battery system had to take from the grid.
"""

import math

from .models.hourly import (
    HourlyLoadProfile,
    HourlyProductionProfile,
    HourlySelfConsumptionResult,
    _MONTH_HOURS,
    _MONTH_OFFSETS,
)

# Battery efficiency constants (LFP standard)
ROUND_TRIP_EFFICIENCY = 0.90
CHARGE_EFFICIENCY = math.sqrt(ROUND_TRIP_EFFICIENCY)  # ~0.9487
DISCHARGE_EFFICIENCY = math.sqrt(ROUND_TRIP_EFFICIENCY)  # ~0.9487
C_RATE = 0.5  # Max charge/discharge rate relative to capacity
USABLE_DEPTH_OF_DISCHARGE = 0.90  # share of nominal capacity the BMS lets you use
INVERTER_AC_CAP_KW = 6.0  # single-phase hybrid inverter ceiling
BATTERY_STANDBY_KW = 0.02  # BMS + inverter idle draw, ~175 kWh/yr
HOURLY_RESOLUTION_DERATE = 0.10  # see module docstring

_CYCLIC_SOC_TOLERANCE_KWH = 1e-6
_CYCLIC_SOC_MAX_PASSES = 20


def _direct_use(
    load_vals: list[float],
    prod_vals: list[float],
    *,
    ac_cap_kw: float,
    derate: float,
) -> tuple[list[float], list[float], list[float]]:
    """Direct PV use (de-rated for the hourly-resolution bias), surplus and deficit per hour."""
    kept = 1.0 - derate
    used = [prod_h if prod_h < load_h else load_h for load_h, prod_h in zip(load_vals, prod_vals)]
    direct = [(ac_cap_kw if ac_cap_kw < u else u) * kept for u in used]
    surplus = [prod_h - d for prod_h, d in zip(prod_vals, direct)]
    deficit = [load_h - d for load_h, d in zip(load_vals, direct)]
    return direct, surplus, deficit


def _simulate_battery_year(
    direct: list[float],
    surplus_vals: list[float],
    deficit_vals: list[float],
    *,
    usable_kwh: float,
    max_rate_kw: float,
    ac_cap_kw: float,
    soc_start: float,
):
    """One year of battery dispatch from ``soc_start``."""
    charge_eff = CHARGE_EFFICIENCY
    discharge_eff = DISCHARGE_EFFICIENCY
    standby_kw = BATTERY_STANDBY_KW
    self_consumption_kw = [0.0] * 8760
    grid_import_kw = [0.0] * 8760
    grid_export_kw = [0.0] * 8760
    battery_state_kwh = [0.0] * 8760
    standby_kwh = 0.0
    soc = soc_start

    for h in range(8760):
        self_from_pv = direct[h]
        surplus = surplus_vals[h]
        deficit = deficit_vals[h]
        battery_delivered = 0.0

        # Charge from surplus (DC side, before the inverter's AC cap)
        if surplus > 0:
            charge_energy = surplus
            room = (usable_kwh - soc) / charge_eff
            if room < charge_energy:
                charge_energy = room
            if max_rate_kw < charge_energy:
                charge_energy = max_rate_kw
            soc += charge_energy * charge_eff
            surplus -= charge_energy

        # What is left goes out through the inverter; above the AC cap it is clipped
        ac_headroom = ac_cap_kw - self_from_pv
        if not ac_headroom > 0.0:
            ac_headroom = 0.0
        if ac_headroom < surplus:
            surplus = ac_headroom
        ac_headroom -= surplus

        # Discharge to cover deficit, within the inverter's AC headroom
        if deficit > 0:
            discharge_from_soc = deficit / discharge_eff
            if soc < discharge_from_soc:
                discharge_from_soc = soc
            if max_rate_kw < discharge_from_soc:
                discharge_from_soc = max_rate_kw
            headroom_limit = ac_headroom / discharge_eff
            if headroom_limit < discharge_from_soc:
                discharge_from_soc = headroom_limit
            soc -= discharge_from_soc
            battery_delivered = discharge_from_soc * discharge_eff
            deficit -= battery_delivered

        # Continuous standby: from the battery while it has charge, else from the grid
        from_battery = soc if soc < standby_kw else standby_kw
        soc -= from_battery
        standby_from_grid = standby_kw - from_battery
        standby_kwh += standby_kw

        # Clamp SOC to prevent floating-point drift
        if usable_kwh < soc:
            soc = usable_kwh
        if not soc > 0.0:
            soc = 0.0
        battery_state_kwh[h] = soc

        self_consumption_kw[h] = self_from_pv + battery_delivered - standby_from_grid
        grid_import_kw[h] = (deficit if deficit > 0.0 else 0.0) + standby_from_grid
        grid_export_kw[h] = surplus if surplus > 0.0 else 0.0

    return self_consumption_kw, grid_import_kw, grid_export_kw, battery_state_kwh, standby_kwh, soc


def match_hourly(
    load: HourlyLoadProfile,
    production: HourlyProductionProfile,
    battery_kwh: float = 0.0,
    resolution_derate: float = HOURLY_RESOLUTION_DERATE,
) -> HourlySelfConsumptionResult:
    """Match hourly consumption against production to compute self-consumption.

    For each hour, direct PV consumption is calculated first. If a battery is
    present, surplus PV charges the battery and deficit is covered by discharge,
    subject to usable capacity, SOC, C-rate and the inverter's AC cap. The year
    is simulated until the state of charge at the end equals the one at the
    start.

    Args:
        load: 8760-element hourly consumption profile (kW).
        production: 8760-element hourly PV production profile (kW).
        battery_kwh: Nominal battery capacity in kWh (0 = no battery).
        resolution_derate: Share of hourly direct PV use removed to correct
            the 1-hour averaging bias (0 disables it).

    Returns:
        HourlySelfConsumptionResult with hourly arrays, monthly aggregates,
        and annual totals.
    """
    load_vals = load.values
    prod_vals = production.values
    has_battery = battery_kwh > 0

    usable_kwh = battery_kwh * USABLE_DEPTH_OF_DISCHARGE if has_battery else 0.0
    ac_cap_kw = min(production.kwp, INVERTER_AC_CAP_KW) if has_battery else math.inf

    direct, surplus, deficit = _direct_use(
        load_vals, prod_vals, ac_cap_kw=ac_cap_kw, derate=resolution_derate
    )
    soc_start = 0.0
    if has_battery:
        for _ in range(_CYCLIC_SOC_MAX_PASSES):
            (
                self_consumption_kw,
                grid_import_kw,
                grid_export_kw,
                battery_state_kwh,
                standby_kwh,
                soc_end,
            ) = _simulate_battery_year(
                direct,
                surplus,
                deficit,
                usable_kwh=usable_kwh,
                max_rate_kw=battery_kwh * C_RATE,
                ac_cap_kw=ac_cap_kw,
                soc_start=soc_start,
            )
            if abs(soc_end - soc_start) < _CYCLIC_SOC_TOLERANCE_KWH:
                break
            soc_start = soc_end
    else:
        self_consumption_kw = direct
        grid_import_kw = [d if d > 0.0 else 0.0 for d in deficit]
        grid_export_kw = [x if x > 0.0 else 0.0 for x in surplus]
        battery_state_kwh = None
        standby_kwh = 0.0

    # Monthly aggregates
    monthly_summary: list[dict] = []
    for month_idx in range(12):
        start = _MONTH_OFFSETS[month_idx]
        end = start + _MONTH_HOURS[month_idx]
        monthly_summary.append({
            "month": month_idx + 1,
            "self_consumption_kwh": round(sum(self_consumption_kw[start:end]), 4),
            "grid_import_kwh": round(sum(grid_import_kw[start:end]), 4),
            "grid_export_kwh": round(sum(grid_export_kw[start:end]), 4),
            "production_kwh": round(sum(prod_vals[start:end]), 4),
            "consumption_kwh": round(sum(load_vals[start:end]), 4),
        })

    # Annual totals
    annual_sc = round(sum(self_consumption_kw), 4)
    annual_import = round(sum(grid_import_kw), 4)
    annual_export = round(sum(grid_export_kw), 4)
    total_production = sum(prod_vals)
    sc_ratio = annual_sc / total_production if total_production > 0 else 0.0

    return HourlySelfConsumptionResult(
        self_consumption_kw=self_consumption_kw,
        grid_import_kw=grid_import_kw,
        grid_export_kw=grid_export_kw,
        battery_state_kwh=battery_state_kwh,
        battery_initial_soc_kwh=soc_start if has_battery else None,
        annual_battery_standby_kwh=round(standby_kwh, 4),
        monthly_summary=monthly_summary,
        annual_self_consumption_kwh=annual_sc,
        annual_grid_import_kwh=annual_import,
        annual_grid_export_kwh=annual_export,
        self_consumption_ratio=round(sc_ratio, 4),
    )
