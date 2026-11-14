"""Expected dividends and the owner's confirmation (M7.13, M7.29; dividends.py): one line per
account that held the share the day before the ex-date, the tax core's arithmetic and likely-TDS
flag per payer, "credited" only on the owner's word, a tab's own entry never doubled. Through the
sealed API on a temporary store; no network."""

from __future__ import annotations

import httpx
import pytest
from test_portfolio_ca_ops import event, record, scenario
from test_portfolio_closed_world import answer_misses
from test_portfolio_corpactions_reader import HELD, NIGHT, TRADED, UPSTOX
from test_portfolio_import_ops import NOW, OWNER
from test_portfolio_purposes import FAM, JOURNAL, JRN

from sudarshan.autologin.handoff import seal
from sudarshan.portfolio import corpactions as ca
from sudarshan.portfolio import dividends
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.core import kinds

U = 1_000_000
TODAY = 20261026
RUPEE = 100


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store_module.stop()


async def year(n, **args):
    answer = await n.rig.call("dividends", **args)
    assert answer["status"] == "success", answer
    return answer["data"]


def d1(n) -> tuple[int, int]:
    """Design D1: 300 shares held on 13 Aug 2026; Rs 5.50 a share with the ex-date 14 Aug, and
    later in the year Rs 30 a share from the same company."""
    record(n, 20260813, **{UPSTOX: 300})
    record(n, 20260918, **{UPSTOX: 300})
    return (event(n, "dividend", 20260814, amount_u=55000),
            event(n, "dividend", 20260921, amount_u=300000))  # fmt: skip


def buy(n, day: int, shares: int, key: str) -> None:
    entry = {"kind": "BUY", "trade_date": day, "account_id": n.rig.account["id"],
             "asset_id": n.rig.share["id"], "qty_u": shares * U, "price_u": 10_000_000}  # fmt: skip
    n.rig.book.activity_create(OWNER, n.rig.tab["id"], n.rig.version(), entry, key, now=NOW)


# ---- expected ------------------------------------------------------------------------------------
@scenario
async def test_d1_the_expected_amounts_and_the_likely_tds_of_one_payer(n):
    first, second = d1(n)
    data = await year(n)
    assert (data["fy"], data["as_of"]) == (2026, NOW)
    (payer,) = data["dividends"]["payers"]
    assert [(line["event_id"], line["qty_u"], line["expected_paise"], line["state"])
            for line in payer["lines"]] == [(first, 300 * U, 1650 * RUPEE, "expected"),
                                            (second, 300 * U, 9000 * RUPEE, "expected")]  # fmt: skip
    # 1,650 + 9,000 = 10,650 from one payer, above 10,000: TDS likely, about 1,065.
    assert (payer["expected_paise"], payer["tds_likely"], payer["tds_expected_paise"]) == (
        10650 * RUPEE, True, 1065 * RUPEE)  # fmt: skip
    totals = data["dividends"]
    assert (totals["total_paise"], totals["tds_expected_paise"], totals["unconfirmed_count"]) == (
        10650 * RUPEE, 1065 * RUPEE, 2)  # fmt: skip
    line = payer["lines"][0]
    assert set(line) == {*dividends.LINE_KEYS, "name"}
    assert line["member_id"] == data["member_id"] and line["fy"] == 2026
    assert answer_misses(data) == set()
    assert n.svc.store.write(OWNER, lambda tx: dividends.sync(tx, TODAY, NOW)) == 0  # settled


@scenario
async def test_the_tds_flag_is_per_payer_never_on_the_years_total(n):
    """Mutation target 22: the threshold applied to the year's total fails here."""
    record(n, 20260813, **{UPSTOX: 200, TRADED: 200})
    event(n, "dividend", 20260814, amount_u=300000)  # Rs 30 x 200 = 6,000
    event(n, "dividend", 20260814, amount_u=300000, isin=TRADED)  # another payer, 6,000
    totals = (await year(n))["dividends"]
    assert totals["total_paise"] == 12000 * RUPEE and totals["tds_expected_paise"] == 0
    assert [p["tds_likely"] for p in totals["payers"]] == [False, False]


