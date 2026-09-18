"""Optimisation engine.

Every recommendation is a hypothesis with an expected impact across five axes, a
confidence, and the evidence that produced it. A recommendation with no evidence is not
emitted. Nothing here executes anything: execution goes through ``workflow.py``, which
requires a policy decision and a human.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Sequence

from ..telemetry.schema import RequestRecord
from ..token_engine.efficiency import EfficiencyReport, FindingType


class Confidence(str, Enum):
    LOW = "low"           # heuristic only, validate in the simulator first
    MEDIUM = "medium"     # supported by aggregate telemetry
    HIGH = "high"         # supported by a direct, repeated observation


@dataclass(frozen=True)
class Impact:
    energy_wh: float           # negative = saving
    latency_ms: float          # positive = slower
    quality: str               # "none" | "possible regression" | "improved"
    security: str              # "none" | "requires review" | "weakens control"
    cost: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Recommendation:
    id: str
    title: str
    action: str                 # machine-readable: enable_prefix_cache | increase_batch | ...
    rationale: str
    impact: Impact
    confidence: Confidence
    evidence: tuple[str, ...]
    requires_approval: bool = True
    reversible: bool = True
    validation: str = "run the scenario in simulation/ before applying to production"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["impact"] = self.impact.as_dict()
        d["confidence"] = self.confidence.value
        d["evidence"] = list(self.evidence)
        return d


class OptimizationEngine:
    """Turns efficiency findings and telemetry into ranked, auditable recommendations."""

    def __init__(self, min_energy_wh: float = 1e-6) -> None:
        self.min_energy_wh = min_energy_wh

    def from_efficiency(self, report: EfficiencyReport) -> list[Recommendation]:
        recs: list[Recommendation] = []
        for f in report.findings:
            if f.wasted_energy_wh < self.min_energy_wh and f.type != FindingType.UNDERUSED_CONTEXT_WINDOW:
                continue
            builder = {
                FindingType.DUPLICATE_CALL: self._dedupe,
                FindingType.REPEATED_CONTEXT: self._prefix_cache,
                FindingType.OVERSIZED_CONTEXT: self._trim_context,
                FindingType.OVERLONG_OUTPUT: self._cap_output,
                FindingType.REDUNDANT_AGENT_LOOP: self._agent_budget,
                FindingType.UNDERUSED_CONTEXT_WINDOW: self._smaller_window,
                FindingType.FAILED_WORK: self._fix_errors,
            }.get(f.type)
            if builder:
                recs.append(builder(f))
        return sorted(recs, key=lambda r: r.impact.energy_wh)

    def from_utilization(self, records: Sequence[RequestRecord], gpu_util_mean: float,
                         batch_mean: float, slo_headroom_ms: float) -> list[Recommendation]:
        recs = []
        if gpu_util_mean < 0.45 and batch_mean < 8 and slo_headroom_ms > 50:
            energy = sum(r.energy_wh or 0.0 for r in records)
            recs.append(Recommendation(
                _rid(), "Raise the dynamic batching window",
                "increase_batch_window",
                "Accelerators are drawing near-idle power for a large share of wall-clock time. "
                "Batching more requests per step amortises fixed per-step cost over more tokens.",
                Impact(energy_wh=-0.10 * energy, latency_ms=min(slo_headroom_ms * 0.4, 60.0),
                       quality="none", security="none"),
                Confidence.MEDIUM,
                (f"mean GPU utilisation {gpu_util_mean:.0%}",
                 f"mean batch size {batch_mean:.1f}",
                 f"{slo_headroom_ms:.0f} ms of p95 SLO headroom available",
                 "energy figure is a projection from the current window, not a measurement"),
            ))
        return recs

    # --- builders ---

    def _dedupe(self, f) -> Recommendation:
        return Recommendation(
            _rid(), "Cache responses for repeated identical prompts", "enable_response_cache",
            "Identical prompts are being re-executed. A response cache removes the compute entirely.",
            Impact(-f.wasted_energy_wh, 0.0, "none", "requires review"),
            Confidence.HIGH, f.evidence + (
                f"{f.affected_requests} duplicate requests, {f.wasted_tokens:,} tokens",
                "security review: a shared cache must be keyed per tenant to avoid cross-tenant leakage"),
        )

    def _prefix_cache(self, f) -> Recommendation:
        return Recommendation(
            _rid(), "Enable prefix / KV caching for the shared system context", "enable_prefix_cache",
            "The same context prefix is re-processed on every request. Prefix caching skips prefill "
            "for the shared portion.",
            Impact(-f.wasted_energy_wh, -5.0, "none", "requires review"),
            Confidence.MEDIUM, f.evidence + (
                "prefix cache entries must be scoped per tenant",),
        )

    def _trim_context(self, f) -> Recommendation:
        return Recommendation(
            _rid(), "Trim context for outlier requests in this task class", "trim_context",
            "A minority of requests carry several times the context of their peers for the same task. "
            "Retrieval tuning or context compaction reduces prefill work.",
            Impact(-f.wasted_energy_wh, -10.0, "possible regression", "none"),
            Confidence.LOW, f.evidence + (
                "validate answer quality on a held-out set before rolling out",),
        )

    def _cap_output(self, f) -> Recommendation:
        return Recommendation(
            _rid(), "Set a per-task output token cap", "cap_output_tokens",
            "Decoding dominates per-request energy. Capping output length for tasks whose answers are "
            "normally short removes the tail.",
            Impact(-f.wasted_energy_wh, -80.0, "possible regression", "none"),
            Confidence.MEDIUM, f.evidence,
        )

    def _agent_budget(self, f) -> Recommendation:
        return Recommendation(
            _rid(), "Add a step budget and loop detector to agent sessions", "agent_step_budget",
            "Agent sessions are repeating identical steps without converging. A step budget bounds the "
            "energy an unproductive session can spend.",
            Impact(-f.wasted_energy_wh, 0.0, "possible regression", "none"),
            Confidence.MEDIUM, f.evidence,
        )

    def _smaller_window(self, f) -> Recommendation:
        return Recommendation(
            _rid(), "Deploy a shorter-context variant for this workload", "reduce_context_window",
            "Most requests use a small fraction of the allocated window. A shorter window frees KV cache "
            "memory, which raises the achievable batch size.",
            Impact(0.0, 0.0, "none", "none"),
            Confidence.LOW, f.evidence + (
                "no energy projection: this change acts indirectly through batch size, "
                "measure it in the simulator first",),
        )

    def _fix_errors(self, f) -> Recommendation:
        return Recommendation(
            _rid(), "Reduce the request failure rate before tuning anything else", "reduce_error_rate",
            "Failed requests consume full energy and produce nothing. This is the cheapest energy "
            "available and it improves the SLO at the same time.",
            Impact(-f.wasted_energy_wh, 0.0, "improved", "none"),
            Confidence.HIGH, f.evidence, requires_approval=False,
        )


def _rid() -> str:
    return f"rec-{uuid.uuid4().hex[:10]}"
