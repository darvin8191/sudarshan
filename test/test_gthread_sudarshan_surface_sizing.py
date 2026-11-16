"""SUDARSHAN M6.9 (GA1.1): closes racing a positions book that lags, with real threads.

The scalping terminal's closes and the strategy module's exits send a plain order for the
quantity their own book holds, through ``sudarshan.books.surface_exits.exit_once``. Upstox's
positions can show a fill a minute late while its order book shows it at once. The hold of an
earlier exit ends when the order book shows that exit complete, so before GA1.1 the next close,
still sized from the lagging positions, went out too: the position was closed twice and reversed.

Under the gthread worker the closes are real threads (a tap, the trailing stop's auto-exit and a
strategy tick can arrive together), so each case starts them on a barrier. The first test asserts
the defect itself on the old shape of the call, so the rest cannot pass vacuously.

Upstox is an in-memory account here: positions rows that never show a fill (the lag), an order
book that shows every fill at once. No network, no account.
"""

from __future__ import annotations

import threading
import time

import pytest

from sudarshan.books import exit_guard, surface_exits

X = "NIFTY27OCT2625900CE"
TOKEN = "NSE_FO|1001"
POSITION = ("NFO", X, "MIS")
THREADS = 8
AUTH = "gthread-surface-sizing-token"


class LaggingUpstox:
    """One long position of ``held`` whose positions row never moves; fills show in the order
    book the moment an order is placed."""

    def __init__(self, held: int = 65):
        self.held = held
        self.orders: list[dict] = []
        self._lock = threading.Lock()
        self.before_orders_read = None  # called inside every order book read, before it answers
        self.after_positions_read = None  # called inside every positions read, after its snapshot
        self.reads: list[str] = []

    def get_positions(self, auth):
        row = {
            "instrument_token": TOKEN,
            "exchange": "NFO",
            "product": "I",
            "tradingsymbol": X,
            "quantity": self.held,
            "day_buy_quantity": self.held,
            "day_sell_quantity": 0,
        }
        with self._lock:
            self.reads.append("positions")
        if self.after_positions_read is not None:
            self.after_positions_read()
        return {"status": "success", "data": [row]}

    def get_order_book(self, auth):
        if self.before_orders_read is not None:
            self.before_orders_read()
        with self._lock:
            self.reads.append("orders")
            rows = [
                {
                    "order_id": "261027000000",
                    "instrument_token": TOKEN,
                    "exchange": "NFO",
                    "product": "I",
                    "tradingsymbol": X,
                    "transaction_type": "BUY",
                    "status": "complete",
                    "order_type": "MARKET",
                    "quantity": self.held,
                    "filled_quantity": self.held,
                    "pending_quantity": 0,
                },
                *[dict(order) for order in self.orders],
            ]
        return {"status": "success", "data": rows}

    def fill(self, side: str, quantity: int) -> str:
        """An order placed and filled at once; the order book shows it complete from now."""
        with self._lock:
            order_id = f"2610270000{len(self.orders) + 1:02d}"
            self.orders.append(
                {
                    "order_id": order_id,
                    "instrument_token": TOKEN,
                    "exchange": "NFO",
                    "product": "I",
                    "tradingsymbol": X,
                    "transaction_type": side,
                    "status": "complete",
                    "order_type": "MARKET",
                    "quantity": quantity,
                    "filled_quantity": quantity,
                    "pending_quantity": 0,
                }
            )
        return order_id

    @property
    def true_position(self) -> int:
        with self._lock:
            return self.held + sum(
                order["quantity"] if order["transaction_type"] == "BUY" else -order["quantity"]
                for order in self.orders
            )

    def statuses(self) -> dict[str, str]:
        with self._lock:
            return {order["order_id"]: "complete" for order in self.orders}


@pytest.fixture
def upstox(monkeypatch):
    import broker.upstox.api.order_api as module

    account = LaggingUpstox()
    monkeypatch.setattr(module, "get_positions", account.get_positions)
    monkeypatch.setattr(module, "get_order_book", account.get_order_book)
    monkeypatch.setattr(module, "get_token", lambda symbol, exchange: TOKEN)
    monkeypatch.setattr(module, "get_br_symbol", lambda symbol, exchange: symbol)
    monkeypatch.setattr(exit_guard, "EXITS", exit_guard.ExitsInFlight())
    monkeypatch.setattr(surface_exits, "REFUSALS", surface_exits._Refusals())
    module._POSITION_BOOK.invalidate(AUTH)
    yield account
    module._POSITION_BOOK.invalidate(AUTH)


