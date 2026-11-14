"""An automation add is sized from the fills (A7). ORDER PATH.

The intake's one sender marks its call, and only that call. The second half runs the real intake
over the real smart order service and the real Upstox module, with the simulated Upstox of the
books' tests behind them (its positions book shows a fill a minute late): the alerts of session
S10, steps 1 to 4, in the Live pipe.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from automation_world import NOW, TV, FakeDesk, strategy_alert

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "books" / "tests"))
import books_upstox_world as upstox_world  # noqa: E402

from sudarshan.automation import contract, flag, intake  # noqa: E402
from sudarshan.automation import model as m  # noqa: E402
from sudarshan.automation.desk import Desk  # noqa: E402
from sudarshan.books import add_sizing, exit_guard  # noqa: E402

FUT = "NIFTY24NOV26FUT"
FORM = {"name": "ORB", "kind": "future", "underlying": "NIFTY", "product": "MIS", "lots": 1}


# ---- the mark ------------------------------------------------------------------------------------


@pytest.fixture
def session(monkeypatch):
    """The owner's broker session, as the desk reads it for its own call."""
    from database import auth_db

    monkeypatch.setattr(auth_db, "get_auth_token_broker", lambda key: ("token", "upstox"))


def test_the_intake_s_sender_marks_its_call_and_only_its_call(monkeypatch, session):
    import services.place_smart_order_service as service

    seen = []

    def placed(order, api_key=None, auth_token=None, broker=None, analyze=None):
        assert (auth_token, broker) == ("token", "upstox")
        seen.append((add_sizing.asked(), dict(order), analyze))
        return True, {"status": "success", "orderid": "1"}, 200

    monkeypatch.setattr(service, "place_smart_order", placed)
    order = {"symbol": FUT, "exchange": "NFO", "position_size": "65"}
    assert Desk().place(order, "k" * 64, False)[0] is True
    assert seen == [(True, order, False)]  # marked, and the order itself carries nothing new
    assert add_sizing.asked() is False


def test_the_mark_is_let_go_when_the_call_fails(monkeypatch, session):
    import services.place_smart_order_service as service

    def placed(order, api_key=None, auth_token=None, broker=None, analyze=None):
        raise RuntimeError("no answer")

    monkeypatch.setattr(service, "place_smart_order", placed)
    with pytest.raises(RuntimeError):
        Desk().place({}, "k" * 64, False)
    assert add_sizing.asked() is False


def test_the_rest_api_s_own_smart_order_is_not_marked(monkeypatch):
    """A caller of /api/v1/placesmartorder cannot ask for it: no field of the request is read."""
    monkeypatch.setattr(exit_guard, "EXITS", exit_guard.ExitsInFlight())
    broker = upstox_world.Broker({"SBIN": 0}, 0.0)
    world = upstox_world.install(monkeypatch, broker)
    from services.place_smart_order_service import place_smart_order

    ok, _body, code = place_smart_order(
        {
            "apikey": "KEY", "strategy": "TV", "symbol": "SBIN", "exchange": "NSE",
            "action": "BUY", "quantity": "10", "position_size": "10", "product": "MIS",
            "pricetype": "MARKET", "sized_from_fills": True, "add_sized_from_fills": "1",
        },
        auth_token="token",
        broker="upstox",
    )  # fmt: skip
    assert (ok, code) == (True, 200)
    assert world.broker.order_book_reads == 0


# ---- S10, steps 1 to 4, in the Live pipe over a positions book that lags -------------------------


class LiveDesk(FakeDesk):
    """The tests' desk with the real sender: OpenAlgo's smart order service and Upstox module."""

    def __init__(self):
        super().__init__()
        self.guard = "on"

    broker_session = Desk.broker_session  # the real read, of the session the fixture stands in

    def place(self, order, api_key, analyze):
        assert analyze is False
        self.placed.append({**order, "analyze": analyze})
        return Desk.place(self, order, api_key, analyze)


