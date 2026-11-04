"""Every op of the tabs, books, layouts and views end to end through the sealed API (PB10)."""

from __future__ import annotations

import re

from test_portfolio_import_ops import NOW, OWNER, Rig, scenario_of  # noqa: F401  (the rig)

from sudarshan.portfolio import ops
from sudarshan.portfolio.core import kinds

TERMS = re.compile(r"HTTP|Traceback|Exception|sqlite|SQL|\b[45]\d\d\b|None|null")


def clean(answer: dict) -> dict:
    """An answer that is an error says it in words a trader can act on."""
    if answer.get("status") == "error":
        assert not TERMS.search(answer["message"]), answer["message"]
    return answer


def buy(rig, **more):
    return {
        "kind": "BUY", "trade_date": 20261001, "account_id": rig.account["id"],
        "asset_id": rig.share["id"], "qty_u": 10_000_000, "amount_paise": 1_000_000, **more,
    }  # fmt: skip


def test_every_op_of_the_contract_is_served_but_the_three_that_belong_to_other_parts():
    assert set(kinds.OP_NAMES) - set(ops.REGISTRY) == set()


@scenario_of
async def test_state_names_the_owners_tabs_members_and_accounts(rig):
    data = (await rig.call("state"))["data"]
    assert data["today"] == 20261026 and data["now"] == NOW
    assert {"Long term", "Upstox"} <= {t["name"] for t in data["tabs"]}
    assert any(a["name"] == "Zerodha" for a in data["accounts"]) and data["members"]
    assert "principal" not in str(data) and "request_id" not in str(data)
    assert (await rig.call("summary"))["data"]["entries"] is not None


@scenario_of
async def test_a_tab_is_made_changed_archived_and_deleted_in_the_owners_words(rig):
    made = (await rig.call("tab_create", request_id="r-1", name="Gold", kind="gold", pinned=True))[
        "data"
    ]
    again = (await rig.call("tab_create", request_id="r-1", name="Gold", kind="gold"))["data"]
    assert again["id"] == made["id"] and made["pinned"] is True and made["system"] is False
    changed = (await rig.call("tab_update", id=made["id"], version=made["version"], name="Metals"))[
        "data"
    ]
    assert changed["name"] == "Metals" and changed["version"] == made["version"] + 1
    stale = clean(
        await rig.call("tab_update", id=made["id"], version=made["version"], name="Again")
    )
    assert stale["code"] == "version_conflict" and "changed on another screen" in stale["message"]
    blocked = clean(
        await rig.call(
            "tab_delete", id=made["id"], version=changed["version"], confirm_name="Metals"
        )
    )
    assert blocked["code"] == "refused" and "archive" in blocked["message"].lower()
    archived = (
        await rig.call("tab_update", id=made["id"], version=changed["version"], archived=True)
    )["data"]
    wrong = clean(
        await rig.call("tab_delete", id=made["id"], version=archived["version"], confirm_name="x")
    )
    assert wrong["code"] == "refused"
    gone = await rig.call(
        "tab_delete", id=made["id"], version=archived["version"], confirm_name="Metals"
    )
    assert gone["data"] == {"id": made["id"]}
    assert clean(await rig.call("tab_get", id=made["id"]))["code"] == "not_found"
    bad = clean(await rig.call("tab_create", request_id="r-2", name="  ", kind="gold"))
    assert bad["code"] == "bad_request"


@scenario_of
async def test_members_accounts_and_assets_are_made_and_changed(rig):
    me = (await rig.call("member_create", request_id="m-1", name="Spouse", relation="family"))[
        "data"
    ]
    assert me["name"] == "Spouse" and "request_id" not in me
    me2 = (await rig.call("member_update", id=me["id"], version=me["version"], name="Partner"))[
        "data"
    ]
    assert me2["name"] == "Partner"
    acc = (
        await rig.call(
            "account_create", request_id="a-9", member_id=me["id"], name="Bank", kind="bank"
        )
    )["data"]
    assert acc["system"] is False and acc["member_id"] == me["id"]
    acc2 = (await rig.call("account_update", id=acc["id"], version=acc["version"], note="joint"))[
        "data"
    ]
    assert acc2["note"] == "joint"
    asset = (
        await rig.call(
            "asset_create",
            request_id="x-1",
            kind="manual",
            name="Painting",
            asset_class="other",
            unit_label="piece",
            first_value={"day": 20261001, "price_u": 50_000_000},
        )
    )["data"]
    assert asset["aliases"] == [] and asset["tags"] == [] and "principal" not in asset
    twin = clean(
        await rig.call(
            "asset_create", request_id="x-2", kind="manual", name="painting", asset_class="other"
        )
    )
    assert twin["code"] == "refused" and "already" in twin["message"]
    tagged = (
        await rig.call(
            "asset_update", id=asset["id"], version=asset["version"], tags=["art"], sector="Art"
        )
    )["data"]
    assert tagged["tags"] == ["art"] and tagged["sector"] == "Art"
    listed = (
        await rig.call(
            "asset_create", request_id="x-3", kind="listed", symbol="tcs", exchange="NSE"
        )
    )["data"]
    assert listed["symbol"] == "TCS" and listed["isin"] == "INE467B01029"
    assert listed["name"] == "Tata Consultancy Services"
    assert {"kind": "symbol", "value": "TCS"} in listed["aliases"]
    assert {"kind": "isin", "value": "INE467B01029"} in listed["aliases"]
    unknown = clean(
        await rig.call(
            "asset_create", request_id="x-4", kind="listed", symbol="NOSUCH", exchange="NSE"
        )
    )
    assert unknown["code"] == "not_found" and "NOSUCH" in unknown["message"]


