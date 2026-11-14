"""The tax core's inputs from the portfolio (phase P7, M7.6; design p7 4.1 to 4.3, 11.2).

Three kinds of test. A report-cache replay: rows typed from Upstox's documented samples (a share
sale, option trades with ``strike_price`` as a string, an empty ``expiry`` on the share), parsed
as the reader parses Upstox's answer, kept in the cache, then through the adapter and the core.
The ledger agreement: one synthetic custom tab replayed by the portfolio's own ledger and, through
the adapter, by the tax core's lots: realised totals and open lots match line for line. And the
adapter's own rules: who counts, what is said when something is missing, and that it decides
nothing itself. All data is synthetic.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from sudarshan.portfolio import reports, tax_adapter
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.core.ledger import replay
from sudarshan.portfolio.core.model import Entry
from sudarshan.portfolio.store import DB_FILENAME, PortfolioStore, Scope
from sudarshan.tax import lots
from sudarshan.tax.model import OpeningLot, Trade
from sudarshan.tax.pack import build_pack

OWNER = Scope.owner()
NOW = 1_794_650_000_000  # 14 Nov 2026
TODAY = 20261114
FY = 2026
ISIN = "INE002A01018"
KEY = "INE000X01010"
U = 1_000_000


@pytest.fixture(autouse=True)
def _stop():
    yield
    store_module.stop()


@pytest.fixture
def made(tmp_path: Path):
    opened = PortfolioStore(tmp_path / "s" / DB_FILENAME, now=NOW)
    yield opened
    opened.close()


def answered(raw):
    """As the reader parses Upstox's answer: every fraction a Decimal."""
    return json.loads(json.dumps(raw), parse_float=Decimal)


# ---- Upstox's documented rows ------------------------------------------------------------------
EQ_SALE = {
    "exchange": "NSE", "segment": "EQ", "option_type": None, "quantity": 10, "amount": 26000.0,
    "trade_id": "50011", "trade_date": "2026-10-27", "transaction_type": "SELL",
    "scrip_name": "RELIANCE INDUSTRIES", "strike_price": None, "expiry": "", "price": 2600.0,
    "isin": ISIN, "symbol": "RELIANCE", "instrument_token": "NSE_EQ|" + ISIN,
}  # fmt: skip


def option(trade_id, day, side, strike, kind, price):
    return {
        "exchange": "NFO", "segment": "FO", "option_type": kind, "quantity": 65,
        "amount": 65 * price, "trade_id": trade_id, "trade_date": day, "transaction_type": side,
        "scrip_name": f"NIFTY {strike} {kind}", "strike_price": f"{strike}.0",
        "expiry": "2026-11-03", "price": price, "isin": None,
        "symbol": f"NIFTY26NOV{strike}{kind}", "instrument_token": None,
    }  # fmt: skip


FO_ROWS = [
    option("50012", "2026-10-28", "SELL", 25900, "CE", 125.0),
    option("50014", "2026-10-30", "BUY", 25900, "CE", 100.0),
    option("50013", "2026-10-29", "BUY", 26000, "PE", 50.0),  # still held at its expiry
]
PAIRS = {
    "EQ": [{"quantity": 10, "isin": ISIN, "scrip_name": "RELIANCE INDUSTRIES", "trade_type": "EQ",
            "buy_date": "10-01-2024", "buy_average": 2000.0, "sell_date": "27-10-2026",
            "sell_average": 2600.0, "buy_amount": 20000.0, "sell_amount": 26000.0}],
    "FO": [{"quantity": 65, "isin": None, "scrip_name": "NIFTY 25900 CE", "trade_type": "OPT",
            "buy_date": "30-10-2026", "buy_average": 100.0, "sell_date": "28-10-2026",
            "sell_average": 125.0, "buy_amount": 6500.0, "sell_amount": 8125.0},
           {"quantity": 65, "isin": None, "scrip_name": "NIFTY 26000 PE", "trade_type": "OPT",
            "buy_date": "29-10-2026", "buy_average": 50.0, "sell_date": "03-11-2026",
            "sell_average": 12.5, "buy_amount": 3250.0, "sell_amount": 812.5}],
}  # fmt: skip


