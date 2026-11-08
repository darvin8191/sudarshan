"""The book: tabs, members, accounts, assets and ledger-checked entries (PB2)."""

from __future__ import annotations

import pytest

from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.book import WORDS as BOOK_WORDS
from sudarshan.portfolio.book import Book
from sudarshan.portfolio.core.kinds import MAX_TABS, WORDS
from sudarshan.portfolio.store import (
    DB_FILENAME,
    Invalid,
    NotFound,
    PortfolioStore,
    Refused,
    Scope,
    VersionConflict,
)

NOW = 1_793_000_000_000  # 26 Oct 2026
OWNER = Scope.owner()
ISIN = "INE002A01018"


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store_module.stop()


@pytest.fixture
def made(tmp_path):
    opened = PortfolioStore(tmp_path / "s" / DB_FILENAME, now=NOW)
    yield opened
    opened.close()


@pytest.fixture
def book(made):
    return Book(made)


@pytest.fixture
def world(book):
    """A member, a demat account, a listed share, a fund and an empty tab."""
    me = book.members(OWNER)[0]
    account = book.account_create(OWNER, "a-1", me["id"], "Zerodha", "demat", now=NOW)
    share = book.asset_create(
        OWNER, "s-1", "listed", now=NOW, exchange="NSE", symbol="reliance", isin=ISIN,
        name="Reliance Industries",
    )  # fmt: skip
    fund = book.asset_create(
        OWNER, "f-1", "mf", now=NOW, amfi_code="120503", name="Some Flexi Cap Fund"
    )
    tab = book.tab_create(OWNER, "t-1", "Long term", "stocks", now=NOW)
    return {"me": me, "account": account, "share": share, "fund": fund, "tab": tab}


def entry(w, kind="BUY", **more):
    base = {"kind": kind, "trade_date": 20250110, "account_id": w["account"]["id"],
            "asset_id": w["share"]["id"], "qty_u": 10_000_000, "amount_paise": 1_428_250}  # fmt: skip
    return {**base, **more}


def add(book, w, rid, version=None, **more):
    tab = w["tab"]
    version = book.tabs(OWNER)[-1]["version"] if version is None else version
    return book.activity_create(OWNER, tab["id"], version, entry(w, **more), rid, now=NOW)


def tab_version(book, w):
    return next(t for t in book.tabs(OWNER) if t["id"] == w["tab"]["id"])["version"]


def activities(book, w):
    return book.activities_list(OWNER, w["tab"]["id"])["items"]


# ---- tabs ----------------------------------------------------------------------------------
def test_a_tab_is_made_with_defaults_after_the_upstox_tab(book):
    tab = book.tab_create(OWNER, "r-1", "  Gold  ", "gold", now=NOW)
    assert (tab["name"], tab["kind"], tab["icon"], tab["colour"]) == (
        "Gold",
        "gold",
        "briefcase",
        "brand",
    )
    assert (tab["version"], tab["archived"], tab["system"], tab["manual_old_days"]) == (
        1,
        False,
        False,
        30,
    )
    assert [t["name"] for t in book.tabs(OWNER)] == ["Upstox", "Gold"]
    assert tab["position"] > book.tabs(OWNER)[0]["position"]


def test_a_retried_create_returns_the_row_already_made(book):
    first = book.tab_create(OWNER, "same", "A", "stocks", now=NOW)
    again = book.tab_create(OWNER, "same", "A", "stocks", now=NOW + 5)
    assert again == first and len(book.tabs(OWNER)) == 2
    me = book.members(OWNER)[0]
    m1 = book.member_create(OWNER, "m", "Someone", now=NOW)
    assert book.member_create(OWNER, "m", "Someone", now=NOW) == m1
    a1 = book.account_create(OWNER, "ac", me["id"], "Bank", "bank", now=NOW)
    assert book.account_create(OWNER, "ac", me["id"], "Bank", "bank", now=NOW) == a1
    s1 = book.asset_create(OWNER, "as", "manual", now=NOW, name="Locker gold")
    assert book.asset_create(OWNER, "as", "manual", now=NOW, name="Locker gold") == s1
    assert (
        len(book.members(OWNER)) == 2
        and len(book.accounts(OWNER)) == 2
        and len(book.assets(OWNER)) == 1
    )


