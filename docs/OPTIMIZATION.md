# Optimisation

## From recommendations to plans

V1 produced a ranked list of recommendations. V2 produces a **plan**, because a ranked list
makes an error nobody notices: it assumes the effects are additive.

```
current state → generate candidates → simulate each → evaluate energy, latency, quality,
security, availability → build a plan → validate the final state
```

`services/optimization/planner.py` re-simulates after every accepted step and reports
`interaction_error_pct` — the gap between what summing the steps promised and what the plan
actually delivered. Prefix caching and a wider batch window both remove the same prefill
work; applied together they save far less than the sum of their individual savings, and that
gap is exactly what a spreadsheet of recommendations gets wrong.

Candidates are **simulated, not asserted**. Every delta comes from running the configuration
through an evaluator — the digital twin locally, a canary in production — and carries the
provenance of whatever produced it. The planner is a plain callable away from either, and
does not know which it is talking to.

The plan is validated on its **end state**, separately from the individual steps: a sequence
of individually safe changes can still land somewhere unsafe.

## Admissibility, not ranking

```
minimise   energy
subject to p95 latency ≤ SLO
           quality     ≥ floor
           availability ≥ SLO
           security    = compliant
```

`services/optimization/frontier.py` partitions candidates into admissible and inadmissible,
with a reason for every exclusion. An inadmissible candidate is not a worse option — it is
not an option. Among the survivors it exposes the Pareto front rather than collapsing three
axes into a score, because the remaining trade-off belongs to the operator and not to a
weighting WATTS picked quietly.

## Quality floors

WATTS cannot tell you whether a smaller model is good enough for your task. Nobody can, from
telemetry: quality is measured offline, on your own data, with a metric that suits the work
(`classification_accuracy`, `retrieval_recall`, `task_success_rate`, `evaluation_score`).

So the rule in `services/optimization/quality.py` is:

> A configuration with no quality observation does not meet the floor. It is unknown, and
> unknown is inadmissible.

The alternative — assuming a configuration is fine until proven otherwise — is how energy
optimisation quietly becomes quality regression. An observation resting on too few samples
is treated the same way. Enabling routing in production therefore requires an offline
evaluation first; that is a prerequisite, not a recommendation.

## If nothing changes

`services/optimization/no_action.py` answers the comparison every proposal implies: where
each tracked dimension lands on the current trajectory, against its limit, with its
interval, plus the thermal and SLO state. It separates *breach expected* from *breach
possible*, because those call for different responses. See `docs/FORECASTING.md`.

## Shape of a recommendation

Every proposal carries an expected impact on five axes, a confidence and the evidence that
produced it (`services/optimization/recommendations.py`):

```
Recommendation
  ├ expected energy impact   (negative = saving)
  ├ latency impact           (positive = slower)
  ├ quality impact           none | possible regression | improved
  ├ security impact          none | requires review | weakens control
  ├ confidence               low | medium | high
  └ evidence                 what was observed, in the telemetry
```

A recommendation with no evidence is not emitted. A recommendation is never applied by
WATTS: it is submitted to the workflow, evaluated by policy, and waits for a human.

## The levers

| Lever | Mechanism | Costs |
|---|---|---|
| Response cache | removes duplicate compute entirely | cache must be tenant-scoped; staleness |
| Prefix / KV cache | skips prefill of a shared context | memory; tenant scoping |
| Output cap | decode dominates energy; the tail is expensive | truncated answers if set badly |
| Context trimming | less prefill work | retrieval quality |
| Dynamic batching | amortises fixed per-step cost over more tokens | latency, via the wait window |
| Model routing | cheapest model meeting the quality floor | requires calibrated quality tiers |
| Consolidation | raises utilisation; idle power is amortised | queueing, so it spends SLO headroom |
| Scheduling | moves flexible work to cheaper or cleaner hours | only for deferrable work |
| Agent step budget | bounds what an unproductive loop can spend | may cut off legitimate long tasks |
| Fix the error rate | failed work is 100% waste | none — do this first |

Ordered by what the data usually shows: fix errors, then remove duplicate work, then cap
output, then batch, then route, then consolidate, then schedule. The first three cost
nothing in latency.

## Routing

`services/optimization/router.py` filters on hard constraints before it looks at energy:

1. availability of the candidate
2. quality tier ≥ the task's floor
3. context fits
4. **policy** (allowlists, data classification, tenant)
5. estimated p95 within the latency SLO
6. estimated energy within the energy budget

Only then does it minimise energy, tie-broken by higher quality and lower latency. If
nothing survives, it fails closed and returns every rejection with its reason. It never
relaxes a constraint to produce an answer.

The quality tier is the part you must supply. WATTS cannot tell you whether a small model
is good enough for your extraction task; that is an offline evaluation on your own data,
and it is a prerequisite for enabling routing in production.

## Scheduling

`services/scheduler/engine.py` places jobs against per-slot price, carbon intensity and
capacity. Deferral is offered only to work that is deferrable, non-critical and not
interactive inference, and only within its deadline. Critical work is placed first, at the
earliest free slot, under every policy.

Price weight and carbon weight are separate knobs because the cheapest hour and the
cleanest hour are usually different hours (experiment 07 shows the divergence). Choosing
between them is a policy decision, and WATTS does not make it for you: carbon weight is
zero by default.

## Anomalies and root cause

The anomaly engine watches energy per unit of useful work, not power, and **grades** causes
rather than ranking them (`services/energy_engine/rca.py`): primary, contributing, possible,
insufficient evidence. A hypothesis becomes primary only when it is well supported *and*
clearly ahead of the runner-up — two hypotheses at 0.45 and 0.42 are an unresolved question,
not a winner and a loser, and saying so is the honest output. Every cause names the
telemetry that would confirm it. Signatures:

| Signature | Ranked cause |
|---|---|
| temperature ↑, clock ↓ | thermal throttling |
| batch size ↓ | inefficient batching |
| new models in the mix | model change |
| PUE ↑ with IT flat | cooling overhead |
| utilisation flat, throughput ↓, no thermal signature | GPU degradation |
| token volume collapsed | workload change |
| nothing correlates | unexplained — escalate, do not optimise |

## Human in the loop

```
Recommendation → Impact analysis → Security validation → Human approval → Execution → Audit
```

Guarantees at each step: reliability SLOs must be healthy; policy must pass; the approver
must hold `optimization:approve`; the executor must hold `change:execute`; execution is
rate limited, breaker protected, and rolls back on failed validation. The whole path is
written to the hash chain.

## What WATTS will not do

* Choose the lowest-energy model regardless of quality.
* Defer or downgrade critical work for energy.
* Trade a security control for kilowatt-hours.
* Apply a change without a human, outside the narrow class of self-evidently safe ones
  (for example, "reduce the error rate", which is a recommendation to the owning team, not
  an infrastructure change).
* Report a saving it did not compute from a run.
* Treat an unevaluated configuration as one that meets the quality floor.
* Present the sum of independent estimates as the effect of applying them together.
