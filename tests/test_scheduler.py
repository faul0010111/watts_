import unittest

from conftest import ROOT  # noqa: F401
from services.scheduler import (
    Criticality, EnergyAwareScheduler, GridSignal, Job, JobClass, SchedulerConfig,
)

CHEAP_HOUR = 6


def signals(gpus=8):
    return [GridSignal(h * 3600, 0.05 if h == CHEAP_HOUR else 0.20,
                       120.0 if h == CHEAP_HOUR else 400.0, available_gpus=gpus)
            for h in range(12)]


class TestScheduler(unittest.TestCase):
    def setUp(self):
        self.sched = EnergyAwareScheduler(SchedulerConfig(slot_s=3600, max_defer_s=10 * 3600))

    def test_critical_inference_is_never_deferred(self):
        job = Job("crit", JobClass.INFERENCE, Criticality.CRITICAL, 2, 600, 5.0)
        placement = self.sched.schedule([job], signals()).placements[0]
        self.assertEqual(placement.deferred_s, 0.0)

    def test_interactive_inference_is_never_deferred_even_if_flagged(self):
        job = Job("inf", JobClass.INFERENCE, Criticality.FLEXIBLE, 2, 600, 5.0, deferrable=True)
        self.assertEqual(self.sched.schedule([job], signals()).placements[0].deferred_s, 0.0)

    def test_flexible_batch_moves_to_the_cheap_slot(self):
        job = Job("batch", JobClass.BATCH, Criticality.FLEXIBLE, 2, 3600, 50.0,
                  deadline_s=11 * 3600, deferrable=True)
        placement = self.sched.schedule([job], signals()).placements[0]
        self.assertEqual(placement.start_s, CHEAP_HOUR * 3600)

    def test_deadline_beats_price(self):
        job = Job("batch", JobClass.BATCH, Criticality.FLEXIBLE, 2, 3600, 50.0,
                  deadline_s=3 * 3600, deferrable=True)
        placement = self.sched.schedule([job], signals()).placements[0]
        self.assertLessEqual(placement.start_s + job.duration_s, job.deadline_s)

    def test_carbon_weight_changes_the_choice(self):
        # Cheapest hour and cleanest hour differ.
        sig = [GridSignal(h * 3600, 0.05 if h == 2 else 0.20,
                          100.0 if h == 8 else 500.0, available_gpus=8) for h in range(12)]
        job = Job("batch", JobClass.BATCH, Criticality.FLEXIBLE, 2, 3600, 100.0,
                  deadline_s=11 * 3600, deferrable=True)
        price_only = EnergyAwareScheduler(SchedulerConfig(slot_s=3600, carbon_weight=0.0,
                                                          max_defer_s=10 * 3600))
        carbon_aware = EnergyAwareScheduler(SchedulerConfig(slot_s=3600, price_weight=1.0,
                                                            carbon_weight=5.0,
                                                            max_defer_s=10 * 3600))
        self.assertEqual(price_only.schedule([job], sig).placements[0].start_s, 2 * 3600)
        self.assertEqual(carbon_aware.schedule([job], sig).placements[0].start_s, 8 * 3600)

    def test_capacity_is_respected(self):
        jobs = [Job(f"j{i}", JobClass.BATCH, Criticality.NORMAL, 4, 3600, 10.0) for i in range(4)]
        result = EnergyAwareScheduler(SchedulerConfig(slot_s=3600)).schedule(jobs, signals(gpus=8))
        starts = [p.start_s for p in result.placements]
        self.assertLessEqual(starts.count(0.0), 2)   # only two 4-GPU jobs fit in an 8-GPU slot

    def test_unplaceable_job_is_reported_not_forced(self):
        job = Job("huge", JobClass.TRAINING, Criticality.NORMAL, 64, 3600, 10.0)
        result = EnergyAwareScheduler().schedule([job], signals(gpus=8))
        self.assertEqual(len(result.unplaced), 1)

    def test_no_signals_is_an_error(self):
        with self.assertRaises(ValueError):
            EnergyAwareScheduler().schedule([], [])

    def test_reason_explains_every_placement(self):
        jobs = [Job("crit", JobClass.INFERENCE, Criticality.CRITICAL, 2, 600, 5.0),
                Job("batch", JobClass.BATCH, Criticality.FLEXIBLE, 2, 3600, 50.0,
                    deadline_s=11 * 3600, deferrable=True)]
        for p in self.sched.schedule(jobs, signals()).placements:
            self.assertTrue(p.reason)


if __name__ == "__main__":
    unittest.main()
