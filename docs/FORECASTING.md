# Forecasting

WATTS forecasts five things, because they answer different operational questions:

| Target | Question |
|---|---|
| energy | will the energy budget hold? |
| tokens | is demand growing, and how fast? |
| GPU demand | will there be enough accelerators at peak? |
| cooling load | will the facility keep up, or will throttling start? |
| budget breach probability | how likely is the breach, not merely whether it is expected |

## Models

| Model | When it is the right tool |
|---|---|
| naive / seasonal naive | a strong daily or weekly cycle and little trend |
| Holt (damped) | the default: trend without a reliable season, and damping stops a short burst becoming an exponential forecast |
| feature model | when the drivers are known or planned — a token forecast, a deployment, a batch change |

The univariate forecasters answer "what does this series usually do next". That is enough
for a smooth load and useless the moment traffic shape changes, because energy is not an
autonomous process: it is produced by tokens, batching, utilisation and heat.

`services/forecasting/features.py` fits energy against those drivers by ordinary least
squares on standardised features, with three refusals built in:

* a feature that never varies is **dropped**, not given a meaningless coefficient;
* fewer samples than 5× the number of features **refuses to fit** rather than overfitting;
* collinear drivers raise rather than returning an unidentifiable model.

`FeatureModel.drivers` ranks features by standardised effect, so the answer to "which driver
moves energy most" does not depend on which happens to be measured in the largest units. The
model's own dictionary carries the caveat that association is not causation: temperature
correlating with energy may be the thermal feedback loop, or may be the time of day.

## Intervals

All intervals come from residuals, never from a formula assumed in advance:

* univariate: empirical quantiles of walk-forward residuals at each horizon;
* feature model: held-out residual spread.

Intervals widen with horizon because the residuals do. They do **not** cover a change in the
workload's character — a new tenant, a model swap, a marketing launch — and the forecast
says so rather than implying a confidence it does not have.

## Validation

`backtest()` runs walk-forward validation and reports MAE, MAPE and **interval coverage**.

Coverage is the one to read. An 80% interval that contains the truth 45% of the time is not
a conservative forecast, it is a wrong one, and the point estimate beside it should not be
trusted either. Coverage materially above 80% means the intervals are too wide to be useful
for planning.

## Breach probability

A central projection that lands just under a limit is not reassuring. What an operator needs
is the chance of crossing it.

```
P(breach) = 1 − Φ((limit − central) / σ),   σ from the forecaster's own interval
```

The normal approximation is stated in the output rather than hidden, and the severity bands
are deliberately plain: *expected* (≥80%), *likely enough to plan for* (≥30%), *unlikely but
possible* (≥5%), *negligible*.

Where a forecast could not be produced — too little history — WATTS reports
`insufficient_history` and the no-action projection lists the dimension as unavailable. It
never extrapolates by hand to fill a gap in a table.

## The no-action projection

Every optimisation proposal is implicitly a comparison against doing nothing, and that
comparison is usually left unstated. `services/optimization/no_action.py` makes it explicit:
where each tracked dimension lands on the current trajectory, with its interval, against its
limit, plus the thermal and SLO state.

It separates **breach expected** (the central path crosses the limit) from **breach
possible** (only the upper bound does). Those call for different responses, and collapsing
them into one alert wastes either attention or time.

A 5% saving is a win in a flat workload and irrelevant in one growing 30% a month. The
no-action projection is what tells you which you have.
