"""M6.9 (O1): what a trader is told while a Live strategy exit is held, and how often.

Since GA1.1 an exit whose size Upstox's books cannot confirm is held, not sent. Before this
module the engine wrote an order row, a ``leg_exit_held`` event and a page push on every tick and
told nobody. ``sudarshan.books.held_exits`` decides, under one lock, what one hold calls for.
Telegram is faked by the folder's ``held_exit_messages`` fixture: no test here reaches a network.
"""

from __future__ import annotations

import re
import threading
from types import SimpleNamespace

import pytest

from sudarshan.books import exit_guard, held_exits, surface_exits
from sudarshan.books.held_exits import AGAIN, FIRST, GONE, ONCE, Holds, Note

X = "NIFTY27OCT2625900CE"
POSITION = ("NFO", X, "MIS")
ORDER = {
    "symbol": X,
    "exchange": "NFO",
    "action": "SELL",
    "quantity": "65",
    "product": "MIS",
    "strategy": "Iron fly",
}
KEY = (7, 1)


def _result(why: str, error: str = "sentence"):
    return SimpleNamespace(ok=False, held=True, error=error, response={"why": why})


# ---- the rules, on a clock the test holds ------------------------------------------------------
def test_first_hold_then_quiet_inside_a_minute_then_one_reminder_then_the_clear():
    holds = Holds()
    assert holds.note(KEY, "unconfirmed", 100.0) == Note(event=True, tell=FIRST)
    for at in (100.5, 102.0, 130.0, 159.9):  # every tick inside the minute: nothing
        assert holds.note(KEY, "unconfirmed", at) == Note()
    assert holds.note(KEY, "unconfirmed", 160.0) == Note(tell=AGAIN, minutes=1)
    assert holds.note(KEY, "unconfirmed", 219.0) == Note()
    assert holds.note(KEY, "unconfirmed", 220.0) == Note(tell=AGAIN, minutes=2)
    assert holds.clear(KEY, 230.0) == 2  # the exit went: he is told, once
    assert holds.clear(KEY, 231.0) is None and len(holds) == 0


def test_the_position_gone_after_an_alert_is_told_once_and_then_nothing():
    holds = Holds()
    holds.note(KEY, "unconfirmed", 0.0)
    assert holds.note(KEY, "no_position", 4.0) == Note(event=True, tell=GONE)
    for at in (6.0, 70.0, 250.0, 500.0, 740.0):  # the leg stays open in the run: never repeated
        assert holds.note(KEY, "no_position", at) == Note()


@pytest.mark.parametrize("why", ["no_position", "other_way", "elsewhere"])
def test_what_the_books_say_is_told_once_when_it_is_the_first_thing_known(why):
    holds = Holds()
    assert holds.note(KEY, why, 0.0) == Note(event=True, tell=ONCE)
    assert holds.note(KEY, why, 2.0) == Note()
    assert holds.note(KEY, why, 200.0) == Note()
    assert holds.clear(KEY, 201.0) is None  # no size alert went, so no "sent" message is owed


@pytest.mark.parametrize("why", ["busy", "earlier", "working", ""])
def test_a_wait_for_another_exit_is_one_event_and_no_message(why):
    holds = Holds()
    assert holds.note(KEY, why, 0.0) == Note(event=True)
    assert holds.note(KEY, why, 1.0) == Note()
    # the size then cannot be confirmed: that is the first size hold, and it is told
    assert holds.note(KEY, "unconfirmed", 2.0) == Note(event=True, tell=FIRST)


def test_a_leg_that_stopped_asking_is_forgotten_and_a_later_hold_is_a_first_hold():
    holds = Holds()
    holds.note(KEY, "unconfirmed", 0.0)
    holds.note((7, 2), "unconfirmed", 0.0)
    later = held_exits.STALE_S + 1.0
    assert holds.note(KEY, "unconfirmed", later) == Note(event=True, tell=FIRST)
    assert len(holds) == 1  # the other leg's record went with the sweep
    assert holds.run_is_held(7, later) and not holds.run_is_held(8, later)


def test_two_legs_are_told_apart():
    holds = Holds()
    assert holds.note((7, 1), "unconfirmed", 0.0).tell == FIRST
    assert holds.note((7, 2), "unconfirmed", 0.0).tell == FIRST


def test_ticks_racing_on_one_leg_send_one_first_message(held_exit_messages):
    """Eight ticks let go together by a barrier: one first message, one event."""
    threads, barrier, said = 8, threading.Barrier(8), []

    def tick():
        barrier.wait()
        said.append(held_exits.held(7, 1, "exit_sl", ORDER, _result("unconfirmed")))

    workers = [threading.Thread(target=tick) for _ in range(threads)]
    for each in workers:
        each.start()
    for each in workers:
        each.join(10)
    assert len(said) == threads
    assert len(held_exit_messages) == 1 and "Exit NOT sent" in held_exit_messages[0]
    assert sum(1 for each in said if each.event) == 1


