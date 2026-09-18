"""Render a pipeline run as a report a human reads top to bottom.

Order is deliberate: what happened, whether objectives held, what it cost, what changed and
why, what is predicted, what WATTS proposes, what security allowed, and finally how to
reproduce every number above. Executive summary first, because most readers stop there -
and the summary must therefore be the most careful section, not the loosest.
"""
from __future__ import annotations

import time


def render_markdown(data: dict) -> str:
    run = data["run"]
    obs, energy = data["observation"], data["energy"]
    slo, budgets = data["slo"], data["budgets"]
    out: list[str] = []
    w = out.append

    w(f"# WATTS report — {run['configuration']['workload']}")
    w("")
    w(f"`{run['run_id']}` · seed `{run['seed']}` · config `{run['configuration_hash']}` · "
      f"policy `{run['policy_version']}` · WATTS {run['version']} · {run['started_at_utc']}")
    w("")
    w(f"> **Every figure below is `{obs['provenance']}`.** It was produced by the digital "
      f"twin on this machine, not measured on hardware. Relative comparisons under this "
      f"configuration are meaningful; absolute watt-hours are not until the model profiles "
      f"are calibrated (`docs/CALIBRATION.md`).")
    w("")

    # --- executive summary --------------------------------------------------
    w("## Executive summary")
    w("")
    reliability = slo.get("reliability_violations") or []
    plan = data["plan"]
    w(f"- **Window:** {obs['requests']:,} requests, {obs['total_tokens']:,} tokens over "
      f"{obs['window_s']:,.0f} s on {run['configuration']['gpus']} accelerators.")
    w(f"- **Energy:** {energy['facility_wh']:,.1f} Wh facility "
      f"({energy['facility_wh_per_1k_tokens']:.4f} Wh per 1k tokens, PUE "
      f"{energy['pue']['pue']:.3f} — {energy['pue']['basis']}).")
    w(f"- **Objectives:** " + ("all reliability objectives held."
                              if not reliability else
                              f"{len(reliability)} reliability violation(s): {reliability[0]}."))
    breaches = budgets.get("breaches", [])
    w(f"- **Budgets:** " + ("within every declared budget."
                            if not breaches else
                            ", ".join(f"{b['dimension']} at {b['utilization']:.0%} of limit"
                                      for b in breaches) + "."))
    w(f"- **Proposed plan:** {plan['energy_delta_pct']:+.1f}% energy per 1k tokens across "
      f"{len(plan['steps'])} re-simulated step(s); summing the steps independently would "
      f"have claimed {plan['naive_sum_of_steps_pct']:+.1f}%.")
    w(f"- **Executed:** nothing. {data['security_gate']['note']}")
    w("")

    # --- SLO ---------------------------------------------------------------
    w("## SLO status")
    w("")
    w("| Objective | Observed | Target | State |")
    w("|---|---|---|---|")
    cfg = run["configuration"]
    rows = [
        ("p95 latency", f"{slo['p95_latency_ms']:,.0f} ms", f"{cfg['p95_latency_slo_ms']:,.0f} ms",
         slo["p95_latency_ms"] <= cfg["p95_latency_slo_ms"]),
        ("availability", f"{slo['availability']:.3%}", f"{cfg['availability_slo']:.2%}",
         slo["availability"] >= cfg["availability_slo"]),
        ("error rate", f"{slo['error_rate']:.3%}", f"{cfg['max_error_rate']:.2%}",
         slo["error_rate"] <= cfg["max_error_rate"]),
        ("Wh per successful request", f"{slo['wh_per_successful_request']:.4f}",
         f"{cfg['max_wh_per_successful_request']:.2f}",
         slo["wh_per_successful_request"] <= cfg["max_wh_per_successful_request"]),
    ]
    for name, observed, target, ok in rows:
        w(f"| {name} | {observed} | {target} | {'within' if ok else '**over**'} |")
    w("")
    w(f"Latency headroom remaining: **{slo['latency_headroom_ms']:,.0f} ms**. "
      "This is the currency every batching or consolidation change spends.")
    w("")

    # --- budgets -----------------------------------------------------------
    w("## Energy and token budgets")
    w("")
    if not budgets.get("tracked", True):
        w("No budget is declared for this workload.")
    else:
        w("| Dimension | Projected hourly | Limit | Utilisation |")
        w("|---|---|---|---|")
        for u in budgets.get("utilization", {}).items() if isinstance(
                budgets.get("utilization"), dict) else []:
            pass
        for b in budgets.get("breaches", []):
            w(f"| {b['dimension']} | {b['projected']:,.0f} | {b['limit']:,.0f} | "
              f"**{b['utilization']:.0%}** |")
        if not budgets.get("breaches"):
            w(f"| energy_wh_per_hour | {energy['facility_wh'] * 3600 / obs['window_s']:,.0f} | "
              f"{cfg['energy_budget_wh_per_hour']:,.0f} | within |")
    w("")

    # --- energy attribution -------------------------------------------------
    attribution = data["attribution"]
    w("## Energy attribution")
    w("")
    w("| Component | Wh | Share | Provenance |")
    w("|---|---|---|---|")
    for name, comp in sorted(attribution["components"].items(),
                             key=lambda kv: -kv[1]["value"]):
        share = attribution["shares"].get(name, 0.0)
        w(f"| {name} | {comp['value']:.4f} | {share:.1%} | {comp['provenance']} |")
    w("")
    w("The request total carries the provenance of its measurement; the split between "
      "components is an attribution produced by the model profile and the declared "
      "coefficients. It is never a measurement of a component.")
    w("")
    calibration = data["calibration"]
    w(f"**Calibration status:** {len(calibration['calibrated'])} of "
      f"{len(calibration['models'])} model profiles calibrated. "
      f"Uncalibrated: {', '.join(calibration['uncalibrated']) or 'none'}.")
    w("")

    # --- efficiency ---------------------------------------------------------
    eff = data["efficiency"]
    m = eff["metrics"]
    w("## Token efficiency")
    w("")
    w(f"- {m['tokens_per_request']:,.0f} tokens per request, "
      f"{m['wh_per_token']:.6f} Wh per token")
    w(f"- **{m['wh_per_useful_task']:.4f} Wh per useful task** "
      f"({m['useful_tasks']:,} tasks reached a successful outcome)")
    w(f"- {m['useful_work_fraction']:.1%} of requests succeeded; "
      f"{m['failed_requests']:,} failed")
    w("")
    if eff["findings"]:
        w("| Finding | Requests | Wasted Wh | Confidence | Recommendation |")
        w("|---|---|---|---|---|")
        for f in eff["findings"][:6]:
            w(f"| `{f['finding_id']}` {f['type']} | {f['affected_requests']:,} | "
              f"{f['wasted_energy_wh']:.3f} | {f['confidence']:.2f} | {f['recommendation']} |")
        w("")
        w(f"_{eff['note']}._")
    else:
        w("No waste patterns detected in this window.")
    w("")

    # --- anomalies and root cause -------------------------------------------
    anomalies = data["anomalies"]
    w("## Energy anomalies and root cause")
    w("")
    w(f"Comparison window: {anomalies['comparison_window']}.")
    w("")
    w("| Metric | Observed | Baseline | Robust z | Severity | Method |")
    w("|---|---|---|---|---|---|")
    for d in anomalies["detections"]["detections"]:
        w(f"| {d['metric']} | {d['observed']:.4g} | {d['baseline']:.4g} | {d['score']:+.1f} | "
          f"{d['severity']} | {d['method']['estimator']} |")
    w("")
    rca = anomalies["root_cause"]
    w(f"**{rca['statement']}**")
    w("")
    for c in rca["contributing_factors"]:
        w(f"- contributing: {c['cause'].replace('_', ' ')} ({c['confidence']:.2f}) — "
          f"validate with {c['validation_required']}")
    for c in rca["possible_causes"]:
        w(f"- possible: {c['cause'].replace('_', ' ')} ({c['confidence']:.2f})")
    w("")
    w(f"_{rca['note']}_")
    w("")

    # --- forecast ------------------------------------------------------------
    forecast = data["forecast"]
    w("## Forecast")
    w("")
    w("| Target | Horizon value | 80% interval |")
    w("|---|---|---|")
    for key, label in (("energy", "facility power (W)"), ("tokens", "tokens/s"),
                       ("gpu_demand", "GPU demand"), ("cooling_load", "cooling power (W)")):
        fc = forecast[key]
        if not fc["points"]:
            w(f"| {label} | — | {fc['warning'] or 'unavailable'} |")
            continue
        p = fc["points"][-1]
        w(f"| {label} | {p['value']:,.2f} | {p['lower']:,.2f} – {p['upper']:,.2f} |")
    w("")
    for b in forecast["breach_probabilities"]:
        w(f"- **{b['dimension']} budget:** {b['probability']:.0%} chance of exceeding "
          f"{b['limit']:,.0f} within {b['horizon_s'] / 60:.0f} min — {b['severity']} "
          f"({b['method']}).")
    w("")

    # --- no action -----------------------------------------------------------
    na = data["no_action"]
    w("## If nothing changes")
    w("")
    w("| Dimension | Now | Projected | 80% interval | Limit | State |")
    w("|---|---|---|---|---|---|")
    for d in na["dimensions"]:
        state = ("**breach expected**" if d["breach_expected"]
                 else "breach possible" if d["breach_possible"] else "within limit")
        w(f"| {d['name']} | {d['current']:,.2f} | {d['projected']:,.2f} "
          f"({d['change_pct']:+.1f}%) | {d['lower']:,.2f} – {d['upper']:,.2f} | "
          f"{d['limit']:,.0f} | {state} |")
    w("")
    w(f"Thermal: {na['thermal_state']}. SLO: {na['slo_state']}.")
    w("")
    w(f"**Verdict:** {na['verdict']}.")
    w("")

    # --- frontier -------------------------------------------------------------
    frontier = data["frontier"]
    w("## Energy–latency–quality frontier")
    w("")
    w("Constraints: " + "; ".join(frontier["constraints"]) + ".")
    w("")
    w("| Configuration | Wh/1k tokens | p95 ms | Quality | Admissible |")
    w("|---|---|---|---|---|")
    for c in frontier["candidates"]:
        quality = f"{c['quality']:.3f}" if c["quality"] is not None else "unknown"
        w(f"| {c['name']} | {c['energy_wh_per_1k_tokens']:.4f} | {c['p95_latency_ms']:,.0f} | "
          f"{quality} | {'yes' if c['admissible'] else 'no'} |")
    w("")
    for name, reasons in frontier["inadmissible"].items():
        w(f"- `{name}` excluded: {reasons[0]}")
    w("")
    w(f"**Selected:** {frontier['recommendation'] or 'none'} — "
      f"{frontier['recommendation_note']}")
    w("")

    # --- plan ------------------------------------------------------------------
    w("## Optimisation plan")
    w("")
    w("| # | Change | Δ at step | Cumulative | p95 after | Gate |")
    w("|---|---|---|---|---|---|")
    for s in plan["steps"]:
        w(f"| {s['order']} | {s['change']} | {s['energy_delta_pct_at_this_step']:+.1f}% | "
          f"{s['cumulative_energy_delta_pct']:+.1f}% | {s['p95_after_ms']:,.0f} ms | "
          f"{s['approval_requirement']} |")
    if not plan["steps"]:
        w("| — | no admissible change found | — | — | — | — |")
    w("")
    w(f"Plan effect **{plan['energy_delta_pct']:+.1f}%**; naive sum of the same steps "
      f"{plan['naive_sum_of_steps_pct']:+.1f}%; interaction error "
      f"{plan['interaction_error_pct']:+.1f} points. {plan['note']}.")
    w("")
    validation = plan["final_validation"]
    w(f"Final-state validation: **{'passed' if validation['passed'] else 'FAILED'}** "
      f"({', '.join(k for k in validation['checks'])}).")
    w("")
    if plan["rejected"]:
        w("Rejected candidates:")
        for r in plan["rejected"][:5]:
            reason = r["reasons"][0] if r["reasons"] else "n/a"
            w(f"- **{r['candidate']['name']}** — {reason}")
        w("")

    # --- security gate ----------------------------------------------------------
    w("## Security gate")
    w("")
    w("| Proposal | Energy Wh | Security | Outcome |")
    w("|---|---|---|---|")
    for row in data["security_gate"]["proposals"]:
        w(f"| {row['recommendation']} | {row['energy_wh']:+.3f} | {row['security']} | "
          f"{row['state'].replace('_', ' ')} |")
    if not data["security_gate"]["proposals"]:
        w("| — | — | — | no proposals in this window |")
    w("")

    # --- finops -------------------------------------------------------------------
    finops = data["finops"]
    w("## Energy FinOps")
    w("")
    w("| Component | Cost |")
    w("|---|---|")
    for name, value in finops["composition"].items():
        w(f"| {name.replace('_', ' ')} | {finops['currency']} {value:,.4f} |")
    w(f"| **total** | **{finops['currency']} {finops['total']:,.4f}** |")
    w("")
    w(f"{finops['convention']}. Cooling electricity inside the energy line: "
      f"{finops['currency']} {finops['cooling_energy_within_energy']:,.4f}.")
    w("")
    units = finops["unit_costs"]
    w(f"- {finops['currency']} {units['cost_per_1k_tokens']:.5f} per 1k tokens "
      f"(of which {units['energy_cost_per_1k_tokens']:.5f} is electricity)")
    w(f"- {finops['currency']} {units['cost_per_successful_task']:.5f} per successful task")
    if finops["carbon_g"] is not None:
        w(f"- {finops['carbon_g']:,.1f} gCO₂e — {finops['carbon_note']}")
    w("")

    # --- audit ----------------------------------------------------------------------
    audit = data["audit"]
    w("## Audit chain")
    w("")
    w(f"{audit['entries']} entries, chain **{'valid' if audit['chain_valid'] else 'BROKEN'}**, "
      f"head `{audit['head'][:24]}…`, policy version `{audit['policy_version']}`.")
    w("")
    w(f"_{audit['note']}._")
    w("")

    # --- benchmark and experiments -----------------------------------------------------
    bench = data["benchmark"]
    w("## Benchmark")
    w("")
    if bench["status"] != "available":
        w(f"**STATUS: {bench['status']}** — {bench['note']}")
    else:
        w(f"From a previous run ({bench['generated_at']}, provenance `{bench['provenance']}`):")
        w("")
        w("| Strategy | Wh/1k tokens | p95 ms | SLO met |")
        w("|---|---|---|---|")
        for row in bench["rows"]:
            w(f"| {row.get('strategy')} | {row.get('wh_per_1k_tokens', 0):.4f} | "
              f"{row.get('p95_latency_ms', 0):,.0f} | "
              f"{'yes' if row.get('slo_met') else 'no'} |")
    w("")
    experiments = data["experiments"]
    w("## Experiments")
    w("")
    if experiments["status"] != "available":
        w(f"**STATUS: {experiments['status']}** — {experiments['note']}")
    else:
        for e in experiments["experiments"]:
            finding = e["findings"][0] if e["findings"] else "(no finding recorded)"
            w(f"- **{e['experiment']}** ({e['provenance']}): {finding}")
    w("")

    # --- provenance ----------------------------------------------------------------------
    prov = data["provenance"]
    w("## Provenance summary")
    w("")
    w("| Section | Provenance |")
    w("|---|---|")
    for section, value in prov["sections"].items():
        w(f"| {section} | {value} |")
    w("")
    w(f"Weakest provenance across attributed components: **{prov['weakest']}**. "
      f"Measured fraction: {prov['measured_fraction']:.1%}. "
      f"Calibrated fraction: {prov['calibrated_fraction']:.1%}.")
    w("")
    w(f"**{prov['declaration']}**")
    w("")

    # --- reproduction -----------------------------------------------------------------------
    w("## Reproduction")
    w("")
    w("```bash")
    w(run["reproduction_command"])
    w("```")
    w("")
    w(f"- run id `{run['run_id']}`, seed `{run['seed']}`, configuration hash "
      f"`{run['configuration_hash']}`")
    w(f"- WATTS {run['version']}, revision `{run['code_revision']}`, policy "
      f"`{run['policy_version']}`")
    w(f"- Python {run['python']} on {run['platform']}")
    w("")
    w("The same seed and configuration reproduce this run exactly. Any difference in the "
      "numbers above means the code, the configuration or the seed changed — which is the "
      "point of printing all three.")
    w("")
    w(f"_Generated {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())} by WATTS "
      f"{run['version']}._")
    return "\n".join(out)
