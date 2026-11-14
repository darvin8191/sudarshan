"""Option strategies and the two-step (A8). ORDER PATH, behind the automation flag.

An option strategy only buys: a long target buys a call, a short target buys a put, a flat target
sells what is held. The other side is always another contract, so it is always two orders: the
held option's exit first, and the entry only if that exit answered clearly. Everything runs on the
tests' fake desk with a frozen clock; the expiry-day tests are the builder's session S12.
"""

from __future__ import annotations

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
)
from test_automation_orders import together

from sudarshan.automation import contract, flag, intake
from sudarshan.automation import model as m

FORM = {"name": "OPT", "kind": "option", "underlying": "NIFTY", "product": "MIS", "lots": 1}
CE, PE = "NIFTY17NOV2625000CE", "NIFTY17NOV2625000PE"


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


def send(store, desk, token, position, fired=None):
    fired = desk.clock - 1 if fired is None else fired
    return intake.handle(token, indicator_alert(position, fired), TV, desk, store)


def orders(desk):
    return [(o["symbol"], o["action"], o["position_size"]) for o in desk.placed]


# ---- the form -----------------------------------------------------------------------------------


def test_an_option_strategy_is_saved_with_its_rule_and_its_strike():
    got = contract.clean({**FORM, "expiry_rule": "next", "strike_offset": "otm2"})
    assert (got["kind"], got["expiry_rule"], got["strike_offset"]) == ("option", "next", "OTM2")
    assert (got["exchange"], got["lots"], got["size_mode"]) == ("NFO", 1, "fixed")
    assert got["contract_key"] == "NFO:NIFTY:OPT"
    plain = contract.clean(FORM)
    assert (plain["expiry_rule"], plain["strike_offset"]) == ("nearest", "ATM")
    assert plain["roll_on_expiry_day"] is False
    assert contract.clean({**FORM, "product": "NRML"})["roll_on_expiry_day"] is True


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"strike_offset": "ITM11"}, "strike_offset"),
        ({"strike_offset": "DEEP"}, "strike_offset"),
        ({"expiry_rule": "next_month"}, "expiry_rule"),
        ({"product": "CNC"}, "product"),
        ({"exchange": "MCX"}, "exchange"),
        ({"underlying": ""}, "underlying"),
    ],
)
def test_what_an_option_strategy_cannot_be(change, field):
    with pytest.raises(contract.Refused) as refused:
        contract.clean({**FORM, **change})
    assert refused.value.field == field


# ---- buying only --------------------------------------------------------------------------------


def test_long_buys_the_call_short_buys_the_put_and_nothing_is_ever_sold_to_open(store, desk):
    strategy, token = make(store)
    assert send(store, desk, token, "long", NOW - 5).verdict == "sent"
    assert desk.position(CE) == 65
    got = send(store, desk, token, "short", NOW - 4)
    assert (
        got.verdict == "sent" and got.sentence == "OPT: order sent to buy 65 NIFTY 17 Nov 25000 PE."
    )
    assert (desk.position(CE), desk.position(PE)) == (0, 65)
    assert send(store, desk, token, "short", NOW - 3).verdict == "no_action"  # already holds it
    assert send(store, desk, token, "flat", NOW - 2).verdict == "sent"
    assert orders(desk) == [
        (CE, "BUY", "65"),
        (CE, "SELL", "0"), (PE, "BUY", "65"),
        (PE, "BUY", "65"),
        (PE, "SELL", "0"),
    ]  # fmt: skip
    for _symbol, action, size in orders(desk):
        assert (action, size) in (("BUY", "65"), ("SELL", "0"))  # D11: never a sale that opens
    state = store.state(strategy.id)
    assert (state.last_target, state.held_symbol, state.held_side) == (0, None, None)


def test_what_is_held_is_long_whichever_way_the_strategy_points(store, desk):
    strategy, token = make(store)
    send(store, desk, token, "short")
    state = store.state(strategy.id)
    assert (state.last_target, state.held_symbol, state.held_side) == (-1, PE, "long")


