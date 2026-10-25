"""Full-program check of the optional history layer on the compare_methods.py --synthetic data.

Runs VWAPS_auditable refill + backtest with --history-days N for each case and scores it
against the same truth and the same hidden observations as VWAPS (original).

    uv run python research/history_program.py [--days 0,5,10,20]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))
import analyze_comparison as A  # noqa: E402

import compare_methods as C  # noqa: E402

KEYS = C.KEYS


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="0,5,10,20")
    ap.add_argument("--out", type=Path, default=ROOT / "comparison")
    a = ap.parse_args()
    out = a.out.resolve()
    mapping = next(out.glob("mapping.*"))
    eex = out / "eex_clean"
    rows = []
    for data in ("ratio", "additive"):
        truth = A.read(out / f"data_{data}" / "synthetic_truth.csv").rename(columns={"date": "reference_date"})
        vwap = out / f"data_{data}" / "synthetic_vwaps.csv"
        for offset in (0, -1):
            case = f"synthetic_{data}_offset{offset}"
            base = A.read(out / case / "vwaps" / "filled_history.csv")
            loo_v = A.read(out / case / "vwaps" / "backtest_loo.csv")
            loo_v = loo_v[loo_v.method == "pipeline_configured"][KEYS + ["own", "pred"]]
            for n in (int(x) for x in a.days.split(",")):
                folder = out / case / f"auditable_h{n}"
                cfg = C.make_config(ROOT, folder, vwap, mapping, eex, offset)
                C.run(ROOT, cfg, "refill", "--history-days", str(n))
                C.run(ROOT, cfg, "backtest", "--history-days", str(n))
                fb = A.read(folder / "filled_history.csv")
                m = (
                    base[KEYS + ["source", "price"]]
                    .rename(columns={"source": "src_v", "price": "vwaps"})
                    .merge(fb[KEYS + ["source", "price"]].rename(columns={"source": "src_a", "price": "aud"}), on=KEYS)
                    .merge(truth[KEYS + ["truth"]], on=KEYS)
                )
                m = m[(m.src_v != "own") & m.vwaps.notna() & m.aud.notna() & m.truth.notna()]
                loo_a = A.read(folder / "backtest_loo.csv")
                loo_a = loo_a[loo_a.method == "pipeline_configured"][KEYS + ["pred"]].rename(columns={"pred": "aud"})
                paired = loo_v.merge(loo_a, on=KEYS)
                rows.append(
                    {
                        "caso": case,
                        "history_days": n,
                        "huecos VWAPS": (m.vwaps - m.truth).abs().mean(),
                        "huecos auditable": (m.aud - m.truth).abs().mean(),
                        "LOO VWAPS": (paired.pred - paired.own).abs().mean(),
                        "LOO auditable": (paired.aud - paired.own).abs().mean(),
                        "celdas eex_history": int((fb.source == "eex_history").sum()),
                    }
                )
                print(rows[-1], flush=True)
    r = pd.DataFrame(rows)
    r.to_csv(out / "history_program.csv", index=False)
    print(r.round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
