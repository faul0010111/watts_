import unittest
from dataclasses import replace

from conftest import ROOT  # noqa: F401
from services.energy_engine import Provenance
from simulation.twin import (
    CoolingSpec, DatacenterConfig, ServingConfig, Simulation, WorkloadConfig,
)

SHORT = WorkloadConfig(duration_s=120, base_rps=2.0, seed=11)
SERVING = ServingConfig(max_batch=8, max_wait_s=0.1, default_model="watts-sim-medium")


def run(serving=SERVING, workload=SHORT, dc=None):
    return Simulation(dc=dc or DatacenterConfig(), serving=serving, workload=workload).run()


class TestReproducibility(unittest.TestCase):
    def test_same_seed_gives_identical_results(self):
        a, b = run().summary(), run().summary()
        self.assertEqual(a, b)

    def test_different_seed_gives_different_results(self):
        other = run(workload=replace(SHORT, seed=99)).summary()
        self.assertNotEqual(run().summary()["total_tokens"], other["total_tokens"])

    def test_config_is_recorded_for_reproduction(self):
        config = run().config
        self.assertIn("serving", config)
        self.assertEqual(config["workload"]["seed"], 11)


class TestProvenanceLabelling(unittest.TestCase):
    def test_every_record_is_labelled_simulated(self):
        result = run()
        self.assertTrue(result.records)
        for record in result.records:
            self.assertEqual(record.energy_provenance, Provenance.SIMULATED.value)

    def test_summary_declares_simulation(self):
        summary = run().summary()
        self.assertTrue(summary["simulated"])
        self.assertEqual(summary["provenance"], "simulated")

    def test_no_prompt_text_anywhere_in_records(self):
        for record in run().records:
            self.assertEqual(record.metadata, {})
            self.assertIsNone(getattr(record, "prompt", None))


class TestPhysics(unittest.TestCase):
    def test_facility_energy_exceeds_it_energy(self):
        result = run()
        self.assertGreater(result.facility_energy_wh, result.it_energy_wh)
        self.assertGreater(result.pue, 1.0)

    def test_batching_lowers_energy_per_token(self):
        load = WorkloadConfig(duration_s=180, base_rps=3.0, seed=11)
        single = run(replace(SERVING, max_batch=1, max_wait_s=0.0), load).summary()
        batched = run(replace(SERVING, max_batch=16, max_wait_s=0.2), load).summary()
        self.assertLess(batched["wh_per_1k_tokens"], single["wh_per_1k_tokens"])

    def test_degraded_cooling_raises_pue_and_temperature(self):
        good = run(dc=DatacenterConfig(cooling=CoolingSpec(efficiency=1.0))).summary()
        bad = run(dc=DatacenterConfig(
            cooling=CoolingSpec(efficiency=0.4, inlet_temp_c=30))).summary()
        self.assertGreater(bad["pue"], good["pue"])
        self.assertGreater(bad["max_gpu_temp_c"], good["max_gpu_temp_c"])
        self.assertGreater(bad["facility_wh_per_1k_tokens"], good["facility_wh_per_1k_tokens"])

    def test_response_cache_produces_hits_and_saves_energy(self):
        load = WorkloadConfig(duration_s=180, base_rps=2.0, seed=11, duplicate_rate=0.4)
        plain = run(SERVING, load).summary()
        cached = run(replace(SERVING, response_cache=True), load).summary()
        self.assertGreater(cached["cache_hits"], 0)
        self.assertLess(cached["facility_energy_wh"], plain["facility_energy_wh"])

    def test_output_cap_reduces_tokens_and_energy(self):
        capped = run(replace(SERVING, output_cap={"reasoning": 100, "summarization": 100})).summary()
        plain = run().summary()
        self.assertLess(capped["total_tokens"], plain["total_tokens"])
        self.assertLess(capped["facility_energy_wh"], plain["facility_energy_wh"])

    def test_idle_fleet_still_draws_power(self):
        idle = run(workload=replace(SHORT, base_rps=0.05)).summary()
        self.assertGreater(idle["facility_energy_wh"], 0.0)

    def test_power_series_is_monotonic_in_time(self):
        series = run().power_series
        times = [p["t_s"] for p in series]
        self.assertEqual(times, sorted(times))


if __name__ == "__main__":
    unittest.main()


class TestQueueing(unittest.TestCase):
    """Arrival → queue → batch formation → service → response."""

    def _run(self, **serving):
        base = dict(max_batch=8, max_wait_s=0.1, default_model="watts-sim-medium")
        base.update(serving)
        return Simulation(dc=DatacenterConfig(gpus=2), serving=ServingConfig(**base),
                          workload=WorkloadConfig(duration_s=180, base_rps=2.0, seed=11)).run()

    def test_latency_decomposes_into_waiting_and_serving(self):
        result = self._run()
        for record in result.records[:50]:
            self.assertIsNotNone(record.queue_wait_ms)
            self.assertIsNotNone(record.service_ms)
            self.assertAlmostEqual(record.latency_ms,
                                   record.queue_wait_ms + record.service_ms, places=6)

    def test_summary_reports_percentiles_for_both_parts(self):
        summary = self._run().summary()
        for key in ("queue_wait_p50_ms", "queue_wait_p95_ms", "queue_wait_p99_ms",
                    "service_p50_ms", "service_p95_ms", "queue_share_of_latency",
                    "mean_queue_depth", "max_queue_depth", "device_utilization",
                    "throughput_requests_s"):
            self.assertIn(key, summary)

    def test_a_wider_wait_window_buys_energy_with_queue_time(self):
        narrow = self._run(max_wait_s=0.0).summary()
        wide = self._run(max_wait_s=0.5).summary()
        self.assertGreaterEqual(wide["mean_queue_wait_ms"], narrow["mean_queue_wait_ms"])

    def test_queue_depth_series_tracks_the_window(self):
        result = self._run()
        self.assertTrue(result.queue_series)
        self.assertEqual([q["t_s"] for q in result.queue_series],
                         sorted(q["t_s"] for q in result.queue_series))

    def test_device_utilization_is_a_fraction(self):
        utilization = self._run().summary()["device_utilization"]
        self.assertGreaterEqual(utilization, 0.0)
        self.assertLessEqual(utilization, 1.0)

    def test_heavier_load_deepens_the_queue(self):
        light = Simulation(dc=DatacenterConfig(gpus=2),
                           serving=ServingConfig(max_batch=8, max_wait_s=0.1,
                                                 default_model="watts-sim-medium"),
                           workload=WorkloadConfig(duration_s=180, base_rps=0.5, seed=11)).run()
        heavy = self._run()
        self.assertGreater(heavy.summary()["mean_queue_depth"],
                           light.summary()["mean_queue_depth"])
