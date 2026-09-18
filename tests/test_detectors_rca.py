import unittest

from conftest import ROOT  # noqa: F401
from services.energy_engine import Provenance, analyse_causes, robust_z, run_detection_suite
from services.energy_engine.anomaly import CauseHypothesis, EnergyAnomaly
from services.energy_engine.detectors import latency_energy_ratio


def flat_history(value=0.05, n=20, jitter=0.002):
    return [value + jitter * ((i % 5) - 2) for i in range(n)]


class TestDetectors(unittest.TestCase):
    def test_method_is_always_reported(self):
        d = robust_z(flat_history(), 0.05, metric="wh_per_1k_tokens")
        self.assertIn("median/MAD", d.method.describe())
        for key in ("estimator", "baseline_windows", "baseline_value", "dispersion",
                    "threshold", "robust", "direction"):
            self.assertIn(key, d.method.as_dict())

    def test_insufficient_history_does_not_fire(self):
        d = robust_z([0.05, 0.05], 5.0, metric="m")
        self.assertFalse(d.detected)
        self.assertIn("insufficient history", d.reason)

    def test_excursion_fires_with_severity(self):
        d = robust_z(flat_history(), 0.20, metric="wh_per_1k_tokens")
        self.assertTrue(d.detected)
        self.assertEqual(d.severity, "page")

    def test_baseline_is_the_series_own_history(self):
        # A rack that always runs high is not anomalous at its usual value.
        self.assertFalse(robust_z(flat_history(value=1.9), 1.9, metric="pue").detected)

    def test_lower_direction_detects_a_collapse(self):
        d = robust_z(flat_history(value=100.0, jitter=2.0), 10.0, metric="tokens_per_s",
                     direction="lower")
        self.assertTrue(d.detected)

    def test_flat_baseline_falls_back_and_says_so(self):
        d = robust_z([1.0] * 20, 2.0, metric="pue")
        self.assertIn("relative change", d.method.estimator)
        self.assertIn("perfectly flat", d.reason)

    def test_suite_skips_metrics_without_telemetry(self):
        history = [{"wh_per_1k_tokens": v} for v in flat_history()]
        suite = run_detection_suite(history, {"wh_per_1k_tokens": 0.05})
        self.assertEqual([d.metric for d in suite.results], ["wh_per_1k_tokens"])

    def test_suite_lists_what_fires_and_what_pages(self):
        history = [{"wh_per_1k_tokens": v, "pue": 1.4} for v in flat_history()]
        suite = run_detection_suite(history, {"wh_per_1k_tokens": 0.3, "pue": 1.4})
        self.assertIn("wh_per_1k_tokens", suite.firing[0].metric)
        self.assertTrue(suite.pages)

    def test_latency_energy_ratio_handles_zero_energy(self):
        self.assertEqual(latency_energy_ratio(100.0, 0.0), float("inf"))


def anomaly(hypotheses):
    return EnergyAnomaly(True, "wh_per_1k_tokens", 0.05, 0.08, 60.0, 5.2, tuple(hypotheses))


class TestRootCause(unittest.TestCase):
    def test_clear_leader_becomes_primary(self):
        rca = analyse_causes(anomaly([
            CauseHypothesis("cooling_overhead", 0.80, ("PUE 1.42 → 1.85",)),
            CauseHypothesis("workload_change", 0.20, ("tokens flat",))]))
        self.assertIsNotNone(rca.primary)
        self.assertEqual(rca.primary.cause, "cooling_overhead")
        self.assertTrue(rca.conclusive)

    def test_contested_field_yields_no_primary_cause(self):
        rca = analyse_causes(anomaly([
            CauseHypothesis("cooling_overhead", 0.45, ("a",)),
            CauseHypothesis("thermal_throttling", 0.42, ("b",))]))
        self.assertIsNone(rca.primary)
        self.assertFalse(rca.conclusive)
        self.assertIn("cannot be separated", rca.statement())

    def test_every_cause_names_its_validation_step(self):
        rca = analyse_causes(anomaly([CauseHypothesis("thermal_throttling", 0.9, ("temp up",))]))
        self.assertIn("DCGM", rca.primary.validation_required)

    def test_weak_hypotheses_are_graded_as_possible_or_insufficient(self):
        rca = analyse_causes(anomaly([
            CauseHypothesis("cooling_overhead", 0.9, ("a",)),
            CauseHypothesis("gpu_degradation", 0.2, ("b",)),
            CauseHypothesis("model_change", 0.05, ("c",))]))
        grades = {c.cause: c.grade for c in rca.causes}
        self.assertEqual(grades["gpu_degradation"], "possible")
        self.assertEqual(grades["model_change"], "insufficient_evidence")

    def test_no_hypotheses_is_reported_as_unexplained(self):
        rca = analyse_causes(anomaly([]))
        self.assertEqual(rca.causes[0].grade, "insufficient_evidence")
        self.assertIn("nothing in the collected telemetry", rca.statement())

    def test_simulated_analysis_says_within_simulation(self):
        rca = analyse_causes(anomaly([CauseHypothesis("cooling_overhead", 0.9, ("a",))]),
                             provenance=Provenance.SIMULATED)
        self.assertIn("within simulation", rca.statement())

    def test_measured_analysis_says_from_telemetry(self):
        rca = analyse_causes(anomaly([CauseHypothesis("cooling_overhead", 0.9, ("a",))]),
                             provenance=Provenance.MEASURED)
        self.assertIn("from telemetry", rca.statement())

    def test_dict_separates_the_four_grades(self):
        d = analyse_causes(anomaly([CauseHypothesis("cooling_overhead", 0.9, ("a",))])).as_dict()
        for key in ("primary_cause", "contributing_factors", "possible_causes",
                    "insufficient_evidence"):
            self.assertIn(key, d)

    def test_no_anomaly_produces_no_claim(self):
        quiet = EnergyAnomaly(False, "wh_per_1k_tokens", 0.05, 0.05, 0.0, 0.2, ())
        self.assertIn("no anomaly detected", analyse_causes(quiet).statement())


if __name__ == "__main__":
    unittest.main()
