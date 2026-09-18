"""What-if scenario simulator.

Answers questions of the form "what happens to energy, latency, cost and carbon if we
change X?" by running the twin under each configuration with the *same seed*, so the
only difference between scenarios is the change under test.

Scenarios are compared, never ranked into a single "best": the right trade depends on
the SLO and the policy of the workload, and WATTS does not decide that for the operator.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Sequence

from services.token_engine.economics import EconomicsInputs, TokenEconomics
from .twin import DatacenterConfig, ServingConfig, Simulation, WorkloadConfig


@dataclass
class Scenario:
    name: str
    question: str = ""
    datacenter: DatacenterConfig | None = None
    serving: ServingConfig | None = None
    workload: WorkloadConfig | None = None

    def run(self, base: "Scenario | None" = None, dt: float = 0.05) -> dict:
        sim = Simulation(
            dc=self.datacenter or (base.datacenter if base else None) or DatacenterConfig(),
            serving=self.serving or (base.serving if base else None) or ServingConfig(),
            workload=self.workload or (base.workload if base else None) or WorkloadConfig(),
            dt=dt,
        )
        result = sim.run()
        summary = result.summary()
        summary["scenario"] = self.name
        summary["question"] = self.question
        summary["config"] = result.config
        return summary


def compare_scenarios(scenarios: Sequence[Scenario], economics: EconomicsInputs | None = None,
                      dt: float = 0.05) -> dict:
    """Run every scenario and express each one relative to the first."""
    runs = [s.run(dt=dt) for s in scenarios]
    base = runs[0]
    rows = []
    for r in runs:
        row = {
            "scenario": r["scenario"],
            "question": r["question"],
            "facility_energy_wh": r["facility_energy_wh"],
            "wh_per_1k_tokens": r["wh_per_1k_tokens"],
            "wh_per_successful_request": r["wh_per_successful_request"],
            "p95_latency_ms": r["p95_latency_ms"],
            "tokens_per_s": r["tokens_per_s"],
            "pue": r["pue"],
            "error_rate": r["error_rate"],
            "max_gpu_temp_c": r["max_gpu_temp_c"],
            "delta_energy_pct": _pct(r["facility_energy_wh"], base["facility_energy_wh"]),
            "delta_p95_latency_pct": _pct(r["p95_latency_ms"], base["p95_latency_ms"]),
            "delta_throughput_pct": _pct(r["tokens_per_s"], base["tokens_per_s"]),
        }
        if economics:
            econ = TokenEconomics(int(r["total_tokens"]), r["facility_energy_wh"], 0.0, economics)
            row["cost"] = econ.total_cost
            row["carbon_g"] = econ.carbon_g
        rows.append(row)
    return {
        "provenance": "simulated",
        "baseline": base["scenario"],
        "scenarios": rows,
        "note": ("All figures come from the WATTS digital twin with a fixed seed. "
                 "They describe the model's behaviour, not any real accelerator. "
                 "Scenarios are compared, not ranked: choose against your own SLO and policy."),
    }


def _pct(value: float, base: float) -> float | None:
    if not base:
        return None
    return (value - base) / base * 100.0
