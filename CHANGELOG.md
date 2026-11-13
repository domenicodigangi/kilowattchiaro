# kilowattchiaro-engine changelog

The engine's own changelog, published with every production release. The
package version follows the engine version (`ENGINE_VERSION`), which every
evaluation result carries as `engine_version`; a `.postN` release republishes
an engine whose code changed without moving any number.

## 1.1.1 — 2026-11-12

A failed PVGIS call on `/preventivo` is "could not check", not a flag (Wayfinder ticket
344, part 1). When the installer's system cannot be re-evaluated because the solar-data
service fails, the quote check no longer compares the quote with the originally evaluated
system, a different size and price. That comparison raised a false critical
"Produzione stimata molto superiore alla nostra stima" (+49% on the case in the engine
accuracy memo) and judged the claimed payback against the wrong system. Now the
production, payback and SSP checks do not run, the quote comparison carries
`production_check_available: false`, and the `/preventivo` report says under
"Dimensionamento" that the production check could not run. No `/solar` figure
and no golden quote figure moves; the snapshot only gains `production_check_available: true`
on every quote.

## 1.1.0 — 2026-11-12

One economics engine (Wayfinder ticket 312). The results page no longer recomputes NPV,
payback or the verdict in the browser: every figure and the verdict now come from the
engine. No engine figure moves (every golden scenario keeps its numbers); the engine adds
three outputs to each installed scenario:

- `verdict`: `positive` ("Sembra conveniente") when NPV is above zero and the system pays
  back within 20 years, `caution` when it saves money without a positive NPV, else
  `negative`. The rule is the one the browser applied, now in
  [`solar_eval/sensitivity.py`](src/kilowattchiaro_engine/solar_eval/sensitivity.py).
- `sensitivity`: the scenario re-run for the engine's five price scenarios (Riferimento,
  Alta elettrificazione, Rinnovabili abbondanti, Prezzi stabili, Crisi energetica) x the
  three cost tiers (basso, medio, alto). The price-scenario choice and the cost tier on
  the results page select a cell; a change of system or battery size still calls the
  engine. The cell of the request equals the scenario itself.
- `robustness`: "Robusto", "Dipende da: <input>" or "Fragile", from a grid of the
  detrazione (the applied rate and the lower one the rules can apply to the same home:
  paid by 31/12/2026, main home 50% to 36% and other homes 36% to 30%; paid from 2027 or
  with no date, no detrazione axis) x the five price scenarios x self-consumption 10 points of production lower. Robusto: no
  cell changes "conviene or not". Dipende da: only the named input, or the smallest
  combination, changes it. Fragile: two or more inputs each change it alone. No
  probabilities are printed. It is shown under the verdict and in the results email.

