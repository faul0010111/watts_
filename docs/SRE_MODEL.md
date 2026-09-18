# SRE model

WATTS treats energy as an operational variable alongside latency, availability and error
rate — with one asymmetry that runs through the whole system:

> Reliability objectives constrain energy objectives. Never the reverse.

An energy target that is missed is a cost problem. A latency or availability target that
is missed because of an energy optimisation is an incident.

## Objectives

An `EnergySLO` (`services/optimization/budgets.py`) bundles four numbers:

```python
EnergySLO(
    name="checkout-assistant",
    p95_latency_ms=800,
    availability=0.999,
    max_error_rate=0.01,
    max_wh_per_successful_request=0.05,
)
```

`SLOReport` separates `violations` from `reliability_violations`. The workflow refuses to
apply any optimisation while a *reliability* violation is open: you do not tune energy
during an incident. An energy violation, by contrast, triggers the
detect → explain → recommend → require approval loop.

## Budgets

Per workload, any of: energy Wh/hour, tokens/hour, cost/hour, Wh per successful request.
The tracker projects the current window to an hourly rate and reports utilisation against
each limit, so a breach comes with the number that caused it rather than a bare alert.

```
Production agent
  energy budget   ≤ 50 kWh/hour
  token budget    ≤ 2M tokens/hour
  latency SLO     p95 < 800 ms
  energy SLO      ≤ 0.05 Wh per successful request
```

Breaching a budget never throttles traffic automatically. It produces an explanation and a
proposal that a human approves.

## Latency headroom as currency

Most energy optimisations buy watt-hours with milliseconds: a wider batching window, a
larger batch, a deferred job. `WATTS-SEC-007` makes that explicit — an optimisation whose
expected latency cost exceeds the remaining headroom (`p95 target − observed p95`) is
denied. Headroom is a budget like any other, and it is the one operators most often spend
without noticing.

## Golden signals, extended

| Signal | Metric | Where |
|---|---|---|
| Latency | p50 / p95 / p99 per task class | telemetry |
| Traffic | requests/s, tokens/s, batch size | telemetry |
| Errors | error rate, and the energy spent on failed work | efficiency engine |
| Saturation | GPU utilisation, queue depth, KV cache pressure | GPU telemetry |
| **Queueing** | queue wait vs service time, p50/p95/p99 of each | telemetry |
| **Energy** | Wh/request, Wh/1k tokens, Wh per successful task | energy + token engines |
| **Thermal** | peak temperature, clock factor, throttled fraction | thermal engine |
| **Facility** | PUE, cooling draw | energy engine |

## Queue wait versus service time

Latency decomposes into waiting and serving, and WATTS records both per request
(`queue_wait_ms`, `service_ms`). The split is what makes the batching trade-off legible:
widening a batch window buys energy with **queue time**, so if p95 is already dominated by
service time, widening it buys nothing and costs latency anyway.

`queue_share_of_latency` is the number to watch during any consolidation or batching change.
When it rises while throughput is flat, the change is spending SLO headroom without
producing anything.

## Alerting

Alert on efficiency per unit of work, not on power. A rack drawing more power because it
is doing more work is not an incident.

| Condition | Severity | First question |
|---|---|---|
| Wh per 1k tokens breaks its robust baseline (z ≥ 3.5) | page | which cause does the anomaly engine rank first, and what is its evidence? |
| PUE outlier against its own history | page | facility event, or IT load collapse? |
| Throttled fraction rising with flat throughput | page | cooling capability |
| Energy budget projected over limit | ticket | which workload, and which recommendation? |
| Failed-work energy above threshold | ticket | error rate, before any tuning |
| Profiles still uncalibrated in a published report | ticket | calibrate before quoting absolute figures |

## Runbook: energy anomaly

1. **Confirm the unit.** Is Wh per 1k tokens up, or only total power? Only the former is
   an anomaly.
2. **Read the graded causes** and the method that produced them
   (`services/energy_engine/rca.py`). A *primary* cause has a clear lead over the
   runner-up; with no primary cause, the field is contested and the report says which
   telemetry would separate the candidates. Every detection prints its window, baseline,
   estimator and threshold — a z-score without those is not evidence.
3. **Thermal throttling** — temperature up, clock down: check inlet temperature, airflow,
   cooling units. Facility problem.
4. **Inefficient batching** — batch size down: check queue depth, wait window, traffic
   shape change.
5. **Model change** — new models in the mix: check the routing configuration and
   deployments.
6. **Cooling overhead** — PUE up with IT flat: facility again.
7. **GPU degradation** — utilisation flat, throughput down, no thermal signature: check ECC
   errors, power capping, PCIe link width, a failing device.
8. **Workload change** — token volume collapsed: fixed idle power is being amortised over
   less work. Consolidate.
9. If nothing correlates, escalate to infrastructure and record the window for later
   analysis. Do not apply an optimisation to hide it.

## Retention

| Data | Retention | Why |
|---|---|---|
| Raw request records | 7–30 days | debugging and anomaly forensics |
| Per-minute aggregates | 13 months | year-over-year comparison |
| Benchmark and experiment outputs | indefinite | reproducibility |
| Audit chain | indefinite, append-only | accountability |
