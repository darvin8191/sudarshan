"""A scalping or strategy close sized against exits still filling (M6.9, GA1.1).

Both surfaces send a plain order for the quantity their own book holds. The scalping terminal's
book is Upstox's positions, which can show a fill a minute late; the strategy module's is its own
legs, which know nothing of an exit sent from another surface. The hold of an earlier exit ends
when Upstox's order book shows it complete, and that can be before the positions show its fill: a
close sent then closed the position a second time and reversed it.

In Live a close is now sized as the app's own exits are, from the positions rows corrected by
the order book's fills (sudarshan/books/surface_exits.py, ``Book``), the positions read first.
The tests marked DEFECT fail on 21511c32, the head this was built on; the others are controls or
pin the new rule's edges.
"""

from __future__ import annotations

import json
import pathlib
import time

import pytest
from books_upstox_world import Broker, install

import restx_api  # noqa: F401  (at collection: place_order_service's import cycle needs it)
from sudarshan.books import exit_guard, surface_exits

X = "NIFTY27OCT2625900CE"
POSITION = ("NFO", X, "MIS")
LAG = 60.0  # Upstox's positions show a fill this late; its order book shows it at once
REAL_BOOK = surface_exits.upstox_book  # conftest.py serves a stand-in to every other test
VECTORS = json.loads(
    (pathlib.Path(__file__).parent / "surface_sizing_vectors.json").read_text(encoding="utf-8")
)["cases"]


def _ok(orderid="261027000001"):
    return True, {"status": "success", "orderid": orderid}, 200


def _book(**fields):
    return lambda exchange, symbol, product, side: surface_exits.Book(**fields)


class _Order:
    """What a surface's ``send`` and ``resize`` do to one order."""

    def __init__(self, quantity=65):
        self.quantity = quantity
        self.sent: list[int] = []

    def resize(self, held):
        self.quantity = held

    def send(self):
        self.sent.append(self.quantity)
        return _ok(f"2610270000{len(self.sent):02d}")


def _close(order, read_book, side="SELL", quantity=None, book="live", resize=True):
    return surface_exits.exit_once(
        book,
        *POSITION,
        order.send,
        side=side,
        quantity=order.quantity if quantity is None else quantity,
        read_book=read_book,
        resize=order.resize if resize else None,
    )


# -- golden vectors: Upstox's raw rows in, the order out ------------------------------------------


@pytest.fixture
def raw_rows(monkeypatch):
    """The real reader over raw rows: only Upstox's two reads and the symbol master are replaced."""
    import broker.upstox.api.order_api as upstox
    from utils.position_read import PositionReadError

    served = {"calls": []}

    def get_positions(auth):
        served["calls"].append("positions")
        if served["positions"] is None:
            raise PositionReadError("upstox", "test: the read failed")
        return {"status": "success", "data": served["positions"]}

    def get_order_book(auth):
        served["calls"].append("orders")
        if served["orders"] is None:
            return {"status": "error", "message": "down"}
        return {"status": "success", "data": served["orders"]}

    monkeypatch.setattr(upstox, "get_positions", get_positions)
    monkeypatch.setattr(upstox, "get_order_book", get_order_book)
    monkeypatch.setattr(upstox, "get_token", lambda symbol, exchange: "NSE_FO|1001")
    monkeypatch.setattr(upstox, "get_br_symbol", lambda symbol, exchange: symbol)
    monkeypatch.setattr(surface_exits, "upstox_book", REAL_BOOK)
    upstox._POSITION_BOOK.invalidate("vector-token")
    yield served
    upstox._POSITION_BOOK.invalidate("vector-token")


_SENTENCES = {
    "nothing_left": lambda: exit_guard.nothing_left_to_close(POSITION),
    "would_add": lambda: exit_guard.nothing_left_to_close(POSITION, -65),
    "not_sized": lambda: exit_guard.exit_not_sized(POSITION, True),
    "orders_not_read": lambda: exit_guard.exit_not_sized(POSITION, False),
    "positions_not_read": lambda: exit_guard.positions_not_read(POSITION),
    "another_product": lambda: exit_guard.held_in_another_product(POSITION, {"NRML": 65}),
}


