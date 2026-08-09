"""Hourly energy profile models for load curves, production, and self-consumption.

These models are the shared data contract between load curve inference
(Workstream A), production estimation (PVGIS TMY), and self-consumption matching.

All arrays contain exactly 8760 values (365 days * 24 hours, non-leap TMY convention).
Values are in kW (power); since each slot is 1 hour, kW == kWh for that hour.
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from ..tariff import classify_band
from .contract import TariffBand

# Cumulative hour offsets for each month in a non-leap year (365 days = 8760 hours).
_MONTH_HOURS = [744, 672, 744, 720, 744, 720, 744, 744, 720, 744, 720, 744]
_MONTH_OFFSETS = []
_offset = 0
for _h in _MONTH_HOURS:
    _MONTH_OFFSETS.append(_offset)
    _offset += _h


def _validate_8760(values: list[float]) -> list[float]:
    if len(values) != 8760:
        raise ValueError(f"Expected 8760 hourly values, got {len(values)}")
    return values


def _validate_non_negative(values: list[float]) -> list[float]:
    if any(v < 0 for v in values):
        raise ValueError("All hourly values must be non-negative")
    return values


def _total_kwh(values: list[float]) -> float:
    return sum(values)


def _by_month(values: list[float]) -> list[float]:
    result = []
    for i, hours in enumerate(_MONTH_HOURS):
        start = _MONTH_OFFSETS[i]
        result.append(sum(values[start : start + hours]))
    return result


class HourlyLoadProfile(BaseModel):
    """8760-element hourly consumption profile (kW).

    Source can be bill inference, e-distribuzione download, or synthetic profile.
    Used as input to the self-consumption matching engine.
    """

    values: list[float] = Field(description="8760 hourly consumption values in kW")
    source: str = Field(description="e.g. 'bill_inference', 'e_distribuzione', 'synthetic'")
    profile_type: str = Field(default="residential", description="e.g. 'residential', 'commercial'")
    potenza_impegnata_kw: float = Field(default=3.0, description="Committed power in kW")
    year: int = Field(default=2025, description="Reference year for tariff band classification")
    selected_archetype: str | None = Field(default=None, description="ID of the archetype selected during inference")

    @field_validator("values")
    @classmethod
    def check_values(cls, v: list[float]) -> list[float]:
        _validate_8760(v)
        _validate_non_negative(v)
        return v

    def total_kwh(self) -> float:
        return _total_kwh(self.values)

    def by_month(self) -> list[float]:
        return _by_month(self.values)

    def to_monthly_f1_f2_f3(self) -> list[dict]:
        """Classify each hour into F1/F2/F3 and return monthly band totals.

        Returns a 12-element list:
          [{"month": 1, "f1_kwh": ..., "f2_kwh": ..., "f3_kwh": ...}, ...]
        """
        # Pre-build band classification for each of the 8760 hours
        bands: list[TariffBand] = []
        for month_idx in range(12):
            month = month_idx + 1
            start = _MONTH_OFFSETS[month_idx]
            hours_in_month = _MONTH_HOURS[month_idx]
            days_in_month = hours_in_month // 24

            for day in range(1, days_in_month + 1):
                for hour in range(24):
                    dt = datetime(self.year, month, day, hour)
                    bands.append(classify_band(dt))

        result = []
        for month_idx in range(12):
            start = _MONTH_OFFSETS[month_idx]
            hours_in_month = _MONTH_HOURS[month_idx]
            f1 = f2 = f3 = 0.0
            for i in range(start, start + hours_in_month):
                band = bands[i]
                if band == TariffBand.F1:
                    f1 += self.values[i]
                elif band == TariffBand.F2:
                    f2 += self.values[i]
                else:
                    f3 += self.values[i]
            result.append({
                "month": month_idx + 1,
                "f1_kwh": round(f1, 6),
                "f2_kwh": round(f2, 6),
                "f3_kwh": round(f3, 6),
            })
        return result


class HourlyProductionProfile(BaseModel):
    """8760-element hourly PV production profile (kW).

    Typically sourced from PVGIS TMY data or Google Solar API.
    """

    values: list[float] = Field(description="8760 hourly production values in kW")
    lat: float = Field(description="Latitude of the installation")
    lon: float = Field(description="Longitude of the installation")
    kwp: float = Field(description="System size in kWp")
    tilt: float = Field(description="Panel tilt angle in degrees")
    azimuth: float = Field(description="Panel azimuth in degrees (0=south)")
    source: str = Field(default="pvgis_tmy", description="e.g. 'pvgis_tmy', 'google_solar'")

    @field_validator("values")
    @classmethod
    def check_values(cls, v: list[float]) -> list[float]:
        _validate_8760(v)
        _validate_non_negative(v)
        return v

    def total_kwh(self) -> float:
        return _total_kwh(self.values)

    def by_month(self) -> list[float]:
        return _by_month(self.values)


class HourlySelfConsumptionResult(BaseModel):
    """Result of hourly self-consumption matching between load and production.

    Contains per-hour arrays and pre-computed aggregates.
    """

    self_consumption_kw: list[float] = Field(description="8760 hourly self-consumption values in kW")
    grid_import_kw: list[float] = Field(description="8760 hourly grid import values in kW")
    grid_export_kw: list[float] = Field(description="8760 hourly grid export values in kW")
    battery_state_kwh: list[float] | None = Field(default=None, description="8760 hourly battery state in kWh (None if no battery)")
    monthly_summary: list[dict] = Field(description="12-element list with per-month totals")
    annual_self_consumption_kwh: float
    annual_grid_import_kwh: float
    annual_grid_export_kwh: float
    self_consumption_ratio: float = Field(description="0.0 to 1.0")

    @field_validator("self_consumption_kw", "grid_import_kw", "grid_export_kw")
    @classmethod
    def check_8760(cls, v: list[float]) -> list[float]:
        return _validate_8760(v)

    @field_validator("battery_state_kwh")
    @classmethod
    def check_battery_8760(cls, v: list[float] | None) -> list[float] | None:
        if v is not None:
            _validate_8760(v)
        return v
