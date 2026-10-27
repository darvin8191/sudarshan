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


def test_the_minute_budgets_hold_across_lanes(quick, monkeypatch):
    monkeypatch.setattr(outbox, "MINUTE_S", 1.0)
    monkeypatch.setattr(outbox, "PER_MINUTE", 5)
    monkeypatch.setattr(outbox, "MARKET_PER_MINUTE", 3)
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.01)
    monkeypatch.setattr(lane, "MAX_LINES", 1)
    monkeypatch.setattr(outbox, "MAX_LINES", 1)

    async def scenario():
        sender, pacer = Sender(), Pacer()
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
        starts = [(t, lane_) for t, lane_, _ in sender.sent]
        for t0, _ in starts:  # every one-second window
            window = [lane_ for t, lane_ in starts if t0 <= t < t0 + 1.0]
            assert len(window) <= 5 and window.count(MARKET) <= 3
        assert sum(lane_ == ORDERS for _, lane_ in starts) == 6  # orders kept their share

    asyncio.run(scenario())


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
