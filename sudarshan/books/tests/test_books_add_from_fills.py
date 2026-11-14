"""An Automation add is sized from the fills (M12.1, A7), over the simulated Upstox.

A smart order that only adds is sized from Upstox's positions row alone, and the row can show a
fill a minute late. The Automation intake marks its call (``sudarshan.books.add_sizing``), and an
order of its that only adds is then sized as one that reduces is: the rows, then the order book,
corrected by its fills. Every other caller is untouched.

The first tests assert the defect itself on a call without the mark, so the rest cannot pass
vacuously. The golden cases are the ``add`` cases of fill_vectors.json: raw Upstox rows in, the
order that goes out.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from books_upstox_world import INSTRUMENTS, Broker, install

from sudarshan.books import add_sizing, exit_guard

X = "NIFTY27OCT2625900CE"  # MIS
Y = "BANKNIFTY27OCT2657500PE"  # NRML
Z = "SBIN"  # MIS

VECTORS = [
    case
    for case in json.loads((Path(__file__).parent / "fill_vectors.json").read_text())["cases"]
    if "add" in case
]
MATCHED = "Positions Already Matched. No Action needed."


@pytest.fixture
def clock(monkeypatch):
    real = time.monotonic
    offset = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: real() + offset[0])

    class Clock:
        @staticmethod
        def advance(seconds: float) -> None:
            offset[0] += seconds

    return Clock()


def _sent(broker, symbol):
    return [(o["side"], o["qty"]) for o in broker.orders if o["symbol"] == symbol]


def _fresh_book():
    import broker.upstox.api.order_api as upstox

    upstox._POSITION_BOOK.invalidate("token")


def _to(world, symbol, size, marked=True):
    """A smart order to ``size`` as the intake sends it: the strategy's own quantity, the signed
    target. ``marked``: inside the intake's mark."""
    action = "BUY" if size > 0 else "SELL"
    quantity = str(abs(size) or 65)
    _fresh_book()
    if not marked:
        return world.tv_smart_exit(symbol, quantity, action, str(size))
    with add_sizing.sized_from_fills():
        return world.tv_smart_exit(symbol, quantity, action, str(size))


# -- the defect, on a call without the mark ----------------------------------------------------------


def test_the_defect_an_unmarked_entry_after_an_exit_inside_the_lag_is_missed(monkeypatch, clock):
    """Long 65, exited (the row lags a minute). A new entry to 65 reads the row's old 65 and
    answers "already matched": the position stays flat."""
    world = install(monkeypatch, Broker({X: 65}, 60.0))
    assert world.tv_smart_exit(X, "65", "SELL", "0")[0] == 200
    clock.advance(2.0)
    code, body = _to(world, X, 65, marked=False)
    assert (code, body.get("message")) == (200, MATCHED)
    assert world.broker.true_qty(X) == 0


def test_the_defect_an_unmarked_entry_sent_twice_inside_the_lag_doubles(monkeypatch, clock):
    world = install(monkeypatch, Broker({X: 0}, 60.0))
    assert _to(world, X, 65, marked=False)[0] == 200
    clock.advance(2.0)
    assert _to(world, X, 65, marked=False)[0] == 200
    assert world.broker.true_qty(X) == 130


# -- the same, marked --------------------------------------------------------------------------------


def test_a_marked_entry_after_the_strategy_s_own_exit_inside_the_lag_goes(monkeypatch, clock):
    world = install(monkeypatch, Broker({X: 65}, 60.0))
    assert world.tv_smart_exit(X, "65", "SELL", "0")[0] == 200
    clock.advance(2.0)
    code, body = _to(world, X, 65)
    assert code == 200 and body.get("orderid"), body
    assert world.broker.true_qty(X) == 65
    assert _sent(world.broker, X) == [("SELL", 65), ("BUY", 65)]


def test_a_marked_entry_sent_again_inside_the_lag_finds_the_first(monkeypatch, clock):
    world = install(monkeypatch, Broker({X: 0}, 60.0))
    assert _to(world, X, 65)[0] == 200
    clock.advance(2.0)
    code, body = _to(world, X, 65)
    assert (code, body.get("message")) == (200, MATCHED)
    assert _sent(world.broker, X) == [("BUY", 65)]


def test_a_marked_short_entry_and_its_re_entry_inside_the_lag(monkeypatch, clock):
    world = install(monkeypatch, Broker({Y: 0}, 60.0))
    assert _to(world, Y, -30)[0] == 200
    clock.advance(1.0)
    assert _to(world, Y, -30)[1].get("message") == MATCHED
    assert world.tv_smart_exit(Y, "30", "BUY", "0")[0] == 200
    clock.advance(1.0)
    assert _to(world, Y, -30)[0] == 200
    assert world.broker.history(Y) == [0, -30, 0, -30]


