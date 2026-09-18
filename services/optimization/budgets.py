"""Energy budgets and energy SLOs.

WATTS treats energy as an operational variable alongside latency, availability and error
rate. The rule that binds them: an energy optimisation that breaks a reliability SLO is
a regression, not a saving. ``SLOReport.violations`` is what gates every optimisation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from ..telemetry.schema import RequestRecord


@dataclass(frozen=True)
class Budget:
    workload: str
    energy_wh_per_hour: float | None = None
    tokens_per_hour: float | None = None
    cost_per_hour: float | None = None
    wh_per_successful_request: float | None = None


@dataclass
class BudgetState:
    workload: str
    window_s: float
    energy_wh: float = 0.0
    tokens: int = 0
    cost: float = 0.0
    successful_requests: int = 0

    def projected_hourly(self, value: float) -> float:
        if self.window_s <= 0:
            return 0.0
        return value * 3600.0 / self.window_s


@dataclass(frozen=True)
class BudgetBreach:
    dimension: str
    limit: float
    projected: float
    utilization: float

    def as_dict(self) -> dict:
        return {"dimension": self.dimension, "limit": self.limit,
                "projected": self.projected, "utilization": self.utilization}


class BudgetTracker:
    def __init__(self, budgets: dict[str, Budget]) -> None:
        self.budgets = budgets

    def evaluate(self, state: BudgetState) -> dict:
        budget = self.budgets.get(state.workload)
        if budget is None:
            return {"workload": state.workload, "tracked": False, "breaches": []}

        breaches: list[BudgetBreach] = []
        checks = [
            ("energy_wh_per_hour", budget.energy_wh_per_hour, state.projected_hourly(state.energy_wh)),
            ("tokens_per_hour", budget.tokens_per_hour, state.projected_hourly(state.tokens)),
            ("cost_per_hour", budget.cost_per_hour, state.projected_hourly(state.cost)),
        ]
        for dim, limit, projected in checks:
            if limit:
                util = projected / limit
                if util > 1.0:
                    breaches.append(BudgetBreach(dim, limit, projected, util))

        if budget.wh_per_successful_request and state.successful_requests:
            per_req = state.energy_wh / state.successful_requests
            if per_req > budget.wh_per_successful_request:
                breaches.append(BudgetBreach("wh_per_successful_request",
                                             budget.wh_per_successful_request, per_req,
                                             per_req / budget.wh_per_successful_request))

        return {
            "workload": state.workload,
            "tracked": True,
            "breaches": [b.as_dict() for b in breaches],
            "action": "detect -> explain -> recommend -> require approval" if breaches else "within budget",
        }


@dataclass(frozen=True)
class EnergySLO:
    """Reliability and energy objectives evaluated together."""

    name: str
    p95_latency_ms: float
    availability: float                  # e.g. 0.999
    max_error_rate: float                # e.g. 0.01
    max_wh_per_successful_request: float


@dataclass
class SLOReport:
    slo: EnergySLO
    p95_latency_ms: float
    availability: float
    error_rate: float
    wh_per_successful_request: float
    samples: int

    @property
    def violations(self) -> list[str]:
        v = []
        if self.p95_latency_ms > self.slo.p95_latency_ms:
            v.append(f"p95 latency {self.p95_latency_ms:.0f} ms > {self.slo.p95_latency_ms:.0f} ms")
        if self.availability < self.slo.availability:
            v.append(f"availability {self.availability:.4f} < {self.slo.availability:.4f}")
        if self.error_rate > self.slo.max_error_rate:
            v.append(f"error rate {self.error_rate:.3%} > {self.slo.max_error_rate:.3%}")
        if self.wh_per_successful_request > self.slo.max_wh_per_successful_request:
            v.append(f"energy {self.wh_per_successful_request:.4f} Wh/req > "
                     f"{self.slo.max_wh_per_successful_request:.4f} Wh/req")
        return v

    @property
    def reliability_violations(self) -> list[str]:
        """Violations that an optimisation is never allowed to cause."""
        return [v for v in self.violations if not v.startswith("energy ")]

    @property
    def latency_headroom_ms(self) -> float:
        return self.slo.p95_latency_ms - self.p95_latency_ms

    def as_dict(self) -> dict:
        return {
            "slo": self.slo.name,
            "p95_latency_ms": self.p95_latency_ms,
            "availability": self.availability,
            "error_rate": self.error_rate,
            "wh_per_successful_request": self.wh_per_successful_request,
            "latency_headroom_ms": self.latency_headroom_ms,
            "violations": self.violations,
            "reliability_violations": self.reliability_violations,
            "met": not self.violations,
            "samples": self.samples,
        }


def evaluate_slo(slo: EnergySLO, records: Sequence[RequestRecord]) -> SLOReport:
    if not records:
        return SLOReport(slo, 0.0, 1.0, 0.0, 0.0, 0)
    latencies = sorted(r.latency_ms for r in records)
    idx = max(0, min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1)))))
    ok = [r for r in records if r.success]
    energy = sum(r.energy_wh or 0.0 for r in records)
    return SLOReport(
        slo=slo,
        p95_latency_ms=latencies[idx],
        availability=len(ok) / len(records),
        error_rate=1 - len(ok) / len(records),
        wh_per_successful_request=energy / len(ok) if ok else float("inf"),
        samples=len(records),
    )
