"""The re-review of v0.5.1 (e3e74bb): the scalping Close all over a close that may have gone
(MEDIUM-A), an unlisted fill whose filled quantity is null (LOW-B) and a listed sale from holdings
with the order book unread (LOW-E), over the simulated Upstox where a test needs one (M6.9).

MEDIUM-A: a scalping close that Upstox did not answer (a timeout) may have reached it. The Close
all said "No scalping position was closed." over it, with the failure's own words ("The read
operation timed out"); and the close was noted for 15 s only, after which a re-tap was sized from
a positions row that can lag the fill by a minute and sent a second exit: a reversal. Such a close
is now said as not confirmed, and its hold is settled by the order book, the way the risk gate
keeps a market exit's claim.

Each test here fails on e3e74bb unless it says it is a control.
"""

from __future__ import annotations

import re
import time
from types import SimpleNamespace

import pytest
from books_upstox_world import Broker, install

import restx_api  # noqa: F401  (at collection: place_order_service's import cycle needs it)
from sudarshan.books import exit_guard, surface_exits

X = "NIFTY27OCT2625900CE"  # MIS, NFO, token NSE_FO|1001
Y = "BANKNIFTY27OCT2657500PE"  # NRML, NFO, token NSE_FO|1002
TOKENS = {X: "NSE_FO|1001", Y: "NSE_FO|1002"}
TIMED_OUT = (False, {"status": "error", "message": "The read operation timed out"}, 500)
PAST_150_S = exit_guard.EXIT_HOLD_UNCONFIRMED_S + 1
SIXTEEN_HOURS = 16 * 3600.0


def _for_a_trader(said):
    for word in ("None", "token", "HTTP", "timed out", "status", "order book", "row"):
        assert word not in said, said
    assert not re.search(r"\b(arm|armed|arming|4\d\d|5\d\d)\b", said), said


def _working(symbol=X, side="SELL", quantity=65, kind="MARKET", order_id="261027000091"):
    """An order still working at Upstox, as its order book lists it."""
    return {"order_id": order_id, "status": "trigger pending" if kind.startswith("SL") else "open",
            "instrument_token": TOKENS[symbol], "exchange": "NFO",
            "product": "I" if symbol == X else "D", "transaction_type": side, "order_type": kind,
            "quantity": quantity, "filled_quantity": 0, "pending_quantity": quantity,
            "trigger_price": 98.5 if kind.startswith("SL") else 0, "price": 0,
            "order_timestamp": "2026-11-09 10:00:00"}  # fmt: skip


@pytest.fixture
def clock(monkeypatch):
    """exit_guard's own clock, moved by hand (``clock.now`` is its monotonic time)."""
    state = SimpleNamespace(now=time.monotonic())
    fake = SimpleNamespace(monotonic=lambda: state.now, time=time.time, sleep=time.sleep)
    monkeypatch.setattr(exit_guard, "time", fake)
    return state


@pytest.fixture
def scalper(monkeypatch):
    """The scalping terminal's close as production calls it (the api key alone), in Live.

    ``answers`` are what place_order answers, one per close (success with an order id once they
    run out); ``book`` is Upstox's order book, were it read (None: the read fails); ``sent``
    records every order that reached place_order."""
    import blueprints.scalping as scalping
    import broker.upstox.api.order_api as upstox
    import database.auth_db as auth_db

    state = SimpleNamespace(answers=[], sent=[], book=[])

    def place_order(order_data, api_key, auth_token, broker):
        state.sent.append((order_data["symbol"], order_data["action"], int(order_data["quantity"])))
        if state.answers:
            return state.answers.pop(0)
        return True, {"status": "success", "orderid": f"2610270000{len(state.sent):02d}"}, 200

    def get_order_book(auth):
        if state.book is None:
            return {"status": "error", "message": "down"}
        return {"status": "success", "data": list(state.book)}

    monkeypatch.setattr("services.place_order_service.place_order", place_order)
    monkeypatch.setattr(scalping, "_is_derivative", lambda exchange: False)  # no symbol master
    monkeypatch.setattr(scalping, "_current_mode", lambda: "live")
    monkeypatch.setattr(
        auth_db, "get_auth_token_broker", lambda key: ("tok", "upstox") if key else (None, None)
    )
    monkeypatch.setattr(upstox, "get_token", lambda symbol, exchange: TOKENS.get(symbol))
    monkeypatch.setattr(upstox, "get_order_book", get_order_book)

    def close(symbol=X, product="MIS", action="SELL", quantity=65):
        return scalping._reducing_exit(symbol, "NFO", product, action, quantity, None, None, "key")

    state.close = close
    return state