@pytest.fixture
def live(monkeypatch, store, session):
    import services.order_router_service as router

    monkeypatch.setenv(flag.ENV, "on")
    intake._per_link.clear()
    intake._refused.clear()
    monkeypatch.setitem(upstox_world.INSTRUMENTS, FUT, ("NSE_FO|2001", "NFO", "I", "MIS"))
    monkeypatch.setitem(upstox_world.BY_TOKEN, "NSE_FO|2001", FUT)
    monkeypatch.setattr(exit_guard, "EXITS", exit_guard.ExitsInFlight())
    broker = upstox_world.Broker({FUT: 0}, 60.0)
    upstox_world.install(monkeypatch, broker)
    # The order mode is never asked by the intake's own call (H1): an answer here would be a bug.
    monkeypatch.setattr(router, "should_route_to_pending", _never_asked)
    desk = LiveDesk()
    strategy, token = store.create(contract.clean(FORM))
    strategy = store.set_mode(strategy.id, strategy.version, m.LIVE)
    store.set_state(strategy.id, strategy.version, m.ACTIVE)

    def send(position=None, ago=1.0, **more):
        """One alert, two seconds after the last; with no position, the last alert again."""
        import broker.upstox.api.order_api as upstox

        upstox._POSITION_BOOK.invalidate("token")
        if position is not None:
            desk.clock += 2.0
            send.last = strategy_alert(position, desk.clock - ago, **more)
        return intake.handle(token, send.last, TV, desk, store)

    return broker, desk, send


def _never_asked(*args, **kwargs):
    raise AssertionError("the intake's order asked the order mode")


def _sent(broker):
    return [(o["side"], o["qty"]) for o in broker.orders]


def test_s10_steps_1_to_4_in_the_live_pipe_with_the_row_a_minute_late(live):
    broker, desk, send = live
    # 1. long: one order, long 65
    assert send("long", before="flat").verdict == "sent"
    assert broker.true_qty(FUT) == 65
    # 2. the same alert again: the intake's own claim answers, nothing reaches the service
    assert send().verdict == "repeat"
    assert len(desk.placed) == 1
    # a later long alert (a new fire time): the row shows nothing yet, the fills show the 65
    assert send("long", before="long").verdict == "no_action"
    assert _sent(broker) == [("BUY", 65)]
    # 3. flat closes; flat again finds nothing
    assert send("flat", before="long").verdict == "sent"
    assert broker.true_qty(FUT) == 0
    assert send("flat", before="flat").verdict == "no_action"
    # the entry straight after the exit, inside the row's lag: it goes
    assert send("long", before="flat").verdict == "sent"
    assert broker.true_qty(FUT) == 65
    # 4. short one second later: one reversal, short 65, no more
    assert send("short", before="long").verdict == "sent"
    assert broker.true_qty(FUT) == -65
    assert _sent(broker) == [("BUY", 65), ("SELL", 65), ("BUY", 65), ("SELL", 130)]
    assert broker.history(FUT) == [0, 65, 0, 65, -65]


def test_an_add_that_cannot_be_sized_sends_nothing_and_moves_no_state(live, store):
    broker, desk, send = live
    broker.orders_unreadable = True
    out = send("long", before="flat")
    assert out.verdict == "order_refused"
    assert "this order was not sent" in out.sentence and "424" not in out.sentence
    assert broker.orders == []
    state = store.state(store.strategies()[0].id)
    assert (state.last_target, state.held_symbol) == (0, None)
    # the next alert is judged afresh, and goes once the order book answers
    broker.orders_unreadable = False
    assert send("long", before="flat").verdict == "sent"
    assert _sent(broker) == [("BUY", 65)]


def test_the_sandbox_pipe_is_as_it_was(monkeypatch, store):
    """In the sandbox the mark changes nothing: the broker's module is never asked."""
    import broker.upstox.api.order_api as upstox

    monkeypatch.setenv(flag.ENV, "sandbox")
    asked = []
    monkeypatch.setattr(upstox, "place_smartorder_api", lambda *a: asked.append(a))
    desk = FakeDesk()
    strategy, token = store.create(contract.clean(FORM))
    store.set_state(strategy.id, strategy.version, m.ACTIVE)
    out = intake.handle(token, strategy_alert("long", NOW - 1, before="flat"), TV, desk, store)
    assert out.verdict == "sent" and desk.placed[0]["analyze"] is True
    assert asked == []
