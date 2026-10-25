"""Neighbour method: EEX + trust x weighted deviation (VWAP - EEX) of nearby own VWAPs."""

import json
import math
from dataclasses import replace
from datetime import date, timedelta

import pandas as pd
import pytest

from vwaps.config import load_config, validate_config
from vwaps.fill import CurveFiller
from vwaps.io_eex import REQUIRED, EexBook
from vwaps.mapping import ProductMap
from vwaps.neighbours import log_ttm
from vwaps.product_config import effective_config, parse_parameter_cells
from vwaps.tenors import resolve_tenor
from vwaps.tuning import tune_parameters

DAY = date(2026, 9, 30)  # Wednesday
YESTERDAY = date(2026, 9, 29)
OCT, NOV, DEC, JAN = date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)
EEX_TODAY = {OCT: 100.0, NOV: 110.0, DEC: 120.0, JAN: 130.0}
EEX_YESTERDAY = {OCT: 98.0, NOV: 109.0, DEC: 119.0, JAN: 129.0}


def configured(tmp_path, extra=""):
    path = tmp_path / "config.toml"
    path.write_text('[targets]\ntenors = ["M+1", "M+2", "M+3", "M+4"]\n' + extra, encoding="utf-8")
    return load_config(path)


def mapping():
    return ProductMap("P", "fill", "DE", "Base", "fixture.csv", "Base", "Europe/Berlin", region="North", unit="EUR/MWh")


def book(curves=((YESTERDAY, EEX_YESTERDAY), (DAY, EEX_TODAY))):
    rows = [(day, "Month", start, price) for day, prices in curves for start, price in prices.items()]
    return EexBook(pd.DataFrame(rows, columns=REQUIRED))


def own(observations, day=DAY):
    return pd.DataFrame(
        [dict(date=day, product="P", region="North", unit="EUR/MWh", tenor=tenor, vwap=price, volume=10.0) for tenor, price in observations]
    )


def run(cfg, observations, eex=None, start=DAY, end=DAY, **kw):
    curve = mapping()
    result = CurveFiller(cfg, observations, [curve], {curve.key: eex or book()}).run(start, end, **kw)
    assert not result.errors
    return result


def at(result, day=DAY):
    return result.filled[result.filled["reference_date"] == day].set_index("tenor")


def ltm(tenor, day=DAY):
    return log_ttm(resolve_tenor(tenor, day), day)


def expected(target, anchors, eex_target, tau=0.7, k=1.0):
    """Hand calculation of the documented formula."""
    w = [math.exp(-abs(ltm(t) - ltm(target)) / tau) for t, _ in anchors]
    local = sum(wi * dev for wi, (_, dev) in zip(w, anchors, strict=False)) / sum(w)
    trust = sum(w) / (sum(w) + k)
    return eex_target + trust * local, trust, local


def test_price_is_eex_plus_trust_times_weighted_nearby_deviation(tmp_path):
    out = at(run(configured(tmp_path), own([("M+1", 103.0), ("M+3", 121.0)])))  # deviations +3, +1
    assert out.loc["M+1", "source"] == "own" and out.loc["M+1", "price"] == 103.0
    price, trust, local = expected("M+2", [("M+1", 3.0), ("M+3", 1.0)], 110.0)
    m2 = out.loc["M+2"]
    assert m2["source"] == "eex_adjusted" and m2["estimation_method"] == "eex_plus_neighbours"
    assert m2["price"] == pytest.approx(price)
    assert m2["trust"] == pytest.approx(trust) and m2["local_deviation"] == pytest.approx(local)
    assert m2["adjustment"] == pytest.approx(price - 110.0)
    assert m2["confidence"] == pytest.approx(round(0.4 + 0.5 * trust, 3))
    audit = json.loads(m2["audit"])
    assert {a["tenor"] for a in audit} == {"M+1", "M+3"}
    assert sum(a["share"] for a in audit) == pytest.approx(1.0)
    assert m2["explanation"].startswith("EEX 110.00 + ") and m2["explanation"].endswith(f"= {price:.2f}")


def test_closer_vwaps_weigh_more_and_far_ones_fade_towards_eex(tmp_path):
    out = at(run(configured(tmp_path), own([("M+1", 110.0)])))  # +10 over EEX
    adjustments = [out.loc[t, "adjustment"] for t in ("M+2", "M+3", "M+4")]
    assert 0 < adjustments[2] < adjustments[1] < adjustments[0] < 10.0


