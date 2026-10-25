"""Last fallback: contracts EEX does not quote are rebuilt from EEX (parent x/+ sibling shape),
and days without any publication of the market use a neighbouring market's curve."""

import json
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from vwaps.config import load_config, validate_config
from vwaps.fill import CurveFiller
from vwaps.io_eex import REQUIRED, EexBook
from vwaps.mapping import ProductMap

DAY = date(2026, 9, 30)  # Wednesday
NL = chr(10)
YESTERDAY = date(2026, 9, 29)


def configured(tmp_path, tenors, extra="", offered=False):
    """offered=False isolates the proxy mechanics from the rule 'only tenors EEX offers'."""
    path = tmp_path / "config.toml"
    path.write_text(
        '[paths]\neex_curves_dir = "curves"\n[targets]\ntenors = '
        + json.dumps(tenors)
        + f"\nonly_eex_offered = {str(offered).lower()}\n"
        + ("" if "[fallback]" in extra else "[fallback]\nno_info_window = 1\n")
        + extra,
        encoding="utf-8",
    )
    return load_config(path)


def mapping(area="FR"):
    return ProductMap("P", "fill", area, "Base", "fixture.csv", "Base", "Europe/Berlin", region="North", unit="EUR/MWh")


def book(rows):
    return EexBook(pd.DataFrame(rows, columns=REQUIRED))


def run(cfg, eex, own=None, curve=None):
    curve = curve or mapping()
    obs = own if own is not None else pd.DataFrame(columns=["date", "product", "region", "unit", "tenor", "vwap", "volume"])
    result = CurveFiller(cfg, obs, [curve], {curve.key: eex}).run(DAY, DAY)
    assert not result.errors
    return result.filled.set_index("tenor")


def test_unquoted_weekend_is_its_week_plus_ewma_sibling_spread(tmp_path):
    cfg = configured(tmp_path, ["WE+1", "WE+2"])
    weeks = {date(2026, 10, 5): 100.0, date(2026, 10, 12): 110.0}
    rows = []
    for pub, spread in ((YESTERDAY, -20.0), (DAY, -30.0)):
        rows += [(pub, "Week", start, price) for start, price in weeks.items()]
        rows.append((pub, "Weekend", date(2026, 10, 10), 100.0 + spread))  # WE+1 quoted, WE+2 not
    out = run(cfg, book(rows))
    we2 = out.loc["WE+2"]
    w_y, w_t = 2 ** (-1 / 8.0), 1.0  # EWMA half-life 8 publications
    ewma = (w_y * -20.0 + w_t * -30.0) / (w_y + w_t)
    assert we2["source"] == "eex_proxy" and we2["eex_proxy_rule"] == "parent_plus_spread"
    assert we2["price"] == pytest.approx(110.0 + ewma)
    assert we2["eex_proxy"] == pytest.approx(110.0 + ewma) and pd.isna(we2["eex_settle"])
    detail = json.loads(we2["eex_proxy_detail"])
    assert detail["parent_kind"] == "Week" and detail["observations"] == 2
    assert "[proxy: week 110.00" in we2["explanation"]
    assert we2["confidence"] == pytest.approx(0.2)


def test_unquoted_quarter_is_its_year_times_ewma_sibling_ratio(tmp_path):
    cfg = configured(tmp_path, ["Q+6"])  # Q1-2028
    rows = [
        (DAY, "Year", date(2027, 1, 1), 100.0),
        (DAY, "Year", date(2028, 1, 1), 90.0),
        (DAY, "Quarter", date(2027, 1, 1), 120.0),  # Q1-2027 = 1.2 x its year
    ]
    q = run(cfg, book(rows)).loc["Q+6"]
    assert q["eex_proxy_rule"] == "parent_x_ratio"
    assert q["price"] == pytest.approx(90.0 * 1.2)


def test_unquoted_month_without_donor_takes_its_quarter(tmp_path):
    cfg = configured(tmp_path, ["M+2"])
    m2 = run(cfg, book([(DAY, "Quarter", date(2026, 10, 1), 150.0)])).loc["M+2"]
    assert m2["eex_proxy_rule"] == "parent" and m2["price"] == pytest.approx(150.0)


