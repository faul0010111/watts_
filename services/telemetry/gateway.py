"""Telemetry gateway: the trust boundary of WATTS.

Everything downstream (energy engine, optimisation, forecasting) treats telemetry as
ground truth, so the gateway is where telemetry has to earn that status. It enforces:

* authenticity  - HMAC signature per record with a per-workload key;
* freshness     - bounded clock skew;
* replay safety - a seen-request-id window;
* plausibility  - physical bounds, so a workload cannot claim 5 W while saturating an
                  80 GB accelerator and win the efficiency leaderboard;
* privacy       - schema validation rejects content-bearing fields.

Rejected records are counted and surfaced; they are never silently dropped.
"""
from __future__ import annotations

import hmac
import time
from collections import OrderedDict, Counter
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .schema import RequestRecord, PrivacyViolation


class RejectReason(str, Enum):
    BAD_SIGNATURE = "bad_signature"
    UNKNOWN_WORKLOAD = "unknown_workload"
    REPLAY = "replay"
    CLOCK_SKEW = "clock_skew"
    IMPLAUSIBLE_ENERGY = "implausible_energy"
    IMPLAUSIBLE_TOKENS = "implausible_tokens"
    PRIVACY = "privacy_violation"


@dataclass(frozen=True)
class IngestResult:
    accepted: bool
    reason: RejectReason | None = None
    detail: str = ""


class TelemetryGateway:
    def __init__(
        self,
        workload_keys: Mapping[str, bytes],
        *,
        max_skew_s: float = 300.0,
        replay_window: int = 100_000,
        max_device_power_w: float = 1_200.0,
        min_wh_per_1k_tokens: float = 1e-4,
        max_wh_per_1k_tokens: float = 1_000.0,
    ) -> None:
        self.workload_keys = dict(workload_keys)
        self.max_skew_s = max_skew_s
        self.replay_window = replay_window
        self.max_device_power_w = max_device_power_w
        self.min_wh_per_1k = min_wh_per_1k_tokens
        self.max_wh_per_1k = max_wh_per_1k_tokens
        self._seen: OrderedDict[str, float] = OrderedDict()
        self.accepted: list[RequestRecord] = []
        self.rejections: Counter = Counter()

    def ingest(self, record: RequestRecord, signature: str, workload: str,
               now: float | None = None) -> IngestResult:
        now = time.time() if now is None else now

        key = self.workload_keys.get(workload)
        if key is None:
            return self._reject(RejectReason.UNKNOWN_WORKLOAD, workload)

        try:
            expected = record.signature(key)
        except PrivacyViolation as exc:  # pragma: no cover - schema guards on construction
            return self._reject(RejectReason.PRIVACY, str(exc))

        if not hmac.compare_digest(expected, signature):
            return self._reject(RejectReason.BAD_SIGNATURE, record.request_id)

        if abs(now - record.timestamp) > self.max_skew_s:
            return self._reject(RejectReason.CLOCK_SKEW,
                                f"{abs(now - record.timestamp):.0f}s from gateway clock")

        if record.request_id in self._seen:
            return self._reject(RejectReason.REPLAY, record.request_id)

        plausibility = self._check_plausibility(record)
        if plausibility is not None:
            return self._reject(*plausibility)

        self._seen[record.request_id] = now
        while len(self._seen) > self.replay_window:
            self._seen.popitem(last=False)
        self.accepted.append(record)
        return IngestResult(True)

    def _check_plausibility(self, r: RequestRecord) -> tuple[RejectReason, str] | None:
        if r.total_tokens == 0 and r.latency_ms > 0 and r.success:
            return RejectReason.IMPLAUSIBLE_TOKENS, "successful request with zero tokens"
        if r.context_window and r.input_tokens > r.context_window:
            return RejectReason.IMPLAUSIBLE_TOKENS, "input tokens exceed declared context window"
        if r.energy_wh is not None:
            if r.energy_wh < 0:
                return RejectReason.IMPLAUSIBLE_ENERGY, "negative energy"
            implied_w = r.energy_wh * 3600.0 / max(r.latency_ms / 1000.0, 1e-6)
            if implied_w > self.max_device_power_w * max(1, r.batch_size) * 8:
                return RejectReason.IMPLAUSIBLE_ENERGY, f"implies {implied_w:.0f} W on one device"
            if r.total_tokens:
                per_1k = r.energy_wh / (r.total_tokens / 1000.0)
                if per_1k < self.min_wh_per_1k or per_1k > self.max_wh_per_1k:
                    return RejectReason.IMPLAUSIBLE_ENERGY, f"{per_1k:.4g} Wh/1k tokens out of bounds"
        return None

    def _reject(self, reason: RejectReason, detail: str = "") -> IngestResult:
        self.rejections[reason.value] += 1
        return IngestResult(False, reason, detail)

    def stats(self) -> dict:
        return {
            "accepted": len(self.accepted),
            "rejected": sum(self.rejections.values()),
            "rejections_by_reason": dict(self.rejections),
        }