def test_shrink_k_and_tau_log_control_trust_and_reach(tmp_path):
    base = at(run(configured(tmp_path), own([("M+1", 110.0)])))
    strict = at(run(configured(tmp_path, "[method]\nshrink_k = 10.0\n"), own([("M+1", 110.0)])))
    wide = at(run(configured(tmp_path, "[method]\ntau_log = 5.0\n"), own([("M+1", 110.0)])))
    assert strict.loc["M+3", "adjustment"] < base.loc["M+3", "adjustment"] < wide.loc["M+3", "adjustment"]


def test_without_own_vwaps_the_price_is_a_transformed_eex(tmp_path):
    out = at(run(configured(tmp_path), own([("M+1", 100.0)], day=YESTERDAY), start=DAY, end=DAY))
    assert set(out["source"]) == {"eex_smoothed"}
    # EWMA over the last 2 publications, half-life 0.5: yesterday 0.25, today 1.
    expected = [(0.25 * EEX_YESTERDAY[d] + EEX_TODAY[d]) / 1.25 for d in (OCT, NOV, DEC, JAN)]
    assert list(out["price"]) == pytest.approx(expected)
    assert (out["trust"] == 0).all() and out["explanation"].str.contains("EWMA of EEX").all()


def test_additive_method_is_safe_with_zero_and_negative_prices(tmp_path):
    eex = book(((DAY, {OCT: -5.0, NOV: 0.0, DEC: 2.0, JAN: 130.0}),))
    out = at(run(configured(tmp_path), own([("M+1", -3.0)]), eex=eex))  # +2 over EEX
    price, _, _ = expected("M+2", [("M+1", 2.0)], 0.0)
    assert out.loc["M+2", "price"] == pytest.approx(price)
    assert all(math.isfinite(p) for p in out["price"])


def test_vwap_without_comparable_eex_is_published_but_does_not_move_others(tmp_path):
    eex = book(((DAY, {NOV: 110.0, DEC: 120.0}),))  # October not quoted
    out = at(run(configured(tmp_path), own([("M+1", 150.0)]), eex=eex))
    assert out.loc["M+1", "source"] == "own"
    # Only one publication in this book: the EWMA has nothing else to average.
    assert out.loc["M+2", "source"] == "eex_smoothed" and out.loc["M+2", "price"] == 110.0


def test_min_volume_excludes_anchor_but_keeps_original(tmp_path):
    cfg = configured(tmp_path, "[method]\nmin_volume = 50\n")
    out = at(run(cfg, own([("M+1", 110.0)])))
    assert out.loc["M+1", "source"] == "own"
    assert out.loc["M+2", "source"] == "eex_smoothed"


def test_offset_minus_one_uses_yesterdays_eex_for_targets_and_deviations(tmp_path):
    cfg = configured(tmp_path, "[eex]\noffset_days = -1\n")
    out = at(run(cfg, own([("M+1", 103.0)])))
    assert set(out["eex_asof"]) == {YESTERDAY}
    price, _, _ = expected("M+2", [("M+1", 103.0 - EEX_YESTERDAY[OCT])], EEX_YESTERDAY[NOV])
    assert out.loc["M+2", "eex_settle"] == EEX_YESTERDAY[NOV]
    assert out.loc["M+2", "price"] == pytest.approx(price)


def test_tenors_eex_does_not_offer_are_not_produced(tmp_path):
    cfg = replace(configured(tmp_path), tenors=["M+1", "M+2", "M+6"])
    out = at(run(cfg, own([("M+1", 103.0)])))
    assert "M+6" not in out.index  # EEX never quoted it in this market
    assert set(out.index) == {"M+1", "M+2"}
    early = at(
        run(cfg, own([("M+1", 103.0)], day=date(2026, 9, 28)), start=date(2026, 9, 28), end=date(2026, 9, 28)), day=date(2026, 9, 28)
    )
    assert set(early.index) == {"M+1"}  # no EEX publication yet: only the own VWAP


def test_every_empty_cell_states_its_reason(tmp_path):
    cfg = replace(configured(tmp_path), tenors=["M+1", "M+2", "M+6"], only_eex_offered=False, fallback_enabled=False)
    out = at(run(cfg, own([("M+1", 103.0)])))
    assert out.loc["M+6", "source"] == "missing"
    assert out.loc["M+6", "missing_reason"] == "eex_not_quoted"
    assert out.loc["M+2", "missing_reason"] == ""
    early = at(
        run(cfg, own([("M+1", 103.0)], day=date(2026, 9, 28)), start=date(2026, 9, 28), end=date(2026, 9, 28)), day=date(2026, 9, 28)
    )
    assert set(early.loc[["M+2", "M+6"], "missing_reason"]) == {"no_eex_publication"}


