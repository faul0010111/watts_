import unittest

from conftest import ROOT  # noqa: F401
from services.calibration import (
    CalibrationRegistry, CalibrationSample, FittingError, MeasurementSession, build_record,
    calibration_report, compare_prediction, fit_coefficients, validate,
)
from services.energy_engine.model import Provenance
from services.token_engine import load_default_profiles

BASELINE, PREFILL, DECODE = 0.004, 1.1e-5, 1.3e-4
SHAPES = [(100, 100), (1000, 100), (100, 1000), (4000, 1000), (2000, 500), (500, 250)]


def hardware_session(noise: float = 0.0, provenance=Provenance.MEASURED) -> MeasurementSession:
    session = MeasurementSession(model="watts-sim-medium", hardware="1xA100-80GB",
                                 runtime="vLLM 0.5.4", adapter="dcgm")
    for i, (inp, out) in enumerate(SHAPES):
        truth = BASELINE + PREFILL * inp + DECODE * out
        session.add(CalibrationSample(input_tokens=inp, output_tokens=out,
                                      measured_wh=truth * (1 + noise * ((i % 3) - 1)),
                                      provenance=provenance, source="dcgm"))
    return session.close()


class TestSession(unittest.TestCase):
    def test_one_simulated_sample_makes_the_session_simulated(self):
        session = hardware_session()
        session.ended_at = None
        session.add(CalibrationSample(10, 10, 0.01, provenance=Provenance.SIMULATED))
        self.assertIs(session.provenance, Provenance.SIMULATED)
        self.assertFalse(session.is_hardware_session)

    def test_closed_session_cannot_be_extended(self):
        with self.assertRaises(RuntimeError):
            hardware_session().add(CalibrationSample(1, 1, 0.1))

    def test_coverage_records_the_sampled_span(self):
        coverage = hardware_session().coverage
        self.assertEqual(coverage["input_tokens"]["max"], 4000)
        self.assertEqual(coverage["distinct_shapes"], len(SHAPES))

    def test_negative_energy_is_rejected(self):
        with self.assertRaises(ValueError):
            CalibrationSample(10, 10, -1.0)


class TestFitting(unittest.TestCase):
    def test_recovers_the_coefficients_it_was_given(self):
        c = fit_coefficients(hardware_session())
        self.assertAlmostEqual(c.prefill_wh_per_token, PREFILL, places=7)
        self.assertAlmostEqual(c.decode_wh_per_token, DECODE, places=7)
        self.assertAlmostEqual(c.baseline_wh, BASELINE, places=5)

    def test_reports_the_decode_prefill_ratio(self):
        self.assertAlmostEqual(fit_coefficients(hardware_session()).decode_prefill_ratio,
                               DECODE / PREFILL, places=3)

    def test_refuses_too_few_samples(self):
        session = MeasurementSession("m", "h", "r", "a")
        for i in range(3):
            session.add(CalibrationSample(100 * i, 10, 0.01 * i))
        with self.assertRaises(FittingError):
            fit_coefficients(session)

    def test_refuses_when_shapes_do_not_separate_prefill_from_decode(self):
        session = MeasurementSession("m", "h", "r", "a")
        for i in range(6):
            session.add(CalibrationSample(100, 100, 0.02))
        with self.assertRaises(FittingError):
            fit_coefficients(session)

    def test_validation_reports_error_and_bias(self):
        session = hardware_session()
        result = validate(fit_coefficients(session), session.samples, in_sample=True)
        self.assertLess(result.mae, 1e-6)
        self.assertTrue(result.acceptable)
        self.assertEqual(len(result.ci95_bias), 2)

    def test_noisy_data_still_fits_but_carries_error(self):
        session = hardware_session(noise=0.08)
        result = validate(fit_coefficients(session), session.samples, in_sample=True)
        self.assertGreater(result.mae, 0.0)

    def test_compare_prediction_reports_direction(self):
        result = compare_prediction([1.1, 1.2, 1.3], [1.0, 1.0, 1.0])
        self.assertEqual(result["direction"], "over-predicts")


class TestRegistryAndStatus(unittest.TestCase):
    def _record(self, provenance=Provenance.MEASURED):
        session = hardware_session(provenance=provenance)
        coefficients = fit_coefficients(session)
        return build_record(session, coefficients,
                            validate(coefficients, session.samples, in_sample=True))

    def test_unknown_model_is_uncalibrated(self):
        self.assertEqual(CalibrationRegistry().status("anything"), "uncalibrated")

    def test_simulated_session_is_never_a_calibration(self):
        record = self._record(Provenance.SIMULATED)
        self.assertEqual(record.status, "self-consistency-check")
        registry = CalibrationRegistry([record])
        self.assertNotEqual(registry.status("watts-sim-medium"), "calibrated")
        self.assertIsNone(registry.calibration_id("watts-sim-medium"))

    def test_hardware_session_yields_a_calibration(self):
        registry = CalibrationRegistry([self._record()])
        self.assertEqual(registry.status("watts-sim-medium"), "calibrated")
        self.assertIsNotNone(registry.calibration_id("watts-sim-medium"))

    def test_failed_validation_cannot_be_registered(self):
        record = self._record()
        broken = type(record)(**{**record.__dict__,
                                 "validation": type(record.validation)(
                                     **{**record.validation.__dict__,
                                        "mean_relative_error": 0.9, "bias": 10.0, "mae": 1.0})})
        with self.assertRaises(ValueError):
            CalibrationRegistry().register(broken)

    def test_applying_a_calibration_updates_the_profile(self):
        registry = CalibrationRegistry([self._record()])
        profile = registry.apply_to_profile(load_default_profiles().get("watts-sim-medium"))
        self.assertTrue(profile.calibrated)
        self.assertAlmostEqual(profile.decode_weight / profile.prefill_weight,
                               DECODE / PREFILL, places=3)
        self.assertIsNotNone(profile.absolute_wh(1000, 100))

    def test_uncalibrated_profile_refuses_to_quote_watt_hours(self):
        profile = load_default_profiles().get("watts-sim-medium")
        self.assertFalse(profile.calibrated)
        self.assertIsNone(profile.absolute_wh(1000, 100))

    def test_simulated_record_does_not_change_the_profile(self):
        registry = CalibrationRegistry([self._record(Provenance.SIMULATED)])
        original = load_default_profiles().get("watts-sim-medium")
        self.assertEqual(registry.apply_to_profile(original), original)

    def test_summary_lists_uncalibrated_models(self):
        summary = CalibrationRegistry().summary(["a", "b"])
        self.assertEqual(summary["uncalibrated"], ["a", "b"])
        self.assertEqual(summary["calibrated_fraction"], 0.0)

    def test_coverage_check_flags_extrapolation(self):
        record = self._record()
        self.assertTrue(record.covers(1000, 500))
        self.assertFalse(record.covers(50_000, 500))


class TestReport(unittest.TestCase):
    def test_simulated_report_says_it_is_not_a_calibration(self):
        session = hardware_session(provenance=Provenance.SIMULATED)
        coefficients = fit_coefficients(session)
        record = build_record(session, coefficients,
                              validate(coefficients, session.samples, in_sample=True))
        text = calibration_report(record)
        self.assertIn("NOT A CALIBRATION", text)
        self.assertIn("in-sample", text)

    def test_hardware_report_permits_quoting_watt_hours(self):
        session = hardware_session()
        coefficients = fit_coefficients(session)
        record = build_record(session, coefficients,
                              validate(coefficients, session.samples))
        self.assertIn("CALIBRATED", calibration_report(record))


if __name__ == "__main__":
    unittest.main()