@pytest.mark.parametrize("case", VECTORS, ids=[case["name"] for case in VECTORS])
def test_golden_vector(case, raw_rows):
    raw_rows.update(positions=case["positions"], orders=case["orders"])
    order = _Order(case["quantity"])
    ok, body, status = _close(
        order, surface_exits.book_reader("key", "vector-token", "upstox"), side=case["side"]
    )
    if "send" in case:
        assert ok and order.sent == [case["send"]], body
        assert body.get("sent_quantity") == (
            case["send"] if case["send"] != case["quantity"] else None
        )
        return
    assert (ok, status) == (False, case["status"]) and order.sent == [], body
    assert body["nothing_sent"] is True and surface_exits.held(body, status)
    if case["refused"] == "market_working":
        assert "MARKET order" in body["message"] and "still working at Upstox" in body["message"]
    else:
        assert body["message"] == _SENTENCES[case["refused"]]()


def test_the_positions_are_read_first_then_the_order_book(raw_rows):
    """A fill that lands between the two reads is then in the order book and not in the row, and
    the correction still comes out right (sudarshan/books/fills.py)."""
    raw_rows.update(positions=VECTORS[0]["positions"], orders=VECTORS[0]["orders"])
    _close(_Order(), surface_exits.book_reader("key", "vector-token", "upstox"))
    assert raw_rows["calls"] == ["positions", "orders"]


def test_the_sentences_name_the_position_and_what_to_do():
    flat = exit_guard.nothing_left_to_close(POSITION)
    assert flat.startswith("Upstox's orders show NIFTY 25900 CE is already closed as MIS")
    assert "no exit was sent" in flat and "Check Orders and Positions" in flat
    added = exit_guard.nothing_left_to_close(POSITION, -65)
    assert "is now short 65 as MIS" in added and "was not sent" in added
    unread = exit_guard.positions_not_read(POSITION)
    assert "Upstox did not send your positions in time" in unread and "Upstox app" in unread
    for said in (flat, added, unread, exit_guard.fills_not_shown_yet(POSITION)):
        assert "Error" not in said and "409" not in said and "424" not in said


# -- the helper's own rules ---------------------------------------------------------------------------


def test_closable_is_only_what_the_side_closes():
    assert surface_exits.closable("SELL", 65) == 65
    assert surface_exits.closable("buy", -130) == 130
    assert surface_exits.closable("SELL", -65) == 0  # a sale would add to the short
    assert surface_exits.closable("BUY", 65) == 0
    assert surface_exits.closable("SELL", 0) == 0
    assert surface_exits.closable("", 65) == 0  # no side: nothing can be said to close


def test_a_close_never_sends_more_than_its_surface_asked():
    order = _Order(65)
    ok, body, _status = _close(order, _book(held=650))
    assert ok and order.sent == [65] and "sent_quantity" not in body


def test_a_close_is_made_smaller_and_says_what_went():
    order = _Order(130)
    ok, body, _status = _close(order, _book(held=65))
    assert ok and order.sent == [65] and body["sent_quantity"] == 65


def test_a_close_that_cannot_be_made_smaller_sends_nothing():
    order = _Order(130)
    ok, body, status = _close(order, _book(held=65), resize=False)
    assert (ok, status) == (False, 424) and order.sent == []
    assert body["message"] == exit_guard.exit_not_sized(POSITION)


def test_a_quantity_that_is_no_number_sends_nothing():
    order = _Order()
    ok, _body, status = _close(order, _book(held=65), quantity="lots")
    assert (ok, status) == (False, 424) and order.sent == []


def test_the_sandbox_is_left_alone():
    """Control: the sandbox's book shows an exit the moment it is placed; nothing is read."""
    read = []
    order = _Order()

    def read_book(*position):
        read.append(position)
        return surface_exits.Book(held=0)

    ok, _body, _status = _close(order, read_book, book="sandbox")
    assert ok and order.sent == [65] and read == []


