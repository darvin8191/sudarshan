"""Bursts merge, the pace holds, failures retry or drop; and the request OpenAlgo receives."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import httpx
import pytest
from alerts_fakes import API_KEY, NEW_API_KEY, OWNER, FakeNotify, ist, wait_until

from sudarshan.alerts import outbox
from sudarshan.alerts.orders import Alert
from sudarshan.alerts.outbox import DROP, RETRY, SENT, UNCONFIRMED, OpenAlgoTelegram, Outbox
from sudarshan.alerts.owner import Owner

AT = ist(2026, 10, 21, 10, 42, 5)


def alert(key: str, kind: str = "filled", filled: int = 65, at=AT) -> Alert:
    return Alert(key, kind, filled, f"{kind.capitalize()}: order {key} {filled}", at)


class Recorder:
    """A send() that records texts and times and answers from a script (then SENT)."""

    def __init__(self, *outcomes: str):
        self.outcomes = list(outcomes)
        self.texts: list[str] = []
        self.times: list[float] = []

    async def __call__(self, text: str) -> str:
        self.texts.append(text)
        self.times.append(time.monotonic())
        return self.outcomes.pop(0) if self.outcomes else SENT


async def _run(box: Outbox, until, timeout: float = 5.0) -> None:
    task = asyncio.create_task(box.run())
    try:
        await wait_until(until, timeout)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _box(send, now=AT) -> Outbox:
    return Outbox(send, lambda: now)


def test_a_burst_of_four_fills_is_one_message(fast):
    async def scenario():
        send = Recorder()
        box = _box(send)
        box.add([alert("a"), alert("b")])
        box.add([alert("c")])
        box.add([alert("d")])
        await _run(box, lambda: send.texts and not len(box))
        await asyncio.sleep(0.2)
        assert len(send.texts) == 1
        assert send.texts[0].split("\n")[1:5] == [
            "Filled: order a 65", "Filled: order b 65", "Filled: order c 65", "Filled: order d 65",
        ]  # fmt: skip

    asyncio.run(scenario())


def test_one_line_per_order_with_its_newest_state(fast):
    async def scenario():
        send = Recorder()
        box = _box(send)
        box.add([alert("a", "part", 20), alert("b", "part", 10), alert("a", "part", 40)])
        box.add([alert("a", "filled", 65)])
        await _run(box, lambda: send.texts and not len(box))
        assert send.texts[0].split("\n")[1:3] == ["Filled: order a 65", "Part: order b 10"]

    asyncio.run(scenario())


def test_the_pace_and_the_per_minute_cap(fast, monkeypatch):
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.2)
    monkeypatch.setattr(outbox, "PER_MINUTE", 3)

    async def scenario():
        send = Recorder()
        box = _box(send)
        task = asyncio.create_task(box.run())
        for n in range(4):
            box.add([alert(f"k{n}")])
            await wait_until(lambda n=n: len(send.texts) > n or n == 3, 3)
        await asyncio.sleep(0.5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        gaps = [b - a for a, b in zip(send.times, send.times[1:], strict=False)]
        assert len(send.texts) == 3 and all(gap >= 0.19 for gap in gaps)  # 4th: next minute
        assert len(box) == 1

    asyncio.run(scenario())


def test_long_bursts_are_split_by_lines_and_characters(fast, monkeypatch):
    monkeypatch.setattr(outbox, "MAX_LINES", 4)

    async def scenario():
        send = Recorder()
        box = _box(send)
        box.add([alert(f"x{n}") for n in range(10)])
        await _run(box, lambda: len(send.texts) == 3)
        assert [len(t.split("\n")) - 2 for t in send.texts] == [4, 4, 2]
        monkeypatch.setattr(outbox, "MAX_LINES", 100)
        monkeypatch.setattr(outbox, "MAX_CHARS", 120)
        long_send = Recorder()
        long_box = _box(long_send)
        long_box.add([Alert(f"y{n}", "filled", 1, "Filled: " + "z" * 40, AT) for n in range(6)])
        await _run(long_box, lambda: not len(long_box))
        assert all(len(t) <= 120 for t in long_send.texts) and len(long_send.texts) > 1

    asyncio.run(scenario())


def test_failures_retry_then_succeed_and_drops_are_final(fast):
    async def scenario():
        send = Recorder(RETRY, RETRY)
        box = _box(send)
        box.add([alert("r")])
        await _run(box, lambda: len(send.texts) == 3)
        assert send.texts[0] == send.texts[2]  # the same message, sent once it could be
        assert box.stats["messages"] == 1
        dropped = Recorder(DROP)
        box = _box(dropped)
        box.add([alert("d")])
        await _run(box, lambda: dropped.texts and not len(box))
        await asyncio.sleep(0.2)
        assert len(dropped.texts) == 1 and box.stats["failed"] == 1

    asyncio.run(scenario())


def test_an_unconfirmed_message_is_tried_a_few_times_only(fast):
    async def scenario():
        send = Recorder(*[UNCONFIRMED] * 10)
        box = _box(send)
        box.add([alert("u")])
        await _run(box, lambda: len(send.texts) >= outbox.UNCONFIRMED_TRIES and not len(box))
        await asyncio.sleep(0.3)
        assert len(send.texts) == outbox.UNCONFIRMED_TRIES

    asyncio.run(scenario())


def test_a_newer_state_wins_over_a_message_put_back(fast):
    async def scenario():
        box = None

        async def send(text):
            if "part" in text.lower() and box is not None:
                box.add([alert("a", "filled", 65)])  # arrives while the first try fails
                return RETRY
            texts.append(text)
            return SENT

        texts: list[str] = []
        box = _box(send)
        box.add([alert("a", "part", 20)])
        await _run(box, lambda: texts and not len(box))
        assert texts == [texts[0]] and "Filled: order a 65" in texts[0]

    asyncio.run(scenario())


def test_old_alerts_are_dropped_after_the_age_limit(fast):
    async def scenario():
        send = Recorder(RETRY, RETRY, RETRY, RETRY)
        box = _box(send, now=AT + outbox.MAX_AGE + timedelta(seconds=1))
        box.add([alert("o")])
        await _run(box, lambda: send.texts and not len(box))
        assert box.stats["failed"] == 1 and len(send.texts) == 1

    asyncio.run(scenario())


def test_memory_is_bounded_and_the_overflow_is_told(fast, monkeypatch):
    monkeypatch.setattr(outbox, "MAX_PENDING", 5)
    monkeypatch.setattr(outbox, "MAX_LINES", 50)

    async def scenario():
        send = Recorder()
        box = _box(send)
        box.add([alert(f"m{n}") for n in range(8)])
        assert len(box) == 5
        await _run(box, lambda: send.texts and not len(box))
        text = send.texts[0]
        assert "Filled: order m3 65" in text and "Filled: order m2 65" not in text
        assert "3 more order updates could not be listed here." in text

    asyncio.run(scenario())


def test_flush_sends_without_the_burst_pause(fast, monkeypatch):
    monkeypatch.setattr(outbox, "COALESCE_S", 30.0)

    async def scenario():
        send = Recorder()
        box = _box(send)
        task = asyncio.create_task(box.run())
        box.add([alert("f")])
        assert await box.flush(2.0) == 0 and len(send.texts) == 1
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


# -- the request to OpenAlgo ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (FakeNotify.SENT, SENT),
        (FakeNotify.QUEUED, UNCONFIRMED),
        (FakeNotify.STOPPED, DROP),
        (FakeNotify.NOT_LINKED, DROP),
        (FakeNotify.DOWN, RETRY),
        ((429, {"error": "ratelimit exceeded"}), RETRY),
        ((400, {"status": "error"}), DROP),
    ],
)
def test_openalgo_answers(answer, outcome):
    async def scenario():
        async with FakeNotify() as fake, httpx.AsyncClient(trust_env=False) as client:
            fake.default = answer
            owner = Owner(OWNER, API_KEY)
            telegram = OpenAlgoTelegram(fake.url, client, lambda: owner, lambda: owner)
            assert await telegram.send("सुदर्शन / SUDARSHAN\nFilled: x") == outcome
            assert fake.paths == ["/api/v1/telegram/notify"]
            assert fake.bodies[0] == {
                "apikey": API_KEY,
                "username": OWNER,
                "message": "सुदर्शन / SUDARSHAN\nFilled: x",
                "wait_for_delivery": True,
            }

    asyncio.run(scenario())


def test_a_refused_key_is_read_again_and_the_new_one_used():
    async def scenario():
        async with FakeNotify(key=NEW_API_KEY) as fake, httpx.AsyncClient(trust_env=False) as c:
            old, new = Owner(OWNER, API_KEY), Owner(OWNER, NEW_API_KEY)
            reloads = []
            telegram = OpenAlgoTelegram(fake.url, c, lambda: old, lambda: reloads.append(1) or new)
            assert await telegram.send("text") == SENT
            assert [b["apikey"] for b in fake.bodies] == [API_KEY, NEW_API_KEY] and reloads
            # Still refused after reading again: retried later, never with a guessed key.
            stale = OpenAlgoTelegram(fake.url, c, lambda: old, lambda: old)
            assert await stale.send("text") == RETRY

    asyncio.run(scenario())


def test_openalgo_not_running_is_a_retry():
    async def scenario():
        async with httpx.AsyncClient(trust_env=False) as client:
            owner = Owner(OWNER, API_KEY)
            telegram = OpenAlgoTelegram("http://127.0.0.1:9", client, lambda: owner, lambda: owner)
            assert await telegram.send("text") == RETRY
            nobody = OpenAlgoTelegram("http://127.0.0.1:9", client, lambda: None, lambda: None)
            assert await nobody.send("text") == RETRY

    asyncio.run(scenario())
