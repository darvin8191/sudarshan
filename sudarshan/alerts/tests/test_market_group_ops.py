"""List alerts' owner ops (M8.14, A5): group_add, group_edit, group_pause, group_resume and
group_delete on the desk, the fit check on both limits, groups_by_ref, the members client built
from the vault purpose, and a members route that is not there yet."""

from __future__ import annotations

import logging

import httpx
import pytest
from alerts_fakes import FakeOpenAlgo, ist, wait_until
from cryptography.fernet import Fernet
from groups_fakes import StubMembers, key, row
from market_fakes import FakeFeed, ms
from test_market_desk import closed, opened, refused, run
from test_market_group_sync import GROUP, MOVE, REF, make_group, members_of, ready
from test_market_runtime import Clock, record

from sudarshan.alerts.market import groups
from sudarshan.alerts.market import members as members_mod
from sudarshan.alerts.market.members import OpenAlgoMembers
from sudarshan.autologin.handoff import NonceBook, open_sealed
from sudarshan.vault import WATCHLISTS_INTERNAL_INFO, fernet_for

DRAFT = {"ref": REF, "name": "Swing", "kind": "move", "params": MOVE, "frequency": "every_time",
         "cooldown_s": 300}  # fmt: skip
OWNER = {"principal": "owner"}
RANKED_WORDS = (
    "A ranked list changes with every read. Set the alert on a list or a built-in list instead."
)


def lists(n: int, ref: str = REF) -> dict:
    return {ref: [row(i) for i in range(1, n + 1)]}


async def add(office, **fields) -> dict:
    return await office.call("group_add", OWNER | {"group": DRAFT | fields})


