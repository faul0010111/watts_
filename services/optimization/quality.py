"""Quality floors.

WATTS cannot tell you whether a smaller model is good enough for your task. Nobody can,
from telemetry: quality is measured offline, on your own data, with a metric that suits the
work. What WATTS can do is refuse to trade quality it has not measured.

Hence the rule this module enforces:

    A configuration with no quality observation does not meet the floor. It is unknown,
    and unknown is inadmissible.

The alternative - assuming a configuration is fine until proven otherwise - is how energy
optimisation quietly becomes quality regression.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Metrics that suit different kinds of work. The list is open: register any metric name,
# as long as somebody measured it.
STANDARD_METRICS = {
    "classification_accuracy": "share of correctly labelled items",
    "retrieval_recall": "share of relevant documents retrieved",
    "task_success_rate": "share of agent tasks that reached the intended outcome",
    "evaluation_score": "score from a task-specific rubric or judge, normalised to 0..1",
    "exact_match": "share of answers matching the reference exactly",
}


@dataclass(frozen=True)
class QualityFloor:
    """The minimum acceptable quality for a workload, and how it is measured."""

    metric: str
    minimum: float
    evaluated_on: str = ""        # the dataset or rubric the floor refers to
    rationale: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.minimum <= 1.0:
            raise ValueError("quality floors are normalised to 0..1")
        if not self.metric:
            raise ValueError("a quality floor without a metric is not a floor")

    def describe(self) -> str:
        what = STANDARD_METRICS.get(self.metric, "custom metric")
        where = f" on {self.evaluated_on}" if self.evaluated_on else ""
        return f"{self.metric} ≥ {self.minimum:.3f} ({what}){where}"


@dataclass(frozen=True)
class QualityObservation:
    """A measured quality figure for one configuration. Offline evaluation, not telemetry."""

    configuration: str            # model name, routing policy, or any named configuration
    metric: str
    value: float
    samples: int
    evaluated_on: str
    source: str = "offline-evaluation"
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.samples <= 0:
            raise ValueError("a quality observation with no samples is not an observation")

    def as_dict(self) -> dict:
        return {
            "configuration": self.configuration, "metric": self.metric, "value": self.value,
            "samples": self.samples, "evaluated_on": self.evaluated_on,
            "source": self.source, "timestamp": self.timestamp,
        }


@dataclass
class QualityRegistry:
    """What has actually been evaluated, and therefore what may be routed to."""

    observations: dict[tuple[str, str], QualityObservation] = field(default_factory=dict)
    min_samples: int = 30

    def record(self, observation: QualityObservation) -> QualityObservation:
        self.observations[(observation.configuration, observation.metric)] = observation
        return observation

    def get(self, configuration: str, metric: str) -> QualityObservation | None:
        return self.observations.get((configuration, metric))

    def check(self, configuration: str, floor: QualityFloor | None) -> tuple[bool, str]:
        """Does this configuration meet the floor, and on what evidence?

        Returns ``(ok, reason)``. ``ok`` is False whenever the answer is not a measured yes.
        """
        if floor is None:
            return True, "no quality floor declared for this workload"

        observation = self.get(configuration, floor.metric)
        if observation is None:
            return False, (
                f"no {floor.metric} observation for '{configuration}'. WATTS will not assume "
                f"a configuration meets a quality floor it has never been evaluated against "
                f"(docs/OPTIMIZATION.md, 'Quality floors')."
            )
        if observation.samples < self.min_samples:
            return False, (
                f"{floor.metric} for '{configuration}' rests on {observation.samples} samples, "
                f"below the {self.min_samples} this registry requires to act on"
            )
        if observation.value < floor.minimum:
            return False, (
                f"{floor.metric} {observation.value:.3f} is below the floor {floor.minimum:.3f} "
                f"(evaluated on {observation.evaluated_on}, n={observation.samples})"
            )
        return True, (f"{floor.metric} {observation.value:.3f} ≥ {floor.minimum:.3f} "
                      f"(n={observation.samples}, {observation.evaluated_on})")

    def value(self, configuration: str, metric: str) -> float | None:
        observation = self.get(configuration, metric)
        return observation.value if observation else None

    def coverage(self, configurations: list[str], metric: str) -> dict:
        """Which configurations may be considered at all, and which are simply unknown."""
        known = [c for c in configurations if self.get(c, metric)]
        return {
            "metric": metric,
            "evaluated": sorted(known),
            "unevaluated": sorted(set(configurations) - set(known)),
            "coverage": len(known) / len(configurations) if configurations else 0.0,
            "note": "unevaluated configurations are inadmissible, not assumed adequate",
        }

    def as_dict(self) -> dict:
        return {"min_samples": self.min_samples,
                "observations": [o.as_dict() for o in self.observations.values()]}
