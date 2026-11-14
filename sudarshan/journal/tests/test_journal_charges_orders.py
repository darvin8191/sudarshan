"""Charges across trades (review M1): an order shared by two trades (a reversal) pays one
brokerage between them and the day's trades add up to the day's estimate; Upstox's history,
which names no order, is taken as one order a side and day; a segment the rate card does not
cover says so instead of 0.00, on the trade, on the sheet, in the reports and in the analytics'
marker. Through the store, the ops and the journal's rig; every value is synthetic."""

from __future__ import annotations

import sqlite3
from fractions import Fraction

import pytest
from test_journal_capture import files as files  # noqa: F401  (a fixture)
from test_journal_capture import ist_ms, scenario
from test_journal_capture import rig as rig  # noqa: F401  (a fixture)
from test_journal_jobs import history_row
from test_journal_reports import call

from sudarshan.journal import migrations, store
from sudarshan.journal.book import Facts, replay_tx
from sudarshan.journal.core import charges, metrics
from sudarshan.journal.core.money import u_to_paise
from sudarshan.journal.jobs import catchup, day
from sudarshan.journal.render import tables
from sudarshan.journal.store import JournalStore, Scope

OWNER = Scope.owner()
NOW = 1_794_300_000_000
DAY = 20261126
OPTION = "NIFTY01DEC2625000CE"


def fill(trade_id, order_id, action, qty, price_rs, clock, **more):
    price_u = int(price_rs * 10_000)
    row = {"mode": "live", "day": DAY, "trade_id": trade_id, "order_id": order_id,
           "exchange": "NFO", "symbol": OPTION, "instrument_key": "NSE_FO|70001",
           "product": "MIS", "action": action, "qty": qty, "price_u": price_u,
           "value_paise": u_to_paise(qty * price_u), "exch_ts_ms": ist_ms(DAY, clock),
           "order_ts_ms": None, "source": "stream_read"}  # fmt: skip
    return {**row, **more}


@pytest.fixture
def made(tmp_path):
    opened = JournalStore(tmp_path / store.DB_FILENAME, now=NOW)
    yield opened
    opened.close()
    store.stop()


def put(made, rows, days=(DAY,)):
    made.write(OWNER, lambda tx: tx.fills_upsert(rows, NOW))
    made.write(OWNER, lambda tx: replay_tx(tx, "live", set(days), NOW, Facts()))
    return made.read(OWNER, lambda tx: tx.rows("round_trip", order="opened_ms,id"))


REVERSAL = [
    fill("1", "A", "BUY", 65, 100, "10:00:00"),
    fill("2", "B", "SELL", 130, 104, "10:05:00"),
    fill("3", "C", "BUY", 65, 103, "10:09:00"),
]


def test_a_reversal_order_pays_one_brokerage_and_the_trades_add_up_to_the_day(made):
    """The review's case: BUY 65, SELL 130 in one order, BUY 65. Before, the two trades' charges
    came to Rs 173.52 against the day's Rs 138.12: order B's Rs 30 and its GST were in both."""
    first, second = put(made, REVERSAL)
    day_total = day.day_charges(REVERSAL, {}, "plus")
    assert day_total == 13_812
    assert first["charges_est_paise"] + second["charges_est_paise"] == day_total
    assert (first["side"], second["side"]) == ("long", "short")


def test_the_trade_sheet_shows_each_trades_share_of_the_shared_order(rig):
    first, second = put(rig.svc.store, REVERSAL)
    sheets = [call(rig, "trip_get", id=trip["id"]) for trip in (first, second)]
    brokerage = [s["charges"]["parts"]["brokerage_paise"] for s in sheets]
    assert brokerage == [4_500, 4_500]  # A's 30 and half of B's; the other half and C's 30
    totals = [s["charges"]["parts"]["total_paise"] for s in sheets]
    assert totals == [first["charges_est_paise"], second["charges_est_paise"]]
    # the shared fill, as executed, with this trade's part of it (the phone's review M3)
    shared = [next(f for f in s["fills"] if f["trade_id"] == "2") for s in sheets]
    assert [(f["qty"], f["trip_qty"]) for f in shared] == [(130, 65), (130, 65)]
    whole = next(f for f in sheets[0]["fills"] if f["trade_id"] == "1")
    assert (whole["qty"], whole["trip_qty"]) == (65, 65)


def test_an_order_filled_in_parts_across_two_trades_still_adds_up(made):
    rows = [
        fill("1", "A", "BUY", 65, 100, "10:00:00"),
        fill("2", "B", "SELL", 30, 104, "10:05:00"),
        fill("3", "B", "SELL", 100, 104.5, "10:05:01"),  # the same order, a second fill
        fill("4", "C", "BUY", 65, 103, "10:09:00"),
    ]
    trips = put(made, rows)
    assert sum(t["charges_est_paise"] for t in trips) == day.day_charges(rows, {}, "plus")


def test_shares_are_cut_at_rounded_points_so_they_always_add_up():
    parts = {"brokerage": 3000, "stt": 2028, "exchange": 480, "sebi": 1, "stamp": 0, "gst": 627,
             "total": 6136}  # fmt: skip
    one = charges.allocate(parts, 0, 65, 130)
    two = charges.allocate(parts, 65, 65, 130)
    for key in charges.PARTS:
        assert one[key] + two[key] == parts[key], key
    assert one["sebi"] + two["sebi"] == 1  # never 1 + 1


