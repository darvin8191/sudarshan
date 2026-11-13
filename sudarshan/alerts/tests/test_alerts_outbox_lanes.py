"""Telegram lanes (M10.5): fills first through a market storm, minute budgets, results per message,
the 5-minute limit for market alerts, and the market lane's own wording."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest
from alerts_fakes import ist, wait_until

from sudarshan.alerts import outbox
from sudarshan.alerts.market import lane
from sudarshan.alerts.market.lane import Line, MarketLane
from sudarshan.alerts.orders import Alert
from sudarshan.alerts.outbox import DROP, MARKET, ORDERS, RETRY, SENT, UNCONFIRMED, Outbox, Pacer

AT = ist(2026, 10, 21, 10, 42, 5)


def fill(key: str) -> Alert:
    return Alert(key, "filled", 65, f"Filled: order {key} 65", AT)


def line(n: int, at=AT) -> Line:
    return Line(n, f"Alert: SBIN crossed above ₹{100 + n}.00: now ₹{100 + n}.05 (NSE)", at)


class Sender:
    """Records each message's text, lane and start time; answers from a script, then SENT."""

    def __init__(self, *outcomes: tuple[str, str], hold_s: float = 0.0):
        self.outcomes, self.hold_s = list(outcomes), hold_s
        self.sent: list[tuple[float, str, str]] = []

    def lane(self, name: str):
        async def send(text: str):
            self.sent.append((time.monotonic(), name, text))
            if self.hold_s and name == MARKET:
                await asyncio.sleep(self.hold_s)  # Telegram slow to answer a market message
            outcome = self.outcomes.pop(0) if self.outcomes else (SENT, "")
            return outcome if name == MARKET else outcome[0]

        return send


class Results:
    def __init__(self):
        self.got: list[tuple[list[int], str, str]] = []

    def __call__(self, ids, status, reason):
        self.got.append((list(ids), status, reason))


@pytest.fixture
def quick(monkeypatch):
    for name, value in {"COALESCE_S": 0.05, "MIN_GAP_S": 0.1, "RETRY_S": (0.05, 0.05)}.items():
        monkeypatch.setattr(outbox, name, value)
    for name, value in {"COALESCE_S": 0.05, "RETRY_S": (0.05, 0.05), "POLL_S": 0.01}.items():
        monkeypatch.setattr(lane, name, value)


async def _running(*boxes):
    return [asyncio.create_task(box.run()) for box in boxes]


async def _cancel(tasks) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def test_a_fill_goes_out_within_the_burst_pause_and_one_gap_during_a_market_storm(quick):
    """A storm keeps the market lane busy, and one market message hangs on Telegram for 2 s.
    The fill still starts within COALESCE_S + MIN_GAP_S of arriving, and no market message
    starts while it waits."""

    async def scenario():
        sender, results, pacer = Sender(hold_s=2.0), Results(), Pacer()
        orders = Outbox(sender.lane(ORDERS), lambda: AT, pacer)
        market = MarketLane(sender.lane(MARKET), lambda: AT, pacer, results)
        tasks = await _running(orders, market)
        try:
            for n in range(40):
                market.add([line(n)])
                await asyncio.sleep(0.01)
            await wait_until(lambda: any(lane_ == MARKET for _, lane_, _ in sender.sent), 2)
            arrived = time.monotonic()
            orders.add([fill("f1")])
            await wait_until(lambda: any(lane_ == ORDERS for _, lane_, _ in sender.sent), 2)
            started = next(t for t, lane_, _ in sender.sent if lane_ == ORDERS)
            assert started - arrived <= outbox.COALESCE_S + outbox.MIN_GAP_S + 0.3  # not 2 s
            between = [
                t for t, lane_, _ in sender.sent if lane_ == MARKET and arrived < t < started
            ]
            assert between == []  # the market lane gave way while the fill waited
        finally:
            await _cancel(tasks)

    asyncio.run(scenario())


