"""The tax working paper's ops (phase P7, M7.27, P3; design p7 8, 9.1).

``tax_get`` answers the core's pack for a year and taxpayer, with each earlier year's carried
losses brought in (and the owner's own figure winning); ``tax_inputs`` and ``tax_input_set`` keep
the owner's figures, each checked and versioned, a dry run saying what a save would change and
writing nothing. The ops are called as the API calls them, on a real store. Synthetic data only.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import pytest
from test_portfolio_tax_adapter import ISIN, NOW, U, answered, cache

from sudarshan.portfolio import config, lotcheck, reports
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.core import kinds
from sudarshan.portfolio.errors import ApiError
from sudarshan.portfolio.ops import REGISTRY
from sudarshan.portfolio.store import DB_FILENAME, PortfolioStore, Scope
from sudarshan.tax import model, rules

OWNER = Scope.owner()
LOSS_ISIN = "INE000L01011"
NOT_THERE = "That family member is not in your portfolio. Reload the page."


@pytest.fixture(autouse=True)
def _stop():
    yield
    store_module.stop()


class Ctx:
    """What an op is given: the settings (tax on), the store and the clock."""

    def __init__(self, store):
        self.settings = dataclasses.replace(config.Settings(enabled=True), tax=True)
        self.store, self.reports = store, None
        self.upstox_id = store.read(OWNER, lambda tx: tx.rows("account", system=1)[0]["id"])

    def clock(self) -> int:
        return NOW


@pytest.fixture
def ctx(tmp_path: Path):
    made = PortfolioStore(tmp_path / "s" / DB_FILENAME, now=NOW)
    cache(made)
    yield Ctx(made)
    made.close()


def call(ctx, name: str, **message):
    return asyncio.run(REGISTRY[name](ctx, message))


def refused(ctx, name: str, **message) -> ApiError:
    with pytest.raises(ApiError) as caught:
        call(ctx, name, **message)
    return caught.value


def loss_last_year(ctx) -> None:
    """FY 2025-26 read in full: 10 shares bought at 1,000 and sold at 800, a short-term loss."""
    rows = [
        reports.parse_trade(answered({
            "exchange": "NSE", "segment": "EQ", "option_type": None, "quantity": 10,
            "amount": 10 * price, "trade_id": trade_id, "trade_date": day,
            "transaction_type": side, "scrip_name": "LOSS LTD", "strike_price": None,
            "expiry": "", "price": price, "isin": LOSS_ISIN, "symbol": "LOSS",
            "instrument_token": None,
        }))
        for trade_id, day, side, price in (("40001", "2025-06-02", "BUY", 1000.0),
                                           ("40002", "2025-09-01", "SELL", 800.0))
    ]  # fmt: skip
    ctx.store.write(OWNER, lambda tx: tx.report_put("trades", 2025, "EQ", "ok", NOW, None,
                                                    {"rows": rows}, NOW))  # fmt: skip


def lot(ctx, **more) -> dict:
    return {"member_id": 1, "account_id": ctx.upstox_id, "isin": ISIN, "acquired_day": 20160310,
            "qty_u": 10 * U, "cost_paise": 1_500_000, **more}  # fmt: skip


def brought(capital: dict) -> list[dict]:
    return [step for step in capital["set_off"] if step["step"] == "brought_forward"]


# ---- the lots against the holdings, and what the paper cannot count -----------------------------
def marks(pack: dict) -> list[bool]:
    """The "to check" mark of every sale of the paper, whichever list it is in."""
    lists = ("schedule", "needs_fmv", "unknown_cost", "exempt_buybacks")
    return [sale["to_check"] for key in lists for sale in pack["capital_gains"][key]]


def holding(ctx, day: int, shares: int | None) -> None:
    """The Upstox account's closing record of a day holding ``shares`` of the share, its
    holdings' value beside the line as the 15:55 record writes it. None: a record whose
    holdings could not be read (only its positions answered: no line and no value)."""
    lines = [] if shares is None else [{"instrument_key": f"NSE_EQ|{ISIN}", "qty": shares * U}]
    head = {"read_at": NOW, "positions_pnl_paise": 0,
            "holdings_value_paise": None if shares is None else shares * 150_000}  # fmt: skip
    ctx.store.write(OWNER, lambda tx: tx.upstox_eod_put(day, head, lines, NOW))


def test_the_paper_checks_its_lots_against_the_holdings_and_marks_the_shares_sales(ctx):
    """Design p7 4.3. The year's history sells 10 of the share on 27 Oct. With 25 entered as
    bought before the records the lots hold 25 - 10 = 15; Upstox held 40 at the close of
    12 Nov (the last record two days or more before the trades were read on the 14th). 15 is
    not 40: the paper says so first among its notes, carries the check and marks the share's
    sale "to check". Old code compared nothing: no ``lot_check`` and no mark."""
    pack = call(ctx, "tax_get")
    assert pack["lot_check"]["state"] == "waiting"  # no closing record to compare with yet
    assert marks(pack) == [False]
    # Said among the notes as it is: old code added "Sales of these shares are marked to
    # check." where no share was named and nothing was marked.
    assert pack["notes"][0] == lotcheck.WAITING_RECORD
    call(ctx, "tax_input_set", kind="opening_lot", request_id="lot-1",
         row=lot(ctx, qty_u=25 * U, cost_paise=3_750_000))  # fmt: skip
    holding(ctx, 20261112, 40)
    pack = call(ctx, "tax_get")
    check = pack["lot_check"]
    assert (check["state"], check["day"], check["account_id"]) == ("ok", 20261112, ctx.upstox_id)
    assert [(r["isin"], r["held_qty_u"], r["lots_qty_u"]) for r in check["rows"]] == [
        (ISIN, 40 * U, 15 * U)
    ]
    assert check["words"].startswith("Lots and holdings differ for 1 share: ")
    assert "40 held, 15 in lots. Compared at the close of 12 Nov 2026." in check["words"]
    assert pack["notes"][0] == f"{check['words']} Sales of these shares are marked to check."
    assert marks(pack) == [True]
    text = call(ctx, "tax_csv", schedule="capital_gains")["text"]
    head, sale = text.splitlines()[3], text.splitlines()[4]
    assert head.endswith(",To check: lots and holdings differ") and sale.endswith(",To check")
    # 15 held: 25 - 10 = 15 in the lots, so they agree, and nothing is marked.
    holding(ctx, 20261112, 15)
    pack = call(ctx, "tax_get")
    assert (pack["lot_check"]["rows"], pack["lot_check"]["words"]) == ([], None)
    assert marks(pack) == [False]
    assert not pack["notes"][0].startswith("Lots and holdings differ")


def test_the_check_names_a_bonus_or_split_first_and_never_a_day_that_is_not_read(ctx):
    """v0.13.0 verification H1, its probe. 25 entered as bought before the records and 10 sold
    on 27 Oct: 15 in the lots. Upstox holds 30 after a 1:1 bonus, which no lot counts while the
    Calendar is switched off. Old code: "If you bought the rest before 1 Apr 2024 or moved it
    in from another account, add that purchase under Tax, Inputs.", a purchase with a made-up
    cost for the bonus shares, before a day of years nobody read."""
    call(ctx, "tax_input_set", kind="opening_lot", request_id="lot-1",
         row=lot(ctx, qty_u=25 * U, cost_paise=3_750_000))  # fmt: skip
    holding(ctx, 20261112, 30)
    later = (
        "If the company gave a bonus or split its shares since you bought, do nothing: that is "
        "confirmed in the Calendar in a later release."
    )

    def words() -> str:
        (row,) = call(ctx, "tax_get")["lot_check"]["rows"]
        assert (row["held_qty_u"], row["lots_qty_u"], row["fix"]) == (30 * U, 15 * U, None)
        head = f"{row['name']}: Upstox holds 30 and the lots say 15. "
        assert row["words"].startswith(head)
        return row["words"][len(head) :]

    # Only this year's trades are read: the two earlier years Upstox keeps are read first.
    assert words() == (
        f"{later} SUDARSHAN has read Upstox's trades from 1 Apr 2026 only: read FY 2024-25 and "
        "FY 2025-26 on the Upstox tab's Trades view first. Then add a purchase under Tax, Inputs "
        "only for shares bought before the first day read, or moved in from another account."
    )
    loss_last_year(ctx)  # FY 2025-26 read in full
    assert words().endswith(
        "from 1 Apr 2025 only: read FY 2024-25 on the Upstox tab's Trades view first. Then add "
        "a purchase under Tax, Inputs only for shares bought before the first day read, or moved "
        "in from another account."
    )
    ctx.store.write(OWNER, lambda tx: tx.report_put("trades", 2024, "EQ", "ok", NOW, None,
                                                    {"rows": []}, NOW))  # fmt: skip
    assert words() == (
        f"{later} Add a purchase under Tax, Inputs only for shares bought before 1 Apr 2024, "
        "where the trades SUDARSHAN has read begin, or moved in from another account."
    )
    # The Calendar switched on: a bonus or a split is confirmed there.
    ctx.settings = dataclasses.replace(ctx.settings, insights=True)
    assert words().startswith(
        "If the company gave a bonus or split its shares since you bought, confirm it in the "
        "Calendar; do not add a purchase for it. Add a purchase under Tax, Inputs only for "
    )


def test_the_check_is_carried_only_on_the_paper_of_the_upstox_accounts_member(ctx):
    """v0.13.0 verification M1, its probe. The lots and the holdings compared are the Upstox
    account's, and its member is the owner. Old code put the owner's row on every taxpayer's
    paper: another family member's opened with "Lots and holdings differ for 1 share: ...", in
    the paper and the CSV that go to that member's CA."""
    call(ctx, "tax_input_set", kind="opening_lot", request_id="lot-1",
         row=lot(ctx, qty_u=25 * U, cost_paise=3_750_000))  # fmt: skip
    holding(ctx, 20261112, 40)
    assert len(call(ctx, "tax_get")["lot_check"]["rows"]) == 1  # the owner's paper
    other = ctx.store.write(OWNER, lambda tx: tx.insert("member", {"name": "B"}, NOW))["id"]
    pack = call(ctx, "tax_get", member=other)
    assert pack["taxpayer"] == other
    assert pack["lot_check"] == {"state": "none", "account_id": None, "day": None, "words": None,
                                 "rows": [], "unrecorded_count": 0}  # fmt: skip
    assert not [note for note in pack["notes"] if note.startswith("Lots and holdings differ")]
    text = call(ctx, "tax_csv", schedule="capital_gains", member=other)["text"]
    assert not [line for line in text.splitlines() if line.endswith(",To check")]


