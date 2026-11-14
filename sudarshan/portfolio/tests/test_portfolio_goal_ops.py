"""M7.34 (P7 lane R, R6): goals through the sealed API."""

from __future__ import annotations

import dataclasses

from test_portfolio_import_ops import NOW, OWNER, Rig, scenario_of  # noqa: F401  (the rig)
from test_portfolio_tab_ops import clean

from sudarshan.portfolio.core import goals, kinds
from sudarshan.portfolio.ops import REGISTRY

TODAY = 20261026
OPS = ("goals_list", "goal_create", "goal_update")


def on(rig):
    rig.svc.settings = dataclasses.replace(rig.svc.settings, goals=True)
    return rig


def hold(rig, tab, rupees, request, name="Gold bar"):
    """One unit of a manual asset bought for, and worth, ``rupees``."""
    asset = rig.book.asset_create(OWNER, f"a-{request}", "manual", now=NOW, name=name,
                                  asset_class="gold")  # fmt: skip
    version = next(t for t in rig.book.tabs(OWNER) if t["id"] == tab["id"])["version"]
    entry = {"kind": "BUY", "trade_date": 20261020, "account_id": rig.account["id"],
             "asset_id": asset["id"], "qty_u": 1_000_000, "amount_paise": rupees * 100}  # fmt: skip
    rig.book.activity_create(OWNER, tab["id"], version, entry, request, now=NOW)
    rig.svc.store.write(
        OWNER, lambda tx: tx.set_price_point(asset["id"], 20261020, rupees * 10_000, NOW)
    )
    return asset


def home(rig, **more):
    return {"request_id": "g-1", "name": "Home", "target_paise": 100_000_000,
            "target_day": 20311026, "rate_bp": 1200, "tab_ids": [rig.tab["id"]], **more}  # fmt: skip


def test_the_three_ops_are_built_wait_for_the_goals_switch_and_are_no_family_ops():
    assert set(OPS) <= set(REGISTRY)
    assert {kinds.SWITCH_OF[name] for name in OPS} == {"goals"}
    assert not set(OPS) & set(kinds.FAMILY_OPS) and not set(OPS) & set(kinds.JOURNAL_OPS)


@scenario_of
async def test_while_the_switch_is_off_each_answers_not_ready(rig):
    for name, args in (("goals_list", {}), ("goal_create", home(rig)),
                       ("goal_update", {"id": 1, "version": 1})):  # fmt: skip
        assert (await rig.call(name, **args))["code"] == "not_ready", name


@scenario_of
async def test_a_goal_counts_its_tabs_and_says_what_to_add_each_month(rig):
    on(rig)
    assert (await rig.call("goals_list"))["data"] == {"as_of": NOW, "goals": []}
    hold(rig, rig.tab, 200_000, "b-1")
    made = (await rig.call("goal_create", **home(rig, name_alt="Ghar")))["data"]
    me = rig.book.members(OWNER)[0]["id"]
    # The design's GL1: 2,00,000 of 10,00,000, 60 months, 12%: 8,060 a month.
    assert made == {
        "as_of": NOW,
        "goals": [
            {
                "id": made["goals"][0]["id"], "member_id": me, "name": "Home", "name_alt": "Ghar",
                "target_paise": 100_000_000, "target_day": 20311026, "rate_bp": 1200,
                "tab_ids": [rig.tab["id"]], "fixed_paise": None, "archived": False, "version": 1,
                "value_now_paise": 20_000_000, "progress_pct": "20.0000",
                "required_monthly_paise": 806_000, "months_left": 60, "on_track": False,
            }
        ],
    }  # fmt: skip
    again = (await rig.call("goal_create", **home(rig, name="Typed twice")))["data"]
    assert again == made  # the same request made nothing new
    assert (await rig.call("goals_list"))["data"] == made
    # A second tab linked: its value adds up.
    other = rig.book.tab_create(OWNER, "t-2", "Second", "mixed", now=NOW)
    hold(rig, other, 400_000, "b-2", name="Plot")
    goal = made["goals"][0]
    both = (
        await rig.call(
            "goal_update", id=goal["id"], version=1, tab_ids=[rig.tab["id"], other["id"]]
        )
    )["data"]["goals"][0]
    assert both["value_now_paise"] == 60_000_000 and both["version"] == 2
    assert both["on_track"] is True and both["required_monthly_paise"] == 0  # GL7
    assert both["progress_pct"] == "60.0000"
    stale = clean(await rig.call("goal_update", id=goal["id"], version=1, name="Late"))
    assert stale["code"] == "version_conflict"
    # A change decided on an old screen is told so first, whatever else is wrong with it.
    stale = clean(await rig.call("goal_update", id=goal["id"], version=1, target_paise=0))
    assert stale["code"] == "version_conflict"
    assert clean(await rig.call("goal_update", id=999, version=1, name="x"))["code"] == "not_found"


