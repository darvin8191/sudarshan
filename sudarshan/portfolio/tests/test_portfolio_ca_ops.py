"""A bonus or a split reaches the lots only through the owner's one tap (M7.27, M7.36; design p7
6.3, owner decision 10): ``ca_list`` proposes a reading on his own quantity with the other way
round beside it, ``ca_decide`` records the one he took, and ``accepted_actions`` hands the tax
core only what he applied. Through the sealed API on a temporary store; no network."""

from __future__ import annotations

import asyncio
import dataclasses

import httpx
import pytest
from test_portfolio_closed_world import answer_misses
from test_portfolio_corpactions_reader import HELD, TRADED, UPSTOX, Night
from test_portfolio_import_ops import NOW, OWNER
from test_portfolio_purposes import FAM, JOURNAL, JRN

from sudarshan.autologin.handoff import seal
from sudarshan.portfolio import corpactions as ca
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.core import kinds
from sudarshan.tax import lots as tax_lots
from sudarshan.tax.model import Action, Trade, build

U = 1_000_000
TODAY = 20261026
EX = 20261020  # a Tuesday; the closing record of the day before is the 19th


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store_module.stop()


def scenario(test):
    def wrapped(tmp_path):
        async def go():
            n = Night(tmp_path)
            n.svc.settings = dataclasses.replace(n.svc.settings, insights=True)
            n.svc.api.purposes += [("family", FAM), ("journal", JRN)]
            await n.svc.api.start(0)
            try:
                await test(n)
            finally:
                await n.svc.api.close()
                await n.client.aclose()
                n.svc.store.close()

        asyncio.run(go())

    wrapped.__name__ = test.__name__
    return wrapped


def event(n, kind, ex_day=EX, ratio=None, isin=UPSTOX, amount_u=None) -> int:
    row = {"isin": isin, "kind": kind, "ex_day": ex_day, "amount_u": amount_u, "details": [],
           "ratio_num": ratio[0] if ratio else None, "ratio_den": ratio[1] if ratio else None}  # fmt: skip
    row["raw_hash"] = ca.event_hash(row)
    return n.svc.store.write(OWNER, lambda tx: tx.ca_event_put(row, NOW))[0]["id"]


def record(n, day, **held) -> None:
    """The Upstox account's closing record of a day: ``isin=shares``."""
    lines = [
        {"instrument_key": f"NSE_EQ|{isin}", "qty": shares * U} for isin, shares in held.items()
    ]
    n.svc.store.write(OWNER, lambda tx: tx.upstox_eod_put(day, {"read_at": NOW}, lines, NOW))


def actions(n):
    return n.read(ca.accepted_actions)


