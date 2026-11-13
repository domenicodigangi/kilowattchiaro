"""Orientation-aware hourly production from the PVGIS TMY — engine release v1.

Uses recorded PVGIS v5.3 data for Pisa (``tests/fixtures/pvgis``): the /tmy
response and PVcalc monthly yields at 30 degrees for four orientations.
"""

from __future__ import annotations

import pytest

from mock_pvgis import parse_tmy_response
from kilowattchiaro_engine.pv_production import (
    FAIMAN_U0,
    FAIMAN_U1,
    SegmentProduction,
    faiman_cell_temperature,
    hourly_production_kw,
    segment_hourly_kw_utc,
    temperature_factor,
    utc_to_local,
)
from kilowattchiaro_engine.models.hourly import _MONTH_HOURS, _MONTH_OFFSETS
from reference_cases import PISA_LAT, PISA_LON, pisa_pvcalc_monthly, pisa_tmy

SOUTH, EAST, WEST, NORTH = 0.0, -90.0, 90.0, 180.0


def _segment(azimuth: float, kwp: float = 1.0) -> SegmentProduction:
    monthly = tuple(v * kwp for v in pisa_pvcalc_monthly(azimuth))
    return SegmentProduction(kwp=kwp, tilt_deg=30.0, azimuth_pvgis_deg=azimuth, monthly_kwh=monthly)


def _morning_share(values: list[float]) -> float:
    return sum(v for i, v in enumerate(values) if i % 24 < 12) / sum(values)


class TestMonthlyScaling:
    @pytest.mark.parametrize("azimuth", [SOUTH, EAST, WEST, NORTH])
    def test_each_month_matches_pvcalc(self, azimuth):
        segment = _segment(azimuth, kwp=6.0)
        hourly = segment_hourly_kw_utc(pisa_tmy(), PISA_LAT, PISA_LON, segment)
        for month_idx in range(12):
            start = _MONTH_OFFSETS[month_idx]
            month_sum = sum(hourly[start : start + _MONTH_HOURS[month_idx]])
            assert month_sum == pytest.approx(segment.monthly_kwh[month_idx], rel=1e-9)

    def test_annual_total_survives_the_move_to_local_time(self):
        segment = _segment(SOUTH, kwp=6.0)
        local = hourly_production_kw(pisa_tmy(), PISA_LAT, PISA_LON, [segment])
        assert sum(local) == pytest.approx(sum(segment.monthly_kwh), rel=1e-9)


class TestOrientationShape:
    """East roofs produce in the morning, west roofs in the afternoon."""

    def test_morning_share_orders_east_south_west(self):
        tmy = pisa_tmy()
        share = {
            az: _morning_share(hourly_production_kw(tmy, PISA_LAT, PISA_LON, [_segment(az)]))
            for az in (EAST, SOUTH, WEST)
        }
        assert share[EAST] > share[SOUTH] + 0.10
        assert share[SOUTH] > share[WEST] + 0.10

    def test_east_west_roof_is_the_sum_of_its_segments(self):
        tmy = pisa_tmy()
        east = hourly_production_kw(tmy, PISA_LAT, PISA_LON, [_segment(EAST, 3.0)])
        west = hourly_production_kw(tmy, PISA_LAT, PISA_LON, [_segment(WEST, 3.0)])
        both = hourly_production_kw(tmy, PISA_LAT, PISA_LON, [_segment(EAST, 3.0), _segment(WEST, 3.0)])
        assert both == pytest.approx([e + w for e, w in zip(east, west)])


class TestLocalTime:
    """PVGIS hours are UTC; the load profile runs on the Italian clock."""

    def test_winter_is_utc_plus_one_and_summer_is_utc_plus_two(self):
        utc = [0.0] * 8760
        utc[11] = 1.0  # 1 January 11:00 UTC
        utc[181 * 24 + 11] = 2.0  # 1 July 11:00 UTC
        local = utc_to_local(utc)
        assert local[12] == 1.0
        assert local[181 * 24 + 13] == 2.0
        assert sum(local) == 3.0

    def test_july_peak_hour_is_the_early_afternoon_on_the_local_clock(self):
        local = hourly_production_kw(pisa_tmy(), PISA_LAT, PISA_LON, [_segment(SOUTH)])
        by_hour = [0.0] * 24
        july = _MONTH_OFFSETS[6]
        for h in range(july, july + _MONTH_HOURS[6]):
            by_hour[h % 24] += local[h]
        assert by_hour.index(max(by_hour)) == 13


class TestFaimanTemperature:
    def test_formula(self):
        assert (FAIMAN_U0, FAIMAN_U1) == (26.9, 6.2)
        assert faiman_cell_temperature(30.0, 900.0, 2.0) == pytest.approx(30.0 + 900.0 / (26.9 + 12.4))
        assert temperature_factor(25.0) == 1.0
        assert temperature_factor(55.0) == pytest.approx(1 - 0.0038 * 30)

    def test_summer_midday_loses_more_than_winter_midday(self):
        tmy = pisa_tmy()
        segment = _segment(SOUTH)
        hourly = segment_hourly_kw_utc(tmy, PISA_LAT, PISA_LON, segment)

        def factors(month_idx: int) -> float:
            start = _MONTH_OFFSETS[month_idx]
            hours = [h for h in range(start, start + _MONTH_HOURS[month_idx]) if h % 24 in (10, 11, 12)]
            values = []
            for h in hours:
                r = tmy.records[h]
                if hourly[h] > 0.3:
                    values.append(temperature_factor(faiman_cell_temperature(r.temperature_c, 800.0, r.wind_speed_ms)))
            return sum(values) / len(values)

        assert factors(6) < factors(0) - 0.05


def test_negative_reanalysis_wind_is_clamped_to_zero():
    """PVGIS ERA5 wind can be slightly negative; parsing must not reject the TMY."""
    row = {"time(UTC)": "20210101:0000", "T2m": 6.5, "G(h)": 0.0, "Gb(n)": 0.0, "Gd(h)": 0.0, "WS10m": -0.04}
    data = {"inputs": {"location": {"irradiance_time_offset": 0.1746}}, "outputs": {"tmy_hourly": [row] * 8760}}
    tmy = parse_tmy_response(data, 43.72, 10.40)
    assert tmy.records[0].wind_speed_ms == 0.0
    assert tmy.irradiance_time_offset_h == pytest.approx(0.1746)
    assert tmy.has_components