def charges(total, stt, ipft=None):
    return {"charges_breakdown": {
        "total": total, "brokerage": 20.0, "taxes": {"gst": 3.6, "stt": stt, "stamp_duty": 2},
        "charges": {"transaction": 0.56, "clearing": 0, "ipft": ipft, "others": 0,
                    "sebi_turnover": 0.01, "demat_transaction": 18.5},
    }}  # fmt: skip


CHARGES = {"EQ": charges(40.0, 26), "FO": charges(154.23, 15)}
TRADES = {"EQ": [EQ_SALE], "FO": FO_ROWS}


def cache(made, segments=("EQ", "FO")) -> None:
    """The year's reports as the reader leaves them: complete, each row parsed by reports.py."""

    def put(tx):
        for segment in segments:
            rows = [reports.parse_trade(answered(raw)) for raw in TRADES[segment]]
            tx.report_put("trades", FY, segment, "ok", NOW, None,
                          {"rows": rows, "total_records": len(rows)}, NOW)  # fmt: skip
            payload = {"rows": [reports.parse_pnl_row(answered(r)) for r in PAIRS[segment]],
                       "trades_count": len(PAIRS[segment]),
                       "charges": reports.parse_charges(answered(CHARGES[segment]))}  # fmt: skip
            tx.report_put("pnl", FY, segment, "ok", NOW, None, payload, NOW)

    made.write(OWNER, put)


def gathered(made, member: int = 1):
    return made.read(OWNER, lambda tx: tax_adapter.inputs(tx, member, FY, TODAY, NOW))


def upstox_id(made) -> int:
    return made.read(OWNER, lambda tx: tx.rows("account", system=1)[0]["id"])


PE = ("NFO", "NIFTY 26000 PE", 20261103, 260_000_000, "PE")


def test_documented_rows_become_the_cores_trades_contracts_and_charges(made):
    cache(made)
    given, upstox = gathered(made), upstox_id(made)
    sale = next(t for t in given.trades if t.segment == "EQ")
    assert (sale.instrument, sale.side, sale.qty_u, sale.amount_paise) == (
        ISIN,
        "SELL",
        10 * U,
        2_600_000,
    )
    assert (sale.taxpayer, sale.account, sale.source) == (1, upstox, "upstox_history")
    assert {t.instrument for t in given.trades if t.segment == "FO"} == {
        ("NFO", "NIFTY 25900 CE", 20261103, 259_000_000, "CE"),  # the strike was a string
        PE,
    }
    # the purchase from before the history comes from Upstox's pair, at its own date and cost
    assert given.opening == (
        OpeningLot(1, upstox, ISIN, 20240110, 10 * U, 2_000_000, "upstox_pair"),
    )
    assert given.settlements == {PE: 125_000}  # the pair closing on the expiry day
    assert (
        given.charges["FO"]["total_paise"] == 15_423 and given.charges["FO"]["ipft_paise"] is None
    )
    assert given.charges_state == "ok"
    assert given.upstox_pnl == {"NIFTY 25900 CE": 162_500, "NIFTY 26000 PE": -243_750}
    assert [(s["segment"], s["state"]) for s in given.sources] == [
        ("EQ", "ok"), ("FO", "ok"), ("COM", "unavailable"), ("CD", "unavailable"),
        ("MF", "unavailable"),
    ]  # fmt: skip
    said = " ".join(given.missing)
    assert "commodity, currency and funds trades of FY 2026-27 are not read in full" in said
    assert "trades of FY 2025-26 are not read in full" in said


