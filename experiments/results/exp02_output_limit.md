# Experiment 02 - Output token limits

**Research question.** What does an output token cost relative to an input token?

*Produced by the WATTS digital twin. Valid as a statement about the model under the stated configuration, not as a hardware measurement.*

| Output tokens | Wh/request | Wh/1k tokens | p95 ms | vs shortest |
|---|---|---|---|---|
| 32 | 0.03650 | 0.01793 | 987 | +0% |
| 64 | 0.04112 | 0.01989 | 1090 | +13% |
| 128 | 0.05035 | 0.02363 | 1299 | +38% |
| 256 | 0.06882 | 0.03046 | 1729 | +89% |
| 512 | 0.10577 | 0.04205 | 3111 | +190% |
| 1,024 | 0.17775 | 0.05871 | 7357 | +387% |

## What the run shows

- Each additional output token costs about 0.1424 mWh in this configuration, against a 2,000-token input held constant.
- Going from 32 to 1024 output tokens raised energy per request by 387% and p95 latency by 645%.
- An output cap is the cheapest energy lever available, but it is the one most likely to cut an answer short: validate quality on a held-out set per task class before applying it.

## Manifest

- experiment `exp02_output_limit` v1.0 (schema 2)
- seed `11`, provenance `simulated`
- method: single-factor sweep on the WATTS digital twin, repeated across seeds
- metrics: output_tokens, wh_per_request, wh_per_1k_tokens, p95_latency_ms, energy_vs_shortest_pct

## Reproduce

```bash
make experiment EXP=exp02_output_limit
```
