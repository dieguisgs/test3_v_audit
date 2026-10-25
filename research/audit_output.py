"""Sanity audit of a filled_history.csv: looks for anything odd a reviewer would flag.

uv run python research/audit_output.py [output/filled_history.csv]
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "output" / "filled_history.csv"
    f = pd.read_csv(path, keep_default_na=False, na_values=[""], low_memory=False)
    f["reference_date"] = pd.to_datetime(f["reference_date"])
    est = f[~f["source"].isin(["own", "missing"])].copy()
    est["adj"] = est["price"] - est["eex_settle"].fillna(est["eex_proxy"])
    print(f"rows={len(f)}  estimated={len(est)}  own={int((f.source == 'own').sum())}  missing={int((f.source == 'missing').sum())}")

    print("\n1. Integrity")
    keys = ["reference_date", "product", "region", "unit", "tenor"]
    print("   duplicate keys:", int(f.duplicated(keys).sum()))
    print(
        "   non-missing rows without finite price:",
        int((f.source != "missing").sum() - f.loc[f.source != "missing", "price"].notna().sum()),
    )
    print("   confidence outside [0,1]:", int(((f.confidence < 0) | (f.confidence > 1)).sum()))
    print("   negative prices:", int((f.price < 0).sum()), "| zero-hour rows priced:", int(((f.hours <= 0) & f.price.notna()).sum()))

    print("\n2. Size of the adjustment over EEX (EUR/MWh), by source")
    print(est.groupby("source")["adj"].describe(percentiles=[0.05, 0.5, 0.95]).round(2).to_string())
    rel = (est["adj"].abs() / est["eex_settle"].abs().clip(lower=10)).fillna(0)
    print("   cells moved more than 5% from EEX:", int((rel > 0.05).sum()))
    print(est.loc[rel.nlargest(5).index, ["reference_date", "product", "tenor", "source", "eex_settle", "price", "anchors"]].to_string())

    print("\n3. Day-to-day jump of the same delivery contract vs the EEX jump (estimated cells)")
    g = f[f.price.notna()].sort_values("reference_date")
    g["contract"] = g["product"] + "|" + g["kind"] + "|" + g["delivery_start"].astype(str)
    g["dp"] = g.groupby("contract")["price"].diff()
    g["de"] = g.groupby("contract")["eex_settle"].diff()
    g["prev_src"] = g.groupby("contract")["source"].shift()
    j = g[(g.source != "own") & (g.prev_src != "own") & g.dp.notna() & g.de.notna()]
    j = j.assign(extra=(j.dp - j.de).abs())
    print(j["extra"].describe(percentiles=[0.5, 0.95, 0.99]).round(2).to_string())
    print(j.nlargest(5, "extra")[["reference_date", "product", "tenor", "source", "prev_src", "dp", "de"]].round(2).to_string())

    print("\n4. Coherence: quarter vs hour-weighted mean of its three months (same day, same product)")
    m = f[(f.kind == "Month") & f.price.notna()].copy()
    q = f[(f.kind == "Quarter") & f.price.notna()].copy()
    m["qstart"] = pd.to_datetime(m["delivery_start"]).dt.to_period("Q").dt.start_time
    q["qstart"] = pd.to_datetime(q["delivery_start"])
    mm = m.groupby(["reference_date", "product", "qstart"]).apply(
        lambda x: pd.Series({"n": len(x), "mean": (x.price * x.hours).sum() / x.hours.sum()}), include_groups=False
    )
    mm = mm[mm.n == 3].reset_index()
    c = mm.merge(q[["reference_date", "product", "qstart", "price", "source"]], on=["reference_date", "product", "qstart"])
    c["gap"] = (c["price"] - c["mean"]).abs()
    print(
        f"   quarters with all 3 months: {len(c)} | mean |Q - months| = {c.gap.mean():.3f} | p95 = {c.gap.quantile(0.95):.3f} | max = {c.gap.max():.3f}"
    )
    print(c.groupby("source")["gap"].mean().round(3).to_string())

    print("\n5. Proxies: shape vs its parent (weekend below its week? quarter/season within plausible range?)")
    px = f[f.source == "eex_proxy"]
    print(px.groupby("eex_proxy_rule")["price"].describe().round(2).to_string())
    print("\n6. Source mix by product")
    print(pd.crosstab(f["product"], f["source"], normalize="index").round(3).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
