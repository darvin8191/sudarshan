"""Alerts place smart orders (A5). ORDER PATH.

Everything runs on the tests' fake desk (automation_world.py): its smart order service keeps
positions by target as the real one does, per pipe, so "which pipe" and "how many orders" are
facts a test can read. The barrier tests release two requests together and each fails on the code
without its claim (the mutations are listed with the commit).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from automation_world import (
    NOT_CONFIRMED,
    NOW,
    REFUSED_BUSY,
    REFUSED_GATE,
    REFUSED_UNSIZED,
    TV,
    FakeDesk,
    at,
    indicator_alert,
    strategy_alert,
)

from sudarshan.automation import contract, control, flag, intake, rules
from sudarshan.automation import model as m

FORM = {"name": "ORB", "kind": "future", "underlying": "NIFTY", "product": "MIS", "lots": 1}
NOV, DEC = "NIFTY24NOV26FUT", "NIFTY29DEC26FUT"
VECTORS = json.loads((Path(__file__).parent / "order_vectors.json").read_text())["cases"]


@pytest.fixture
def desk():
    return FakeDesk()


@pytest.fixture(autouse=True)
def _sandbox_stage(monkeypatch):
    monkeypatch.setenv(flag.ENV, "sandbox")
    intake._per_link.clear()
    intake._refused.clear()


def make(store, state=m.ACTIVE, mode=m.SANDBOX, **change):
    strategy, token = store.create(contract.clean({**FORM, **change}))
    if mode != m.SANDBOX:
        strategy = store.set_mode(strategy.id, strategy.version, mode)
    if state != m.STOPPED:
        strategy = store.set_state(strategy.id, strategy.version, state)
    return strategy, token


def send(store, desk, token, body):
    return intake.handle(token, body, TV, desk, store)


def lines(store, strategy_id):
    return list(reversed(store.alerts(strategy_id)))


def sent_orders(desk):
    return [(o["symbol"], o["action"], o["position_size"], o["analyze"]) for o in desk.placed]


# ---- alert to order: the vectors ----------------------------------------------------------------


@pytest.mark.parametrize("case", VECTORS, ids=[c["name"] for c in VECTORS])
def test_order_vector(case, store):
    desk = FakeDesk(at(case["now"]))
    desk.listed |= {("NFO", "NIFTY24NOV26FUT"), ("NFO", "NIFTY29DEC26FUT")}
    strategy, _ = store.create(contract.clean(case["form"]))
    strategy = store.set_state(strategy.id, strategy.version, m.ACTIVE)
    state = m.State(strategy.id, **case["state"])
    data = dict(case["alert"])
    data["fired"] = int((desk.clock + data.pop("fired_offset_s")) * 1000)
    from sudarshan.automation import payload

    made = intake.plan(strategy, state, payload.parse(json.dumps(data)), desk, stage="sandbox")
    assert made.verdict is None, made.sentence
    assert [step.order for step in made.steps] == case["orders"]
    assert made.analyze is case["analyze"]


def test_the_order_never_carries_the_key_or_anything_of_the_alerts(store, desk):
    strategy, _ = make(store)
    got = intake.check(strategy.id, strategy_alert("long", NOW - 1, contracts="999"), desk, store)
    assert set(got["order"]) == {
        "strategy", "exchange", "symbol", "product", "action", "pricetype", "quantity",
        "position_size", "price", "trigger_price", "disclosed_quantity",
    }  # fmt: skip
    assert "999" not in json.dumps(
        got["order"]
    )  # mutation 8: TradingView's contracts are not the size


# ---- the session S10, steps 1 to 9, on the fake desk --------------------------------------------


def test_long_then_the_same_alert_again_then_flat_and_flat(store, desk):
    strategy, token = make(store)
    long_ = strategy_alert("long", NOW - 1, before="flat")
    assert send(store, desk, token, long_).verdict == "sent"
    assert desk.position(NOV) == 65  # 1: one sandbox order, long 65
    assert send(store, desk, token, long_).verdict == "repeat"  # 2: already handled
    assert len(desk.placed) == 1
    flat = strategy_alert("flat", NOW, before="long", order_id="X")
    assert send(store, desk, token, flat).verdict == "sent" and desk.position(NOV) == 0  # 3
    again = send(store, desk, token, strategy_alert("flat", NOW + 1, before="flat", order_id="X"))
    assert again.verdict == "no_action"  # flat again, a new fire time: nothing to close
    assert again.sentence == "ORB holds no position of its own. Nothing to send."
    assert [o["position_size"] for o in desk.placed] == ["65", "0"]  # M4: nothing held, no order
    assert all(o["analyze"] is True for o in desk.placed)
    said = [r["sentence"] for r in lines(store, strategy.id)]
    assert said[0] == "ORB: order sent to be long 65 NIFTY 24 Nov FUT."
    assert said[1] == "Repeat of the alert fired at 10:15:01. Nothing was sent."
    assert said[2] == "ORB: exit sent for NIFTY 24 Nov FUT."
    state = store.state(strategy.id)
    assert (state.last_target, state.held_symbol, state.alerts_today) == (0, None, 3)


def test_long_then_short_a_second_later_is_one_reversal(store, desk):
    strategy, token = make(store)
    send(store, desk, token, strategy_alert("long", NOW - 2, before="flat"))
    got = send(store, desk, token, strategy_alert("short", NOW - 1, before="long", order_id="S"))
    assert got.verdict == "sent" and desk.position(NOV) == -65  # 4: short 65, no more
    assert [o["position_size"] for o in desk.placed] == ["65", "-65"]
    state = store.state(strategy.id)
    assert (state.last_target, state.held_side, state.held_symbol) == (-1, "short", NOV)


def test_two_alerts_of_one_second_arriving_swapped_end_where_the_chain_ends(store, desk):
    """5: flat-after-long arrives first; it waits for the long, and both apply in chain order."""
    strategy, token = make(store)
    first = strategy_alert("long", NOW - 1, before="flat", order_id="L")
    second = strategy_alert("flat", NOW - 1, before="long", order_id="X")
    results = {}

    def late_sibling():
        time.sleep(0.4)
        results["long"] = send(store, desk, token, first)

    sibling = threading.Thread(target=late_sibling)
    sibling.start()
    started = time.monotonic()
    results["flat"] = send(store, desk, token, second)
    waited = time.monotonic() - started
    sibling.join(10)
    assert 0.3 < waited < rules.CHAIN_WAIT_S + 0.5 and desk.slept >= 0.3  # the log shows the wait
    assert [o["position_size"] for o in desk.placed] == ["65", "0"]
    assert desk.position(NOV) == 0 and results["flat"].verdict == "sent"
    assert "an earlier alert did not arrive" not in results["flat"].sentence


def test_a_gap_is_applied_after_the_wait_and_said(store, desk):
    strategy, token = make(store)
    got = send(store, desk, token, strategy_alert("flat", NOW - 1, before="long", order_id="X"))
    assert desk.slept == pytest.approx(rules.CHAIN_WAIT_S, abs=0.11)
    assert got.verdict == "no_action"
    assert got.sentence == (
        "ORB: an earlier alert did not arrive. This one was applied: now flat. "
        "ORB holds no position of its own. Nothing to send."
    )
    assert desk.placed == []
    assert [t[1] for t in desk.told] == ["gap"]


def test_a_late_entry_is_not_sent_and_a_late_exit_goes(store, desk):
    """6."""
    strategy, token = make(store)
    send(store, desk, token, strategy_alert("long", NOW - 400))
    assert desk.placed == [] and [t[1] for t in desk.told] == ["late"]
    assert "arrived 6 min 40 s late and was not sent" in desk.told[0][2]
    assert store.state(strategy.id).last_fired_s is None  # nothing went: the state stays
    send(store, desk, token, indicator_alert("long", NOW - 1))
    assert desk.position(NOV) == 65
    desk.clock += 200
    late_exit = send(store, desk, token, indicator_alert("flat", desk.clock - 180))
    assert late_exit.verdict == "sent" and desk.position(NOV) == 0
    assert late_exit.sentence.endswith("(alert was 3 min late)")


def test_an_alert_fired_before_the_last_one_applied_is_not_sent(store, desk):
    """7: an exit included: an old exit must not close a newer entry."""
    strategy, token = make(store)
    send(store, desk, token, indicator_alert("long", NOW - 1))
    old = send(store, desk, token, indicator_alert("flat", NOW - 5))
    assert old.verdict == "out_of_order" and desk.position(NOV) == 65
    assert (
        old.sentence
        == "ORB: an alert fired at 10:14:57 arrived after a newer one and was not applied."
    )
    assert len(desk.placed) == 1


OPTION_FORM = {"kind": "option", "expiry_rule": "nearest", "strike_offset": "ATM"}


@pytest.mark.parametrize("change", [{}, OPTION_FORM], ids=["future", "option"])
@pytest.mark.parametrize("held", [False, True], ids=["flat", "holding"])
def test_h2_one_alert_dated_ahead_does_not_stop_the_later_ones(store, desk, change, held):
    """A flat alert dated a month ahead (a wrong time in the alert message, or anyone holding the
    link) is applied as a late exit. The alerts after it are still applied, an exit included."""
    strategy, token = make(store, **change)
    if held:
        assert send(store, desk, token, indicator_alert("long", NOW - 2)).verdict == "sent"
    ahead = send(store, desk, token, indicator_alert("flat", NOW + 30 * 86_400))
    assert ahead.verdict == ("sent" if held else "no_action")
    state = store.state(strategy.id)
    assert state.last_fired_s == int(NOW) and state.held_symbol is None  # clamped to the clock
    said = lines(store, strategy.id)[-1]["sentence"]
    assert "ahead of this server's clock" in said and " late" not in said
    desk.clock = NOW + 60
    assert send(store, desk, token, indicator_alert("long", desk.clock - 1)).verdict == "sent"
    desk.clock = NOW + 3600
    assert send(store, desk, token, indicator_alert("flat", desk.clock - 1)).verdict == "sent"
    assert not any(desk.positions.values())
    assert "out_of_order" not in [line["verdict"] for line in lines(store, strategy.id)]


def test_h2_an_entry_dated_ahead_is_refused_and_leaves_no_mark(store, desk):
    strategy, token = make(store)
    got = send(store, desk, token, indicator_alert("long", NOW + 30 * 86_400))
    assert got.verdict == "late" and "ahead of this server's clock" in got.sentence
    assert store.state(strategy.id).last_fired_s is None and desk.placed == []
    assert send(store, desk, token, indicator_alert("long", NOW - 1)).verdict == "sent"


def test_h2_alerts_are_still_ordered_after_one_dated_ahead(store, desk):
    """The clamp keeps the rule: an alert that fired before the newest one applied is refused."""
    strategy, token = make(store)
    send(store, desk, token, indicator_alert("flat", NOW + 600))
    assert send(store, desk, token, indicator_alert("long", NOW - 1)).verdict == "out_of_order"
    assert send(store, desk, token, indicator_alert("long", NOW)).verdict == "sent"


@pytest.mark.parametrize("how", ["new link", "stop and start", "stop only"])
def test_h2_new_link_and_stop_then_start_clear_the_mark(store, desk, how):
    """A row written before the clamp, or a clock that was wrong for a while: the owner's New
    link, or Stop and Start, begins the order of alerts again. A stop alone keeps it, so an old
    exit still cannot close a newer entry while the strategy is left to its exits."""
    strategy, token = make(store)
    assert send(store, desk, token, indicator_alert("long", NOW - 1)).verdict == "sent"
    store.write_state(strategy.id, {"last_fired_s": int(NOW) + 3})  # inside the slack: it counts
    assert send(store, desk, token, indicator_alert("flat", NOW)).verdict == "out_of_order"
    current = store.strategy(strategy.id)
    if how == "new link":
        _changed, token = control.new_link(
            current.id, current.version, "the app", desk=desk, store=store
        )
    else:
        current = control.switch(
            current.id, current.version, m.EXITS_ONLY, "the app", desk=desk, store=store
        )
        if how == "stop and start":
            control.switch(current.id, current.version, m.ACTIVE, "the app", desk=desk, store=store)
    kept = how == "stop only"
    assert store.state(strategy.id).last_fired_s == (int(NOW) + 3 if kept else None)
    got = send(store, desk, token, indicator_alert("flat", NOW + 1))
    assert got.verdict == ("out_of_order" if kept else "sent")
    assert store.state(strategy.id).held_symbol == (NOV if kept else None)


def test_stopped_for_new_entries_a_short_sends_nothing_and_a_flat_closes(store, desk):
    """8."""
    strategy, token = make(store)
    send(store, desk, token, indicator_alert("long", NOW - 3))
    control.switch(strategy.id, None, m.EXITS_ONLY, "the app", desk=desk, store=store)
    refused = send(store, desk, token, indicator_alert("short", NOW - 2))
    assert refused.verdict == "stopped" and desk.position(NOV) == 65
    assert "stopped for new entries" in refused.sentence
    assert send(store, desk, token, indicator_alert("flat", NOW - 1)).verdict == "sent"
    assert desk.position(NOV) == 0 and len(desk.placed) == 2


def test_stop_everything_a_flat_sends_nothing_and_the_owner_is_told_once(store, desk):
    """9."""
    strategy, token = make(store)
    send(store, desk, token, indicator_alert("long", NOW - 3))
    control.switch(strategy.id, None, m.STOPPED, "the app", desk=desk, store=store)
    desk.told.clear()
    for n in range(2):
        assert send(store, desk, token, indicator_alert("flat", NOW - 2 + n)).verdict == "stopped"
    assert desk.position(NOV) == 65 and len(desk.placed) == 1
    assert [t[1] for t in desk.told] == ["stopped"]


def test_the_kill_switchs_refusal_is_kept_in_its_words_and_nothing_is_retried(store, desk):
    """11: the gate refuses below; the intake keeps the sentence and moves no state."""
    strategy, token = make(store)
    desk.script = [REFUSED_GATE]
    got = send(store, desk, token, indicator_alert("long", NOW - 1))
    assert got.verdict == "order_refused" and len(desk.placed) == 1
    assert got.sentence == "ORB: the order was not sent. The kill switch is on."
    assert store.state(strategy.id) == m.State(
        strategy.id, day="2026-11-16", alerts_today=1, version=2
    )
    assert desk.told == []  # the gate and the order alerts tell this one themselves
    # and the same alert again is a repeat, not a second try
    assert send(store, desk, token, indicator_alert("long", NOW - 1)).verdict == "repeat"
    assert len(desk.placed) == 1


@pytest.mark.parametrize("key", ["risk_note", "message"])  # the sandbox's answer, and the Live one
def test_a_reversal_the_risk_gate_cut_to_its_exit_leaves_the_strategy_flat(store, desk, key):
    """Found in the builder session S10, step 11: with the kill switch on, a short alert while
    long sends only the exit. The order went, but the position is flat, not short."""
    strategy, token = make(store)
    send(store, desk, token, indicator_alert("long", NOW - 2))
    note = "Only the exit was sent: sell 65 to close NIFTY FUT. The other 65 were refused."
    desk.script = [(True, {"status": "success", "orderid": "77", key: note}, 200)]
    got = send(store, desk, token, indicator_alert("short", NOW - 1))
    assert got.verdict == "sent"
    assert got.sentence == f"ORB: {note}"
    state = store.state(strategy.id)
    assert (state.last_target, state.held_symbol, state.held_side) == (0, None, None)
    assert desk.told == []  # the gate tells the owner of the part it refused


# ---- what each answer leaves behind -------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "verdict", "says", "moved"),
    [
        (REFUSED_BUSY, "order_refused", "ORB: the order was not sent. Another exit for it is on its way.", False),
        (REFUSED_UNSIZED, "order_refused", "ORB: the order was not sent. The quantity held is unknown.", False),
        (NOT_CONFIRMED, "not_confirmed", "ORB: the order is not confirmed. Check Orders. This order is not confirmed.", True),
        (RuntimeError("boom {x}"), "not_confirmed", "ORB: the order is not confirmed. Check Orders.", True),
        ((True, {"status": "success"}, 200), "not_confirmed", "ORB: the order is not confirmed. Check Orders.", True),
    ],
)  # fmt: skip
def test_an_answer_that_is_not_a_clear_yes(store, desk, answer, verdict, says, moved):
    strategy, token = make(store)
    desk.script = [answer]
    got = send(store, desk, token, indicator_alert("long", NOW - 1))
    assert (got.verdict, got.sentence) == (verdict, says)
    assert "boom" not in got.sentence and "{" not in got.sentence
    state = store.state(strategy.id)
    # an entry that may have gone is held; one that was refused is not (mutation 12)
    assert (state.held_symbol == NOV) is moved and (state.last_fired_s is not None) is moved
    assert len(desk.placed) == 1  # nothing is retried


def test_an_exit_that_is_not_confirmed_keeps_the_held_contract(store, desk):
    strategy, token = make(store)
    send(store, desk, token, indicator_alert("long", NOW - 2))
    desk.script = [NOT_CONFIRMED]
    assert send(store, desk, token, indicator_alert("flat", NOW - 1)).verdict == "not_confirmed"
    state = store.state(strategy.id)
    assert (state.held_symbol, state.held_side, state.last_target) == (NOV, "long", 0)


def test_the_alerts_row_is_written_before_the_order_is_sent(store, desk):
    """Mutation 2. At the moment of dispatch the claim is already in the store, as ``claimed``."""
    strategy, token = make(store)
    seen = []
    desk.before_place = lambda order: seen.append(
        [(r["verdict"], r["order_id"]) for r in store.alerts(strategy.id)]
    )
    send(store, desk, token, indicator_alert("long", NOW - 1))
    assert seen == [[("claimed", None)]]
    assert [(r["verdict"], bool(r["order_id"])) for r in store.alerts(strategy.id)] == [
        ("sent", True)
    ]


def test_an_alert_whose_handling_died_stays_claimed_and_is_never_sent_twice(store, desk):
    strategy, token = make(store)

    def dies(order):
        raise SystemExit("the worker was stopped")  # not an Exception: nothing catches it

    desk.before_place = dies
    body = indicator_alert("long", NOW - 1)
    with pytest.raises(SystemExit):
        send(store, desk, token, body)
    desk.before_place = None
    assert [r["verdict"] for r in lines(store, strategy.id)] == ["claimed"]
    assert send(store, desk, token, body).verdict == "repeat"  # TradingView sends it again
    # M2: the next alert, holding the lock, settles the row from the books: nothing is held
    rows = lines(store, strategy.id)
    assert desk.placed == [] and [r["verdict"] for r in rows] == ["not_confirmed", "repeat"]
    assert "Positions was read: ORB holds nothing." in rows[0]["sentence"]
    assert len(intake.LOCKS) == 0  # the lock was let go on the way out


def test_the_two_delay_figures_are_on_the_line(store, desk):
    strategy, token = make(store)
    desk.delay = 0.0
    send(store, desk, token, indicator_alert("long", NOW - 4))
    row = store.alert(1)
    assert row["tv_delay_ms"] == 4000 and row["answer_ms"] == 0 and row["mode"] == "sandbox"
    assert (row["exchange"], row["symbol"], row["action"], row["quantity"], row["target"]) == (
        "NFO", NOV, "BUY", 65, 1,
    )  # fmt: skip


# ---- the pipe is the strategy's -----------------------------------------------------------------


def test_stage_sandbox_sends_to_the_sandbox_whatever_the_apps_mode(store, desk):
    """Mutation 9: the pipe is never read from the app's toggle."""
    strategy, token = make(store)
    for n, app_in_analyze in enumerate((False, True)):
        desk.analyze_app = app_in_analyze
        send(store, desk, token, indicator_alert("long" if n == 0 else "flat", NOW - 2 + n))
    assert [o["analyze"] for o in desk.placed] == [True, True]