# -- MEDIUM-A: what a close that may have gone says ------------------------------------------------


def test_a_timed_out_scalping_close_says_not_confirmed_never_the_failure_s_words(scalper):
    """The single Close (and the trailing stop's auto-exit) over a timeout: on e3e74bb the answer
    carried "The read operation timed out" as its message."""
    scalper.answers = [TIMED_OUT]
    ok, body, status = scalper.close()
    assert scalper.sent == [(X, "SELL", 65)]
    assert (ok, status) == (False, 500)
    assert "not confirmed" in body["message"] and "may have gone through" in body["message"]
    assert "Check Orders" in body["message"] and body.get("not_confirmed") is True
    _for_a_trader(body["message"])


def test_control_a_close_upstox_refused_keeps_its_own_words(scalper):
    """Control: a refusal (nothing reached Upstox) is said as before, and notes no hold."""
    scalper.answers = [(False, {"status": "error", "message": "Insufficient funds"}, 400)]
    ok, body, status = scalper.close()
    assert (ok, status) == (False, 400) and body["message"] == "Insufficient funds"
    assert "not_confirmed" not in body
    assert exit_guard.EXITS.placed_lately("live") == set()


# -- MEDIUM-A: how long a close that may have gone holds the position ------------------------------


def test_a_close_after_a_timed_out_close_is_held_past_the_15_s_note(scalper, clock):
    """Upstox timed out on the close and filled it; the positions row still shows the 65 (it can
    lag a minute). The re-tap 20 s later is sized from that row. On e3e74bb the 15 s note was gone
    and a second SELL 65 went: short 65. Now the order book settles the hold, and for 150 s from
    the answer whatever it says, because the positions may not show the fill yet."""
    scalper.answers = [TIMED_OUT]
    scalper.close()
    clock.now += exit_guard.EXIT_SHOWS_WITHIN_S + 5
    ok, body, status = scalper.close()
    assert scalper.sent == [(X, "SELL", 65)], "a second exit went beside the first"
    assert (ok, status) == (False, 409)
    assert "is not confirmed" in body["message"] and "may have gone through" in body["message"]
    assert "can take a minute to show it" in body["message"]
    _for_a_trader(body["message"])


def test_control_within_15_s_a_close_after_a_timed_out_one_is_refused_as_before(scalper, clock):
    """Control: inside the 15 s note it was refused on e3e74bb too."""
    scalper.answers = [TIMED_OUT]
    scalper.close()
    clock.now += 5
    ok, _body, status = scalper.close()
    assert (ok, status) == (False, 409) and scalper.sent == [(X, "SELL", 65)]


