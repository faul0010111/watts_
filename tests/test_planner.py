import unittest

from conftest import ROOT  # noqa: F401
from services.optimization import (
    CandidateChange, OptimizationPlanner, QualityFloor, QualityObservation, QualityRegistry,
    WorkloadConstraints,
)
from services.optimization.no_action import project_no_action
from services.forecasting import HoltForecaster
from services.security import PolicyEngine
from services.token_engine import TokenEfficiencyEngine
from services.telemetry.schema import RequestRecord

CONSTRAINTS = WorkloadConstraints("w", p95_latency_ms=5_000)


def metrics(energy: float, p95: float = 1_000.0, **kw) -> dict:
    base = {"facility_wh_per_1k_tokens": energy, "p95_latency_ms": p95, "error_rate": 0.0,
            "provenance": "simulated", "exceeded_critical_temp": False, "seeds": 1}
    base.update(kw)
    return base


class StubEvaluator:
    """Each knob multiplies energy, and the effects overlap - as they do in reality."""

    def __init__(self, base=0.10):
        self.base = base
        self.calls = []

    def __call__(self, knobs: dict) -> dict:
        self.calls.append(dict(knobs))
        energy = self.base
        if knobs.get("prefix_cache"):
            energy *= 0.80
        if knobs.get("response_cache"):
            energy *= 0.85 if not knobs.get("prefix_cache") else 0.95   # overlapping saving
        if knobs.get("routing"):
            energy *= 0.60
        p95 = 1_000.0 + (400.0 if knobs.get("max_wait_s", 0) >= 0.2 else 0.0)
        return metrics(energy, p95)


def candidate(name="Enable prefix caching", action="enable_prefix_cache", **kw) -> CandidateChange:
    return CandidateChange("c1", name, action, kw.pop("knobs", {"prefix_cache": True}),
                           "rationale", ("evidence",), **kw)


class TestCandidateGeneration(unittest.TestCase):
    def setUp(self):
        self.planner = OptimizationPlanner(StubEvaluator(), CONSTRAINTS)

    def test_no_evidence_means_no_candidate(self):
        empty = TokenEfficiencyEngine().analyze([])
        state = {"mean_batch_size": 16, "latency_headroom_ms": 0, "routing_enabled": True}
        self.assertEqual(self.planner.generate_candidates(state, empty), [])

    def test_batching_proposed_only_with_headroom(self):
        state = {"mean_batch_size": 2, "latency_headroom_ms": 5, "routing_enabled": True}
        actions = [c.action for c in self.planner.generate_candidates(state, None)]
        self.assertNotIn("increase_batch_window", actions)

    def test_batching_proposed_when_headroom_exists(self):
        state = {"mean_batch_size": 2, "latency_headroom_ms": 2_000, "routing_enabled": True}
        actions = [c.action for c in self.planner.generate_candidates(state, None)]
        self.assertIn("increase_batch_window", actions)


