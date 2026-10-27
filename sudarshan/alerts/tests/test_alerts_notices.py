"""M18.x: the server's own notices on Telegram: what the deploy tool recorded, told once.

The deploy tool runs as root and holds no Telegram or OpenAlgo secret, so it records facts in a file
(``sudarshan/ops/events.py``) and ``NoticeDesk`` words and sends them through the path the order
alerts take. Here: the wording (a trader's words, never the tool's), the once-only rule across looks,
restarts and two services on one record, the grouping (one message for a run, one for a pile-up
after downtime, nothing for what is history), what happens when sending fails, the pace on
Telegram (no faster than the order alerts' own, one message a minute), the quiet while the market
trades (only failures go out then), and the service telling a notice while the alert window is
closed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alerts_fakes import API_KEY, OWNER, FakeOpenAlgo, RunningClock, ist, wait_until

from sudarshan.alerts import notices, outbox
from sudarshan.alerts.config import Settings
from sudarshan.alerts.notices import LanePace, NoticeDesk, NoticePace, compose
from sudarshan.alerts.outbox import DROP, RETRY, SENT, UNCONFIRMED
from sudarshan.alerts.owner import Owner
from sudarshan.alerts.service import AlertService
from sudarshan.alerts.store import Store
from sudarshan.autologin.messages import BRAND
from sudarshan.autologin.schedule import IST, TradingCalendar
from sudarshan.ops.events import DEFAULT_SPOOLS, Event, record

T0 = ist(2026, 10, 24, 18, 52)  # a Saturday evening: the owner's server installs a release


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


class Send:
    """A send() that records texts and answers from a script, then SENT."""

    def __init__(self, *outcomes: str):
        self.outcomes = list(outcomes)
        self.texts: list[str] = []

    async def __call__(self, text: str) -> str:
        self.texts.append(text)
        return self.outcomes.pop(0) if self.outcomes else SENT


class Ready:
    def __init__(self, value: bool = True):
        self.value, self.calls = value, 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.value


def note(path: Path, kind: str, event_id: str, at: datetime = T0, run: str | None = None, **facts):
    src = facts.pop("src", "deploy")
    assert record(
        path, kind=kind, event_id=event_id, at=at.timestamp(), run=run or event_id, src=src, **facts
    )  # fmt: skip


def desk(tmp_path: Path, clock: Clock, send=None, ready=None, spools=None, store=None, **kwargs):
    send = send if send is not None else Send()
    store = store if store is not None else Store(tmp_path / "alerts.db")
    spools = spools if spools is not None else [tmp_path / "events.jsonl"]
    return NoticeDesk(spools, store, send, ready or Ready(), clock, **kwargs), send


@pytest.fixture(autouse=True)
def _unpaced(monkeypatch):
    """Most tests are about words, claims and retries: the pace is out of their way. The tests
    of the pace itself (below) put it back."""
    monkeypatch.setattr(notices, "NOTICES_PER_MINUTE", 1000)
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.0)


def event(
    kind: str, at: datetime = T0, event_id: str | None = None, run: str | None = None, **facts
):
    ident = event_id or f"e-{kind}"
    return Event(ident, kind, at.timestamp(), run or ident, "deploy", facts)


async def settle(d: NoticeDesk, clock: Clock) -> None:
    """The desk sees what is waiting, and the quiet that lets a run finish passes."""
    await d.sweep()
    clock.advance(notices.SETTLE_S + 1)


# ---- the words --------------------------------------------------------------------------------


def test_an_install_is_one_short_line_under_the_brand():
    text = compose([event("installed", tag="v0.3.3", backup="yes")], T0 + timedelta(minutes=1))
    assert text == f"{BRAND}\nv0.3.3 installed at 18:52 IST. A backup was taken."
    bare = compose([event("installed", tag="v0.3.3", backup="no")], T0)
    assert bare == f"{BRAND}\nv0.3.3 installed at 18:52 IST."


@pytest.mark.parametrize(
    ("why", "cause"),
    [
        ("health", "the app did not come up healthy"),
        ("migrations", "a database update did not finish"),
        ("settings", "a settings update did not finish"),
        ("error", "the update stopped unexpectedly"),
        ("something new", "the update stopped unexpectedly"),
        ("", "the update stopped unexpectedly"),
    ],
)
def test_a_rollback_names_the_cause_and_the_version_that_is_back(why, cause):
    rolled = event("rolled_back", tag="v0.3.3", was="v0.3.2", why=why, up="yes")
    assert compose([rolled], T0) == (
        f"{BRAND}\nCould not install v0.3.3 at 18:52 IST: {cause}, so v0.3.2 is back. "
        "Nothing for you to do; the release will be looked at."
    )


def test_a_rollback_without_the_old_tag_does_not_invent_one():
    text = compose([event("rolled_back", tag="v0.3.3", why="health", up="yes")], T0)
    assert "so the earlier version is back." in text


def test_a_rollback_that_left_the_app_down_says_so_and_does_not_say_it_is_back():
    down = event("rolled_back", tag="v0.3.3", was="v0.3.2", why="migrations", up="no")
    text = compose([down], T0)
    assert text.endswith("and the earlier version did not start either. SUDARSHAN is not running. "
                         "The server needs attention now.")  # fmt: skip
    assert "is back" not in text


def test_refused_services_are_told_with_the_install_as_one_message():
    run = "20261024-185200-v0.3.3"
    found = [
        event("unit_refused", T0 + timedelta(seconds=9), "r2", run, tag="v0.3.3", unit="sudarshan-b.timer"),
        event("installed", T0, "i1", run, tag="v0.3.3", backup="yes"),
        event("unit_refused", T0 + timedelta(seconds=7), "r1", run, tag="v0.3.3", unit="sudarshan-a.service"),
    ]  # fmt: skip
    text = compose(found, T0 + timedelta(minutes=2))
    assert text.splitlines() == [
        BRAND,
        "v0.3.3 installed at 18:52 IST. A backup was taken.",
        "2 background services in v0.3.3 were not installed: sudarshan-a.service, "
        "sudarshan-b.timer. They did not pass the server's safety check. The rest of the release "
        "is in place. Nothing for you to do; it will be looked at.",
    ]  # fmt: skip
    one = compose(found[:1], T0)
    assert "A background service in v0.3.3 was not installed: sudarshan-b.timer." in one


def test_a_waiting_release_says_when_it_will_be_installed():
    text = compose([event("waiting", tag="v0.3.4")], T0)
    assert text == (
        f"{BRAND}\nv0.3.4 is ready. It will be installed when the market is closed. "
        "Nothing for you to do."
    )


def test_a_notice_from_another_day_carries_its_date():
    late = ist(2026, 10, 23, 23, 50)
    assert "v0.3.3 installed at 23 Oct 23:50 IST." in compose(
        [event("installed", late, tag="v0.3.3")], ist(2026, 10, 24, 0, 5)
    )
    just_after_midnight = ist(2026, 10, 24, 0, 10)  # the day before in UTC, today in India
    assert "installed at 00:10 IST." in compose(
        [event("installed", just_after_midnight, tag="v0.3.3")], ist(2026, 10, 24, 0, 30)
    )


def test_runs_are_told_in_the_order_they_happened():
    found = [
        event("installed", T0, "i2", "run2", tag="v0.3.4"),
        event("waiting", T0 - timedelta(hours=2), "w1", "run0", tag="v0.3.3"),
        event("installed", T0 - timedelta(hours=1), "i1", "run1", tag="v0.3.3"),
    ]
    lines = compose(found, T0).splitlines()[1:]
    assert [line.split(" ")[0] for line in lines] == ["v0.3.3", "v0.3.3", "v0.3.4"]
    assert "is ready" in lines[0] and "installed at 17:52" in lines[1] and "18:52" in lines[2]


HOSTILE = "v1.0.0\n*bold* `code` [link](x) _under_ \x1b[31m"


def test_a_fact_is_never_repeated_unless_it_is_a_release_or_a_service_name():
    found = [
        event("installed", tag=HOSTILE, backup="yes"),
        event("rolled_back", event_id="r", tag=HOSTILE, was=HOSTILE, why=HOSTILE, up="maybe"),
        event("unit_refused", event_id="u1", tag=HOSTILE, unit="sudarshan-x`.service"),
        event("unit_refused", event_id="u2", tag="v1.0.0", unit="sudarshan-ok.service"),
        event("waiting", event_id="w", tag=HOSTILE),
    ]
    text = compose(found, T0)
    assert not set("*_`[]\x1b") & set(text)
    assert "bold" not in text and "under" not in text and "link" not in text
    assert "sudarshan-x" not in text and "sudarshan-ok.service" in text
    assert "The new release installed at 18:52 IST." in text


def test_no_message_carries_a_status_code_a_tool_name_or_a_symbol():
    found = [
        event("installed", tag="v0.3.3", backup="yes"),
        event("rolled_back", event_id="r1", tag="v0.3.3", was="v0.3.2", why="health", up="yes"),
        event("rolled_back", event_id="r2", tag="v0.3.3", why="error", up="no"),
        event("unit_refused", event_id="u", tag="v0.3.3", unit="sudarshan-x.service"),
        event("waiting", event_id="w", tag="v0.3.3"),
    ]
    text = compose(found, T0)
    assert not re.search(r"\b[1-5]\d\d\b", text)
    for word in ("traceback", "exception", "http", "errno", "exit code", "systemd", "jq", "json"):
        assert word not in text.lower(), word
    assert all(ord(ch) < 0x2000 or 0x900 <= ord(ch) <= 0x97F for ch in text)  # no emoji or symbols
    assert text.splitlines()[0] == BRAND


# ---- told once, as one message ----------------------------------------------------------------


def test_a_fresh_notice_waits_for_its_run_to_settle_then_goes_once(tmp_path):
    clock = Clock(T0 + timedelta(seconds=5))
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3", backup="yes")
    d, send = desk(tmp_path, clock)

    async def scenario():
        assert await d.sweep() == pytest.approx(notices.SETTLE_S + 0.5)
        clock.advance(20)
        assert await d.sweep() == pytest.approx(notices.SETTLE_S - 20 + 0.5)
        assert send.texts == []
        clock.advance(26)  # 46 s since it was first seen
        assert await d.sweep() == notices.POLL_S
        assert send.texts == [f"{BRAND}\nv0.3.3 installed at 18:52 IST. A backup was taken."]
        clock.advance(60)
        assert await d.sweep() == notices.POLL_S and len(send.texts) == 1  # a second look
        assert (d.stats["messages"], d.stats["notices"]) == (1, 1)
        again, again_send = desk(tmp_path, clock)  # the service restarted on the same record
        await again.sweep()
        clock.advance(notices.SETTLE_S + 5)
        await again.sweep()
        assert again_send.texts == []

    asyncio.run(scenario())


def test_the_facts_of_a_run_are_told_together(tmp_path):
    clock = Clock(T0)
    spool = tmp_path / "events.jsonl"
    run = "20261024-185200-v0.3.3"
    note(spool, "installed", "i1", run=run, tag="v0.3.3", backup="yes")
    d, send = desk(tmp_path, clock)

    async def scenario():
        await d.sweep()
        clock.advance(30)  # the refusals of the unit files come in while the desk waits
        note(spool, "unit_refused", "r1", T0 + timedelta(seconds=30), run, tag="v0.3.3",
             unit="sudarshan-a.service")  # fmt: skip
        assert await d.sweep() == pytest.approx(notices.SETTLE_S + 0.5)  # a new one: wait again
        clock.advance(notices.SETTLE_S - 1)
        assert await d.sweep() == pytest.approx(1.5) and send.texts == []
        clock.advance(2)
        await d.sweep()
        assert len(send.texts) == 1
        assert send.texts[0].count("\n") == 2 and "sudarshan-a.service" in send.texts[0]

    asyncio.run(scenario())


def test_a_clock_that_was_wrong_when_a_notice_was_written_cannot_hold_it_back(tmp_path):
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", T0 + timedelta(days=3), tag="v0.3.3")
    d, send = desk(tmp_path, clock)

    async def scenario():
        await settle(d, clock)
        await d.sweep()
        assert len(send.texts) == 1

    asyncio.run(scenario())


def test_what_piled_up_while_the_service_was_down_is_one_message(tmp_path):
    clock = Clock(T0)
    spool = tmp_path / "events.jsonl"
    note(spool, "waiting", "w1", T0 - timedelta(hours=3), tag="v0.3.3")
    note(spool, "installed", "i1", T0 - timedelta(hours=2), tag="v0.3.3", backup="yes")
    note(spool, "waiting", "w2", T0 - timedelta(hours=1), tag="v0.3.4")
    d, send = desk(tmp_path, clock)

    async def scenario():
        await settle(d, clock)
        await d.sweep()
        assert len(send.texts) == 1
        lines = send.texts[0].splitlines()
        assert lines[0] == BRAND and len(lines) == 4
        assert "v0.3.3 is ready" in lines[1] and "installed at 16:52" in lines[2]
        assert "v0.3.4 is ready" in lines[3]

    asyncio.run(scenario())


def test_a_long_pile_goes_out_in_batches_and_each_notice_once(tmp_path, monkeypatch):
    monkeypatch.setattr(notices, "MAX_EVENTS", 3)
    clock = Clock(T0)
    spool = tmp_path / "events.jsonl"
    for n in range(7):
        note(spool, "waiting", f"w{n}", T0 - timedelta(minutes=10 - n), tag=f"v0.3.{n}")
    d, send = desk(tmp_path, clock)

    async def scenario():
        await settle(d, clock)
        for _ in range(4):
            await d.sweep()
        assert [t.count("is ready") for t in send.texts] == [3, 3, 1]
        assert len({line for t in send.texts for line in t.splitlines()[1:]}) == 7

    asyncio.run(scenario())


def test_a_notice_from_before_the_last_twelve_hours_is_history(tmp_path):
    clock = Clock(T0)
    spool = tmp_path / "events.jsonl"
    note(spool, "installed", "old", T0 - timedelta(hours=13), tag="v0.3.2")
    note(spool, "installed", "recent", T0 - timedelta(hours=11, minutes=59), tag="v0.3.3")
    d, send = desk(tmp_path, clock)

    async def scenario():
        await settle(d, clock)
        await d.sweep()
        assert len(send.texts) == 1 and "v0.3.3 installed" in send.texts[0]
        assert "v0.3.2" not in send.texts[0]
        assert d.stats["stale"] == 1
        assert d.store.notice_states(["old"])["old"][0] == "stale"
        again, again_send = desk(tmp_path, clock)  # a restart finds it marked
        await settle(again, clock)
        await again.sweep()
        assert again_send.texts == []

    asyncio.run(scenario())


# ---- when sending fails -----------------------------------------------------------------------


def test_a_message_openalgo_could_not_take_is_tried_again_with_growing_pauses(tmp_path):
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")
    d, send = desk(tmp_path, clock, Send(RETRY, RETRY, SENT))

    async def scenario():
        await settle(d, clock)
        assert await d.sweep() == notices.RETRY_S[0]
        assert d.store.notice_states(["i1"]) == {}  # the claim is given back
        clock.advance(notices.RETRY_S[0])
        assert await d.sweep() == notices.RETRY_S[1]
        clock.advance(notices.RETRY_S[1])
        assert await d.sweep() == notices.POLL_S
        assert len(send.texts) == 3 and len(set(send.texts)) == 1  # the same words each time
        assert d.store.notice_states(["i1"])["i1"][0] == "sent"
        # the ladder starts from its first step again for the next trouble
        note(tmp_path / "events.jsonl", "waiting", "w1", clock(), tag="v0.3.4")
        send.outcomes.append(RETRY)
        await settle(d, clock)
        assert await d.sweep() == notices.RETRY_S[0]

    asyncio.run(scenario())


def test_the_pauses_stop_growing_at_the_last_step(tmp_path):
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")
    d, _ = desk(tmp_path, clock, Send(*[RETRY] * 9))

    async def scenario():
        await settle(d, clock)
        waits = []
        for _ in range(8):
            waits.append(await d.sweep())
            clock.advance(waits[-1])
        assert waits == [*notices.RETRY_S, *[notices.RETRY_S[-1]] * 3]

    asyncio.run(scenario())


def test_a_notice_that_keeps_failing_ends_as_history_not_as_a_burst(tmp_path):
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")
    d, send = desk(tmp_path, clock, Send(*[RETRY] * 1000))

    async def scenario():
        await settle(d, clock)
        await d.sweep()
        clock.advance(13 * 3600)
        assert await d.sweep() == notices.POLL_S  # it aged out while trying
        assert d.store.notice_states(["i1"])["i1"][0] == "stale"
        before = len(send.texts)
        clock.advance(60)
        await d.sweep()
        assert len(send.texts) == before

    asyncio.run(scenario())


def test_a_message_telegram_did_not_confirm_is_given_up_after_three_tries(tmp_path):
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")
    d, send = desk(tmp_path, clock, Send(UNCONFIRMED, UNCONFIRMED, UNCONFIRMED))

    async def scenario():
        await settle(d, clock)
        assert await d.sweep() == notices.RETRY_S[0]
        clock.advance(30)
        assert await d.sweep() == notices.RETRY_S[1]
        clock.advance(60)
        assert await d.sweep() == notices.POLL_S  # the third: no more
        assert d.store.notice_states(["i1"])["i1"][0] == "dropped" and d.stats["dropped"] == 1
        clock.advance(600)
        await d.sweep()
        assert len(send.texts) == 3

    asyncio.run(scenario())


def test_a_confirmed_message_clears_the_count_of_unconfirmed_ones(tmp_path):
    clock = Clock(T0)
    spool = tmp_path / "events.jsonl"
    note(spool, "installed", "i1", tag="v0.3.3")
    d, send = desk(
        tmp_path, clock, Send(UNCONFIRMED, UNCONFIRMED, SENT, UNCONFIRMED, UNCONFIRMED, SENT)
    )

    async def scenario():
        await settle(d, clock)
        for _ in range(3):
            await d.sweep()
            clock.advance(60)
        note(spool, "waiting", "w1", clock(), tag="v0.3.4")
        await settle(d, clock)
        for _ in range(3):
            await d.sweep()
            clock.advance(60)
        assert [d.store.notice_states([i])[i][0] for i in ("i1", "w1")] == ["sent", "sent"]

    asyncio.run(scenario())


def test_a_message_that_can_never_be_sent_is_dropped_not_held_back(tmp_path):
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")
    d, send = desk(tmp_path, clock, Send(DROP))

    async def scenario():
        await settle(d, clock)
        assert await d.sweep() == notices.POLL_S
        assert d.store.notice_states(["i1"])["i1"][0] == "dropped" and d.stats["dropped"] == 1
        clock.advance(600)
        await d.sweep()
        assert len(send.texts) == 1

    asyncio.run(scenario())


def test_without_the_owners_key_nothing_is_lost(tmp_path):
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")
    ready = Ready(False)
    d, send = desk(tmp_path, clock, ready=ready)

    async def scenario():
        await settle(d, clock)
        assert await d.sweep() == notices.RETRY_S[0]
        assert send.texts == [] and d.store.notice_states(["i1"]) == {}
        ready.value = True
        clock.advance(notices.RETRY_S[0])
        await d.sweep()
        assert len(send.texts) == 1

    asyncio.run(scenario())


def test_a_sender_that_raises_costs_one_try_and_the_claim_is_given_back(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.alerts")
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")

    class Boom:
        calls = 0

        async def __call__(self, text):
            Boom.calls += 1
            if Boom.calls == 1:
                raise RuntimeError("the network fell over")
            return SENT

    d, _ = desk(tmp_path, clock, Boom())

    async def scenario():
        await settle(d, clock)
        assert await d.sweep() == notices.RETRY_S[0]
        assert d.store.notice_states(["i1"]) == {}
        clock.advance(30)
        await d.sweep()
        assert d.store.notice_states(["i1"])["i1"][0] == "sent"

    asyncio.run(scenario())
    assert "a server notice could not be sent" in caplog.text


def test_a_composer_that_raises_does_not_leave_the_notice_claimed(tmp_path, monkeypatch):
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")
    d, send = desk(tmp_path, clock)
    real = notices.compose
    monkeypatch.setattr(notices, "compose", lambda *a: (_ for _ in ()).throw(ValueError("bug")))

    async def scenario():
        await settle(d, clock)
        assert await d.sweep() == notices.RETRY_S[0] and send.texts == []
        assert d.store.notice_states(["i1"]) == {}
        monkeypatch.setattr(notices, "compose", real)
        clock.advance(30)
        await d.sweep()
        assert len(send.texts) == 1

    asyncio.run(scenario())


# ---- once, across services and restarts -------------------------------------------------------


def test_a_claim_left_by_a_stopped_process_is_taken_over_after_a_while(tmp_path):
    clock = Clock(T0 + timedelta(seconds=60))
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")
    d, send = desk(tmp_path, clock)
    assert d.store.claim_notices(["i1"], clock().timestamp(), notices.CLAIM_S) == ["i1"]

    async def scenario():
        clock.advance(10)
        assert await d.sweep() == notices.POLL_S  # another process has it, 10 s ago
        clock.advance(notices.CLAIM_S + 1)
        await settle(d, clock)  # now it is the desk's to tell
        await d.sweep()
        assert len(send.texts) == 1

    asyncio.run(scenario())


def test_two_services_on_one_record_never_tell_the_same_notice_twice(tmp_path):
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")
    sent: list[str] = []

    async def slow(text: str) -> str:
        await asyncio.sleep(0.05)  # both are in flight at once
        sent.append(text)
        return SENT

    first, _ = desk(tmp_path, clock, slow)
    second, _ = desk(tmp_path, clock, slow, store=Store(tmp_path / "alerts.db"))

    async def scenario():
        await first.sweep()
        await second.sweep()
        clock.advance(notices.SETTLE_S + 1)
        await asyncio.gather(first.sweep(), second.sweep())

    asyncio.run(scenario())
    assert len(sent) == 1


def test_a_send_that_is_cancelled_gives_the_claim_back(tmp_path):
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")

    async def scenario():
        started = asyncio.Event()

        async def hang(text: str) -> str:
            started.set()
            await asyncio.sleep(3600)
            return SENT

        d, _ = desk(tmp_path, clock, hang)
        await settle(d, clock)
        task = asyncio.create_task(d.sweep())
        await started.wait()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert d.store.notice_states(["i1"]) == {}
        later, send = desk(tmp_path, clock)
        await settle(later, clock)
        await later.sweep()
        assert len(send.texts) == 1

    asyncio.run(scenario())


# ---- the pace on Telegram ---------------------------------------------------------------------


@pytest.fixture
def paced(monkeypatch):
    """The real limits: the order alerts' gap between two messages, and one message a minute."""
    monkeypatch.setattr(notices, "NOTICES_PER_MINUTE", 1)
    monkeypatch.setattr(outbox, "MIN_GAP_S", 1.1)


