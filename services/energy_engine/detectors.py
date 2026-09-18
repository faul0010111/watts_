"""Anomaly detection with a visible method.

A z-score without its window, baseline and estimator is not evidence, it is a number that
sounds like evidence. Every detection this module produces carries the method that made it,
so a reviewer can disagree with the method rather than argue with the verdict.

Default estimator is the median with median absolute deviation (MAD). It is used rather
than mean and standard deviation because the thing being detected - an occasional large
excursion - is exactly the thing that inflates a standard deviation and hides the next
excursion behind it.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from statistics import median
from typing import Sequence

# Scale factor making MAD a consistent estimator of σ for normally distributed data.
MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class DetectionMethod:
    """Everything needed to reproduce or challenge a detection."""

    estimator: str                 # "median/MAD (robust)" | "mean/σ"
    baseline_windows: int
    baseline_value: float
    dispersion: float
    threshold: float
    robust: bool
    direction: str                 # "upper" | "lower" | "two-sided"
    notes: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def describe(self) -> str:
        return (f"{self.estimator} over {self.baseline_windows} windows; baseline "
                f"{self.baseline_value:.4g}, dispersion {self.dispersion:.4g}, "
                f"|z| ≥ {self.threshold} ({self.direction})")


@dataclass(frozen=True)
class Detection:
    metric: str
    detected: bool
    observed: float
    baseline: float
    delta_pct: float
    score: float                   # robust z, or 0 when dispersion is degenerate
    severity: str                  # "none" | "watch" | "page"
    method: DetectionMethod
    reason: str

    def as_dict(self) -> dict:
        return {
            "metric": self.metric, "detected": self.detected, "observed": self.observed,
            "baseline": self.baseline, "delta_pct": self.delta_pct, "score": self.score,
            "severity": self.severity, "reason": self.reason,
            "method": self.method.as_dict(), "method_description": self.method.describe(),
        }


def robust_z(history: Sequence[float], current: float, *, threshold: float = 3.5,
             metric: str = "metric", direction: str = "upper",
             min_history: int = 10) -> Detection:
    """Median/MAD outlier test against the metric's own history.

    Thresholds are relative to the series, never absolute: a rack that always runs at
    1.9 PUE is not anomalous at 1.9, and one that always runs at 1.15 is.
    """
    values = [v for v in history if v is not None]
    if len(values) < min_history:
        method = DetectionMethod("median/MAD (robust)", len(values), float("nan"),
                                 float("nan"), threshold, True, direction,
                                 "insufficient history")
        return Detection(metric, False, current, float("nan"), 0.0, 0.0, "none", method,
                         f"insufficient history: {len(values)} windows, {min_history} required")

    base = median(values)
    mad = median([abs(v - base) for v in values])
    dispersion = mad * MAD_TO_SIGMA
    method = DetectionMethod("median/MAD (robust)", len(values), base, dispersion,
                             threshold, True, direction)

    if dispersion <= 0:
        # A perfectly flat baseline makes z undefined; fall back to a relative test and say so.
        delta = (current - base) / base * 100.0 if base else 0.0
        detected = (delta > 20.0) if direction == "upper" else abs(delta) > 20.0
        return Detection(metric, detected, current, base, delta, 0.0,
                         "watch" if detected else "none",
                         DetectionMethod("relative change (MAD is zero)", len(values), base,
                                         0.0, 20.0, True, direction,
                                         "baseline has no dispersion; z is undefined"),
                         "baseline is perfectly flat; used a 20% relative change test instead")

    z = (current - base) / dispersion
    if direction == "upper":
        detected = z >= threshold
    elif direction == "lower":
        detected = z <= -threshold
    else:
        detected = abs(z) >= threshold

    severity = "none"
    if detected:
        severity = "page" if abs(z) >= threshold * 1.5 else "watch"

    delta_pct = (current - base) / base * 100.0 if base else 0.0
    return Detection(metric, detected, current, base, delta_pct, z, severity, method,
                     (f"{metric} at {current:.4g} is {z:+.1f} robust σ from its own "
                      f"baseline {base:.4g}") if detected else
                     f"{metric} within {threshold} robust σ of baseline")


@dataclass
class DetectionSuite:
    """The seven detections WATTS runs on every window, each on its own baseline."""

    results: list[Detection]

    @property
    def firing(self) -> list[Detection]:
        return [d for d in self.results if d.detected]

    @property
    def pages(self) -> list[Detection]:
        return [d for d in self.results if d.severity == "page"]

    def get(self, metric: str) -> Detection | None:
        return next((d for d in self.results if d.metric == metric), None)

    def as_dict(self) -> dict:
        return {
            "detections": [d.as_dict() for d in self.results],
            "firing": [d.metric for d in self.firing],
            "pages": [d.metric for d in self.pages],
            "note": ("each metric is tested against its own history, not a global "
                     "threshold; a busy rack drawing more power is not an anomaly"),
        }


def run_detection_suite(history: Sequence[dict], current: dict, *,
                        threshold: float = 3.5, min_history: int = 10) -> DetectionSuite:
    """Run every detector over aligned windows of aggregated telemetry.

    ``history`` and ``current`` are dicts of window aggregates. Missing keys are skipped:
    a facility without cooling telemetry gets no PUE detection, rather than a fabricated one.
    """
    specs = [
        # metric key,                   direction, what an excursion means
        ("wh_per_1k_tokens", "upper", "energy per unit of useful work"),
        ("gpu_power_w", "two-sided", "device power at the fleet level"),
        ("gpu_temp_max_c", "upper", "thermal state"),
        ("pue", "upper", "facility overhead"),
        ("tokens_per_s", "two-sided", "workload volume"),
        ("output_tokens_per_request", "upper", "token volume per request"),
        ("latency_energy_ratio", "two-sided", "latency and energy moving apart"),
    ]
    results: list[Detection] = []
    for key, direction, _meaning in specs:
        if key not in current:
            continue
        series = [w[key] for w in history if key in w and w[key] is not None]
        results.append(robust_z(series, current[key], threshold=threshold, metric=key,
                                direction=direction, min_history=min_history))
    return DetectionSuite(results)


def latency_energy_ratio(p95_latency_ms: float, wh_per_1k_tokens: float) -> float:
    """Latency per unit of energy.

    Watched because the two usually move together: work that takes longer costs more. When
    they diverge - latency up, energy flat - the bottleneck is queueing or a dependency,
    not the accelerator, and no energy optimisation will help.
    """
    return p95_latency_ms / wh_per_1k_tokens if wh_per_1k_tokens else float("inf")
