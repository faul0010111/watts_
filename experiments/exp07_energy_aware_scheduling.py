"""Experiment 07 - energy-aware scheduling.

Places a mixed queue - critical inference, normal training, flexible batch and embedding
work - against an hourly price and carbon curve, and compares three policies:

* ``fifo``            - place everything at the earliest free slot
* ``price_aware``     - defer flexible work to cheaper slots
* ``price_and_carbon``- defer flexible work weighing price and grid carbon intensity

The result to check is not the saving. It is that critical work starts immediately under
every policy: the scheduler has no authority to trade availability for energy.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import write  # noqa: E402
from services.scheduler import (  # noqa: E402
    Criticality, EnergyAwareScheduler, GridSignal, Job, JobClass, SchedulerConfig,
)

# Illustrative 24-hour price and carbon curves, supplied by the operator in a real
# deployment (day-ahead market feed, grid carbon API). Declared here, not measured.
PRICE = [0.11, 0.10, 0.09, 0.09, 0.10, 0.13, 0.18, 0.22, 0.21, 0.17, 0.15, 0.14,
         0.13, 0.12, 0.12, 0.14, 0.19, 0.26, 0.28, 0.24, 0.19, 0.16, 0.14, 0.12]
CARBON = [210, 190, 180, 175, 180, 230, 310, 380, 350, 280, 220, 170,
          140, 120, 125, 160, 260, 400, 430, 390, 330, 290, 250, 230]


def jobs() -> list[Job]:
    return [
        Job("prod-inference", JobClass.INFERENCE, Criticality.CRITICAL, 8, 3600, 12.0),
        Job("fraud-scoring", JobClass.INFERENCE, Criticality.CRITICAL, 4, 3600, 6.0),
        Job("nightly-finetune", JobClass.TRAINING, Criticality.NORMAL, 8, 4 * 3600, 180.0,
            deadline_s=22 * 3600, deferrable=True),
        Job("corpus-embedding", JobClass.EMBEDDING, Criticality.FLEXIBLE, 4, 2 * 3600, 60.0,
            deadline_s=20 * 3600, deferrable=True),
        Job("etl-backfill", JobClass.DATA_PROCESSING, Criticality.FLEXIBLE, 2, 3600, 25.0,
            deadline_s=18 * 3600, deferrable=True),
    ]


def signals(available_gpus: int = 16) -> list[GridSignal]:
    return [GridSignal(h * 3600, PRICE[h], CARBON[h], available_gpus=available_gpus)
            for h in range(24)]


def main() -> None:
    policies = [
        ("fifo", SchedulerConfig(slot_s=3600, price_weight=0.0, max_defer_s=0.0)),
        ("price_aware", SchedulerConfig(slot_s=3600, price_weight=1.0, carbon_weight=0.0,
                                        max_defer_s=14 * 3600)),
        ("price_and_carbon", SchedulerConfig(slot_s=3600, price_weight=1.0, carbon_weight=0.6,
                                             max_defer_s=14 * 3600)),
    ]
    rows, base_cost, base_carbon = [], None, None
    detail: dict[str, list] = {}
    for name, cfg in policies:
        result = EnergyAwareScheduler(cfg).schedule(jobs(), signals())
        base_cost = base_cost if base_cost is not None else result.total_cost
        base_carbon = base_carbon if base_carbon is not None else result.total_carbon_g
        critical = [p for p in result.placements if p.job_id in ("prod-inference", "fraud-scoring")]
        rows.append({
            "policy": name,
            "total_cost": result.total_cost,
            "total_carbon_kg": (result.total_carbon_g or 0) / 1000.0,
            "cost_vs_fifo_pct": (result.total_cost - base_cost) / base_cost * 100 if base_cost else 0,
            "carbon_vs_fifo_pct": ((result.total_carbon_g - base_carbon) / base_carbon * 100
                                   if base_carbon else 0),
            "max_defer_h": max(p.deferred_s for p in result.placements) / 3600.0,
            "critical_delay_s": max(p.deferred_s for p in critical),
            "unplaced": len(result.unplaced),
        })
        detail[name] = [p.as_dict() for p in result.placements]

    worst_critical = max(r["critical_delay_s"] for r in rows)
    findings = [
        f"Critical inference started immediately under every policy (maximum delay "
        f"{worst_critical:.0f} s). This is the invariant the scheduler exists to preserve.",
        f"Price-aware deferral moved flexible work by up to {rows[1]['max_defer_h']:.0f} h and "
        f"changed energy cost by {rows[1]['cost_vs_fifo_pct']:+.1f}% and carbon by "
        f"{rows[1]['carbon_vs_fifo_pct']:+.1f}% against FIFO.",
        f"Adding carbon to the objective changed cost by {rows[2]['cost_vs_fifo_pct']:+.1f}% and "
        f"carbon by {rows[2]['carbon_vs_fifo_pct']:+.1f}%: the cheapest hour and the cleanest "
        "hour are not the same hour, so the weighting is a policy decision, not a technical one.",
        "Price and carbon curves here are illustrative inputs. Connect your market feed and grid "
        "intensity source before drawing conclusions about your own site.",
    ]
    path = write("exp07_energy_aware_scheduling", "Experiment 07 - Energy-aware scheduling",
                 "Can deferring flexible workloads reduce cost and carbon without delaying "
                 "critical work?", rows,
                 [("policy", "Policy", "{}"), ("total_cost", "Cost", "{:.2f}"),
                  ("total_carbon_kg", "Carbon kg", "{:.1f}"),
                  ("cost_vs_fifo_pct", "cost vs FIFO", "{:+.1f}%"),
                  ("carbon_vs_fifo_pct", "carbon vs FIFO", "{:+.1f}%"),
                  ("max_defer_h", "Max defer h", "{:.1f}"),
                  ("critical_delay_s", "Critical delay s", "{:.0f}"),
                  ("unplaced", "Unplaced", "{:.0f}")],
                 {"price_curve": PRICE, "carbon_curve": CARBON,
                  "jobs": [j.id for j in jobs()], "placements": detail}, findings)
    print(f"\nplacements written to {path}")


if __name__ == "__main__":
    main()
