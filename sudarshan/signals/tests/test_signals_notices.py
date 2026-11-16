"""T9: "has not published" and the other TRINETRA notices: decided (signals/notices.py), recorded
(ops/events.py, src signals) and worded by the alerts' notice desk (alerts/notices.py)."""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, time, timedelta, timezone

import pytest
from signals_fakes import KEYS, Box, document, free_port, settings_for, until

from sudarshan.alerts import notices as desk
from sudarshan.ops import events
from sudarshan.signals import contract as c
from sudarshan.signals import notices
from sudarshan.signals.notices import Memory
from sudarshan.signals.service import Service

IST = timezone(timedelta(hours=5, minutes=30))
MONDAY = date(2026, 11, 16)
MIN = 60_000


def at(hh: int, mm: int, day: date = MONDAY) -> int:
    return int(datetime.combine(day, time(hh, mm), IST).timestamp() * 1000)


def trading(day) -> bool:
    return day.weekday() < 5


def market(moment: datetime) -> bool:
    local = moment.astimezone(IST)
    return trading(local.date()) and time(9, 15) <= local.time() < time(15, 40)


def last_doc(published: int, fresh_for: int = 6 * MIN, stopping=False, received=None) -> dict:
    return {"published_ms": published, "fresh_until_ms": published + fresh_for,
            "received_ms": published if received is None else received, "stopping": stopping}  # fmt: skip


def due(memory=Memory(), *, now, last, newer=None):
    return notices.due(memory, now_ms=now, last=last, newer_format_ms=newer,
                       is_trading_day=trading, in_market_hours=market)  # fmt: skip


def kinds(found) -> list[str]:
    return [n.kind for n in found[0]]


def test_quiet_is_told_once_at_0920_on_a_trading_day():
    yesterday = last_doc(at(15, 30, MONDAY - timedelta(days=3)))  # Friday's last reading
    assert kinds(due(now=at(9, 19), last=yesterday)) == []
    found, memory = due(now=at(9, 20), last=yesterday)
    assert [(n.kind, n.ident) for n in found] == [("signals_quiet", "signals-quiet-20261116")]
    assert memory.quiet_day == 20261116 and memory.silent_since == yesterday["published_ms"]
    assert kinds(due(memory, now=at(9, 40), last=yesterday)) == [], "once a day, and no stale"
    assert kinds(due(now=at(9, 20, date(2026, 11, 21)), last=yesterday)) == []  # a Saturday
    assert kinds(due(now=at(9, 20), last=None)) == [], "never connected: nothing to tell"
    today = last_doc(at(8, 31))
    assert kinds(due(now=at(9, 20), last=today)) == []  # it published after 08:30


