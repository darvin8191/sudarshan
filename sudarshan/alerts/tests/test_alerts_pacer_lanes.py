"""AL1: the owner bot's lanes on the one Pacer: orders, market, signals, notices, reports (Y18,
R8, P8 5.2).

The class is ``sudarshan.common.pacer.Pacer`` (tested in ``sudarshan/common/tests``); here the
owner bot's numbers and rules, and the notice desk taking its slots from it. The signals lane's
vectors (P8 10.8) are at the end: an order never waits more than one gap behind a signal, 4 a
minute, and it gives way to a market message that has waited 20 s.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest
from alerts_fakes import API_KEY, OWNER, FakeOpenAlgo, RunningClock, ist, wait_until

from sudarshan.alerts import notices, outbox
from sudarshan.alerts.notices import LanePace
from sudarshan.alerts.outbox import MARKET, NOTICES, ORDERS, REPORTS, SIGNALS, Pacer
from sudarshan.alerts.owner import Owner
from sudarshan.alerts.service import AlertService
from sudarshan.common import pacer as common
from sudarshan.ops.events import record
from sudarshan.tasks import cancel_and_wait


def slots(pacer: Pacer, lane: str) -> list[float]:
    return [at for at, which in pacer._starts if which == lane]


def test_the_lanes_and_their_order_of_service():
    pacer = Pacer()
    assert list(pacer.lanes) == [ORDERS, MARKET, SIGNALS, NOTICES, REPORTS]
    assert (ORDERS, MARKET, SIGNALS, NOTICES, REPORTS) == (
        "orders", "market", "signals", "notices", "reports",
    )  # fmt: skip
    assert isinstance(pacer, common.Pacer)
    assert pacer.lanes[ORDERS].yields_to == ()
    assert pacer.lanes[MARKET].yields_to == (ORDERS,)
    assert pacer.lanes[SIGNALS].yields_to == (ORDERS,)
    assert [other for other, _ in pacer.lanes[SIGNALS].yields_after] == [MARKET]
    assert pacer.lanes[NOTICES].yields_to == (ORDERS,)
    assert pacer.lanes[REPORTS].yields_to == (ORDERS, MARKET)


def test_the_caps_a_minute():
    assert (outbox.PER_MINUTE, outbox.MARKET_PER_MINUTE) == (20, 12)
    assert (outbox.NOTICES_PER_MINUTE, outbox.REPORTS_PER_MINUTE) == (1, 4)
    assert (outbox.SIGNALS_PER_MINUTE, outbox.SIGNALS_YIELD_MARKET_S) == (4, 20.0)
    assert notices.NOTICES_PER_MINUTE == 1  # the notice lane's cap stays in notices.py


@pytest.fixture
def fast(monkeypatch):
    """No gap and a one-second minute, so a minute's rules show in a test that takes no time."""
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.0)
    monkeypatch.setattr(outbox, "MINUTE_S", 1000.0)


def test_each_lane_keeps_its_own_cap(fast):
    pacer = Pacer()
    now = time.monotonic()
    pacer.took(NOTICES)
    assert pacer.allowed_at(NOTICES) > now + 900  # 1 a minute
    for _ in range(4):
        pacer.took(REPORTS)
    assert pacer.allowed_at(REPORTS) > now + 900  # 4 a minute: two reports of two slots
    assert pacer.allowed_at(ORDERS) <= time.monotonic()
    pacer = Pacer()
    for _ in range(12):
        pacer.took(MARKET)
    assert pacer.allowed_at(MARKET) > now + 900  # 12 a minute


def test_the_order_lane_keeps_at_least_eight_a_minute_whatever_the_others_do(fast, monkeypatch):
    monkeypatch.setattr(notices, "NOTICES_PER_MINUTE", 5)  # a lane cap that would let it be crowded
    pacer = Pacer()
    for lane, count in ((MARKET, 6), (NOTICES, 3), (REPORTS, 3)):  # the others, 12 together
        for _ in range(count):
            pacer.took(lane)
    for lane in (MARKET, NOTICES, REPORTS):
        assert pacer.allowed_at(lane) > time.monotonic() + 900  # the 12 they share are used
    for _ in range(8):
        assert pacer.allowed_at(ORDERS) <= time.monotonic()
        pacer.took(ORDERS)
    assert pacer.allowed_at(ORDERS) > time.monotonic() + 900  # the 20 are used


