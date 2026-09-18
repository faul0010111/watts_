# Threat model

Scope: the WATTS control plane — SDK, gateway, engines, policy, dashboard. Out of scope:
the LLM serving stack itself, the facility BMS, and the host operating systems.

## Assets

| Asset | Why it matters |
|---|---|
| Telemetry records | reveal workload volume, shape and timing per tenant |
| Prompt and context hashes | identity of content, correlatable if salts leak |
| Energy and cost figures | commercially sensitive; drive automated decisions |
| Policy set | the only thing standing between an optimiser and a control |
| Audit chain | the record of who changed what |
| Workload HMAC keys | forge telemetry, impersonate a workload |
| Recommendation pipeline | a path to influence infrastructure changes |

## Trust boundaries

1. **Application → SDK.** The SDK takes counts, not messages, so an application cannot
   hand WATTS content even by mistake.
2. **SDK → Gateway.** Untrusted. Signed, replay-checked, plausibility-checked.
3. **Gateway → Engines.** Trusted after validation.
4. **Engines → Policy → Action.** Proposals only; policy decides admissibility; humans
   decide execution.
5. **Assistant → Everything.** Untrusted principal with a tool allowlist.

## Threats and mitigations

| # | Threat | Impact | Mitigation | Where |
|---|---|---|---|---|
| T1 | Forged low-energy telemetry to look efficient | wrong optimisation decisions, gamed chargeback | HMAC per workload, plausibility bounds | `telemetry/gateway.py` |
| T2 | Inflated telemetry against another tenant | reputational and cost damage | per-workload keys, tenant isolation, bounds | `gateway.py`, `WATTS-SEC-005` |
| T3 | Replay of old records | inflated volume, hidden regressions | request-id replay window, clock skew limit | `gateway.py` |
| T4 | Prompt injection inside telemetry fields | assistant proposes a harmful change | telemetry is data; assistant cannot approve or execute; tool allowlist | `watts_llm_guard.rego` |
| T5 | Assistant induced to route regulated data off-prem | compliance breach | per-request policy evaluation on routing | `WATTS-SEC-002` |
| T6 | Optimisation used to disable a control (shared cache, less logging) | isolation or audit loss | control weakening denied outright | `WATTS-SEC-003` |
| T7 | Energy optimisation causing an outage | availability loss | SLO gate, headroom rule, breaker, rate limit, rollback | `optimization/workflow.py` |
| T8 | Deferring critical work to a cheap hour | availability loss | criticality rule; interactive inference never deferred | `WATTS-SEC-006`, `scheduler/engine.py` |
| T9 | Cross-tenant telemetry aggregation | data leak | tenant isolation in policy and queries | `WATTS-SEC-005` |
| T10 | Audit tampering after a bad change | loss of accountability | hash chain, external anchoring of the head | `security/audit.py` |
| T11 | Prompt-hash correlation across tenants | content inference | per-tenant salts, truncated digests | `telemetry/schema.py` |
| T12 | Dashboard leaking one tenant's data to another | data leak | tenant-scoped queries; the static build embeds a single tenant's window | `apps/dashboard/` |
| T13 | Compromised HMAC key | forged telemetry at scale | per-workload keys, rotation, anomaly engine flags the discontinuity | deployment |
| T14 | Supply chain (dependencies) | arbitrary code execution | reference implementation has zero runtime dependencies | `pyproject.toml` |
| T15 | Denial of service by telemetry flood | ingestion loss | sampling in the SDK, rejection counters, backpressure at the gateway | `sdk/`, `gateway.py` |
| T16 | Audit history quietly deleted under a retention policy | loss of accountability with a chain that still verifies | `prune()` leaves a `SealedSegment` recording the range, count and continuation hash | `security/audit.py` |
| T17 | Optimisation adopted for a configuration whose quality was never evaluated | silent quality regression sold as an energy saving | unknown quality is inadmissible in the frontier, the planner and the benchmark | `optimization/quality.py` |
| T18 | Uncalibrated coefficients quoted as measured watt-hours | decisions and public claims built on model artefacts | calibration status travels with every profile; `absolute_wh()` returns `None` | `calibration/registry.py` |

## Residual risks

* **Attribution is model-dependent.** A wrong profile misattributes energy between input
  and output tokens, and FinOps allocation inherits that error. Request totals stay correct.
  Mitigation: calibrate (`docs/CALIBRATION.md`), and print the uncalibrated list in every
  report — which the generator does automatically.
* **Hashes are identifiers.** With a known salt and a guessable prompt set, an attacker can
  confirm whether a specific prompt was sent. Rotate salts; treat them as secrets.
* **Simulation is not reality.** The twin's constants are declared, not measured. Every
  output is labelled `simulated`, and the benchmark refuses to present it otherwise.
* **Human approval can be rubber-stamped.** WATTS records who approved what; it cannot make
  the review meaningful. Pair high-impact changes with a second approver in your process.
