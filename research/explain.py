"""Explainable finalists.  uv run python research/explain.py"""

import bench as B
import final as F
import modes as M
import numpy as np
import pandas as pd


def tramo(tau, k, tau_g):
    """Local additive basis + the move of the curve SECTION around the gap (in %), not the whole day."""

    def f(an, t, h):
        if not an:
            return t.ref
        d = np.array([abs(a.x - t.x) for a in an])
        w = np.exp(-d / tau)
        v = np.exp(-d / tau_g)
        b = np.array([a.own - a.ref for a in an])
        r = np.array([abs(a.ref) for a in an])
        g = float((v * b).sum() / (v * r).sum()) * abs(t.ref)
        return t.ref + float(((w * b).sum() + k * g) / (w.sum() + k))

    return f


B.METHODS = {
    "eex": B.m_eex,
    "VWAPS2 actual": B.m_spread_ewma5,
    "VWAPS-like aditivo": M.va,
    "A vecinos (aditivo, sin tipos)": M.vwaps_like("add", 0.5, 1.0, 1.0),
    "B vecinos + mov. dia": F.local_plus_pct(0.3, 0.25),
}
for tg in (1.0, 1.5, 2.5):
    B.METHODS[f"C vecinos + mov. tramo tg{tg}"] = tramo(0.3, 0.25, tg)
rows = []
for label, noise in [("ruido0.3%", 0.003), ("ruido1%", 0.01)]:
    df = pd.concat([B.run(s, seeds=(1, 2, 3), noise=noise) for s in ("intraday", "lag", "cross")], ignore_index=True)
    s = B.summarize(df)
    s.insert(0, "case", label)
    rows.append(s)
s = pd.concat(rows)
s.to_csv(B.OUT / "explain_summary.csv", index=False)
pd.set_option("display.width", 300)
pd.set_option("display.max_columns", 40)
print(s.set_index(["case", "scenario", "group"]).drop(columns="n").T.round(3).to_string())
