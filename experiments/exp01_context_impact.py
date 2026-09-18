"""Experiment 01 - how input context size drives energy.

Varies input tokens with the output length held constant, so every difference in energy
comes from prefill work. This is also the calibration experiment: run it against metered
hardware and the slope of Wh against input tokens gives the prefill coefficient for a
model profile.
"""
from common import ServingConfig, pct, run_seeds, single_task_workload, write

CONTEXT_SIZES = [500, 1_000, 2_000, 4_000, 8_000, 16_000]


def main() -> None:
    serving = ServingConfig(max_batch=8, max_wait_s=0.1, default_model="watts-sim-medium")
    rows, base = [], None
    for size in CONTEXT_SIZES:
        wl = single_task_workload(size, 200, duration_s=240, rps=0.8)
        r = run_seeds(serving, wl)
        base = base or r
        rows.append({
            "input_tokens": size,
            "wh_per_request": r["wh_per_request"],
            "wh_per_1k_tokens": r["wh_per_1k_tokens"],
            "p95_latency_ms": r["p95_latency_ms"],
            "energy_vs_smallest_pct": pct(r["wh_per_request"], base["wh_per_request"]),
            "seed_spread_wh_per_request": r["wh_per_request__spread"],
        })

    first, last = rows[0], rows[-1]
    growth = last["wh_per_request"] / first["wh_per_request"] if first["wh_per_request"] else 0
    ratio = CONTEXT_SIZES[-1] / CONTEXT_SIZES[0]
    findings = [
        f"Input grew {ratio:.0f}x; energy per request grew {growth:.1f}x, so prefill cost is "
        f"close to linear in context length while the fixed per-request cost dilutes.",
        f"Energy per 1k tokens fell from {first['wh_per_1k_tokens']:.4f} to "
        f"{last['wh_per_1k_tokens']:.4f} Wh: long contexts are cheaper *per token* and more "
        f"expensive *per request*. Reporting only Wh/token hides the cost of context growth.",
        "Calibration: the slope of Wh/request against input tokens is the prefill coefficient "
        "for a model profile. Run this against metered hardware before quoting absolute energy.",
    ]
    write("exp01_context_impact", "Experiment 01 - Context size and energy",
          "How does the size of the context influence consumption?", rows,
          [("input_tokens", "Input tokens", "{:,}"), ("wh_per_request", "Wh/request", "{:.5f}"),
           ("wh_per_1k_tokens", "Wh/1k tokens", "{:.5f}"),
           ("p95_latency_ms", "p95 ms", "{:.0f}"),
           ("energy_vs_smallest_pct", "vs smallest", "{:+.0f}%"),
           ("seed_spread_wh_per_request", "seed spread", "{:.5f}")],
          {"model": "watts-sim-medium", "output_tokens": 200, "rps": 0.8, "duration_s": 240,
           "max_batch": 8, "context_sizes": CONTEXT_SIZES}, findings)


if __name__ == "__main__":
    main()