@scenario_of
async def test_a_goal_with_a_typed_amount_and_the_default_return(rig):
    on(rig)
    made = (
        await rig.call(
            "goal_create", request_id="g-2", name="Car", target_paise=12_000_000,
            target_day=20271026, fixed_paise=0,
        )
    )["data"]["goals"][0]  # fmt: skip
    assert made["rate_bp"] == goals.DEFAULT_RATE_BP == 1000 and made["tab_ids"] == []
    assert made["fixed_paise"] == 0 and made["value_now_paise"] == 0
    assert made["months_left"] == 12 and made["progress_pct"] == "0.0000"
    assert made["required_monthly_paise"] == goals.required_monthly(12_000_000, 0, 12, 1000)
    flat = (await rig.call("goal_update", id=made["id"], version=1, rate_bp=0))["data"]["goals"][0]
    assert flat["required_monthly_paise"] == 1_000_000  # 1,20,000 over 12 months
    typed = (await rig.call("goal_update", id=made["id"], version=2, fixed_paise=6_000_000))[
        "data"
    ]["goals"][0]
    assert typed["value_now_paise"] == 6_000_000 and typed["required_monthly_paise"] == 500_000
    # From a typed amount to tabs: both fields in one change.
    moved = (
        await rig.call(
            "goal_update", id=made["id"], version=3, fixed_paise=None, tab_ids=[rig.tab["id"]]
        )
    )["data"]["goals"][0]
    assert moved["fixed_paise"] is None and moved["tab_ids"] == [rig.tab["id"]]
    assert moved["value_now_paise"] == 0  # the tab holds nothing yet


@scenario_of
async def test_what_cannot_be_a_goal_is_refused_in_the_owners_words(rig):
    on(rig)
    upstox = next(t for t in rig.book.tabs(OWNER) if t["kind"] == "upstox")
    for change, said in (
        ({"name": "  "}, "Enter a name"),
        ({"name": "n" * 81}, "up to 80 characters"),
        ({"name_alt": 7}, "Enter a name"),
        ({"target_paise": 0}, "target amount above zero"),
        ({"target_paise": 1.5}, "target amount above zero"),
        ({"target_day": TODAY}, "after today"),
        ({"target_day": 20261301}, "after today"),
        ({"rate_bp": 5001}, "between 0% and 50%"),
        ({"rate_bp": -1}, "between 0% and 50%"),
        ({"tab_ids": [999]}, "from your own tabs"),
        ({"tab_ids": [upstox["id"]]}, "from your own tabs"),
        ({"tab_ids": "all"}, "from your own tabs"),
        ({"tab_ids": []}, "one of the two"),
        ({"fixed_paise": 5}, "one of the two"),
        ({"tab_ids": [], "fixed_paise": -1}, "below zero"),
        ({"member_id": 999}, "from your members"),
        ({"request_id": ""}, "Reload the page"),
    ):
        refused = clean(await rig.call("goal_create", **home(rig, **change)))
        assert refused["code"] == "bad_request" and said in refused["message"], change
    for missing in ("name", "target_paise", "target_day"):
        args = home(rig)
        del args[missing]
        assert clean(await rig.call("goal_create", **args))["code"] == "bad_request", missing
    assert (await rig.call("goals_list"))["data"]["goals"] == []
    made = (await rig.call("goal_create", **home(rig)))["data"]["goals"][0]
    for change, said in (
        ({"fixed_paise": 100}, "one of the two"),
        ({"tab_ids": []}, "one of the two"),
        ({"archived": "yes"}, "cannot be edited"),
        ({"target_day": 20200101}, "after today"),
    ):
        refused = clean(await rig.call("goal_update", id=made["id"], version=1, **change))
        assert refused["code"] == "bad_request" and said in refused["message"], change
    assert (await rig.call("goals_list"))["data"]["goals"] == [made]


