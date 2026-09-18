"""Energy-aware model router.

The router does not pick the cheapest model. It filters on hard constraints first
(security policy, quality floor, latency SLO, context capacity) and only then optimises
energy inside the surviving set. If nothing survives, it fails closed with the reason -
it never relaxes a constraint to find an answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..security.policy import PolicyEngine, PolicyInput
from ..token_engine.profiles import ModelProfile


@dataclass(frozen=True)
class ModelCandidate:
    profile: ModelProfile
    provider: str
    p95_latency_ms_per_1k_output: float
    wh_per_1k_tokens: float              # calibrated or simulated; carries its own provenance
    provenance: str = "simulated"
    available: bool = True


@dataclass(frozen=True)
class RoutingRequest:
    task_class: str
    min_quality_tier: int
    latency_slo_ms: float
    expected_input_tokens: int
    expected_output_tokens: int
    data_classification: str = "public"
    energy_budget_wh: float | None = None
    environment: str = "production"
    tenant: str = "default"
    criticality: str = "normal"


@dataclass(frozen=True)
class RoutingDecision:
    model: str | None
    provider: str | None
    estimated_energy_wh: float
    estimated_latency_ms: float
    rejected: tuple[tuple[str, str], ...]   # (model, reason)
    reason: str
    energy_saving_vs_default_wh: float = 0.0

    @property
    def routed(self) -> bool:
        return self.model is not None

    def as_dict(self) -> dict:
        return {
            "model": self.model, "provider": self.provider,
            "estimated_energy_wh": self.estimated_energy_wh,
            "estimated_latency_ms": self.estimated_latency_ms,
            "rejected": [{"model": m, "reason": r} for m, r in self.rejected],
            "reason": self.reason,
            "energy_saving_vs_default_wh": self.energy_saving_vs_default_wh,
        }


class EnergyAwareRouter:
    def __init__(self, candidates: list[ModelCandidate], policy: PolicyEngine,
                 approved_models: tuple[str, ...] = (), approved_providers: tuple[str, ...] = (),
                 default_model: str | None = None) -> None:
        self.candidates = candidates
        self.policy = policy
        self.approved_models = approved_models
        self.approved_providers = approved_providers
        self.default_model = default_model

    def _estimate(self, c: ModelCandidate, req: RoutingRequest) -> tuple[float, float]:
        tokens = req.expected_input_tokens + req.expected_output_tokens
        energy = c.wh_per_1k_tokens * tokens / 1000.0
        latency = c.p95_latency_ms_per_1k_output * max(req.expected_output_tokens, 1) / 1000.0
        return energy, latency

    def route(self, req: RoutingRequest) -> RoutingDecision:
        rejected: list[tuple[str, str]] = []
        viable: list[tuple[ModelCandidate, float, float]] = []

        for c in self.candidates:
            name = c.profile.name
            if not c.available:
                rejected.append((name, "candidate unavailable"))
                continue
            if c.profile.quality_tier < req.min_quality_tier:
                rejected.append((name, f"quality tier {c.profile.quality_tier} below required "
                                       f"{req.min_quality_tier}"))
                continue
            if req.expected_input_tokens > c.profile.max_context:
                rejected.append((name, "context does not fit"))
                continue

            decision = self.policy.evaluate(PolicyInput(
                action="route_workload",
                tenant=req.tenant,
                environment=req.environment,
                model=name,
                provider=c.provider,
                data_classification=req.data_classification,
                approved_models=self.approved_models,
                approved_providers=self.approved_providers,
                workload_criticality=req.criticality,
            ))
            if not decision.allow:
                rejected.append((name, f"{decision.rule_id}: {decision.reason}"))
                continue

            energy, latency = self._estimate(c, req)
            if latency > req.latency_slo_ms:
                rejected.append((name, f"estimated p95 {latency:.0f} ms exceeds SLO "
                                       f"{req.latency_slo_ms:.0f} ms"))
                continue
            if req.energy_budget_wh is not None and energy > req.energy_budget_wh:
                rejected.append((name, f"estimated {energy:.4f} Wh exceeds budget "
                                       f"{req.energy_budget_wh:.4f} Wh"))
                continue
            viable.append((c, energy, latency))

        if not viable:
            return RoutingDecision(None, None, 0.0, 0.0, tuple(rejected),
                                   "no candidate satisfies the hard constraints; "
                                   "request must be rejected or constraints revisited")

        # Among viable candidates: lowest energy, tie-broken by higher quality then lower latency.
        best = min(viable, key=lambda t: (round(t[1], 9), -t[0].profile.quality_tier, t[2]))
        c, energy, latency = best

        saving = 0.0
        if self.default_model:
            for cand, e, _ in viable:
                if cand.profile.name == self.default_model:
                    saving = e - energy
                    break

        return RoutingDecision(
            c.profile.name, c.provider, energy, latency, tuple(rejected),
            f"lowest estimated energy among {len(viable)} candidates that satisfy quality tier "
            f">= {req.min_quality_tier}, latency SLO and security policy",
            saving,
        )
