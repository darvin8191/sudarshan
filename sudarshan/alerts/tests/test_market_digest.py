"""List alerts on Telegram (M8.14): the first alert at once, then one digest a window."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest
from alerts_fakes import ist, wait_until

from sudarshan.alerts import outbox
from sudarshan.alerts.market import lane, words
from sudarshan.alerts.market.lane import Group, Line, MarketLane
from sudarshan.alerts.outbox import MARKET, SENT, Pacer

AT = ist(2026, 11, 9, 10, 40, 0)
WINDOW = 0.6  # a test's "5 minutes"


def swing(window: float = WINDOW) -> Group:
    return Group(7, "Swing list · Up 2% on the day", window)


def fire(n: int, at=AT, group: Group | None = None) -> Line:
    text = f"Alert: S{n} up 2.{n % 10}% on the day: ₹{100 + n}.00 (previous close ₹98.00) (NSE)"
    return Line(n, text, at, group if group is not None else swing())


class Sender:
    def __init__(self):
        self.sent: list[tuple[float, str]] = []

    async def __call__(self, text: str):
        self.sent.append((time.monotonic(), text))
        return SENT, ""


class Results:
    def __init__(self):
        self.got: list[tuple[list[int], str, str]] = []

    def __call__(self, ids, status, reason):
        self.got.append((list(ids), status, reason))

    def ids(self, status: str) -> list[int]:
        return sorted(i for ids, s, _ in self.got if s == status for i in ids)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    for name, value in {"COALESCE_S": 0.05, "MIN_GAP_S": 0.05}.items():
        monkeypatch.setattr(outbox, name, value)
    monkeypatch.setattr(lane, "COALESCE_S", 0.05)
    monkeypatch.setattr(lane, "POLL_S", 0.01)


def make(clock=lambda: AT, pacer=None):
    sender, results = Sender(), Results()
    return MarketLane(sender, clock, pacer or Pacer(), results), sender, results


async def _go(market):
    return asyncio.create_task(market.run())


async def _stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_the_first_alert_of_a_list_goes_within_the_burst_not_after_the_window():
    """Mutation target: holding the first alert for the window (the old 5-minute digest)."""

    async def scenario():
        market, sender, results = make()
        task = await _go(market)
        try:
            started = time.monotonic()
            market.add([fire(1)])
            await wait_until(lambda: sender.sent, 2)
            assert sender.sent[0][0] - started < 0.3 < WINDOW
            assert sender.sent[0][1].split("\n")[0] == "सुदर्शन / SUDARSHAN"
            assert "list ·" not in sender.sent[0][1]  # the first goes as any market alert does
            await wait_until(lambda: results.ids("sent") == [1], 2)
        finally:
            await _stop(task)

    asyncio.run(scenario())


def test_seven_later_fires_inside_the_window_make_one_message_at_its_end():
    async def scenario():
        market, sender, results = make()
        task = await _go(market)
        try:
            market.add([fire(1)])
            await wait_until(lambda: sender.sent, 2)
            first_sent = sender.sent[0][0]
            market.add([fire(n) for n in range(2, 9)])
            await asyncio.sleep(WINDOW / 2)
            assert len(sender.sent) == 1  # nothing inside the window
            await wait_until(lambda: len(sender.sent) == 2, 3)
            at, text = sender.sent[1]
            assert WINDOW - 0.1 <= at - first_sent <= WINDOW + 0.5  # the window's end
            lines = text.split("\n")
            assert lines[0] == "सुदर्शन / SUDARSHAN"
            assert lines[1] == "Swing list · Up 2% on the day: 7 alerts, 10:40 IST"
            assert len(lines) == 2 + 7 and lines[2].startswith("S2 up")  # "Alert: " dropped
            assert "more on the Alerts page" not in text
            await wait_until(lambda: results.ids("sent") == list(range(1, 9)), 2)
        finally:
            await _stop(task)

    asyncio.run(scenario())


def test_twenty_later_fires_make_fifteen_lines_and_five_more():
    async def scenario():
        market, sender, results = make()
        task = await _go(market)
        try:
            market.add([fire(1)])
            await wait_until(lambda: sender.sent, 2)
            market.add([fire(n) for n in range(2, 22)])
            await wait_until(lambda: len(sender.sent) == 2, 3)
            lines = sender.sent[1][1].split("\n")
            assert lines[1] == "Swing list · Up 2% on the day: 20 alerts, 10:40 IST"
            assert len(lines) == 2 + 15 + 1 and lines[-1] == "5 more on the Alerts page."
            await wait_until(lambda: len(results.got) >= 3, 2)
            assert results.ids("sent") == list(range(1, 17))  # the first, and the 15 listed
            assert results.ids("not_sent") == list(range(17, 22))  # the 5 the history keeps
            assert {r for _, s, r in results.got if s == "not_sent"} == {lane.UNLISTED}
        finally:
            await _stop(task)

    asyncio.run(scenario())


def test_a_window_with_nothing_waiting_sends_nothing_and_the_next_fire_is_first_again():
    async def scenario():
        market, sender, results = make()
        task = await _go(market)
        try:
            market.add([fire(1)])
            await wait_until(lambda: sender.sent, 2)
            await asyncio.sleep(WINDOW + 0.3)
            assert len(sender.sent) == 1 and not market._windows
            again = time.monotonic()
            market.add([fire(2)])
            await wait_until(lambda: len(sender.sent) == 2, 2)
            assert sender.sent[1][0] - again < 0.4 and "list ·" not in sender.sent[1][1]
        finally:
            await _stop(task)

    asyncio.run(scenario())


def test_a_window_that_ends_with_a_digest_sent_opens_the_next_from_that_send():
    async def scenario():
        market, sender, results = make()
        task = await _go(market)
        try:
            market.add([fire(1)])
            await wait_until(lambda: sender.sent, 2)
            market.add([fire(2)])
            await wait_until(lambda: len(sender.sent) == 2, 3)
            digest_sent = sender.sent[1][0]
            market.add([fire(3)])  # inside the next window: buffered, not first
            await asyncio.sleep(0.2)
            assert len(sender.sent) == 2
            await wait_until(lambda: len(sender.sent) == 3, 3)
            assert 0.4 <= sender.sent[2][0] - digest_sent <= WINDOW + 0.5
            assert "Swing list" in sender.sent[2][1] and "1 alert," in sender.sent[2][1]
        finally:
            await _stop(task)

    asyncio.run(scenario())


def test_150_fires_in_a_burst_make_one_message_of_15_lines_and_the_rest_wait():
    """A gap-up morning: the queue holds the first message's lines, never 150 (or the
    lane's MAX_PENDING 200), and past 200 buffered only a count is kept."""

    async def scenario():
        market, sender, results = make()
        market.add([fire(n) for n in range(1, 261)])
        assert len(market) == 15  # the first message; 245 went to the window's buffer
        task = await _go(market)
        try:
            await wait_until(lambda: sender.sent, 2)
            assert len(sender.sent[0][1].split("\n")) == 1 + 15 + 1  # header, 15 lines, time
            assert len(market) == 0
            await wait_until(lambda: len(sender.sent) == 2, 3)
            window_lines = sender.sent[1][1].split("\n")
            assert window_lines[1].endswith(": 245 alerts, 10:40 IST")
            assert window_lines[-1] == "230 more on the Alerts page."  # 185 unlisted + 45 past 200
            too_many = [i for ids, _, r in results.got if r == lane.TOO_MANY_IN_WINDOW for i in ids]
            assert sorted(too_many) == list(range(216, 261))  # the 45 past the buffer's 200
        finally:
            await _stop(task)

    asyncio.run(scenario())