def test_a_live_strategy_at_stage_on(store, desk, monkeypatch):
    monkeypatch.setenv(flag.ENV, "on")
    desk.guard = "on"
    strategy, token = make(store, mode=m.LIVE)
    assert send(store, desk, token, indicator_alert("long", NOW - 5)).verdict == "sent"
    assert desk.placed[-1]["analyze"] is False and desk.position(NOV, analyze=False) == 65
    # the app goes to Analyze mode: an entry is refused, its exit still goes to Upstox
    desk.analyze_app = True
    refused = send(store, desk, token, indicator_alert("short", NOW - 4))
    assert refused.verdict == "mode_refused" and len(desk.placed) == 1
    assert refused.sentence == (
        "The app is in Analyze mode, so ORB's sell was not sent. Its exit alerts still go to Upstox."
    )
    closed = send(store, desk, token, indicator_alert("flat", NOW - 3))
    assert closed.verdict == "sent" and desk.placed[-1]["analyze"] is False
    assert desk.position(NOV, analyze=False) == 0 and desk.position(NOV, analyze=True) == 0


M4_KINDS = [
    ({}, NOV),
    (
        {"kind": "same", "underlying": "", "chart": "NSE:RELIANCE", "quantity": 10, "lots": None},
        "RELIANCE",
    ),
    ({"kind": "option", "expiry_rule": "nearest", "strike_offset": "ATM"}, "NIFTY17NOV2625000CE"),
]