def test_a_closing_record_whose_holdings_were_not_read_is_never_compared(ctx):
    """v0.13.0 verification M2, its probe. The record of 12 Nov read the positions but not the
    holdings: no line, no holdings' value. Old code took it for the day compared, as holding
    nothing: "Upstox holds 0 and the lots say 15. A sale, a transfer out or a bonus or split may
    be missing", that note first in the paper and every sale of the share marked to check. It
    is no holdings record: the record of 11 Nov, holding the 15 the lots say, is compared."""
    call(ctx, "tax_input_set", kind="opening_lot", request_id="lot-1",
         row=lot(ctx, qty_u=25 * U, cost_paise=3_750_000))  # fmt: skip
    holding(ctx, 20261111, 15)
    holding(ctx, 20261112, None)
    pack = call(ctx, "tax_get")
    check = pack["lot_check"]
    assert (check["state"], check["day"], check["rows"]) == ("ok", 20261111, [])
    assert marks(pack) == [False]
    # With no record of holdings two days before the trades were read, it waits for one.
    holding(ctx, 20261111, None)
    assert call(ctx, "tax_get")["lot_check"]["state"] == "waiting"


def test_with_the_calendar_switched_off_the_check_offers_no_tap(ctx):
    """v0.14.0 verification L5. A release rolled back after a bonus was confirmed the wrong way
    round: 30 held before the 1:2 bonus of 9 Oct, applied as 2 new for every 1 held, so the lots
    say 30 + 60 - 10 = 80 where Upstox holds 30 + 15 - 10 = 35. With the Calendar switched off
    ``ca_decide`` answers "not ready", so a button would only say that: old code showed it. The
    sentence still says what was found; switched on again, the tap is offered."""
    from sudarshan.portfolio import corpactions

    def put(tx):
        row = {"isin": ISIN, "kind": "bonus", "ex_day": 20261009, "amount_u": None,
               "ratio_num": 1, "ratio_den": 2, "details": []}  # fmt: skip
        event = tx.ca_event_put({**row, "raw_hash": corpactions.event_hash(row)}, NOW)[0]
        decided = {"event_id": event["id"], "scope": "upstox", "state": "applied",
                   "ratio_num": 2, "ratio_den": 1, "activity_id": None}  # fmt: skip
        return tx.insert("ca_decision", decided, NOW)

    call(ctx, "tax_input_set", kind="opening_lot", request_id="lot-1",
         row=lot(ctx, qty_u=30 * U, cost_paise=3_750_000))  # fmt: skip
    holding(ctx, 20261112, 35)
    decision = ctx.store.write(OWNER, put)
    (row,) = call(ctx, "tax_get")["lot_check"]["rows"]
    assert (row["held_qty_u"], row["lots_qty_u"], row["fix"]) == (35 * U, 80 * U, None)
    assert row["words"].endswith(
        "Read the other way round, the lots would say 35: it was likely confirmed the wrong way "
        "round."
    )
    ctx.settings = dataclasses.replace(ctx.settings, insights=True)
    (row,) = call(ctx, "tax_get")["lot_check"]["rows"]
    assert row["fix"] == {"event_id": decision["event_id"], "version": decision["version"],
                          "state": "applied", "ratio_num": 1, "ratio_den": 2}  # fmt: skip
    # Not decided at all: To confirm is named only while it is there to open.
    ctx.store.write(OWNER, lambda tx: tx.remove("ca_decision", decision["id"]))
    (row,) = call(ctx, "tax_get")["lot_check"]["rows"]
    assert row["words"].endswith("The bonus of 9 Oct 2026 waits for you under To confirm.")
    ctx.settings = dataclasses.replace(ctx.settings, insights=False)
    (row,) = call(ctx, "tax_get")["lot_check"]["rows"]
    assert "To confirm" not in row["words"] and row["fix"] is None


