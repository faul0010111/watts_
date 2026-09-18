# Experiment 04 - Energy-aware model routing

**Research question.** Can model routing reduce energy without violating the SLO or the quality floor?

*Produced by the WATTS digital twin. Valid as a statement about the model under the stated configuration, not as a hardware measurement.*

| Strategy | Wh/1k tokens | Wh/req (ok) | Facility Wh | p95 ms | energy vs large | p95 vs large |
|---|---|---|---|---|---|---|
| always-large | 0.08546 | 0.21225 | 170.54 | 33987 | +0% | +0% |
| always-medium | 0.02665 | 0.06621 | 110.16 | 3646 | -69% | -89% |
| routed | 0.03868 | 0.09612 | 123.45 | 17545 | -55% | -48% |

## What the run shows

- Routing cut energy per token by 55% against serving everything on the large model, while p95 latency moved -48%.
- The saving comes from the task mix: classification and extraction do not need a frontier model, and reasoning still gets one. Routing is a mix decision, not a uniform downgrade.
- 'always-medium' is cheaper still, and is the wrong answer: it serves tier-5 reasoning on a tier-3 model. It is included to show what the router refuses to do.

## Manifest

- experiment `exp04_model_routing` v1.0 (schema 2)
- seed `11`, provenance `simulated`
- method: single-factor sweep on the WATTS digital twin, repeated across seeds
- metrics: strategy, wh_per_1k_tokens, wh_per_successful_request, facility_energy_wh, p95_latency_ms, energy_vs_large_pct, p95_vs_large_pct

## Reproduce

```bash
make experiment EXP=exp04_model_routing
```