def test_past_150_s_a_market_order_working_on_the_closing_side_keeps_the_hold(scalper, clock):
    """The timed-out close rests at its protected price: past 150 s the order book shows a MARKET
    SELL working on the position. On e3e74bb the hold was long gone and SELL 65 went beside it.
    Once that order is seen ended, the hold stands 150 s more from the read that last showed it
    working (the positions may not show its fill yet), and then the close goes."""
    scalper.answers = [TIMED_OUT]
    scalper.close()
    clock.now += PAST_150_S
    scalper.book = [_working()]
    ok, body, status = scalper.close()
    assert scalper.sent == [(X, "SELL", 65)], "a second exit went beside a working one"
    assert (ok, status) == (False, 409)
    assert (
        "SELL 65 MARKET order" in body["message"] and "still working at Upstox" in body["message"]
    )
    # The order fills; 60 s after the read that showed it working, the positions may still lag.
    scalper.book = []
    clock.now += 60
    ok, body, status = scalper.close()
    assert (ok, status) == (False, 409) and len(scalper.sent) == 1
    assert "is not confirmed" in body["message"]
    # Past 150 s from that read, nothing working: the hold ends and the close goes.
    clock.now += exit_guard.EXIT_HOLD_UNCONFIRMED_S
    ok, _body, _status = scalper.close()
    assert ok and len(scalper.sent) == 2
    assert exit_guard.EXITS.placed_lately("live") == {("NFO", X, "MIS")}  # the new close's hold


def test_past_150_s_an_order_book_that_cannot_be_read_ends_nothing_until_16_h(scalper, clock):
    """A read that fails says nothing about the close, so it ends nothing (the risk gate's rule
    for a market exit's claim); on e3e74bb the close went once the 15 s note was gone. The hold
    never lasts past 16 h."""
    scalper.answers = [TIMED_OUT]
    scalper.close()
    scalper.book = None
    clock.now += PAST_150_S
    ok, body, status = scalper.close()
    assert (ok, status) == (False, 409) and len(scalper.sent) == 1
    assert "is not confirmed" in body["message"]
    clock.now += SIXTEEN_HOURS
    assert exit_guard.EXITS.placed_lately("live") == set()  # forgotten, nothing kept all night
    ok, _body, _status = scalper.close()
    assert ok and len(scalper.sent) == 2


def test_past_150_s_a_book_whose_working_orders_cannot_be_worked_out_keeps_the_hold(
    scalper, clock, monkeypatch
):
    """The order book was read, but looking the contract up broke: that says nothing either, and
    the hold stands (on e3e74bb the close went)."""
    import broker.upstox.api.order_api as upstox

    scalper.answers = [TIMED_OUT]
    scalper.close()

    def broken(symbol, exchange):
        raise RuntimeError("the symbol master is being reloaded")

    monkeypatch.setattr(upstox, "get_token", broken)
    clock.now += PAST_150_S
    ok, _body, status = scalper.close()
    assert (ok, status) == (False, 409) and len(scalper.sent) == 1


def test_a_hold_noted_while_the_book_was_read_is_never_overwritten(scalper, clock, monkeypatch):
    """The hold is kept counting from a read that showed the close working; a hold noted for the
    position while that read was made (another exit's, with its order) is the newer one and stays.
    On e3e74bb the close went at 150 s."""
    import broker.upstox.api.order_api as upstox

    scalper.answers = [TIMED_OUT]
    scalper.close()
    clock.now += PAST_150_S
    scalper.book = [_working()]

    def token_while_another_exit_is_noted(symbol, exchange):
        exit_guard.exit_placed("live", "NFO", X, "MIS", ("261027000099",))
        return TOKENS[symbol]

    monkeypatch.setattr(upstox, "get_token", token_while_another_exit_is_noted)
    ok, _body, status = scalper.close()
    assert (ok, status) == (False, 409) and len(scalper.sent) == 1
    hold = exit_guard.EXITS._placed["live"][("NFO", X, "MIS")]
    assert hold.order_ids == ("261027000099",) and hold.seen_working is None


def test_past_150_s_a_contract_the_order_book_cannot_be_matched_with_keeps_the_hold(
    scalper, clock, monkeypatch
):
    """The order book was read, but the contract has no token to match its rows by: it cannot
    say whether the close still works, so the hold stands (on e3e74bb the close went)."""
    import broker.upstox.api.order_api as upstox

    scalper.answers = [TIMED_OUT]
    scalper.close()
    monkeypatch.setattr(upstox, "get_token", lambda symbol, exchange: None)
    clock.now += PAST_150_S
    ok, body, status = scalper.close()
    assert (ok, status) == (False, 409) and len(scalper.sent) == 1
    assert "is not confirmed" in body["message"]


