"""Auditable gap filling: EEX + the deviation of nearby own VWAPs.

For a target contract without own VWAP:

    deviation_i = VWAP_i - EEX_i                          (EUR/MWh, per anchor)
    w_i         = exp(-|ltm_i - ltm_target| / tau_log)     (closeness on the curve)
                  x (volume_i / median volume) ** volume_weight   (more volume, less noise)
    local       = sum(w_i * deviation_i) / sum(w_i)        (weighted mean deviation)
    lambda      = sum(w_i) / (sum(w_i) + shrink_k)         (how much we trust it)
    price       = EEX_target + lambda * local

ltm is log(1 + days to the middle of delivery). Without usable anchors,
lambda = 0 and the price is EEX. Everything is additive (no division by
prices).

Optional history (history_days > 0): the same local deviation is computed for
the SAME delivery contract on each of the last N input days, with that day's
own VWAPs and that day's allowed EEX:

    hist   = sum(W_d * local_d) / (sum(W_d) + shrink_k)      (prudent mean)
    price  = EEX_target + (W * local + shrink_k * hist) / (W + shrink_k)

so the share of trust that today's VWAPs do not cover goes to recent history
instead of to zero. With no history the formula above is unchanged. History
is recomputed from the input on every run (no stored state), so `daily`
equals `refill`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from vwaps.tenors import Period


@dataclass
class Anchor:
    tenor: str
    period: Period
    own: float
    eex: float
    volume: float
    ltm: float  # log(1 + days to the middle of delivery)

    @property
    def deviation(self) -> float:
        return self.own - self.eex


def log_ttm(p: Period, ref: date) -> float:
    mid = (p.start - ref).days + (p.end - p.start).days / 2.0
    return math.log1p(max(mid, 0.5))


@dataclass(frozen=True)
class Estimate:
    price: float
    eex: float
    local_deviation: float  # weighted mean deviation of today's anchors (nan without anchors)
    weight_sum: float  # sum of today's closeness weights
    trust: float  # share applied to today's deviation, W / (W + k)
    contributions: list[dict]
    hist_deviation: float = math.nan  # prudent mean deviation of recent days (nan without history)
    hist_share: float = 0.0  # share applied to it, k / (W + k)
    history: tuple = ()  # per-day detail: date, local deviation, weight

    @property
    def adjustment(self) -> float:
        return self.price - self.eex

    @property
    def hist_days(self) -> int:
        return len(self.history)

    def explanation(self, reference: str = "EEX") -> str:
        """One readable line: reference + today's part + history part = price."""
        parts = []
        if self.contributions:
            top = sorted(self.contributions, key=lambda c: -c["share"])[:3]
            detail = ", ".join(f"{c['tenor']} {c['deviation']:+.2f} ({c['share']:.0%})" for c in top)
            more = f" +{len(self.contributions) - len(top)} more" if len(self.contributions) > len(top) else ""
            parts.append(f"{self.trust:.2f} x {self.local_deviation:+.2f} [{detail}{more}]")
        if self.history:
            parts.append(f"{self.hist_share:.2f} x {self.hist_deviation:+.2f} [history {self.hist_days} days]")
        if not parts:
            return f"{reference} {self.eex:.2f} (no own VWAP nearby) = {self.price:.2f}"
        return f"{reference} {self.eex:.2f} + " + " + ".join(parts) + f" = {self.price:.2f}"


def volume_factor(volume: float, median: float, volume_weight: float) -> float:
    """(volume / median volume of the day) ** volume_weight; 1 when volume is unknown."""
    if volume_weight == 0 or not (math.isfinite(volume) and volume > 0 and median > 0):
        return 1.0
    return (volume / median) ** volume_weight


def _local(
    target: Period, anchors: list[Anchor], day: date, tau_log: float, exclude_target: bool, volume_weight: float = 0.0
) -> tuple[float, float, list[tuple[float, Anchor]]]:
    """Weighted mean deviation at the target's position and the sum of weights.

    Weight = closeness x volume factor: a VWAP with 4x the day's median volume weighs 2x with
    volume_weight = 0.5 (its noise is smaller)."""
    usable = [a for a in anchors if (not exclude_target or a.period.key != target.key) and math.isfinite(a.own) and math.isfinite(a.eex)]
    if not usable:
        return math.nan, 0.0, []
    lt = log_ttm(target, day)
    volumes = sorted(a.volume for a in usable if math.isfinite(a.volume) and a.volume > 0)
    median = (
        volumes[len(volumes) // 2]
        if len(volumes) % 2
        else (sum(volumes[len(volumes) // 2 - 1 : len(volumes) // 2 + 1]) / 2 if volumes else 0.0)
    )
    weighted = [(math.exp(-abs(a.ltm - lt) / tau_log) * volume_factor(a.volume, median, volume_weight), a) for a in usable]
    total = math.fsum(w for w, _ in weighted)
    if not (total > 0 and math.isfinite(total)):
        return math.nan, 0.0, []  # every weight underflowed: no usable evidence
    local = math.fsum(w * a.deviation for w, a in weighted) / total
    return local, total, weighted


def estimate(
    target: Period,
    target_eex: float,
    anchors: list[Anchor],
    day: date,
    tau_log: float,
    shrink_k: float,
    history: list[tuple[date, list[Anchor]]] = (),
    volume_weight: float = 0.0,
) -> Estimate:
    local, total, weighted = _local(target, anchors, day, tau_log, exclude_target=True, volume_weight=volume_weight)
    lt = log_ttm(target, day)
    contributions = [
        {
            "tenor": a.tenor,
            "period": f"{a.period.start}..{a.period.end}",
            "vwap": a.own,
            "eex": a.eex,
            "deviation": a.deviation,
            "distance": abs(a.ltm - lt),
            "volume": a.volume if math.isfinite(a.volume) else None,
            "weight": w,
            "share": w / total,
        }
        for w, a in sorted(weighted, key=lambda x: -x[0])
    ]
    # History: same delivery contract, each past day with its own VWAPs and EEX.
    # A past VWAP of the target contract itself is legitimate past information.
    days = []
    for past_day, past_anchors in history:
        past_local, past_total, _ = _local(target, past_anchors, past_day, tau_log, exclude_target=False, volume_weight=volume_weight)
        if past_total > 0:
            days.append({"date": past_day.isoformat(), "deviation": past_local, "weight": past_total})
    hist = (math.fsum(d["weight"] * d["deviation"] for d in days) / (math.fsum(d["weight"] for d in days) + shrink_k)) if days else math.nan
    today = total * local if total > 0 else 0.0
    past = shrink_k * hist if days else 0.0
    price = target_eex + (today + past) / (total + shrink_k)
    trust = total / (total + shrink_k)
    return Estimate(
        price, target_eex, local, total, trust, contributions, hist, shrink_k / (total + shrink_k) if days else 0.0, tuple(days)
    )
