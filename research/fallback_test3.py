"""Can the EEX proxy be made more accurate?  uv run python research/fallback_test3.py

Same exam as fallback_test2.py (rebuild contracts EEX does quote), with: number of
siblings, EWMA length, and ensembles (mean of two independent reconstructions).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
from fallback_test import AREAS, EEX, PARENT, curve, parent_price, slot
from fallback_test2 import donor_shape, ewma


def siblings(c, kind, s, how, n):
    vals = []
    for (k, ss), p in c.items():
        if k != kind or ss == s or slot(kind, ss) != slot(kind, s):
            continue
        pp = parent_price(c, kind, ss)
        if pp:
            vals.append((abs((ss - s).days), p - pp if how == "spread" else p / pp))
    if not vals:
        return None
    vals.sort()
    return float(np.mean([v for _, v in vals[:n]]))


def main():
    curves = {}
    for area in AREAS:
        d = pd.read_csv(EEX / area / "Base.csv").dropna(subset=["settlPx", "deliveryStart"])
        d["s"] = pd.to_datetime(d.deliveryStart)
        curves[area] = {t: curve(g) for t, g in d.groupby("tradeDate")}
    rows = []
    for area in AREAS:
        donor = curves["FR" if area == "DE" else "DE"]
        dates = sorted(curves[area])
        for i in range(20, len(dates)):
            t = pd.Timestamp(dates[i])
            c = curves[area][dates[i]]
            dc = donor.get(dates[i])
            for (kind, s), price in c.items():
                if kind not in PARENT or s <= t:
                    continue
                pp = parent_price(c, kind, s)
                if pp is None:
                    continue
                past = [curves[area][dates[j]] for j in range(i - 19, i + 1)]
                r = {"area": area, "kind": kind, "truth": price, "parent": pp}
                for n in (1, 2, 4):
                    sp = ewma([siblings(x, kind, s, "spread", n) for x in past], 20, 8)
                    ra = ewma([siblings(x, kind, s, "ratio", n) for x in past], 20, 8)
                    r[f"spread n{n}"] = pp + sp if sp is not None else math.nan
                    r[f"ratio n{n}"] = pp * ra if ra is not None else math.nan
                sp_simple = ewma([siblings(x, kind, s, "spread", 2) for x in past], 20, 1e9)  # equal weights
                r["spread n2 media simple"] = pp + sp_simple if sp_simple is not None else math.nan
                ds = donor_shape(dc, kind, s)
                r["donor"] = pp * ds if ds is not None else math.nan
                rows.append(r)
    df = pd.DataFrame(rows)
    df["ens spread+donor"] = df[["spread n2", "donor"]].mean(axis=1, skipna=False)
    df["ens ratio+donor"] = df[["ratio n2", "donor"]].mean(axis=1, skipna=False)
    df["ens spread+ratio"] = df[["spread n2", "ratio n2"]].mean(axis=1, skipna=False)
    df["ens parent+donor"] = df[["parent", "donor"]].mean(axis=1, skipna=False)
    methods = [c for c in df.columns if c not in ("area", "kind", "truth")]
    out = []
    for kind, g in df.groupby("kind"):
        common = g.dropna(subset=["spread n2", "ratio n2", "donor"])  # same cells for every method
        rec = {"kind": kind, "n": len(common)}
        for m in methods:
            rec[m] = (common[m] - common.truth).abs().mean()
        out.append(rec)
    out = pd.DataFrame(out).set_index("kind").T
    out.to_csv(Path(__file__).with_name("fallback_summary3.csv"))
    pd.set_option("display.width", 250)
    print(out.round(2).to_string())
    print("\nby area (best rule per kind)")
    best = {"Weekend": "spread n2", "Quarter": "ratio n2", "Season": "ratio n2", "Month": "donor"}
    for kind, m in best.items():
        g = df[df.kind == kind].dropna(subset=[m])
        print(kind, m, (g[m] - g.truth).abs().groupby(g.area).mean().round(2).to_dict())


if __name__ == "__main__":
    main()