def test_past_150_s_a_reader_that_cannot_say_what_works_keeps_the_hold(scalper, clock, monkeypatch):
    """A broker reader that answers statuses alone (no word on working orders) cannot settle the
    hold either (on e3e74bb the close went)."""
    scalper.answers = [TIMED_OUT]
    scalper.close()
    monkeypatch.setattr(surface_exits, "upstox_statuses", lambda auth, broker: {})
    clock.now += PAST_150_S
    ok, _body, status = scalper.close()
    assert (ok, status) == (False, 409) and len(scalper.sent) == 1


def test_control_past_150_s_with_nothing_working_the_hold_ends_and_the_close_goes(scalper, clock):
    """Control: the book is read and shows no market order on the closing side; a resting stop
    (SL-M, the trader's own) does not hold a close, as it never holds a single exit."""
    scalper.answers = [TIMED_OUT]
    scalper.close()
    clock.now += PAST_150_S
    scalper.book = [_working(kind="SL-M", order_id="261027000092")]
    ok, _body, _status = scalper.close()
    assert ok and scalper.sent == [(X, "SELL", 65), (X, "SELL", 65)]


def test_control_a_market_order_on_the_other_side_does_not_hold_the_close(scalper, clock):
    """Control: a MARKET BUY working (it adds to the long) is not on the side the close closes on."""
    scalper.answers = [TIMED_OUT]
    scalper.close()
    clock.now += PAST_150_S
    scalper.book = [_working(side="BUY", order_id="261027000093")]
    ok, _body, _status = scalper.close()
    assert ok and len(scalper.sent) == 2


def test_control_a_close_upstox_answered_with_its_order_is_held_as_before(scalper, clock):
    """Control: a close whose answer named its order is held until the book shows that order
    ended, and no longer (the strategy and scalping sizing residual stays as decided)."""
    scalper.close()
    clock.now += exit_guard.EXIT_SHOWS_WITHIN_S + 5
    scalper.book = [{**_working(order_id="261027000001"), "status": "complete",
                     "filled_quantity": 65, "pending_quantity": 0}]  # fmt: skip
    ok, _body, _status = scalper.close()
    assert ok and len(scalper.sent) == 2


# -- MEDIUM-A: the Close all's answer ---------------------------------------------------------------


@pytest.fixture
def terminal(scalper, monkeypatch):
    """The scalping terminal's Close all route over X long 65 (MIS) and Y short 30 (NRML); the
    positions are what ``scalper.positions`` says, both by default."""
    from flask import Flask

    import blueprints.scalping as scalping
    import database.scalping_db as scalping_db
    import services.positionbook_service as positionbook_service
    import utils.session as session_utils
    from database.latency_db import init_latency_db

    init_latency_db()
    scalper.positions = [
        {"symbol": X, "exchange": "NFO", "product": "MIS", "quantity": 65},
        {"symbol": Y, "exchange": "NFO", "product": "NRML", "quantity": -30},
    ]
    monkeypatch.setattr(session_utils, "is_session_valid", lambda: True)
    monkeypatch.setattr(scalping, "get_api_key_for_tradingview", lambda user: "key")
    monkeypatch.setattr(
        scalping_db,
        "get_tracked_symbols",
        lambda mode=None: [
            {"symbol": p["symbol"], "exchange": p["exchange"], "product": p["product"]}
            for p in scalper.positions
        ],
    )
    monkeypatch.setattr(
        positionbook_service,
        "get_positionbook",
        lambda api_key, auth_token, broker: (
            True,
            {"status": "success", "data": list(scalper.positions)},
            200,
        ),
    )
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(scalping.scalping_bp)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = "owner"
        sess["broker"] = "upstox"

    def close_all():
        reply = client.post("/scalping/api/close_all", json={})
        return reply.status_code, reply.get_json()

    return close_all


