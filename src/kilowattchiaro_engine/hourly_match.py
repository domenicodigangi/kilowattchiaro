"""Hourly self-consumption matching engine.

Greedy hour-by-hour algorithm that matches consumption against production
to compute actual self-consumption ratios, replacing hardcoded 30%/70% assumptions.

Includes battery charge/discharge logic with round-trip efficiency and C-rate limits.
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


def match_hourly(
    load: HourlyLoadProfile,
    production: HourlyProductionProfile,
    battery_kwh: float = 0.0,
) -> HourlySelfConsumptionResult:
    """Match hourly consumption against production to compute self-consumption.

    For each hour, direct PV consumption is calculated first. If a battery is
    present, surplus PV charges the battery and deficit is covered by discharge,
    subject to capacity, SOC, and C-rate constraints.

    Args:
        load: 8760-element hourly consumption profile (kW).
        production: 8760-element hourly PV production profile (kW).
        battery_kwh: Usable battery capacity in kWh (0 = no battery).

    Returns:
        HourlySelfConsumptionResult with hourly arrays, monthly aggregates,
        and annual totals.
    """
    load_vals = load.values
    prod_vals = production.values
    has_battery = battery_kwh > 0

    self_consumption_kw: list[float] = [0.0] * 8760
    grid_import_kw: list[float] = [0.0] * 8760
    grid_export_kw: list[float] = [0.0] * 8760
    battery_state_kwh: list[float] | None = [0.0] * 8760 if has_battery else None

    max_charge_rate = battery_kwh * C_RATE if has_battery else 0.0
    max_discharge_rate = battery_kwh * C_RATE if has_battery else 0.0
    soc = 0.0

    for h in range(8760):
        load_h = load_vals[h]
        prod_h = prod_vals[h]

        # Direct PV self-consumption
        self_from_pv = min(load_h, prod_h)
        surplus = prod_h - self_from_pv
        deficit = load_h - self_from_pv

        battery_delivered = 0.0

        if has_battery:
            # Charge from surplus
            if surplus > 0:
                available_capacity = battery_kwh - soc
                # charge_energy is what's taken from PV; battery stores charge_energy * CHARGE_EFF
                charge_energy = min(
                    surplus,
                    available_capacity / CHARGE_EFFICIENCY,
                    max_charge_rate,
                )
                soc += charge_energy * CHARGE_EFFICIENCY
                surplus -= charge_energy

            # Discharge to cover deficit
            if deficit > 0:
                # discharge_from_soc is what leaves the battery;
                # load receives discharge_from_soc * DISCHARGE_EFF
                discharge_from_soc = min(
                    deficit / DISCHARGE_EFFICIENCY,
                    soc,
                    max_discharge_rate,
                )
                soc -= discharge_from_soc
                battery_delivered = discharge_from_soc * DISCHARGE_EFFICIENCY
                deficit -= battery_delivered

            # Clamp SOC to prevent floating-point drift
            soc = max(0.0, min(soc, battery_kwh))
            battery_state_kwh[h] = soc  # type: ignore[index]

        self_consumption_kw[h] = self_from_pv + battery_delivered
        grid_import_kw[h] = max(0.0, deficit)
        grid_export_kw[h] = max(0.0, surplus)

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
        monthly_summary=monthly_summary,
        annual_self_consumption_kwh=annual_sc,
        annual_grid_import_kwh=annual_import,
        annual_grid_export_kwh=annual_export,
        self_consumption_ratio=round(sc_ratio, 4),
    )
