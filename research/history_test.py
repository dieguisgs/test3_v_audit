"""Does a simple, auditable history layer help?  uv run python research/history_test.py"""

import math

import bench as B
import modes as M
import numpy as np
import pandas as pd


def x_at(t, d):
    mid = t.start + (B.end_of(t.kind, t.start) - t.start) / 2
    return math.log1p(max((mid - d).days, 0.5))


def local(an, x, tau):
    if not an:
        return 0.0, 0.0
    w = np.array([math.exp(-abs(a.x - x) / tau) for a in an])
    b = np.array([a.own - a.ref for a in an])
    return float((w * b).sum() / w.sum()), float(w.sum())


def with_history(n, tau=0.5, k=1.0):
    """price = EEX + (W*local + k*hist) / (W + k),  hist = sum W_d local_d / (sum W_d + k)
    over the last n days, same absolute contract, each day with its own VWAPs and EEX."""

    def f(an, t, h):
        loc, W = local(an, t.x, tau)
        num = den = 0.0
        for d, past in h.past[-n:] if n else []:
            l_d, w_d = local(past, x_at(t, d), tau)
            num += w_d * l_d
            den += w_d
        hist = num / (den + k) if den else 0.0
        return t.ref + (W * loc + k * hist) / (W + k)

    return f


if __name__ == "__main__":
    B.METHODS = {"eex": B.m_eex, "auditable (sin historia)": M.vwaps_like("add", 0.5, 1.0, 1.0)}
    for n in (3, 5, 10, 20):
        B.METHODS[f"con historia {n} dias"] = with_history(n)
    rows = []
    for noise in (0.003, 0.01):
        df = pd.concat([B.run(s, seeds=(1, 2), noise=noise) for s in ("intraday", "lag", "persist", "persist_lag")], ignore_index=True)
        sm = B.summarize(df)
        sm.insert(0, "ruido", noise)
        rows.append(sm)
    s = pd.concat(rows)
    s.to_csv(B.OUT / "history_summary.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print(s[s.group == "ALL"].set_index(["ruido", "scenario"]).drop(columns=["group", "n"]).T.round(3).to_string())
    print(s[(s.ruido == 0.01)].set_index(["scenario", "group"]).drop(columns=["ruido", "n"]).T.round(3).to_string())
