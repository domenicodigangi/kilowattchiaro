"""Solar installation financial evaluation engine.

Public API: ``evaluate_solar``. Everything else re-exported here is
internal — tests import private helpers (``_npv_20yr``, etc.) for
unit-test coverage and the names are kept stable.

Phase graph (rendered by GitHub when viewing this file):

```mermaid
flowchart TD
  I[Inputs: consumption, kwp, options] --> P1[Phase 1: Resolve costs]
  I --> P2[Phase 2: Compute self-consumption]
  P2 -- TMY available --> H[Hourly match]
  P2 -- fallback or no TMY --> M[Monthly default ratio]
  H --> P3
  M --> P3
  P1 --> P3[Phase 3: Status-quo + price context]
  P3 --> P4[Phase 4: Solar cash-flow projection]
  P4 --> P5[Phase 5: Solar + status-quo ScenarioResults]
  P5 -. include_battery .-> P6[Phase 6: Battery projection + ScenarioResult]
  P5 --> P7[Phase 7: Assumptions dict]
  P6 --> P7
  P7 --> P8[Phase 8: Return SolarEvalResult]
```

The phase numbers match the ``# === Phase N — ... ===`` markers in
``orchestrator.py``. If you change the phase structure of
``evaluate_solar``, update both this diagram and those markers.
"""

from __future__ import annotations

from .cash_flow import (
    _battery_factor,
    _build_annual_cash_flow_projection,
    _cumulative_cash_flow,
    _legacy_cash_flows,
    _panel_factor,
)
from .models import (
    AnnualBreakdownEntry,
    AnnualCashFlowProjection,
    AnnualEnergyProfile,
)
from .npv import _estimate_irr, _npv_20yr, _npv_sensitivity
from .orchestrator import evaluate_solar
from .payback import _payback_years
from .scenario import (
    _annual_profile_from_monthly,
    _breakdown_to_dicts,
    _monthly_from_hourly_match,
    _scaled_price_trajectories,
    _solar_monthly,
    _status_quo_monthly,
)

__all__ = [
    "AnnualBreakdownEntry",
    "AnnualCashFlowProjection",
    "AnnualEnergyProfile",
    "evaluate_solar",
    # Internal helpers, re-exported for existing test imports.
    "_annual_profile_from_monthly",
    "_battery_factor",
    "_breakdown_to_dicts",
    "_build_annual_cash_flow_projection",
    "_cumulative_cash_flow",
    "_estimate_irr",
    "_legacy_cash_flows",
    "_monthly_from_hourly_match",
    "_npv_20yr",
    "_npv_sensitivity",
    "_panel_factor",
    "_payback_years",
    "_scaled_price_trajectories",
    "_solar_monthly",
    "_status_quo_monthly",
]