def test_the_gap_holds_across_all_five_lanes(monkeypatch):
    monkeypatch.setattr(outbox, "MIN_GAP_S", 1.1)
    lanes = (ORDERS, MARKET, SIGNALS, NOTICES, REPORTS)
    for lane in lanes:
        pacer = Pacer()
        pacer.took(lane)
        for other in lanes:
            gap = pacer.allowed_at(other) - time.monotonic()
            assert 0.0 < gap <= 1.1 or (other == lane and lane in (NOTICES, REPORTS))


def test_a_waiting_order_alert_holds_back_every_other_lane():
    pacer = Pacer()
    assert not any(pacer.blocked(lane) for lane in pacer.lanes)
    pacer.orders_waiting = lambda: True
    blocked = [lane for lane in pacer.lanes if pacer.blocked(lane)]
    assert blocked == [MARKET, SIGNALS, NOTICES, REPORTS]
    assert pacer.orders_waiting() is True
    pacer.orders_waiting = lambda: False
    assert not any(pacer.blocked(lane) for lane in pacer.lanes)


def test_a_notice_that_has_waited_its_limit_holds_back_the_market_lane(monkeypatch):
    """L2: the notice lane tells the Pacer from when a notice ready to go found no slot; once it
    has waited NOTICE_WAIT_S the market lane takes no slot until the notice has taken one."""
    assert outbox.NOTICE_WAIT_S == 60.0
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.0)
    monkeypatch.setattr(notices, "NOTICES_PER_MINUTE", 1)
    now = {"t": 1000.0}
    pacer = Pacer()
    pacer.clock = lambda: now["t"]
    pace = LanePace(pacer)
    for _ in range(12):  # a market storm has used the twelve slots the lanes share
        pacer.took(MARKET)
    assert pace.wait(0.0) > 0 and pacer.waiting(NOTICES) and not pacer.blocked(MARKET)
    now["t"] += 59.0
    assert pace.wait(0.0) > 0 and not pacer.blocked(MARKET)  # the wait counts from the first ask
    now["t"] += 2.0
    assert pacer.blocked(MARKET) and not pacer.blocked(NOTICES)
    assert [lane for lane in pacer.lanes if pacer.blocked(lane)] == [MARKET]
    pace.took(0.0)
    assert not pacer.waiting(NOTICES) and not pacer.blocked(MARKET)
    assert pace.wait(0.0) > 0 and pacer.waiting(NOTICES)  # the next notice waits a minute
    pace.idle()  # nothing ready to go after all
    assert not pacer.waiting(NOTICES)
    now["t"] += 100.0
    assert pace.wait(0.0) == 0.0 and not pacer.waiting(NOTICES)  # a slot: nothing waits


def test_a_market_storm_holds_a_notice_back_no_longer_than_its_limit(monkeypatch):
    """L2: a steady storm takes every slot the lanes share, and the notice desk, which looks again
    just after a slot frees (0.05 s), loses each one to the market lane, which is there on time.
    Once the notice has waited NOTICE_WAIT_S it takes the next slot. A minute is two seconds here,
    and the gap between two messages (0.1 s) is longer than the desk's lateness, as the real one
    (1.1 s) is."""
    from sudarshan.alerts.market import lane as market_lane
    from sudarshan.alerts.market.lane import Line, MarketLane

    monkeypatch.setattr(outbox, "MINUTE_S", 2.0)
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.1)
    monkeypatch.setattr(outbox, "NOTICE_WAIT_S", 2.0)
    monkeypatch.setattr(notices, "NOTICES_PER_MINUTE", 1)
    for name, value in {"COALESCE_S": 0.01, "POLL_S": 0.01, "MAX_LINES": 1}.items():
        monkeypatch.setattr(market_lane, name, value)
    at = ist(2026, 10, 21, 10, 42, 5)
    sent: list[tuple[float, str]] = []

    async def send_market(text):
        sent.append((time.monotonic(), MARKET))
        return outbox.SENT, ""

    async def scenario():
        pacer = Pacer()
        market = MarketLane(send_market, lambda: at, pacer, lambda *a: None)
        market.add([Line(n, f"Alert: line {n}", at) for n in range(150)])
        task = asyncio.create_task(market.run())
        try:
            await wait_until(lambda: len(sent) >= 12, 3)  # the shared slots are taken
            pace, began = LanePace(pacer), time.monotonic()
            while (wait := pace.wait(0.0)) > 0:  # the desk's own loop: look, sleep, look again
                await asyncio.sleep(wait + 0.05)
                assert time.monotonic() - began < 5.0, "the storm held the notice back"
            pace.took(0.0)
            waited = time.monotonic() - began
            assert waited < outbox.NOTICE_WAIT_S + 1.0, waited  # the limit, then one slot
            assert len(market) > 0  # the storm was still on when the notice went
        finally:
            await cancel_and_wait(task)

    asyncio.run(scenario())