@scenario
async def test_d2_a_purchase_on_the_ex_date_expects_nothing(n):
    """Mutation target 22: a dividend expected for a purchase on the ex-date fails here."""
    buy(n, 20260814, 300, "e-1")
    event(n, "dividend", 20260814, amount_u=55000, isin=HELD)
    assert (await year(n))["dividends"]["payers"] == []
    buy(n, 20260917, 100, "e-2")  # bought before the next ex-date: both lots are held by then
    event(n, "dividend", 20260921, amount_u=300000, isin=HELD)
    (payer,) = (await year(n))["dividends"]["payers"]
    assert [(line["qty_u"], line["expected_paise"], line["account_id"])
            for line in payer["lines"]] == [(400 * U, 12000 * RUPEE, n.rig.account["id"])]  # fmt: skip
    assert payer["name"] == "Reliance Industries"


@scenario
async def test_no_amount_is_guessed_where_no_record_says_what_was_held(n):
    record(n, 20260601, **{UPSTOX: 300})  # ten weeks before the ex-date: says nothing of it
    event(n, "dividend", 20260814, amount_u=55000)
    record(n, 20260918, **{TRADED: 5})  # this record shows none of UPSTOX on its day
    event(n, "dividend", 20260921, amount_u=300000)
    event(n, "dividend", 20260921, isin=TRADED)  # held, but Upstox gave no amount
    event(n, "dividend", 20261102, amount_u=10000, isin=TRADED)  # to come: no line yet
    assert (await year(n))["dividends"]["payers"] == []


@scenario
async def test_a_late_closing_record_moves_an_expected_line_and_a_withdrawn_event_removes_it(n):
    first, second = d1(n)
    await year(n)
    record(n, 20260813, **{UPSTOX: 250})  # the record of the day before is corrected
    (payer,) = (await year(n))["dividends"]["payers"]
    assert [line["expected_paise"] for line in payer["lines"]] == [1375 * RUPEE, 9000 * RUPEE]
    keep = payer["lines"][1]
    done = await n.rig.call("dividend_set", id=keep["id"], state="confirmed",
                            credited_paise=9000 * RUPEE, version=keep["version"])  # fmt: skip
    assert done["status"] == "success"
    record(n, 20260918, **{UPSTOX: 250})  # a record corrected after he confirmed changes nothing
    (payer,) = (await year(n))["dividends"]["payers"]
    assert [(line["qty_u"], line["expected_paise"]) for line in payer["lines"]][1] == (
        300 * U, 9000 * RUPEE)  # fmt: skip
    n.svc.store.write(OWNER, lambda tx: tx.ca_events_gone(UPSTOX, [], NOW))  # Upstox drops both
    (payer,) = (await year(n))["dividends"]["payers"]
    assert [(line["event_id"], line["state"]) for line in payer["lines"]] == [(second, "confirmed")]
    assert first != second


# ---- credited is the owner's word ----------------------------------------------------------------
@scenario
async def test_the_owner_confirms_a_line_and_the_figures_follow(n):
    d1(n)
    (payer,) = (await year(n))["dividends"]["payers"]
    small, big = payer["lines"]
    done = await n.rig.call("dividend_set", id=big["id"], state="confirmed", version=1,
                            credited_paise=8640 * RUPEE, tds_paise=960 * RUPEE,
                            credited_day=20260930)  # fmt: skip
    assert done["data"] == {**{k: big[k] for k in dividends.LINE_KEYS}, "state": "confirmed",
                            "credited_paise": 8640 * RUPEE, "tds_paise": 960 * RUPEE,
                            "credited_day": 20260930, "version": 2}  # fmt: skip
    gone = await n.rig.call("dividend_set", id=small["id"], state="not_received", version=1)
    assert gone["data"]["state"] == "not_received"
    (payer,) = (await year(n))["dividends"]["payers"]
    # The dividend is the credit and the tax deducted: 8,640 + 960 = 9,600; the other came to
    # nothing. 9,600 is not above 10,000.
    assert (payer["confirmed_paise"], payer["total_paise"], payer["tds_likely"]) == (
        9600 * RUPEE, 9600 * RUPEE, False)  # fmt: skip
    assert payer["expected_paise"] == 10650 * RUPEE  # what was expected stays on show
    back = await n.rig.call("dividend_set", id=big["id"], state="expected", version=2)
    assert (back["data"]["credited_paise"], back["data"]["tds_paise"]) == (None, None)
    stale = await n.rig.call("dividend_set", id=big["id"], state="confirmed", version=2,
                             credited_paise=1)  # fmt: skip
    assert stale["code"] == "version_conflict"


