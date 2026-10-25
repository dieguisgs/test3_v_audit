"""Do the promising settings add up?  uv run python research/param_combo.py"""

import bench as B
import pandas as pd
from param_study import method

B.METHODS = {
    "eex": B.m_eex,
    "ACTUAL tau .5 k 1": method(),
    "tau .7 k 1": method(tau_s=0.7, tau_l=0.7),
    "tau .7 k .5": method(tau_s=0.7, tau_l=0.7, k=0.5),
    "tau .7 k .5 hl 3": method(tau_s=0.7, tau_l=0.7, k=0.5, hl=3),
    "tau .5 k .5 hl 3": method(k=0.5, hl=3),
    "tau 1 k .5 hl 3": method(tau_s=1.0, tau_l=1.0, k=0.5, hl=3),
}
if __name__ == "__main__":
    rows = []
    for label, kw in (("ruido 1%", dict(noise=0.01)), ("ruido 1% + 5% raros", dict(noise=0.01, outliers=0.05))):
        df = pd.concat([B.run(s, seeds=(2,), **kw) for s in ("intraday", "lag", "persist", "persist_lag")], ignore_index=True)
        sm = B.summarize(df)
        sm.insert(0, "caso", label)
        rows.append(sm)
        print(label, flush=True)
    s = pd.concat(rows)
    s.to_csv(B.OUT / "param_combo.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    a = s[s.group == "ALL"].set_index(["caso", "scenario"]).drop(columns=["group", "n"]).T
    print(a.round(3).to_string())
    print("\n% vs ACTUAL (negativo = mejor)")
    print((100 * (a / a.loc["ACTUAL tau .5 k 1"] - 1)).round(1).to_string())
    g = s[(s.caso == "ruido 1%") & (s.scenario == "intraday")].set_index("group").drop(columns=["caso", "scenario", "n"]).T
    print("\nPor tramo (ruido 1%, offset 0)")
    print(g.round(3).to_string())
