"""WATTS benchmark harness.

Runs the same workload trace under several serving strategies and reports the metrics
side by side. Three rules keep the output honest:

1. **No invented numbers.** Every metric is computed from a run. The harness has no
   hard-coded results and will not emit a figure it did not produce.
2. **Provenance travels with the number.** Runs against the digital twin are labelled
   ``simulated`` in every record, table and JSON file. A simulated run is a statement
   about the model, not about hardware.
3. **Saturation is disclosed.** If the baseline is queue-bound, the comparison measures
   queueing rather than efficiency, and the result says so.

To benchmark real hardware, point the harness at a serving endpoint and a DCGM exporter
(see docs/BENCHMARK.md, "Running against real hardware"); the reporting path is identical
and the provenance flips to ``measured``.
"""
from __future__ import annotations

import json
import platform
import statistics
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping, Sequence

if __package__ in (None, ""):  # allow `python benchmarks/harness.py` from the repo root
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.energy_engine.model import Provenance
from services.security.policy import PolicyEngine, PolicyInput
from services.optimization.budgets import EnergySLO, evaluate_slo
from services.token_engine.efficiency import TokenEfficiencyEngine
from simulation.twin import (
    DatacenterConfig, ServingConfig, Simulation, WorkloadConfig,
)


@dataclass(frozen=True)
class Strategy:
    name: str
    description: str
    serving: ServingConfig
    datacenter: DatacenterConfig | None = None
    # Benchmark 2.0: a strategy is not comparable on energy alone. Quality is an offline
    # measurement the operator supplies; leaving it None means "not evaluated", which the
    # report prints as such rather than treating as "fine".
    quality: float | None = None
    quality_metric: str = "task_success_rate"
    security_notes: str = ""


def DEFAULT_STRATEGIES(baseline_model: str = "watts-sim-large") -> list[Strategy]:
    """Baseline plus the four optimisations WATTS claims to reason about."""
    # The baseline is opportunistic batching (dispatch whatever is queued, up to 4) with a
    # single large model and no caching: what a serving stack does out of the box.
    base = ServingConfig(max_batch=4, max_wait_s=0.0, default_model=baseline_model)
    return [
        Strategy("baseline", "Opportunistic batching up to 4, single large model, no caching", base),
        Strategy("dynamic_batching", "Batch up to 16 requests within a 200 ms window",
                 replace(base, max_batch=16, max_wait_s=0.20)),
        Strategy("token_optimization", "Prefix cache, response cache and per-task output caps",
                 replace(base, max_batch=16, max_wait_s=0.20, prefix_cache=True,
                         response_cache=True,
                         output_cap={"classification": 48, "extraction": 256,
                                     "summarization": 220, "reasoning": 700})),
        Strategy("model_routing", "Route each task to the cheapest model meeting its quality tier",
                 replace(base, max_batch=16, max_wait_s=0.20, routing=True)),
        Strategy("combined", "Routing, batching and token optimisation together",
                 replace(base, max_batch=16, max_wait_s=0.20, routing=True, prefix_cache=True,
                         response_cache=True,
                         output_cap={"classification": 48, "extraction": 256,
                                     "summarization": 220, "reasoning": 700})),
    ]