def _windows_hold(starts: list[tuple[float, str]]) -> bool:
    """At most 5 starts, 3 of them market, in every one-second window that opens at a start."""
    for t0, _ in starts:
        window = [lane_ for t, lane_ in starts if t0 <= t < t0 + 1.0]
        if len(window) > 5 or window.count(MARKET) > 3:
            return False
    return True


def _budgets_run(monkeypatch, late_first_s: float = 0.0):
    """Fills arriving into a market storm under small budgets. Returns the starts as the pacer
    recorded them (the times its budgets are kept on) and as the sender's clock read them."""
    monkeypatch.setattr(outbox, "MINUTE_S", 1.0)
    monkeypatch.setattr(outbox, "PER_MINUTE", 5)
    monkeypatch.setattr(outbox, "MARKET_PER_MINUTE", 3)
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.01)
    monkeypatch.setattr(lane, "MAX_LINES", 1)
    monkeypatch.setattr(outbox, "MAX_LINES", 1)

    async def scenario():
        sender, pacer = Sender(), Pacer()
        recorded: list[tuple[float, str]] = []
        took = pacer.took

        def took_and_record(lane_: str, slots: int = 1) -> None:
            took(lane_, slots)
            recorded.extend(list(pacer._starts)[-max(1, slots) :])  # the pacer's own times

        pacer.took = took_and_record
        if late_first_s:  # the first send reads its clock late; nothing is held back
            send_orders, send_market = sender.lane(ORDERS), sender.lane(MARKET)

            def late(send):
                async def wrapped(text: str):
                    first = not sender.sent
                    outcome = await send(text)
                    if first:
                        at, name, said = sender.sent[0]
                        sender.sent[0] = (at + late_first_s, name, said)
                    return outcome

                return wrapped

            orders = Outbox(late(send_orders), lambda: AT, pacer)
            market = MarketLane(late(send_market), lambda: AT, pacer, Results())
        else:
            orders = Outbox(sender.lane(ORDERS), lambda: AT, pacer)
            market = MarketLane(sender.lane(MARKET), lambda: AT, pacer, Results())
        tasks = await _running(orders, market)
        try:
            market.add([line(n) for n in range(20)])
            await asyncio.sleep(0.3)
            orders.add([fill(f"k{n}") for n in range(6)])
            await asyncio.sleep(2.2)
        finally:
            await _cancel(tasks)
        return recorded, [(t, lane_) for t, lane_, _ in sender.sent]

    return asyncio.run(scenario())


def test_the_minute_budgets_hold_across_lanes(quick, monkeypatch):
    """Judged on the pacer's own start times: those are what its budgets are kept on. The sender's
    clock is read a moment after the slot is taken, and a pause there (a GC pass, a loaded CI
    runner) put a sixth start inside a one-second window the pacer had kept to five (CI run
    43494809732); the next test makes that certain."""
    recorded, sent = _budgets_run(monkeypatch)
    assert _windows_hold(recorded)
    assert sum(lane_ == ORDERS for _, lane_ in sent) == 6  # orders kept their share
    assert [lane_ for _, lane_ in recorded] == [lane_ for _, lane_ in sent]  # one start, one send


def test_the_budget_check_reads_the_pacers_clock_not_the_senders(quick, monkeypatch):
    """The race, made certain: the first send reads its clock 50 ms late (a pause between the
    pacer's start and the sender's reading). The sender's times then show six starts in a
    second; the pacer's own times show it kept to five, so the check above reads those."""
    recorded, sent = _budgets_run(monkeypatch, late_first_s=0.05)
    assert not _windows_hold(sent)  # the old measurement fails on a schedule that was kept
    assert _windows_hold(recorded)


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ([], ("sent", "")),
        ([(DROP, outbox.DROP_REASONS[409])], ("not_sent", outbox.DROP_REASONS[409])),
        ([(DROP, "")], ("not_sent", outbox.REFUSED)),
        ([(UNCONFIRMED, "")] * 3, ("not_sent", lane.NOT_CONFIRMED)),
        ([(RETRY, ""), (RETRY, "")], ("sent", "")),
    ],
)
def test_what_became_of_each_message_is_reported(quick, script, expected):
    async def scenario():
        sender, results = Sender(*script), Results()
        market = MarketLane(sender.lane(MARKET), lambda: AT, Pacer(), results)
        market.add([line(1), line(2)])
        tasks = await _running(market)
        try:
            await wait_until(lambda: results.got, 3)
        finally:
            await _cancel(tasks)
        assert results.got == [([1, 2], *expected)]

    asyncio.run(scenario())


