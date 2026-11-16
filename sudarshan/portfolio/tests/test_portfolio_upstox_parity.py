"""The Upstox tab's parity views (M7.15): allocation by class and sector, and the return from the
trade history and the closing records.

The vectors (``vectors/upstox_parity.json``) are worked out by hand and run against the phone's
copy of the bars too (``upstox/allocation.test.ts``). The ops run through the sealed API against
a fake Upstox; nothing here leaves the machine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from portfolio_ts import interfaces, members
from test_portfolio_closed_world import MASK, answer_misses
from test_portfolio_import_ops import NOW, OWNER
from test_portfolio_purposes import fake as fake  # noqa: F401  (the fake Upstox fixture)
from test_portfolio_purposes import scenario

from sudarshan.portfolio import serialize, upstox_views
from sudarshan.portfolio.core import kinds, performance, upstoxbook
from sudarshan.portfolio.core.allocation import BOOK_BY, BookLine, allocate_book, allocate_book_all
from sudarshan.portfolio.core.money import pct

FILE = json.loads(
    (Path(__file__).parent / "vectors" / "upstox_parity.json").read_text(encoding="utf-8")
)
REPO = Path(__file__).resolve().parents[3]
TYPES_TS = REPO / "frontend" / "src" / "sudarshan" / "portfolio" / "types.ts"
TCS, SMALLCO = "INE467B01029", "INE000Z01011"


def _trades(rows):
    return [
        upstoxbook.Trade(d, isin, side == "BUY", qty, paise) for d, isin, side, qty, paise in rows
    ]


# ---- the bars (vectors) ----------------------------------------------------------------------
@pytest.mark.parametrize("case", FILE["allocation"], ids=lambda c: c["id"])
def test_the_book_s_bars_are_the_vectors(case):
    lines = [BookLine(*line) for line in case["lines"]]
    assert allocate_book_all(lines) == case["expect"]
    # The order the holdings arrive in never changes a bar.
    assert allocate_book_all(reversed(lines)) == case["expect"]


def test_the_bars_add_up_to_the_book_and_nothing_is_guessed():
    lines = [BookLine("a", 700, "equity", None), BookLine("b", 300, None, None)]
    for by in BOOK_BY:
        assert sum(bar.value_paise for bar in allocate_book(by, lines)) == 1000
    loose = allocate_book("class", lines)[1]
    assert (loose.key, loose.label, loose.value_paise) == ("", "Not classified", 300)
    with pytest.raises(ValueError):
        allocate_book("mcap", lines)
    with pytest.raises(ValueError):
        allocate_book("class", [BookLine("a", 1, "shares", None)])


# ---- the counted shares (vectors) ---------------------------------------------------------------
@pytest.mark.parametrize("case", FILE["counted"], ids=lambda c: c["id"])
def test_the_counted_shares_and_their_money_are_the_vectors(case):
    got = upstoxbook.counted(
        _trades(case["trades"]),
        [upstoxbook.Held(*line) for line in case["held"]],
        [upstoxbook.Action(*a) for a in case["actions"]],
        case["through"],
    )
    want = case["expect"]
    assert [[f.day, f.in_paise, f.out_paise] for f in got.flows] == want["flows"]
    assert got.value_paise == want["value_paise"]
    assert [list(u) for u in got.uncounted] == want["uncounted"]
    assert [list(g) for g in got.gone] == want["gone"]
    assert (got.left_out_paise, got.unmatched) == (want["left_out_paise"], want["unmatched"])
    assert all(f.fee_paise == 0 for f in got.flows)
    if "summary" in case:
        said = case["summary"]
        out = performance.summary(got.flows, got.value_paise, said["valuation_day"])
        assert out["xirr"]["state"] == said["xirr_state"]
        assert out["xirr"]["pct"] == said["xirr_pct"]
        assert out["absolute_return_pct"] == said["absolute_return_pct"]
        assert out["total_gain_paise"] == said["total_gain_paise"]


def test_a_share_held_from_before_the_history_is_never_cost_nothing():
    """The mutation this lane exists to stop: 30 held and 10 bought must not read as 30 bought
    for the price of 10."""
    case = next(c for c in FILE["counted"] if c["id"].startswith("C2"))
    got = upstoxbook.counted(
        _trades(case["trades"]), [upstoxbook.Held(*line) for line in case["held"]]
    )
    whole = sum(u.value_paise for u in got.uncounted) + got.value_paise
    assert whole == 150000 and got.value_paise == 50000
    out = performance.summary(got.flows, got.value_paise, 20251001)
    assert out["absolute_return_pct"] == "25.0000"  # not 275%, which cost 0 would give


def test_the_units_held_from_before_are_worked_back_from_the_holdings():
    days = upstoxbook._days(_trades([[20250501, "X", "BUY", 6_000_000, 1], [20250901, "X", "SELL", 8_000_000, 1]]))["X"]  # fmt: skip
    assert upstoxbook.opening_units(days, [], 4_000_000) == 6_000_000
    assert upstoxbook.opening_units(days, [], 0) == 2_000_000  # the sale needed 2 more
    assert upstoxbook.opening_units([], [], 0) == 0
    split = [upstoxbook.Action(20250601, "X", "SPLIT", 2, 1)]
    # 6 bought become 12, 8 sold: 4 known; 10 held needs 6 more now, which were 3 before.
    assert upstoxbook.opening_units(days, split, 10_000_000) == 3_000_000


# ---- the daily series (vectors) -----------------------------------------------------------------
@pytest.mark.parametrize("case", FILE["points"], ids=lambda c: c["id"])
def test_the_series_and_its_time_weighted_return_are_the_vectors(case):
    points = upstoxbook.points([tuple(r) for r in case["records"]], _trades(case["trades"]))
    got = [[p.day, p.value_paise, p.in_paise, p.out_paise] for p in points]
    assert got == case["expect"]
    twr = performance.twr(points)
    assert (twr.pct, twr.start_day) == (case["twr_pct"], case["twr_start"])


def test_the_record_valued_is_from_before_the_read_and_from_a_day_without_a_delivery_trade():
    trades = _trades(
        [
            [20261022, "X", "BUY", 1_000_000, 1],
            [20261021, "Y", "BUY", 1_000_000, 1],
            [20261021, "Y", "SELL", 1_000_000, 1],
        ]
    )
    days = [20261020, 20261021, 20261022, 20261023]
    assert upstoxbook.last_quiet_day(days, trades, 20261023) == 20261021  # a round trip is quiet
    assert upstoxbook.last_quiet_day(days, trades, 20261026) == 20261023
    assert upstoxbook.last_quiet_day(days, trades, 20261020) is None
    assert upstoxbook.points([], trades) == []


# ---- the contract ----------------------------------------------------------------------------
def test_the_answers_have_the_fields_the_phone_reads():
    found = interfaces(TYPES_TS.read_text(encoding="utf-8"))
    part = {"state": "ok", "read_at": 1, "note": None, "data": None}
    assert list(upstox_views.allocation(None, part)) == members(found["UpstoxAllocation"])
    assert list(upstox_views._waiting("x")) == members(found["UpstoxPerformance"])
    figures = [*performance.summary([], 0, 20261023), "twr", "benchmarks"]
    assert figures == members(found["UpstoxReturn"])
    assert members(found["UpstoxUncounted"]) == ["isin", "symbol", "name", "qty_u", "value_paise"]
    assert members(found["UpstoxHoldingClass"]) == ["instrument_key", "asset_class", "sector"]


def test_neither_op_is_a_family_op_and_the_masks_hide_their_figures_anyway():
    for name in ("upstox_allocation", "upstox_performance"):
        assert name in kinds.OP_NAMES and name not in kinds.FAMILY_OPS
        assert name not in kinds.JOURNAL_OPS
    figures = (
        "allocation", "note", "words", "qty_u", "value_paise", "uncounted_value_paise",
        "money_in_paise", "money_out_paise", "net_put_in_paise", "total_gain_paise", "fee_paise",
        "absolute_return_pct", "xirr", "twr", "benchmarks",
    )  # fmt: skip
    for key in figures:
        assert serialize.hidden(key, MASK), key


def test_every_sentence_is_a_trader_s():
    from test_portfolio_contract import _A_STATUS_CODE, _FORBIDDEN_WORDS

    for sentence in upstox_views.WORDS.values():
        assert sentence.isascii() and not _FORBIDDEN_WORDS.search(sentence), sentence
        assert not _A_STATUS_CODE.search(sentence), sentence


# ---- the ops, through the sealed API ----------------------------------------------------------------
@scenario
async def test_the_allocation_op_classifies_from_the_owner_s_assets_and_says_what_is_not(rig, fake):
    rig.book.asset_create(
        OWNER, "s-2", "listed", now=NOW, exchange="NSE", symbol="TCS", isin=TCS,
        name="Tata Consultancy Services", sector="IT",
    )  # fmt: skip
    data = await rig.owner("upstox_allocation")
    assert data["state"] == "ok" and data["read_at"] == NOW
    assert data["classes"] == [
        {"instrument_key": f"NSE_EQ|{TCS}", "asset_class": "equity", "sector": "IT"},
        {"instrument_key": f"NSE_EQ|{SMALLCO}", "asset_class": None, "sector": None},
    ]
    # 10 at 3,500.55 and 3 at 90: 35,005.50 and 270.00.
    tcs, small = 3_500_550, 27_000
    assert data["allocation"]["class"] == [
        {"key": "equity", "label": "Equity", "value_paise": tcs,
         "pct": pct(tcs, tcs + small), "holdings": 1},
        {"key": "", "label": "Not classified", "value_paise": small,
         "pct": pct(small, tcs + small), "holdings": 1},
    ]  # fmt: skip
    assert [bar["key"] for bar in data["allocation"]["sector"]] == ["IT", ""]
    assert data["allocation"]["sector"][1]["label"] == "No sector set"
    assert data["note"].startswith("1 of your holdings has no class or sector yet.")
    assert answer_misses(data) == set()
    # The page's own holdings read and this one are one read of Upstox.
    await rig.owner("upstox_book", parts=["holdings"])
    assert fake.count("holdings") == 1


@scenario
async def test_holdings_that_cannot_be_read_say_so_and_show_no_bar(rig, fake):
    fake.status["holdings"] = 500
    data = await rig.owner("upstox_allocation")
    assert data["state"] == "unavailable" and data["allocation"] is None and data["classes"] == []
    assert "Upstox" in data["note"] and "500" not in data["note"]


def _trade(day, isin, side, shares, rupees, symbol="TCS"):
    return {
        "trade_id": f"{day}-{side}", "trade_date": day, "exchange": "NSE", "segment": "EQ",
        "transaction_type": side, "scrip_name": symbol, "symbol": symbol, "isin": isin,
        "instrument_token": None, "option_type": None, "strike_price_u": None, "expiry": None,
        "qty_u": shares * 1_000_000, "price_u": rupees * 10_000 // shares,
        "amount_paise": rupees * 100,
    }  # fmt: skip


def _fill(rig, trades, records, years=(2026, 2025, 2024)):
    """The trade history as the report reader keeps it (read just now, so nothing is read from
    Upstox again) and the closing records as the closing job writes them."""

    def write(tx):
        for fy in years:
            rows = [t for t in trades if (t["trade_date"] - 400) // 10000 == fy]
            tx.report_put("trades", fy, "EQ", "ok", NOW, None, {"rows": rows}, NOW)
        for day, lines in records.items():
            head = {"read_at": NOW - 1, "holdings_value_paise": 1, "holdings_cost_paise": 1,
                    "positions_pnl_paise": 0, "funds": None}  # fmt: skip
            tx.upstox_eod_put(
                day,
                head,
                [{"instrument_key": f"NSE_EQ|{isin}", "asset_id": None, "qty": shares * 1_000_000,
                  "t1_qty": 0, "avg_price_u": None, "price_u": rupees * 10_000, "basis": "last"}
                 for isin, shares, rupees in lines],
                NOW,
            )  # fmt: skip
        tx.index_close_put("nifty50", 20251023, 250_000_000, "candle")
        tx.index_close_put("nifty50", 20261023, 262_500_000, "candle")

    rig.svc.store.write(OWNER, write)


RECORDS = {
    20261022: [(TCS, 10, 3000), (SMALLCO, 3, 90)],
    20261023: [(TCS, 10, 3300), (SMALLCO, 3, 90)],
}


@scenario
async def test_the_performance_op_gives_the_return_of_the_counted_shares(rig, fake):
    """10 TCS bought for 30,000 on 23 Oct 2025 are worth 33,000 at the record of 23 Oct 2026:
    10% in 365 days. 3 SMALLCO have no purchase in the history: listed, not counted."""
    _fill(rig, [_trade(20251023, TCS, "BUY", 10, 30000)], RECORDS)
    data = await rig.owner("upstox_performance")
    assert data["state"] == "ok" and data["note"] is None
    assert data["from"] == 20240401 and data["read_at"] == NOW - 1
    got = data["data"]
    assert got["valuation_day"] == 20261023 and got["first_flow_day"] == 20251023
    assert (got["value_paise"], got["money_in_paise"], got["money_out_paise"]) == (3_300_000, 3_000_000, 0)  # fmt: skip
    assert got["total_gain_paise"] == 300_000 and got["absolute_return_pct"] == "10.0000"
    assert got["xirr"] == {"state": "shown", "pct": "10.0000", "from_day": None, "words": None}
    # All the holdings, from the first record: 30,270 to 33,270.
    assert got["twr"]["start_day"] == 20261022
    assert got["twr"]["pct"] == pct(300_000, 3_027_000)
    nifty50, nifty500 = got["benchmarks"]
    assert (nifty50["value_paise"], nifty50["xirr_pct"]) == (3_150_000, "5.0000")
    assert nifty500["value_paise"] is None and nifty500["words"][-1].startswith("NIFTY 500")
    assert data["uncounted"] == [
        {"isin": SMALLCO, "symbol": None, "name": SMALLCO, "qty_u": 3_000_000, "value_paise": 27_000}
    ]  # fmt: skip
    assert data["uncounted_value_paise"] == 27_000
    assert data["words"][0].startswith("Shares and ETFs only, from Upstox's trade history since 1 Apr 2024.")  # fmt: skip
    assert any(w.startswith("1 holding worth") and "not counted" in w for w in data["words"])
    assert answer_misses(data) == set()
    assert fake.queries == []  # the cache was fresh: Upstox was not asked for anything


@scenario
async def test_a_record_of_the_read_day_or_of_a_delivery_day_is_not_valued(rig):
    """The newest record is of a day TCS was bought on, so the one before it is valued, and the
    purchase waits: it is neither money in nor value yet."""
    trades = [_trade(20251023, TCS, "BUY", 10, 30000), _trade(20261023, TCS, "BUY", 1, 3300)]
    _fill(rig, trades, RECORDS)
    data = await rig.owner("upstox_performance")
    got = data["data"]
    assert got["valuation_day"] == 20261022 and got["money_in_paise"] == 3_000_000
    assert got["value_paise"] == 3_000_000 and got["xirr"]["state"] == "later"
    assert got["twr"]["pct"] is None and got["twr"]["words"]


@scenario
async def test_without_a_record_or_the_year_s_trades_it_waits_in_words(rig):
    _fill(rig, [_trade(20251023, TCS, "BUY", 10, 30000)], {})
    data = await rig.owner("upstox_performance")
    assert data["state"] == "waiting" and data["data"] is None
    assert data["note"] == upstox_views.WORDS["no_record"] and data["uncounted"] == []
    reading = [{"fy": 2026, "state": "reading", "note": "Reading from Upstox.", "rows": []}]
    assert upstox_views.performance_report(None, reading)["note"] == "Reading from Upstox."
    silent = [{"fy": 2026, "state": "unavailable", "note": None, "rows": []}]
    assert upstox_views.performance_report(None, silent)["note"] == upstox_views.WORDS["waiting"]


@scenario
async def test_an_earlier_year_not_read_shortens_the_history_and_says_so(rig):
    _fill(rig, [_trade(20251023, TCS, "BUY", 10, 30000)], RECORDS, years=(2026, 2025))

    def unread(tx):
        tx.report_put("trades", 2024, "EQ", "partial", NOW, "More pages follow.", {"rows": []}, NOW)

    rig.svc.store.write(OWNER, unread)

    def read(tx):
        shown = [
            {"fy": fy, **{k: tx.report_get("trades", fy, "EQ")[k] for k in ("state", "read_at")},
             "rows": tx.report_get("trades", fy, "EQ")["payload"]["rows"]}
            for fy in (2026, 2025, 2024)
        ]  # fmt: skip
        return upstox_views.performance_report(tx, shown)

    data = rig.svc.store.read(OWNER, read)
    assert data["from"] == 20250401
    assert any(w.startswith("FY 2024-25 is not read from Upstox yet") for w in data["words"])


@scenario
async def test_a_family_member_is_given_neither_view(rig):
    await rig.grant("7", [rig.tab["id"], rig.upstox_tab])
    for name in ("upstox_allocation", "upstox_performance"):
        status, body = await rig.family("7", name)
        assert status == 403 and body["status"] == "error", name
        assert "allocation" not in body and "data" not in body
