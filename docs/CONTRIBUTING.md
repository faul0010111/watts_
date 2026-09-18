# Contributing

## Ground rules

1. **No invented numbers.** If a figure appears in code, a document or a test, it must be
   reproducible from a command in the README. Illustrative inputs (a price curve, a carbon
   curve) must be labelled as declared inputs where they appear.
2. **Label provenance.** Any new metric carries `measured`, `derived`, `estimated` or
   `simulated`, and aggregates report the weakest provenance of their parts.
3. **Security is not a trade-off axis.** A change that lets an energy saving weaken a
   control will be rejected, however large the saving.
4. **Policy changes land in both places.** A rule added to `policies/*.rego` needs its
   mirror in `services/security/policy.py` in the same commit, or
   `tests/test_policy_parity.py` fails. If the change alters attribution, policy outcomes,
   budget arithmetic or canonical JSON, regenerate `parity/vectors.json` in the same
   commit — and never to make a failing implementation pass.
5. **"Calibrated" is a claim about hardware.** Do not set `source="calibrated"` on a profile
   without a registered `MeasurementSession` behind it. `CalibrationRegistry` enforces this;
   do not work around it.
6. **Unknown is not the same as fine.** A configuration with no quality observation is
   inadmissible, not adequate. Any code path that treats missing evidence as a pass will be
   rejected.
7. **The reference implementation stays dependency-free.** `services/`, `simulation/`,
   `benchmarks/` and `experiments/` use only the standard library, so anyone can run and
   audit them. Optional extras go behind `[project.optional-dependencies]`.

## Getting set up

```bash
git clone <repo> && cd watts
make test        # 300+ tests, a few seconds
make demo
```

No virtualenv is required for the reference implementation. Use one if you install the
`dev` or `gpu` extras.

## Before opening a pull request

```bash
make lint
make test
make parity           # if you touched attribution, policy, budgets or the record schema
make experiments      # if you touched the twin, the engines or the experiments
python watts.py demo  # the report must still generate end to end
```

If you changed the twin's physics or the energy model, say so explicitly in the PR: those
changes move every number in the repository, and the experiment outputs should be
regenerated in the same commit.

## What is most useful

* **Calibration data.** A `ModelProfile` with `source: "calibrated"` and a documented
  `calibrated_on`, from real metered hardware, is the single most valuable contribution.
* **Accelerator adapters.** Anything that reports utilisation, power, temperature, clock
  and memory bandwidth fits the existing schema.
* **Policy rules.** New rules with tests, in both Rego and the Python mirror.
* **Serving-stack integrations.** vLLM, TGI, Triton, Ray Serve.
* **Adversarial telemetry tests.** Ways to fool the gateway's plausibility checks.

## Style

Code reads like an explanation. Module docstrings say what the module is for and what
guarantee it provides; comments explain *why* a constant or a check exists, not what the
line does. Prefer a named function over a clever expression.

Tests are the specification: each one states a property in its name
(`test_critical_inference_is_never_deferred`), not a mechanism.

## Reporting a security issue

Do not open a public issue for a vulnerability in the policy engine, the gateway's
validation, or the audit chain. Use the repository's private security advisory channel.