def test_the_check_reads_nothing_the_paper_has_read_and_a_fault_in_it_leaves_the_paper(
    ctx, monkeypatch
):
    """v0.14.0 verification L7. The check runs on every open of the Tax view, and used to read
    the running year's history and every share's name a second time (0.67 s of 20,000 trades
    and 250 shares, each share's replay sifting the whole history); and a fault inside it took
    the whole Tax view down. It now takes the inputs and names the paper read (each year read
    once, the names once), sorts each share out once, and a fault in it is a sentence."""
    from sudarshan.portfolio import corpactions, lotcheck, tax_adapter

    read = {"inputs": 0, "names": 0}
    inputs, names = tax_adapter.inputs, corpactions.share_names

    def counted_inputs(*args, **kwargs):
        read["inputs"] += 1
        return inputs(*args, **kwargs)

    def counted_names(*args, **kwargs):
        read["names"] += 1
        return names(*args, **kwargs)

    call(ctx, "tax_input_set", kind="opening_lot", request_id="lot-1",
         row=lot(ctx, qty_u=25 * U, cost_paise=3_750_000))  # fmt: skip
    holding(ctx, 20261112, 40)
    monkeypatch.setattr(tax_adapter, "inputs", counted_inputs)
    monkeypatch.setattr(corpactions, "share_names", counted_names)
    pack = call(ctx, "tax_get")
    assert len(pack["lot_check"]["rows"]) == 1
    # One read of each year; the names once for the paper and once for its dividends block.
    # The check reads neither again (old code: 4 and 3).
    assert read == {"inputs": kinds.REPORT_YEARS, "names": 2}

    def broken(*args, **kwargs):
        raise RuntimeError("a fault inside the check")

    monkeypatch.setattr(lotcheck, "_actions", broken)
    pack = call(ctx, "tax_get")
    assert pack["lot_check"] == {"state": "waiting", "account_id": None, "day": None,
                                 "words": lotcheck.UNCHECKED, "rows": [],
                                 "unrecorded_count": 0}  # fmt: skip
    assert pack["notes"][0] == lotcheck.UNCHECKED and marks(pack) == [False]
    assert lotcheck.UNCHECKED == (
        "SUDARSHAN could not check the tax lots against your Upstox holdings this time. The rest "
        "of this page is not affected, and the check runs again when you open it next."
    )
    assert "To check" in call(ctx, "tax_csv", schedule="capital_gains")["text"]  # the CSV too


