"""What the journal did not see (review B1): a position held before its first fill of the
instrument, and a contract that ends at expiry. Neither becomes a trade on the wrong side, a live
P&L or a figure; each is listed apart and every report says what it leaves out. Pure cases, the
store's replay, and the journal's own rig against a fake portfolio; every value is synthetic."""

from __future__ import annotations

import pytest
from journal_fakes import trade_row
from test_journal_analytics import book as book  # noqa: F401  (a fixture)
from test_journal_capture import DAY, OPT, STOCK, ist_ms, scenario
from test_journal_capture import files as files  # noqa: F401  (a fixture)
from test_journal_capture import rig as rig  # noqa: F401  (a fixture)
from test_journal_reports import call

from sudarshan.journal import store
from sudarshan.journal.book import Facts, replay_tx
from sudarshan.journal.core import metrics, words
from sudarshan.journal.core.money import u_to_paise
from sudarshan.journal.core.pairing import (
    held_before,
    pair_fills,
    past_expiry,
    symbol_expiry,
)
from sudarshan.journal.jobs import check
from sudarshan.journal.store import JournalStore, Scope

OWNER = Scope.owner()
NOW = 1_794_300_000_000
FUT = "NSE_FO|50002"  # NIFTY18NOV2625000CE in the rig's master, expiring Wed 18 Nov 2026
FUT_SYMBOL = "NIFTY18NOV2625000CE"
LINE = (
    "Not in these figures: 1 position opened before the journal started, 1 contract that "
    "ended at expiry. Upstox's own report has them."
)


def fill(trade_id, action, qty, price_rs, clock, day=DAY, **more):
    out = {"trade_id": trade_id, "exchange": "NSE", "symbol": "ACME", "product": "CNC",
           "action": action, "qty": qty, "price_u": int(price_rs * 10_000),
           "exch_ts_ms": ist_ms(day, clock), "day": day}  # fmt: skip
    return {**out, **more}


def live(trips):
    return [(t["status"], t["side"], t["entry_qty"], t["exit_qty"]) for t in trips]


# ---- the pairing, pure ---------------------------------------------------------------------
def test_a_lone_delivery_sale_is_unmatched_never_an_open_short():
    """Measured in the review: one CNC SELL with nothing before it came out as an open short."""
    (trip,) = pair_fills([fill("1", "SELL", 10, 1300, "10:00:00")])
    assert trip["status"] == "unmatched" and trip["side"] == "long"
    assert (trip["entry_qty"], trip["exit_qty"], trip["qty_max"]) == (0, 10, 10)
    assert trip["gross_paise"] is None and trip["hold_s"] is None and trip["entry_avg_u"] == 0
    assert trip["close_day"] == DAY  # nothing of it is left open


def test_a_later_delivery_buy_opens_a_new_long_and_closes_nothing():
    trips = pair_fills([fill("1", "SELL", 10, 1300, "10:00:00"),
                        fill("2", "BUY", 10, 1250, "11:00:00", day=20261110)])  # fmt: skip
    assert live(trips) == [("unmatched", "long", 0, 10), ("open", "long", 10, 0)]
    assert trips[1]["legs"] == [{"trade_id": "2", "role": "entry", "qty": 10,
                                 "price_u": 12_500_000, "exch_ts_ms": ist_ms(20261110, "11:00:00"),
                                 "day": 20261110}]  # fmt: skip


def test_a_delivery_sale_past_what_was_seen_bought_closes_the_trip_and_lists_the_rest():
    trips = pair_fills([fill("1", "BUY", 10, 100, "10:00:00"),
                        fill("2", "SELL", 15, 110, "11:00:00")])  # fmt: skip
    assert live(trips) == [("closed", "long", 10, 10), ("unmatched", "long", 0, 5)]
    assert trips[0]["gross_paise"] == 10_000  # 10 x 10.00, the 5 beyond are not in it


def test_an_intraday_sale_from_flat_is_still_a_short():
    (trip,) = pair_fills([fill("1", "SELL", 10, 1300, "10:00:00", product="MIS")])
    assert (trip["status"], trip["side"]) == ("open", "short")


