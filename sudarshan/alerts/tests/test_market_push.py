"""Market alerts to the open apps (M10.7): the pusher's sealed batches, and the runtime's fires,
cards, line and test through it, against a fake OpenAlgo push route."""

from __future__ import annotations

import asyncio
import os
import time

import httpx
import pytest
from alerts_fakes import API_KEY, OWNER, FakeOpenAlgo, ist, wait_until
from market_fakes import FakeFeed, ms
from test_market_desk import closed, draft, following
from test_market_runtime import SBIN, Clock, record, trade

from sudarshan.alerts.market import lane, push, runtime
from sudarshan.alerts.market.config import MarketSettings
from sudarshan.alerts.market.desk import OwnerDesk
from sudarshan.alerts.market.engine import Fire, State
from sudarshan.alerts.market.push import ALERT_EVENT, DELIVERY_EVENT, STATE_EVENT, Pusher
from sudarshan.alerts.market.runtime import MarketRuntime
from sudarshan.alerts.outbox import Pacer
from sudarshan.alerts.owner import Owner
from sudarshan.vault import ALERTS_PUSH_INFO, fernet_for

FERNET = fernet_for(os.environ["API_KEY_PEPPER"], ALERTS_PUSH_INFO)


def run(scenario) -> None:
    asyncio.run(scenario())


async def pushing(notify, results: list) -> tuple[Pusher, httpx.AsyncClient, asyncio.Task]:
    client = httpx.AsyncClient(trust_env=False)
    pusher = Pusher(notify.url, client, FERNET, lambda ids, screens: results.append((ids, screens)))
    return pusher, client, asyncio.create_task(pusher.run())


