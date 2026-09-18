# WATTS

**An energy intelligence and optimisation control plane for AI infrastructure.**

WATTS connects the thing you bill for to the thing you pay for:

```
tokens → compute → GPU → energy → thermal → cooling → cost → carbon
                                    ↓
                            security + SRE policy
```

Conventional monitoring tells you a GPU drew 380 W. It does not tell you that the 380 W
went to 12,000 prompt tokens that had already been sent four times that hour, on a model
two tiers larger than the task needed. WATTS answers two questions:

1. **What does it cost, in energy, to produce one AI response?**
2. **How do we run the same workload on less energy without breaking security,
   availability or the SLO?**

Everything here runs on a laptop. A digital twin simulates the accelerators, the heat and
the cooling, so the whole pipeline — telemetry, energy attribution, efficiency analysis,
anomaly detection, forecasting, policy, approval, audit — is reproducible without a data
centre and without a GPU.

---

## Run it

Requires Python 3.10+. No dependencies, no services, no build step.

```bash
git clone <this repo> && cd watts

python watts.py demo --seed 42  # the whole control plane, end to end, and a full report
python watts.py bench           # strategy benchmark
python watts.py calibrate       # the calibration loop (against the twin, and it says so)
python watts.py dashboard       # build the Command Center as one HTML file
make test                       # 300+ tests
make experiments                # the nine reproducible experiments
```

`python watts.py demo` runs the whole pipeline over a simulated serving window and writes a
report — Markdown and JSON — in which **no number was typed by hand**:

```
observe → measure/estimate → attribute → explain → forecast → simulate →
optimise → validate security → request approval → audit
```

```
 observe    557 requests, 1,414,734 tokens, 1.08% errors
 measure    95.7 Wh facility, PUE 1.418 (simulated)
 attribute  0.0677 facility Wh / 1k tokens; 4 profiles uncalibrated
 ingest     494 accepted, forged record rejected (bad_signature)
 explain    4 efficiency findings; primary cause within simulation: cooling overhead…
 forecast   no-action breaches: none
 optimise   frontier selects watts-sim-medium; plan -7.8% over 2 step(s)
 validate   naive sum would have claimed -8.0% (interaction error +0.2 pts)
 approve    4 proposals, 0 blocked by policy, 0 executed
 audit      8 entries, chain valid
 cost       USD 0.00065 per 1k tokens (electricity billed separately)
 provenance weakest=simulated, measured=0%, calibrated=0%
```

(That is the actual output of `python watts.py demo --seed 42` on the default
configuration, not an illustration. Run it and you get the same numbers.)

The report ends with the run id, the seed, the configuration hash and the command that
reproduces it exactly.

---

## The idea in one screen

**Energy per token is the unit.** WATTS measures energy per request or per window — the
only thing a power meter can actually report — and *attributes* it to input and output
tokens using per-model prefill and decode weights. The attribution is labelled as an
attribution. Output tokens cost far more than input tokens, because decode runs one step
at a time while prefill batches the whole prompt.

**Every number carries its provenance.** Energy travels inside an `EnergyValue` — value,
unit, source, provenance, timestamp, confidence, calibration id — and the envelope is
awkward to strip on purpose: adding a metered reading to a modelled one yields the weaker
label, scaling a measurement yields `derived`, and asking a simulated value to be a
measurement raises. `measured` · `derived` · `estimated` · `simulated`.

**Calibrated means a session exists.** A model profile is calibrated if, and only if, a
registered measurement session on real hardware backs it (`services/calibration/`). Until
then `ModelProfile.absolute_wh()` returns `None` rather than a number, and every report
prints the list of uncalibrated profiles. `python watts.py calibrate` runs the loop against
the twin and produces a report headed **NOT A CALIBRATION**, because fitting the simulator
to its own output proves the fitting code works and nothing else.

**Quality is a constraint, and unknown is inadmissible.** WATTS cannot tell from telemetry
whether a smaller model is good enough for your task. A configuration with no offline
quality observation therefore does not meet the floor — it is unknown, and unknown is not an
option. Assuming otherwise is how energy optimisation quietly becomes quality regression.

**Effects are not additive.** The planner re-simulates after every accepted step and reports
the gap between what summing the steps promised and what the plan delivered. Prefix caching
and a wider batch window remove the same prefill work; a list of independent recommendations
gets that wrong every time.

**Security is not a trade-off axis.** An optimisation that would share a cache across
tenants, drop an audit control, downgrade a critical workload or spend latency the SLO
does not have is *blocked*, not weighed against the kilowatt-hours it would save
(`policies/`, `services/security/policy.py`). Experiment 08 measures what those controls
cost and why the constrained answer is the correct one.

**The model proposes, a human disposes.** The optimisation assistant can read telemetry,
run simulations and write proposals. It cannot approve, execute, change policy, or read
across tenants. Every step lands in a hash-chained audit log.

**Nothing is invented.** No benchmark table in this repository contains a number that was
not produced by a run, and every table says whether the run was simulated or measured.
Model coefficients ship as *declared defaults* and are marked uncalibrated until you
calibrate them on your own hardware.

