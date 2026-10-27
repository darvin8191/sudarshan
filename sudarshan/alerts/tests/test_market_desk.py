"""The owner's desk (M10.1, M10.3, M10.4, M10.14): the local API's ops on running market alerts,
against a fake feed and a fake OpenAlgo /notify, on the test's own clock."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import pytest
from alerts_fakes import API_KEY, OWNER, FakeOpenAlgo, ist, wait_until
from market_fakes import FakeFeed, ms
from test_market_runtime import SBIN, Clock, create, record, trade

from sudarshan.alerts.market import api, desk
from sudarshan.alerts.market.api import ApiError
from sudarshan.alerts.market.config import MarketSettings
from sudarshan.alerts.market.desk import OwnerDesk
from sudarshan.alerts.market.runtime import MarketRuntime
from sudarshan.alerts.outbox import Pacer
from sudarshan.alerts.owner import Owner

RELIANCE = "NSE_EQ|INE002A01018"
NIFTY = "NSE_INDEX|Nifty 50"
SBIN_PRICE = {"symbol": "SBIN", "exchange": "NSE", "instrument_key": SBIN, "tick_size": "0.05",
              "kind": "price", "params": {"op": "crosses_above", "level": "100"}}  # fmt: skip


def draft(**fields) -> dict:
    return SBIN_PRICE | fields


async def opened(settings, feed, clock, **env) -> tuple[OwnerDesk, MarketRuntime, asyncio.Task]:
    market = MarketSettings.from_env({"SUDARSHAN_MARKET_ALERTS": "on",
                                      "SUDARSHAN_ALERTS_FEED_URL": feed.url,
                                      "SUDARSHAN_ALERTS_FEED_ZMQ": feed.endpoint} | env)  # fmt: skip
    rt = MarketRuntime(settings, market, owner_reader=lambda: Owner(OWNER, API_KEY),
                       pacer=Pacer(), clock=clock)  # fmt: skip
    task = asyncio.create_task(rt.run())
    await wait_until(lambda: rt.state == "running")
    while rt._health is None:  # a slow heartbeat: send health until the SUB has joined
        await feed.send_health()
        await asyncio.sleep(0.02)
    await wait_until(lambda: len(feed.claims) >= 1)
    await asyncio.sleep(0.15)  # the topic subscriptions reach the publisher
    return OwnerDesk(rt), rt, task


async def following(rt: MarketRuntime, key: str = SBIN) -> None:
    """Until the runtime follows the key's topic and the subscription has reached the publisher
    (a ZMQ subscriber joins late: a frame sent at once is lost)."""
    await wait_until(lambda: key in rt.link.topics)
    await asyncio.sleep(0.15)


async def closed(rt: MarketRuntime, task: asyncio.Task) -> None:
    rt.stop("test")
    await asyncio.wait_for(task, 10)


async def refused(call) -> ApiError:
    with pytest.raises(ApiError) as caught:
        await call
    return caught.value


def run(scenario) -> None:
    asyncio.run(scenario())


def test_the_state_carries_the_alerts_in_words_and_the_engine_line(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock(), frequency="every_time", cooldown_s=300, note="book half",
                   ends="2026-12-20")  # fmt: skip
            office, rt, task = await opened(settings, feed, clock)
            state = await office.call("state", {"principal": "owner"})
            assert state["enabled"] is True
            assert state["engine"] == {"code": "ok", "line": "Watching · Real-time",
                                       "real_time": True}  # fmt: skip
            assert state["segments"] == {"NSE_EQ": state["engine"]}
            assert state["limits"] == {"instruments": 200, "instruments_used": 1, "active": 500,
                                       "active_used": 1}  # fmt: skip
            assert state["prefs"] == {"telegram": True, "app": True}
            assert state["last_event_id"] == 0 and state["server_time"] == ms(clock())
            [card] = state["alerts"]
            assert card["status"] == "active" and card["version"] == 1
            assert card["words"] == {
                "condition": "Crosses above ₹100.00",
                "meta": "Every time · wait 5 min · ends 20 Dec",
                "checks_at": None,
            }
            assert card["check"] is None and card["fired_today"] == 0 and card["auction"] is None
            assert card["note"] == "book half" and card["status_reason"] == ""
            await trade(feed, clock, 99.95)
            await trade(feed, clock, 100.05, ltq=2)
            await wait_until(lambda: len(notify.bodies) == 1)
            await wait_until(lambda: record(settings, "events") != [])
            state = await office.call("state", {"principal": "owner"})
            assert state["alerts"][0]["fired_today"] == 1 and state["last_event_id"] == 1
            assert office.health() == {"service": "sudarshan-alerts", "market_alerts": "on",
                                       "state": "running", "alerts_active": 1,
                                       "instruments": 1}  # fmt: skip
            await closed(rt, task)

    run(scenario)


def test_outside_the_session_the_lines_say_when_checking_starts(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 8, 30))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock())
            office, rt, task = await opened(settings, feed, clock)
            state = await office.call("state", {"principal": "owner"})
            assert state["engine"]["line"] == "Market closed · checks from 09:15"
            assert state["alerts"][0]["check"]["line"] == "Market closed · checks from 09:15"
            clock.advance(8 * 3600)  # 16:30: the next session is Thursday's
            line = office.engine_line(ms(clock()))
            assert line == {"code": "outside_hours", "line": "Market closed · checks from Thu 09:15",
                            "real_time": False}  # fmt: skip
            await closed(rt, task)

    run(scenario)


def test_a_feed_that_is_down_is_said_on_every_line_inside_the_session(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock())
            office, rt, task = await opened(settings, feed, clock)
            feed.health |= {"state": "backoff", "subscriptions_live": False}
            await wait_until(lambda: rt.down_since is not None)
            state = await office.call("state", {"principal": "owner"})
            assert state["engine"]["code"] == "feed_down"
            assert state["engine"]["line"] == (
                "Not checking since 10:00:00 · the price feed is reconnecting"
            )
            assert state["alerts"][0]["check"] == state["engine"]
            feed.health |= {"state": "connected", "subscriptions_live": True}
            await wait_until(lambda: rt.down_since is None)
            assert office.engine_line(ms(clock()))["line"] == "Watching · Real-time"
            feed.health["last_message_age_s"] = 15.0  # connected, but silent (review A7.e)
            await wait_until(lambda: rt.down_since is not None)
            assert office.engine_line(ms(clock()))["line"] == (
                "Not checking since 09:59:45 · the price feed is reconnecting"
            )
            await closed(rt, task)

    run(scenario)


def test_a_new_alert_is_claimed_at_the_feed_before_it_is_saved_and_never_fires_at_once(
    alerts, quick
):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await opened(settings, feed, clock)
            feed.snapshot[SBIN] = {"ltp": 100.4, "ltt": ms(clock()) - 2000, "ltq": 7,
                                   "ts_recv": ms(clock()) - 2000}  # fmt: skip
            card = await office.call("create", {"alert": draft(frequency="every_time")})
            assert card["id"] == 1 and card["status"] == "active"
            assert feed.claims[1]["keys"] == {SBIN: "ltpc"}  # the desk's claim, before the save
            assert record(settings, "alert", 1)["status"] == "active"
            await wait_until(lambda: rt.book.state(1).side == 1)  # seeded: above already
            await following(rt)
            await trade(feed, clock, 100.5, ltq=8)  # still above: no crossing, nothing told
            await wait_until(lambda: rt.stats["judged"] == 1)
            await trade(feed, clock, 99.9, ltq=9)
            await trade(feed, clock, 100.1, ltq=10)
            await wait_until(lambda: len(notify.bodies) == 1)
            await closed(rt, task)

    run(scenario)


def test_the_feed_s_refusal_or_silence_is_said_and_nothing_is_saved(alerts, quick, caplog):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await opened(settings, feed, clock)
            feed.refuse = (409, "sudarshan-alerts: over its quota of 300 keys")
            full = await refused(office.call("create", {"alert": draft()}))
            assert (full.status, full.code, full.message) == (409, "feed", desk.WORDS["feed_full"])
            feed.answering = False
            silent = await refused(office.call("create", {"alert": draft()}))
            assert (silent.status, silent.code) == (503, "feed")
            assert silent.message == desk.WORDS["feed_silent"]
            feed.answering = True
            assert record(settings, "alerts") == [] and rt.book.alerts() == []
            await wait_until(lambda: feed.keys == {})  # the refused key is not left claimed
            await closed(rt, task)

    run(scenario)
    assert "over its quota of 300 keys" in caplog.text  # the feed's words, in the journal


def test_a_change_past_a_limit_is_refused_and_one_that_adds_nothing_passes(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await opened(settings, feed, clock,
                                            SUDARSHAN_MARKET_ALERTS_MAX_INSTRUMENTS="1",
                                            SUDARSHAN_MARKET_ALERTS_MAX_ACTIVE="2")  # fmt: skip
            await office.call("create", {"alert": draft()})
            other = draft(symbol="RELIANCE", instrument_key=RELIANCE)
            limit = await refused(office.call("create", {"alert": other}))
            assert (limit.status, limit.code) == (409, "limit")
            assert limit.message.startswith("Alerts can follow at most 1 instruments at once.")
            second = await office.call("create", {"alert": draft(params={
                "op": "crosses_below", "level": "90"})})  # the same instrument  # fmt: skip
            active = await refused(office.call("create", {"alert": draft()}))
            assert active.message == desk.WORDS["active"].format(n="2")
            paused = await office.call("update", {"id": second["id"], "version": 1,
                                                  "action": "pause"})  # fmt: skip
            assert paused["status"] == "paused"
            await office.call("create", {"alert": draft()})  # room again
            again = await refused(office.call("update", {"id": second["id"], "version": 2,
                                                         "action": "resume"}))  # fmt: skip
            assert again.code == "limit"
            assert record(settings, "alert", second["id"])["status"] == "paused"
            await closed(rt, task)

    run(scenario)


def test_a_definition_the_engine_cannot_run_is_refused_with_its_field(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await opened(settings, feed, clock)
            bad = await refused(office.call("create", {"alert": draft(params={
                "op": "crosses_above", "level": "0"})}))  # fmt: skip
            assert (bad.status, bad.code, bad.field) == (400, "invalid", "level")
            assert bad.message == "Enter the price level, a number above 0."
            junk = await refused(office.call("create", {"alert": "SBIN"}))
            assert (junk.status, junk.message) == (400, desk.WORDS["draft"])
            await closed(rt, task)

    run(scenario)


def test_an_edit_resets_a_changed_condition_and_keeps_the_end_it_does_not_name(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await opened(settings, feed, clock)
            card = await office.call("create", {"alert": draft(frequency="every_time",
                                                               ends="2026-12-20")})  # fmt: skip
            await following(rt)
            await trade(feed, clock, 99.95)
            await wait_until(lambda: rt.book.state(card["id"]).side == -1)
            edited = await office.call("update", {
                "id": card["id"], "version": 1, "action": "edit",
                "alert": draft(frequency="every_time", params={"op": "crosses_above",
                                                               "level": "101"})})  # fmt: skip
            assert edited["version"] == 2 and edited["ends_at"] == card["ends_at"]
            assert edited["words"]["condition"] == "Crosses above ₹101.00"
            assert rt.book.state(card["id"]).side is None  # starts again, from the last price:
            await wait_until(lambda: rt.book.state(card["id"]).side == -1)  # 99.95 is below 101
            await trade(feed, clock, 100.5, ltq=2)
            await trade(feed, clock, 101.05, ltq=3)
            await wait_until(lambda: len(notify.bodies) == 1)
            assert "crossed above ₹101.00: now ₹101.05" in notify.messages[0]
            stale = await refused(office.call("update", {
                "id": card["id"], "version": 1, "action": "edit", "alert": draft()}))  # fmt: skip
            assert (stale.status, stale.code) == (409, "conflict")
            assert stale.message == (
                "This alert was changed on another screen. Open it again, then edit it."
            )
            gone = await refused(office.call("update", {"id": 99, "version": 1,
                                                        "action": "pause"}))  # fmt: skip
            assert (gone.status, gone.code) == (404, "gone")
            await closed(rt, task)

    run(scenario)


def test_pause_and_resume_fire_on_nothing_and_a_stopped_alert_restarts_once(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await opened(settings, feed, clock)
            card = await office.call("create", {"alert": draft()})  # Once
            await following(rt)
            await trade(feed, clock, 99.95)
            await wait_until(lambda: rt.stats["judged"] == 1)
            await office.call("update", {"id": 1, "version": 1, "action": "pause"})
            await trade(feed, clock, 100.3, ltq=2)  # crossed while paused: not told
            feed.snapshot[SBIN] = {"ltp": 100.3, "ltt": ms(clock()), "ltq": 2,
                                   "ts_recv": ms(clock())}  # fmt: skip
            resumed = await office.call("update", {"id": 1, "version": 2, "action": "resume"})
            assert resumed["status"] == "active"
            await wait_until(lambda: rt.book.state(1).side == 1)  # seeded above: no fire
            await asyncio.sleep(0.15)  # the topic subscription reaches the publisher again
            await trade(feed, clock, 99.8, ltq=3)
            await trade(feed, clock, 100.2, ltq=4)
            await wait_until(lambda: len(notify.bodies) == 1)  # fired once, then stopped
            await wait_until(lambda: record(settings, "alert", 1)["status"] == "stopped")
            again = await refused(office.call("update", {"id": 1, "version": 3,
                                                         "action": "pause"}))  # fmt: skip
            assert again.code == "refused" and again.message.startswith("This alert has stopped")
            restarted = await office.call("update", {"id": 1, "version": 3,
                                                     "action": "restart"})  # fmt: skip
            assert (restarted["status"], restarted["version"]) == ("active", 4)
            await wait_until(lambda: getattr(rt.book.state(1), "side", None) == 1)
            await asyncio.sleep(0.15)
            await trade(feed, clock, 99.7, ltq=5)
            await trade(feed, clock, 100.4, ltq=6)
            await wait_until(lambda: len(notify.bodies) == 2)
            assert card["words"]["meta"].startswith("Once")
            await closed(rt, task)

    run(scenario)


def test_a_restart_past_its_end_is_refused_without_claiming(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock(), ends=f"{ist(2026, 10, 21, 10, 0, 30).isoformat()}")
            office, rt, task = await opened(settings, feed, clock)
            clock.advance(60)
            await wait_until(lambda: record(settings, "alert", 1)["status"] == "stopped")
            await wait_until(lambda: feed.keys == {})
            claims = len(feed.claims)
            ended = await refused(office.call("update", {"id": 1, "version": 1,
                                                         "action": "restart"}))  # fmt: skip
            assert ended.message == (
                "This alert's end date has passed. Change the end date, then restart it."
            )
            await asyncio.sleep(0.3)
            assert all(c["keys"] == {} for c in feed.claims[claims:])
            await closed(rt, task)

    run(scenario)


def test_a_delete_leaves_the_book_and_the_claims_at_once_and_its_history_stays(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await opened(settings, feed, clock)
            await office.call("create", {"alert": draft(frequency="every_time")})
            await following(rt)
            await trade(feed, clock, 99.95)
            await trade(feed, clock, 100.05, ltq=2)
            await wait_until(lambda: len(notify.bodies) == 1)
            assert await office.call("delete", {"id": 1}) == {"deleted": True}
            assert rt.book.alerts() == []
            await wait_until(lambda: feed.keys == {})
            gone = await refused(office.call("delete", {"id": 1}))
            assert gone.code == "gone"
            assert len(record(settings, "events")) == 1
            await closed(rt, task)

    run(scenario)


def test_history_pages_and_what_fired_since_are_read_in_order(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await opened(settings, feed, clock)
            await office.call("create", {"alert": draft(frequency="every_time", note="book half")})
            await following(rt)
            for n, price in enumerate((99.9, 100.1, 99.8, 100.2, 99.7, 100.3)):
                await trade(feed, clock, price, ltq=n + 1)
            await wait_until(lambda: len(record(settings, "events")) == 3)
            first = await office.call("events", {"limit": 2})
            assert [e["id"] for e in first["events"]] == [3, 2] and first["more"] is True
            older = await office.call("events", {"limit": 2, "before": 2})
            assert [e["id"] for e in older["events"]] == [1] and older["more"] is False
            since = await office.call("events_since", {"after": 1})
            assert [e["id"] for e in since["events"]] == [2, 3] and since["more"] is False
            event = since["events"][0]
            assert event["title"] == "SBIN crossed above ₹100.00" and event["alert_id"] == 1
            assert (event["detail"], event["note"]) == ("now ₹100.20", "book half")
            assert event["trade_time"] <= event["at"] and event["test"] is False
            assert set(event["deliveries"]) == {"telegram"}
            await office.call("update", {"id": 1, "version": 1, "action": "edit", "alert": draft(
                frequency="every_time", note="sold half")})  # fmt: skip
            again = (await office.call("events_since", {"after": 1}))["events"][0]
            said = ("title", "detail", "note", "line")  # as they were at the fire
            assert {k: again[k] for k in said} == {k: event[k] for k in said}
            bad = await refused(office.call("events_since", {}))
            assert bad.status == 400
            await closed(rt, task)

    run(scenario)


def test_the_settings_and_the_test_go_through_the_channels_they_name(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await opened(settings, feed, clock)
            sent = await office.call("test", {})
            assert sent["deliveries"]["telegram"]["status"] == "sent"
            assert notify.messages[0].split("\n")[1] == words_test_line()
            prefs = await office.call("prefs_set", {"telegram": False, "app": True})
            assert prefs == {"telegram": False, "app": True} == rt.prefs
            assert await office.call("prefs_get", {}) == prefs
            off = await office.call("test", {})
            assert off["deliveries"]["telegram"] == {"status": "off", "attempts": 0,
                                                     "reason": "Off in your alert settings",
                                                     "updated_at": ms(clock())}  # fmt: skip
            assert len(notify.bodies) == 1
            bad = await refused(office.call("prefs_set", {"telegram": "yes", "app": True}))
            assert (bad.status, bad.field) == (400, "channels")
            history = await office.call("events", {})
            assert [e["test"] for e in history["events"]] == [True, True]
            assert {(e["title"], e["detail"], e["note"]) for e in history["events"]} == {
                ("Test alert", "sent from the Alerts page", "")}  # fmt: skip
            await closed(rt, task)

    run(scenario)


def words_test_line() -> str:
    from sudarshan.alerts.market.words import TEST_LINE

    return TEST_LINE


def test_a_card_says_what_its_alert_waits_for(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await opened(settings, feed, clock)
            bid = await office.call("create", {"alert": draft(params={
                "op": "crosses_above", "level": "100", "source": "bid"})})  # fmt: skip
            assert bid["check"] == {
                "code": "waiting",
                "real_time": True,
                "line": "Waiting for the first bid from the price feed",
            }
            assert feed.keys == {SBIN: "full"}  # the bid comes in full mode only (A7.i)
            move = await office.call("create", {"alert": draft(
                kind="move", params={"direction": "up", "percent": "2"})})  # fmt: skip
            while rt.last_frame(SBIN) is None:  # until the topic subscription has joined
                await feed.tick(SBIN, 101.0, bid=100.9)
                await asyncio.sleep(0.05)
            await wait_until(lambda: rt.book.waiting(move["id"]) is not None)
            state = await office.call("state", {})
            cards = {card["id"]: card for card in state["alerts"]}
            assert cards[bid["id"]]["check"] is None  # its bid has come
            assert cards[move["id"]]["check"]["line"] == "Waiting for the previous close"
            assert cards[move["id"]]["words"]["condition"] == "Up 2% on the day"
            await closed(rt, task)

    run(scenario)


def test_a_stock_that_closes_by_auction_is_claimed_in_full_and_says_when_its_price_comes(
    alerts, quick
):
    """Cross-design X6i, X6j, X6m: its print is known by its volume, which comes in full mode
    only; until the print its line says when the price comes, and its card carries the times."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 15, 20))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await opened(settings, feed, clock)
            rt._fno = frozenset({"SBIN"})  # the master's stock futures, as the morning read gives
            card = await office.call("create", {"alert": draft()})
            assert feed.claims[1]["keys"] == {SBIN: "full"}  # the desk's claim, before the save
            waiting = {"code": "closing_auction", "real_time": False,
                       "line": "Closing auction · price at about 15:30"}  # fmt: skip
            assert card["check"] == waiting
            assert card["auction"] == {"from": ms(ist(2026, 10, 21, 15, 15)),
                                       "close": ms(ist(2026, 10, 21, 15, 30)),
                                       "until": ms(ist(2026, 10, 21, 15, 35))}  # fmt: skip
            assert (await office.call("state", {}))["engine"] == waiting
            quotes = await office.call("create", {"alert": draft(params={
                "op": "crosses_above", "level": "100", "source": "bid"})})  # fmt: skip
            assert quotes["check"]["line"] == (
                "Closing auction · bid and ask are not checked after 15:15"
            )
            await closed(rt, task)

    run(scenario)


