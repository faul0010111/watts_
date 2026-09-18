"""Calibration report.

Written so that a reviewer can decide, without reading any code, whether the numbers this
calibration produces may be quoted as watt-hours.
"""
from __future__ import annotations

import time

from .registry import CalibrationRecord


def calibration_report(record: CalibrationRecord) -> str:
    c, v = record.coefficients, record.validation
    cov = record.coverage or {}
    created = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(record.created_at))

    header = {
        "calibrated": "CALIBRATED - absolute watt-hours from this model may be quoted, "
                      "within the sampled range.",
        "self-consistency-check": "NOT A CALIBRATION - this session was produced by the "
                                  "digital twin. It shows the fit reproduces the twin, "
                                  "which says nothing about real hardware.",
        "calibration-failed": "FAILED - the fit does not describe the measurements well "
                              "enough to be used.",
    }[record.status]

    lines = [
        f"# Calibration report - {record.model}",
        "",
        f"**{header}**",
        "",
        f"- calibration id: `{record.calibration_id}`",
        f"- session: `{record.session_id}`",
        f"- hardware: {record.hardware}",
        f"- runtime: {record.runtime}",
        f"- provenance of samples: {record.provenance.value}",
        f"- produced: {created}",
        "",
        "## Coefficients",
        "",
        "| Term | Value | Meaning |",
        "|---|---|---|",
        f"| baseline | {c.baseline_wh:.6f} Wh/request | fixed cost before a single token moves |",
        f"| prefill | {c.prefill_wh_per_token:.8f} Wh/input token | marginal cost of prompt |",
        f"| decode | {c.decode_wh_per_token:.8f} Wh/output token | marginal cost of generation |",
        f"| ratio | {c.decode_prefill_ratio:.1f}× | how much more an output token costs |",
        "",
        f"Method: {c.method}. R² = {c.r_squared:.4f} over {c.samples} samples.",
        "",
        "## Error analysis",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| MAE | {v.mae:.6f} Wh |",
        f"| RMSE | {v.rmse:.6f} Wh |",
        f"| mean relative error | {v.mean_relative_error:.2%} |",
        f"| bias | {v.bias:+.6f} Wh |",
        f"| 95% interval for bias | [{v.ci95_bias[0]:+.6f}, {v.ci95_bias[1]:+.6f}] Wh |",
        f"| residual σ | {v.residual_std:.6f} Wh |",
        f"| validation | {v.method} |",
        "",
    ]

    if v.in_sample:
        lines.append("> These residuals are in-sample and therefore optimistic. Hold out a "
                     "portion of the session, or run a second session, before trusting them.")
        lines.append("")

    if v.systematically_biased:
        direction = "over" if v.bias > 0 else "under"
        lines.append(f"> The fit {direction}-predicts systematically, not just noisily. "
                     "Something the model omits is correlated with the workload - batch "
                     "size and context length are the usual suspects.")
        lines.append("")

    if cov.get("samples"):
        lines += [
            "## Coverage",
            "",
            f"- {cov['samples']} samples across {cov.get('distinct_shapes', 0)} distinct shapes",
            f"- input tokens {cov['input_tokens']['min']}–{cov['input_tokens']['max']}",
            f"- output tokens {cov['output_tokens']['min']}–{cov['output_tokens']['max']}",
            f"- batch size {cov['batch_size']['min']}–{cov['batch_size']['max']}",
            "",
            "Predictions outside these ranges are extrapolation. WATTS flags them rather "
            "than silently reporting a confident number.",
            "",
        ]

    if record.notes:
        lines += ["## Notes", "", record.notes, ""]

    return "\n".join(lines)
