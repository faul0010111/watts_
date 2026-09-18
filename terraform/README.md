# Terraform

Module layout for the WATTS control plane's own dependencies.

```
terraform/
├── main.tf                 stream, time-series store, policy bundle, per-workload keys
└── modules/
    ├── stream/             Kafka-compatible topics and retention
    ├── timeseries/         TimescaleDB with hypertables and retention policies
    └── policy/             OPA bundle distribution
```

`modules/` is intentionally left as an interface for you to fill: how you run Kafka,
Postgres and OPA is a house decision, and `main.tf` shows what WATTS needs from each.

Two choices that are not negotiable if you want the guarantees in `docs/SECURITY.md`:

* **One HMAC key per workload.** A compromised key must forge telemetry for that workload
  only. `main.tf` generates them and stores them in a single Kubernetes Secret; rotate on a
  schedule and treat the secret as tier-0.
* **Audit anchoring.** Point the audit chain head at an append-only store (object lock,
  WORM bucket, transparency log). The hash chain gives tamper evidence; external anchoring
  makes it tamper resistance.

The GPU fleet and the serving stack are out of scope by design. WATTS observes them and
proposes changes; it does not provision or mutate them.