@pytest.mark.parametrize(("change", "symbol"), M4_KINDS, ids=["future", "same", "option"])
@pytest.mark.parametrize("app_in_analyze", [False, True], ids=["app Live", "app in Analyze"])
def test_m4_a_live_flat_alert_with_nothing_held_sends_nothing(
    store, desk, monkeypatch, change, symbol, app_in_analyze
):
    """Mutation 9, extended. A strategy only ever closes what it opened: the owner's own position
    in the contract its rule means today is not the strategy's to close."""
    monkeypatch.setenv(flag.ENV, "on")
    desk.guard, desk.analyze_app = "on", app_in_analyze
    strategy, token = make(store, mode=m.LIVE, **change)
    exchange = "NSE" if symbol == "RELIANCE" else "NFO"
    by_hand = (False, exchange, symbol, strategy.product)
    desk.positions[by_hand] = 130  # opened by hand at Upstox, same contract and product
    ticker = {"ticker": "NSE:RELIANCE"} if symbol == "RELIANCE" else {}
    got = send(store, desk, token, indicator_alert("flat", NOW - 1, **ticker))
    assert got.verdict == "no_action" and desk.placed == []
    assert "Nothing to send." in got.sentence and "holds no" in got.sentence
    assert desk.positions[by_hand] == 130
    state = store.state(strategy.id)
    assert (state.last_target, state.held_symbol, state.last_fired_s) == (0, None, int(NOW) - 1)


