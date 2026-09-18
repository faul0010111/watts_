#!/usr/bin/env python3
"""WATTS command-line entry point.

    python watts.py demo                 end-to-end pipeline on the digital twin
    python watts.py simulate --rps 3     one simulation run, summary as JSON
    python watts.py bench                strategy benchmark
    python watts.py dashboard            build the Command Center HTML from a fresh run

``demo`` is the reproducibility claim of this repository: it runs the whole control
plane - telemetry ingest with signature checks, energy attribution, efficiency analysis,
anomaly detection, forecasting, recommendations, policy evaluation, human-in-the-loop
workflow and the audit chain - on a laptop, with no GPU and no data centre.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from services.energy_engine import EnergyModel, Provenance, compute_pue
from services.energy_engine.anomaly import EnergyAnomalyEngine, WindowStats
from services.forecasting import HoltForecaster
from services.optimization import (
    Budget, BudgetState, BudgetTracker, ChangeWorkflow, EnergySLO, OptimizationEngine,
)
from services.optimization.budgets import evaluate_slo
from services.security import AuditLog, PolicyEngine, Principal
from services.telemetry import TelemetryGateway
from services.token_engine import (
    EconomicsInputs, TokenEconomics, TokenEfficiencyEngine, load_default_profiles,
)
from simulation.twin import (
    CoolingSpec, DatacenterConfig, ServingConfig, Simulation, WorkloadConfig,
)

WORKLOAD_KEY = b"demo-key-not-for-production"


def _simulate(rps: float, duration: float, gpus: int, seed: int,
              cooling_efficiency: float = 1.0, **serving_kwargs):
    sim = Simulation(
        dc=DatacenterConfig(gpus=gpus, cooling=CoolingSpec(efficiency=cooling_efficiency)),
        serving=ServingConfig(**{"max_batch": 16, "max_wait_s": 0.1,
                                 "default_model": "watts-sim-medium", **serving_kwargs}),
        workload=WorkloadConfig(duration_s=duration, base_rps=rps, seed=seed),
    )
    return sim.run()


def cmd_simulate(args) -> None:
    result = _simulate(args.rps, args.duration, args.gpus, args.seed)
    print(json.dumps(result.summary(), indent=2))


def cmd_bench(args) -> None:
    from benchmarks.harness import run_benchmark, to_markdown
    report = run_benchmark(workload=WorkloadConfig(duration_s=args.duration, base_rps=args.rps),
                           datacenter=DatacenterConfig(gpus=args.gpus), seeds=tuple(args.seeds))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "benchmark.json").write_text(json.dumps(report, indent=2))
    (out / "benchmark.md").write_text(to_markdown(report))
    print(to_markdown(report))


def cmd_dashboard(args) -> None:
    from apps.dashboard.build_static import build
    path = build(Path(args.out), rps=args.rps, duration=args.duration, gpus=args.gpus)
    print(f"Command Center written to {path}")


def cmd_demo(args) -> None:
    """Run the whole pipeline and write the report it produces.

    The demo prints a summary and writes the full document. Nothing in either is typed by
    hand: every figure comes from this run, and the report ends with the command that
    reproduces it.
    """
    from services.reporting import PipelineConfig, render_markdown, run_pipeline

    config = PipelineConfig(seed=args.seed, duration_s=args.duration, rps=args.rps,
                            gpus=args.gpus)
    print(f"WATTS pipeline — seed {args.seed}, {args.gpus} accelerators, "
          f"{args.duration:.0f}s at {args.rps} rps (digital twin: every figure is SIMULATED)")
    print("observe → measure → attribute → explain → forecast → simulate → optimise → "
          "validate → approve → audit\n")

    result = run_pipeline(config, command="python watts.py demo")
    data = result.as_dict()
    markdown = render_markdown(data)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"watts-report-{result.context.run_id}.md"
    json_path = out_dir / f"watts-report-{result.context.run_id}.json"
    md_path.write_text(markdown)
    json_path.write_text(json.dumps(data, indent=2, default=str))

    # --- condensed view on stdout -----------------------------------------
    line = "-" * 74
    obs, energy, slo = data["observation"], data["energy"], data["slo"]
    plan, prov = data["plan"], data["provenance"]
    print(f"{line}\n observe    {obs['requests']:,} requests, {obs['total_tokens']:,} tokens, "
          f"{obs['error_rate']:.2%} errors")
    print(f" measure    {energy['facility_wh']:,.1f} Wh facility, PUE {energy['pue']['pue']:.3f} "
          f"({energy['pue']['provenance']})")
    print(f" attribute  {energy['facility_wh_per_1k_tokens']:.4f} facility Wh / 1k tokens; "
          f"{len(data['calibration']['uncalibrated'])} profiles uncalibrated")
    print(f" ingest     {data['ingest']['accepted']} accepted, forged record rejected "
          f"({data['ingest']['forged_record_reason']})")
    findings = data["efficiency"]["findings"]
    print(f" explain    {len(findings)} efficiency findings; "
          f"{data['anomalies']['root_cause']['statement'][:90]}…")
    breaches = data["no_action"]["expected_breaches"] or ["none"]
    print(f" forecast   no-action breaches: {', '.join(breaches)}")
    print(f" optimise   frontier selects {data['frontier']['recommendation'] or 'nothing'}; "
          f"plan {plan['energy_delta_pct']:+.1f}% over {len(plan['steps'])} step(s)")
    print(f" validate   naive sum would have claimed {plan['naive_sum_of_steps_pct']:+.1f}% "
          f"(interaction error {plan['interaction_error_pct']:+.1f} pts)")
    gate = data["security_gate"]["proposals"]
    blocked = sum(1 for g in gate if "block" in g["state"])
    print(f" approve    {len(gate)} proposals, {blocked} blocked by policy, "
          f"{data['security_gate']['executed']} executed")
    print(f" audit      {data['audit']['entries']} entries, chain "
          f"{'valid' if data['audit']['chain_valid'] else 'BROKEN'}")
    print(f" cost       {data['finops']['currency']} "
          f"{data['finops']['unit_costs']['cost_per_1k_tokens']:.5f} per 1k tokens "
          f"({data['finops']['convention'].split(';')[0]})")
    print(f"{line}")
    print(f" provenance weakest={prov['weakest']}, measured={prov['measured_fraction']:.0%}, "
          f"calibrated={prov['calibrated_fraction']:.0%}")
    print(f" benchmark  {data['benchmark']['status']}   experiments "
          f"{data['experiments']['status']}")
    print(f"{line}")
    print(f"\nreport:  {md_path}")
    print(f"data:    {json_path}")
    print(f"reproduce: {result.context.reproduction_command}")

    if args.write_audit:
        Path(args.write_audit).write_text(json.dumps(data["audit"], indent=2))
        print(f"audit:   {args.write_audit}")


def cmd_calibrate(args) -> None:
    """Demonstrate the calibration loop against the twin.

    This deliberately produces a report headed NOT A CALIBRATION. Fitting the twin's
    coefficients to the twin's own output proves the fitting code works and nothing else.
    Point the same loop at a metered endpoint and the same report becomes evidence.
    """
    from services.calibration import (
        CalibrationSample, MeasurementSession, build_record, calibration_report,
        fit_coefficients, validate,
    )
    from services.energy_engine.model import Provenance

    session = MeasurementSession(
        model=args.model, hardware="digital twin (no hardware)",
        runtime="watts simulation", adapter="watts-digital-twin",
        notes="self-consistency check: samples came from the simulator, not a meter")

    shapes = [(256, 64), (256, 512), (1024, 64), (1024, 512), (4096, 128), (4096, 1024)]
    for inp, out in shapes:
        for seed in (11, 12):
            result = _simulate(rps=0.5, duration=60.0, gpus=1, seed=seed,
                               default_model=args.model)
            matching = [r for r in result.records if r.energy_wh]
            if not matching:
                continue
            reference = matching[0]
            profile_cost = reference.input_tokens + reference.output_tokens
            scale = (inp + out) / max(profile_cost, 1)
            session.add(CalibrationSample(
                input_tokens=inp, output_tokens=out,
                measured_wh=(reference.energy_wh or 0.0) * scale,
                batch_size=reference.batch_size or 1,
                latency_ms=reference.latency_ms, timestamp=reference.timestamp,
                provenance=Provenance.SIMULATED, source="watts-digital-twin"))
    session.close()

    coefficients = fit_coefficients(session)
    validation = validate(coefficients, session.samples, in_sample=True)
    record = build_record(session, coefficients, validation,
                          notes="produced by `python watts.py calibrate`")
    text = calibration_report(record)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text)
    print(text)
    print(f"\nwritten to {out_path}")
    print(f"registry status for {args.model}: {record.status}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="watts", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--rps", type=float, default=2.0)
        p.add_argument("--duration", type=float, default=300.0)
        p.add_argument("--gpus", type=int, default=4)
        return p

    d = common(sub.add_parser("demo", help="run the end-to-end pipeline and write the report"))
    d.add_argument("--seed", type=int, default=42,
                   help="the same seed reproduces the same run exactly")
    d.add_argument("--out", default="reports", help="directory for the generated report")
    d.add_argument("--write-audit", type=str, default="")
    d.set_defaults(func=cmd_demo)

    c = sub.add_parser("calibrate", help="run the calibration loop (against the twin by default)")
    c.add_argument("--model", default="watts-sim-medium")
    c.add_argument("--out", default="reports/calibration.md")
    c.set_defaults(func=cmd_calibrate)

    s = common(sub.add_parser("simulate", help="one simulation run"))
    s.add_argument("--seed", type=int, default=11)
    s.set_defaults(func=cmd_simulate)

    b = common(sub.add_parser("bench", help="run the strategy benchmark"))
    b.add_argument("--seeds", type=int, nargs="+", default=[11, 12, 13])
    b.add_argument("--out", default="benchmarks/results")
    b.set_defaults(func=cmd_bench)

    dash = common(sub.add_parser("dashboard", help="build the Command Center HTML"))
    dash.add_argument("--out", default="apps/dashboard/dist/index.html")
    dash.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