def test_while_starting_or_stopped_the_desk_answers_in_words(alerts, quick, tmp_path):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            market = MarketSettings.from_env({"SUDARSHAN_MARKET_ALERTS": "on",
                                              "SUDARSHAN_ALERTS_FEED_URL": feed.url,
                                              "SUDARSHAN_ALERTS_FEED_ZMQ": feed.endpoint})  # fmt: skip
            rt = MarketRuntime(settings, market, owner_reader=lambda: Owner(OWNER, API_KEY),
                               pacer=Pacer(), clock=clock)  # fmt: skip
            office = OwnerDesk(rt)
            state = await office.call("state", {})
            assert state["engine"] == {"code": "starting", "line": "Starting up",
                                       "real_time": False}  # fmt: skip
            starting = await refused(office.call("create", {"alert": draft()}))
            assert (starting.status, starting.code) == (503, "starting")
            settings.state_dir.mkdir(parents=True, exist_ok=True)
            (settings.state_dir / "market.db").mkdir()  # a directory: the record cannot open
            await rt.run()
            stopped = await refused(office.call("events", {}))
            assert (stopped.status, stopped.code) == (503, "stopped")
            assert stopped.message.startswith("Market alerts are off: their record could not")
            assert (await office.call("state", {}))["engine"]["line"] == stopped.message

    run(scenario)


