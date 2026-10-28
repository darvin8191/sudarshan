"""The daily loss limit in the gate (M5.14): the day locks at the limit, new risk is refused for
the rest of India's day while exits go, the figure is looked at only in its segment's session,
and an unreadable book never refuses an exit.

The Upstox book is the fake of test_guard_gate.py, its rows carrying Upstox's day figures.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_guard_gate import (  # noqa: F401 - clock: the fake clock fixture
    CE,
    JOIN_S,
    ON,
    PE,
    WAIT,
    Book,
    broker,
    change,
    clock,
    order,
    refused_message,
    row,
)

from sudarshan.guard import daily, flag, gate, readers, words
from sudarshan.guard.store import KillSwitchState

LIMIT = 4000.0
OPEN = {"session": True}


def day_row(symbol: str, quantity: int, realised: float, unrealised: float) -> dict:
    return {**row(symbol, quantity), "realised": realised, "unrealised": unrealised}


@pytest.fixture
def day(risk_store, monkeypatch):
    import database.user_db as user_db
    import extensions
    from sudarshan.guard import killswitch

    book = Book()
    fresh = gate.Gate(store=risk_store, read=book)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    risk_store.save_settings("owner", daily_loss_limit=LIMIT)
    monkeypatch.setattr(fresh, "_in_session", lambda exchange, at: OPEN["session"])
    OPEN["session"] = True
    told: list[str] = []
    monkeypatch.setattr(killswitch, "_tell", lambda owner, text: told.append(text))
    monkeypatch.setattr(user_db, "find_user_by_username", lambda: SimpleNamespace(username="o"))
    shown: list[tuple] = []
    monkeypatch.setattr(extensions, "emit_from_any_thread", lambda *args, **kw: shown.append(args))
    return SimpleNamespace(gate=fresh, book=book, store=risk_store, told=told, shown=shown)


def today() -> str:
    return daily.day_of(datetime.now(UTC)).isoformat()


def test_above_the_limit_a_new_order_goes(day):
    day.book.rows = [day_row(CE, 65, -1000.0, -500.0)]
    upstox = broker()
    assert upstox.place(order("BUY", symbol=PE), "token")[2] == "OID0"
    assert day.store.day_lock("live", today()) is None and day.told == []


def test_at_the_limit_the_day_locks_and_new_risk_is_refused(day):
    day.book.rows = [day_row(CE, 65, -3000.0, -1000.0)]  # exactly the limit: reached
    upstox = broker()
    message = refused_message(upstox.place(order("BUY", symbol=PE), "token"))
    assert message.startswith(
        "New orders are refused for today: the day's loss of ₹4,000 has reached your daily "
        "limit of ₹4,000 (Settings > Risk)."
    )
    assert message.endswith("Exits and cancels still go. New orders open again tomorrow.")
    assert upstox.reached == []
    lock = day.store.day_lock("live", today())
    assert (lock.figure, lock.loss_limit) == (-4000.0, LIMIT)
    assert day.told == [words.daily_lock_told(-4000.0, LIMIT, "live")]
    assert day.shown == [("sudarshan_risk_day", {"pipe": "live", "locked": True})]
    audit = [entry for entry in day.store.history() if entry.kind == "daily_lock"]
    assert len(audit) == 1 and audit[0].pipe == "live"


def test_once_locked_an_exit_goes_and_the_lock_holds_whatever_follows(day):
    day.book.rows = [day_row(CE, 65, -4200.0, 0.0)]
    upstox = broker()
    refused_message(upstox.place(order("BUY", symbol=PE), "token"))
    assert upstox.place(order("SELL"), "token")[2] == "OID0"  # the exit of the CE long
    day.book.rows = [day_row(CE, 0, 1500.0, 0.0)]  # the day recovers: the lock stays
    refused_message(upstox.place(order("BUY", symbol=PE), "token"))
    day.store.save_settings("owner", daily_loss_limit=None)  # turned off: today stays locked
    refused_message(upstox.place(order("BUY", symbol=PE), "token"))
    OPEN["session"] = False  # after the close: an evening order is refused too
    refused_message(upstox.place(order("BUY", symbol=PE), "token"))
    assert len(day.told) == 1


def test_a_lock_from_another_day_does_not_hold(day):
    yesterday = (datetime.now(UTC) - timedelta(days=1)).astimezone(daily.IST).date().isoformat()
    day.store.lock_day("live", yesterday, -9000.0, LIMIT)
    day.book.rows = [day_row(CE, 65, -100.0, 0.0)]
    assert broker().place(order("BUY", symbol=PE), "token")[2] == "OID0"


def test_outside_its_segment_s_session_the_figure_is_not_looked_at(day):
    """After the close (or before the open, when Upstox's book still shows the day before),
    a breach is not written; a lock already written still holds."""
    OPEN["session"] = False
    day.book.rows = [day_row(CE, 65, -9000.0, 0.0)]
    upstox = broker()
    assert upstox.place(order("BUY", symbol=PE), "token")[2] == "OID0"
    assert day.store.day_lock("live", today()) is None


def test_two_orders_at_the_breach_write_one_lock_and_both_are_refused(day):
    """Barrier: both read the book at the limit before either writes the lock."""
    day.book.rows = [day_row(CE, 65, -5000.0, 0.0)]
    both_read = threading.Barrier(2)
    day.book.before_rows = lambda: both_read.wait(WAIT)
    upstox = broker()
    answers = []

    def send():
        answers.append(upstox.place(order("BUY", symbol=PE), "token")[0].status)

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_S)
    assert not any(thread.is_alive() for thread in threads)
    assert answers == [403, 403] and upstox.reached == []
    assert len(day.told) == 1
    assert len([entry for entry in day.store.history() if entry.kind == "daily_lock"]) == 1


def test_an_unreadable_book_never_refuses_an_order_it_cannot_judge(day):
    """It may be an exit: under the daily limit alone it goes, noted, and the banner says the
    limit is not being checked."""
    day.book.fail.add("rows")
    upstox = broker()
    assert upstox.place(order("SELL"), "token")[2] == "OID0"
    assert day.gate.day_unchecked("live")
    day.gate.flush_notes()
    audit = [entry for entry in day.store.history() if entry.kind == "guard_unavailable"]
    assert audit and audit[0].summary == words.daily_not_checked("live")


def test_the_screens_hear_once_that_the_daily_limit_is_not_being_checked(
    day,
    clock,  # noqa: F811
):
    """Limits review M7: the banner says it while the owner trades, not on a reload."""
    day.book.fail.add("rows")
    upstox = broker()
    told = ("sudarshan_risk_day", {"pipe": "live", "unchecked": True})
    assert upstox.place(order("SELL"), "token")[2] == "OID0"  # goes, unjudged
    assert day.shown == [told]
    clock.now += 30
    assert upstox.place(order("SELL"), "token")[2] == "OID1"
    oco = order("BUY", symbol=PE, trigger_type="OCO")
    refused_message(upstox.gtt_place(oco, "token"))  # its figure cannot be read either
    assert day.shown == [told]  # still being said: not told again
    clock.now += gate.UNCHECKED_SHOWN_S + 1
    assert not day.gate.day_unchecked("live")
    refused_message(upstox.gtt_place(oco, "token"))
    assert day.shown == [told, told]
    assert day.gate.day_unchecked("live")


def test_a_two_leg_gtt_is_refused_when_the_figure_cannot_be_read(day):
    """New risk whatever the book says, so the figure decides, and it cannot be read."""
    day.book.fail.add("rows")
    upstox = broker()
    answer = upstox.gtt_place(order("BUY", symbol=PE, trigger_type="OCO"), "token")
    assert refused_message(answer) == words.daily_unreadable("live")


def test_a_book_up_to_fifteen_seconds_old_serves_the_figure(day, clock):  # noqa: F811
    day.book.rows = [day_row(CE, 65, -100.0, 0.0)]
    upstox = broker()
    assert upstox.place(order("BUY", symbol=PE), "token")[2] == "OID0"  # the book is read
    day.book.fail.add("rows")
    clock.now += 10
    gtt = order("BUY", symbol=PE, trigger_type="OCO")
    assert upstox.gtt_place(gtt, "token")[2] == "GTT-1"  # the figure from the 10 s old book
    clock.now += 6
    assert refused_message(upstox.gtt_place(gtt, "token")) == words.daily_unreadable("live")


def test_on_a_locked_day_an_order_whose_book_cannot_be_read_is_refused(day):
    """Decision d (limits review M3): no positions, not even a book from the last 15 s, so what
    the gate cannot judge is refused, with the way to an urgent exit."""
    day.store.lock_day("live", today(), -5200.0, LIMIT)
    day.book.fail.add("rows")
    upstox = broker()
    message = refused_message(upstox.place(order("BUY", symbol=PE), "token"))
    assert message == words.book_unreadable("live", None, locked=(-5200.0, LIMIT))
    assert message.startswith(
        "This order was refused: new orders are refused for today, the day's loss of ₹5,200 "
        "having reached your daily limit of ₹4,000, and your positions could not be read from "
        "Upstox, so SUDARSHAN cannot tell whether it is an exit."
    )
    assert message.endswith("For an urgent exit, use the Upstox app.")
    assert upstox.reached == []


def test_on_a_locked_day_a_book_read_in_the_last_fifteen_seconds_judges_an_order(
    day,
    clock,  # noqa: F811
):
    day.store.lock_day("live", today(), -5200.0, LIMIT)
    day.book.rows = [day_row(CE, 65, -5200.0, 0.0)]
    upstox = broker()
    refused_message(upstox.place(order("BUY", symbol=PE), "token"))  # reads the book
    day.book.fail.add("rows")
    clock.now += gate.DAY_BOOK_REUSE_S - 1
    assert upstox.place(order("SELL"), "token")[2] == "OID0"  # the CE exit, on that book
    message = refused_message(upstox.place(order("BUY", symbol=PE), "token"))
    assert message.startswith("New orders are refused for today: the day's loss of ₹5,200")


def test_on_a_locked_day_a_change_whose_order_cannot_be_read_is_refused(day):
    day.store.lock_day("live", today(), -5200.0, LIMIT)
    day.book.fail.add("orders")
    body, status = broker().modify(change("E1", 65, 101), "token")
    assert status == 403
    assert body["message"] == words.order_not_found("live", None, locked=(-5200.0, LIMIT))


def test_the_figure_for_an_order_judged_without_the_book_is_read_afresh(
    day,
    clock,  # noqa: F811
):
    """A two-leg GTT reads no book to be judged, so its figure comes from a read of its own,
    never from a book another order read seconds ago (mutant G4)."""
    day.book.rows = [day_row(CE, 65, -1000.0, 0.0)]
    upstox = broker()
    assert upstox.place(order("BUY", symbol=PE), "token")[2] == "OID0"  # read: -1,000
    day.book.rows = [day_row(CE, 65, -4500.0, 0.0)]
    clock.now += 5
    oco = refused_message(upstox.gtt_place(order("BUY", symbol=PE, trigger_type="OCO"), "token"))
    assert oco.startswith("New orders are refused for today: the day's loss of ₹4,500")


def test_a_book_kept_for_the_figure_is_as_old_as_its_read(day, clock):  # noqa: F811
    """Gate review L6: a read that took 10 s, kept, is 16 s old 6 s after it answered, too old
    to serve the figure."""
    day.book.rows = [day_row(CE, 65, -3000.0, 0.0)]
    original = day.book.live_rows

    def slow(auth):
        clock.now += 10
        return original(auth)

    day.book.live_rows = slow
    upstox = broker()
    assert upstox.place(order("BUY", symbol=PE), "token")[2] == "OID0"  # read from 1000 to 1010
    day.book.live_rows = original
    day.book.fail.add("rows")
    clock.now += 6
    gtt = order("BUY", symbol=PE, trigger_type="OCO")
    assert refused_message(upstox.gtt_place(gtt, "token")) == words.daily_unreadable("live")


class TwoReads(Book):
    """Two orders reading the book at once, each its own answer (the barrier tests)."""

    def __init__(self, first: list, second: list):
        super().__init__()
        self.answers = [first, second]
        self.both_read = threading.Barrier(2)
        self.taking = threading.Lock()

    def live_rows(self, auth):
        self._read("rows")
        with self.taking:
            rows = self.answers.pop(0)
        self.both_read.wait(WAIT)
        return rows


def test_a_lock_that_cannot_be_saved_holds_once_with_the_first_figure(day, monkeypatch):
    """The risk database will not take the lock: it holds in this process, and the order that
    finds it written meanwhile names that lock's figure, not the one it read (mutant G14)."""
    reads = TwoReads([day_row(CE, 65, -4000.0, 0.0)], [day_row(CE, 65, -5000.0, 0.0)])
    monkeypatch.setattr(day.gate, "_read", reads)

    def no_disk(*args):
        raise OSError("disk")

    monkeypatch.setattr(day.store, "lock_day", no_disk)
    upstox = broker()
    said = []

    def send():
        gtt = order("BUY", symbol=PE, trigger_type="OCO")  # judged without the book
        said.append(refused_message(upstox.gtt_place(gtt, "token")))

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_S)
    assert not any(thread.is_alive() for thread in threads)
    assert len(said) == 2 and said[0] == said[1]  # one lock, one figure
    assert len(day.told) == 1