def test_two_messages_are_never_closer_than_the_order_alerts_gap(monkeypatch):
    monkeypatch.setattr(notices, "NOTICES_PER_MINUTE", 1000)  # the gap alone
    monkeypatch.setattr(outbox, "MIN_GAP_S", 1.1)
    pace = NoticePace()
    assert pace.wait(100.0) == 0.0
    pace.took(100.0)
    assert pace.wait(100.0) == pytest.approx(1.1)
    assert pace.wait(100.5) == pytest.approx(0.6)
    assert pace.wait(101.1) == 0.0
    monkeypatch.setattr(outbox, "MIN_GAP_S", 2.0)  # it follows the order alerts' own constant
    assert pace.wait(101.1) == pytest.approx(0.9)


def test_at_most_one_message_a_minute(paced):
    pace = NoticePace()
    pace.took(100.0)
    assert pace.wait(101.2) == pytest.approx(58.8)  # the gap is over, the minute is not
    assert pace.wait(159.9) == pytest.approx(0.1)
    assert pace.wait(160.0) == 0.0
    pace.took(160.0)
    assert pace.wait(161.0) == pytest.approx(59.0)


def test_the_minute_budget_counts_every_message_in_it(monkeypatch):
    monkeypatch.setattr(notices, "NOTICES_PER_MINUTE", 3)
    monkeypatch.setattr(outbox, "MIN_GAP_S", 1.1)
    pace = NoticePace()
    for at in (0.0, 2.0, 4.0):
        assert pace.wait(at) == 0.0
        pace.took(at)
    assert pace.wait(5.0) == pytest.approx(55.0)  # the first one leaves the minute at 60
    assert pace.wait(60.0) == 0.0
    pace.took(60.0)
    assert pace.wait(61.0) == pytest.approx(1.0)  # now the one from 2.0 is the oldest in the minute