def test_defect_without_the_lock_s_one_decision_every_tick_would_tell(held_exit_messages):
    """The old shape, asserted so the test above cannot pass vacuously: each tick deciding for
    itself (a fresh record each time, as with nothing remembered) tells every time."""
    for _ in range(8):
        held_exits.HOLDS.reset()
        held_exits.held(7, 1, "exit_sl", ORDER, _result("unconfirmed"))
    assert len(held_exit_messages) == 8


# ---- through held() and sent(), with the module's clock ----------------------------------------
@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(held_exits, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


def test_the_whole_story_of_one_hold(held_exit_messages, clock):
    told = held_exit_messages
    first = held_exits.held(7, 1, "exit_sl", ORDER, _result("unconfirmed"))
    assert first.event and first.severity == "critical"
    assert first.message == (
        "Exit NOT sent for leg 1 (SELL 65 NIFTY 25900 CE): SUDARSHAN could not confirm the "
        "position size from Upstox. It keeps trying and writes here when the exit goes. Close "
        "the position in the Upstox app if you want out now."
    )
    assert told == [
        "Exit NOT sent (Live)\nStrategy: Iron fly\nLeg 1: SELL 65 NIFTY 25900 CE\n"
        "The stop loss fired, but the exit was not sent because SUDARSHAN could not confirm the "
        "position size from Upstox. SUDARSHAN keeps trying and will tell you when the exit "
        "goes. If you want out now, close it in the Upstox app."
    ]
    clock[0] += 30
    assert held_exits.held(7, 1, "exit_sl", ORDER, _result("unconfirmed")).event is False
    assert len(told) == 1
    clock[0] += 31
    assert held_exits.held(7, 1, "exit_sl", ORDER, _result("unconfirmed")).event is False
    assert told[1] == (
        "Exit still NOT sent (Live), 1 min now\nStrategy: Iron fly\n"
        "Leg 1: SELL 65 NIFTY 25900 CE\n"
        "SUDARSHAN still cannot confirm the position size from Upstox. If you want out now, "
        "close it in the Upstox app."
    )
    clock[0] += 5
    sent = SimpleNamespace(ok=True, response={"orderid": "1", "sent_quantity": 30})
    assert held_exits.sent(7, 1, ORDER, sent) == "; it had been held 1 min"
    assert told[2] == (
        "Exit sent (Live)\nStrategy: Iron fly\nLeg 1: SELL 30 NIFTY 25900 CE\n"
        "The exit that was held for 1 min has now been sent. Check Orders for its fill."
    )
    assert held_exits.sent(7, 1, ORDER, sent) == "" and len(told) == 3


def test_a_leg_the_account_no_longer_holds_says_so(held_exit_messages, clock):
    sentence = exit_guard.nothing_left_to_close(POSITION)
    said = held_exits.held(7, 1, "exit_trail", ORDER, _result("no_position", sentence))
    assert said.severity == "warn" and said.message == (
        "No exit sent for leg 1 (NIFTY 25900 CE): Upstox shows no position for this leg. The "
        "run still shows the leg as open; check Positions in the Upstox app."
    )
    assert held_exit_messages == [
        "Exit NOT sent (Live)\nStrategy: Iron fly\nLeg 1: SELL 65 NIFTY 25900 CE\n"
        "The trailing stop fired, but Upstox shows no position for this leg, so no exit was "
        "sent. The run still shows the leg as open; check Positions in the Upstox app."
    ]
    for _ in range(30):  # an hour of ticks: the leg stays open in the run, nothing is repeated
        clock[0] += 120
        assert not held_exits.held(7, 1, "exit_trail", ORDER, _result("no_position")).event
    assert len(held_exit_messages) == 1


def test_gone_after_a_size_alert_reads_as_the_clear(held_exit_messages, clock):
    held_exits.held(7, 1, "exit_sl", ORDER, _result("unconfirmed"))
    clock[0] += 10
    said = held_exits.held(7, 1, "exit_sl", ORDER, _result("no_position"))
    assert said.event and held_exit_messages[1] == (
        "Nothing left to close (Live)\nStrategy: Iron fly\nLeg 1: NIFTY 25900 CE\n"
        "Upstox shows no position for this leg now, so no exit was sent and none is needed. "
        "The run still shows the leg as open; check Positions in the Upstox app."
    )


def test_held_in_another_product_is_told_in_the_books_own_words(held_exit_messages):
    sentence = exit_guard.held_in_another_product(POSITION, {"NRML": 65})
    said = held_exits.held(7, 1, "exit_eod", ORDER, _result("elsewhere", sentence))
    assert said.severity == "critical" and said.message.endswith(sentence)
    assert held_exit_messages[0].endswith(
        f"The end-of-day square-off was due, but the exit was not sent. {sentence}"
    )


_NOT_FOR_A_TRADER = re.compile(
    r"\b(4\d\d|5\d\d)\b|Error|Exception|Traceback|timed out|HTTP|\barm", re.I
)


@pytest.mark.parametrize("kind", [*held_exits._DUE, "exit_recovery"])
def test_every_message_is_for_a_trader(kind):
    texts = [
        held_exits.first_text(1, kind, ORDER),
        held_exits.again_text(1, ORDER, 0),
        held_exits.again_text(1, ORDER, 12),
        held_exits.once_text(1, kind, ORDER, "no_position", ""),
        held_exits.gone_text(1, ORDER),
        held_exits.sent_text(1, ORDER, None, 0),
        *(
            held_exits.event_line(1, ORDER, why, "Said.")[0]
            for why in ("unconfirmed", "no_position")
        ),
    ]
    for text in texts:
        assert not _NOT_FOR_A_TRADER.search(text), text
        assert "NIFTY 25900 CE" in text and ("Leg 1" in text or "leg 1" in text)
    for text in texts[:6]:
        assert "Strategy: Iron fly" in text
    assert "Upstox app" in texts[0] and "could not confirm the position size" in texts[0]


def test_a_failure_to_tell_never_reaches_the_engine(monkeypatch):
    def broken(text):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr(held_exits, "_tell", broken)
    said = held_exits.held(7, 1, "exit_sl", ORDER, _result("unconfirmed"))
    assert said.event is True  # recorded as a hold, as before
    assert held_exits.sent(7, 1, ORDER, SimpleNamespace(ok=True, response={})) == ""


def test_telling_goes_through_the_risk_guard_s_queue_and_never_raises(monkeypatch):
    """The real ``_tell`` (the fixture's fake undone): handed to ``gate._tell_owner``, which
    queues on OpenAlgo's alert executor; a failure there is swallowed."""
    from sudarshan.guard import gate

    monkeypatch.undo()
    heard = []
    monkeypatch.setattr(gate, "_tell_owner", heard.append)
    held_exits._tell("one")
    assert heard == ["one"]

    def broken(text):
        raise RuntimeError("no bot")

    monkeypatch.setattr(gate, "_tell_owner", broken)
    held_exits._tell("two")  # does not raise


# ---- the kept refusal, and why ----------------------------------------------------------------
def _close(book, quantity="65", side="SELL"):
    sent = []

    def send():
        sent.append(1)
        return True, {"status": "success", "orderid": "9"}, 200

    answer = surface_exits.exit_once(
        "live", *POSITION, send, side=side, quantity=quantity,
        read_book=lambda *a: book, resize=lambda q: None,
    )  # fmt: skip
    return answer, sent


@pytest.mark.parametrize(
    ("book", "why", "status"),
    [
        (surface_exits.Book(positions_read=False, orders_read=False), "unconfirmed", 424),
        (surface_exits.Book(orders_read=False), "unconfirmed", 424),
        (surface_exits.Book(held=None), "unconfirmed", 424),
        (surface_exits.Book(held=0), "no_position", 409),
        (surface_exits.Book(held=-65), "other_way", 409),
        (surface_exits.Book(held=0, elsewhere={"NRML": 65}), "elsewhere", 409),
    ],
)
def test_a_sizing_refusal_says_why_and_is_kept_two_seconds(book, why, status):
    assert surface_exits.kept_refusal("live", *POSITION, "SELL") is None
    (ok, body, code), sent = _close(book)
    assert (ok, code, sent) == (False, status, []) and body["why"] == why
    kept = surface_exits.kept_refusal("live", *POSITION, "sell")
    assert kept == (ok, body, code)
    assert surface_exits.kept_refusal("live", *POSITION, "BUY") is None  # the other side
    assert surface_exits.kept_refusal("sandbox", *POSITION, "SELL") is None


def test_a_kept_refusal_ends_with_its_two_seconds(monkeypatch):
    _close(surface_exits.Book(held=None))
    real = surface_exits.time.monotonic
    monkeypatch.setattr(
        surface_exits.time, "monotonic", lambda: real() + surface_exits.SIZING_REFUSAL_KEPT_S + 0.1
    )
    assert surface_exits.kept_refusal("live", *POSITION, "SELL") is None


def test_a_close_that_goes_keeps_nothing():
    (ok, _body, _code), sent = _close(surface_exits.Book(held=65))
    assert ok and sent == [1]
    assert surface_exits.kept_refusal("live", *POSITION, "SELL") is None