def test_a_derivative_sale_against_an_opening_long_is_unmatched_and_the_rest_a_short():
    key = ("live", "NFO", FUT_SYMBOL, "carry")
    base = {"exchange": "NFO", "symbol": FUT_SYMBOL, "product": "NRML"}
    one = [fill("1", "SELL", 65, 100, "10:00:00", **base)]
    (trip,) = pair_fills(one, {key: 65})
    assert (trip["status"], trip["exit_qty"], trip["qty_max"]) == ("unmatched", 65, 65)
    assert pair_fills(one)[0]["status"] == "open"  # without the opening: the short the review saw
    flip = pair_fills([fill("1", "SELL", 130, 100, "10:00:00", **base)], {key: 65})
    assert live(flip) == [("unmatched", "long", 0, 65), ("open", "short", 65, 0)]


def test_an_unmatched_trip_holds_until_the_position_is_flat_and_takes_its_adds():
    key = ("live", "NSE", "ACME", "carry")
    trips = pair_fills([fill("1", "SELL", 30, 100, "10:00:00"),
                        fill("2", "BUY", 5, 99, "10:30:00"),
                        fill("3", "SELL", 75, 101, "11:00:00", day=20261112)], {key: 100})  # fmt: skip
    (trip,) = trips
    assert (trip["status"], trip["entry_qty"], trip["exit_qty"]) == ("unmatched", 5, 105)
    assert trip["close_day"] == 20261112 and trip["qty_max"] == 105
    assert [leg["role"] for leg in trip["legs"]] == ["exit", "entry", "exit"]


def test_what_was_held_comes_from_the_opening_less_the_days_before_its_read():
    opening = {"qty": 30, "day": 20261111}
    rebuilt = [fill("h1", "SELL", 35, 100, "12:00:00", day=20261109)]  # a day from the history
    assert held_before(opening, rebuilt, cash=True) == 65  # 30 held on the 11th, 35 sold before
    bought = [fill("h1", "BUY", 30, 100, "12:00:00", day=20261109)]
    assert held_before(opening, bought, cash=True) == 0  # the 30 held were the 30 bought
    assert held_before({"qty": 0, "day": 20261111}, bought, cash=True) == 0  # never short
    assert held_before({"qty": 0, "day": 20261111}, bought, cash=False) == -30
    assert held_before(None, bought, cash=False) == 0
    same_day = [fill("t1", "SELL", 10, 100, "10:00:00", day=20261111)]
    assert held_before(opening, same_day, cash=True) == 30  # the read is of the day's start
    withdrawn = [{**rebuilt[0], "source": "withdrawn"}]
    assert held_before(opening, withdrawn, cash=True) == 30


def test_a_contract_that_expired_before_the_read_says_nothing_of_what_was_held():
    bought = [fill("h1", "BUY", 65, 100, "12:00:00", day=20261109, exchange="NFO",
                   symbol=FUT_SYMBOL, product="NRML")]  # fmt: skip
    opening = {"qty": 0, "day": 20261120}
    assert held_before(opening, bought, cash=False, expiry_day=20261118) == 0
    assert held_before(opening, bought, cash=False, expiry_day=20261126) == -65


def test_the_expiry_day_of_a_symbol_and_when_it_has_passed():
    assert symbol_expiry("NIFTY18NOV2625000CE") == 20261118
    assert symbol_expiry("VEDL25APR24292.5CE") == 20240425
    assert symbol_expiry("CRUDEOILM20MAY24FUT") == 20240520
    assert symbol_expiry("NIFTYNXT5025NOV2625000PE") == 20261125
    assert symbol_expiry("ACME") is None and symbol_expiry("X31FEB26100CE") is None
    assert past_expiry("NFO", 20261118, 20261119, after_close=False)
    assert not past_expiry("NFO", 20261118, 20261118, after_close=False)  # still trading
    assert past_expiry("NFO", 20261118, 20261118, after_close=True)  # after 15:30
    assert not past_expiry("MCX", 20261118, 20261118, after_close=True)  # trades into the night
    assert not past_expiry("NFO", None, 20261118, after_close=True)


# ---- the store's replay ----------------------------------------------------------------------
@pytest.fixture
def made(tmp_path):
    opened = JournalStore(tmp_path / store.DB_FILENAME, now=NOW)
    yield opened
    opened.close()
    store.stop()


