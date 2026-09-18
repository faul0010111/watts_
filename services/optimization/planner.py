"""Optimisation engine 2.0: a planner, not a list of tips.

    current state → generate candidates → simulate each → evaluate energy, latency,
    quality, security, availability → build a plan → validate the plan

Two properties separate this from ranking recommendations by expected saving.

**Candidates are simulated, not asserted.** Every delta in a plan comes from running the
configuration through an evaluator - the digital twin locally, a canary in production -
and the evaluation carries the provenance of whatever produced it.

**Effects are not additive.** Prefix caching and a bigger batch window both reduce the same
prefill work; applying them in sequence saves far less than the sum of their individual
savings. The planner therefore re-simulates after every accepted step and reports the gap
between the naive sum and the recalculated total, because that gap is exactly the error a
spreadsheet of recommendations would have made.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable, Sequence

from ..energy_engine.model import Provenance
from ..security.policy import PolicyEngine, PolicyInput
from ..token_engine.efficiency import EfficiencyReport, FindingType
from .frontier import FrontierPoint, WorkloadConstraints
from .quality import QualityRegistry

# An evaluator takes a set of serving knobs and returns metrics for that configuration.
# It is deliberately a plain callable: the planner does not know whether the numbers came
# from a twin, a canary deployment or a replay of recorded telemetry.
Evaluator = Callable[[dict], dict]


@dataclass(frozen=True)
class CandidateChange:
    """A single, reversible change to the serving configuration."""

    id: str
    name: str
    action: str                       # machine-readable knob name
    knobs: dict                       # what changes in the serving configuration
    rationale: str
    evidence: tuple[str, ...] = ()
    weakens_control: bool = False
    affects_criticality: str = ""     # set when the change touches critical workloads
    expected_latency_cost_ms: float = 0.0
    reversible: bool = True

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "action": self.action, "knobs": self.knobs,
                "rationale": self.rationale, "evidence": list(self.evidence),
                "reversible": self.reversible}


@dataclass(frozen=True)
class CandidateEvaluation:
    """What a candidate actually did when it was simulated, on every axis that matters."""

    candidate: CandidateChange
    energy_delta_wh: float
    energy_delta_pct: float
    latency_delta_ms: float
    quality_delta: float | None       # None when quality for this configuration is unmeasured
    security_impact: str
    availability_impact: str
    confidence: str
    evidence: tuple[str, ...]
    provenance: Provenance
    policy_result: dict
    approval_requirement: str
    admissible: bool
    reasons: tuple[str, ...] = ()
    metrics: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "candidate": self.candidate.as_dict(),
            "energy_delta_wh": self.energy_delta_wh,
            "energy_delta_pct": self.energy_delta_pct,
            "latency_delta_ms": self.latency_delta_ms,
            "quality_delta": self.quality_delta,
            "security_impact": self.security_impact,
            "availability_impact": self.availability_impact,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "provenance": self.provenance.value,
            "policy_result": self.policy_result,
            "approval_requirement": self.approval_requirement,
            "admissible": self.admissible,
            "reasons": list(self.reasons),
        }


@dataclass
class PlanStep:
    order: int
    evaluation: CandidateEvaluation
    metrics_before: dict
    metrics_after: dict
    cumulative_energy_delta_pct: float

    def as_dict(self) -> dict:
        return {
            "order": self.order,
            "change": self.evaluation.candidate.name,
            "action": self.evaluation.candidate.action,
            "energy_delta_pct_at_this_step": self.evaluation.energy_delta_pct,
            "cumulative_energy_delta_pct": self.cumulative_energy_delta_pct,
            "p95_before_ms": self.metrics_before.get("p95_latency_ms"),
            "p95_after_ms": self.metrics_after.get("p95_latency_ms"),
            "policy_result": self.evaluation.policy_result,
            "approval_requirement": self.evaluation.approval_requirement,
        }


@dataclass
class OptimizationPlan:
    workload: str
    baseline: dict
    steps: list[PlanStep] = field(default_factory=list)
    rejected: list[CandidateEvaluation] = field(default_factory=list)
    final_metrics: dict = field(default_factory=dict)
    final_validation: dict = field(default_factory=dict)
    provenance: Provenance = Provenance.SIMULATED

    @property
    def energy_delta_pct(self) -> float:
        base = self.baseline.get("facility_wh_per_1k_tokens") or 0.0
        final = self.final_metrics.get("facility_wh_per_1k_tokens") or 0.0
        return (final - base) / base * 100.0 if base else 0.0

    @property
    def naive_sum_pct(self) -> float:
        """What a list of independent recommendations would have promised."""
        return sum(s.evaluation.energy_delta_pct for s in self.steps)

    @property
    def interaction_error_pct(self) -> float:
        """Reality minus promise, in percentage points.

        Positive means the plan saved *less* than summing the steps would have promised,
        which is the normal case: two changes that remove the same prefill work cannot both
        remove it. A spreadsheet of independent recommendations makes exactly this error.
        """
        return self.energy_delta_pct - self.naive_sum_pct

    def as_dict(self) -> dict:
        return {
            "workload": self.workload,
            "provenance": self.provenance.value,
            "baseline": self.baseline,
            "steps": [s.as_dict() for s in self.steps],
            "rejected": [r.as_dict() for r in self.rejected],
            "final_metrics": self.final_metrics,
            "final_validation": self.final_validation,
            "energy_delta_pct": self.energy_delta_pct,
            "naive_sum_of_steps_pct": self.naive_sum_pct,
            "interaction_error_pct": self.interaction_error_pct,
            "note": ("each step was re-simulated on the state left by the previous one; "
                     "the interaction error is what summing independent estimates would "
                     "have got wrong"),
        }

    def to_markdown(self) -> str:
        lines = [f"### Optimisation plan — {self.workload}", "",
                 "| # | Change | Δ energy at step | Cumulative | p95 after | Gate |",
                 "|---|---|---|---|---|---|"]
        for s in self.steps:
            lines.append(
                f"| {s.order} | {s.evaluation.candidate.name} | "
                f"{s.evaluation.energy_delta_pct:+.1f}% | "
                f"{s.cumulative_energy_delta_pct:+.1f}% | "
                f"{s.metrics_after.get('p95_latency_ms', 0):,.0f} ms | "
                f"{s.evaluation.approval_requirement} |")
        if not self.steps:
            lines.append("| — | no admissible change | — | — | — | — |")
        lines += ["",
                  f"Plan effect: **{self.energy_delta_pct:+.1f}%** energy per 1k tokens. "
                  f"Summing the steps independently would have claimed "
                  f"{self.naive_sum_pct:+.1f}% — an over-claim of "
                  f"{self.interaction_error_pct:+.1f} points (positive means the plan "
                  f"delivered less than the sum promised).",
                  ""]
        for r in self.rejected:
            lines.append(f"- rejected **{r.candidate.name}**: {r.reasons[0] if r.reasons else 'n/a'}")
        return "\n".join(lines)


class OptimizationPlanner:
    """Builds and validates plans. Executes nothing."""

    def __init__(self, evaluator: Evaluator, constraints: WorkloadConstraints,
                 policy: PolicyEngine | None = None,
                 quality: QualityRegistry | None = None,
                 min_improvement_pct: float = 1.0) -> None:
        self.evaluator = evaluator
        self.constraints = constraints
        self.policy = policy or PolicyEngine()
        self.quality = quality or QualityRegistry()
        self.min_improvement_pct = min_improvement_pct

    # --- candidate generation ---------------------------------------------

    def generate_candidates(self, state: dict,
                            report: EfficiencyReport | None = None) -> list[CandidateChange]:
        """Propose changes the evidence supports, and only those.

        A candidate with no finding and no telemetry signal behind it is not generated:
        the planner is not a checklist of things one could try.
        """
        candidates: list[CandidateChange] = []
        findings = {f.type: f for f in (report.findings if report else [])}

        if FindingType.DUPLICATE_CALL in findings:
            f = findings[FindingType.DUPLICATE_CALL]
            candidates.append(CandidateChange(
                _cid(), "Enable per-tenant response cache", "enable_response_cache",
                {"response_cache": True},
                "Identical prompts are recomputed inside the dedup window.",
                tuple(f.evidence[:2]) + (f"{f.affected_requests} duplicate requests observed",)))

        if FindingType.REPEATED_CONTEXT in findings:
            f = findings[FindingType.REPEATED_CONTEXT]
            candidates.append(CandidateChange(
                _cid(), "Enable prefix caching", "enable_prefix_cache",
                {"prefix_cache": True},
                "The same context prefix is re-processed across requests.",
                tuple(f.evidence[:2])))

        if FindingType.OVERLONG_OUTPUT in findings:
            f = findings[FindingType.OVERLONG_OUTPUT]
            candidates.append(CandidateChange(
                _cid(), "Cap output length per task class", "cap_output",
                {"output_cap": {"reasoning": 600, "summarization": 400}},
                "Decode dominates request energy and the output tail is unbounded.",
                tuple(f.evidence[:2])))

        batch = state.get("mean_batch_size", 0.0)
        headroom = state.get("latency_headroom_ms", 0.0)
        if batch < 8 and headroom > 100:
            candidates.append(CandidateChange(
                _cid(), "Widen the dynamic batching window", "increase_batch_window",
                {"max_batch": 16, "max_wait_s": 0.2},
                "Batches are small while the SLO still has latency headroom to spend.",
                (f"mean batch size {batch:.1f}",
                 f"{headroom:,.0f} ms of p95 headroom remaining"),
                expected_latency_cost_ms=200.0))

        if state.get("routing_enabled") is False:
            candidates.append(CandidateChange(
                _cid(), "Route by task quality tier", "enable_routing",
                {"routing": True},
                "Every task is served by one model regardless of the quality it needs.",
                ("single-model serving observed in the window",)))

        return candidates

    # --- candidate evaluation ----------------------------------------------

    def evaluate(self, candidate: CandidateChange, base_knobs: dict,
                 base_metrics: dict, slo_headroom_ms: float) -> CandidateEvaluation:
        """Simulate the candidate and judge it on all five axes."""
        knobs = {**base_knobs, **candidate.knobs}
        metrics = self.evaluator(knobs)

        base_energy = base_metrics.get("facility_wh_per_1k_tokens") or 0.0
        new_energy = metrics.get("facility_wh_per_1k_tokens") or 0.0
        energy_delta_pct = ((new_energy - base_energy) / base_energy * 100.0) if base_energy else 0.0
        latency_delta = (metrics.get("p95_latency_ms", 0.0)
                         - base_metrics.get("p95_latency_ms", 0.0))

        decision = self.policy.evaluate(PolicyInput(
            action="apply_optimization",
            optimization_type=candidate.action,
            weakens_control=candidate.weakens_control,
            workload_criticality=candidate.affects_criticality or "normal",
            slo_headroom_ms=slo_headroom_ms,
            expected_latency_delta_ms=max(latency_delta, candidate.expected_latency_cost_ms),
            energy_saving_wh=abs(min(0.0, new_energy - base_energy)),
        ))

        reasons: list[str] = []
        if not decision.allow:
            reasons.append(f"{decision.rule_id}: {decision.reason}")

        # Quality: unmeasured is not the same as unchanged.
        quality_delta = None
        floor = self.constraints.quality_floor
        if floor is not None and candidate.action == "enable_routing":
            ok, why = self.quality.check("routed", floor)
            if not ok:
                reasons.append(why)
            else:
                quality_delta = 0.0
        elif floor is None or candidate.action in ("enable_response_cache", "enable_prefix_cache",
                                                   "increase_batch_window"):
            # These change what is computed, not which model answers; quality is unaffected
            # unless the cache returns stale content, which is a correctness setting.
            quality_delta = 0.0

        p95 = metrics.get("p95_latency_ms", 0.0)
        if p95 > self.constraints.p95_latency_ms:
            reasons.append(f"p95 would reach {p95:,.0f} ms, beyond the SLO "
                           f"{self.constraints.p95_latency_ms:,.0f} ms")

        error_rate = metrics.get("error_rate", 0.0)
        availability_impact = "none"
        if error_rate > self.constraints.max_error_rate:
            availability_impact = f"error rate {error_rate:.2%} above limit"
            reasons.append(availability_impact)

        if energy_delta_pct > -self.min_improvement_pct:
            reasons.append(f"energy change {energy_delta_pct:+.1f}% does not clear the "
                           f"{self.min_improvement_pct:.1f}% threshold worth a change")

        provenance = Provenance(metrics.get("provenance", Provenance.SIMULATED.value))
        confidence = ("high" if provenance in (Provenance.MEASURED, Provenance.DERIVED)
                      else "medium" if metrics.get("seeds", 1) > 1 else "low")

        return CandidateEvaluation(
            candidate=candidate,
            energy_delta_wh=(new_energy - base_energy),
            energy_delta_pct=energy_delta_pct,
            latency_delta_ms=latency_delta,
            quality_delta=quality_delta,
            security_impact=("weakens control" if candidate.weakens_control
                             else "none" if decision.allow else "blocked by policy"),
            availability_impact=availability_impact,
            confidence=confidence,
            evidence=candidate.evidence + (
                f"simulated: {new_energy:.4f} vs {base_energy:.4f} facility Wh/1k tokens",),
            provenance=provenance,
            policy_result={"allow": decision.allow, "rule_id": decision.rule_id,
                           "reason": decision.reason},
            approval_requirement=("blocked" if reasons else "human approval required"),
            admissible=not reasons,
            reasons=tuple(reasons),
            metrics=metrics,
        )

    # --- plan construction --------------------------------------------------

    def plan(self, base_knobs: dict, candidates: Sequence[CandidateChange],
             *, max_steps: int = 4) -> OptimizationPlan:
        """Greedy, re-simulated after every accepted step.

        Greedy because the operator has to apply the steps in some order anyway, and the
        biggest safe win first is the order that survives being interrupted halfway.
        """
        baseline = self.evaluator(base_knobs)
        plan = OptimizationPlan(workload=self.constraints.name, baseline=baseline,
                                final_metrics=baseline,
                                provenance=Provenance(baseline.get("provenance",
                                                                   Provenance.SIMULATED.value)))
        state_knobs = dict(base_knobs)
        current = baseline
        remaining = list(candidates)

        while remaining and len(plan.steps) < max_steps:
            headroom = self.constraints.p95_latency_ms - current.get("p95_latency_ms", 0.0)
            evaluations = [self.evaluate(c, state_knobs, current, headroom) for c in remaining]
            admissible = [e for e in evaluations if e.admissible]
            if not admissible:
                plan.rejected.extend(e for e in evaluations
                                     if e.candidate.id not in {r.candidate.id for r in plan.rejected})
                break

            best = min(admissible, key=lambda e: e.energy_delta_pct)
            state_knobs = {**state_knobs, **best.candidate.knobs}
            after = best.metrics
            base_energy = baseline.get("facility_wh_per_1k_tokens") or 0.0
            cumulative = (((after.get("facility_wh_per_1k_tokens") or 0.0) - base_energy)
                          / base_energy * 100.0) if base_energy else 0.0
            plan.steps.append(PlanStep(len(plan.steps) + 1, best, current, after, cumulative))
            plan.rejected.extend(e for e in evaluations
                                 if not e.admissible
                                 and e.candidate.id not in {r.candidate.id for r in plan.rejected})
            current = after
            remaining = [c for c in remaining if c.id != best.candidate.id]

        plan.final_metrics = current
        plan.final_validation = self._validate(current)
        return plan

    def _validate(self, metrics: dict) -> dict:
        """The check that runs on the end state, not on any individual step."""
        c = self.constraints
        checks = {
            "p95_within_slo": metrics.get("p95_latency_ms", 0.0) <= c.p95_latency_ms,
            "error_rate_within_limit": metrics.get("error_rate", 0.0) <= c.max_error_rate,
            "no_thermal_excursion": not metrics.get("exceeded_critical_temp", False),
        }
        if c.energy_budget_wh_per_hour and metrics.get("facility_wh_per_hour"):
            checks["within_energy_budget"] = (metrics["facility_wh_per_hour"]
                                              <= c.energy_budget_wh_per_hour)
        failed = [k for k, ok in checks.items() if not ok]
        return {
            "checks": checks,
            "passed": not failed,
            "failed": failed,
            "note": ("validation runs on the final state: a sequence of individually safe "
                     "steps can still land somewhere unsafe"),
        }


def _cid() -> str:
    return f"cand-{uuid.uuid4().hex[:8]}"
