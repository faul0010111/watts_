# Energy model

```
Total facility energy =
    GPU energy
  + CPU energy
  + Memory energy
  + Network energy
  + Storage energy
  + Cooling and conversion overhead
```

Units throughout: power in watts, energy in watt-hours, time in seconds.

## Provenance

Every energy figure travels inside an `EnergyValue` (`services/energy_engine/value.py`):

```python
EnergyValue(value, unit, source, provenance, timestamp, confidence, calibration_id)
```

The envelope is deliberately awkward to strip. Arithmetic on energy values returns energy
values and keeps the **weakest** provenance of its inputs, so adding a metered reading to a
modelled one cannot produce a measurement; scaling a measurement yields `derived`, not
`measured`; and `require(Provenance.MEASURED)` raises rather than letting a caller relabel
evidence it did not get.

The four labels (`services/energy_engine/model.py`):

| Label | Meaning | Typical source |
|---|---|---|
| `measured` | read from a hardware counter | NVML/DCGM power, RAPL, PDU, BMS |
| `derived` | arithmetic over measured values only | integrating a measured power series |
| `estimated` | a model with declared coefficients | the per-core CPU model below |
| `simulated` | produced by the digital twin | every number in `simulation/` |

An `EnergyBreakdown` reports the **weakest** provenance among its parts and a
`measured_fraction`: the share of IT energy actually backed by metering. A consumer that
needs evidence can filter on it. A number that mixes a meter reading with a coefficient is
never presented as a measurement.

## GPU energy

Measured wherever possible: sample device power (NVML/DCGM) and integrate trapezoidally.

```
E_gpu (Wh) = ∫ P(t) dt / 3600
```

Sampling at 100 ms or faster matters: LLM serving power swings between prefill bursts and
decode steps, and a 10 s scrape will average away most of the signal you are trying to
attribute.

Device power excludes the rest of the node. Where a node-level meter exists, prefer it and
treat the GPU figure as a component.

## Decomposing one request

```
E_request = E_baseline + E_prefill + E_decode + E_memory + E_network + E_storage + E_shared
```

`decompose_request_energy` subtracts the non-compute components first, because they come
from their own models and do not scale with tokens. Whatever remains is compute, and *that*
is what the prefill/decode weights divide. Doing it the other way round — splitting
everything by token weight and labelling part of it "network" — would be arithmetic dressed
up as physics.

When the measurement covers only the serving window, which is the normal case because that
is what a GPU meter reports, the split inside that window is a model.
`decomposition_is_attribution` says so on every result, and each component carries its own
provenance so a consumer never has to infer it.

## Estimated components

Used only when the component cannot be measured. All coefficients are **declared
defaults**, not empirical findings, and they live in `EnergyModelConfig` so you can
replace them:

| Component | Model | Default |
|---|---|---|
| CPU | `cores × (idle_w + util × (busy_w − idle_w))` | 2 W idle, 12 W busy per core |
| Memory | `GB × W/GB` | 0.35 W/GB |
| Network | `GB × J/GB` | 2,000 J/GB |
| Storage | `GB_read × J/GB + GB_written × J/GB` | 300 / 900 J/GB |

Prefer RAPL for CPU and DRAM when it is available; it moves those components from
`estimated` to `measured` and materially raises `measured_fraction`.

## Cooling and PUE

```
PUE = total facility energy / IT equipment energy
cooling_and_conversion = IT × (PUE − 1)
```

`compute_pue()` refuses to report a value it cannot justify. With a facility meter the
result is `derived`; with only a modelled cooling load it is `estimated` and says so in
`basis`. It also rejects a facility total below the IT total, which usually means the
metering boundaries do not match.

In the twin, PUE is **derived, not assumed**: cooling power comes from the heat actually
removed at a coefficient of performance that degrades as the hottest device runs further
above the inlet temperature, plus a fixed per-accelerator overhead for PDU and UPS losses,
lighting, network and storage.

WATTS reports current, historical, estimated and anomalous PUE. The anomaly test is a
robust median/MAD outlier test (`pue_anomaly`), not a fixed threshold: what matters is a
change against your own baseline.

## Thermal coupling

Heat is not a side effect in this model, it is a feedback loop:

```
power → temperature → leakage (more watts for the same work)
                   → throttling (less work for the same watts)
                   → cooling demand → facility overhead → PUE
```

The twin models a lumped thermal mass with conductive removal to the inlet, a leakage term
that raises static power with temperature, and clock throttling above the throttle point.
Experiment 06 sweeps cooling capability and shows the split: with cooling degraded to 35%,
GPU energy per 1k tokens moves a few percent while *facility* energy per 1k tokens moves
tens of percent. A GPU-only view of efficiency misses most of the damage.

## Carbon

Carbon is strictly downstream of energy and never conflated with it:

```
gCO2e = kWh × grid carbon intensity (gCO2e/kWh, supplied by the operator)
```

`TokenEconomics` returns `None` for carbon when no intensity source is configured rather
than substituting an average, and every carbon figure it emits carries the source name and
a note that the value is derived, not measured.

## Where the numbers come in

One energy engine, several sources of truth (`services/telemetry/adapters.py`):

```
      Simulation adapter (digital twin)   ─┐
      NVML / DCGM / external meter        ─┴→  Energy engine → everything downstream
```

Every adapter returns the same `PowerSample` stream and declares its own provenance, so
swapping the twin for a real accelerator changes the evidence attached to every number and
nothing else. The hardware adapters **refuse to start** rather than degrade quietly: an
adapter that cannot reach NVML must not fall back to a model, because the difference between
measured and estimated is the thing WATTS exists to preserve.

## Calibration

The defaults let you compare strategies. They do not let you quote watt-hours. The full
procedure, the error analysis and the status values are in `docs/CALIBRATION.md`; in short:

1. Fix one model, one accelerator type and one serving configuration.
2. Run `experiments/exp01_context_impact.py` against the real endpoint with a DCGM
   exporter attached, sweeping input tokens at a constant output length. The slope of
   Wh/request against input tokens is your **prefill coefficient**.
3. Run `experiments/exp02_output_limit.py` the same way, constant input, sweeping output.
   The slope is your **decode coefficient**.
4. Write both into a `ModelProfile`, set `source: "calibrated"` and record the hardware,
   serving runtime and date in `calibrated_on`.
5. Re-run the benchmark. Provenance on the affected numbers changes from `simulated` to
   `measured`/`derived`, and the disclaimer in the report changes with it.

Until step 4 is done, `CalibrationRegistry.summary()` lists every profile still running on
declared defaults, and `ModelProfile.absolute_wh()` returns `None` rather than a number. The
demo report and the Command Center both print that list. Show it in anything you publish.