def as_row(f, key="NSE_EQ|INE000A00001"):
    return {"mode": "live", "day": f["day"], "trade_id": f["trade_id"],
            "order_id": f"o-{f['trade_id']}", "exchange": f["exchange"], "symbol": f["symbol"],
            "instrument_key": key, "product": f["product"], "action": f["action"],
            "qty": f["qty"], "price_u": f["price_u"],
            "value_paise": u_to_paise(f["qty"] * f["price_u"]), "exch_ts_ms": f["exch_ts_ms"],
            "order_ts_ms": None, "source": "stream_read"}  # fmt: skip


def replay(made, fills, facts=None, key="NSE_EQ|INE000A00001", now=NOW):
    rows = [as_row(f, key) for f in fills]
    made.write(OWNER, lambda tx: tx.fills_upsert(rows, now))
    days = {r["day"] for r in rows}
    return made.write(OWNER, lambda tx: replay_tx(tx, "live", days, now, facts or Facts()))


def trips_of(made):
    return made.read(OWNER, lambda tx: tx.rows("round_trip", order="opened_ms,id"))


def test_an_opening_read_at_first_sight_is_kept_and_paired_from(made):
    base = {"exchange": "NFO", "symbol": FUT_SYMBOL, "product": "NRML"}
    facts = Facts(openings={("NFO", FUT_SYMBOL, "carry"): (65, "positions")},
                  opening_day=DAY, opening_ms=NOW)  # fmt: skip
    counts = replay(made, [fill("1", "SELL", 65, 100, "10:00:00", **base)], facts, FUT)
    (trip,) = trips_of(made)
    assert trip["status"] == "unmatched" and counts["unmatched"] == 1
    kept = made.read(OWNER, lambda tx: tx.openings_of("live"))
    assert kept[("NFO", FUT_SYMBOL, "carry")]["qty"] == 65
    # A later fill of the key pairs from the kept opening: a buy now opens a new long.
    replay(made, [fill("2", "BUY", 65, 101, "11:00:00", **base)], key=FUT)
    assert [(t["status"], t["side"]) for t in trips_of(made)] == [
        ("unmatched", "long"), ("open", "long"),
    ]  # fmt: skip


def test_a_replay_never_turns_an_expired_trip_back_into_an_open_one(made):
    base = {"exchange": "NFO", "symbol": FUT_SYMBOL, "product": "NRML"}
    replay(made, [fill("1", "BUY", 65, 100, "10:00:00", **base)], key=FUT)
    (opened,) = trips_of(made)
    made.write(OWNER, lambda tx: tx.touch("round_trip", opened["id"],
                                          {"status": "expired", "close_day": 20261118}, NOW))  # fmt: skip
    later = ist_ms(20261118, "16:30:00")
    replay(made, [fill("2", "BUY", 65, 100, "15:00:00", day=20261118, **base)], key=FUT,
           now=later)  # fmt: skip
    (trip,) = trips_of(made)
    assert (trip["status"], trip["close_day"], trip["entry_qty"]) == ("expired", 20261118, 130)


def test_a_replay_after_the_expiry_day_marks_a_trip_still_open_expired(made):
    base = {"exchange": "NFO", "symbol": FUT_SYMBOL, "product": "NRML"}
    replay(made, [fill("1", "BUY", 65, 100, "10:00:00", **base)], key=FUT,
           now=ist_ms(20261119, "22:00:00"))  # fmt: skip
    (trip,) = trips_of(made)
    assert (trip["status"], trip["close_day"]) == ("expired", 20261118)


# ---- end to end, on the journal's rig --------------------------------------------------------
def holding(key, qty, t1=0):
    return {"instrument_key": key, "exchange": None, "symbol": None, "name": "ACME", "isin": None,
            "qty_u": qty * 1_000_000, "t1_qty_u": t1 * 1_000_000, "avg_price_u": 2_000_000,
            "last_price_u": 2_500_000, "close_price_u": None, "pnl_paise": 0, "product": "D",
            "collateral_qty_u": 0, "collateral_type": None, "haircut_pct": None,
            "cnc_used_qty_u": 0}  # fmt: skip