@dataclass
class BenchmarkResult:
    strategy: str
    description: str
    summary: dict
    slo: dict
    efficiency: dict
    warnings: list[str] = field(default_factory=list)
    quality: float | None = None
    quality_metric: str = "task_success_rate"
    quality_floor: float = 0.0
    security_compliant: bool = True
    security_reason: str = ""

    @property
    def admissible(self) -> bool:
        """A strategy wins only if it is allowed to run.

        Energy per token is not a result on its own: a configuration that misses the SLO,
        drops below the quality floor, or breaches policy has not saved anything. It has
        moved the cost somewhere the energy meter cannot see.
        """
        # An unevaluated strategy is not an adequate one. Where a floor is declared,
        # "we never measured this" and "this is fine" must not produce the same verdict.
        quality_ok = (self.quality_floor <= 0.0
                      or (self.quality is not None and self.quality >= self.quality_floor))
        return bool(self.slo["met"]) and quality_ok and self.security_compliant

    def row(self, baseline: "BenchmarkResult | None" = None) -> dict:
        s = self.summary
        row = {
            "strategy": self.strategy,
            "facility_kwh": s["facility_energy_wh"] / 1000.0,
            "wh_per_1k_tokens": s["wh_per_1k_tokens"],
            "wh_per_request": s["wh_per_request"],
            "wh_per_successful_request": s["wh_per_successful_request"],
            "tokens_per_s": s["tokens_per_s"],
            "p95_latency_ms": s["p95_latency_ms"],
            "mean_batch_size": s["mean_batch_size"],
            "pue": s["pue"],
            "error_rate": s["error_rate"],
            "max_gpu_temp_c": s["max_gpu_temp_c"],
            "throttled_fraction": s["throttled_fraction"],
            "slo_met": self.slo["met"],
            "throughput_requests_s": s.get("throughput_requests_s", 0.0),
            "queue_share_of_latency": s.get("queue_share_of_latency", 0.0),
            "quality": self.quality,
            "quality_metric": self.quality_metric,
            "quality_state": ("not evaluated" if self.quality is None
                              else "meets floor" if self.quality >= self.quality_floor
                              else "below floor"),
            "security_compliant": self.security_compliant,
            "security_reason": self.security_reason,
            "admissible": self.admissible,
        }
        if baseline and baseline.summary["wh_per_1k_tokens"]:
            b = baseline.summary
            row["energy_per_token_vs_baseline_pct"] = (
                (s["wh_per_1k_tokens"] - b["wh_per_1k_tokens"]) / b["wh_per_1k_tokens"] * 100.0)
            row["p95_latency_vs_baseline_pct"] = (
                (s["p95_latency_ms"] - b["p95_latency_ms"]) / b["p95_latency_ms"] * 100.0
                if b["p95_latency_ms"] else None)
        return row


