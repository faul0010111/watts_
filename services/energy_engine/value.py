"""Energy provenance 2.0.

A watt-hour on its own is not a fact. It becomes one when it carries where it came from,
when it was produced, how sure the producer is, and - if a model was involved - which
calibration session backs that model.

``EnergyValue`` is that envelope. It is deliberately awkward to strip: arithmetic on
energy values returns energy values, and the result keeps the *weakest* provenance of its
inputs. Adding a metered reading to a modelled one cannot produce a measurement.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Iterable, Sequence

from .model import Provenance


class ProvenanceError(RuntimeError):
    """Raised when a caller asks a value to be stronger evidence than it is."""


@dataclass(frozen=True)
class EnergyValue:
    """One energy figure and everything needed to judge it."""

    value: float
    unit: str = "Wh"
    source: str = "unknown"                    # what produced it: "nvml", "twin", "cpu-linear-model"
    provenance: Provenance = Provenance.ESTIMATED
    timestamp: float = 0.0                     # epoch seconds of the observation window
    confidence: float | None = None            # 0..1, only when the producer can state one
    calibration_id: str | None = None          # the session backing the coefficients, if any

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        if self.provenance is Provenance.ESTIMATED and self.calibration_id == "":
            raise ValueError("calibration_id must be None or a real id, never an empty string")

    # --- evidence ---------------------------------------------------------

    @property
    def is_measurement(self) -> bool:
        """True only for values a meter produced, or pure arithmetic over such values."""
        return self.provenance in (Provenance.MEASURED, Provenance.DERIVED)

    @property
    def calibrated(self) -> bool:
        return self.calibration_id is not None

    def require(self, minimum: Provenance) -> "EnergyValue":
        """Assert this value is at least as strong as ``minimum``, or refuse to proceed."""
        if self.provenance.rank > minimum.rank:
            raise ProvenanceError(
                f"{self.source} produced a {self.provenance.value} value; this caller "
                f"requires {minimum.value} or stronger. WATTS does not relabel evidence."
            )
        return self

    # --- arithmetic that preserves evidence -------------------------------

    def __add__(self, other: "EnergyValue") -> "EnergyValue":
        if not isinstance(other, EnergyValue):
            return NotImplemented
        if self.unit != other.unit:
            raise ValueError(f"cannot add {self.unit} to {other.unit}")
        return EnergyValue(
            value=self.value + other.value,
            unit=self.unit,
            source=f"sum({self.source}, {other.source})",
            provenance=Provenance.weakest((self.provenance, other.provenance)),
            timestamp=max(self.timestamp, other.timestamp),
            confidence=_min_optional(self.confidence, other.confidence),
            calibration_id=self.calibration_id if self.calibration_id == other.calibration_id else None,
        )

    def scaled(self, factor: float, *, source: str | None = None) -> "EnergyValue":
        """Multiply by a unitless factor. A scaled measurement is derived, never measured."""
        provenance = (Provenance.DERIVED if self.provenance is Provenance.MEASURED
                      else self.provenance)
        return replace(self, value=self.value * factor, provenance=provenance,
                       source=source or f"{self.source}×{factor:g}")

    def converted(self, unit: str, factor: float) -> "EnergyValue":
        return replace(self, value=self.value * factor, unit=unit,
                       provenance=(Provenance.DERIVED if self.provenance is Provenance.MEASURED
                                   else self.provenance))

    @classmethod
    def total(cls, values: Sequence["EnergyValue"], *, source: str = "total") -> "EnergyValue":
        if not values:
            return cls(0.0, source=source, provenance=Provenance.DERIVED)
        unit = values[0].unit
        if any(v.unit != unit for v in values):
            raise ValueError("cannot total values with different units")
        return cls(
            value=sum(v.value for v in values),
            unit=unit,
            source=source,
            provenance=Provenance.weakest(v.provenance for v in values),
            timestamp=max(v.timestamp for v in values),
            confidence=_min_optional(*[v.confidence for v in values]),
            calibration_id=_single({v.calibration_id for v in values}),
        )

    # --- serialisation ----------------------------------------------------

    def as_dict(self) -> dict:
        return {
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            "provenance": self.provenance.value,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "calibration_id": self.calibration_id,
        }

    def label(self, digits: int = 4) -> str:
        """Human-readable form that always shows the provenance beside the number."""
        return f"{self.value:.{digits}f} {self.unit} [{self.provenance.value}]"


def measured(value: float, source: str, *, unit: str = "Wh", timestamp: float | None = None,
             confidence: float | None = None) -> EnergyValue:
    return EnergyValue(value, unit, source, Provenance.MEASURED,
                       time.time() if timestamp is None else timestamp, confidence)


def derived(value: float, source: str, *, unit: str = "Wh", timestamp: float = 0.0,
            confidence: float | None = None, calibration_id: str | None = None) -> EnergyValue:
    return EnergyValue(value, unit, source, Provenance.DERIVED, timestamp, confidence, calibration_id)


def estimated(value: float, source: str, *, unit: str = "Wh", timestamp: float = 0.0,
              confidence: float | None = None, calibration_id: str | None = None) -> EnergyValue:
    return EnergyValue(value, unit, source, Provenance.ESTIMATED, timestamp, confidence, calibration_id)


def simulated(value: float, source: str = "watts-digital-twin", *, unit: str = "Wh",
              timestamp: float = 0.0, confidence: float | None = None) -> EnergyValue:
    return EnergyValue(value, unit, source, Provenance.SIMULATED, timestamp, confidence)


def provenance_summary(values: Iterable[EnergyValue]) -> dict:
    """What a report must print beside its headline numbers.

    ``measured_fraction`` is by energy, not by count: ten estimated microwatt-hours next to
    one metered kilowatt-hour is a measured picture, and ten metered microwatt-hours next
    to one modelled kilowatt-hour is not.
    """
    vals = [v for v in values]
    if not vals:
        return {"values": 0, "weakest": None, "measured_fraction": 0.0,
                "by_provenance": {}, "calibrated_fraction": 0.0, "uncalibrated_sources": []}
    total = sum(abs(v.value) for v in vals) or 1.0
    by: dict[str, dict] = {}
    for v in vals:
        row = by.setdefault(v.provenance.value, {"count": 0, "energy": 0.0})
        row["count"] += 1
        row["energy"] += v.value
    measured_energy = sum(abs(v.value) for v in vals if v.is_measurement)
    calibrated_energy = sum(abs(v.value) for v in vals if v.calibrated)
    return {
        "values": len(vals),
        "weakest": Provenance.weakest(v.provenance for v in vals).value,
        "measured_fraction": measured_energy / total,
        "calibrated_fraction": calibrated_energy / total,
        "by_provenance": by,
        "uncalibrated_sources": sorted({v.source for v in vals
                                        if not v.calibrated and not v.is_measurement}),
    }


def _min_optional(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


def _single(values: set) -> str | None:
    values = {v for v in values if v is not None}
    return values.pop() if len(values) == 1 else None