def overnight(key, qty, product="D"):
    return {"instrument_key": key, "exchange": None, "symbol": None, "name": key,
            "product": product, "qty_u": 0, "overnight_qty_u": qty * 1_000_000,
            "day_buy_qty_u": 0, "day_sell_qty_u": qty * 1_000_000, "avg_price_u": 1_000_000,
            "buy_price_u": 0, "sell_price_u": 0, "last_price_u": 1_000_000, "close_price_u": None,
            "multiplier": 1, "pnl_paise": 0, "realised_paise": 0, "unrealised_paise": 0,
            "value_paise": 0}  # fmt: skip


def answer(rig, op, rows):
    rig.portfolio.answers[op] = {"state": "ok", "read_at": rig.now, "note": None, "data": rows}


def record(rig, rows):
    rig.answer_trades(rows)
    scenario(rig, rig.capture.read_live)


def rig_trips(rig, **equal):
    return rig.svc.store.read(
        OWNER, lambda tx: tx.rows("round_trip", order="opened_ms,id", **equal)
    )


def asked(rig, op):
    return sum(1 for name, _, _ in rig.portfolio.calls if name == op)


def test_a_lone_cnc_sale_with_holdings_reported_is_unmatched_and_a_later_buy_a_new_long(rig):
    answer(rig, "holdings", [holding(STOCK["token"], 10)])
    sale = trade_row("t1", "o1", key=STOCK["token"], symbol="ACME", product="D", side="SELL",
                     qty=10, price_u=2_500_000, at=ist_ms(DAY, "10:05:00"))  # fmt: skip
    record(rig, [sale])
    (trip,) = rig_trips(rig)
    assert (trip["status"], trip["exit_qty"]) == ("unmatched", 10)
    assert rig_trips(rig, status="open") == []  # no open short
    kept = rig.svc.store.read(OWNER, lambda tx: tx.openings_of("live"))
    assert kept[("NSE", "ACME", "carry")]["qty"] == 10
    assert kept[("NSE", "ACME", "carry")]["source"] == "holdings"
    rig.now = ist_ms(DAY, "11:30:00")
    buy = trade_row("t2", "o2", key=STOCK["token"], symbol="ACME", product="D", side="BUY",
                    qty=10, price_u=2_400_000, at=ist_ms(DAY, "11:00:00"))  # fmt: skip
    record(rig, [sale, buy])
    trips = rig_trips(rig)
    assert [(t["status"], t["side"], t["entry_qty"], t["exit_qty"]) for t in trips] == [
        ("unmatched", "long", 0, 10), ("open", "long", 10, 0),
    ]  # fmt: skip
    assert trips[0]["id"] == trip["id"] and trips[0]["net_paise"] is None
    assert asked(rig, "holdings") == 1  # read once, at first sight


def test_an_nrml_sale_against_an_overnight_long_on_day_one_is_unmatched(rig):
    answer(rig, "positions", [overnight(FUT, 65)])
    sale = trade_row("t1", "o1", key=FUT, product="D", side="SELL", qty=65,
                     price_u=600_000, at=ist_ms(DAY, "10:05:00"))  # fmt: skip
    record(rig, [sale])
    (trip,) = rig_trips(rig)
    assert (trip["status"], trip["symbol"], trip["exit_qty"]) == ("unmatched", FUT_SYMBOL, 65)
    assert rig_trips(rig, status="open") == []
    assert asked(rig, "positions") == 1


def test_a_read_that_did_not_happen_is_asked_again_at_the_next_replay(rig):
    sale = trade_row("t1", "o1", key=FUT, product="D", side="SELL", qty=65,
                     price_u=600_000, at=ist_ms(DAY, "10:05:00"))  # fmt: skip
    record(rig, [sale])  # the portfolio has no answer: nothing kept, the sale reads as a short
    assert [t["status"] for t in rig_trips(rig)] == ["open"]
    answer(rig, "positions", [overnight(FUT, 65)])
    rig.now = ist_ms(DAY, "10:30:00")
    more = trade_row("t2", "o2", key=FUT, product="D", side="BUY", qty=65, price_u=590_000,
                     at=ist_ms(DAY, "10:20:00"))  # fmt: skip
    record(rig, [sale, more])
    assert [(t["status"], t["side"]) for t in rig_trips(rig)] == [
        ("unmatched", "long"), ("open", "long"),
    ]  # fmt: skip