def test_a_clock_set_back_holds_the_notices_a_minute_at_most(paced):
    pace = NoticePace()
    pace.took(10_000.0)
    assert pace.wait(100.0) == 60.0


def test_a_second_message_waits_for_its_minute_and_is_not_claimed_meanwhile(tmp_path, paced):
    clock = Clock(T0)
    spool = tmp_path / "events.jsonl"
    note(spool, "installed", "i1", tag="v0.3.3")
    d, send = desk(tmp_path, clock)

    async def scenario():
        await settle(d, clock)
        await d.sweep()  # the first message starts at T0 + 46 s
        assert len(send.texts) == 1
        assert await d.sweep() == notices.POLL_S  # nothing is due, so the pace is not in it
        note(spool, "waiting", "w1", clock(), tag="v0.3.4")
        await settle(d, clock)  # the run is complete 46 s later: the minute is not over
        assert await d.sweep() == pytest.approx(14.05)
        assert len(send.texts) == 1 and d.store.notice_states(["w1"]) == {}
        clock.advance(14.1)
        assert await d.sweep() == notices.POLL_S
        assert len(send.texts) == 2 and "v0.3.4 is ready" in send.texts[1]

    asyncio.run(scenario())


def test_a_long_pile_goes_out_a_message_a_minute(tmp_path, paced, monkeypatch):
    monkeypatch.setattr(notices, "MAX_EVENTS", 3)
    clock = Clock(T0)
    spool = tmp_path / "events.jsonl"
    for n in range(7):
        note(spool, "waiting", f"w{n}", T0 - timedelta(minutes=10 - n), tag=f"v0.3.{n}")
    d, send = desk(tmp_path, clock)

    async def scenario():
        await settle(d, clock)
        await d.sweep()
        assert len(send.texts) == 1
        for sent in (2, 3):
            wait = await d.sweep()
            assert wait == pytest.approx(60.05) and len(send.texts) == sent - 1
            clock.advance(wait)
            await d.sweep()
            assert len(send.texts) == sent
        assert [t.count("is ready") for t in send.texts] == [3, 3, 1]

    asyncio.run(scenario())


