"""Policy engine.

This is the Python mirror of ``policies/*.rego``. OPA is the enforcement point in a
deployed WATTS; this module exists so the simulator, the tests and the CI suite can
evaluate the same rules without a running OPA, and so a policy change can be diffed
against both implementations (tests/test_policy_parity.py).

Two invariants hold everywhere in WATTS:

1. Default deny for anything that routes production traffic or changes infrastructure.
2. An energy optimisation that would weaken a security control is blocked, not traded
   off. Energy is never a reason to lower a control.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence


@dataclass(frozen=True)
class PolicyInput:
    action: str                       # route_workload | apply_optimization | execute_change | read_telemetry
    principal: str = "system"
    roles: tuple[str, ...] = ()
    tenant: str = "default"
    environment: str = "production"   # production | staging | dev
    model: str = ""
    provider: str = ""
    data_classification: str = "public"   # public | internal | confidential | restricted
    approved_models: tuple[str, ...] = ()
    approved_providers: tuple[str, ...] = ()
    optimization_type: str = ""
    weakens_control: bool = False
    control_touched: str = ""
    energy_saving_wh: float = 0.0
    slo_headroom_ms: float = 0.0
    expected_latency_delta_ms: float = 0.0
    requires_human_approval: bool = True
    human_approved: bool = False
    workload_criticality: str = "normal"   # critical | normal | flexible
    cross_tenant: bool = False

    def as_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class PolicyDecision:
    allow: bool
    rule_id: str
    reason: str
    obligations: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"allow": self.allow, "rule_id": self.rule_id,
                "reason": self.reason, "obligations": list(self.obligations)}


@dataclass(frozen=True)
class Rule:
    """A deny rule. Rules only ever deny; absence of a deny plus an explicit allow passes."""

    id: str
    description: str
    applies_to: tuple[str, ...]
    predicate: Callable[[PolicyInput], bool]
    reason: str
    obligations: tuple[str, ...] = ()


def default_policy_set() -> list[Rule]:
    return [
        Rule(
            "WATTS-SEC-001",
            "Only approved models serve production workloads",
            ("route_workload",),
            lambda i: i.environment == "production" and bool(i.approved_models)
                      and i.model not in i.approved_models,
            "model is not on the production allowlist",
        ),
        Rule(
            "WATTS-SEC-002",
            "Sensitive data stays on approved models and providers",
            ("route_workload",),
            lambda i: i.data_classification in ("confidential", "restricted")
                      and (
                          (bool(i.approved_providers) and i.provider not in i.approved_providers)
                          or (bool(i.approved_models) and i.model not in i.approved_models)
                      ),
            "workload carries sensitive data and the target model or provider is not approved for it",
        ),
        Rule(
            "WATTS-SEC-003",
            "Energy optimisation never weakens a security control",
            ("apply_optimization", "execute_change"),
            lambda i: i.weakens_control,
            "optimisation would weaken a security control; energy savings are not a valid trade",
        ),
        Rule(
            "WATTS-SEC-004",
            "Infrastructure changes require human approval",
            ("execute_change",),
            lambda i: i.requires_human_approval and not i.human_approved,
            "change requires a recorded human approval before execution",
            ("record_approval", "write_audit_entry"),
        ),
        Rule(
            "WATTS-SEC-005",
            "No cross-tenant routing or aggregation",
            ("route_workload", "read_telemetry"),
            lambda i: i.cross_tenant,
            "cross-tenant access is denied; telemetry and routing are tenant-isolated",
        ),
        Rule(
            "WATTS-SEC-006",
            "Critical workloads are never delayed or downgraded for energy",
            ("apply_optimization",),
            lambda i: i.workload_criticality == "critical"
                      and i.optimization_type in ("defer_workload", "downgrade_model", "throttle"),
            "workload is critical; deferral, downgrade and throttling are not available to the optimiser",
        ),
        Rule(
            "WATTS-SEC-007",
            "An optimisation may not consume more latency than the SLO has left",
            ("apply_optimization",),
            lambda i: i.expected_latency_delta_ms > i.slo_headroom_ms,
            "expected latency increase exceeds the remaining SLO headroom",
        ),
        Rule(
            "WATTS-SEC-008",
            "Only operators and SREs may execute changes",
            ("execute_change",),
            lambda i: not ({"sre", "operator", "platform-admin"} & set(i.roles)),
            "principal lacks a role authorised to execute infrastructure changes",
        ),
    ]


class PolicyEngine:
    def __init__(self, rules: Sequence[Rule] | None = None) -> None:
        self.rules = list(rules) if rules is not None else default_policy_set()
        self.evaluations: list[tuple[PolicyInput, PolicyDecision]] = []

    def evaluate(self, inp: PolicyInput) -> PolicyDecision:
        obligations: list[str] = []
        for rule in self.rules:
            if inp.action not in rule.applies_to:
                continue
            if rule.predicate(inp):
                decision = PolicyDecision(False, rule.id, rule.reason, rule.obligations)
                self.evaluations.append((inp, decision))
                return decision
            obligations.extend(o for o in rule.obligations if o not in obligations)
        decision = PolicyDecision(True, "WATTS-ALLOW", "no deny rule matched", tuple(obligations))
        self.evaluations.append((inp, decision))
        return decision

    def explain(self, inp: PolicyInput) -> list[dict]:
        """Every applicable rule and whether it fired. Used by the Command Center."""
        out = []
        for rule in self.rules:
            if inp.action not in rule.applies_to:
                continue
            fired = rule.predicate(inp)
            out.append({"rule_id": rule.id, "description": rule.description,
                        "fired": fired, "effect": "deny" if fired else "pass"})
        return out
