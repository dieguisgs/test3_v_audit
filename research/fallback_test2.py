"""Variants to make the EEX fallback more accurate (same exam as fallback_test.py).

For every contract EEX DOES quote, pretend it is missing and rebuild it:

    parent                parent price only
    spread EWMA5/EWMA20   parent + EWMA of sibling spread (sibling - its parent)
    ratio  EWMA5/EWMA20   parent x EWMA of sibling ratio (sibling / its parent)
    donor shape           parent x (donor target / donor parent), same publication, from a
                          neighbouring market that quotes the contract (DE, or FR for DE)
    residual (+donor)     months only: quarter minus the quoted months of that quarter,
                          split among missing months with the donor shape (or equally)

    uv run python research/fallback_test2.py
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
from fallback_test import AREAS, EEX, PARENT, curve, end_of, parent_price, slot


def sibling_values(c, kind, s, how):
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
    return float(np.mean([v for _, v in vals[:2]]))


def ewma(values, n, halflife):
    vals = values[-n:]
    w = [2 ** (-(len(vals) - 1 - i) / halflife) for i in range(len(vals))]
    ok = [(v, wi) for v, wi in zip(vals, w, strict=True) if v is not None]
    return sum(v * wi for v, wi in ok) / sum(wi for _, wi in ok) if ok else None


def donor_shape(dc, kind, s):
    if dc is None or (kind, s) not in dc:
        return None
    pp = parent_price(dc, kind, s)
    return dc[(kind, s)] / pp if pp else None


def month_residual(c, s, dc):
    """Quarter price minus quoted months of its quarter; split by donor shape or equally."""
    q0 = s - pd.DateOffset(months=(s.month - 1) % 3)
    if ("Quarter", q0) not in c:
        return None, None
    months = [q0 + pd.DateOffset(months=i) for i in range(3)]
    days = {m: (end_of("Month", m) - m).days for m in months}
    known = {m: c[("Month", m)] for m in months if m != s and ("Month", m) in c}
    missing = [m for m in months if m not in known]
    if len(known) == 0:
        return None, None
    rest = c[("Quarter", q0)] * sum(days.values()) - sum(known[m] * days[m] for m in known)
    flat = rest / sum(days[m] for m in missing)
    shaped = None
    if dc is not None and all(("Month", m) in dc for m in missing):
        dw = {m: dc[("Month", m)] for m in missing}
        dmean = sum(dw[m] * days[m] for m in missing) / sum(days[m] for m in missing)
        shaped = flat * dw[s] / dmean
    return flat, shaped


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
                sp = [sibling_values(x, kind, s, "spread") for x in past]
                ra = [sibling_values(x, kind, s, "ratio") for x in past]
                ds = donor_shape(dc, kind, s)
                row = {
                    "area": area,
                    "kind": kind,
                    "truth": price,
                    "parent": pp,
                    "spread EWMA5": pp + e if (e := ewma(sp, 5, 2)) is not None else math.nan,
                    "spread EWMA20": pp + e if (e := ewma(sp, 20, 8)) is not None else math.nan,
                    "ratio EWMA5": pp * e if (e := ewma(ra, 5, 2)) is not None else math.nan,
                    "ratio EWMA20": pp * e if (e := ewma(ra, 20, 8)) is not None else math.nan,
                    "donor shape": pp * ds if ds is not None else math.nan,
                }
                if kind == "Month":
                    flat, shaped = month_residual(c, s, dc)
                    row["residual"] = flat if flat is not None else math.nan
                    row["residual+donor"] = shaped if shaped is not None else math.nan
                rows.append(row)
    r = pd.DataFrame(rows)
    methods = [
        "parent",
        "spread EWMA5",
        "spread EWMA20",
        "ratio EWMA5",
        "ratio EWMA20",
        "donor shape",
        "residual",
        "residual+donor",
    ]
    out = []
    for kind, g in r.groupby("kind"):
        rec = {"kind": kind, "n": len(g), "price": g.truth.mean()}
        for m in methods:
            if m in g:
                ok = g[m].notna()
                rec[m] = (g.loc[ok, m] - g.loc[ok, "truth"]).abs().mean()
                rec[m + " cov"] = ok.mean()
        out.append(rec)
    out = pd.DataFrame(out)
    out.to_csv(Path(__file__).with_name("fallback_summary2.csv"), index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    print(out.set_index("kind").T.round(2).to_string())


if __name__ == "__main__":
    main()
