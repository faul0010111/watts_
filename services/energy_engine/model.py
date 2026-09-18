"""
WATTS energy model.

Total Energy = GPU + CPU + Memory + Network + Storage + Cooling overhead.

Every number carries a ``Provenance``. WATTS never mixes a measured value with an
estimated one without saying so: an ``EnergyBreakdown`` keeps the provenance of each
component and reports the weakest provenance of the aggregate. Consumers that need
hard evidence can filter on ``measured_fraction``.

Units: power in watts (W), energy in watt-hours (Wh), time in seconds (s).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Iterable, Sequence


class Provenance(str, Enum):
    """Where a number came from. Ordered from strongest to weakest evidence."""

    MEASURED = "measured"      # read from a hardware counter (NVML/DCGM, RAPL, PDU, BMS)
    DERIVED = "derived"        # arithmetic over measured values only
    ESTIMATED = "estimated"    # model with declared coefficients, no direct measurement
    SIMULATED = "simulated"    # produced by the WATTS digital twin, not a real system

    @property
    def rank(self) -> int:
        return {"measured": 0, "derived": 1, "estimated": 2, "simulated": 3}[self.value]

    @staticmethod
    def weakest(values: Iterable["Provenance"]) -> "Provenance":
        vals = list(values)
        if not vals:
            return Provenance.ESTIMATED
        return max(vals, key=lambda p: p.rank)


@dataclass(frozen=True)
class PowerSample:
    """A single power reading at a point in time."""

    t_s: float
    watts: float
    provenance: Provenance = Provenance.MEASURED
    source: str = "unknown"

    def __post_init__(self) -> None:
        if self.watts < 0:
            raise ValueError("power cannot be negative")


def integrate_power(samples: Sequence[PowerSample]) -> tuple[float, Provenance]:
    """Trapezoidal integration of a power series into Wh.

    Returns ``(wh, provenance)``. Provenance is DERIVED when every sample was
    measured, otherwise the weakest provenance in the series.
    """
    ordered = sorted(samples, key=lambda s: s.t_s)
    if len(ordered) < 2:
        return 0.0, (ordered[0].provenance if ordered else Provenance.ESTIMATED)
    joules = 0.0
    for a, b in zip(ordered, ordered[1:]):
        dt = b.t_s - a.t_s
        if dt <= 0:
            continue
        joules += 0.5 * (a.watts + b.watts) * dt
    prov = Provenance.weakest(s.provenance for s in ordered)
    if prov is Provenance.MEASURED:
        prov = Provenance.DERIVED
    return joules / 3600.0, prov


@dataclass(frozen=True)
class ComponentEnergy:
    component: str
    wh: float
    provenance: Provenance
    method: str = ""   # how this number was produced, in one line

    def as_dict(self) -> dict:
        d = asdict(self)
        d["provenance"] = self.provenance.value
        return d


@dataclass(frozen=True)
class EnergyBreakdown:
    """IT energy per component plus the cooling/facility overhead."""

    components: tuple[ComponentEnergy, ...]
    cooling_wh: float
    cooling_provenance: Provenance
    pue: float
    window_s: float = 0.0

    @property
    def it_energy_wh(self) -> float:
        return sum(c.wh for c in self.components)

    @property
    def facility_energy_wh(self) -> float:
        return self.it_energy_wh + self.cooling_wh

    @property
    def provenance(self) -> Provenance:
        return Provenance.weakest([c.provenance for c in self.components] + [self.cooling_provenance])

    @property
    def measured_fraction(self) -> float:
        """Share of IT energy backed by hardware counters (0..1)."""
        it = self.it_energy_wh
        if it <= 0:
            return 0.0
        measured = sum(c.wh for c in self.components
                       if c.provenance in (Provenance.MEASURED, Provenance.DERIVED))
        return measured / it

    def get(self, component: str) -> float:
        for c in self.components:
            if c.component == component:
                return c.wh
        return 0.0

    def as_dict(self) -> dict:
        return {
            "components": [c.as_dict() for c in self.components],
            "cooling_wh": self.cooling_wh,
            "cooling_provenance": self.cooling_provenance.value,
            "it_energy_wh": self.it_energy_wh,
            "facility_energy_wh": self.facility_energy_wh,
            "pue": self.pue,
            "provenance": self.provenance.value,
            "measured_fraction": self.measured_fraction,
            "window_s": self.window_s,
        }


@dataclass(frozen=True)
class EnergyModelConfig:
    """Coefficients used only when a component cannot be measured.

    These are *declared defaults*, not empirical results. Override them with values
    from your own hardware (see docs/ENERGY_MODEL.md, "Calibration") before using the
    output for anything but relative comparison.
    """

    cpu_w_per_core_busy: float = 12.0
    cpu_w_per_core_idle: float = 2.0
    dram_w_per_gb: float = 0.35
    network_j_per_gb: float = 2_000.0
    storage_j_per_gb_read: float = 300.0
    storage_j_per_gb_write: float = 900.0
    default_pue: float = 1.45          # used when facility metering is absent
    coefficient_source: str = "declared-default"


class EnergyModel:
    """Composes component energies into a facility-level breakdown."""

    def __init__(self, config: EnergyModelConfig | None = None) -> None:
        self.config = config or EnergyModelConfig()

    # --- component estimators (used only when measurement is unavailable) ---

    def cpu_energy_wh(self, cores: int, utilization: float, seconds: float) -> ComponentEnergy:
        c = self.config
        util = min(max(utilization, 0.0), 1.0)
        watts = cores * (c.cpu_w_per_core_idle + util * (c.cpu_w_per_core_busy - c.cpu_w_per_core_idle))
        return ComponentEnergy(
            "cpu", watts * seconds / 3600.0, Provenance.ESTIMATED,
            f"linear core model ({c.coefficient_source}); prefer RAPL when available",
        )

    def memory_energy_wh(self, gb: float, seconds: float) -> ComponentEnergy:
        watts = gb * self.config.dram_w_per_gb
        return ComponentEnergy("memory", watts * seconds / 3600.0, Provenance.ESTIMATED,
                               "per-GB DRAM static model")

    def network_energy_wh(self, gb_transferred: float) -> ComponentEnergy:
        j = gb_transferred * self.config.network_j_per_gb
        return ComponentEnergy("network", j / 3600.0, Provenance.ESTIMATED, "per-GB transfer model")

    def storage_energy_wh(self, gb_read: float = 0.0, gb_written: float = 0.0) -> ComponentEnergy:
        c = self.config
        j = gb_read * c.storage_j_per_gb_read + gb_written * c.storage_j_per_gb_write
        return ComponentEnergy("storage", j / 3600.0, Provenance.ESTIMATED, "per-GB I/O model")

    @staticmethod
    def gpu_energy_from_samples(samples: Sequence[PowerSample]) -> ComponentEnergy:
        wh, prov = integrate_power(samples)
        return ComponentEnergy("gpu", wh, prov, "trapezoidal integration of NVML/DCGM power samples")

    # --- composition ---

    def compose(
        self,
        components: Sequence[ComponentEnergy],
        *,
        pue: float | None = None,
        pue_provenance: Provenance = Provenance.ESTIMATED,
        window_s: float = 0.0,
    ) -> EnergyBreakdown:
        """Add facility overhead on top of IT energy.

        ``cooling_wh`` is everything the facility spends that is not IT load
        (cooling, power conversion losses, lighting): ``it * (pue - 1)``.
        """
        effective_pue = self.config.default_pue if pue is None else pue
        if effective_pue < 1.0:
            raise ValueError("PUE cannot be lower than 1.0")
        it = sum(c.wh for c in components)
        return EnergyBreakdown(
            components=tuple(components),
            cooling_wh=it * (effective_pue - 1.0),
            cooling_provenance=pue_provenance,
            pue=effective_pue,
            window_s=window_s,
        )
