"""Scaled size (A8b). ORDER PATH, behind the automation flag.

A scaled strategy's size is the chart's position size times the strategy's multiplier. It must
come to whole lots and never above the strategy's cap; anything else sends nothing. The size is
the chart's position after the alert, never the order's own contracts, and it is never rounded.
"""

from __future__ import annotations

import pytest
from automation_world import NOW, TV, FakeDesk, indicator_alert, strategy_alert

from sudarshan.automation import contract, flag, intake
from sudarshan.automation import model as m

FORM = {
    "name": "SCL", "kind": "future", "underlying": "NIFTY", "product": "MIS",
    "size_mode": "scaled", "multiplier": 65, "max_lots": 3,
}  # fmt: skip
NOV = "NIFTY24NOV26FUT"


@pytest.fixture
def desk():
    return FakeDesk()


@pytest.fixture(autouse=True)
def _sandbox_stage(monkeypatch):
    monkeypatch.setenv(flag.ENV, "sandbox")
    intake._per_link.clear()
    intake._refused.clear()


def make(store, **change):
    strategy, token = store.create(contract.clean({**FORM, **change}))
    return store.set_state(strategy.id, strategy.version, m.ACTIVE), token


def send(store, desk, token, position, size, n, **more):
    body = strategy_alert(position, NOW - 20 + n, size=size, order_id=f"o{n}", **more)
    return intake.handle(token, body, TV, desk, store)


def test_the_form_keeps_the_multiplier_and_the_cap_and_no_fixed_size():
    got = contract.clean(FORM)
    assert (got["size_mode"], got["multiplier"], got["max_lots"]) == ("scaled", 65.0, 3)
    assert (got["lots"], got["quantity"]) == (None, None)
    share = contract.clean(
        {"name": "S", "kind": "same", "chart": "NSE:RELIANCE", "product": "MIS",
         "size_mode": "scaled", "multiplier": 1, "max_lots": 500}
    )  # fmt: skip
    assert (share["max_lots"], share["quantity"], share["lots"]) == (500, None, None)
    fixed = contract.clean({"name": "F", "kind": "future", "underlying": "NIFTY", "product": "MIS"})
    assert (fixed["size_mode"], fixed["multiplier"], fixed["max_lots"], fixed["lots"]) == (
        "fixed", None, None, 1,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"multiplier": None}, "multiplier"),
        ({"multiplier": 0}, "multiplier"),
        ({"multiplier": True}, "multiplier"),
        ({"multiplier": "65"}, "multiplier"),
        ({"max_lots": None}, "max_lots"),
        ({"max_lots": 0}, "max_lots"),
        ({"max_lots": 101}, "max_lots"),
        ({"size_mode": "double"}, "size_mode"),
        ({"kind": "option"}, "size_mode"),
    ],
)
def test_a_scaled_form_that_cannot_be_saved(change, field):
    with pytest.raises(contract.Refused) as refused:
        contract.clean({**FORM, **change})
    assert refused.value.field == field


def test_scaling_in_and_out_and_the_exit(store, desk):
    strategy, token = make(store)
    assert send(store, desk, token, "long", 1, 1, before="flat").verdict == "sent"
    assert desk.position(NOV) == 65
    got = send(store, desk, token, "long", 3, 2, before="long")
    assert got.sentence == "SCL: order sent to be long 195 NIFTY 24 Nov FUT."
    assert desk.position(NOV) == 195
    assert send(store, desk, token, "long", 2, 3, before="long").verdict == "sent"
    assert desk.position(NOV) == 130  # scaled out by one lot
    assert send(store, desk, token, "short", 1, 4, before="long").verdict == "sent"
    assert desk.position(NOV) == -65
    assert send(store, desk, token, "flat", 0, 5, before="short").verdict == "sent"
    assert desk.position(NOV) == 0
    assert [o["position_size"] for o in desk.placed] == ["65", "195", "130", "-65", "0"]


def test_above_the_cap_nothing_is_sent_and_the_position_stays(store, desk):
    strategy, token = make(store)
    send(store, desk, token, "long", 3, 1)
    got = send(store, desk, token, "long", 4, 2)
    assert got.verdict == "not_sized" and len(desk.placed) == 1 and desk.position(NOV) == 195
    assert got.sentence == (
        "SCL: the chart's size times this strategy's multiplier is above its cap for "
        "NIFTY 24 Nov FUT (195). Nothing was sent. Raise the cap in the strategy if you mean to "
        "trade this size."
    )
    assert [t[1] for t in desk.told] == ["not_sized"]
    state = store.state(strategy.id)
    assert (state.last_target, state.held_symbol) == (1, NOV)  # nothing moved


def test_a_size_that_is_not_whole_lots_is_never_rounded(store, desk):
    strategy, token = make(store, multiplier=1)
    got = send(store, desk, token, "long", 100, 1)
    assert got.verdict == "not_sized" and desk.placed == []
    assert "does not come to whole lots of 65 of NIFTY 24 Nov FUT" in got.sentence


def test_an_alert_with_no_size_sends_nothing_but_its_exit_goes(store, desk):
    strategy, token = make(store)
    send(store, desk, token, "long", 2, 1)
    got = intake.handle(token, indicator_alert("short", NOW - 5), TV, desk, store)
    assert got.verdict == "not_sized" and len(desk.placed) == 1
    assert "does not carry the chart's position size" in got.sentence
    out = intake.handle(token, indicator_alert("flat", NOW - 4), TV, desk, store)
    assert out.verdict == "sent" and desk.position(NOV) == 0  # an exit needs no size


def test_the_size_is_the_charts_position_never_the_orders_contracts(store, desk):
    """Mutation 8, for the scaled mode: contracts 999 is the order, size 2 is the position."""
    strategy, token = make(store)
    send(store, desk, token, "long", 2, 1, contracts="999")
    assert [o["position_size"] for o in desk.placed] == ["130"]


def test_a_fixed_strategy_still_ignores_the_charts_size(store, desk):
    strategy, token = make(store, size_mode="fixed", lots=1, multiplier=None, max_lots=None)
    send(store, desk, token, "long", 40, 1)
    assert [o["position_size"] for o in desk.placed] == ["65"]


def test_scaled_shares(store, desk):
    strategy, token = make(
        store, kind="same", chart="NSE:RELIANCE", underlying=None, multiplier=10, max_lots=100
    )
    body = strategy_alert("long", NOW - 5, size=5, ticker="NSE:RELIANCE")
    assert intake.handle(token, body, TV, desk, store).verdict == "sent"
    assert desk.position("RELIANCE") == 50
    # a share whose name ends in CE is not read as an option
    assert store.alerts(strategy.id)[0]["sentence"] == "SCL: order sent to be long 50 RELIANCE."
    over = strategy_alert("long", NOW - 4, size=11, ticker="NSE:RELIANCE", order_id="b")
    assert intake.handle(token, over, TV, desk, store).verdict == "not_sized"
    assert desk.position("RELIANCE") == 50