def test_flat_goes_to_the_option_that_was_bought_not_to_todays_strike(store, desk):
    """Mutation 7: the price moved 300 points and the day turned; the exit names what is held."""
    strategy, token = make(store, product="NRML", expiry_rule="next")
    send(store, desk, token, "long")
    held = "NIFTY24NOV2625000CE"
    assert desk.position(held, product="NRML") == 65
    desk.price["NIFTY"], desk.clock = 24700.0, at("2026-11-18 11:00:00")
    reads = desk.price_reads
    got = send(store, desk, token, "flat")
    assert got.verdict == "sent" and orders(desk)[-1] == (held, "SELL", "0")
    assert desk.price_reads == reads  # an exit needs no price and waits for none


def test_the_same_target_again_never_moves_to_a_new_strike_or_expiry(store, desk):
    strategy, token = make(store, product="NRML", expiry_rule="next")
    send(store, desk, token, "long")
    desk.price["NIFTY"], desk.clock = 25190.0, at("2026-11-18 11:00:00")
    got = send(store, desk, token, "long")
    assert got.verdict == "no_action" and len(desk.placed) == 2
    assert desk.placed[-1]["symbol"] == "NIFTY24NOV2625000CE"
    assert got.sentence == "OPT: already holds 65 NIFTY 24 Nov 25000 CE. Nothing to send."


def test_flat_with_nothing_held_sends_nothing_and_reads_no_price(store, desk):
    strategy, token = make(store)
    got = send(store, desk, token, "flat")
    assert got.verdict == "no_action" and desk.placed == [] and desk.price_reads == 0
    assert got.sentence == "OPT holds no option. Nothing to send."
    assert desk.told == []
    state = store.state(strategy.id)
    assert (state.last_target, state.last_fired_s) == (0, int(NOW - 1))


def test_no_price_no_strike_and_no_order_but_the_exit_still_goes(store, desk):
    strategy, token = make(store)
    send(store, desk, token, "long", NOW - 5)
    desk.price["NIFTY"] = None
    got = send(store, desk, token, "short", NOW - 4)
    assert got.verdict == "not_mapped" and len(desk.placed) == 1
    assert got.sentence == (
        "OPT: the last price of NIFTY could not be read, so no strike was chosen. Nothing was sent."
    )
    assert store.state(strategy.id).held_symbol == CE  # still held, still managed
    assert send(store, desk, token, "flat", NOW - 3).verdict == "sent" and desk.position(CE) == 0


# ---- the two-step -------------------------------------------------------------------------------


@pytest.mark.parametrize("answer", [REFUSED_BUSY, REFUSED_UNSIZED, REFUSED_GATE, NOT_CONFIRMED])
def test_the_put_is_never_bought_when_the_calls_exit_did_not_answer_clearly(store, desk, answer):
    """Mutation 6: 409, 424, 403 and not confirmed each leave the entry unsent."""
    strategy, token = make(store)
    send(store, desk, token, "long", NOW - 5)
    desk.script = [answer]
    got = send(store, desk, token, "short", NOW - 4)
    assert [o["symbol"] for o in desk.placed] == [CE, CE] and desk.position(PE) == 0
    confirmed = answer is not NOT_CONFIRMED
    assert got.verdict == ("order_refused" if confirmed else "not_confirmed")
    how = "was not sent" if confirmed else "is not confirmed"
    assert got.sentence == (
        f"OPT: the exit of NIFTY 17 Nov 25000 CE {how}, so NIFTY 17 Nov 25000 PE was not "
        "entered. Check Orders."
    )
    assert [t[1] for t in desk.told] == ["two_step"]
    state = store.state(strategy.id)
    assert state.held_symbol == CE  # the call is still there, and still the strategy's
    assert state.last_target == (1 if confirmed else 0)  # an exit may have gone: not long now


def test_an_exit_that_found_nothing_to_close_lets_the_entry_go(store, desk):
    strategy, token = make(store)
    send(store, desk, token, "long", NOW - 5)
    desk.positions.clear()  # the owner closed the call himself
    got = send(store, desk, token, "short", NOW - 4)
    assert got.verdict == "sent" and orders(desk)[1:] == [(CE, "SELL", "0"), (PE, "BUY", "65")]
    assert store.state(strategy.id).held_symbol == PE


