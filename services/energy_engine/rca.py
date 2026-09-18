"""Root cause analysis 2.0.

The failure mode this module exists to prevent: a correlation, stated confidently, that
sends an on-call engineer to the wrong team at three in the morning.

So causes are graded, not ranked:

* **primary** - one hypothesis is clearly best supported, and its mechanism explains the
  size of the change, not merely its direction;
* **contributing** - supported, but not sufficient on its own;
* **possible** - consistent with the data, and with several other explanations too;
* **insufficient evidence** - the signals that would separate the hypotheses were not
  collected.

The last category is the important one. "We do not know, and here is the telemetry that
would tell us" is a useful answer. A confident wrong answer is not.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .anomaly import CauseHypothesis, EnergyAnomaly
from .model import Provenance

# What would confirm each mechanism, beyond the correlation that suggested it. These are
# the checks a human runs before believing the analysis.
VALIDATION_STEPS: dict[str, str] = {
    "thermal_throttling": "inlet temperature and airflow at the affected rack; DCGM clock "
                          "throttle reasons (SW_THERMAL / HW_THERMAL)",
    "inefficient_batching": "serving queue depth and batch size distribution over the window",
    "model_change": "deployment and routing configuration history for the window",
    "cooling_overhead": "facility telemetry: CRAC state, chilled water temperature, metered "
                        "facility power",
    "gpu_degradation": "ECC error counters, PCIe link width, power-cap state, per-device "
                       "throughput against its fleet peers",
    "workload_change": "request volume and task mix by tenant",
    "unexplained": "nothing correlated; capture a full-resolution window and compare device "
                   "by device",
}


@dataclass(frozen=True)
class GradedCause:
    cause: str
    grade: str                     # primary | contributing | possible | insufficient_evidence
    confidence: float
    evidence: tuple[str, ...]
    validation_required: str
    mechanism: str = ""

    def as_dict(self) -> dict:
        return {
            "cause": self.cause, "grade": self.grade, "confidence": self.confidence,
            "evidence": list(self.evidence), "validation_required": self.validation_required,
            "mechanism": self.mechanism,
        }


@dataclass
class RootCauseAnalysis:
    anomaly: EnergyAnomaly
    causes: list[GradedCause] = field(default_factory=list)
    provenance: Provenance = Provenance.SIMULATED
    method: str = ""

    @property
    def primary(self) -> GradedCause | None:
        return next((c for c in self.causes if c.grade == "primary"), None)

    @property
    def contributing(self) -> list[GradedCause]:
        return [c for c in self.causes if c.grade == "contributing"]

    @property
    def possible(self) -> list[GradedCause]:
        return [c for c in self.causes if c.grade == "possible"]

    @property
    def conclusive(self) -> bool:
        return self.primary is not None

    def statement(self) -> str:
        """One sentence, with its uncertainty and its provenance attached."""
        if not self.anomaly.detected:
            return "no anomaly detected in this window"
        qualifier = ("within simulation" if self.provenance is Provenance.SIMULATED
                     else "from telemetry")
        primary = self.primary
        if primary is None:
            contested = self.contributing + self.possible
            if contested:
                names = ", ".join(c.cause.replace("_", " ") for c in contested[:3])
                return (f"no single cause is best supported {qualifier}; {names} are each "
                        f"consistent with the data and cannot be separated with the signals "
                        f"collected")
            return (f"energy per unit of work moved {self.anomaly.delta_pct:+.1f}% and "
                    f"nothing in the collected telemetry correlates with it")
        return (f"primary cause {qualifier}: {primary.cause.replace('_', ' ')} "
                f"(confidence {primary.confidence:.2f}). {primary.mechanism} "
                f"Validation required: {primary.validation_required}.")

    def as_dict(self) -> dict:
        return {
            "anomaly": self.anomaly.as_dict(),
            "statement": self.statement(),
            "primary_cause": self.primary.as_dict() if self.primary else None,
            "contributing_factors": [c.as_dict() for c in self.contributing],
            "possible_causes": [c.as_dict() for c in self.possible],
            "insufficient_evidence": [c.as_dict() for c in self.causes
                                      if c.grade == "insufficient_evidence"],
            "conclusive": self.conclusive,
            "provenance": self.provenance.value,
            "method": self.method,
            "note": ("correlation is graded, never promoted to causation automatically; "
                     "every cause names the telemetry that would confirm it"),
        }

    def to_markdown(self) -> str:
        lines = ["### Root cause analysis", "", self.statement(), ""]
        if self.contributing:
            lines.append("**Contributing factors**")
            lines += [f"- {c.cause.replace('_', ' ')} ({c.confidence:.2f}): {c.evidence[0]}"
                      for c in self.contributing]
            lines.append("")
        if self.possible:
            lines.append("**Possible, not separable with the signals collected**")
            lines += [f"- {c.cause.replace('_', ' ')} ({c.confidence:.2f}): "
                      f"validate with {c.validation_required}" for c in self.possible]
            lines.append("")
        return "\n".join(lines)


def analyse_causes(anomaly: EnergyAnomaly, *,
                   provenance: Provenance = Provenance.SIMULATED,
                   primary_margin: float = 0.20,
                   primary_minimum: float = 0.40) -> RootCauseAnalysis:
    """Grade the anomaly engine's hypotheses instead of ranking them.

    A hypothesis becomes *primary* only when it is both well supported in absolute terms
    and clearly ahead of the runner-up. Two hypotheses at 0.45 and 0.42 are not a winner
    and a loser; they are an unresolved question, and saying so is the honest output.
    """
    hypotheses: list[CauseHypothesis] = sorted(anomaly.hypotheses,
                                               key=lambda h: -h.confidence)
    causes: list[GradedCause] = []

    if not hypotheses:
        causes.append(GradedCause(
            "unexplained", "insufficient_evidence", 0.0,
            ("no collected signal correlated with the change in energy per unit of work",),
            VALIDATION_STEPS["unexplained"],
            "The change is real but nothing in the telemetry explains it."))
        return RootCauseAnalysis(anomaly, causes, provenance,
                                 method="no hypotheses generated")

    top = hypotheses[0]
    runner_up = hypotheses[1].confidence if len(hypotheses) > 1 else 0.0
    clear_lead = (top.confidence - runner_up) >= primary_margin
    well_supported = top.confidence >= primary_minimum

    for i, h in enumerate(hypotheses):
        if i == 0 and clear_lead and well_supported:
            grade = "primary"
        elif h.confidence >= primary_minimum:
            grade = "contributing" if i > 0 or not clear_lead else "primary"
        elif h.confidence >= 0.15:
            grade = "possible"
        else:
            grade = "insufficient_evidence"
        causes.append(GradedCause(
            cause=h.cause,
            grade=grade,
            confidence=h.confidence,
            evidence=h.evidence,
            validation_required=VALIDATION_STEPS.get(h.cause, "corroborating telemetry"),
            mechanism=_MECHANISMS.get(h.cause, ""),
        ))

    # When the leader has no clear lead, nothing is primary: the field is contested.
    if not (clear_lead and well_supported):
        causes = [GradedCause(**{**c.__dict__,
                                "grade": "possible" if c.grade == "primary" else c.grade})
                  for c in causes]

    method = (f"hypotheses graded by margin: primary requires confidence ≥ {primary_minimum:.2f} "
              f"and a lead of ≥ {primary_margin:.2f} over the runner-up "
              f"(top {top.confidence:.2f}, runner-up {runner_up:.2f})")
    return RootCauseAnalysis(anomaly, causes, provenance, method=method)


_MECHANISMS: dict[str, str] = {
    "thermal_throttling": ("Higher temperature raises leakage current and lowers the clock, "
                           "so the same work takes more energy and more time."),
    "inefficient_batching": ("Smaller batches amortise the fixed per-step cost of decoding "
                             "over fewer tokens."),
    "model_change": ("A different model mix changes the compute per token directly."),
    "cooling_overhead": ("Facility power rose while IT power did not, so the overhead ratio "
                         "moved rather than the workload."),
    "gpu_degradation": ("Throughput fell without a thermal or workload explanation, which "
                        "points at the device rather than its environment."),
    "workload_change": ("Fixed idle power is being amortised over less useful work."),
}