def test_daily_equals_refill(tmp_path):
    cfg = configured(tmp_path)
    obs = pd.concat([own([("M+1", 99.0)], YESTERDAY), own([("M+1", 103.0)], DAY)], ignore_index=True)
    refill = at(run(cfg, obs, start=YESTERDAY, end=DAY))
    daily = at(run(cfg, obs))
    pd.testing.assert_frame_equal(refill, daily)


def test_loo_reports_eex_neighbours_and_pipeline(tmp_path):
    result = run(configured(tmp_path), own([("M+1", 103.0), ("M+2", 112.0)]), loo=True)
    methods = set(result.loo["method"])
    assert methods == {"eex", "neighbours", "pipeline_configured"}
    row = result.loo[(result.loo.tenor == "M+2") & (result.loo.method == "neighbours")].iloc[0]
    price, _, _ = expected("M+2", [("M+1", 3.0)], 110.0)
    assert row["pred"] == pytest.approx(price)


def test_config_validation_and_per_product_cells(tmp_path):
    cfg = configured(tmp_path, '[run]\nconfiguration_mode = "individual"\n')
    for bad in ({"tau_log": 0}, {"shrink_k": -1.0}, {"tau_log": float("nan")}):
        with pytest.raises(ValueError):
            validate_config(replace(cfg, **bad))
    cells = parse_parameter_cells({"tau_log": "0.3", "shrink_k": ""})
    assert cells == {"tau_log": 0.3}
    resolved = effective_config(cfg, replace(mapping(), parameter_overrides=cells))
    assert (resolved.tau_log, resolved.shrink_k) == (0.3, cfg.shrink_k)
    for section in ("[spread]\nwindow = 5\n", "[shape]\nmode = 'off'\n"):
        path = tmp_path / "old.toml"
        path.write_text(section, encoding="utf-8")
        assert not hasattr(load_config(path), "spread_window")


def test_real_engine_tuning_over_tau_and_k(tmp_path):
    cfg = configured(tmp_path)
    days = [DAY + timedelta(days=n) for n in range(-21, 1) if (DAY + timedelta(days=n)).weekday() < 5]
    rows = [
        (day, "Month", start, price + math.sin(i + j)) for i, day in enumerate(days) for j, (start, price) in enumerate(EEX_TODAY.items())
    ]
    eex = EexBook(pd.DataFrame(rows, columns=REQUIRED))
    observations = pd.concat(
        [own([("M+1", 101.0 + 0.1 * i), ("M+2", 112.0 - 0.1 * i)], day) for i, day in enumerate(days)], ignore_index=True
    )
    curve = mapping()
    result = tune_parameters(
        cfg, observations, [curve], {curve.key: eex}, days[0], days[-1], {"tau_log": [0.3, 1.0], "shrink_k": [0.5, 2.0]}, 4, 10
    )
    assert result.selected_config["tau_log"] in (0.3, 1.0)
    assert result.selected_config["shrink_k"] in (0.5, 2.0)
    assert not result.validation_report.empty