def test_the_core_works_the_year_out_from_them(made):
    """The adapter decides nothing: the pack's figures are the core's arithmetic on its inputs."""
    cache(made)
    pack = build_pack(gathered(made))
    (sale,) = pack["capital_gains"]["schedule"]
    assert sale["expenses_paise"] == 4_000 - 2_600  # the year's share charges without STT
    assert (sale["gain_paise"], sale["term"]) == (600_000 - 1_400, "long")
    business = pack["business"]["non_speculative"]
    # +1,625.00 on the call sold and bought back; the put bought at 3,250 settled at 812.50
    assert business["gross_paise"] == 162_500 - 243_750
    assert business["charges_paise"] == 15_423  # business counts every part, STT included
    assert pack["business"]["differs"] == []  # the core's figures agree with Upstox's own
    assert pack["inputs"]["missing"] and pack["running"] is True


def test_who_counts_by_what(made):
    """The Upstox account counts by its reports (a ledger entry on it is left out, so nothing
    counts twice), another account by its ledger, one set to none not at all; and a taxpayer is
    given only his own accounts."""
    cache(made)

    def setup(tx):
        other = tx.insert("member", {"name": "B"}, NOW)["id"]
        tab = tx.insert("tab", {"name": "T", "kind": "stocks", "icon": "chart", "colour": "brand"},
                        NOW)  # fmt: skip
        asset = tx.insert("asset", {"kind": "listed", "name": "X", "isin": KEY,
                                    "asset_class": "equity"}, NOW)  # fmt: skip
        made_ids = [tx.rows("account", system=1)[0]["id"]]
        for member, mode in ((1, "none"), (1, None), (other, None)):
            row = {"member_id": member, "name": "A", "kind": "demat", "tax_mode": mode}
            made_ids.append(tx.insert("account", row, NOW)["id"])
        for n, account in enumerate(made_ids, start=1):
            tx.insert("activity", {
                "tab_id": tab["id"], "seq": n, "kind": "BUY", "trade_date": 20260501,
                "account_id": account, "asset_id": asset["id"], "qty_u": U, "amount_paise": 10_000,
            }, NOW)  # fmt: skip
        return made_ids

    _, _, demat, theirs = made.write(OWNER, setup)
    mine, others = gathered(made), gathered(made, member=2)
    assert [t.account for t in mine.trades if t.source == "ledger"] == [demat]
    assert [s["kind"] for s in mine.sources].count("ledger") == 1
    assert [t.account for t in others.trades] == [theirs]
    assert [s["kind"] for s in others.sources] == ["ledger"]


def test_the_upstox_account_set_not_to_count_reads_no_report(made):
    cache(made)
    made.write(OWNER, lambda tx: tx.change("account", tx.rows("account", system=1)[0]["id"], 1,
                                           {"tax_mode": "none"}, NOW))  # fmt: skip
    given = gathered(made)
    assert given.trades == () and given.sources == () and given.missing == ()


def test_a_tender_the_owner_marked_and_one_the_history_lacks(made):
    cache(made)
    upstox = upstox_id(made)

    def mark(tx):
        for trade_id, day in (("50011", 20261027), (None, 20260715)):
            tx.insert("tax_trade_mark", {
                "member_id": 1, "account_id": upstox, "isin": ISIN, "day": day, "qty_u": 5 * U,
                "price_u": 30_000_000, "mark": "buyback", "trade_id": trade_id,
            }, NOW)  # fmt: skip

    made.write(OWNER, mark)
    tenders = [t for t in gathered(made).trades if t.kind_hint == "buyback"]
    assert [(t.day, t.amount_paise, t.source) for t in tenders] == [
        (20261027, 2_600_000, "upstox_history"),  # the history's own sale, by its trade id
        (20260715, 1_500_000, "owner"),  # 5 at 3,000 that the owner entered
    ]


def test_the_owners_own_purchase_of_a_share_wins_over_upstoxs_pair(made):
    cache(made)
    upstox = upstox_id(made)
    made.write(OWNER, lambda tx: tx.insert("tax_opening_lot", {
        "member_id": 1, "account_id": upstox, "isin": ISIN, "acquired_day": 20160310,
        "qty_u": 10 * U, "cost_paise": 1_500_000, "source": "owner",
    }, NOW))  # fmt: skip
    (lot,) = gathered(made).opening
    assert (lot.source, lot.acquired_day, lot.cost_paise) == ("owner", 20160310, 1_500_000)