def test_a_waiting_market_message_holds_back_the_reports_and_nothing_else():
    pacer = Pacer()
    pacer.set_waiting(MARKET, lambda: True)
    assert [lane for lane in pacer.lanes if pacer.blocked(lane)] == [REPORTS]


def test_the_market_lane_says_when_it_has_messages_waiting():
    from sudarshan.alerts.market.lane import MarketLane

    pacer = Pacer()

    async def send(text):
        return outbox.SENT, ""

    lane = MarketLane(send, lambda: ist(2026, 10, 21, 10, 0), pacer, lambda *a: None)
    assert not pacer.waiting(MARKET)
    lane._pending[1] = object()  # whatever waits there
    assert pacer.waiting(MARKET)


def test_a_notice_follows_a_report_by_one_gap_and_a_report_takes_two_slots(monkeypatch):
    monkeypatch.setattr(outbox, "MIN_GAP_S", 1.1)
    pacer = Pacer()
    pacer.took(REPORTS, 2)
    assert len(slots(pacer, REPORTS)) == 2
    assert 0.0 < pacer.allowed_at(NOTICES) - time.monotonic() <= 1.1


def test_the_lane_pace_follows_the_pacer_and_the_notice_cap(monkeypatch):
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.0)
    monkeypatch.setattr(notices, "NOTICES_PER_MINUTE", 1)
    pacer = Pacer()
    pace = LanePace(pacer)
    assert pace.wait(0.0) == 0.0
    pace.took(0.0)
    assert 59.0 < pace.wait(0.0) <= 60.0  # one a minute
    pacer.orders_waiting = lambda: True
    assert pace.wait(0.0) == notices.GIVE_WAY_S
    monkeypatch.setattr(notices, "NOTICES_PER_MINUTE", 1000)
    pacer.orders_waiting = lambda: False
    assert pace.wait(0.0) == 0.0  # the cap is read when asked


def test_the_service_builds_the_notice_desk_on_the_outbox_pacer(
    settings_for, tmp_path, monkeypatch
):
    for name, value in {"POLL_S": 0.05, "SETTLE_S": 0.1, "RETRY_S": (0.05, 0.05)}.items():
        monkeypatch.setattr(notices, name, value)
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.05)
    spool = tmp_path / "notice-spool.jsonl"
    evening = ist(2026, 10, 25, 20, 0)
    clock = RunningClock(evening)
    assert record(
        spool, kind="installed", event_id="i1", at=(evening - timedelta(minutes=1)).timestamp(),
        run="i1", src="deploy", tag="v0.3.3",
    )  # fmt: skip

    async def scenario():
        async with FakeOpenAlgo() as notify:
            settings = settings_for(None, notify, SUDARSHAN_ALERTS_ALWAYS_ON="0")
            svc = AlertService(settings, owner_reader=lambda: Owner(OWNER, API_KEY), clock=clock)
            task = asyncio.create_task(svc.run())
            await wait_until(lambda: len(notify.bodies) == 1, 10)
            pace = svc.notices.pace
            assert isinstance(pace, LanePace) and pace.pacer is svc.outbox.pacer
            assert len(slots(svc.outbox.pacer, NOTICES)) == 1  # the notice took a slot
            svc.stop("test")
            await asyncio.wait_for(task, 10)

    asyncio.run(scenario())


# ---- the signals lane (AL1, P8 5.2 and 10.8) ----------------------------------------------------