def test_cli_tune_exports_method_selection_without_changing_config(tmp_path):
    from vwaps import cli

    days = [DAY + timedelta(days=n) for n in range(-14, 1) if (DAY + timedelta(days=n)).weekday() < 5]
    config = tmp_path / "config.toml"
    config.write_text(
        '[paths]\nvwap_input="own.csv"\nmapping="mapping.csv"\neex_curves_dir="."\noutput_dir="output"\n'
        '[targets]\ntenors=["M+1", "M+2"]\n'
        '[vwap_columns]\ntenor="tenor"\nvolume="volume"\n',
        encoding="utf-8",
    )
    before = config.read_text(encoding="utf-8")
    pd.DataFrame(
        [
            dict(
                product="P",
                region="North",
                unit="EUR/MWh",
                use="fill",
                area="DE",
                profile="Base",
                eex_file="eex.csv",
                hours="Base",
                timezone="Europe/Berlin",
                comment="",
            )
        ]
    ).to_csv(tmp_path / "mapping.csv", index=False)
    pd.DataFrame(
        [
            dict(reference_date=str(day), product="P", region="North", unit="EUR/MWh", tenor=tenor, vwap=price + 0.2 * i, volume=10.0)
            for i, day in enumerate(days)
            for tenor, price in (("M+1", 101.0), ("M+2", 112.0))
        ]
    ).to_csv(tmp_path / "own.csv", index=False)
    pd.DataFrame(
        [
            dict(tradeDate=str(day), maturityType="Month", deliveryStart=str(start), settlPx=price + i * 0.3)
            for i, day in enumerate(days)
            for start, price in ((OCT, 100.0), (NOV, 111.0), (DEC, 122.0))
        ]
    ).to_csv(tmp_path / "eex.csv", index=False)
    assert cli.main(["tune", "--tau-logs", "0.3,0.7", "--shrink-ks", "1", "--validation-days", "2"], config) == 0
    selected = json.loads((tmp_path / "output" / "tuning_selected.json").read_text(encoding="utf-8"))
    method = selected["selected_parameters"]["method"]
    assert method["tau_log"] in (0.3, 0.7) and method["shrink_k"] == 1.0
    assert config.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------- optional history


def ltm_on(tenor, day, on):
    """Position of the contract `tenor` (as seen on `day`) measured from another day."""
    return log_ttm(resolve_tenor(tenor, day), on)


def test_history_is_off_by_default_and_changes_nothing(tmp_path):
    obs = pd.concat([own([("M+1", 99.0)], YESTERDAY), own([("M+1", 103.0)], DAY)], ignore_index=True)
    out = at(run(configured(tmp_path), obs, start=YESTERDAY, end=DAY))
    assert configured(tmp_path).history_days == 0
    assert set(out["source"]) <= {"own", "eex_adjusted"}
    assert (out.loc[out.source != "own", "hist_days"] == 0).all()
    assert (out["hist_audit"].fillna("") == "").all()


def test_without_vwaps_today_history_moves_the_price_and_is_auditable(tmp_path):
    cfg = configured(tmp_path, "[method]\nhistory_days = 5\n")
    out = at(run(cfg, own([("M+1", 99.0)], YESTERDAY)))  # yesterday Oct +1 over yesterday's EEX
    nov = out.loc["M+2"]
    w = math.exp(-abs(ltm_on("M+1", YESTERDAY, YESTERDAY) - ltm_on("M+2", DAY, YESTERDAY)) / 0.7)
    hist = w * 1.0 / (w + 1.0)
    assert nov["source"] == "eex_history" and nov["estimation_method"] == "eex_plus_history"
    assert nov["price"] == pytest.approx(110.0 + hist)
    assert nov["hist_deviation"] == pytest.approx(hist) and nov["hist_share"] == 1.0
    assert nov["hist_days"] == 1 and nov["trust"] == 0
    detail = json.loads(nov["hist_audit"])
    assert detail == [{"date": str(YESTERDAY), "deviation": pytest.approx(1.0), "weight": pytest.approx(w)}]
    assert "history 1 days" in nov["explanation"]


def test_today_and_history_share_the_trust(tmp_path):
    cfg = configured(tmp_path, "[method]\nhistory_days = 5\n")
    obs = pd.concat([own([("M+2", 113.0)], YESTERDAY), own([("M+1", 103.0)], DAY)], ignore_index=True)
    m2 = at(run(cfg, obs)).loc["M+2"]
    w_today = math.exp(-abs(ltm("M+1") - ltm("M+2")) / 0.7)
    # Yesterday's VWAP of the same contract (Nov, +4) is legitimate past information.
    w_past = math.exp(-abs(ltm_on("M+2", YESTERDAY, YESTERDAY) - ltm_on("M+2", DAY, YESTERDAY)) / 0.7)
    hist = w_past * 4.0 / (w_past + 1.0)
    expected_price = 110.0 + (w_today * 3.0 + 1.0 * hist) / (w_today + 1.0)
    assert m2["source"] == "eex_adjusted" and m2["estimation_method"] == "eex_plus_neighbours_and_history"
    assert m2["price"] == pytest.approx(expected_price)
    assert m2["trust"] + m2["hist_share"] == pytest.approx(1.0)
    assert m2["price"] == pytest.approx(110.0 + m2["trust"] * m2["local_deviation"] + m2["hist_share"] * m2["hist_deviation"])