async def done(client: httpx.AsyncClient, task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await client.aclose()


def test_a_burst_goes_in_one_sealed_request_and_its_fires_learn_their_screens():
    async def scenario():
        async with FakeOpenAlgo() as notify:
            results: list = []
            pusher, client, task = await pushing(notify, results)
            notify.screens = 2
            for n in range(3):
                pusher.add(ALERT_EVENT, {"event_id": n + 1, "title": f"fire {n}"}, n + 1)
            pusher.add(STATE_EVENT, {"alerts": []})
            await wait_until(lambda: results)
            assert len(notify.pushes) == 1  # one request for the burst, sealed for "push"
            assert [item["event"] for item in notify.pushes[0]] == [ALERT_EVENT] * 3 + [STATE_EVENT]
            assert results == [([1, 2, 3], 2)]
            await done(client, task)

    run(scenario)


def test_the_batches_keep_to_the_route_s_limits(monkeypatch):
    """Cross-design X9: at most 50 items and 128 KiB a request; one item too large is dropped."""

    async def scenario():
        async with FakeOpenAlgo() as notify:
            results: list = []
            pusher, client, task = await pushing(notify, results)
            monkeypatch.setattr(push, "MAX_TEXT", 2_000)
            pusher.add(STATE_EVENT, {"blob": "x" * 3_000}, 99)  # larger than any request
            for n in range(120):
                pusher.add(ALERT_EVENT, {"event_id": n, "title": "t" * 20}, n)
            await wait_until(lambda: sum(len(ids) for ids, _ in results) == 121)
            assert results[0] == ([99], None)  # told as not sent, never posted
            sizes = [len(items) for items in notify.pushes]
            assert sum(sizes) == 120 and max(sizes) <= 50
            assert all(len(str(items)) < 2_500 for items in notify.pushes)
            await done(client, task)

    run(scenario)


def test_a_push_that_is_not_taken_is_told_not_sent_and_never_sent_again():
    async def scenario():
        async with FakeOpenAlgo() as notify:
            results: list = []
            pusher, client, task = await pushing(notify, results)
            notify.push_status = 500
            pusher.add(ALERT_EVENT, {"event_id": 5}, 5)
            await wait_until(lambda: results)
            await asyncio.sleep(0.2)
            assert results == [([5], None)] and len(notify.pushes) == 1
            await done(client, task)

    run(scenario)


async def started(settings, feed, clock) -> tuple[OwnerDesk, MarketRuntime, asyncio.Task]:
    market = MarketSettings.from_env({"SUDARSHAN_MARKET_ALERTS": "on",
                                      "SUDARSHAN_ALERTS_FEED_URL": feed.url,
                                      "SUDARSHAN_ALERTS_FEED_ZMQ": feed.endpoint})  # fmt: skip
    rt = MarketRuntime(settings, market, owner_reader=lambda: Owner(OWNER, API_KEY),
                       pacer=Pacer(), clock=clock, push_fernet=FERNET)  # fmt: skip
    office = OwnerDesk(rt)
    task = asyncio.create_task(rt.run())
    await wait_until(lambda: rt.state == "running")
    while rt._health is None:  # a slow heartbeat: send health until the SUB has joined
        await feed.send_health()
        await asyncio.sleep(0.02)
    await wait_until(lambda: len(feed.claims) >= 1)
    return office, rt, task


def items(notify, event: str) -> list[dict]:
    return [item["data"] for batch in notify.pushes for item in batch if item["event"] == event]


def test_a_fire_reaches_the_open_apps_and_its_delivery_says_on_how_many_screens(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            notify.screens = 2
            settings = alerts(notify)
            office, rt, task = await started(settings, feed, clock)
            await office.call("create", {"alert": draft(frequency="every_time", note="book half")})
            await following(rt)
            await trade(feed, clock, 99.95)
            await trade(feed, clock, 100.05, ltq=2)
            await wait_until(lambda: items(notify, ALERT_EVENT))
            [shown] = items(notify, ALERT_EVENT)
            assert shown == {
                "event_id": 1, "alert_id": 1, "symbol": "SBIN", "exchange": "NSE",
                "title": "SBIN crossed above ₹100.00", "detail": "now ₹100.05",
                "note": "book half", "at": "2026-10-21T10:00:02+05:30",
                "at_ms": ms(ist(2026, 10, 21, 10, 0, 2)), "found_later": False, "opening": False,
                "auction": False, "test": False,
            }  # fmt: skip
            missed = (await office.call("events_since", {"after": 0}))["events"][0]
            said = ("title", "detail", "note")  # a toast built from History reads the same
            assert {k: missed[k] for k in said} == {k: shown[k] for k in said}
            await wait_until(lambda: record(settings, "events")[0]["deliveries"]["app"]["status"]
                             == "sent")  # fmt: skip
            app = record(settings, "events")[0]["deliveries"]["app"]
            assert app["reason"] == "Shown on 2 open screens"
            notify.screens = 0
            await trade(feed, clock, 99.9, ltq=3)
            await trade(feed, clock, 100.2, ltq=4)
            await wait_until(lambda: len(record(settings, "events")) == 2 and record(
                settings, "events")[0]["deliveries"]["app"]["status"] == "not_sent")  # fmt: skip
            assert record(settings, "events")[0]["deliveries"]["app"]["reason"] == "No app was open"
            await closed(rt, task)

    run(scenario)


def test_a_telegram_delivery_that_settles_reaches_history_in_the_open_apps(alerts, quick):
    """Contract v3: each fire's Telegram delivery, once it leaves pending, as market.db holds it;
    a refused one too. The app delivery's own result is not pushed."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await started(settings, feed, clock)
            await office.call("create", {"alert": draft(frequency="every_time")})
            await following(rt)
            await trade(feed, clock, 99.95)
            await trade(feed, clock, 100.05, ltq=2)
            await wait_until(lambda: items(notify, DELIVERY_EVENT))
            [settled] = items(notify, DELIVERY_EVENT)
            stored = record(settings, "events")[0]["deliveries"]["telegram"]
            assert settled == {"event_id": 1, "channel": "telegram", "delivery": stored}
            assert stored["status"] == "sent" and stored["reason"] == ""
            notify.default = FakeOpenAlgo.STOPPED  # OpenAlgo's bot is stopped: not sent, in words
            await trade(feed, clock, 99.9, ltq=3)
            await trade(feed, clock, 100.2, ltq=4)
            await wait_until(lambda: len(items(notify, DELIVERY_EVENT)) == 2)
            refused = items(notify, DELIVERY_EVENT)[1]
            assert refused["event_id"] == 2 and refused["delivery"]["status"] == "not_sent"
            assert refused["delivery"]["reason"]
            assert {i["channel"] for i in items(notify, DELIVERY_EVENT)} == {"telegram"}
            await closed(rt, task)

    run(scenario)


def test_with_the_app_off_in_the_settings_nothing_is_pushed(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await started(settings, feed, clock)
            await office.call("prefs_set", {"telegram": True, "app": False})
            await office.call("create", {"alert": draft(frequency="every_time")})
            await following(rt)
            await trade(feed, clock, 99.95)
            await trade(feed, clock, 100.05, ltq=2)
            await wait_until(lambda: len(notify.bodies) == 1)
            assert record(settings, "events")[0]["deliveries"]["app"] == {
                "status": "off", "reason": "Off in your alert settings", "attempts": 0,
                "updated_at": ms(clock())}  # fmt: skip
            assert items(notify, ALERT_EVENT) == []
            await closed(rt, task)

    run(scenario)


def test_an_alert_that_stops_itself_and_the_page_line_reach_the_cards_at_once(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await started(settings, feed, clock)
            await wait_until(lambda: any(i["engine"]["line"] == "Watching · Real-time"
                                         for i in items(notify, STATE_EVENT)))  # fmt: skip
            made = await office.call("create", {"alert": draft()})  # once
            await wait_until(lambda: any(i["alerts"] for i in items(notify, STATE_EVENT)))
            brief = [i for i in items(notify, STATE_EVENT) if i["alerts"]][0]["alerts"][0]
            expected = {"id": 1, "version": 1, "status": "active", "status_reason": "",
                        "check": None, "fired_today": 0}  # fmt: skip
            assert brief == expected == {k: made[k] for k in brief}
            await following(rt)
            await trade(feed, clock, 99.95)
            await trade(feed, clock, 100.05, ltq=2)
            await wait_until(lambda: any(a["status"] == "stopped" for i in items(
                notify, STATE_EVENT) for a in i["alerts"]))  # fmt: skip
            stop = [a for i in items(notify, STATE_EVENT) for a in i["alerts"]][-1]
            assert stop["status_reason"] == "Fired once at 10:00 on 21 Oct"
            assert stop["fired_today"] == 1 and stop["check"] is None
            feed.health |= {"state": "backoff", "subscriptions_live": False}
            await wait_until(lambda: items(notify, STATE_EVENT)[-1]["engine"]["code"] ==
                             "feed_down")  # fmt: skip
            await office.call("delete", {"id": 1})
            await wait_until(lambda: any(i["removed"] == [1] for i in items(notify, STATE_EVENT)))
            await closed(rt, task)

    run(scenario)


def test_the_settings_test_reaches_the_app_and_telegram_with_each_answer(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await started(settings, feed, clock)
            sent = await office.call("test", {})
            assert sent["deliveries"]["telegram"]["status"] == "sent"
            assert sent["deliveries"]["app"]["status"] == "sent"
            assert sent["deliveries"]["app"]["reason"] == "Shown on 1 open screen"
            [toast] = items(notify, ALERT_EVENT)
            assert (toast["title"], toast["detail"], toast["test"]) == (
                "Test alert", "sent from the Alerts page", True)  # fmt: skip
            await closed(rt, task)

    run(scenario)


def test_an_app_delivery_left_pending_at_a_restart_is_never_sent_late(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 30))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            from test_market_runtime import create

            row = create(settings, clock(), frequency="every_time")
            at = ms(clock())
            fire = Fire(row["id"], "1:v1:x1", 20261021, 0, at, at, 10005, 10000, 1, 1, version=1)
            record(settings, "record_fire", fire, State(20261021, 1), title="t", line="l",
                   value="v", level="l", channels={"telegram": False, "app": True})  # fmt: skip
            office, rt, task = await started(settings, feed, clock)
            await wait_until(lambda: record(settings, "events")[0]["deliveries"]["app"]["status"]
                             == "not_sent")  # fmt: skip
            assert record(settings, "events")[0]["deliveries"]["app"]["reason"] == (
                runtime.RESTARTED
            )
            assert items(notify, ALERT_EVENT) == []
            await closed(rt, task)

    run(scenario)


def swallowing(job):
    """``job`` as a lost cancel leaves it (sudarshan/tasks.py): its first cancel swallowed, as
    anyio's connect_tcp can inside httpx, and then parked."""

    async def carries_on(self) -> None:
        try:
            await job(self)
        except asyncio.CancelledError:
            pass
        await asyncio.Event().wait()

    return carries_on


@pytest.mark.parametrize(
    "owner, name",
    [(push.Pusher, "run"), (lane.MarketLane, "run"), (runtime.MarketRuntime, "_claims")],
    ids=["app push", "Telegram", "claims"],
)
def test_a_stop_ends_even_when_a_task_swallows_its_cancel(alerts, quick, monkeypatch, owner, name):
    """Each task the stop cancels may be inside an httpx request, where a cancel can be lost:
    the stop used to wait for ever (two 10 s timeouts in 8 runs of this file, one hang)."""
    monkeypatch.setattr(owner, name, swallowing(getattr(owner, name)))

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            office, rt, task = await started(alerts(notify), feed, clock)
            began = time.monotonic()
            await closed(rt, task)
            assert time.monotonic() - began < 5 and rt.state == "stopped"

    run(scenario)


@pytest.mark.parametrize("screens", [1, 3])
def test_the_words_of_an_app_delivery(screens):
    assert (
        push.shown(screens) == {1: "Shown on 1 open screen", 3: "Shown on 3 open screens"}[screens]
    )


def test_the_service_builds_market_alerts_with_their_api_and_their_push(alerts, tmp_path):
    """service._market: switched off, the API says so and the feed drops the keys; on, the
    runtime gets the push key and the owner's desk."""
    from sudarshan.alerts import service
    from sudarshan.alerts.market.api import OffDesk
    from sudarshan.alerts.market.runtime import ReleasedClaims

    async def scenario():
        async with FakeOpenAlgo() as notify:
            settings = alerts(notify)
            off, api, port = service._market(settings, None, tmp_path / "openalgo.db", Pacer())
            assert isinstance(off, ReleasedClaims) and isinstance(api.desk, OffDesk)
            assert port == 5582
            market = MarketSettings.from_env({"SUDARSHAN_MARKET_ALERTS": "on"})
            on, api, port = service._market(settings, market, tmp_path / "openalgo.db", Pacer())
            assert isinstance(on, MarketRuntime) and isinstance(api.desk, OwnerDesk)
            assert on.push_fernet is not None and port == 5582
            on._writer.shutdown()

    run(scenario)
