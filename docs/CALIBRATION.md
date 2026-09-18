# Calibration

> A model is calibrated if, and only if, a registered measurement session on real hardware
> backs it. Everything else is uncalibrated, and every report that quotes it says so.

WATTS ships coefficients so that the pipeline has a shape. It does not ship the claim that
those coefficients describe your accelerators. Calibration is what converts "this strategy
uses 40% less energy than that one under this model" into "this strategy uses 40% less
energy on this hardware".

## The loop

```
digital twin → prediction → hardware measurement → comparison → calibration
                     ↑                                              │
                     └────────────── updated coefficients ──────────┘
```

`services/calibration/` implements each step:

| Step | Where |
|---|---|
| session recording | `session.py` — `MeasurementSession`, `CalibrationSample` |
| coefficient fitting | `fitting.py` — `fit_coefficients` |
| error analysis | `fitting.py` — `validate`, `compare_prediction` |
| status and registry | `registry.py` — `CalibrationRecord`, `CalibrationRegistry` |
| report | `report.py` — `calibration_report` |

## The model being fitted

```
Wh(request) = baseline + prefill · input_tokens + decode · output_tokens
```

Ordinary least squares on the 3×3 normal equations, solved by Gaussian elimination. No
dependencies; the whole computation can be checked by hand.

`baseline` is the term most often forgotten. It absorbs the fixed per-request cost —
scheduler overhead, tokenisation, the share of idle power the request occupies — and it is
why per-token figures look wrong at small token counts. Fitting without it pushes that
fixed cost into the prefill coefficient, which then over-predicts every long prompt.

## Procedure

1. **Fix everything except the factor you are sweeping.** One model, one accelerator type,
   one serving configuration, one batch size. Mixing factors produces a coefficient that
   cannot be attributed to anything.
2. **Sweep input at constant output** (`experiments/exp01_context_impact.py`).
3. **Sweep output at constant input** (`experiments/exp02_output_limit.py`).
4. **Record each run as a `CalibrationSample`** with the energy your meter reported. DCGM at
   100 ms or faster; a 10 s scrape averages away the prefill/decode structure you are
   trying to separate.
5. **Fit, then validate on data you did not fit on.** In-sample residuals are optimistic and
   the report says so on every line it prints them.
6. **Register the record.** `CalibrationRegistry.register` refuses a record whose validation
   did not pass, so a bad fit cannot quietly become the source of truth.
7. **Apply it to the profile.** `apply_to_profile` rewrites the weights, sets
   `source="calibrated"`, and records the hardware, runtime and date in `calibrated_on`.

```bash
python watts.py calibrate            # runs the loop against the twin
```

That command deliberately produces a report headed **NOT A CALIBRATION**. Fitting the
twin's coefficients to the twin's own output proves the fitting code works and nothing
else. Point the same loop at a metered endpoint and the same report becomes evidence.

## Reading the error analysis

| Metric | What it tells you |
|---|---|
| MAE | typical error in watt-hours |
| RMSE | error weighted towards the large misses |
| mean relative error | the number to quote; the acceptance bar is 10% |
| **bias** | the important one — see below |
| 95% interval for bias | whether the bias is real or noise |
| R² | how much of the variance the three terms explain |

**Bias matters more than MAE.** A fit that is randomly wrong by ±8% is usable. A fit that is
consistently 8% low is describing a different system: something correlated with the workload
is missing from the model, and batch size and context length are the usual suspects.
`ValidationResult.systematically_biased` is true when the bias exceeds half the MAE, and the
report prints a paragraph explaining what to look for.

## Coverage and extrapolation

Every record carries the span of shapes its session actually measured. `record.covers(in,
out)` answers whether a given request is inside that span. Outside it, the prediction is
extrapolation — the fit may be perfect at 100–4,000 input tokens and badly wrong at 120,000,
because attention cost is not linear across that range.

Report the coverage next to the coefficients. A 2% mean relative error over a range you
never sampled is not a 2% error.

## Status values

| Status | Meaning |
|---|---|
| `uncalibrated` | no session exists. Absolute watt-hours are model artefacts; relative comparisons under one configuration remain valid |
| `self-consistency-check` | a session exists but its samples are simulated. Proves the fitting procedure works, says nothing about hardware |
| `calibrated` | a hardware session exists and its validation passed |
| `calibration-failed` | a hardware session exists and its validation did not pass. Cannot be registered |

`CalibrationRegistry.summary()` returns the block every report must carry: which models are
calibrated, which are not, and the note that absolute figures from the latter are model
artefacts. The demo report prints it, the dashboard prints it, and
`ModelProfile.absolute_wh()` returns `None` rather than a number for any profile that has
never been calibrated.

## What to publish alongside a calibration

Accelerator model and count · serving runtime and version · model and quantisation · batch
configuration · sampling interval of the meter · ambient inlet temperature · the sampled
token range · whether the validation was in-sample or held out.

Without those, a coefficient is a number without a claim attached to it.