@scenario_of
async def test_a_value_kept_by_hand_is_set_for_a_day_and_a_share_cannot_have_one(rig):
    gold = (
        await rig.call(
            "asset_create", request_id="g-1", kind="manual", name="Gold bar", asset_class="gold"
        )
    )["data"]
    done = await rig.call("price_point_set", id=gold["id"], day=20261020, price_u=70_000_000)
    assert done["data"] == {"asset_id": gold["id"], "day": 20261020, "price_u": 70_000_000}
    share = clean(
        await rig.call("price_point_set", id=rig.share["id"], day=20261020, price_u=70_000_000)
    )
    assert share["code"] == "refused" and "keep by hand" in share["message"]
    future = clean(
        await rig.call("price_point_set", id=gold["id"], day=20261101, price_u=70_000_000)
    )
    assert future["code"] == "bad_request" and "after today" in future["message"]
    zero = clean(await rig.call("price_point_set", id=gold["id"], day=20261020, price_u=0))
    assert zero["code"] == "bad_request"


@scenario_of
async def test_entries_are_checked_saved_changed_and_removed_with_the_tab_version(rig):
    tab = rig.tab["id"]
    dry = (
        await rig.call(
            "activity_create",
            id=tab,
            version=rig.version(),
            request_id="e-0",
            dry_run=True,
            entry=buy(rig),
        )
    )["data"]
    assert dry["activity"] is None and dry["effect"]["words"]
    assert rig.book.activities_list(OWNER, tab)["items"] == []
    version = rig.version()
    made = (
        await rig.call("activity_create", id=tab, version=version, request_id="e-1", entry=buy(rig))
    )["data"]
    assert made["activity"]["id"] and made["tab_version"] == version + 1
    assert "import_key" not in made["activity"] and "principal" not in made["activity"]
    stale = clean(
        await rig.call("activity_create", id=tab, version=version, request_id="e-2", entry=buy(rig))
    )
    assert stale["code"] == "version_conflict"
    short = clean(
        await rig.call(
            "activity_create",
            id=tab,
            version=made["tab_version"],
            request_id="e-3",
            entry=buy(rig, kind="SELL", qty_u=20_000_000),
        )
    )
    assert short["code"] == "refused" and "sale" in short["message"].lower()
    edited = (
        await rig.call(
            "activity_update",
            id=made["activity"]["id"],
            version=made["tab_version"],
            entry=buy(rig, qty_u=12_000_000),
        )
    )["data"]
    assert edited["activity"]["qty_u"] == 12_000_000
    page = (await rig.call("activities_list", id=tab, limit=1))["data"]
    assert len(page["items"]) == 1 and page["next"] is None
    gone = (
        await rig.call("activity_delete", id=made["activity"]["id"], version=edited["tab_version"])
    )["data"]
    assert gone["tab_version"] == edited["tab_version"] + 1
    assert (await rig.call("activities_list", id=tab))["data"]["items"] == []


