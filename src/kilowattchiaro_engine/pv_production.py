"""Orientation-aware hourly PV production from the PVGIS TMY (engine release v1).

For each roof segment the hourly series is built from the direct and diffuse
components the PVGIS ``/tmy`` call already returns (no extra API calls):

1. Sun position for the TMY timestamp plus PVGIS's ``irradiance_time_offset``
   (the irradiance stamped HH:00 is for HH:10 UTC; checked by closure
   ``GHI = DNI cos z + DHI`` to 0.2% on the Pisa TMY).
2. Plane-of-array irradiance with the Hay-Davies sky model and a 0.2 ground
   albedo.
3. Cell temperature with the Faiman model PVGIS uses,
   ``T_cell = T_a + G_POA / (U0 + U1 * WS)`` (free-standing: U0 = 26.9,
   U1 = 6.2), and a power coefficient of -0.38 %/K.
4. Each month is scaled to that segment's PVcalc ``E_m``, so monthly and annual
   energy match PVGIS exactly; the TMY only supplies the hourly shape.

Segments are summed hour by hour, then moved from UTC to the local clock
(Europe/Rome, with daylight saving) the load profile uses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from .models.hourly import _MONTH_HOURS, _MONTH_OFFSETS
from .models.solar_eval import PVGISTMYResult

FAIMAN_U0 = 26.9  # W/m2K, PVGIS free-standing
FAIMAN_U1 = 6.2  # W s/m3K, PVGIS free-standing
POWER_TEMP_COEFF_PER_K = -0.0038
GROUND_ALBEDO = 0.2
_SOLAR_CONSTANT_WM2 = 1361.0
_MIN_COS_ZENITH = 0.0872  # 85 degrees: keeps the circumsolar ratio bounded at the horizon
_REFERENCE_YEAR = 2025  # non-leap, matches the load profile calendar
_LOCAL_TZ = ZoneInfo("Europe/Rome")


@dataclass(frozen=True)
class SegmentProduction:
    """One roof segment as PVGIS PVcalc sees it.

    ``azimuth_pvgis_deg`` uses the PVGIS convention: 0 = south, -90 = east,
    90 = west, 180 = north. ``monthly_kwh`` holds the 12 PVcalc ``E_m`` values
    for this segment at ``kwp``.
    """

    kwp: float
    tilt_deg: float
    azimuth_pvgis_deg: float
    monthly_kwh: tuple[float, ...]
    # PVcalc SD_y for this segment at ``kwp``; None when PVGIS gave none
    yearly_sd_kwh: float | None = None


def faiman_cell_temperature(ambient_c: float, poa_wm2: float, wind_ms: float) -> float:
    return ambient_c + poa_wm2 / (FAIMAN_U0 + FAIMAN_U1 * wind_ms)


def temperature_factor(cell_c: float) -> float:
    return 1.0 + POWER_TEMP_COEFF_PER_K * (cell_c - 25.0)


def _sun_geometry(day_of_year: int, utc_hours: float, lat_deg: float, lon_deg: float):
    """Declination, hour angle (radians) and cos(zenith), NOAA/Spencer series."""
    g = 2 * math.pi / 365 * (day_of_year - 1 + (utc_hours - 12) / 24)
    eot_min = 229.18 * (
        0.000075
        + 0.001868 * math.cos(g)
        - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g)
        - 0.040849 * math.sin(2 * g)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(g)
        + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g)
        + 0.00148 * math.sin(3 * g)
    )
    true_solar_min = utc_hours * 60 + eot_min + 4 * lon_deg
    hour_angle = math.radians(true_solar_min / 4 - 180)
    lat = math.radians(lat_deg)
    cos_z = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    return decl, hour_angle, cos_z


def _cos_incidence(decl, hour_angle, lat_deg, tilt_deg, azimuth_pvgis_deg) -> float:
    """Angle of incidence on a tilted plane (Duffie & Beckman eq. 1.6.2)."""
    lat = math.radians(lat_deg)
    beta = math.radians(tilt_deg)
    gamma = math.radians(azimuth_pvgis_deg)
    return (
        math.sin(decl) * math.sin(lat) * math.cos(beta)
        - math.sin(decl) * math.cos(lat) * math.sin(beta) * math.cos(gamma)
        + math.cos(decl) * math.cos(lat) * math.cos(beta) * math.cos(hour_angle)
        + math.cos(decl) * math.sin(lat) * math.sin(beta) * math.cos(gamma) * math.cos(hour_angle)
        + math.cos(decl) * math.sin(beta) * math.sin(gamma) * math.sin(hour_angle)
    )


def plane_of_array_wm2(
    tmy: PVGISTMYResult,
    lat: float,
    lon: float,
    tilt_deg: float,
    azimuth_pvgis_deg: float,
) -> tuple[float, ...]:
    """Hourly plane-of-array irradiance (Hay-Davies), UTC-indexed."""
    if not tmy.has_components:
        raise ValueError("TMY has no DNI/DHI components; cannot transpose to the roof plane")
    beta = math.radians(tilt_deg)
    sky_view = (1 + math.cos(beta)) / 2
    ground_view = (1 - math.cos(beta)) / 2
    poa: list[float] = []
    for i, r in enumerate(tmy.records):
        day_of_year = i // 24 + 1
        utc_hours = i % 24 + tmy.irradiance_time_offset_h
        decl, hour_angle, cos_z = _sun_geometry(day_of_year, utc_hours, lat, lon)
        dni = r.dni_wm2 or 0.0
        dhi = r.dhi_wm2 or 0.0
        ground = r.ghi_wm2 * GROUND_ALBEDO * ground_view
        if cos_z <= 0:
            poa.append(dhi * sky_view + ground)
            continue
        cos_aoi = max(0.0, _cos_incidence(decl, hour_angle, lat, tilt_deg, azimuth_pvgis_deg))
        extraterrestrial = _SOLAR_CONSTANT_WM2 * (1 + 0.033 * math.cos(2 * math.pi * day_of_year / 365))
        anisotropy = min(dni / extraterrestrial, 1.0)
        circumsolar_ratio = cos_aoi / max(cos_z, _MIN_COS_ZENITH)
        diffuse = dhi * (anisotropy * circumsolar_ratio + (1 - anisotropy) * sky_view)
        poa.append(dni * cos_aoi + diffuse + ground)
    return tuple(poa)


def _plane_series(
    tmy: PVGISTMYResult,
    lat: float,
    lon: float,
    tilt_deg: float,
    azimuth_pvgis_deg: float,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Plane-of-array irradiance and cell-temperature factor per hour, computed once per TMY and plane."""
    key = (lat, lon, tilt_deg, azimuth_pvgis_deg)
    series = tmy._plane_series.get(key)
    if series is None:
        poa = plane_of_array_wm2(tmy, lat, lon, tilt_deg, azimuth_pvgis_deg)
        factors = tuple(
            temperature_factor(faiman_cell_temperature(r.temperature_c, g, r.wind_speed_ms))
            for g, r in zip(poa, tmy.records)
        )
        series = tmy._plane_series[key] = (poa, factors)
    return series


