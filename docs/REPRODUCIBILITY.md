# Reproducibility

> If a number in this repository is not reproducible from a command in the README, that is a
> bug.

## The run context

Every report carries the six things needed to reproduce it:

```
run_id                 run-97ddc8efcd5a
seed                   42
configuration_hash     a41f0c9e7b2d5188
version                WATTS 0.2.0
code_revision          git short SHA
policy_version         watts-policy-1
```

plus the Python version, the platform and the exact command:

```bash
python watts.py demo --seed 42
```

Same seed, same configuration, same code → identical numbers. Any difference means one of
those three changed, which is exactly why all three are printed.

```bash
python watts.py demo --seed 42          # writes reports/watts-report-<run_id>.md and .json
python watts.py simulate --seed 42      # the underlying window only
make experiment EXP=exp09_prefill_vs_decode
python watts.py bench --seeds 11 12 13
```

## Determinism

The digital twin is seeded and fully deterministic: arrival times, task mix, token counts,
failures and duplicates all derive from one `random.Random(seed)`. `tests/test_simulation.py`
asserts that the same seed produces byte-identical summaries and that a different seed does
not, so a regression in determinism fails the build rather than being discovered later in a
confusing diff.

The configuration hash covers the whole `PipelineConfig`, so changing the arrival rate
changes the hash even when the seed does not change.

## Variance

One run is an anecdote. `run_seeds()` repeats a configuration across seeds and reports the
spread; the benchmark reports mean and standard deviation of Wh per 1k tokens per strategy.

**Read the spread before believing a difference.** If the seed-to-seed spread is larger than
the gap between two strategies, the gap is noise and the table is telling you nothing.

## Experiment manifests

Every experiment result carries a manifest:

```json
{
  "experiment_id": "exp09_prefill_vs_decode",
  "version": "1.0",
  "schema_version": "2",
  "seed": 11,
  "configuration": {...},
  "inputs": {...},
  "method": "...",
  "metrics": [...],
  "outputs": [...],
  "provenance": "simulated",
  "reproduce": "make experiment EXP=exp09_prefill_vs_decode"
}
```

An experiment result without its manifest is a table of numbers whose origin the reader has
to take on trust.

## Findings are generated, not written

Experiment findings are computed from the rows the run produced, in code, at the bottom of
each experiment file. They therefore cannot drift away from the data, and they cannot
survive a change in the twin's physics without changing too.

The same rule applies to the report: `services/reporting/render.py` contains no numeric
literals about results. Every figure in the generated document comes from the run.

## Parity between implementations

`parity/vectors.json` holds fixed inputs and the outputs the Python reference produces for
energy attribution, policy decisions, budget calculations and canonical JSON. Both
`tests/test_java_parity.py` and the JUnit test in `apps/api/` check against that one file,
so the two implementations cannot drift silently.

Regenerate the vectors only when the reference behaviour changed deliberately, in the same
commit as that change. Regenerating them to make a failing implementation pass removes the
only thing keeping the two honest.

## Status quoting

A result carries the status of everything it rests on:

* `simulated` unless a hardware adapter produced the telemetry;
* `uncalibrated` unless a registered hardware calibration session backs the model profile;
* `NOT RUN` where a benchmark or experiment has not been executed in this checkout.

`NOT RUN` is a real state that renders like any other, so nobody is tempted to fill the gap
with numbers from somewhere else.

## Publishing results

Publish, alongside any figures built on WATTS: hardware, serving runtime and version, model
and quantisation, batching configuration, workload mix, duration, seeds, inlet temperature,
whether the facility figure was metered or modelled, which profiles were calibrated, and the
run id and configuration hash. Anything less is not reproducible, whatever it says at the
top of the page.
