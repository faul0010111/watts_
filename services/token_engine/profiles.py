"""Model profiles: the coefficients used to split GPU energy between prefill and decode.

A profile is never an empirical claim shipped by WATTS. Each one records where its
numbers came from (``source``) and whether it was calibrated on your hardware
(``calibrated_on``). Uncalibrated profiles are usable for *relative* comparison between
strategies on the same hardware; they are not a measurement of any vendor's model.

Calibrate with ``experiments/exp01_context_impact.py`` against a real GPU, then persist
the result. See docs/TOKEN_MODEL.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass(frozen=True)
class ModelProfile:
    name: str
    family: str
    params_b: float                     # billions of parameters, 0 if undisclosed
    prefill_weight: float               # relative compute cost of one input token
    decode_weight: float                # relative compute cost of one output token
    max_context: int
    quality_tier: int                   # 1 (small/fast) .. 5 (frontier)
    generative: bool = True             # False for embedding models: they cannot serve chat traffic
    approved_for_sensitive: bool = False
    source: str = "declared-default"    # "declared-default" | "calibrated" | "vendor-doc"
    calibrated_on: str = ""             # e.g. "1xA100-80GB, vLLM 0.5.4, 2026-04-11"
    notes: str = ""
    # Filled only by services/calibration when a real measurement session backs them.
    # The weights above are shares, so attribution works without these; these are what
    # let WATTS quote watt-hours rather than proportions.
    wh_per_input_token: float | None = None
    wh_per_output_token: float | None = None
    baseline_wh: float | None = None
    calibration_id: str | None = None

    @property
    def calibrated(self) -> bool:
        """True only when a registered calibration session produced these coefficients."""
        return self.source == "calibrated" and self.calibration_id is not None

    def absolute_wh(self, input_tokens: int, output_tokens: int) -> float | None:
        """Predicted energy in Wh, or None when this profile has never been calibrated.

        Returning None is the point: a caller that needs watt-hours must handle the case
        where WATTS does not know them, rather than receive a fabricated figure.
        """
        if self.wh_per_input_token is None or self.wh_per_output_token is None:
            return None
        return ((self.baseline_wh or 0.0)
                + self.wh_per_input_token * input_tokens
                + self.wh_per_output_token * output_tokens)

    def relative_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Unitless compute weight of a request under this profile."""
        return input_tokens * self.prefill_weight + output_tokens * self.decode_weight

    def as_dict(self) -> dict:
        return asdict(self)


class ProfileRegistry:
    def __init__(self, profiles: list[ModelProfile] | None = None) -> None:
        self._profiles: dict[str, ModelProfile] = {p.name: p for p in (profiles or [])}

    def add(self, profile: ModelProfile) -> None:
        self._profiles[profile.name] = profile

    def get(self, name: str) -> ModelProfile:
        if name not in self._profiles:
            raise KeyError(
                f"no profile for model '{name}'. WATTS refuses to guess energy coefficients; "
                f"register a ModelProfile first (docs/TOKEN_MODEL.md)."
            )
        return self._profiles[name]

    def names(self) -> list[str]:
        return sorted(self._profiles)

    def all(self) -> list[ModelProfile]:
        return [self._profiles[n] for n in self.names()]

    def uncalibrated(self) -> list[str]:
        return [p.name for p in self.all() if p.source != "calibrated"]

    @classmethod
    def from_json(cls, path: str | Path) -> "ProfileRegistry":
        data = json.loads(Path(path).read_text())
        return cls([ModelProfile(**p) for p in data["profiles"]])


def load_default_profiles() -> ProfileRegistry:
    """Reference profiles for the simulator.

    Decode weight exceeds prefill weight because decoding is memory-bandwidth bound and
    processes one token per step, while prefill is compute bound and batches the whole
    prompt. The ratio, not the absolute value, is what drives WATTS' attribution.
    """
    path = Path(__file__).with_name("model_profiles.json")
    if path.exists():
        return ProfileRegistry.from_json(path)
    return ProfileRegistry()