def test_the_kept_tax_pack_says_the_checks_sentences_of_the_paper_as_it_was_made(
    ctx, monkeypatch
):
    """v0.13.0 verification N3. A check that waits, or that could not be made, heads the
    paper's notes. The journal prints the paper and keeps it whole in the archive, where old
    code's "The rest of this page is not affected, and the check runs again when you open it
    next" read wrongly. The pack (``tax_pack``) says each of them of the paper as it was made;
    the page keeps its own words."""
    assert call(ctx, "tax_get")["notes"][0] == lotcheck.WAITING_RECORD
    assert call(ctx, "tax_pack")["notes"][0] == (
        "The tax lots were not checked against the Upstox holdings for this paper: when it was "
        "made, no closing record of the Upstox account was there from two days or more before "
        "its trades were last read."
    )
    call(ctx, "tax_input_set", kind="opening_lot", request_id="lot-1",
         row=lot(ctx, qty_u=25 * U, cost_paise=3_750_000))  # fmt: skip
    holding(ctx, 20261112, 40)

    def broken(*args, **kwargs):
        raise RuntimeError("a fault inside the check")

    monkeypatch.setattr(lotcheck, "_actions", broken)
    assert call(ctx, "tax_get")["notes"][0] == lotcheck.UNCHECKED
    assert call(ctx, "tax_pack")["notes"][0] == (
        "The tax lots could not be checked against the Upstox holdings when this paper was made. "
        "The rest of the paper is not affected."
    )
    for schedule in ("capital_gains", "dividends"):
        text = call(ctx, "tax_csv", schedule=schedule)["text"]
        assert "this page" not in text and "open it next" not in text
    monkeypatch.undo()
    # The year's share trades not read in full: the check waits for them.
    record = ctx.store.read(OWNER, lambda tx: tx.report_get("trades", 2026, "EQ"))
    ctx.store.write(OWNER, lambda tx: tx.report_put("trades", 2026, "EQ", "partial", NOW, None,
                                                    record["payload"], NOW))  # fmt: skip
    assert lotcheck.WAITING_TRADES in call(ctx, "tax_get")["notes"]
    assert lotcheck.FILE_WORDS[lotcheck.WAITING_TRADES] in call(ctx, "tax_pack")["notes"]
    assert lotcheck.FILE_WORDS[lotcheck.WAITING_TRADES] == (
        "The tax lots were not checked against the Upstox holdings for this paper: Upstox's "
        "share trades of this year were not read in full when it was made."
    )


def test_a_bonus_still_to_come_is_said_as_coming_and_the_notes_name_the_share(ctx):
    """v0.14.0 verification L4. Two bonuses not confirmed: one of 9 Oct, past, and one of
    5 Dec, still to come on 14 Nov. Old code said of both "A bonus of INE002A01018 ... is not
    confirmed ... Confirm it", the second while ``ca_decide`` refuses it as early, and named
    the share by its ISIN only."""
    from sudarshan.portfolio import corpactions

    def put(tx):
        for day in (20261009, 20261205):
            row = {"isin": ISIN, "kind": "bonus", "ex_day": day, "amount_u": None,
                   "ratio_num": 1, "ratio_den": 2, "details": []}  # fmt: skip
            tx.ca_event_put({**row, "raw_hash": corpactions.event_hash(row)}, NOW)

    call(ctx, "tax_input_set", kind="opening_lot", request_id="lot-1",
         row=lot(ctx, qty_u=30 * U, cost_paise=3_750_000))  # fmt: skip
    ctx.store.write(OWNER, put)
    pack = call(ctx, "tax_get")
    lists = ("schedule", "needs_fmv", "unknown_cost", "exempt_buybacks")
    (name,) = {sale["name"] for key in lists for sale in pack["capital_gains"][key]}
    assert name and name != ISIN
    assert f"A bonus of {name} on 9 Oct 2026 is not confirmed, so the lots were left as they " \
        "were. Confirm it and the figures follow." in pack["notes"]  # fmt: skip
    assert f"A bonus of {name} on 5 Dec 2026 is coming. The lots change once you confirm it, " \
        "on or after that day." in pack["notes"]  # fmt: skip
    assert not [note for note in pack["notes"] if ISIN in note]


def test_the_paper_says_why_it_counts_no_dividend_and_lists_what_it_could_not_count(ctx):
    """v0.13.0 review H1. The paper is on while Upstox's dividends are not read at all (the
    insights switch is off): its dividends total of nothing used to stand without a word, as
    "No dividend is recorded for this financial year." in the Tax pack."""
    switched_off = (
        "This paper counts no dividends yet: SUDARSHAN reads them from Upstox's corporate "
        "actions only once the dividends view is switched on in a later release. Take the "
        "year's dividends from your bank statements or Form 26AS."
    )
    pack = call(ctx, "tax_get")
    assert pack["dividends"]["note"] == switched_off and switched_off in pack["notes"]
    assert (pack["dividends"]["uncounted"], pack["dividends"]["uncounted_count"]) == ([], 0)
    assert switched_off in call(ctx, "tax_csv", schedule="dividends")["text"]
    assert call(ctx, "tax_pack")["dividends"]["note"] == switched_off  # the journal's Tax pack
    # Switched on, before the first night's read.
    ctx.settings = dataclasses.replace(ctx.settings, insights=True)
    assert call(ctx, "tax_get")["dividends"]["note"] == (
        "This paper counts no dividends yet: SUDARSHAN reads Upstox's corporate actions for the "
        "first time tonight after 23:45, and the dividends of what you held follow."
    )

    # A night has read the list: a dividend of July, months before any closing record.
    def read(tx):
        from sudarshan.portfolio import corpactions, dividends

        row = {"isin": ISIN, "kind": "dividend", "ex_day": 20260715, "amount_u": 200_000,
               "ratio_num": None, "ratio_den": None, "details": []}  # fmt: skip
        tx.ca_event_put({**row, "raw_hash": corpactions.event_hash(row)}, NOW)
        tx.job_put("corporate_actions", 20261113, "ok", "1 of 1 instruments read", NOW)
        return dividends.sync(tx, 20261114, NOW)

    assert ctx.store.write(OWNER, read) == 1
    pack = call(ctx, "tax_get")
    block = pack["dividends"]
    assert (block["note"], block["uncounted_count"], block["total_paise"]) == (None, 1, 0)
    (left,) = block["uncounted"]
    assert (left["isin"], left["ex_day"]) == (ISIN, 20260715)
    assert left["words"].startswith("No holdings record reaches the day before this ex-date")
    assert pack["notes"][-1] == (
        f"1 dividend of {pack['fy_label']} could not be counted and is in no total; it is "
        "listed under Dividends with its date."
    )
    text = call(ctx, "tax_csv", schedule="dividends")["text"]
    assert "Not counted,1" in text and f"{ISIN}," in text and "2026-07-15" in text
    # Switched off again with a line already made: the paper never says it counts none.
    ctx.settings = dataclasses.replace(ctx.settings, insights=False)
    assert call(ctx, "tax_get")["dividends"]["note"].startswith(
        "The dividends view is switched off, so no dividend after it was switched off is read"
    )


