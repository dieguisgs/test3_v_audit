"""Extra parameters: are they worth it?  uv run python research/param_study.py"""

import math

import bench as B
import numpy as np
import pandas as pd
from history_test import x_at

CUT = 3.5  # log days ~ 1 month: below = short end (days, weekends, weeks, BOM)


def local(an, x, tau, clip=None):
    if not an:
        return 0.0, 0.0
    dev = np.array([a.own - a.ref for a in an])
    if clip and len(dev) >= 3:
        med = np.median(dev)
        mad = 1.4826 * np.median(np.abs(dev - med))
        if mad > 0:
            dev = np.clip(dev, med - clip * mad, med + clip * mad)
    w = np.array([math.exp(-abs(a.x - x) / tau) for a in an])
    return float((w * dev).sum() / w.sum()), float(w.sum())


def method(tau_s=0.5, tau_l=0.5, k=1.0, n=10, hl=None, clip=None):
    def f(an, t, h):
        tau = tau_s if t.x < CUT else tau_l
        loc, W = local(an, t.x, tau, clip)
        num = den = 0.0
        past = h.past[-n:] if n else []
        for age, (d, pa) in enumerate(reversed(past)):
            l_d, w_d = local(pa, x_at(t, d), tau, clip)
            if hl:
                w_d *= 2 ** (-age / hl)
            num += w_d * l_d
            den += w_d
        hist = num / (den + k) if den else 0.0
        return t.ref + (W * loc + k * hist) / (W + k)

    return f


B.METHODS = {
    "eex": B.m_eex,
    "BASE (tau .5, k 1, hist 10)": method(),
    "tau corto .3 / largo .5": method(tau_s=0.3),
    "tau corto .7 / largo .5": method(tau_s=0.7),
    "tau corto .5 / largo .3": method(tau_l=0.3),
    "tau corto .5 / largo .8": method(tau_l=0.8),
    "k 0.5": method(k=0.5),
    "k 2": method(k=2.0),
    "hist decae hl 3 dias": method(hl=3),
    "hist decae hl 5 dias": method(hl=5),
    "clip 2.5 MAD": method(clip=2.5),
    "clip 4 MAD": method(clip=4.0),
}
if __name__ == "__main__":
    rows = []
    for label, kw in (("ruido 1%", dict(noise=0.01)), ("ruido 1% + 5% raros", dict(noise=0.01, outliers=0.05))):
        df = pd.concat([B.run(s, seeds=(1,), **kw) for s in ("intraday", "lag", "persist", "persist_lag")], ignore_index=True)
        sm = B.summarize(df)
        sm.insert(0, "caso", label)
        rows.append(sm)
        print(label, flush=True)
    s = pd.concat(rows)
    s.to_csv(B.OUT / "param_summary.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    a = s[s.group == "ALL"].set_index(["caso", "scenario"]).drop(columns=["group", "n"]).T
    base = a.loc["BASE (tau .5, k 1, hist 10)"]
    print(a.round(3).to_string())
    print("\n% vs BASE (negativo = mejor)")
    print((100 * (a / base - 1)).round(1).to_string())