def test_a_marked_add_further_the_same_way_counts_the_add_the_row_does_not_show(monkeypatch, clock):
    """A scaled strategy: 65, then 130, then 130 again and 195, all inside the row's lag."""
    world = install(monkeypatch, Broker({X: 0}, 60.0))
    for size in (65, 130, 130, 195):
        assert _to(world, X, size)[0] == 200
        clock.advance(1.0)
    assert _sent(world.broker, X) == [("BUY", 65), ("BUY", 65), ("BUY", 65)]
    assert world.broker.true_qty(X) == 195


def test_a_marked_add_with_no_row_at_all_is_sized_from_the_fills(monkeypatch, clock):
    """An instrument Upstox's positions do not list yet: its fills alone say what is held."""
    world = install(monkeypatch, Broker({}, 60.0))
    world.broker.fills.append((time.monotonic(), X, 65))
    code, body = _to(world, X, 65)
    assert (code, body.get("message")) == (200, MATCHED)
    assert world.broker.orders == []


# -- unknown: nothing is sent ------------------------------------------------------------------------


def test_a_marked_add_is_not_sent_when_the_order_book_cannot_be_read(monkeypatch):
    broker = Broker({X: 65, Z: 0}, 0.0)
    broker.orders_unreadable = True
    world = install(monkeypatch, broker)
    for symbol, size in ((X, 130), (Z, 10)):
        code, body = _to(world, symbol, size)
        assert code == 424, body
        product = INSTRUMENTS[symbol][3]
        assert body["message"] == exit_guard.exit_not_sized(
            (INSTRUMENTS[symbol][1], symbol, product), orders_read=False, order=True
        )
    assert broker.orders == []


def test_a_marked_add_is_not_sent_when_the_order_book_is_cut_off(monkeypatch):
    broker = Broker({X: 0}, 0.0)
    broker.order_book_delay = exit_guard.EXIT_ORDERS_READ_S + 5.0
    monkeypatch.setattr(exit_guard, "EXIT_ORDERS_READ_S", 0.2)
    world = install(monkeypatch, broker)
    started = time.monotonic()
    code, body = _to(world, X, 65)
    assert code == 424 and "did not send your orders in time" in body["message"], body
    assert time.monotonic() - started < 3.0  # the read's own limit, never the client's
    assert broker.orders == []


def test_the_not_sized_refusal_is_told_and_is_a_sentence_for_a_trader(monkeypatch):
    """The owner hears of it on Telegram (the services tell only a ``nothing_sent`` failure), in
    words with no status code and no cause guessed."""
    from events import OrderFailedEvent
    from utils.event_bus import bus

    heard = []
    monkeypatch.setattr(bus, "publish", lambda event: heard.append(event))
    broker = Broker({X: 0}, 0.0)
    broker.orders_unreadable = True
    world = install(monkeypatch, broker)
    code, body = _to(world, X, 65)
    assert code == 424
    failed = [e for e in heard if isinstance(e, OrderFailedEvent)]
    assert len(failed) == 1 and failed[0].response_data.get("nothing_sent") is True
    for word in ("424", "409", "HTTP", "None", "token", "exception"):
        assert word not in body["message"]
    assert "this order was not sent" in body["message"]


def test_an_unmarked_add_still_goes_when_the_order_book_cannot_be_read(monkeypatch):
    broker = Broker({X: 65}, 0.0)
    broker.orders_unreadable = True
    world = install(monkeypatch, broker)
    assert _to(world, X, 130, marked=False)[0] == 200
    assert broker.order_book_reads == 0 and broker.true_qty(X) == 130


# -- the reads: one for a marked add, none for any other ---------------------------------------------


def test_a_marked_add_reads_the_order_book_once_after_the_positions(monkeypatch):
    broker = Broker({X: 65, Z: 0}, 0.0)
    world = install(monkeypatch, broker)
    seen = []
    get = broker.get

    def watched(url, headers=None, **kwargs):
        seen.append("orders" if "order" in url else "positions")
        return get(url, headers=headers, **kwargs)

    broker.get = watched
    assert _to(world, X, 130)[0] == 200
    assert seen == ["positions", "orders"]
    assert _to(world, Z, 10)[0] == 200
    assert broker.order_book_reads == 2
    assert broker.true_qty(X) == 130 and broker.true_qty(Z) == 10