def test_a_retry_cannot_come_sooner_than_the_minute(tmp_path, paced):
    """The attempt took the slot, as an order alert's does: the first pause of the ladder (30 s)
    is stretched to the minute, the later ones are longer than it already."""
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")
    d, send = desk(tmp_path, clock, Send(RETRY, SENT))

    async def scenario():
        await settle(d, clock)
        assert await d.sweep() == notices.RETRY_S[0]
        clock.advance(notices.RETRY_S[0])
        assert await d.sweep() == pytest.approx(30.05) and len(send.texts) == 1
        clock.advance(30.1)
        await d.sweep()
        assert len(send.texts) == 2 and d.store.notice_states(["i1"])["i1"][0] == "sent"

    asyncio.run(scenario())


def test_a_notice_without_the_owners_key_does_not_use_up_the_minute(tmp_path, paced):
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")
    ready = Ready(False)
    d, send = desk(tmp_path, clock, ready=ready)

    async def scenario():
        await settle(d, clock)
        assert await d.sweep() == notices.RETRY_S[0]  # nothing was sent
        ready.value = True
        clock.advance(notices.RETRY_S[0])
        await d.sweep()
        assert len(send.texts) == 1

    asyncio.run(scenario())


# ---- the Pacer hook (M10.5) -------------------------------------------------------------------


