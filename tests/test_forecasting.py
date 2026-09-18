import math
import unittest

from conftest import ROOT  # noqa: F401
from services.forecasting import HoltForecaster, SeasonalNaiveForecaster, backtest


def trend_series(n=80, slope=2.0, noise=0.0):
    return [100 + slope * i + (noise * math.sin(i)) for i in range(n)]


class TestHolt(unittest.TestCase):
    def test_insufficient_history_is_reported_not_guessed(self):
        f = HoltForecaster().forecast([1, 2, 3], [1], "m", 60)
        self.assertEqual(f.points, ())
        self.assertIn("insufficient_history", f.warning)

    def test_follows_a_linear_trend(self):
        f = HoltForecaster().forecast(trend_series(), [5], "m", 60)
        self.assertGreater(f.points[0].value, trend_series()[-1])

    def test_intervals_widen_with_horizon(self):
        f = HoltForecaster().forecast(trend_series(noise=5.0), [1, 10], "m", 60)
        narrow = f.points[0].upper - f.points[0].lower
        wide = f.points[1].upper - f.points[1].lower
        self.assertGreater(wide, narrow)

    def test_lower_bound_is_never_negative(self):
        f = HoltForecaster().forecast([1.0] * 30, [20], "m", 60)
        self.assertGreaterEqual(f.points[0].lower, 0.0)

    def test_at_resolves_by_seconds(self):
        f = HoltForecaster().forecast(trend_series(), [1, 5, 15], "m", 60)
        self.assertEqual(f.at(300).horizon_steps, 5)

    def test_backtest_reports_coverage(self):
        result = backtest(HoltForecaster(), trend_series(n=120, noise=4.0), horizon=3)
        self.assertGreater(result["samples"], 10)
        self.assertIsNotNone(result["mape"])
        self.assertGreaterEqual(result["interval_coverage"], 0.0)


class TestSeasonalNaive(unittest.TestCase):
    def test_needs_two_full_seasons(self):
        f = SeasonalNaiveForecaster(24).forecast([1.0] * 30, [1], "m", 3600)
        self.assertIn("insufficient_history", f.warning)

    def test_repeats_the_previous_cycle(self):
        series = [float(i % 24) for i in range(96)]
        f = SeasonalNaiveForecaster(24).forecast(series, [1, 2], "m", 3600)
        self.assertEqual(f.points[0].value, series[-24])


if __name__ == "__main__":
    unittest.main()


class TestFeatureModel(unittest.TestCase):
    def _rows(self, n=40):
        return [{"energy_wh": 0.4 + 0.002 * i + 0.00004 * (i % 7),
                 "input_tokens": 900 + 8 * i, "output_tokens": 180 + (i % 5),
                 "requests_per_second": 2.0 + 0.01 * i, "batch_size": 4 + (i % 3),
                 "gpu_utilization": 0.4 + 0.004 * i, "temperature_c": 58 + 0.1 * i,
                 "queue_depth": i % 4, "hour": (i // 4) % 24} for i in range(n)]

    def test_refuses_to_fit_on_too_little_history(self):
        from services.forecasting import InsufficientData, fit_feature_model
        with self.assertRaises(InsufficientData):
            fit_feature_model(self._rows(5))

    def test_constant_features_are_dropped_not_given_a_coefficient(self):
        from services.forecasting import fit_feature_model
        rows = [{**r, "queue_depth": 1} for r in self._rows()]
        model = fit_feature_model(rows)
        self.assertIn("queue_depth", model.dropped)
        self.assertNotIn("queue_depth", model.features)

    def test_reports_which_driver_matters_most(self):
        from services.forecasting import fit_feature_model
        model = fit_feature_model(self._rows())
        self.assertTrue(model.drivers)
        self.assertIn("caveat", model.as_dict())

    def test_forecast_carries_residual_based_intervals(self):
        from services.forecasting import fit_feature_model, forecast_with_features
        model = fit_feature_model(self._rows())
        forecast = forecast_with_features(model, self._rows()[-3:], "energy_wh", 60.0)
        self.assertEqual(len(forecast.points), 3)
        for point in forecast.points:
            self.assertLessEqual(point.lower, point.value)
            self.assertGreaterEqual(point.upper, point.value)

    def test_no_future_rows_produces_a_warning_not_a_guess(self):
        from services.forecasting import fit_feature_model, forecast_with_features
        forecast = forecast_with_features(fit_feature_model(self._rows()), [], "energy_wh", 60.0)
        self.assertEqual(forecast.points, ())
        self.assertIn("no future feature rows", forecast.warning)


class TestForecastSuite(unittest.TestCase):
    def _suite(self, **kw):
        from services.forecasting import build_forecast_suite
        n = 60
        params = dict(
            energy_series=[1000 + 8 * i for i in range(n)],
            token_series=[600 + 2 * i for i in range(n)],
            gpu_demand_series=[3.0 + 0.01 * i for i in range(n)],
            cooling_series=[400 + 3 * i for i in range(n)],
            step_s=60.0)
        params.update(kw)
        return build_forecast_suite(**params)

    def test_produces_all_four_targets(self):
        suite = self._suite()
        for forecast in (suite.energy, suite.tokens, suite.gpu_demand, suite.cooling_load):
            self.assertTrue(forecast.points)

    def test_breach_probability_is_reported_when_a_limit_exists(self):
        suite = self._suite(energy_limit=1_400.0)
        self.assertTrue(suite.breaches)
        probability = suite.breaches[0].probability
        self.assertGreaterEqual(probability, 0.0)
        self.assertLessEqual(probability, 1.0)

    def test_a_distant_limit_is_negligible_and_a_near_one_is_not(self):
        far = self._suite(energy_limit=1e9).breaches[0]
        near = self._suite(energy_limit=1_200.0).breaches[0]
        self.assertLess(far.probability, near.probability)
        self.assertEqual(far.severity, "negligible")

    def test_no_limit_means_no_probability_is_invented(self):
        self.assertEqual(self._suite().breaches, [])

    def test_method_is_stated_with_the_probability(self):
        self.assertIn("normal approximation", self._suite(energy_limit=1_400.0).breaches[0].method)
