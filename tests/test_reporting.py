"""The report is the product. These tests hold it to the promises it makes."""
import json
import re
import unittest

from conftest import ROOT  # noqa: F401
from services.reporting import PipelineConfig, render_markdown, run_pipeline

CONFIG = PipelineConfig(seed=42, duration_s=90.0, rps=1.2, gpus=2)


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_pipeline(CONFIG)
        cls.data = cls.result.as_dict()
        cls.markdown = render_markdown(cls.data)

    def test_every_stage_of_the_pipeline_is_present(self):
        for section in ("observation", "ingest", "energy", "attribution", "efficiency",
                        "anomalies", "forecast", "slo", "budgets", "no_action", "frontier",
                        "plan", "security_gate", "finops", "audit", "benchmark",
                        "experiments", "provenance", "calibration"):
            self.assertIn(section, self.data, section)

    def test_run_context_makes_the_run_reproducible(self):
        run = self.data["run"]
        for key in ("run_id", "seed", "configuration_hash", "reproduction_command",
                    "version", "policy_version", "code_revision"):
            self.assertIn(key, run)
        self.assertIn("--seed 42", run["reproduction_command"])

    def test_same_seed_reproduces_the_same_numbers(self):
        other = run_pipeline(CONFIG).as_dict()
        self.assertEqual(self.data["energy"]["facility_wh"], other["energy"]["facility_wh"])
        self.assertEqual(self.data["observation"]["total_tokens"],
                         other["observation"]["total_tokens"])

    def test_different_seed_changes_the_run(self):
        other = run_pipeline(PipelineConfig(seed=7, duration_s=90.0, rps=1.2, gpus=2)).as_dict()
        self.assertNotEqual(self.data["observation"]["total_tokens"],
                            other["observation"]["total_tokens"])

    def test_configuration_hash_tracks_the_configuration(self):
        other = run_pipeline(PipelineConfig(seed=42, duration_s=90.0, rps=3.0, gpus=2))
        self.assertNotEqual(self.result.context.configuration_hash,
                            other.context.configuration_hash)

    def test_forged_telemetry_never_reaches_the_engines(self):
        self.assertFalse(self.data["ingest"]["forged_record_accepted"])
        self.assertTrue(self.data["ingest"]["forged_record_reason"])

    def test_nothing_is_executed(self):
        self.assertEqual(self.data["security_gate"]["executed"], 0)

    def test_audit_chain_is_verified_in_the_run(self):
        self.assertTrue(self.data["audit"]["chain_valid"])

    def test_simulated_run_is_labelled_simulated_everywhere(self):
        self.assertEqual(self.data["observation"]["provenance"], "simulated")
        self.assertEqual(self.data["energy"]["pue"]["provenance"], "simulated")
        self.assertEqual(self.data["provenance"]["weakest"], "simulated")

    def test_calibration_status_is_reported_as_uncalibrated(self):
        self.assertEqual(self.data["calibration"]["calibrated"], [])
        self.assertTrue(self.data["calibration"]["uncalibrated"])

    def test_benchmark_and_experiments_report_a_status(self):
        self.assertIn(self.data["benchmark"]["status"], {"available", "NOT RUN"})
        self.assertIn(self.data["experiments"]["status"], {"available", "NOT RUN"})

    def test_plan_reports_the_interaction_error(self):
        self.assertIn("interaction_error_pct", self.data["plan"])

    def test_report_is_json_serialisable(self):
        json.dumps(self.data, default=str)


class TestRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = run_pipeline(CONFIG).as_dict()
        cls.markdown = render_markdown(cls.data)

    def test_every_required_section_is_rendered(self):
        for heading in ("Executive summary", "SLO status", "Energy attribution",
                        "Token efficiency", "Energy anomalies", "Forecast",
                        "If nothing changes", "Energy–latency–quality frontier",
                        "Optimisation plan", "Security gate", "Energy FinOps",
                        "Audit chain", "Benchmark", "Experiments", "Provenance summary",
                        "Reproduction"):
            self.assertIn(heading, self.markdown, heading)

    def test_simulated_provenance_is_stated_before_any_number(self):
        head = self.markdown[:self.markdown.index("## Executive summary")]
        self.assertIn("simulated", head)

    def test_reproduction_command_is_printed(self):
        self.assertIn(self.data["run"]["reproduction_command"], self.markdown)

    def test_declaration_about_hand_written_numbers_is_present(self):
        self.assertIn("No figure in this report was entered by hand", self.markdown)

    def test_no_placeholder_values_leak_into_the_document(self):
        for placeholder in ("TODO", "FIXME", "XXX", "lorem", "{}"):
            self.assertNotIn(placeholder, self.markdown)

    def test_nan_never_reaches_the_reader(self):
        self.assertNotIn("nan", re.sub(r"[A-Za-z]nan|nan[A-Za-z]", "", self.markdown.lower()))


if __name__ == "__main__":
    unittest.main()
