# VWAPS_auditable — Simple, auditable VWAP curve filling

Sibling of `VWAPS` with **the same infrastructure**: commands, `config.toml`, Excel/CSV mapping with
`use = fill/off`, per-product configuration, outputs, backtest, tune and MLflow. The **gap filling**
is simpler:

```
gap_price = EEX_gap + λ · closeness-weighted mean of (VWAP − EEX) over the day's own VWAPs
```

- Three parameters (`tau_log`, `shrink_k`, and `history_days` for an optional stateless history).
- No ratio or auto mode, correlation, shape or smoothing.
- Every price carries an `explanation` and an `audit` breakdown.
- Every empty cell carries a `missing_reason`.

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