def test_another_broker_s_close_goes_at_its_own_size_as_before():
    """Control: only Upstox's rows can be corrected (sudarshan/books/fills.py reads them)."""
    order = _Order()
    assert REAL_BOOK("tok", "zerodha", *POSITION, "SELL") == surface_exits.Book(sized=False)
    ok, _body, _status = _close(order, _book(sized=False))
    assert ok and order.sent == [65]


def test_a_reader_that_breaks_sends_nothing():
    order = _Order()

    def read_book(*position):
        raise RuntimeError("the read broke")

    ok, body, status = _close(order, read_book)
    assert (ok, status) == (False, 424) and order.sent == []
    assert body["message"] == exit_guard.positions_not_read(POSITION)


def test_a_session_that_cannot_be_resolved_sends_nothing(monkeypatch):
    """The scalping terminal passes no broker session: it is resolved from the api key, as the
    order's own is. One that cannot be found reads as positions not read."""
    import database.auth_db as auth_db

    asked = []
    monkeypatch.setattr(
        auth_db, "get_auth_token_broker", lambda key: asked.append(key) or (None, None)
    )
    order = _Order()
    ok, body, status = _close(order, surface_exits.book_reader("key"))
    assert asked == ["key"] and (ok, status) == (False, 424) and order.sent == []
    assert body["message"] == exit_guard.positions_not_read(POSITION)


def test_the_scalping_reader_reads_with_the_session_its_api_key_resolves(monkeypatch):
    import database.auth_db as auth_db

    read_with = []
    monkeypatch.setattr(auth_db, "get_auth_token_broker", lambda key: ("its-token", "upstox"))
    monkeypatch.setattr(
        surface_exits,
        "upstox_book",
        lambda token, broker, *position: (
            read_with.append((token, broker)) or surface_exits.Book(held=65)
        ),
    )
    order = _Order()
    ok, _body, _status = _close(order, surface_exits.book_reader("key"))
    assert ok and read_with == [("its-token", "upstox")]


def test_the_hold_of_an_earlier_exit_is_asked_before_the_size():
    """An exit still working says so, with its order, whatever the books say is held."""
    exit_guard.exit_placed("live", *POSITION, ("261027000007",))
    order = _Order()
    ok, body, status = _close(order, _book(statuses={"261027000007": "open"}, held=65))
    assert (ok, status) == (False, 409) and order.sent == []
    assert "order ending 0007" in body["message"] and "still working at Upstox" in body["message"]


def test_a_sent_close_notes_its_hold_and_a_refused_size_notes_none():
    order = _Order()
    _close(order, _book(held=65))
    assert exit_guard.EXITS.placed_lately("live") == {POSITION}
    exit_guard.EXITS = exit_guard.ExitsInFlight()
    _close(_Order(), _book(held=0))
    assert exit_guard.EXITS.placed_lately("live") == set() and len(exit_guard.EXITS) == 0


def test_a_refused_size_is_kept_for_two_seconds_then_read_again(monkeypatch):
    """The strategy module tries a refused exit again on every tick; Upstox allows 500 reads a
    minute. A refusal for the size answers the same for SIZING_REFUSAL_KEPT_S without a read."""
    reads = []

    def read_book(*position):
        reads.append(position)
        return surface_exits.Book(held=0 if len(reads) == 1 else 65)

    order = _Order()
    first = _close(order, read_book)
    again = _close(order, read_book)
    assert first == again and first[2] == 409 and len(reads) == 1 and order.sent == []
    # The other side of the position is another question, and is read for.
    _close(_Order(), read_book, side="BUY")
    assert len(reads) == 2
    monkeypatch.setattr(surface_exits, "SIZING_REFUSAL_KEPT_S", 0.0)
    time.sleep(0.01)
    ok, _body, _status = _close(order, read_book)
    assert ok and len(reads) == 3 and order.sent == [65]
    assert len(surface_exits.REFUSALS) <= 1


def test_the_kept_refusals_do_not_grow(monkeypatch):
    monkeypatch.setattr(surface_exits, "SIZING_REFUSAL_KEPT_S", 0.0)
    for n in range(50):
        surface_exits.REFUSALS.keep(("live", ("NFO", f"S{n}", "MIS"), "SELL"), _ok())
        time.sleep(0.0005)
    assert len(surface_exits.REFUSALS) <= 2