class Clock:
    """The Pacer's own clock, moved by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def owner_pacer(clock: Clock) -> Pacer:
    pacer = Pacer()
    pacer.clock = clock
    return pacer


def test_the_signals_lane_takes_at_most_four_starts_a_minute(monkeypatch):
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.0)
    clock = Clock()
    pacer = owner_pacer(clock)
    for _ in range(4):
        assert pacer.allowed_at(SIGNALS) <= clock.now
        pacer.took(SIGNALS)
    assert pacer.allowed_at(SIGNALS) == clock.now + 60.0  # the fifth waits for the minute
    assert pacer.allowed_at(ORDERS) <= clock.now  # the cap is the lane's own
    assert pacer.allowed_at(MARKET) <= clock.now
    clock.now += 60.0
    assert pacer.allowed_at(SIGNALS) <= clock.now


def test_an_order_never_waits_more_than_one_gap_behind_a_signal(monkeypatch):
    """A signal that has just started holds an order for one gap at most; a signal waiting holds
    it for nothing, since the lane takes no slot while order alerts wait (mutant: yields_to=())."""
    monkeypatch.setattr(outbox, "MIN_GAP_S", 1.1)
    clock = Clock()
    pacer = owner_pacer(clock)
    for _ in range(3):  # a burst of signals, each one gap apart
        assert pacer.allowed_at(SIGNALS) <= clock.now
        pacer.took(SIGNALS)
        assert pacer.allowed_at(ORDERS) - clock.now == pytest.approx(1.1)
        clock.now += 1.1
    pacer.orders_waiting = lambda: True
    assert pacer.blocked(SIGNALS)  # an order alert waiting: no slot for a signal
    assert not pacer.blocked(ORDERS) and pacer.allowed_at(ORDERS) <= clock.now


def test_the_signals_lane_gives_way_to_a_market_message_that_has_waited_20_seconds():
    clock = Clock()
    pacer = owner_pacer(clock)
    since: dict[str, float | None] = {"at": None}
    pacer.set_waiting(MARKET, lambda: since["at"] is not None, since=lambda: since["at"])
    assert not pacer.blocked(SIGNALS)
    since["at"] = clock.now  # a market message starts to wait (the market lane's own cap, say)
    clock.now += 19.9
    assert not pacer.blocked(SIGNALS)  # a fresh market message: the two lanes race for the slot
    clock.now += 0.2
    assert pacer.blocked(SIGNALS)  # 20.1 s: the signal waits until the market lane has sent
    assert not pacer.blocked(MARKET)  # and never the other way round
    since["at"] = None  # the market message took its slot
    assert not pacer.blocked(SIGNALS)


def test_the_market_lane_tells_the_pacer_since_when_its_messages_wait():
    """Without ``since`` the Pacer counts a market wait as 0 s and the 20 s rule never holds."""
    from sudarshan.alerts.market.lane import Line, MarketLane

    async def send(text):
        return outbox.SENT, ""

    pacer = Pacer()
    lane = MarketLane(send, lambda: ist(2026, 11, 16, 10, 0), pacer, lambda *a: None)
    assert pacer.waited(MARKET) == 0.0
    lane.add([Line(1, "Alert: line 1", ist(2026, 11, 16, 10, 0))])
    lane._since -= outbox.SIGNALS_YIELD_MARKET_S + 1.0  # as if it had waited 21 s for a slot
    assert pacer.waited(MARKET) > outbox.SIGNALS_YIELD_MARKET_S
    assert pacer.blocked(SIGNALS) and pacer.blocked(REPORTS)  # reports wait for any


def test_a_waiting_signal_holds_back_no_other_lane():
    pacer = Pacer()
    pacer.set_waiting(SIGNALS, lambda: True, since=lambda: pacer.clock() - 3600.0)
    assert [lane for lane in pacer.lanes if pacer.blocked(lane)] == []


def test_signals_share_the_twelve_slots_the_order_lane_leaves(monkeypatch):
    """Four signals and eight market messages use the twelve the lanes share: the notices and the
    reports wait, and the order lane still has its eight."""
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.0)
    clock = Clock()
    pacer = owner_pacer(clock)
    for lane, count in ((SIGNALS, 4), (MARKET, 8)):
        for _ in range(count):
            pacer.took(lane)
    for lane in (MARKET, SIGNALS, NOTICES, REPORTS):
        assert pacer.allowed_at(lane) > clock.now
    for _ in range(8):
        assert pacer.allowed_at(ORDERS) <= clock.now
        pacer.took(ORDERS)
    assert pacer.allowed_at(ORDERS) > clock.now
