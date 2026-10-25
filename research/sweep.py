"""Parameter sweep / hybrid candidates on top of bench.py.  uv run python research/sweep.py"""

from __future__ import annotations

import math
import sys

import bench as B
import numpy as np
import pandas as pd


def basis(a, mode):
    return a.own / a.ref if mode == "ratio" else a.own - a.ref


def apply(t, b, mode):
    return t.ref * b if mode == "ratio" else t.ref + b


def neutral(mode):
    return 1.0 if mode == "ratio" else 0.0


def shrunk(mode, tau, k, other):
    def f(an, t, h):
        if not an:
            return t.ref
        w = np.array([math.exp(-abs(a.x - t.x) / tau) * (1.0 if a.kind == t.kind else other) for a in an])
        b = np.array([basis(a, mode) for a in an])
        g = float(np.median(b))
        return apply(t, float(((w * b).sum() + k * g) / (w.sum() + k)), mode)

    return f


def interp_shrunk(mode, tau):
    """Basis curve: linear interpolation of the anchors' basis in log-time, pulled towards the
    day's median basis as the distance to the closest anchor grows (flat ends -> median)."""

    def f(an, t, h):
        if not an:
            return t.ref
        pts = sorted((a.x, basis(a, mode)) for a in an)
        xs, ys = map(np.array, zip(*pts, strict=False))
        g = float(np.median(ys))
        bi = float(np.interp(t.x, xs, ys))
        d = float(np.min(np.abs(xs - t.x)))
        w = math.exp(-d / tau)
        return apply(t, w * bi + (1 - w) * g, mode)

    return f


def mixed(tau, k, other, cut):
    """Additive basis for short delivery (x < cut), ratio for the rest."""
    fa, fr = shrunk("add", tau, k, other), shrunk("ratio", tau, k, other)
    return lambda an, t, h: fa(an, t, h) if t.x < cut else fr(an, t, h)


def build():
    m = {
        "eex": B.m_eex,
        "kernel_ratio (~VWAPS)": B.m_kernel_ratio,
        "shrunk_ratio": B.m_shrunk_ratio,
        "interp_add": B.m_interp_add,
        "global_ratio": B.m_global_ratio,
    }
    for mode in ("ratio", "add"):
        for tau in (0.3, 0.5, 1.0, 2.0):
            for k in (0.25, 1.0, 3.0):
                for other in (0.3, 1.0):
                    m[f"shrunk_{mode}_t{tau}_k{k}_o{other}"] = shrunk(mode, tau, k, other)
        for tau in (0.2, 0.5, 1.0):
            m[f"interpshrunk_{mode}_t{tau}"] = interp_shrunk(mode, tau)
    for cut in (2.5, 3.5):
        m[f"mixed_t0.5_k1_o1_cut{cut}"] = mixed(0.5, 1.0, 1.0, cut)
    return m


if __name__ == "__main__":
    B.METHODS = build()
    scns = sys.argv[1:] or ["intraday", "lag", "cross"]
    df = pd.concat([B.run(s, seeds=(1, 2)) for s in scns], ignore_index=True)
    s = B.summarize(df)
    s.to_csv(B.OUT / "sweep_summary.csv", index=False)
    t = s.set_index(["scenario", "group"]).drop(columns="n").T
    # rank by the average of intraday ALL and lag ALL, relative to eex
    t["score_same_mkt"] = (t[("intraday", "ALL")] + t[("lag", "ALL")]) / 2
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.max_rows", 200)
    print(t.sort_values("score_same_mkt").round(3).head(40).to_string())
