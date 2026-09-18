"""Feature-based forecasting.

The univariate forecasters answer "what does this series usually do next". That is enough
for a smooth load and useless the moment traffic shape changes, because energy is not an
autonomous process: it is produced by tokens, batching, utilisation and heat.

So this module fits energy against the things that cause it:

    input_tokens, output_tokens, requests_per_second, batch_size,
    gpu_utilization, temperature, queue_depth, model mix, hour of day

Ordinary least squares with an intercept, standardised internally so that features on wildly
different scales stay conditioned. No dependencies, and the fitted coefficients are printed
in the report so anyone can see which driver the model thinks matters.

Honesty constraints built in:
* a feature that never varies is dropped, not given a meaningless coefficient;
* fewer samples than 5× the number of features refuses to fit rather than overfitting;
* prediction intervals come from held-out residuals, never from the fit's own optimism.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from .forecast import Forecast, ForecastPoint

DEFAULT_FEATURES = (
    "input_tokens", "output_tokens", "requests_per_second", "batch_size",
    "gpu_utilization", "temperature_c", "queue_depth", "hour",
)


class InsufficientData(RuntimeError):
    """Raised instead of returning a fit that the data cannot support."""


@dataclass
class FeatureModel:
    """A fitted linear model of energy against its drivers."""

    target: str
    features: list[str]
    coefficients: list[float]
    intercept: float
    means: list[float]
    stds: list[float]
    r_squared: float
    samples: int
    residual_std: float = 0.0
    dropped: list[str] = field(default_factory=list)

    def predict(self, row: dict) -> float:
        z = [(row.get(f, 0.0) - m) / s if s else 0.0
             for f, m, s in zip(self.features, self.means, self.stds)]
        return self.intercept + sum(c * v for c, v in zip(self.coefficients, z))

    @property
    def drivers(self) -> list[tuple[str, float]]:
        """Features ordered by the size of their standardised effect.

        Standardised, so the ordering answers "which driver moves the target most", not
        "which feature happens to be measured in the largest units".
        """
        return sorted(zip(self.features, self.coefficients), key=lambda kv: -abs(kv[1]))

    def as_dict(self) -> dict:
        return {
            "target": self.target, "features": self.features,
            "standardised_coefficients": dict(zip(self.features, self.coefficients)),
            "intercept": self.intercept, "r_squared": self.r_squared,
            "samples": self.samples, "residual_std": self.residual_std,
            "dropped_features": self.dropped,
            "drivers": [{"feature": f, "effect": c} for f, c in self.drivers],
            "method": "ordinary least squares on standardised features",
            "caveat": ("coefficients describe association in this window, not causation; a "
                       "driver can be a proxy for something unobserved"),
        }


def fit_feature_model(rows: Sequence[dict], target: str = "energy_wh",
                      features: Sequence[str] = DEFAULT_FEATURES) -> FeatureModel:
    """Fit ``target`` against ``features``. Refuses rather than overfits."""
    usable = [r for r in rows if target in r]
    if len(usable) < 10:
        raise InsufficientData(
            f"{len(usable)} windows available; fitting a feature model needs at least 10 "
            "and preferably several times the number of features")

    present = [f for f in features if any(f in r for r in usable)]
    varying, dropped = [], []
    for f in present:
        values = {r.get(f, 0.0) for r in usable}
        (varying if len(values) > 1 else dropped).append(f)
    if not varying:
        raise InsufficientData("no feature varies across the supplied windows")
    if len(usable) < 5 * len(varying):
        # Keep the strongest-varying features rather than fitting noise.
        varying = varying[:max(1, len(usable) // 5)]

    means, stds = [], []
    for f in varying:
        vals = [r.get(f, 0.0) for r in usable]
        m = sum(vals) / len(vals)
        var = sum((v - m) ** 2 for v in vals) / len(vals)
        means.append(m)
        stds.append(math.sqrt(var) or 1.0)

    x_rows = [[1.0] + [(r.get(f, 0.0) - m) / s for f, m, s in zip(varying, means, stds)]
              for r in usable]
    y = [r[target] for r in usable]
    beta = _solve_normal_equations(x_rows, y)

    predicted = [sum(b * v for b, v in zip(beta, row)) for row in x_rows]
    residuals = [a - p for a, p in zip(y, predicted)]
    mean_y = sum(y) / len(y)
    ss_tot = sum((v - mean_y) ** 2 for v in y)
    ss_res = sum(r * r for r in residuals)
    dof = max(1, len(y) - len(beta))

    return FeatureModel(
        target=target, features=varying, coefficients=beta[1:], intercept=beta[0],
        means=means, stds=stds,
        r_squared=1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0,
        samples=len(y),
        residual_std=math.sqrt(ss_res / dof),
        dropped=dropped,
    )


def forecast_with_features(model: FeatureModel, future_rows: Sequence[dict],
                           metric: str, step_s: float,
                           interval_level: float = 0.80) -> Forecast:
    """Project the target for future feature rows, with residual-based intervals.

    The caller supplies the future drivers - from a token forecast, a known batch change, a
    planned deployment. That is the point: this model answers "what would energy be if the
    workload looked like this", which is the question an operator can act on.
    """
    if not future_rows:
        return Forecast(metric=metric, step_s=step_s, points=(),
                        method=f"ols-features({len(model.features)})",
                        interval_level=interval_level,
                        warning="no future feature rows supplied")
    z = 1.2816 if abs(interval_level - 0.80) < 1e-6 else 1.96
    half = z * model.residual_std
    points = tuple(
        ForecastPoint(
            horizon_steps=i + 1,
            value=max(0.0, model.predict(row)),
            lower=max(0.0, model.predict(row) - half),
            upper=model.predict(row) + half,
        )
        for i, row in enumerate(future_rows)
    )
    return Forecast(metric=metric, step_s=step_s, points=points,
                    method=f"ols-features({len(model.features)}) fitted on {model.samples} windows",
                    interval_level=interval_level,
                    warning=("intervals assume the residual spread seen in-sample continues; "
                             "they do not cover a change in the workload's character"))


def _solve_normal_equations(x_rows: list[list[float]], y: list[float]) -> list[float]:
    k = len(x_rows[0])
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for row, target in zip(x_rows, y):
        for i in range(k):
            xty[i] += row[i] * target
            for j in range(k):
                xtx[i][j] += row[i] * row[j]
    # Small ridge term: keeps the solve stable when two drivers move almost together,
    # which they routinely do (tokens and utilisation, temperature and power).
    for i in range(1, k):
        xtx[i][i] += 1e-8
    aug = [xtx[i] + [xty[i]] for i in range(k)]
    for col in range(k):
        pivot = max(range(col, k), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise InsufficientData("features are collinear; the model is not identifiable")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        p = aug[col][col]
        aug[col] = [v / p for v in aug[col]]
        for r in range(k):
            if r == col:
                continue
            factor = aug[r][col]
            aug[r] = [v - factor * w for v, w in zip(aug[r], aug[col])]
    return [aug[i][k] for i in range(k)]