# -- over a simulated Upstox whose positions lag ----------------------------------------------------


@pytest.fixture
def world(monkeypatch):
    world = install(monkeypatch, Broker({X: 65}, LAG))
    world.broker.base_day = {X: (65, 0)}
    monkeypatch.setattr(surface_exits, "upstox_book", REAL_BOOK)
    return world


def _through_upstox(order_data, auth_token):
    """A surface's plain order, placed through the real Upstox module's place_order_api."""
    import broker.upstox.api.order_api as upstox

    order = {"price": "0", "trigger_price": "0", "disclosed_quantity": "0", **order_data}
    order["quantity"] = str(order["quantity"])
    res, response, orderid = upstox.place_order_api(order, auth_token)
    if orderid:
        return True, {"status": "success", "orderid": orderid}, 200
    return False, {"status": "error", "message": str(response)}, getattr(res, "status", 500)


@pytest.fixture
def scalping(world, monkeypatch):
    import blueprints.scalping as scalping

    monkeypatch.setattr(
        "services.place_order_service.place_order",
        lambda order_data, api_key, auth_token, broker: _through_upstox(order_data, "token"),
    )
    monkeypatch.setattr(scalping, "_is_derivative", lambda exchange: False)  # no symbol master
    monkeypatch.setattr(scalping, "_current_mode", lambda: "live")
    return scalping


@pytest.fixture
def dispatch(world, monkeypatch):
    import services.place_order_service as place_order_service
    from services.strategy_module import order_dispatch as od

    forced = []

    def place_order_with_auth(order, auth_token, broker, original, force_live=False):
        forced.append((force_live, order["quantity"], original["quantity"]))
        return _through_upstox(order, auth_token)

    monkeypatch.setattr(od, "resolve_live_auth", lambda api_key: ("token", "upstox", None))
    monkeypatch.setattr(place_order_service, "place_order_with_auth", place_order_with_auth)
    od.forced = forced
    yield od
    del od.forced


def _scalp_close(scalping, quantity=65):
    return scalping._reducing_exit(X, "NFO", "MIS", "SELL", quantity, None, None, "KEY")


@pytest.fixture
def scalping_session(monkeypatch):
    import database.auth_db as auth_db

    monkeypatch.setattr(auth_db, "get_auth_token_broker", lambda key: ("token", "upstox"))


def _leg_exit(quantity="65"):
    return {"symbol": X, "exchange": "NFO", "action": "SELL", "quantity": quantity,
            "product": "MIS", "pricetype": "MARKET", "price": "0", "trigger_price": "0",
            "strategy": "S"}  # fmt: skip


def test_defect_a_scalping_close_after_the_app_s_exit_filled_does_not_close_twice(
    world, scalping, scalping_session
):
    """DEFECT. The app's Exit fills; Upstox's order book shows it complete, so its hold ends;
    Upstox's positions still show +65 for a minute, and so does the terminal. Close there sent
    SELL 65 again on 21511c32 and left the account short 65."""
    code, _body = world.phone_exit(X)
    assert code == 200 and world.broker.true_qty(X) == 0
    assert world.broker.shown_qty(X, time.monotonic()) == 65  # the lag
    ok, body, status = _scalp_close(scalping)
    assert world.broker.true_qty(X) == 0, world.broker.history(X)
    assert (ok, status) == (False, 409) and body["nothing_sent"] is True
    assert body["message"] == exit_guard.nothing_left_to_close(POSITION)
    assert exit_guard.EXITS.placed_lately("live") == set()  # the first hold ended; none new


def test_defect_a_second_scalping_close_inside_the_lag_does_not_reverse(
    world, scalping, scalping_session
):
    """DEFECT. The terminal's own Close, then Close again (or its trailing stop's auto-exit)
    before the positions show the first fill."""
    ok, _body, _status = _scalp_close(scalping)
    assert ok and world.broker.true_qty(X) == 0
    ok, body, status = _scalp_close(scalping)
    assert (ok, status) == (False, 409) and world.broker.true_qty(X) == 0, body
    assert len(world.broker.orders) == 1


