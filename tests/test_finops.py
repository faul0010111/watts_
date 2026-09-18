import unittest

from conftest import ROOT  # noqa: F401
from services.finops import CostModel, CostRates, allocate_costs, cost_report
from services.telemetry.schema import RequestRecord

RATES = CostRates(electricity_per_kwh=0.12, accelerator_per_hour=2.40,
                  host_per_accelerator_hour=0.30, carbon_g_per_kwh=380.0)


def records(n=20):
    out = []
    for i in range(n):
        out.append(RequestRecord(
            request_id=f"r{i}", model="m", provider="p", input_tokens=1000, output_tokens=100,
            latency_ms=500.0, gpu_id="g", tenant="acme" if i % 2 else "globex",
            timestamp=float(i), task_class="reasoning" if i % 2 else "classification",
            success=i % 10 != 0, energy_wh=0.1 if i % 2 else 0.02))
    return out


class TestCostModel(unittest.TestCase):
    def setUp(self):
        self.model = CostModel(RATES, accelerators=4)

    def test_components_are_reported_separately(self):
        b = self.model.breakdown(it_energy_wh=800, cooling_energy_wh=320, window_s=3600)
        for key in ("energy", "accelerator", "host", "network", "storage", "cooling_energy"):
            self.assertIn(key, b.as_dict())

    def test_cloud_rate_does_not_bill_electricity_twice(self):
        inclusive = CostModel(CostRates(electricity_per_kwh=0.12, accelerator_per_hour=2.40,
                                        accelerator_rate_includes_energy=True), accelerators=4)
        b = inclusive.breakdown(it_energy_wh=800, cooling_energy_wh=320, window_s=3600)
        self.assertGreater(b.energy, 0.0)               # still shown
        self.assertAlmostEqual(b.total, b.accelerator + b.host + b.network + b.storage)
        self.assertIn("double counting", b.note)

    def test_owned_hardware_adds_electricity(self):
        b = self.model.breakdown(it_energy_wh=800, cooling_energy_wh=320, window_s=3600)
        self.assertAlmostEqual(b.total, b.energy + b.accelerator + b.host + b.network + b.storage)

    def test_energy_is_usually_a_small_share_of_rented_infrastructure(self):
        b = self.model.breakdown(it_energy_wh=800, cooling_energy_wh=320, window_s=3600)
        self.assertLess(b.as_dict()["energy_share"], 0.10)

    def test_unset_rates_produce_zero_not_a_plausible_number(self):
        b = CostModel(CostRates()).breakdown(it_energy_wh=800, cooling_energy_wh=0, window_s=3600)
        self.assertEqual(b.total, 0.0)

    def test_carbon_requires_a_configured_intensity(self):
        self.assertIsNone(CostModel(CostRates()).carbon_g(1000.0))
        self.assertAlmostEqual(self.model.carbon_g(1000.0), 380.0)


class TestAllocation(unittest.TestCase):
    def test_allocates_by_energy_not_by_request_count(self):
        allocations = allocate_costs(records(), model=CostModel(RATES, accelerators=4),
                                     it_energy_wh=800, cooling_energy_wh=320, window_s=3600,
                                     dimension="task_class")
        by_key = {a.key: a for a in allocations}
        self.assertGreater(by_key["reasoning"].breakdown.total,
                           by_key["classification"].breakdown.total * 3)

    def test_tenant_allocation_covers_every_tenant(self):
        allocations = allocate_costs(records(), model=CostModel(RATES, accelerators=4),
                                     it_energy_wh=800, cooling_energy_wh=320, window_s=3600,
                                     dimension="tenant")
        self.assertEqual({a.key for a in allocations}, {"acme", "globex"})

    def test_unit_costs_use_successful_work(self):
        allocations = allocate_costs(records(), model=CostModel(RATES, accelerators=4),
                                     it_energy_wh=800, cooling_energy_wh=320, window_s=3600)
        for a in allocations:
            self.assertGreaterEqual(a.cost_per_successful_task, a.cost_per_request)


class TestReport(unittest.TestCase):
    def test_report_shows_composition_and_convention(self):
        model = CostModel(RATES, accelerators=4)
        b = model.breakdown(it_energy_wh=800, cooling_energy_wh=320, window_s=3600)
        report = cost_report(b, [], tokens=1_000_000, successful_requests=900,
                             carbon_g=model.carbon_g(1120.0))
        self.assertIn("composition", report)
        self.assertIn("convention", report)
        self.assertIn("derived from energy", report["carbon_note"])

    def test_missing_carbon_is_explained_not_omitted(self):
        b = CostModel(CostRates()).breakdown(it_energy_wh=1, cooling_energy_wh=0, window_s=60)
        report = cost_report(b, [], tokens=1000, successful_requests=10, carbon_g=None)
        self.assertIn("no grid intensity configured", report["carbon_note"])


if __name__ == "__main__":
    unittest.main()
