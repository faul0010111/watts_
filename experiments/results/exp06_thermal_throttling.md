# Experiment 06 - Thermal throttling and efficiency

**Research question.** How does temperature influence computational efficiency, and what does cooling cost?

*Produced by the WATTS digital twin. Valid as a statement about the model under the stated configuration, not as a hardware measurement.*

| Cooling state | Peak C | Clock | Throttled | tok/s | GPU Wh/1k | Facility Wh/1k | Cooling Wh | PUE | facility vs design |
|---|---|---|---|---|---|---|---|---|---|
| design point (1.00 eff, 22 C inlet) | 46.8 | 1.000 | 0.0% | 7487 | 0.02254 | 0.04775 | 36.5 | 1.403 | +0% |
| reduced airflow (0.80 eff, 24 C inlet) | 55.6 | 1.000 | 0.0% | 7487 | 0.02316 | 0.05105 | 48.5 | 1.472 | +7% |
| fouled coils (0.60 eff, 27 C inlet) | 70.5 | 1.000 | 0.0% | 7487 | 0.02418 | 0.05697 | 70.4 | 1.593 | +19% |
| one unit down (0.45 eff, 30 C inlet) | 83.3 | 0.837 | 53.1% | 7428 | 0.02412 | 0.06245 | 96.7 | 1.766 | +31% |
| two units down (0.35 eff, 33 C inlet) | 100.9 | 0.736 | 71.2% | 7425 | 0.02424 | 0.06899 | 127.0 | 1.959 | +44% |

## What the run shows

- From the design point to the worst case, peak GPU temperature went from 46.8 C to 100.9 C and the mean clock factor from 1.000 to 0.736.
- PUE moved 1.403 -> 1.959. Facility energy per 1k tokens rose +44% while GPU energy per 1k tokens moved +8%: most of the damage is in the facility, not on the die, which is why a GPU-only view of efficiency misses it.
- Throughput fell to 7425 tok/s from 7487 tok/s at identical offered load, so the queue absorbs the difference and latency rises.
- Detection rule: temperature up, clock down, cooling draw up, throughput flat or falling is a facility problem, not a workload problem. WATTS' anomaly engine ranks it as thermal throttling on exactly this signature.

## Manifest

- experiment `exp06_thermal_throttling` v1.0 (schema 2)
- seed `11`, provenance `simulated`
- method: single-factor sweep on the WATTS digital twin, repeated across seeds
- metrics: cooling, max_gpu_temp_c, mean_clock_factor, throttled_fraction, tokens_per_s, wh_per_1k_tokens, facility_wh_per_1k_tokens, cooling_energy_wh, pue, facility_energy_vs_design_pct

## Reproduce

```bash
make experiment EXP=exp06_thermal_throttling
```