def test_a_tab_needs_a_name_a_kind_and_a_request_id(book):
    for args in (
        ("r", " ", "stocks"),
        ("r", "x" * 41, "stocks"),
        ("r", "A", "upstox"),
        ("r", "A", "nope"),
        ("", "A", "stocks"),
    ):
        with pytest.raises(Invalid):
            book.tab_create(OWNER, *args, now=NOW)
    assert len(book.tabs(OWNER)) == 1


def test_at_most_fifty_tabs_not_archived(book):
    made = [book.tab_create(OWNER, f"r{n}", f"T{n}", "other", now=NOW) for n in range(MAX_TABS)]
    with pytest.raises(Refused) as caught:
        book.tab_create(OWNER, "over", "One more", "other", now=NOW)
    assert str(caught.value) == "A portfolio can have up to 50 tabs. Archive one you no longer use."
    book.tab_update(OWNER, made[0]["id"], 1, {"archived": True}, now=NOW)
    again = book.tab_create(OWNER, "over", "One more", "other", now=NOW)
    with pytest.raises(Refused):  # bringing one back would make 51
        book.tab_update(OWNER, made[0]["id"], 2, {"archived": False}, now=NOW)
    assert again["name"] == "One more"
    assert (
        book.tab_create(OWNER, "r3", "T3", "other", now=NOW)["id"] == made[3]["id"]
    )  # a resend still answers


def test_a_tab_is_changed_only_from_its_version(book):
    tab = book.tab_create(OWNER, "r", "Old", "stocks", now=NOW)
    new = book.tab_update(
        OWNER, tab["id"], 1, {"name": "New", "pinned": True, "manual_old_days": 60}, now=NOW
    )
    assert (new["name"], new["pinned"], new["version"], new["manual_old_days"]) == (
        "New",
        True,
        2,
        60,
    )
    with pytest.raises(VersionConflict) as caught:
        book.tab_update(OWNER, tab["id"], 1, {"name": "Lost"}, now=NOW)
    assert str(caught.value) == WORDS["version_conflict"]
    for bad in ({"manual_old_days": 3}, {"icon": "skull"}, {"system": True}, {"name": ""}):
        with pytest.raises(Invalid):
            book.tab_update(OWNER, tab["id"], 2, bad, now=NOW)
    assert book.tabs(OWNER)[-1]["name"] == "New"


def test_the_upstox_tab_can_only_be_pinned_and_never_deleted(book):
    upstox = book.tabs(OWNER)[0]
    with pytest.raises(Refused):
        book.tab_update(OWNER, upstox["id"], 1, {"archived": True}, now=NOW)
    with pytest.raises(Refused):
        book.tab_update(OWNER, upstox["id"], 1, {"name": "Mine"}, now=NOW)
    assert book.tab_update(OWNER, upstox["id"], 1, {"pinned": False}, now=NOW)["pinned"] is False
    with pytest.raises(Refused) as caught:
        book.tab_delete(OWNER, upstox["id"], 2, "Upstox", now=NOW)
    assert str(caught.value) == BOOK_WORDS["delete_upstox"]


def test_only_an_archived_tab_is_deleted_and_only_by_typing_its_name(book, world):
    tab = world["tab"]
    add(book, world, "e-1", version=1)
    with pytest.raises(Refused) as caught:
        book.tab_delete(OWNER, tab["id"], 2, "Long term", now=NOW)
    assert str(caught.value) == BOOK_WORDS["delete_first_archive"]
    book.tab_update(OWNER, tab["id"], 2, {"archived": True}, now=NOW)
    with pytest.raises(Refused) as caught:
        book.tab_delete(OWNER, tab["id"], 3, "long term", now=NOW)
    assert str(caught.value) == BOOK_WORDS["delete_name"]
    assert book.tab_delete(OWNER, tab["id"], 3, "Long term", now=NOW) == {"id": tab["id"]}
    assert [t["name"] for t in book.tabs(OWNER)] == ["Upstox"]
    assert book.store.read(OWNER, lambda tx: tx.count("activity")) == 0
    with pytest.raises(NotFound):
        book.tab_delete(OWNER, tab["id"], 3, "Long term", now=NOW)


