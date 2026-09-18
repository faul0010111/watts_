"""Attribute request energy to input and output tokens.

Measuring energy *per token* directly is not possible on shared hardware: the GPU meter
reports device power, not per-token power. WATTS therefore measures energy per request
(or per window) and *attributes* it to tokens using the model profile's prefill/decode
weights. The split is an attribution, and is labelled as such; the request total keeps
the provenance of the underlying measurement.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..energy_engine.model import Provenance
from .profiles import ModelProfile


@dataclass(frozen=True)
class TokenEnergyAttribution:
    input_tokens: int
    output_tokens: int
    energy_wh: float
    input_energy_wh: float
    output_energy_wh: float
    provenance: Provenance
    attribution_method: str

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def wh_per_input_token(self) -> float:
        return self.input_energy_wh / self.input_tokens if self.input_tokens else 0.0

    @property
    def wh_per_output_token(self) -> float:
        return self.output_energy_wh / self.output_tokens if self.output_tokens else 0.0

    @property
    def wh_per_total_token(self) -> float:
        return self.energy_wh / self.total_tokens if self.total_tokens else 0.0

    def as_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "energy_wh": self.energy_wh,
            "input_energy_wh": self.input_energy_wh,
            "output_energy_wh": self.output_energy_wh,
            "wh_per_input_token": self.wh_per_input_token,
            "wh_per_output_token": self.wh_per_output_token,
            "wh_per_total_token": self.wh_per_total_token,
            "provenance": self.provenance.value,
            "attribution_method": self.attribution_method,
        }


def attribute_energy(
    energy_wh: float,
    input_tokens: int,
    output_tokens: int,
    profile: ModelProfile,
    provenance: Provenance = Provenance.DERIVED,
) -> TokenEnergyAttribution:
    """Split request energy proportionally to the profile's compute weights."""
    if energy_wh < 0:
        raise ValueError("energy cannot be negative")
    w_in = input_tokens * profile.prefill_weight
    w_out = output_tokens * profile.decode_weight
    total_w = w_in + w_out
    if total_w <= 0:
        return TokenEnergyAttribution(input_tokens, output_tokens, energy_wh, 0.0, 0.0,
                                      provenance, "no tokens to attribute")
    return TokenEnergyAttribution(
        input_tokens, output_tokens, energy_wh,
        energy_wh * w_in / total_w,
        energy_wh * w_out / total_w,
        provenance,
        f"prefill/decode weights from profile '{profile.name}' (source={profile.source})",
    )


def energy_per_token(records) -> dict:
    """Aggregate Wh/token over a set of records that already carry ``energy_wh``."""
    tokens_in = sum(r.input_tokens for r in records)
    tokens_out = sum(r.output_tokens for r in records)
    energy = sum(r.energy_wh or 0.0 for r in records)
    requests = len(records)
    total = tokens_in + tokens_out
    return {
        "requests": requests,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "total_tokens": total,
        "energy_wh": energy,
        "wh_per_total_token": energy / total if total else 0.0,
        "wh_per_1k_tokens": energy / (total / 1000.0) if total else 0.0,
        "wh_per_request": energy / requests if requests else 0.0,
    }


# ---------------------------------------------------------------------------
# Attribution 2.0: decomposition of a single request's energy
# ---------------------------------------------------------------------------

from typing import Sequence  # noqa: E402

from ..energy_engine.value import EnergyValue, estimated as _estimated, provenance_summary  # noqa: E402


@dataclass(frozen=True)
class AttributionInputs:
    """What the decomposition needs beyond token counts.

    All defaults are declared, not measured. They are here so the decomposition has a
    shape; replace them with your own figures before quoting component watt-hours.
    """

    baseline_wh_per_request: float = 0.0     # scheduler, tokenisation, idle share
    memory_gb: float = 0.0
    network_gb: float = 0.0
    storage_gb_read: float = 0.0
    storage_gb_written: float = 0.0
    shared_infrastructure_share: float = 0.0  # 0..1 of request energy, e.g. control plane
    source: str = "declared-default"


@dataclass(frozen=True)
class RequestEnergyDecomposition:
    """Request energy split into the components that produced it.

        E_request = E_baseline + E_prefill + E_decode + E_memory
                    + E_network + E_storage + E_shared

    When the underlying measurement covers only the serving window - which is the normal
    case, because that is what a GPU meter reports - the split *inside* that window is a
    model, not a measurement. ``decomposition_is_attribution`` says so, and each component
    carries its own provenance so a consumer never has to infer it.
    """

    request_id: str
    model: str
    input_tokens: int
    output_tokens: int
    total: EnergyValue
    components: tuple[tuple[str, EnergyValue], ...]
    attribution_method: str
    decomposition_is_attribution: bool = True

    @property
    def measured_window(self) -> bool:
        return self.total.is_measurement

    def get(self, component: str) -> EnergyValue | None:
        for name, value in self.components:
            if name == component:
                return value
        return None

    @property
    def residual_wh(self) -> float:
        """Total minus the sum of parts. Non-zero means the model does not close."""
        return self.total.value - sum(v.value for _, v in self.components)

    def as_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total": self.total.as_dict(),
            "components": {name: value.as_dict() for name, value in self.components},
            "residual_wh": self.residual_wh,
            "attribution_method": self.attribution_method,
            "decomposition_is_attribution": self.decomposition_is_attribution,
            "measured_window": self.measured_window,
            "provenance_summary": provenance_summary([v for _, v in self.components]),
            "note": ("the request total carries the provenance of its measurement; the "
                     "split between components is produced by the model profile and the "
                     "declared coefficients, and is an attribution"),
        }


