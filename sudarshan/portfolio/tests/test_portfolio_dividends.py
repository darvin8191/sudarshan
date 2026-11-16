"""Expected dividends and the owner's confirmation (M7.13, M7.29; dividends.py): one line per
account that held the share the day before the ex-date, the tax core's arithmetic and likely-TDS
flag per payer, "credited" only on the owner's word, a tab's own entry never doubled. Through the
sealed API on a temporary store; no network."""

from __future__ import annotations

import httpx
import pytest
from test_portfolio_ca_ops import event, partly_read, record, scenario
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
    assert set(line) == {*dividends.LINE_KEYS, "name", "record_day"}
    # Each quantity is from the record of the trading day before its ex-date (Thursday 13 Aug,
    # Friday 18 Sep before Monday 21 Sep), so neither line names a record (review L4).
    assert [ln["record_day"] for ln in payer["lines"]] == [None, None]
    assert line["member_id"] == data["member_id"] and line["fy"] == 2026
    assert answer_misses(data) == set()
    assert n.svc.store.write(OWNER, lambda tx: dividends.sync(tx, TODAY, NOW)) == 0  # settled


@scenario
async def test_a_payer_held_only_at_upstox_has_its_name(n):
    """Review M2: names came from the custom tabs' assets only, so a share held only at Upstox
    read as its ISIN on the Dividends card. The Upstox holding's own name now names it."""
    d1(n)
    assert (await year(n))["dividends"]["payers"][0]["name"] is None  # nothing names it yet
    n.reader._held_now = lambda: [{"isin": UPSTOX, "name": "Probe Industries Limited"}]
    (payer,) = (await year(n))["dividends"]["payers"]
    assert payer["name"] == "Probe Industries Limited"
    assert {line["name"] for line in payer["lines"]} == {"Probe Industries Limited"}


# ---- a dividend is never silently dropped (review H1) --------------------------------------------
@scenario
async def test_a_dividend_from_before_the_first_closing_record_is_listed_as_not_counted(n):
    """The reviewer's July probe. Closing records on 13 and 19 Oct 2026; dividends with the
    ex-dates 15 Jul (Rs 20 a share), 16 Oct and 20 Oct (Rs 5.50 each).

    Counted: 100 x 5.50 = 550.00 (the record of the 13th) and 300 x 5.50 = 1,650.00 (the
    19th): 2,200.00. The July dividend has no closing record within 7 days before it, and the
    share was held at the first record after it (100 on 13 Oct, nothing bought since July), so
    it is listed as not counted, with its sentence, and is in no total.
    Old code: no line and no sentence anywhere; the year read Rs 2,200.00 as if complete."""
    record(n, 20261013, **{UPSTOX: 100})
    event(n, "dividend", ex_day=20261016, amount_u=55_000)
    record(n, 20261019, **{UPSTOX: 300})
    event(n, "dividend", ex_day=20261020, amount_u=55_000)
    july = event(n, "dividend", ex_day=20260715, amount_u=200_000)
    data = await year(n)
    totals = data["dividends"]
    (payer,) = totals["payers"]
    # Review L4: the 16 Oct line's 100 is from the record of Tuesday 13 Oct, not of Thursday
    # 15 Oct, the trading day before its ex-date (none was taken): the line says which record.
    assert [(ln["ex_day"], ln["expected_paise"], ln["record_day"]) for ln in payer["lines"]] == [
        (20261016, 550 * RUPEE, 20261013), (20261020, 1650 * RUPEE, None)]  # fmt: skip
    assert (totals["total_paise"], totals["unconfirmed_count"]) == (2200 * RUPEE, 2)
    (left,) = totals["uncounted"]
    assert (left["event_id"], left["isin"], left["ex_day"], left["qty_u"]) == (
        july, UPSTOX, 20260715, 0)  # fmt: skip
    assert left["words"] == (
        "No holdings record reaches the day before this ex-date, so the amount is not known: "
        "confirm what reached your bank on the Dividends card"
    )
    assert totals["uncounted_count"] == 1
    assert answer_misses(data) == set()
    # The working paper's block says how many it could not count, and lists them.
    block = n.read(lambda tx: dividends.for_pack(tx, 2026, data["member_id"], today=TODAY))
    assert (block["total_paise"], block["uncounted_count"]) == (2200 * RUPEE, 1)
    assert block["uncounted"] == [
        {"isin": UPSTOX, "name": None, "ex_day": 20260715, "words": left["words"]}
    ]
    # The owner confirms what reached his bank, as for any other line: Rs 1,800 credited and
    # Rs 200 deducted count as 2,000, and the line is a line of its payer from then on.
    done = await n.rig.call("dividend_set", id=left["id"], version=left["version"],
                            state="confirmed", credited_paise=1800 * RUPEE,
                            tds_paise=200 * RUPEE, credited_day=20260801)  # fmt: skip
    assert done["status"] == "success"
    totals = (await year(n))["dividends"]
    assert (totals["uncounted"], totals["total_paise"]) == ([], (2200 + 2000) * RUPEE)
    assert n.svc.store.write(OWNER, lambda tx: dividends.sync(tx, TODAY, NOW)) == 0