# ---- tax_get -----------------------------------------------------------------------------------
def test_the_paper_is_the_cores_pack_of_the_year_and_the_owner_by_default(ctx):
    pack = call(ctx, "tax_get")
    assert (pack["fy"], pack["taxpayer"], pack["running"]) == (2026, 1, True)
    assert pack["note"].startswith("A working paper: verify with your CA.")
    (sale,) = pack["capital_gains"]["schedule"]
    assert (sale["isin"], sale["gain_paise"], sale["term"]) == (ISIN, 598_600, "long")
    assert pack["business"]["non_speculative"]["gross_paise"] == -81_250
    assert call(ctx, "tax_get", fy=2026, member=1) == pack


def test_a_loss_of_an_earlier_year_is_brought_in_and_the_owners_figure_wins(ctx):
    loss_last_year(ctx)
    capital = call(ctx, "tax_get")["capital_gains"]
    assert brought(capital) == [
        {"step": "brought_forward", "bucket": "stcl", "loss_fy": 2025, "against": "long",
         "rate_bp": 1250, "paise": 200_000}
    ]  # fmt: skip
    assert capital["lt_gain_paise"] == 598_600 and capital["carried"] == []
    carry = {"member_id": 1, "loss_fy": 2025, "bucket": "stcl", "paise": 50_000}
    call(ctx, "tax_input_set", kind="carry", row=carry, request_id="c-1")
    capital = call(ctx, "tax_get")["capital_gains"]
    assert [step["paise"] for step in brought(capital)] == [50_000]
    assert [(r["paise"], r["source"]) for r in capital["own_figures"]] == [(200_000, "computed")]


def test_what_is_not_read_is_said_in_the_paper(ctx):
    missing = call(ctx, "tax_get")["inputs"]["missing"]
    said = "Losses brought forward from FY 2024-25 and FY 2025-26 come from what is read of those"
    assert any(line.startswith(said) for line in missing)


def test_a_year_upstox_does_not_keep_and_a_member_not_there_are_refused_in_words(ctx):
    old = refused(ctx, "tax_get", fy=2023)
    assert (old.code, old.message, old.field) == ("bad_request", kinds.WORDS["tax_years"], "fy")
    gone = refused(ctx, "tax_get", member=99)
    assert (gone.code, gone.message) == ("not_found", NOT_THERE)


def test_the_years_dividends_are_in_the_paper_and_no_longer_listed_as_not_computed(ctx):
    line = {"member_id": 1, "account_id": ctx.upstox_id, "isin": ISIN, "fy": 2026,
            "ex_day": 20260815, "qty_u": 10 * U, "expected_paise": 10_000, "state": "confirmed",
            "credited_paise": 9_000, "tds_paise": 1_000, "credited_day": 20260820}  # fmt: skip
    ctx.store.write(OWNER, lambda tx: tx.insert("dividend_line", line, NOW))
    pack = call(ctx, "tax_get")
    (payer,) = pack["dividends"]["by_payer"]
    assert (payer["isin"], payer["confirmed_paise"], payer["total_paise"]) == (ISIN, 10_000, 10_000)
    assert pack["dividends"]["total_paise"] == 10_000 and pack["dividends"]["buyback_paise"] == 0
    assert not [words for words in pack["not_computed"] if words.startswith("Dividends")]


# ---- tax_inputs and saving one ------------------------------------------------------------------
def test_the_inputs_name_each_account_with_its_mode_and_the_default_readings(ctx):
    found = call(ctx, "tax_inputs")
    assert found["member_id"] == 1
    assert found["accounts"] == [
        {
            "id": ctx.upstox_id,
            "name": "Upstox",
            "kind": "upstox",
            "tax_mode": "reports",
            "version": 1,
        }
    ]
    unset = dict.fromkeys(("turnover_method", "exempt_order", "lt_boundary"))
    assert found["settings"] == {"member_id": 1, **unset, "allocate_expenses": True, "version": 0}
    lists = ("opening_lots", "fmvs", "carries", "day_overrides", "trade_marks")
    assert [found[name] for name in lists] == [[]] * 5


