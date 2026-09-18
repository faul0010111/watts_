import unittest

from conftest import ROOT  # noqa: F401
from services.optimization import (
    EnergyLatencyQualityFrontier, FrontierPoint, QualityFloor, QualityObservation,
    QualityRegistry, WorkloadConstraints,
)

FLOOR = QualityFloor("task_success_rate", 0.90, "evalset-v3")


def registry(**values) -> QualityRegistry:
    reg = QualityRegistry()
    for name, value in values.items():
        reg.record(QualityObservation(name, "task_success_rate", value, 200, "evalset-v3"))
    return reg


def frontier(quality: QualityRegistry | None = None, **kw) -> EnergyLatencyQualityFrontier:
    constraints = WorkloadConstraints("w", p95_latency_ms=5_000, quality_floor=FLOOR, **kw)
    return EnergyLatencyQualityFrontier(constraints, quality or registry(a=0.95, b=0.92, c=0.80))


class TestQualityFloor(unittest.TestCase):
    def test_floor_must_be_normalised(self):
        with self.assertRaises(ValueError):
            QualityFloor("m", 1.4)

    def test_observation_needs_samples(self):
        with self.assertRaises(ValueError):
            QualityObservation("a", "m", 0.9, 0, "evalset")

    def test_unevaluated_configuration_is_not_assumed_adequate(self):
        ok, reason = registry().check("unknown-model", FLOOR)
        self.assertFalse(ok)
        self.assertIn("no task_success_rate observation", reason)

    def test_too_few_samples_is_not_evidence(self):
        reg = QualityRegistry()
        reg.record(QualityObservation("a", "task_success_rate", 0.99, 5, "evalset"))
        self.assertFalse(reg.check("a", FLOOR)[0])

    def test_no_floor_means_no_constraint(self):
        self.assertTrue(registry().check("anything", None)[0])

    def test_coverage_lists_what_cannot_be_considered(self):
        coverage = registry(a=0.95).coverage(["a", "b"], "task_success_rate")
        self.assertEqual(coverage["unevaluated"], ["b"])


class TestFrontier(unittest.TestCase):
    def setUp(self):
        self.f = frontier()
        self.f.add(FrontierPoint("a", 0.09, 1_000))
        self.f.add(FrontierPoint("b", 0.05, 2_000))
        self.f.add(FrontierPoint("c", 0.01, 900))          # cheapest, fails the quality floor
        self.f.add(FrontierPoint("d", 0.02, 9_000))        # cheap, fails latency and unknown quality

    def test_cheapest_option_can_be_inadmissible(self):
        self.assertNotEqual(self.f.recommend()[0].name, "c")

    def test_every_exclusion_carries_a_reason(self):
        for rejected in self.f.rejected():
            self.assertTrue(rejected.reasons[0])

    def test_unknown_quality_excludes_a_candidate(self):
        reasons = self.f.as_dict()["inadmissible"]["d"]
        self.assertTrue(any("no task_success_rate observation" in r for r in reasons)
                        or any("exceeds the SLO" in r for r in reasons))

    def test_recommends_lowest_energy_among_admissible(self):
        best, note = self.f.recommend()
        self.assertEqual(best.name, "b")
        self.assertIn("lowest-energy admissible", note)

    def test_pareto_keeps_non_dominated_options(self):
        names = {p.name for p in self.f.pareto()}
        self.assertIn("b", names)      # cheapest admissible
        self.assertIn("a", names)      # slower-but-better-quality trade remains visible

    def test_security_block_is_not_a_trade_off(self):
        f = frontier()
        f.add(FrontierPoint("a", 0.01, 500, security_compliant=False,
                            security_reason="WATTS-SEC-002: restricted data off-prem"))
        best, note = f.recommend()
        self.assertIsNone(best)
        self.assertIn("WATTS-SEC-002", str(f.as_dict()["inadmissible"]))

    def test_energy_budget_is_a_constraint(self):
        f = frontier(energy_budget_wh_per_hour=100.0)
        f.add(FrontierPoint("a", 0.05, 500, energy_wh_per_hour=500.0))
        self.assertIsNone(f.recommend()[0])

    def test_no_candidates_is_reported_not_guessed(self):
        best, note = frontier().recommend()
        self.assertIsNone(best)
        self.assertIn("no candidates", note)

    def test_markdown_shows_unknown_quality_as_unknown(self):
        self.assertIn("unknown", self.f.to_markdown())

    def test_error_rate_beyond_limit_is_inadmissible(self):
        f = frontier()
        f.add(FrontierPoint("a", 0.05, 500, error_rate=0.30, availability=0.70))
        self.assertIsNone(f.recommend()[0])


if __name__ == "__main__":
    unittest.main()