def test_charges_of_a_segment_not_read_are_absent_never_zero(made):
    cache(made, segments=("FO",))
    sale = {"rows": [reports.parse_trade(answered(EQ_SALE))]}
    made.write(OWNER, lambda tx: tx.report_put("trades", FY, "EQ", "ok", NOW, None, sale, NOW))
    given = gathered(made)
    assert set(given.charges) == {"FO"} and given.charges_state == "partial"
    assert given.upstox_pnl is not None  # every derivative segment traded has its report


def test_the_settings_are_the_taxpayers_and_default_to_the_rule_rows():
    assert tax_adapter.settings_of(None).allocate_expenses is True
    chosen = tax_adapter.settings_of({"value": {"turnover_method": "abs_pnl_plus_sale_premium",
                                                "allocate_expenses": False}})  # fmt: skip
    assert (chosen.turnover_method, chosen.allocate_expenses) == ("abs_pnl_plus_sale_premium",
                                                                  False)  # fmt: skip


def test_a_row_that_names_no_share_or_contract_is_counted_and_left_out():
    rows = [reports.parse_trade(answered({**EQ_SALE, "isin": None})),
            reports.parse_trade(answered({**FO_ROWS[0], "expiry": ""}))]  # fmt: skip
    assert tax_adapter.upstox_trades([{"rows": rows}], 1, 1, {}) == ([], 2)


def test_a_future_is_keyed_as_fut_with_no_strike():
    raw = {**FO_ROWS[0], "option_type": None, "strike_price": 0, "scrip_name": "NIFTY FUT"}
    row = reports.parse_trade(answered(raw))
    assert tax_adapter.contract_of(row) == ("NFO", "NIFTY FUT", 20261103, 0, "FUT")


def test_a_commodity_settles_by_the_pairs_amount_not_by_its_price():
    contract = ("MCX", "CRUDEOIL FUT", 20261119, 0, "FUT")
    held = Trade(1, 1, 20261110, 1, "BUY", "COM", contract, U, 6_000_000, 6_000_000)
    pair = {"scrip_name": "CRUDEOIL FUT", "qty_u": U, "buy_date": 20261110,
            "sell_date": 20261119, "sell_average_u": 6_100_000, "buy_average_u": 6_000_000,
            "sell_amount_paise": 6_100_000, "buy_amount_paise": 6_000_000}  # fmt: skip
    prices, closing = tax_adapter.settlements_of([pair], [held])
    assert prices == {}
    assert [(t.side, t.day, t.amount_paise, t.kind_hint) for t in closing] == [
        ("SELL", 20261119, 6_100_000, "expiry")
    ]


def test_a_fund_counts_as_equity_only_when_tagged_so_and_a_manual_asset_never():
    assert tax_adapter.tax_class({"kind": "mf", "asset_class": "mf_equity"}) == "equity"
    assert tax_adapter.tax_class({"kind": "mf", "asset_class": "mf_debt"}) == "manual"
    assert tax_adapter.tax_class({"kind": "listed", "asset_class": "gold"}) == "manual"
    assert tax_adapter.tax_class({"kind": "manual", "asset_class": "real_estate"}) == "manual"
    assert tax_adapter.tax_class({"kind": "liability", "asset_class": "other"}) == "liability"
    assert tax_adapter.tax_class(None) is None


# ---- the ledger agreement (design p7 11.2) -----------------------------------------------------
ACCOUNT, ASSET, TAB = 7, 3, 1
NO_STT = {"brokerage_paise": 0, "stt_paise": 0}
ASSETS = {ASSET: {"id": ASSET, "kind": "listed", "name": "X Ltd", "isin": KEY,
                  "asset_class": "equity"}}  # fmt: skip


