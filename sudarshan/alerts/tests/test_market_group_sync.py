"""List alerts follow their list (M8.14, A3): the sync, the freeze, group_sync and the backstop,
against a stub of the members route, on the test's own clock."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from alerts_fakes import FakeOpenAlgo, ist, wait_until
from cryptography.fernet import Fernet
from groups_fakes import StubMembers, key, row
from market_fakes import FakeFeed, ms
from test_market_desk import closed, opened, refused, run
from test_market_runtime import Clock, create, record

from sudarshan.alerts.market import members as members_mod
from sudarshan.alerts.market import spec
from sudarshan.alerts.market.desk import _day
from sudarshan.alerts.market.store import MarketStore
from sudarshan.autologin.handoff import NonceBook, open_sealed

REF = "list:7"
MOVE = {"direction": "up", "percent": "2", "base": "close"}
GROUP = {"ref": REF, "name": "Swing", "kind": "move", "params": MOVE, "frequency": "every_time",
         "cooldown_s": 300, "delivery": "first_then_digest", "digest_min": 5,
         "synced_version": "v0", "ends_at": None}  # fmt: skip


def make_group(settings, clock, numbers, **fields) -> dict:
    """A group with its members made before the service starts (no desk op makes one yet)."""
    store = MarketStore(settings.state_dir / "market.db")
    try:
        base = {
            "instrument_key": "",
            "symbol": "",
            "exchange": "NSE",
            "tick_size": "0.05",
            "kind": "move",
            "params": MOVE,
            "frequency": "every_time",
            "cooldown_s": 300,
        }
        found = [spec.check(base | {"instrument_key": key(n), "symbol": f"S{n}"}, ms(clock()))
                 for n in numbers]  # fmt: skip
        return store.create_group("owner", GROUP | fields, found, ms(clock()))
    finally:
        store.close()


async def ready(settings, feed, clock, stub, **env):
    office, rt, task = await opened(settings, feed, clock, **env)
    rt.members = stub
    office._group_day, office._group_next = _day(rt.now_ms()), 10**15  # only what a test asks for
    return office, rt, task


def members_of(settings, group_id) -> list[str]:
    return [m["symbol"] for m in record(settings, "group_members", group_id)]


def test_the_sync_adds_removes_and_keeps(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            group = make_group(settings, clock, [1, 2, 3])
            kept = {m["symbol"]: m["id"] for m in record(settings, "group_members", group["id"])}
            stub = StubMembers({REF: [row(1), row(3), row(4)]}, version="v5")
            office, rt, task = await ready(settings, feed, clock, stub)
            await wait_until(lambda: key(2) in feed.keys)
            brief = await office.sync(group["id"])
            assert brief["covered"] == 3 and brief["status_reason"] == ""
            now = {m["symbol"]: m["id"] for m in record(settings, "group_members", group["id"])}
            assert sorted(now) == ["S1", "S3", "S4"]
            assert now["S1"] == kept["S1"] and now["S3"] == kept["S3"]  # kept: the same alerts
            assert rt.alert(kept["S2"]) is None and rt.alert(now["S4"]) is not None
            await wait_until(lambda: key(4) in feed.keys and key(2) not in feed.keys)
            saved = record(settings, "group", group["id"])
            assert saved["synced_version"] == "v5" and saved["version"] == group["version"]
            assert stub.calls == [("members", REF)]
            await closed(rt, task)

    run(scenario)


def test_a_claim_goes_only_when_no_other_alert_holds_the_key(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            group = make_group(settings, clock, [1, 2])
            create(settings, clock(), instrument_key=key(2), symbol="S2")  # holds key 2 as well
            stub = StubMembers({REF: [row(1)]})
            office, rt, task = await ready(settings, feed, clock, stub)
            await wait_until(lambda: key(2) in feed.keys)
            await office.sync(group["id"])
            assert members_of(settings, group["id"]) == ["S1"]
            await asyncio.sleep(0.4)  # the claims job has had its turn
            assert key(2) in feed.keys and key(2) in rt.book.modes()
            stub.lists[REF] = []
            await office.sync(group["id"])
            await asyncio.sleep(0.4)
            assert key(1) not in feed.keys and key(2) in feed.keys  # the plain alert still holds it
            await closed(rt, task)

    run(scenario)


def test_v2_off_freezes_the_group_in_words_and_it_resumes_when_back(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            group = make_group(settings, clock, [1, 2, 3])
            stub = StubMembers({REF: [row(1)]})
            stub.state = "off"
            office, rt, task = await ready(settings, feed, clock, stub)
            brief = await office.sync(group["id"])
            assert brief["status"] == "active"
            assert brief["status_reason"] == (
                "The watchlist feature is switched off, so this alert keeps its last 3 symbols."
            )
            assert members_of(settings, group["id"]) == ["S1", "S2", "S3"]  # never an empty list
            assert record(settings, "group", group["id"])["synced_version"] == "v0"
            assert await office.sync(group["id"]) is None  # already said: nothing written again
            stub.state = "ok"
            brief = await office.sync(group["id"])
            assert brief["status_reason"] == "" and brief["covered"] == 1
            assert members_of(settings, group["id"]) == ["S1"]
            await closed(rt, task)

    run(scenario)


def test_a_deleted_list_stops_the_group(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            group = make_group(settings, clock, [1, 2])
            stub = StubMembers()
            stub.gone.add(REF)
            office, rt, task = await ready(settings, feed, clock, stub)
            brief = await office.sync(group["id"])
            words = "The list Swing was deleted, so this alert stopped."
            assert brief["status"] == "stopped" and brief["status_reason"] == words
            rows = record(settings, "group_members", group["id"])
            assert {m["status"] for m in rows} == {"stopped"} and len(rows) == 2
            assert all(rt.alert(m["id"]).status == "stopped" for m in rows)  # fmt: skip
            await wait_until(lambda: key(1) not in feed.keys)
            assert await office.sync(group["id"]) is None  # a stopped group is not asked again
            await closed(rt, task)

    run(scenario)


def test_growth_past_the_limits_is_covered_in_list_order_with_the_words(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            group = make_group(settings, clock, [1, 2])
            stub = StubMembers({REF: [row(n) for n in range(1, 9)]})
            office, rt, task = await ready(
                settings, feed, clock, stub, SUDARSHAN_MARKET_ALERTS_MAX_ACTIVE="5"
            )
            brief = await office.sync(group["id"])
            assert brief["covered"] == 5
            assert brief["status_reason"] == (
                "Covers 5 of 8: alerts can follow at most 200 instruments and 5 Active alerts."
            )
            assert members_of(settings, group["id"]) == ["S1", "S2", "S3", "S4", "S5"]
            await closed(rt, task)

    run(scenario)


def test_left_out_rows_are_told_and_an_unreadable_row_is_skipped(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            group = make_group(settings, clock, [1])
            bad = row(9, exchange="MCX")  # an exchange alerts do not follow
            stub = StubMembers({REF: [row(1), bad]})
            stub.left_out[REF] = [{"symbol": "NIFTY ATM", "why": "atm", "reason": "x"}]
            office, rt, task = await ready(settings, feed, clock, stub)
            brief = await office.sync(group["id"])
            assert brief["covered"] == 1
            assert brief["left_out"] == [
                "ATM and strike rows change contract during the day and are left out: 1.",
                "Rows without a contract to follow are left out: 1."]  # fmt: skip
            await closed(rt, task)

    run(scenario)


def test_group_sync_runs_one_sync_at_once_for_its_ref(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            mine = make_group(settings, clock, [1])
            other = make_group(settings, clock, [2], ref="list:8", name="Other")
            stub = StubMembers({REF: [row(1), row(3)], "list:8": [row(2)]})
            office, rt, task = await ready(settings, feed, clock, stub)
            done = await office.call("group_sync", {"principal": "owner", "ref": REF})
            assert [g["id"] for g in done["groups"]] == [mine["id"]]
            assert stub.calls == [("members", REF)]
            assert members_of(settings, mine["id"]) == ["S1", "S3"]
            assert members_of(settings, other["id"]) == ["S2"]
            assert (await refused(office.call("group_sync", {"principal": "owner"}))).status == 400
            await closed(rt, task)

    run(scenario)


def test_a_route_that_is_down_changes_nothing(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            group = make_group(settings, clock, [1, 2])
            stub = StubMembers({REF: []})
            stub.down = True
            office, rt, task = await ready(settings, feed, clock, stub)
            assert await office.sync(group["id"]) is None
            assert members_of(settings, group["id"]) == ["S1", "S2"]
            assert record(settings, "group", group["id"])["status_reason"] == ""
            await closed(rt, task)

    run(scenario)


def test_the_backstop_asks_the_version_and_syncs_only_when_it_moved(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            group = make_group(settings, clock, [1], synced_version="v1")
            stub = StubMembers({REF: [row(1), row(2)]}, version="v1")
            office, rt, task = await ready(settings, feed, clock, stub)
            now = rt.now_ms()

            async def step():
                office._group_next = 0
                office._group_step(now)
                await rt.group_task

            await step()
            assert stub.calls == [("version",)]  # the same version: nothing more is asked
            assert members_of(settings, group["id"]) == ["S1"]
            stub.current = "v2"
            await step()
            assert stub.calls[1:] == [("version",), ("members", REF)]
            assert members_of(settings, group["id"]) == ["S1", "S2"]
            assert record(settings, "group", group["id"])["synced_version"] == "v2"
            await step()
            assert stub.calls[-1] == ("version",) and len(stub.calls) == 4
            await closed(rt, task)

    run(scenario)


def test_the_check_is_one_task_inside_a_session_and_leaves_none_behind(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 8, 30))  # before the session
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            make_group(settings, clock, [1])
            stub = StubMembers({REF: [row(1)]})
            office, rt, task = await ready(settings, feed, clock, stub)
            now = rt.now_ms()
            office._group_next = 0
            office._group_step(now)
            assert rt.group_task is None and stub.calls == []  # outside the window: no check
            clock.advance(90 * 60)
            now = rt.now_ms()
            assert rt.in_session(now)
            office._group_step(now)
            first = rt.group_task
            office._group_step(now)
            assert rt.group_task is first  # one at a time, and one a minute
            await first
            office._group_step(now + 1000)
            assert rt.group_task is first  # a minute has not passed
            # a morning sync of a new day starts at once, and a stop leaves nothing running
            office._group_day = None
            office._group_step(now)
            assert rt.group_task is not first
            await closed(rt, task)
            assert rt.group_task.done()
            assert not [t for t in asyncio.all_tasks() if t.get_name() == "market alerts: list alerts"
                        and not t.done()]  # fmt: skip

    run(scenario)


def test_a_future_that_expires_is_asked_again_after_its_end(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            group = make_group(settings, clock, [1])
            soon = ms(clock()) + 5_000
            stub = StubMembers({REF: [row(1, valid_until=soon)]})
            office, rt, task = await ready(settings, feed, clock, stub)
            await office.sync(group["id"])
            assert office._ask == {group["id"]: soon}
            stub.calls.clear()
            office._group_next = 0
            office._group_step(soon - 1)  # before its end: the ordinary check, the version only
            await rt.group_task
            assert stub.calls == [("version",)]
            stub.calls.clear()
            office._group_next = 0
            clock.advance(6)
            office._group_step(rt.now_ms())  # after its end: the list is asked again
            await rt.group_task
            assert ("members", REF) in stub.calls
            await closed(rt, task)

    run(scenario)


def test_a_re_ask_at_a_futures_end_stays_when_the_list_cannot_be_read(alerts, quick):
    """Review L4: the re-ask was dropped before the read, so a read that failed at the roll left
    the group on the expired future until the lists version next moved or the morning sync."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            group = make_group(settings, clock, [1])
            soon = ms(clock()) + 5_000
            stub = StubMembers({REF: [row(1, valid_until=soon)]})
            office, rt, task = await ready(settings, feed, clock, stub)
            await office.sync(group["id"])
            assert office._ask == {group["id"]: soon}
            clock.advance(6)
            stub.down = True  # OpenAlgo does not answer at the roll
            office._group_next = 0
            office._group_step(rt.now_ms())
            await rt.group_task
            assert office._ask == {group["id"]: soon}  # kept: the next check asks again
            stub.down = False
            stub.lists[REF] = [row(2)]  # the next contract
            office._group_next = 0
            office._group_step(rt.now_ms())
            await rt.group_task
            assert members_of(settings, group["id"]) == ["S2"]
            assert group["id"] not in office._ask
            await closed(rt, task)

    run(scenario)


