"""Experiment 06 - temperature, throttling and efficiency.

Degrades cooling capability step by step at a constant workload. As the inlet temperature
rises and cooling efficiency drops, the accelerators run hotter, the clock throttles, work
takes longer at similar power, and both the energy per token and the PUE get worse.

This is the loop WATTS calls thermal efficiency degradation: rising temperature plus
sustained utilisation plus rising cooling draw, with falling useful throughput.
"""
from common import CoolingSpec, DatacenterConfig, ServingConfig, WorkloadConfig, pct, run_seeds, write

COOLING = [(1.00, 22.0, "design point"), (0.80, 24.0, "reduced airflow"),
           (0.60, 27.0, "fouled coils"), (0.45, 30.0, "one unit down"),
           (0.35, 33.0, "two units down")]


def main() -> None:
    serving = ServingConfig(max_batch=16, max_wait_s=0.1, default_model="watts-sim-medium")
    workload = WorkloadConfig(duration_s=600, base_rps=3.0)
    rows, base = [], None
    for efficiency, inlet, label in COOLING:
        dc = DatacenterConfig(cooling=CoolingSpec(inlet_temp_c=inlet, efficiency=efficiency))
        r = run_seeds(serving, workload, dc=dc)
        base = base or r
        rows.append({
            "cooling": f"{label} ({efficiency:.2f} eff, {inlet:.0f} C inlet)",
            "max_gpu_temp_c": r["max_gpu_temp_c"],
            "mean_clock_factor": r["mean_clock_factor"],
            "throttled_fraction": r["throttled_fraction"],
            "tokens_per_s": r["tokens_per_s"],
            "wh_per_1k_tokens": r["wh_per_1k_tokens"],
            "facility_wh_per_1k_tokens": r["facility_wh_per_1k_tokens"],
            "cooling_energy_wh": r["cooling_energy_wh"],
            "pue": r["pue"],
            "gpu_energy_vs_design_pct": pct(r["wh_per_1k_tokens"], base["wh_per_1k_tokens"]),
            "facility_energy_vs_design_pct": pct(r["facility_wh_per_1k_tokens"],
                                                 base["facility_wh_per_1k_tokens"]),
        })

    worst, best = rows[-1], rows[0]
    findings = [
        f"From the design point to the worst case, peak GPU temperature went from "
        f"{best['max_gpu_temp_c']:.1f} C to {worst['max_gpu_temp_c']:.1f} C and the mean clock "
        f"factor from {best['mean_clock_factor']:.3f} to {worst['mean_clock_factor']:.3f}.",
        f"PUE moved {best['pue']:.3f} -> {worst['pue']:.3f}. Facility energy per 1k tokens rose "
        f"{worst['facility_energy_vs_design_pct']:+.0f}% while GPU energy per 1k tokens moved "
        f"{worst['gpu_energy_vs_design_pct']:+.0f}%: most of the damage is in the facility, not "
        "on the die, which is why a GPU-only view of efficiency misses it.",
        f"Throughput fell to {worst['tokens_per_s']:.0f} tok/s from {best['tokens_per_s']:.0f} "
        "tok/s at identical offered load, so the queue absorbs the difference and latency rises.",
        "Detection rule: temperature up, clock down, cooling draw up, throughput flat or falling "
        "is a facility problem, not a workload problem. WATTS' anomaly engine ranks it as "
        "thermal throttling on exactly this signature.",
    ]
    write("exp06_thermal_throttling", "Experiment 06 - Thermal throttling and efficiency",
          "How does temperature influence computational efficiency, and what does cooling cost?",
          rows,
          [("cooling", "Cooling state", "{}"), ("max_gpu_temp_c", "Peak C", "{:.1f}"),
           ("mean_clock_factor", "Clock", "{:.3f}"),
           ("throttled_fraction", "Throttled", "{:.1%}"),
           ("tokens_per_s", "tok/s", "{:.0f}"),
           ("wh_per_1k_tokens", "GPU Wh/1k", "{:.5f}"),
           ("facility_wh_per_1k_tokens", "Facility Wh/1k", "{:.5f}"),
           ("cooling_energy_wh", "Cooling Wh", "{:.1f}"), ("pue", "PUE", "{:.3f}"),
           ("facility_energy_vs_design_pct", "facility vs design", "{:+.0f}%")],
          {"rps": 3.0, "duration_s": 600, "cooling_states": [c[2] for c in COOLING]}, findings)


if __name__ == "__main__":
    main()
