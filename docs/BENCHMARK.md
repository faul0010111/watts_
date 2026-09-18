# Benchmark

```bash
python watts.py bench                                  # defaults
python benchmarks/harness.py --duration 900 --rps 2 --gpus 8 --seeds 11 12 13
```

Outputs `benchmarks/results/benchmark.json` and `benchmark.md`.

## Rules

1. **No invented numbers.** The harness contains no hard-coded results. Every figure comes
   from a run.
2. **Provenance travels with the number.** Runs against the twin are labelled `simulated`
   in every record, row and file, with a disclaimer that they must not be quoted as
   hardware measurements.
3. **Saturation is disclosed.** If a strategy is queue-bound (p95 more than 4× p50), the
   report says the comparison is measuring queueing as much as efficiency.
4. **SLO state is reported beside energy.** A strategy that saves energy and misses the
   SLO has not won.
5. **Variance is reported.** Multiple seeds give mean and spread for Wh per 1k tokens.
6. **Admissibility decides what counts as a result.** A strategy is a result only if it met
   the SLO, cleared the quality floor and passed security policy. Energy per token from an
   inadmissible strategy is not a saving; it is a cost moved somewhere the meter cannot see.
7. **A benchmark that has not been run says `STATUS: NOT RUN`.** `not_run()` returns a report
   shaped like a real one, so consumers render it without special casing and nobody is
   tempted to fill the gap with numbers from elsewhere.

## Strategies

| Strategy | What changes |
|---|---|
| `baseline` | opportunistic batching up to 4, one large model, no caching — a serving stack out of the box |
| `dynamic_batching` | batch up to 16 within a 200 ms window |
| `token_optimization` | prefix cache, response cache, per-task output caps |
| `model_routing` | cheapest model meeting each task's quality tier |
| `combined` | routing + batching + token optimisation |

## Metrics

kWh · Wh/1k tokens · Wh/request · Wh/successful request · tokens/s · requests/s · p95
latency · queue share of latency · mean batch size · PUE · error rate · peak temperature ·
throttled fraction · **quality** · **security compliance** · SLO met · **admissible**.

Quality is an operator input, not something WATTS measures from telemetry: pass a figure per
strategy from your own offline evaluation. A strategy with no quality observation is printed
as `not evaluated` and is **not** admissible where a floor is declared — "we never measured
this" and "this is fine" must not produce the same verdict.

Security compliance is evaluated, not assumed: the models each strategy actually used are
run through the policy engine against the allowlist you supply.

## Reading a result

A run of the default configuration (8 accelerators, 2 requests/s, 900 s, three seeds,
digital twin) produced the table in `benchmarks/results/benchmark.md`. Reproduce it with
the command above; do not quote the numbers from this document, quote your own run.

Things to check before believing any row:

* Is the baseline saturated? If the warnings list it as queue-bound, the "improvement"
  partly reflects a collapsing baseline rather than better efficiency.
* Did the strategy meet the SLO? Energy per token is meaningless if p95 doubled.
* Is the spread across seeds smaller than the difference between strategies? If not, the
  difference is noise.
* Is the strategy admissible? A row that saves 52% and misses the SLO has not won anything.
* Are the model profiles calibrated? If `CalibrationRegistry.summary()` lists uncalibrated
  models, the absolute watt-hours are model artefacts. Relative comparison remains valid.

## Running against real hardware

The reporting path is identical; only the data source changes.

1. Serve a real model (vLLM, TGI, Triton) and expose token counts per request.
2. Instrument the client with `sdk/llm_telemetry`, sending counts and hashes.
3. Run a DCGM exporter at 100 ms resolution or faster; slower scrapes average away the
   prefill/decode structure.
4. Point the gateway at both, with a per-workload HMAC key.
5. Replace the twin in the harness with the measured window; provenance flips from
   `simulated` to `measured`/`derived` and the disclaimer changes with it.
6. Calibrate the model profiles first (`docs/ENERGY_MODEL.md`, "Calibration"), otherwise
   the input/output split stays an estimate on top of measured totals.

Report, alongside every measured result: accelerator model and count, serving runtime and
version, model and quantisation, batching configuration, request mix, duration, ambient
inlet temperature, and whether the facility figure came from a meter or a model.
