"""Experiment 04 - energy-aware model routing.

Compares serving every task with one large model against routing each task to the
cheapest model that still meets its declared quality tier. The quality floor is a hard
constraint: routing never drops below the tier a task asked for, so the saving is
achieved without a quality trade the operator did not authorise.

What this experiment cannot show: whether the quality tiers are correct. That requires an
offline evaluation per task class on your own data, and it is a prerequisite for turning
routing on in production.
"""
from common import ServingConfig, WorkloadConfig, pct, run_seeds, write

STRATEGIES = [("always-large", "watts-sim-large", False),
              ("always-medium", "watts-sim-medium", False),
              ("routed", "watts-sim-large", True)]


def main() -> None:
    workload = WorkloadConfig(duration_s=420, base_rps=1.0)
    rows, base = [], None
    for name, model, routing in STRATEGIES:
        serving = ServingConfig(max_batch=16, max_wait_s=0.1, default_model=model, routing=routing)
        r = run_seeds(serving, workload)
        base = base or r
        rows.append({
            "strategy": name,
            "wh_per_1k_tokens": r["wh_per_1k_tokens"],
            "wh_per_successful_request": r["wh_per_successful_request"],
            "facility_energy_wh": r["facility_energy_wh"],
            "p95_latency_ms": r["p95_latency_ms"],
            "energy_vs_large_pct": pct(r["wh_per_1k_tokens"], base["wh_per_1k_tokens"]),
            "p95_vs_large_pct": pct(r["p95_latency_ms"], base["p95_latency_ms"]),
        })

    routed = rows[-1]
    findings = [
        f"Routing cut energy per token by {abs(routed['energy_vs_large_pct']):.0f}% against "
        f"serving everything on the large model, while p95 latency moved "
        f"{routed['p95_vs_large_pct']:+.0f}%.",
        "The saving comes from the task mix: classification and extraction do not need a "
        "frontier model, and reasoning still gets one. Routing is a mix decision, not a "
        "uniform downgrade.",
        "'always-medium' is cheaper still, and is the wrong answer: it serves tier-5 reasoning "
        "on a tier-3 model. It is included to show what the router refuses to do.",
    ]
    write("exp04_model_routing", "Experiment 04 - Energy-aware model routing",
          "Can model routing reduce energy without violating the SLO or the quality floor?", rows,
          [("strategy", "Strategy", "{}"), ("wh_per_1k_tokens", "Wh/1k tokens", "{:.5f}"),
           ("wh_per_successful_request", "Wh/req (ok)", "{:.5f}"),
           ("facility_energy_wh", "Facility Wh", "{:.2f}"),
           ("p95_latency_ms", "p95 ms", "{:.0f}"),
           ("energy_vs_large_pct", "energy vs large", "{:+.0f}%"),
           ("p95_vs_large_pct", "p95 vs large", "{:+.0f}%")],
          {"rps": 1.0, "duration_s": 420, "strategies": [s[0] for s in STRATEGIES]}, findings)


if __name__ == "__main__":
    main()
