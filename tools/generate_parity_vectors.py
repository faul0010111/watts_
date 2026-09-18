"""Generate the parity vectors both implementations must reproduce.

The Python reference implementation is the specification. The JVM control plane is a second
implementation of the same rules, and two implementations of the same rules drift unless
something forces them not to. These vectors are that something: fixed inputs with the
outputs the reference produces, checked by a Python test and by a JUnit test reading the
same file.

Four areas are covered, chosen because a silent divergence in any of them would be
expensive and invisible:

* **energy attribution** — a routing decision built on a different input/output split is a
  different decision;
* **policy decisions** — the simulator allowing what production denies is the worst
  possible failure mode for this project;
* **budget calculations** — chargeback that disagrees between systems is chargeback nobody
  trusts;
* **canonical JSON** — if the two serialise a record differently, every signature fails.

Run after changing any of those: `python tools/generate_parity_vectors.py`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.energy_engine.model import Provenance  # noqa: E402
from services.optimization.budgets import Budget, BudgetState, BudgetTracker  # noqa: E402
from services.security.policy import PolicyEngine, PolicyInput  # noqa: E402
from services.telemetry.schema import RequestRecord  # noqa: E402
from services.token_engine import attribute_energy, load_default_profiles  # noqa: E402

OUT = ROOT / "parity" / "vectors.json"
REGISTRY = load_default_profiles()
ENGINE = PolicyEngine()


def attribution_vectors() -> list[dict]:
    cases = [
        ("attr-01", "watts-sim-medium", 1_000, 100, 0.0500),
        ("attr-02", "watts-sim-medium", 100, 1_000, 0.1000),
        ("attr-03", "watts-sim-small", 4_000, 256, 0.0250),
        ("attr-04", "watts-sim-large", 8_000, 2_000, 1.2500),
        ("attr-05", "watts-sim-medium", 0, 0, 0.0100),      # degenerate: nothing to attribute
    ]
    out = []
    for case_id, model, inp, outp, energy in cases:
        result = attribute_energy(energy, inp, outp, REGISTRY.get(model))
        out.append({
            "id": case_id,
            "input": {"model": model, "input_tokens": inp, "output_tokens": outp,
                      "energy_wh": energy},
            "expected": {
                "input_energy_wh": round(result.input_energy_wh, 12),
                "output_energy_wh": round(result.output_energy_wh, 12),
                "wh_per_input_token": round(result.wh_per_input_token, 12),
                "wh_per_output_token": round(result.wh_per_output_token, 12),
            },
        })
    return out


def policy_vectors() -> list[dict]:
    cases = [
        ("pol-01", dict(action="route_workload", model="watts-sim-large",
                        approved_models=("watts-sim-small", "watts-sim-medium"))),
        ("pol-02", dict(action="route_workload", model="watts-sim-medium",
                        approved_models=("watts-sim-medium",))),
        ("pol-03", dict(action="route_workload", model="watts-sim-medium", provider="public-api",
                        approved_models=("watts-sim-medium",), approved_providers=("on-prem",),
                        data_classification="restricted")),
        ("pol-04", dict(action="apply_optimization", weakens_control=True,
                        energy_saving_wh=50_000.0)),
        ("pol-05", dict(action="apply_optimization", optimization_type="defer_workload",
                        workload_criticality="critical")),
        ("pol-06", dict(action="apply_optimization", slo_headroom_ms=40.0,
                        expected_latency_delta_ms=250.0)),
        ("pol-07", dict(action="execute_change", roles=("sre",), requires_human_approval=True,
                        human_approved=False)),
        ("pol-08", dict(action="execute_change", roles=("viewer",),
                        requires_human_approval=False)),
        ("pol-09", dict(action="read_telemetry", cross_tenant=True)),
        ("pol-10", dict(action="execute_change", roles=("sre",), requires_human_approval=True,
                        human_approved=True)),
    ]
    out = []
    for case_id, kwargs in cases:
        decision = ENGINE.evaluate(PolicyInput(**kwargs))
        out.append({
            "id": case_id,
            "input": {k: (list(v) if isinstance(v, tuple) else v) for k, v in kwargs.items()},
            "expected": {"allow": decision.allow, "rule_id": decision.rule_id},
        })
    return out


def budget_vectors() -> list[dict]:
    cases = [
        ("bud-01", Budget("w", energy_wh_per_hour=100.0),
         BudgetState("w", window_s=360.0, energy_wh=20.0)),
        ("bud-02", Budget("w", energy_wh_per_hour=1_000.0),
         BudgetState("w", window_s=3_600.0, energy_wh=500.0)),
        ("bud-03", Budget("w", tokens_per_hour=1_000_000.0),
         BudgetState("w", window_s=600.0, tokens=200_000)),
        ("bud-04", Budget("w", wh_per_successful_request=0.05),
         BudgetState("w", window_s=600.0, energy_wh=30.0, successful_requests=400)),
    ]
    out = []
    for case_id, budget, state in cases:
        result = BudgetTracker({"w": budget}).evaluate(state)
        out.append({
            "id": case_id,
            "input": {
                "budget": {k: v for k, v in budget.__dict__.items() if v is not None},
                "state": state.__dict__,
            },
            "expected": {
                "breaches": [{"dimension": b["dimension"],
                              "projected": round(b["projected"], 9),
                              "utilization": round(b["utilization"], 9)}
                             for b in result["breaches"]],
            },
        })
    return out


def canonical_json_vectors() -> list[dict]:
    record = RequestRecord(
        request_id="req-parity-001", model="watts-sim-medium", provider="on-prem",
        input_tokens=1_000, output_tokens=120, latency_ms=842.5, gpu_id="gpu-0",
        tenant="acme", timestamp=1_750_000_000.0, security_policy="default",
        prompt_hash="0123456789abcdef", context_prefix_hash=None,
        session_id_hash="fedcba9876543210", task_class="extraction", batch_size=8,
        context_window=32_000, success=True, cache_hit=False, agent_step=0,
        energy_wh=0.0421, energy_provenance=Provenance.SIMULATED.value,
        queue_wait_ms=120.0, service_ms=722.5)
    return [{
        "id": "json-01",
        "input": record.as_dict(),
        "expected": {
            "canonical_json": record.canonical_json(),
            "signature_hex": record.signature(b"parity-test-key"),
            "hmac_key_utf8": "parity-test-key",
        },
    }]


def main() -> None:
    payload = {
        "schema_version": "1",
        "purpose": ("fixed inputs and the outputs the Python reference implementation "
                    "produces; any second implementation must reproduce them exactly"),
        "tolerance": 1e-9,
        "note": ("regenerate with `python tools/generate_parity_vectors.py` whenever the "
                 "reference behaviour changes deliberately, and never to make a failing "
                 "implementation pass"),
        "attribution": attribution_vectors(),
        "policy": policy_vectors(),
        "budgets": budget_vectors(),
        "canonical_json": canonical_json_vectors(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    counts = {k: len(v) for k, v in payload.items() if isinstance(v, list)}
    print(f"wrote {OUT} ({counts})")


if __name__ == "__main__":
    main()