def test_an_entry_the_gate_refused_after_the_exit_went_leaves_the_strategy_flat(store, desk):
    strategy, token = make(store)
    send(store, desk, token, "long", NOW - 5)
    desk.script = [(True, {"status": "success", "orderid": "1"}, 200), REFUSED_GATE]
    got = send(store, desk, token, "short", NOW - 4)
    assert got.verdict == "order_refused" and "The kill switch is on." in got.sentence
    state = store.state(strategy.id)
    assert (state.last_target, state.held_symbol) == (0, None)


@pytest.mark.parametrize("round_", range(5))
def test_barrier_7_two_reversals_released_together_and_the_exit_refused(store, desk, round_):
    """Zero entry dispatches: the exit answers 409 to both, one after the other, and neither
    alert's put is ever sent."""
    strategy, token = make(store)
    send(store, desk, token, "long", NOW - 9)
    desk.script, desk.delay = [REFUSED_BUSY, REFUSED_BUSY], 0.05
    got = together(
        lambda: intake.handle(token, indicator_alert("short", NOW - 2), TV, desk, store),
        lambda: intake.handle(token, indicator_alert("short", NOW - 1), TV, desk, store),
    )
    # the one that fired first may arrive second and read as out of order: still nothing sent
    assert {g.verdict for g in got} <= {"order_refused"}
    assert [o["symbol"] for o in desk.placed] == [CE, CE, CE]
    assert all(o["position_size"] == "0" for o in desk.placed[1:])
    assert (desk.position(CE), desk.position(PE)) == (65, 0)
    assert store.state(strategy.id).held_symbol == CE


# ---- expiry day: the builder's session S12, on a frozen clock -----------------------------------


def test_s12_an_expiry_day_from_the_morning_to_the_day_after(store, desk):
    strategy, token = make(store)
    desk.clock = at("2026-11-17 10:00:00")  # Tuesday, the weekly expiry
    got = send(store, desk, token, "long")
    assert got.verdict == "sent" and orders(desk) == [(CE, "BUY", "65")]
    assert got.sentence == (
        "OPT: order sent to buy 65 NIFTY 17 Nov 25000 CE. NIFTY 17 Nov 25000 CE expires today."
    )
    assert [t[1] for t in desk.told] == ["expires_today"]
    desk.clock = at("2026-11-17 11:00:00")
    got = send(store, desk, token, "short")  # the call is sold, and only then the put bought
    assert orders(desk)[1:] == [(CE, "SELL", "0"), (PE, "BUY", "65")]
    desk.clock = at("2026-11-17 14:00:00")
    assert send(store, desk, token, "flat").verdict == "sent"
    desk.clock = at("2026-11-17 15:26:00")  # the contract stops at 15:40 until 6 Dec 2026
    got = send(store, desk, token, "long")
    assert got.verdict == "not_mapped" and len(desk.placed) == 4
    assert got.sentence == (
        "OPT: NIFTY 17 Nov 25000 CE stops trading at 15:40. Too close for a new position. "
        "Nothing was sent."
    )
    desk.clock = at("2026-11-17 15:41:00")  # after the stop: next week's contract
    got = send(store, desk, token, "long")
    assert got.verdict == "sent" and orders(desk)[-1] == ("NIFTY24NOV2625000CE", "BUY", "65")
    assert "expires today" not in got.sentence


def test_expires_today_is_not_told_for_an_order_that_did_not_go(store, desk):
    strategy, token = make(store)
    desk.clock, desk.script = at("2026-11-17 10:00:00"), [REFUSED_GATE]
    assert send(store, desk, token, "long").verdict == "order_refused"
    assert desk.told == []


def test_s12_the_new_regime_stops_an_index_option_at_1530(store, desk):
    strategy, token = make(store)
    desk.clock = at("2026-12-08 15:20:00")
    got = send(store, desk, token, "long")
    assert got.verdict == "not_mapped" and desk.placed == []
    assert "NIFTY 8 Dec 25000 CE stops trading at 15:30. Too close" in got.sentence
    desk.clock = at("2026-12-08 15:31:00")
    assert send(store, desk, token, "long").verdict == "sent"
    assert orders(desk) == [("NIFTY15DEC2625000CE", "BUY", "65")]