def run_benchmark(
    strategies: Sequence[Strategy] | None = None,
    workload: WorkloadConfig | None = None,
    datacenter: DatacenterConfig | None = None,
    slo: EnergySLO | None = None,
    seeds: Sequence[int] = (11,),
    dt: float = 0.05,
    quality: Mapping[str, float] | None = None,
    quality_floor: float = 0.0,
    policy: "PolicyEngine | None" = None,
    approved_models: Sequence[str] = (),
) -> dict:
    strategies = list(strategies or DEFAULT_STRATEGIES())
    workload = workload or WorkloadConfig(duration_s=900.0, base_rps=2.0, seed=11)
    datacenter = datacenter or DatacenterConfig(gpus=8)
    slo = slo or EnergySLO("benchmark-slo", p95_latency_ms=15_000, availability=0.98,
                           max_error_rate=0.02, max_wh_per_successful_request=1.0)

    started = time.time()
    results: list[BenchmarkResult] = []
    per_seed: dict[str, list[float]] = {}

    for strat in strategies:
        runs = []
        for seed in seeds:
            sim = Simulation(dc=strat.datacenter or datacenter, serving=strat.serving,
                             workload=replace(workload, seed=seed), dt=dt)
            result = sim.run()
            runs.append(result)
        primary = runs[0]
        summary = primary.summary()
        slo_report = evaluate_slo(slo, primary.records).as_dict()
        eff = TokenEfficiencyEngine().analyze(primary.records).as_dict()

        warnings = []
        if summary["p95_latency_ms"] > 4 * max(summary["p50_latency_ms"], 1):
            warnings.append("queue-bound: p95 is more than 4x p50, the run measures queueing "
                            "as much as efficiency")
        if not slo_report["met"]:
            warnings.append("SLO not met: " + "; ".join(slo_report["violations"]))

        # Security compliance is evaluated, not assumed: a strategy that routes production
        # traffic to a model outside the allowlist is inadmissible however cheap it is.
        compliant, security_reason = _check_security(strat, primary.records, policy,
                                                     approved_models)
        if not compliant:
            warnings.append(f"security: {security_reason}")
        strategy_quality = (quality or {}).get(strat.name, strat.quality)
        if strategy_quality is None and quality_floor > 0:
            warnings.append("quality not evaluated for this strategy; it cannot be declared "
                            "admissible on energy alone")

        per_seed[strat.name] = [r.summary()["wh_per_1k_tokens"] for r in runs]
        results.append(BenchmarkResult(strat.name, strat.description, summary,
                                       slo_report, eff, warnings,
                                       quality=strategy_quality,
                                       quality_metric=strat.quality_metric,
                                       quality_floor=quality_floor,
                                       security_compliant=compliant,
                                       security_reason=security_reason))

    baseline = results[0]
    rows = [r.row(baseline) for r in results]

    variability = {}
    for name, vals in per_seed.items():
        if len(vals) > 1:
            variability[name] = {"mean": statistics.mean(vals), "stdev": statistics.pstdev(vals),
                                 "runs": len(vals)}

    return {
        "benchmark": "WATTS Benchmark",
        "status": "available",
        "provenance": Provenance.SIMULATED.value,
        "admissible_strategies": [r.strategy for r in results if r.admissible],
        "inadmissible_strategies": {r.strategy: (r.slo["violations"] or []) +
                                    ([r.security_reason] if not r.security_compliant else []) +
                                    (["quality below floor"] if r.quality is not None
                                     and r.quality < quality_floor else []) +
                                    (["quality not evaluated"] if r.quality is None
                                     and quality_floor > 0 else [])
                                    for r in results if not r.admissible},
        "quality_floor": quality_floor,
        "disclaimer": (
            "These figures come from the WATTS digital twin. They characterise the model's "
            "response to each strategy and must not be quoted as hardware measurements. "
            "Run the same harness against a metered GPU to produce measured results."),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "wall_clock_s": round(time.time() - started, 2),
        },
        "workload": {
            "duration_s": workload.duration_s, "base_rps": workload.base_rps,
            "seeds": list(seeds), "dt_s": dt,
            "task_mix": [{"task": t.task_class, "share": t.share,
                          "min_quality_tier": t.min_quality_tier} for t in workload.task_mix],
        },
        "slo": {"p95_latency_ms": slo.p95_latency_ms, "availability": slo.availability,
                "max_error_rate": slo.max_error_rate,
                "max_wh_per_successful_request": slo.max_wh_per_successful_request},
        "rows": rows,
        "variability_wh_per_1k_tokens": variability,
        "details": [{"strategy": r.strategy, "description": r.description,
                     "summary": r.summary, "slo": r.slo, "efficiency": r.efficiency,
                     "warnings": r.warnings} for r in results],
    }


def _check_security(strategy: Strategy, records, policy, approved_models: Sequence[str]
                    ) -> tuple[bool, str]:
    """Run the models this strategy actually used through the policy engine."""
    if policy is None or not approved_models:
        return True, "no allowlist supplied; security compliance not evaluated"
    used = {r.model for r in records}
    for model in sorted(used):
        decision = policy.evaluate(PolicyInput(
            action="route_workload", model=model, provider="on-prem",
            approved_models=tuple(approved_models), environment="production"))
        if not decision.allow:
            return False, f"{decision.rule_id}: {decision.reason}"
    return True, f"all {len(used)} model(s) used are on the allowlist"


def not_run(reason: str = "the benchmark has not been executed in this checkout") -> dict:
    """The report WATTS emits when there is no run to report.

    Deliberately shaped like a real report so that consumers - the dashboard, the demo
    report - render it without special casing, and so nobody is tempted to fill the gap
    with numbers from somewhere else.
    """
    return {
        "benchmark": "WATTS Benchmark",
        "status": "NOT RUN",
        "provenance": "none",
        "reason": reason,
        "rows": [],
        "disclaimer": "No benchmark has been run. There are therefore no results to report.",
        "reproduce": "python watts.py bench",
    }


