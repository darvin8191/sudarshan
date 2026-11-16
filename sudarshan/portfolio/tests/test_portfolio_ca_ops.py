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


def history(n, *trades, isin=UPSTOX, read_at=NOW) -> None:
    """Upstox's share trades of FY 2026-27, read in full: ``(day, "BUY" | "SELL", shares)`` of
    one share at Rs 100."""
    rows = [
        {"trade_date": day, "trade_id": str(50_000 + i), "exchange": "NSE", "segment": "EQ",
         "transaction_type": side, "scrip_name": "PROBE LTD", "symbol": "PROBE", "isin": isin,
         "option_type": None, "strike_price_u": None, "expiry": None, "qty_u": shares * U,
         "price_u": 1_000_000, "amount_paise": shares * 10_000}
        for i, (day, side, shares) in enumerate(trades)
    ]  # fmt: skip
    n.svc.store.write(
        OWNER,
        lambda tx: tx.report_put("trades", 2026, "EQ", "ok", read_at, None, {"rows": rows}, NOW),
    )


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
        "added_qty_u": 50 * U, "after_qty_u": 150 * U, "fraction": False, "left_qty_u": 0,
        "held_from": "record",
        "other": {
            "ratio_num": 2, "ratio_den": 1,
            "words": "Bonus 1:2: you held 100, you get 200 more, 300 in all. Is that right?",
            "added_qty_u": 200 * U, "after_qty_u": 300 * U, "fraction": False,
            "left_qty_u": 0,
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
async def test_the_upstox_account_is_asked_only_where_a_record_shows_it_held_the_share(n):
    """Review M3. Old code asked about every bonus and split since 1 Apr 2024 of any share of
    the account, with no quantity ("Split 1:10: every 1 share becomes 10. Is that right?")."""
    record(n, 20261019, **{UPSTOX: 100})  # holds UPSTOX; the record shows none of TRADED
    event(n, "bonus", ratio=(1, 1), isin=TRADED)
    assert await waiting(n) == []  # the record says it was not held: nothing to confirm
    # A split of UPSTOX in July 2024, which no closing record reaches: until the year's share
    # trades are read in full nothing can tell what was held then, so it is not asked yet,
    # only counted.
    event(n, "split", ex_day=20240705, ratio=(1, 10))
    data = (await n.rig.call("ca_list"))["data"]
    assert (data["to_confirm"], data["left_out_count"]) == ([], 1)
    # The reviewer's probe: 40 of TRADED bought on 5 Oct 2026; a split of July 2024, long
    # before. The lots of the trade history hold nothing then (0 bought before 5 Jul 2024), so
    # applying it would change no lot: it is not asked, and the lots now say the same of
    # UPSTOX's split, so nothing is left out either.
    history(n, (20261005, "BUY", 40), isin=TRADED)
    event(n, "split", ex_day=20240705, ratio=(1, 10), isin=TRADED)
    event(n, "bonus", ex_day=20250910, ratio=(1, 2), isin="INE000Z01019")  # not the account's
    event(n, "split", ex_day=20240328, ratio=(1, 2), isin=TRADED)  # before FY 2024-25
    data = (await n.rig.call("ca_list"))["data"]
    assert (data["to_confirm"], data["left_out_count"]) == ([], 0)
    assert ca.reach_from(TODAY) == 20240401


@scenario
async def test_a_share_held_through_a_split_before_the_first_record_is_asked_with_its_quantity(n):
    """Review M3, the other half: 60 bought on 10 Jun 2026 and 15 more on 1 Jul, 5 sold on
    8 Jul; a split with the ex-date 15 Jul 2026, three months before the first closing record
    (19 Oct). The lots hold 60 + 15 - 5 = 70 on 14 Jul, so the card says 70, never "no
    quantity", and a purchase on the ex-date itself (20 more) does not count."""
    history(n, (20260610, "BUY", 60), (20260701, "BUY", 15), (20260708, "SELL", 5),
            (20260715, "BUY", 20))  # fmt: skip
    record(n, 20261019, **{UPSTOX: 370})
    event(n, "split", ex_day=20260715, ratio=(1, 5))
    data = (await n.rig.call("ca_list"))["data"]
    (one,) = data["to_confirm"]
    (side,) = one["expected"]
    assert (side["held_qty_u"], side["held_from"]) == (70 * U, "trades")
    assert side["words"] == "Split 1:5: you held 70, they become 350. Is that right?"
    assert data["left_out_count"] == 0
    assert one["name"] == "PROBE LTD"  # review M2: a share held only at Upstox has its name


# ---- one event per company and ex-date (review H3) -----------------------------------------------
def relist(n, old_ratio=(1, 5), new_ratio=(10, 2), ex_day=EX) -> int:
    """Upstox writes the same split in other words: the old row is no longer listed."""
    row = {"isin": UPSTOX, "kind": "split", "ex_day": ex_day, "amount_u": None, "details": [],
           "ratio_num": new_ratio[0], "ratio_den": new_ratio[1]}  # fmt: skip
    row["raw_hash"] = ca.event_hash(row)

    def write(tx):
        made = tx.ca_event_put(row, NOW + 1)[0]["id"]
        tx.ca_events_gone(UPSTOX, [row["raw_hash"]], NOW + 1)
        return made

    return n.svc.store.write(OWNER, write)


@scenario
async def test_a_split_relisted_in_other_words_is_not_asked_and_not_applied_a_second_time(n):
    """The reviewer's probe. 100 shares; split 1:5 applied as 5 for 1: 100 x 5 = 500. Upstox
    then lists the same split of the same day as 10:2 (a face value of 10 made 2).

    Old code: "To confirm" showed it again, a second tap was accepted, ``accepted_actions``
    returned (5, 1) and (10, 2), and the lots held 100 x 5 x 5 = 2,500."""
    record(n, 20261019, **{UPSTOX: 100})
    first = event(n, "split", ratio=(1, 5))
    await n.rig.call("ca_decide", id=first, scope="upstox", state="applied", ratio_num=5,
                     ratio_den=1)  # fmt: skip
    assert lots_after(n)[0] == [(20260610, 500, 1_000_000)]
    second = relist(n)
    assert await waiting(n) == []  # no second ask
    assert [(a["num"], a["den"], a["accepted"]) for a in actions(n)] == [(5, 1, True)]
    # A second tap, forced past the list (an old page, a script), is refused in words.
    for ratio in ((10, 2), (2, 10)):
        again = await n.rig.call("ca_decide", id=second, scope="upstox", state="applied",
                                 ratio_num=ratio[0], ratio_den=ratio[1])  # fmt: skip
        assert (again["status"], again["code"]) == ("error", "refused"), again
        assert again["message"] == (
            "This split is already applied (every 1 made 5). Upstox lists it again in other "
            "words; applying it again would count it twice."
        )
    # Even a second decision row written straight into the store reaches the lots once.
    n.svc.store.write(
        OWNER,
        lambda tx: tx.insert("ca_decision", {"event_id": second, "scope": "upstox",
                                             "state": "applied", "ratio_num": 10, "ratio_den": 2,
                                             "activity_id": None}, NOW),
    )  # fmt: skip
    assert [(a["num"], a["den"]) for a in actions(n)] == [(5, 1)]
    assert lots_after(n)[0] == [(20260610, 500, 1_000_000)]  # 100 x 5, not 100 x 5 x 5


@scenario
async def test_a_dismissed_split_relisted_is_not_asked_again_and_a_corrected_date_follows(n):
    record(n, 20261019, **{UPSTOX: 100})
    first = event(n, "split", ratio=(1, 5))
    await n.rig.call("ca_decide", id=first, scope="upstox", state="dismissed", ratio_num=1,
                     ratio_den=1)  # fmt: skip
    relist(n, new_ratio=(2, 10))  # 2:10 is 1:5
    assert await waiting(n) == [] and actions(n) == []
    # An applied split whose ex-date Upstox corrects by two days (same ratio) is still that
    # split: applied once, dated as Upstox lists it now.
    other = event(n, "bonus", ex_day=20261012, ratio=(1, 1))
    record(n, 20261009, **{UPSTOX: 100})
    await n.rig.call("ca_decide", id=other, scope="upstox", state="applied", ratio_num=1,
                     ratio_den=1)  # fmt: skip
    row = {"isin": UPSTOX, "kind": "bonus", "ex_day": 20261014, "amount_u": None, "details": [],
           "ratio_num": 1, "ratio_den": 1}  # fmt: skip
    row["raw_hash"] = ca.event_hash(row)
    n.svc.store.write(OWNER, lambda tx: (tx.ca_event_put(row, NOW + 1),
                                         tx.ca_events_gone(UPSTOX, [row["raw_hash"]], NOW + 2)))  # fmt: skip
    assert [(a["kind"], a["ex_day"], a["accepted"]) for a in actions(n)] == [
        ("BONUS", 20261014, True)
    ]
    assert await waiting(n) == []


def test_two_ratios_say_the_same_thing_as_fractions_or_the_other_way_round():
    """1:5 against: 2:10 (1 x 10 = 5 x 2), 10:2 the other way round (1 x 10 = 5 x 2 crosswise:
    1 x 10 == 5 x 2), 5:1; never 1:10 (1 x 10 != 5 x 1 and 1 x 1 != 5 x 10) or no ratio."""
    same = ca.core.same_ratio
    assert same((1, 5), (2, 10)) and same((1, 5), (10, 2)) and same((1, 5), (5, 1))
    assert not same((1, 5), (1, 10)) and not same((1, 5), (None, None))
    a = {"isin": UPSTOX, "kind": "split", "ex_day": 20261020, "ratio_num": 1, "ratio_den": 5}
    assert ca.core.twins(a, {**a, "ratio_num": 1, "ratio_den": 10})  # one day: one event
    assert ca.core.twins(a, {**a, "ex_day": 20261027, "ratio_num": 10, "ratio_den": 2})  # 7 days
    assert not ca.core.twins(a, {**a, "ex_day": 20261028})  # 8 days: another event
    assert not ca.core.twins(a, {**a, "ex_day": 20261022, "ratio_den": 10})  # another ratio
    assert not ca.core.twins(a, {**a, "kind": "bonus"}) and not ca.core.twins(
        a, {**a, "isin": TRADED}
    )


# ---- the lots against the holdings (design p7 4.3, 6.3; review H3) -------------------------------
#: A split of Friday 9 Oct 2026, a week and more before the records compared (its new shares reach
#: the demat account days after the ex-date, so the check leaves the week after it alone).
SPLIT_DAY = 20261009


async def check_of(n):
    answer = await n.rig.call("ca_list")
    assert answer["status"] == "success", answer
    return answer["data"]["lot_check"]


@scenario
async def test_the_lots_are_checked_against_the_holdings_and_a_double_apply_is_caught(n):
    """100 bought on 10 Jun 2026. Split 1:5 on 9 Oct: Upstox holds 100 on the 8th and 500 on
    the 21st. The trades were read on 26 Oct (NOW), so the record compared is the 21st's, the
    last one two days or more before. Applied as 5 for 1 the lots say 100 x 5 = 500: agreed.

    Then the same split is applied a second time in a way no matching can see (Upstox lists it
    again dated 28 Sep, eleven days off, and the owner taps yes): the lots say 100 x 5 x 5 =
    2,500 against 500 held, and the check says so, naming both figures, with the change that
    makes them agree: leaving out the one confirmed last. Old code had no such check at all."""
    history(n, (20260610, "BUY", 100))
    record(n, 20261008, **{UPSTOX: 100})
    record(n, 20261021, **{UPSTOX: 500})
    split = event(n, "split", ex_day=SPLIT_DAY, ratio=(1, 5))
    before = await check_of(n)
    # Not applied yet: 100 in the lots against 500 held, and the sentence names the split that
    # waits, the likeliest reason, never a purchase to add.
    assert (before["state"], before["day"]) == ("ok", 20261021)
    (row,) = before["rows"]
    assert (row["held_qty_u"], row["lots_qty_u"], row["fix"]) == (500 * U, 100 * U, None)
    assert row["words"] == ("PROBE LTD: Upstox holds 500 and the lots say 100. The split of "
                            "9 Oct 2026 waits for you under To confirm.")  # fmt: skip
    await n.rig.call("ca_decide", id=split, scope="upstox", state="applied", ratio_num=5,
                     ratio_den=1)  # fmt: skip
    after = await check_of(n)
    assert (after["state"], after["rows"], after["words"]) == ("ok", [], None)
    # The forced second apply.
    twice = event(n, "split", ex_day=20260928, ratio=(1, 5))
    done = await n.rig.call("ca_decide", id=twice, scope="upstox", state="applied",
                            ratio_num=5, ratio_den=1)  # fmt: skip
    assert done["status"] == "success"
    check = await check_of(n)
    (row,) = check["rows"]
    assert (row["isin"], row["name"], row["held_qty_u"], row["lots_qty_u"]) == (
        UPSTOX, "PROBE LTD", 500 * U, 2500 * U)  # fmt: skip
    assert row["words"] == (
        "PROBE LTD: after the split of 28 Sep 2026 the lots say 2,500 and Upstox holds 500. "
        "Without it the lots would say 500: it may have been counted twice."
    )
    assert check["words"] == (
        "Lots and holdings differ for 1 share: PROBE LTD 500 held, 2,500 in lots. Compared at "
        "the close of 21 Oct 2026."
    )
    version = n.read(lambda tx: tx.rows("ca_decision", event_id=twice))[0]["version"]
    assert row["fix"] == {"event_id": twice, "version": version, "state": "dismissed",
                          "ratio_num": None, "ratio_den": None}  # fmt: skip
    assert answer_misses({"lot_check": check}) == set()
    # The fix, sent as the phone sends it, is the decision changed from its version.
    fixed = await n.rig.call("ca_decide", id=twice, scope="upstox", **row["fix"])
    assert fixed["status"] == "success", fixed
    assert (await check_of(n))["rows"] == []
    assert lots_after(n)[0] == [(20260610, 500, 1_000_000)]


@scenario
async def test_a_wrong_way_split_is_caught_with_its_fix_and_like_is_compared_with_like(n):
    """The wrong reading of 1:5 (every 5 made 1): 100 / 5 = 20 in the lots, 500 at Upstox, and
    the other reading, 100 x 5 = 500, makes them agree: the check offers it."""
    history(n, (20260610, "BUY", 100), (20261023, "BUY", 7), read_at=NOW + 2 * 86_400_000)
    record(n, 20261008, **{UPSTOX: 100})
    record(n, 20261016, **{UPSTOX: 500})
    record(n, 20261023, **{UPSTOX: 507})
    record(n, 20261026, **{UPSTOX: 507})
    split = event(n, "split", ex_day=SPLIT_DAY, ratio=(1, 5))
    await n.rig.call("ca_decide", id=split, scope="upstox", state="applied", ratio_num=1,
                     ratio_den=5)  # fmt: skip
    check = await check_of(n)
    # Trades read on 28 Oct: the record compared is the 26th's, with the trades up to it:
    # 100 / 5 + 7 = 27 against 507.
    assert check["day"] == 20261026
    (row,) = check["rows"]
    assert (row["held_qty_u"], row["lots_qty_u"]) == (507 * U, 27 * U)
    assert row["words"] == (
        "PROBE LTD: after the split of 9 Oct 2026 the lots say 27 and Upstox holds 507. Read "
        "the other way round, the lots would say 507: it was likely confirmed the wrong way "
        "round."
    )
    assert (row["fix"]["state"], row["fix"]["ratio_num"], row["fix"]["ratio_den"]) == (
        "applied", 5, 1)  # fmt: skip
    # Trades read on the 25th: the record of the 23rd is the last one two days before, but the
    # share was traded that day (its record may not show that day's trade yet), so it is not
    # compared tonight: no row, never a difference made of the 23rd's own purchase.
    history(n, (20260610, "BUY", 100), (20261023, "BUY", 7), read_at=NOW - 86_400_000)
    check = await check_of(n)
    assert (check["day"], check["rows"]) == (20261023, [])
    # Trades read on the 22nd know nothing of the 23rd: the record of the 16th is compared, with
    # the trades up to the 16th (20 against 500), never the 23rd's holdings.
    history(n, (20260610, "BUY", 100), read_at=NOW - 4 * 86_400_000)
    check = await check_of(n)
    assert check["day"] == 20261016
    assert [(r["held_qty_u"], r["lots_qty_u"]) for r in check["rows"]] == [(500 * U, 20 * U)]
    # Turned round from the row, the lots and the holdings agree.
    row = check["rows"][0]
    assert (await n.rig.call("ca_decide", id=split, scope="upstox", **row["fix"]))["status"] == (
        "success")  # fmt: skip
    assert (await check_of(n))["rows"] == []


@scenario
async def test_the_week_after_a_split_is_left_alone(n):
    """Split 1:5 on 20 Oct, applied. Upstox credits the new shares days after the ex-date, so
    the record of the 21st may still say 100: the check does not compare the share in the week
    after the split (that night it compares the others), and compares it after."""
    history(n, (20260610, "BUY", 100), (20260610, "BUY", 50))
    record(n, 20261019, **{UPSTOX: 150})
    record(n, 20261021, **{UPSTOX: 150})  # the new shares not credited yet
    split = event(n, "split", ratio=(1, 5))
    await n.rig.call("ca_decide", id=split, scope="upstox", state="applied", ratio_num=5,
                     ratio_den=1)  # fmt: skip
    check = await check_of(n)
    assert (check["day"], check["rows"]) == (20261021, [])
    # A week on, with the record still at 150, the difference is said.
    history(n, (20260610, "BUY", 100), (20260610, "BUY", 50), read_at=NOW + 4 * 86_400_000)
    record(n, 20261028, **{UPSTOX: 150})
    (row,) = (await check_of(n))["rows"]
    assert (row["held_qty_u"], row["lots_qty_u"]) == (150 * U, 750 * U)


@scenario
async def test_the_check_waits_for_the_trades_and_counts_shares_with_no_purchase_record(n):
    record(n, 20261023, **{UPSTOX: 100, TRADED: 30})
    check = await check_of(n)
    assert (check["state"], check["rows"]) == ("waiting", [])
    assert "waits until Upstox's share trades of this year are read in full" in check["words"]
    # Read on the 23rd itself: no record two days before it, so it waits for one.
    history(n, (20260610, "BUY", 60), read_at=NOW - 3 * 86_400_000)
    check = await check_of(n)
    assert (check["state"], check["rows"]) == ("waiting", [])
    assert check["words"].startswith("The check of the tax lots against your Upstox holdings "
                                     "waits for a closing record")  # fmt: skip
    # UPSTOX: 60 bought in the records, 100 held. A bonus or a split Upstox has not listed is
    # the likelier cause, so it is named first and sent to the Calendar, never to a purchase
    # (v0.13.0 verification H1); a purchase only before the trades read begin, and those begin
    # on 1 Apr 2026, so the two earlier years Upstox keeps are read first. Old code: "If you
    # bought the rest before 1 Apr 2024 ... add that purchase", a day whose years were never
    # read. TRADED: held, never bought in the records, no bonus or split decided: not a
    # mismatch, counted in one figure.
    history(n, (20260610, "BUY", 60))
    check = await check_of(n)
    (row,) = check["rows"]
    assert (row["isin"], row["held_qty_u"], row["lots_qty_u"]) == (UPSTOX, 100 * U, 60 * U)
    assert row["words"] == (
        "PROBE LTD: Upstox holds 100 and the lots say 60. If the company gave a bonus or split "
        "its shares since you bought, confirm it in the Calendar; do not add a purchase for it. "
        "SUDARSHAN has read Upstox's trades from 1 Apr 2026 only: read FY 2024-25 and "
        "FY 2025-26 on the Upstox tab's Trades view first. Then add a purchase under Tax, "
        "Inputs only for shares bought before the first day read, or moved in from another "
        "account."
    )
    assert (row["fix"], check["unrecorded_count"]) == (None, 1)


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
    assert set(data) == {"events", "to_confirm", "reads", "left_out_count", "read_status",
                         "lot_check"}  # fmt: skip
    assert set(data["events"][0]) == {"id", "isin", "name", "kind", "ex_day", "amount_u",
                                      "ratio_num", "ratio_den", "details", "read_at", "gone_at",
                                      "decisions"}  # fmt: skip
    assert data["reads"] == [{"isin": UPSTOX, "read_at": NOW, "state": "ok", "note": None}]
    assert answer_misses(data) == set()
    outside = await n.rig.call("ca_list", **{"from": 20270101, "to": 20270131})
    assert outside["data"]["events"] == [] and len(outside["data"]["to_confirm"]) == 1
    for bad in ({"from": 20261301}, {"from": 20261101, "to": 20261001}, {"scope": 5}):
        assert (await n.rig.call("ca_list", **bad))["code"] == "bad_request"
