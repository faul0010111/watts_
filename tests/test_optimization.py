import unittest

from conftest import ROOT  # noqa: F401
from services.optimization import (
    Budget, BudgetState, BudgetTracker, ChangeWorkflow, CircuitBreaker, EnergyAwareRouter,
    EnergySLO, ModelCandidate, OptimizationEngine, RoutingRequest, WorkflowState,
)
from services.optimization.budgets import evaluate_slo
from services.optimization.recommendations import Confidence, Impact, Recommendation
from services.security import AuditLog, PolicyEngine, Principal
from services.telemetry.schema import RequestRecord
from services.token_engine import load_default_profiles

REG = load_default_profiles()


def candidates():
    return [
        ModelCandidate(REG.get("watts-sim-small"), "on-prem", 120.0, 0.4),
        ModelCandidate(REG.get("watts-sim-medium"), "on-prem", 300.0, 2.0),
        ModelCandidate(REG.get("watts-sim-large"), "public-api", 900.0, 9.0),
    ]


class TestRouter(unittest.TestCase):
    def setUp(self):
        self.router = EnergyAwareRouter(candidates(), PolicyEngine(),
                                        approved_models=tuple(REG.names()),
                                        approved_providers=("on-prem",))

    def test_picks_lowest_energy_that_meets_quality(self):
        d = self.router.route(RoutingRequest("classification", 1, 5_000, 1_000, 50))
        self.assertEqual(d.model, "watts-sim-small")

    def test_never_drops_below_the_quality_floor(self):
        d = self.router.route(RoutingRequest("reasoning", 5, 60_000, 1_000, 500))
        self.assertEqual(d.model, "watts-sim-large")

    def test_latency_slo_removes_candidates(self):
        d = self.router.route(RoutingRequest("reasoning", 5, 10, 1_000, 500))
        self.assertFalse(d.routed)
        self.assertIn("no candidate", d.reason)

    def test_energy_budget_is_a_hard_constraint(self):
        d = self.router.route(RoutingRequest("classification", 1, 5_000, 1_000, 50,
                                             energy_budget_wh=1e-6))
        self.assertFalse(d.routed)

    def test_sensitive_data_blocks_unapproved_provider(self):
        d = self.router.route(RoutingRequest("reasoning", 5, 60_000, 1_000, 500,
                                             data_classification="restricted"))
        self.assertFalse(d.routed)
        self.assertTrue(any("WATTS-SEC-002" in reason for _, reason in d.rejected))

    def test_context_that_does_not_fit_is_rejected(self):
        d = self.router.route(RoutingRequest("classification", 1, 5_000, 20_000, 50))
        self.assertNotEqual(d.model, "watts-sim-small")

    def test_rejections_are_explained(self):
        d = self.router.route(RoutingRequest("reasoning", 5, 60_000, 1_000, 500))
        self.assertTrue(all(reason for _, reason in d.rejected))


def records(n=100, latency=500.0, success=True, energy=0.1):
    return [RequestRecord(request_id=f"r{i}", model="m", provider="p", input_tokens=1000,
                          output_tokens=100, latency_ms=latency, gpu_id="g", tenant="t",
                          timestamp=float(i), success=success, energy_wh=energy)
            for i in range(n)]


class TestSLOAndBudgets(unittest.TestCase):
    def test_slo_met(self):
        slo = EnergySLO("s", 1_000, 0.99, 0.02, 1.0)
        self.assertEqual(evaluate_slo(slo, records()).violations, [])

    def test_energy_violation_is_not_a_reliability_violation(self):
        slo = EnergySLO("s", 1_000, 0.99, 0.02, 0.01)
        report = evaluate_slo(slo, records())
        self.assertTrue(report.violations)
        self.assertEqual(report.reliability_violations, [])

    def test_latency_violation_is_a_reliability_violation(self):
        slo = EnergySLO("s", 100, 0.99, 0.02, 1.0)
        self.assertTrue(evaluate_slo(slo, records()).reliability_violations)

    def test_budget_projects_to_hourly(self):
        tracker = BudgetTracker({"w": Budget("w", energy_wh_per_hour=100.0)})
        state = BudgetState("w", window_s=360.0, energy_wh=20.0)   # 200 Wh/h projected
        result = tracker.evaluate(state)
        self.assertEqual(len(result["breaches"]), 1)
        self.assertAlmostEqual(result["breaches"][0]["projected"], 200.0)

    def test_untracked_workload_is_reported_as_untracked(self):
        self.assertFalse(BudgetTracker({}).evaluate(BudgetState("w", 60.0))["tracked"])


