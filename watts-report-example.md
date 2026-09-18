# WATTS report — checkout-assistant

`run-63512fb856eb` · seed `42` · config `4ca9a88e13ba3509` · policy `watts-policy-1` · WATTS 0.2.0 · 2026-09-18 10:15:18Z

> **Every figure below is `simulated`.** It was produced by the digital twin on this machine, not measured on hardware. Relative comparisons under this configuration are meaningful; absolute watt-hours are not until the model profiles are calibrated (`docs/CALIBRATION.md`).

## Executive summary

- **Window:** 557 requests, 1,414,734 tokens over 305 s on 4 accelerators.
- **Energy:** 95.7 Wh facility (0.0677 Wh per 1k tokens, PUE 1.418 — facility and IT totals from a simulated source, divided).
- **Objectives:** all reliability objectives held.
- **Budgets:** tokens_per_hour at 139% of limit.
- **Proposed plan:** -7.8% energy per 1k tokens across 2 re-simulated step(s); summing the steps independently would have claimed -8.0%.
- **Executed:** nothing. the assistant may propose and simulate; approval and execution require a human with the operator role, so this run changes nothing

## SLO status

| Objective | Observed | Target | State |
|---|---|---|---|
| p95 latency | 5,102 ms | 12,000 ms | within |
| availability | 98.923% | 98.00% | within |
| error rate | 1.077% | 2.00% | within |
| Wh per successful request | 0.0683 | 0.50 | within |

Latency headroom remaining: **6,898 ms**. This is the currency every batching or consolidation change spends.

## Energy and token budgets

| Dimension | Projected hourly | Limit | Utilisation |
|---|---|---|---|
| tokens_per_hour | 16,690,291 | 12,000,000 | **139%** |

## Energy attribution

| Component | Wh | Share | Provenance |
|---|---|---|---|
| prefill | 6.8143 | 50.2% | simulated |
| decode | 6.7682 | 49.8% | simulated |

The request total carries the provenance of its measurement; the split between components is an attribution produced by the model profile and the declared coefficients. It is never a measurement of a component.

**Calibration status:** 0 of 4 model profiles calibrated. Uncalibrated: watts-sim-embed, watts-sim-large, watts-sim-medium, watts-sim-small.

## Token efficiency

- 2,540 tokens per request, 0.000027 Wh per token
- **0.4703 Wh per useful task** (80 tasks reached a successful outcome)
- 98.9% of requests succeeded; 6 failed

| Finding | Requests | Wasted Wh | Confidence | Recommendation |
|---|---|---|---|---|
| `WF-f96c6ebe86` repeated_context | 355 | 6.808 | 0.60 | enable prefix/KV caching for the shared context |
| `WF-7ccc79a065` duplicate_call | 44 | 3.755 | 0.90 | enable a per-tenant response cache keyed on the prompt hash |
| `WF-606826dd7c` failed_work | 6 | 0.311 | 0.95 | fix the error rate before tuning anything else: failed work is pure waste |
| `WF-a5cf02faa4` underused_context_window | 433 | 0.000 | 0.40 | deploy a shorter-context variant or raise the batch size |

_findings may overlap; do not sum them as independent savings._

## Energy anomalies and root cause

Comparison window: same workload with cooling capability degraded to 40%.

| Metric | Observed | Baseline | Robust z | Severity | Method |
|---|---|---|---|---|---|
| wh_per_1k_tokens | 0.09 | 0.06767 | +27.8 | page | median/MAD (robust) |
| gpu_temp_max_c | 68.86 | 43.85 | +77.0 | page | median/MAD (robust) |
| pue | 1.819 | 1.421 | +47.4 | page | median/MAD (robust) |
| tokens_per_s | 4636 | 4636 | +0.0 | none | relative change (MAD is zero) |
| latency_energy_ratio | 5.669e+04 | 7.539e+04 | +0.0 | watch | relative change (MAD is zero) |