@scenario
async def test_a_dividend_of_a_share_bought_after_it_is_not_listed_at_all(n):
    """Not counted is for what may have been held. 50 held on 13 Oct, all of them bought on
    1 Sep 2026 (the trade history says so): 50 - 50 = 0 on the eve of 15 Jul, so the July
    dividend was never his, and is not listed. A share with no record after the ex-date and
    none before is listed: nothing says it was not held."""
    from test_portfolio_ca_ops import history

    history(n, (20260901, "BUY", 50))
    record(n, 20261013, **{UPSTOX: 50})
    event(n, "dividend", ex_day=20260715, amount_u=200_000)
    assert (await year(n))["dividends"]["uncounted"] == []
    # 30 of the 50 were sold again on 5 Oct and 30 bought on 1 Aug besides: held 13 Oct = 50,
    # bought since the ex-date 80, sold since 30: 50 - (80 - 30) = 0, still not held in July.
    history(n, (20260801, "BUY", 30), (20260901, "BUY", 50), (20261005, "SELL", 30))
    assert (await year(n))["dividends"]["uncounted"] == []
    # 20 of what he holds were bought before July: 50 - (30 - 0) = 20 held on the eve.
    history(n, (20260610, "BUY", 20), (20260801, "BUY", 30))
    (left,) = (await year(n))["dividends"]["uncounted"]
    assert (left["ex_day"], left["name"]) == (20260715, "PROBE LTD")  # named (review M2)


@scenario
async def test_a_dividend_with_no_amount_is_listed_not_dropped(n):
    record(n, 20261019, **{UPSTOX: 300})
    event(n, "dividend", ex_day=20261020)  # Upstox gives no rupees a share
    totals = (await year(n))["dividends"]
    (left,) = totals["uncounted"]
    assert (left["qty_u"], left["expected_paise"]) == (300 * U, 0)
    assert left["words"].startswith("Upstox lists no amount for this dividend")
    assert totals["payers"] == [] and totals["total_paise"] == 0


@scenario
async def test_a_closing_record_whose_holdings_were_not_read_never_drops_a_dividend(n):
    """v0.14.0 verification M2, its probe. On Monday 19 Oct, the eve of the ex-date, the closing
    record read the positions but not the holdings. Old code read it as holding nothing: no
    line, and nothing under "Not counted" either (payers 0, uncounted 0), the dividend gone
    without a word. It is no holdings record: Friday's says 100, and the line names it."""
    record(n, 20261016, **{UPSTOX: 100})
    partly_read(n, 20261019)
    event(n, "dividend", ex_day=20261020, amount_u=55_000)
    totals = (await year(n))["dividends"]
    (payer,) = totals["payers"]
    assert [(ln["qty_u"], ln["expected_paise"], ln["record_day"]) for ln in payer["lines"]] == [
        (100 * U, 550 * RUPEE, 20261016)]  # fmt: skip
    assert (totals["uncounted"], totals["total_paise"]) == ([], 550 * RUPEE)


# ---- before the first read, and a year too old (review M1, L5) -----------------------------------
@scenario
async def test_the_year_says_when_the_first_read_is_and_refuses_a_year_before_the_rules(n):
    data = await year(n)
    assert data["read_status"] == {"state": "never", "night": None, "words": ca.FIRST_READ}
    # FY 2019-20 is before the first threshold row (1 Apr 2020). Old code: "The portfolio
    # service could not do this just now." (a LookupError).
    old = await n.rig.call("dividends", fy=2019)
    assert (old["status"], old["code"]) == ("error", "bad_request")
    assert old["message"] == (
        "Dividends are worked out from the financial year 2020-21 on. Choose a later year."
    )
    assert (await n.rig.call("dividends", fy=2020))["status"] == "success"


@scenario
async def test_the_papers_block_says_why_it_counts_no_dividend(n):
    """v0.13.0 review H1: the working paper's block of a year nothing was read for, before the
    first read, after a failed night, and with the insights switch off (v0.13.0 itself)."""
    data = await year(n)
    block = n.read(lambda tx: dividends.for_pack(tx, 2026, data["member_id"], today=TODAY))
    assert block["note"] == (
        "This paper counts no dividends yet: SUDARSHAN reads Upstox's corporate actions for the "
        "first time tonight after 23:45, and the dividends of what you held follow."
    )
    # v0.13.0, insights switched off: nothing is ever read, and the paper says where to look.
    off = n.read(lambda tx: dividends.for_pack(tx, 2026, data["member_id"], today=TODAY,
                                               insights=False))  # fmt: skip
    assert off["note"] == (
        "This paper counts no dividends yet: SUDARSHAN reads them from Upstox's corporate "
        "actions only once the dividends view is switched on in a later release. Take the "
        "year's dividends from your bank statements or Form 26AS."
    )
    # After a failed night, that night's sentence, after a word on what it means here.
    n.svc.store.write(OWNER, lambda tx: tx.job_put("corporate_actions", TODAY - 1, "failed",
                                                   f"0 of 1 read. {ca.WORDS['refused']}", NOW))  # fmt: skip
    failed = n.read(lambda tx: dividends.for_pack(tx, 2026, data["member_id"], today=TODAY))
    assert failed["note"] == (
        "Dividends may be missing here. Upstox's list could not be read last night. Upstox did "
        "not allow SUDARSHAN to read its list of events with the read-only token. SUDARSHAN "
        "tries again tonight after 23:45."
    )
    assert n.read(lambda tx: dividends.for_pack(tx, 2019, 1))["total_paise"] == 0


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
