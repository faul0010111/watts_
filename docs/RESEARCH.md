# Research

WATTS exists because the industry reports AI energy at the wrong granularity. Facility
totals and GPU utilisation are published; energy per unit of delivered work is not. Without
the second, "efficiency" claims cannot be compared, and optimisation cannot be evaluated.

## Position

1. **The unit of AI energy is the successful task, not the token.** A token-level figure
   flatters any system that generates more tokens to do the same job. WATTS reports Wh per
   successful request and Wh per successful task alongside Wh per token, and treats the
   per-task figure as the objective.
2. **Attribution must be declared.** Per-token energy on shared, batched hardware is
   always an attribution. Publishing it as a measurement is the most common error in this
   space.
3. **Efficiency is a facility property.** Cooling, inlet temperature and PUE change energy
   per token as much as batching does, and they are invisible to GPU-only tooling.
4. **Optimisation without policy is not optimisation.** An energy saving obtained by
   weakening isolation, dropping audit or degrading critical service is a regression
   recorded as a win.

## Open questions

| Question | Status in this repo | What is missing |
|---|---|---|
| What does an output token cost relative to an input token? | `exp09` recovers the ratio by least squares, under the twin | the same procedure against metered hardware |
| How much does Wh/token vary across models on identical hardware? | `exp04` under the twin | measured runs across real model families |
| Is the prefill/decode weight ratio stable across context lengths and batch sizes? | assumed constant | calibration data at several points |
| Can Wh be predicted from token counts and GPU metrics alone? | `exp05` plus the forecaster | measured validation, cross-hardware transfer |
| What is the marginal cooling cost of a marginal token? | `exp06` under the twin | facility-metered study at several inlet temperatures |
| Where is the efficiency-optimal utilisation point given an SLO? | `exp05` finds a knee | measured study; the knee is workload-specific |
| Does routing degrade quality in ways per-task evaluation misses? | out of scope | task-level evaluation methodology |
| How should energy be allocated to tenants in a shared batch? | attribution by compute weight, and FinOps allocation by energy share | fairness analysis, alternatives (Shapley-style allocation) |
| Do optimisation effects compose? | the planner measures the interaction error per plan | measured study; the size of the effect is configuration-dependent |

## Reproducibility

Every result in this repository comes from a command in the README, with the seed, the
configuration and the environment recorded in the output file. There are no numbers in the
documentation that were typed by hand from a remembered run — where a document refers to a
result, it points at the file and the command that produces it.

If you publish results built on WATTS, publish alongside them: hardware, serving runtime
and version, model and quantisation, batching configuration, workload mix, duration,
seeds, inlet temperature, whether the facility figure was metered or modelled, and which
model profiles were calibrated.

## Prior art and adjacent work

WATTS deliberately overlaps with, and does not replace:

* **GPU telemetry** (DCGM, NVML) — the measurement layer WATTS consumes.
* **Facility monitoring** (DCIM, BMS, PUE reporting) — the layer WATTS relates GPU energy
  to.
* **Carbon-aware scheduling** research — WATTS treats carbon as an operator-supplied
  signal, kept strictly downstream of energy.
* **Serving-level efficiency work** (continuous batching, paged attention, quantisation) —
  the mechanisms WATTS measures and recommends, not ones it implements.

The contribution is the join: tokens to compute to energy to heat to cooling to cost to
carbon, under security and reliability policy, in one observable system.

## Citing

```
WATTS: Energy Intelligence and Optimisation Control Plane for AI Infrastructure.
Open-source, experimental. https://github.com/<org>/watts
```

State the version or commit, and whether your figures are simulated or measured.