def test_an_unmarked_add_makes_no_order_book_read(monkeypatch):
    """Every other caller is untouched: from flat, further the same way, and already there."""
    broker = Broker({X: 65, Z: 0}, 0.0)
    world = install(monkeypatch, broker)
    assert _to(world, X, 130, marked=False)[0] == 200
    assert _to(world, Z, 10, marked=False)[0] == 200
    assert _to(world, Z, 10, marked=False)[1].get("message") == MATCHED
    assert broker.order_book_reads == 0


def test_the_mark_is_one_call_s_alone():
    assert add_sizing.asked() is False
    with add_sizing.sized_from_fills():
        assert add_sizing.asked() is True
        seen = []
        thread = threading.Thread(target=lambda: seen.append(add_sizing.asked()))
        thread.start()
        thread.join()
        assert seen == [False]  # another request's thread never sees it
    assert add_sizing.asked() is False
    with pytest.raises(RuntimeError), add_sizing.sized_from_fills():
        raise RuntimeError("the call failed")
    assert add_sizing.asked() is False


def test_the_mark_is_no_part_of_the_order_or_of_what_is_reported(monkeypatch):
    from utils.event_bus import bus

    heard = []
    monkeypatch.setattr(bus, "publish", lambda event: heard.append(event))
    broker = Broker({X: 0}, 0.0)
    world = install(monkeypatch, broker)
    placed = []
    post = broker.post

    def watched(url, headers=None, content=None, **kwargs):
        placed.append(json.loads(content))
        return post(url, headers=headers, content=content, **kwargs)

    broker.post = watched
    code, body = _to(world, X, 65)
    assert code == 200 and set(body) == {"status", "orderid"}
    text = json.dumps([placed, body, [vars(event) for event in heard]], default=str)
    assert "sized_from_fills" not in text and "add_sized" not in text


# -- an add is never refused for being busy ----------------------------------------------------------


def test_a_marked_add_goes_while_an_exit_of_the_position_is_claimed(monkeypatch):
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    with exit_guard.one_exit("live", "NFO", X, "MIS") as busy:
        assert not busy
        assert _to(world, X, 130)[0] == 200
    assert world.broker.true_qty(X) == 130


def test_a_marked_add_goes_whatever_is_working_or_held(monkeypatch):
    broker = Broker({X: 65}, 0.0)
    broker.rest(X, "SELL", 65, kind="MARKET", trigger=0)
    world = install(monkeypatch, broker)
    exit_guard.exit_placed("live", "NFO", X, "MIS", ["2510270000990000"])
    assert _to(world, X, 130)[0] == 200
    assert _sent(broker, X) == [("BUY", 65)]


def test_a_marked_add_takes_no_claim_and_notes_no_hold(monkeypatch):
    world = install(monkeypatch, Broker({X: 0}, 0.0))
    claimed, noted = [], []
    one_exit, exit_placed = exit_guard.one_exit, exit_guard.exit_placed
    monkeypatch.setattr(
        exit_guard, "one_exit", lambda *a, **k: claimed.append(a) or one_exit(*a, **k)
    )
    monkeypatch.setattr(
        exit_guard, "exit_placed", lambda *a, **k: noted.append(a) or exit_placed(*a, **k)
    )
    assert _to(world, X, 65)[0] == 200
    assert claimed == [] and noted == []


# -- the fills show it is no add: claimed, checked and held as an order that crosses is -------------


def test_a_marked_short_target_the_row_reads_as_an_entry_lands_on_its_target(monkeypatch, clock):
    """Long 65 a second ago (the row still flat), then the short alert. Sized from the row it
    sells 65 and ends flat; sized from the fills it sells 130, and it is claimed and held."""
    world = install(monkeypatch, Broker({X: 0}, 60.0))
    assert _to(world, X, 65)[0] == 200
    clock.advance(1.0)
    noted = []
    exit_placed = exit_guard.exit_placed
    monkeypatch.setattr(
        exit_guard, "exit_placed", lambda *a, **k: noted.append(a) or exit_placed(*a, **k)
    )
    code, body = _to(world, X, -65)
    assert code == 200, body
    assert _sent(world.broker, X) == [("BUY", 65), ("SELL", 130)]
    assert world.broker.true_qty(X) == -65
    assert [note[:4] for note in noted] == [("live", "NFO", X, "MIS")]


def test_the_unmarked_control_of_that_ends_flat(monkeypatch, clock):
    world = install(monkeypatch, Broker({X: 0}, 60.0))
    assert _to(world, X, 65, marked=False)[0] == 200
    clock.advance(1.0)
    assert _to(world, X, -65, marked=False)[0] == 200
    assert world.broker.true_qty(X) == 0