def test_a_version_moved_for_another_list_writes_the_version_only_and_pushes_nothing(alerts, quick):
    """Review L1: any list's change, a screener apply or a restart moves the lists version, and
    every group then wrote sync_group and pushed a brief though its own list held what it held."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            group = make_group(settings, clock, [1, 2])
            stub = StubMembers({REF: [row(1), row(2)]}, version="v1")
            office, rt, task = await ready(settings, feed, clock, stub)
            await office.sync(group["id"])  # in step with v1
            before = record(settings, "group", group["id"])
            pushed, syncs = [], []
            real_push, real_sync = office._push, rt.store.sync_group
            office._push = lambda now, *a, **k: pushed.append(k) or real_push(now, *a, **k)
            rt.store.sync_group = lambda *a, **k: syncs.append(1) or real_sync(*a, **k)
            clock.advance(60)
            stub.current = "v2"  # another list changed
            brief = await office.sync(group["id"])
            after = record(settings, "group", group["id"])
            assert after["synced_version"] == "v2" and after["synced_at"] == ms(clock())
            assert syncs == [] and pushed == []  # no sync_group, no brief
            assert (after["covered"], after["left_out"], after["version"], after["updated_at"]) == (
                before["covered"],
                before["left_out"],
                before["version"],
                before["updated_at"],
            )
            assert brief["covered"] == 2
            assert members_of(settings, group["id"]) == ["S1", "S2"]
            stub.lists[REF] = [row(1)]  # its own list changes: written and pushed as before
            stub.current = "v3"
            await office.sync(group["id"])
            assert syncs == [1] and len(pushed) == 1
            assert members_of(settings, group["id"]) == ["S1"]
            await closed(rt, task)

    run(scenario)


def test_the_members_answer_carries_the_refs_own_version():
    got = members_mod.read({"status": "ok", "data": {"version": "b:4", "ref_version": "0a1b2c3d4e5f",
                                                     "rows": [], "left_out": []}})  # fmt: skip
    assert got.ref_version == "0a1b2c3d4e5f" and got.version == "b:4"
    assert members_mod.read({"status": "ok", "data": {"version": "b:4"}}).ref_version is None
    assert members_mod.read({"status": "ok", "data": {"ref_version": 7}}).ref_version is None


def test_the_sealed_route_is_asked_with_its_op_and_its_answers_are_read(monkeypatch):
    fernet = Fernet(Fernet.generate_key())
    seen: list[dict] = []
    answers = {
        "members": {"status": "ok", "data": {"version": 4, "rows": [
            {"instrument_key": "K1", "symbol": "A", "exchange": "NSE", "tick_size": "0.05",
             "expiry": None, "lot": 1, "valid_until": 9}],
            "left_out": [{"why": "atm", "reason": "changes contract during the day"}]}},
        "version": {"status": "error", "code": "off", "message": "off"},
    }  # fmt: skip

    def handle(request: httpx.Request) -> httpx.Response:
        op = request.url.params["op"]
        message = open_sealed(fernet, request.content, op=op, seen=NonceBook(10))
        seen.append(message)
        return httpx.Response(200 if op == "members" else 409, json=answers[op])

    monkeypatch.setattr(members_mod.loopback, "refusal", lambda url: None)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            route = members_mod.OpenAlgoMembers("http://127.0.0.1:5000", client, fernet)
            got = await route.members("list:7")
            assert got.state == "ok" and got.version == "4" and got.rows[0].valid_until == 9
            assert got.left_out[0]["why"] == "atm"
            assert (await route.version()).state == "off"  # off is an answer, not a failure
            monkeypatch.setattr(members_mod.loopback, "refusal", lambda url: "another user")
            with pytest.raises(members_mod.Unavailable):
                await route.version()  # the key goes only to our own user's port

    asyncio.run(scenario())
    assert [m["op"] for m in seen] == ["members", "version"] and seen[0]["ref"] == "list:7"
    assert json.dumps(seen[0]).count("nonce") == 1


def test_an_answer_that_cannot_be_read_is_unavailable_not_empty():
    with pytest.raises(members_mod.Unavailable):
        members_mod.read({"status": "ok", "data": {"rows": [{"symbol": "A"}]}})
    with pytest.raises(members_mod.Unavailable):
        members_mod.read({"status": "error", "code": "failed"}, 500)


def test_a_waiting_state_push_folds_a_groups_newer_card_over_its_older():
    from sudarshan.alerts.market.push import STATE_EVENT, Pusher

    pusher = Pusher("http://127.0.0.1:1", None, None, lambda *a: None)  # nothing is sent here
    base = {"engine": {}, "server_time": 1, "removed": []}
    pusher.add(STATE_EVENT, base | {"alerts": [], "groups": [{"id": 1, "covered": 3}]})
    pusher.add(STATE_EVENT, base | {"alerts": [], "groups": [{"id": 1, "covered": 4},
                                                              {"id": 2, "covered": 1}]})  # fmt: skip
    [(item, _)] = pusher._pending
    assert item["data"]["groups"] == [{"id": 1, "covered": 4}, {"id": 2, "covered": 1}]


def test_a_deleted_groups_end_is_forgotten_and_asks_nothing(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            make_group(settings, clock, [1])
            stub = StubMembers({REF: [row(1)]})
            office, rt, task = await ready(settings, feed, clock, stub)
            office._ask = {999: 1}  # a group that is not there any more
            office._group_next = 0
            office._group_step(rt.now_ms())
            await rt.group_task
            assert office._ask == {} and stub.calls == []
            await closed(rt, task)

    run(scenario)
