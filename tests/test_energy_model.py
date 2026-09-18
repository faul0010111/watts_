import unittest

from conftest import ROOT  # noqa: F401  (adds the repo root to sys.path)
from services.energy_engine import (
    ComponentEnergy, EnergyModel, PowerSample, Provenance, compute_pue, integrate_power,
)
from services.energy_engine.anomaly import EnergyAnomalyEngine, WindowStats
from services.energy_engine.pue import pue_anomaly


class TestIntegration(unittest.TestCase):
    def test_constant_power_integrates_exactly(self):
        samples = [PowerSample(t, 3600.0) for t in range(0, 11)]
        wh, prov = integrate_power(samples)
        self.assertAlmostEqual(wh, 10.0)          # 3600 W for 10 s = 10 Wh
        self.assertIs(prov, Provenance.DERIVED)

    def test_unsorted_samples_are_ordered(self):
        a, _ = integrate_power([PowerSample(0, 100), PowerSample(10, 200)])
        b, _ = integrate_power([PowerSample(10, 200), PowerSample(0, 100)])
        self.assertAlmostEqual(a, b)

    def test_single_sample_yields_zero(self):
        wh, _ = integrate_power([PowerSample(0, 500)])
        self.assertEqual(wh, 0.0)

    def test_negative_power_rejected(self):
        with self.assertRaises(ValueError):
            PowerSample(0, -1)


class TestProvenance(unittest.TestCase):
    def test_weakest_wins(self):
        self.assertIs(
            Provenance.weakest([Provenance.MEASURED, Provenance.SIMULATED, Provenance.DERIVED]),
            Provenance.SIMULATED)

    def test_breakdown_reports_measured_fraction(self):
        model = EnergyModel()
        gpu = ComponentEnergy("gpu", 90.0, Provenance.MEASURED, "nvml")
        cpu = model.cpu_energy_wh(8, 0.5, 3600)
        breakdown = model.compose([gpu, cpu], pue=1.5)
        self.assertGreater(breakdown.measured_fraction, 0.5)
        self.assertIs(breakdown.provenance, Provenance.ESTIMATED)   # weakest component wins
        self.assertAlmostEqual(breakdown.facility_energy_wh, breakdown.it_energy_wh * 1.5)


class TestPUE(unittest.TestCase):
    def test_pue_below_one_is_rejected(self):
        with self.assertRaises(ValueError):
            EnergyModel().compose([], pue=0.9)

    def test_facility_below_it_is_rejected(self):
        with self.assertRaises(ValueError):
            compute_pue(100.0, 90.0)

    def test_estimated_pue_is_labelled(self):
        snap = compute_pue(100.0, None, cooling_energy_wh=40.0, facility_meter_available=False)
        self.assertIs(snap.provenance, Provenance.ESTIMATED)
        self.assertAlmostEqual(snap.pue, 1.4)

    def test_pue_anomaly_needs_history(self):
        self.assertFalse(pue_anomaly([1.4, 1.41], 2.0)["anomalous"])

    def test_pue_anomaly_detects_outlier(self):
        history = [1.40, 1.41, 1.39, 1.40, 1.42, 1.40, 1.41, 1.39, 1.40, 1.41]
        self.assertTrue(pue_anomaly(history, 1.95)["anomalous"])


class TestAnomalyEngine(unittest.TestCase):
    def _history(self, **overrides):
        base = dict(wh_per_1k_tokens=0.05, gpu_util_mean=0.6, gpu_temp_mean_c=50.0,
                    sm_clock_mean_mhz=1400, batch_size_mean=8.0, tokens=1e6, pue=1.4,
                    throughput_tokens_s=5000, model_mix={"m": 1.0})
        base.update(overrides)
        return WindowStats(**base)

    def test_no_anomaly_on_stable_signal(self):
        history = [self._history(wh_per_1k_tokens=0.05 + 0.001 * (i % 3)) for i in range(20)]
        result = EnergyAnomalyEngine().detect(history, self._history())
        self.assertFalse(result.detected)

    def test_thermal_signature_ranked_first(self):
        history = [self._history(wh_per_1k_tokens=0.05 + 0.0005 * (i % 3)) for i in range(20)]
        current = self._history(wh_per_1k_tokens=0.09, gpu_temp_mean_c=72.0,
                                sm_clock_mean_mhz=1100)
        result = EnergyAnomalyEngine().detect(history, current)
        self.assertTrue(result.detected)
        self.assertEqual(result.hypotheses[0].cause, "thermal_throttling")
        self.assertTrue(result.hypotheses[0].evidence)

    def test_short_history_never_alerts(self):
        result = EnergyAnomalyEngine().detect([self._history()] * 3, self._history(wh_per_1k_tokens=9.0))
        self.assertFalse(result.detected)


if __name__ == "__main__":
    unittest.main()
