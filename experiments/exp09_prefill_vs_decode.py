"""Experiment 09 - prefill versus decode energy.

The claim that an output token costs more than an input token is repeated everywhere,
including in this repository's own documentation. This experiment exists to stop it being
an assumption: it varies the input/output ratio and lets the run produce the number.

Design. Four canonical shapes, from the WATTS V2 brief:

    100 in / 100 out    balanced
    1,000 in / 100 out  prompt-heavy
    100 in / 1,000 out  generation-heavy
    4,000 in / 1,000 out  long-context generation

Each is run across batch sizes and across seeds, holding everything else constant. From the
per-request energies a least-squares fit recovers the marginal cost of an input token and
of an output token, and the ratio between them is *derived from the run*, not asserted.

What this can and cannot show. It is the twin, so the ratio it recovers is the one the twin
was built with - this is a consistency check on the measurement procedure, not evidence
about any real model. The same script pointed at metered hardware (docs/CALIBRATION.md)
produces the real number, which is exactly why the procedure is worth testing here first.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    DEFAULT_SEEDS, ROOT, ServingConfig, manifest, pct, run_seeds, single_task_workload, write,
)

sys.path.insert(0, str(ROOT))

from services.calibration import (  # noqa: E402
    CalibrationSample, MeasurementSession, fit_coefficients, validate,
)
from services.energy_engine.model import Provenance  # noqa: E402

SHAPES = [(100, 100), (1_000, 100), (100, 1_000), (4_000, 1_000)]
BATCHES = [1, 8]


def main() -> None:
    rows = []
    session = MeasurementSession(
        model="watts-sim-medium", hardware="digital twin (no hardware)",
        runtime="watts simulation", adapter="watts-digital-twin",
        notes="samples produced by exp09; simulated, so any fit is a consistency check")

    baseline = None
    for batch in BATCHES:
        for inp, out in SHAPES:
            serving = ServingConfig(max_batch=batch, max_wait_s=0.2 if batch > 1 else 0.0,
                                    default_model="watts-sim-medium")
            workload = single_task_workload(inp, out, duration_s=240.0, rps=1.5, tier=3)
            result = run_seeds(serving, workload, seeds=DEFAULT_SEEDS)
            wh_per_request = result["wh_per_request"]
            if baseline is None:
                baseline = wh_per_request
            rows.append({
                "shape": f"{inp:,} in / {out:,} out",
                "batch": batch,
                "input_tokens": inp,
                "output_tokens": out,
                "wh_per_request": wh_per_request,
                "wh_per_1k_tokens": result["wh_per_1k_tokens"],
                "wh_per_output_token": wh_per_request / out,
                "p95_latency_ms": result["p95_latency_ms"],
                "vs_balanced": pct(wh_per_request, baseline),
                "spread": result["wh_per_request__spread"],
            })
            session.add(CalibrationSample(
                input_tokens=inp, output_tokens=out, measured_wh=wh_per_request,
                batch_size=1, latency_ms=result["p95_latency_ms"],
                provenance=Provenance.SIMULATED, source="watts-digital-twin"))
    session.close()

    coefficients = fit_coefficients(session, per_request=True)
    validation = validate(coefficients, session.samples, in_sample=True)

    # Findings are written from the fitted numbers, so they cannot contradict the table.
    ratio = coefficients.decode_prefill_ratio
    prompt_heavy = next(r for r in rows if r["input_tokens"] == 1_000 and r["batch"] == 1)
    generation_heavy = next(r for r in rows if r["output_tokens"] == 1_000
                            and r["input_tokens"] == 100 and r["batch"] == 1)
    long_context = next(r for r in rows if r["input_tokens"] == 4_000 and r["batch"] == 1)
    batched = next(r for r in rows if r["input_tokens"] == 4_000 and r["batch"] == 8)

    direction = ("more" if ratio > 1 else "less")
    findings = [
        (f"Fitted on this run, one output token costs {ratio:.1f}× what one input token "
         f"costs — output tokens are {direction} expensive here. The fit recovers "
         f"{coefficients.prefill_wh_per_token:.3e} Wh per input token and "
         f"{coefficients.decode_wh_per_token:.3e} Wh per output token, with a per-request "
         f"baseline of {coefficients.baseline_wh:.5f} Wh (R² {coefficients.r_squared:.3f})."),
        (f"Swapping the ratio at constant total tokens is not neutral: 100/1,000 costs "
         f"{generation_heavy['wh_per_request']:.4f} Wh per request against "
         f"{prompt_heavy['wh_per_request']:.4f} Wh for 1,000/100 — "
         f"{pct(generation_heavy['wh_per_request'], prompt_heavy['wh_per_request']):+.0f}%."),
        (f"Long context with generation (4,000/1,000) reaches "
         f"{long_context['wh_per_request']:.4f} Wh per request but only "
         f"{long_context['wh_per_1k_tokens']:.4f} Wh per 1k tokens: per-token figures fall "
         f"as context grows while per-request cost rises, which is why WATTS reports both."),
        (f"Batching to 8 changes the picture for the same shape: "
         f"{batched['wh_per_1k_tokens']:.4f} against {long_context['wh_per_1k_tokens']:.4f} "
         f"Wh per 1k tokens ({pct(batched['wh_per_1k_tokens'], long_context['wh_per_1k_tokens']):+.0f}%), "
         f"so any prefill/decode coefficient is only valid at the batch size it was fitted at."),
        (f"In-sample error: MAE {validation.mae:.5f} Wh, bias {validation.bias:+.5f} Wh. "
         f"These samples came from the simulator, so this is a check that the fitting "
         f"procedure recovers coefficients — not evidence about any real model."),
    ]

    write(
        "exp09_prefill_vs_decode",
        "Experiment 09 - prefill versus decode energy",
        "What does an output token actually cost, relative to an input token?",
        rows,
        [("shape", "Shape", "{}"), ("batch", "Batch", "{:d}"),
         ("wh_per_request", "Wh/request", "{:.5f}"),
         ("wh_per_1k_tokens", "Wh/1k tokens", "{:.5f}"),
         ("wh_per_output_token", "Wh/output token", "{:.3e}"),
         ("p95_latency_ms", "p95 ms", "{:,.0f}"),
         ("vs_balanced", "vs balanced", "{:+.0f}%"),
         ("spread", "seed spread", "{:.5f}")],
        {"shapes": SHAPES, "batches": BATCHES, "seeds": list(DEFAULT_SEEDS),
         "model": "watts-sim-medium", "duration_s": 240.0, "rps": 1.5, "seed": DEFAULT_SEEDS[0],
         "fit": coefficients.as_dict(), "validation": validation.as_dict()},
        findings,
        experiment_manifest=manifest(
            "exp09_prefill_vs_decode", version="1.0", seed=DEFAULT_SEEDS[0],
            configuration={"shapes": SHAPES, "batches": BATCHES, "seeds": list(DEFAULT_SEEDS)},
            inputs={"token_shapes": len(SHAPES), "batch_sizes": len(BATCHES),
                    "runs": len(SHAPES) * len(BATCHES) * len(DEFAULT_SEEDS)},
            method=("sweep input/output ratio and batch size at fixed arrival rate; recover "
                    "marginal token costs by ordinary least squares over per-request energy"),
            metrics=["wh_per_request", "wh_per_1k_tokens", "wh_per_output_token",
                     "decode_prefill_ratio"],
            outputs=["experiments/results/exp09_prefill_vs_decode.json",
                     "experiments/results/exp09_prefill_vs_decode.md"]))


if __name__ == "__main__":
    main()