def test_an_api_on_a_taken_port_keeps_market_alerts_off_and_order_alerts_on(alerts, quick, caplog):
    import socket

    from sudarshan.alerts import service
    from sudarshan.alerts.market.runtime import ReleasedClaims
    from sudarshan.vault import ALERTS_API_INFO, fernet_for

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            market = MarketSettings.from_env({"SUDARSHAN_MARKET_ALERTS": "on",
                                              "SUDARSHAN_ALERTS_FEED_URL": feed.url,
                                              "SUDARSHAN_ALERTS_FEED_ZMQ": feed.endpoint})  # fmt: skip
            rt = MarketRuntime(settings, market, owner_reader=lambda: Owner(OWNER, API_KEY),
                               pacer=Pacer(), clock=clock)  # fmt: skip
            server = api.MarketApi(OwnerDesk(rt), fernet_for("p" * 48, ALERTS_API_INFO))
            with socket.socket() as holder:
                holder.bind(("127.0.0.1", 0))
                holder.listen()
                kept, opened_api = await service._open_api(server, holder.getsockname()[1], rt)
            assert isinstance(kept, ReleasedClaims) and opened_api is None
            assert kept.feed_url == feed.url

    caplog.set_level(logging.ERROR, logger="sudarshan.alerts")
    run(scenario)
    assert "Market alerts stay off; order alerts carry on" in caplog.text


