import unittest

from conftest import ROOT  # noqa: F401
from services.energy_engine import Provenance
from services.telemetry.schema import RequestRecord
from services.token_engine import (
    EconomicsInputs, FindingType, TokenEconomics, TokenEfficiencyEngine, attribute_energy,
    load_default_profiles,
)


class TestAttribution(unittest.TestCase):
    def setUp(self):
        self.profile = load_default_profiles().get("watts-sim-medium")

    def test_split_conserves_energy(self):
        a = attribute_energy(2.0, 1800, 420, self.profile)
        self.assertAlmostEqual(a.input_energy_wh + a.output_energy_wh, 2.0)

    def test_output_tokens_cost_more_each(self):
        a = attribute_energy(2.0, 1800, 420, self.profile)
        self.assertGreater(a.wh_per_output_token, a.wh_per_input_token)

    def test_method_names_the_profile_source(self):
        a = attribute_energy(1.0, 10, 10, self.profile)
        self.assertIn("declared-default", a.attribution_method)

    def test_zero_tokens_is_safe(self):
        a = attribute_energy(1.0, 0, 0, self.profile)
        self.assertEqual(a.wh_per_total_token, 0.0)

    def test_negative_energy_rejected(self):
        with self.assertRaises(ValueError):
            attribute_energy(-1.0, 1, 1, self.profile)


class TestProfiles(unittest.TestCase):
    def test_unknown_model_refuses_to_guess(self):
        with self.assertRaises(KeyError):
            load_default_profiles().get("some-unregistered-model")

    def test_defaults_are_flagged_uncalibrated(self):
        registry = load_default_profiles()
        self.assertEqual(set(registry.uncalibrated()), set(registry.names()))

    def test_embedding_profile_is_not_generative(self):
        self.assertFalse(load_default_profiles().get("watts-sim-embed").generative)


def rec(i, **kw):
    base = dict(request_id=f"r{i}", model="m", provider="p", input_tokens=1000,
                output_tokens=100, latency_ms=500.0, gpu_id="g", tenant="t",
                timestamp=float(i), task_class="classification", energy_wh=0.1,
                context_window=16384)
    base.update(kw)
    return RequestRecord(**base)


class TestEfficiency(unittest.TestCase):
    def test_duplicates_detected(self):
        records = [rec(i, prompt_hash="same") for i in range(5)]
        report = TokenEfficiencyEngine().analyze(records)
        kinds = {f.type for f in report.findings}
        self.assertIn(FindingType.DUPLICATE_CALL, kinds)

    def test_cache_hits_are_not_counted_as_duplicates(self):
        records = [rec(i, prompt_hash="same", cache_hit=(i > 0)) for i in range(5)]
        report = TokenEfficiencyEngine().analyze(records)
        self.assertNotIn(FindingType.DUPLICATE_CALL, {f.type for f in report.findings})

    def test_failed_work_is_reported_with_full_energy(self):
        records = [rec(i, prompt_hash=f"h{i}", success=(i % 2 == 0)) for i in range(10)]
        report = TokenEfficiencyEngine().analyze(records)
        failed = [f for f in report.findings if f.type is FindingType.FAILED_WORK][0]
        self.assertEqual(failed.affected_requests, 5)
        self.assertAlmostEqual(failed.wasted_energy_wh, 0.5)

    def test_metrics_use_successful_tasks(self):
        records = [rec(i, prompt_hash=f"h{i}", success=(i < 8)) for i in range(10)]
        m = TokenEfficiencyEngine().analyze(records).metrics
        self.assertEqual(m.requests, 10)
        self.assertEqual(m.successful_tasks, 8)
        self.assertGreater(m.wh_per_successful_task, m.wh_per_token)

    def test_report_warns_findings_overlap(self):
        report = TokenEfficiencyEngine().analyze([rec(i, prompt_hash="same") for i in range(5)])
        self.assertIn("do not sum", report.as_dict()["note"])


class TestEconomics(unittest.TestCase):
    def test_carbon_is_none_without_intensity(self):
        econ = TokenEconomics(1000, 10.0, 60.0, EconomicsInputs(0.12))
        self.assertIsNone(econ.carbon_g)
        self.assertIsNone(econ.as_dict()["per_1k_tokens"]["carbon_g"])

    def test_carbon_derives_from_energy(self):
        econ = TokenEconomics(1000, 1000.0, 0.0, EconomicsInputs(0.1, carbon_intensity_g_per_kwh=400))
        self.assertAlmostEqual(econ.carbon_g, 400.0)
        self.assertIn("not measured", econ.as_dict()["carbon"]["note"])


if __name__ == "__main__":
    unittest.main()
