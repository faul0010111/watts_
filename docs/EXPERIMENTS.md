# Experiments

```bash
make experiments                              # all eight
make experiment EXP=exp06_thermal_throttling  # one
```

Each writes `experiments/results/<name>.json` (full configuration and rows, for
re-analysis) and `<name>.md` (the table plus what the run showed). Findings are generated
from the numbers the run produced, so they cannot drift away from the data.

Every experiment fixes a seed, varies one factor, and holds everything else constant.
`run_seeds` repeats each configuration across seeds and reports the spread, so you can see
whether a difference is larger than the noise.

| # | Experiment | Question | Key output |
|---|---|---|---|
| 01 | `exp01_context_impact` | How does context size drive energy? | Wh/request vs input tokens; the slope is your prefill coefficient |
| 02 | `exp02_output_limit` | What does an output token cost? | Wh per additional output token; the decode coefficient |
| 03 | `exp03_dynamic_batching` | Does batching save energy, at what latency cost? | Wh/1k tokens and p95 across batch ceilings and wait windows |
| 04 | `exp04_model_routing` | Can routing save energy without breaking the quality floor? | routed vs always-large vs always-medium |
| 05 | `exp05_gpu_utilization` | How does utilisation change the cost of a token? | Wh/1k tokens vs arrival rate, and where the latency knee is |
| 06 | `exp06_thermal_throttling` | How does temperature affect efficiency? | temperature, clock, PUE and facility Wh/1k across cooling states |
| 07 | `exp07_energy_aware_scheduling` | Can deferral cut cost and carbon without delaying critical work? | cost and carbon per policy, and critical delay (always zero) |
| 08 | `exp08_security_constrained_optimization` | How do optimisation and security combine? | saving claimed unconstrained vs saving that survives policy |
| 09 | `exp09_prefill_vs_decode` | What does an output token actually cost, relative to an input token? | fitted prefill and decode coefficients, and the ratio between them |

## Manifests

Every result file carries a manifest: `experiment_id`, `version`, `schema_version`, `seed`,
`configuration`, `inputs`, `method`, `metrics`, `outputs`, `provenance` and the command that
reproduces it. A result without its manifest is a table of numbers whose origin the reader
has to take on trust.

## Design notes

**One factor at a time.** Experiment 01 holds output length constant so every difference
is prefill; experiment 02 does the reverse. Mixing them produces a number that cannot be
attributed to anything.

**Report both per-request and per-token.** They move in opposite directions as context
grows, and quoting only one is how people talk themselves into expensive decisions.

**Report facility energy, not only GPU energy.** Experiment 06 is the demonstration: with
cooling degraded, GPU energy per token barely moves while facility energy per token rises
by tens of percent.

**Let the run produce the claim.** Experiment 09 exists because "output tokens cost more"
was an assumption this repository repeated in its own documentation. The experiment sweeps
the ratio and fits the coefficients, so the number in the findings is the number the run
produced — and the same script against metered hardware produces the real one.

**Show what was blocked.** Experiment 08's result is not "security costs 1.5 kWh". It is
"the 1.5 kWh consists of these five changes, blocked by these four rules, and here is what
each would have done".

## Turning an experiment into a measurement

The scripts take a configuration and a workload. Swap the twin for a metered serving
endpoint (`docs/BENCHMARK.md`, "Running against real hardware") and the same scripts
produce measured results, with the provenance labels and disclaimers changing accordingly.
Experiments 01 and 02 double as the calibration procedure.