def entry(n, kind, day, **more):
    row = {"id": n, "tab_id": TAB, "seq": n, "status": "posted", "kind": kind, "trade_date": day,
           "account_id": ACCOUNT, "to_account_id": None, "asset_id": ASSET, "qty_u": None,
           "price_u": None, "amount_paise": None, "charges_paise": 0, "charges_detail": None,
           "lot_date": None, "ratio_num": None, "ratio_den": None}  # fmt: skip
    return {**row, **more}


TAB_ENTRIES = [
    entry(1, "TRANSFER", 20240603, account_id=None, to_account_id=ACCOUNT, qty_u=20 * U,
          amount_paise=4_000_000, lot_date=20160310),  # in from outside, bought in 2016
    entry(2, "BUY", 20240701, qty_u=10 * U, amount_paise=300_000, charges_paise=2_000,
          charges_detail={**NO_STT, "brokerage_paise": 2_000}),
    entry(3, "SPLIT", 20250105, account_id=None, ratio_num=5, ratio_den=1),
    entry(4, "BONUS", 20250210, qty_u=150 * U),
    entry(5, "RIGHTS", 20250301, qty_u=15 * U, amount_paise=120_001),
    entry(6, "TRANSFER", 20250601, account_id=None, to_account_id=ACCOUNT, qty_u=50 * U,
          amount_paise=500_000, lot_date=20160310),  # after the split, entered in today's units
    entry(7, "SELL", 20250820, qty_u=120 * U, amount_paise=1_080_000, charges_paise=3_000,
          charges_detail={**NO_STT, "brokerage_paise": 3_000}),
    entry(8, "SELL", 20260505, qty_u=100 * U, amount_paise=950_000),
    entry(9, "SELL", 20260506, qty_u=33 * U, price_u=1_000_000),  # amount from quantity x price
    entry(10, "SELL", 20260601, qty_u=104 * U, amount_paise=1_000_003),  # part of a lot, odd paise
]  # fmt: skip


def test_the_tax_lots_and_the_ledger_agree_line_for_line():
    position = replay([Entry.of(row) for row in TAB_ENTRIES]).position(ACCOUNT, ASSET)
    trades, opening, actions, missing = tax_adapter.ledger_inputs(
        TAB_ENTRIES, {ACCOUNT: {"id": ACCOUNT}}, ASSETS, {}, 1, 0
    )
    assert missing == []
    book = lots.replay(trades, opening, actions)
    assert [(lot.acquired_day, lot.qty_u, lot.cost_paise) for lot in book.open_lots] == [
        (lot.day, lot.qty_u, lot.cost_paise) for lot in position.lots
    ]
    realised = sum(d.sale_paise - d.cost_paise - d.charges_paise for d in book.disposals)
    assert realised == position.realised_paise == -1_500_997
    assert book.intraday == () and book.notes == ()
    # the transfer after the split is undone to its 2016 units for the core, which splits it again
    assert sorted(lot.qty_u for lot in opening if lot.source == "ledger") == [10 * U, 20 * U]


def test_charges_count_without_stt_and_what_is_not_followed_is_said():
    rows = [
        entry(1, "BUY", 20250101, qty_u=U, amount_paise=100_000, charges_paise=3_000,
              charges_detail={**NO_STT, "brokerage_paise": 2_000, "stt_paise": 1_000}),
        entry(2, "BUY", 20250102, qty_u=U, amount_paise=100_000, charges_paise=20),
        entry(3, "TRANSFER", 20250201, to_account_id=8, qty_u=U),
    ]  # fmt: skip
    trades, _, _, missing = tax_adapter.ledger_inputs(rows, {ACCOUNT: {}}, ASSETS, {}, 1, 0)
    # STT is no expense for capital gains; charges with no breakdown are unknown, never all of it
    assert [t.charges_ex_stt_paise for t in trades] == [2_000, None]
    assert any("A transfer of X Ltd on 1 Feb 2025 is not followed" in line for line in missing)
    assert any(line.startswith("1 entry has charges without a breakdown") for line in missing)