def test_m4_a_live_strategy_that_holds_sends_its_exit_to_upstox(store, desk, monkeypatch):
    monkeypatch.setenv(flag.ENV, "on")
    desk.guard = "on"
    strategy, token = make(store, mode=m.LIVE)
    assert send(store, desk, token, indicator_alert("long", NOW - 2)).verdict == "sent"
    desk.analyze_app = True  # the exit goes where the entry went, whatever the app's mode
    assert send(store, desk, token, indicator_alert("flat", NOW - 1)).verdict == "sent"
    assert [(o["position_size"], o["analyze"]) for o in desk.placed] == [
        ("65", False),
        ("0", False),
    ]
    assert desk.position(NOV, analyze=False) == 0
    # and flat again: nothing of its own is left, so nothing is sent
    assert send(store, desk, token, indicator_alert("flat", NOW)).verdict == "no_action"
    assert len(desk.placed) == 2


def test_m4_in_the_sandbox_too_a_flat_alert_with_nothing_held_sends_nothing(store, desk):
    strategy, token = make(store)
    desk.positions[(True, "NFO", NOV, "MIS")] = 65  # the owner's own sandbox position
    got = send(store, desk, token, indicator_alert("flat", NOW - 1))
    assert got.verdict == "no_action" and desk.placed == []
    assert got.sentence == "ORB holds no position of its own. Nothing to send."
    assert desk.position(NOV) == 65


