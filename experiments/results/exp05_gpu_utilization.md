# Experiment 05 - GPU utilisation and energy per token

**Research question.** How does utilisation change the energy cost of a token?

*Produced by the WATTS digital twin. Valid as a statement about the model under the stated configuration, not as a hardware measurement.*

| Arrival rate | tok/s | Mean batch | Wh/1k tokens | Facility Wh | PUE | p95 ms |
|---|---|---|---|---|---|---|
| 0.25 | 627 | 1.0 | 0.02569 | 78.8 | 1.494 | 3244 |
| 0.50 | 1159 | 1.0 | 0.02589 | 83.2 | 1.480 | 3358 |
| 1.00 | 2390 | 1.0 | 0.02666 | 94.0 | 1.453 | 3683 |
| 2.00 | 4973 | 1.3 | 0.02560 | 115.0 | 1.417 | 4875 |
| 3.00 | 7355 | 2.0 | 0.02269 | 129.2 | 1.402 | 6263 |
| 4.00 | 9857 | 3.1 | 0.01959 | 139.7 | 1.393 | 7607 |

## What the run shows

- Energy per token fell from 0.02569 Wh/1k at 0.25 rps to 0.01959 Wh/1k at 4.0 rps: the same idle power spread over more work.
- Facility energy rose from 78.8 Wh to 139.7 Wh over the same window. Absolute consumption and consumption per token move in opposite directions, which is why WATTS reports both.
- p95 latency went from 3244 ms to 7607 ms. Past the knee, further consolidation buys efficiency by spending the SLO.
- Operational reading: consolidate workloads onto fewer busy accelerators and power down the rest, rather than running a large fleet at low utilisation.

## Manifest

- experiment `exp05_gpu_utilization` v1.0 (schema 2)
- seed `11`, provenance `simulated`
- method: single-factor sweep on the WATTS digital twin, repeated across seeds
- metrics: rps, tokens_per_s, mean_batch_size, wh_per_1k_tokens, facility_energy_wh, pue, p95_latency_ms

## Reproduce

```bash
make experiment EXP=exp05_gpu_utilization
```
