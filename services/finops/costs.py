"""Energy FinOps.

Energy cost is not infrastructure cost, and conflating them is how an "energy saving"
becomes a bill that did not move.

On rented accelerators the electricity is inside the hourly rate you already pay; saving a
kilowatt-hour saves money only if it frees an accelerator-hour you then stop renting. On
owned hardware the electricity is a real line item, and the depreciation is sunk. The two
cases give opposite answers to "was that optimisation worth it", so this module keeps every
component separate and always shows the composition:

    total = energy + accelerator + host infrastructure + network + storage + cooling

``cooling`` is reported both inside the energy cost (it is electricity) and as its own line,
because facility teams and platform teams ask about it differently. The breakdown states
which convention it is using rather than leaving the reader to guess.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Sequence

from ..telemetry.schema import RequestRecord


@dataclass(frozen=True)
class CostRates:
    """Operator-supplied prices. WATTS has no opinion about what yours are.

    Every default here is zero or explicitly declared, so an unset rate produces a zero
    line rather than a plausible-looking invented one.
    """

    electricity_per_kwh: float = 0.0
    accelerator_per_hour: float = 0.0        # rented or amortised, per device-hour
    host_per_accelerator_hour: float = 0.0   # CPU, DRAM, chassis share
    network_per_gb: float = 0.0
    storage_per_gb_month: float = 0.0
    carbon_g_per_kwh: float | None = None    # for the carbon line, never for cost
    currency: str = "USD"
    source: str = "operator-supplied"
    accelerator_rate_includes_energy: bool = False   # true for most cloud instance pricing

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CostBreakdown:
    """One window's cost, by component, with the convention that produced it."""

    energy: float
    accelerator: float
    host: float
    network: float
    storage: float
    cooling_energy: float                   # subset of `energy`, shown separately
    currency: str
    double_counted: bool
    note: str

    @property
    def total(self) -> float:
        """Accelerator rates that already include electricity make the energy line a view,
        not an addition. Adding it anyway would bill the same kilowatt-hour twice."""
        base = self.accelerator + self.host + self.network + self.storage
        return base if self.double_counted else base + self.energy

    def as_dict(self) -> dict:
        d = asdict(self)
        d["total"] = self.total
        d["energy_share"] = (self.energy / self.total) if self.total else 0.0
        return d


@dataclass(frozen=True)
class WorkloadCost:
    """Cost attributed to one dimension - a tenant, a model, a workload."""

    key: str
    dimension: str
    requests: int
    successful_requests: int
    tokens: int
    energy_wh: float
    breakdown: CostBreakdown

    @property
    def cost_per_request(self) -> float:
        return self.breakdown.total / self.requests if self.requests else 0.0

    @property
    def cost_per_successful_task(self) -> float:
        return (self.breakdown.total / self.successful_requests
                if self.successful_requests else float("inf"))

    @property
    def cost_per_1k_tokens(self) -> float:
        return self.breakdown.total / (self.tokens / 1000.0) if self.tokens else 0.0

    @property
    def energy_cost_per_1k_tokens(self) -> float:
        return self.breakdown.energy / (self.tokens / 1000.0) if self.tokens else 0.0

    def as_dict(self) -> dict:
        return {
            "key": self.key, "dimension": self.dimension, "requests": self.requests,
            "successful_requests": self.successful_requests, "tokens": self.tokens,
            "energy_wh": self.energy_wh,
            "cost_per_request": self.cost_per_request,
            "cost_per_successful_task": self.cost_per_successful_task,
            "cost_per_1k_tokens": self.cost_per_1k_tokens,
            "energy_cost_per_1k_tokens": self.energy_cost_per_1k_tokens,
            "breakdown": self.breakdown.as_dict(),
        }