def test_live_waits_for_the_risk_guard(store, desk, monkeypatch):
    """Mutation 10."""
    monkeypatch.setenv(flag.ENV, "on")
    strategy, token = make(store, mode=m.LIVE)
    for guard in ("off", "sandbox"):
        desk.guard = guard
        got = send(store, desk, token, indicator_alert("long", NOW - 5 + len(guard)))
        assert got.verdict == "mode_refused"
        assert "Live automation waits for the risk guard to cover Live orders." in got.sentence
    assert desk.placed == []


def test_a_live_strategy_at_stage_sandbox_sends_nothing_anywhere(store, desk):
    """The flag stepped back with a Live strategy holding a position: its exit must not be
    answered by the sandbox as if the real position were closed."""
    strategy, token = make(store, mode=m.LIVE)
    for position in ("long", "flat"):
        got = send(
            store, desk, token, indicator_alert(position, NOW - (2 if position == "long" else 1))
        )
        assert got.verdict == "mode_refused" and "close it yourself in Positions" in got.sentence
    assert desk.placed == []


def test_a_delayed_data_chart_is_refused_for_a_live_strategy_only(store, desk, monkeypatch):
    monkeypatch.setenv(flag.ENV, "on")
    desk.guard = "on"
    live, live_token = make(store, mode=m.LIVE)
    sandbox, sandbox_token = make(store, name="Second", underlying="BANKNIFTY")
    body = indicator_alert("long", NOW - 1, ticker="NSE_DLY:NIFTY")
    got = send(store, desk, live_token, body)
    assert got.verdict == "mode_refused" and "delayed prices" in got.sentence
    assert send(store, desk, sandbox_token, body).verdict == "sent"
    assert [o["analyze"] for o in desk.placed] == [True]


def test_semi_auto_is_not_used(store, desk):
    """An order queued in the Action Center is later sent wherever the app's mode then points:
    a Sandbox strategy's order could reach Upstox. The intake sends nothing and says why."""
    strategy, token = make(store)
    desk.semi = True
    got = send(store, desk, token, indicator_alert("long", NOW - 1))
    assert got.verdict == "mode_refused" and "Action Center" in got.sentence and desk.placed == []


class RealSender(FakeDesk):
    """The tests' desk with the real sender (``Desk.place``): OpenAlgo's smart order service is
    called as the intake calls it. What lies behind the service is stood in by the test."""

    def place(self, order, api_key, analyze):
        from sudarshan.automation.desk import Desk

        if self.before_place:
            self.before_place(order)
        return Desk.place(self, order, api_key, analyze)