def test_s12_an_option_held_into_its_stop_is_cleared_with_its_sentence(store, desk):
    strategy, token = make(store)
    desk.clock = at("2026-11-17 10:00:00")
    send(store, desk, token, "long")
    desk.clock = at("2026-11-18 09:20:00")
    got = send(store, desk, token, "short")
    assert got.verdict == "sent"
    assert "NIFTY 17 Nov 25000 CE expired. OPT now holds nothing." in got.sentence
    # nothing is sent for the expired call: one order, the new put
    assert orders(desk)[1:] == [("NIFTY24NOV2625000PE", "BUY", "65")]
    assert store.state(strategy.id).held_symbol == "NIFTY24NOV2625000PE"


def test_s12_a_flat_alert_after_the_held_option_expired_says_so_and_sends_nothing(store, desk):
    strategy, token = make(store)
    desk.clock = at("2026-11-17 10:00:00")
    send(store, desk, token, "long")
    desk.clock = at("2026-11-18 09:20:00")
    got = send(store, desk, token, "flat")
    assert got.verdict == "no_action" and len(desk.placed) == 1
    assert got.sentence == (
        "NIFTY 17 Nov 25000 CE expired. OPT now holds nothing. OPT holds no option. Nothing to send."
    )
    assert [t[1] for t in desk.told] == ["expires_today", "expired"]
    state = store.state(strategy.id)
    assert (state.held_symbol, state.last_target) == (None, 0)


def test_s12_a_carry_strategy_enters_the_next_expiry_from_the_morning_of_expiry_day(store, desk):
    strategy, token = make(store, product="NRML")
    desk.clock = at("2026-11-17 09:20:00")
    got = send(store, desk, token, "short")
    assert got.verdict == "sent" and orders(desk) == [("NIFTY24NOV2625000PE", "BUY", "65")]
    assert "expires today" not in got.sentence


def test_the_check_sheet_says_both_steps_and_sends_nothing(store, desk):
    strategy, token = make(store)
    send(store, desk, token, "long", NOW - 5)
    got = intake.check(strategy.id, indicator_alert("short", NOW - 1), desk, store)
    assert got["sentence"] == (
        "Would first send an exit for NIFTY 17 Nov 25000 CE, and only if that answers clearly: "
        "Would send an order to the Sandbox to buy 65 NIFTY 17 Nov 25000 PE."
    )
    assert got["order"]["action"] == "BUY" and len(desk.placed) == 1


# ---- M3: an intraday strategy's held note does not outlive its day ------------------------------

WED, THU = "2026-11-18 10:00:00", "2026-11-19 10:00:00"  # the nearest expiry is Tue 24 Nov
YESTERDAY, TODAY = "NIFTY24NOV2625000CE", "NIFTY24NOV2624800CE"


def _squared_off(desk, when=THU, price=24810.0):
    """The broker squares the intraday position off before the close; no flat alert comes."""
    desk.positions.clear()
    desk.clock = at(when)
    desk.price["NIFTY"] = price


def test_m3_the_day_after_a_square_off_the_entry_reads_the_price_and_picks_todays_strike(
    store, desk
):
    desk.clock = at(WED)
    strategy, token = make(store)
    assert send(store, desk, token, "long").verdict == "sent"
    assert orders(desk) == [(YESTERDAY, "BUY", "65")] and desk.price_reads == 1
    _squared_off(desk)
    got = send(store, desk, token, "long")
    assert got.verdict == "sent" and desk.price_reads == 2
    assert orders(desk)[1:] == [(TODAY, "BUY", "65")]  # one order: nothing is sent for the old one
    assert (
        "NIFTY 24 Nov 25000 CE was an intraday position of an earlier day, and intraday "
        "positions are closed before the session ends. OPT now holds nothing."
    ) in got.sentence
    state = store.state(strategy.id)
    assert (state.held_symbol, state.last_target) == (TODAY, 1)
    assert state.held_since_ms == int(at(THU) * 1000)
    assert ("day_ended" in [t[1] for t in desk.told]) and "expired" not in [t[1] for t in desk.told]