def test_an_open_option_past_its_expiry_is_expired_after_the_close_check(rig):
    answer(rig, "positions", [])
    buy = trade_row("t1", "o1", key=FUT, product="D", side="BUY", qty=65, price_u=600_000,
                    at=ist_ms(DAY, "10:05:00"))  # fmt: skip
    record(rig, [buy])
    assert [t["status"] for t in rig_trips(rig)] == ["open"]
    day = 20261119  # the day after its expiry
    rig.now = ist_ms(day, "16:05:00")
    rig.answer_trades([])
    line = scenario(rig, lambda: check.check_day(rig.svc, day, rig.now, False))
    (trip,) = rig_trips(rig)
    assert (trip["status"], trip["close_day"], trip["net_paise"]) == ("expired", 20261118, None)
    assert line.endswith("; 1 ended at expiry")
    assert call(rig, "trips", status="open") == []
    assert [t["status"] for t in call(rig, "trips", status="expired")] == ["expired"]


def test_an_option_expiring_today_is_expired_by_that_days_close_check(rig):
    answer(rig, "positions", [])
    record(rig, [trade_row("t1", "o1", key=FUT, product="D", side="BUY", qty=65,
                           price_u=600_000, at=ist_ms(DAY, "10:05:00"))])  # fmt: skip
    rig.now = ist_ms(20261118, "16:05:00")
    rig.answer_trades([])
    scenario(rig, lambda: check.check_day(rig.svc, 20261118, rig.now, False))
    assert [t["status"] for t in rig_trips(rig)] == ["expired"]


def test_a_monthly_statement_says_what_its_figures_leave_out_and_leaves_them_out(rig, book):  # noqa: F811
    closed = book.read(OWNER, lambda tx: tx.rows("round_trip", status="closed"))
    # a share sold that the journal never saw bought, and an option that ended at expiry
    answer(rig, "positions", [])
    rig.now = ist_ms(20261110, "10:30:00")
    record(rig, [
        trade_row("u1", "ou1", key=STOCK["token"], symbol="ACME", product="D", side="SELL",
                  qty=10, price_u=2_500_000, at=ist_ms(20261110, "10:05:00")),
        trade_row("x1", "ox1", key=FUT, product="D", side="BUY", qty=65, price_u=600_000,
                  at=ist_ms(20261110, "10:10:00")),
    ])  # fmt: skip
    rig.now = ist_ms(20261119, "16:05:00")
    rig.answer_trades([])
    scenario(rig, lambda: check.check_day(rig.svc, 20261119, rig.now, False))
    assert sorted(t["status"] for t in rig_trips(rig) if t["status"] != "closed") == [
        "expired", "unmatched",
    ]  # fmt: skip
    rig.now = ist_ms(20261201, "09:00:00")
    call(rig, "report_build", kind="monthly", period="202611")
    row = rig.svc.store.read(OWNER, lambda tx: tx.rows("report", kind="monthly"))[0]
    assert LINE in row["text"].splitlines()
    want = metrics.compute([{**t, "charges_paise": t["charges_est_paise"]} for t in closed])
    assert row["rows"]["trades"] == 4 == want["trips"]
    assert row["rows"]["net_paise"] == want["net_paise"]
    assert row["rows"]["left_out"] == {"unmatched": 1, "expired": 1}
    assert f"Net {words.rupees(want['net_paise'], True)}" in row["text"]
    assert "ACME" not in (row["rows"].get("csv") or "")  # neither is a row of the month's CSV


def test_the_left_out_line_reads_for_one_and_for_many():
    assert words.left_out_line(0, 0) is None
    assert words.left_out_line(1, 0) == (
        "Not in these figures: 1 position opened before the journal started. Upstox's own "
        "report has it."
    )
    assert words.left_out_line(2, 3, "Not in the trade count") == (
        "Not in the trade count: 2 positions opened before the journal started, 3 contracts "
        "that ended at expiry. Upstox's own report has them."
    )
    assert OPT["symbol"] != FUT_SYMBOL  # the rig's two options: one expires on 25 Nov, one 18 Nov