@pytest.fixture
def service(monkeypatch):
    """The smart order service's two exits: the Action Center's queue, and the order itself
    (the sandbox or the broker, as ``analyze`` says)."""
    import services.order_router_service as router
    import services.place_smart_order_service as real

    seen = {"queued": [], "placed": [], "order_mode": "auto"}

    def queue(api_key, data, kind):
        seen["queued"].append((kind, dict(data)))
        said = "Order queued for approval in Action Center"
        return True, {"status": "success", "message": said, "mode": "semi_auto"}, 200

    def with_auth(order, auth_token, broker, original, analyze=None):
        seen["placed"].append({"symbol": order["symbol"], "analyze": analyze})
        return True, {"status": "success", "orderid": f"SB-{len(seen['placed'])}"}, 200

    monkeypatch.setattr(
        router, "should_route_to_pending", lambda key, kind=None: seen["order_mode"] == "semi_auto"
    )
    monkeypatch.setattr(router, "queue_order", queue)
    monkeypatch.setattr(real, "place_smart_order_with_auth", with_auth)
    return seen


def test_h1_the_order_mode_switched_between_the_plan_and_the_send_moves_nothing(store, service):
    """The owner switches to Semi-Auto while the alert is between its claim and its order. The
    Sandbox strategy's order still goes to the sandbox: nothing is queued for the Action Center,
    where an approval would later send it wherever the app's mode then points."""
    desk = RealSender()
    strategy, token = make(store)
    held, go = threading.Event(), threading.Event()
    desk.before_place = lambda order: (held.set(), go.wait(5))
    out = []
    alert = threading.Thread(
        target=lambda: out.append(send(store, desk, token, indicator_alert("long", NOW - 1)))
    )
    alert.start()
    assert held.wait(5)  # planned and claimed, holding the lock, about to send
    assert lines(store, strategy.id)[-1]["verdict"] == "claimed"
    service["order_mode"] = "semi_auto"
    go.set()
    alert.join(10)
    assert service["queued"] == []
    assert service["placed"] == [{"symbol": NOV, "analyze": True}]
    line = lines(store, strategy.id)[-1]
    assert (out[0].verdict, line["verdict"], line["mode"]) == ("sent", "sent", "sandbox")
    assert line["order_id"] == "SB-1" and "Action Center" not in line["sentence"]


def test_h1_the_two_steps_second_order_does_not_ask_the_order_mode_either(store, service):
    """The window is the whole first order for a two-step: switched during the exit, the entry
    still goes to the sandbox."""
    desk = RealSender()
    strategy, token = make(store)
    desk.clock = at("2026-11-24 15:41:00")  # the November future has stopped: December is entered
    store.write_state(strategy.id, {
        "last_target": 1, "held_exchange": "NFO", "held_symbol": "NIFTY25JAN27FUT",
        "held_side": m.LONG,
    })  # fmt: skip
    calls = []
    desk.before_place = lambda order: (
        calls.append(order["symbol"]),
        service.__setitem__("order_mode", "semi_auto"),
    )
    got = send(store, desk, token, indicator_alert("short", desk.clock - 1))
    assert calls == ["NIFTY25JAN27FUT", DEC] and got.verdict == "sent"
    assert service["queued"] == []
    assert [p["analyze"] for p in service["placed"]] == [True, True]


def test_h1_no_broker_session_is_a_refusal_in_words_and_nothing_is_queued(store, service):
    desk = RealSender()
    desk.broker_session = lambda api_key: (None, None)
    service["order_mode"] = "semi_auto"
    desk.semi = False
    strategy, token = make(store)
    got = send(store, desk, token, indicator_alert("long", NOW - 1))
    assert got.verdict == "order_refused" and "not logged in to Upstox" in got.sentence
    assert service["queued"] == [] and service["placed"] == []
    assert store.state(strategy.id).held_symbol is None


def test_h1_the_sender_refuses_an_order_with_no_pipe():
    from sudarshan.automation.desk import Desk

    with pytest.raises(ValueError):
        Desk().place({}, "k" * 64, None)


def test_no_api_key_is_a_refusal_in_words(store, desk):
    strategy, token = make(store)
    desk.key = None
    got = send(store, desk, token, indicator_alert("long", NOW - 1))
    assert got.verdict == "order_refused" and "No API key is set up" in got.sentence


# ---- contracts ----------------------------------------------------------------------------------


def test_a_flat_target_goes_to_the_held_contract_after_the_roll(store, desk):
    """Mutation 7."""
    strategy, token = make(store, product="NRML")
    send(store, desk, token, indicator_alert("long", NOW - 1))
    assert desk.position(NOV, product="NRML") == 65
    desk.clock = at("2026-11-24 10:00:00")  # expiry day: the rule now gives December for an entry
    got = send(store, desk, token, indicator_alert("flat", desk.clock - 1))
    assert got.verdict == "sent" and desk.placed[-1]["symbol"] == NOV
    assert desk.position(NOV, product="NRML") == 0 and desk.position(DEC, product="NRML") == 0


def test_the_other_side_in_another_contract_exits_first_and_enters_only_after(store, desk):
    strategy, token = make(store, product="NRML")
    send(store, desk, token, indicator_alert("long", NOW - 1))
    desk.clock = at("2026-11-24 10:00:00")
    got = send(store, desk, token, indicator_alert("short", desk.clock - 1))
    assert got.verdict == "sent"
    assert sent_orders(desk)[1:] == [(NOV, "SELL", "0", True), (DEC, "SELL", "-65", True)]
    state = store.state(strategy.id)
    assert (state.held_symbol, state.held_side, state.last_target) == (DEC, "short", -1)


