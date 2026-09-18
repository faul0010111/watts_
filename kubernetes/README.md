# Kubernetes

Manifests for the production topology. The reference implementation does not need them.

| File | Purpose |
|---|---|
| `namespace.yaml` | namespace, resource quota, default-deny network policy |
| `telemetry-gateway.yaml` | the ingest trust boundary, with the OPA sidecar |
| `dcgm-exporter.yaml` | per-node GPU telemetry DaemonSet |

Principles encoded in these manifests:

* **Default-deny networking.** The gateway accepts from application namespaces and talks
  to the stream. Nothing else is reachable by default.
* **No privileged control-plane pods.** DCGM needs host access; WATTS services do not.
* **Policy travels with the workload.** OPA runs as a sidecar so a policy decision never
  depends on a remote call succeeding.
* **The control plane cannot restart your workloads.** It has no RBAC to mutate serving
  Deployments; it emits proposals.