# ---- members, accounts, assets -------------------------------------------------------------
def test_members_and_accounts_are_changed_only_from_their_version(book, world):
    me = world["me"]
    renamed = book.member_update(
        OWNER, me["id"], 1, {"name": "Darvin", "relation": "self"}, now=NOW
    )
    assert (renamed["name"], renamed["version"]) == ("Darvin", 2)
    with pytest.raises(VersionConflict) as caught:
        book.member_update(OWNER, me["id"], 1, {"name": "x"}, now=NOW)
    assert str(caught.value) == WORDS["version_conflict_item"]
    account = world["account"]
    done = book.account_update(
        OWNER, account["id"], 1, {"name": "Zerodha main", "note": "kite"}, now=NOW
    )
    assert (done["name"], done["version"]) == ("Zerodha main", 2)
    with pytest.raises(VersionConflict):
        book.account_update(OWNER, account["id"], 1, {"name": "x"}, now=NOW)
    with pytest.raises(NotFound):
        book.member_update(OWNER, 999, 1, {"name": "x"}, now=NOW)


def test_the_upstox_account_is_never_edited_and_an_account_needs_a_member(book, world):
    system = next(a for a in book.accounts(OWNER) if a["system"])
    with pytest.raises(Refused) as caught:
        book.account_update(OWNER, system["id"], 1, {"name": "x"}, now=NOW)
    assert str(caught.value) == BOOK_WORDS["upstox_account_change"]
    with pytest.raises(Invalid):
        book.account_create(OWNER, "z", 999, "Orphan", "bank", now=NOW)
    with pytest.raises(Invalid):
        book.account_create(OWNER, "z", world["me"]["id"], "Fake Upstox", "upstox", now=NOW)


def test_a_listed_asset_keeps_every_isin_and_symbol_it_has_had(book, world):
    share = world["share"]
    assert (share["symbol"], share["exchange"], share["asset_class"]) == (
        "RELIANCE",
        "NSE",
        "equity",
    )
    assert {(a["kind"], a["value"]) for a in share["aliases"]} == {
        ("isin", ISIN),
        ("symbol", "RELIANCE"),
    }
    book.asset_alias_add(OWNER, share["id"], "isin", "ine002a01010", now=NOW)
    book.asset_alias_add(OWNER, share["id"], "symbol", "500325", now=NOW)
    for lookup in ({"isin": "INE002A01010"}, {"symbol": "500325"}, {"isin": ISIN}):
        assert book.asset_find(OWNER, **lookup)["id"] == share["id"]
    assert book.asset_find(OWNER, symbol="NOPE") is None
    assert book.asset_find(OWNER, amfi_code="120503")["id"] == world["fund"]["id"]
    other = book.asset_create(OWNER, "o", "manual", now=NOW, name="Gold coin")
    with pytest.raises(Refused):
        book.asset_alias_add(OWNER, other["id"], "isin", ISIN, now=NOW)
    with pytest.raises(Refused):  # the same company again, even by an old name
        book.asset_create(
            OWNER, "dup", "listed", now=NOW, exchange="BSE", symbol="500325", name="Reliance"
        )
    with pytest.raises(Refused):
        book.asset_create(OWNER, "dup2", "mf", now=NOW, amfi_code="120503", name="Again")


def test_assets_are_checked_and_manual_ones_may_start_with_a_value(book, world):
    for kwargs in (
        {"kind": "listed", "exchange": "NYSE", "symbol": "X"},
        {"kind": "listed", "exchange": "NSE"},
        {"kind": "listed", "exchange": "NSE", "symbol": "ABC", "isin": "short"},
        {"kind": "mf", "amfi_code": "abc", "name": "F"},
        {"kind": "manual"},
        {"kind": "manual", "name": "A", "tags": ["a,b"]},
        {"kind": "manual", "name": "A", "asset_class": "nope"},
        {"kind": "manual", "name": "A", "first_value": {"day": 20250230, "price_u": 5}},
    ):
        with pytest.raises(Invalid):
            book.asset_create(OWNER, "bad", now=NOW, **kwargs)
    gold = book.asset_create(
        OWNER, "g", "manual", now=NOW, name="Gold", asset_class="gold", unit_label="g",
        tags=["family", "family", "safe"], first_value={"day": 20250101, "price_u": 60_000_000},
    )  # fmt: skip
    assert gold["tags"] == ["family", "safe"] and gold["unit_label"] == "g"
    assert (
        book.store.read(
            OWNER, lambda tx: tx.conn.exec_driver_sql("SELECT price_u FROM price_point").scalar()
        )
        == 60_000_000
    )
    with pytest.raises(Refused):
        book.asset_create(OWNER, "g2", "manual", now=NOW, name="gold")