@dataclass
class CostModel:
    """Turns a window of telemetry plus facility energy into money, by component."""

    rates: CostRates
    accelerators: int = 1
    network_gb: float = 0.0
    storage_gb: float = 0.0

    def breakdown(self, *, it_energy_wh: float, cooling_energy_wh: float,
                  window_s: float) -> CostBreakdown:
        kwh = (it_energy_wh + cooling_energy_wh) / 1000.0
        device_hours = self.accelerators * window_s / 3600.0
        energy_cost = kwh * self.rates.electricity_per_kwh
        cooling_cost = (cooling_energy_wh / 1000.0) * self.rates.electricity_per_kwh
        note = ("accelerator rate is quoted inclusive of electricity, so the energy line is "
                "shown for visibility and excluded from the total to avoid double counting"
                if self.rates.accelerator_rate_includes_energy else
                "electricity billed separately from the accelerator rate; all lines add")
        return CostBreakdown(
            energy=energy_cost,
            accelerator=device_hours * self.rates.accelerator_per_hour,
            host=device_hours * self.rates.host_per_accelerator_hour,
            network=self.network_gb * self.rates.network_per_gb,
            storage=self.storage_gb * self.rates.storage_per_gb_month * (window_s / 2_592_000.0),
            cooling_energy=cooling_cost,
            currency=self.rates.currency,
            double_counted=self.rates.accelerator_rate_includes_energy,
            note=note,
        )

    def carbon_g(self, facility_energy_wh: float) -> float | None:
        """Carbon is derived from energy, never from cost, and never invented."""
        if self.rates.carbon_g_per_kwh is None:
            return None
        return facility_energy_wh / 1000.0 * self.rates.carbon_g_per_kwh


def allocate_costs(records: Sequence[RequestRecord], *, model: CostModel,
                   it_energy_wh: float, cooling_energy_wh: float, window_s: float,
                   dimension: str = "tenant") -> list[WorkloadCost]:
    """Split the window's cost across tenants, models or task classes.

    Allocation is by attributed energy share, because energy is the only thing WATTS
    measures per request. Allocating by request count would charge a one-token
    classification the same as a 40k-token reasoning call, which is how chargeback loses
    the trust of the teams being charged.
    """
    getter = {
        "tenant": lambda r: r.tenant,
        "model": lambda r: r.model,
        "task_class": lambda r: r.task_class,
        "workload": lambda r: r.security_policy,
    }[dimension]

    total_energy = sum(r.energy_wh or 0.0 for r in records) or 1.0
    groups: dict[str, list[RequestRecord]] = {}
    for r in records:
        groups.setdefault(getter(r), []).append(r)

    out: list[WorkloadCost] = []
    for key, group in groups.items():
        share = sum(r.energy_wh or 0.0 for r in group) / total_energy
        breakdown = model.breakdown(
            it_energy_wh=it_energy_wh * share,
            cooling_energy_wh=cooling_energy_wh * share,
            window_s=window_s)
        scaled = CostBreakdown(
            energy=breakdown.energy,
            accelerator=breakdown.accelerator * share,
            host=breakdown.host * share,
            network=breakdown.network * share,
            storage=breakdown.storage * share,
            cooling_energy=breakdown.cooling_energy,
            currency=breakdown.currency,
            double_counted=breakdown.double_counted,
            note=breakdown.note + f"; allocated by energy share ({share:.1%})",
        )
        out.append(WorkloadCost(
            key=key, dimension=dimension, requests=len(group),
            successful_requests=sum(1 for r in group if r.success),
            tokens=sum(r.total_tokens for r in group),
            energy_wh=sum(r.energy_wh or 0.0 for r in group),
            breakdown=scaled))
    return sorted(out, key=lambda w: -w.breakdown.total)


def cost_report(breakdown: CostBreakdown, allocations: Sequence[WorkloadCost],
                *, tokens: int, successful_requests: int,
                carbon_g: float | None = None) -> dict:
    """The FinOps block of the report: composition first, unit costs second."""
    total = breakdown.total
    return {
        "currency": breakdown.currency,
        "total": total,
        "composition": {
            "energy": breakdown.energy,
            "accelerator": breakdown.accelerator,
            "host_infrastructure": breakdown.host,
            "network": breakdown.network,
            "storage": breakdown.storage,
        },
        "cooling_energy_within_energy": breakdown.cooling_energy,
        "convention": breakdown.note,
        "unit_costs": {
            "cost_per_1k_tokens": total / (tokens / 1000.0) if tokens else 0.0,
            "energy_cost_per_1k_tokens": (breakdown.energy / (tokens / 1000.0)
                                          if tokens else 0.0),
            "cost_per_successful_task": (total / successful_requests
                                         if successful_requests else float("inf")),
        },
        "carbon_g": carbon_g,
        "carbon_note": ("derived from energy × operator-supplied grid intensity; not a cost "
                        "and not a measurement" if carbon_g is not None
                        else "no grid intensity configured, so no carbon figure is reported"),
        "allocations": [a.as_dict() for a in allocations],
    }