def test_on_a_locked_day_a_reversal_sends_its_exit_and_refuses_the_rest(day):
    """The coordinator's decision a, under the day's lock."""
    day.store.lock_day("live", today(), -5200.0, LIMIT)
    day.book.rows = [day_row(CE, 65, -5200.0, 0.0)]
    upstox = broker()
    res, body, ref = upstox.place(order("SELL", 100), "token")
    assert (res.status, ref) == (200, "OID0")
    assert [sent["quantity"] for _what, sent in upstox.reached] == ["65"]
    assert body["risk_note"] == (
        "Only the exit was sent: sell 65 to close NIFTY 25900 CE. The other 35, which would have "
        "opened it the other way, were refused: the day's loss of ₹5,200 has reached your daily "
        "limit of ₹4,000 (Settings > Risk). New orders open again tomorrow."
    )


def test_on_a_locked_day_an_exit_already_on_its_way_is_named(day, clock):  # noqa: F811
    """The gate review's L2: refused for its claims, an exit is told about the exit on its way,
    not that it is a new order."""
    day.store.lock_day("live", today(), -5200.0, LIMIT)
    day.book.rows = [day_row(CE, 65, -5200.0, 0.0)]
    upstox = broker()
    assert upstox.place(order("SELL", 65), "token")[2] == "OID0"
    said = refused_message(upstox.place(order("SELL", 65), "token"))
    assert said.startswith("An exit for NIFTY 25900 CE is already on its way (sent ")
    assert said.endswith(
        "New orders are refused for today: the day's loss of ₹5,200 has reached your daily limit "
        "of ₹4,000 (Settings > Risk), so nothing beyond the open position can be sent."
    )