def test_an_asset_is_changed_only_from_its_version_and_only_a_manual_one_renamed(book, world):
    share = world["share"]
    done = book.asset_update(
        OWNER, share["id"], 1, {"sector": "Energy", "mcap": "large", "tags": ["core"]}, now=NOW
    )
    assert (done["sector"], done["mcap"], done["tags"], done["version"]) == (
        "Energy",
        "large",
        ["core"],
        2,
    )
    with pytest.raises(VersionConflict):
        book.asset_update(OWNER, share["id"], 1, {"sector": "x"}, now=NOW)
    with pytest.raises(Refused):
        book.asset_update(OWNER, share["id"], 2, {"name": "Renamed"}, now=NOW)
    gold = book.asset_create(OWNER, "g", "manual", now=NOW, name="Gold")
    assert (
        book.asset_update(OWNER, gold["id"], 1, {"name": "Gold bars"}, now=NOW)["name"]
        == "Gold bars"
    )


# ---- entries -------------------------------------------------------------------------------
def test_a_purchase_is_saved_with_its_effect_and_moves_the_tab_version(book, world):
    saved = add(book, world, "e-1", version=1, charges_paise=2000)
    assert saved["effect"]["words"] == "Cost ₹14,302.50 with ₹20.00 charges"
    assert (saved["effect"]["cost_paise"], saved["effect"]["gain_paise"]) == (1_430_250, None)
    assert saved["tab_version"] == 2 == tab_version(book, world)
    row = saved["activity"]
    assert (row["tab_id"], row["seq"], row["source"], row["status"]) == (
        world["tab"]["id"],
        1,
        "manual",
        "posted",
    )
    assert "request_id" not in row and "principal" not in row
    second = add(book, world, "e-2", trade_date=20250210, qty_u=5_000_000, amount_paise=755_000)
    assert (second["activity"]["seq"], second["tab_version"]) == (2, 3)


def test_a_sale_tells_its_lots_and_gain(book, world):
    add(book, world, "e-1", version=1)
    sale = add(
        book,
        world,
        "e-2",
        kind="SELL",
        trade_date=20250310,
        qty_u=4_000_000,
        amount_paise=600_000,
        charges_paise=1000,
    )
    assert sale["effect"]["words"].startswith("From 10 Jan 2025: 4 at ₹1,428.25 · gain")
    assert sale["effect"]["words"].startswith("From 10 Jan 2025: 4 at ₹1,428.25")
    assert sale["effect"]["gain_paise"] == 600_000 - 1000 - 571_300


def test_a_sale_of_more_than_is_held_is_refused_in_the_ledgers_words(book, world):
    add(book, world, "e-1", version=1)
    version = tab_version(book, world)
    with pytest.raises(Refused) as caught:
        add(book, world, "e-2", kind="SELL", trade_date=20250312, qty_u=12_000_000, amount_paise=1)
    assert str(caught.value) == (
        "Zerodha holds 10 Reliance Industries on 12 Mar 2025, so a sale of 12 cannot be saved. "
        "Add the missing purchase first."
    )
    assert caught.value.reason == "short"
    assert tab_version(book, world) == version and len(activities(book, world)) == 1


def test_deleting_a_purchase_a_later_sale_needs_is_refused_naming_the_sale(book, world):
    buy = add(book, world, "e-1", version=1)["activity"]
    add(book, world, "e-2", kind="SELL", trade_date=20250312, qty_u=4_000_000, amount_paise=1)
    version = tab_version(book, world)
    with pytest.raises(Refused) as caught:
        book.activity_delete(OWNER, buy["id"], version, now=NOW)
    assert str(caught.value) == (
        "Deleting this purchase would leave the sale on 12 Mar 2025 without shares. "
        "Delete or change that sale first."
    )
    assert len(activities(book, world)) == 2 and tab_version(book, world) == version
    sale = next(a for a in activities(book, world) if a["kind"] == "SELL")
    gone = book.activity_delete(OWNER, sale["id"], version, now=NOW)
    assert gone == {"id": sale["id"], "tab_version": version + 1}
    assert (
        book.activity_delete(OWNER, buy["id"], version + 1, now=NOW)["tab_version"] == version + 2
    )
    assert activities(book, world) == []


