import unittest

from conftest import ROOT  # noqa: F401
from services.energy_engine import Provenance
from services.energy_engine.value import (
    EnergyValue, ProvenanceError, derived, estimated, measured, provenance_summary, simulated,
)


class TestEnergyValue(unittest.TestCase):
    def test_carries_the_full_envelope(self):
        v = measured(1.5, "nvml", timestamp=10.0, confidence=0.9)
        d = v.as_dict()
        for key in ("value", "unit", "source", "provenance", "timestamp", "confidence",
                    "calibration_id"):
            self.assertIn(key, d)

    def test_confidence_must_be_a_probability(self):
        with self.assertRaises(ValueError):
            EnergyValue(1.0, confidence=1.4)

    def test_measured_plus_estimated_is_not_a_measurement(self):
        total = measured(1.0, "nvml") + estimated(0.2, "cpu-model")
        self.assertIs(total.provenance, Provenance.ESTIMATED)
        self.assertFalse(total.is_measurement)

    def test_scaling_a_measurement_makes_it_derived(self):
        self.assertIs(measured(1.0, "nvml").scaled(0.5).provenance, Provenance.DERIVED)

    def test_units_cannot_be_mixed(self):
        with self.assertRaises(ValueError):
            measured(1.0, "a") + EnergyValue(1.0, unit="kWh", source="b")

    def test_total_takes_the_weakest_provenance(self):
        total = EnergyValue.total([measured(1.0, "a"), derived(1.0, "b"), simulated(1.0)])
        self.assertIs(total.provenance, Provenance.SIMULATED)

    def test_confidence_of_a_sum_is_the_least_confident_part(self):
        total = measured(1.0, "a", confidence=0.9) + measured(1.0, "b", confidence=0.4)
        self.assertEqual(total.confidence, 0.4)

    def test_calibration_id_survives_only_when_shared(self):
        a = derived(1.0, "x", calibration_id="cal-1")
        self.assertEqual((a + derived(1.0, "y", calibration_id="cal-1")).calibration_id, "cal-1")
        self.assertIsNone((a + derived(1.0, "y", calibration_id="cal-2")).calibration_id)

    def test_require_refuses_to_relabel_evidence(self):
        with self.assertRaises(ProvenanceError):
            simulated(1.0).require(Provenance.MEASURED)
        measured(1.0, "nvml").require(Provenance.DERIVED)   # stronger than required: fine

    def test_label_always_shows_provenance(self):
        self.assertIn("[simulated]", simulated(0.5).label())


class TestProvenanceSummary(unittest.TestCase):
    def test_measured_fraction_is_by_energy_not_by_count(self):
        values = [measured(1000.0, "meter")] + [estimated(0.001, "model") for _ in range(10)]
        self.assertGreater(provenance_summary(values)["measured_fraction"], 0.99)

    def test_reports_the_weakest_and_the_uncalibrated_sources(self):
        summary = provenance_summary([measured(1.0, "nvml"), estimated(1.0, "cpu-model")])
        self.assertEqual(summary["weakest"], "estimated")
        self.assertIn("cpu-model", summary["uncalibrated_sources"])

    def test_empty_input_does_not_invent_a_summary(self):
        self.assertEqual(provenance_summary([])["values"], 0)


if __name__ == "__main__":
    unittest.main()
