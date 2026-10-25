"""Does weighting anchors by traded volume help?  uv run python research/volume_study.py"""

import math

import bench as B
import numpy as np
import pandas as pd
from history_test import x_at


def local(an, x, tau, beta):
    if not an:
        return 0.0, 0.0
    med = float(np.median([a.volume for a in an]))
    w = np.array([math.exp(-abs(a.x - x) / tau) * (a.volume / med) ** beta for a in an])
    b = np.array([a.own - a.ref for a in an])
    return float((w * b).sum() / w.sum()), float(w.sum())


def method(beta, tau=0.7, k=1.0, n=10):
    def f(an, t, h):
        loc, W = local(an, t.x, tau, beta)
        num = den = 0.0
        for d, pa in h.past[-n:]:
            l_d, w_d = local(pa, x_at(t, d), tau, beta)
            num += w_d * l_d
            den += w_d
        hist = num / (den + k) if den else 0.0
        return t.ref + (W * loc + k * hist) / (W + k)

    return f


B.METHODS = {"eex": B.m_eex, "sin volumen (actual)": method(0.0), "volumen^0.5": method(0.5), "volumen^1": method(1.0)}
if __name__ == "__main__":
    rows = []
    for label, kw in (("ruido depende del volumen", dict(noise=0.01, volume_noise=True)), ("ruido igual para todos", dict(noise=0.01))):
        df = pd.concat([B.run(s, seeds=(1,), **kw) for s in ("intraday", "lag", "persist", "persist_lag")], ignore_index=True)
        sm = B.summarize(df)
        sm.insert(0, "caso", label)
        rows.append(sm)
        print(label, flush=True)
    s = pd.concat(rows)
    s.to_csv(B.OUT / "volume_summary.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    a = s[s.group == "ALL"].set_index(["caso", "scenario"]).drop(columns=["group", "n"]).T
    print(a.round(3).to_string())
    print("\n% vs actual (negativo = mejor)")
    print((100 * (a / a.loc["sin volumen (actual)"] - 1)).round(1).to_string())