def test_close_all_never_says_nothing_closed_over_a_close_that_may_have_gone(scalper, terminal):
    """The re-review's probe: one position, its close timed out. On e3e74bb: 409, "No scalping
    position was closed. NIFTY27OCT2625900CE: The read operation timed out"."""
    scalper.positions = scalper.positions[:1]
    scalper.answers = [TIMED_OUT]
    code, body = terminal()
    assert scalper.sent == [(X, "SELL", 65)]
    message = body["message"]
    assert "No scalping position was closed" not in message, (code, message)
    assert code == 502 and body["status"] == "error" and body.get("not_confirmed") is True
    assert message.startswith("No scalping close is confirmed.")
    assert "not confirmed" in body["results"][0]["message"]
    _for_a_trader(message)


def test_close_all_with_one_closed_and_one_not_confirmed_says_which(scalper, terminal):
    """On e3e74bb: 200, "Closed 1 of 2 scalping positions. BANKNIFTY...: The read operation timed
    out"."""
    scalper.answers = [
        (True, {"status": "success", "orderid": "261027000001"}, 200),
        TIMED_OUT,
    ]
    code, body = terminal()
    assert sorted(scalper.sent) == sorted([(X, "SELL", 65), (Y, "BUY", 30)])
    assert code == 502 and body.get("not_confirmed") is True, (code, body)
    assert body["message"].startswith("Closed 1 of 2 scalping positions.")
    assert f"{Y}: This exit is not confirmed" in body["message"]
    _for_a_trader(body["message"])


def test_control_close_all_refused_by_a_hold_says_nothing_was_closed(scalper, terminal):
    """Control (the re-review's): a close refused by the guard, nothing sent, may say so."""
    scalper.positions = scalper.positions[:1]
    with exit_guard.one_exit("live", "NFO", X, "MIS"):
        code, body = terminal()
    assert scalper.sent == []
    assert code == 409 and body["message"].startswith("No scalping position was closed.")
    assert "not_confirmed" not in body


def test_close_all_re_tapped_after_the_15_s_note_sends_nothing_beside_the_first(
    scalper, terminal, clock
):
    """The failure the re-review traced: the close timed out (and filled), the trader re-taps
    after 15 s, the positions row still shows 65. On e3e74bb a second SELL 65 went: short 65."""
    scalper.positions = scalper.positions[:1]
    scalper.answers = [TIMED_OUT]
    terminal()
    clock.now += exit_guard.EXIT_SHOWS_WITHIN_S + 5
    code, body = terminal()
    assert scalper.sent == [(X, "SELL", 65)], "a second exit went beside the first"
    assert code == 409 and body["message"].startswith("No scalping position was closed.")
    assert "is not confirmed" in body["message"]
    _for_a_trader(body["message"])


# -- MEDIUM-A: the strategy module's exits and the app's Exit meet the same hold -------------------


@pytest.fixture
def dispatch(monkeypatch):
    """The strategy module's live exit dispatch, placement answered from ``answers``."""
    import services.place_order_service as place_order_service
    from services.strategy_module import order_dispatch as od

    state = SimpleNamespace(answers=[], sent=[])

    def place_order_with_auth(order, auth_token, broker, original, force_live=False):
        state.sent.append((order["symbol"], order["action"], order["quantity"]))
        if state.answers:
            return state.answers.pop(0)
        return True, {"status": "success", "orderid": f"2610270000{len(state.sent):02d}"}, 200

    monkeypatch.setattr(od, "resolve_live_auth", lambda api_key: ("tok", "upstox", None))
    monkeypatch.setattr(place_order_service, "place_order_with_auth", place_order_with_auth)
    order = {"symbol": X, "exchange": "NFO", "action": "SELL", "quantity": "65", "product": "MIS",
             "pricetype": "MARKET", "price": "0", "trigger_price": "0", "strategy": "S"}  # fmt: skip
    state.exit = lambda: od.dispatch_order(mode="live", api_key="k", order=dict(order), closes=True)
    return state