def decompose_request_energy(
    total: EnergyValue,
    *,
    request_id: str,
    input_tokens: int,
    output_tokens: int,
    profile: ModelProfile,
    inputs: AttributionInputs | None = None,
    energy_model=None,
    window_s: float = 0.0,
) -> RequestEnergyDecomposition:
    """Split one request's measured (or simulated) energy into named components.

    The non-compute components are subtracted first, because they are produced by their own
    models and do not scale with tokens. Whatever remains is compute, and *that* is what
    the prefill/decode weights divide. Doing it the other way round - splitting everything
    by token weight and calling part of it "network" - would be arithmetic dressed up as
    physics.
    """
    ins = inputs or AttributionInputs()
    calibration_id = profile.calibration_id if profile.calibrated else None
    components: list[tuple[str, EnergyValue]] = []

    def side(name: str, wh: float, source: str) -> None:
        if wh > 0:
            components.append((name, _estimated(wh, source, timestamp=total.timestamp)))

    baseline = ins.baseline_wh_per_request or (profile.baseline_wh or 0.0)
    side("baseline", baseline, f"per-request fixed cost ({ins.source})")

    if energy_model is not None and window_s > 0:
        if ins.memory_gb:
            side("memory", energy_model.memory_energy_wh(ins.memory_gb, window_s).wh,
                 "per-GB DRAM static model")
        if ins.network_gb:
            side("network", energy_model.network_energy_wh(ins.network_gb).wh,
                 "per-GB transfer model")
        if ins.storage_gb_read or ins.storage_gb_written:
            side("storage", energy_model.storage_energy_wh(
                ins.storage_gb_read, ins.storage_gb_written).wh, "per-GB I/O model")

    shared_wh = total.value * max(0.0, min(ins.shared_infrastructure_share, 1.0))
    side("shared_infrastructure", shared_wh, "declared share of control-plane overhead")

    compute_wh = max(0.0, total.value - sum(v.value for _, v in components))
    w_in = input_tokens * profile.prefill_weight
    w_out = output_tokens * profile.decode_weight
    total_w = w_in + w_out

    if total_w > 0:
        split_provenance = (Provenance.DERIVED if total.is_measurement else total.provenance)
        components.append(("prefill", EnergyValue(
            compute_wh * w_in / total_w, total.unit,
            f"prefill weight of profile '{profile.name}' ({profile.source})",
            split_provenance, total.timestamp, calibration_id=calibration_id)))
        components.append(("decode", EnergyValue(
            compute_wh * w_out / total_w, total.unit,
            f"decode weight of profile '{profile.name}' ({profile.source})",
            split_provenance, total.timestamp, calibration_id=calibration_id)))
    else:
        components.append(("prefill", EnergyValue(0.0, total.unit, "no tokens",
                                                  total.provenance, total.timestamp)))
        components.append(("decode", EnergyValue(0.0, total.unit, "no tokens",
                                                 total.provenance, total.timestamp)))

    return RequestEnergyDecomposition(
        request_id=request_id,
        model=profile.name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total=total,
        components=tuple(components),
        attribution_method=(
            f"non-compute components from declared models ({ins.source}); remaining compute "
            f"split by prefill/decode weights of profile '{profile.name}' "
            f"(source={profile.source}, calibration={calibration_id or 'none'})"
        ),
    )


def aggregate_decompositions(decompositions: Sequence[RequestEnergyDecomposition]) -> dict:
    """Roll up component energy across requests, keeping provenance per component."""
    if not decompositions:
        return {"requests": 0, "components": {}, "total_wh": 0.0}
    totals: dict[str, list[EnergyValue]] = {}
    for d in decompositions:
        for name, value in d.components:
            totals.setdefault(name, []).append(value)
    rolled = {name: EnergyValue.total(vals, source=f"sum:{name}").as_dict()
              for name, vals in totals.items()}
    grand = sum(d.total.value for d in decompositions)
    return {
        "requests": len(decompositions),
        "total_wh": grand,
        "components": rolled,
        "shares": {name: (sum(v.value for v in vals) / grand if grand else 0.0)
                   for name, vals in totals.items()},
        "provenance_summary": provenance_summary(
            [v for vals in totals.values() for v in vals]),
    }