@scenario_of
async def test_an_overdue_goal_keeps_its_day_and_asks_for_all_that_is_missing(rig):
    on(rig)
    made = (
        await rig.call("goal_create", **home(rig, tab_ids=[], fixed_paise=5_000_000,
                                             target_paise=10_000_000, target_day=20261101))
    )["data"]["goals"][0]  # fmt: skip
    rig.now = NOW + 30 * 86_400_000  # 25 Nov 2026: the day has passed
    late = (await rig.call("goals_list"))["data"]["goals"][0]
    assert late["months_left"] == 0 and late["required_monthly_paise"] == 4_952_600  # GL4
    kept = await rig.call("goal_update", id=made["id"], version=1, name="Late", target_day=20261101)
    assert kept["data"]["goals"][0]["name"] == "Late"


@scenario_of
async def test_twenty_goals_a_member_and_an_archived_one_makes_room(rig):
    on(rig)
    for at in range(kinds.GOALS_MAX):
        done = await rig.call("goal_create", **home(rig, request_id=f"g-{at}", name=f"Goal {at}"))
        assert len(done["data"]["goals"]) == at + 1
    full = clean(await rig.call("goal_create", **home(rig, request_id="g-x")))
    assert full["code"] == "bad_request" and "up to 20 goals" in full["message"]
    first = done["data"]["goals"][0]
    shelved = (await rig.call("goal_update", id=first["id"], version=1, archived=True))["data"]
    assert shelved["goals"][0]["archived"] is True and len(shelved["goals"]) == 20
    more = await rig.call("goal_create", **home(rig, request_id="g-x"))
    assert len(more["data"]["goals"]) == 21
    back = clean(await rig.call("goal_update", id=first["id"], version=2, archived=False))
    assert back["code"] == "bad_request" and "up to 20 goals" in back["message"]
    # Another member has room of his own.
    wife = rig.book.member_create(OWNER, "m-2", "Second member", now=NOW)
    hers = await rig.call("goal_create", **home(rig, request_id="g-y", member_id=wife["id"]))
    assert hers["data"]["goals"][-1]["member_id"] == wife["id"]


@scenario_of
async def test_a_deleted_tab_counts_as_nothing_and_no_key_is_unknown_to_the_mask_table(rig):
    from test_portfolio_closed_world import answer_misses

    on(rig)
    other = rig.book.tab_create(OWNER, "t-2", "Second", "mixed", now=NOW)
    hold(rig, rig.tab, 200_000, "b-1")
    made = (await rig.call("goal_create", **home(rig, tab_ids=[rig.tab["id"], other["id"]])))[
        "data"
    ]
    assert answer_misses(made) == set()
    version = next(t for t in rig.book.tabs(OWNER) if t["id"] == other["id"])["version"]
    shelved = rig.book.tab_update(OWNER, other["id"], version, {"archived": True}, now=NOW)
    rig.book.tab_delete(OWNER, other["id"], shelved["version"], "Second", now=NOW)
    after = (await rig.call("goals_list"))["data"]["goals"][0]
    assert (
        after["value_now_paise"] == 20_000_000 and after["tab_ids"] == made["goals"][0]["tab_ids"]
    )
    renamed = await rig.call("goal_update", id=after["id"], version=1, name="Still here")
    assert renamed["data"]["goals"][0]["name"] == "Still here"
