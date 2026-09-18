"""Human-in-the-loop change workflow and failure safety.

Recommendation -> impact analysis -> security validation -> human approval -> execution,
with every transition written to the audit chain. Failure safety is part of the workflow,
not an afterthought: if the optimiser itself misbehaves, the circuit breaker opens and
WATTS falls back to the safe default (change nothing), because an energy control plane
must never be able to cause an outage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from ..security.audit import AuditLog
from ..security.policy import PolicyEngine, PolicyInput
from ..security.rbac import Permission, Principal, RBAC
from .budgets import SLOReport
from .recommendations import Recommendation


class WorkflowState(str, Enum):
    PROPOSED = "proposed"
    IMPACT_ANALYSED = "impact_analysed"
    POLICY_BLOCKED = "policy_blocked"
    SLO_BLOCKED = "slo_blocked"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTED = "executed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class CircuitBreaker:
    """Opens after repeated failures; closes only after a cooldown and a successful probe."""

    def __init__(self, failure_threshold: int = 3, cooldown_s: float = 900.0) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def open(self) -> bool:
        return self.opened_at is not None

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self, now: float) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = now

    def allow(self, now: float) -> bool:
        if self.opened_at is None:
            return True
        if now - self.opened_at >= self.cooldown_s:
            self.opened_at = None
            self.failures = self.failure_threshold - 1
            return True
        return False


@dataclass
class ChangeWorkflow:
    policy: PolicyEngine
    audit: AuditLog
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    rate_limit_per_hour: int = 6
    _executions: list[float] = field(default_factory=list)

    def submit(
        self,
        rec: Recommendation,
        *,
        principal: Principal,
        slo: SLOReport,
        workload_criticality: str = "normal",
        now: float = 0.0,
    ) -> tuple[WorkflowState, str]:
        self.audit.append(actor=principal.id, action="propose", recommendation_id=rec.id,
                          policy_decision={}, change={"action": rec.action},
                          outcome=WorkflowState.PROPOSED.value, timestamp=now)

        if not self.breaker.allow(now):
            return self._record(rec, principal, WorkflowState.FAILED,
                                "optimisation circuit breaker is open; safe default is no change", now)

        # An optimisation may not be applied while reliability SLOs are already breached.
        if slo.reliability_violations:
            return self._record(rec, principal, WorkflowState.SLO_BLOCKED,
                                "reliability SLO already violated: " +
                                "; ".join(slo.reliability_violations), now)

        decision = self.policy.evaluate(PolicyInput(
            action="apply_optimization",
            principal=principal.id,
            roles=principal.roles,
            tenant=principal.tenant,
            optimization_type=rec.action,
            weakens_control=(rec.impact.security == "weakens control"),
            slo_headroom_ms=slo.latency_headroom_ms,
            expected_latency_delta_ms=rec.impact.latency_ms,
            workload_criticality=workload_criticality,
            requires_human_approval=rec.requires_approval,
        ))
        if not decision.allow:
            return self._record(rec, principal, WorkflowState.POLICY_BLOCKED,
                                f"{decision.rule_id}: {decision.reason}", now,
                                policy=decision.as_dict())

        if rec.requires_approval:
            return self._record(rec, principal, WorkflowState.AWAITING_APPROVAL,
                                "impact analysed and policy passed; waiting for a human decision",
                                now, policy=decision.as_dict())
        return self._record(rec, principal, WorkflowState.APPROVED,
                            "no approval required for this class of change", now,
                            policy=decision.as_dict())

    def approve(self, rec: Recommendation, approver: Principal, now: float = 0.0) -> WorkflowState:
        RBAC.require(approver, Permission.APPROVE_OPTIMIZATION)
        self._record(rec, approver, WorkflowState.APPROVED, "approved by human operator", now)
        return WorkflowState.APPROVED

    def execute(self, rec: Recommendation, operator: Principal,
                apply_fn: Callable[[Recommendation], bool], now: float = 0.0) -> WorkflowState:
        RBAC.require(operator, Permission.EXECUTE_CHANGE)

        recent = [t for t in self._executions if now - t < 3600.0]
        if len(recent) >= self.rate_limit_per_hour:
            self._record(rec, operator, WorkflowState.FAILED,
                         f"rate limit reached ({self.rate_limit_per_hour} changes/hour)", now)
            return WorkflowState.FAILED
        if not self.breaker.allow(now):
            self._record(rec, operator, WorkflowState.FAILED, "circuit breaker open", now)
            return WorkflowState.FAILED

        try:
            ok = apply_fn(rec)
        except Exception as exc:  # noqa: BLE001 - failure must never propagate to the data plane
            self.breaker.record_failure(now)
            self._record(rec, operator, WorkflowState.ROLLED_BACK, f"execution raised: {exc}", now)
            return WorkflowState.ROLLED_BACK

        self._executions.append(now)
        if not ok:
            self.breaker.record_failure(now)
            self._record(rec, operator, WorkflowState.ROLLED_BACK,
                         "post-change validation failed; previous configuration restored", now)
            return WorkflowState.ROLLED_BACK

        self.breaker.record_success()
        self._record(rec, operator, WorkflowState.EXECUTED, "change applied and validated", now)
        return WorkflowState.EXECUTED

    def _record(self, rec, principal, state: WorkflowState, reason: str, now: float,
                policy: dict | None = None) -> tuple[WorkflowState, str]:
        self.audit.append(actor=principal.id, action=state.value, recommendation_id=rec.id,
                          policy_decision=policy or {}, change={"action": rec.action},
                          outcome=reason, timestamp=now)
        return state, reason
