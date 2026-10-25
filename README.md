# VWAPS_auditable — Simple, auditable VWAP curve filling

Sibling of `VWAPS` with **the same infrastructure**: commands, `config.toml`, Excel/CSV mapping with
`use = fill/off`, per-product configuration, outputs, backtest, tune and MLflow. The **gap filling**
is simpler:

```
gap_price = EEX_gap + λ · closeness-weighted mean of (VWAP − EEX) over the day's own VWAPs
```

- Four main parameters: `tau_log`, `shrink_k`, `volume_weight` and `history_days` (optional stateless history).
- Additive core without auto mode, correlation or shape; the fallback without own information uses a short EEX EWMA.
- Every price carries an `explanation` and an `audit` or `aggregation_audit` breakdown.
- Every empty cell carries a `missing_reason`.

## Quarter–month consistency

With the `layers.coherence` layer enabled, when a quarter **has no own VWAP** and its three
complete months have prices for the same curve and reference date, its final price is their
**delivery-hour-weighted mean**:
`quarter_price = Σ(month_price × month_hours) / Σ(month_hours)`. Months can be own or estimated;
they must cover the quarter exactly, have positive hours and not be excluded as anchors by the
filters. Otherwise the usual method's result is retained.

The layer is optional and enabled by default to preserve the previous behaviour. In `config.toml`:

```toml
[layers]
coherence = true  # false: retain the usual quarter estimate
arbitrage = false # independent reconstruction of gaps without an EEX reference
```

`true` enables the layer and `false` disables it, without quotes in TOML. With `coherence = false`,
the usual estimate is not replaced by the monthly mean.
If enabled, the separate `arbitrage` layer can still reconstruct gaps from available contracts.
In `individual` mode, the mapping's `layer_coherence` column enables or disables the layer per
product; an empty cell inherits the TOML setting.
The change applies to subsequent calculations. To recalculate previously published dates, use
`python run.py refill --from YYYY-MM-DD`; `catchup` only processes pending dates.

It uses `source = months_aggregate`, `estimation_method = quarter_from_months` and an
`aggregation_audit` breakdown. It does not adjust months or enforce day/week/season/year consistency.
It can use own months outside requested tenors, but does not estimate unrequested months or expand
offered tenors. The flow is: usual filling → optional coherence → optional arbitrage → consistency
report. Running coherence before arbitrage prevents circular reconstructions.

**If the quarter and its months all have own VWAPs and disagree, every own price is preserved.**
Trades may have occurred at different times: preserving these prices and requiring equality
simultaneously is impossible. The consistency report records the discrepancy when the quarter and
all three months are published in the curve, also with `coherence = false`, without changing own
VWAPs. This rule guarantees the quarter–month relation only for aggregated quarters; it does not
guarantee simultaneous coherence of the whole curve or, by itself, lower estimation error.

Details: [ALGORITMO.md](ALGORITMO.md) (Spanish, full) and [ALGORITHM.md](ALGORITHM.md) (English).
Results against the original method: [ANALISIS_COMPARATIVA.md](ANALISIS_COMPARATIVA.md).
First run with official data and how to evaluate (Spanish): [PRIMER_USO.md](PRIMER_USO.md).

## Quick start

```
uv sync
python run.py mapping
python run.py refill --from 2026-08-10
python run.py daily
python run.py backtest
```

## Checks

```
uv run pytest
python research/check_daily.py --offset 0
python compare_methods.py --vwap "path/to/your_vwaps.xlsx"
python compare_methods.py --synthetic
```
