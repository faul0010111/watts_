# Token model

## Why attribution, not measurement

A power meter reports device watts. It cannot report "the watts spent on token 412 of
request X". On shared, batched hardware the question does not even have a clean answer:
tokens from different requests are generated in the same forward pass.

So WATTS **measures energy per request or per window** and **attributes** it to tokens:

```
w_in  = input_tokens  × profile.prefill_weight
w_out = output_tokens × profile.decode_weight

input_energy  = E × w_in  / (w_in + w_out)
output_energy = E × w_out / (w_in + w_out)
```

The result carries `attribution_method`, naming the profile and its source. The request
total keeps the provenance of the underlying measurement; the split is always an
attribution.

## Prefill and decode

That an output token costs more than an input token is repeated everywhere, including
earlier versions of this document. `experiments/exp09_prefill_vs_decode.py` exists so it
stops being an assumption: it sweeps the input/output ratio across batch sizes, recovers the
marginal cost of each token type by least squares, and prints the ratio the run produced.
Run it against metered hardware and you get your ratio, not a remembered one.

The mechanism the experiment is testing:

* **Prefill** processes the whole prompt in parallel. It is compute bound and batches
  extremely well.
* **Decode** produces one token per step, re-reading model weights each time. It is
  memory-bandwidth bound, so per token it is far more expensive.

Two consequences that drive most of WATTS' recommendations:

1. Capping output length saves more energy than trimming an equal number of input tokens.
2. Long contexts are cheaper *per token* and more expensive *per request*. Reporting only
   Wh/token hides the cost of context growth — WATTS always reports Wh/request beside it.

## Metrics

| Metric | Meaning |
|---|---|
| `Wh / input token` | attributed prefill cost |
| `Wh / output token` | attributed decode cost |
| `Wh / total token` | blended, mix-dependent — never compare it across different mixes |
| `Wh / request` | the honest per-unit-of-work figure |
| `Wh / successful request` | the one that matters: failed work is pure waste |
| `Wh / successful task` | for agents, where one task spans many requests |
| `Wh / useful output token` | output tokens from successful requests only |
| `Wh / useful task` | energy per session that reached an outcome — the objective |
| `Wh / workload window` | everything the workload spent, useful or not |

WATTS counts a **task** as a session, not a request: an agent that needs forty calls to
answer one question has completed one task, and its energy belongs to that task.
`useful_work_fraction` reports the share of requests that succeeded, because the cheapest
optimisation available is usually to stop paying for failures.

`Wh/successful task` is the metric to optimise. An agent that halves its per-request
energy while doubling the number of requests it needs has made things worse, and only the
per-task view shows it.

## Model profiles

`services/token_engine/model_profiles.json`. Each profile declares prefill and decode
weights, maximum context, a quality tier, whether it is generative, whether it is approved
for sensitive data, and — importantly — `source` and `calibrated_on`.

`ProfileRegistry.get()` raises on an unknown model rather than guessing coefficients.
WATTS would rather refuse to report than report a fabricated number.

The shipped profiles are stand-ins for the simulator (`watts-sim-small`, `-medium`,
`-large`, `-embed`). They are not measurements of any vendor's model and are named so that
they cannot be mistaken for one.

## Token efficiency

`services/token_engine/efficiency.py` finds work that cost energy and produced nothing,
using metadata only — no prompt text is ever required:

| Finding | Signal | Usual fix |
|---|---|---|
| `duplicate_call` | same prompt hash, same model, inside the dedup window, no cache hit | per-tenant response cache |
| `repeated_context` | same context prefix hash across many requests | prefix / KV caching |
| `oversized_context` | input far above the median for the same task class | retrieval tuning, context compaction |
| `overlong_output` | output far above the median for the same task class | per-task output cap |
| `redundant_agent_loop` | a session repeating identical steps with no success | step budget, loop detector |
| `underused_context_window` | most requests use a fraction of the allocated window | shorter-window deployment, bigger batches |
| `failed_work` | request failed | fix the error rate first: it is free energy and a better SLO |

Every finding carries a stable `finding_id` (so it can be tracked across runs on the same
evidence), the provenance of its energy figure, its confidence, the evidence behind it, and
a recommended action. A finding without evidence is not emitted.

Findings can overlap, so the report exposes `wasted_energy_wh_upper_bound` with an
explicit note not to add them up as independent savings.

## Privacy

The record schema (`services/telemetry/schema.py`) rejects prompt text at the type
boundary: prohibited metadata keys, credential-shaped values and oversized strings all
raise `PrivacyViolation` at construction. Prompt identity travels as a salted, truncated
SHA-256 hash, so WATTS can say "this exact context was sent 412 times" while holding none
of it. Use a per-tenant salt: it prevents correlation across tenants and dictionary
attacks against common prompts.