def test_a_market_alert_is_not_sent_after_5_minutes(quick):
    async def scenario():
        sender, results = Sender((RETRY, ""), (RETRY, "")), Results()
        later = AT + lane.MAX_AGE + timedelta(seconds=1)
        market = MarketLane(sender.lane(MARKET), lambda: later, Pacer(), results)
        market.add([line(1), line(2, at=later)])
        tasks = await _running(market)
        try:
            await wait_until(lambda: len(results.got) == 2, 3)
        finally:
            await _cancel(tasks)
        assert results.got == [([1], "not_sent", lane.TOO_OLD), ([2], "sent", "")]
        assert lane.TOO_OLD == (
            "OpenAlgo could not send it within 5 minutes; it is in the alert history"
        )

    asyncio.run(scenario())


def test_an_overflow_is_told_in_the_market_lanes_own_words(quick, monkeypatch):
    monkeypatch.setattr(lane, "MAX_PENDING", 3)
    monkeypatch.setattr(lane, "MAX_LINES", 50)

    async def scenario():
        sender, results = Sender(), Results()
        market = MarketLane(sender.lane(MARKET), lambda: AT, Pacer(), results)
        market.add([line(n) for n in range(5)])
        assert results.got == [([0], "not_sent", lane.TOO_MANY), ([1], "not_sent", lane.TOO_MANY)]
        tasks = await _running(market)
        try:
            await wait_until(lambda: len(results.got) == 3, 3)
        finally:
            await _cancel(tasks)
        ((_, _, text),) = sender.sent
        assert text.split("\n")[0] == "सुदर्शन / SUDARSHAN"
        assert "2 more market alerts could not be listed here. The Alerts page has them." in text
        assert "order book" not in text and text.endswith("10:42:05 IST")

    asyncio.run(scenario())


def test_flush_sends_the_market_lane_without_the_burst_pause(quick, monkeypatch):
    monkeypatch.setattr(lane, "COALESCE_S", 30.0)

    async def scenario():
        sender = Sender()
        market = MarketLane(sender.lane(MARKET), lambda: AT, Pacer(), Results())
        tasks = await _running(market)
        market.add([line(1)])
        try:
            assert await market.flush(2.0) == 0 and len(sender.sent) == 1
        finally:
            await _cancel(tasks)

    asyncio.run(scenario())


def test_the_order_lane_alone_keeps_its_pace(quick):
    """The pacer an Outbox makes for itself behaves as the order lane always did."""
    pacer = Pacer()
    assert pacer.allowed_at(ORDERS) == 0.0
    pacer.took(ORDERS)
    assert pacer.allowed_at(MARKET) >= time.monotonic() + outbox.MIN_GAP_S - 0.05


def test_a_market_line_waiting_behind_order_alerts_is_never_sent_late(quick):
    """Review M1: the age limit was checked only when a failed message was put back, so a line
    held by the order lane's retries went out 12 minutes late. While orders wait the market lane
    sends nothing, even with its sender idle (mutant L1); after 5 minutes the line is told not
    sent instead (mutant L4: 30 minutes)."""

    async def scenario():
        now, sender, results, pacer = {"t": AT}, Sender(), Results(), Pacer()
        waiting = {"orders": True}  # the order lane retrying, OpenAlgo busy
        pacer.orders_waiting = lambda: waiting["orders"]
        market = MarketLane(sender.lane(MARKET), lambda: now["t"], pacer, results)
        market.add([line(1)])
        tasks = await _running(market)
        try:
            await asyncio.sleep(0.3)
            assert sender.sent == [] and results.got == []  # fills first
            now["t"] = AT + timedelta(minutes=12)
            await wait_until(lambda: results.got, 3)
            waiting["orders"] = False
            await asyncio.sleep(0.2)
        finally:
            await _cancel(tasks)
        assert results.got == [([1], "not_sent", lane.TOO_OLD)] and sender.sent == []

    asyncio.run(scenario())


