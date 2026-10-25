"""EEX proxy for contracts that EEX does not quote (last fallback, so no gap stays empty).

A contract without an EEX price (for example WE+2, Sum+3, a distant month in a small
market) is rebuilt from EEX itself:

    parent   hour-weighted EEX price of the coarser contracts that contain it
             (week for days/weekends/BOW, month for weeks/BOM, quarter for months,
             year for quarters and seasons)

    long contracts (Quarter, Season):   proxy = parent x EWMA(sibling / its parent)
    short contracts (Day, Weekend, Week, BOW, BOM):
                                        proxy = parent + EWMA(sibling - its parent)
    months:                             proxy = parent x (donor month / donor quarter),
                                        donor = a neighbouring market that quotes it;
                                        parent alone without donor

Siblings are quoted contracts of the same kind in the same seasonal slot (same quarter
of the year, same season, same weekday...); the two closest are averaged on each
publication, and the EWMA runs over the last `window` allowed publications. Only
publications allowed for the reference date (eex.offset_days) are used. The proxy is
then adjusted by own VWAPs exactly like a real EEX price, and is reported with its
rule and inputs so every number can be recomputed.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta

from vwaps.io_eex import EexBook
from vwaps.pricer import Pricer
from vwaps.tenors import Period, add_months

# Parents in order of preference: the smallest quoted contract that contains the target.
PARENTS = {
    "Day": ("Week", "Month"),
    "Weekend": ("Week", "Month"),
    "BOW": ("Week", "Month"),
    "Week": ("Month",),
    "BOM": ("Month",),
    "Month": ("Quarter",),
    "Quarter": ("Year",),
    "Season": ("Year",),
}
RATIO_KINDS = ("Quarter", "Season")  # parents are years: far from zero, shape is proportional
SPAN = {"Day": 1, "Weekend": 2, "Week": 7}


def _end(kind: str, start: date) -> date:
    if kind in SPAN:
        return start + timedelta(days=SPAN[kind])
    return add_months(start, {"Month": 1, "Quarter": 3, "Season": 6, "Year": 12}[kind])


def _parent_periods(kind: str, s: date, e: date) -> list[tuple[date, date]]:
    """Contracts of `kind` that overlap [s, e)."""
    if kind == "Week":
        start = s - timedelta(days=s.weekday())
    elif kind == "Month":
        start = s.replace(day=1)
    elif kind == "Quarter":
        start = date(s.year, 3 * ((s.month - 1) // 3) + 1, 1)
    else:  # Year
        start = date(s.year, 1, 1)
    out = []
    while start < e:
        end = _end(kind, start)
        out.append((start, end))
        start = end
    return out


def _siblings(kind: str, s: date) -> list[date]:
    """Candidate sibling starts in the same seasonal slot, closest first."""
    if kind == "Day":
        steps = [7 * i for i in (1, 2, 3, 4)]
    elif kind in ("Weekend", "Week"):
        steps = [7 * i for i in range(1, 9)]
    elif kind in ("Quarter", "Season"):
        return [d for n in (1, 2, 3) for d in (add_months(s, -12 * n), add_months(s, 12 * n))]
    else:
        return []
    return [d for k in steps for d in (s - timedelta(days=k), s + timedelta(days=k))]


@dataclass
class Proxy:
    price: float
    rule: str
    detail: dict = field(default_factory=dict)

    def describe(self) -> str:
        d = self.detail
        parent = f"{d['parent_kind'].lower()} {d['parent']:.2f}"
        if self.rule == "parent_x_ratio":
            return f"{parent} x EWMA sibling ratio {d['shape']:.4f}"
        if self.rule == "parent_plus_spread":
            return f"{parent} {d['shape']:+.2f} EWMA sibling spread"
        if self.rule == "parent_x_donor_shape":
            return f"{parent} x {d['donor']} month shape {d['shape']:.4f}"
        return f"{parent} (no shape available)"


class EexFallback:
    """Rebuild unquoted contracts from one curve's EEX book (and an optional donor)."""

    def __init__(self, book: EexBook, hours, window: int, halflife: float, donor: EexBook | None = None, donor_name: str = ""):
        self.book = book
        self.hours = hours
        self.window = window
        self.halflife = halflife
        self.donor = donor
        self.donor_name = donor_name
        self._dates = tuple(book.trade_dates)
        self._pricers: dict[tuple[int, date], Pricer] = {}

    def _pricer(self, book: EexBook, publication: date) -> Pricer:
        key = (id(book), publication)
        if key not in self._pricers:
            quotes, asof = book.quotes(publication, 0)
            self._pricers[key] = Pricer(quotes if asof == publication else {}, self.hours)
        return self._pricers[key]

    def _parent(self, pricer: Pricer, kind: str, s: date, e: date, pk: str) -> float | None:
        hours = self.hours.for_kind(kind) if hasattr(self.hours, "for_kind") else self.hours
        num = den = 0.0
        for ps, pe in _parent_periods(pk, s, e):
            a, b = max(s, ps), min(e, pe)
            h = hours(a, b)
            if h <= 0:
                continue
            q = pricer.price(ps, pe, kind=pk)
            if q is None or not math.isfinite(q[0]):
                return None
            num += q[0] * h
            den += h
        return num / den if den > 0 else None

    def _shape(self, pricer: Pricer, kind: str, s: date, publication: date, pk: str) -> float | None:
        """Mean sibling shape (ratio or spread to its parent) on one publication."""
        values = []
        for cs in _siblings(kind, s):
            if cs <= publication:
                continue
            ce = _end(kind, cs)
            q = pricer.price(cs, ce, kind=kind)
            if q is None or not math.isfinite(q[0]):
                continue
            parent = self._parent(pricer, kind, cs, ce, pk)
            if parent is None or (kind in RATIO_KINDS and parent <= 0):
                continue
            values.append(q[0] / parent if kind in RATIO_KINDS else q[0] - parent)
            if len(values) == 2:
                break
        return sum(values) / len(values) if values else None

    def estimate(self, period: Period, asof: date | None, donor_asof: date | None = None) -> Proxy | None:
        if asof is None or period.kind not in PARENTS:
            return None
        s, e, kind = period.start, period.end, period.kind
        pricer = self._pricer(self.book, asof)
        for pk in PARENTS[kind]:
            parent = self._parent(pricer, kind, s, e, pk)
            if parent is not None:
                break
        else:
            return None
        if kind == "Month":
            if self.donor is not None and donor_asof is not None:
                dp = self._pricer(self.donor, donor_asof)
                q = dp.price(s, e, kind="Month")
                dparent = self._parent(dp, kind, s, e, pk)
                if q is not None and dparent and dparent > 0 and math.isfinite(q[0]):
                    shape = q[0] / dparent
                    return Proxy(
                        parent * shape,
                        "parent_x_donor_shape",
                        {
                            "parent_kind": pk,
                            "parent": parent,
                            "shape": shape,
                            "donor": self.donor_name,
                            "donor_publication": donor_asof.isoformat(),
                        },
                    )
            return Proxy(parent, "parent", {"parent_kind": pk, "parent": parent})
        end = bisect_right(self._dates, asof)
        publications = self._dates[max(0, end - self.window) : end]
        n = len(publications)
        observations = []
        for i, pub in enumerate(publications):
            v = self._shape(self._pricer(self.book, pub), kind, s, pub, pk)
            if v is not None:
                observations.append({"publication": pub.isoformat(), "value": v, "weight": 2.0 ** (-(n - 1 - i) / self.halflife)})
        if not observations:
            return Proxy(parent, "parent", {"parent_kind": pk, "parent": parent})
        total = math.fsum(o["weight"] for o in observations)
        shape = math.fsum(o["value"] * o["weight"] for o in observations) / total
        if kind in RATIO_KINDS:
            return Proxy(
                parent * shape, "parent_x_ratio", {"parent_kind": pk, "parent": parent, "shape": shape, "observations": len(observations)}
            )
        return Proxy(
            parent + shape, "parent_plus_spread", {"parent_kind": pk, "parent": parent, "shape": shape, "observations": len(observations)}
        )