What a user sees moves, because the browser's own model is gone. It ignored the engine's
price trajectories, charged the full turnkey battery price as the mid-life replacement
(the engine charges the 55% cell-pack share and credits salvage) and applied its own
cash-flow shape. On the 24 golden cases the page showed an NPV below the engine's by
EUR 172-1,053 for PV only (median 471) and EUR 1,530-3,536 with a battery (median 3,091);
three battery cases read "Può convenire, ma va letto con cautela" where the engine says
"Sembra conveniente". Reference case (Pisa, 6 kWp + 10 kWh, 4,000 kWh/yr, paid from
2027): the page showed EUR 8,408 (PV only) and EUR 4,937 (with battery); it now shows the
engine's EUR 8,976 and EUR 8,028. The scenario presets "Prudente" and "Favorevole"
(browser multipliers on today's price and its growth) are replaced by the five price
scenarios, and the uncertainty band on the cumulative chart is now the range across them.
The payback shown is the engine's (one decimal, e.g. 6.6 years) instead of the first
whole year of positive cumulative cash flow.

## 1.0.1 — 2026-11-12

Assumptions register and one cost benchmark (Wayfinder ticket 314). Every money input
(the PV and battery cost bands, the ARERA typical bill, the ARERA price components, the
export capture factor, the RID minimum price and the detrazione rules) now lives in
[`backend/app/assumptions.yaml`](src/kilowattchiaro_engine/assumptions.yaml) with its source, the
date it was last checked, an owner and a review interval. The engine reads it, and
[/metodologia](https://kilowattchiaro.it/metodologia#registro-ipotesi) shows it with its
dates. CI fails every build when a value is overdue for re-checking, and warns when a
rule ends within 60 days or has already ended (on 2026-11-12 it warns for the ARERA
2026 bill, the RID 2026 minimum price and the detrazione rates paid by 31/12/2026).

The cost benchmark has one source: the regional bands the quote cost verdict was frozen
on (ticket 303, evidence
`docs/business/benchmark-calibration-memo-2026-10-01.md`).
Three other copies are gone: the flat EUR 1,400 / 1,700 / 2,500 per kWp used when no
location was known (now the Centro band for the size, EUR 1,050 / 1,350 / 1,750 at
3–6 kWp), the EUR 2,200 per kWp in the quick estimate, and the EUR 1,200 / 1,700 / 1,870
in the open-data price rubric and the price-index pages. No `/solar` figure and no
`/preventivo` cost verdict moves (all 24 golden cases and the 10 golden quotes keep their
verdicts); what moves:

| Where | Before | After |
|---|---|---|
| `/preventivo` re-evaluation price range (`reeval_solar_battery.investment_range_eur`), 8 of the 10 golden quotes | flat national band, regardless of the quote's location (e.g. quote 2, 6 kWp + 10 kWh: EUR 13,400–24,500, central 17,200) | the band of the quote's own region (quote 2, Centro: EUR 11,300–20,000, central 15,100, the same band its cost verdict uses) |
| Quick estimate (3 kWp, no account), payback | investment EUR 2,200/kWp (Florence, 4,500 kWh/yr: 19.2 years) | regional band central for 1–3 kWp (Florence, Centro EUR 1,450/kWp: 12.6 years) |
| Price-index pages, Pisa and Lucca (3–6 kWp) | EUR 1,200–1,870/kWp, central 1,700 | EUR 1,050–1,750/kWp, central 1,350 (Centro band) |
| Price-index page, Palermo (3–6 kWp) | EUR 1,200–1,870/kWp, central 1,700 | EUR 1,050–1,800/kWp, central 1,350 (Isole band) |
| Open-data price rubric | one national table, version 1.0 of 6 July 2026 | the engine's verdict thresholds for each region and size band, checked 1 October 2026 |
| Engine without a location (MCP `solar_evaluate`) | EUR 1,700/kWp for every size | Centro band for the size |

Rubric label a 3–6 kWp quote gets, before (national table) and after (the engine's own
verdict, which `/preventivo` already gave):

| EUR/kWp | Before (all regions) | After, Centro | After, Nord | After, Isole |
|---|---|---|---|---|
| 1,000 | sospettosamente basso | competitivo | competitivo | competitivo |
| 1,150 | sospettosamente basso | competitivo | competitivo | competitivo |
| 1,250 | competitivo | in linea | competitivo | in linea |
| 1,400 | competitivo | in linea | in linea | in linea |
| 1,500 | competitivo | sopra mercato | in linea | sopra mercato |
| 1,600–1,700 | in linea | sopra mercato | sopra mercato | sopra mercato |
| 1,800 | in linea | fuori mercato | sopra mercato | sopra mercato |
| 1,900 | sopra mercato | fuori mercato | fuori mercato | fuori mercato |

## Unreleased — golden snapshots (ticket 315)

No figure moves, so `ENGINE_VERSION` stays 1.0.0, the first version the snapshots are
stamped with. Adds the golden snapshot gate described above. Roofs are synthetic; no
Google Solar response is committed (Google's terms require cached Solar data to be
refreshed within 30 days). PVGIS responses (open JRC data) are recorded per location in
`backend/tests/golden/cassettes/`; the quotes run against a fixed component catalogue and
recall list in `backend/tests/golden/catalogue/`, so a catalogue update moves no golden figure.

## Unreleased — disclosure (ticket 313)

No figure moves, so `ENGINE_VERSION` stays 1.0.0. Results gain new `assumptions` keys:

- **Production range** (`production_range_halfwidth_pct`,
  `production_interannual_sd_pct`, `production_model_margin_pct`): PVGIS year-to-year
  SD as a P10–P90 band plus a 5% model margin. Absent when PVGIS returns no `SD_y`.
  Evidence: `docs/claims/production-range.md`.
- **Assumed inputs** (`roof_orientation_source`, `consumption_source`, `imagery_quality`,
  `detrazione_payment_date_assumed`, `investment_source`), echoed from the request so the
  results page can show a "Valore predefinito" chip for each. The request gains optional
  `consumption.source`, `roof.orientation_source` and `roof.imagery_quality`; Google Solar
  results gain `imagery_quality` and `default_segment`.

## 1.0.0 — 2026-10-28

First versioned release (Wayfinder ticket 311). Results before this date carry no
version. The before/after table for the reference cases is in
`docs/engine-release-v1-before-after.md`.

**Reference-case note.** On the ticket's reference case (Pisa, 6 kWp + 10 kWh,
4,000 kWh/yr) a lone north roof with a battery has NPV −EUR 378 when paid in 2027
(36%), with PV-only north at +EUR 3,162. Paid in 2026 (50%) it is +EUR 1,426 at 3%
and −EUR 926 at the 5% sensitivity, so the expectation that it goes negative holds
only under the 2027 rule once the cautious-side corrections ship with the rest. The
regression test pins a 2027 payment (owner decision, 2026-10-28); orientation
ordering (south > east > north) is tested under the 2026 rule.

**Rules and verdict-changing defects**

- **Detrazione by payment date and residence.** 50% for a main home paid by
  31 December 2026, 36% for other homes; from 1 January 2027, 36% and 30%. The cap
  is EUR 96,000 of eligible spend. The wizard assumes a main home unless told
  otherwise. Without a payment date the estimate assumes a payment from 2027
  (36% main home, 30% other homes), the cautious side given installation lead
  times (owner decision, 2026-11-01). The results page, the results email and the
  narrative say so, and say that paying by 31/12/2026 can mean a higher deduction
  (50% main home, 36% other homes).
- **Orientation-aware hourly production.** Each roof segment's hourly output is
  computed on its own tilted plane from the PVGIS TMY direct and diffuse irradiance
  (Hay-Davies), corrected for cell temperature (Faiman, from air temperature and
  wind), and scaled month by month to that segment's PVGIS PVcalc yield. Segments are
  summed and moved from UTC to the Italian clock. Multi-segment roofs no longer fall
  back to fixed 30%/70% self-consumption ratios.
- **Archetype from the F1/F2/F3 split.** When the split comes from a bill it alone
  picks the load archetype; the annual-kWh rule that put every 4,000 kWh household on
  the summer-peaking air-conditioning profile is gone. Without a split the load is the
  average of the seven archetypes.
- **Avoided kWh at the variable tariff.** Fixed charges (about EUR 155 a year) are not
  avoided by PV, so savings no longer count them.

**Cautious-side corrections, shipped together with the above**

- Default tariff refreshed from ARERA (29 September 2026): EUR 0.255/kWh variable plus
  EUR 155/yr fixed, instead of EUR 0.25 all-in.
- Exported energy valued at the solar-weighted market price after tax (about
  EUR 0.076/kWh in year 0, checked against GME CNOR hourly prices for 2025) instead of
  the EUR 0.0475 minimum guaranteed price. The minimum stays as a floor.
- PVGIS called with free-standing mounting instead of building-integrated (about +4%
  production).
- Investment defaults come from one source, the regional benchmark bands. The
  price tier chosen on the results page (basso/medio/alto) is sent as the tier, and
  the engine prices it from the floor/central/ceiling of the bands for the size being
  evaluated, so every size (and every recommendation candidate) gets its own band.

**Model fixes from the 2026-10-27 openmod audit**

- Hourly-resolution bias: 1-hour averaging overestimates direct PV self-consumption
  (it does not underestimate it, as the methodology said). Direct use is de-rated by
  10%.
- Load energy is conserved when peaks are capped: the hourly load sums exactly to the
  annual consumption.
- Cell temperature and per-segment scaling as above.
- Battery replacement priced at the cell-pack cost (55% of the turnkey price), with the
  unused life credited at year 20.
- Export price trajectory starts from the real wholesale base; maintenance grows with
  inflation.
- Battery: 90% usable depth of discharge, inverter output capped at min(kWp, 6 kW),
  about 20 W of standby draw (about 175 kWh a year), and a state of charge that ends
  the year where it starts.

**Labels and checks**

- The 3% discount rate is labelled as nominal (it is applied to nominal cash flows);
  the NPV at 5% nominal is shown alongside.
- Possible shading is flagged when Google Solar's per-segment yield is more than 15%
  below PVGIS for the same segment.
