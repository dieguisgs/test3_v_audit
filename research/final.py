"""Final candidates.  uv run python research/final.py"""

import math

import bench as B
import modes as M
import numpy as np
import pandas as pd


def pct_shift(an, t, h):
    """Simplest: one number per day, g = sum(own-eex)/sum|eex|, applied as eex*(1+g)."""
    if not an:
        return t.ref
    g = sum(a.own - a.ref for a in an) / sum(abs(a.ref) for a in an)
    return t.ref + g * abs(t.ref)


def local_plus_pct(tau, k):
    """Local additive basis from nearby anchors, blended with the day's % move scaled to the target."""

    def f(an, t, h):
        if not an:
            return t.ref
        w = np.array([math.exp(-abs(a.x - t.x) / tau) for a in an])
        b = np.array([a.own - a.ref for a in an])
        g = sum(b) / sum(abs(a.ref) for a in an) * abs(t.ref)
        return t.ref + float(((w * b).sum() + k * g) / (w.sum() + k))

    return f


if __name__ == "__main__":
    B.METHODS = {
        "eex": B.m_eex,
        "spread_ewma5 (VWAPS2 today)": B.m_spread_ewma5,
        "VWAPS-like additive": M.va,
        "VWAPS-like auto": M.auto(M.va, M.vr),
        "new ratio": M.nr,
        "pct_shift (1 number/day)": pct_shift,
    }
    for tau in (0.3, 0.5):
        for k in (0.25, 0.5, 1.0):
            B.METHODS[f"local+pct t{tau} k{k}"] = local_plus_pct(tau, k)
    rows = []
    for label, noise in [("noise0.3%", 0.003), ("noise1%", 0.01)]:
        df = pd.concat([B.run(s, seeds=(1, 2, 3), noise=noise) for s in ("intraday", "lag", "lagfull", "cross")], ignore_index=True)
        s = B.summarize(df)
        s.insert(0, "case", label)
        rows.append(s)
    s = pd.concat(rows)
    s.to_csv(B.OUT / "final_summary.csv", index=False)
    pd.set_option("display.width", 300)
    pd.set_option("display.max_columns", 40)
    print(s.set_index(["case", "scenario", "group"]).drop(columns="n").T.round(3).to_string())