def test_defect_a_scalping_close_sends_only_what_is_still_held(world, scalping, scalping_session):
    """DEFECT. Two lots held, one closed from the app; the terminal still shows two and closes
    two: SELL 130 over a position of 65 left the account short 65. Now SELL 65 goes."""
    world.broker.base[X] = 130
    world.broker.base_day = {X: (130, 0)}
    import broker.upstox.api.order_api as upstox

    res, _response, orderid = upstox.place_order_api(
        {**_leg_exit("65"), "disclosed_quantity": "0"}, "token"
    )
    assert orderid and world.broker.true_qty(X) == 65
    ok, body, _status = _scalp_close(scalping, 130)
    assert ok and body["sent_quantity"] == 65 and world.broker.true_qty(X) == 0, body
    assert [order["qty"] for order in world.broker.orders] == [65, 65]


def test_control_a_scalping_close_of_a_held_position_goes_once(world, scalping, scalping_session):
    ok, body, status = _scalp_close(scalping)
    assert (ok, status) == (True, 200) and world.broker.true_qty(X) == 0, body
    assert exit_guard.EXITS.placed_lately("live") == {POSITION}


def test_a_scalping_close_with_the_order_book_down_sends_nothing(world, scalping, scalping_session):
    world.broker.orders_unreadable = True
    ok, body, status = _scalp_close(scalping)
    assert (ok, status) == (False, 424) and world.broker.true_qty(X) == 65
    assert body["message"] == exit_guard.exit_not_sized(POSITION, False)
    assert "Close it from the Upstox app" in body["message"]


def test_defect_a_strategy_exit_after_the_app_s_exit_filled_is_held_not_sent(world, dispatch):
    """DEFECT. The run's own book still holds the leg; the app closed the position. On 21511c32
    the leg's stop then sent SELL 65 and the account went short 65. Now it is a refused dispatch
    marked held: the engine releases its claim_leg_exit, keeps the leg managed and records
    leg_exit_held at warn (test_books_exit_rereview_v051.py pins that for any held dispatch)."""
    code, _body = world.phone_exit(X)
    assert code == 200
    result = dispatch.dispatch_order(mode="live", api_key="k", order=_leg_exit(), closes=True)
    assert world.broker.true_qty(X) == 0 and dispatch.forced == []
    assert result.ok is False and result.held is True and result.rejected
    assert result.error == exit_guard.nothing_left_to_close(POSITION)


def test_a_strategy_exit_made_smaller_keeps_force_live(world, dispatch):
    world.broker.base[X] = 130
    world.broker.base_day = {X: (130, 0)}
    code, _body = world.tv_smart_exit(X, quantity="0", action="SELL", position_size="65")
    assert code == 200 and world.broker.true_qty(X) == 65
    # The reversal's own hold ends when the order book shows its order complete.
    result = dispatch.dispatch_order(mode="live", api_key="k", order=_leg_exit("130"), closes=True)
    assert result.ok and world.broker.true_qty(X) == 0
    assert dispatch.forced == [(True, "65", "65")]


def test_control_a_strategy_entry_is_not_sized_or_read_for(world, dispatch):
    """Control: only an order marked as closing a position the run holds is sized."""
    reads = world.broker.order_book_reads
    order = {**_leg_exit(), "action": "BUY"}
    result = dispatch.dispatch_order(mode="live", api_key="k", order=order)
    assert result.ok and world.broker.order_book_reads == reads
    assert dispatch.forced == [(True, "65", "65")]


def test_control_a_sandbox_strategy_exit_never_reads_upstox(world, dispatch, monkeypatch):
    import services.sandbox_service as sandbox_service

    monkeypatch.setattr(
        sandbox_service,
        "sandbox_place_order",
        lambda order, api_key, original: (True, {"status": "success", "orderid": "S1"}, 200),
    )
    reads = (world.broker.reads, world.broker.order_book_reads)
    result = dispatch.dispatch_order(mode="sandbox", api_key="k", order=_leg_exit(), closes=True)
    assert result.ok and (world.broker.reads, world.broker.order_book_reads) == reads