@scenario_of
async def test_tab_get_says_no_price_yet_and_performance_answers(rig):
    await rig.call(
        "activity_create", id=rig.tab["id"], version=rig.version(), request_id="e-1", entry=buy(rig)
    )
    view = (await rig.call("tab_get", id=rig.tab["id"]))["data"]
    (row,) = view["rows"]
    assert row["fresh"]["words"] == "No price yet" and row["price"]["price_u"] is None
    assert {"cas", "auction_from", "auction_until", "auction_close"} <= set(row)
    assert view["totals"]["unpriced"] == 1 and view["layout"]["version"] == 0
    then = (await rig.call("tab_get", id=rig.tab["id"], as_of=20260930))["data"]
    assert then["rows"] == [] and then["as_of"] == 20260930
    bad = clean(await rig.call("tab_get", id=rig.tab["id"], as_of="soon"))
    assert bad["code"] == "bad_request"
    report = (await rig.call("performance", id=rig.tab["id"]))["data"]
    assert report["money_in_paise"] == 1_000_000 and len(report["benchmarks"]) == 2
    assert report["series"] == [] and report["series_words"]  # nothing priced: no complete day yet


@scenario_of
async def test_the_layout_is_saved_from_its_version_and_reset_to_the_kinds_defaults(rig):
    tab = rig.tab["id"]
    first = (await rig.call("layout_get", id=tab))["data"]
    assert first["version"] == 0 and first["spec"] == first["defaults"]
    spec = {**first["spec"], "widgets": ["summary", "holdings"], "sort": "name", "group": "account"}
    saved = (await rig.call("layout_set", id=tab, version=0, spec=spec))["data"]
    assert saved["version"] == 1 and saved["spec"]["widgets"] == ["summary", "holdings"]
    stale = clean(await rig.call("layout_set", id=tab, version=0, spec=spec))
    assert stale["code"] == "version_conflict"
    for broken in ({"sort": "luck"}, {"widgets": ["summary", "summary"]}, {"columns": []}, "text"):
        bad = clean(await rig.call("layout_set", id=tab, version=1, spec=broken))
        assert bad["code"] == "bad_request", broken
    unknown = (await rig.call("layout_set", id=tab, version=1, spec={**spec, "colour": "red"}))[
        "data"
    ]
    assert "colour" not in unknown["spec"]  # keys we do not know are dropped
    reset = (await rig.call("layout_set", id=tab, version=2, spec=None))["data"]
    assert reset["spec"] == reset["defaults"] and reset["version"] == 3
    assert (await rig.call("tab_get", id=tab))["data"]["layout"]["version"] == 3


@scenario_of
async def test_saved_views_are_made_changed_switched_and_removed(rig):
    tab = rig.tab["id"]
    spec = {
        "filters": {"classes": ["equity"]},
        "sort": "name",
        "group": "none",
        "columns": ["name", "value"],
    }
    first = (
        await rig.call(
            "view_create", id=tab, request_id="v-1", name="Shares", spec=spec, is_default=True
        )
    )["data"]
    assert first["is_default"] is True and first["spec"]["filters"]["classes"] == ["equity"]
    again = (await rig.call("view_create", id=tab, request_id="v-1", name="Shares", spec=spec))[
        "data"
    ]
    assert again["id"] == first["id"]  # sent twice: the one already made
    taken = clean(
        await rig.call(
            "view_create", id=tab, request_id="v-2", name="shares", spec={**spec, "sort": "value"}
        )
    )
    assert taken["code"] == "refused" and "already has a view" in taken["message"]
    second = (
        await rig.call(
            "view_create", id=tab, request_id="v-3", name="Gold", spec=spec, is_default=True
        )
    )["data"]
    listed = (await rig.call("view_list", id=tab))["data"]["views"]
    assert {v["name"]: v["is_default"] for v in listed} == {"Shares": False, "Gold": True}
    renamed = (
        await rig.call("view_update", id=second["id"], version=second["version"], name="Metals")
    )["data"]
    assert renamed["name"] == "Metals"
    stale = clean(
        await rig.call("view_update", id=second["id"], version=second["version"], name="X")
    )
    assert stale["code"] == "version_conflict"
    assert (
        clean(await rig.call("view_create", id=tab, request_id="v-4", name="", spec=spec))["code"]
        == "bad_request"
    )
    shown = (await rig.call("tab_get", id=tab, view=first["id"]))["data"]
    assert shown["filters"]["classes"] == ["equity"] and len(shown["views"]) == 2
    old = clean(await rig.call("view_delete", id=first["id"], version=first["version"]))
    assert old["code"] == "version_conflict"  # making Gold the default moved Shares on
    fresh = next(
        v for v in (await rig.call("view_list", id=tab))["data"]["views"] if v["id"] == first["id"]
    )
    gone = await rig.call("view_delete", id=first["id"], version=fresh["version"])
    assert gone["data"] == {"id": first["id"]}
    missing = clean(await rig.call("view_delete", id=999, version=1))
    assert missing["code"] == "not_found"