def lots_after(n, bought: int = 100):
    """The tax core's lots of the Upstox share: ``bought`` shares bought in June 2026, then
    whatever ``accepted_actions`` hands it."""
    buy = Trade(1, 1, 20260610, 1, "BUY", "EQ", UPSTOX, bought * U, 1_000_000, bought * 10_000)
    book = tax_lots.replay([buy], actions=[build(Action, row) for row in actions(n)])
    return [
        (lot.acquired_day, lot.qty_u // U, lot.cost_paise) for lot in book.open_lots
    ], book.notes


async def waiting(n, **args):
    answer = await n.rig.call("ca_list", **args)
    assert answer["status"] == "success", answer
    return answer["data"]["to_confirm"]


# ---- the switch, and who may ask ---------------------------------------------------------------
def test_the_ops_wait_for_the_insights_switch(tmp_path):
    async def go():
        n = Night(tmp_path)
        await n.svc.api.start(0)
        try:
            for op in ("ca_list", "ca_decide"):
                assert (await n.rig.call(op, id=1))["code"] == "not_ready"
        finally:
            await n.svc.api.close()
            await n.client.aclose()
            n.svc.store.close()

    asyncio.run(go())


@scenario
async def test_no_family_member_and_no_other_service_is_answered(n):
    assert not {"ca_list", "ca_decide"} & {
        *kinds.FAMILY_OPS,
        *kinds.JOURNAL_OPS,
        *kinds.SHARING_OPS,
    }
    async with httpx.AsyncClient(trust_env=False) as client:
        for fernet, who in ((FAM, "viewer:7"), (JRN, JOURNAL)):
            for op in ("ca_list", "ca_decide"):
                answer = await client.post(
                    f"http://127.0.0.1:{n.svc.api.port}/v1/{op}",
                    content=seal(fernet, op, principal=who, args={"id": 1}), timeout=20,
                )  # fmt: skip
                assert answer.status_code == 403, (who, op)


# ---- proposed, then confirmed ------------------------------------------------------------------
@scenario
async def test_a_bonus_is_proposed_on_the_owners_quantity_and_changes_no_lot_until_his_tap(n):
    """Mutation target 18: a proposed action that changes lots fails here."""
    record(n, 20261019, **{UPSTOX: 100})
    bonus = event(n, "bonus", ratio=(1, 2))
    (one,) = await waiting(n)
    assert (one["id"], one["kind"], one["ratio_num"], one["ratio_den"]) == (bonus, "bonus", 1, 2)
    assert one["decisions"] == []
    (side,) = one["expected"]
    assert side == {
        "scope": "upstox", "held_qty_u": 100 * U, "ratio_num": 1, "ratio_den": 2,
        "words": "Bonus 1:2: you held 100, you get 50 more, 150 in all. Is that right?",
        "added_qty_u": 50 * U, "after_qty_u": 150 * U, "fraction": False,
        "other": {
            "ratio_num": 2, "ratio_den": 1,
            "words": "Bonus 1:2: you held 100, you get 200 more, 300 in all. Is that right?",
            "added_qty_u": 200 * U, "after_qty_u": 300 * U, "fraction": False,
        },
    }  # fmt: skip
    # Not decided: the tax core is handed the proposed reading as not accepted, and adds nothing.
    assert actions(n) == [{"kind": "BONUS", "instrument": UPSTOX, "ex_day": EX, "num": 1, "den": 2,
                           "new_isin": None, "accepted": False}]  # fmt: skip
    held, notes = lots_after(n)
    assert held == [(20260610, 100, 1_000_000)] and len(notes) == 1

    # The owner takes it the other way round.
    done = await n.rig.call("ca_decide", id=bonus, scope="upstox", state="applied",
                            ratio_num=2, ratio_den=1, request_id="r-1")  # fmt: skip
    assert done["data"] == {"id": 1, "event_id": bonus, "scope": "upstox", "state": "applied",
                            "ratio_num": 2, "ratio_den": 1, "activity_id": None, "version": 1}  # fmt: skip
    assert actions(n)[0] == {"kind": "BONUS", "instrument": UPSTOX, "ex_day": EX, "num": 2,
                             "den": 1, "new_isin": None, "accepted": True}  # fmt: skip
    # The bonus lot costs nothing and is dated the ex-date (owner decision 6).
    assert lots_after(n) == ([(20260610, 100, 1_000_000), (EX, 200, 0)], ())
    assert await waiting(n) == []
    shown = (await n.rig.call("ca_list"))["data"]["events"]
    assert [d["state"] for d in shown[0]["decisions"]] == ["applied"]
    again = await n.rig.call("ca_decide", id=bonus, scope="upstox", state="applied",
                             ratio_num=2, ratio_den=1, request_id="r-1")  # fmt: skip
    assert again["code"] == "version_conflict"  # a decision is changed from its version only


@scenario
async def test_a_split_is_proposed_as_one_into_many_and_a_decision_can_be_taken_back(n):
    record(n, 20261019, **{UPSTOX: 100})
    split = event(n, "split", ratio=(1, 5))
    (side,) = (await waiting(n))[0]["expected"]
    assert (side["ratio_num"], side["ratio_den"], side["after_qty_u"]) == (5, 1, 500 * U)
    assert side["words"] == "Split 1:5: you held 100, they become 500. Is that right?"
    assert side["other"]["words"] == "Split 1:5: you held 100, they become 20. Is that right?"
    row = (await n.rig.call("ca_decide", id=split, scope="upstox", state="applied",
                            ratio_num=5, ratio_den=1))["data"]  # fmt: skip
    assert lots_after(n) == ([(20260610, 500, 1_000_000)], ())  # cost and day stay
    back = await n.rig.call("ca_decide", id=split, scope="upstox", state="dismissed",
                            version=row["version"])  # fmt: skip
    assert (back["data"]["state"], back["data"]["ratio_num"], back["data"]["version"]) == (
        "dismissed", None, 2)  # fmt: skip
    assert actions(n) == [] and lots_after(n) == ([(20260610, 100, 1_000_000)], ())
    assert await waiting(n) == []  # dismissed is decided: it is not asked again


@scenario
async def test_only_one_of_the_two_readings_can_be_applied(n):
    record(n, 20261019, **{UPSTOX: 100})
    bonus = event(n, "bonus", ratio=(1, 2))
    for num, den in ((3, 4), (1, 0), (None, 2), (True, 2), (1.0, 2)):
        answer = await n.rig.call("ca_decide", id=bonus, scope="upstox", state="applied",
                                  ratio_num=num, ratio_den=den)  # fmt: skip
        assert answer["code"] == "bad_request", (num, den)
    assert (await n.rig.call("ca_decide", id=bonus, scope="upstox", state="applied",
                             ratio_num=3, ratio_den=4))["message"] == ca.SAY["ratio"]  # fmt: skip
    assert n.read(lambda tx: tx.rows("ca_decision")) == []
    for bad in ({"scope": "tab:999"}, {"scope": "everything"}, {"scope": None}, {"state": "maybe"}):
        args = {"scope": "upstox", "state": "applied", "ratio_num": 1, "ratio_den": 2, **bad}
        assert (await n.rig.call("ca_decide", id=bonus, **args))["code"] == "bad_request", bad
    assert (await n.rig.call("ca_decide", id=999, scope="upstox", state="dismissed"))["code"] == (
        "not_found")  # fmt: skip


@scenario
async def test_an_event_with_no_ratio_takes_the_ratio_the_owner_types(n):
    record(n, 20261019, **{UPSTOX: 100})
    bonus = event(n, "bonus")
    (side,) = (await waiting(n))[0]["expected"]
    assert (side["ratio_num"], side["words"], side["other"]) == (None, ca.SAY["no_ratio"], None)
    assert actions(n) == []  # nothing to hand the tax core until he says
    done = await n.rig.call("ca_decide", id=bonus, scope="upstox", state="applied",
                            ratio_num=3, ratio_den=1)  # fmt: skip
    assert done["status"] == "success" and actions(n)[0]["num"] == 3


@scenario
async def test_a_dividend_or_an_event_to_come_is_never_confirmed(n):
    record(n, 20261019, **{UPSTOX: 100})
    dividend = event(n, "dividend", amount_u=55000)
    later = event(n, "split", ex_day=20261102, ratio=(1, 5))
    assert await waiting(n) == []
    answer = await n.rig.call("ca_decide", id=dividend, scope="upstox", state="applied",
                              ratio_num=1, ratio_den=1)  # fmt: skip
    assert (answer["code"], answer["message"]) == ("bad_request", ca.SAY["kind"])
    early = await n.rig.call("ca_decide", id=later, scope="upstox", state="applied",
                             ratio_num=5, ratio_den=1)  # fmt: skip
    assert early["code"] == "refused"
    assert early["message"] == ("This split has not happened yet. Confirm it on or after "
                                "2 Nov 2026.")  # fmt: skip
    assert [a["accepted"] for a in actions(n)] == [False]


# ---- which holdings an event waits for ---------------------------------------------------------
@scenario
async def test_the_upstox_account_is_asked_only_where_it_held_or_may_have_held_the_share(n):
    record(n, 20261019, **{UPSTOX: 100})  # holds UPSTOX; the record shows none of TRADED
    not_held = event(n, "bonus", ratio=(1, 1), isin=TRADED)
    assert await waiting(n) == []  # the record says it was not held: nothing to confirm
    # An ex-date no closing record reaches: asked only for a share of the account, inside the
    # years its reports reach, and then without an example.
    rows = [{"isin": TRADED, "segment": "EQ"}]
    n.svc.store.write(
        OWNER, lambda tx: tx.report_put("trades", 2025, "EQ", "ok", NOW, None, {"rows": rows}, NOW)
    )
    old = event(n, "bonus", ex_day=20250910, ratio=(1, 2), isin=TRADED)
    event(n, "bonus", ex_day=20250910, ratio=(1, 2), isin="INE000Z01019")  # not the account's
    event(n, "split", ex_day=20240328, ratio=(1, 2), isin=TRADED)  # before FY 2024-25
    found = await waiting(n)
    assert [(e["id"], len(e["expected"])) for e in found] == [(old, 1)]
    side = found[0]["expected"][0]
    assert (side["held_qty_u"], side["words"]) == (
        None, "Bonus 1:2: 1 new share for every 2 held. Is that right?")  # fmt: skip
    assert "after_qty_u" not in side and not_held != old
    assert ca.reach_from(TODAY) == 20240401


@scenario
async def test_an_event_upstox_no_longer_lists_is_not_asked_but_an_applied_one_stays(n):
    record(n, 20261019, **{UPSTOX: 100})
    kept = event(n, "split", ratio=(1, 5))
    asked = event(n, "bonus", ex_day=20261021, ratio=(1, 1))
    await n.rig.call(
        "ca_decide", id=kept, scope="upstox", state="applied", ratio_num=5, ratio_den=1
    )
    n.svc.store.write(OWNER, lambda tx: tx.ca_events_gone(UPSTOX, [], NOW))
    assert await waiting(n) == []
    assert [(a["kind"], a["accepted"]) for a in actions(n)] == [("SPLIT", True)]
    events = (await n.rig.call("ca_list"))["data"]["events"]
    assert {e["id"]: e["gone_at"] for e in events} == {kept: NOW, asked: NOW}


# ---- a custom tab -------------------------------------------------------------------------------
@scenario
async def test_a_tab_is_offered_the_entry_and_the_decision_never_writes_the_ledger(n):
    n.hold()  # 10 shares of the rig's share bought 1 Oct 2026 in the tab
    split = event(n, "split", ratio=(1, 5), isin=HELD)
    tab, scope = n.rig.tab, f"tab:{n.rig.tab['id']}"
    (side,) = (await waiting(n))[0]["expected"]
    assert (side["scope"], side["tab_id"], side["name"], side["held_qty_u"]) == (
        scope, tab["id"], "Long term", 10 * U)  # fmt: skip
    assert side["words"] == "Split 1:5: you held 10, they become 50. Is that right?"
    assert side["entries"] == [{"kind": "SPLIT", "trade_date": EX, "asset_id": n.rig.share["id"],
                                "ratio_num": 5, "ratio_den": 1}]  # fmt: skip
    assert (await waiting(n, scope="upstox")) == []
    before = n.rig.book.activities_list(OWNER, tab["id"])["items"]
    early = await n.rig.call("ca_decide", id=split, scope=scope, state="applied",
                             ratio_num=5, ratio_den=1)  # fmt: skip
    assert (early["code"], early["message"]) == (
        "refused", "Add the split to Long term first, then confirm it here.")  # fmt: skip
    assert n.rig.book.activities_list(OWNER, tab["id"])["items"] == before
    # The owner adds the offered entry through the entry form (its own op, unchanged code).
    made = n.rig.book.activity_create(OWNER, tab["id"], n.rig.version(), side["entries"][0],
                                      "e-2", now=NOW)  # fmt: skip
    assert await waiting(n) == []  # the tab has its entry: nothing left to confirm
    done = await n.rig.call("ca_decide", id=split, scope=scope, state="applied",
                            ratio_num=5, ratio_den=1)  # fmt: skip
    assert done["data"]["activity_id"] == made["activity"]["id"]
    assert len(n.rig.book.activities_list(OWNER, tab["id"])["items"]) == len(before) + 1
    # The tab's decision is the tab's: for the Upstox account the event is still undecided.
    assert [a["accepted"] for a in actions(n)] == [False]


@scenario
async def test_a_bonus_is_offered_to_each_account_of_a_tab_that_held_the_share(n):
    n.hold()
    bought_on_ex = {"kind": "BUY", "trade_date": EX, "account_id": n.rig.account["id"],
                    "asset_id": n.rig.share["id"], "qty_u": 90 * U, "price_u": 10_000_000}  # fmt: skip
    n.rig.book.activity_create(
        OWNER, n.rig.tab["id"], n.rig.version(), bought_on_ex, "e-3", now=NOW
    )
    event(n, "bonus", ratio=(3, 2), isin=HELD)
    (side,) = (await waiting(n))[0]["expected"]
    assert side["held_qty_u"] == 10 * U  # the 90 bought on the ex-date earn no bonus
    assert side["entries"] == [{"kind": "BONUS", "trade_date": EX, "asset_id": n.rig.share["id"],
                                "account_id": n.rig.account["id"], "qty_u": 15 * U}]  # fmt: skip


# ---- the answer's shape -------------------------------------------------------------------------
@scenario
async def test_the_list_answers_the_contracts_shape_and_no_key_is_unaccounted_for(n):
    record(n, 20261019, **{UPSTOX: 100})
    event(n, "bonus", ratio=(1, 2))
    n.svc.store.write(OWNER, lambda tx: tx.ca_read_put(UPSTOX, NOW, "ok", None))
    data = (await n.rig.call("ca_list"))["data"]
    assert set(data) == {"events", "to_confirm", "reads"}
    assert set(data["events"][0]) == {"id", "isin", "name", "kind", "ex_day", "amount_u",
                                      "ratio_num", "ratio_den", "details", "read_at", "gone_at",
                                      "decisions"}  # fmt: skip
    assert data["reads"] == [{"isin": UPSTOX, "read_at": NOW, "state": "ok", "note": None}]
    assert answer_misses(data) == set()
    outside = await n.rig.call("ca_list", **{"from": 20270101, "to": 20270131})
    assert outside["data"]["events"] == [] and len(outside["data"]["to_confirm"]) == 1
    for bad in ({"from": 20261301}, {"from": 20261101, "to": 20261001}, {"scope": 5}):
        assert (await n.rig.call("ca_list", **bad))["code"] == "bad_request"
