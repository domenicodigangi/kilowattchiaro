"""TMY-to-production conversion (pure).

The full PVGIS connector (HTTP, caching, retry) lives in the private
service layer; the engine needs only this deterministic transform.
"""

from .models.solar_eval import PVGISTMYResult


def tmy_to_hourly_kw(
    tmy: PVGISTMYResult, kwp: float, system_efficiency: float = 0.86
) -> list[float]:
    """Convert TMY GHI data to hourly kW production.

    Formula: kw = ghi * kwp * system_efficiency / 1000.
    Default efficiency 0.86 matches the system_loss=14% convention.
    """
    return [r.ghi_wm2 * kwp * system_efficiency / 1000 for r in tmy.records]