def test_a_strategy_exit_after_one_that_may_have_gone_is_held_past_the_15_s_note(
    dispatch, scalper, clock
):
    """The run sizes its exit from its own book. Its first exit timed out; the retry 20 s later
    (the next tick, or the pending-stop job) went on e3e74bb. Now it is a hold, not a send."""
    dispatch.answers = [TIMED_OUT]
    first = dispatch.exit()
    assert first.ok is False
    clock.now += exit_guard.EXIT_SHOWS_WITHIN_S + 5
    retry = dispatch.exit()
    assert dispatch.sent == [(X, "SELL", "65")], "a second exit went beside the first"
    assert retry.ok is False and retry.held is True
    assert "is not confirmed" in retry.error


def test_the_app_s_exit_after_a_timed_out_scalping_close_is_held(scalper, clock, monkeypatch):
    """The scalping close timed out; Upstox's order book does not list it yet, so nothing corrects
    the row (65). The app's Exit 20 s later is sized from the row. On e3e74bb it sent SELL 65 beside
    the close. Now the close's hold refuses it, and says why."""
    world = install(monkeypatch, Broker({X: 65}, 60.0))
    scalper.answers = [TIMED_OUT]
    scalper.close()
    clock.now += exit_guard.EXIT_SHOWS_WITHIN_S + 5
    code, body = world.phone_exit(X)
    assert world.broker.orders == [], "the app's Exit went beside the scalping close"
    assert code == 409 and "is not confirmed" in body["message"]
    _for_a_trader(body["message"])


def test_a_reversal_after_a_timed_out_scalping_close_says_this_order_was_not_sent(
    scalper, clock, monkeypatch
):
    """TradingView reverses (SELL 130 to -65) 20 s after the scalping close timed out. On e3e74bb
    the 15 s note was gone and the SELL 130 went beside the close: short 130 when both fill. The
    refusal speaks of the order, not of an exit."""
    world = install(monkeypatch, Broker({X: 65}, 60.0))
    scalper.answers = [TIMED_OUT]
    scalper.close()
    clock.now += exit_guard.EXIT_SHOWS_WITHIN_S + 5
    code, body = world.tv_smart_exit(X, quantity="130", action="SELL", position_size="-65")
    assert world.broker.orders == [], "the reversal went beside the scalping close"
    said = body["message"]
    assert code == 409 and "is not confirmed" in said and "this order was not sent" in said
    assert "send it again if the position still needs it" in said
    assert "close it from the Upstox app" not in said
    _for_a_trader(said)


def test_exit_all_after_a_timed_out_scalping_close_leaves_the_position_out(
    scalper, clock, monkeypatch
):
    """The same with Exit all: on e3e74bb it sent SELL 65 beside the close and answered success."""
    world = install(monkeypatch, Broker({X: 65}, 60.0))
    scalper.answers = [TIMED_OUT]
    scalper.close()
    clock.now += exit_guard.EXIT_SHOWS_WITHIN_S + 5
    code, body = world.app_exit_all()
    assert world.broker.orders == [], "Exit all went beside the scalping close"
    assert code != 200 and body["status"] == "error", (code, body)
    assert "is not confirmed" in body["message"]
    left = [p for p in body["positions"] if p["symbol"] == X]
    assert left and left[0]["outcome"] == "not_sent"


def test_control_exit_all_past_150_s_with_nothing_working_exits_the_position(
    scalper, clock, monkeypatch
):
    """Control: once the hold has ended on the order book's word, Exit all exits the position as
    any other (the close never reached this simulated Upstox, so the 65 are still held)."""
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    scalper.answers = [TIMED_OUT]
    scalper.close()
    clock.now += PAST_150_S
    code, body = world.app_exit_all()
    assert (code, body["status"]) == (200, "success"), body
    assert [(o["side"], o["qty"]) for o in world.broker.orders] == [("SELL", 65)]