class FakePacer:
    """The shape outbox.Pacer has in the market alerts lanes: slots by lane, and order alerts that
    can be waiting."""

    def __init__(self):
        self.waiting, self.free_at, self.took_lanes = False, 0.0, []

    def orders_waiting(self) -> bool:
        return self.waiting

    def allowed_at(self, lane: str) -> float:
        return self.free_at

    def took(self, lane: str) -> None:
        self.took_lanes.append(lane)


def test_the_lane_pace_gives_way_to_order_alerts_and_takes_its_own_lane():
    pacer = FakePacer()
    pace = LanePace(pacer)
    assert pace.wait(0.0) == 0.0
    pacer.waiting = True
    assert pace.wait(0.0) == notices.GIVE_WAY_S  # a fill is waiting: a notice is not first
    pacer.waiting, pacer.free_at = False, time.monotonic() + 5.0
    assert 4.0 < pace.wait(0.0) <= 5.0  # the Pacer's gap and minute budget decide
    pace.took(0.0)
    assert pacer.took_lanes == ["notices"]


def test_the_desk_takes_its_slot_from_a_shared_pacer(tmp_path):
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")
    pacer = FakePacer()
    pacer.waiting = True
    d, send = desk(tmp_path, clock, pace=LanePace(pacer))

    async def scenario():
        await settle(d, clock)
        assert await d.sweep() == pytest.approx(notices.GIVE_WAY_S + 0.05)
        assert send.texts == [] and d.store.notice_states(["i1"]) == {}
        pacer.waiting = False
        await d.sweep()
        assert len(send.texts) == 1 and pacer.took_lanes == ["notices"]

    asyncio.run(scenario())


@pytest.mark.skipif(
    not hasattr(outbox, "Pacer"), reason="outbox.Pacer arrives with the market alerts lanes"
)
def test_the_lane_pace_against_the_real_pacer(paced):
    pacer = outbox.Pacer()
    pace = LanePace(pacer)
    assert pace.wait(0.0) == 0.0
    pacer.took(outbox.ORDERS)
    assert 0.0 < pace.wait(0.0) <= outbox.MIN_GAP_S  # a notice follows an order alert by one gap
    pacer.orders_waiting = lambda: True
    assert pace.wait(0.0) == notices.GIVE_WAY_S
    pacer.orders_waiting = lambda: False
    pace.took(0.0)
    gap = pacer.allowed_at(outbox.ORDERS) - time.monotonic()
    assert 0.0 < gap <= outbox.MIN_GAP_S  # and an order alert follows a notice by one gap


# ---- quiet while the market trades ------------------------------------------------------------

WEDNESDAY = ist(2026, 10, 21, 10, 0)  # the market trades


class Market:
    """A market_open that the test opens and closes."""

    def __init__(self, open_: bool):
        self.open = open_

    def __call__(self, at: datetime) -> bool:
        return self.open


