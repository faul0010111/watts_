# Experiment 07 - Energy-aware scheduling

**Research question.** Can deferring flexible workloads reduce cost and carbon without delaying critical work?

*Produced by the WATTS digital twin. Valid as a statement about the model under the stated configuration, not as a hardware measurement.*

| Policy | Cost | Carbon kg | cost vs FIFO | carbon vs FIFO | Max defer h | Critical delay s | Unplaced |
|---|---|---|---|---|---|---|---|
| fifo | 28.73 | 54.6 | +0.0% | +0.0% | 1.0 | 0 | 0 |
| price_aware | 25.83 | 51.5 | -10.1% | -5.8% | 2.0 | 0 | 0 |
| price_and_carbon | 33.78 | 35.6 | +17.6% | -34.9% | 13.0 | 0 | 0 |

## What the run shows

- Critical inference started immediately under every policy (maximum delay 0 s). This is the invariant the scheduler exists to preserve.
- Price-aware deferral moved flexible work by up to 2 h and changed energy cost by -10.1% and carbon by -5.8% against FIFO.
- Adding carbon to the objective changed cost by +17.6% and carbon by -34.9%: the cheapest hour and the cleanest hour are not the same hour, so the weighting is a policy decision, not a technical one.
- Price and carbon curves here are illustrative inputs. Connect your market feed and grid intensity source before drawing conclusions about your own site.

## Manifest

- experiment `exp07_energy_aware_scheduling` v1.0 (schema 2)
- seed `11`, provenance `simulated`
- method: single-factor sweep on the WATTS digital twin, repeated across seeds
- metrics: policy, total_cost, total_carbon_kg, cost_vs_fifo_pct, carbon_vs_fifo_pct, max_defer_h, critical_delay_s, unplaced

## Reproduce

```bash
make experiment EXP=exp07_energy_aware_scheduling
```