def test_history_window_counts_business_days(tmp_path):
    old = date(2026, 9, 21)  # Monday, 7 business days before DAY
    eex = book(((old, EEX_YESTERDAY), (YESTERDAY, EEX_YESTERDAY), (DAY, EEX_TODAY)))
    obs = own([("M+1", 99.0)], old)
    short = at(run(configured(tmp_path, "[method]\nhistory_days = 5\n"), obs, eex=eex))
    long = at(run(configured(tmp_path, "[method]\nhistory_days = 7\n"), obs, eex=eex))
    assert set(short["source"]) == {"eex_smoothed"}
    assert set(long["source"]) == {"eex_history"}


def test_daily_equals_refill_with_history(tmp_path):
    cfg = configured(tmp_path, "[method]\nhistory_days = 5\n")
    obs = pd.concat([own([("M+1", 99.0), ("M+3", 121.0)], YESTERDAY), own([("M+2", 111.0)], DAY)], ignore_index=True)
    pd.testing.assert_frame_equal(at(run(cfg, obs, start=YESTERDAY, end=DAY)), at(run(cfg, obs)))


def test_history_days_validation_and_cells(tmp_path):
    cfg = configured(tmp_path)
    for bad in (-1, 1.5, True):
        with pytest.raises(ValueError):
            validate_config(replace(cfg, history_days=bad))
    assert parse_parameter_cells({"history_days": "10"}) == {"history_days": 10}


def test_more_volume_more_weight(tmp_path):
    from vwaps.neighbours import Anchor, estimate

    def anchor(tenor, vwap, eex, volume):
        per = resolve_tenor(tenor, DAY)
        return Anchor(tenor, per, vwap, eex, volume, log_ttm(per, DAY))

    target = resolve_tenor("M+2", DAY)
    anchors = [anchor("M+1", 101.0, 100.0, 40.0), anchor("M+3", 121.0, 120.0, 10.0)]
    flat = estimate(target, 110.0, anchors, DAY, 0.7, 1.0, volume_weight=0.0)
    weighted = estimate(target, 110.0, anchors, DAY, 0.7, 1.0, volume_weight=0.5)
    share = {c["tenor"]: c["share"] for c in weighted.contributions}
    closeness = {c["tenor"]: c["weight"] for c in flat.contributions}
    median = (40.0 + 10.0) / 2
    expected_ratio = (closeness["M+1"] * (40 / median) ** 0.5) / (closeness["M+3"] * (10 / median) ** 0.5)
    assert share["M+1"] / share["M+3"] == pytest.approx(expected_ratio)  # 4x volume -> 2x weight
    assert {c["tenor"]: c["volume"] for c in weighted.contributions} == {"M+1": 40.0, "M+3": 10.0}
    unknown = [anchor("M+1", 101.0, 100.0, float("nan")), anchor("M+3", 121.0, 120.0, 10.0)]
    assert estimate(target, 110.0, unknown, DAY, 0.7, 1.0, volume_weight=0.5).price == pytest.approx(
        estimate(target, 110.0, unknown, DAY, 0.7, 1.0, volume_weight=0.0).price
    )


def test_unknown_volume_does_not_move_others_when_a_minimum_is_set(tmp_path):
    obs = own([("M+1", 110.0)])
    obs["volume"] = float("nan")
    lenient = at(run(configured(tmp_path), obs))
    strict = at(run(configured(tmp_path, "[method]" + chr(10) + "min_volume = 10" + chr(10)), obs))
    assert lenient.loc["M+2", "source"] == "eex_adjusted"  # no minimum: unknown volume still counts
    assert strict.loc["M+1", "source"] == "own"  # the original is always published
    assert strict.loc["M+2", "source"] != "eex_adjusted"  # it moves nobody
    assert "anchor_excluded" in str(strict.loc["M+1", "flag"])


def test_tiny_reach_is_rejected_and_never_divides_by_zero(tmp_path):
    from vwaps.neighbours import Anchor, estimate

    with pytest.raises(ValueError):
        validate_config(replace(configured(tmp_path), tau_log=0.0001))
    per = resolve_tenor("M+1", DAY)
    far = Anchor("M+1", per, 150.0, 100.0, 10.0, log_ttm(per, DAY))
    target = resolve_tenor("Cal+3", DAY)
    e = estimate(target, 80.0, [far], DAY, 1e-6, 1.0)  # every weight underflows to zero
    assert e.price == 80.0 and e.trust == 0.0 and not e.contributions