@scenario
async def test_a_confirmation_that_is_not_one_is_refused_in_words(n):
    d1(n)
    line = (await year(n))["dividends"]["payers"][0]["lines"][0]
    for bad, words in (
        ({"state": "paid"}, "state"),
        ({"state": "confirmed"}, "amount"),
        ({"state": "confirmed", "credited_paise": -1}, "amount"),
        ({"state": "confirmed", "credited_paise": 1.5}, "amount"),
        ({"state": "confirmed", "credited_paise": 100, "tds_paise": -5}, "tds"),
        ({"state": "confirmed", "credited_paise": 100, "credited_day": 20261027}, "day"),
        ({"state": "confirmed", "credited_paise": 100, "credited_day": 20261332}, "day"),
    ):
        answer = await n.rig.call("dividend_set", id=line["id"], version=1, **bad)
        assert (answer["code"], answer["message"]) == ("bad_request", dividends.SAY[words]), bad
    assert (await n.rig.call("dividend_set", id=999, state="expected", version=1))["code"] == (
        "not_found")  # fmt: skip
    assert (await n.rig.call("dividends", member=999))["code"] == "not_found"
    assert (await n.rig.call("dividends", fy=2027))["code"] == "bad_request"


@scenario
async def test_a_dividend_counts_in_the_year_it_was_credited_in(n):
    record(n, 20260327, **{UPSTOX: 100})
    event(n, "dividend", 20260330, amount_u=100000)  # ex-date in FY 2025-26
    line = (await year(n, fy=2025))["dividends"]["payers"][0]["lines"][0]
    await n.rig.call("dividend_set", id=line["id"], state="confirmed", version=1,
                     credited_paise=1000 * RUPEE, credited_day=20260403)  # fmt: skip
    assert (await year(n, fy=2025))["dividends"]["payers"] == []
    assert (await year(n, fy=2026))["dividends"]["total_paise"] == 1000 * RUPEE
    await n.rig.call("dividend_set", id=line["id"], state="expected", version=2)
    assert (await year(n, fy=2025))["dividends"]["unconfirmed_count"] == 1


# ---- a tab's own entry ---------------------------------------------------------------------------
@scenario
async def test_a_tabs_own_dividend_entry_is_linked_and_never_doubled(n):
    buy(n, 20260601, 300, "e-1")
    event(n, "dividend", 20260814, amount_u=55000, isin=HELD)
    entry = {"kind": "DIVIDEND", "trade_date": 20260902, "account_id": n.rig.account["id"],
             "asset_id": n.rig.share["id"], "amount_paise": 1650 * RUPEE, "tds_paise": 165 * RUPEE}  # fmt: skip
    made = n.rig.book.activity_create(OWNER, n.rig.tab["id"], n.rig.version(), entry, "e-2",
                                      now=NOW)  # fmt: skip
    (payer,) = (await year(n))["dividends"]["payers"]
    (line,) = payer["lines"]
    assert (line["state"], line["activity_id"], line["credited_day"]) == (
        "confirmed", made["activity"]["id"], 20260902)  # fmt: skip
    assert (line["credited_paise"], line["tds_paise"]) == (1485 * RUPEE, 165 * RUPEE)
    assert payer["total_paise"] == 1650 * RUPEE  # the entry's gross, once
    answer = await n.rig.call("dividend_set", id=line["id"], state="not_received",
                              version=line["version"])  # fmt: skip
    assert (answer["code"], answer["entry_id"]) == ("refused", made["activity"]["id"])


