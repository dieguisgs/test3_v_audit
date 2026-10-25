"""Sensitivity: which layer fills each gap, how well, by country; and how to transform EEX
when there is no own information (never publish the official EEX price as is).

    uv run python research/sensitivity.py

Layers (main method, history 10 sessions):
    today    own VWAPs of the day near the gap (with history for the remaining trust)
    history  no own VWAP today, VWAPs in the last 10 sessions
    none     no own information: the reference price alone

No-information transformations, scored on days without any own VWAP:
    raw EEX            the official price as is (what we want to avoid publishing)
    EWMA n/hl          EWMA of the reference price over the last n allowed publications
"""

from __future__ import annotations

import math

import bench as B
import numpy as np
import pandas as pd
from history_test import x_at

TAU, K, N_HIST = 0.5, 1.0, 10


def local(an, x):
    if not an:
        return 0.0, 0.0
    w = np.array([math.exp(-abs(a.x - x) / TAU) for a in an])
    b = np.array([a.own - a.ref for a in an])
    return float((w * b).sum() / w.sum()), float(w.sum())


def layer_and_price(an, t, h):
    loc, W = local(an, t.x)
    num = den = 0.0
    for d, past in h.past[-N_HIST:]:
        l_d, w_d = local(past, x_at(t, d))
        num += w_d * l_d
        den += w_d
    hist = num / (den + K) if den else 0.0
    layer = 0 if an else (1 if den else 2)
    return layer, t.ref + (W * loc + K * hist) / (W + K)


def ref_ewma(n, hl):
    """EWMA of the reference price of the same contract over the last n reference curves."""

    def f(an, t, h):
        curves = h.curves[-n:]
        vals, ws = [], []
        for i, c in enumerate(curves):
            key = (t.kind, t.start)
            if key in c.index:
                vals.append(float(c[key]))
                ws.append(2 ** (-(len(curves) - 1 - i) / hl))
        return float(np.dot(vals, ws) / sum(ws)) if ws else t.ref

    return f


B.METHODS = {
    "eex": B.m_eex,
    "main": lambda an, t, h: layer_and_price(an, t, h)[1],
    "layer": lambda an, t, h: float(layer_and_price(an, t, h)[0]),
    "ref EWMA 2/0.5": ref_ewma(2, 0.5),
    "ref EWMA 2/1": ref_ewma(2, 1.0),
    "ref EWMA 3/1": ref_ewma(3, 1.0),
    "ref EWMA 5/2 (original)": ref_ewma(5, 2.0),
}

if __name__ == "__main__":
    frames = [B.run(s, seeds=(1,), noise=0.01, no_anchor_share=0.25) for s in ("intraday", "lag", "persist", "persist_lag")]
    df = pd.concat(frames, ignore_index=True)
    df.to_csv(B.OUT / "sensitivity_cells.csv", index=False)
    df["err_main"] = (df["main"] - df["truth"]).abs()
    df["err_eex"] = (df["eex"] - df["truth"]).abs()
    names = {0: "today", 1: "history", 2: "none"}
    df["layer"] = df["layer"].map(names)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)

    print("\n== 1. Layer usage and error (EUR/MWh) by scenario")
    t1 = df.groupby(["scenario", "layer"]).agg(cells=("err_main", "size"), mae_method=("err_main", "mean"), mae_eex=("err_eex", "mean"))
    t1["share"] = t1["cells"] / t1.groupby(level=0)["cells"].transform("sum")
    print(t1.round(3).to_string())

    print("\n== 2. Error by country (method vs raw EEX), offset 0 (intraday) and -1 (lag)")
    t2 = df[df.scenario.isin(["intraday", "lag"])].pivot_table(
        index="area", columns="scenario", values=["err_main", "err_eex"], aggfunc="mean"
    )
    t2[("improvement %", "intraday")] = 100 * (1 - t2[("err_main", "intraday")] / t2[("err_eex", "intraday")])
    t2[("improvement %", "lag")] = 100 * (1 - t2[("err_main", "lag")] / t2[("err_eex", "lag")])
    print(t2.round(3).to_string())
    print("\n   by country and curve section (offset 0)")
    t2b = df[df.scenario == "intraday"].pivot_table(index="area", columns="group", values="err_main", aggfunc="mean")
    print(t2b.round(3).to_string())

    print("\n== 3. Days without any own VWAP: transform the reference instead of publishing it as is")
    blank = df[df.n_anchors == 0]
    rows = []
    for scn, g in blank.groupby("scenario"):
        r = {"scenario": scn, "cells": len(g), "raw EEX": (g.eex - g.truth).abs().mean(), "method (history)": g.err_main.mean()}
        for m in ("ref EWMA 2/0.5", "ref EWMA 2/1", "ref EWMA 3/1", "ref EWMA 5/2 (original)"):
            r[m] = (g[m] - g.truth).abs().mean()
        rows.append(r)
    t3 = pd.DataFrame(rows)
    print(t3.round(3).to_string(index=False))
    t1.to_csv(B.OUT / "sensitivity_layers.csv")
    t2.to_csv(B.OUT / "sensitivity_countries.csv")
    t3.to_csv(B.OUT / "sensitivity_no_info.csv", index=False)
