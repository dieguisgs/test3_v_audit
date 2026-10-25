"""Quarter gaps use complete monthly delivery strips without changing own VWAPs."""

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
from vwaps.product_config import effective_config, parse_parameter_cells, validate_parameter_overrides
from vwaps.tenors import resolve_tenor

DAY = date(2026, 9, 30)
MONTHS = ["M+1", "M+2", "M+3"]
TARGETS = [*MONTHS, "Q+1"]
CURVE = ProductMap("P", "fill", "DE", "Base", "fixture.csv", "Base", "Europe/Berlin", region="North", unit="EUR/MWh")


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[targets]\ntenors = ["M+1", "M+2", "M+3", "Q+1"]\n', encoding="utf-8")
    return replace(load_config(path), fallback_enabled=False)


def own(observations, *, day=DAY, curve=CURVE):
    return pd.DataFrame(
        [(day, curve.product, curve.region, curve.unit, tenor, price, volume) for tenor, price, volume in observations],
        columns=["date", "product", "region", "unit", "tenor", "vwap", "volume"],
    )


def book(prices=None, *, day=DAY):
    prices = {"M+1": 90.0, "M+2": 100.0, "M+3": 110.0, "Q+1": 100.0} if prices is None else prices
    return EexBook(
        pd.DataFrame(
            [(day, resolve_tenor(label, day).kind, resolve_tenor(label, day).start, price) for label, price in prices.items()],
            columns=REQUIRED,
        )
    )


def run(cfg, observations, reference=None, *, curve=CURVE, loo=False):
    result = CurveFiller(cfg, observations, [curve], {curve.key: reference}).run(DAY, DAY, loo=loo)
    assert not result.errors
    return result


@pytest.mark.parametrize("setting,enabled", [("", True), ("coherence = true", True), ("coherence = false", False)])
def test_coherence_config_defaults_to_existing_behavior_and_loads_explicit_booleans(tmp_path, setting, enabled):
    path = tmp_path / "config.toml"
    path.write_text(f"[layers]\n{setting}\n", encoding="utf-8")
    assert load_config(path).layer_coherence is enabled


@pytest.mark.parametrize("value", ["false", 0, 1, [], None])
def test_coherence_rejects_non_boolean_values_in_config_and_overrides(cfg, value):
    with pytest.raises(ValueError, match=r"layers\.coherence"):
        validate_config(replace(cfg, layer_coherence=value))
    with pytest.raises(ValueError, match="layer_coherence"):
        validate_parameter_overrides({"layer_coherence": value})


