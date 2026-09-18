"""WATTS LLM telemetry SDK (Python).

Wrap an LLM call and WATTS records what it needs: counts, timings, identifiers, hashes.
It never sees prompt or completion text - the API takes *token counts*, not messages, so
an integration cannot leak content even by mistake.

    watts = WattsTelemetry(TelemetryConfig(endpoint="http://watts-gateway:8080",
                                           workload="checkout-assistant",
                                           hmac_key=os.environb[b"WATTS_KEY"],
                                           tenant="acme"))

    with watts.track(model="gpt-oss-20b", provider="self-hosted",
                     task_class="classification") as span:
        response = my_llm.generate(messages)
        span.set_tokens(input_tokens=response.usage.input, output_tokens=response.usage.output)
        span.set_prompt_identity(prompt_key, prefix_key)   # hashed locally, never sent raw

The span computes latency, signs the record and hands it to the transport. The default
transport prints to stderr in dry-run mode so a first integration cannot accidentally
ship anything anywhere.
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from enum import Enum
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator

if __package__ in (None, ""):  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.telemetry.schema import PrivacyViolation, RequestRecord, stable_hash


class PrivacyMode(str, Enum):
    """How much identity leaves the process. Secure by default, on purpose.

    ``STRICT`` is the default and the only mode most deployments need: counts, timings and
    per-tenant salted hashes. ``METADATA`` additionally permits a small set of scalar
    labels the caller supplies, still validated against the prohibited-field list. There is
    no mode that sends prompt or completion text, because the record type has no field for
    it - privacy here is a property of the schema, not of a setting someone can flip.
    """

    STRICT = "strict"        # counts, timings, hashes. Nothing else.
    METADATA = "metadata"    # plus caller-supplied scalar labels, validated
    NO_HASHES = "no-hashes"  # counts and timings only; disables duplicate detection


@dataclass
class TelemetryConfig:
    workload: str
    tenant: str = "default"
    endpoint: str | None = None
    hmac_key: bytes = b""
    hash_salt: str = ""
    security_policy: str = "default"
    dry_run: bool = True
    sample_rate: float = 1.0
    privacy_mode: PrivacyMode = PrivacyMode.STRICT

    def __post_init__(self) -> None:
        self.privacy_mode = PrivacyMode(self.privacy_mode)
        if not self.hash_salt and self.privacy_mode is not PrivacyMode.NO_HASHES:
            # Falling back to the tenant name keeps hashes from being globally
            # correlatable, but it is not a secret. Say so once, loudly, at construction.
            warnings.warn(
                "no hash_salt configured: falling back to the tenant name. Hashes are then "
                "predictable to anyone who knows the tenant. Set a per-tenant secret salt "
                "before sending telemetry from production.",
                stacklevel=2)

    @property
    def sends_hashes(self) -> bool:
        return self.privacy_mode is not PrivacyMode.NO_HASHES


class _Span:
    def __init__(self, parent: "WattsTelemetry", model: str, provider: str,
                 task_class: str, gpu_id: str, context_window: int, agent_step: int,
                 session_id: str | None) -> None:
        self.parent = parent
        self.record_id = f"req-{uuid.uuid4().hex[:12]}"
        self.model, self.provider = model, provider
        self.task_class, self.gpu_id = task_class, gpu_id
        self.context_window, self.agent_step = context_window, agent_step
        self.session_id_hash = (stable_hash(session_id, parent.config.hash_salt)
                                if session_id else None)
        self.input_tokens = 0
        self.output_tokens = 0
        self.batch_size = 1
        self.success = True
        self.cache_hit = False
        self.prompt_hash: str | None = None
        self.context_prefix_hash: str | None = None
        self.energy_wh: float | None = None
        self.metadata: dict = {}
        self.privacy_suppressed = False
        self._start = time.perf_counter()

    def set_labels(self, **labels) -> "_Span":
        """Attach scalar labels. Permitted only under PrivacyMode.METADATA.

        Labels still pass the gateway's prohibited-field and credential checks; this is an
        additional door, not an unlocked one.
        """
        mode = self.parent.config.privacy_mode
        if mode is not PrivacyMode.METADATA:
            raise PrivacyViolation(
                f"labels require privacy_mode={PrivacyMode.METADATA.value}; this client runs "
                f"in {mode.value}. Raise the mode deliberately or drop the labels.")
        self.metadata.update(labels)
        return self

    def set_tokens(self, input_tokens: int, output_tokens: int) -> "_Span":
        self.input_tokens, self.output_tokens = int(input_tokens), int(output_tokens)
        return self

    def set_prompt_identity(self, prompt: str | None = None,
                            context_prefix: str | None = None) -> "_Span":
        """Hash locally. The text never leaves the process.

        Under ``PrivacyMode.NO_HASHES`` nothing is derived from the prompt at all: the call
        becomes a no-op, and WATTS loses duplicate and prefix detection for this workload.
        That trade is the caller's to make, and it is made explicit rather than silent.
        """
        cfg = self.parent.config
        if not cfg.sends_hashes:
            self.privacy_suppressed = True
            return self
        salt = cfg.hash_salt or cfg.tenant
        if prompt is not None:
            self.prompt_hash = stable_hash(prompt, salt)
        if context_prefix is not None:
            self.context_prefix_hash = stable_hash(context_prefix, salt)
        return self

    def set_batch(self, batch_size: int) -> "_Span":
        self.batch_size = max(1, int(batch_size))
        return self

    def set_energy(self, wh: float) -> "_Span":
        """Attach a measured per-request energy figure if the serving layer has one."""
        self.energy_wh = float(wh)
        return self

    def mark_failed(self) -> "_Span":
        self.success = False
        return self

    def mark_cache_hit(self) -> "_Span":
        self.cache_hit = True
        return self

    def to_record(self) -> RequestRecord:
        cfg = self.parent.config
        return RequestRecord(
            request_id=self.record_id, model=self.model, provider=self.provider,
            input_tokens=self.input_tokens, output_tokens=self.output_tokens,
            latency_ms=(time.perf_counter() - self._start) * 1000.0,
            gpu_id=self.gpu_id, tenant=cfg.tenant, security_policy=cfg.security_policy,
            prompt_hash=self.prompt_hash, context_prefix_hash=self.context_prefix_hash,
            session_id_hash=self.session_id_hash, task_class=self.task_class,
            batch_size=self.batch_size, context_window=self.context_window,
            success=self.success, cache_hit=self.cache_hit, agent_step=self.agent_step,
            energy_wh=self.energy_wh,
            metadata=dict(self.metadata),
        )


class WattsTelemetry:
    def __init__(self, config: TelemetryConfig,
                 transport: Callable[[dict], None] | None = None) -> None:
        self.config = config
        self.transport = transport or self._default_transport
        self.sent = 0
        self.dropped = 0

    @contextmanager
    def track(self, *, model: str, provider: str, task_class: str = "unspecified",
              gpu_id: str = "unknown", context_window: int = 0, agent_step: int = 0,
              session_id: str | None = None) -> Iterator[_Span]:
        span = _Span(self, model, provider, task_class, gpu_id, context_window,
                     agent_step, session_id)
        try:
            yield span
        except Exception:
            span.mark_failed()
            raise
        finally:
            self._emit(span)

    def _emit(self, span: _Span) -> None:
        import random
        if random.random() > self.config.sample_rate:
            self.dropped += 1
            return
        record = span.to_record()
        payload = {
            "workload": self.config.workload,
            "record": record.as_dict(),
            "signature": record.signature(self.config.hmac_key) if self.config.hmac_key else "",
        }
        self.transport(payload)
        self.sent += 1

    def _default_transport(self, payload: dict) -> None:
        if self.config.dry_run or not self.config.endpoint:
            print(f"[watts:dry-run] {json.dumps(payload['record'], sort_keys=True)}",
                  file=sys.stderr)
            return
        import urllib.request  # local import: the SDK has no hard dependency on networking
        req = urllib.request.Request(
            f"{self.config.endpoint.rstrip('/')}/v1/telemetry",
            data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "X-WATTS-Signature": payload["signature"],
                     "X-WATTS-Workload": payload["workload"]})
        urllib.request.urlopen(req, timeout=2.0).read()


def track_llm_call(watts: WattsTelemetry, **span_kwargs):
    """Decorator form. The wrapped function must return ``(result, input_tokens, output_tokens)``."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            with watts.track(**span_kwargs) as span:
                result, in_tok, out_tok = fn(*args, **kwargs)
                span.set_tokens(in_tok, out_tok)
                return result
        return wrapper
    return decorator