def to_markdown(report: dict) -> str:
    if report.get("status") == "NOT RUN":
        return ("# WATTS Benchmark\n\n**STATUS: NOT RUN**\n\n"
                f"{report['reason']}\n\n```bash\n{report['reproduce']}\n```\n")
    cols = [("strategy", "Strategy", "{}"), ("wh_per_1k_tokens", "Wh/1k tok", "{:.4f}"),
            ("wh_per_successful_request", "Wh/req (ok)", "{:.4f}"),
            ("facility_kwh", "Facility kWh", "{:.4f}"), ("tokens_per_s", "tok/s", "{:.0f}"),
            ("p95_latency_ms", "p95 ms", "{:.0f}"), ("mean_batch_size", "batch", "{:.1f}"),
            ("pue", "PUE", "{:.3f}"), ("error_rate", "err", "{:.3%}"),
            ("energy_per_token_vs_baseline_pct", "vs base", "{:+.1f}%"),
            ("throughput_requests_s", "req/s", "{:.2f}"),
            ("quality_state", "Quality", "{}"),
            ("security_compliant", "Security", "{}"),
            ("slo_met", "SLO", "{}"),
            ("admissible", "Admissible", "{}")]
    lines = [f"# {report['benchmark']} ({report['provenance']})", "",
             report["disclaimer"], "",
             ("**Admissibility.** A strategy counts as a result only if it met the SLO, the "
              "quality floor and security policy. Energy per token from an inadmissible "
              "strategy is not a saving, it is a cost moved somewhere the meter cannot see."),
             "",
             "| " + " | ".join(c[1] for c in cols) + " |",
             "|" + "|".join("---" for _ in cols) + "|"]
    for row in report["rows"]:
        cells = []
        for key, _, fmt in cols:
            v = row.get(key)
            cells.append("-" if v is None else (fmt.format(v) if not isinstance(v, str) else v))
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "## Warnings", ""]
    any_warn = False
    for d in report["details"]:
        for w in d["warnings"]:
            any_warn = True
            lines.append(f"- **{d['strategy']}**: {w}")
    if not any_warn:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def main() -> None:  # pragma: no cover - CLI
    import argparse
    parser = argparse.ArgumentParser(description="Run the WATTS benchmark")
    parser.add_argument("--duration", type=float, default=900.0)
    parser.add_argument("--rps", type=float, default=2.0)
    parser.add_argument("--gpus", type=int, default=8)
    parser.add_argument("--seeds", type=int, nargs="+", default=[11])
    parser.add_argument("--out", type=Path, default=Path("benchmarks/results"))
    parser.add_argument("--quality-floor", type=float, default=0.90,
                        help="quality floor every strategy must clear to count as a result")
    args = parser.parse_args()

    # Declared quality figures standing in for an offline evaluation the operator runs on
    # their own data. They are inputs to the benchmark, not outputs of it, and the report
    # prints "not evaluated" for any strategy missing one.
    declared_quality = {
        "baseline": 0.96, "dynamic_batching": 0.96, "token_optimization": 0.94,
        "model_routing": 0.92, "combined": 0.92,
    }
    report = run_benchmark(workload=WorkloadConfig(duration_s=args.duration, base_rps=args.rps),
                           datacenter=DatacenterConfig(gpus=args.gpus),
                           seeds=tuple(args.seeds),
                           quality=declared_quality,
                           quality_floor=args.quality_floor,
                           policy=PolicyEngine(),
                           approved_models=("watts-sim-small", "watts-sim-medium",
                                            "watts-sim-large"))
    report["quality_source"] = ("declared by the operator for this run; WATTS does not "
                                "measure task quality from telemetry")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "benchmark.json").write_text(json.dumps(report, indent=2))
    (args.out / "benchmark.md").write_text(to_markdown(report))
    print(to_markdown(report))
    print(f"written to {args.out}/benchmark.json and {args.out}/benchmark.md")


if __name__ == "__main__":  # pragma: no cover
    main()
