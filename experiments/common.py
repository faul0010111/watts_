"""Shared plumbing for WATTS experiments.

Every experiment: fixes a seed, varies one factor, keeps everything else constant, and
writes both JSON (for re-analysis) and Markdown (for reading) into experiments/results/.
Each file records the full configuration so a run can be reproduced exactly, and carries
the provenance of its numbers. No experiment writes a figure it did not compute.
"""
from __future__ import annotations

import json
import platform
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulation.twin import (  # noqa: E402
    CoolingSpec, DatacenterConfig, ServingConfig, Simulation, TaskMix, WorkloadConfig,
)

RESULTS = ROOT / "experiments" / "results"
DEFAULT_SEEDS = (11, 12, 13)


def run(serving: ServingConfig, workload: WorkloadConfig,
        dc: DatacenterConfig | None = None, dt: float = 0.05) -> dict:
    result = Simulation(dc=dc or DatacenterConfig(), serving=serving, workload=workload,
                        dt=dt).run()
    return result.summary()


def run_seeds(serving: ServingConfig, workload: WorkloadConfig,
              dc: DatacenterConfig | None = None, seeds: Sequence[int] = DEFAULT_SEEDS,
              dt: float = 0.05) -> dict:
    """Run one configuration across seeds and report mean and spread of each metric."""
    runs = [run(serving, replace(workload, seed=s), dc, dt) for s in seeds]
    keys = [k for k, v in runs[0].items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    out = {"seeds": list(seeds), "runs": runs}
    for k in keys:
        vals = [r[k] for r in runs]
        out[k] = sum(vals) / len(vals)
        out[f"{k}__spread"] = max(vals) - min(vals)
    return out


def single_task_workload(input_tokens: int, output_tokens: int, *, duration_s: float = 300.0,
                         rps: float = 1.0, tier: int = 3, seed: int = 11) -> WorkloadConfig:
    """A one-task-class workload, for isolating a single factor."""
    mix = (TaskMix("probe", 1.0, tier, (input_tokens, max(1, input_tokens // 20)),
                   (output_tokens, max(1, output_tokens // 20)), False, 60_000.0),)
    return WorkloadConfig(duration_s=duration_s, base_rps=rps, task_mix=mix,
                          duplicate_rate=0.0, failure_rate=0.0, seed=seed)


EXPERIMENT_SCHEMA_VERSION = "2"


def manifest(name: str, *, version: str, seed: int, configuration: dict, inputs: dict,
             method: str, metrics: list[str], outputs: list[str],
             provenance: str = "simulated") -> dict:
    """The manifest every experiment carries.

    An experiment result without its manifest is a table of numbers whose origin the reader
    has to take on trust. With it, the reader has the seed, the configuration, the method
    and the provenance, and can re-run the thing and compare.
    """
    return {
        "experiment_id": name,
        "version": version,
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "seed": seed,
        "configuration": configuration,
        "inputs": inputs,
        "method": method,
        "metrics": metrics,
        "outputs": outputs,
        "provenance": provenance,
        "reproduce": f"make experiment EXP={name}",
    }


def write(name: str, title: str, question: str, rows: list[dict], columns: list[tuple[str, str, str]],
          config: dict, findings: list[str], *, experiment_manifest: dict | None = None) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    auto_manifest = experiment_manifest or manifest(
        name, version="1.0", seed=config.get("seed", DEFAULT_SEEDS[0]),
        configuration=config, inputs={"rows": len(rows)},
        method="single-factor sweep on the WATTS digital twin, repeated across seeds",
        metrics=[c[0] for c in columns],
        outputs=[f"experiments/results/{name}.json", f"experiments/results/{name}.md"])
    payload = {
        "experiment": name,
        "manifest": auto_manifest,
        "title": title,
        "research_question": question,
        "provenance": "simulated",
        "disclaimer": ("Produced by the WATTS digital twin. Valid as a statement about the "
                       "model under the stated configuration, not as a hardware measurement."),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "config": config,
        "rows": rows,
        "findings": findings,
    }
    json_path = RESULTS / f"{name}.json"
    json_path.write_text(json.dumps(payload, indent=2))

    lines = [f"# {title}", "", f"**Research question.** {question}", "",
             f"*{payload['disclaimer']}*", "",
             "| " + " | ".join(c[1] for c in columns) + " |",
             "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        cells = []
        for key, _, fmt in columns:
            v = row.get(key)
            cells.append("-" if v is None else (v if isinstance(v, str) else fmt.format(v)))
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "## What the run shows", ""] + [f"- {f}" for f in findings]
    lines += ["", "## Manifest", "",
              f"- experiment `{auto_manifest['experiment_id']}` v{auto_manifest['version']} "
              f"(schema {auto_manifest['schema_version']})",
              f"- seed `{auto_manifest['seed']}`, provenance `{auto_manifest['provenance']}`",
              f"- method: {auto_manifest['method']}",
              f"- metrics: {', '.join(auto_manifest['metrics'])}"]
    lines += ["", "## Reproduce", "", "```bash", f"make experiment EXP={name}", "```", ""]
    (RESULTS / f"{name}.md").write_text("\n".join(lines))
    print("\n".join(lines))
    return json_path


def pct(value: float, base: float) -> float | None:
    return None if not base else (value - base) / base * 100.0