def test_unquoted_month_uses_neighbouring_market_shape(tmp_path):
    cfg = configured(tmp_path, ["M+2"])
    donor = tmp_path / "curves" / "DE"
    donor.mkdir(parents=True)
    pd.DataFrame(
        [
            dict(tradeDate=str(DAY), maturityType="Month", deliveryStart=f"2026-{m:02d}-01", settlPx=p)
            for m, p in ((10, 90.0), (11, 110.0), (12, 100.0))
        ]
    ).to_csv(donor / "fixture.csv", index=False)
    m2 = run(cfg, book([(DAY, "Quarter", date(2026, 10, 1), 150.0)])).loc["M+2"]
    donor_quarter = (90.0 * 31 + 110.0 * 30 + 100.0 * 31) / 92
    assert m2["eex_proxy_rule"] == "parent_x_donor_shape"
    assert m2["price"] == pytest.approx(150.0 * 110.0 / donor_quarter, rel=1e-3)


def test_own_vwaps_still_adjust_a_proxy(tmp_path):
    cfg = configured(tmp_path, ["M+1", "M+2"])
    rows = [(DAY, "Quarter", date(2026, 10, 1), 150.0), (DAY, "Month", date(2026, 10, 1), 148.0)]
    own = pd.DataFrame([dict(date=DAY, product="P", region="North", unit="EUR/MWh", tenor="M+1", vwap=150.0, volume=1.0)])
    m2 = run(cfg, book(rows), own).loc["M+2"]
    # November is not quoted (nor the rest of its quarter): proxy = quarter 150, then the own
    # October (+2 over EEX) moves it like any other gap.
    assert m2["source"] == "eex_proxy" and m2["eex_proxy"] == pytest.approx(150.0)
    assert m2["anchors"] == "M+1"
    assert m2["price"] == pytest.approx(150.0 + m2["trust"] * 2.0)
    assert m2["confidence"] == pytest.approx(round(0.2 + 0.3 * m2["trust"], 3))


def test_day_without_publication_uses_neighbouring_market_curve(tmp_path):
    cfg = configured(tmp_path, ["M+1", "M+2"])
    donor = tmp_path / "curves" / "DE"
    donor.mkdir(parents=True)
    pd.DataFrame(
        [dict(tradeDate=str(DAY), maturityType="Month", deliveryStart=f"2026-{m:02d}-01", settlPx=p) for m, p in ((10, 100.0), (11, 110.0))]
    ).to_csv(donor / "fixture.csv", index=False)
    own_market = book([(date(2026, 10, 5), "Month", date(2026, 11, 1), 120.0)])  # starts after DAY
    own = pd.DataFrame([dict(date=DAY, product="P", region="North", unit="EUR/MWh", tenor="M+1", vwap=105.0, volume=1.0)])
    out = run(cfg, own_market, own)
    m2 = out.loc["M+2"]
    assert m2["source"] == "eex_donor" and m2["eex_reference"] == "DE/fixture.csv"
    assert m2["eex_settle"] == 110.0
    assert 110.0 < m2["price"] < 115.0  # DE shape, FR level (+5 over DE in October)
    assert m2["explanation"].startswith("EEX DE/fixture.csv 110.00 + ")


def test_fallback_can_be_switched_off(tmp_path):
    cfg = configured(tmp_path, ["WE+2"], "[fallback]\nenabled = false\nno_info_window = 1\n")
    out = run(cfg, book([(DAY, "Week", date(2026, 10, 12), 110.0)]))
    assert out.loc["WE+2", "source"] == "missing" and out.loc["WE+2", "missing_reason"] == "eex_not_quoted"


def test_fallback_config_validation(tmp_path):
    cfg = configured(tmp_path, ["M+1"])
    assert (cfg.fallback_enabled, cfg.fallback_window, cfg.fallback_halflife) == (True, 20, 8.0)
    for bad in ({"fallback_window": 0}, {"fallback_halflife": -1.0}, {"fallback_enabled": "yes"}, {"fallback_donors": "DE"}):
        with pytest.raises(ValueError):
            validate_config(replace(cfg, **bad))


