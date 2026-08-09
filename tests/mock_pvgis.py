"""Static mock responses for PVGIS and Google Solar connectors.

Used when MOCK_EXTERNAL_APIS=true to enable E2E testing without API keys.
"""

import math

from kilowattchiaro_engine.models.hourly import _MONTH_HOURS
from kilowattchiaro_engine.models.solar_eval import (
    GeoBoundingBox,
    GeoPoint,
    GoogleSolarResult,
    PVGISHourlyRecord,
    PVGISMonthly,
    PVGISResult,
    PVGISTMYResult,
    RoofSegment,
    SolarPanel,
)

# Typical Rome (41.9, 12.5) production for a 6 kWp system
MOCK_PVGIS_MONTHLY = [
    PVGISMonthly(month=1, e_m=420.3, h_m=78.5),
    PVGISMonthly(month=2, e_m=480.1, h_m=92.3),
    PVGISMonthly(month=3, e_m=620.5, h_m=125.8),
    PVGISMonthly(month=4, e_m=710.2, h_m=152.4),
    PVGISMonthly(month=5, e_m=830.4, h_m=185.6),
    PVGISMonthly(month=6, e_m=870.1, h_m=198.2),
    PVGISMonthly(month=7, e_m=910.3, h_m=212.5),
    PVGISMonthly(month=8, e_m=850.7, h_m=195.8),
    PVGISMonthly(month=9, e_m=690.2, h_m=148.3),
    PVGISMonthly(month=10, e_m=560.8, h_m=112.6),
    PVGISMonthly(month=11, e_m=430.6, h_m=82.1),
    PVGISMonthly(month=12, e_m=380.4, h_m=70.2),
]


def get_mock_pvgis_result(lat: float, lon: float, peakpower_kwp: float) -> PVGISResult:
    """Return static PVGIS data, scaled by peak power relative to 6 kWp base."""
    scale = peakpower_kwp / 6.0
    monthly = [
        PVGISMonthly(
            month=m.month,
            e_m=round(m.e_m * scale, 1),
            h_m=m.h_m,
        )
        for m in MOCK_PVGIS_MONTHLY
    ]
    yearly_kwh = round(sum(m.e_m for m in monthly), 1)
    return PVGISResult(
        monthly=monthly,
        yearly_kwh=yearly_kwh,
        location=f"{lat},{lon}",
    )


MOCK_GOOGLE_SOLAR_RESULT = GoogleSolarResult(
    segments=[
        RoofSegment(
            segment_index=0,
            tilt_degrees=30.0,
            azimuth_degrees=180.0,
            area_m2=45.0,
            yearly_energy_kwh=6800.0,
            center=GeoPoint(latitude=41.90018, longitude=12.50002),
            bounding_box=GeoBoundingBox(
                sw=GeoPoint(latitude=41.89998, longitude=12.49986),
                ne=GeoPoint(latitude=41.90038, longitude=12.50018),
            ),
        ),
        RoofSegment(
            segment_index=1,
            tilt_degrees=30.0,
            azimuth_degrees=270.0,
            area_m2=22.0,
            yearly_energy_kwh=2900.0,
            center=GeoPoint(latitude=41.89992, longitude=12.50024),
            bounding_box=GeoBoundingBox(
                sw=GeoPoint(latitude=41.89976, longitude=12.50008),
                ne=GeoPoint(latitude=41.90008, longitude=12.50042),
            ),
        ),
    ],
    panels=[
        SolarPanel(
            segment_index=0,
            center=GeoPoint(latitude=41.90008, longitude=12.49994),
            yearly_energy_kwh=260.0,
            orientation="LANDSCAPE",
        ),
        SolarPanel(
            segment_index=0,
            center=GeoPoint(latitude=41.90014, longitude=12.50004),
            yearly_energy_kwh=262.0,
            orientation="LANDSCAPE",
        ),
        SolarPanel(
            segment_index=0,
            center=GeoPoint(latitude=41.90024, longitude=12.50010),
            yearly_energy_kwh=258.0,
            orientation="LANDSCAPE",
        ),
        SolarPanel(
            segment_index=1,
            center=GeoPoint(latitude=41.89988, longitude=12.50016),
            yearly_energy_kwh=220.0,
            orientation="PORTRAIT",
        ),
        SolarPanel(
            segment_index=1,
            center=GeoPoint(latitude=41.89996, longitude=12.50030),
            yearly_energy_kwh=224.0,
            orientation="PORTRAIT",
        ),
    ],
    total_usable_area_m2=67.0,
    max_panel_count=26,
    max_kwp=10.4,
    yearly_energy_kwh=9700.0,
    carbon_offset_kg=3200.0,
)


def get_mock_google_solar_result() -> GoogleSolarResult:
    """Return static Google Solar Building Insights data."""
    return MOCK_GOOGLE_SOLAR_RESULT


def get_mock_pvgis_tmy_result() -> PVGISTMYResult:
    """Generate synthetic 8760 hourly TMY records for Rome (41.9N, 12.5E).

    Uses sinusoidal GHI curves with seasonal variation.
    Calibrated so 6 kWp * 0.86 efficiency yields ~7500-9500 kWh/year.
    """
    # Per-month: (sunrise_hour, sunset_hour, peak_ghi_wm2, avg_temp_c)
    _MONTH_PARAMS = [
        (7, 17, 300, 7),    # Jan
        (7, 18, 380, 9),    # Feb
        (6, 18, 480, 12),   # Mar
        (6, 19, 580, 15),   # Apr
        (5, 20, 700, 20),   # May
        (5, 21, 780, 24),   # Jun
        (5, 21, 800, 27),   # Jul
        (6, 20, 720, 27),   # Aug
        (6, 19, 560, 23),   # Sep
        (7, 18, 400, 17),   # Oct
        (7, 17, 310, 12),   # Nov
        (7, 17, 270, 8),    # Dec
    ]

    records: list[PVGISHourlyRecord] = []
    hour_index = 0

    for month_idx, hours_in_month in enumerate(_MONTH_HOURS):
        sunrise, sunset, peak_ghi, avg_temp = _MONTH_PARAMS[month_idx]
        days_in_month = hours_in_month // 24
        daylight_hours = sunset - sunrise

        for day in range(days_in_month):
            for hour in range(24):
                if sunrise <= hour < sunset:
                    # Sinusoidal curve between sunrise and sunset
                    frac = (hour - sunrise + 0.5) / daylight_hours
                    ghi = peak_ghi * math.sin(math.pi * frac)
                else:
                    ghi = 0.0

                # Daily temperature variation: +4C at 14:00, -4C at 05:00
                temp = avg_temp + 4.0 * math.sin(math.pi * (hour - 5) / 12)

                records.append(PVGISHourlyRecord(
                    hour_index=hour_index,
                    ghi_wm2=round(ghi, 1),
                    temperature_c=round(temp, 1),
                    wind_speed_ms=3.0,
                ))
                hour_index += 1

    return PVGISTMYResult(records=records, location="41.9,12.5")