def test_a_three_trade_history_day_pays_one_brokerage_a_side(rig):
    fri = 20261106
    rig.answer_trades([])
    rig.portfolio.answers["historical_trades"] = {
        "state": "ok", "read_at": 1, "note": None, "total_pages": 1, "total_records": 3,
        "rows": [
            history_row("h1", fri, "BUY", qty=65),
            history_row("h2", fri, "BUY", qty=65, price_u=1_002_000),
            history_row("h3", fri, "SELL", qty=130, price_u=1_010_000),
        ],
    }  # fmt: skip
    rig.portfolio.answers["pnl_report"] = {"state": "ok", "read_at": 1, "rows": [],
                                           "page_size": 5000, "charges": {}}  # fmt: skip
    rig.now = ist_ms(20261109, "22:00:00")
    scenario(rig, lambda: catchup.catch_up(rig.svc, 20261109, rig.now, False))
    (trip,) = rig.svc.store.read(OWNER, lambda tx: tx.rows("round_trip", open_day=fri))
    sheet = call(rig, "trip_get", id=trip["id"])
    assert sheet["charges"]["orders"] == 2  # was 3: each history trade an order of its own
    assert sheet["charges"]["parts"]["brokerage_paise"] == 2 * 3_000
    assert {f["source"] for f in sheet["fills"]} == {"history"}


def test_a_bse_trade_says_its_charges_are_not_estimated(made):
    rows = [
        fill("1", "A", "BUY", 10, 100, "10:00:00", exchange="BSE", symbol="ACME",
             instrument_key="BSE_EQ|INE000A00001", product="CNC"),
        fill("2", "B", "SELL", 10, 110, "11:00:00", exchange="BSE", symbol="ACME",
             instrument_key="BSE_EQ|INE000A00001", product="CNC"),
    ]  # fmt: skip
    (trip,) = put(made, rows)
    assert trip["charges_modelled"] is False and trip["charges_est_paise"] == 0
    assert trip["net_paise"] == trip["gross_paise"] == 10_000
    row = {**trip, "charges_paise": 0}
    assert tables.unpriced(row)
    csv_text = tables.trades_csv([row])
    assert "not estimated" in csv_text.splitlines()[1]
    block = metrics.compute([row])
    assert (block["charges_estimated"], block["charges_not_estimated"]) == (False, 1)


def test_the_analytics_marker_says_when_charges_are_an_estimate():
    est = {"net_paise": 100, "charges_paise": 10, "charges_actual_paise": None}
    actual = {"net_paise": 100, "charges_paise": 12, "charges_actual_paise": 12}
    assert metrics.compute([est])["charges_estimated"] is True
    assert metrics.compute([actual])["charges_estimated"] is False
    assert metrics.compute([])["charges_estimated"] is False
    assert metrics.compute([est, actual])["charges_not_estimated"] == 0


def test_step_three_backfills_the_flag_from_each_trips_own_segment(tmp_path):
    """journal.db forced back to step 2 with trips in it: the new column is made and each trip's
    value comes from its exchange and symbol, never from the column's default."""
    path = tmp_path / store.DB_FILENAME
    engine = store.engine(path)
    try:
        migrations.migrate(engine, migrations.STEPS[:2])
    finally:
        engine.dispose()
    trips = [("NFO", "NIFTY01DEC2625000CE"), ("NFO", "NIFTY29DEC26FUT"), ("NSE", "ACME"),
             ("BSE", "ACME"), ("CDS", "USDINR29DEC26FUT"), ("MCX", "CRUDEOIL18DEC26FUT"),
             ("NFO", "NIFTY")]  # fmt: skip
    with sqlite3.connect(path) as conn:
        for n, (exchange, symbol) in enumerate(trips):
            conn.execute(
                "INSERT INTO round_trip (principal, mode, exchange, symbol, product, "
                "product_class, side, qty_max, entry_qty, exit_qty, entry_value_u, exit_value_u, "
                "entry_avg_u, opened_ms, open_day, status, created_at, updated_at) VALUES "
                "('owner', 'live', ?, ?, 'MIS', 'intraday', 'long', 1, 1, 0, 1, 0, 1, ?, "
                "20261109, 'open', 1, 1)",
                (exchange, symbol, n),
            )
    opened = JournalStore(path, now=NOW)
    try:
        assert opened.applied == [n for n, _ in migrations.STEPS if n > 2]
        got = opened.read(OWNER, lambda tx: [(t["exchange"], t["symbol"], t["charges_modelled"])
                                             for t in tx.rows("round_trip")])  # fmt: skip
        assert got == [
            ("NFO", "NIFTY01DEC2625000CE", True), ("NFO", "NIFTY29DEC26FUT", True),
            ("NSE", "ACME", True), ("BSE", "ACME", False), ("CDS", "USDINR29DEC26FUT", False),
            ("MCX", "CRUDEOIL18DEC26FUT", False), ("NFO", "NIFTY", False),
        ]  # fmt: skip
        assert all(
            ok == (charges.segment_of(ex, sym, "MIS") != "not_modelled") for ex, sym, ok in got
        )  # the backfill says what the rate card says
    finally:
        opened.close()
        store.stop()
    again = JournalStore(path, now=NOW)  # run again: nothing to do, nothing changed
    try:
        assert again.applied == []
    finally:
        again.close()
        store.stop()


def test_a_share_of_an_order_is_its_fraction_of_the_whole():
    rows = [fill("2", "B", "SELL", 130, 104, "10:05:00")]
    parts, _ = charges.estimate_order(rows, "plus")
    share = charges.estimate_shared({("B", DAY): (rows, 0, 65, 130)}, "plus")
    assert share["parts"]["brokerage_paise"] == Fraction(parts["brokerage"], 2)
