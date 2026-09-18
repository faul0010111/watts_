# Infrastructure

Nothing here is required to run WATTS. `python watts.py demo` exercises the entire
pipeline in-process. This directory exists so the reference implementation can be
developed against the production topology.

| File | What it gives you |
|---|---|
| `docker-compose.yml` | Redpanda (Kafka API), TimescaleDB, Redis, OPA, Prometheus, Grafana |
| `sql/001_schema.sql` | hypertables, per-minute rollups, retention, append-only audit table |
| `prometheus.yml` | scrape configuration, including a 1 s scrape for DCGM |

Secrets come from the environment and the compose file fails fast if they are absent:

```bash
export WATTS_DB_PASSWORD=... GRAFANA_PASSWORD=...
make docker-up
```

The DCGM exporter is commented out; uncomment it on a host with NVIDIA accelerators.

See `../kubernetes/` for cluster manifests and `../terraform/` for the module layout.