def test_on_a_locked_day_a_wing_roll_is_sent_whole_or_not_at_all(day):
    """The gate review's M6: [sell the long PE wing, buy a further wing] sent leg by leg sold
    the wing and refused the new one, leaving the short PE uncovered. The batch is refused
    whole; its exit is named."""
    day.store.lock_day("live", today(), -5200.0, LIMIT)
    day.book.rows = [day_row(PE, 65, -5200.0, 0.0)]
    legs = [
        gate._upstox_place(order("SELL", 65, symbol=PE), "token"),  # closes the wing
        gate._upstox_place(order("BUY", 65, symbol=CE), "token"),  # a further wing: new risk
    ]
    found = day.gate.check_together(legs)
    assert found is not None
    index, decision = found
    assert index == 1 and decision.code == gate.DAILY_LOSS_LIMIT
    assert decision.detail["exits"] == [0]
    assert day.gate.check_together(legs[:1]) is None  # the exit alone goes


def test_a_batch_that_reaches_the_limit_locks_the_day_and_is_refused_whole(day):
    day.book.rows = [day_row(PE, 65, -4100.0, 0.0)]
    legs = [
        gate._upstox_place(order("SELL", 65, symbol=PE), "token"),
        gate._upstox_place(order("BUY", 65, symbol=CE), "token"),
    ]
    found = day.gate.check_together(legs)
    assert found is not None and found[1].code == gate.DAILY_LOSS_LIMIT
    assert day.store.day_lock("live", today()) is not None
    assert len(day.told) == 1


