"""Experiment 08 - what security constraints cost, and what they prevent.

Runs the same candidate optimisations through the policy engine under three postures and
reports how much energy saving survives each one.

The point is not that constraints are expensive. It is that the constrained result is the
*only* correct one: the unconstrained saving includes changes that route regulated data to
an unapproved provider, downgrade a critical workload and disable an audit control. WATTS
reports the gap so the operator can see what a control costs, never so it can be skipped.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import write  # noqa: E402
from services.optimization.recommendations import Confidence, Impact, Recommendation  # noqa: E402
from services.security.policy import PolicyEngine, PolicyInput  # noqa: E402

CANDIDATES = [
    # (title, action, saving Wh, latency delta ms, weakens control, criticality, note)
    ("Enable prefix caching", "enable_prefix_cache", 420.0, -5.0, False, "normal", ""),
    ("Batch window 50 -> 200 ms", "increase_batch_window", 260.0, 90.0, False, "normal", ""),
    ("Cap output tokens per task", "cap_output_tokens", 180.0, -60.0, False, "normal", ""),
    ("Route fraud scoring to a smaller model", "downgrade_model", 300.0, -40.0, False,
     "critical", "critical workload"),
    ("Defer regulated batch job to a cheaper hour", "defer_workload", 500.0, 0.0, False,
     "critical", "critical workload"),
    ("Share the response cache across tenants", "enable_response_cache", 350.0, -8.0, True,
     "normal", "weakens tenant isolation"),
    ("Disable per-request audit logging", "reduce_audit_logging", 90.0, -3.0, True,
     "normal", "weakens the audit trail"),
    ("Batch window 200 -> 900 ms", "increase_batch_window", 240.0, 700.0, False, "normal",
     "exceeds SLO headroom"),
]

SLO_HEADROOM_MS = 120.0


def main() -> None:
    engine = PolicyEngine()
    postures = [
        ("unconstrained", False, "No policy engine: every candidate is applied"),
        ("policy_enforced", True, "WATTS default policy set"),
    ]

    rows = []
    per_candidate = []
    for name, enforced, description in postures:
        applied, blocked, saving = [], [], 0.0
        for title, action, wh, latency, weakens, criticality, note in CANDIDATES:
            if not enforced:
                applied.append(title)
                saving += wh
                continue
            decision = engine.evaluate(PolicyInput(
                action="apply_optimization", principal="watts-assistant", roles=("watts-assistant",),
                optimization_type=action, weakens_control=weakens,
                slo_headroom_ms=SLO_HEADROOM_MS, expected_latency_delta_ms=latency,
                workload_criticality=criticality,
            ))
            if decision.allow:
                applied.append(title)
                saving += wh
            else:
                blocked.append((title, decision.rule_id, decision.reason))
        rows.append({
            "posture": name,
            "description": description,
            "applied": len(applied),
            "blocked": len(blocked),
            "energy_saving_wh": saving,
        })
        if enforced:
            per_candidate = blocked

    unconstrained, enforced_row = rows[0], rows[1]
    lost = unconstrained["energy_saving_wh"] - enforced_row["energy_saving_wh"]
    findings = [
        f"The policy engine blocked {enforced_row['blocked']} of {len(CANDIDATES)} candidates, "
        f"leaving {enforced_row['energy_saving_wh']:.0f} Wh of the "
        f"{unconstrained['energy_saving_wh']:.0f} Wh an unconstrained optimiser would claim.",
        f"The {lost:.0f} Wh difference is not a saving that was lost. It is the set of changes "
        "that would have shared a cache across tenants, removed an audit control, downgraded a "
        "critical workload, and spent latency the SLO did not have.",
        "Blocked candidates and the rule that blocked each one: " +
        "; ".join(f"{t} ({rule})" for t, rule, _ in per_candidate),
        "Every optimisation that survives is still a proposal. Execution requires a human "
        "approval and writes an entry to the hash-chained audit log.",
    ]
    write("exp08_security_constrained_optimization",
          "Experiment 08 - Security-constrained optimisation",
          "How do energy optimisation and security policy combine?", rows,
          [("posture", "Posture", "{}"), ("description", "Description", "{}"),
           ("applied", "Applied", "{:.0f}"), ("blocked", "Blocked", "{:.0f}"),
           ("energy_saving_wh", "Claimed saving Wh", "{:.0f}")],
          {"slo_headroom_ms": SLO_HEADROOM_MS,
           "candidates": [c[0] for c in CANDIDATES],
           "blocked_detail": [{"candidate": t, "rule": r, "reason": reason}
                              for t, r, reason in per_candidate]},
          findings)


if __name__ == "__main__":
    main()