def test_in_market_hours_only_a_failure_is_told_and_the_rest_waits_for_the_close(tmp_path):
    clock, market = Clock(WEDNESDAY), Market(True)
    spool = tmp_path / "events.jsonl"
    note(spool, "waiting", "w1", WEDNESDAY, run="run-w", tag="v0.3.4")
    note(spool, "installed", "i1", WEDNESDAY, run="run-i", tag="v0.3.3", backup="yes")
    d, send = desk(tmp_path, clock, market_open=market)

    async def scenario():
        await settle(d, clock)
        assert await d.sweep() == notices.POLL_S  # nothing is due
        assert send.texts == [] and d.store.notice_states(["w1", "i1"]) == {}
        note(spool, "rolled_back", "r1", clock(), "run-r", tag="v0.3.5", was="v0.3.3",
             why="health", up="yes")  # fmt: skip
        await settle(d, clock)
        await d.sweep()
        assert len(send.texts) == 1 and "Could not install v0.3.5" in send.texts[0]
        assert "v0.3.4" not in send.texts[0] and "v0.3.3 installed" not in send.texts[0]
        market.open = False  # 15:40: what waited goes, in one message, after the usual settle
        await d.sweep()
        assert len(send.texts) == 1
        clock.advance(notices.SETTLE_S + 1)
        await d.sweep()
        assert len(send.texts) == 2
        assert "v0.3.4 is ready" in send.texts[1] and "v0.3.3 installed" in send.texts[1]
        assert "Could not install" not in send.texts[1]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("kind", "facts", "goes"),
    [
        ("rolled_back", {"tag": "v0.3.3", "why": "health", "up": "yes"}, True),
        ("unit_refused", {"tag": "v0.3.3", "unit": "sudarshan-x.service"}, True),
        ("installed", {"tag": "v0.3.3", "backup": "yes"}, False),
        ("waiting", {"tag": "v0.3.3"}, False),
    ],
)
def test_which_notices_are_told_in_market_hours(tmp_path, kind, facts, goes):
    clock = Clock(WEDNESDAY)
    note(tmp_path / "events.jsonl", kind, "n1", WEDNESDAY, **facts)
    d, send = desk(tmp_path, clock, market_open=Market(True))

    async def scenario():
        await settle(d, clock)
        await d.sweep()
        assert bool(send.texts) is goes

    asyncio.run(scenario())


def test_a_failures_whole_run_is_told_with_it(tmp_path):
    clock = Clock(WEDNESDAY)
    spool = tmp_path / "events.jsonl"
    note(spool, "installed", "i1", WEDNESDAY, "run-1", tag="v0.3.3", backup="yes")
    note(spool, "unit_refused", "u1", WEDNESDAY, "run-1", tag="v0.3.3", unit="sudarshan-x.service")
    note(spool, "waiting", "w1", WEDNESDAY, "run-2", tag="v0.3.4")
    d, send = desk(tmp_path, clock, market_open=Market(True))

    async def scenario():
        await settle(d, clock)
        await d.sweep()
        assert len(send.texts) == 1
        assert "v0.3.3 installed" in send.texts[0] and "sudarshan-x.service" in send.texts[0]
        assert "v0.3.4" not in send.texts[0]

    asyncio.run(scenario())


def test_a_notice_held_for_the_close_still_ages_out(tmp_path):
    clock, market = Clock(T0), Market(True)
    note(tmp_path / "events.jsonl", "waiting", "w1", T0 - timedelta(hours=11, minutes=50),
         tag="v0.3.4")  # fmt: skip
    d, send = desk(tmp_path, clock, market_open=market)

    async def scenario():
        await settle(d, clock)
        await d.sweep()
        assert d.store.notice_states(["w1"]) == {}  # held, not claimed, not told
        clock.advance(20 * 60)  # the market is still open, and the notice is now history
        await d.sweep()
        assert d.store.notice_states(["w1"])["w1"][0] == "stale"
        market.open = False
        clock.advance(notices.SETTLE_S + 1)
        await d.sweep()
        assert send.texts == []

    asyncio.run(scenario())


