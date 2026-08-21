# The quote-fairness engine — design

*"Is this quote fair?"* is KiloWattChiaro's second product moment. This
document describes how the answer is computed. **The code in this
chapter is a skeleton, deliberately not runnable** — the full
implementation (benchmark data, i18n, service wiring, evals) is part of
the private product. The evaluation engine it calls **is** published in
this repository; the agentic layer around it is private and runs the
production quote check at
[kilowattchiaro.it/preventivo](https://kilowattchiaro.it/preventivo).

## Two layers

1. **A deterministic comparison engine** (private module
   `app/engine/quote/`, same style as the published engine): given a
   structured quote and the user's own evaluation, it re-runs the
   published engine with the installer's proposed configuration on the
   user's actual roof and consumption, then judges price, production
   claims, sizing, and contract terms against benchmarks.
2. **An agentic reader** (Fly.io service): OCR + strict structured
   extraction turns the PDF into that structured quote; component claims
   are resolved against a curated, datasheet-pinned catalogue with a
   citation gate — **any technical claim that cannot be matched to a
   datasheet page is not asserted**. The agent never does the money
   math: it calls the deterministic engines over MCP.

## The deterministic layer, in skeleton

The orchestrator's real signature — the key design point is that the
comparison *re-evaluates*, it doesn't pattern-match:

```python
async def compare_quote(
    quote: InstallerQuote,
    original_request: SolarEvalRequest,
    original_result: SolarEvalResult,
    lang: str = DEFAULT_LANG,
) -> QuoteComparisonResult:
    """Compare an installer quote against the user's wizard evaluation.

    If the quote contains system_kwp, we re-run the evaluation with the
    installer's proposed configuration on the user's roof/consumption.
    """
```

The result model (abridged — the real one carries ~40 fields):

```python
class QuoteComparisonResult(BaseModel):
    # Cost: quoted €/kWp vs a regional (nord/centro/sud/isole) ×
    # size-band benchmark of (floor, central, ceiling) values
    quoted_total_eur: float
    our_cost_per_kwp_range: tuple[float, float]
    cost_verdict: Literal[
        "below_market", "competitive", "fair", "above_market", "overpriced"
    ]

    # Production realism: installer's annual kWh claim vs our PVGIS-based
    # estimate for the same roof
    their_annual_kwh: float | None
    our_annual_kwh: float
    production_verdict: Literal[
        "realistic", "optimistic", "unrealistic", "conservative"
    ] | None

    # Sizing, battery pricing, payback re-check, red flags …


class QuoteRedFlag(BaseModel):
    code: str
    severity: Literal["info", "warning", "critical"]
    title_it: str
    description_it: str
```

Red flags are seven deterministic check families — cost, production
claims, system sizing, payback plausibility, warranties (panels,
inverter, battery), VAT treatment, and missing tax-credit mention. Each
produces a typed, localized finding; none of them involves a model.

## What is deliberately not here

- **The benchmark matrix** (regional €/kWp bands, battery €/kWh bands) —
  the published [price rubric CSV](https://kilowattchiaro.it/open-data)
  (CC BY 4.0) carries the public reference values; the verdict
  calibration is the product.
- **The eval harness** that gates the agentic layer in CI (fault
  injection, SKU-resolution benchmarks, web-evidence gates) — part of
  the private agentic layer.
- **Service wiring**: upload grants, quotas, persistence, i18n.

The intent of this document is that a reader can judge the *design* —
re-evaluate rather than pattern-match, deterministic verdicts, citation
gate, typed red flags — while the calibrated data and glue that make it
a product stay private.
