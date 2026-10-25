"""Robustness: more VWAP noise and fewer anchors.  uv run python research/robust.py"""

import bench as B
import pandas as pd
import sweep as S

base_p = B.p_obs
B.METHODS = {
    "eex": B.m_eex,
    "spread_ewma5 (VWAPS2)": B.m_spread_ewma5,
    "spread_nearest_any": lambda an, t, h: B.m_spread_nearest(an, t, h, same_kind=False),
    "kernel_ratio (~VWAPS)": B.m_kernel_ratio,
    "global_ratio": B.m_global_ratio,
    "interp_ratio": B.m_interp_ratio,
    "interpshrunk_ratio_t0.5": S.interp_shrunk("ratio", 0.5),
    "interpshrunk_ratio_t1.0": S.interp_shrunk("ratio", 1.0),
    "shrunk_ratio_t0.3_k0.25_o1": S.shrunk("ratio", 0.3, 0.25, 1.0),
}
rows = []
for label, noise, scale in [
    ("base", 0.003, 1),
    ("noise1%", 0.01, 1),
    ("noise2%", 0.02, 1),
    ("sparse", 0.003, 0.4),
    ("sparse+noise1%", 0.01, 0.4),
]:
    B.p_obs = lambda k, p, s=scale: base_p(k, p) * s
    df = pd.concat([B.run(s, seeds=(1, 2), noise=noise) for s in ("intraday", "lag")], ignore_index=True)
    s = B.summarize(df)
    s.insert(0, "case", label)
    rows.append(s)
    print(label, flush=True)
s = pd.concat(rows)
s.to_csv(B.OUT / "robust_summary.csv", index=False)
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
print(s[s.group == "ALL"].set_index(["case", "scenario"]).drop(columns=["group", "n"]).T.round(3).to_string())
print(s[s.group == "short"].set_index(["case", "scenario"]).drop(columns=["group", "n"]).T.round(3).to_string())