def test_a_group_over_a_list_is_made_with_both_numbers_said(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await ready(settings, feed, clock, StubMembers(lists(24)))
            made = await add(office)
            assert made["fit"] == (
                "This alert needs 24 instruments and 24 alerts. In use now: 0 of 200 instruments "
                "and 0 of 500 Active alerts. It fits.")  # fmt: skip
            group = made["group"]
            assert group["members"] == 24 and group["covered"] == 24 and group["status"] == "active"
            assert group["words"]["title"] == "Swing list · Up 2% on the day"
            assert len(members_of(settings, group["id"])) == 24
            assert len(rt.book.alerts()) == 24
            state = await office.call("state", OWNER)
            assert state["groups_by_ref"] == {REF: [group["id"]]}
            assert [g["id"] for g in state["groups"]] == [group["id"]]
            assert {a["group_id"] for a in state["alerts"]} == {group["id"]}
            await closed(rt, task)

    run(scenario)


def test_the_third_group_on_a_180_symbol_list_is_refused_with_both_numbers_and_nothing_made(
    alerts, quick
):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await ready(settings, feed, clock, StubMembers(lists(180)))
            first = await add(office, name="One")
            second = await add(office, name="Two", kind="vwap", params={"direction": "above"})
            assert second["fit"].endswith("In use now: 180 of 200 instruments and 180 of 500 "
                                          "Active alerts. It fits.")  # fmt: skip
            third = await refused(add(office, name="Three", kind="day_break",
                                      params={"side": "high", "after": "09:30"}))  # fmt: skip
            assert (third.status, third.code) == (409, "limit")
            assert third.message == ("It does not fit: it needs 180 alerts and 140 of the 500 are "
                                     "free. Nothing was made.")  # fmt: skip
            assert [g["id"] for g in record(settings, "groups")] == [
                first["group"]["id"],
                second["group"]["id"],
            ]
            assert len(record(settings, "alerts")) == 360 and len(rt.book.alerts()) == 360
            await closed(rt, task)

    run(scenario)


def test_the_limit_is_checked_in_the_transaction_too(alerts, quick):
    """The early check reads the Book; a change the Book has not seen yet is caught by the
    transaction, which refuses in the same words."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await ready(settings, feed, clock, StubMembers(lists(30)),
                                           SUDARSHAN_MARKET_ALERTS_MAX_ACTIVE="40")  # fmt: skip
            make_group(settings, clock, range(101, 121))  # 20 Active alerts the Book does not hold
            caught = await refused(add(office))
            assert caught.code == "limit" and caught.message == (
                "It does not fit: it needs 30 alerts and 20 of the 40 are free. Nothing was made."
            )
            assert len(record(settings, "groups")) == 1
            await closed(rt, task)

    run(scenario)


@pytest.mark.parametrize("ref", ["ranked:upstox:gainers", "ranked:x"])
def test_a_ranked_list_cannot_carry_a_group(alerts, quick, ref):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            stub = StubMembers(lists(3, ref))
            office, rt, task = await ready(settings, feed, clock, stub)
            caught = await refused(add(office, ref=ref))
            assert (caught.status, caught.code, caught.message) == (400, "invalid", RANKED_WORDS)
            other = await refused(add(office, ref="screen:4"))
            assert other.code == "invalid" and "ranked" not in other.message
            assert stub.calls == [] and record(settings, "groups") == []
            await closed(rt, task)

    run(scenario)


def test_a_price_level_is_not_offered_across_a_list(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await ready(settings, feed, clock, StubMembers(lists(3)))
            caught = await refused(add(office, kind="price",
                                       params={"op": "crosses_above", "level": "100"}))  # fmt: skip
            assert caught.field == "kind" and caught.message == groups.PRICE_LEVEL
            await closed(rt, task)

    run(scenario)


def test_pause_resume_edit_and_delete_apply_to_every_member_in_one_transaction(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            stub = StubMembers(lists(12))
            office, rt, task = await ready(settings, feed, clock, stub)
            made = (await add(office))["group"]
            gid = made["id"]
            ids = [m["id"] for m in record(settings, "group_members", gid)]

            paused = (await office.call("group_pause", OWNER | {"id": gid, "version": 1}))["group"]
            assert paused["status"] == "paused" and paused["version"] == 2
            rows = record(settings, "group_members", gid)
            assert {m["status"] for m in rows} == {"paused"} and len(rows) == 12
            assert {m["version"] for m in rows} == {2}  # one transaction: every member moved once
            assert all(rt.alert(i).status == "paused" for i in ids)
            again = await refused(office.call("group_pause", OWNER | {"id": gid, "version": 2}))
            assert again.code == "refused" and again.message == "This alert is already paused."

            edit = {"params": MOVE | {"percent": "3"}, "frequency": "every_time", "cooldown_s": 600,
                    "name": "Swing 3"}  # fmt: skip
            edited = (
                await office.call("group_edit", OWNER | {"id": gid, "version": 2, "group": edit})
            )["group"]
            assert edited["status"] == "paused" and edited["name"] == "Swing 3"
            assert edited["words"]["condition"] == "Up 3% on the day"
            rows = record(settings, "group_members", gid)
            assert {m["params"]["percent"] for m in rows} == {"3"}
            assert {m["cooldown_s"] for m in rows} == {600} and {m["status"] for m in rows} == {
                "paused"}  # fmt: skip
            assert all(rt.alert(m["id"]).params["percent"] == "3" for m in rows)
            stale = await refused(
                office.call("group_edit", OWNER | {"id": gid, "version": 2, "group": edit})
            )
            assert stale.code == "conflict"

            resumed = (await office.call("group_resume", OWNER | {"id": gid, "version": 3}))[
                "group"
            ]
            assert resumed["status"] == "active"
            assert {m["status"] for m in record(settings, "group_members", gid)} == {"active"}
            assert all(rt.alert(i).status == "active" for i in ids)
            await wait_until(lambda: key(1) in feed.keys)
            assert ("members", REF) in stub.calls  # a resume follows its list at once

            gone = await office.call("group_delete", OWNER | {"id": gid})
            assert gone == {"deleted": True, "alerts": 12}
            assert record(settings, "groups") == [] and record(settings, "alerts") == []
            assert rt.book.alerts() == [] and all(rt.alert(i) is None for i in ids)
            assert (await refused(office.call("group_delete", OWNER | {"id": gid}))).code == "gone"
            await closed(rt, task)

    run(scenario)


def test_a_resume_that_does_not_fit_is_refused_and_changes_nothing(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await ready(settings, feed, clock, StubMembers(lists(30)),
                                           SUDARSHAN_MARKET_ALERTS_MAX_ACTIVE="40")  # fmt: skip
            gid = (await add(office))["group"]["id"]
            await office.call("group_pause", OWNER | {"id": gid, "version": 1})
            make_group(settings, clock, range(101, 121))
            for member in record(settings, "group_members", 2):
                rt.owner_put(member)
            caught = await refused(office.call("group_resume", OWNER | {"id": gid, "version": 2}))
            assert caught.code == "limit" and caught.message.endswith("Nothing was changed.")
            assert record(settings, "group", gid)["status"] == "paused"
            assert {m["status"] for m in record(settings, "group_members", gid)} == {"paused"}
            await closed(rt, task)

    run(scenario)


def test_a_list_that_cannot_be_read_makes_nothing_and_says_so(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            stub = StubMembers(lists(5))
            office, rt, task = await ready(settings, feed, clock, stub)
            stub.down = True
            caught = await refused(add(office))
            assert (caught.status, caught.code) == (503, "not_answering")
            assert caught.message == ("The watchlist could not be read just now, so nothing was "
                                      "made. Try again in a minute.")  # fmt: skip
            stub.down, stub.state = False, "off"
            assert (await refused(add(office))).code == "off"
            stub.state = "ok"
            stub.gone.add(REF)
            assert (await refused(add(office))).code == "gone"
            assert record(settings, "groups") == []
            await closed(rt, task)

    run(scenario)


def test_the_members_client_seals_under_the_vault_purpose(monkeypatch):
    monkeypatch.setattr(members_mod.loopback, "refusal", lambda url: None)

    async def scenario():
        fernet = fernet_for("p" * 48, WATCHLISTS_INTERNAL_INFO)
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            opened_ = open_sealed(fernet, request.content, op=request.url.params["op"],
                                  seen=NonceBook(8), max_bytes=16 * 1024)  # fmt: skip
            seen.append((request.url.params["op"], opened_.get("ref")))
            return httpx.Response(
                200,
                json={"status": "ok", "data": {"version": "v1", "rows": [], "created_at": "t1"}},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            client_ = members_mod.OpenAlgoMembers("http://127.0.0.1:5000", client, fernet)
            answer = await client_.members(REF)
            assert answer.list_key == "t1" and seen == [("members", REF)]
            other = members_mod.OpenAlgoMembers(
                "http://127.0.0.1:5000", client, fernet_for("p" * 48, b"sudarshan/other/v1")
            )
            with pytest.raises(Exception):  # noqa: B017 - a seal under another purpose never opens
                await other.version()

    import asyncio

    asyncio.run(scenario())


def test_the_service_hands_the_runtime_a_members_fernet_under_the_watchlists_purpose():
    import inspect

    from sudarshan.alerts import service

    source = inspect.getsource(service._market_parts)
    assert "members_fernet=fernet_for(pepper, WATCHLISTS_INTERNAL_INFO)" in source


def test_a_404_from_the_members_route_leaves_the_group_quiet_with_its_words(
    alerts, quick, caplog, monkeypatch
):
    monkeypatch.setattr(members_mod.loopback, "refusal", lambda url: None)

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            group = make_group(settings, clock, [1, 2, 3])
            office, rt, task = await ready(settings, feed, clock, StubMembers())
            seals = []

            def handler(request: httpx.Request) -> httpx.Response:
                seals.append(request.url.params["op"])
                return httpx.Response(404, text="<html>Not found</html>")

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                fernet = fernet_for("p" * 48, WATCHLISTS_INTERNAL_INFO)
                rt.members = OpenAlgoMembers("http://127.0.0.1:5000", client, fernet)
                caplog.set_level(logging.DEBUG)
                assert await office.sync(group["id"]) is None
                assert members_of(settings, group["id"]) == ["S1", "S2", "S3"]
                state = await office.call("state", OWNER)
                [card] = state["groups"]
                assert card["follow"] == (
                    "The list could not be read just now, so this alert keeps its last 3 symbols "
                    "and asks again in a minute.")  # fmt: skip
                assert card["status"] == "active" and card["status_reason"] == ""
                office._group_next = 0
                office._group_step(rt.now_ms() + 61_000)  # the minute's check: the same, quiet
                if rt.group_task is not None:
                    await rt.group_task
                assert seals and set(seals) <= {"members", "version"}
                assert not [r for r in caplog.records if r.levelno >= logging.WARNING
                            and "list" in r.getMessage().lower()]  # fmt: skip
            await closed(rt, task)

    run(scenario)


def test_a_reused_list_id_stops_the_group_with_the_deleted_list_words(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            stub = StubMembers(lists(3))
            office, rt, task = await ready(settings, feed, clock, stub)
            made = (await add(office))["group"]
            assert record(settings, "group", made["id"])["list_key"] == "2026-10-01 09:00:00"
            stub.created[REF] = "2026-10-20 12:00:00"  # OpenAlgo gave the id to another list
            brief = await office.sync(made["id"])
            assert brief["status"] == "stopped"
            assert brief["status_reason"] == "The list Swing was deleted, so this alert stopped."
            assert {m["status"] for m in record(settings, "group_members", made["id"])} == {
                "stopped"}  # fmt: skip
            await closed(rt, task)

    run(scenario)


def test_removals_apply_even_when_the_adds_do_not_fit_and_the_sync_plans_again(alerts, quick):
    """Review L13: a plan made on stale holders must never keep an alert the owner removed."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            group = make_group(settings, clock, [1, 2, 3])
            stub = StubMembers({REF: [row(1), row(4), row(5)]})  # 2, 3 removed; 4, 5 added
            office, rt, task = await ready(settings, feed, clock, stub,
                                           SUDARSHAN_MARKET_ALERTS_MAX_ACTIVE="4")  # fmt: skip
            for member in record(settings, "group_members", group["id"]):
                rt.owner_put(member)
            make_group(settings, clock, [11])  # Active alerts the Book has not seen: stale holders
            brief = await office.sync(group["id"])
            names = members_of(settings, group["id"])
            assert "S2" not in names and "S3" not in names  # removed whatever the limits said
            assert names[0] == "S1" and len(names) <= 3
            assert brief is not None
            await closed(rt, task)

    run(scenario)


def test_the_morning_sync_runs_again_once_the_master_is_loaded(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 8, 30))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            make_group(settings, clock, [1])
            stub = StubMembers({REF: [row(1)]})
            office, rt, task = await ready(settings, feed, clock, stub)
            day = ist(2026, 10, 21, 8, 30).date()
            office._group_day = None
            office._group_step(rt.now_ms())  # the morning build, before the master is loaded
            await rt.group_task
            assert stub.calls == [("members", REF)]
            stub.calls.clear()
            office._group_next = 0
            rt._master_read = (day, False)  # read before 09:00: not the day's
            office._group_step(rt.now_ms())
            assert rt.group_task.done() and stub.calls == []
            rt._master_read = (day, True)  # the day's login downloaded it; the runtime read it
            office._group_next = 10**15
            office._group_step(rt.now_ms())
            await rt.group_task
            assert stub.calls == [("members", REF)]
            stub.calls.clear()
            office._group_step(rt.now_ms())  # once only
            assert stub.calls == []
            await closed(rt, task)

    run(scenario)