def test_the_queue_holds_one_composed_message_for_a_window_never_the_buffer():
    market, _, _ = make()
    market.add([fire(n) for n in range(1, 16)])  # the first message's 15
    market.add([fire(n) for n in range(16, 216)])  # 200 buffered
    assert len(market) == 15
    market._tick(force=True)
    assert len(market) == 16  # plus one composed digest: not 200 more entries
    assert sum(1 for p in market._pending.values() if p.ids) == 1


def test_a_digest_composed_at_10_45_for_fires_from_10_40_is_sent_not_dropped():
    """The 5-minute age limit counts from the digest's composition, never from the first fire."""

    async def scenario():
        now = {"t": AT}
        market, sender, results = make(lambda: now["t"])
        task = await _go(market)
        try:
            market.add([fire(1)])
            await wait_until(lambda: sender.sent, 2)
            market.add([fire(2, AT), fire(3, AT + timedelta(minutes=4, seconds=50))])
            now["t"] = AT + timedelta(minutes=5, seconds=10)  # composed at 10:45:10
            await wait_until(lambda: len(sender.sent) == 2, 3)
            assert "2 alerts, 10:40-10:44 IST" in sender.sent[1][1]
            await wait_until(lambda: results.ids("sent") == [1, 2, 3], 2)
            assert results.ids("not_sent") == []
        finally:
            await _stop(task)

    asyncio.run(scenario())


def test_an_order_fill_during_a_window_goes_first_and_the_digest_waits_for_it():
    async def scenario():
        pacer = Pacer()
        waiting = {"orders": False}
        pacer.orders_waiting = lambda: waiting["orders"]
        market, sender, results = make(pacer=pacer)
        task = await _go(market)
        try:
            market.add([fire(1)])
            await wait_until(lambda: sender.sent, 2)
            market.add([fire(2)])
            waiting["orders"] = True  # a fill is waiting when the window ends
            await asyncio.sleep(WINDOW + 0.4)
            assert len(sender.sent) == 1  # the digest is composed and held behind the fill
            assert any(p.ids for p in market._pending.values())
            waiting["orders"] = False
            await wait_until(lambda: len(sender.sent) == 2, 2)
        finally:
            await _stop(task)

    asyncio.run(scenario())


