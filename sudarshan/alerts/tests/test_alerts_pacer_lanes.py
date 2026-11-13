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
