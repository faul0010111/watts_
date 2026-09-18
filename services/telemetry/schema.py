"""Telemetry schema and privacy guarantees.

WATTS is an energy control plane, not a prompt logger. The record below is the *only*
thing that crosses the wire by default: counts, identifiers, timings and hashes. Prompt
and completion text are rejected at the type boundary, not filtered downstream, so a
careless SDK integration fails loudly instead of quietly shipping user content.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Mapping

#: Keys that must never appear in telemetry metadata.
PROHIBITED_FIELDS = frozenset({
    "prompt", "prompts", "messages", "input_text", "completion", "completions",
    "response_text", "output_text", "content", "text", "system_prompt",
    "api_key", "apikey", "authorization", "password", "secret", "token",
    "access_token", "refresh_token", "private_key", "cookie", "session_id",
})

_SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),          # provider-style API keys
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\."),        # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"),
)


class PrivacyViolation(ValueError):
    """Raised when a caller tries to attach content that WATTS must not store."""


def stable_hash(value: str, salt: str = "", length: int = 16) -> str:
    """Salted, truncated SHA-256. Used for prompt/context identity without the text.

    A hash lets WATTS say "this exact context was sent 412 times" without ever holding
    the context. Use a per-tenant salt so hashes cannot be correlated across tenants or
    brute-forced against a dictionary of common prompts.
    """
    digest = hashlib.sha256((salt + "\x1f" + value).encode("utf-8")).hexdigest()
    return digest[:length]


def sanitize_metadata(meta: Mapping[str, Any] | None) -> dict:
    """Validate free-form metadata. Raises on prohibited keys or secret-looking values."""
    if not meta:
        return {}
    clean: dict = {}
    for key, value in meta.items():
        if key.lower() in PROHIBITED_FIELDS:
            raise PrivacyViolation(f"metadata key '{key}' is not allowed in WATTS telemetry")
        if isinstance(value, str):
            if len(value) > 512:
                raise PrivacyViolation(
                    f"metadata value for '{key}' exceeds 512 chars; send a hash, not content")
            for pattern in _SECRET_VALUE_PATTERNS:
                if pattern.search(value):
                    raise PrivacyViolation(f"metadata value for '{key}' looks like a credential")
        if isinstance(value, (dict, list)):
            raise PrivacyViolation(f"metadata value for '{key}' must be a scalar")
        clean[key] = value
    return clean


@dataclass(frozen=True)
class GPUSample:
    """One DCGM/NVML scrape for a single device."""

    device_id: str
    t_s: float
    utilization: float           # 0..1
    memory_used_gb: float
    power_w: float
    temperature_c: float
    sm_clock_mhz: float
    memory_bandwidth_gbps: float = 0.0
    model: str = "unknown"       # e.g. "A100-SXM4-80GB"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class RequestRecord:
    """One LLM request, as seen by the energy control plane."""

    request_id: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    gpu_id: str
    tenant: str
    timestamp: float = field(default_factory=time.time)
    security_policy: str = "default"

    # optional, still non-content
    prompt_hash: str | None = None          # salted hash of the full prompt
    context_prefix_hash: str | None = None  # salted hash of the reusable prefix
    session_id_hash: str | None = None
    task_class: str = "unspecified"         # classification | extraction | reasoning | ...
    batch_size: int = 1
    context_window: int = 0
    success: bool = True
    cache_hit: bool = False
    agent_step: int = 0
    energy_wh: float | None = None          # filled by the energy engine
    energy_provenance: str | None = None
    # Latency decomposition. Queue wait is what an energy optimisation spends when it
    # widens a batching window; service time is what the accelerator actually did. Keeping
    # them apart is what lets WATTS tell "we are slow because we are batching" from
    # "we are slow because the model is bigger".
    queue_wait_ms: float | None = None
    service_ms: float | None = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token counts cannot be negative")
        if self.latency_ms < 0:
            raise ValueError("latency cannot be negative")
        self.metadata = sanitize_metadata(self.metadata)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def context_utilization(self) -> float:
        if not self.context_window:
            return 0.0
        return self.total_tokens / self.context_window

    def as_dict(self) -> dict:
        return asdict(self)

    def canonical_json(self) -> str:
        """Deterministic serialisation, used for signing and replay protection."""
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))

    def signature(self, key: bytes) -> str:
        return hmac.new(key, self.canonical_json().encode("utf-8"), hashlib.sha256).hexdigest()