def test_changing_an_entry_replays_the_tab(book, world):
    buy = add(book, world, "e-1", version=1)["activity"]
    add(book, world, "e-2", kind="SELL", trade_date=20250312, qty_u=6_000_000, amount_paise=1)
    version = tab_version(book, world)
    with pytest.raises(Refused) as caught:  # 10 down to 5 would leave the sale of 6 short
        book.activity_update(OWNER, buy["id"], version, entry(world, qty_u=5_000_000), now=NOW)
    assert "Changing this purchase would leave the sale on 12 Mar 2025 without shares" in str(
        caught.value
    )
    done = book.activity_update(
        OWNER, buy["id"], version, entry(world, qty_u=8_000_000, amount_paise=100), now=NOW
    )
    assert (done["activity"]["qty_u"], done["activity"]["seq"], done["tab_version"]) == (
        8_000_000,
        1,
        version + 1,
    )
    own = book.activity_update(
        OWNER, buy["id"], version + 1, entry(world, trade_date=20250105), now=NOW
    )
    assert own["activity"]["trade_date"] == 20250105
    with pytest.raises(
        Refused
    ) as caught:  # the changed entry is the one that fails: the ledger's own sentence
        book.activity_update(
            OWNER,
            buy["id"],
            version + 2,
            entry(world, kind="SELL", qty_u=20_000_000, amount_paise=1),
            now=NOW,
        )
    assert str(caught.value).startswith("Zerodha holds no Reliance Industries on 10 Jan 2025")


def test_a_dry_run_says_the_effect_and_writes_nothing(book, world):
    version = tab_version(book, world)
    seen = book.activity_create(
        OWNER,
        world["tab"]["id"],
        version,
        entry(world, charges_paise=2000),
        "d-1",
        now=NOW,
        dry_run=True,
    )
    assert seen["activity"] is None and seen["tab_version"] == version
    assert seen["effect"]["words"] == "Cost ₹14,302.50 with ₹20.00 charges"
    assert activities(book, world) == [] and tab_version(book, world) == version
    with pytest.raises(Refused):  # the refusal is the same words
        book.activity_create(
            OWNER,
            world["tab"]["id"],
            version,
            entry(world, kind="SELL"),
            "d-2",
            now=NOW,
            dry_run=True,
        )
    real = add(book, world, "d-1", version=version)  # the dry run left its request id free
    assert real["activity"]["id"]
    again = book.activity_update(
        OWNER,
        real["activity"]["id"],
        real["tab_version"],
        entry(world, qty_u=1_000_000),
        now=NOW,
        dry_run=True,
    )
    assert again["activity"] is None and activities(book, world)[0]["qty_u"] == 10_000_000


def test_a_resent_create_returns_the_entry_already_saved_whatever_the_version_is_now(book, world):
    first = add(book, world, "same", version=1)
    add(book, world, "other", trade_date=20250111)
    again = book.activity_create(OWNER, world["tab"]["id"], 1, entry(world), "same", now=NOW + 99)
    assert again["activity"] == first["activity"] and len(activities(book, world)) == 2
    assert again["effect"]["words"].startswith("Cost ₹14,282.50")
    assert again["tab_version"] == tab_version(book, world)


def test_every_entry_write_refuses_a_stale_tab_version_and_writes_nothing(book, world):
    first = add(book, world, "e-1", version=1)["activity"]
    stale = 1
    before = (activities(book, world), tab_version(book, world))
    for call in (
        lambda: book.activity_create(
            OWNER, world["tab"]["id"], stale, entry(world), "e-9", now=NOW
        ),
        lambda: book.activity_create(
            OWNER, world["tab"]["id"], stale, entry(world), "e-9", now=NOW, dry_run=True
        ),
        lambda: book.activity_update(
            OWNER, first["id"], stale, entry(world, qty_u=1_000_000), now=NOW
        ),
        lambda: book.activity_delete(OWNER, first["id"], stale, now=NOW),
    ):
        with pytest.raises(VersionConflict) as caught:
            call()
        assert str(caught.value) == WORDS["version_conflict"]
    assert (activities(book, world), tab_version(book, world)) == before