@scenario
async def test_one_entry_is_one_dividend_and_goes_to_the_line_whose_amount_it_matches(n):
    buy(n, 20260601, 300, "e-1")
    small = event(n, "dividend", 20260814, amount_u=55000, isin=HELD)  # Rs 1,650 expected
    big = event(n, "dividend", 20260921, amount_u=300000, isin=HELD)  # Rs 9,000 expected
    entry = {"kind": "DIVIDEND", "trade_date": 20260925, "account_id": n.rig.account["id"],
             "asset_id": n.rig.share["id"], "amount_paise": 9000 * RUPEE}  # fmt: skip
    n.rig.book.activity_create(OWNER, n.rig.tab["id"], n.rig.version(), entry, "e-2", now=NOW)
    (payer,) = (await year(n))["dividends"]["payers"]
    # The entry is within 60 days of both ex-dates; it is the Rs 9,000 one, and only that one.
    assert {line["event_id"]: line["state"] for line in payer["lines"]} == {
        small: "expected", big: "confirmed"}  # fmt: skip
    assert payer["total_paise"] == (1650 + 9000) * RUPEE
    other = {**entry, "trade_date": 20260820, "amount_paise": 1600 * RUPEE}  # not the same paise
    n.rig.book.activity_create(OWNER, n.rig.tab["id"], n.rig.version(), other, "e-3", now=NOW)
    (payer,) = (await year(n))["dividends"]["payers"]
    assert [line["state"] for line in payer["lines"]] == ["confirmed", "confirmed"]
    assert payer["total_paise"] == (1600 + 9000) * RUPEE  # by date, taking the entry's figure


# ---- into the working paper ----------------------------------------------------------------------
@scenario
async def test_the_working_papers_block_counts_the_accounts_that_count_for_tax(n):
    d1(n)
    buy(n, 20260601, 100, "e-1")
    event(n, "dividend", 20260814, amount_u=55000, isin=HELD)  # Rs 550 in the tab's account
    data = await year(n)
    who = data["member_id"]
    block = n.read(lambda tx: dividends.for_pack(tx, 2026, who, buyback_paise=6_000_000))
    assert block["total_paise"] == (10650 + 550) * RUPEE + 6_000_000
    assert (block["buyback_paise"], block["unconfirmed_count"]) == (6_000_000, 3)
    assert block["tds_expected_paise"] == 1065 * RUPEE
    assert [set(p) for p in block["by_payer"]] == [
        {"isin", "name", "expected_paise", "confirmed_paise", "total_paise", "tds_likely"}
    ] * 2
    account = n.rig.account
    n.svc.store.write(OWNER, lambda tx: tx.change(
        "account", account["id"], account["version"], {"tax_mode": "none"}, NOW))  # fmt: skip
    left = n.read(lambda tx: dividends.for_pack(tx, 2026, who))
    assert [p["isin"] for p in left["by_payer"]] == [UPSTOX] and left["total_paise"] == 1065000


# ---- the night, the switch and who may ask -------------------------------------------------------
@scenario
async def test_the_nightly_read_makes_the_lines_of_what_was_held(n):
    record(n, 20260813, **{UPSTOX: 300})
    record(n, 20261026, **{UPSTOX: 300})
    row = {"name": "Dividend", "expiry_date": "14 Aug 2026", "amount": 5.5, "ratio": None}
    n.upstox.answers[UPSTOX] = [row]
    n.rig.now = NIGHT
    assert await ca.corporate_actions_step(n.svc, NIGHT) == "ok"
    job = n.read(lambda tx: tx.job_get("corporate_actions", TODAY))
    assert job["detail"].endswith("; 1 dividend lines made or changed")
    (line,) = n.read(lambda tx: tx.rows("dividend_line"))
    assert (line["expected_paise"], line["state"], line["fy"]) == (1650 * RUPEE, "expected", 2026)


@scenario
async def test_no_family_member_and_no_other_service_sees_a_dividend(n):
    d1(n)
    assert not {"dividends", "dividend_set", "calendar"} & {
        *kinds.FAMILY_OPS, *kinds.JOURNAL_OPS, *kinds.SHARING_OPS}  # fmt: skip
    async with httpx.AsyncClient(trust_env=False) as client:
        for fernet, who in ((FAM, "viewer:7"), (JRN, JOURNAL)):
            for op in ("dividends", "dividend_set"):
                answer = await client.post(
                    f"http://127.0.0.1:{n.svc.api.port}/v1/{op}",
                    content=seal(fernet, op, principal=who, args={"id": 1}), timeout=20,
                )  # fmt: skip
                assert answer.status_code == 403, (who, op)
    n.svc.settings = __import__("dataclasses").replace(n.svc.settings, insights=False)
    for op in ("dividends", "dividend_set"):
        assert (await n.rig.call(op, id=1))["code"] == "not_ready"
    assert n.read(lambda tx: tx.rows("dividend_line")) == []  # switched off: nothing was made
