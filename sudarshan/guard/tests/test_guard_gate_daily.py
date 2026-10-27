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
from test_guard_gate import CE, JOIN_S, ON, PE, WAIT, Book, broker, order, refused_message, row

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


def test_a_two_leg_gtt_is_refused_when_the_figure_cannot_be_read(day):
    """New risk whatever the book says, so the figure decides, and it cannot be read."""
    day.book.fail.add("rows")
    upstox = broker()
    answer = upstox.gtt_place(order("BUY", symbol=PE, trigger_type="OCO"), "token")
    assert refused_message(answer) == words.daily_unreadable("live")


def test_a_book_up_to_fifteen_seconds_old_serves_the_figure(day, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        gate, "time", SimpleNamespace(monotonic=lambda: clock["now"], time=lambda: clock["now"])
    )
    day.book.rows = [day_row(CE, 65, -100.0, 0.0)]
    upstox = broker()
    assert upstox.place(order("BUY", symbol=PE), "token")[2] == "OID0"  # the book is read
    day.book.fail.add("rows")
    clock["now"] += 10
    gtt = order("BUY", symbol=PE, trigger_type="OCO")
    assert upstox.gtt_place(gtt, "token")[2] == "GTT-1"  # the figure from the 10 s old book
    clock["now"] += 6
    assert refused_message(upstox.gtt_place(gtt, "token")) == words.daily_unreadable("live")


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
    """The sandbox's figure: today's realised P&L in its funds and its open positions' P&L."""
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

    db = SimpleNamespace(
        SandboxFunds=Model([SimpleNamespace(today_realized_pnl=Decimal("-1250.50"))]),
        SandboxPositions=Model([SimpleNamespace(pnl=Decimal("-300")), SimpleNamespace(pnl=None)]),
    )
    monkeypatch.setattr(readers, "_sandbox", lambda: db)
    figure = readers.sandbox_day("owner")
    assert (figure.realised, figure.unrealised) == (-1250.5, -300.0)


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
