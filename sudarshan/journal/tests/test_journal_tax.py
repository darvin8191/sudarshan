"""The tax pack's kinds and years (review H1): shares bought and sold the same day are intraday
whatever the product, the 12-month line goes by calendar dates (correct across 29 Feb), and a
delivery position sold in parts books each sale in the year of that sale, matched first in, first
out. Vectors ``tax.json`` (recomputed by hand), then the pack itself on the journal's rig."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest
from render_pdfread import read_pdf
from test_journal_capture import files as files  # noqa: F401  (a fixture)
from test_journal_capture import ist_ms
from test_journal_capture import rig as rig  # noqa: F401  (a fixture)
from test_journal_reports import call

from sudarshan.journal import periods
from sudarshan.journal.book import Facts, replay_tx
from sudarshan.journal.core import lots
from sudarshan.journal.core.money import u_to_paise
from sudarshan.journal.render import tables
from sudarshan.journal.store import Scope

OWNER = Scope.owner()
DATA = json.loads((Path(__file__).parent / "vectors" / "tax.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("vec", DATA["kinds"], ids=[v["id"] for v in DATA["kinds"]])
def test_a_trade_is_listed_under_its_kind_by_its_dates(vec):
    assert tables.kind_of_trade(vec["trip"]) == vec["kind"], vec["note"]


@pytest.mark.parametrize("vec", DATA["lots"], ids=[v["id"] for v in DATA["lots"]])
def test_each_sale_is_booked_in_its_own_year_and_holding_period(vec):
    charges = {(day, action): paise for day, action, paise in vec["charges"]}
    found = lots.lots_of(vec["legs"], vec["side"], charges)
    for year in vec["years"]:
        got = tables.lot_entries(vec["exchange"], found, year["start"], year["end"])
        assert got == year["entries"], (vec["id"], year["start"], vec["note"])


def test_the_lots_of_a_trade_add_up_to_its_gross():
    """L2's two lots are the trade's 1,050.00, split by date only."""
    vec = next(v for v in DATA["lots"] if v["id"] == "L2")
    found = lots.lots_of(vec["legs"], "long")
    assert sum(lot.qty for lot in found) == 120
    assert lots.u_paise(sum((lot.gross_u for lot in found), start=Fraction(0))) == 105_000


def test_a_short_is_matched_the_same_way_mirrored():
    legs = [{"day": 20261102, "action": "SELL", "qty": 65, "price_u": 1_000_000},
            {"day": 20261104, "action": "BUY", "qty": 65, "price_u": 900_000}]  # fmt: skip
    (lot,) = lots.lots_of(legs, "short")
    assert (lot.open_day, lot.close_day, lots.u_paise(lot.gross_u)) == (20261102, 20261104, 65_000)


def test_twelve_months_on_is_a_calendar_date():
    assert lots.plus_months(20270310, 12) == 20280310
    assert lots.plus_months(20280229, 12) == 20290228
    assert lots.plus_months(20261031, 4) == 20270228
    assert not lots.held_over_12_months(20270310, 20280309)
    assert not lots.held_over_12_months(20270310, 20280310)
    assert lots.held_over_12_months(20270310, 20280311)


# ---- the pack, on the rig --------------------------------------------------------------------
def fill(trade_id, action, qty, price_rs, day):
    at = ist_ms(day, "11:00:00")
    price_u = int(price_rs * 10_000)
    return {"mode": "live", "day": day, "trade_id": trade_id, "order_id": f"o-{trade_id}",
            "exchange": "NSE", "symbol": "ACME", "instrument_key": "NSE_EQ|INE000A00001",
            "product": "CNC", "action": action, "qty": qty, "price_u": price_u,
            "value_paise": u_to_paise(qty * price_u), "exch_ts_ms": at, "order_ts_ms": None,
            "source": "stream_read"}  # fmt: skip


def held_across_the_year_end(store):
    """L1 through the store: 100 bought in June 2026, 50 sold in February, 50 in May 2027."""
    for row in (fill("b1", "BUY", 100, 1000, 20260615), fill("s1", "SELL", 50, 1100, 20270215),
                fill("s2", "SELL", 50, 1200, 20270517)):  # fmt: skip
        store.write(OWNER, lambda tx, row=row: tx.fills_upsert([row], 1))
        store.write(OWNER, lambda tx, row=row: replay_tx(tx, "live", {row["day"]}, 1, Facts()))


def entries_of(rig, key, today):
    span = periods.resolve_span("fy", key, today)
    found = rig.svc.store.read(OWNER, periods._read(span, 2000 + int(key[2:4])))
    trips, sold = found[0], found[-1]
    tags = {}
    rows = [periods_for_core(t, tags) for t in trips]
    return periods.tax_entries(span, rows, sold)


def periods_for_core(trip, tags):
    from sudarshan.journal.ops.analytics_ops import for_core

    return for_core(trip, tags, {}, None)


def test_a_delivery_position_sold_in_parts_books_each_sale_in_its_own_year(rig):
    held_across_the_year_end(rig.svc.store)
    (trip,) = rig.svc.store.read(OWNER, lambda tx: tx.rows("round_trip"))
    assert (trip["status"], trip["close_day"]) == ("closed", 20270517)  # one trade, as paired
    first = entries_of(rig, "FY2627", 20270402)
    assert [(e["kind"], e["gross_paise"]) for e in first] == [
        ("Equity delivery, held up to 12 months", 500_000),
    ]  # the February sale, in its own year (today: missing from it)
    second = entries_of(rig, "FY2728", 20280402)
    assert [(e["kind"], e["gross_paise"]) for e in second] == [
        ("Equity delivery, held up to 12 months", 1_000_000),
    ]  # only the May sale (today: the whole gain, 15,000.00, lands here)
    charges = sum(e["charges_paise"] for e in first + second)
    assert charges == trip["charges_est_paise"]  # every order's charges, once, in some year


def test_the_tax_pack_says_what_it_leaves_out_and_how_its_kinds_are_made(rig):
    held_across_the_year_end(rig.svc.store)
    rig.now = ist_ms(20270402, "09:00:00")
    call(rig, "report_build", kind="fy", period="FY2627")
    row = rig.svc.store.read(OWNER, lambda tx: tx.rows("report", kind="fy"))[0]
    blob = rig.svc.store.read(OWNER, lambda tx: tx.report_bytes(row["id"]))
    text = "\n".join(ln for page in read_pdf(blob).pages for ln in page.lines)
    assert "What this paper leaves out" in text
    assert "Charges are the journal's estimates" in text
    assert "first in, first out" in text
    assert "Equity delivery, held up to 12 months" in text


def test_a_reversal_orders_charges_are_booked_once_across_its_two_trades(rig):
    """The tax pack's sales take the same share of a shared order as the trades (review M1)."""
    rows = [
        {**fill("r1", "BUY", 65, 100, 20260615), "exchange": "NFO",
         "symbol": "NIFTY30JUL2625000CE", "product": "NRML", "order_id": "A"},
        {**fill("r2", "SELL", 130, 104, 20260701), "exchange": "NFO",
         "symbol": "NIFTY30JUL2625000CE", "product": "NRML", "order_id": "B"},
        {**fill("r3", "BUY", 65, 103, 20260710), "exchange": "NFO",
         "symbol": "NIFTY30JUL2625000CE", "product": "NRML", "order_id": "C"},
    ]  # fmt: skip
    store = rig.svc.store
    for row in rows:
        store.write(OWNER, lambda tx, row=row: tx.fills_upsert([row], 1))
        store.write(OWNER, lambda tx, row=row: replay_tx(tx, "live", {row["day"]}, 1, Facts()))
    trips = store.read(OWNER, lambda tx: tx.rows("round_trip", order="opened_ms"))
    assert [t["status"] for t in trips] == ["closed", "closed"]
    entries = entries_of(rig, "FY2627", 20270402)
    assert len(entries) == 2
    assert sum(e["charges_paise"] for e in entries) == sum(t["charges_est_paise"] for t in trips)
