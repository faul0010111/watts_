# Command Center

Two builds of the same dashboard:

**Static (this repository).** `python watts.py dashboard` runs the digital twin, pushes the
result through the real pipeline — efficiency analysis, anomaly detection, forecasting,
policy evaluation, audit — and writes one self-contained HTML file to
`apps/dashboard/dist/index.html`. No server, no database, no build step. Open it in a
browser.

**Live (production target).** Next.js + TypeScript + Tailwind reading the API over
WebSockets, tenant-scoped. Not implemented here; see `docs/ARCHITECTURE.md`.

## The one design rule

**Colour encodes provenance, not decoration.** Green is a hardware counter, blue is
arithmetic over measured values, amber is a declared coefficient, violet is the digital
twin. The legend is the first thing on the page, and no number appears without its label.
An operator should never have to ask whether a figure was measured or modelled.

## What the page shows

| Panel | Answers |
|---|---|
| Power bus | where the facility's watts went: accelerators, hosts, cooling — and the PUE that falls out of it |
| Power timeline | how that split moved over the window |
| Cost of a thousand tokens | energy, accelerator time, money, carbon — each labelled by provenance |
| Energy per token by task | which task classes are expensive, per unit of work |
| Thermal state | peak temperature per accelerator against the throttle point, and the clock factor it cost |
| Forecast | facility power with an 80% interval from walk-forward residuals |
| Objectives and budgets | latency, availability, energy per successful request, energy and token budgets |
| Anomaly strip | appears only when energy per unit of work breaks its baseline, with ranked causes and evidence |
| Proposals | each with its five-axis impact and its policy outcome — nothing is applied |
| Audit chain | entry count, chain validity, head digest |

## Building

```bash
python watts.py dashboard                                   # defaults
python apps/dashboard/build_static.py --rps 3 --gpus 8 --duration 900 --out /tmp/watts.html
```

`template.html` holds the markup, styles and rendering code; `build_static.py` produces the
data and embeds it as JSON. To change what is shown, edit both.