def test_m3_the_other_side_the_next_day_is_one_order_not_a_two_step(store, desk):
    desk.clock = at(WED)
    strategy, token = make(store)
    send(store, desk, token, "long")
    _squared_off(desk)
    assert send(store, desk, token, "short").verdict == "sent"
    assert orders(desk)[1:] == [("NIFTY24NOV2624800PE", "BUY", "65")]


def test_m3_a_flat_alert_the_next_day_says_so_and_sends_nothing(store, desk):
    desk.clock = at(WED)
    strategy, token = make(store)
    send(store, desk, token, "long")
    _squared_off(desk)
    got = send(store, desk, token, "flat")
    assert got.verdict == "no_action" and len(desk.placed) == 1
    assert got.sentence.endswith("OPT now holds nothing. If it is still open, close it in "
                                 "Positions. OPT holds no option. Nothing to send.")  # fmt: skip
    state = store.state(strategy.id)
    assert (state.held_symbol, state.held_side, state.last_target) == (None, None, 0)


def test_m3_the_same_day_keeps_the_note_whatever_the_hour(store, desk):
    """Until the date changes the note stays: a flat alert at 15:29 still exits the held one."""
    desk.clock = at(WED)
    strategy, token = make(store)
    send(store, desk, token, "long")
    desk.clock = at("2026-11-18 23:59:58")
    assert send(store, desk, token, "flat").verdict == "sent"
    assert orders(desk) == [(YESTERDAY, "BUY", "65"), (YESTERDAY, "SELL", "0")]


def test_m3_a_carry_strategy_keeps_its_note_overnight(store, desk):
    desk.clock = at(WED)
    strategy, token = make(store, product="NRML")
    send(store, desk, token, "long")
    held = desk.placed[0]["symbol"]
    desk.clock = at(THU)
    desk.price["NIFTY"] = 24810.0
    reads = desk.price_reads
    got = send(store, desk, token, "long")
    assert got.verdict == "no_action" and desk.price_reads == reads  # the held contract is asked
    assert desk.placed[-1]["symbol"] == held and "intraday" not in got.sentence
    assert store.state(strategy.id).held_symbol == held
    assert send(store, desk, token, "flat").verdict == "sent"
    assert desk.placed[-1]["symbol"] == held


def test_m3_an_intraday_future_strategys_note_is_cleared_too(store, desk):
    """Every MIS kind: with the note kept, the next day's flat alert would send an exit for a
    position the strategy no longer has (and close one the owner opened by hand)."""
    desk.clock = at(WED)
    strategy, token = store.create(
        contract.clean({"name": "ORB", "kind": "future", "underlying": "NIFTY", "product": "MIS",
                        "lots": 1})
    )  # fmt: skip
    store.set_state(strategy.id, strategy.version, m.ACTIVE)
    assert send(store, desk, token, "short").verdict == "sent"
    _squared_off(desk)
    desk.positions[(True, "NFO", "NIFTY24NOV26FUT", "MIS")] = 65  # the owner's own, by hand
    got = send(store, desk, token, "flat")
    assert got.verdict == "no_action" and len(desk.placed) == 1
    assert "NIFTY 24 Nov FUT was an intraday position of an earlier day" in got.sentence
    assert desk.position("NIFTY24NOV26FUT") == 65


@pytest.mark.parametrize(
    ("product", "since", "today", "ended"),
    [
        ("MIS", "2026-11-18 15:29:59", "2026-11-18", False),
        ("MIS", "2026-11-18 23:59:59", "2026-11-19", True),
        ("MIS", "2026-11-18 09:15:00", "2026-11-19", True),
        ("MIS", "2026-11-19 00:00:00", "2026-11-19", False),
        ("NRML", "2026-11-18 09:15:00", "2026-11-25", False),
        ("CNC", "2026-11-18 09:15:00", "2026-11-25", False),
        ("MIS", None, "2026-11-19", False),
    ],
)
def test_m3_day_ended_vectors(product, since, today, ended):
    from sudarshan.automation import symbols

    since_ms = int(at(since) * 1000) if since else None
    assert symbols.day_ended(product, since_ms, today) is ended