class TestEvaluation(unittest.TestCase):
    def setUp(self):
        self.planner = OptimizationPlanner(StubEvaluator(), CONSTRAINTS, PolicyEngine())

    def test_candidate_is_simulated_not_asserted(self):
        evaluator = StubEvaluator()
        planner = OptimizationPlanner(evaluator, CONSTRAINTS)
        planner.evaluate(candidate(), {}, metrics(0.10), 4_000)
        self.assertTrue(evaluator.calls)

    def test_evaluation_covers_all_five_axes(self):
        e = self.planner.evaluate(candidate(), {}, metrics(0.10), 4_000)
        for field in ("energy_delta_pct", "latency_delta_ms", "quality_delta",
                      "security_impact", "availability_impact"):
            self.assertTrue(hasattr(e, field))

    def test_control_weakening_is_blocked_however_large_the_saving(self):
        e = self.planner.evaluate(candidate(weakens_control=True), {}, metrics(1.0), 4_000)
        self.assertFalse(e.admissible)
        self.assertIn("WATTS-SEC-003", e.reasons[0])

    def test_latency_beyond_headroom_is_blocked(self):
        e = self.planner.evaluate(
            candidate(knobs={"max_wait_s": 0.2}, expected_latency_cost_ms=400.0),
            {}, metrics(0.10), 40.0)
        self.assertFalse(e.admissible)

    def test_change_below_the_threshold_is_not_worth_making(self):
        planner = OptimizationPlanner(lambda knobs: metrics(0.0999), CONSTRAINTS)
        e = planner.evaluate(candidate(), {}, metrics(0.10), 4_000)
        self.assertFalse(e.admissible)
        self.assertIn("threshold", e.reasons[-1])

    def test_routing_without_a_quality_observation_is_blocked(self):
        constraints = WorkloadConstraints("w", 5_000,
                                          quality_floor=QualityFloor("task_success_rate", 0.9))
        planner = OptimizationPlanner(StubEvaluator(), constraints, PolicyEngine(),
                                      QualityRegistry())
        e = planner.evaluate(candidate("Route", "enable_routing", knobs={"routing": True}),
                             {}, metrics(0.10), 4_000)
        self.assertFalse(e.admissible)

    def test_routing_with_a_quality_observation_is_allowed(self):
        reg = QualityRegistry()
        reg.record(QualityObservation("routed", "task_success_rate", 0.93, 200, "evalset"))
        constraints = WorkloadConstraints("w", 5_000,
                                          quality_floor=QualityFloor("task_success_rate", 0.9))
        planner = OptimizationPlanner(StubEvaluator(), constraints, PolicyEngine(), reg)
        e = planner.evaluate(candidate("Route", "enable_routing", knobs={"routing": True}),
                             {}, metrics(0.10), 4_000)
        self.assertTrue(e.admissible, e.reasons)


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.planner = OptimizationPlanner(StubEvaluator(), CONSTRAINTS, PolicyEngine())
        self.candidates = [
            candidate(),
            CandidateChange("c2", "Enable response cache", "enable_response_cache",
                            {"response_cache": True}, "r", ("e",)),
        ]

    def test_effects_are_recalculated_not_summed(self):
        plan = self.planner.plan({}, self.candidates)
        self.assertEqual(len(plan.steps), 2)
        # The two changes overlap, so the plan must land above the naive sum.
        self.assertGreater(plan.energy_delta_pct, plan.naive_sum_pct)
        self.assertGreater(plan.interaction_error_pct, 0.0)

    def test_each_step_starts_from_the_previous_state(self):
        plan = self.planner.plan({}, self.candidates)
        self.assertEqual(plan.steps[1].metrics_before, plan.steps[0].metrics_after)

    def test_biggest_safe_win_comes_first(self):
        plan = self.planner.plan({}, self.candidates)
        self.assertEqual(plan.steps[0].evaluation.candidate.action, "enable_prefix_cache")

    def test_plan_stops_when_nothing_admissible_remains(self):
        plan = self.planner.plan({}, [candidate(weakens_control=True)])
        self.assertEqual(plan.steps, [])
        self.assertTrue(plan.rejected)

    def test_final_state_is_validated_separately(self):
        planner = OptimizationPlanner(
            lambda knobs: metrics(0.05 if knobs else 0.10,
                                  p95=9_000.0 if knobs else 1_000.0), CONSTRAINTS)
        plan = planner.plan({}, [candidate()])
        self.assertFalse(plan.steps)          # blocked before it could be applied
        self.assertTrue(plan.final_validation["passed"])

    def test_markdown_states_the_interaction_error(self):
        self.assertIn("over-claim", self.planner.plan({}, self.candidates).to_markdown())


class TestNoAction(unittest.TestCase):
    def _forecast(self, series):
        return HoltForecaster().forecast(series, [4], "m", 60.0)

    def test_expected_breach_is_reported(self):
        rising = [100 + 10 * i for i in range(40)]
        projection = project_no_action(horizon_s=240,
                                       energy=(self._forecast(rising), rising[-1], 450.0))
        self.assertIn("energy", projection.expected_breaches)
        self.assertIn("breaches energy", projection.verdict)

    def test_possible_breach_is_distinguished_from_expected(self):
        noisy = [100 + (8 if i % 2 else -8) for i in range(40)]
        projection = project_no_action(horizon_s=240,
                                       energy=(self._forecast(noisy), 100.0, 106.0))
        self.assertEqual(projection.expected_breaches, [])
        self.assertIn("energy", projection.possible_breaches)

    def test_missing_forecast_is_a_warning_not_an_extrapolation(self):
        empty = self._forecast([1.0, 2.0])
        projection = project_no_action(horizon_s=240, energy=(empty, 1.0, 10.0))
        self.assertEqual(projection.dimensions, [])
        self.assertTrue(projection.warnings)

    def test_thermal_projection_flags_the_critical_point(self):
        flat = [100.0] * 40
        projection = project_no_action(
            horizon_s=240, energy=(self._forecast(flat), 100.0, 1_000.0),
            thermal={"projected_peak_c": 95.0, "throttle_c": 83.0, "critical_c": 92.0})
        self.assertIn("critical", projection.thermal_state)
        self.assertTrue(projection.warnings)


if __name__ == "__main__":
    unittest.main()