def test_a_group_each_at_once_and_an_ordinary_alert_are_not_held():
    async def scenario():
        market, sender, results = make()
        task = await _go(market)
        try:
            market.add([fire(1, group=swing(0)), fire(2, group=swing(0))])
            plain = Line(3, "Alert: SBIN up (NSE)", AT)
            market.add([plain])
            await wait_until(lambda: results.ids("sent") == [1, 2, 3], 2)
            assert len(sender.sent) == 1 and not market._windows
        finally:
            await _stop(task)

    asyncio.run(scenario())


def test_two_lists_keep_their_own_windows():
    async def scenario():
        market, sender, results = make()
        other = Group(8, "Gap list · Up 3% on the day", WINDOW)
        task = await _go(market)
        try:
            market.add([fire(1), fire(2, group=other)])
            await wait_until(lambda: sender.sent, 2)
            market.add([fire(3), fire(4, group=other)])
            await wait_until(lambda: len(sender.sent) == 3, 3)
            texts = " ".join(t for _, t in sender.sent[1:])
            assert "Swing list" in texts and "Gap list" in texts
            assert results.ids("sent") == [1, 2, 3, 4]
        finally:
            await _stop(task)

    asyncio.run(scenario())


def test_a_stop_sends_what_a_window_holds_without_waiting_for_its_end():
    async def scenario():
        market, sender, results = make()
        task = await _go(market)
        try:
            market.add([fire(1)])
            await wait_until(lambda: sender.sent, 2)
            market.add([fire(2), fire(3)])
            assert await market.flush(2.0) == 0
            assert len(sender.sent) == 2 and "2 alerts" in sender.sent[1][1]
        finally:
            await _stop(task)

    asyncio.run(scenario())


def test_the_digest_words_for_a_trader():
    when = ist(2026, 11, 9, 10, 40, 5)
    text = words.digest("Swing list · Up 2% on the day", ["Alert: RELIANCE up 2.03% (NSE)"], when,
                        when + timedelta(minutes=5), 1)  # fmt: skip
    assert text == (
        "सुदर्शन / SUDARSHAN\n"
        "Swing list · Up 2% on the day: 2 alerts, 10:40-10:45 IST\n"
        "RELIANCE up 2.03% (NSE)\n"
        "1 more on the Alerts page."
    )
    one = words.digest("Swing list · Up 2% on the day", ["Alert: SBIN up (NSE)"], when, when)
    assert "1 alert, 10:40 IST" in one and "more on the Alerts page" not in one
    assert words.group_title("Swing", "move", {"direction": "up", "base": "close", "percent": "2"}
                             ) == "Swing list · Up 2% on the day"  # fmt: skip
    assert (
        words.group_title("Swing", "vwap", {"direction": "either"}) == "Swing list · Crosses VWAP"
    )
    assert words.group_title("Swing", "day_break", {"side": "high", "after": "09:30"}) == (
        "Swing list · Breaks today's high after 09:30")  # fmt: skip


def test_a_window_whose_waiting_lines_all_went_too_old_ends_and_sends_its_buffer():
    """Review L5: a group whose first 15 waiting lines were all dropped as too old (Telegram
    unreachable for 5 minutes) left a window with a buffer and no end; its lines went out only
    after the group's next fire plus a window, however old by then."""
    now = [AT]
    market, _, results = make(clock=lambda: now[0])
    market.add([fire(n) for n in range(1, 16)])  # the queue's first message of the group
    market.add([fire(16)])  # past the first 15: waits in the group's window
    window = market._windows[7]
    assert window.end is None and [line.event_id for line in window.buffer] == [16]
    now[0] = AT + lane.MAX_AGE + timedelta(seconds=1)
    market._expire()  # Telegram never answered: the 15 are too old
    assert results.ids("not_sent") == list(range(1, 16))
    assert window.end is not None and window.end <= time.monotonic()  # it ends now
    market._tick()
    assert window.buffer == []
    assert [list(line.ids) for line in market._pending.values()] == [[16]]  # one digest waits


def test_a_window_whose_waiting_lines_were_dropped_for_room_ends_too():
    market, _, results = make()
    market.add([fire(n) for n in range(1, 16)])
    market.add([fire(16)])
    others = [Line(1000 + n, f"Alert: X{n} up", AT) for n in range(lane.MAX_PENDING)]
    market.add(others)  # the oldest, the group's 15, give way
    assert set(range(1, 16)) <= set(results.ids("not_sent"))
    assert market._windows[7].end is not None
    market._tick()
    assert any(list(line.ids) == [16] for line in market._pending.values())
