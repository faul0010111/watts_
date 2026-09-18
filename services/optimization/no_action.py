"""The no-action projection.

Every optimisation proposal is implicitly a comparison against doing nothing, and that
comparison is usually left unstated - which lets a 5% saving look like a win in a workload
whose demand is growing 30% a month, and lets a change look urgent in a workload that is
about to go quiet on its own.

So WATTS answers the question directly: *what happens if nothing is changed?*

The projection is a forecast, with all a forecast's uncertainty attached. It is never
presented as a fact about the future.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..forecasting.forecast import Forecast


@dataclass(frozen=True)
class ProjectedDimension:
    name: str
    current: float
    projected: float
    lower: float
    upper: float
    unit: str
    limit: float | None = None

    @property
    def change_pct(self) -> float:
        return (self.projected - self.current) / self.current * 100.0 if self.current else 0.0

    @property
    def utilisation(self) -> float | None:
        return self.projected / self.limit if self.limit else None

    @property
    def breach_expected(self) -> bool:
        return self.limit is not None and self.projected > self.limit

    @property
    def breach_possible(self) -> bool:
        """The upper bound crosses the limit even though the central path does not."""
        return self.limit is not None and self.upper > self.limit and not self.breach_expected

    def as_dict(self) -> dict:
        return {
            "name": self.name, "current": self.current, "projected": self.projected,
            "lower": self.lower, "upper": self.upper, "unit": self.unit,
            "change_pct": self.change_pct, "limit": self.limit,
            "utilisation": self.utilisation, "breach_expected": self.breach_expected,
            "breach_possible": self.breach_possible,
        }


@dataclass
class NoActionProjection:
    """Where the workload lands on its current trajectory, and what that costs."""

    horizon_s: float
    dimensions: list[ProjectedDimension] = field(default_factory=list)
    slo_state: str = "unknown"
    thermal_state: str = "unknown"
    warnings: list[str] = field(default_factory=list)
    method: str = ""

    @property
    def expected_breaches(self) -> list[str]:
        return [d.name for d in self.dimensions if d.breach_expected]

    @property
    def possible_breaches(self) -> list[str]:
        return [d.name for d in self.dimensions if d.breach_possible]

    @property
    def verdict(self) -> str:
        if self.expected_breaches:
            return (f"doing nothing breaches {', '.join(self.expected_breaches)} within "
                    f"{self.horizon_s / 3600:.1f} h on the central projection")
        if self.possible_breaches:
            return (f"doing nothing stays within limits on the central projection, but the "
                    f"upper bound crosses {', '.join(self.possible_breaches)}")
        return ("doing nothing keeps every tracked dimension within its limit over the "
                "projection horizon")

    def as_dict(self) -> dict:
        return {
            "horizon_s": self.horizon_s,
            "dimensions": [d.as_dict() for d in self.dimensions],
            "slo_state": self.slo_state,
            "thermal_state": self.thermal_state,
            "expected_breaches": self.expected_breaches,
            "possible_breaches": self.possible_breaches,
            "verdict": self.verdict,
            "warnings": self.warnings,
            "method": self.method,
            "note": ("a projection, not a prediction of fact; intervals come from the "
                     "forecaster's own residuals over this history"),
        }

    def to_markdown(self) -> str:
        rows = ["| Dimension | Now | Projected | 80% interval | Limit | State |",
                "|---|---|---|---|---|---|"]
        for d in self.dimensions:
            state = ("breach expected" if d.breach_expected
                     else "breach possible" if d.breach_possible
                     else "within limit" if d.limit else "no limit set")
            limit = f"{d.limit:,.0f}" if d.limit else "—"
            rows.append(f"| {d.name} | {d.current:,.3f} | {d.projected:,.3f} "
                        f"({d.change_pct:+.1f}%) | {d.lower:,.3f}–{d.upper:,.3f} | "
                        f"{limit} | {state} |")
        rows += ["", f"**If nothing changes:** {self.verdict}."]
        for w in self.warnings:
            rows.append(f"- {w}")
        return "\n".join(rows)


def project_no_action(
    *,
    horizon_s: float,
    energy: tuple[Forecast, float, float | None],
    tokens: tuple[Forecast, float, float | None] | None = None,
    thermal: dict | None = None,
    slo_state: str = "unknown",
) -> NoActionProjection:
    """Assemble the projection from forecasts the caller has already produced.

    Each ``(forecast, current_value, limit)`` triple is projected to the last horizon point
    the forecaster produced. Where a forecast could not be made - too little history - the
    dimension is reported as unavailable rather than extrapolated by hand.
    """
    projection = NoActionProjection(
        horizon_s=horizon_s,
        slo_state=slo_state,
        method=("central path and interval from the supplied forecasters; limits are the "
                "operator's declared budgets"),
    )

    def add(name: str, triple, unit: str) -> None:
        forecast, current, limit = triple
        if not forecast.points:
            projection.warnings.append(
                f"{name}: no projection ({forecast.warning or 'no forecast points'})")
            return
        point = forecast.points[-1]
        projection.dimensions.append(ProjectedDimension(
            name=name, current=current, projected=point.value,
            lower=point.lower, upper=point.upper, unit=unit, limit=limit))

    add("energy", energy, "Wh/h")
    if tokens:
        add("tokens", tokens, "tokens/h")

    if thermal:
        peak = thermal.get("projected_peak_c", thermal.get("peak_c", 0.0))
        throttle = thermal.get("throttle_c", 0.0)
        critical = thermal.get("critical_c", 0.0)
        if critical and peak >= critical:
            projection.thermal_state = f"critical: projected peak {peak:.1f} °C ≥ {critical:.0f} °C"
            projection.warnings.append(
                "thermal projection crosses the critical point; this is a facility problem, "
                "not something an energy optimisation should be used to mask")
        elif throttle and peak >= throttle:
            projection.thermal_state = (f"throttling: projected peak {peak:.1f} °C ≥ "
                                        f"{throttle:.0f} °C")
        else:
            projection.thermal_state = f"nominal: projected peak {peak:.1f} °C"

    return projection
