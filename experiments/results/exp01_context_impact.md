# Experiment 01 - Context size and energy

**Research question.** How does the size of the context influence consumption?

*Produced by the WATTS digital twin. Valid as a statement about the model under the stated configuration, not as a hardware measurement.*

| Input tokens | Wh/request | Wh/1k tokens | p95 ms | vs smallest | seed spread |
|---|---|---|---|---|---|
| 500 | 0.03677 | 0.05252 | 992 | +0% | 0.00011 |
| 1,000 | 0.04476 | 0.03726 | 1171 | +22% | 0.00017 |
| 2,000 | 0.06074 | 0.02757 | 1538 | +65% | 0.00031 |
| 4,000 | 0.09270 | 0.02203 | 2521 | +152% | 0.00058 |
| 8,000 | 0.15556 | 0.01893 | 6037 | +323% | 0.00352 |
| 16,000 | 0.24823 | 0.01529 | 17401 | +575% | 0.01026 |

## What the run shows

- Input grew 32x; energy per request grew 6.8x, so prefill cost is close to linear in context length while the fixed per-request cost dilutes.
- Energy per 1k tokens fell from 0.0525 to 0.0153 Wh: long contexts are cheaper *per token* and more expensive *per request*. Reporting only Wh/token hides the cost of context growth.
- Calibration: the slope of Wh/request against input tokens is the prefill coefficient for a model profile. Run this against metered hardware before quoting absolute energy.

## Manifest

- experiment `exp01_context_impact` v1.0 (schema 2)
- seed `11`, provenance `simulated`
- method: single-factor sweep on the WATTS digital twin, repeated across seeds
- metrics: input_tokens, wh_per_request, wh_per_1k_tokens, p95_latency_ms, energy_vs_smallest_pct, seed_spread_wh_per_request

## Reproduce

```bash
make experiment EXP=exp01_context_impact
```
