"""Coefficient fitting and error analysis.

The model fitted is deliberately the simplest one that can be defended:

    Wh(request) = baseline + prefill · input_tokens + decode · output_tokens

``baseline`` absorbs the per-request fixed cost - scheduler overhead, the share of idle
power the request occupies, tokenisation. It is the term most often forgotten, and the
reason per-token figures look wrong at small token counts.

Ordinary least squares, solved by Gaussian elimination on the 3x3 normal equations. No
dependencies, and the whole computation is auditable by hand on a sheet of paper.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Sequence

from .session import CalibrationSample, MeasurementSession


class FittingError(RuntimeError):
    """Raised when the data cannot support the fit that was asked for."""


@dataclass(frozen=True)
class Coefficients:
    baseline_wh: float                 # fixed cost per request
    prefill_wh_per_token: float        # marginal cost of one input token
    decode_wh_per_token: float         # marginal cost of one output token
    method: str = "ordinary least squares on (1, input_tokens, output_tokens)"
    r_squared: float = 0.0
    samples: int = 0

    @property
    def decode_prefill_ratio(self) -> float:
        """How much more a generated token costs than a prompt token, on this hardware."""
        if self.prefill_wh_per_token <= 0:
            return float("inf")
        return self.decode_wh_per_token / self.prefill_wh_per_token

    def predict(self, input_tokens: int, output_tokens: int) -> float:
        return (self.baseline_wh
                + self.prefill_wh_per_token * input_tokens
                + self.decode_wh_per_token * output_tokens)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["decode_prefill_ratio"] = self.decode_prefill_ratio
        return d


@dataclass(frozen=True)
class ValidationResult:
    """Error analysis of a fit against held-out or in-sample data."""

    samples: int
    mae: float                          # mean absolute error, Wh
    rmse: float                         # root mean square error, Wh
    mean_relative_error: float          # mean |predicted - measured| / measured
    bias: float                         # mean (predicted - measured); sign matters
    residual_std: float
    ci95_bias: tuple[float, float]      # 95% interval for the bias, normal approximation
    in_sample: bool
    method: str

    # Errors below this are numerical noise, not evidence of anything. Without a floor,
    # a perfect fit fails the bias test on floating-point dust.
    NEGLIGIBLE_WH = 1e-9

    @property
    def systematically_biased(self) -> bool:
        """Wrong in a consistent direction, rather than merely noisy."""
        if self.mae <= self.NEGLIGIBLE_WH:
            return False
        return abs(self.bias) > 0.5 * self.mae

    @property
    def acceptable(self) -> bool:
        """A deliberately modest bar: within 10% on average and not systematically off."""
        return self.mean_relative_error <= 0.10 and not self.systematically_biased

    def as_dict(self) -> dict:
        d = asdict(self)
        d["ci95_bias"] = list(self.ci95_bias)
        d["acceptable"] = self.acceptable
        return d


def fit_coefficients(session: MeasurementSession, *, per_request: bool = True) -> Coefficients:
    """Fit baseline, prefill and decode coefficients from a session.

    ``per_request=True`` divides batched measurements by the batch size, so the fit
    describes one request's share rather than the whole batch.
    """
    samples = session.samples
    if len(samples) < 4:
        raise FittingError(
            f"session {session.session_id} has {len(samples)} samples; fitting three "
            "coefficients needs at least four, and realistically dozens across several "
            "token shapes. WATTS does not fit a model it cannot defend."
        )
    shapes = {(s.input_tokens, s.output_tokens) for s in samples}
    if len(shapes) < 3:
        raise FittingError(
            "the session varies fewer than three distinct (input, output) shapes, so "
            "prefill and decode cannot be separated. Sweep input at fixed output and "
            "output at fixed input (docs/CALIBRATION.md)."
        )

    rows = []
    for s in samples:
        wh = s.measured_wh / s.batch_size if per_request else s.measured_wh
        rows.append((1.0, float(s.input_tokens), float(s.output_tokens), wh))

    beta = _solve_ols(rows)
    predicted = [beta[0] + beta[1] * r[1] + beta[2] * r[2] for r in rows]
    actual = [r[3] for r in rows]
    mean = sum(actual) / len(actual)
    ss_tot = sum((a - mean) ** 2 for a in actual)
    ss_res = sum((a - p) ** 2 for a, p in zip(actual, predicted))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return Coefficients(
        baseline_wh=beta[0],
        prefill_wh_per_token=beta[1],
        decode_wh_per_token=beta[2],
        r_squared=r2,
        samples=len(rows),
    )


def validate(coefficients: Coefficients, samples: Sequence[CalibrationSample],
             *, in_sample: bool = False, per_request: bool = True) -> ValidationResult:
    """Error analysis: how wrong is this fit, and is it wrong in a consistent direction?"""
    if not samples:
        raise FittingError("no samples to validate against")
    errors, rel_errors = [], []
    for s in samples:
        actual = s.measured_wh / s.batch_size if per_request else s.measured_wh
        predicted = coefficients.predict(s.input_tokens, s.output_tokens)
        errors.append(predicted - actual)
        if actual > 0:
            rel_errors.append(abs(predicted - actual) / actual)

    n = len(errors)
    mae = sum(abs(e) for e in errors) / n
    rmse = math.sqrt(sum(e * e for e in errors) / n)
    bias = sum(errors) / n
    var = sum((e - bias) ** 2 for e in errors) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var)
    half = 1.96 * std / math.sqrt(n) if n > 1 else 0.0

    return ValidationResult(
        samples=n,
        mae=mae,
        rmse=rmse,
        mean_relative_error=sum(rel_errors) / len(rel_errors) if rel_errors else float("nan"),
        bias=bias,
        residual_std=std,
        ci95_bias=(bias - half, bias + half),
        in_sample=in_sample,
        method=("in-sample residuals; expect optimism" if in_sample
                else "held-out residuals, normal approximation for the bias interval"),
    )


def compare_prediction(predicted_wh: Sequence[float], measured_wh: Sequence[float]) -> dict:
    """Twin vs hardware, the comparison step of the calibration loop.

    Returns the same error statistics as ``validate`` but for an arbitrary pair of series,
    so the digital twin can be checked against a metered run without being refitted.
    """
    if len(predicted_wh) != len(measured_wh) or not predicted_wh:
        raise FittingError("prediction and measurement series must be non-empty and aligned")
    errors = [p - m for p, m in zip(predicted_wh, measured_wh)]
    n = len(errors)
    mae = sum(abs(e) for e in errors) / n
    bias = sum(errors) / n
    rel = [abs(e) / m for e, m in zip(errors, measured_wh) if m > 0]
    return {
        "samples": n,
        "mae": mae,
        "rmse": math.sqrt(sum(e * e for e in errors) / n),
        "bias": bias,
        "mean_relative_error": sum(rel) / len(rel) if rel else float("nan"),
        "direction": "over-predicts" if bias > 0 else "under-predicts" if bias < 0 else "unbiased",
    }


def _solve_ols(rows: list[tuple[float, float, float, float]]) -> list[float]:
    """Normal equations XᵀX β = Xᵀy, solved with partial-pivot Gaussian elimination."""
    k = 3
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for r in rows:
        x = r[:k]
        y = r[k]
        for i in range(k):
            xty[i] += x[i] * y
            for j in range(k):
                xtx[i][j] += x[i] * x[j]

    aug = [xtx[i] + [xty[i]] for i in range(k)]
    for col in range(k):
        pivot = max(range(col, k), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise FittingError(
                "the design matrix is singular: the sampled token shapes do not vary "
                "independently, so the coefficients are not identifiable."
            )
        aug[col], aug[pivot] = aug[pivot], aug[col]
        p = aug[col][col]
        aug[col] = [v / p for v in aug[col]]
        for r in range(k):
            if r == col:
                continue
            factor = aug[r][col]
            aug[r] = [v - factor * w for v, w in zip(aug[r], aug[col])]
    return [aug[i][k] for i in range(k)]
