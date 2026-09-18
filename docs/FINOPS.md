# Energy FinOps

Energy cost is not infrastructure cost. Conflating the two is how an energy saving becomes a
bill that did not move, and it happens in both directions:

* **On rented accelerators**, electricity is already inside the hourly rate. Saving a
  kilowatt-hour saves money only if it frees an accelerator-hour you then stop renting.
* **On owned hardware**, electricity is a real line item and the depreciation is sunk, so the
  arithmetic is the reverse.

The same optimisation is worth different amounts in the two cases. `services/finops/` keeps
the components apart and always prints which convention it used.

## Composition

```
total = energy + accelerator + host infrastructure + network + storage
```

with cooling electricity reported **both** inside the energy line (it is electricity) and as
its own figure, because facility and platform teams ask about it differently.

When `accelerator_rate_includes_energy` is set — the normal case for cloud instance
pricing — the energy line is displayed and excluded from the total. Adding it would bill the
same kilowatt-hour twice, and `CostBreakdown.note` says so in the output rather than in a
comment nobody reads.

## Rates are operator inputs

`CostRates` defaults to zero for every price. WATTS has no opinion about your electricity
tariff, your reserved-instance discount or your carbon intensity, and an unset rate produces
a zero line rather than a plausible-looking invented one.

```python
CostRates(
    electricity_per_kwh=0.12,
    accelerator_per_hour=2.40,
    host_per_accelerator_hour=0.30,
    carbon_g_per_kwh=380.0,          # optional; omit and no carbon figure is reported
    accelerator_rate_includes_energy=False,
)
```

## Unit costs

| Metric | Use |
|---|---|
| cost per 1k tokens | comparing models and strategies |
| energy cost per 1k tokens | the part an energy optimisation can actually move |
| **cost per successful task** | the one to optimise |
| cost per request | useful only when request shapes are comparable |

Cost per successful task is the honest figure for the same reason `Wh` per successful task
is: a system that halves per-request cost while doubling the requests a task needs has got
more expensive.

## Allocation

`allocate_costs` splits a window across tenants, models or task classes **by attributed
energy share**, because energy is the only thing WATTS measures per request.

Allocating by request count would charge a one-token classification the same as a
40,000-token reasoning call. That is how chargeback loses the trust of the teams being
charged, and once lost it does not come back.

The allocation inherits the attribution's provenance: on shared batched hardware the split
between tenants is a model, and the report says so. If you need a defensible allocation for
billing rather than for engineering, read `docs/RESEARCH.md` — fair allocation inside a
shared batch is an open question, not a solved one.

## Carbon

```
gCO₂e = kWh × grid carbon intensity (operator-supplied)
```

Strictly downstream of energy, never mixed into cost, and `None` when no intensity source is
configured rather than substituted with a regional average. Every carbon figure WATTS emits
carries the source name and a note that it is derived.

## What this does not do

* It does not model reserved-instance amortisation, spot pricing or committed-use discounts.
  Feed it the effective rate you actually pay.
* It does not claim a saving until the hardware is released or the rate changes. An idle
  accelerator you are still renting costs the same as a busy one.
* It does not produce carbon accounting claims or offsets. It reports energy multiplied by a
  number you supplied.
