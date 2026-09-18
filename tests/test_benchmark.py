import unittest

from conftest import ROOT  # noqa: F401
from benchmarks.harness import DEFAULT_STRATEGIES, run_benchmark, to_markdown
from simulation.twin import DatacenterConfig, WorkloadConfig


class TestBenchmark(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_benchmark(
            strategies=DEFAULT_STRATEGIES()[:3],
            workload=WorkloadConfig(duration_s=150.0, base_rps=1.5, seed=11),
            datacenter=DatacenterConfig(gpus=4), seeds=(11, 12))

    def test_marks_results_as_simulated(self):
        self.assertEqual(self.report["provenance"], "simulated")
        self.assertIn("must not be quoted as hardware measurements", self.report["disclaimer"])

    def test_every_strategy_produced_a_row(self):
        self.assertEqual(len(self.report["rows"]), 3)

    def test_rows_carry_computed_metrics(self):
        for row in self.report["rows"]:
            self.assertGreater(row["wh_per_1k_tokens"], 0.0)
            self.assertIn("slo_met", row)

    def test_reports_seed_variability(self):
        self.assertTrue(self.report["variability_wh_per_1k_tokens"])
        for stats in self.report["variability_wh_per_1k_tokens"].values():
            self.assertEqual(stats["runs"], 2)

    def test_records_the_environment_for_reproduction(self):
        self.assertIn("python", self.report["environment"])
        self.assertEqual(self.report["workload"]["seeds"], [11, 12])

    def test_markdown_renders_the_disclaimer_and_rows(self):
        md = to_markdown(self.report)
        self.assertIn("simulated", md)
        self.assertIn("baseline", md)


if __name__ == "__main__":
    unittest.main()


class TestAdmissibility(unittest.TestCase):
    """Benchmark 2.0: energy per token from an inadmissible strategy is not a result."""

    @classmethod
    def setUpClass(cls):
        from services.security.policy import PolicyEngine
        cls.report = run_benchmark(
            strategies=DEFAULT_STRATEGIES()[:2],
            workload=WorkloadConfig(duration_s=120.0, base_rps=0.5, seed=11),
            datacenter=DatacenterConfig(gpus=8), seeds=(11,),
            quality={"baseline": 0.96}, quality_floor=0.90,
            policy=PolicyEngine(),
            approved_models=("watts-sim-small", "watts-sim-medium", "watts-sim-large"))

    def test_rows_report_quality_state_and_security(self):
        for row in self.report["rows"]:
            self.assertIn(row["quality_state"],
                          {"not evaluated", "meets floor", "below floor"})
            self.assertIn("security_compliant", row)
            self.assertIn("admissible", row)

    def test_unevaluated_quality_is_not_treated_as_adequate(self):
        row = next(r for r in self.report["rows"] if r["strategy"] == "dynamic_batching")
        self.assertEqual(row["quality_state"], "not evaluated")
        self.assertFalse(row["admissible"])

    def test_inadmissible_strategies_carry_their_reasons(self):
        for reasons in self.report["inadmissible_strategies"].values():
            self.assertTrue(reasons)

    def test_throughput_is_reported(self):
        self.assertIn("throughput_requests_s", self.report["rows"][0])

    def test_markdown_explains_admissibility(self):
        self.assertIn("Admissibility", to_markdown(self.report))

    def test_unrun_benchmark_says_so_instead_of_guessing(self):
        from benchmarks.harness import not_run
        report = not_run()
        self.assertEqual(report["status"], "NOT RUN")
        self.assertEqual(report["rows"], [])
        self.assertIn("STATUS: NOT RUN", to_markdown(report))

    def test_disallowed_model_makes_a_strategy_inadmissible(self):
        from services.security.policy import PolicyEngine
        report = run_benchmark(
            strategies=DEFAULT_STRATEGIES()[:1],
            workload=WorkloadConfig(duration_s=90.0, base_rps=0.5, seed=11),
            datacenter=DatacenterConfig(gpus=8), seeds=(11,),
            quality={"baseline": 0.99}, quality_floor=0.90,
            policy=PolicyEngine(), approved_models=("watts-sim-small",))
        self.assertFalse(report["rows"][0]["security_compliant"])
        self.assertIn("WATTS-SEC-001", report["rows"][0]["security_reason"])
