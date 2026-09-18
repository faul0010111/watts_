"""Experiment 05 - utilisation and the cost of idle.

Sweeps the arrival rate over a fixed fleet. Idle accelerators still draw power, so the
fixed cost is amortised over whatever work arrives: energy per token is expected to fall
as utilisation rises, until queueing starts to dominate latency.

This is the experiment that decides fleet sizing: the cheapest token is the one served by
a busy GPU, and the most expensive is the one served by a nearly idle fleet.
"""
from common import ServingConfig, WorkloadConfig, run_seeds, write

RATES = [0.25, 0.5, 1.0, 2.0, 3.0, 4.0]


def main() -> None:
    serving = ServingConfig(max_batch=16, max_wait_s=0.1, default_model="watts-sim-medium")
    rows = []
    for rps in RATES:
        r = run_seeds(serving, WorkloadConfig(duration_s=360, base_rps=rps))
        rows.append({
            "rps": rps,
            "tokens_per_s": r["tokens_per_s"],
            "mean_batch_size": r["mean_batch_size"],
            "wh_per_1k_tokens": r["wh_per_1k_tokens"],
            "facility_energy_wh": r["facility_energy_wh"],
            "pue": r["pue"],
            "p95_latency_ms": r["p95_latency_ms"],
        })

    cheapest = min(rows, key=lambda r: r["wh_per_1k_tokens"])
    lightest, heaviest = rows[0], rows[-1]
    findings = [
        f"Energy per token fell from {lightest['wh_per_1k_tokens']:.5f} Wh/1k at "
        f"{lightest['rps']} rps to {cheapest['wh_per_1k_tokens']:.5f} Wh/1k at "
        f"{cheapest['rps']} rps: the same idle power spread over more work.",
        f"Facility energy rose from {lightest['facility_energy_wh']:.1f} Wh to "
        f"{heaviest['facility_energy_wh']:.1f} Wh over the same window. Absolute consumption "
        "and consumption per token move in opposite directions, which is why WATTS reports both.",
        f"p95 latency went from {lightest['p95_latency_ms']:.0f} ms to "
        f"{heaviest['p95_latency_ms']:.0f} ms. Past the knee, further consolidation buys "
        "efficiency by spending the SLO.",
        "Operational reading: consolidate workloads onto fewer busy accelerators and power down "
        "the rest, rather than running a large fleet at low utilisation.",
    ]
    write("exp05_gpu_utilization", "Experiment 05 - GPU utilisation and energy per token",
          "How does utilisation change the energy cost of a token?", rows,
          [("rps", "Arrival rate", "{:.2f}"), ("tokens_per_s", "tok/s", "{:.0f}"),
           ("mean_batch_size", "Mean batch", "{:.1f}"),
           ("wh_per_1k_tokens", "Wh/1k tokens", "{:.5f}"),
           ("facility_energy_wh", "Facility Wh", "{:.1f}"), ("pue", "PUE", "{:.3f}"),
           ("p95_latency_ms", "p95 ms", "{:.0f}")],
          {"duration_s": 360, "gpus": 4, "model": "watts-sim-medium", "rates": RATES}, findings)


if __name__ == "__main__":
    main()