def test_the_service_serves_the_api_and_closes_it_with_everything_else(alerts, quick):
    import httpx

    from sudarshan.alerts import service
    from sudarshan.autologin.handoff import seal
    from sudarshan.vault import ALERTS_API_INFO, fernet_for

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            market = MarketSettings.from_env({"SUDARSHAN_MARKET_ALERTS": "on",
                                              "SUDARSHAN_ALERTS_FEED_URL": feed.url,
                                              "SUDARSHAN_ALERTS_FEED_ZMQ": feed.endpoint})  # fmt: skip
            pacer = Pacer()
            svc = service.AlertService(settings, owner_reader=lambda: Owner(OWNER, API_KEY),
                                       pacer=pacer)  # fmt: skip
            rt = MarketRuntime(settings, market, owner_reader=lambda: Owner(OWNER, API_KEY),
                               pacer=pacer, clock=clock)  # fmt: skip
            fernet = fernet_for("p" * 48, ALERTS_API_INFO)
            server = api.MarketApi(OwnerDesk(rt), fernet)
            with socket_port() as port:
                pass
            serving = asyncio.create_task(service._serve(svc, rt, server, port))
            await wait_until(lambda: rt.state == "running")
            async with httpx.AsyncClient(trust_env=False) as client:
                health = (await client.get(f"http://127.0.0.1:{port}/health", timeout=5)).json()
                assert health["market_alerts"] == "on" and health["state"] == "running"
                body = seal(fernet, "create", principal="owner", alert=draft())
                made = await client.post(f"http://127.0.0.1:{port}/v1/create", content=body,
                                         timeout=10)  # fmt: skip
                assert made.status_code == 200 and made.json()["data"]["id"] == 1
            service._stop(svc, rt, "test")
            await asyncio.wait_for(serving, 10)
            assert server.server is None  # closed with the rest

    run(scenario)