def test_a_delivery_dip_never_locks_the_day(day):
    """Shares bought today to hold (CNC, product D in cash) are not in the day's figure."""
    day.book.rows = [
        {"instrument_token": "NSE_EQ|R", "exchange": "NSE", "product": "D", "quantity": 400,
         "realised": 0.0, "unrealised": -4000.0},
        day_row(CE, 65, 0.0, -100.0),
    ]  # fmt: skip
    assert broker().place(order("BUY", symbol=PE), "token")[2] == "OID0"
    assert day.store.day_lock("live", today()) is None


def test_the_kill_switch_is_said_before_the_daily_limit(day):
    day.book.rows = [day_row(CE, 65, -5000.0, 0.0)]
    upstox = broker()
    refused_message(upstox.place(order("BUY", symbol=PE), "token"))
    day.gate.set_state(ON)
    message = refused_message(upstox.place(order("BUY", symbol=PE), "token"))
    assert message.startswith("New orders are refused: the kill switch is on")


def test_without_a_limit_nothing_more_is_read(day):
    """No limit and no lock today: an order costs the gate no read at all."""
    day.store.save_settings("owner", daily_loss_limit=None)
    upstox = broker()
    assert upstox.place(order("BUY", symbol=PE), "token")[2] == "OID0"
    assert day.book.calls == []