---

## What is in the box

| Area | Where | What it does |
|---|---|---|
| Telemetry | `services/telemetry/` | Record schema that rejects prompt text at the type boundary; gateway with HMAC signatures, replay window and physical-plausibility checks; adapters for the twin, NVML, DCGM and external meters |
| Calibration | `services/calibration/` | Measurement sessions, coefficient fitting, MAE/RMSE/bias with intervals, and the status that decides whether watt-hours may be quoted |
| Energy model | `services/energy_engine/` | GPU + CPU + memory + network + storage + cooling overhead, per-component provenance, PUE, anomaly engine with ranked causes |
| Token engine | `services/token_engine/` | Prefill/decode attribution, efficiency findings, token economics (cost, GPU time, carbon per 1k tokens) |
| Optimisation | `services/optimization/` | Energy–latency–quality frontier with admissibility, quality floors, a planner that re-simulates each step, no-action projection, energy-aware router, SLOs and budgets, human-in-the-loop workflow with circuit breaker |
| FinOps | `services/finops/` | Energy cost kept apart from infrastructure cost, allocation by attributed energy share, carbon strictly downstream |
| Reporting | `services/reporting/` | The pipeline and the generated report, with a run context that reproduces it |
| Scheduling | `services/scheduler/` | Price- and carbon-aware placement that never defers critical or interactive work |
| Forecasting | `services/forecasting/` | Damped Holt, seasonal naive and feature models; energy, token, GPU-demand and cooling forecasts with residual intervals, walk-forward backtesting and budget-breach probability |
| Security | `services/security/`, `policies/` | Policy engine mirrored in OPA/Rego, RBAC, hash-chained audit log, guardrails for the assistant itself |
| Digital twin | `simulation/` | Serving queue, batching, thermals, throttling, cooling, power distribution, what-if scenarios |
| Benchmark | `benchmarks/` | Baseline vs batching vs token optimisation vs routing vs combined, with saturation warnings |
| Experiments | `experiments/` | Nine reproducible studies, each with a manifest |
| SDK | `sdk/` | Python and TypeScript middleware that sends counts and hashes, never content |
| Dashboard | `apps/dashboard/` | Command Center as a single self-contained HTML file |

## Two tracks

This repository is the **reference implementation and research harness**, written in
dependency-free Python so that anyone can read it, run it and check the numbers.

The **production control plane** targets the stack in `docs/ARCHITECTURE.md` — Java 21 and
Spring Boot behind Kafka, TimescaleDB, OPA and OpenTelemetry, with a Next.js dashboard.
`apps/api/` holds that scaffold. The reference implementation is the specification the JVM
services are built against: the policy rules, the energy model and the attribution maths
are identical, and `tests/test_policy_parity.py` keeps the policy definitions honest
across implementations.

## Research questions

Each one has an experiment that answers it from a run, in `experiments/`:

0. What does an output token actually cost, relative to an input token? → `exp09`
1. How much does energy per token vary between models? → `exp04`
2. How does context size drive consumption? → `exp01`
3. Does dynamic batching reduce energy per request, and at what latency cost? → `exp03`
4. Can model routing save energy without breaking the SLO or the quality floor? → `exp04`
5. Can energy be predicted from token volume and GPU metrics? → `exp05`, `services/forecasting/`
6. How does temperature affect computational efficiency? → `exp06`
7. What does cooling cost in an AI workload? → `exp06`
8. How do energy optimisation and security policy combine? → `exp08`

Output tokens, context size, batching and routing are covered by `exp01`–`exp04`;
utilisation, thermals, scheduling and security constraints by `exp05`–`exp08`; the
prefill/decode ratio itself, rather than the assumption about it, by `exp09`.

## Documentation

`docs/ARCHITECTURE.md` · `docs/ENERGY_MODEL.md` · `docs/TOKEN_MODEL.md` ·
`docs/CALIBRATION.md` · `docs/OPTIMIZATION.md` · `docs/SECURITY.md` ·
`docs/THREAT_MODEL.md` · `docs/SRE_MODEL.md` · `docs/FINOPS.md` ·
`docs/FORECASTING.md` · `docs/EXPERIMENTS.md` · `docs/BENCHMARK.md` ·
`docs/REPRODUCIBILITY.md` · `docs/RESEARCH.md` · `docs/CONTRIBUTING.md` ·
`docs/ROADMAP.md`

## Status and honesty

WATTS is **experimental**. The twin's constants are shaped like datacentre accelerators
but the throughput constant is arbitrary and fixes the model's time scale, nothing more.
Relative comparisons between strategies under the same configuration are meaningful.
Absolute watt-hours are not, until you calibrate against metered hardware
(`docs/CALIBRATION.md`). No model profile in this repository is calibrated, and every
report says so on its own front page.

If you find a number in this repository that is not reproducible from a command in this
README, that is a bug. Please open an issue.

## Licence

Apache-2.0. See `LICENSE`.
