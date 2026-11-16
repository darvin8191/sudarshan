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
#: V1: the one sentence that says how to get out by hand, the same in all three places.
HOW = (
    "If you want out now, close it in the Upstox app with a market order; SUDARSHAN then sends "
    "nothing. Do not leave a limit order waiting there: SUDARSHAN's exit can still go, and both "
    "would fill."
)


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
    # v0.15.0 verify V3: he was told no exit went, so he is told when one does (before: None,
    # and his last message said no exit was sent while one had gone)
    assert holds.clear(KEY, 201.0) == 3


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
        f"position size from Upstox. It keeps trying and writes here when the exit goes. {HOW}"
    )
    assert told == [
        "Exit NOT sent (Live)\nStrategy: Iron fly\nLeg 1: SELL 65 NIFTY 25900 CE\n"
        "The stop loss fired, but the exit was not sent because SUDARSHAN could not confirm the "
        "position size from Upstox. SUDARSHAN keeps trying and will tell you when the exit "
        f"goes. {HOW}"
    ]
    clock[0] += 30
    assert held_exits.held(7, 1, "exit_sl", ORDER, _result("unconfirmed")).event is False
    assert len(told) == 1
    clock[0] += 31
    assert held_exits.held(7, 1, "exit_sl", ORDER, _result("unconfirmed")).event is False
    assert told[1] == (
        "Exit still NOT sent (Live), 1 min now\nStrategy: Iron fly\n"
        "Leg 1: SELL 65 NIFTY 25900 CE\n"
        f"SUDARSHAN still cannot confirm the position size from Upstox. {HOW}"
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


def test_v1_all_three_places_say_market_order_and_never_a_waiting_limit_order():
    """V1: sent to the Upstox app with no more said, he could leave a LIMIT close there, which
    the books do not count as an exit on its way; SUDARSHAN's exit and his could both fill."""
    places = [
        held_exits.first_text(1, "exit_sl", ORDER),
        held_exits.again_text(1, ORDER, 3),
        held_exits.event_line(1, ORDER, "unconfirmed", "")[0],
    ]
    for text in places:
        assert text.endswith(HOW), text
        assert "with a market order; SUDARSHAN then sends nothing" in text
        assert "Do not leave a limit order waiting there" in text
    assert held_exits.CLOSE_IN_UPSTOX == HOW


# ---- V2: the reminder backs off ----------------------------------------------------------------
def test_v2_the_reminder_goes_each_minute_then_each_five_then_each_fifteen():
    """A tick every 2 s for three hours, on a clock the test holds: the minutes at which a
    reminder goes."""
    holds, told = Holds(), []
    assert holds.note(KEY, "unconfirmed", 0.0).tell == FIRST
    for tick in range(1, 3 * 3600 // 2 + 1):
        note = holds.note(KEY, "unconfirmed", tick * 2.0)
        if note.tell:
            assert note.tell == AGAIN and note.minutes * 60 == tick * 2
            told.append(note.minutes)
    assert told == [1, 2, 3, 4, 5, 10, 15, 20, 25, 30, 45, 60, 75, 90, 105, 120, 135, 150, 165, 180]


def _reminders_in_a_session() -> int:
    """Reminders over a session of 6 h 15 min, a tick every 2 s, the size never confirmed."""
    holds = Holds()
    holds.note(KEY, "unconfirmed", 0.0)
    return sum(bool(holds.note(KEY, "unconfirmed", float(at)).tell) for at in range(2, 22501, 2))


def test_v2_a_whole_session_is_33_reminders(monkeypatch):
    assert _reminders_in_a_session() == 33
    # The old rule, a minute apart all session, asserted so the count above cannot pass
    # vacuously: the verifier's "about 370 messages".
    monkeypatch.setattr(held_exits, "REMIND", ())
    monkeypatch.setattr(held_exits, "REMIND_LATE_S", 60.0)
    assert _reminders_in_a_session() == 375


def test_v2_sent_and_nothing_left_still_go_at_once_however_long_the_gap(held_exit_messages, clock):
    for ends in ("sent", "gone"):
        held_exits.HOLDS.reset()
        held_exit_messages.clear()
        held_exits.held(7, 1, "exit_sl", ORDER, _result("unconfirmed"))
        for _ in range(40 * 30):  # 40 minutes of ticks: well inside a 15 minute gap
            clock[0] += 2
            held_exits.held(7, 1, "exit_sl", ORDER, _result("unconfirmed"))
        assert [t.split("\n")[0].rsplit(", ", 1)[1] for t in held_exit_messages[1:]] == [
            f"{m} min now" for m in (1, 2, 3, 4, 5, 10, 15, 20, 25, 30)
        ]
        clock[0] += 2  # 2 s after the last tick, 10 minutes after the last reminder
        if ends == "sent":
            held_exits.sent(7, 1, ORDER, SimpleNamespace(ok=True, response={}))
            assert held_exit_messages[-1].startswith("Exit sent (Live)")
            assert "held for 40 min" in held_exit_messages[-1]
        else:
            held_exits.held(7, 1, "exit_sl", ORDER, _result("no_position"))
            assert held_exit_messages[-1].startswith("Nothing left to close (Live)")
        assert len(held_exit_messages) == 12


# ---- V3: nothing after "no position" until the position shows again ----------------------------
def _story(held_exit_messages, clock, steps):
    out = []
    for dt, what in steps:
        clock[0] += dt
        before = len(held_exit_messages)
        if what == "sent":
            held_exits.sent(7, 1, ORDER, SimpleNamespace(ok=True, response={}))
        else:
            held_exits.held(7, 1, "exit_sl", ORDER, _result(what, "BOOKS."))
        out.append([t.split("\n")[0] for t in held_exit_messages[before:]])
    return out


U, N = "unconfirmed", "no_position"


def test_v3_the_verifier_s_sequence_ends_on_nothing_left_to_close(held_exit_messages, clock):
    """The probe's "unconfirmed, gone, unconfirmed again, gone again". Before: the read that
    failed at 82 s said "Exit still NOT sent (Live), 1 min now", and nothing corrected it when
    the books answered "no position" again: his last message was wrong."""
    said = _story(
        held_exit_messages,
        clock,
        [(0, U), (10, N), (2, N), (70, U), (2, N), (60, U), (61, U), (1, N)],
    )
    assert said == [["Exit NOT sent (Live)"], ["Nothing left to close (Live)"], *([[]] * 6)]
    assert held_exit_messages[-1].startswith("Nothing left to close (Live)")


def test_v3_no_position_first_then_a_failed_read_says_nothing_more(held_exit_messages, clock):
    said = _story(held_exit_messages, clock, [(0, N), (2, U), (70, U), (2, N), (290, U)])
    assert said == [["Exit NOT sent (Live)"], [], [], [], []]
    assert "Upstox shows no position for this leg" in held_exit_messages[0]


def test_v3_a_record_forgotten_after_five_quiet_minutes_still_ends_on_the_books_answer(
    held_exit_messages, clock
):
    """No tick for 15 minutes forgets the record (as since O1): the next failed read is a first
    hold again, and the books' next answer is told after it, so his last word is right."""
    said = _story(held_exit_messages, clock, [(0, U), (10, N), (900, U), (2, N), (2, U)])
    assert said == [
        ["Exit NOT sent (Live)"],
        ["Nothing left to close (Live)"],
        ["Exit NOT sent (Live)"],
        ["Nothing left to close (Live)"],
        [],
    ]


@pytest.mark.parametrize("shown", ["other_way", "elsewhere", "sent"])
def test_v3_the_position_shown_again_makes_the_next_hold_a_first_hold(
    held_exit_messages, clock, shown
):
    said = _story(held_exit_messages, clock, [(0, U), (10, N), (30, shown), (2, U), (61, U)])
    assert said == [
        ["Exit NOT sent (Live)"],
        ["Nothing left to close (Live)"],
        ["Exit sent (Live)" if shown == "sent" else "Exit NOT sent (Live)"],
        ["Exit NOT sent (Live)"],  # a first hold again, not a reminder
        ["Exit still NOT sent (Live), 1 min now"],
    ]
    assert "could not confirm the position size" in held_exit_messages[-2]
    if shown != "sent":
        assert held_exit_messages[2].endswith("BOOKS.")  # the books' own sentence, once


def test_v3_a_market_order_working_after_no_position_tells_nothing(held_exit_messages, clock):
    """A working market order is checked before the size, so it says nothing about the
    position: no first hold again, and no message."""
    said = _story(held_exit_messages, clock, [(0, U), (10, N), (30, "working"), (2, U), (61, U)])
    assert said == [["Exit NOT sent (Live)"], ["Nothing left to close (Live)"], [], [], []]


def test_v3_books_that_flap_after_no_position_cannot_repeat_messages(held_exit_messages, clock):
    """Each of "held the other way" and "in another product" starts a first hold again once a
    record, so books flapping every read tell a few lines and then nothing."""
    flap = [(2, w) for w in ("other_way", N, U, "other_way", N, "elsewhere", N, U) * 25]
    said = _story(held_exit_messages, clock, [(0, U), (10, N), *flap])
    assert [line for each in said for line in each] == [
        "Exit NOT sent (Live)",
        "Nothing left to close (Live)",
        "Exit NOT sent (Live)",  # held the other way, the books' sentence
        "Exit NOT sent (Live)",  # no position, after it
        "Exit NOT sent (Live)",  # in another product, the books' sentence
        "Exit NOT sent (Live)",  # no position, after it
    ]


def test_v3_a_first_hold_again_writes_its_event_again():
    holds = Holds()
    holds.note(KEY, U, 0.0)
    assert holds.note(KEY, N, 5.0) == Note(event=True, tell=GONE)
    assert holds.note(KEY, U, 70.0) == Note()
    assert holds.note(KEY, "working", 75.0) == Note()
    assert holds.note(KEY, "elsewhere", 80.0) == Note(event=True, tell=ONCE)
    assert holds.note(KEY, U, 82.0) == Note(event=True, tell=FIRST)
    assert holds.note(KEY, U, 142.0) == Note(tell=AGAIN, minutes=1)
    assert holds.note(KEY, N, 150.0) == Note(event=True, tell=GONE)
    assert holds.clear(KEY, 160.0) == 1  # the exit went after all: he is told


def test_v3_an_exit_that_goes_after_no_position_is_told(held_exit_messages, clock):
    """The books lag just after an entry: "no position" first, and the exit goes a read later.
    Before: no message, his last word "no exit was sent ... check Positions in the Upstox app"
    while SUDARSHAN's exit was on its way (a close of his own there would reverse him)."""
    said = _story(held_exit_messages, clock, [(0, N), (2, "sent")])
    assert said == [["Exit NOT sent (Live)"], ["Exit sent (Live)"]]


# ---- V5: one record for each position of a leg -------------------------------------------------
def test_v5_a_flipped_leg_s_two_sides_are_told_apart(held_exit_messages, clock):
    """After a signal flip the old side and the new side share a leg id. Before: one record for
    both, so the new side's exit going said "Exit sent ... held for 2 min" of an exit that was
    never held, and ended the old side's hold."""
    old = {**ORDER, "action": "SELL"}  # the old long, still held: its exit is held
    new = {**ORDER, "action": "BUY"}  # the new short
    held_exits.held(7, 1, "exit_sl", old, _result("unconfirmed"), ref="old-long")
    clock[0] += 125
    assert held_exits.sent(7, 1, new, SimpleNamespace(ok=True, response={}), ref="new-short") == ""
    assert len(held_exit_messages) == 1 and "Leg 1: SELL 65" in held_exit_messages[0]
    assert held_exits.run_is_held(7)  # the old side is still held, and still reminded
    held_exits.held(7, 1, "exit_sl", old, _result("unconfirmed"), ref="old-long")
    assert held_exit_messages[1].startswith("Exit still NOT sent (Live), 2 min now")
    assert "Leg 1: SELL 65" in held_exit_messages[1]
    # the new side's own hold is its own first hold, with its own event
    said = held_exits.held(7, 1, "exit_sl", new, _result("unconfirmed"), ref="new-short")
    assert said.event and "Leg 1: BUY 65" in held_exit_messages[2]
    assert held_exit_messages[2].startswith("Exit NOT sent (Live)")
    clock[0] += 5
    assert held_exits.sent(7, 1, old, SimpleNamespace(ok=True, response={}), ref="old-long")
    assert held_exit_messages[3].startswith("Exit sent (Live)")
    assert "Leg 1: SELL 65" in held_exit_messages[3] and "held for 2 min" in held_exit_messages[3]
    assert held_exits.run_is_held(7) and len(held_exits.HOLDS) == 1


def test_v5_a_wait_on_the_new_side_is_not_swallowed_by_the_old_side_s_record():
    holds = Holds()
    assert holds.note((7, 1, "old-long"), "unconfirmed", 0.0) == Note(event=True, tell=FIRST)
    assert holds.note((7, 1, "new-short"), "working", 30.0) == Note(event=True)
    assert holds.note((7, 1, "old-long"), "working", 31.0) == Note()


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
