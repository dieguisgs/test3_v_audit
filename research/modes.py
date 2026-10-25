"""ratio vs additive vs auto.  uv run python research/modes.py"""

import math

import bench as B
import numpy as np
import pandas as pd
import sweep as S


def vwaps_like(mode, tau=0.5, other=0.6, k=1.0):
    """VWAPS local layer with its config: kernel on log-time and kind, shrunk towards
    'no adjustment' (the hist factor is neutral here: these deviations do not persist)."""

    def f(an, t, h):
        if not an:
            return t.ref
        w = np.array([math.exp(-abs(a.x - t.x) / tau) * (1.0 if a.kind == t.kind else other) for a in an])
        b = np.array([S.basis(a, mode) for a in an])
        loc = float((w * b).sum() / w.sum())
        W = w.sum()
        lam = W / (W + k)
        return S.apply(t, lam * loc + (1 - lam) * S.neutral(mode), mode)

    return f


def auto(fa, fr, cut=3.5):  # additive for delivery within ~1 month, ratio after
    return lambda an, t, h: fa(an, t, h) if t.x < cut else fr(an, t, h)


va, vr = vwaps_like("add"), vwaps_like("ratio")
na, nr = S.shrunk("add", 0.3, 0.25, 1.0), S.shrunk("ratio", 0.3, 0.25, 1.0)
if __name__ == "__main__":
    B.METHODS = {
        "eex": B.m_eex,
        "VWAPS-like additive": va,
        "VWAPS-like ratio": vr,
        "VWAPS-like auto": auto(va, vr),
        "new additive": na,
        "new ratio": nr,
        "new auto": auto(na, nr),
    }
    rows = []
    for label, noise in [("noise0.3%", 0.003), ("noise1%", 0.01)]:
        df = pd.concat([B.run(s, seeds=(1, 2), noise=noise) for s in ("intraday", "lag", "cross")], ignore_index=True)
        s = B.summarize(df)
        s.insert(0, "case", label)
        rows.append(s)
    s = pd.concat(rows)
    s.to_csv(B.OUT / "modes_summary.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print(s.set_index(["case", "scenario", "group"]).drop(columns="n").round(3).to_string())
