"""Short-horizon forecasting for GPU demand, energy, cooling load and token volume.

Two deliberately simple models with honest intervals:

* ``HoltForecaster``      - level + trend, for 5 min to 1 hour.
* ``SeasonalNaiveForecaster`` - repeats the previous cycle, for 24 hours where the daily
  shape dominates.

Intervals come from empirical quantiles of walk-forward residuals at the same horizon,
so they widen when the model has been wrong at that horizon rather than assuming
normality. A forecast with too little history reports ``insufficient_history`` instead
of inventing a number.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Sequence


@dataclass(frozen=True)
class ForecastPoint:
    horizon_steps: int
    value: float
    lower: float
    upper: float

    def as_dict(self) -> dict:
        return {"horizon_steps": self.horizon_steps, "value": self.value,
                "lower": self.lower, "upper": self.upper}


@dataclass(frozen=True)
class Forecast:
    metric: str
    step_s: float
    points: tuple[ForecastPoint, ...]
    method: str
    interval_level: float = 0.80
    warning: str = ""

    def at(self, seconds_ahead: float) -> ForecastPoint | None:
        steps = max(1, round(seconds_ahead / self.step_s))
        for p in self.points:
            if p.horizon_steps == steps:
                return p
        return self.points[-1] if self.points else None

    def as_dict(self) -> dict:
        return {"metric": self.metric, "step_s": self.step_s, "method": self.method,
                "interval_level": self.interval_level, "warning": self.warning,
                "points": [p.as_dict() for p in self.points]}


def _quantile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


class HoltForecaster:
    """Holt's linear trend with damping."""

    def __init__(self, alpha: float = 0.3, beta: float = 0.1, phi: float = 0.92,
                 min_history: int = 12) -> None:
        self.alpha, self.beta, self.phi = alpha, beta, phi
        self.min_history = min_history

    def _fit(self, series: Sequence[float]) -> tuple[float, float, list[float]]:
        level = series[0]
        trend = series[1] - series[0] if len(series) > 1 else 0.0
        one_step_errors: list[float] = []
        for y in series[1:]:
            pred = level + self.phi * trend
            one_step_errors.append(y - pred)
            new_level = self.alpha * y + (1 - self.alpha) * pred
            trend = self.beta * (new_level - level) + (1 - self.beta) * self.phi * trend
            level = new_level
        return level, trend, one_step_errors

    def forecast(self, series: Sequence[float], horizons: Sequence[int], metric: str,
                 step_s: float, interval_level: float = 0.80) -> Forecast:
        series = [float(v) for v in series]
        if len(series) < self.min_history:
            return Forecast(metric, step_s, (), "holt-damped", interval_level,
                            f"insufficient_history: {len(series)} of {self.min_history} points")
        level, trend, errors = self._fit(series)
        abs_err = sorted(abs(e) for e in errors)
        q = _quantile(abs_err, interval_level)
        points = []
        for h in horizons:
            damp = sum(self.phi ** (i + 1) for i in range(h))
            value = level + damp * trend
            # residual spread grows with sqrt(h) for a random-walk-like error process
            spread = q * (h ** 0.5)
            points.append(ForecastPoint(h, value, max(0.0, value - spread), value + spread))
        return Forecast(metric, step_s, tuple(points), "holt-damped", interval_level)


class SeasonalNaiveForecaster:
    """Repeat the value one full season back; interval from same-phase residuals."""

    def __init__(self, season_length: int) -> None:
        self.season_length = season_length

    def forecast(self, series: Sequence[float], horizons: Sequence[int], metric: str,
                 step_s: float, interval_level: float = 0.80) -> Forecast:
        m = self.season_length
        if len(series) < 2 * m:
            return Forecast(metric, step_s, (), "seasonal-naive", interval_level,
                            f"insufficient_history: need {2 * m} points, have {len(series)}")
        residuals = sorted(abs(series[i] - series[i - m]) for i in range(m, len(series)))
        q = _quantile(residuals, interval_level)
        points = []
        for h in horizons:
            value = series[-m + ((h - 1) % m)]
            points.append(ForecastPoint(h, value, max(0.0, value - q), value + q))
        return Forecast(metric, step_s, tuple(points), "seasonal-naive", interval_level)


def backtest(forecaster, series: Sequence[float], horizon: int, metric: str = "metric",
             step_s: float = 60.0, min_train: int = 24) -> dict:
    """Walk-forward evaluation. Returns MAPE, MAE and interval coverage."""
    errors, pct_errors, covered, total = [], [], 0, 0
    for cut in range(min_train, len(series) - horizon):
        f = forecaster.forecast(series[:cut], [horizon], metric, step_s)
        if not f.points:
            continue
        p = f.points[0]
        actual = series[cut + horizon - 1]
        errors.append(abs(p.value - actual))
        if actual:
            pct_errors.append(abs(p.value - actual) / abs(actual))
        total += 1
        if p.lower <= actual <= p.upper:
            covered += 1
    return {
        "samples": total,
        "mae": mean(errors) if errors else None,
        "mape": mean(pct_errors) if pct_errors else None,
        "interval_coverage": covered / total if total else None,
        "horizon_steps": horizon,
    }
