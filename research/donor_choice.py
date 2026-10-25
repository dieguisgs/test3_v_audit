"""Which neighbouring market gives the best month shape for each market?  uv run python research/donor_choice.py"""

import pandas as pd
from fallback_test import AREAS, EEX, curve, parent_price
from fallback_test2 import donor_shape

curves = {}
for area in AREAS:
    d = pd.read_csv(EEX / area / "Base.csv").dropna(subset=["settlPx", "deliveryStart"])
    d["s"] = pd.to_datetime(d.deliveryStart)
    curves[area] = {t: curve(g) for t, g in d.groupby("tradeDate")}
rows = []
for area in AREAS:
    for t, c in curves[area].items():
        for (kind, s), price in c.items():
            if kind != "Month" or s <= pd.Timestamp(t):
                continue
            pp = parent_price(c, kind, s)
            if pp is None:
                continue
            for donor in AREAS:
                if donor == area:
                    continue
                ds = donor_shape(curves[donor].get(t), kind, s)
                if ds is not None:
                    rows.append((area, donor, abs(pp * ds - price), abs(pp - price)))
r = pd.DataFrame(rows, columns=["area", "donor", "err", "err_parent"])
tab = r.pivot_table(index="area", columns="donor", values="err", aggfunc="mean").round(2)
tab["solo quarter"] = r.groupby("area")["err_parent"].mean().round(2)
print(tab.to_string())
