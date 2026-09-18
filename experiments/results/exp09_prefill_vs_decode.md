# Experiment 09 - prefill versus decode energy

**Research question.** What does an output token actually cost, relative to an input token?

*Produced by the WATTS digital twin. Valid as a statement about the model under the stated configuration, not as a hardware measurement.*

| Shape | Batch | Wh/request | Wh/1k tokens | Wh/output token | p95 ms | vs balanced | seed spread |
|---|---|---|---|---|---|---|---|
| 100 in / 100 out | 1 | 0.01596 | 0.08008 | 1.596e-04 | 409 | +0% | 0.00008 |
| 1,000 in / 100 out | 1 | 0.03033 | 0.02756 | 3.033e-04 | 737 | +90% | 0.00007 |
| 100 in / 1,000 out | 1 | 0.14875 | 0.13520 | 1.487e-04 | 41,924 | +832% | 0.00052 |
| 4,000 in / 1,000 out | 1 | 0.21198 | 0.04239 | 2.120e-04 | 105,681 | +1229% | 0.00059 |
| 100 in / 100 out | 8 | 0.01596 | 0.08008 | 1.596e-04 | 609 | +0% | 0.00008 |
| 1,000 in / 100 out | 8 | 0.03033 | 0.02756 | 3.033e-04 | 937 | +90% | 0.00007 |
| 100 in / 1,000 out | 8 | 0.12718 | 0.11561 | 1.272e-04 | 9,105 | +697% | 0.00633 |
| 4,000 in / 1,000 out | 8 | 0.14434 | 0.02883 | 1.443e-04 | 18,092 | +805% | 0.00678 |

## What the run shows

- Fitted on this run, one output token costs 12.5× what one input token costs — output tokens are more expensive here. The fit recovers 1.059e-05 Wh per input token and 1.323e-04 Wh per output token, with a per-request baseline of 0.00409 Wh (R² 0.938).
- Swapping the ratio at constant total tokens is not neutral: 100/1,000 costs 0.1487 Wh per request against 0.0303 Wh for 1,000/100 — +390%.
- Long context with generation (4,000/1,000) reaches 0.2120 Wh per request but only 0.0424 Wh per 1k tokens: per-token figures fall as context grows while per-request cost rises, which is why WATTS reports both.
- Batching to 8 changes the picture for the same shape: 0.0288 against 0.0424 Wh per 1k tokens (-32%), so any prefill/decode coefficient is only valid at the batch size it was fitted at.
- In-sample error: MAE 0.01236 Wh, bias -0.00000 Wh. These samples came from the simulator, so this is a check that the fitting procedure recovers coefficients — not evidence about any real model.

## Manifest

- experiment `exp09_prefill_vs_decode` v1.0 (schema 2)
- seed `11`, provenance `simulated`
- method: sweep input/output ratio and batch size at fixed arrival rate; recover marginal token costs by ordinary least squares over per-request energy
- metrics: wh_per_request, wh_per_1k_tokens, wh_per_output_token, decode_prefill_ratio

## Reproduce

```bash
make experiment EXP=exp09_prefill_vs_decode
```
