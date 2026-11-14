"""What a strategy may be, which contract it owns, and the page's types (A1)."""

from __future__ import annotations

import re

import pytest

from sudarshan.automation import contract, gentypes
from sudarshan.automation import model as m

BANNED = re.compile(r"\b(arm|armed|arming|paper|virtual)\b|\{|\}|\b[45]\d\d\b", re.I)


def test_a_future_strategy_is_cleaned_to_its_columns(form):
    got = contract.clean(form)
    assert (got["exchange"], got["underlying"], got["expiry_rule"]) == ("NFO", "NIFTY", "month")
    assert (got["lots"], got["quantity"], got["size_mode"]) == (1, None, "fixed")
    assert (got["max_age_s"], got["max_alerts_day"]) == (30, 20)
    assert got["contract_key"] == "NFO:NIFTY:FUT" and got["roll_on_expiry_day"] is False
    assert contract.clean({**form, "product": "NRML"})["roll_on_expiry_day"] is True  # carry rolls


@pytest.mark.parametrize(
    ("chart", "product", "extra", "key", "exchange", "underlying"),
    [
        ("NSE:RELIANCE", "MIS", {"quantity": 10}, "NSE:RELIANCE", "NSE", "RELIANCE"),
        (
            "bse:reliance",
            "CNC",
            {"quantity": 5, "direction": "long"},
            "BSE:RELIANCE",
            "BSE",
            "RELIANCE",
        ),
        ("NSE:M_M", "MIS", {"quantity": 1}, "NSE:M_M", "NSE", "M_M"),
        ("NSE:NIFTY1!", "MIS", {"lots": 2}, "NFO:NIFTY:FUT", "NFO", "NIFTY"),
        ("NSE:CNXFINANCE2!", "NRML", {"lots": 1}, "NFO:FINNIFTY:FUT", "NFO", "FINNIFTY"),
        (
            "NSE:NIFTY261124C25000",
            "MIS",
            {"lots": 1, "direction": "long"},
            "NFO:NIFTY261124C25000",
            "NFO",
            "NIFTY",
        ),
    ],
)
def test_a_strategy_that_trades_its_chart(chart, product, extra, key, exchange, underlying):
    got = contract.clean({"name": "S", "kind": "same", "chart": chart, "product": product, **extra})
    assert (got["contract_key"], got["exchange"], got["underlying"]) == (key, exchange, underlying)


def test_a_chart_future_and_a_named_future_are_one_contract():
    by_chart = contract.clean(
        {"name": "A", "kind": "same", "chart": "NSE:NIFTY1!", "product": "MIS"}
    )
    by_name = contract.clean(
        {"name": "B", "kind": "future", "underlying": "NIFTY", "product": "MIS"}
    )
    assert by_chart["contract_key"] == by_name["contract_key"]


@pytest.mark.parametrize(
    ("change", "field", "says"),
    [
        ({"name": ""}, "name", "Give the strategy a name"),
        ({"name": "x" * 41}, "name", "Give the strategy a name"),
        ({"name": "ORB {1}"}, "name", "Give the strategy a name"),
        ({"kind": "option"}, "kind", "Option strategies come in a later release."),
        ({"kind": "basket"}, "kind", "Choose what the strategy trades."),
        ({"exchange": "MCX"}, "exchange", "Commodity strategies are not supported yet."),
        ({"chart": "MCX:CRUDEOIL1!"}, "chart", "Commodity strategies are not supported yet."),
        ({"chart": "NIFTY"}, "chart", "exchange first"),
        ({"underlying": ""}, "underlying", "Name the underlying"),
        ({"product": "CNC"}, "product", "Futures and options take"),
        ({"product": "BO"}, "product", "Choose Intraday"),
        ({"lots": 0}, "lots", "Lots must be a whole number from 1 to 100."),
        ({"lots": 1.5}, "lots", "Lots must be a whole number"),
        ({"lots": True}, "lots", "Lots must be a whole number"),
        ({"max_age_s": 9}, "max_age_s", "from 10 to 120"),
        ({"max_age_s": 121}, "max_age_s", "from 10 to 120"),
        ({"max_alerts_day": 0}, "max_alerts_day", "from 1 to 200"),
        ({"expiry_rule": "nearest"}, "expiry_rule", "this month's future or next month's"),
        ({"direction": "up"}, "direction", "Choose long and short"),
        ({"mode": "live"}, None, "does not know"),  # the mode has its own route
        ({"state": "active"}, None, "does not know"),
        ({"token": "x"}, None, "does not know"),
    ],
)
def test_a_form_that_cannot_be_saved_is_refused_in_a_sentence(form, change, field, says):
    with pytest.raises(contract.Refused) as refused:
        contract.clean({**form, **change})
    assert says in str(refused.value) and refused.value.field == field
    assert not BANNED.search(str(refused.value))


@pytest.mark.parametrize(
    ("form", "says"),
    [
        ({"kind": "same", "product": "MIS", "quantity": 1}, "needs the chart's name"),
        (
            {"kind": "same", "chart": "NSE:NIFTY", "product": "MIS", "quantity": 1},
            "An index cannot",
        ),
        ({"kind": "same", "chart": "NFO:NIFTY1!", "product": "MIS"}, "NSE or BSE chart"),
        ({"kind": "same", "chart": "NSE:SBIN", "product": "NRML", "quantity": 1}, "Shares take"),
        ({"kind": "same", "chart": "NSE:SBIN", "product": "CNC", "quantity": 1}, "cannot go short"),
        ({"kind": "same", "chart": "NSE:SBIN", "product": "MIS"}, "Shares must be a whole number"),
        (
            {"kind": "same", "chart": "NSE:NIFTY261124C25000", "product": "MIS", "lots": 1},
            "can only buy that option",
        ),
    ],
)
def test_the_rules_of_a_chart_strategy(form, says):
    with pytest.raises(contract.Refused) as refused:
        contract.clean({"name": "S", **form})
    assert says in str(refused.value)


def test_the_form_of_a_saved_strategy_cleans_to_the_same_columns(form, store):
    saved, _ = store.create(contract.clean(form))
    assert contract.clean(contract.form_of(saved)) == contract.clean(form)


def test_the_token_pattern_is_the_one_the_store_makes(store, form):
    _, token = store.create(contract.clean(form))
    assert contract.TOKEN.fullmatch(token)


def test_the_types_name_every_route_field_and_verdict():
    text = gentypes.render()
    for verdict in m.VERDICTS:
        assert f"'{verdict}'" in text
    for _method, path, *_ in contract.ROUTES:
        assert f"'{contract.API_PREFIX}{path}'" in text
    for name in (
        "Strategy",
        "StrategyForm",
        "AlertLine",
        "AutomationEvent",
        "ListAnswer",
        "Refusal",
    ):
        assert f"export interface {name} {{" in text
    assert "token_sha256" not in text and "payload" not in text  # never sent to the page
    assert set(contract.ALERT) <= {
        c.name
        for c in __import__(
            "sudarshan.automation.schema", fromlist=["AlertRow"]
        ).AlertRow.__table__.columns
    }
    referenced = set(re.findall(r": ([A-Z][A-Za-z]+)", text))
    declared = set(re.findall(r"export (?:interface|type) ([A-Za-z]+)", text))
    assert referenced - declared <= {"Record", "Partial"}, referenced - declared


def test_the_page_file_when_it_exists_is_the_generated_one():
    if not gentypes.TARGET.exists():
        pytest.skip("the page's lane has not written types.ts yet")
    assert gentypes.TARGET.read_text() == gentypes.render()