def test_the_hold_is_logged_once_not_at_every_look(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.alerts")
    clock = Clock(WEDNESDAY)
    note(tmp_path / "events.jsonl", "waiting", "w1", WEDNESDAY, tag="v0.3.4")
    d, _ = desk(tmp_path, clock, market_open=Market(True))

    async def scenario():
        for _ in range(4):
            await d.sweep()
            clock.advance(20)

    asyncio.run(scenario())
    assert caplog.text.count("wait for the market to close") == 1


def test_a_market_that_cannot_be_read_does_not_hold_a_notice_back(tmp_path, caplog):
    clock = Clock(WEDNESDAY)
    note(tmp_path / "events.jsonl", "installed", "i1", WEDNESDAY, tag="v0.3.3")
    note(tmp_path / "events.jsonl", "waiting", "w1", WEDNESDAY + timedelta(seconds=1), tag="v0.3.4")

    def broken(at: datetime) -> bool:
        raise RuntimeError("the calendar was locked")

    d, send = desk(tmp_path, clock, market_open=broken)

    async def scenario():
        await settle(d, clock)
        await d.sweep()
        assert len(send.texts) == 1
        note(tmp_path / "events.jsonl", "waiting", "w2", clock(), tag="v0.3.5")
        await settle(d, clock)
        await d.sweep()
        assert len(send.texts) == 2

    asyncio.run(scenario())
    assert caplog.text.count("market hours could not be read") == 1  # once, not at every look


@pytest.mark.parametrize(
    ("at", "quiet"),
    [
        (ist(2026, 10, 21, 9, 14), False),
        (ist(2026, 10, 21, 9, 15), True),
        (ist(2026, 10, 21, 15, 39), True),
        (ist(2026, 10, 21, 15, 40), False),
        (ist(2026, 10, 24, 10, 0), False),  # a Saturday
        (ist(2026, 11, 8, 13, 0), False),  # Muhurat Sunday, before its window
        (ist(2026, 11, 8, 14, 30), True),  # in the window the day's line gives
        (ist(2026, 11, 8, 15, 40), False),
    ],
)
def test_the_trading_calendar_decides_the_quiet_hours_a_special_days_own_included(
    tmp_path, at, quiet
):
    holidays, extra = tmp_path / "nse_holidays.txt", tmp_path / "nse_extra_trading_days.txt"
    holidays.write_text("2026-11-09\n")
    extra.write_text("2026-11-08 14:05-15:40  # Muhurat\n")
    clock = Clock(at)
    note(tmp_path / "events.jsonl", "waiting", "w1", at, tag="v0.3.4")
    d, send = desk(tmp_path, clock, market_open=TradingCalendar(holidays, extra).in_market_hours)

    async def scenario():
        await settle(d, clock)
        await d.sweep()
        assert bool(send.texts) is not quiet

    asyncio.run(scenario())


# ---- the files --------------------------------------------------------------------------------


def test_spools_that_are_missing_garbage_or_not_files_cost_nothing(tmp_path):
    clock = Clock(T0)
    garbage = tmp_path / "garbage.jsonl"
    garbage.write_bytes(b'not json\n{"v": "1", "id"\n\x00\x01\xff\n')
    folder = tmp_path / "folder"
    folder.mkdir()
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    link = tmp_path / "link.jsonl"
    link.symlink_to(garbage)
    good = tmp_path / "good.jsonl"
    note(good, "installed", "i1", tag="v0.3.3")
    spools = [tmp_path / "missing.jsonl", garbage, folder, fifo, link, good]
    d, send = desk(tmp_path, clock, spools=spools)

    async def scenario():
        started = time.monotonic()
        await settle(d, clock)
        await d.sweep()
        assert time.monotonic() - started < 5  # the FIFO was not waited on
        assert len(send.texts) == 1 and "v0.3.3 installed" in send.texts[0]

    asyncio.run(scenario())


def test_both_spools_are_read_and_an_id_in_both_is_told_once(tmp_path):
    clock = Clock(T0)
    deploy, backup = tmp_path / "deploy.jsonl", tmp_path / "backup.jsonl"
    note(deploy, "installed", "same", tag="v0.3.3")
    note(backup, "installed", "same", tag="v0.3.3", src="backup")
    note(backup, "waiting", "other", T0 + timedelta(seconds=1), tag="v0.3.4", src="backup")
    d, send = desk(tmp_path, clock, spools=[deploy, backup])

    async def scenario():
        await settle(d, clock)
        await d.sweep()
        assert len(send.texts) == 1 and send.texts[0].count("\n") == 2

    asyncio.run(scenario())


def test_a_record_that_cannot_be_opened_still_keeps_the_rule_for_this_run(tmp_path):
    clock = Clock(T0)
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    store = Store(blocker / "alerts.db")
    assert store.in_memory
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")
    d, send = desk(tmp_path, clock, store=store)

    async def scenario():
        await settle(d, clock)
        await d.sweep()
        clock.advance(60)
        await d.sweep()
        assert len(send.texts) == 1

    asyncio.run(scenario())


# ---- the loop ---------------------------------------------------------------------------------


@pytest.fixture
def quick(monkeypatch):
    for name, value in {"POLL_S": 0.02, "SETTLE_S": 0.05, "RETRY_S": (0.02, 0.02)}.items():
        monkeypatch.setattr(notices, name, value)


def test_the_loop_keeps_looking_after_a_failure(tmp_path, quick, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.alerts")
    clock = RunningClock(T0 + timedelta(seconds=30))
    note(tmp_path / "events.jsonl", "installed", "i1", T0, tag="v0.3.3")
    d, send = desk(tmp_path, clock)
    real = d.store.notice_states
    failures = []

    def flaky(ids):
        if not failures:
            failures.append(1)
            raise RuntimeError("the record was locked")
        return real(ids)

    d.store.notice_states = flaky

    async def scenario():
        task = asyncio.create_task(d.run())
        await wait_until(lambda: send.texts, 5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert len(send.texts) == 1 and "the notices could not be looked at" in caplog.text


def test_close_gives_a_message_on_its_way_time_to_finish(tmp_path):
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")

    async def scenario():
        started = asyncio.Event()

        async def slow(text: str) -> str:
            started.set()
            await asyncio.sleep(0.3)
            return SENT

        d, _ = desk(tmp_path, clock, slow)
        await settle(d, clock)
        task = asyncio.create_task(d.sweep())
        await started.wait()
        began = time.monotonic()
        await d.close(5.0)
        assert 0.15 < time.monotonic() - began < 2.0
        await task
        assert d.store.notice_states(["i1"])["i1"][0] == "sent"

    asyncio.run(scenario())


def test_close_does_not_wait_for_ever_for_a_message_that_hangs(tmp_path):
    clock = Clock(T0)
    note(tmp_path / "events.jsonl", "installed", "i1", tag="v0.3.3")

    async def scenario():
        started = asyncio.Event()

        async def hang(text: str) -> str:
            started.set()
            await asyncio.sleep(3600)
            return SENT

        d, _ = desk(tmp_path, clock, hang)
        await settle(d, clock)
        task = asyncio.create_task(d.sweep())
        await started.wait()
        began = time.monotonic()
        await d.close(0.2)
        assert time.monotonic() - began < 2.0
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_a_closed_desk_stops_looking(tmp_path, quick):
    d, _ = desk(tmp_path, Clock(T0))

    async def scenario():
        task = asyncio.create_task(d.run())
        await asyncio.sleep(0.05)
        await d.close(1.0)
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


# ---- the record (alerts.db) -------------------------------------------------------------------


def test_claims_are_once_only_and_survive_a_restart(tmp_path):
    path = tmp_path / "alerts.db"
    store = Store(path)
    assert store.claim_notices(["a", "b"], 1000.0, 300.0) == ["a", "b"]
    assert store.claim_notices(["a", "c"], 1010.0, 300.0) == ["c"]  # a is held
    store.settle_notices(["a"], "sent", 1020.0)
    store.close()
    store = Store(path)
    assert store.claim_notices(["a"], 9_999_999.0, 300.0) == []  # told is told, however late
    assert store.notice_states(["a", "b", "zz"]) == {
        "a": ("sent", 1020.0),
        "b": ("claimed", 1000.0),
    }


def test_a_stale_claim_is_taken_over_and_a_fresh_one_is_not(tmp_path):
    store = Store(tmp_path / "alerts.db")
    store.claim_notices(["a"], 1000.0, 300.0)
    assert store.claim_notices(["a"], 1299.0, 300.0) == []
    assert store.claim_notices(["a"], 1301.0, 300.0) == ["a"]
    assert store.notice_states(["a"])["a"] == ("claimed", 1301.0)


def test_releasing_gives_back_a_claim_and_never_a_told_notice(tmp_path):
    store = Store(tmp_path / "alerts.db")
    store.claim_notices(["a", "b"], 1000.0, 300.0)
    store.settle_notices(["b"], "sent", 1001.0)
    store.release_notices(["a", "b", "never-claimed"])
    assert store.notice_states(["a", "b"]) == {"b": ("sent", 1001.0)}
    assert store.claim_notices(["a"], 1002.0, 300.0) == ["a"]


def test_notice_rows_are_forgotten_after_sixty_days(tmp_path):
    path = tmp_path / "alerts.db"
    store = Store(path)
    day = 86400.0
    store.settle_notices(["old"], "sent", 1_000_000.0)
    store.settle_notices(["new"], "sent", 1_000_000.0 + 40 * day)
    store.prune(datetime.fromtimestamp(1_000_000.0 + 61 * day, IST))
    assert set(Store(path).notice_states(["old", "new"])) == {"new"}  # read from the file


def test_a_record_without_a_file_keeps_the_same_rules_in_memory(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    store = Store(blocker / "alerts.db")
    assert store.in_memory
    assert store.claim_notices(["a"], 1000.0, 300.0) == ["a"]
    assert store.claim_notices(["a"], 1001.0, 300.0) == []
    assert store.claim_notices(["a"], 1400.0, 300.0) == ["a"]  # taken over when stale
    store.settle_notices(["a"], "dropped", 1401.0)
    store.release_notices(["a"])
    assert store.notice_states(["a"]) == {"a": ("dropped", 1401.0)}
    assert store.claim_notices(["a"], 9999.0, 300.0) == []


# ---- the service ------------------------------------------------------------------------------


@pytest.fixture
def quick_service(monkeypatch):
    for name, value in {"POLL_S": 0.05, "SETTLE_S": 0.1, "RETRY_S": (0.05, 0.05)}.items():
        monkeypatch.setattr(notices, name, value)
    from sudarshan.alerts import outbox, service

    for name, value in {"CLOSED_POLL_S": 0.1, "TICK_S": 0.02, "WATCH_EVERY_S": 0.05}.items():
        monkeypatch.setattr(service, name, value)
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.05)


class Keys:
    def __init__(self):
        self.reads = 0

    def __call__(self) -> Owner:
        self.reads += 1
        return Owner(OWNER, API_KEY)


def test_the_service_tells_a_deploy_notice_while_the_alert_window_is_closed(
    settings_for, quick_service, tmp_path
):
    spool = tmp_path / "notice-spool.jsonl"  # the file settings_for points the service at
    sunday_evening = ist(2026, 10, 25, 20, 0)  # not a trading day: the order stream is not open
    clock = RunningClock(sunday_evening)
    note(
        spool, "installed", "i1", sunday_evening - timedelta(minutes=1), tag="v0.3.3", backup="yes"
    )

    async def scenario():
        async with FakeOpenAlgo() as notify:
            settings = settings_for(None, notify, SUDARSHAN_ALERTS_ALWAYS_ON="0")
            keys = Keys()
            svc = AlertService(settings, owner_reader=keys, clock=clock)
            task = asyncio.create_task(svc.run())
            await wait_until(lambda: len(notify.bodies) == 1, 10)
            body = notify.bodies[0]
            assert (body["apikey"], body["username"]) == (API_KEY, OWNER)
            assert body["message"] == (
                f"{BRAND}\nv0.3.3 installed at 19:59 IST. A backup was taken."
            )
            assert svc.state == "closed" and keys.reads >= 1
            svc.stop("test")
            await asyncio.wait_for(task, 10)
            # a new process on the same record: told is told
            again = AlertService(settings, owner_reader=Keys(), clock=clock)
            task = asyncio.create_task(again.run())
            await asyncio.sleep(0.6)
            again.stop("test")
            await asyncio.wait_for(task, 10)
            assert len(notify.bodies) == 1

    asyncio.run(scenario())


def test_turning_order_alerts_off_stops_the_notices_too(settings_for, quick_service, tmp_path):
    spool = tmp_path / "notice-spool.jsonl"
    clock = RunningClock(ist(2026, 10, 25, 20, 0))
    note(spool, "installed", "i1", clock() - timedelta(minutes=1), tag="v0.3.3")

    async def scenario():
        async with FakeOpenAlgo() as notify:
            settings = settings_for(None, notify, SUDARSHAN_ORDER_ALERTS="off")
            await asyncio.wait_for(
                AlertService(settings, owner_reader=Keys(), clock=clock).run(), 5
            )
            assert notify.bodies == []

    asyncio.run(scenario())


def test_the_spool_list_comes_from_the_settings(settings_for):
    assert settings_for().notice_spools == (tmp := settings_for().notice_spools) and len(tmp) == 1
    assert settings_for(SUDARSHAN_NOTICE_SPOOLS="/a/one:/b/two::  :/c").notice_spools == (
        Path("/a/one"), Path("/b/two"), Path("/c"),
    )  # fmt: skip
    assert settings_for(SUDARSHAN_NOTICE_SPOOLS="").notice_spools == DEFAULT_SPOOLS
    assert isinstance(settings_for(), Settings)


class MovableClock(RunningClock):
    """Real time's pace from a start the test can move on, as the day goes by."""

    def jump_to(self, at: datetime) -> None:
        self.start, self._began = at, time.monotonic()


def test_the_service_keeps_a_notice_for_the_close_but_tells_a_failure_at_once(
    settings_for, quick_service, tmp_path
):
    spool = tmp_path / "notice-spool.jsonl"
    clock = MovableClock(ist(2026, 10, 21, 10, 0))  # a Wednesday: the market trades

    async def scenario():
        async with FakeOpenAlgo() as notify:
            settings = settings_for(None, notify, SUDARSHAN_ALERTS_ALWAYS_ON="0")
            svc = AlertService(settings, owner_reader=Keys(), clock=clock)
            note(spool, "waiting", "w1", clock(), tag="v0.3.4")
            task = asyncio.create_task(svc.run())
            await asyncio.sleep(0.6)  # a dozen looks at this fixture's timings
            assert notify.bodies == []
            note(spool, "rolled_back", "r1", clock(), "run-r", tag="v0.3.5", was="v0.3.3",
                 why="health", up="yes")  # fmt: skip
            await wait_until(lambda: len(notify.bodies) == 1, 10)
            first = notify.bodies[0]["message"]
            assert "Could not install v0.3.5" in first and "v0.3.4" not in first
            clock.jump_to(ist(2026, 10, 21, 15, 41))  # the market has closed
            await wait_until(lambda: len(notify.bodies) == 2, 10)
            assert "v0.3.4 is ready" in notify.bodies[1]["message"]
            svc.stop("test")
            await asyncio.wait_for(task, 10)

    asyncio.run(scenario())
