"""DL1: the sandbox's daily loss figure is worked out from its rows as they stand.

The sandbox stores a position's P&L only when its positions page marks it, and a fill leaves
the stored figure as it was, for the old quantity. Seen on 9 Nov: the card read +8,601.80 at
11:20:42, 11:22:00 and 11:22:26 while the option moved, and became right after the Positions page
was opened. v0.6.1's first fix marked the positions before reading and shared the marking for a
second; a partial exit inside that second was then counted twice (review of v0.6.1, HIGH-1):
SELL 50 of a long 100, then BUY 1 0.3 s later, read -15,000 against a true -10,000 and locked
the sandbox's day. Now nothing is written: each figure reads the rows afresh and works out their
P&L at the sandbox's own prices, and only the prices Upstox answered are shared for a second.

Real sandbox tables, OrderManager and ExecutionEngine on a throwaway database; only the quotes
are fixed. No broker.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_guard_sandbox import USER, _place, sandbox  # noqa: F401 - the fixture

from sudarshan.guard import daily, readers

LIMIT = 12_000.0
JOIN_S = 20.0


@pytest.fixture(autouse=True)
def _fresh_prices():
    """No price kept from another test (getattr: a build without the shared prices still runs
    every test to its own assertion)."""
    kept = getattr(readers, "_sandbox_prices", None)
    if kept is not None:
        kept.clear()
    yield
    if kept is not None:
        kept.clear()


def _a_second_later() -> None:
    """More than a second since the last figure: nothing shared is used any more (a build that
    shared a whole marking, ``_sandbox_marked``, forgets it too, so these tests fail there on
    the figure itself)."""
    for name in ("_sandbox_prices", "_sandbox_marked"):
        kept = getattr(readers, name, None)
        if kept is not None:
            kept.clear()


def _prices(monkeypatch, price: dict, *, ticks: bool = True) -> list:
    """The sandbox's fills and its two price sources at ``price["ltp"]``: the feed's ticks
    (``ticks``), else multiquotes. Returns the multiquotes reads made."""
    from sandbox.execution_engine import ExecutionEngine
    from sandbox.position_manager import PositionManager

    asked: list = []

    def quote(self, symbol, exchange):
        p = price["ltp"]
        return {"ltp": p, "bid": p, "ask": p, "high": p, "low": p}

    def batch(self, symbols):
        asked.append(list(symbols))
        return {s: {"ltp": price["ltp"]} for s in symbols}

    monkeypatch.setattr(ExecutionEngine, "_fetch_quote", quote)
    monkeypatch.setattr(
        PositionManager,
        "_fetch_quotes_from_websocket",
        lambda self, symbols: {s: {"ltp": price["ltp"]} for s in symbols} if ticks else {},
    )
    monkeypatch.setattr(PositionManager, "_fetch_quotes_batch", batch)
    return asked


def _quiet_announcements(monkeypatch) -> list:
    import database.user_db as user_db
    import extensions
    from sudarshan.guard import killswitch

    told: list = []
    monkeypatch.setattr(killswitch, "_tell", lambda owner, text: told.append(text))
    monkeypatch.setattr(user_db, "find_user_by_username", lambda: SimpleNamespace(username="o"))
    monkeypatch.setattr(extensions, "emit_from_any_thread", lambda *a, **k: None)
    return told


def _stored() -> SimpleNamespace:
    from database.sandbox_db import SandboxFunds, SandboxPositions, db_session

    db_session.remove()
    row = SandboxPositions.query.filter_by(user_id=USER, symbol="SBIN", product="MIS").first()
    funds = SandboxFunds.query.filter_by(user_id=USER).first()
    return SimpleNamespace(
        quantity=row.quantity, pnl=float(row.pnl), unrealized=float(funds.unrealized_pnl)
    )


def test_a_partial_exit_is_counted_once_in_the_next_figure(sandbox, monkeypatch):  # noqa: F811
    price = {"ltp": 800.0}
    _prices(monkeypatch, price)
    assert _place("BUY", 100)[0]
    price["ltp"] = 700.0
    _a_second_later()
    before = datetime.now(UTC)
    first = readers.sandbox_day(USER)
    assert first.total == -10_000.0
    assert first.marked_at is not None and first.marked_at >= before
    assert _place("SELL", 50)[0]  # half out at 700: -5,000 realised, 50 left
    second = readers.sandbox_day(USER)  # at once: no figure from before the fill
    assert (second.realised, second.unrealised) == (-5_000.0, -5_000.0)
    assert second.total == -10_000.0  # not -15,000


def test_a_partial_exit_then_an_entry_never_locks_the_day_on_a_doubled_loss(
    sandbox,  # noqa: F811
    monkeypatch,
    risk_store,
):
    """The review's scenario: limit 12,000, long 100 at 800, price 700 (truth -10,000). SELL 50
    is sent and filled; a BUY 1 right after was judged on -15,000, locked the day and told
    Telegram. It goes now, and nothing is locked or told."""
    price = {"ltp": 800.0}
    _prices(monkeypatch, price)
    told = _quiet_announcements(monkeypatch)
    risk_store.save_settings("owner", daily_loss_limit=LIMIT)
    monkeypatch.setattr(sandbox, "_in_session", lambda exchange, at: True)
    assert _place("BUY", 100)[0]
    price["ltp"] = 700.0
    _a_second_later()  # the exit comes more than a second after the BUY's own check
    out = _place("SELL", 50)  # an exit
    assert out[0], out
    entry = _place("BUY", 1)  # the next order, within the second
    assert entry[0] and entry[2] == 200, entry
    today = daily.day_of(datetime.now(UTC)).isoformat()
    assert risk_store.day_lock("sandbox", today) is None
    assert told == []
    assert abs(readers.sandbox_day(USER).total + 10_000.0) < 1.0  # the BUY 1 moved the average


def test_a_read_between_a_fills_two_commits_waits_for_the_second(sandbox, monkeypatch):  # noqa: F811
    """A fill that reduces a position commits its realised P&L to the funds first and the new
    quantity after, under the execution engine's lock. A figure read in between counted the
    closed half twice; it waits for the second commit instead."""
    from sandbox.fund_manager import FundManager

    price = {"ltp": 800.0}
    _prices(monkeypatch, price)
    assert _place("BUY", 100)[0]
    price["ltp"] = 700.0
    booked, go = threading.Event(), threading.Event()
    original = FundManager.release_margin

    def release_then_pause(self, *args, **kwargs):
        done = original(self, *args, **kwargs)  # the funds' commit: -5,000 realised
        booked.set()
        go.wait(JOIN_S)  # the position still says 100
        return done

    monkeypatch.setattr(FundManager, "release_margin", release_then_pause)
    seller = threading.Thread(target=lambda: _place("SELL", 50))
    seller.start()
    assert booked.wait(JOIN_S)
    _a_second_later()
    seen: list = []
    reader = threading.Thread(target=lambda: seen.append(readers.sandbox_day(USER)))
    reader.start()
    reader.join(0.5)  # it reads now, or waits for the fill
    go.set()
    seller.join(JOIN_S)
    reader.join(JOIN_S)
    assert not seller.is_alive() and not reader.is_alive()
    assert len(seen) == 1
    assert seen[0].total == -10_000.0  # not -15,000


def test_the_figure_is_worked_out_and_nothing_is_written(sandbox, monkeypatch):  # noqa: F811
    price = {"ltp": 800.0}
    _prices(monkeypatch, price)
    assert _place("BUY", 100)[0]
    price["ltp"] = 700.0
    _a_second_later()
    before = _stored()
    assert readers.sandbox_day(USER).total == -10_000.0
    assert _stored() == before  # the stored P&L and the funds' unrealised stay as they were


def test_a_short_is_worked_out_by_the_sandboxs_arithmetic_and_contract_value(
    sandbox,  # noqa: F811
    monkeypatch,
):
    from sandbox.position_manager import PositionManager

    price = {"ltp": 800.0}
    _prices(monkeypatch, price)
    assert _place("SELL", 10)[0]  # short 10 at 800
    price["ltp"] = 750.0
    _a_second_later()
    assert readers.sandbox_day(USER).total == 500.0
    monkeypatch.setattr(PositionManager, "_get_contract_value", lambda self, s, e: Decimal("0.5"))
    assert readers.sandbox_day(USER).total == 250.0


def test_only_upstoxs_prices_are_shared_for_a_second_never_the_rows(sandbox, monkeypatch):  # noqa: F811
    """No tick from the feed: multiquotes prices the figure. Two figures in the same second ask
    it once, and a fill between them still counts in the second."""
    price = {"ltp": 800.0}
    asked = _prices(monkeypatch, price, ticks=False)
    assert _place("BUY", 100)[0]
    price["ltp"] = 700.0
    _a_second_later()
    assert readers.sandbox_day(USER).total == -10_000.0
    assert _place("SELL", 50)[0]
    assert readers.sandbox_day(USER).total == -10_000.0
    assert asked == [[("SBIN", "NSE")]]
    _a_second_later()  # asked again
    assert readers.sandbox_day(USER).total == -10_000.0
    assert len(asked) == 2


def test_an_expired_contract_is_valued_at_its_settlement_price_with_no_quote(
    sandbox,  # noqa: F811
    monkeypatch,
):
    """An option past its expiry that the sandbox has not settled yet: its settlement will use
    its last price (the default), so the figure does too, and asks no quote for it."""
    from database.sandbox_db import SandboxPositions, db_session

    price = {"ltp": 800.0}
    asked = _prices(monkeypatch, price, ticks=False)
    db_session.add(
        SandboxPositions(
            user_id=USER, symbol="NIFTY28OCT2525900CE", exchange="NFO", product="NRML",
            quantity=65, average_price=Decimal("100"), ltp=Decimal("120"), pnl=Decimal("0"),
            pnl_percent=Decimal("0"), accumulated_realized_pnl=Decimal("0"),
            margin_blocked=Decimal("0"),
        )
    )  # fmt: skip
    db_session.commit()
    db_session.remove()
    assert readers.sandbox_day(USER).total == 1_300.0  # (120 - 100) x 65
    assert asked == []
    db_session.remove()
    row = SandboxPositions.query.filter_by(user_id=USER, symbol="NIFTY28OCT2525900CE").first()
    assert row.quantity == 65  # left to the sandbox's own settlement: the figure settles nothing


def test_a_fill_while_the_prices_are_read_leaves_no_mark_and_the_figure_true(
    sandbox,  # noqa: F811
    monkeypatch,
):
    """Review of v0.6.1, MEDIUM-1: the marking loaded the rows, waited on its quotes (a REST
    call yields under eventlet; under gthread another order simply runs) and wrote P&L from the
    quantity it had loaded, so a fill landing in that wait left a mark for the old quantity in
    the table (50 held, P&L -10,000 worked out for 100). The rows are read once now, and nothing
    is written."""
    from sandbox.position_manager import PositionManager

    price = {"ltp": 800.0}
    _prices(monkeypatch, price)
    assert _place("BUY", 100)[0]
    price["ltp"] = 700.0
    _a_second_later()
    fired = {"done": False}

    def ticks_with_a_fill_meanwhile(self, symbols):
        if not fired["done"]:
            fired["done"] = True
            other = threading.Thread(target=lambda: _place("SELL", 50))  # its own session
            other.start()
            other.join(JOIN_S)
        return {s: {"ltp": price["ltp"]} for s in symbols}

    monkeypatch.setattr(
        PositionManager, "_fetch_quotes_from_websocket", ticks_with_a_fill_meanwhile
    )
    figure = readers.sandbox_day(USER)
    stored = _stored()
    assert fired["done"] and stored.quantity == 50
    assert stored.pnl == 0.0  # the fill's own figure, never one worked out for 100
    assert figure.total == -10_000.0  # the rows as read: the truth before the fill and after it


def test_a_position_with_no_price_now_makes_the_figure_unreadable_never_the_old_one(
    sandbox,  # noqa: F811
    monkeypatch,
):
    """Review of v0.6.1, MEDIUM-2: no tick for 5 s and multiquotes refused, busy or timed out:
    the marking kept the old mark (-10,000 at 700) and said "read at" now while the market had
    fallen to 600 (truth -20,000). With no price for an open position the figure is unreadable."""
    from sandbox.position_manager import PositionManager

    price = {"ltp": 800.0}
    _prices(monkeypatch, price)
    assert _place("BUY", 100)[0]
    price["ltp"] = 700.0
    _a_second_later()
    assert readers.sandbox_day(USER).total == -10_000.0
    price["ltp"] = 600.0  # the market falls further, but no price can be had now
    monkeypatch.setattr(PositionManager, "_fetch_quotes_from_websocket", lambda self, s: {})
    monkeypatch.setattr(PositionManager, "_fetch_quotes_batch", lambda self, s: {})
    _a_second_later()
    with pytest.raises(readers.Unreadable):
        readers.sandbox_day(USER)


def test_with_no_price_new_orders_pause_and_exits_go_and_the_screens_hear_why(
    sandbox,  # noqa: F811
    monkeypatch,
    risk_store,
):
    from sandbox.position_manager import PositionManager
    from sudarshan.guard import words

    price = {"ltp": 800.0}
    _prices(monkeypatch, price)
    _quiet_announcements(monkeypatch)
    risk_store.save_settings("owner", daily_loss_limit=LIMIT)
    monkeypatch.setattr(sandbox, "_in_session", lambda exchange, at: True)
    assert _place("BUY", 100)[0]
    monkeypatch.setattr(PositionManager, "_fetch_quotes_from_websocket", lambda self, s: {})
    monkeypatch.setattr(PositionManager, "_fetch_quotes_batch", lambda self, s: {})
    _a_second_later()
    ok, body, status = _place("BUY", 1)
    assert (ok, status) == (False, 403)
    assert body["message"] == words.daily_unreadable("sandbox")
    assert sandbox.day_unchecked("sandbox") and sandbox.day_unchecked_paused("sandbox")
    assert _place("SELL", 50)[0]  # an exit goes


def test_an_exit_never_reads_the_figure_and_an_entry_does(sandbox, monkeypatch, risk_store):  # noqa: F811
    """Review of v0.6.1, MEDIUM-3: the figure was read for every order, exits too, inside the
    sandbox position's lock (a GTT leg fired on a tick held the feed's drain thread for it). An
    exit always goes, so it reads nothing for the daily limit now."""
    from sandbox import position_locks

    price = {"ltp": 800.0}
    _prices(monkeypatch, price)
    _quiet_announcements(monkeypatch)
    risk_store.save_settings("owner", daily_loss_limit=LIMIT)
    monkeypatch.setattr(sandbox, "_in_session", lambda exchange, at: True)
    original = readers.sandbox_day
    read: list = []

    def counted(user):
        read.append(position_locks.held_position_count())
        return original(user)

    monkeypatch.setattr(readers, "sandbox_day", counted)
    assert _place("BUY", 100)[0]
    assert read == [1]  # an entry: read, inside its position's lock
    assert _place("SELL", 50)[0]
    assert _place("SELL", 50)[0]  # the rest
    assert read == [1]  # two exits: never read
    assert _place("BUY", 1)[0]
    assert len(read) == 2


def test_the_sandbox_price_read_is_cut_off_like_every_other_gate_read(sandbox, monkeypatch):  # noqa: F811
    """Its multiquotes read honours the gate's 4 s limit (and is never retried), never the
    shared client's 120 s."""
    from broker.upstox.api import order_api
    from sandbox.position_manager import PositionManager

    price = {"ltp": 800.0}
    _prices(monkeypatch, price, ticks=False)
    assert _place("BUY", 10)[0]
    limits: list = []

    def batch(self, symbols):
        limits.append(order_api._READ_LIMIT.get())
        return {s: {"ltp": price["ltp"]} for s in symbols}

    monkeypatch.setattr(PositionManager, "_fetch_quotes_batch", batch)
    _a_second_later()
    readers.sandbox_day(USER)
    assert limits == [readers.READ_LIMIT_S]
    assert order_api._READ_LIMIT.get() is None  # and only for that read
