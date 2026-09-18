# Architecture

## Data path

```
LLM applications
      │  counts, timings, hashes — never prompt or completion text
      ▼
LLM Telemetry SDK  (sdk/llm_telemetry, sdk/ts)
      ▼
Telemetry Gateway  (services/telemetry/gateway.py)   ← trust boundary
      │  HMAC per workload · replay window · clock skew · physical plausibility
      ▼
Stream (Kafka in production; in-process lists in the reference implementation)
      ▼
Energy Intelligence
 ┌──────────────┬──────────────┬─────────────┬────────────┬──────────────┐
 │ Token engine │ Energy engine│ SRE engine  │ Security   │ Forecasting  │
 │ attribution, │ components,  │ SLOs,       │ policy,    │ Holt, feature│
 │ efficiency   │ PUE, RCA     │ budgets     │ RBAC,audit │ models, risk │
 ├──────────────┼──────────────┴─────────────┴────────────┴──────────────┤
 │ Calibration  │ FinOps: energy vs infrastructure cost, allocation      │
 └──────────────┴───────────────────────────────────────────────────────┘
      ▼
Policy Engine (OPA/Rego; mirrored in services/security/policy.py)
      ▼
Optimisation Planner → candidates simulated, plan re-simulated per step → proposals only
      ▼
Human approval → execution → audit chain
      ▼
Command Center (apps/dashboard)
```

Two properties define the shape:

**The gateway is the only way in.** Everything downstream treats telemetry as ground
truth, so authenticity, freshness and plausibility are checked once, at the edge, and
rejected records are counted rather than dropped silently.

**Telemetry enters through one adapter interface.** Simulation, NVML, DCGM and external
meters all produce the same `PowerSample` stream and declare their own provenance, so the
code path that produces a report cannot know whether it was fed a simulation — and therefore
the two paths cannot drift apart.

**The policy engine sits between analysis and action.** No component reaches an
infrastructure API directly. The optimisation engine produces proposals; the policy engine
decides what is admissible; a human decides what happens.

## Control path

```
Recommendation → Impact analysis → Policy validation → Human approval → Execution → Audit
                                        │                                   │
                                   blocked here                      circuit breaker,
                                   is blocked, not                   rate limit, rollback
                                   traded against energy
```

`services/optimization/workflow.py` implements this, including the failure-safety
behaviour: if execution raises, the change rolls back and the breaker counts a failure;
after three failures the breaker opens and the safe default (change nothing) holds until a
cooldown passes. An energy control plane must never be able to cause an outage.

## Reference implementation vs production stack

| Concern | Reference (this repo, runs anywhere) | Production target |
|---|---|---|
| Services | Python packages under `services/` | Java 21 + Spring Boot (WebFlux), Spring AI, Spring Security |
| Transport | in-process | Kafka, OpenTelemetry OTLP |
| Storage | in-memory | PostgreSQL + TimescaleDB (time series), Redis (hot state) |
| Policy | `services/security/policy.py` | OPA sidecar evaluating `policies/*.rego` |
| Calibration | `services/calibration/` | same, fed by a metered calibration runner |
| GPU telemetry | digital twin | NVIDIA DCGM exporter / NVML |
| Dashboard | one static HTML file | Next.js + TypeScript + Tailwind over WebSockets |
| Deployment | `python watts.py` | Docker, Kubernetes, Terraform |

The reference implementation is not a prototype to be thrown away: it is the executable
specification. The attribution maths, the policy rules and the SLO semantics are defined
here and tested here, and the JVM services are expected to match.
`tests/test_policy_parity.py` enforces that for the policy set, and `parity/vectors.json`
does it for energy attribution, policy decisions, budget calculations and canonical JSON —
checked from both sides (`docs/REPRODUCIBILITY.md`).

## Extension points

* **Accelerators.** `simulation/twin.py:GPUSpec` and the telemetry `GPUSample` schema are
  vendor-neutral: utilisation, power, temperature, clock, memory bandwidth. NVML/DCGM is
  the first adapter; anything that reports those fields fits.
* **Model profiles.** `services/token_engine/model_profiles.json`. Add a profile, set
  `source: "calibrated"` and record the hardware in `calibrated_on`.
* **Grid signals.** `services/scheduler/engine.py:GridSignal` takes price, carbon intensity
  and renewable fraction per slot from whatever feed you have.
* **Policy.** Add a rule to `policies/*.rego` and its mirror in `services/security/policy.py`
  in the same commit.