class socket_port:
    """A free port on 127.0.0.1, released on exit for the test to bind."""

    def __enter__(self) -> int:
        import socket

        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        return self.sock.getsockname()[1]

    def __exit__(self, *exc) -> None:
        self.sock.close()


def test_the_words_of_a_card(alerts, quick):
    from datetime import date

    from sudarshan.alerts.market import spec, words

    def alert(**fields) -> spec.Alert:
        now = ms(ist(2026, 10, 21, 10, 0))
        row = spec.check(draft(**fields), now) | {"id": 1, "version": 1, "status": "active"}
        return spec.Alert.of(row)

    today = date(2026, 10, 21)
    nifty = {"symbol": "NIFTY", "exchange": "NSE_INDEX", "instrument_key": NIFTY,
             "tick_size": None}  # fmt: skip
    cases = [
        (alert(**nifty, params={"op": "crosses_above", "level": "25900"}),
         "Crosses above 25,900.00", "Once · ends 20 Dec"),
        (alert(params={"op": "at_or_below", "level": "1428.25", "source": "ask"},
               frequency="every_time", cooldown_s=90, ends=None),
         "Ask is at or below ₹1,428.25", "Every time · wait 1 min 30 s · no end date"),
        (alert(kind="move", params={"base": "open", "direction": "either", "percent": "2.5"},
               candle=15, ends="2027-01-05"),
         "Up or down 2.5% from today's open", "Once per 15-min candle · ends 5 Jan 2027"),
        (alert(kind="day_break", params={"side": "low", "after": "10:00"}),
         "Breaks today's low after 10:00", "Once a day · ends 20 Dec"),
        (alert(kind="vwap", params={"direction": "below"}, frequency="at_close", candle="day",
               ends="2026-11-02T14:30:00+05:30"),
         "Crosses below VWAP", "At the day's close · wait 15 min · ends 2 Nov 14:30"),
    ]  # fmt: skip
    for made, condition, meta in cases:
        assert (words.condition(made), words.meta(made, today)) == (condition, meta)
    option = alert(symbol="NIFTY25NOV2625900CE", exchange="NFO", tick_size="0.05",
                   instrument_key="NSE_FO|45450", expiry="2026-11-25")  # fmt: skip
    assert words.meta(option, today) == "Once · until expiry 25 Nov"


def test_every_op_is_answered_and_every_message_is_the_owner_s(alerts, quick):
    """No op the API takes is missing from the desk, and no word the owner reads is a code."""
    for op in api.OPS:
        assert callable(getattr(OwnerDesk, f"_{op}"))
    for text in (*desk.WORDS.values(), *api.WORDS.values()):
        assert text[0].isupper() and text.endswith(".") and "_" not in text


def test_a_moment_s_day_is_ist():
    assert desk._day(ms(datetime(2026, 10, 21, 0, 10, tzinfo=ist(2026, 1, 1).tzinfo))).day == 21
