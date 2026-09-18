"""The energy–latency–quality frontier.

Optimisation in WATTS is not a ranking. Ranking configurations by energy answers a question
nobody asked: the cheapest configuration is almost always one that breaks something.

The question that matters is constrained:

    minimise   energy
    subject to p95 latency ≤ SLO
               quality     ≥ floor
               availability ≥ SLO
               security    = compliant

So this module does two things. It partitions candidates into *admissible* and
*inadmissible*, with a reason for every exclusion - an inadmissible candidate is not a
worse option, it is not an option. And among the admissible ones it exposes the Pareto
frontier, because the remaining trade-off between energy, latency and quality belongs to
the operator, not to an optimiser that has quietly picked a weighting.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..energy_engine.model import Provenance
from .quality import QualityFloor, QualityRegistry


@dataclass(frozen=True)
class WorkloadConstraints:
    """What the workload requires, independent of any candidate configuration."""

    name: str
    p95_latency_ms: float
    min_availability: float = 0.99
    max_error_rate: float = 0.02
    quality_floor: QualityFloor | None = None
    energy_budget_wh_per_hour: float | None = None
    security_must_be_compliant: bool = True

    def describe(self) -> list[str]:
        rows = [f"p95 latency ≤ {self.p95_latency_ms:,.0f} ms",
                f"availability ≥ {self.min_availability:.3%}",
                f"error rate ≤ {self.max_error_rate:.2%}"]
        if self.quality_floor:
            rows.append(self.quality_floor.describe())
        if self.energy_budget_wh_per_hour:
            rows.append(f"energy ≤ {self.energy_budget_wh_per_hour:,.0f} Wh/h")
        if self.security_must_be_compliant:
            rows.append("security policy: compliant")
        return rows


@dataclass(frozen=True)
class FrontierPoint:
    """One configuration, measured or simulated, on the three axes that matter."""

    name: str
    energy_wh_per_1k_tokens: float
    p95_latency_ms: float
    availability: float = 1.0
    error_rate: float = 0.0
    energy_wh_per_hour: float | None = None
    security_compliant: bool = True
    security_reason: str = ""
    provenance: Provenance = Provenance.SIMULATED
    evidence: tuple[str, ...] = ()
    quality_value: float | None = None       # filled from the quality registry
    quality_reason: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "energy_wh_per_1k_tokens": self.energy_wh_per_1k_tokens,
            "p95_latency_ms": self.p95_latency_ms,
            "availability": self.availability,
            "error_rate": self.error_rate,
            "energy_wh_per_hour": self.energy_wh_per_hour,
            "quality": self.quality_value,
            "quality_reason": self.quality_reason,
            "security_compliant": self.security_compliant,
            "security_reason": self.security_reason,
            "provenance": self.provenance.value,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class Admissibility:
    point: FrontierPoint
    admissible: bool
    reasons: tuple[str, ...]          # why it was excluded, or the evidence that it passed

    def as_dict(self) -> dict:
        return {**self.point.as_dict(), "admissible": self.admissible,
                "reasons": list(self.reasons)}


class EnergyLatencyQualityFrontier:
    """Admissibility first, Pareto second, recommendation last."""

    def __init__(self, constraints: WorkloadConstraints,
                 quality: QualityRegistry | None = None) -> None:
        self.constraints = constraints
        self.quality = quality or QualityRegistry()
        self._points: list[FrontierPoint] = []

    def add(self, point: FrontierPoint) -> FrontierPoint:
        self._points.append(point)
        return point

    def extend(self, points: list[FrontierPoint]) -> None:
        self._points.extend(points)

    # --- step 1: admissibility --------------------------------------------

    def evaluate(self) -> list[Admissibility]:
        c = self.constraints
        out: list[Admissibility] = []
        for p in self._points:
            reasons: list[str] = []
            passes: list[str] = []

            if p.p95_latency_ms > c.p95_latency_ms:
                reasons.append(f"p95 {p.p95_latency_ms:,.0f} ms exceeds the SLO "
                               f"{c.p95_latency_ms:,.0f} ms")
            else:
                passes.append(f"p95 {p.p95_latency_ms:,.0f} ms within SLO")

            if p.availability < c.min_availability:
                reasons.append(f"availability {p.availability:.3%} below "
                               f"{c.min_availability:.3%}")
            if p.error_rate > c.max_error_rate:
                reasons.append(f"error rate {p.error_rate:.2%} above {c.max_error_rate:.2%}")

            ok_quality, quality_reason = self.quality.check(p.name, c.quality_floor)
            if not ok_quality:
                reasons.append(quality_reason)
            else:
                passes.append(quality_reason)

            if c.security_must_be_compliant and not p.security_compliant:
                reasons.append(p.security_reason or "blocked by security policy")
            elif p.security_compliant:
                passes.append("security policy: compliant")

            if (c.energy_budget_wh_per_hour and p.energy_wh_per_hour
                    and p.energy_wh_per_hour > c.energy_budget_wh_per_hour):
                reasons.append(f"projected {p.energy_wh_per_hour:,.0f} Wh/h exceeds the "
                               f"budget {c.energy_budget_wh_per_hour:,.0f} Wh/h")

            resolved = FrontierPoint(
                **{**p.__dict__,
                   "quality_value": (p.quality_value if p.quality_value is not None
                                     else self._quality_value(p.name)),
                   "quality_reason": quality_reason})
            out.append(Admissibility(resolved, not reasons, tuple(reasons or passes)))
        return out

    def admissible(self) -> list[FrontierPoint]:
        return [a.point for a in self.evaluate() if a.admissible]

    def rejected(self) -> list[Admissibility]:
        return [a for a in self.evaluate() if not a.admissible]

    # --- step 2: the frontier ---------------------------------------------

    def pareto(self) -> list[FrontierPoint]:
        """Non-dominated admissible points: less energy, less latency, more quality.

        A point is dominated when another is at least as good on all three axes and
        strictly better on one. Points whose quality is unknown never reach here, because
        they are not admissible in the first place.
        """
        points = self.admissible()
        front: list[FrontierPoint] = []
        for p in points:
            if not any(_dominates(q, p) for q in points if q is not p):
                front.append(p)
        return sorted(front, key=lambda p: p.energy_wh_per_1k_tokens)

    # --- step 3: the recommendation ---------------------------------------

    def recommend(self) -> tuple[FrontierPoint | None, str]:
        """Lowest-energy admissible point, or an explanation of why there is none."""
        front = self.pareto()
        if not front:
            rejected = self.rejected()
            if not rejected:
                return None, "no candidates were evaluated"
            summary = "; ".join(f"{a.point.name}: {a.reasons[0]}" for a in rejected[:4])
            return None, (f"no candidate satisfies the constraints of "
                          f"'{self.constraints.name}'. {summary}")
        best = front[0]
        others = [p for p in front[1:]]
        note = (f"chosen as the lowest-energy admissible configuration"
                + (f"; {len(others)} other non-dominated options remain, differing in "
                   f"latency or quality" if others else ""))
        return best, note

    def _quality_value(self, configuration: str) -> float | None:
        floor = self.constraints.quality_floor
        if floor is None:
            return None
        return self.quality.value(configuration, floor.metric)

    # --- reporting ---------------------------------------------------------

    def as_dict(self) -> dict:
        evaluation = self.evaluate()
        best, note = self.recommend()
        front_names = {p.name for p in self.pareto()}
        return {
            "workload": self.constraints.name,
            "constraints": self.constraints.describe(),
            "candidates": [a.as_dict() for a in evaluation],
            "admissible": [a.point.name for a in evaluation if a.admissible],
            "inadmissible": {a.point.name: list(a.reasons)
                             for a in evaluation if not a.admissible},
            "pareto_front": sorted(front_names),
            "recommendation": best.name if best else None,
            "recommendation_note": note,
            "method": ("constrained selection: admissibility is a filter, not a penalty "
                       "term; the remaining trade-off is exposed as a Pareto front rather "
                       "than collapsed into a score"),
        }

    def to_markdown(self) -> str:
        rows = ["| Configuration | Wh/1k tok | p95 ms | Quality | Security | Admissible |",
                "|---|---|---|---|---|---|"]
        for a in self.evaluate():
            p = a.point
            quality = f"{p.quality_value:.3f}" if p.quality_value is not None else "unknown"
            rows.append(f"| {p.name} | {p.energy_wh_per_1k_tokens:.4f} | "
                        f"{p.p95_latency_ms:,.0f} | {quality} | "
                        f"{'ok' if p.security_compliant else 'blocked'} | "
                        f"{'yes' if a.admissible else 'no'} |")
        best, note = self.recommend()
        rows += ["", f"**Recommendation:** {best.name if best else 'none'} — {note}"]
        for a in self.rejected():
            rows.append(f"- rejected `{a.point.name}`: {a.reasons[0]}")
        return "\n".join(rows)


def _dominates(a: FrontierPoint, b: FrontierPoint) -> bool:
    qa = a.quality_value if a.quality_value is not None else 0.0
    qb = b.quality_value if b.quality_value is not None else 0.0
    not_worse = (a.energy_wh_per_1k_tokens <= b.energy_wh_per_1k_tokens
                 and a.p95_latency_ms <= b.p95_latency_ms
                 and qa >= qb)
    strictly_better = (a.energy_wh_per_1k_tokens < b.energy_wh_per_1k_tokens
                       or a.p95_latency_ms < b.p95_latency_ms
                       or qa > qb)
    return not_worse and strictly_better