@pytest.mark.parametrize("answer", [REFUSED_BUSY, REFUSED_UNSIZED, REFUSED_GATE, NOT_CONFIRMED])
def test_the_entry_is_never_sent_when_the_exit_did_not_answer_clearly(store, desk, answer):
    """Barrier 7 and mutation 6: zero entry dispatches."""
    strategy, token = make(store, product="NRML")
    send(store, desk, token, indicator_alert("long", NOW - 1))
    desk.clock = at("2026-11-24 10:00:00")
    desk.script = [answer]
    got = send(store, desk, token, indicator_alert("short", desk.clock - 1))
    assert [o["symbol"] for o in desk.placed] == [NOV, NOV]  # the entry in December never went
    assert desk.position(DEC, product="NRML") == 0
    confirmed = answer is not NOT_CONFIRMED
    assert got.verdict == ("order_refused" if confirmed else "not_confirmed")
    how = "was not sent" if confirmed else "is not confirmed"
    assert got.sentence == (
        f"ORB: the exit of NIFTY 24 Nov FUT {how}, so NIFTY 29 Dec FUT was not entered. Check Orders."
    )
    assert [t[1] for t in desk.told] == ["two_step"]
    assert store.state(strategy.id).held_symbol == NOV  # still held, still managed


def test_a_held_contract_past_its_stop_is_cleared_with_its_sentence(store, desk):
    strategy, token = make(store)
    send(store, desk, token, strategy_alert("long", NOW - 1, before="flat"))
    desk.clock = at("2026-11-25 09:20:00")  # the morning after November's expiry
    got = send(
        store, desk, token, strategy_alert("long", desk.clock - 1, before="long", order_id="L2")
    )
    assert got.verdict == "sent" and desk.placed[-1]["symbol"] == DEC
    assert "NIFTY 24 Nov FUT expired. ORB now holds nothing." in got.sentence
    assert "an earlier alert did not arrive" not in got.sentence  # the chain rule is silent once
    assert store.state(strategy.id).held_symbol == DEC


@pytest.mark.parametrize(
    ("change", "body", "verdict", "says"),
    [
        ({"lots": 28}, "long", "not_sized", "above the most the exchange takes in one order"),
        (
            {"underlying": "FINNIFTY"},
            "long",
            "not_mapped",
            "no open FINNIFTY future is in today's contract list",
        ),
    ],
)
def test_what_cannot_be_mapped_or_sized_sends_nothing(store, desk, change, body, verdict, says):
    strategy, token = make(store, **change)
    got = send(store, desk, token, indicator_alert(body, NOW - 1))
    assert got.verdict == verdict and says in got.sentence and desk.placed == []
    assert [t[1] for t in desk.told] == [verdict]


def test_too_close_to_the_stop_and_a_master_not_loaded(store, desk):
    strategy, token = make(store)
    desk.clock = at("2026-11-24 15:30:00")
    got = send(store, desk, token, indicator_alert("long", desk.clock - 1))
    assert got.verdict == "not_mapped"
    assert got.sentence == (
        "ORB: NIFTY 24 Nov FUT stops trading at 15:40. Too close for a new position. Nothing was sent."
    )
    desk.loaded = False
    got = send(store, desk, token, indicator_alert("long", desk.clock))
    assert "today's contract list is not loaded yet" in got.sentence and desk.placed == []


def test_the_cap_stops_entries_tells_once_and_lets_the_exit_go(store, desk):
    strategy, token = make(store, max_alerts_day=3)
    for n, position in enumerate(("long", "flat", "long")):
        assert send(store, desk, token, indicator_alert(position, NOW - 9 + n)).verdict == "sent"
    capped = send(store, desk, token, indicator_alert("short", NOW - 5))
    assert capped.verdict == "capped" and store.strategy(strategy.id).state == m.EXITS_ONLY
    assert store.strategy(strategy.id).stopped_why == "too many alerts today"
    more = send(store, desk, token, indicator_alert("short", NOW - 4))
    assert more.verdict == "stopped"
    assert [t[1] for t in desk.told] == ["capped"]  # one notice, not one per alert
    exit_ = send(store, desk, token, indicator_alert("flat", NOW - 3))
    assert exit_.verdict == "sent" and desk.position(NOV) == 0


# ---- barrier-synchronised races -----------------------------------------------------------------


def together(*calls):
    """Run the calls from threads released by one barrier; return their results in order."""
    barrier, results = threading.Barrier(len(calls)), [None] * len(calls)

    def run(index, call):
        barrier.wait(timeout=10)
        try:
            results[index] = call()
        except Exception as exc:
            results[index] = exc

    threads = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive()
    return results


@pytest.mark.parametrize("round_", range(5))
def test_barrier_1_two_identical_alerts_one_order(store, desk, round_):
    strategy, token = make(store)
    desk.delay = 0.05
    body = strategy_alert("long", NOW - 1, before="flat")
    got = together(*[lambda: send(store, desk, token, body)] * 2)
    assert sorted(g.verdict for g in got) == ["repeat", "sent"]
    assert len(desk.placed) == 1 and desk.position(NOV) == 65
    assert sorted(g.body["message"] for g in got) == ["already handled", "handled"]
    assert store.state(strategy.id).alerts_today == 1