def test_a_date_after_today_is_a_draft_only_and_a_draft_counts_nowhere(book, world):
    with pytest.raises(Invalid) as caught:
        add(book, world, "f-1", version=1, trade_date=20271231)
    assert str(caught.value) == "A date after today can be saved as a draft only."
    draft = add(book, world, "f-2", version=1, trade_date=20271231, status="draft")
    assert (
        draft["effect"]["words"] == BOOK_WORDS["draft"] and draft["activity"]["status"] == "draft"
    )
    add(book, world, "f-4", trade_date=20250110, status="draft")  # a past draft is allowed too
    with pytest.raises(Refused):  # neither draft bought anything
        add(book, world, "f-3", kind="SELL", qty_u=1_000_000, amount_paise=1)


def test_the_quantity_step_follows_the_asset(book, world):
    with pytest.raises(Invalid) as caught:
        add(book, world, "q-1", version=1, qty_u=1_500_000)
    assert str(caught.value) == "Shares are whole numbers."
    fund = world["fund"]["id"]
    ok = add(book, world, "q-2", version=1, asset_id=fund, qty_u=109_459_000, amount_paise=500_000)
    assert ok["activity"]["qty_u"] == 109_459_000
    with pytest.raises(Invalid):
        add(book, world, "q-3", asset_id=fund, qty_u=1_000_001)


def test_an_entry_is_refused_where_it_cannot_go(book, world):
    upstox_tab, upstox_account = (
        book.tabs(OWNER)[0],
        next(a for a in book.accounts(OWNER) if a["system"]),
    )
    with pytest.raises(Refused) as caught:
        book.activity_create(OWNER, upstox_tab["id"], 1, entry(world), "u-1", now=NOW)
    assert str(caught.value) == BOOK_WORDS["upstox_tab"]
    with pytest.raises(Refused) as caught:
        add(book, world, "u-2", version=1, account_id=upstox_account["id"])
    assert str(caught.value) == BOOK_WORDS["upstox_account"]
    for bad in (
        {"account_id": 999},
        {"asset_id": 999},
        {"qty_u": None},
        {"kind": "NOPE"},
        {"trade_date": 20250230},
    ):
        with pytest.raises((Invalid, Refused)):
            add(book, world, "u-3", version=1, **bad)
    other = book.tab_create(OWNER, "t-2", "Loans", "mixed", now=NOW)
    gone = book.asset_create(
        OWNER, "l", "liability", now=NOW, name="Home loan", tab_id=other["id"],
        version=other["version"], first_value={"day": 20261001, "price_u": 5_000_000_0000},
    )  # fmt: skip
    with pytest.raises(Invalid) as caught:
        add(book, world, "u-4", version=1, asset_id=gone["id"])
    assert str(caught.value) == BOOK_WORDS["liability"]
    with pytest.raises(Invalid):
        book.activity_create(
            OWNER, world["tab"]["id"], 1, {**entry(world), "colour": "red"}, "u-5", now=NOW
        )
    book.tab_update(OWNER, world["tab"]["id"], 1, {"archived": True}, now=NOW)
    with pytest.raises(Refused) as caught:
        add(book, world, "u-6", version=2)
    assert str(caught.value) == BOOK_WORDS["archived"]
    assert activities(book, world) == []


def test_a_charges_breakdown_must_add_up(book, world):
    ok = add(
        book, world, "c-1", version=1, charges_detail={"brokerage_paise": 1500, "stt_paise": 500}
    )
    assert ok["activity"]["charges_paise"] == 2000
    assert ok["activity"]["charges_detail"] == {"brokerage_paise": 1500, "stt_paise": 500}
    for bad in ({"brokerage_paise": 1}, {"mystery_paise": 5}):
        with pytest.raises(Invalid):
            add(book, world, "c-2", charges_paise=2000, charges_detail=bad)