def test_a_marked_order_the_fills_show_crossing_is_refused_while_an_exit_is_claimed(
    monkeypatch, clock
):
    world = install(monkeypatch, Broker({X: 0}, 60.0))
    assert _to(world, X, 65)[0] == 200
    clock.advance(1.0)
    with exit_guard.one_exit("live", "NFO", X, "MIS") as busy:
        assert not busy
        code, body = _to(world, X, -65)
    assert code == 409 and "not sent" in body["message"], body
    assert _sent(world.broker, X) == [("BUY", 65)]


def test_a_marked_order_the_fills_show_crossing_is_refused_while_a_market_exit_works(
    monkeypatch, clock
):
    """The closing side is the one that closes what the fills show (the row shows nothing)."""
    world = install(monkeypatch, Broker({X: 0}, 60.0))
    assert _to(world, X, 65)[0] == 200
    clock.advance(1.0)
    world.broker.rest(X, "SELL", 65, kind="MARKET", trigger=0)
    code, body = _to(world, X, -65)
    assert code == 409 and "still working at Upstox" in body["message"], body
    assert _sent(world.broker, X) == [("BUY", 65)]


# -- golden vectors: raw Upstox rows in, the order out -----------------------------------------------


def _from_rows(monkeypatch, case, marked):
    """``place_smartorder_api`` over one vector's raw rows; what it sent, and its answer."""
    import broker.upstox.api.order_api as upstox

    add = case["add"]
    sent = []

    def place(data, auth):
        sent.append([data["action"], int(data["quantity"])])
        return upstox._ErrorResponse(200), {"status": "success"}, "261027000999"

    def order_book(auth):
        if case["orders"] is None:
            raise TimeoutError("the order book did not answer")
        return {"status": "success", "data": case["orders"]}

    monkeypatch.setattr(
        upstox, "_get_cached_positions", lambda a: {"status": "success", "data": case["positions"]}
    )
    monkeypatch.setattr(upstox, "get_order_book", order_book)
    monkeypatch.setattr(upstox, "get_token", lambda symbol, exchange: case["token"])
    monkeypatch.setattr(upstox, "get_br_symbol", lambda symbol, exchange: symbol)
    monkeypatch.setattr(upstox, "place_order_api", place)
    data = {
        "strategy": "Test",
        "symbol": add["symbol"],
        "exchange": add["exchange"],
        "product": add["product"],
        "action": "BUY" if add["target"] > 0 else "SELL",
        "pricetype": "MARKET",
        "quantity": "65",
        "position_size": str(add["target"]),
    }
    if marked:
        with add_sizing.sized_from_fills():
            res, body, _orderid = upstox.place_smartorder_api(data, "token")
    else:
        res, body, _orderid = upstox.place_smartorder_api(data, "token")
    return sent, res, body


def test_the_vectors_cover_what_the_design_names():
    names = " ".join(case["name"] for case in VECTORS)
    for words in ("row does not show", "own exit", "converted", "filled quantity null"):
        assert words in names, words
    assert len(VECTORS) >= 12


@pytest.mark.parametrize("case", VECTORS, ids=[case["name"] for case in VECTORS])
def test_golden_an_add_sized_from_raw_upstox_rows(monkeypatch, case):
    add = case["add"]
    sent, res, body = _from_rows(monkeypatch, case, marked=True)
    assert sent == ([add["sends"]] if add["sends"] else [])
    if add["answer"] == "unknown":
        assert case["held"] is None
        assert res.status == 424 and body["nothing_sent"] is True
        assert "this order was not sent" in body["message"]
    elif add["answer"] == "matched":
        assert res is None and body["message"].startswith("No action needed")
    else:
        assert res.status == 200


@pytest.mark.parametrize("case", VECTORS, ids=[case["name"] for case in VECTORS])
def test_golden_the_same_rows_without_the_mark_go_as_the_row_says(monkeypatch, case):
    sent, _res, _body = _from_rows(monkeypatch, case, marked=False)
    assert sent == ([case["add"]["row_sends"]] if case["add"]["row_sends"] else [])


def test_the_vectors_differ_from_the_row_where_the_row_lags():
    differ = [case["name"] for case in VECTORS if case["add"]["sends"] != case["add"]["row_sends"]]
    assert len(differ) >= 8, differ


# -- barrier 8: an intake add and an Exit all together -----------------------------------------------
#
# The add is sized after the order book it read, and Exit all's claims are as they were: the add
# takes none, so Exit all never leaves the position out as busy, and the add is never refused.