def test_a_dry_run_says_what_a_save_would_change_and_writes_nothing(ctx):
    shown = call(ctx, "tax_input_set", kind="opening_lot", row=lot(ctx), dry_run=True)
    assert shown == {"kind": "opening_lot", "row": None, "effect": [
        "In FY 2026-27 this changes long-term gain from Rs 5,986 to Rs 0.",
        "In FY 2026-27 this changes the number of sales that need the 31 Jan 2018 price from 0 "
        "to 1.",
    ]}  # fmt: skip
    assert call(ctx, "tax_inputs")["opening_lots"] == []
    saved = call(ctx, "tax_input_set", kind="opening_lot", row=lot(ctx), request_id="o-1")
    assert saved["row"]["source"] == "owner" and saved["effect"] == []
    fmv = {"isin": ISIN, "fmv_u": 18_000_000}  # 1,800 on 31 Jan 2018: the cost it sets
    priced = call(ctx, "tax_input_set", kind="fmv", row=fmv, dry_run=True)
    assert priced["effect"][0] == "In FY 2026-27 this changes long-term gain from Rs 0 to Rs 7,986."


def test_a_save_sent_twice_makes_one_row_and_a_second_price_for_a_share_is_refused(ctx):
    first = call(ctx, "tax_input_set", kind="fmv", row={"isin": ISIN.lower(), "fmv_u": 1},
                 request_id="f-1")  # fmt: skip
    again = call(ctx, "tax_input_set", kind="fmv", row={"isin": ISIN, "fmv_u": 1}, request_id="f-1")
    assert again == first and first["row"]["isin"] == ISIN
    twice = refused(ctx, "tax_input_set", kind="fmv", row={"isin": ISIN, "fmv_u": 2})
    assert twice.code == "refused"
    assert twice.message == "A 31 Jan 2018 price for this share is already entered. Edit that one."


@pytest.mark.parametrize(
    ("row", "field", "words"),
    [
        ({"isin": "INE12", "fmv_u": 1}, "isin",
         "An ISIN is 12 letters and digits, such as INE002A01018."),
        ({"isin": ISIN, "fmv_u": 0}, "fmv_u",
         "Enter the price of 31 Jan 2018 as a number above zero."),
        ({"isin": ISIN, "fmv_u": 1.5}, "fmv_u",
         "Enter the price of 31 Jan 2018 as a number above zero."),
    ],
)  # fmt: skip
def test_a_field_that_is_not_right_is_refused_with_its_sentence(ctx, row, field, words):
    wrong = refused(ctx, "tax_input_set", kind="fmv", row=row)
    assert (wrong.code, wrong.field, wrong.message) == ("bad_request", field, words)


def test_a_date_after_today_a_loss_of_this_year_and_another_members_account_are_refused(ctx):
    late = refused(ctx, "tax_input_set", kind="opening_lot", row=lot(ctx, acquired_day=20261115))
    assert late.field == "acquired_day"
    carry = {"member_id": 1, "loss_fy": 2026, "bucket": "stcl", "paise": 1}
    assert refused(ctx, "tax_input_set", kind="carry", row=carry).field == "loss_fy"
    other = ctx.store.write(OWNER, lambda tx: tx.insert("member", {"name": "B"}, NOW))["id"]
    wrong = refused(ctx, "tax_input_set", kind="opening_lot", row=lot(ctx, member_id=other))
    assert (wrong.code, wrong.message) == (
        "bad_request",
        "That account is not one of this member's. Choose another.",
    )


def test_the_readings_are_saved_from_their_version_and_reach_the_paper(ctx):
    row = {
        "member_id": 1,
        "turnover_method": "abs_pnl_plus_sale_premium",
        "allocate_expenses": True,
    }
    saved = call(ctx, "tax_input_set", kind="settings", row=row, version=0)["row"]
    assert (saved["turnover_method"], saved["version"]) == ("abs_pnl_plus_sale_premium", 1)
    stale = refused(ctx, "tax_input_set", kind="settings", row=row, version=0)
    assert stale.code == "version_conflict"
    assert call(ctx, "tax_get")["business"]["turnover_method"] == "abs_pnl_plus_sale_premium"
    odd = refused(ctx, "tax_input_set", kind="settings", row={**row, "lt_boundary": "x"}, version=1)
    assert odd.field == "lt_boundary"


def test_an_input_is_deleted_from_the_version_it_was_read_at(ctx):
    mark = {"member_id": 1, "account_id": ctx.upstox_id, "isin": ISIN, "day": 20261027,
            "qty_u": U, "price_u": 1, "trade_id": "50011"}  # fmt: skip
    row = call(ctx, "tax_input_set", kind="trade_mark", row=mark)["row"]
    stale = refused(ctx, "tax_input_delete", kind="trade_mark", id=row["id"], version=9)
    assert stale.code == "version_conflict"
    assert refused(ctx, "tax_input_delete", kind="settings", id=1, version=1).field == "kind"
    gone = call(ctx, "tax_input_delete", kind="trade_mark", id=row["id"], version=1)
    assert gone == {"id": row["id"]} and call(ctx, "tax_inputs")["trade_marks"] == []


# ---- the closed lists are the core's ------------------------------------------------------------
def test_the_lists_the_phone_offers_are_the_cores_own():
    assert kinds.CARRY_BUCKETS == model.BUCKETS
    for names, table in ((kinds.TURNOVER_METHODS, rules.TURNOVER_METHOD),
                         (kinds.EXEMPT_ORDERS, rules.EXEMPT_ORDER),
                         (kinds.LT_BOUNDARIES, rules.LT_BOUNDARY)):  # fmt: skip
        assert names == (table[-1].value, *table[-1].alternatives)


