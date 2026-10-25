"""How well can a contract that EEX does not quote be rebuilt from EEX itself?

For every contract that EEX DOES quote, pretend it is missing and rebuild it from
the same publication:

    parent        hour-weighted EEX price of the coarser contracts that contain it
                  (week for a weekend, quarter for a month, year for a quarter/season)
    parent+spread parent + EWMA (5 publications, half-life 2) of the spread
                  "sibling - its parent" of quoted siblings in the same seasonal slot
                  (weekend: any weekend; month: same position in its quarter;
                   quarter: same quarter of the year; season: same season type)

    uv run python research/fallback_test.py
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EEX = ROOT / "eex_scraper" / "output" / "curves" / "POWER"
AREAS = ["DE", "FR", "NL", "AT", "BE", "CH", "ES", "IT"]


def end_of(kind, s):
    return {
        "Day": s + pd.Timedelta(days=1),
        "Weekend": s + pd.Timedelta(days=2),
        "Week": s + pd.Timedelta(days=7),
        "Month": s + pd.DateOffset(months=1),
        "Quarter": s + pd.DateOffset(months=3),
        "Year": s + pd.DateOffset(years=1),
        "Season": s + pd.DateOffset(months=6),
    }[kind]


PARENT = {"Weekend": "Week", "Month": "Quarter", "Quarter": "Year", "Season": "Year"}


def slot(kind, s):
    if kind == "Month":
        return (s.month - 1) % 3
    if kind == "Quarter":
        return s.month
    if kind == "Season":
        return s.month  # 4 = summer, 10 = winter
    return 0


def curve(g):
    """dict (kind, start) -> price, adding seasons built from quoted quarters."""
    c = {(k, s): p for k, s, p in zip(g.maturityType, g.s, g.settlPx, strict=True)}
    for (k, s), _ in list(c.items()):
        if k == "Quarter" and s.month in (4, 10):
            nxt = s + pd.DateOffset(months=3)
            if ("Quarter", nxt) in c:
                h1 = (end_of("Quarter", s) - s).days
                h2 = (end_of("Quarter", nxt) - nxt).days
                c[("Season", s)] = (c[("Quarter", s)] * h1 + c[("Quarter", nxt)] * h2) / (h1 + h2)
    return c


def parent_price(c, kind, s):
    """Hour(day)-weighted price of the parent-kind contracts overlapping [s, e)."""
    pk = PARENT[kind]
    e = end_of(kind, s)
    total = weight = 0.0
    for (k, ps), p in c.items():
        if k != pk:
            continue
        pe = end_of(pk, ps)
        overlap = (min(e, pe) - max(s, ps)).days
        if overlap > 0:
            total += p * overlap
            weight += overlap
    return total / weight if weight >= (e - s).days else None


def sibling_spread(c, kind, s):
    """Mean spread sibling - parent over quoted siblings in the same slot (target excluded)."""
    vals = []
    for (k, ss), p in c.items():
        if k != kind or ss == s or slot(kind, ss) != slot(kind, s):
            continue
        pp = parent_price(c, kind, ss)
        if pp is not None:
            vals.append((abs((ss - s).days), p - pp))
    if not vals:
        return None
    vals.sort()
    return float(np.mean([v for _, v in vals[:2]]))  # two closest siblings


def main():
    rows = []
    w = np.array([2 ** (-(4 - i) / 2) for i in range(5)])
    for area in AREAS:
        d = pd.read_csv(EEX / area / "Base.csv").dropna(subset=["settlPx", "deliveryStart"])
        d["s"] = pd.to_datetime(d.deliveryStart)
        curves = {t: curve(g) for t, g in d.groupby("tradeDate")}
        dates = sorted(curves)
        for i in range(4, len(dates)):
            t = pd.Timestamp(dates[i])
            c = curves[dates[i]]
            for (kind, s), price in c.items():
                if kind not in PARENT or s <= t:
                    continue
                pp = parent_price(c, kind, s)
                if pp is None:
                    continue
                spreads = [sibling_spread(curves[dates[j]], kind, s) for j in range(i - 4, i + 1)]
                ok = [(sp, wi) for sp, wi in zip(spreads, w, strict=True) if sp is not None]
                ewma = sum(sp * wi for sp, wi in ok) / sum(wi for _, wi in ok) if ok else None
                rows.append(
                    {
                        "area": area,
                        "kind": kind,
                        "truth": price,
                        "parent": pp,
                        "parent+spread": pp + ewma if ewma is not None else math.nan,
                        "parent+spread today": pp + spreads[-1] if spreads[-1] is not None else math.nan,
                    }
                )
    r = pd.DataFrame(rows)
    out = []
    for kind, g in r.groupby("kind"):
        g = g.dropna()
        out.append(
            {
                "kind": kind,
                "n": len(g),
                "precio medio": g.truth.mean(),
                "solo padre": (g.parent - g.truth).abs().mean(),
                "padre + spread hoy": (g["parent+spread today"] - g.truth).abs().mean(),
                "padre + EWMA spread": (g["parent+spread"] - g.truth).abs().mean(),
            }
        )
    out = pd.DataFrame(out)
    out.to_csv(Path(__file__).with_name("fallback_summary.csv"), index=False)
    print(out.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