def test_a_split_that_leaves_a_fraction_is_refused_and_a_good_one_saves(book, world):
    add(book, world, "s-0", version=1)
    split = {
        "kind": "SPLIT",
        "trade_date": 20250601,
        "account_id": None,
        "asset_id": world["share"]["id"],
        "ratio_num": 3,
        "ratio_den": 2,
    }
    version = tab_version(book, world)
    seen = book.activity_create(OWNER, world["tab"]["id"], version, split, "sp-1", now=NOW)
    assert seen["effect"]["words"].startswith("3 new for every 2 old")
    with pytest.raises(Refused) as caught:
        book.activity_create(
            OWNER,
            world["tab"]["id"],
            seen["tab_version"],
            {**split, "ratio_den": 4, "ratio_num": 1, "trade_date": 20250602},
            "sp-2",
            now=NOW,
        )
    assert "would leave" in str(caught.value) and "Zerodha" in str(caught.value)


def test_entries_page_newest_first(book, world):
    for n in range(5):
        add(book, world, f"p-{n}", trade_date=20250101 + n, qty_u=1_000_000, amount_paise=100)
    first = book.activities_list(OWNER, world["tab"]["id"], limit=2)
    assert [a["trade_date"] for a in first["items"]] == [20250105, 20250104] and first["next"]
    second = book.activities_list(OWNER, world["tab"]["id"], limit=2, before=first["next"])
    assert [a["trade_date"] for a in second["items"]] == [20250103, 20250102]
    last = book.activities_list(OWNER, world["tab"]["id"], limit=2, before=second["next"])
    assert [a["trade_date"] for a in last["items"]] == [20250101] and last["next"] is None
    for bad in ("x", "1:2"):
        with pytest.raises(Invalid):
            book.activities_list(OWNER, world["tab"]["id"], before=bad)
    assert book.activities_list(OWNER, world["tab"]["id"], asset_id=999)["items"] == []
    with pytest.raises(NotFound):
        book.activities_list(OWNER, 999)


def test_another_principals_entries_and_tabs_are_out_of_reach(tmp_path):
    both = PortfolioStore(tmp_path / "two" / DB_FILENAME, principals=("owner", "viewer"), now=NOW)
    book = Book(both)
    viewer = Scope("viewer")
    tab = book.tab_create(viewer, "v", "Theirs", "stocks", now=NOW)
    with pytest.raises(NotFound):
        book.tab_update(OWNER, tab["id"], 1, {"name": "taken"}, now=NOW)
    with pytest.raises(NotFound):
        book.activities_list(OWNER, tab["id"])
    with pytest.raises(NotFound):
        book.tab_delete(OWNER, tab["id"], 1, "Theirs", now=NOW)
    both.close()


# ---- moving a tab renumbers the others: positions are 0..n-1, never shared ----


def _order(book):
    return [
        (t["name"], t["position"]) for t in sorted(book.tabs(OWNER), key=lambda t: t["position"])
    ]


def _four(book):
    return [book.tab_create(OWNER, f"m{i}", f"T{i}", "stocks", now=NOW) for i in range(1, 4)]


def test_new_tabs_take_the_next_place_and_positions_are_unique(book):
    _four(book)
    assert _order(book) == [("Upstox", 0), ("T1", 1), ("T2", 2), ("T3", 3)]


def test_moving_a_tab_to_the_first_place_renumbers_the_others(book):
    t1, t2, t3 = _four(book)
    moved = book.tab_update(OWNER, t3["id"], 1, {"position": 1}, now=NOW)
    assert moved["position"] == 1 and moved["version"] == 2
    assert _order(book) == [("Upstox", 0), ("T3", 1), ("T1", 2), ("T2", 3)]


def test_moving_a_tab_to_the_last_place(book):
    t1, t2, t3 = _four(book)
    book.tab_update(OWNER, t1["id"], 1, {"position": 3}, now=NOW)
    assert _order(book) == [("Upstox", 0), ("T2", 1), ("T3", 2), ("T1", 3)]


def test_moving_a_tab_to_the_middle_and_past_the_ends(book):
    t1, t2, t3 = _four(book)
    book.tab_update(OWNER, t3["id"], 1, {"position": 2}, now=NOW)
    assert _order(book) == [("Upstox", 0), ("T1", 1), ("T3", 2), ("T2", 3)]
    book.tab_update(OWNER, t1["id"], 1, {"position": 99}, now=NOW)  # clamped, not a gap
    assert _order(book) == [("Upstox", 0), ("T3", 1), ("T2", 2), ("T1", 3)]
    book.tab_update(OWNER, t2["id"], 1, {"position": 0}, now=NOW)  # never ahead of Upstox
    assert _order(book)[0] == ("Upstox", 0)
    assert sorted(p for _, p in _order(book)) == [0, 1, 2, 3]


