"""Energy anomaly engine.

The signal WATTS watches is energy per unit of useful work (Wh per 1k tokens), not raw
power: a rack drawing more power because it is doing more work is not an anomaly.

When the signal breaks, the engine ranks hypotheses against correlated telemetry rather
than asserting a single cause. Each hypothesis carries the evidence that supports it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Sequence


@dataclass(frozen=True)
class CauseHypothesis:
    cause: str
    confidence: float          # 0..1, relative weight among hypotheses
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class EnergyAnomaly:
    detected: bool
    metric: str
    baseline: float
    observed: float
    delta_pct: float
    robust_z: float
    hypotheses: tuple[CauseHypothesis, ...] = ()

    def as_dict(self) -> dict:
        return {
            "detected": self.detected,
            "metric": self.metric,
            "baseline": self.baseline,
            "observed": self.observed,
            "delta_pct": self.delta_pct,
            "robust_z": self.robust_z,
            "hypotheses": [
                {"cause": h.cause, "confidence": h.confidence, "evidence": list(h.evidence)}
                for h in self.hypotheses
            ],
        }


@dataclass
class WindowStats:
    """Aggregated telemetry for one observation window."""

    wh_per_1k_tokens: float
    gpu_util_mean: float = 0.0
    gpu_temp_mean_c: float = 0.0
    sm_clock_mean_mhz: float = 0.0
    batch_size_mean: float = 1.0
    tokens: float = 0.0
    pue: float = 1.0
    model_mix: dict = field(default_factory=dict)
    throughput_tokens_s: float = 0.0


class EnergyAnomalyEngine:
    def __init__(self, z_threshold: float = 3.5, min_history: int = 10) -> None:
        self.z_threshold = z_threshold
        self.min_history = min_history

    def detect(self, history: Sequence[WindowStats], current: WindowStats) -> EnergyAnomaly:
        vals = [w.wh_per_1k_tokens for w in history if w.wh_per_1k_tokens > 0]
        if len(vals) < self.min_history:
            return EnergyAnomaly(False, "wh_per_1k_tokens", 0.0, current.wh_per_1k_tokens, 0.0, 0.0)

        med = median(vals)
        mad = median([abs(v - med) for v in vals]) or 1e-9
        z = 0.6745 * (current.wh_per_1k_tokens - med) / mad
        delta_pct = (current.wh_per_1k_tokens - med) / med * 100.0
        detected = z >= self.z_threshold

        hypotheses: list[CauseHypothesis] = []
        if detected:
            hypotheses = self._attribute(history, current)
        return EnergyAnomaly(detected, "wh_per_1k_tokens", med, current.wh_per_1k_tokens,
                             delta_pct, z, tuple(hypotheses))

    def _attribute(self, history: Sequence[WindowStats], cur: WindowStats) -> list[CauseHypothesis]:
        base = history[-min(len(history), 20):]

        def avg(attr: str) -> float:
            vals = [getattr(w, attr) for w in base]
            return sum(vals) / len(vals) if vals else 0.0

        raw: list[tuple[str, float, list[str]]] = []

        temp_delta = cur.gpu_temp_mean_c - avg("gpu_temp_mean_c")
        clock_delta = cur.sm_clock_mean_mhz - avg("sm_clock_mean_mhz")
        if temp_delta > 4 and clock_delta < -30:
            raw.append(("thermal_throttling", 0.9 + min(temp_delta, 20) / 100,
                        [f"GPU temperature +{temp_delta:.1f} C",
                         f"SM clock {clock_delta:.0f} MHz",
                         "clock falling while temperature rises is the throttling signature"]))

        batch_delta = cur.batch_size_mean - avg("batch_size_mean")
        if batch_delta < -0.2 * max(avg("batch_size_mean"), 1e-9):
            raw.append(("inefficient_batching", 0.7,
                        [f"mean batch size {avg('batch_size_mean'):.1f} -> {cur.batch_size_mean:.1f}",
                         "fixed per-step overhead is amortised over fewer tokens"]))

        base_mix = base[-1].model_mix if base else {}
        if cur.model_mix and base_mix and set(cur.model_mix) != set(base_mix):
            new = sorted(set(cur.model_mix) - set(base_mix))
            if new:
                raw.append(("model_change", 0.8,
                            [f"models not present in the baseline window: {', '.join(new)}"]))

        pue_delta = cur.pue - avg("pue")
        if pue_delta > 0.05:
            raw.append(("cooling_overhead", 0.6,
                        [f"PUE {avg('pue'):.2f} -> {cur.pue:.2f}",
                         "facility overhead grew faster than IT load"]))

        util_delta = cur.gpu_util_mean - avg("gpu_util_mean")
        thr_ratio = (cur.throughput_tokens_s / avg("throughput_tokens_s")) if avg("throughput_tokens_s") else 1.0
        if abs(util_delta) < 0.05 and thr_ratio < 0.9 and temp_delta <= 4:
            raw.append(("gpu_degradation", 0.5,
                        ["utilisation unchanged but throughput dropped",
                         f"throughput ratio {thr_ratio:.2f} vs baseline",
                         "no thermal signature: check ECC errors, power capping, PCIe link width"]))

        tok_ratio = (cur.tokens / avg("tokens")) if avg("tokens") else 1.0
        if tok_ratio < 0.5:
            raw.append(("workload_change", 0.45,
                        [f"token volume at {tok_ratio:.0%} of baseline",
                         "fixed idle power is amortised over less work"]))

        if not raw:
            raw.append(("unexplained", 0.3,
                        ["no correlated GPU, batching, model or facility signal",
                         "escalate to infrastructure: PDU, power capping, noisy neighbour"]))

        total = sum(w for _, w, _ in raw)
        return [CauseHypothesis(c, round(w / total, 3), tuple(e))
                for c, w, e in sorted(raw, key=lambda r: -r[1])]