**primary cause within simulation: cooling overhead (confidence 1.00). Facility power rose while IT power did not, so the overhead ratio moved rather than the workload. Validation required: facility telemetry: CRAC state, chilled water temperature, metered facility power.**


_correlation is graded, never promoted to causation automatically; every cause names the telemetry that would confirm it_

## Forecast

| Target | Horizon value | 80% interval |
|---|---|---|
| facility power (W) | 1,087.73 | 514.52 – 1,660.94 |
| tokens/s | 3,111.14 | 0.00 – 17,494.62 |
| GPU demand | 4.00 | 4.00 – 4.00 |
| cooling power (W) | 176.63 | 83.92 – 269.34 |

- **energy budget:** 6% chance of exceeding 1,800 within 3 min — unlikely but possible (normal approximation to the forecaster's 80% interval (σ ≈ 447.3)).
- **tokens budget:** 49% chance of exceeding 3,333 within 3 min — likely enough to plan for (normal approximation to the forecaster's 80% interval (σ ≈ 6825)).

## If nothing changes

| Dimension | Now | Projected | 80% interval | Limit | State |
|---|---|---|---|---|---|
| energy | 1,129.46 | 1,087.73 (-3.7%) | 514.52 – 1,660.94 | 1,800 | within limit |
| tokens | 4,636.19 | 3,111.14 (-32.9%) | 0.00 – 17,494.62 | 3,333 | breach possible |

Thermal: nominal: projected peak 43.8 °C. SLO: within objectives.

**Verdict:** doing nothing stays within limits on the central projection, but the upper bound crosses tokens.

## Energy–latency–quality frontier

Constraints: p95 latency ≤ 12,000 ms; availability ≥ 98.000%; error rate ≤ 2.00%; task_success_rate ≥ 0.900 (share of agent tasks that reached the intended outcome) on declared-example-evalset; energy ≤ 1,800 Wh/h; security policy: compliant.

| Configuration | Wh/1k tokens | p95 ms | Quality | Admissible |
|---|---|---|---|---|
| watts-sim-small | 0.0494 | 951 | 0.790 | no |
| watts-sim-medium | 0.0677 | 5,102 | 0.930 | yes |
| watts-sim-large | 0.1171 | 60,048 | 0.960 | no |

- `watts-sim-small` excluded: task_success_rate 0.790 is below the floor 0.900 (evaluated on declared-example-evalset, n=200)
- `watts-sim-large` excluded: p95 60,048 ms exceeds the SLO 12,000 ms

**Selected:** watts-sim-medium — chosen as the lowest-energy admissible configuration

## Optimisation plan

| # | Change | Δ at step | Cumulative | p95 after | Gate |
|---|---|---|---|---|---|
| 1 | Enable prefix caching | -4.5% | -4.5% | 4,979 ms | human approval required |
| 2 | Enable per-tenant response cache | -3.5% | -7.8% | 4,379 ms | human approval required |

Plan effect **-7.8%**; naive sum of the same steps -8.0%; interaction error +0.2 points. each step was re-simulated on the state left by the previous one; the interaction error is what summing independent estimates would have got wrong.

Final-state validation: **passed** (p95_within_slo, error_rate_within_limit, no_thermal_excursion, within_energy_budget).

Rejected candidates:
- **Widen the dynamic batching window** — energy change -0.2% does not clear the 1.0% threshold worth a change
- **Route by task quality tier** — WATTS-SEC-007: expected latency increase exceeds the remaining SLO headroom

## Security gate

| Proposal | Energy Wh | Security | Outcome |
|---|---|---|---|
| Enable prefix / KV caching for the shared system context | -6.808 | requires review | awaiting approval |
| Cache responses for repeated identical prompts | -3.755 | requires review | awaiting approval |
| Reduce the request failure rate before tuning anything else | -0.311 | none | approved |
| Deploy a shorter-context variant for this workload | +0.000 | none | awaiting approval |

## Energy FinOps

| Component | Cost |
|---|---|
| energy | USD 0.0099 |
| accelerator | USD 0.8137 |
| host infrastructure | USD 0.1017 |
| network | USD 0.0000 |
| storage | USD 0.0000 |
| **total** | **USD 0.9254** |

electricity billed separately from the accelerator rate; all lines add. Cooling electricity inside the energy line: USD 0.0018.

- USD 0.00065 per 1k tokens (of which 0.00001 is electricity)
- USD 0.00168 per successful task
- 36.4 gCO₂e — derived from energy × operator-supplied grid intensity; not a cost and not a measurement

## Audit chain

8 entries, chain **valid**, head `26466ca051a4fb70e29d0698…`, policy version `watts-policy-1`.

_hash chaining evidences sequence integrity; anchor the head externally for tamper resistance._

## Benchmark

From a previous run (None, provenance `simulated`):

| Strategy | Wh/1k tokens | p95 ms | SLO met |
|---|---|---|---|
| baseline | 0.0841 | 48,868 | no |
| dynamic_batching | 0.0841 | 36,441 | no |
| token_optimization | 0.0730 | 24,568 | no |
| model_routing | 0.0404 | 17,977 | no |
| combined | 0.0342 | 14,794 | yes |

## Experiments

- **exp01_context_impact** (simulated): Input grew 32x; energy per request grew 6.8x, so prefill cost is close to linear in context length while the fixed per-request cost dilutes.
- **exp02_output_limit** (simulated): Each additional output token costs about 0.1424 mWh in this configuration, against a 2,000-token input held constant.
- **exp03_dynamic_batching** (simulated): Lowest energy per token: batch<=16, wait 300 ms at 0.02243 Wh/1k (-19% against batch 1), with p95 latency -89%.
- **exp04_model_routing** (simulated): Routing cut energy per token by 55% against serving everything on the large model, while p95 latency moved -48%.
- **exp05_gpu_utilization** (simulated): Energy per token fell from 0.02569 Wh/1k at 0.25 rps to 0.01959 Wh/1k at 4.0 rps: the same idle power spread over more work.
- **exp06_thermal_throttling** (simulated): From the design point to the worst case, peak GPU temperature went from 46.8 C to 100.9 C and the mean clock factor from 1.000 to 0.736.
- **exp07_energy_aware_scheduling** (simulated): Critical inference started immediately under every policy (maximum delay 0 s). This is the invariant the scheduler exists to preserve.
- **exp08_security_constrained_optimization** (simulated): The policy engine blocked 5 of 8 candidates, leaving 860 Wh of the 2340 Wh an unconstrained optimiser would claim.
- **exp09_prefill_vs_decode** (simulated): Fitted on this run, one output token costs 12.5× what one input token costs — output tokens are more expensive here. The fit recovers 1.059e-05 Wh per input token and 1.323e-04 Wh per output token, with a per-request baseline of 0.00409 Wh (R² 0.938).

## Provenance summary

| Section | Provenance |
|---|---|
| observation | simulated |
| energy | simulated |
| attribution | simulated window, modelled split |
| forecast | derived from simulated history |
| frontier | simulated |
| plan | simulated |
| finops | estimated from operator-supplied rates |
| benchmark | available |
| experiments | available |

Weakest provenance across attributed components: **simulated**. Measured fraction: 0.0%. Calibrated fraction: 0.0%.

**No figure in this report was entered by hand. Every number was produced by this run or read from a results file written by a previous run, and each carries the provenance of its source.**

## Reproduction

```bash
python watts.py demo --seed 42
```

- run id `run-63512fb856eb`, seed `42`, configuration hash `4ca9a88e13ba3509`
- WATTS 0.2.0, revision `unknown`, policy `watts-policy-1`
- Python 3.12.3 on Linux x86_64

The same seed and configuration reproduce this run exactly. Any difference in the numbers above means the code, the configuration or the seed changed — which is the point of printing all three.

_Generated 2026-09-18 10:15:19Z by WATTS 0.2.0._