def test_settings_that_cannot_be_read_leave_the_daily_limit_unchecked(day, monkeypatch):
    def broken():
        raise OSError("disk")

    monkeypatch.setattr(day.store, "settings", broken)
    day.book.rows = [day_row(CE, 65, -9000.0, 0.0)]
    assert broker().place(order("BUY", symbol=PE), "token")[2] == "OID0"


def test_the_figure_is_read_from_the_same_book_the_order_is_judged_on(day):
    day.book.rows = [day_row(CE, 65, -1000.0, 0.0)]
    broker().place(order("BUY", symbol=PE), "token")
    assert day.book.calls.count("rows") == 1


def test_readers_sandbox_day_is_its_funds_and_open_positions(monkeypatch):
    """The sandbox's figure: today's realised P&L in its funds and its open positions' P&L, its
    delivery (CNC in cash) left out of both (the owner's decision)."""
    from decimal import Decimal

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def filter_by(self, **kwargs):
            return self

        def filter(self, *args):
            return self

        def populate_existing(self):
            return self

        def first(self):
            return self.rows[0] if self.rows else None

        def all(self):
            return self.rows

    class Model:
        def __init__(self, rows):
            self.query = Query(rows)
            self.user_id = self.quantity = 0  # compared, never used

    def position(product, exchange, quantity, pnl, booked):
        return SimpleNamespace(
            product=product, exchange=exchange, quantity=quantity, pnl=pnl,
            today_realized_pnl=booked,
        )  # fmt: skip

    db = SimpleNamespace(
        SandboxFunds=Model([SimpleNamespace(today_realized_pnl=Decimal("-1250.50"))]),
        SandboxPositions=Model(
            [
                position("MIS", "NFO", 65, Decimal("-300"), Decimal("0")),
                position("NRML", "NFO", 0, None, None),  # closed: nothing open
                position("CNC", "NSE", 400, Decimal("-4000"), Decimal("-250")),  # delivery
                position("CNC", "BSE", 0, Decimal("0"), Decimal("-100")),  # delivery, sold today
            ]
        ),
    )
    monkeypatch.setattr(readers, "_sandbox", lambda: db)
    figure = readers.sandbox_day("owner")
    assert (figure.realised, figure.unrealised) == (-900.5, -300.0)


def test_a_segment_s_window_is_kept_and_a_closed_day_is_asked_again(risk_store, monkeypatch):
    """The calendar answers "closed" and "could not read" the same way, so only an open
    window is kept; a closed day costs one calendar read per order (a weekend's few)."""
    import database.market_calendar_db as calendar

    asked = []
    windows = {"NFO": {"start_ms": 0, "end_ms": 2**62}, "NSE": None}

    def window(day, exchange):
        asked.append(exchange)
        return windows[exchange]

    monkeypatch.setattr(calendar, "get_effective_session_window", window)
    fresh = gate.Gate(store=risk_store)
    at = datetime.now(UTC)
    assert fresh._in_session("NFO", at) and fresh._in_session("NFO", at)
    assert not fresh._in_session("NSE", at) and not fresh._in_session("NSE", at)
    assert asked == ["NFO", "NSE", "NSE"]


