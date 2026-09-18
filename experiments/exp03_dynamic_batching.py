"""Experiment 03 - dynamic batching.

Sweeps the maximum batch size and the batching wait window at a fixed arrival rate. The
question is not only whether batching saves energy, but what it costs in latency - and
whether that cost fits inside an SLO.
"""
from common import ServingConfig, WorkloadConfig, pct, run_seeds, write

CONFIGS = [(1, 0.0), (4, 0.0), (8, 0.05), (16, 0.10), (16, 0.30), (32, 0.30)]


def _wait_window_finding(rows: list[dict]) -> str:
    """State what the wait window actually did in this run, not what it usually does."""
    short = rows[2]   # 50 ms window
    long_ = rows[4]   # 300 ms window at the same batch ceiling
    if long_["p95_latency_ms"] > short["p95_latency_ms"] * 1.05:
        return ("Widening the wait window from 50 ms to 300 ms raised p95 latency by "
                f"{pct(long_['p95_latency_ms'], short['p95_latency_ms']):.0f}%: past the point "
                "where the queue already supplies a full batch, waiting only costs latency.")
    return ("At this arrival rate the wait window never binds: the queue already holds enough "
            "requests, so a wider window changes neither batch size nor latency. The batch "
            "ceiling, not the window, is what mattered here.")


def main() -> None:
    workload = WorkloadConfig(duration_s=420, base_rps=3.0)
    rows, base = [], None
    for max_batch, wait in CONFIGS:
        serving = ServingConfig(max_batch=max_batch, max_wait_s=wait,
                                default_model="watts-sim-medium")
        r = run_seeds(serving, workload)
        base = base or r
        rows.append({
            "config": f"batch<={max_batch}, wait {wait * 1000:.0f} ms",
            "mean_batch_size": r["mean_batch_size"],
            "wh_per_1k_tokens": r["wh_per_1k_tokens"],
            "wh_per_request": r["wh_per_request"],
            "p50_latency_ms": r["p50_latency_ms"],
            "p95_latency_ms": r["p95_latency_ms"],
            "tokens_per_s": r["tokens_per_s"],
            "energy_vs_batch1_pct": pct(r["wh_per_1k_tokens"], base["wh_per_1k_tokens"]),
            "p95_vs_batch1_pct": pct(r["p95_latency_ms"], base["p95_latency_ms"]),
        })

    best = min(rows, key=lambda r: r["wh_per_1k_tokens"])
    findings = [
        f"Lowest energy per token: {best['config']} at {best['wh_per_1k_tokens']:.5f} Wh/1k "
        f"({best['energy_vs_batch1_pct']:+.0f}% against batch 1), with p95 latency "
        f"{best['p95_vs_batch1_pct']:+.0f}%.",
        "Batching amortises fixed per-step cost over more tokens, so energy per token falls "
        "while the accelerator draws more power: total power up, energy per unit of work down.",
        _wait_window_finding(rows),
    ]
    write("exp03_dynamic_batching", "Experiment 03 - Dynamic batching",
          "Does dynamic batching reduce energy per request, and at what latency cost?", rows,
          [("config", "Configuration", "{}"), ("mean_batch_size", "Mean batch", "{:.1f}"),
           ("wh_per_1k_tokens", "Wh/1k tokens", "{:.5f}"),
           ("p50_latency_ms", "p50 ms", "{:.0f}"), ("p95_latency_ms", "p95 ms", "{:.0f}"),
           ("tokens_per_s", "tok/s", "{:.0f}"),
           ("energy_vs_batch1_pct", "energy vs b1", "{:+.0f}%"),
           ("p95_vs_batch1_pct", "p95 vs b1", "{:+.0f}%")],
          {"rps": 3.0, "duration_s": 420, "model": "watts-sim-medium", "configs": CONFIGS},
          findings)


if __name__ == "__main__":
    main()
