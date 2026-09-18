"""Token Efficiency Engine: find work that cost energy and produced nothing.

The engine looks for waste that is visible in metadata alone - no prompt text is ever
needed. Each finding quantifies the tokens and energy involved so a recommendation can
be ranked by impact rather than by how alarming it sounds.
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from statistics import median
from typing import Sequence

from ..telemetry.schema import RequestRecord


class FindingType(str, Enum):
    DUPLICATE_CALL = "duplicate_call"
    REPEATED_CONTEXT = "repeated_context"
    OVERSIZED_CONTEXT = "oversized_context"
    OVERLONG_OUTPUT = "overlong_output"
    REDUNDANT_AGENT_LOOP = "redundant_agent_loop"
    UNDERUSED_CONTEXT_WINDOW = "underused_context_window"
    FAILED_WORK = "failed_work"


# What to do about each kind of waste. Kept beside the detector so a finding can never be
# emitted without a defensible next step attached to it.
RECOMMENDED_ACTION: dict[str, str] = {
    "duplicate_call": "enable a per-tenant response cache keyed on the prompt hash",
    "repeated_context": "enable prefix/KV caching for the shared context",
    "oversized_context": "tune retrieval and compact the context for this task class",
    "overlong_output": "set a per-task output cap",
    "redundant_agent_loop": "add a step budget and a loop detector to the agent",
    "underused_context_window": "deploy a shorter-context variant or raise the batch size",
    "failed_work": "fix the error rate before tuning anything else: failed work is pure waste",
}


@dataclass(frozen=True)
class Finding:
    """One piece of work that cost energy and produced nothing.

    Every field a reviewer needs is on the finding itself: what was seen, how much it cost,
    how sure the detector is, where the numbers came from, and what to do about it.
    """

    type: FindingType
    summary: str
    affected_requests: int
    wasted_tokens: int
    wasted_energy_wh: float
    evidence: tuple[str, ...] = ()
    confidence: float = 0.5
    # Provenance of the *energy* figure: it is only ever as strong as the telemetry the
    # waste was computed from, and the share attributed to waste is itself a model.
    provenance: str = "derived"
    recommendation: str = ""

    @property
    def finding_id(self) -> str:
        """Stable across runs on the same evidence, so findings can be tracked over time."""
        payload = f"{self.type.value}|{self.affected_requests}|{self.wasted_tokens}|{self.summary}"
        return f"WF-{hashlib.sha256(payload.encode()).hexdigest()[:10]}"

    @property
    def recommended_action(self) -> str:
        return self.recommendation or RECOMMENDED_ACTION.get(self.type.value, "")

    def as_dict(self) -> dict:
        return {
            "finding_id": self.finding_id,
            "type": self.type.value,
            "summary": self.summary,
            "affected_requests": self.affected_requests,
            "wasted_tokens": self.wasted_tokens,
            "wasted_energy_wh": self.wasted_energy_wh,
            "evidence": list(self.evidence),
            "confidence": self.confidence,
            "provenance": self.provenance,
            "recommendation": self.recommended_action,
        }


@dataclass
class EfficiencyMetrics:
    """Raw token volume on one side, useful work on the other.

    The distinction is the whole point. A system that doubles its token volume to do the
    same job has got worse, and only the per-task figures show it.
    """

    requests: int = 0
    successful_tasks: int = 0
    total_tokens: int = 0
    energy_wh: float = 0.0
    useful_output_tokens: int = 0
    useful_tasks: int = 0          # distinct sessions that reached a successful outcome
    failed_requests: int = 0
    window_s: float = 0.0
    provenance: str = "derived"

    @property
    def wh_per_useful_task(self) -> float:
        """Energy per task that actually delivered something.

        For an agent, one task spans many requests. Optimising Wh/request while the agent
        needs twice as many requests is a loss recorded as a win; this metric refuses it.
        """
        return self.energy_wh / self.useful_tasks if self.useful_tasks else float("inf")

    @property
    def wh_per_workload_window(self) -> float:
        """Everything the workload spent in the window, useful or not."""
        return self.energy_wh

    @property
    def wh_per_hour(self) -> float:
        return self.energy_wh * 3600.0 / self.window_s if self.window_s else 0.0

    @property
    def wh_per_1k_tokens(self) -> float:
        return self.energy_wh / (self.total_tokens / 1000.0) if self.total_tokens else 0.0

    @property
    def useful_work_fraction(self) -> float:
        """Share of requests that succeeded. The cheapest optimisation lives here."""
        return self.successful_tasks / self.requests if self.requests else 0.0

    @property
    def tokens_per_request(self) -> float:
        return self.total_tokens / self.requests if self.requests else 0.0

    @property
    def tokens_per_successful_task(self) -> float:
        return self.total_tokens / self.successful_tasks if self.successful_tasks else 0.0

    @property
    def wh_per_token(self) -> float:
        return self.energy_wh / self.total_tokens if self.total_tokens else 0.0

    @property
    def wh_per_successful_task(self) -> float:
        return self.energy_wh / self.successful_tasks if self.successful_tasks else 0.0

    @property
    def wh_per_useful_output_token(self) -> float:
        return self.energy_wh / self.useful_output_tokens if self.useful_output_tokens else 0.0

    def as_dict(self) -> dict:
        return {
            "requests": self.requests,
            "successful_tasks": self.successful_tasks,
            "total_tokens": self.total_tokens,
            "energy_wh": self.energy_wh,
            "tokens_per_request": self.tokens_per_request,
            "tokens_per_successful_task": self.tokens_per_successful_task,
            "wh_per_token": self.wh_per_token,
            "wh_per_successful_task": self.wh_per_successful_task,
            "wh_per_useful_output_token": self.wh_per_useful_output_token,
            "wh_per_useful_task": self.wh_per_useful_task,
            "wh_per_workload_window": self.wh_per_workload_window,
            "wh_per_hour": self.wh_per_hour,
            "wh_per_1k_tokens": self.wh_per_1k_tokens,
            "useful_tasks": self.useful_tasks,
            "failed_requests": self.failed_requests,
            "useful_work_fraction": self.useful_work_fraction,
            "window_s": self.window_s,
            "provenance": self.provenance,
        }


@dataclass
class EfficiencyReport:
    metrics: EfficiencyMetrics
    findings: list[Finding] = field(default_factory=list)

    @property
    def wasted_energy_wh(self) -> float:
        """Upper bound: findings can overlap, so this is not additive with certainty."""
        return sum(f.wasted_energy_wh for f in self.findings)

    def as_dict(self) -> dict:
        return {
            "metrics": self.metrics.as_dict(),
            "findings": [f.as_dict() for f in sorted(
                self.findings, key=lambda f: -f.wasted_energy_wh)],
            "wasted_energy_wh_upper_bound": self.wasted_energy_wh,
            "note": "findings may overlap; do not sum them as independent savings",
        }


class TokenEfficiencyEngine:
    def __init__(
        self,
        duplicate_window_s: float = 900.0,
        context_utilization_floor: float = 0.10,
        oversized_context_factor: float = 2.5,
        output_overrun_factor: float = 2.0,
    ) -> None:
        self.duplicate_window_s = duplicate_window_s
        self.context_utilization_floor = context_utilization_floor
        self.oversized_context_factor = oversized_context_factor
        self.output_overrun_factor = output_overrun_factor

    def analyze(self, records: Sequence[RequestRecord]) -> EfficiencyReport:
        timestamps = [r.timestamp for r in records]
        # A "task" is a session, not a request: an agent that needs forty calls to answer
        # one question has completed one task, and its energy belongs to that task.
        successful_sessions = {r.session_id_hash for r in records
                               if r.success and r.session_id_hash}
        sessionless_successes = sum(1 for r in records if r.success and not r.session_id_hash)
        provenances = {r.energy_provenance for r in records if r.energy_provenance}
        metrics = EfficiencyMetrics(
            requests=len(records),
            successful_tasks=sum(1 for r in records if r.success),
            total_tokens=sum(r.total_tokens for r in records),
            energy_wh=sum(r.energy_wh or 0.0 for r in records),
            useful_output_tokens=sum(r.output_tokens for r in records if r.success),
            useful_tasks=len(successful_sessions) + sessionless_successes,
            failed_requests=sum(1 for r in records if not r.success),
            window_s=(max(timestamps) - min(timestamps)) if len(timestamps) > 1 else 0.0,
            provenance=(sorted(provenances)[-1] if provenances else "derived"),
        )
        findings: list[Finding] = []
        for check in (self._duplicates, self._repeated_context, self._oversized_context,
                      self._overlong_output, self._agent_loops, self._underused_window,
                      self._failed_work):
            findings.extend(check(records))
        return EfficiencyReport(metrics, findings)

    # --- individual checks -------------------------------------------------

    @staticmethod
    def _energy(rs) -> float:
        return sum(r.energy_wh or 0.0 for r in rs)

    def _duplicates(self, records) -> list[Finding]:
        by_key: dict[tuple, list[RequestRecord]] = defaultdict(list)
        for r in records:
            if r.prompt_hash:
                by_key[(r.model, r.tenant, r.prompt_hash)].append(r)
        dup_records: list[RequestRecord] = []
        examples: list[str] = []
        for (model, tenant, h), group in by_key.items():
            group.sort(key=lambda r: r.timestamp)
            repeats = [b for a, b in zip(group, group[1:])
                       if b.timestamp - a.timestamp <= self.duplicate_window_s and not b.cache_hit]
            if repeats:
                dup_records.extend(repeats)
                if len(examples) < 3:
                    examples.append(f"prompt {h} sent {len(group)}x to {model} for tenant {tenant}")
        if not dup_records:
            return []
        return [Finding(
            FindingType.DUPLICATE_CALL,
            "Identical prompts re-sent within the dedup window without a cache hit",
            len(dup_records),
            sum(r.total_tokens for r in dup_records),
            self._energy(dup_records),
            tuple(examples + ["a response cache keyed on the prompt hash removes this work entirely"]),
            0.9,
        )]

    def _repeated_context(self, records) -> list[Finding]:
        counts: Counter = Counter()
        tokens: Counter = Counter()
        energy: dict[str, float] = defaultdict(float)
        for r in records:
            if r.context_prefix_hash and not r.cache_hit:
                counts[r.context_prefix_hash] += 1
                tokens[r.context_prefix_hash] += r.input_tokens
                energy[r.context_prefix_hash] += (r.energy_wh or 0.0)
        repeated = {h: c for h, c in counts.items() if c >= 3}
        if not repeated:
            return []
        affected = sum(repeated.values())
        # Only the re-processing of the shared prefix is avoidable, not the whole request.
        avoidable_tokens = sum(tokens[h] * (c - 1) / c for h, c in repeated.items())
        avoidable_energy = sum(energy[h] * (c - 1) / c * 0.5 for h, c in repeated.items())
        top = sorted(repeated.items(), key=lambda kv: -kv[1])[:3]
        return [Finding(
            FindingType.REPEATED_CONTEXT,
            "The same context prefix is re-processed across requests; prefix caching is not in use",
            affected,
            int(avoidable_tokens),
            avoidable_energy,
            tuple([f"prefix {h} re-sent {c}x" for h, c in top] +
                  ["energy estimate assumes prefix caching removes prefill only, not decode"]),
            0.6,
        )]

    def _oversized_context(self, records) -> list[Finding]:
        by_class: dict[str, list[RequestRecord]] = defaultdict(list)
        for r in records:
            by_class[r.task_class].append(r)
        flagged: list[RequestRecord] = []
        evidence: list[str] = []
        for task, group in by_class.items():
            if len(group) < 20:
                continue
            base = median([r.input_tokens for r in group]) or 0
            if base <= 0:
                continue
            threshold = base * self.oversized_context_factor
            outliers = [r for r in group if r.input_tokens > threshold]
            if outliers:
                flagged.extend(outliers)
                evidence.append(
                    f"task '{task}': median input {base:.0f} tokens, "
                    f"{len(outliers)} requests above {threshold:.0f}")
        if not flagged:
            return []
        excess_tokens = sum(
            r.input_tokens - median([x.input_tokens for x in by_class[r.task_class]])
            for r in flagged)
        share = sum(
            (r.energy_wh or 0.0) * (1 - median([x.input_tokens for x in by_class[r.task_class]]) / r.input_tokens)
            for r in flagged if r.input_tokens)
        return [Finding(
            FindingType.OVERSIZED_CONTEXT,
            "Some requests carry far more context than their peers doing the same task",
            len(flagged), int(excess_tokens), share * 0.5,
            tuple(evidence + ["compare against the per-task median before trimming; outliers may be legitimate"]),
            0.5,
        )]

    def _overlong_output(self, records) -> list[Finding]:
        by_class: dict[str, list[RequestRecord]] = defaultdict(list)
        for r in records:
            by_class[r.task_class].append(r)
        flagged, evidence = [], []
        for task, group in by_class.items():
            if len(group) < 20:
                continue
            base = median([r.output_tokens for r in group]) or 0
            if base <= 0:
                continue
            threshold = base * self.output_overrun_factor
            outliers = [r for r in group if r.output_tokens > threshold]
            if outliers:
                flagged.extend(outliers)
                evidence.append(f"task '{task}': median output {base:.0f}, "
                                f"{len(outliers)} requests above {threshold:.0f}")
        if not flagged:
            return []
        return [Finding(
            FindingType.OVERLONG_OUTPUT,
            "Outputs run well past the typical length for the task; decode dominates energy",
            len(flagged),
            sum(r.output_tokens for r in flagged),
            self._energy(flagged) * 0.4,
            tuple(evidence + ["set max_tokens per task class and measure the quality impact first"]),
            0.55,
        )]

    def _agent_loops(self, records) -> list[Finding]:
        sessions: dict[str, list[RequestRecord]] = defaultdict(list)
        for r in records:
            if r.session_id_hash:
                sessions[r.session_id_hash].append(r)
        looping = []
        for sid, group in sessions.items():
            if len(group) < 4:
                continue
            steps = Counter(r.prompt_hash for r in group if r.prompt_hash)
            repeats = sum(c - 1 for c in steps.values() if c > 1)
            if repeats >= 2 and not any(r.success for r in group[-2:]):
                looping.extend(group)
        if not looping:
            return []
        return [Finding(
            FindingType.REDUNDANT_AGENT_LOOP,
            "Agent sessions repeat the same step without converging on a successful result",
            len(looping),
            sum(r.total_tokens for r in looping),
            self._energy(looping),
            ("repeated identical steps inside one session with no success at the end",
             "add a step budget and a loop detector to the agent runtime"),
            0.7,
        )]

    def _underused_window(self, records) -> list[Finding]:
        flagged = [r for r in records
                   if r.context_window and r.context_utilization < self.context_utilization_floor]
        if len(flagged) < max(10, 0.2 * len(records)):
            return []
        return [Finding(
            FindingType.UNDERUSED_CONTEXT_WINDOW,
            "Most requests use a fraction of the context window they were allocated",
            len(flagged), 0, 0.0,
            (f"{len(flagged)} of {len(records)} requests below "
             f"{self.context_utilization_floor:.0%} window use",
             "a smaller-window deployment of the same model frees KV cache memory and raises batch size"),
            0.4,
        )]

    def _failed_work(self, records) -> list[Finding]:
        failed = [r for r in records if not r.success]
        if not failed:
            return []
        return [Finding(
            FindingType.FAILED_WORK,
            "Energy spent on requests that did not produce a usable result",
            len(failed),
            sum(r.total_tokens for r in failed),
            self._energy(failed),
            (f"{len(failed)} of {len(records)} requests failed",
             "failure energy is pure waste: fix the error rate before tuning the batch size"),
            0.95,
        )]
