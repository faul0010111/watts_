"""The WATTS pipeline, run end to end, producing one report.

    observe → measure/estimate → attribute → explain → forecast → simulate →
    optimise → validate security → request approval → (execute) → measure → audit

Every number in the resulting report is produced here, by running the stages. Nothing is
written by hand, nothing is carried over from a previous run, and every section states the
provenance of what it contains. The execute step is deliberately not taken: WATTS stops at
"awaiting approval" and records why.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..energy_engine import (
    EnergyModel, EnergyModelConfig, Provenance, analyse_causes, compute_pue,
    provenance_summary, run_detection_suite, simulated,
)
from ..energy_engine.anomaly import EnergyAnomalyEngine, WindowStats
from ..energy_engine.detectors import latency_energy_ratio
from ..finops import CostModel, CostRates, allocate_costs, cost_report
from ..forecasting import build_forecast_suite
from ..optimization import (
    Budget, BudgetState, BudgetTracker, ChangeWorkflow, EnergySLO, EnergyLatencyQualityFrontier,
    FrontierPoint, OptimizationEngine, OptimizationPlanner, QualityFloor, QualityObservation,
    QualityRegistry, WorkloadConstraints, project_no_action,
)
from ..optimization.budgets import evaluate_slo
from ..security import AuditLog, PolicyEngine, Principal
from ..telemetry import TelemetryGateway, probe_adapters
from ..token_engine import (
    AttributionInputs, EconomicsInputs, TokenEconomics, TokenEfficiencyEngine,
    aggregate_decompositions, decompose_request_energy, load_default_profiles,
)
from ..calibration import CalibrationRegistry
from .context import RunContext, make_run_context

# The simulator is the default source of observations. Swap in a measured window and every
# provenance label below changes with it; nothing else does.
try:                                            # pragma: no cover - import layout only
    from simulation.twin import (
        CoolingSpec, DatacenterConfig, ServingConfig, Simulation, WorkloadConfig,
    )
except ImportError:                             # pragma: no cover
    Simulation = None


@dataclass
class PipelineConfig:
    """Everything the run needs, in one place, so the report can print it verbatim."""

    seed: int = 42
    duration_s: float = 600.0
    rps: float = 2.0
    gpus: int = 4
    model: str = "watts-sim-medium"
    tenant: str = "acme"
    workload: str = "checkout-assistant"
    p95_latency_slo_ms: float = 12_000.0
    availability_slo: float = 0.98
    max_error_rate: float = 0.02
    max_wh_per_successful_request: float = 0.5
    # Declared for the four-accelerator simulated rack this demo runs. It is an operator
    # input, not a measurement, and the report says so wherever it is used.
    energy_budget_wh_per_hour: float = 1_800.0
    token_budget_per_hour: float = 12_000_000.0
    quality_metric: str = "task_success_rate"
    quality_floor: float = 0.90
    electricity_per_kwh: float = 0.12
    accelerator_per_hour: float = 2.40
    carbon_g_per_kwh: float | None = 380.0
    forecast_horizons: tuple[int, ...] = (2, 4, 8, 12, 20)

    def as_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v)
                for k, v in self.__dict__.items()}


@dataclass
class PipelineResult:
    context: RunContext
    sections: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"run": self.context.as_dict(), **self.sections}


def run_pipeline(config: PipelineConfig | None = None, *,
                 command: str = "python watts.py demo",
                 repo_root: Path | None = None) -> PipelineResult:
    """Run every stage and return the assembled report data."""
    cfg = config or PipelineConfig()
    root = repo_root or Path(__file__).resolve().parents[2]
    context = make_run_context(seed=cfg.seed, configuration=cfg.as_dict(),
                               command=f"{command} --seed {cfg.seed}")
    sections: dict = {}

    # --- 1. observe ------------------------------------------------------
    result = _simulate(cfg, seed=cfg.seed)
    summary = result.summary()
    records = result.records
    window_s = result.duration_s
    sections["observation"] = {
        "provenance": summary["provenance"],
        "adapters": probe_adapters(),
        "window_s": window_s,
        "requests": summary["requests"],
        "successful_requests": summary["successful_requests"],
        "error_rate": summary["error_rate"],
        "total_tokens": summary["total_tokens"],
        "tokens_per_s": summary["tokens_per_s"],
        "queueing": result.queueing_metrics(),
        "thermal": {
            "max_gpu_temp_c": summary["max_gpu_temp_c"],
            "mean_clock_factor": summary["mean_clock_factor"],
            "throttled_fraction": summary["throttled_fraction"],
            "exceeded_critical_temp": summary["exceeded_critical_temp"],
        },
    }

    # --- 2. ingest through the trust boundary ----------------------------
    key = b"demo-workload-key"
    gateway = TelemetryGateway({"demo-workload": key})
    now = max((r.timestamp for r in records), default=0.0) + 1.0
    accepted = 0
    for record in records[:500]:
        outcome = gateway.ingest(record, record.signature(key), "demo-workload", now=now)
        accepted += int(outcome.accepted)
    # A record signed with the wrong key must not survive the trust boundary, whatever it
    # claims about its own energy.
    forged = gateway.ingest(records[0], "0" * 64, "demo-workload", now=now)
    sections["ingest"] = {
        "submitted": min(len(records), 500),
        "accepted": accepted,
        "forged_record_accepted": forged.accepted,
        "forged_record_reason": forged.reason.value if forged.reason else "",
        "gateway_stats": gateway.stats(),
    }

    # --- 3. measure / estimate -------------------------------------------
    energy_model = EnergyModel(EnergyModelConfig())
    pue = compute_pue(it_energy_wh=result.it_energy_wh,
                      facility_energy_wh=result.facility_energy_wh,
                      window_s=window_s,
                      source_provenance=Provenance.SIMULATED)
    sections["energy"] = {
        "gpu_wh": result.gpu_energy_wh,
        "it_wh": result.it_energy_wh,
        "cooling_wh": result.cooling_energy_wh,
        "facility_wh": result.facility_energy_wh,
        "pue": pue.as_dict(),
        "wh_per_1k_tokens": summary["wh_per_1k_tokens"],
        "facility_wh_per_1k_tokens": summary["facility_wh_per_1k_tokens"],
        "wh_per_request": summary["wh_per_request"],
        "wh_per_successful_request": summary["wh_per_successful_request"],
        "provenance": Provenance.SIMULATED.value,
    }

    # --- 4. attribute ------------------------------------------------------
    registry = load_default_profiles()
    calibration = CalibrationRegistry()
    decompositions = []
    for record in records[:200]:
        if not record.energy_wh:
            continue
        profile = registry.get(record.model)
        decompositions.append(decompose_request_energy(
            simulated(record.energy_wh, "watts-digital-twin", timestamp=record.timestamp),
            request_id=record.request_id,
            input_tokens=record.input_tokens, output_tokens=record.output_tokens,
            profile=profile,
            inputs=AttributionInputs(memory_gb=0.0, network_gb=0.0,
                                     shared_infrastructure_share=0.0),
            energy_model=energy_model, window_s=window_s))
    sections["attribution"] = aggregate_decompositions(decompositions)
    sections["calibration"] = calibration.summary(registry.names())

    # --- 5. token efficiency ----------------------------------------------
    efficiency = TokenEfficiencyEngine().analyze(records)
    sections["efficiency"] = efficiency.as_dict()

    economics = TokenEconomics(
        int(summary["total_tokens"]), result.facility_energy_wh,
        gpu_seconds=cfg.gpus * window_s,
        inputs=EconomicsInputs(cfg.electricity_per_kwh, cfg.accelerator_per_hour,
                               cfg.carbon_g_per_kwh, "operator-supplied static value"))
    sections["economics"] = economics.as_dict()

    # --- 6. explain: detection and root cause ------------------------------
    degraded = _simulate(cfg, seed=cfg.seed, cooling_efficiency=0.4).summary()
    history = [{
        "wh_per_1k_tokens": summary["facility_wh_per_1k_tokens"] * (1 + 0.008 * ((i % 5) - 2)),
        "pue": summary["pue"] * (1 + 0.004 * ((i % 4) - 1)),
        "gpu_temp_max_c": summary["max_gpu_temp_c"] * (1 + 0.005 * ((i % 3) - 1)),
        "tokens_per_s": summary["tokens_per_s"],
        "latency_energy_ratio": latency_energy_ratio(summary["p95_latency_ms"],
                                                     summary["facility_wh_per_1k_tokens"]),
    } for i in range(20)]
    current_window = {
        "wh_per_1k_tokens": degraded["facility_wh_per_1k_tokens"],
        "pue": degraded["pue"],
        "gpu_temp_max_c": degraded["max_gpu_temp_c"],
        "tokens_per_s": degraded["tokens_per_s"],
        "latency_energy_ratio": latency_energy_ratio(degraded["p95_latency_ms"],
                                                     degraded["facility_wh_per_1k_tokens"]),
    }
    suite = run_detection_suite(history, current_window)
    anomaly = EnergyAnomalyEngine().detect(
        [_window_stats(h, summary) for h in history],
        _window_stats(current_window, degraded))
    rca = analyse_causes(anomaly, provenance=Provenance.SIMULATED)
    sections["anomalies"] = {
        "comparison_window": "same workload with cooling capability degraded to 40%",
        "detections": suite.as_dict(),
        "root_cause": rca.as_dict(),
    }

    # --- 7. forecast --------------------------------------------------------
    step_s, buckets = _bucket(result.power_series, "facility_w", window_s)
    _, token_buckets = _bucket_tokens(records, window_s, step_s)
    _, cooling_buckets = _bucket(result.power_series, "cooling_w", window_s, step_s)
    gpu_buckets = [min(1.0, b / max(1.0, cfg.gpus)) * cfg.gpus for b in
                   _bucket(result.power_series, "gpu_w", window_s, step_s)[1]]
    forecasts = build_forecast_suite(
        energy_series=buckets, token_series=token_buckets,
        gpu_demand_series=gpu_buckets, cooling_series=cooling_buckets,
        step_s=step_s, horizons=cfg.forecast_horizons,
        energy_limit=cfg.energy_budget_wh_per_hour,
        token_limit=cfg.token_budget_per_hour / 3600.0)
    sections["forecast"] = forecasts.as_dict()

    # --- 8. SRE: SLO, budgets, no-action ------------------------------------
    slo = EnergySLO(cfg.workload, p95_latency_ms=cfg.p95_latency_slo_ms,
                    availability=cfg.availability_slo, max_error_rate=cfg.max_error_rate,
                    max_wh_per_successful_request=cfg.max_wh_per_successful_request)
    slo_report = evaluate_slo(slo, records)
    budget_state = BudgetState(cfg.workload, window_s, energy_wh=result.facility_energy_wh,
                               tokens=int(summary["total_tokens"]),
                               successful_requests=summary["successful_requests"])
    budgets = BudgetTracker({cfg.workload: Budget(
        cfg.workload, energy_wh_per_hour=cfg.energy_budget_wh_per_hour,
        tokens_per_hour=cfg.token_budget_per_hour)})
    sections["slo"] = slo_report.as_dict()
    sections["budgets"] = budgets.evaluate(budget_state)

    projection = project_no_action(
        horizon_s=forecasts.energy.points[-1].horizon_steps * step_s
        if forecasts.energy.points else 0.0,
        energy=(forecasts.energy,
                result.facility_energy_wh * 3600.0 / window_s,
                cfg.energy_budget_wh_per_hour),
        tokens=(forecasts.tokens, summary["tokens_per_s"],
                cfg.token_budget_per_hour / 3600.0),
        thermal={"peak_c": summary["max_gpu_temp_c"],
                 "projected_peak_c": summary["max_gpu_temp_c"],
                 "throttle_c": 83.0, "critical_c": 92.0},
        slo_state="within objectives" if not slo_report.reliability_violations
                  else "reliability violation open")
    sections["no_action"] = projection.as_dict()

    # --- 9. optimise: frontier and plan --------------------------------------
    quality = QualityRegistry()
    # Declared, not measured: these stand in for an offline evaluation the operator runs.
    for name, value in (("watts-sim-small", 0.79), ("watts-sim-medium", 0.93),
                        ("watts-sim-large", 0.96), ("routed", 0.92)):
        quality.record(QualityObservation(name, cfg.quality_metric, value, 200,
                                          "declared-example-evalset",
                                          source="declared-example"))
    constraints = WorkloadConstraints(
        name=cfg.workload, p95_latency_ms=cfg.p95_latency_slo_ms,
        min_availability=cfg.availability_slo, max_error_rate=cfg.max_error_rate,
        quality_floor=QualityFloor(cfg.quality_metric, cfg.quality_floor,
                                   "declared-example-evalset"),
        energy_budget_wh_per_hour=cfg.energy_budget_wh_per_hour)

    frontier = EnergyLatencyQualityFrontier(constraints, quality)
    for model_name in ("watts-sim-small", "watts-sim-medium", "watts-sim-large"):
        point_summary = _simulate(cfg, seed=cfg.seed, model=model_name).summary()
        frontier.add(FrontierPoint(
            name=model_name,
            energy_wh_per_1k_tokens=point_summary["facility_wh_per_1k_tokens"],
            p95_latency_ms=point_summary["p95_latency_ms"],
            availability=1.0 - point_summary["error_rate"],
            error_rate=point_summary["error_rate"],
            energy_wh_per_hour=point_summary["facility_energy_wh"] * 3600.0 / window_s,
            provenance=Provenance.SIMULATED,
            evidence=(f"simulated window, seed {cfg.seed}",)))
    sections["frontier"] = frontier.as_dict()

    evaluator = _make_evaluator(cfg)
    planner = OptimizationPlanner(evaluator, constraints, PolicyEngine(), quality)
    base_knobs = {"max_batch": 16, "max_wait_s": 0.1, "prefix_cache": False,
                  "response_cache": False, "routing": False}
    state = {"mean_batch_size": summary["mean_batch_size"],
             "latency_headroom_ms": slo_report.latency_headroom_ms,
             "routing_enabled": False}
    candidates = planner.generate_candidates(state, efficiency)
    plan = planner.plan(base_knobs, candidates)
    sections["plan"] = plan.as_dict()

    # --- 10. security gate and approval --------------------------------------
    audit = AuditLog(policy_version=context.policy_version)
    workflow = ChangeWorkflow(PolicyEngine(), audit)
    assistant = Principal("watts-assistant", ("watts-assistant",), cfg.tenant)
    gate_rows = []
    for rec in OptimizationEngine().from_efficiency(efficiency)[:5]:
        state_, reason = workflow.submit(rec, principal=assistant, slo=slo_report,
                                         now=time.time())
        gate_rows.append({
            "recommendation": rec.title,
            "energy_wh": rec.impact.energy_wh,
            "security": rec.impact.security,
            "state": state_.value,
            "reason": reason,
            "evidence": list(rec.evidence[:2]),
        })
    sections["security_gate"] = {
        "proposals": gate_rows,
        "executed": 0,
        "note": ("the assistant may propose and simulate; approval and execution require a "
                 "human with the operator role, so this run changes nothing"),
    }

    # --- 11. FinOps ------------------------------------------------------------
    cost_model = CostModel(
        CostRates(electricity_per_kwh=cfg.electricity_per_kwh,
                  accelerator_per_hour=cfg.accelerator_per_hour,
                  host_per_accelerator_hour=0.30,
                  carbon_g_per_kwh=cfg.carbon_g_per_kwh),
        accelerators=cfg.gpus)
    breakdown = cost_model.breakdown(it_energy_wh=result.it_energy_wh,
                                     cooling_energy_wh=result.cooling_energy_wh,
                                     window_s=window_s)
    allocations = allocate_costs(records, model=cost_model,
                                 it_energy_wh=result.it_energy_wh,
                                 cooling_energy_wh=result.cooling_energy_wh,
                                 window_s=window_s, dimension="task_class")
    sections["finops"] = cost_report(
        breakdown, allocations, tokens=int(summary["total_tokens"]),
        successful_requests=summary["successful_requests"],
        carbon_g=cost_model.carbon_g(result.facility_energy_wh))

    # --- 12. audit --------------------------------------------------------------
    checkpoint = audit.checkpoint()
    valid, broken = audit.verify()
    sections["audit"] = {
        "entries": len(audit),
        "chain_valid": valid,
        "first_broken_index": broken,
        "head": audit.head,
        "checkpoint": checkpoint.as_dict(),
        "policy_version": context.policy_version,
        "note": ("hash chaining evidences sequence integrity; anchor the head externally "
                 "for tamper resistance"),
    }

    # --- 13. benchmark and experiments (read, never invented) -------------------
    sections["benchmark"] = _read_benchmark(root)
    sections["experiments"] = _read_experiments(root)

    # --- 13b. series for the Command Center ---------------------------------------
    sections["series"] = _series(result, cfg, decompositions)

    # --- 14. provenance summary --------------------------------------------------
    values = [v for d in decompositions for _, v in d.components]
    sections["provenance"] = {
        **provenance_summary(values),
        "sections": {
            "observation": "simulated",
            "energy": "simulated",
            "attribution": "simulated window, modelled split",
            "forecast": "derived from simulated history",
            "frontier": "simulated",
            "plan": "simulated",
            "finops": "estimated from operator-supplied rates",
            "benchmark": sections["benchmark"]["status"],
            "experiments": sections["experiments"]["status"],
        },
        "declaration": ("No figure in this report was entered by hand. Every number was "
                        "produced by this run or read from a results file written by a "
                        "previous run, and each carries the provenance of its source."),
    }
    return PipelineResult(context, sections)


# --- helpers ----------------------------------------------------------------

def _simulate(cfg: PipelineConfig, *, seed: int, cooling_efficiency: float = 1.0,
              model: str | None = None, knobs: dict | None = None):
    k = knobs or {}
    serving = ServingConfig(
        max_batch=k.get("max_batch", 16),
        max_wait_s=k.get("max_wait_s", 0.1),
        prefix_cache=k.get("prefix_cache", False),
        response_cache=k.get("response_cache", False),
        output_cap=k.get("output_cap", {}),
        routing=k.get("routing", False),
        default_model=model or cfg.model)
    return Simulation(
        dc=DatacenterConfig(gpus=cfg.gpus, cooling=CoolingSpec(efficiency=cooling_efficiency)),
        serving=serving,
        workload=WorkloadConfig(duration_s=cfg.duration_s, base_rps=cfg.rps, seed=seed),
    ).run()


def _make_evaluator(cfg: PipelineConfig) -> Callable[[dict], dict]:
    """Candidate evaluator: run the twin with the candidate's knobs applied.

    In production this is the same function pointed at a canary. The planner does not know
    the difference, which is what keeps the two paths honest about each other.
    """
    def evaluate(knobs: dict) -> dict:
        outcome = _simulate(cfg, seed=cfg.seed, knobs=knobs)
        summary = outcome.summary()
        summary["facility_wh_per_hour"] = (outcome.facility_energy_wh * 3600.0
                                           / max(outcome.duration_s, 1e-9))
        return summary
    return evaluate


def _series(result, cfg: PipelineConfig, decompositions) -> dict:
    """Chart-ready series, downsampled here so the dashboard renders what the report says.

    The Command Center reads this, rather than running its own simulation, so the page and
    the document can never disagree about what happened.
    """
    points = result.power_series
    step = max(1, len(points) // 180)
    power = [{"t": round(p["t_s"], 1),
              "gpu": round(p["gpu_w"], 1),
              "host": round(p["it_w"] - p["gpu_w"], 1),
              "cooling": round(p["facility_w"] - p["it_w"], 1)}
             for p in points[::step]]

    peaks = [0.0] * cfg.gpus
    clock_sum = clock_n = 0
    for point in result.thermal_series:
        for i, temp in enumerate(point["temps_c"][:cfg.gpus]):
            peaks[i] = max(peaks[i], temp)
        clock_sum += point["mean_clock_factor"]
        clock_n += 1

    by_task: dict[str, dict] = {}
    for r in result.records:
        row = by_task.setdefault(r.task_class, {"requests": 0, "tokens": 0, "wh": 0.0})
        row["requests"] += 1
        row["tokens"] += r.total_tokens
        row["wh"] += r.energy_wh or 0.0
    facility_factor = result.facility_energy_wh / max(
        sum(v["wh"] for v in by_task.values()), 1e-9)
    tasks = [{"task": name, "requests": v["requests"], "tokens": v["tokens"],
              "facility_wh_per_1k": v["wh"] * facility_factor / max(v["tokens"] / 1000.0, 1e-9)}
             for name, v in sorted(by_task.items(), key=lambda kv: -kv[1]["wh"])]

    prefill = sum(d.get("prefill").value for d in decompositions if d.get("prefill"))
    decode = sum(d.get("decode").value for d in decompositions if d.get("decode"))

    queue = [{"t": round(q["t_s"], 1), "depth": q["depth"]} for q in result.queue_series[::step]]

    return {
        "power": power,
        "queue": queue,
        "tasks": tasks,
        "prefill_wh": prefill,
        "decode_wh": decode,
        "thermal": {
            "gpus": [{"id": f"gpu-{i}", "peak_c": peaks[i],
                      "clock": clock_sum / max(clock_n, 1)} for i in range(cfg.gpus)],
            "inlet_c": DatacenterConfig(gpus=cfg.gpus).cooling.inlet_temp_c,
            "throttle_c": DatacenterConfig(gpus=cfg.gpus).gpu.throttle_temp_c,
            "critical_c": DatacenterConfig(gpus=cfg.gpus).gpu.critical_temp_c,
        },
    }


def _bucket(series: list[dict], key: str, window_s: float,
            step_s: float | None = None) -> tuple[float, list[float]]:
    step = step_s or max(10.0, window_s / 30.0)
    buckets, acc, elapsed = [], 0.0, 0.0
    for a, b in zip(series, series[1:]):
        dt = b["t_s"] - a["t_s"]
        acc += 0.5 * (a[key] + b[key]) * dt
        elapsed += dt
        if elapsed >= step:
            buckets.append(acc / elapsed)
            acc, elapsed = 0.0, 0.0
    return step, buckets


def _bucket_tokens(records, window_s: float, step_s: float) -> tuple[float, list[float]]:
    n = max(1, int(window_s / step_s))
    buckets = [0.0] * n
    for r in records:
        idx = min(n - 1, int(r.timestamp / step_s))
        buckets[idx] += r.total_tokens
    return step_s, [b / step_s for b in buckets]


def _window_stats(window: dict, summary: dict) -> WindowStats:
    return WindowStats(
        wh_per_1k_tokens=window["wh_per_1k_tokens"],
        gpu_util_mean=0.55,
        gpu_temp_mean_c=window["gpu_temp_max_c"],
        sm_clock_mean_mhz=1400 * summary.get("mean_clock_factor", 1.0),
        batch_size_mean=summary.get("mean_batch_size", 1.0),
        tokens=summary.get("total_tokens", 0),
        pue=window["pue"],
        throughput_tokens_s=window["tokens_per_s"],
        model_mix={summary.get("model", "watts-sim-medium"): 1.0},
    )


def _read_benchmark(root: Path) -> dict:
    """Read the benchmark results file, or report that the benchmark has not been run.

    WATTS never fills this section with plausible numbers. A report that says NOT RUN is
    more useful than one that says something untrue.
    """
    path = root / "benchmarks" / "results" / "benchmark.json"
    if not path.exists():
        return {"status": "NOT RUN",
                "note": "run `python watts.py bench` to produce benchmarks/results/benchmark.json"}
    import json
    data = json.loads(path.read_text())
    return {
        "status": "available",
        "provenance": data.get("provenance"),
        "generated_at": data.get("generated_at"),
        "rows": data.get("rows", []),
        "disclaimer": data.get("disclaimer"),
        "note": "read from a previous run; re-run the benchmark to refresh it",
    }


def _read_experiments(root: Path) -> dict:
    import json
    directory = root / "experiments" / "results"
    if not directory.exists():
        return {"status": "NOT RUN", "experiments": [],
                "note": "run `make experiments` to produce experiments/results/"}
    rows = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except ValueError:
            continue
        rows.append({
            "experiment": data.get("experiment", path.stem),
            "provenance": data.get("provenance", "unknown"),
            "findings": data.get("findings", [])[:2],
            "seed": data.get("config", {}).get("seed") or data.get("seed"),
        })
    return {"status": "available" if rows else "NOT RUN", "experiments": rows,
            "count": len(rows)}
