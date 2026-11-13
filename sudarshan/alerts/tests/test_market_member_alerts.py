"""A member of a list alert changes only through its group (M8.14, A7): the owner's single-alert
ops refuse it in a sentence naming the group and change nothing; the same ops still work on an
alert with no group; the group's own ops still change every member."""

from __future__ import annotations

import pytest
from alerts_fakes import FakeOpenAlgo, ist
from groups_fakes import StubMembers
from market_fakes import FakeFeed
from test_market_desk import closed, draft, refused, run
from test_market_group_ops import OWNER, add, lists
from test_market_group_sync import MOVE, ready
from test_market_runtime import Clock, record

SENTENCE = "This alert is part of the list alert Swing. Change it from the list alert's card."


@pytest.mark.parametrize(
    "op,extra",
    [
        ("update", {"action": "edit", "alert": {"note": "mine"}}),
        ("update", {"action": "pause"}),
        ("update", {"action": "resume"}),
        ("update", {"action": "restart"}),
        ("delete", {}),
    ],
)
def test_each_single_alert_op_refuses_a_member_and_changes_nothing(alerts, quick, op, extra):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await ready(settings, feed, clock, StubMembers(lists(3)))
            gid = (await add(office))["group"]["id"]
            before = record(settings, "group_members", gid)
            member = before[0]
            message = OWNER | {"id": member["id"], "version": member["version"]} | extra
            error = await refused(office.call(op, message))
            assert (error.status, error.code, error.message) == (409, "group_member", SENTENCE)
            assert record(settings, "group_members", gid) == before  # nothing changed
            await closed(rt, task)

    run(scenario)


def test_the_same_ops_still_work_on_an_alert_with_no_group(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await ready(settings, feed, clock, StubMembers(lists(3)))
            made = await office.call("create", OWNER | {"alert": draft()})
            one = OWNER | {"id": made["id"]}
            paused = await office.call("update", one | {"version": made["version"],
                                                        "action": "pause"})  # fmt: skip
            assert paused["status"] == "paused"
            resumed = await office.call("update", one | {"version": paused["version"],
                                                         "action": "resume"})  # fmt: skip
            assert resumed["status"] == "active"
            edited = await office.call("update", one | {"version": resumed["version"],
                                                        "action": "edit",
                                                        "alert": draft(note="mine")})  # fmt: skip
            assert edited["id"] == made["id"]
            assert await office.call("delete", one) == {"deleted": True}
            await closed(rt, task)

    run(scenario)


def test_the_group_ops_still_change_every_member(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await ready(settings, feed, clock, StubMembers(lists(3)))
            gid = (await add(office))["group"]["id"]
            await office.call("group_pause", OWNER | {"id": gid, "version": 1})
            assert {m["status"] for m in record(settings, "group_members", gid)} == {"paused"}
            await office.call("group_resume", OWNER | {"id": gid, "version": 2})
            assert {m["status"] for m in record(settings, "group_members", gid)} == {"active"}
            edit = {"params": MOVE | {"percent": "3"}, "frequency": "every_time",
                    "cooldown_s": 600, "name": "Swing"}  # fmt: skip
            await office.call("group_edit", OWNER | {"id": gid, "version": 3, "group": edit})
            assert {m["cooldown_s"] for m in record(settings, "group_members", gid)} == {600}
            await office.call("group_delete", OWNER | {"id": gid})
            assert record(settings, "group_members", gid) == []
            await closed(rt, task)

    run(scenario)
