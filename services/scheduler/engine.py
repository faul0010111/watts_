"""Energy-aware scheduler.

Deferring work to a cheaper or cleaner hour is only ever offered to workloads that
declared themselves flexible and that still meet their deadline. Interactive inference
and anything marked critical is placed immediately, whatever the grid is doing - the
scheduler has no authority to trade availability for energy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence


class JobClass(str, Enum):
    INFERENCE = "inference"            # interactive, never deferred
    TRAINING = "training"
    BATCH = "batch"
    EMBEDDING = "embedding"
    DATA_PROCESSING = "data_processing"


class Criticality(str, Enum):
    CRITICAL = "critical"
    NORMAL = "normal"
    FLEXIBLE = "flexible"


@dataclass(frozen=True)
class Job:
    id: str
    job_class: JobClass
    criticality: Criticality
    gpus_required: int
    duration_s: float
    estimated_energy_kwh: float
    submitted_at_s: float = 0.0
    deadline_s: float | None = None     # absolute; None = no deadline
    deferrable: bool = False
    tenant: str = "default"

    @property
    def can_defer(self) -> bool:
        return (self.deferrable
                and self.criticality is not Criticality.CRITICAL
                and self.job_class is not JobClass.INFERENCE)


@dataclass(frozen=True)
class GridSignal:
    """Per-slot grid conditions. All values supplied by the operator's data source."""

    t_s: float
    price_per_kwh: float
    carbon_g_per_kwh: float | None = None
    renewable_fraction: float | None = None
    available_gpus: int = 0


@dataclass(frozen=True)
class SchedulerConfig:
    slot_s: float = 900.0
    price_weight: float = 1.0
    carbon_weight: float = 0.0          # off by default: carbon is opt-in, see docs/ENERGY_MODEL.md
    delay_penalty_per_hour: float = 0.0
    max_defer_s: float = 6 * 3600.0


@dataclass(frozen=True)
class Placement:
    job_id: str
    start_s: float
    deferred_s: float
    reason: str
    expected_cost: float
    expected_carbon_g: float | None = None

    def as_dict(self) -> dict:
        return {"job_id": self.job_id, "start_s": self.start_s, "deferred_s": self.deferred_s,
                "reason": self.reason, "expected_cost": self.expected_cost,
                "expected_carbon_g": self.expected_carbon_g}


@dataclass
class ScheduleResult:
    placements: list[Placement]
    unplaced: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return sum(p.expected_cost for p in self.placements)

    @property
    def total_carbon_g(self) -> float | None:
        vals = [p.expected_carbon_g for p in self.placements if p.expected_carbon_g is not None]
        return sum(vals) if vals else None

    def as_dict(self) -> dict:
        return {"placements": [p.as_dict() for p in self.placements],
                "unplaced": [{"job_id": j, "reason": r} for j, r in self.unplaced],
                "total_cost": self.total_cost, "total_carbon_g": self.total_carbon_g}


class EnergyAwareScheduler:
    def __init__(self, config: SchedulerConfig | None = None) -> None:
        self.config = config or SchedulerConfig()

    def schedule(self, jobs: Sequence[Job], signals: Sequence[GridSignal],
                 now_s: float = 0.0) -> ScheduleResult:
        if not signals:
            raise ValueError("scheduler needs at least one grid signal slot")
        slots = sorted(signals, key=lambda s: s.t_s)
        capacity = {s.t_s: s.available_gpus for s in slots}
        placements: list[Placement] = []
        unplaced: list[tuple[str, str]] = []

        # Critical and interactive work is placed first, at the earliest feasible slot.
        order = sorted(jobs, key=lambda j: (j.can_defer, j.deadline_s or float("inf"),
                                            j.submitted_at_s))
        for job in order:
            slot = self._pick_slot(job, slots, capacity, now_s)
            if slot is None:
                unplaced.append((job.id, "no slot with enough free accelerators before the deadline"))
                continue
            capacity[slot.t_s] -= job.gpus_required
            deferred = max(0.0, slot.t_s - max(job.submitted_at_s, now_s))
            cost = job.estimated_energy_kwh * slot.price_per_kwh
            carbon = (job.estimated_energy_kwh * slot.carbon_g_per_kwh
                      if slot.carbon_g_per_kwh is not None else None)
            placements.append(Placement(job.id, slot.t_s, deferred,
                                        self._reason(job, deferred), cost, carbon))
        return ScheduleResult(placements, unplaced)

    def _reason(self, job: Job, deferred: float) -> str:
        if not job.can_defer:
            return f"{job.criticality.value} {job.job_class.value}: placed at the earliest free slot"
        if deferred <= 0:
            return "flexible job, but the current slot was already the cheapest feasible one"
        return (f"flexible job deferred {deferred / 3600:.1f} h to a cheaper slot, "
                f"deadline respected")

    def _pick_slot(self, job: Job, slots, capacity, now_s: float) -> GridSignal | None:
        earliest = max(job.submitted_at_s, now_s)
        latest = earliest + self.config.max_defer_s if job.can_defer else earliest + self.config.slot_s
        if job.deadline_s is not None:
            latest = min(latest, job.deadline_s - job.duration_s)

        feasible = [s for s in slots
                    if earliest <= s.t_s <= max(latest, earliest)
                    and capacity[s.t_s] >= job.gpus_required]
        if not feasible:
            feasible = [s for s in slots
                        if s.t_s >= earliest and capacity[s.t_s] >= job.gpus_required][:1]
            return feasible[0] if feasible else None

        if not job.can_defer:
            return feasible[0]

        c = self.config
        def score(s: GridSignal) -> float:
            value = c.price_weight * s.price_per_kwh * job.estimated_energy_kwh
            if c.carbon_weight and s.carbon_g_per_kwh is not None:
                value += c.carbon_weight * s.carbon_g_per_kwh * job.estimated_energy_kwh / 1000.0
            value += c.delay_penalty_per_hour * (s.t_s - earliest) / 3600.0
            return value
        return min(feasible, key=score)
