"""Short end (days, weekends, weeks): what improves it?  uv run python research/short_study.py"""

import math

import bench as B
import numpy as np
import pandas as pd
from history_test import x_at

SHORT = {"Day", "Weekend", "Week"}


def local(an, t, x, tau, other=1.0, mode="add", only_short=False, beta=0.5):
    pool = [a for a in an if not only_short or a.kind in SHORT]
    if not pool:
        return 0.0, 0.0
    med = float(np.median([a.volume for a in pool]))
    w = np.array([math.exp(-abs(a.x - x) / tau) * (1.0 if a.kind == t.kind else other) * (a.volume / med) ** beta for a in pool])
    if mode == "ratio":
        b = np.array([(a.own - a.ref) / a.ref for a in pool]) * t.ref
    else:
        b = np.array([a.own - a.ref for a in pool])
    return float((w * b).sum() / w.sum()), float(w.sum())


def method(tau=0.7, k=1.0, other=1.0, mode="add", only_short=False, gk=0.0, n=10):
    def f(an, t, h):
        loc, W = local(an, t, t.x, tau, other, mode, only_short)
        num = den = 0.0
        for d, pa in h.past[-n:]:
            l_d, w_d = local(pa, t, x_at(t, d), tau, other, mode, only_short)
            num += w_d * l_d
            den += w_d
        hist = num / (den + k) if den else 0.0
        price = t.ref + (W * loc + k * hist) / (W + k)
        if gk and an:  # day move of the short end, in %, blended in
            sh = [a for a in an if a.kind in SHORT]
            if sh:
                g = sum(a.own - a.ref for a in sh) / sum(abs(a.ref) for a in sh) * abs(t.ref)
                price = price + gk * (g - (price - t.ref))
        return price

    return f


B.METHODS = {
    "eex": B.m_eex,
    "ACTUAL": method(),
    "mismo tipo pesa más (otros x0.3)": method(other=0.3),
    "solo anclas cortas": method(only_short=True),
    "ratio (en %) en el corto": method(mode="ratio"),
    "alcance 0.4": method(tau=0.4),
    "alcance 1.0": method(tau=1.0),
    "k 0.5": method(k=0.5),
    "+30% movimiento del corto": method(gk=0.3),
    "ratio + mismo tipo": method(mode="ratio", other=0.3),
}
if __name__ == "__main__":
    df = pd.concat(
        [
            B.run(s, seeds=(1, 2), noise=0.01, volume_noise=True, only_groups={"short"})
            for s in ("intraday", "lag", "persist", "persist_lag")
        ],
        ignore_index=True,
    )
    s = B.summarize(df)
    s.to_csv(B.OUT / "short_summary.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    a = s[s.group == "ALL"].set_index("scenario").drop(columns=["group", "n"]).T
    print(a.round(3).to_string())
    print("\n% vs ACTUAL")
    print((100 * (a / a.loc["ACTUAL"] - 1)).round(1).to_string())
    by_kind = (
        df.assign(err=lambda x: (x["ACTUAL"] - x.truth).abs(), err_eex=lambda x: (x.eex - x.truth).abs())
        .groupby(["scenario", "kind"])[["err", "err_eex"]]
        .mean()
    )
    print(by_kind.round(3).to_string())
