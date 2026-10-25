"""Benchmark of gap-filling methods on REAL EEX dynamics.

Own VWAPs are unknown at home, so they are simulated from real EEX settlements:

    intraday  own(T) = mid(EEX(T-1), EEX(T)) + noise, reference = EEX(T)      (~ offset 0)
    lag       own(T) = mid(EEX(T-1), EEX(T)) + noise, reference = EEX(T-1)    (~ offset -1)
    lagfull   own(T) = EEX(T) + noise,               reference = EEX(T-1)    (stress: full daily move)
    cross     own(T) = EEX_area(T) + noise,          reference = EEX_DE(T)   (own market != EEX market)

So the gap between own and reference is a real curve move (or a real cross-market basis),
not the generator of any method. Each day a random subset of contracts is "traded"
(denser at the front, as in real VWAPs); the rest are gaps. Every method fills the gaps
and is scored against the hidden truth.

    uv run python research/bench.py            # writes research/results_*.csv and prints tables
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EEX = ROOT / "eex_scraper" / "output" / "curves" / "POWER"
OUT = Path(__file__).resolve().parent
AREAS = ["DE", "FR", "NL", "AT", "BE", "CH", "ES", "IT"]
# targets per kind (front contracts strictly after the trade date), like the VWAPS target curve
N_TARGET = {"Day": 3, "Weekend": 4, "Week": 4, "Month": 10, "Quarter": 8, "Year": 3}
GROUP = {"Day": "short", "Weekend": "short", "Week": "short", "Month": "month", "Quarter": "quarter", "Year": "long"}


# probability that a contract has an own VWAP, by kind and position (0 = front)
def p_obs(kind: str, pos: int) -> float:
    return {
        "Day": [0.6, 0.4, 0.2],
        "Weekend": [0.5, 0.25, 0.1, 0.05],
        "Week": [0.4, 0.2, 0.1, 0.05],
        "Month": [0.85, 0.6, 0.4] + [0.2] * 7,
        "Quarter": [0.7, 0.5, 0.3] + [0.15] * 5,
        "Year": [0.8, 0.4, 0.2],
    }[kind][pos]


def end_of(kind: str, start: pd.Timestamp) -> pd.Timestamp:
    return {
        "Day": start + pd.Timedelta(days=1),
        "Weekend": start + pd.Timedelta(days=2),
        "Week": start + pd.Timedelta(days=7),
        "Month": start + pd.DateOffset(months=1),
        "Quarter": start + pd.DateOffset(months=3),
        "Year": start + pd.DateOffset(years=1),
    }[kind]


def load(area: str) -> dict[pd.Timestamp, pd.Series]:
    d = pd.read_csv(EEX / area / "Base.csv", usecols=["tradeDate", "maturityType", "deliveryStart", "settlPx"])
    d = d.dropna(subset=["settlPx"])
    d = d[d["maturityType"].isin(N_TARGET)]
    d["tradeDate"] = pd.to_datetime(d["tradeDate"])
    d["deliveryStart"] = pd.to_datetime(d["deliveryStart"])
    d = d.drop_duplicates(["tradeDate", "maturityType", "deliveryStart"], keep="last")
    return {t: g.set_index(["maturityType", "deliveryStart"])["settlPx"] for t, g in d.groupby("tradeDate")}


@dataclass
class Cell:
    kind: str
    start: pd.Timestamp
    x: float  # log time to delivery mid-point (days)
    ref: float  # reference (EEX) price
    own: float = math.nan
    pos: int = 0


# --------------------------------------------------------------------------------------------
# Methods. Each gets the day's anchors (own + ref), the target and a history accessor and
# returns a price (or nan). Anchors: list[Cell] with .own; target: Cell.
# --------------------------------------------------------------------------------------------


def m_eex(an, t, h):
    return t.ref


def _nearest(an, t, same_kind=True):
    pool = [a for a in an if a.kind == t.kind] if same_kind else an
    if not pool:
        return None
    return min(pool, key=lambda a: (abs(a.x - t.x), a.start))


def m_spread_nearest(an, t, h, same_kind=True):
    a = _nearest(an, t, same_kind)
    return t.ref if a is None else a.own + (t.ref - a.ref)


def m_ratio_nearest(an, t, h, same_kind=True):
    a = _nearest(an, t, same_kind)
    return t.ref if a is None else a.own * t.ref / a.ref


def m_spread_ewma5(an, t, h):
    """VWAPS2 default: nearest same-kind anchor + EWMA5(halflife 2) of the EEX spread;
    no anchor -> EWMA5 of the EEX price."""
    a = _nearest(an, t, True)
    if a is None:
        return h.ewma(t, None)
    return a.own + h.ewma(t, a)


def _global_ratio(an):
    return float(np.median([a.own / a.ref for a in an])) if an else 1.0


def m_global_ratio(an, t, h):
    """One number per day: median own/EEX ratio of all anchors, applied to the whole curve."""
    return t.ref * _global_ratio(an)


def m_global_shift(an, t, h):
    return t.ref + (float(np.median([a.own - a.ref for a in an])) if an else 0.0)


def _kernel(an, t, tau, other):
    w = np.array([math.exp(-abs(a.x - t.x) / tau) * (1.0 if a.kind == t.kind else other) for a in an])
    return w


def m_kernel_ratio(an, t, h, tau=0.7, other=0.3):
    """VWAPS-like local basis: weighted mean of anchor ratios, weights by distance and kind."""
    if not an:
        return t.ref
    w = _kernel(an, t, tau, other)
    r = np.array([a.own / a.ref for a in an])
    return t.ref * float((w * r).sum() / w.sum())


def m_kernel_add(an, t, h, tau=0.7, other=0.3):
    if not an:
        return t.ref
    w = _kernel(an, t, tau, other)
    b = np.array([a.own - a.ref for a in an])
    return t.ref + float((w * b).sum() / w.sum())


def m_shrunk_ratio(an, t, h, tau=0.7, other=0.3, k=0.5):
    """Kernel ratio shrunk towards the day's global ratio: local where there are close anchors,
    global elsewhere. k = pseudo-weight of the global ratio."""
    if not an:
        return t.ref
    w = _kernel(an, t, tau, other)
    r = np.array([a.own / a.ref for a in an])
    g = _global_ratio(an)
    return t.ref * float(((w * r).sum() + k * g) / (w.sum() + k))


def m_interp_ratio(an, t, h):
    """Ratio interpolated linearly in log-time between the anchors on each side (any kind);
    flat beyond the ends."""
    if not an:
        return t.ref
    pts = sorted((a.x, a.own / a.ref) for a in an)
    xs, ys = zip(*pts, strict=False)
    return t.ref * float(np.interp(t.x, xs, ys))


def m_interp_add(an, t, h):
    if not an:
        return t.ref
    pts = sorted((a.x, a.own - a.ref) for a in an)
    xs, ys = zip(*pts, strict=False)
    return t.ref + float(np.interp(t.x, xs, ys))


def m_line_ratio(an, t, h, lam=1.0):
    """log(own/ref) = a + c*x, ridge on c (shrinks to a parallel shift with few anchors)."""
    if not an:
        return t.ref
    x = np.array([a.x for a in an])
    y = np.log([a.own / a.ref for a in an])
    xm = x.mean()
    c = float(((x - xm) * (y - y.mean())).sum() / (((x - xm) ** 2).sum() + lam))
    a0 = y.mean() - c * xm
    return t.ref * math.exp(a0 + c * t.x)


class History:
    """EWMA of reference prices over the last `window` reference curves (for spread_ewma5)."""

    def __init__(self, curves: list[pd.Series], window=5, halflife=2.0):
        self.curves = curves[-window:]
        n = len(self.curves)
        w = np.array([2 ** (-(n - 1 - i) / halflife) for i in range(n)])
        self.w = w

    def ewma(self, t: Cell, a: Cell | None):
        vals, ws = [], []
        for c, w in zip(self.curves, self.w, strict=False):
            kt = (t.kind, t.start)
            if kt not in c.index:
                continue
            v = c[kt]
            if a is not None:
                ka = (a.kind, a.start)
                if ka not in c.index:
                    continue
                v = v - c[ka]
            vals.append(v)
            ws.append(w)
        if not ws:
            return math.nan if a is not None else t.ref
        return float(np.dot(vals, ws) / sum(ws))


METHODS = {
    "eex": m_eex,
    "spread_nearest_samekind": m_spread_nearest,
    "spread_nearest_any": lambda an, t, h: m_spread_nearest(an, t, h, same_kind=False),
    "spread_ewma5 (VWAPS2)": m_spread_ewma5,
    "ratio_nearest_samekind": m_ratio_nearest,
    "ratio_nearest_any": lambda an, t, h: m_ratio_nearest(an, t, h, same_kind=False),
    "global_ratio": m_global_ratio,
    "global_shift": m_global_shift,
    "kernel_ratio (~VWAPS)": m_kernel_ratio,
    "kernel_add": m_kernel_add,
    "interp_ratio": m_interp_ratio,
    "interp_add": m_interp_add,
    "line_ratio": m_line_ratio,
    "shrunk_ratio": m_shrunk_ratio,
}


def targets(curve: pd.Series, day: pd.Timestamp) -> list[Cell]:
    cells = []
    for kind, n in N_TARGET.items():
        keys = sorted(s for k, s in curve.index if k == kind and s > day)[:n]
        for pos, s in enumerate(keys):
            mid = s + (end_of(kind, s) - s) / 2
            cells.append(Cell(kind, s, math.log1p(max((mid - day).days, 0.5)), math.nan, pos=pos))
    return cells


def scenario_days(scn: str, area: str, curves: dict, de: dict):
    """Yield (day, truth Series, reference Series, reference history list)."""
    dates = sorted(curves)
    for i in range(6, len(dates)):
        t, y = dates[i], dates[i - 1]
        cur, prev = curves[t], curves[y]
        mid = ((cur + prev) / 2).dropna()
        if scn == "intraday":
            yield t, mid, cur, [curves[d] for d in dates[i - 4 : i + 1]]
        elif scn == "lag":
            yield t, mid, prev, [curves[d] for d in dates[i - 5 : i]]
        elif scn == "lagfull":
            yield t, cur, prev, [curves[d] for d in dates[i - 5 : i]]
        elif scn == "persist":  # own = EEX(T) + persistent basis (added in run)
            yield t, cur, cur, [curves[d] for d in dates[i - 4 : i + 1]]
        elif scn == "persist_lag":  # same, reference = EEX(T-1)
            yield t, cur, prev, [curves[d] for d in dates[i - 5 : i]]
        elif scn == "cross":
            if t not in de:
                continue
            dd = [d for d in sorted(de) if d <= t][-5:]
            yield t, cur, de[t], [de[d] for d in dd]


def run(scn: str, seeds=(1, 2, 3), noise=0.003, no_anchor_share=0.05, outliers=0.0, volume_noise=False, only_groups=None) -> pd.DataFrame:
    de = load("DE")
    rows = []
    for area in AREAS:
        if scn == "cross" and area == "DE":
            continue
        curves = de if area == "DE" else load(area)
        for seed in seeds:
            rng = np.random.default_rng(hash((area, seed, scn)) % 2**32)
            level, slope, past = 0.0, 0.0, []
            for day, truth, ref, hist in scenario_days(scn, area, curves, de):
                h = History(hist)
                h.past, h.day = list(past), day
                # persistent basis: AR(1) level and slope along log time, rho = 0.9
                level = 0.9 * level + 0.5 * rng.standard_normal()
                slope = 0.9 * slope + 0.2 * rng.standard_normal()
                cells = []
                for c in targets(truth, day):
                    key = (c.kind, c.start)
                    if key not in ref.index or key not in truth.index:
                        continue
                    c.ref = float(ref[key])
                    c.own = float(truth[key])
                    if scn.startswith("persist"):
                        c.own += level + slope * (c.x - 4.0)
                    cells.append(c)
                if not cells:
                    continue
                blank_day = rng.random() < no_anchor_share
                obs = [c for c in cells if not blank_day and rng.random() < p_obs(c.kind, c.pos)]
                anchors = []
                for a in obs:
                    # Volume-dependent noise: a VWAP of few trades is noisier (sigma ~ 1/sqrt(volume)).
                    vol = float(rng.lognormal(3.0, 1.0)) if volume_noise else 20.0
                    scale = noise * math.sqrt(20.0 / vol) if volume_noise else noise
                    own = a.own * (1 + scale * rng.standard_normal() + (0.03 * rng.choice([-1, 1]) if rng.random() < outliers else 0.0))
                    cell = Cell(a.kind, a.start, a.x, a.ref, own, a.pos)
                    cell.volume = vol
                    anchors.append(cell)
                obs_keys = {(a.kind, a.start) for a in obs}
                past.append((day, anchors))
                for c in cells:
                    if (c.kind, c.start) in obs_keys:
                        continue
                    if only_groups and GROUP[c.kind] not in only_groups:
                        continue
                    row = {
                        "scenario": scn,
                        "area": area,
                        "seed": seed,
                        "day": day,
                        "kind": c.kind,
                        "group": GROUP[c.kind],
                        "pos": c.pos,
                        "n_anchors": len(anchors),
                        "same_kind_anchor": any(a.kind == c.kind for a in anchors),
                        "truth": c.own,
                    }
                    for name, f in METHODS.items():
                        row[name] = f(anchors, c, h)
                    rows.append(row)
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    names = [m for m in METHODS if m in df]
    out = []
    for (scn, grp), g in [((s, "ALL"), x) for s, x in df.groupby("scenario")] + list(df.groupby(["scenario", "group"])):
        r = {"scenario": scn, "group": grp, "n": len(g)}
        for m in names:
            r[m] = (g[m] - g["truth"]).abs().mean()
        out.append(r)
    return pd.DataFrame(out)


if __name__ == "__main__":
    scns = sys.argv[1:] or ["intraday", "lag", "lagfull", "cross"]
    frames = [run(s) for s in scns]
    df = pd.concat(frames, ignore_index=True)
    df.to_csv(OUT / "results_cells.csv", index=False)
    s = summarize(df)
    s.to_csv(OUT / "results_summary.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    t = s.set_index(["scenario", "group"]).drop(columns="n").T.round(3)
    print(t.to_string())
