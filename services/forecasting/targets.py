"""What WATTS forecasts, and the probability it attaches to a breach.

Five targets, because they answer different operational questions:

| Target | Question |
|---|---|
| energy | will the energy budget hold? |
| tokens | is demand growing, and how fast? |
| GPU demand | will there be enough accelerators at peak? |
| cooling load | will the facility keep up, or will throttling start? |
| budget breach probability | how likely is the breach, not just whether it is expected |

The last one exists because a central projection that lands just under a limit is not
reassuring. What an operator needs is the chance of crossing it, which comes from the
forecaster's own residual spread rather than from a confidence-sounding adjective.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .forecast import Forecast, HoltForecaster


@dataclass(frozen=True)
class BreachProbability:
    dimension: str
    limit: float
    horizon_s: float
    central: float
    probability: float
    method: str

    @property
    def severity(self) -> str:
        if self.probability >= 0.80:
            return "expected"
        if self.probability >= 0.30:
            return "likely enough to plan for"
        if self.probability >= 0.05:
            return "unlikely but possible"
        return "negligible"

    def as_dict(self) -> dict:
        return {
            "dimension": self.dimension, "limit": self.limit, "horizon_s": self.horizon_s,
            "central": self.central, "probability": self.probability,
            "severity": self.severity, "method": self.method,
        }


def breach_probability(forecast: Forecast, limit: float, *,
                       dimension: str = "energy") -> BreachProbability | None:
    """Probability the central path's distribution exceeds ``limit`` at the last horizon.

    The interval the forecaster produced is treated as a normal spread around the central
    path - an approximation, stated as one. It is a good deal more informative than a
    yes/no breach flag and a good deal less dishonest than a point estimate.
    """
    if not forecast.points:
        return None
    point = forecast.points[-1]
    horizon_s = point.horizon_steps * forecast.step_s
    half_width = (point.upper - point.lower) / 2.0
    z_for_level = 1.2816 if abs(forecast.interval_level - 0.80) < 1e-6 else 1.96
    sigma = half_width / z_for_level if z_for_level else 0.0
    if sigma <= 0:
        probability = 1.0 if point.value > limit else 0.0
        method = "degenerate interval: the forecaster reported no spread"
    else:
        z = (limit - point.value) / sigma
        probability = 1.0 - _normal_cdf(z)
        method = (f"normal approximation to the forecaster's {forecast.interval_level:.0%} "
                  f"interval (σ ≈ {sigma:.4g})")
    return BreachProbability(dimension, limit, horizon_s, point.value,
                             max(0.0, min(1.0, probability)), method)


@dataclass
class ForecastSuite:
    """All five targets for one window, produced from aligned aggregate series."""

    energy: Forecast
    tokens: Forecast
    gpu_demand: Forecast
    cooling_load: Forecast
    breaches: list[BreachProbability]
    step_s: float

    def as_dict(self) -> dict:
        return {
            "step_s": self.step_s,
            "energy": self.energy.as_dict(),
            "tokens": self.tokens.as_dict(),
            "gpu_demand": self.gpu_demand.as_dict(),
            "cooling_load": self.cooling_load.as_dict(),
            "breach_probabilities": [b.as_dict() for b in self.breaches],
        }

    def to_markdown(self) -> str:
        rows = ["| Target | Now | Horizon | 80% interval |", "|---|---|---|---|"]
        for name, fc, unit in (("energy", self.energy, "W"), ("tokens", self.tokens, "tok/s"),
                               ("GPU demand", self.gpu_demand, "GPU-s"),
                               ("cooling load", self.cooling_load, "W")):
            if not fc.points:
                rows.append(f"| {name} | — | — | {fc.warning or 'unavailable'} |")
                continue
            p = fc.points[-1]
            rows.append(f"| {name} | — | {p.value:,.2f} {unit} "
                        f"(+{p.horizon_steps * fc.step_s / 60:.0f} min) | "
                        f"{p.lower:,.2f}–{p.upper:,.2f} |")
        for b in self.breaches:
            rows.append("")
            rows.append(f"**{b.dimension} budget:** {b.probability:.0%} chance of breaching "
                        f"{b.limit:,.0f} within {b.horizon_s / 60:.0f} min — {b.severity}.")
        return "\n".join(rows)


def build_forecast_suite(
    *,
    energy_series: Sequence[float],
    token_series: Sequence[float],
    gpu_demand_series: Sequence[float],
    cooling_series: Sequence[float],
    step_s: float,
    horizons: Sequence[int] = (2, 4, 8, 12),
    energy_limit: float | None = None,
    token_limit: float | None = None,
) -> ForecastSuite:
    """Forecast every target with the damped Holt model and attach breach probabilities."""
    forecaster = HoltForecaster()
    energy = forecaster.forecast(energy_series, horizons, "facility_power_w", step_s)
    tokens = forecaster.forecast(token_series, horizons, "tokens_per_s", step_s)
    gpu = forecaster.forecast(gpu_demand_series, horizons, "gpu_seconds_per_s", step_s)
    cooling = forecaster.forecast(cooling_series, horizons, "cooling_power_w", step_s)

    breaches = []
    for fc, limit, name in ((energy, energy_limit, "energy"), (tokens, token_limit, "tokens")):
        if limit:
            b = breach_probability(fc, limit, dimension=name)
            if b:
                breaches.append(b)
    return ForecastSuite(energy, tokens, gpu, cooling, breaches, step_s)


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
