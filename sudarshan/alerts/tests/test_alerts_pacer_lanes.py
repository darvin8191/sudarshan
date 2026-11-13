"""AL1: the owner bot's lanes on the one Pacer: orders, market, notices, reports (Y18, R8).

The class is ``sudarshan.common.pacer.Pacer`` (tested in ``sudarshan/common/tests``); here the
owner bot's numbers and rules, and the notice desk taking its slots from it.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest
from alerts_fakes import API_KEY, OWNER, FakeOpenAlgo, RunningClock, ist, wait_until

from sudarshan.alerts import notices, outbox
from sudarshan.alerts.notices import LanePace
from sudarshan.alerts.outbox import MARKET, NOTICES, ORDERS, REPORTS, Pacer
from sudarshan.alerts.owner import Owner
from sudarshan.alerts.service import AlertService
from sudarshan.common import pacer as common
from sudarshan.ops.events import record
from sudarshan.tasks import cancel_and_wait


def slots(pacer: Pacer, lane: str) -> list[float]:
    return [at for at, which in pacer._starts if which == lane]


def test_the_lanes_and_their_order_of_service():
    pacer = Pacer()
    assert list(pacer.lanes) == [ORDERS, MARKET, NOTICES, REPORTS]
    assert (ORDERS, MARKET, NOTICES, REPORTS) == ("orders", "market", "notices", "reports")
    assert isinstance(pacer, common.Pacer)
    assert pacer.lanes[ORDERS].yields_to == ()
    assert pacer.lanes[MARKET].yields_to == (ORDERS,)
    assert pacer.lanes[NOTICES].yields_to == (ORDERS,)
    assert pacer.lanes[REPORTS].yields_to == (ORDERS, MARKET)


def test_the_caps_a_minute():
    assert (outbox.PER_MINUTE, outbox.MARKET_PER_MINUTE) == (20, 12)
    assert (outbox.NOTICES_PER_MINUTE, outbox.REPORTS_PER_MINUTE) == (1, 4)
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


def test_the_gap_holds_across_all_four_lanes(monkeypatch):
    monkeypatch.setattr(outbox, "MIN_GAP_S", 1.1)
    for lane in (ORDERS, MARKET, NOTICES, REPORTS):
        pacer = Pacer()
        pacer.took(lane)
        for other in (ORDERS, MARKET, NOTICES, REPORTS):
            gap = pacer.allowed_at(other) - time.monotonic()
            assert 0.0 < gap <= 1.1 or (other == lane and lane in (NOTICES, REPORTS))


def test_a_waiting_order_alert_holds_back_the_market_notices_and_reports():
    pacer = Pacer()
    assert not any(pacer.blocked(lane) for lane in pacer.lanes)
    pacer.orders_waiting = lambda: True
    assert [lane for lane in pacer.lanes if pacer.blocked(lane)] == [MARKET, NOTICES, REPORTS]
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