# ---- how an account counts (owner decision 9: "not for tax") ------------------------------------
def demat(ctx, member: int = 1) -> dict:
    values = {"member_id": member, "name": "Old demat", "kind": "demat"}
    return ctx.store.write(OWNER, lambda tx: tx.insert("account", values, NOW))


def mode(account: dict, tax_mode: str = "none", member: int = 1) -> dict:
    return {"member_id": member, "account_id": account["id"], "tax_mode": tax_mode}


def listed(ctx) -> dict:
    return {a["id"]: (a["tax_mode"], a["version"]) for a in call(ctx, "tax_inputs")["accounts"]}


def test_an_own_account_is_set_as_not_for_tax_from_its_version_and_back(ctx, caplog):
    account = demat(ctx)
    assert listed(ctx)[account["id"]] == ("ledger", 1)
    with caplog.at_level("INFO", logger="sudarshan.portfolio.ops.tax_ops"):
        saved = call(ctx, "tax_input_set", kind="account_mode", row=mode(account), version=1)
    assert saved == {"kind": "account_mode", "effect": [], "row": {
        "id": account["id"], "name": "Old demat", "kind": "demat", "tax_mode": "none",
        "version": 2}}  # fmt: skip
    assert listed(ctx)[account["id"]] == ("none", 2)
    (line,) = [r.getMessage() for r in caplog.records]
    assert line == f"account {account['id']} of member 1 now counts for tax as none (was ledger)"
    assert "Old demat" not in line  # the log names ids only
    stale = refused(ctx, "tax_input_set", kind="account_mode", row=mode(account, "ledger"),
                    version=1)  # fmt: skip
    assert stale.code == "version_conflict"
    back = call(ctx, "tax_input_set", kind="account_mode", row=mode(account, "ledger"), version=2)
    assert (back["row"]["tax_mode"], back["row"]["version"]) == ("ledger", 3)
    same = call(ctx, "tax_input_set", kind="account_mode", row=mode(account, "ledger"), version=3)
    assert same["row"]["version"] == 3  # nothing changed: nothing written


def test_the_not_for_tax_line_is_logged_once_and_only_for_a_change_that_was_saved(
    ctx, caplog, monkeypatch
):
    """The Inputs sheet asks what a change would do (a dry run, undone) before every save. The
    line is the record of a change made: none from the dry run, none from a save whose commit
    failed, one from the save."""
    account = demat(ctx)
    change = {"kind": "account_mode", "row": mode(account), "version": 1}

    def said():
        return [r.getMessage() for r in caplog.records if r.name.endswith("ops.tax_ops")]

    with caplog.at_level("INFO", logger="sudarshan.portfolio.ops.tax_ops"):
        shown = call(ctx, "tax_input_set", **change, dry_run=True)
        assert shown["row"] is None and listed(ctx)[account["id"]] == ("ledger", 1)
        assert said() == []

        real = ctx.store.write

        def fails_at_the_end(scope, fn):
            def run(tx):
                fn(tx)
                raise OSError("no space left")

            return real(scope, run)

        monkeypatch.setattr(ctx.store, "write", fails_at_the_end)
        assert refused(ctx, "tax_input_set", **change).status >= 500
        monkeypatch.undo()
        assert listed(ctx)[account["id"]] == ("ledger", 1)
        assert said() == []

        call(ctx, "tax_input_set", **change)
        assert listed(ctx)[account["id"]] == ("none", 2)
        assert said() == [
            f"account {account['id']} of member 1 now counts for tax as none (was ledger)"
        ]


def test_the_upstox_account_a_wrong_mode_and_a_missing_version_are_refused_in_words(ctx):
    upstox = {"id": ctx.upstox_id}
    fixed = refused(ctx, "tax_input_set", kind="account_mode", row=mode(upstox), version=1)
    assert (fixed.code, fixed.message) == (
        "refused",
        "The Upstox account always counts from Upstox's reports. Only an account of your own "
        "tabs can be set as not for tax.",
    )
    account = demat(ctx)
    for wrong in ("reports", "off", None):
        bad = refused(ctx, "tax_input_set", kind="account_mode", row=mode(account, wrong),
                      version=1)  # fmt: skip
        assert (bad.code, bad.field) == ("bad_request", "tax_mode")
        assert bad.message == "Choose how this account counts: from its entries, or not for tax."
    assert refused(ctx, "tax_input_set", kind="account_mode", row=mode(account)).field == "version"
    other = ctx.store.write(OWNER, lambda tx: tx.insert("member", {"name": "B"}, NOW))["id"]
    theirs = refused(ctx, "tax_input_set", kind="account_mode", row=mode(account, member=other),
                     version=1)  # fmt: skip
    assert theirs.message == "That account is not one of this member's. Choose another."
    assert listed(ctx)[account["id"]] == ("ledger", 1)


def test_the_kind_is_never_deleted_which_would_delete_the_account(ctx):
    account = demat(ctx)
    gone = refused(ctx, "tax_input_delete", kind="account_mode", id=account["id"], version=1)
    assert (gone.code, gone.field) == ("bad_request", "kind")
    assert account["id"] in listed(ctx)


