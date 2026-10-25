# VWAPS_auditable — Curve filling algorithm

> **In one sentence:** EEX gives the **shape** of the curve; our VWAPs give the **level**. Each gap is
> EEX plus the weighted mean deviation (VWAP − EEX) of our VWAPs **close to it on the curve**, scaled
> by how much evidence there is. With no VWAP nearby the adjustment fades out and the price is EEX.

Full detailed version (Spanish): [ALGORITMO.md](ALGORITMO.md). Comparison with the original method:
[ANALISIS_COMPARATIVA.md](ANALISIS_COMPARATIVA.md).

## Formula

For a target contract **t** without own VWAP and the day's own VWAPs **i**:

```
deviation_i = VWAP_i − EEX_i                                  (EUR/MWh)
x           = ln(1 + days to the middle of delivery)
w_i         = exp(−|x_i − x_t| / tau_log)                     closeness
              × (volume_i / day's median volume) ^ volume_weight   (more volume, less noise)
local       = Σ w_i · deviation_i / Σ w_i                     weighted mean deviation
trust λ     = Σ w_i / (Σ w_i + shrink_k)                      in [0, 1)
price_t     = EEX_t + λ · local
```

Defaults: `tau_log = 0.7` (reach along the curve), `shrink_k = 1.0` (evidence needed), `volume_weight = 0.5`
(4x volume → 2x weight).

**Optional history** (`history_days`, 10 in `config.toml`, 0 = off). The share of trust that today's
VWAPs do not cover goes to what the VWAPs said about the **same contract** in the last N business
days. Each past day is computed with that day's VWAPs and allowed EEX, and recomputed from the input
on every run, so there is no stored state:

```
hist  = Σ W_d · local_d / (Σ W_d + shrink_k)
price = EEX_t + (W · local + shrink_k · hist) / (W + shrink_k)
```

The output adds `hist_deviation`, `hist_share`, `hist_days` and `hist_audit` (per-day JSON). Cells
moved only by history use `source = eex_history`. Cost when deviations do not persist: < 1 %. Gain
when they do: up to 16 %.

- It is additive, so there is no division by prices. Zero and negative prices are safe.
- It uses only the day's VWAPs and the EEX publication allowed by `eex.offset_days`, and the same
  publication for targets and deviations.
- It stores nothing between days, so `daily` equals `refill`, with or without history.

## Example (30/09/2026, DE Base)

| VWAP | Own | EEX | Deviation |
|---|---|---|---|
| M+1 | 105.00 | 104.00 | +1.00 |
| M+3 | 121.20 | 120.00 | +1.20 |

The gap is M+2 with EEX 118.00:
- weights: M+3 0.495 (68 %), M+1 0.237 (32 %);
- local deviation +1.135;
- λ = 0.423;
- **price 118.48**.

The output `explanation` reads:
`EEX 118.00 + 0.42 x +1.14 [M+3 +1.20 (68%), M+1 +1.00 (32%)] = 118.48`.

## Sources and empty cells

After neighbour filling and before optional arbitrage, the optional coherence layer
(`layers.coherence = true`, enabled by default) replaces a quarter without own VWAP by the delivery-hour-weighted mean of its
three complete months, when available for the same curve/date. The months can have own or estimated
prices, must cover the full quarter and have positive hours; months excluded as anchors by filters
are not used. Missing or ineligible components leave the usual result unchanged. With
`layers.coherence = false`, the usual estimate is retained without this replacement. The layer
applies independently of `layers.arbitrage`, which can still reconstruct gaps when coherence is off.
In `individual` mode, the mapping's `layer_coherence` column overrides the setting per product;
an empty cell inherits the TOML setting. Months, days, weeks, seasons, years and
own quarter prices are not changed. If own quarter/month VWAPs disagree, the consistency report
records the discrepancy, also when coherence is disabled: their trades can occur at different times, so keeping all own prices
cannot also guarantee equality.
Own months outside requested targets are eligible, but unrequested months are not estimated and
offered tenors are not expanded. The flow is usual filling → optional coherence → optional arbitrage
→ consistency report. Running coherence before arbitrage prevents circular reconstructions.

`estimation_method = quarter_from_months` identifies this calculation. `aggregation_audit` is a list
with each month's `tenor`, `delivery_start`, `delivery_end`, `price`, `hours`, `share` and `source`;
summing `price × share`
reproduces the quarter price. `explanation` states the calculation. Coherence is guaranteed for
these aggregated quarters only, and is not a claim of improved prediction accuracy.
`confidence` inherits the lowest of the three monthly scores, not a probability. `adjustment` is
the final quarter price minus its EEX/proxy reference, or empty without a reference. `audit`,
`hist_audit`, `local_deviation`, `trust` and numeric `hist_*` diagnostics are empty because the
neighbour calculation no longer produces the final price.

| `source` | Meaning |
|---|---|
| `own` | Own VWAP, as is |
| `eex_adjusted` | EEX + λ · nearby deviation |
| `eex_history` | No own VWAP today; recent days' VWAPs move it |
| `eex` | No comparable own VWAP today or in the history window: EEX |
| `months_aggregate` | Quarter without own VWAP: delivery-hour-weighted mean of its three complete eligible months when `layers.coherence = true`, independently of `layers.arbitrage` |
| `arbitrage` | Optional (`layers.arbitrage`): rebuilt from filled contracts |
| `eex_proxy` | EEX does not quote the contract: parent + EWMA sibling shape (spread for short, ratio for quarters/seasons, neighbour shape for months) |
| `eex_donor` | The market has no EEX publication that day: a neighbouring market's curve gives the shape, own VWAPs the level |
| `missing` | Only `zero_delivery_hours`, or no EEX publication in any configured market |

Copying the containing contract alone is 8.8–32 EUR/MWh off. Adding the sibling or neighbour shape
brings it down to 3–15 EUR/MWh, so proxies carry low confidence (see ALGORITMO.md section 6c).

## What was removed and why

| Removed | Why |
|---|---|
| Ratio and `auto` mode | No measurable gain (< 2 %), and problems with zero or negative prices |
| Stateful history (EWMA, warmup, expiry) | Replaced by the stateless history above |
| Correlation and cross-product help | Complexity, hard to explain |
| Shape | Business decision |
| EWMA smoothing of EEX | Lags; worst with `offset_days = -1` |
| Kind-specific weights | Curve distance already covers them |

## Evidence

The benchmark uses real EEX dynamics: 8 markets, own VWAPs simulated from real daily moves, and
1 % VWAP noise. Mean error on filled gaps, in EUR/MWh:

| Method | offset 0 | offset −1 |
|---|---|---|
| Raw EEX | 1.79 | 1.80 |
| Spread + 5-day smoothing (VWAPS2) | 1.91 | 3.35 |
| Original core logic (additive/auto) | 1.38–1.41 | 1.43–1.44 |
| **This method** | **1.37** | **1.41** |

Scripts are in `research/`. See ALGORITMO.md for parameters, audit columns, commands and guarantees.
