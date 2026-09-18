"""Calibration records and the status they confer.

The rule this module exists to enforce:

    A model is calibrated if, and only if, a registered session on real hardware backs it.

Everything else - a plausible coefficient, a vendor datasheet, a twin that agrees with
itself - is uncalibrated, and every report that quotes it must say so.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..energy_engine.model import Provenance
from ..token_engine.profiles import ModelProfile
from .fitting import Coefficients, ValidationResult
from .session import MeasurementSession


@dataclass(frozen=True)
class CalibrationRecord:
    """The evidence that a set of coefficients describes real hardware."""

    calibration_id: str
    model: str
    hardware: str
    runtime: str
    session_id: str
    coefficients: Coefficients
    validation: ValidationResult
    provenance: Provenance
    created_at: float
    coverage: dict = field(default_factory=dict)
    notes: str = ""

    @property
    def is_hardware_calibration(self) -> bool:
        """False for a twin-against-itself fit, which proves consistency and nothing else."""
        return self.provenance in (Provenance.MEASURED, Provenance.DERIVED)

    @property
    def status(self) -> str:
        if not self.is_hardware_calibration:
            return "self-consistency-check"
        return "calibrated" if self.validation.acceptable else "calibration-failed"

    def covers(self, input_tokens: int, output_tokens: int) -> bool:
        """Is this request shape inside the span the session actually measured?"""
        cov = self.coverage
        if not cov or cov.get("samples", 0) == 0:
            return False
        return (cov["input_tokens"]["min"] <= input_tokens <= cov["input_tokens"]["max"]
                and cov["output_tokens"]["min"] <= output_tokens <= cov["output_tokens"]["max"])

    def as_dict(self) -> dict:
        return {
            "calibration_id": self.calibration_id,
            "model": self.model,
            "hardware": self.hardware,
            "runtime": self.runtime,
            "session_id": self.session_id,
            "status": self.status,
            "provenance": self.provenance.value,
            "is_hardware_calibration": self.is_hardware_calibration,
            "created_at": self.created_at,
            "coefficients": self.coefficients.as_dict(),
            "validation": self.validation.as_dict(),
            "coverage": self.coverage,
            "notes": self.notes,
        }


def build_record(session: MeasurementSession, coefficients: Coefficients,
                 validation: ValidationResult, *, notes: str = "") -> CalibrationRecord:
    return CalibrationRecord(
        calibration_id=f"{session.session_id}:{session.configuration_hash()}",
        model=session.model,
        hardware=session.hardware,
        runtime=session.runtime,
        session_id=session.session_id,
        coefficients=coefficients,
        validation=validation,
        provenance=session.provenance,
        created_at=time.time(),
        coverage=session.coverage,
        notes=notes,
    )


class CalibrationRegistry:
    """Which models are calibrated, on what, and how well."""

    def __init__(self, records: list[CalibrationRecord] | None = None) -> None:
        self._records: dict[str, CalibrationRecord] = {}
        for record in records or []:
            self.register(record)

    def register(self, record: CalibrationRecord) -> CalibrationRecord:
        if record.status == "calibration-failed":
            raise ValueError(
                f"refusing to register {record.calibration_id}: validation did not pass "
                f"(mean relative error {record.validation.mean_relative_error:.1%}, "
                f"bias {record.validation.bias:+.5f} Wh). Fix the fit or widen the session."
            )
        self._records[record.model] = record
        return record

    def get(self, model: str) -> CalibrationRecord | None:
        return self._records.get(model)

    def status(self, model: str) -> str:
        record = self._records.get(model)
        if record is None:
            return "uncalibrated"
        return record.status

    def calibration_id(self, model: str) -> str | None:
        record = self._records.get(model)
        return record.calibration_id if record and record.is_hardware_calibration else None

    def models(self) -> list[str]:
        return sorted(self._records)

    def summary(self, known_models: list[str] | None = None) -> dict:
        """The block every report must carry: who is calibrated and who is not."""
        models = sorted(set(known_models or []) | set(self._records))
        rows = {m: self.status(m) for m in models}
        calibrated = [m for m, s in rows.items() if s == "calibrated"]
        return {
            "models": rows,
            "calibrated": calibrated,
            "uncalibrated": [m for m, s in rows.items() if s != "calibrated"],
            "calibrated_fraction": len(calibrated) / len(rows) if rows else 0.0,
            "note": ("absolute energy figures for uncalibrated models are model artefacts; "
                     "relative comparisons under one configuration remain valid"),
        }

    def apply_to_profile(self, profile: ModelProfile) -> ModelProfile:
        """Return a profile whose weights come from the calibration, if one exists.

        The attribution split depends only on the ratio between decode and prefill cost,
        so the fitted absolute coefficients are carried alongside for anyone who needs
        watt-hours rather than shares.
        """
        record = self._records.get(profile.name)
        if record is None or not record.is_hardware_calibration:
            return profile
        c = record.coefficients
        scale = 1.0 / c.prefill_wh_per_token if c.prefill_wh_per_token > 0 else 1.0
        from dataclasses import replace
        return replace(
            profile,
            prefill_weight=c.prefill_wh_per_token * scale,
            decode_weight=c.decode_wh_per_token * scale,
            source="calibrated",
            calibrated_on=f"{record.hardware}, {record.runtime}, "
                          f"{time.strftime('%Y-%m-%d', time.gmtime(record.created_at))}",
            wh_per_input_token=c.prefill_wh_per_token,
            wh_per_output_token=c.decode_wh_per_token,
            baseline_wh=c.baseline_wh,
            calibration_id=record.calibration_id,
        )

    # --- persistence -------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps({"records": [r.as_dict() for r in self._records.values()]},
                          indent=2, sort_keys=True)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json())
        return p
