# Experiment 03 - Dynamic batching

**Research question.** Does dynamic batching reduce energy per request, and at what latency cost?

*Produced by the WATTS digital twin. Valid as a statement about the model under the stated configuration, not as a hardware measurement.*

| Configuration | Mean batch | Wh/1k tokens | p50 ms | p95 ms | tok/s | energy vs b1 | p95 vs b1 |
|---|---|---|---|---|---|---|---|
| batch<=1, wait 0 ms | 1.0 | 0.02753 | 27769 | 61148 | 6557 | +0% | +0% |
| batch<=4, wait 0 ms | 2.0 | 0.02276 | 2937 | 6311 | 7403 | -17% | -90% |
| batch<=8, wait 50 ms | 2.0 | 0.02270 | 2936 | 6277 | 7404 | -18% | -90% |
| batch<=16, wait 100 ms | 2.0 | 0.02266 | 2968 | 6321 | 7410 | -18% | -90% |
| batch<=16, wait 300 ms | 2.1 | 0.02243 | 3066 | 6422 | 7408 | -19% | -89% |
| batch<=32, wait 300 ms | 2.1 | 0.02243 | 3066 | 6422 | 7408 | -19% | -89% |

## What the run shows

- Lowest energy per token: batch<=16, wait 300 ms at 0.02243 Wh/1k (-19% against batch 1), with p95 latency -89%.
- Batching amortises fixed per-step cost over more tokens, so energy per token falls while the accelerator draws more power: total power up, energy per unit of work down.
- At this arrival rate the wait window never binds: the queue already holds enough requests, so a wider window changes neither batch size nor latency. The batch ceiling, not the window, is what mattered here.

## Manifest

- experiment `exp03_dynamic_batching` v1.0 (schema 2)
- seed `11`, provenance `simulated`
- method: single-factor sweep on the WATTS digital twin, repeated across seeds
- metrics: config, mean_batch_size, wh_per_1k_tokens, p50_latency_ms, p95_latency_ms, tokens_per_s, energy_vs_batch1_pct, p95_vs_batch1_pct

## Reproduce

```bash
make experiment EXP=exp03_dynamic_batching
```