def recommendation(**kw):
    base = dict(id="rec-1", title="t", action="increase_batch_window", rationale="r",
                impact=Impact(-10.0, 20.0, "none", "none"), confidence=Confidence.MEDIUM,
                evidence=("e",))
    base.update(kw)
    return Recommendation(**base)


class TestWorkflow(unittest.TestCase):
    def setUp(self):
        self.audit = AuditLog()
        self.workflow = ChangeWorkflow(PolicyEngine(), self.audit)
        self.assistant = Principal("watts-assistant", ("watts-assistant",))
        self.sre = Principal("sre:ana", ("sre",))
        self.slo = evaluate_slo(EnergySLO("s", 5_000, 0.99, 0.02, 1.0), records())

    def test_proposal_waits_for_a_human(self):
        state, _ = self.workflow.submit(recommendation(), principal=self.assistant, slo=self.slo)
        self.assertIs(state, WorkflowState.AWAITING_APPROVAL)

    def test_blocked_when_reliability_slo_already_broken(self):
        broken = evaluate_slo(EnergySLO("s", 10, 0.99, 0.02, 1.0), records())
        state, reason = self.workflow.submit(recommendation(), principal=self.assistant, slo=broken)
        self.assertIs(state, WorkflowState.SLO_BLOCKED)

    def test_control_weakening_is_blocked(self):
        rec = recommendation(impact=Impact(-100.0, 0.0, "none", "weakens control"))
        state, reason = self.workflow.submit(rec, principal=self.assistant, slo=self.slo)
        self.assertIs(state, WorkflowState.POLICY_BLOCKED)
        self.assertIn("WATTS-SEC-003", reason)

    def test_assistant_cannot_execute(self):
        with self.assertRaises(PermissionError):
            self.workflow.execute(recommendation(), self.assistant, lambda r: True)

    def test_failed_apply_rolls_back(self):
        state = self.workflow.execute(recommendation(), self.sre, lambda r: False)
        self.assertIs(state, WorkflowState.ROLLED_BACK)

    def test_raising_apply_does_not_propagate(self):
        def boom(rec):
            raise RuntimeError("controller unreachable")
        state = self.workflow.execute(recommendation(), self.sre, boom)
        self.assertIs(state, WorkflowState.ROLLED_BACK)

    def test_rate_limit_stops_change_storms(self):
        wf = ChangeWorkflow(PolicyEngine(), AuditLog(), rate_limit_per_hour=2)
        for _ in range(2):
            wf.execute(recommendation(), self.sre, lambda r: True, now=0.0)
        self.assertIs(wf.execute(recommendation(), self.sre, lambda r: True, now=1.0),
                      WorkflowState.FAILED)

    def test_every_step_is_audited(self):
        self.workflow.submit(recommendation(), principal=self.assistant, slo=self.slo)
        ok, _ = self.audit.verify()
        self.assertTrue(ok)
        self.assertGreaterEqual(len(self.audit), 2)


class TestCircuitBreaker(unittest.TestCase):
    def test_opens_after_threshold_and_closes_after_cooldown(self):
        breaker = CircuitBreaker(failure_threshold=2, cooldown_s=100)
        breaker.record_failure(0.0)
        self.assertTrue(breaker.allow(0.0))
        breaker.record_failure(1.0)
        self.assertFalse(breaker.allow(2.0))
        self.assertTrue(breaker.allow(200.0))

    def test_success_resets(self):
        breaker = CircuitBreaker(failure_threshold=2)
        breaker.record_failure(0.0)
        breaker.record_success()
        self.assertEqual(breaker.failures, 0)


class TestOptimizationEngine(unittest.TestCase):
    def test_recommendations_carry_evidence_and_impact(self):
        from services.token_engine import TokenEfficiencyEngine
        recs = OptimizationEngine().from_efficiency(
            TokenEfficiencyEngine().analyze(records(success=False)))
        self.assertTrue(recs)
        for rec in recs:
            self.assertTrue(rec.evidence)
            self.assertIsNotNone(rec.impact.security)

    def test_ranked_by_energy_impact(self):
        from services.token_engine import TokenEfficiencyEngine
        recs = OptimizationEngine().from_efficiency(
            TokenEfficiencyEngine().analyze(records(success=False)))
        energies = [r.impact.energy_wh for r in recs]
        self.assertEqual(energies, sorted(energies))


if __name__ == "__main__":
    unittest.main()
