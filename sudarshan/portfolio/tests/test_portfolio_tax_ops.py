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

from sudarshan.portfolio import config, reports
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