@pytest.mark.parametrize("value", ['"false"', "0", "1", "[]"])
def test_coherence_rejects_non_boolean_toml_values(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text(f"[layers]\ncoherence = {value}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"layers\.coherence"):
        load_config(path)


def test_disabled_coherence_keeps_independent_estimate_and_reports_its_discrepancy(cfg):
    observations = own([(label, 100.0, 10.0) for label in MONTHS])
    reference = book({**dict.fromkeys(MONTHS, 100.0), "Q+1": 130.0})
    enabled = run(cfg, observations, reference)
    disabled = run(replace(cfg, layer_coherence=False), observations, reference)
    on, off = (result.filled.set_index("tenor") for result in (enabled, disabled))
    assert on.loc["Q+1", "price"] == 100.0
    assert off.loc["Q+1", "price"] == 130.0
    assert off.loc["Q+1", "source"] == "eex_adjusted"
    assert off.loc["Q+1", "aggregation_audit"] == ""
    assert off.loc["Q+1", "estimation_method"] == "eex_plus_neighbours"
    pd.testing.assert_frame_equal(on.loc[MONTHS, ["price", "source"]], off.loc[MONTHS, ["price", "source"]])
    assert enabled.consistency.iloc[0]["deviation"] == 0.0
    assert disabled.consistency.iloc[0]["deviation"] == 30.0
    assert on.loc["Q+1", "configuration_id"] != off.loc["Q+1", "configuration_id"]
    assert on.loc["Q+1", "configuration_context_id"] == off.loc["Q+1", "configuration_context_id"]
    assert json.loads(off.loc["Q+1", "configuration_parameters"])["layer_coherence"] is False


def test_disabled_coherence_does_not_fill_missing_quarter_from_own_months(cfg):
    observations = own([(label, 100.0, 10.0) for label in MONTHS])
    rows = run(replace(cfg, layer_coherence=False), observations).filled.set_index("tenor")
    assert rows.loc[MONTHS, "price"].eq(100.0).all()
    assert math.isnan(rows.loc["Q+1", "price"])
    assert rows.loc["Q+1", "source"] == "missing"
    assert rows.loc["Q+1", "aggregation_audit"] == ""


@pytest.mark.parametrize("mode", ["global", "individual"])
def test_mapping_coherence_switch_is_applied_per_curve_only_in_individual_mode(cfg, mode):
    disabled_curve = replace(CURVE, region="South", parameter_overrides=parse_parameter_cells({"layer_coherence": "false"}))
    observations = pd.concat(
        [own([(label, 100.0, 10.0) for label in MONTHS], curve=curve) for curve in (CURVE, disabled_curve)], ignore_index=True
    )
    reference = book({**dict.fromkeys(MONTHS, 100.0), "Q+1": 130.0})
    result = CurveFiller(
        replace(cfg, configuration_mode=mode), observations, [CURVE, disabled_curve], {CURVE.key: reference, disabled_curve.key: reference}
    ).run(DAY, DAY)
    assert not result.errors
    quarters = result.filled[result.filled["tenor"].eq("Q+1")].set_index("region")
    assert quarters.loc["North", "source"] == "months_aggregate"
    assert quarters.loc["North", "price"] == 100.0
    assert quarters.loc["South", "source"] == ("eex_adjusted" if mode == "individual" else "months_aggregate")
    assert quarters.loc["South", "price"] == (130.0 if mode == "individual" else 100.0)
    assert (quarters.loc["North", "configuration_id"] == quarters.loc["South", "configuration_id"]) == (mode == "global")


def test_coherence_runtime_and_command_overrides_take_precedence_without_mutating_config(cfg):
    curve = replace(CURVE, parameter_overrides=parse_parameter_cells({"layer_coherence": "false"}))
    individual = replace(cfg, configuration_mode="individual")
    assert effective_config(individual, curve).layer_coherence is False
    command = replace(individual, command_overrides={"layer_coherence": True})
    assert effective_config(command, curve).layer_coherence is True
    assert effective_config(command, curve, {"layer_coherence": False}).layer_coherence is False
    assert cfg.layer_coherence is True
    assert curve.parameter_overrides == {"layer_coherence": False}


@pytest.mark.parametrize("profile,hours", [("Base", [745.0, 720.0, 744.0]), ("Peak", [264.0, 252.0, 276.0])])
@pytest.mark.parametrize("arbitrage", [False, True])
def test_own_month_strip_uses_actual_profile_hours_and_audits_every_component(cfg, profile, hours, arbitrage):
    curve = replace(CURVE, profile=profile, hours=profile)
    observations = own([("M+1", 80.0, 10.0), ("M+2", 100.0, 20.0), ("M+3", 140.0, 30.0)], curve=curve)
    result = run(replace(cfg, layer_arbitrage=arbitrage), observations, book(), curve=curve)
    rows = result.filled.set_index("tenor")
    expected = sum(price * h for price, h in zip([80.0, 100.0, 140.0], hours, strict=True)) / sum(hours)
    quarter = rows.loc["Q+1"]
    assert quarter["price"] == pytest.approx(expected)
    assert quarter["source"] == "months_aggregate"
    assert quarter["estimation_method"] == "quarter_from_months"
    assert quarter["data_origin"] == "estimated"
    assert quarter["hours"] == sum(hours)
    assert quarter["eex_settle"] == 100.0
    assert quarter["adjustment"] == pytest.approx(expected - 100.0)
    assert rows.loc[MONTHS, "price"].tolist() == [80.0, 100.0, 140.0]
    assert rows.loc[MONTHS, "source"].eq("own").all()
    assert all(pd.isna(quarter[field]) for field in ("local_deviation", "trust", "hist_deviation", "hist_share", "hist_days"))
    assert quarter["audit"] == quarter["hist_audit"] == ""
    components = json.loads(quarter["aggregation_audit"])
    assert [part["tenor"] for part in components] == MONTHS
    assert [part["price"] for part in components] == [80.0, 100.0, 140.0]
    assert [part["hours"] for part in components] == hours
    assert all(part["source"] == "own" for part in components)
    assert sum(part["share"] for part in components) == pytest.approx(1.0)
    assert sum(part["price"] * part["share"] for part in components) == pytest.approx(expected)
    for part in components:
        period = resolve_tenor(part["tenor"], DAY)
        assert part["delivery_start"] == period.start.isoformat()
        assert part["delivery_end"] == period.end.isoformat()
    assert result.consistency.iloc[0]["deviation"] == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("coherence", [False, True])
def test_own_quarter_is_preserved_and_its_discrepancy_reported(cfg, coherence):
    observations = own([("M+1", 100.0, 10.0), ("M+2", 100.0, 10.0), ("M+3", 100.0, 10.0), ("Q+1", 130.0, 10.0)])
    result = run(replace(cfg, layer_coherence=coherence), observations, book())
    quarter = result.filled.set_index("tenor").loc["Q+1"]
    assert quarter["price"] == quarter["own_vwap"] == 130.0
    assert quarter["source"] == "own"
    assert quarter["data_origin"] == "original"
    assert quarter["aggregation_audit"] == ""
    check = result.consistency.iloc[0]
    assert check["from_parts"] == 100.0
    assert check["deviation"] == 30.0


def test_quarter_uses_final_estimated_and_original_month_prices(cfg):
    observations = own([("M+1", 120.0, 10.0)])
    rows = run(cfg, observations, book()).filled.set_index("tenor")
    months = rows.loc[MONTHS]
    assert months["source"].tolist() == ["own", "eex_adjusted", "eex_adjusted"]
    expected = (months["price"] * months["hours"]).sum() / months["hours"].sum()
    quarter = rows.loc["Q+1"]
    assert quarter["price"] == pytest.approx(expected)
    assert quarter["source"] == "months_aggregate"
    components = json.loads(quarter["aggregation_audit"])
    assert [part["source"] for part in components] == months["source"].tolist()
    assert [part["price"] for part in components] == pytest.approx(months["price"].tolist())


def test_all_own_months_outside_targets_can_complete_the_requested_quarter(cfg):
    observations = own([("M+1", 105.0, 10.0), ("M+2", 105.0, 10.0), ("M+3", 105.0, 10.0)])
    result = run(replace(cfg, tenors=["Q+1"]), observations)
    assert result.filled["tenor"].tolist() == ["Q+1"]
    quarter = result.filled.iloc[0]
    assert quarter["price"] == 105.0
    assert quarter["source"] == "months_aggregate"
    assert math.isnan(quarter["adjustment"])
    assert len(json.loads(quarter["aggregation_audit"])) == 3


def test_absent_month_target_is_not_silently_reconstructed_for_aggregation(cfg):
    incomplete = replace(cfg, tenors=["M+1", "M+2", "Q+1"])
    rows = run(incomplete, own([]), book()).filled.set_index("tenor")
    assert rows.index.tolist() == ["M+1", "M+2", "Q+1"]
    assert rows.loc["Q+1", "source"] == "eex_smoothed"
    assert rows.loc["Q+1", "price"] == 100.0
    assert rows.loc["Q+1", "aggregation_audit"] == ""


def test_missing_month_price_keeps_quarter_unpriced(cfg):
    observations = own([("M+1", 100.0, 10.0), ("M+3", 100.0, 10.0)])
    rows = run(cfg, observations).filled.set_index("tenor")
    assert math.isnan(rows.loc["M+2", "price"])
    assert math.isnan(rows.loc["Q+1", "price"])
    assert rows.loc["Q+1", "source"] == "missing"
    assert rows.loc["Q+1", "aggregation_audit"] == ""


@pytest.mark.parametrize("filter_kind", ["volume", "deviation"])
@pytest.mark.parametrize("months_in_targets", [False, True])
def test_rejected_own_month_cannot_reenter_through_aggregation(cfg, filter_kind, months_in_targets):
    filtered = replace(
        cfg,
        tenors=TARGETS if months_in_targets else ["Q+1"],
        min_volume=5.0 if filter_kind == "volume" else 0.0,
        max_anchor_dev=0.2 if filter_kind == "deviation" else 0.0,
    )
    rejected_price = 180.0 if filter_kind == "deviation" else 100.0
    rejected_volume = 1.0 if filter_kind == "volume" else 10.0
    observations = own([("M+1", 100.0, 10.0), ("M+2", rejected_price, rejected_volume), ("M+3", 100.0, 10.0)])
    rows = run(filtered, observations, book()).filled.set_index("tenor")
    assert rows.loc["Q+1", "source"] != "months_aggregate"
    assert rows.loc["Q+1", "aggregation_audit"] == ""
    if months_in_targets:
        assert rows.loc["M+2", "flag"] == "anchor_excluded"
        assert rows.loc["M+2", "price"] == rejected_price


def test_months_from_another_curve_cannot_complete_this_quarter(cfg):
    other = replace(CURVE, region="South")
    observations = pd.concat(
        [own([("M+1", 100.0, 10.0), ("M+2", 100.0, 10.0)]), own([("M+3", 100.0, 10.0)], curve=other)],
        ignore_index=True,
    )
    result = CurveFiller(cfg, observations, [CURVE, other], {CURVE.key: None, other.key: None}).run(DAY, DAY)
    assert not result.errors
    quarters = result.filled[result.filled["tenor"].eq("Q+1")]
    assert len(quarters) == 2
    assert quarters["source"].eq("missing").all()


def test_aggregation_does_not_add_an_unoffered_quarter(cfg):
    observations = own([("M+1", 100.0, 10.0), ("M+2", 100.0, 10.0), ("M+3", 100.0, 10.0)])
    result = run(cfg, observations, book({"M+4": 100.0}))
    assert cfg.only_eex_offered
    assert result.filled["tenor"].tolist() == MONTHS


def test_quarter_result_and_audit_do_not_depend_on_target_order(cfg):
    observations = own([("M+1", 120.0, 10.0)])
    first = run(cfg, observations, book()).filled.set_index("tenor")
    second = run(replace(cfg, tenors=["Q+1", "M+3", "M+1", "M+2"]), observations, book()).filled.set_index("tenor")
    columns = ["price", "source", "estimation_method", "aggregation_audit"]
    pd.testing.assert_frame_equal(first[columns].sort_index(), second[columns].sort_index())


@pytest.mark.parametrize("hidden_price", [200.0, 800.0])
@pytest.mark.parametrize("coherence", [False, True])
def test_loo_removes_hidden_quarter_before_months_are_estimated(cfg, hidden_price, coherence):
    cfg = replace(cfg, layer_coherence=coherence)
    reference = book()
    result = run(cfg, own([("Q+1", hidden_price, 10.0)]), reference, loo=True)
    score = result.loo[(result.loo["tenor"] == "Q+1") & (result.loo["method"] == "pipeline_configured")].iloc[0]
    explicit_hidden = run(cfg, own([]), reference).filled.set_index("tenor")
    months = explicit_hidden.loc[MONTHS]
    expected = (months["price"] * months["hours"]).sum() / months["hours"].sum() if coherence else 100.0
    assert explicit_hidden.loc["Q+1", "source"] == ("months_aggregate" if coherence else "eex_smoothed")
    assert score["pred"] == pytest.approx(expected)
    assert score["pred"] == explicit_hidden.loc["Q+1", "price"]
    assert score["own"] == hidden_price


@pytest.mark.parametrize("coherence", [False, True])
def test_single_day_and_refill_produce_identical_aggregates_with_history(cfg, coherence):
    yesterday = DAY - timedelta(days=1)
    cfg = replace(cfg, history_days=3, layer_coherence=coherence)
    observations = pd.concat(
        [own([("M+1", 105.0, 10.0)], day=yesterday), own([("M+1", 120.0, 10.0)])],
        ignore_index=True,
    )
    settlements = pd.DataFrame(
        [(day, "Month", date(2026, month, 1), price) for day in [yesterday, DAY] for month, price in [(10, 90), (11, 100), (12, 110)]],
        columns=REQUIRED,
    )
    reference = EexBook(settlements)
    daily = run(cfg, observations, reference).filled
    refill = CurveFiller(cfg, observations, [CURVE], {CURVE.key: reference}).run(yesterday, DAY)
    assert not refill.errors
    today = refill.filled[refill.filled["reference_date"].eq(DAY)].reset_index(drop=True)
    pd.testing.assert_frame_equal(daily, today)
    quarter = today.set_index("tenor").loc["Q+1"]
    assert quarter["source"] == ("months_aggregate" if coherence else "eex_adjusted")
    if coherence:
        assert len(json.loads(quarter["aggregation_audit"])) == 3
    else:
        assert quarter["aggregation_audit"] == ""