def _close(account: LaggingUpstox, asked: int = 65, sized: bool = True):
    """One surface's close of ``asked``, as blueprints/scalping.py sends it."""
    order = {"quantity": asked}

    def send():
        order_id = account.fill("SELL", order["quantity"])
        return True, {"status": "success", "orderid": order_id}, 200

    if not sized:  # the call as it was before GA1.1: the surface's own size, the hold alone
        return surface_exits.exit_once("live", *POSITION, send, account.statuses, side="SELL")
    return surface_exits.exit_once(
        "live",
        *POSITION,
        send,
        side="SELL",
        quantity=asked,
        read_book=surface_exits.book_reader(None, AUTH, "upstox"),
        resize=lambda held: order.update(quantity=held),
    )


def _wave(account: LaggingUpstox, asked: int = 65, sized: bool = True) -> list:
    """``THREADS`` closes of the same position released together; their answers."""
    barrier = threading.Barrier(THREADS)
    answers: list = []
    errors: list = []
    lock = threading.Lock()

    def run():
        try:
            barrier.wait(10)
            answer = _close(account, asked, sized)
            with lock:
                answers.append(answer)
        except Exception as error:  # noqa: BLE001 - reported below, never swallowed
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=run, daemon=True) for _ in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert not any(thread.is_alive() for thread in threads), "a close never returned"
    assert errors == []
    assert len(answers) == THREADS
    return answers


def test_the_defect_closes_sized_from_their_own_book_reverse_the_position(upstox):
    """The old shape of the call: each close goes at the size its surface sees. One close takes
    the position flat. Its order shows complete, so its hold ends, and the next close, still
    looking at a positions row that shows +65, goes too: short 65. A wave of them after that
    sells again for every thread whose turn comes after the one before it showed complete."""
    ok, _body, _status = _close(upstox, sized=False)
    assert ok and upstox.true_position == 0
    ok, _body, _status = _close(upstox, sized=False)
    assert ok and upstox.true_position == -65  # closed twice: the reversal GA1.1 ends
    _wave(upstox, sized=False)
    assert upstox.true_position < -65


def test_closes_racing_a_lagging_positions_read_close_the_position_once(upstox):
    first = _wave(upstox)
    assert sum(1 for ok, _body, _status in first if ok) == 1
    assert upstox.true_position == 0
    # The positions row still shows +65 and will for a minute; every surface asks again.
    for _ in range(3):
        again = _wave(upstox)
        assert [ok for ok, _body, _status in again] == [False] * THREADS
        assert all(surface_exits.held(body, status) for _ok, body, status in again)
    assert upstox.true_position == 0 and len(upstox.orders) == 1
    assert len(exit_guard.EXITS) == 0  # every claim was let go


def test_a_fill_that_lands_between_the_two_reads_is_counted(upstox):
    """The positions are read first. An exit from another surface fills after that read and
    before the order book is read: the order book shows it, the row does not, and the close
    sends nothing. Read the other way round the fill would be in neither."""
    read_positions = threading.Event()
    filled = threading.Event()

    upstox.after_positions_read = read_positions.set
    upstox.before_orders_read = lambda: filled.wait(10)

    def elsewhere():
        assert read_positions.wait(10)
        upstox.fill("SELL", 65)  # the app's Exit, filled at Upstox
        filled.set()

    other = threading.Thread(target=elsewhere, daemon=True)
    other.start()
    ok, body, status = _close(upstox)
    other.join(10)
    assert upstox.reads[:2] == ["positions", "orders"]
    assert (ok, status) == (False, 409), body
    assert body["message"] == exit_guard.nothing_left_to_close(POSITION)
    assert upstox.true_position == 0 and len(upstox.orders) == 1


def test_closes_racing_for_more_than_is_held_send_only_what_is_held(upstox):
    """Two lots at the start, one closed from another surface and not shown yet. Eight closes of
    two lots race: one goes, for the one lot still held, and the position ends flat, not short."""
    upstox.held = 130
    upstox.fill("SELL", 65)
    answers = _wave(upstox, asked=130)
    sent = [body for ok, body, _status in answers if ok]
    assert len(sent) == 1 and sent[0]["sent_quantity"] == 65
    assert upstox.true_position == 0
    again = _wave(upstox, asked=130)
    assert not any(ok for ok, _body, _status in again) and upstox.true_position == 0


def test_a_refusal_kept_under_threads_costs_one_read_of_each_book(upstox):
    """Every thread of a wave over a flat position is answered, and the wave after it within the
    two seconds reads Upstox no more: the strategy module asks again on every tick."""
    upstox.fill("SELL", 65)
    _wave(upstox)
    reads = len(upstox.reads)
    started = time.monotonic()
    again = _wave(upstox)
    if time.monotonic() - started < surface_exits.SIZING_REFUSAL_KEPT_S / 2:
        assert len(upstox.reads) == reads
    assert not any(ok for ok, _body, _status in again)
