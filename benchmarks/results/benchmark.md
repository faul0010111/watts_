# WATTS Benchmark (simulated)

These figures come from the WATTS digital twin. They characterise the model's response to each strategy and must not be quoted as hardware measurements. Run the same harness against a metered GPU to produce measured results.

**Admissibility.** A strategy counts as a result only if it met the SLO, the quality floor and security policy. Energy per token from an inadmissible strategy is not a saving, it is a cost moved somewhere the meter cannot see.

| Strategy | Wh/1k tok | Wh/req (ok) | Facility kWh | tok/s | p95 ms | batch | PUE | err | vs base | req/s | Quality | Security | SLO | Admissible |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 0.0841 | 0.2125 | 0.7317 | 4815 | 48868 | 3.4 | 1.396 | 1.005% | +0.0% | 1.93 | meets floor | True | False | False |
| dynamic_batching | 0.0841 | 0.2126 | 0.7338 | 4819 | 36441 | 3.7 | 1.397 | 1.004% | +0.0% | 1.93 | meets floor | True | False | False |
| token_optimization | 0.0730 | 0.1819 | 0.6702 | 4813 | 24568 | 2.1 | 1.405 | 1.004% | -13.2% | 1.95 | meets floor | True | False | False |
| model_routing | 0.0404 | 0.1021 | 0.5343 | 4953 | 17977 | 1.8 | 1.439 | 1.004% | -52.0% | 1.98 | meets floor | True | False | False |
| combined | 0.0342 | 0.0852 | 0.5069 | 4878 | 14794 | 1.4 | 1.448 | 1.004% | -59.4% | 1.98 | meets floor | True | True | True |

## Warnings

- **baseline**: SLO not met: p95 latency 48868 ms > 15000 ms
- **dynamic_batching**: SLO not met: p95 latency 36441 ms > 15000 ms
- **token_optimization**: SLO not met: p95 latency 24568 ms > 15000 ms
- **model_routing**: queue-bound: p95 is more than 4x p50, the run measures queueing as much as efficiency
- **model_routing**: SLO not met: p95 latency 17977 ms > 15000 ms
- **combined**: queue-bound: p95 is more than 4x p50, the run measures queueing as much as efficiency