def test_only_the_moved_tab_gets_a_new_version(book):
    t1, t2, t3 = _four(book)
    book.tab_update(OWNER, t3["id"], 1, {"position": 1}, now=NOW)
    versions = {t["name"]: t["version"] for t in book.tabs(OWNER)}
    assert versions == {"Upstox": 1, "T3": 2, "T1": 1, "T2": 1}  # the others did not change


def test_a_tab_that_does_not_move_keeps_its_version(book):
    t1, t2, t3 = _four(book)
    book.tab_update(OWNER, t1["id"], 1, {"position": 1}, now=NOW)  # already there
    assert {t["name"]: t["version"] for t in book.tabs(OWNER)} == {
        "Upstox": 1, "T1": 2, "T2": 1, "T3": 1,
    }  # fmt: skip


def test_a_move_from_an_old_version_is_refused_and_changes_nothing(book):
    t1, t2, t3 = _four(book)
    book.tab_update(OWNER, t1["id"], 1, {"position": 3}, now=NOW)  # this screen moved it first
    before = _order(book)
    with pytest.raises(VersionConflict) as caught:
        book.tab_update(OWNER, t1["id"], 1, {"position": 1}, now=NOW)  # the other screen, stale
    assert str(caught.value) == WORDS["version_conflict"]
    assert _order(book) == before


def test_a_tab_pushed_aside_by_another_screen_can_still_be_moved_and_stays_unique(book):
    t1, t2, t3 = _four(book)
    book.tab_update(OWNER, t3["id"], 1, {"position": 1}, now=NOW)  # T1 and T2 moved down
    book.tab_update(OWNER, t2["id"], 1, {"position": 1}, now=NOW)  # the other screen, from its view
    assert sorted(p for _, p in _order(book)) == [0, 1, 2, 3]


def test_archived_tabs_stay_after_the_active_ones_in_their_own_order(book):
    t1, t2, t3 = _four(book)
    book.tab_update(OWNER, t1["id"], 1, {"archived": True}, now=NOW)
    assert _order(book) == [("Upstox", 0), ("T2", 1), ("T3", 2), ("T1", 3)]
    t4 = book.tab_create(OWNER, "m4", "T4", "stocks", now=NOW)  # active ones come first
    assert t4["position"] == 3 and t4["version"] == 1
    assert _order(book) == [("Upstox", 0), ("T2", 1), ("T3", 2), ("T4", 3), ("T1", 4)]
    book.tab_update(OWNER, t2["id"], 1, {"archived": True}, now=NOW)
    assert _order(book) == [("Upstox", 0), ("T3", 1), ("T4", 2), ("T1", 3), ("T2", 4)]
    now = {t["name"]: t for t in book.tabs(OWNER)}
    book.tab_update(OWNER, now["T1"]["id"], now["T1"]["version"], {"archived": False}, now=NOW)
    assert [name for name, _ in _order(book)] == ["Upstox", "T3", "T4", "T1", "T2"]
    assert sorted(p for _, p in _order(book)) == [0, 1, 2, 3, 4]


def test_a_tab_is_moved_within_its_own_group_only(book):
    t1, t2, t3 = _four(book)
    book.tab_update(OWNER, t3["id"], 1, {"archived": True}, now=NOW)
    book.tab_update(OWNER, t1["id"], 1, {"position": 99}, now=NOW)  # active: stays before archived
    assert _order(book) == [("Upstox", 0), ("T2", 1), ("T1", 2), ("T3", 3)]


def test_ties_and_gaps_from_older_data_are_closed_by_the_next_move(book):
    t1, t2, t3 = _four(book)
    book.store.write(
        OWNER,
        lambda tx: [
            tx.change("tab", t["id"], t["version"], {"position": 7}, NOW) for t in (t1, t2)
        ],
    )
    book.tab_update(OWNER, t3["id"], 1, {"position": 1}, now=NOW)
    assert sorted(p for _, p in _order(book)) == [0, 1, 2, 3]