def test_offered_tenor_missing_today_is_filled_but_never_offered_is_not_produced(tmp_path):
    cfg = configured(tmp_path, ["WE+1", "WE+2", "WE+3"], offered=True)
    weeks = {date(2026, 10, 5): 100.0, date(2026, 10, 12): 110.0, date(2026, 10, 19): 120.0}
    rows = [(pub, "Week", start, price) for pub in (YESTERDAY, DAY) for start, price in weeks.items()]
    rows += [(pub, "Weekend", date(2026, 10, 10), 80.0) for pub in (YESTERDAY, DAY)]
    rows.append((YESTERDAY, "Weekend", date(2026, 10, 17), 90.0))  # WE+2 offered yesterday, missing today
    out = run(cfg, book(rows))
    assert out.loc["WE+2", "source"] == "eex_proxy"  # offered recently: a punctual gap is filled
    assert "WE+3" not in out.index  # EEX has never offered WE+3 here: not part of the curve


def test_without_any_publication_nothing_but_own_vwaps_is_produced(tmp_path):
    cfg = configured(tmp_path, ["M+1", "M+2"], offered=True)
    own = pd.DataFrame([dict(date=DAY, product="P", region="North", unit="EUR/MWh", tenor="M+1", vwap=105.0, volume=1.0)])
    out = run(cfg, book([(date(2026, 10, 5), "Month", date(2026, 11, 1), 120.0)]), own)
    assert list(out.index) == ["M+1"] and out.loc["M+1", "source"] == "own"


def test_without_own_information_the_official_price_is_never_published_as_is(tmp_path):
    cfg = configured(tmp_path, ["M+1"], "[fallback]\nno_info_window = 2\nno_info_halflife = 0.5\n")
    rows = [(YESTERDAY, "Month", date(2026, 10, 1), 98.0), (DAY, "Month", date(2026, 10, 1), 100.0)]
    m1 = run(cfg, book(rows)).loc["M+1"]
    expected = (0.25 * 98.0 + 1.0 * 100.0) / 1.25  # weights 2^(-age/0.5): yesterday 0.25, today 1
    assert m1["source"] == "eex_smoothed" and m1["price"] == pytest.approx(expected)
    assert m1["price"] != m1["eex_settle"]
    # The breakdown matches what is published: adjustment = published price - EEX used.
    assert m1["adjustment"] == pytest.approx(m1["price"] - m1["eex_settle"])
    assert m1["adjustment"] == pytest.approx(expected - 100.0)
    assert "EWMA of EEX over 2 publication(s)" in m1["explanation"]


def test_unquoted_weekend_averages_siblings_and_neighbour_shape(tmp_path):
    cfg = configured(tmp_path, ["WE+2"])
    donor = tmp_path / "curves" / "DE"
    donor.mkdir(parents=True)
    pd.DataFrame(
        [
            dict(tradeDate=str(DAY), maturityType="Week", deliveryStart="2026-10-12", settlPx=100.0),
            dict(tradeDate=str(DAY), maturityType="Weekend", deliveryStart="2026-10-17", settlPx=70.0),
        ]
    ).to_csv(donor / "fixture.csv", index=False)
    rows = [(DAY, "Week", date(2026, 10, 5), 100.0), (DAY, "Week", date(2026, 10, 12), 110.0)]
    rows.append((DAY, "Weekend", date(2026, 10, 10), 80.0))  # sibling spread -20
    we2 = run(cfg, book(rows)).loc["WE+2"]
    siblings, neighbour = 110.0 - 20.0, 110.0 * 0.7
    assert we2["eex_proxy_rule"] == "mean_siblings_and_donor"
    assert we2["price"] == pytest.approx((siblings + neighbour) / 2)
    assert "DE/fixture.csv weekend shape" in we2["explanation"]


def test_each_market_uses_its_measured_neighbour(tmp_path):
    cfg = configured(tmp_path, ["M+1"])
    assert cfg.fallback_donors_by_area["DE"][0] == "NL"
    assert cfg.fallback_donors_by_area["ES"][0] == "FR"
    extra = "[fallback]" + NL + "no_info_window = 1" + NL + "[fallback.donors_by_area]" + NL + 'DE = ["AT"]' + NL
    custom = configured(tmp_path, ["M+1"], extra)
    assert custom.fallback_donors_by_area == {"DE": ("AT",)}
    with pytest.raises(ValueError):
        validate_config(replace(cfg, fallback_donors_by_area={"DE": "AT"}))