def segment_hourly_kw_utc(
    tmy: PVGISTMYResult,
    lat: float,
    lon: float,
    segment: SegmentProduction,
) -> list[float]:
    """Hourly AC output of one segment, UTC-indexed, each month scaled to its PVcalc E_m."""
    poa, factors = _plane_series(tmy, lat, lon, segment.tilt_deg, segment.azimuth_pvgis_deg)
    kwp = segment.kwp
    shape = [kwp * g / 1000 * f for g, f in zip(poa, factors)]
    hourly = [0.0] * 8760
    for month_idx in range(12):
        start = _MONTH_OFFSETS[month_idx]
        end = start + _MONTH_HOURS[month_idx]
        raw = sum(shape[start:end])
        if raw <= 0:
            continue
        scale = segment.monthly_kwh[month_idx] / raw
        for h in range(start, end):
            hourly[h] = shape[h] * scale
    return hourly


@lru_cache(maxsize=1)
def _utc_to_local_index() -> tuple[int, ...]:
    """Local-clock hour slot (Europe/Rome) for each UTC hour of the reference year."""
    origin = datetime(_REFERENCE_YEAR, 1, 1, tzinfo=timezone.utc)
    year_start = datetime(_REFERENCE_YEAR, 1, 1)
    slots = []
    for i in range(8760):
        local = (origin + timedelta(hours=i)).astimezone(_LOCAL_TZ).replace(tzinfo=None)
        slot = int((local - year_start).total_seconds() // 3600)
        slots.append(slot % 8760)  # the last hour of the year wraps to 1 January
    return tuple(slots)


def utc_to_local(values_utc: list[float]) -> list[float]:
    """Move a UTC-indexed series onto the local clock, keeping every kWh.

    At the spring DST change one local slot gets nothing and at the autumn
    change one gets two hours; both fall at night.
    """
    local = [0.0] * 8760
    for value, slot in zip(values_utc, _utc_to_local_index()):
        local[slot] += value
    return local


def hourly_production_kw(
    tmy: PVGISTMYResult,
    lat: float,
    lon: float,
    segments: list[SegmentProduction],
) -> list[float]:
    """Local-clock hourly AC production of the whole roof (sum of its segments)."""
    total = [0.0] * 8760
    for segment in segments:
        for h, value in enumerate(segment_hourly_kw_utc(tmy, lat, lon, segment)):
            total[h] += value
    return utc_to_local(total)