def _marked_add_in_a_thread(world, symbol, size, before=lambda: None):
    out: dict = {}

    def run():
        before()
        out["thread"] = threading.current_thread()
        out["answer"] = _to(world, symbol, size)

    thread = threading.Thread(target=run, daemon=True)
    return thread, out


def _exit_all_outcome(body, symbol):
    return [p["outcome"] for p in body.get("positions", []) if p["symbol"] == symbol]


def test_barrier_exit_all_runs_whole_while_a_marked_add_reads_its_order_book(monkeypatch):
    """The add waits inside its order book read; Exit all starts and finishes meanwhile. Exit all
    exits the position (the add holds no claim on it), and the add, sized after the book it then
    reads, finds the position flat and buys its whole target."""
    broker = Broker({X: 65}, 60.0)
    world = install(monkeypatch, broker)
    reading, go = threading.Event(), threading.Event()
    thread, out = _marked_add_in_a_thread(world, X, 130)
    order_book = broker._order_book

    def held_up(timeout=None):
        if threading.current_thread() is out.get("thread"):
            reading.set()
            assert go.wait(20)
        return order_book(timeout)

    broker._order_book = held_up
    thread.start()
    assert reading.wait(20), "the marked add made no order book read"
    code, body = world.app_exit_all()
    assert (code, body["status"]) == (200, "success"), body
    assert _exit_all_outcome(body, X) == ["sent"]
    go.set()
    thread.join(20)
    code, body = out["answer"]
    assert code == 200 and body.get("orderid"), body
    assert _sent(broker, X) == [("SELL", 65), ("BUY", 130)]
    assert broker.true_qty(X) == 130


def test_barrier_a_marked_add_runs_whole_while_exit_all_is_sending(monkeypatch):
    """Exit all waits inside the send of the position's exit, holding its claim on the whole
    book. The add is not refused for that, and is sized from the book it read (the exit is not in
    it yet); Exit all then finishes as it would have."""
    broker = Broker({X: 65}, 60.0)
    world = install(monkeypatch, broker)
    sending, go = threading.Event(), threading.Event()
    post = broker.post
    exiting: dict = {}

    def held_up(url, headers=None, content=None, **kwargs):
        if threading.current_thread() is exiting.get("thread"):
            sending.set()
            assert go.wait(20)
        return post(url, headers=headers, content=content, **kwargs)

    broker.post = held_up

    def exit_all():
        exiting["thread"] = threading.current_thread()
        exiting["answer"] = world.app_exit_all()

    thread = threading.Thread(target=exit_all, daemon=True)
    thread.start()
    assert sending.wait(20)
    code, body = _to(world, X, 130)
    assert code == 200 and body.get("orderid"), body
    assert _sent(broker, X) == [("BUY", 65)]
    go.set()
    thread.join(20)
    code, body = exiting["answer"]
    assert (code, body["status"]) == (200, "success"), body
    assert _exit_all_outcome(body, X) == ["sent"]
    assert _sent(broker, X) == [("BUY", 65), ("SELL", 65)]


@pytest.mark.parametrize("round_", range(6))
def test_barrier_a_marked_add_and_exit_all_released_together(monkeypatch, round_):
    """Whichever comes first: the add is never refused and Exit all never leaves the position
    out, and the add's quantity is its target less what the order book it read showed held."""
    broker = Broker({X: 65}, 60.0)
    world = install(monkeypatch, broker)
    barrier = threading.Barrier(2)
    thread, out = _marked_add_in_a_thread(world, X, 130, before=lambda: barrier.wait(10))
    read: dict = {}
    order_book = broker._order_book
    token = INSTRUMENTS[X][0]

    def watched(timeout=None):
        answer = order_book(timeout)
        if threading.current_thread() is out.get("thread"):
            rows = [r for r in answer.json()["data"] if r["instrument_token"] == token]
            read["held"] = 65 + sum(
                (1 if r["transaction_type"] == "BUY" else -1) * r["filled_quantity"] for r in rows
            )
        return answer

    broker._order_book = watched
    exiting: dict = {}

    def exit_all():
        barrier.wait(10)
        exiting["answer"] = world.app_exit_all()

    other = threading.Thread(target=exit_all, daemon=True)
    thread.start()
    other.start()
    thread.join(30)
    other.join(30)
    code, body = out["answer"]
    assert code == 200 and body.get("orderid"), body
    bought = [qty for side, qty in _sent(broker, X) if side == "BUY"]
    assert bought == [130 - read["held"]]
    code, body = exiting["answer"]
    assert (code, body["status"]) == (200, "success"), body
    assert _exit_all_outcome(body, X) == ["sent"]
