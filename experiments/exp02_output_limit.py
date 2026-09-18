"""Experiment 02 - the cost of output tokens.

Output tokens are generated one decode step at a time, so they are expected to cost far
more per token than input tokens. This experiment measures the gap and shows what a
per-task output cap is worth.
"""
from common import ServingConfig, pct, run_seeds, single_task_workload, write

OUTPUT_SIZES = [32, 64, 128, 256, 512, 1_024]


def main() -> None:
    serving = ServingConfig(max_batch=8, max_wait_s=0.1, default_model="watts-sim-medium")
    rows, base = [], None
    for size in OUTPUT_SIZES:
        wl = single_task_workload(2_000, size, duration_s=240, rps=0.8)
        r = run_seeds(serving, wl)
        base = base or r
        rows.append({
            "output_tokens": size,
            "wh_per_request": r["wh_per_request"],
            "wh_per_1k_tokens": r["wh_per_1k_tokens"],
            "p95_latency_ms": r["p95_latency_ms"],
            "energy_vs_shortest_pct": pct(r["wh_per_request"], base["wh_per_request"]),
        })

    first, last = rows[0], rows[-1]
    per_out = ((last["wh_per_request"] - first["wh_per_request"]) /
               (OUTPUT_SIZES[-1] - OUTPUT_SIZES[0]))
    findings = [
        f"Each additional output token costs about {per_out * 1000:.4f} mWh in this "
        f"configuration, against a 2,000-token input held constant.",
        f"Going from {OUTPUT_SIZES[0]} to {OUTPUT_SIZES[-1]} output tokens raised energy per "
        f"request by {pct(last['wh_per_request'], first['wh_per_request']):.0f}% and p95 latency "
        f"by {pct(last['p95_latency_ms'], first['p95_latency_ms']):.0f}%.",
        "An output cap is the cheapest energy lever available, but it is the one most likely to "
        "cut an answer short: validate quality on a held-out set per task class before applying it.",
    ]
    write("exp02_output_limit", "Experiment 02 - Output token limits",
          "What does an output token cost relative to an input token?", rows,
          [("output_tokens", "Output tokens", "{:,}"), ("wh_per_request", "Wh/request", "{:.5f}"),
           ("wh_per_1k_tokens", "Wh/1k tokens", "{:.5f}"),
           ("p95_latency_ms", "p95 ms", "{:.0f}"),
           ("energy_vs_shortest_pct", "vs shortest", "{:+.0f}%")],
          {"model": "watts-sim-medium", "input_tokens": 2000, "rps": 0.8, "duration_s": 240,
           "output_sizes": OUTPUT_SIZES}, findings)


if __name__ == "__main__":
    main()
