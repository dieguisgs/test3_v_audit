"""Curve filling engine shared by `daily` and `refill` (auditable neighbour method).

Each series is one mapped (product, region, unit) curve and is filled on its
own; see ALGORITMO.md / ALGORITHM.md for the method.

Cascade per (day, curve identity, target tenor):
  own           Original VWAP.
  eex_adjusted  EEX + trust x weighted deviation (VWAP - EEX) of nearby own VWAPs
                (plus, with history_days > 0, recent days for the remaining trust).
  eex_history   No own VWAP today, but recent days' VWAPs (history_days > 0).
  eex           EEX price, when no own VWAP of the day can be compared with EEX.
  arbitrage     Optional: rebuild from own/already estimated contracts.
  missing       No estimate is available.

Only EEX publications allowed by eex.offset_days are used. Optional history
is recomputed from the input VWAPs of earlier days (never from earlier
outputs), so a single day computed by `daily` equals the same day inside a
longer `refill`.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from vwaps.config import Config, validate_config
from vwaps.consistency import check_day
from vwaps.fallback import EexFallback, Proxy
from vwaps.hours import hours_fn
from vwaps.identity import CurveKey, curve_keys
from vwaps.io_eex import EexBook
from vwaps.log import get_logger
from vwaps.mapping import ProductMap
from vwaps.neighbours import Anchor, estimate, log_ttm
from vwaps.pricer import Pricer
from vwaps.product_config import configuration_context_records, configuration_record, effective_config
from vwaps.tenors import Key, Period, resolve_tenor

GROUPS = ("short", "month", "quarter", "long")
# Equal delivery intervals can have both full-contract and residual labels.
# Their evidence kind must not depend on the original CSV row order.
OWN_KIND_ORDER = {
    kind: index
    for index, kind in enumerate(
        (
            "Day",
            "Weekend",
            "Week",
            "Month",
            "Quarter",
            "Season",
            "Year",
            "BOW",
            "BOM",
        )
    )
}


@dataclass
class OwnQuote:
    period: Period
    tenors: list[str]
    vwap: float
    volume: float


@dataclass
class Series:
    m: ProductMap
    book: EexBook | None
    own_by_day: dict
    hfn: object
    fallback: EexFallback | None = None
    donor: EexBook | None = None  # first neighbouring market (month shapes)
    donors: list = field(default_factory=list)  # (name, book, fallback) in order of preference

    @property
    def key(self) -> CurveKey:
        return self.m.key

    @property
    def name(self) -> str:
        return self.m.label

    @property
    def output(self) -> bool:
        return self.m.use == "fill"


@dataclass
class DayPrep:
    asof: date | None
    eex: Pricer
    own: dict[Key, OwnQuote]
    anchors: list[Anchor]
    rejected: set = field(default_factory=set)
    evaluation_anchors: list[Anchor] = field(default_factory=list)
    history: list[tuple[date, list[Anchor]]] = field(default_factory=list)
    reference: str = ""  # "" = this market's EEX; else the donor market used as reference curve


@dataclass
class RunResult:
    filled: pd.DataFrame
    consistency: pd.DataFrame
    loo: pd.DataFrame = field(default_factory=pd.DataFrame)
    errors: list[dict] = field(default_factory=list)
    configuration_contexts: dict[str, dict] = field(default_factory=dict)
    # Every (reference_date, product, region, unit) computed in this run, including groups that
    # produced no rows, so publication can replace stale rows from earlier runs.
    computed: pd.DataFrame = field(default_factory=pd.DataFrame)


class CurveFiller:
    """Fill curves using immutable effective settings for each full identity.

    Mapping overrides apply in individual mode. Explicit ``curve_parameters``
    are final candidate overrides used by tuning. Optional
    ``evaluation_configs`` freeze LOO truth eligibility independently of
    candidate anchor filters; they never change production predictions.
    """

    def __init__(
        self,
        cfg: Config,
        vwaps: pd.DataFrame,
        maps: list[ProductMap],
        books: dict[CurveKey | str, EexBook | None],
        *,
        curve_parameters: dict[CurveKey, dict] | None = None,
        evaluation_configs: dict[CurveKey, Config] | None = None,
    ):
        validate_config(cfg)
        self.cfg = cfg
        self.vwaps = vwaps
        self.maps = [m for m in maps if m.active]
        self.curve_parameters = deepcopy(curve_parameters or {})
        unknown = set(self.curve_parameters) - {m.key for m in self.maps}
        if unknown:
            raise ValueError(f"Curve parameters contain unmapped or inactive curve identities: {sorted(unknown)}")
        self.curve_configs = {m.key: effective_config(cfg, m, self.curve_parameters.get(m.key)) for m in self.maps}
        self.configuration_records = {m.key: configuration_record(cfg, m, self.curve_parameters.get(m.key)) for m in self.maps}
        context_records = configuration_context_records(cfg, self.maps, self.curve_parameters)
        self.configuration_contexts = {}
        for key, context in context_records.items():
            context_id = context["configuration_context_id"]
            self.configuration_records[key]["configuration_context_id"] = context_id
            self.configuration_contexts[context_id] = context["document"]
        self.evaluation_configs = deepcopy(evaluation_configs or {})
        unknown = set(self.evaluation_configs) - {m.key for m in self.maps}
        if unknown:
            raise ValueError(f"Evaluation configurations contain inactive or unmapped curve identities: {sorted(unknown)}")
        for evaluation_cfg in self.evaluation_configs.values():
            validate_config(evaluation_cfg)
        self.books = books

    # ------------------------------------------------------------------ run
    def _build_series(self) -> list[Series]:
        by_curve = {key: frame for key, frame in self.vwaps.groupby(curve_keys(self.vwaps), sort=False)}
        out = []
        for m in self.maps:
            g = by_curve.get(m.key)
            legacy_book = self.books.get(m.product) if not m.region and not m.unit else None
            book = self.books.get(m.key, legacy_book)
            hfn = hours_fn(m.hours, m.timezone)
            cfg = self.curve_configs[m.key]
            fallback, donors = None, []
            if book is not None and cfg.fallback_enabled:
                donors = [
                    (name, donor, EexFallback(donor, hfn, cfg.fallback_window, cfg.fallback_halflife))
                    for name, donor in self._donor_books(cfg, m)
                ]
                fallback = EexFallback(book, hfn, cfg.fallback_window, cfg.fallback_halflife, [(n, b) for n, b, _ in donors])
            own = {d: x for d, x in g.groupby("date")} if g is not None else {}
            out.append(Series(m, book, own, hfn, fallback, donors[0][1] if donors else None, donors))
        return out

    def _donor_books(self, cfg: Config, m: ProductMap) -> list[tuple[str, EexBook]]:
        """Same EEX product file in the configured neighbouring markets, in order of preference."""
        cache = self.__dict__.setdefault("_donor_cache", {})
        out = []
        if not m.eex_file:
            return out
        for area in cfg.fallback_donors_by_area.get(m.area, cfg.fallback_donors):
            if area == m.area:
                continue
            path = Path(cfg.eex_curves_dir) / area / Path(m.eex_file).name
            if path not in cache:
                try:
                    cache[path] = EexBook.from_file(path) if path.exists() else None
                except Exception:
                    get_logger().warning("%s: donor EEX file %s is unreadable; skipped", m.label, path)
                    cache[path] = None
            if cache[path] is not None:
                out.append((f"{area}/{path.name}", cache[path]))
        return out

    def run(self, start: date, end: date, loo: bool = False, *, output_keys: set[tuple[date, CurveKey]] | None = None) -> RunResult:
        cfg = self.cfg
        log = get_logger()
        series = self._build_series()
        if start > end:
            raise ValueError("The start date cannot be later than the end date")
        EexBook.cutoff_date(start, cfg.eex_offset_days)
        log.info(
            "Engine %s -> %s | configuration=%s | EEX offset=%d | tau_log=%g shrink_k=%g",
            start,
            end,
            cfg.configuration_mode,
            cfg.eex_offset_days,
            cfg.tau_log,
            cfg.shrink_k,
        )
        # Nothing is learned from earlier days: only the requested days are computed.
        days: set[date] = set()
        for s in series:
            days |= {d for d in s.own_by_day if start <= d <= end}
            if s.book is not None:
                days |= {d for d in s.book.trade_dates if start <= d <= end}
        d = start
        while d <= end:  # Include every weekday in the range, even without data.
            if d.weekday() < 5:
                days.add(d)
            d += timedelta(days=1)
        if output_keys is not None:
            days.update(day for day, _ in output_keys if start <= day <= end)

        no_eex: dict[str, list[date]] = {}
        computed: list[dict] = []
        filled, cons, loo_rows, errors = [], [], [], []
        for day in sorted(days):
            cutoff = EexBook.cutoff_date(day, cfg.eex_offset_days)
            for s in series:
                if not s.output:
                    continue
                if output_keys is not None and (day, s.key) not in output_keys:
                    continue
                try:
                    prep = self._prep(s, day)
                except Exception:
                    errors.append(
                        {"reference_date": day, "product": s.m.product, "region": s.m.region, "unit": s.m.unit, "phase": "prepare"}
                    )
                    log.exception("%s %s: error preparing the day; skipping this curve", day, s.name)
                    continue
                try:
                    rows = self._fill(s, day, prep)
                    cons += check_day(rows, s.hfn)
                    if loo:
                        loo_rows += self._loo(s, day, prep)
                except Exception:
                    errors.append({"reference_date": day, "product": s.m.product, "region": s.m.region, "unit": s.m.unit, "phase": "fill"})
                    log.exception("%s %s: error filling the curve; skipping this curve", day, s.name)
                    continue
                filled += rows
                computed.append({"reference_date": day, "product": s.m.product, "region": s.m.region, "unit": s.m.unit})
                log.info(
                    "%s %s | anchors=%d | EEX cutoff=%s offset=%d latest=%s | methods=%s",
                    day,
                    s.name,
                    len(prep.anchors),
                    cutoff,
                    cfg.eex_offset_days,
                    prep.asof,
                    dict(Counter(r["estimation_method"] for r in rows)),
                )
                asof = prep.asof
                if s.book is not None and asof is None:
                    no_eex.setdefault(s.name, []).append(day)
                elif asof is not None and asof != day:
                    age = (day - asof).days
                    lvl = log.error if age >= cfg.warn_stale_days else log.warning
                    if cfg.eex_offset_days < 0:
                        lvl(
                            "%s %s: EEX cutoff %s (offset %d); latest allowed publication %s is %d calendar days before the reference date",
                            day,
                            s.name,
                            cutoff,
                            cfg.eex_offset_days,
                            asof,
                            age,
                        )
                    else:
                        lvl("%s %s: EEX for %s is unavailable; using EEX from %s (%d days earlier)", day, s.name, day, asof, age)
        not_offered = self.__dict__.pop("_not_offered", Counter())
        if not_offered:
            by_curve: dict[str, list[str]] = {}
            for (name, label), n_days in sorted(not_offered.items()):
                by_curve.setdefault(name, []).append(f"{label} ({n_days} d)")
            for name, labels in by_curve.items():
                log.info(
                    "%s: tenors not produced on some days because this market's EEX does not offer them "
                    "(days without any publication included): %s",
                    name,
                    ", ".join(labels),
                )
        for name, ds in no_eex.items():
            log.warning(
                "%s: no EEX settlement within cutoff/age limits from %s to %s (%d days): only own VWAPs are available",
                name,
                ds[0],
                ds[-1],
                len(ds),
            )
        context_ids = {row["configuration_context_id"] for row in filled}
        context_ids.update(row["configuration_context_id"] for row in loo_rows)
        contexts = {context_id: deepcopy(self.configuration_contexts[context_id]) for context_id in sorted(context_ids)}
        return RunResult(pd.DataFrame(filled), pd.DataFrame(cons), pd.DataFrame(loo_rows), errors, contexts, pd.DataFrame(computed))

    # ------------------------------------------------------- prepare the day
    def _own_quotes(self, day: date, g: pd.DataFrame | None, hfn, *, cfg: Config | None = None) -> dict[Key, OwnQuote]:
        """Aggregate equal delivery intervals independently of input row order.

        Each positive finite volume weights its price; zero or unknown volume
        uses unit weight. Reported volume sums known nonnegative values and
        stays unknown when all volumes are unknown. Equivalent aliases use a
        deterministic evidence kind: full contracts precede BOW and BOM.
        """
        if g is None:
            return {}
        cfg = self.cfg if cfg is None else cfg
        grouped: dict[Key, list[tuple[Period, str, float, float]]] = {}
        for tenor, vwap, vol in g[["tenor", "vwap", "volume"]].itertuples(index=False):
            per = resolve_tenor(tenor, day, cfg.day_convention, cfg.weekend_offset)
            if per is None or not math.isfinite(float(vwap)):
                continue
            # Peak BOW/BOM may share dates with WE/Day but have no delivery hours.
            if hfn(per.start, per.end, kind=per.kind) <= 0:
                continue
            vol = float(vol)
            if not math.isfinite(vol) or vol < 0:
                vol = math.nan
            grouped.setdefault(per.key, []).append((per, str(tenor), float(vwap), vol))
        out: dict[Key, OwnQuote] = {}
        for key, observations in grouped.items():
            period = min((row[0] for row in observations), key=lambda per: (OWN_KIND_ORDER[per.kind], per.kind))
            weights = [row[3] if math.isfinite(row[3]) and row[3] > 0 else 1.0 for row in observations]
            weight_scale = max(weights)
            weights = [weight / weight_scale for weight in weights]
            total = math.fsum(weights)
            price_scale = max(abs(row[2]) for row in observations)
            if price_scale:
                scaled_price = math.fsum(
                    (row[2] / price_scale) * (weight / total) for row, weight in zip(observations, weights, strict=False)
                )
                price = price_scale * max(-1.0, min(1.0, scaled_price))
            else:
                price = 0.0
            known_volumes = [row[3] for row in observations if math.isfinite(row[3])]
            volume = math.fsum(known_volumes) if known_volumes else math.nan
            out[key] = OwnQuote(period, sorted({row[1] for row in observations}), price, volume)
        return out

    def _eligible(
        self, own: dict[Key, OwnQuote], eex: Pricer, day: date, cfg: Config, bad: list[str] | None = None, rejected: set | None = None
    ) -> list[Anchor]:
        """Own VWAPs that may move other contracts: enough volume, a comparable
        EEX price at the same cutoff and, optionally, not too far from it."""
        anchors = []
        for q in own.values():
            unknown_volume = not math.isfinite(q.volume)
            if cfg.min_volume > 0 and (unknown_volume or q.volume < cfg.min_volume):
                # With a minimum set, a VWAP without known volume cannot show it meets it.
                if bad is not None:
                    shown = "unknown" if unknown_volume else f"{q.volume:g}"
                    bad.append(f"{','.join(q.tenors)} volume {shown} < {cfg.min_volume:g}")
                    rejected.add(q.period.key)
                continue
            r = eex.price(q.period.start, q.period.end, kind=q.period.kind)
            if r is None or not math.isfinite(r[0]):
                continue  # Published as own, but there is no EEX price to measure its deviation.
            if cfg.max_anchor_dev > 0 and abs(q.vwap - r[0]) / max(abs(r[0]), 1.0) > cfg.max_anchor_dev:
                if bad is not None:
                    bad.append(f"{','.join(q.tenors)} {q.vwap:g} vs EEX {r[0]:g}")
                    rejected.add(q.period.key)
                continue
            anchors.append(Anchor(",".join(q.tenors), q.period, q.vwap, r[0], q.volume, log_ttm(q.period, day)))
        return anchors

    def _prep(self, s: Series, day: date) -> DayPrep:
        cfg = self.curve_configs[s.key]
        quotes, asof = s.book.available_quotes(day, cfg.max_stale_days, cfg.eex_offset_days) if s.book is not None else ({}, None)
        reference = ""
        if asof is None and cfg.fallback_enabled:
            # No publication of this market yet: the first neighbouring market with one gives the
            # shape and own VWAPs give the level (deviations are measured against that same curve).
            for name, donor, _ in s.donors:
                quotes, asof = donor.available_quotes(day, cfg.max_stale_days, cfg.eex_offset_days)
                if asof is not None:
                    reference = name
                    break
        eex = Pricer(quotes, s.hfn)
        own = self._own_quotes(day, s.own_by_day.get(day), s.hfn, cfg=cfg)
        bad, rejected = [], set()
        anchors = self._eligible(own, eex, day, cfg, bad, rejected)
        if bad:
            get_logger().warning("%s %s: anchors excluded by volume/deviation; originals preserved: %s", day, s.name, "; ".join(bad))
        evaluation = anchors
        if s.key in self.evaluation_configs:
            # Candidate filters must not remove difficult observations from the exam.
            evaluation = self._eligible(own, eex, day, self.evaluation_configs[s.key])
        # History deviations are measured against this market's EEX; never mix them with a donor curve.
        history = [] if reference else self._history(s, day, cfg)
        return DayPrep(asof, eex, own, anchors, rejected, evaluation, history, reference)

    def _reference_ewma(self, s: Series, per: Period, p: DayPrep, n: int, halflife: float, fallback) -> tuple[float, int] | None:
        """EWMA of the reference price of `per` (EEX, donor EEX or proxy) over the last n allowed
        publications, age 0 = latest. Used only when there is no own information."""
        book = next((b for name, b, _ in s.donors if name == p.reference), None) if p.reference else s.book
        if book is None or p.asof is None:
            return None
        pricers = self.__dict__.setdefault("_publication_pricers", {})
        dates = [d for d in book.trade_dates if d <= p.asof][-n:]
        values, weights = [], []
        for i, pub in enumerate(dates):
            key = (id(book), pub)
            if key not in pricers:
                quotes, asof = book.quotes(pub, 0)
                pricers[key] = Pricer(quotes if asof == pub else {}, s.hfn)
            q = pricers[key].price(per.start, per.end, kind=per.kind)
            value = q[0] if q is not None else None
            if value is None and fallback is not None:
                proxy = fallback.estimate(per, pub)
                value = proxy.price if proxy is not None else None
            if value is not None and math.isfinite(value):
                values.append(value)
                weights.append(2.0 ** (-(len(dates) - 1 - i) / halflife))
        if not values:
            return None
        return math.fsum(v * w for v, w in zip(values, weights, strict=True)) / math.fsum(weights), len(values)

    def _offered(self, s: Series, label: str, p: DayPrep, cfg: Config) -> bool:
        """True if this market's EEX priced the relative tenor `label` (exactly or by hours)
        on any of its last `offered_lookback` allowed publications. Tenors that EEX does not
        offer are not produced at all; a tenor offered recently but missing today is filled."""
        if not cfg.only_eex_offered or s.book is None:
            return True
        if p.reference or p.asof is None:
            return False  # no publication of this market: nothing is offered
        cache = self.__dict__.setdefault("_offered_cache", {})
        key = (s.key, label, p.asof, cfg.offered_lookback)
        if key not in cache:
            dates = [d for d in s.book.trade_dates if d <= p.asof][-cfg.offered_lookback :]
            pricers = self.__dict__.setdefault("_publication_pricers", {})
            found = False
            for pub in reversed(dates):
                per = resolve_tenor(label, pub, cfg.day_convention, cfg.weekend_offset)
                if per is None:
                    continue
                if (s.key, pub) not in pricers:
                    quotes, asof = s.book.quotes(pub, 0)
                    pricers[(s.key, pub)] = Pricer(quotes if asof == pub else {}, s.hfn)
                if pricers[(s.key, pub)].price(per.start, per.end, kind=per.kind) is not None:
                    found = True
                    break
            cache[key] = found
        return cache[key]

    def _past_anchors(self, s: Series, day: date, cfg: Config) -> list[Anchor]:
        """Anchors of an earlier input day, with that day's own allowed EEX (cached)."""
        cache = self.__dict__.setdefault("_anchor_cache", {})
        key = (s.key, day)
        if key not in cache:
            quotes, _ = s.book.available_quotes(day, cfg.max_stale_days, cfg.eex_offset_days) if s.book is not None else ({}, None)
            own = self._own_quotes(day, s.own_by_day.get(day), s.hfn, cfg=cfg)
            cache[key] = self._eligible(own, Pricer(quotes, s.hfn), day, cfg)
        return cache[key]

    def _history(self, s: Series, day: date, cfg: Config) -> list[tuple[date, list[Anchor]]]:
        """Input days with own VWAPs in the last `history_days` business days before `day`."""
        if cfg.history_days <= 0:
            return []
        first = np.busday_offset(np.datetime64(day, "D"), -cfg.history_days, roll="backward").astype(date)
        out = []
        for past in sorted(d for d in s.own_by_day if first <= d < day):
            anchors = self._past_anchors(s, past, cfg)
            if anchors:
                out.append((past, anchors))
        return out

    # ------------------------------------------------------------ fill
    def _estimate(
        self,
        day: date,
        per: Period,
        eex_price: float | None,
        anchors: list[Anchor],
        cfg: Config,
        history: list[tuple[date, list[Anchor]]] = (),
        proxy: Proxy | None = None,
        donor: str = "",
        reference_at=None,
    ) -> dict | None:
        """EEX (or its proxy) + today's and recent nearby deviations; None without either."""
        if proxy is not None:
            eex_price = proxy.price
        if eex_price is None or not math.isfinite(eex_price):
            return None
        e = estimate(per, eex_price, anchors, day, cfg.tau_log, cfg.shrink_k, history, cfg.volume_weight)
        today, past = bool(e.contributions), bool(e.history)
        smoothed = None
        if not today and not past and reference_at is not None:
            # No own information: never publish the official reference as is.
            smoothed = reference_at(cfg.no_info_window, cfg.no_info_halflife)
        reference = f"EEX {donor}" if donor else "EEX proxy" if proxy is not None else "EEX"
        explanation = e.explanation(reference)
        price = e.price
        single_publication = False
        if smoothed is not None:
            price, n_obs = smoothed
            single_publication = n_obs == 1
            explanation = (
                f"no own VWAP today nor in history: EWMA of {reference} over {n_obs} publication(s) "
                f"(half-life {cfg.no_info_halflife:g}) = {price:.2f}"
            )
        return {
            "price": price,
            "source": (
                "eex_donor"
                if donor
                else "eex_proxy"
                if proxy is not None
                else "eex_adjusted"
                if today
                else "eex_history"
                if past
                else "eex_smoothed"
            ),
            "estimation_method": (
                "eex_plus_neighbours_and_history"
                if today and past
                else "eex_plus_neighbours"
                if today
                else "eex_plus_history"
                if past
                else "eex"
            ),
            # Main contributors only (>= 5% of today's weight); the full list is in `audit`.
            "anchors": ",".join(c["tenor"] for c in e.contributions if c["share"] >= 0.05),
            "local_deviation": e.local_deviation,
            "trust": e.trust,
            "adjustment": price - eex_price,  # published price minus the reference used (EEX, proxy or donor)
            "hist_deviation": e.hist_deviation,
            "hist_share": e.hist_share,
            "hist_days": e.hist_days,
            "explanation": explanation + (f"  [proxy: {proxy.describe()}]" if proxy is not None else ""),
            "single_publication": single_publication,
            "audit": e.contributions,
            "hist_audit": list(e.history),
            # A proxy rebuilt from coarser EEX contracts is less reliable than a quote.
            "confidence": (
                0.15 + 0.3 * e.trust
                if donor
                else 0.2 + 0.3 * e.trust
                if proxy is not None
                else 0.4 + 0.5 * e.trust + (0.1 * e.hist_share if past else 0.0)
            ),
        }

    def _fill(self, s: Series, day: date, p: DayPrep, labels: list[str] | None = None) -> list[dict]:
        cfg = self.curve_configs[s.key]
        rows, pending = [], []
        requested = list(labels if labels is not None else cfg.tenors)
        for label in dict.fromkeys(requested):
            per = resolve_tenor(label, day, cfg.day_convention, cfg.weekend_offset)
            if per is None:
                continue
            delivery_hours = s.hfn(per.start, per.end, kind=per.kind)
            q = p.own.get(per.key) if delivery_hours > 0 else None
            if q is None and not self._offered(s, label, p, cfg):
                # EEX does not offer this tenor in this market: it is not part of the curve.
                self.__dict__.setdefault("_not_offered", Counter())[(s.name, label)] += 1
                continue
            r = p.eex.price(per.start, per.end, kind=per.kind)
            proxy = None
            fallback = next((fb for name, _, fb in s.donors if name == p.reference), None) if p.reference else s.fallback
            if r is None and q is None and delivery_hours > 0 and fallback is not None:
                proxy = fallback.estimate(per, p.asof)
            row = {
                **self.configuration_records[s.key],
                "reference_date": day,
                "product": s.m.product,
                "region": s.m.region,
                "unit": s.m.unit,
                "area": s.m.area,
                "profile": s.m.profile,
                "tenor": label,
                "kind": per.kind,
                "period": per.name,
                "delivery_start": per.start,
                "delivery_end": per.end,
                "hours": delivery_hours,
                "price": math.nan,
                "source": "missing",
                "confidence": 0.0,
                "data_origin": "missing",
                "estimation_method": "unavailable",
                "own_vwap": q.vwap if q else math.nan,
                "own_volume": q.volume if q else math.nan,
                "eex_settle": r[0] if r else math.nan,
                "eex_method": r[1] if r else "",
                "eex_asof": p.asof,
                "eex_cutoff_date": EexBook.cutoff_date(day, cfg.eex_offset_days),
                "eex_offset_days": cfg.eex_offset_days,
                "eex_reference": p.reference,
                "anchors": "",
                "local_deviation": math.nan,
                "trust": math.nan,
                "adjustment": math.nan,
                "hist_deviation": math.nan,
                "hist_share": math.nan,
                "hist_days": math.nan,
                "explanation": "",
                "audit": "",
                "hist_audit": "",
                "missing_reason": "",
                "eex_proxy": proxy.price if proxy else math.nan,
                "eex_proxy_rule": proxy.rule if proxy else "",
                "eex_proxy_detail": json.dumps(proxy.detail, allow_nan=False, separators=(",", ":")) if proxy else "",
                "flag": "anchor_excluded" if per.key in p.rejected else "",
            }
            if delivery_hours <= 0:
                row["flag"] = ";".join(filter(None, [row["flag"], "zero_delivery_hours"]))
                rows.append(row)
                continue
            if q is not None:
                row.update(price=q.vwap, source="own", confidence=1.0, data_origin="original", estimation_method="none")
            else:
                est = self._estimate(
                    day,
                    per,
                    r[0] if r else None,
                    p.anchors,
                    cfg,
                    p.history,
                    proxy,
                    p.reference,
                    lambda n, hl, per=per, fallback=fallback, s=s, p=p: self._reference_ewma(s, per, p, n, hl, fallback),
                )
                if est is None:
                    pending.append(row)
                else:
                    confidence = est["confidence"] * (0.9 if p.asof != day else 1.0)
                    row.update(
                        price=est["price"],
                        source=est["source"],
                        confidence=round(confidence, 3),
                        data_origin="estimated",
                        estimation_method=est["estimation_method"],
                        anchors=est["anchors"],
                        local_deviation=est["local_deviation"],
                        trust=est["trust"],
                        adjustment=est["adjustment"],
                        hist_deviation=est["hist_deviation"],
                        hist_share=est["hist_share"],
                        hist_days=float(est["hist_days"]),
                        explanation=est["explanation"],
                        audit=json.dumps(est["audit"], allow_nan=False, separators=(",", ":")),
                        hist_audit=(json.dumps(est["hist_audit"], allow_nan=False, separators=(",", ":")) if est["hist_audit"] else ""),
                    )
                    if est["single_publication"]:
                        # Only one publication exists: the EWMA equals the official price (start of history).
                        row["flag"] = ";".join(filter(None, [row["flag"], "single_eex_publication"]))
            rows.append(row)

        if pending and cfg.layer_arbitrage:  # Filled contracts plus VWAPs outside the target list.
            known = {
                (x["delivery_start"], x["delivery_end"]): x["price"]
                for x in rows
                if x["source"] != "missing" and x["hours"] > 0 and (x["delivery_start"], x["delivery_end"]) not in p.rejected
            }
            for k, q in p.own.items():
                if k not in p.rejected:
                    known.setdefault(k, q.vwap)
            arb = Pricer(known, s.hfn)
            for row in pending:
                r = arb.price(row["delivery_start"], row["delivery_end"], kind=row["kind"])
                if r is not None:
                    row.update(
                        price=r[0],
                        source="arbitrage",
                        confidence=0.4,
                        eex_method=row["eex_method"] or r[1],
                        data_origin="estimated",
                        estimation_method=f"contract_{r[1]}",
                    )
        for row in rows:
            if row["source"] == "missing":  # Every empty cell states why it is empty.
                row["missing_reason"] = (
                    "zero_delivery_hours"
                    if row["hours"] <= 0
                    else "no_eex_publication"
                    if p.asof is None
                    else "eex_not_quoted"
                    if not math.isfinite(row["eex_settle"])
                    else "no_estimate"
                )
            if row["source"] != "missing" and not math.isfinite(row["price"]):
                raise ValueError(f"{s.name} {day} {row['tenor']}: computed price must be finite")
        return rows

    # ------------------------------------------------------------ backtest
    def _loo(self, s: Series, day: date, p: DayPrep) -> list[dict]:
        """Hide each anchor and predict it with:
        eex                 raw EEX of the day (baseline shared with VWAPS)
        neighbours          EEX + nearby deviations of the other anchors
        pipeline_configured the deployed configuration, whole curve replayed"""
        cfg = self.curve_configs[s.key]
        out = []
        for a in p.evaluation_anchors:
            r = p.eex.price(a.period.start, a.period.end, kind=a.period.kind)
            if r is None:
                continue  # Same exam as VWAPS: observations without same-cutoff EEX are not scored.
            others = [x for x in p.anchors if x.period.key != a.period.key]
            base = {
                **self.configuration_records[s.key],
                "reference_date": day,
                "product": s.m.product,
                "region": s.m.region,
                "unit": s.m.unit,
                "tenor": a.tenor,
                "kind": a.period.kind,
                "group": a.period.group,
                "own": a.own,
                "volume": a.volume,
                "n_other_anchors": len(others),
                "eex_asof": p.asof,
                "eex_cutoff_date": EexBook.cutoff_date(day, cfg.eex_offset_days),
                "eex_offset_days": cfg.eex_offset_days,
            }
            preds = {
                "eex": r[0],
                "neighbours": estimate(a.period, r[0], others, day, cfg.tau_log, cfg.shrink_k, p.history, cfg.volume_weight).price,
            }
            hidden = replace(
                p, own={k: q for k, q in p.own.items() if k != a.period.key}, anchors=others, rejected=p.rejected - {a.period.key}
            )
            label = next(
                label
                for label in p.own[a.period.key].tenors
                if resolve_tenor(label, day, cfg.day_convention, cfg.weekend_offset).kind == a.period.kind
            )
            deployed = self._fill(s, day, hidden, labels=[*cfg.tenors, label])
            prediction = next((row for row in deployed if row["tenor"] == label), None)
            if prediction is not None and math.isfinite(prediction["price"]):
                preds["pipeline_configured"] = prediction["price"]
            for method, pr in preds.items():
                out.append({**base, "method": method, "pred": pr, "error": pr - a.own})
        return out