@pytest.mark.parametrize("round_", range(5))
def test_barrier_2_two_different_alerts_are_sent_one_after_the_other(store, desk, round_):
    """The second reads the state the first left; their orders never overlap."""
    strategy, token = make(store)
    inside, overlap = [], []

    def enter(order):
        inside.append(order["position_size"])
        overlap.append(len(inside))
        time.sleep(0.15)
        inside.pop()

    desk.before_place = enter
    a = indicator_alert("long", NOW - 2)
    b = indicator_alert("short", NOW - 2, comment="b")
    got = together(lambda: send(store, desk, token, a), lambda: send(store, desk, token, b))
    assert [g.verdict for g in got] == ["sent", "sent"] and overlap == [1, 1]
    last = desk.placed[-1]["position_size"]
    assert desk.position(NOV) == int(last)  # the account is where the last one applied left it
    state = store.state(strategy.id)
    assert state.last_target == (1 if last == "65" else -1) and state.alerts_today == 2


@pytest.mark.parametrize("round_", range(5))
def test_barrier_3_a_stop_and_an_alert(store, desk, round_):
    """Either the alert is applied whole and the stop follows, or the alert sees Stopped: never
    an order sent after the stop was answered."""
    strategy, token = make(store)
    events = []

    def place_marks(order):
        events.append("order starts")
        time.sleep(0.15)
        events.append("order ends")

    desk.before_place = place_marks

    def stop():
        changed = control.switch(strategy.id, None, m.STOPPED, "the app", desk=desk, store=store)
        events.append("stop answered")
        return changed

    alert_result, stopped = together(
        lambda: send(store, desk, token, indicator_alert("long", NOW - 1)), stop
    )
    assert stopped.state == m.STOPPED
    if alert_result.verdict == "sent":
        assert events == ["order starts", "order ends", "stop answered"]
    else:
        assert alert_result.verdict == "stopped" and events == ["stop answered"]
        assert desk.placed == []
    after = send(store, desk, token, indicator_alert("long", NOW))
    assert after.verdict == "stopped"


def test_the_stop_is_read_again_holding_the_lock(store, desk, monkeypatch):
    """Mutation 5, without luck: the strategy is stopped after the alert's first look and before
    it holds the lock. What it read first must not be what it acts on."""
    strategy, token = make(store)
    real_hold = intake.LOCKS.hold

    def stop_just_before(key, timeout=None):
        store.set_state(strategy.id, None, m.STOPPED, "by the test")
        return real_hold(key, timeout=timeout)

    monkeypatch.setattr(intake.LOCKS, "hold", stop_just_before)
    got = send(store, desk, token, indicator_alert("long", NOW - 1))
    assert got.verdict == "stopped" and desk.placed == []


@pytest.mark.parametrize("round_", range(5))
def test_barrier_5_a_new_link_and_an_alert_on_the_old_one(store, desk, round_):
    """The alert is applied or unknown; never applied after the new link was handed out."""
    strategy, token = make(store)
    events = []
    desk.before_place = lambda order: (events.append("order"), time.sleep(0.1))

    def relink():
        made = control.new_link(strategy.id, strategy.version, "the app", desk=desk, store=store)
        events.append("new link answered")
        return made

    alert_result, (changed, new_token) = together(
        lambda: send(store, desk, token, indicator_alert("long", NOW - 1)), relink
    )
    if alert_result.status == 200:
        assert events == ["order", "new link answered"]
    else:
        assert alert_result.status == 404 and events == ["new link answered"] and desk.placed == []
    assert (
        send(store, desk, token, indicator_alert("flat", NOW)).status == 404
    )  # the old link is dead
    assert send(store, desk, new_token, indicator_alert("flat", NOW + 1)).status == 200


@pytest.mark.parametrize("round_", range(5))
def test_barrier_6_two_alerts_at_the_caps_last_place(store, desk, round_):
    """One applied, one capped, one notice."""
    strategy, token = make(store, max_alerts_day=3)
    send(store, desk, token, indicator_alert("long", NOW - 9))
    send(store, desk, token, indicator_alert("flat", NOW - 8))
    desk.told.clear()
    desk.delay = 0.05
    got = together(
        lambda: send(store, desk, token, indicator_alert("long", NOW - 2)),
        lambda: send(store, desk, token, indicator_alert("short", NOW - 2)),
    )
    assert sorted(g.verdict for g in got) == ["capped", "sent"]
    assert len(desk.placed) == 3 and [t[1] for t in desk.told] == ["capped"]
    assert store.strategy(strategy.id).state == m.EXITS_ONLY


def test_the_lock_is_let_go_whatever_happens(store, desk):
    strategy, token = make(store)
    desk.script = [RuntimeError("x"), NOT_CONFIRMED, REFUSED_BUSY]
    for n in range(3):
        send(store, desk, token, indicator_alert("long", NOW - 3 + n))
    assert len(intake.LOCKS) == 0


def test_under_gthread_a_long_wait_for_the_lock_is_answered_busy(store, desk, monkeypatch):
    """Gated on the worker: under eventlet and the development server an alert waits."""
    strategy, token = make(store)
    monkeypatch.setattr(intake.runtime, "gthread_active", lambda: True)
    monkeypatch.setattr(intake, "LOCK_WAIT_S", 0.2)
    with intake.LOCKS.hold(strategy.id):  # another alert of this strategy is being handled
        got = send(store, desk, token, indicator_alert("long", NOW - 1))
    assert got.verdict == "order_refused" and desk.placed == []
    assert got.sentence.startswith("ORB: an earlier alert of this strategy is still being handled")
    assert [t[1] for t in desk.told] == ["busy"]
    # it was not claimed, so the same alert delivered again is handled
    assert send(store, desk, token, indicator_alert("long", NOW - 1)).verdict == "sent"