def test_a_market_line_put_back_at_4_59_is_not_sent_at_6_minutes(quick):
    """Review M1: put back before the limit, it went out after it on the next try."""

    async def scenario():
        now = {"t": AT + timedelta(minutes=4, seconds=59)}
        results = Results()

        async def send(text):
            if now["t"] < AT + timedelta(minutes=5):  # put back while still young enough
                passes = {"t": AT + timedelta(minutes=6)}  # and the retry's wait passes 5:00
                asyncio.get_running_loop().call_later(0.01, now.update, passes)
                return RETRY, ""
            return SENT, ""

        market = MarketLane(send, lambda: now["t"], Pacer(), results)
        market.add([line(1)])
        tasks = await _running(market)
        try:
            await wait_until(lambda: results.got, 3)
        finally:
            await _cancel(tasks)
        assert results.got == [([1], "not_sent", lane.TOO_OLD)]

    asyncio.run(scenario())


def test_a_market_line_four_minutes_old_is_still_sent(quick):
    async def scenario():
        sender, results = Sender(), Results()
        later = AT + timedelta(minutes=4)
        market = MarketLane(sender.lane(MARKET), lambda: later, Pacer(), results)
        market.add([line(1)])
        tasks = await _running(market)
        try:
            await wait_until(lambda: results.got, 3)
        finally:
            await _cancel(tasks)
        assert results.got == [([1], "sent", "")]

    asyncio.run(scenario())


def test_the_market_lane_takes_at_most_12_starts_a_minute_and_never_holds_an_order():
    """The real shares (M10.5): 12 market starts in the last minute stop a 13th, while the order
    lane only keeps the gap (mutant O6: a share of 20)."""
    pacer, now = Pacer(), time.monotonic()
    first = now - 50
    pacer._starts.extend((first + 2 * n, MARKET) for n in range(12))
    assert pacer.allowed_at(MARKET) == pytest.approx(first + outbox.MINUTE_S)
    assert pacer.allowed_at(ORDERS) == pytest.approx(first + 22 + outbox.MIN_GAP_S)
    pacer._starts.popleft()
    assert pacer.allowed_at(MARKET) == pytest.approx(first + 22 + outbox.MIN_GAP_S)


def test_an_order_alert_is_tried_again_for_30_minutes_then_dropped():
    """M6.8's age limit for a fill OpenAlgo could not take (mutant O7: 5 minutes)."""
    now = AT + timedelta(minutes=40)
    box = Outbox(lambda text: None, lambda: now, Pacer())
    young = Alert("young", "filled", 65, "Filled: young", now - timedelta(minutes=29))
    old = Alert("old", "filled", 65, "Filled: old", now - timedelta(minutes=31))
    box._put_back([young, old], 0)
    assert list(box._pending) == ["young"] and box.stats["failed"] == 1


def test_a_stop_flushes_order_alerts_past_the_market_lanes_share(quick):
    """Review L10: at a stop, order alerts get 5 s. With the minute's budget filled by 12 market
    and 8 order messages, they waited for the window and were lost."""

    async def scenario():
        sender, pacer, now = Sender(), Pacer(), time.monotonic()
        pacer._starts.extend((now - 50 + n, MARKET) for n in range(12))
        pacer._starts.extend((now - 30 + n, ORDERS) for n in range(8))
        orders = Outbox(sender.lane(ORDERS), lambda: AT, pacer)
        orders.add([fill("f1")])
        tasks = await _running(orders)
        try:
            assert await orders.flush(2.0) == 0
        finally:
            await _cancel(tasks)
        assert [name for _, name, _ in sender.sent] == [ORDERS]
        assert not pacer.flushing_orders  # the market lane's share counts again after it

    asyncio.run(scenario())