def test_stale_in_market_hours_for_ten_minutes():
    reading = last_doc(at(10, 0))  # fresh until 10:06
    assert kinds(due(now=at(10, 15), last=reading)) == []
    found, memory = due(now=at(10, 16), last=reading)
    assert [(n.kind, n.facts) for n in found] == [
        ("signals_stale", {"since": str(at(10, 0) // 1000)})]  # fmt: skip
    assert kinds(due(memory, now=at(11, 0), last=reading)) == [], "once a spell"
    assert kinds(due(now=at(16, 0), last=reading)) == []  # after the close
    early = last_doc(at(8, 50))  # stale from 08:56: the market has to run 10 minutes first
    assert kinds(due(Memory(quiet_day=20261116), now=at(9, 24), last=early)) == []
    assert kinds(due(Memory(quiet_day=20261116), now=at(9, 25), last=early)) == ["signals_stale"]
    stopped = last_doc(at(10, 0), stopping=True)
    assert kinds(due(now=at(11, 0), last=stopped)) == [], "a planned stop is not a silence"


def test_its_return_is_told_once():
    old = last_doc(at(10, 0))
    _, memory = due(now=at(10, 16), last=old)
    new = last_doc(at(10, 40))
    found, memory = due(memory, now=at(10, 40) + 500, last=new)
    assert [(n.kind, n.facts) for n in found] == [
        ("signals_back", {"since": str(at(10, 40) // 1000)})]  # fmt: skip
    assert memory.silent_since is None
    assert kinds(due(memory, now=at(10, 41), last=new)) == []


def test_a_newer_format_once_a_day():
    reading = last_doc(at(10, 0))
    found, memory = due(now=at(10, 1), last=reading, newer=at(10, 0))
    assert kinds((found, memory)) == ["signals_format"]
    assert "signals_format" not in kinds(due(memory, now=at(12, 0), last=reading, newer=at(11, 0)))
    assert kinds(due(now=at(10, 1), last=reading, newer=at(10, 0) - 86_400_000)) == []


def test_tell_records_each_once_in_the_spool(tmp_path):
    spool = tmp_path / "events.jsonl"
    found, _ = due(now=at(9, 20), last=last_doc(at(15, 30, MONDAY - timedelta(days=3))))
    assert notices.tell(spool, found, at(9, 20)) == found
    assert notices.tell(spool, found, at(9, 21)) == found  # already there counts as told
    (event,) = events.read_events(spool)
    assert (event.src, event.kind, event.id) == (
        "signals",
        "signals_quiet",
        "signals-quiet-20261116",
    )
    assert events.SIGNALS_SPOOL in events.DEFAULT_SPOOLS
    assert events.SIGNALS_SPOOL.parent.name == "sudarshan-signals"


def _event(kind: str, ident: str, at_s: float, **facts) -> events.Event:
    return events.Event(ident, kind, at_s, ident, "signals", facts)


@pytest.mark.parametrize(
    ("kind", "facts", "said"),
    [
        ("signals_quiet", {}, "TRINETRA has not published today."),
        ("signals_stale", {"since": str(at(10, 42) // 1000)},
         "TRINETRA stopped publishing at 10:42 IST. SUDARSHAN cannot restart it; see TRINETRA's "
         "own runbook."),
        ("signals_back", {"since": str(at(11, 5) // 1000)},
         "TRINETRA is publishing again since 11:05 IST."),
        ("signals_format", {}, c.WORDS["newer_format"]),
    ],
)  # fmt: skip
def test_the_desk_words_each_notice_as_the_contract_does(kind, facts, said):
    now = datetime.fromtimestamp(at(12, 0) / 1000, IST)
    lines = desk.batch_lines([_event(kind, f"{kind}-1", at(12, 0) / 1000, **facts)], now)
    assert lines == [said]
    text = lines[0]
    low = text.lower()
    assert text.endswith(".") and all(ord(ch) < 0x2000 for ch in text)
    assert not re.search(r"\b[1-5]\d\d\b", text) and not re.search(r"\blive\b", low)
    for word in ("http", "exception", "json", "letterbox", "/var/", "systemd", "real-time"):
        assert word not in low
    assert kind in desk.KIND_ORDER and kind in events.KINDS
    assert desk._at_once(_event(kind, "x", 1.0)) is (kind != "signals_format")


def test_a_since_that_is_not_a_time_falls_back_to_the_events_own():
    now = datetime.fromtimestamp(at(12, 0) / 1000, IST)
    event = _event("signals_stale", "s-1", at(10, 42) / 1000, since="soon")
    assert desk.batch_lines([event], now) == [
        "TRINETRA stopped publishing at 10:42 IST. SUDARSHAN cannot restart it; see TRINETRA's "
        "own runbook."
    ]


def test_the_service_records_a_stale_spell_in_its_spool(tmp_path):
    port = free_port()
    box = Box(tmp_path / "in")
    svc = Service(settings_for(tmp_path, port), KEYS, hooks={}, jobs={}, letterbox=box.reader,
                  scan_s=0.02, setup_every_s=0.05)  # fmt: skip
    clock = {"now": at(10, 0) + 500}
    svc.clock = lambda: clock["now"]
    box.drop(document(at=at(10, 0)), written_ms=at(10, 0))
    spool = tmp_path / "state" / c.EVENTS_FILE

    async def scenario():
        task = asyncio.create_task(svc.run())
        await until(lambda: svc.last is not None)
        assert not spool.exists()
        clock["now"] = at(10, 17)
        await until(lambda: spool.exists())
        svc.stop()
        await asyncio.wait_for(task, 10)

    asyncio.run(scenario())
    (event,) = events.read_events(spool)
    assert event.kind == "signals_stale" and event.fact("since") == str(at(10, 0) // 1000)
    assert svc.notice_memory["silent_since"] == at(10, 0)