def test_a_dry_run_says_what_leaving_an_account_out_changes_and_writes_nothing(ctx):
    """The tab's account sold 10 shares at a gain; marked not for tax, the gain leaves the paper."""
    account = demat(ctx)

    def setup(tx):
        tab = tx.insert("tab", {"name": "Own", "kind": "stocks", "icon": "chart",
                                "colour": "brand"}, NOW)  # fmt: skip
        asset = tx.insert("asset", {"kind": "listed", "name": "Own Ltd", "isin": "INE000W01016",
                                    "asset_class": "equity"}, NOW)  # fmt: skip
        for seq, (kind, day, rupees) in enumerate((("BUY", 20260406, 1_000),
                                                   ("SELL", 20260601, 1_500)), start=1):  # fmt: skip
            tx.insert("activity", {
                "tab_id": tab["id"], "seq": seq, "kind": kind, "trade_date": day,
                "account_id": account["id"], "asset_id": asset["id"], "qty_u": 10 * U,
                "amount_paise": 10 * rupees * 100,
            }, NOW)  # fmt: skip

    ctx.store.write(OWNER, setup)
    before = call(ctx, "tax_get")["capital_gains"]["st_gain_paise"]
    shown = call(ctx, "tax_input_set", kind="account_mode", row=mode(account), version=1,
                 dry_run=True)  # fmt: skip
    assert shown["row"] is None
    # 5,000 less its share of the year's charges
    assert before == 499_488
    assert shown["effect"][0] == (
        "In FY 2026-27 this changes short-term gain from Rs 4,994.88 to Rs 0."
    )
    assert listed(ctx)[account["id"]] == ("ledger", 1)
    assert call(ctx, "tax_get")["capital_gains"]["st_gain_paise"] == before
    call(ctx, "tax_input_set", kind="account_mode", row=mode(account), version=1)
    assert call(ctx, "tax_get")["capital_gains"]["st_gain_paise"] == 0


# ---- the dry runs the Inputs sheet asks for (lane G: every kind is checked before its save) ------
def test_a_dry_run_of_the_readings_and_of_a_tender_says_its_effect_and_writes_nothing(ctx):
    row = {"member_id": 1, "turnover_method": "abs_pnl_plus_sale_premium",
           "exempt_order": None, "lt_boundary": None, "allocate_expenses": False}  # fmt: skip
    shown = call(ctx, "tax_input_set", kind="settings", row=row, version=0, dry_run=True)
    assert shown["row"] is None
    assert shown["effect"] == [
        "In FY 2026-27 this changes long-term gain from Rs 5,986 to Rs 6,000."
    ]
    assert call(ctx, "tax_inputs")["settings"]["version"] == 0
    tender = {"member_id": 1, "account_id": ctx.upstox_id, "isin": ISIN, "day": 20260715,
              "qty_u": 5 * U, "price_u": 30_000_000, "trade_id": None}  # fmt: skip
    shown = call(ctx, "tax_input_set", kind="trade_mark", row=tender, dry_run=True)
    assert shown["row"] is None and shown["effect"][0].startswith("In FY 2026-27 this changes ")
    assert call(ctx, "tax_inputs")["trade_marks"] == []


# ---- each sale carries its share's name beside the ISIN (lane G) ---------------------------------
def test_a_sale_is_named_by_the_holding_else_by_upstoxs_history_and_keeps_its_isin(ctx):
    loss_last_year(ctx)
    (sale,) = call(ctx, "tax_get", fy=2025)["capital_gains"]["schedule"]
    assert (sale["isin"], sale["name"]) == (LOSS_ISIN, "LOSS LTD")  # Upstox's scrip name

    def hold(tx):
        asset = tx.insert("asset", {"kind": "listed", "name": "Loss Makers Ltd",
                                    "isin": "INE000N01017", "asset_class": "equity"}, NOW)  # fmt: skip
        tx.insert("asset_alias", {"asset_id": asset["id"], "kind": "isin", "value": LOSS_ISIN}, NOW)

    ctx.store.write(OWNER, hold)  # the holding, which had this ISIN before a change of ISIN
    (sale,) = call(ctx, "tax_get", fy=2025)["capital_gains"]["schedule"]
    assert (sale["isin"], sale["name"]) == (LOSS_ISIN, "Loss Makers Ltd")
    assert call(ctx, "tax_pack", fy=2025)["capital_gains"]["schedule"] == [sale]
    text = call(ctx, "tax_csv", fy=2025, schedule="capital_gains")["text"]
    assert f"{LOSS_ISIN},Loss Makers Ltd," in text  # the CSV writes the same name


def test_every_list_of_sales_is_named_and_a_share_no_record_names_has_none():
    from sudarshan.portfolio.ops import tax_ops

    lists = {key: [{"isin": f"INE00{n}X01010"}] for n, key in enumerate(tax_ops.SALE_LISTS)}
    pack = tax_ops._named({"capital_gains": lists}, {"INE000X01010": "First Ltd"})
    rows = [row for key in tax_ops.SALE_LISTS for row in pack["capital_gains"][key]]
    assert [row["name"] for row in rows] == ["First Ltd", None, None, None]
    assert [row["isin"] for row in rows] == [f"INE00{n}X01010" for n in range(4)]
    assert set(tax_ops.SALE_LISTS) == {"schedule", "needs_fmv", "unknown_cost", "exempt_buybacks"}


def test_a_sale_the_paper_does_not_compute_is_named_too():
    """An ETF, a REIT or a bond sold is listed by ISIN and value; the ISIN alone does not say
    what it was, so it carries the same name a share's sale does. A contract names itself."""
    from sudarshan.portfolio.ops import tax_ops

    contract = ["NFO", "ACME", 20260924, 0, "FUT"]
    outside = [{"instrument": "INF204KB17I5"}, {"instrument": "INF000D01015"},
               {"instrument": contract}]  # fmt: skip
    pack = {"capital_gains": {key: [] for key in tax_ops.SALE_LISTS},
            "not_computed_sales": outside}  # fmt: skip
    named = tax_ops._named(pack, {"INF204KB17I5": "GOLD ETF UNITS"})["not_computed_sales"]
    assert named == [
        {"instrument": "INF204KB17I5", "name": "GOLD ETF UNITS"},
        {"instrument": "INF000D01015", "name": None},
        {"instrument": contract},
    ]