def test_a_calendar_that_breaks_reads_as_in_session_from_india_s_morning(risk_store, monkeypatch):
    """Never in the hours after midnight, when Upstox's book can still show the day before."""
    import database.market_calendar_db as calendar

    def broken(day, exchange):
        raise OSError("disk")

    monkeypatch.setattr(calendar, "get_effective_session_window", broken)
    fresh = gate.Gate(store=risk_store)
    assert not fresh._in_session("NFO", datetime(2026, 10, 27, 3, 0, tzinfo=UTC))  # 08:30 IST
    assert fresh._in_session("NFO", datetime(2026, 10, 27, 6, 30, tzinfo=UTC))  # 12:00 IST


def test_the_figure_is_looked_at_by_the_order_s_own_segment(day, monkeypatch):
    """R11: F&O to 15:40, cash to 15:30. At 15:35 an NFO order still reads the day's figure;
    an NSE one does not."""
    monkeypatch.setattr(
        day.gate, "_in_session", lambda exchange, at: exchange in ("NFO", "BFO", "MCX")
    )
    day.book.rows = [day_row(CE, 65, -5000.0, 0.0)]
    upstox = broker()
    cash = {**order("BUY", symbol="INFY"), "exchange": "NSE", "product": "MIS"}
    assert upstox.place(cash, "token")[2] == "OID0"  # cash is closed: not looked at
    assert day.store.day_lock("live", today()) is None
    refused_message(upstox.place(order("BUY", symbol=PE), "token"))  # NFO: locks the day
    assert day.store.day_lock("live", today()) is not None


class SandboxBook:
    """The sandbox as the gate reads it: flat everywhere, and today's figure."""

    def __init__(self, figure):
        self.figure = figure
        self.calls: list[str] = []

    def sandbox_position(self, owner, symbol, exchange, product):
        self.calls.append("position")
        return 0

    def sandbox_day(self, owner):
        self.calls.append("day")
        return self.figure


def test_a_sandbox_order_at_the_limit_locks_the_sandbox_s_day_only(day, monkeypatch):
    book = SandboxBook(daily.DayFigure(-3000.0, -1500.0))
    fresh = gate.Gate(store=day.store, read=book)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(fresh, "_in_session", lambda exchange, at: True)
    kind = gate._Kind(
        gate.SANDBOX, gate._sandbox_place, gate._refuse_in_the_sandbox, gate._answer_in_the_sandbox
    )
    data = {"symbol": PE, "exchange": "NFO", "product": "MIS", "action": "BUY", "quantity": 65}
    decision = fresh.admit(kind, (SimpleNamespace(user_id="owner"), data), {})
    assert decision.refused and decision.code == gate.DAILY_LOSS_LIMIT
    assert book.calls == ["position", "day"]
    assert day.store.day_lock("sandbox", today()) is not None
    assert day.store.day_lock("live", today()) is None
    assert day.told == [words.daily_lock_told(-4500.0, LIMIT, "sandbox")]


def test_an_order_that_read_the_book_just_before_another_locked_the_day_is_refused(day):
    """A reads the book while the day is above the limit; B reads it at the limit and locks the
    day before A decides. A, deciding after the lock, is refused too: the lock holds from the
    moment it is written, not from the moment an order read the book."""
    above = [day_row(CE, 65, -1000.0, 0.0)]
    at_the_limit = [day_row(CE, 65, -4000.0, 0.0)]
    day.book.rows = above
    upstox = broker()
    ran = []

    def b_locks_the_day_first():
        if ran:
            return
        ran.append("B")
        day.book.rows = at_the_limit
        refused_message(upstox.place(order("BUY", symbol=PE), "token"))  # B: the day locks
        day.book.rows = above  # what A is about to read

    day.book.before_rows = b_locks_the_day_first
    refused_message(upstox.place(order("BUY", symbol=PE), "token"))  # A
    assert ran == ["B"] and upstox.reached == []
