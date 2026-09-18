"""Measurement sessions.

A calibration claim is only as good as the session behind it. This module records what was
measured, on what hardware, with which runtime, and when - so that a coefficient can never
be described as calibrated without a session anyone can inspect and re-run.

Nothing here invents a measurement. A session is filled either from a hardware adapter or,
explicitly and visibly, from the digital twin - in which case every sample carries
``Provenance.SIMULATED`` and the resulting calibration is marked as a self-consistency
check, not a calibration against reality.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict

from ..energy_engine.model import Provenance


@dataclass(frozen=True)
class CalibrationSample:
    """One request whose energy was measured, with the shape of the work that caused it."""

    input_tokens: int
    output_tokens: int
    measured_wh: float
    batch_size: int = 1
    latency_ms: float = 0.0
    timestamp: float = 0.0
    provenance: Provenance = Provenance.MEASURED
    source: str = "unknown"

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token counts cannot be negative")
        if self.measured_wh < 0:
            raise ValueError("measured energy cannot be negative")
        if self.batch_size < 1:
            raise ValueError("batch size must be at least 1")

    def as_dict(self) -> dict:
        d = asdict(self)
        d["provenance"] = self.provenance.value
        return d


@dataclass
class MeasurementSession:
    """A bounded set of samples collected under one fixed configuration."""

    model: str
    hardware: str                      # "1xA100-80GB SXM, node dgx-03"
    runtime: str                       # "vLLM 0.5.4, fp16, tp=1"
    adapter: str                       # which telemetry adapter produced the energy figures
    session_id: str = field(default_factory=lambda: f"cal-{uuid.uuid4().hex[:12]}")
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    samples: list[CalibrationSample] = field(default_factory=list)
    notes: str = ""

    def add(self, sample: CalibrationSample) -> None:
        if self.ended_at is not None:
            raise RuntimeError("session is closed; open a new one rather than extending history")
        self.samples.append(sample)
        
    def close(self, when: float | None = None) -> "MeasurementSession":
        self.ended_at = time.time() if when is None else when
        return self

    # --- properties a reviewer will ask about ------------------------------

    @property
    def provenance(self) -> Provenance:
        """The weakest evidence in the session. One simulated sample makes it simulated."""
        if not self.samples:
            return Provenance.ESTIMATED
        return Provenance.weakest(s.provenance for s in self.samples)

    @property
    def is_hardware_session(self) -> bool:
        return self.provenance in (Provenance.MEASURED, Provenance.DERIVED)

    @property
    def coverage(self) -> dict:
        """The span of work shapes the session covers.

        Coefficients fitted outside this span are extrapolation, and the validation report
        says so rather than quietly reporting a low error.
        """
        if not self.samples:
            return {"samples": 0}
        ins = [s.input_tokens for s in self.samples]
        outs = [s.output_tokens for s in self.samples]
        batches = [s.batch_size for s in self.samples]
        return {
            "samples": len(self.samples),
            "input_tokens": {"min": min(ins), "max": max(ins)},
            "output_tokens": {"min": min(outs), "max": max(outs)},
            "batch_size": {"min": min(batches), "max": max(batches)},
            "distinct_shapes": len({(s.input_tokens, s.output_tokens, s.batch_size)
                                    for s in self.samples}),
        }

    def configuration_hash(self) -> str:
        payload = json.dumps({"model": self.model, "hardware": self.hardware,
                              "runtime": self.runtime, "adapter": self.adapter},
                             sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "model": self.model,
            "hardware": self.hardware,
            "runtime": self.runtime,
            "adapter": self.adapter,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "provenance": self.provenance.value,
            "is_hardware_session": self.is_hardware_session,
            "configuration_hash": self.configuration_hash(),
            "coverage": self.coverage,
            "notes": self.notes,
            "samples": [s.as_dict() for s in self.samples],
        }
