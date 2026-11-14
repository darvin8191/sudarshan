"""The journal's day jobs (JB5): the day's close at 15:45 through guard/daily.py's row logic, the
close check after the cash post-close session, and the 22:00 catch-up (today once more, the
history of unmarked days, Upstox's P&L rows). Against fakes on 127.0.0.1; all values synthetic."""

from __future__ import annotations

import pytest
from journal_fakes import trade_row
from test_journal_capture import DAY, OPT, STOCK, ist_ms, scenario
from test_journal_capture import files as files  # noqa: F401  (a fixture)
from test_journal_capture import rig as rig  # noqa: F401  (a fixture)

from sudarshan.journal.jobs import catchup, check, day
from sudarshan.journal.schedule import JobFailed
from sudarshan.journal.store import Scope
from sudarshan.tests.fdwatch import FdWatch

OWNER = Scope.owner()
CLOSE = ist_ms(DAY, "15:45:00")


def live_rows():
    return [
        trade_row("t1", "o1", side="BUY", price_u=1_000_000, at=ist_ms(DAY, "10:01:00")),
        trade_row("t2", "o2", side="SELL", price_u=1_125_000, at=ist_ms(DAY, "10:20:00")),
        trade_row("t3", "o3", key="NSE_FO|50002", side="BUY", product="D",
                  price_u=600_000, at=ist_ms(DAY, "11:00:00")),
    ]  # fmt: skip


def position(key, product, qty, *, realised=0, unrealised=0, last_u=1_000_000, **more):
    return {"instrument_key": key, "exchange": None, "symbol": None, "name": key,
            "product": product, "qty_u": qty * 1_000_000, "overnight_qty_u": 0,
            "day_buy_qty_u": 0, "day_sell_qty_u": 0, "avg_price_u": last_u, "buy_price_u": 0,
            "sell_price_u": 0, "last_price_u": last_u, "close_price_u": None, "multiplier": 1,
            "pnl_paise": realised + unrealised, "realised_paise": realised,
            "unrealised_paise": unrealised, "value_paise": 0, **more}  # fmt: skip


def answer_positions(rig, rows, state="ok"):
    rig.portfolio.answers["positions"] = {"state": state, "read_at": CLOSE - 1000,
                                          "note": None if state == "ok" else "Upstox did not "
                                          "answer.", "data": rows if state == "ok" else None}  # fmt: skip


def closing(rig):
    return rig.svc.store.read(OWNER, lambda tx: tx.day_close_get("live", DAY))


def record(rig, rows):
    rig.answer_trades(rows)
    scenario(rig, rig.capture.read_live)


# ---- the day's close ---------------------------------------------------------------------------
def test_the_day_close_takes_the_guards_figure_and_the_journals_counts(rig):
    record(rig, live_rows())
    answer_positions(rig, [
        position(OPT["token"], "I", 0, realised=81_250),
        position("NSE_FO|50002", "D", 65, unrealised=-12_000, last_u=581_538),
        position(STOCK["token"], "D", 10, realised=5_000, last_u=2_510_000),  # delivery: left out
    ])  # fmt: skip
    rig.now = CLOSE
    line = scenario(rig, lambda: day.close_day(rig.svc, DAY, CLOSE, False))
    row = closing(rig)
    assert (row["realised_paise"], row["unrealised_paise"], row["approximate"]) == (
        81_250, -12_000, False,
    )  # fmt: skip
    assert (row["trades_closed"], row["wins"], row["losses"], row["flats"]) == (1, 1, 0, 0)
    assert row["charges_est_paise"] > 6_000 and row["fills_live"] == 3
    assert row["positions_overnight"] == [
        {"symbol": "NIFTY18NOV2625000CE", "exchange": "NFO", "product": "NRML", "qty": 65},
        {"symbol": "ACME", "exchange": "NSE", "product": "CNC", "qty": 10},
    ]
    assert (row["kill_switch"], row["daily_lock"], row["read_at"]) == ("off", "13:42", CLOSE - 1000)
    assert row["reconciled_at"] is None
    assert line.startswith("positions read, 1 trades closed, 3 fills recorded live, 2 carried; ")
    assert line.endswith(" kept")  # the day report (JB7)


def test_a_row_with_the_days_values_is_checked_against_its_own_trades(rig):
    """Upstox's absurd unrealised on a carried row: the guard's formula wins once the portfolio
    carries the day's values, and the figure is marked approximate (cross-design Y22.4)."""
    answer_positions(rig, [
        position(OPT["token"], "D", 65, unrealised=-5_000_000, last_u=1_000_000,
                 day_buy_value_paise=650_000, day_sell_value_paise=0),
    ])  # fmt: skip
    scenario(rig, lambda: day.close_day(rig.svc, DAY, CLOSE, False))
    row = closing(rig)
    assert (row["realised_paise"], row["unrealised_paise"], row["approximate"]) == (0, 0, True)


def test_unread_positions_fall_back_to_the_journals_trips(rig):
    record(rig, live_rows())
    answer_positions(rig, None, state="unavailable")
    line = scenario(rig, lambda: day.close_day(rig.svc, DAY, CLOSE, False))
    row = closing(rig)
    assert (row["realised_paise"], row["unrealised_paise"]) == (81_250, None)
    assert row["approximate"] is True and row["read_at"] is None
    assert line.startswith("positions not read")


def test_the_guard_module_is_imported_without_openalgos_env(monkeypatch):
    """Importing guard/daily.py brings OpenAlgo's logging module: its .env load is skipped, its
    log file switched off, and this service's own logging is set up again after."""
    import logging
    import os
    import sys

    import dotenv

    seen, real, before = {}, day.importlib.import_module, dotenv.load_dotenv

    def spy(name):
        seen.update(load=dotenv.load_dotenv, to_file=os.environ.get("LOG_TO_FILE"))
        return real(name)

    monkeypatch.delitem(sys.modules, day.GUARD_MODULE, raising=False)
    monkeypatch.setattr(day.importlib, "import_module", spy)
    module = day.guard_daily("DEBUG")
    try:
        assert callable(module.live_figure)
        assert seen["load"] is not before and seen["load"]() is False
        assert seen["to_file"] == "False" and dotenv.load_dotenv is before
        assert logging.getLogger().level == logging.DEBUG
    finally:
        day.setup_logging({"SUDARSHAN_JOURNAL_LOG_LEVEL": "INFO"})


# ---- the close check ---------------------------------------------------------------------------
def test_the_close_check_adds_what_the_live_reads_missed_and_says_so(rig):
    record(rig, live_rows()[:1])
    rig.answer_trades(live_rows())
    rig.now = ist_ms(DAY, "16:05:00")
    line = scenario(rig, lambda: check.check_day(rig.svc, DAY, rig.now, False))
    row = closing(rig)
    assert row["reconciled_at"] == rig.now
    assert row["reconcile_note"] == "The close check found 2 more fills; the journal is corrected."
    assert rig.svc.counts["last_reconcile_ms"] == rig.now
    assert line.startswith("live: 3 rows, 2 new") and "sandbox: " in line
    sources = sorted(f["source"] for f in rig.fills())
    assert sources == ["close_reconcile", "close_reconcile", "stream_read"]


def test_the_close_check_takes_out_a_fill_upstox_no_longer_lists(rig):
    record(rig, live_rows())
    rig.answer_trades(live_rows()[:2])
    scenario(rig, lambda: check.check_day(rig.svc, DAY, rig.now, False))
    assert closing(rig)["reconcile_note"] == (
        "The close check took out 1 fill Upstox's book no longer lists; the journal is corrected."
    )
    record(rig, live_rows()[:2])
    scenario(rig, lambda: check.check_day(rig.svc, DAY, rig.now, False))
    assert closing(rig)["reconcile_note"] == "The close check agreed with the fills recorded live."


def test_a_close_check_that_cannot_read_fails_in_words(rig):
    rig.answer_trades([], state="unavailable")
    with pytest.raises(JobFailed) as caught:
        scenario(rig, lambda: check.check_day(rig.svc, DAY, rig.now, False))
    assert str(caught.value).startswith("The day's trades could not be read for the close check")
    assert closing(rig) is None


# ---- the 22:00 catch-up ----------------------------------------------------------------------
def history_row(trade_id, day_, side, *, segment="FO", key=OPT["token"], price_u=1_000_000,
                qty=65, **more):  # fmt: skip
    return {"trade_id": trade_id, "trade_date": day_, "exchange": "NSE", "segment": segment,
            "transaction_type": side, "scrip_name": "NIFTY", "symbol": "NIFTY", "isin": None,
            "instrument_token": key, "option_type": "CE", "strike_price_u": 250_000_000,
            "expiry": 20261125, "qty_u": qty * 1_000_000, "price_u": price_u,
            "amount_paise": qty * price_u // 100, **more}  # fmt: skip


def pnl_row(sell_date, buy_amount, sell_amount):
    return {"scrip_name": "NIFTY 25000 CE", "isin": None, "trade_type": "FUT/OPT",
            "qty_u": 65_000_000, "buy_date": sell_date, "sell_date": sell_date,
            "buy_average_u": 1_000_000, "sell_average_u": 1_010_000,
            "buy_amount_paise": buy_amount, "sell_amount_paise": sell_amount,
            "pnl_paise": sell_amount - buy_amount}  # fmt: skip


def test_the_catch_up_reads_today_and_fills_the_unmarked_days_from_history(rig):
    """Mon 9 Nov 22:00: today read once more; Thu 5 Nov was checked, Fri 6 and Wed 4 were not;
    a trade the journal already holds keeps its own record; Upstox's P&L pairs of those days
    and the year's charges are kept."""
    fri, thu, wed = 20261106, 20261105, 20261104
    record(rig, [trade_row("h1", "live-o", side="BUY", at=ist_ms(fri, "10:00:00"))])
    rig.svc.store.write(OWNER, lambda tx: tx.day_close_put(
        "live", thu, {"reconciled_at": 1, "reconcile_note": "checked"}, 1))  # fmt: skip
    rig.answer_trades(live_rows())
    rig.portfolio.answers["historical_trades"] = {
        "state": "ok", "read_at": 1, "note": None, "total_pages": 1, "total_records": 5,
        "rows": [
            history_row("h1", fri, "BUY"),  # already recorded live: left as it is
            history_row("h2", fri, "SELL", price_u=1_010_000),
            history_row("h3", thu, "BUY"),  # a checked day: not used
            history_row("h4", wed, "BUY", key="NSE_FO|99999", expiry=20261103),  # expired
            history_row("h5", wed, "SELL", segment="EQ", key=STOCK["token"], qty=10,
                        option_type=None),
        ],
    }  # fmt: skip
    rig.portfolio.answers["pnl_report"] = {
        "state": "ok", "read_at": 1, "note": None, "page_size": 5000,
        "rows": [pnl_row(fri, 6_500_000, 6_565_000), pnl_row(20261020, 100, 200)],
        "charges": {"total_paise": 12_345, "brokerage_paise": 6_000, "gst_paise": 1_080,
                    "stt_paise": 2_000, "stamp_duty_paise": 30},
    }  # fmt: skip
    rig.now = ist_ms(DAY, "22:00:00")
    line = scenario(rig, lambda: catchup.catch_up(rig.svc, DAY, rig.now, False))
    fills = {f["trade_id"]: f for f in rig.fills()}
    assert fills["h1"]["source"] == "stream_read" and fills["h1"]["product"] == "MIS"
    # the product of the day's recorded fill of the instrument, so the two make one trip
    assert fills["h2"]["source"] == "history" and fills["h2"]["product"] == "MIS"
    h2_order = "h:20261106:NFO:NIFTY25NOV2625000CE:MIS:SELL"  # one order a side and day (M1)
    assert fills["h2"]["order_id"] == h2_order and "h3" not in fills
    assert fills["h4"]["symbol"] == "NIFTY03NOV2625000CE" and fills["h4"]["exchange"] == "NFO"
    assert fills["h4"]["product"] == "NRML"  # nothing recorded of it that day: the carry class
    trips = rig.svc.store.read(OWNER, lambda tx: tx.rows("round_trip", open_day=fri))
    assert [(t["status"], t["gross_paise"]) for t in trips] == [("closed", 6_500)]
    assert fills["h5"]["product"] == "CNC" and fills["h5"]["exchange"] == "NSE"
    refs = rig.refs()
    assert refs[h2_order]["surface"] == "history"
    assert refs[h2_order]["strategy"] == "Trade history"
    assert {"t1", "t2", "t3"} <= set(fills)  # today's trades, read once more
    notes = {d: (closing_of(rig, d) or {}).get("reconcile_note") or "" for d in (fri, thu, wed)}
    assert notes[thu] == "checked"
    assert notes[fri].startswith("Rebuilt from Upstox's trade history: 2 trades.")
    assert notes[wed].startswith("Rebuilt from Upstox's trade history: 2 trades.")
    # the segments the journal holds fills of on those days (EQ on Wed, FO on Wed and Fri); the
    # fake answers every segment alike, and the October pair is outside the three days
    pairs = rig.svc.store.read(OWNER, lambda tx: tx.rows("upstox_pair", order="segment"))
    assert [(p["segment"], p["sell_date"], p["realised_paise"]) for p in pairs] == [
        ("EQ", fri, 65_000), ("FO", fri, 65_000),
    ]  # fmt: skip
    charges = rig.svc.store.read(OWNER, lambda tx: tx.rows("upstox_charges", order="segment"))
    assert [(c["segment"], c["total_paise"], c["stamp_paise"]) for c in charges] == [
        ("EQ", 12_345, 30), ("FO", 12_345, 30),
    ]  # fmt: skip
    assert "history: 3 trades added for 2 days" in line
    assert "P&L report: 2 new pairs, charges for 2 segments" in line
    calls = [c[0] for c in rig.portfolio.calls]
    assert calls.count("historical_trades") == 4 and calls.count("trades_day") == 2
    # the same day again: history and the P&L rows are not asked twice
    scenario(rig, lambda: catchup.catch_up(rig.svc, DAY, rig.now, False))
    assert [c[0] for c in rig.portfolio.calls].count("historical_trades") == 4


# ---- shares on a day rebuilt from the history (verification N1) -------------------------------
def share_row(trade_id, day_, side, qty, price_u):
    return history_row(trade_id, day_, side, segment="EQ", key=STOCK["token"], symbol="ACME",
                       scrip_name="ACME", option_type=None, strike_price_u=None, expiry=None,
                       qty=qty, price_u=price_u)  # fmt: skip


def rebuild(rig, rows, holdings=()):
    """The 22:00 catch-up of Mon 9 Nov with ``rows`` as Upstox's history, nothing traded today."""
    rig.answer_trades([])
    rig.portfolio.answers["holdings"] = {"state": "ok", "read_at": 1, "note": None,
                                         "data": list(holdings)}  # fmt: skip
    rig.portfolio.answers["historical_trades"] = {
        "state": "ok", "read_at": 1, "note": None, "total_pages": 1,
        "total_records": len(rows), "rows": rows}  # fmt: skip
    rig.portfolio.answers["pnl_report"] = {"state": "ok", "read_at": 1, "rows": [],
                                           "page_size": 5000, "charges": {}}  # fmt: skip
    rig.now = ist_ms(DAY, "22:00:00")
    scenario(rig, lambda: catchup.catch_up(rig.svc, DAY, rig.now, False))
    return rig.svc.store.read(OWNER, lambda tx: tx.rows("round_trip", order="opened_ms,id"))


def test_an_intraday_share_short_on_a_history_day_is_one_closed_short(rig):
    """Measured by the verifier: SELL 10 (trade 101) then BUY 10 (trade 102) of one share on a
    day rebuilt from the history, nothing held, came out as a sale of shares held before the
    journal plus an open LONG of 10 that does not exist."""
    fri = 20261106
    trips = rebuild(rig, [share_row("101", fri, "SELL", 10, 5_000_000),
                          share_row("102", fri, "BUY", 10, 4_950_000)])  # fmt: skip
    assert [(t["status"], t["side"], t["product_class"], t["entry_qty"], t["exit_qty"],
             t["gross_paise"]) for t in trips] == [("closed", "short", "intraday", 10, 10, 5_000)]  # fmt: skip
    fills = {f["trade_id"]: f for f in rig.fills()}
    assert {f["product"] for f in fills.values()} == {"MIS"}
    assert fills["101"]["order_id"] == "h:20261106:NSE:ACME:MIS:SELL"
    note = closing_of(rig, fri)["reconcile_note"]
    assert note.endswith("2 of them are share trades that bought and sold the same quantity "
                         "that day, counted as bought and sold the same day (intraday).")  # fmt: skip
    # no read of what was held: an intraday key has no opening
    assert not [c for c in rig.portfolio.calls if c[0] == "holdings"]


def test_a_delivery_sale_on_a_history_day_of_shares_held_before_is_still_unmatched(rig):
    """Held 100 for months, sold 40 on a history day: the quantities differ, so it stays a
    delivery sale, listed apart and never a short."""
    from test_journal_unseen import holding

    fri = 20261106
    trips = rebuild(rig, [share_row("101", fri, "SELL", 40, 5_000_000)],
                    holdings=[holding(STOCK["token"], 60)])  # fmt: skip
    assert [(t["status"], t["side"], t["product"], t["exit_qty"], t["gross_paise"])
            for t in trips] == [("unmatched", "long", "CNC", 40, None)]  # fmt: skip
    assert "intraday" not in closing_of(rig, fri)["reconcile_note"]


def test_same_day_shares_only_where_the_days_buys_and_sales_are_equal():
    def f(day_, action, qty, exchange="NSE", symbol="ACME", product="CNC"):
        return {"day": day_, "exchange": exchange, "symbol": symbol, "product": product,
                "action": action, "qty": qty}  # fmt: skip

    equal = [f(1, "SELL", 6), f(1, "SELL", 4), f(1, "BUY", 10)]  # in parts, sale first
    uneven = [f(1, "SELL", 10, symbol="OTHER"), f(1, "BUY", 6, symbol="OTHER")]
    other_day = [f(1, "BUY", 5, symbol="TWO"), f(2, "SELL", 5, symbol="TWO")]  # carried a day
    option = [f(1, "SELL", 65, "NFO", "X", "NRML"), f(1, "BUY", 65, "NFO", "X", "NRML")]
    lone = [f(1, "BUY", 3, symbol="LONE")]
    assert catchup.same_day_shares(equal + uneven + other_day + option + lone) == 3
    assert {x["product"] for x in equal} == {"MIS"}
    assert {x["product"] for x in uneven + other_day + lone} == {"CNC"}
    assert {x["product"] for x in option} == {"NRML"}


# ---- a share trade whose sale was not seen is counted again (verification N2) -----------------
BACK = 20261120  # Fri: the journal is back after more than the 3 trading days history reaches


def held(qty):
    from test_journal_unseen import holding

    return [holding(STOCK["token"], qty)] if qty else []


def buy_shares(rig, qty=10):
    """Mon 9 Nov 10:05: a delivery purchase recorded live, nothing held before it."""
    rig.portfolio.answers["holdings"] = {"state": "ok", "read_at": 1, "note": None, "data": []}
    rig.now = ist_ms(DAY, "10:06:00")
    record(rig, [trade_row("b1", "ob1", key=STOCK["token"], symbol="ACME", product="D",
                           side="BUY", qty=qty, price_u=2_000_000,
                           at=ist_ms(DAY, "10:05:00"))])  # fmt: skip
    (trip,) = share_trips(rig)
    assert (trip["status"], trip["side"], trip["entry_qty"]) == ("open", "long", qty)
    return trip


def night(rig, day_, holdings, history=(), today=()):
    """The 22:00 catch-up of ``day_`` with these holdings; returns the job's line."""
    rig.answer_trades(list(today))
    rig.portfolio.answers["holdings"] = holdings
    rig.portfolio.answers["historical_trades"] = {
        "state": "ok", "read_at": 1, "note": None, "total_pages": 1,
        "total_records": len(history), "rows": list(history)}  # fmt: skip
    rig.portfolio.answers["pnl_report"] = {"state": "ok", "read_at": 1, "rows": [],
                                           "page_size": 5000, "charges": {}}  # fmt: skip
    rig.now = ist_ms(day_, "22:00:00")
    return scenario(rig, lambda: catchup.catch_up(rig.svc, day_, rig.now, False))


def ok(rows):
    return {"state": "ok", "read_at": 1, "note": None, "data": rows}


def share_trips(rig):
    return rig.svc.store.read(OWNER, lambda tx: tx.rows("round_trip", order="opened_ms,id"))


def asked_holdings(rig):
    return sum(1 for c in rig.portfolio.calls if c[0] == "holdings")


def test_an_open_share_trade_sold_while_the_journal_was_off_is_not_left_open(rig):
    """Reasoned by the verifier: bought 10, the journal off for two weeks, the shares sold
    meanwhile. The trade stayed an open LONG for ever, with a moving P&L, and a later purchase
    was added to it."""
    from sudarshan.journal.core.pairing import unclosed

    bought = buy_shares(rig)
    line = night(rig, BACK, ok(held(0)))
    assert "holdings: 1 share trade checked, 1 counted again" in line
    (trip,) = share_trips(rig)
    assert trip["id"] == bought["id"] and unclosed(trip)
    assert (trip["status"], trip["close_day"], trip["net_paise"]) == ("unmatched", BACK, None)
    assert not rig.svc.store.read(OWNER, lambda tx: tx.rows("round_trip", status="open"))
    left = rig.svc.store.read(OWNER, lambda tx: tx.left_out("live", BACK, BACK))
    assert left == {"unmatched": 0, "expired": 0, "unclosed": 1}
    # the same night again asks nothing more
    calls = asked_holdings(rig)
    night(rig, BACK, ok(held(0)))
    assert asked_holdings(rig) == calls
    # a purchase after it is a new trade of its own, never an add to the one that ended
    rig.now = ist_ms(20261123, "10:06:00")
    record(rig, [trade_row("b2", "ob2", key=STOCK["token"], symbol="ACME", product="D",
                           side="BUY", qty=5, price_u=2_100_000,
                           at=ist_ms(20261123, "10:05:00"))])  # fmt: skip
    assert [(t["status"], t["entry_qty"]) for t in share_trips(rig)] == [
        ("unmatched", 10), ("open", 5)]  # fmt: skip


def test_a_wrong_count_is_put_right_the_next_night_and_a_part_sale_runs_off_what_is_held(rig):
    from sudarshan.journal.core.pairing import unclosed

    bought = buy_shares(rig)
    night(rig, BACK, ok(held(0)))  # a read that was wrong: the account holds them
    assert unclosed(share_trips(rig)[0])
    line = night(rig, 20261123, ok(held(10)))
    assert "1 counted again" in line
    (trip,) = share_trips(rig)
    assert (trip["id"], trip["status"], trip["closed_ms"]) == (bought["id"], "open", None)
    assert "0 counted again" in night(rig, 20261124, ok(held(10)))  # agreed: nothing to do
    # 6 of the 10 leave the account unseen; the 4 left are sold live two days later
    night(rig, 20261125, ok(held(4)))
    assert unclosed(share_trips(rig)[0])
    rig.now = ist_ms(20261127, "10:06:00")
    record(rig, [trade_row("s1", "os1", key=STOCK["token"], symbol="ACME", product="D",
                           side="SELL", qty=4, price_u=2_200_000,
                           at=ist_ms(20261127, "10:05:00"))])  # fmt: skip
    assert [(t["status"], t["side"], t["entry_qty"], t["exit_qty"], t["close_day"])
            for t in share_trips(rig)] == [
        ("unmatched", "long", 10, 0, 20261125), ("unmatched", "long", 0, 4, 20261127)]  # fmt: skip


def test_the_count_waits_for_a_night_the_share_was_not_traded_and_for_holdings_that_read(rig):
    buy_shares(rig)
    first_sight = asked_holdings(rig)
    # the night of the purchase: what holdings say on the day of a trade is not relied on
    buy = trade_row("b1", "ob1", key=STOCK["token"], symbol="ACME", product="D", side="BUY",
                    qty=10, price_u=2_000_000, at=ist_ms(DAY, "10:05:00"))  # fmt: skip
    line = night(rig, DAY, ok(held(0)), today=[buy])
    assert "holdings: no open share trade to check" in line
    assert asked_holdings(rig) == first_sight and share_trips(rig)[0]["status"] == "open"
    # holdings that could not be read end nothing, and the same night's next run asks again
    line = night(rig, BACK, {"state": "failed", "read_at": 1, "note": "down", "data": None})
    assert "holdings: not read (the holdings could not be read)" in line
    assert share_trips(rig)[0]["status"] == "open"
    assert "1 counted again" in night(rig, BACK, ok(held(0)))


def test_a_sale_the_history_brings_closes_the_trade_and_nothing_is_counted(rig):
    """Off for two days only: the 22:00 history has the sale, so the trade closes with its
    result and the holdings are not asked about it."""
    buy_shares(rig)
    first_sight = asked_holdings(rig)
    sale = share_row("901", 20261111, "SELL", 10, 2_100_000)
    line = night(rig, 20261112, ok(held(0)), history=[sale])
    (trip,) = share_trips(rig)
    assert (trip["status"], trip["gross_paise"], trip["close_day"]) == ("closed", 10_000, 20261111)
    assert "holdings: no open share trade to check" in line
    assert asked_holdings(rig) == first_sight


def test_todays_trades_unread_leave_the_count_for_a_later_run(rig):
    buy_shares(rig)
    rig.answer_trades([], state="failed")
    rig.portfolio.answers["holdings"] = ok(held(0))
    rig.portfolio.answers["historical_trades"] = {"state": "ok", "read_at": 1, "note": None,
                                                  "total_pages": 1, "total_records": 0, "rows": []}  # fmt: skip
    rig.portfolio.answers["pnl_report"] = {"state": "ok", "read_at": 1, "rows": [],
                                           "page_size": 5000, "charges": {}}  # fmt: skip
    rig.now = ist_ms(BACK, "22:00:00")
    with pytest.raises(JobFailed) as caught:
        scenario(rig, lambda: catchup.catch_up(rig.svc, BACK, rig.now, False))
    assert "holdings: not checked tonight" in str(caught.value)
    assert share_trips(rig)[0]["status"] == "open"


def closing_of(rig, day_):
    return rig.svc.store.read(OWNER, lambda tx: tx.day_close_get("live", day_))


def test_a_catch_up_whose_read_of_today_fails_is_failed(rig):
    rig.answer_trades([], state="unavailable")
    rig.portfolio.answers["historical_trades"] = {"state": "ok", "rows": [], "total_pages": 1}
    with pytest.raises(JobFailed) as caught:
        scenario(rig, lambda: catchup.catch_up(rig.svc, DAY, rig.now, False))
    assert str(caught.value).startswith("Today's trades could not be read at 22:00")


def test_history_symbols_and_fills():
    assert catchup.history_symbol({"segment": "FO", "symbol": "acme", "expiry": 20261126,
                                   "option_type": "CE", "strike_price_u": 2_925_000}) == (
        "ACME26NOV26292.5CE"
    )  # fmt: skip
    assert catchup.history_symbol({"segment": "FO", "symbol": "NIFTY", "expiry": 20261125,
                                   "option_type": None}) == "NIFTY25NOV26FUT"  # fmt: skip
    assert catchup.history_symbol({"segment": "EQ", "symbol": "ACME"}) == "ACME"
    assert catchup.history_symbol({"segment": "FO", "symbol": "NIFTY"}) is None
    row = history_row("x", 20261106, "BUY", key="NSE_FO|99999", qty_u=1_500_000)
    assert catchup.fill_from_history(row, None, 0) is None  # a part quantity is not modelled
    assert catchup.financial_year(20260331) == 2025 and catchup.financial_year(20260401) == 2026


def test_the_day_jobs_leak_no_descriptor(rig):
    """fd-audit (JB5): the jobs reuse the service's one portfolio client, the store's one writer
    thread and engines that close each connection; a round of all three, eight times."""
    rig.portfolio.answers["historical_trades"] = {"state": "ok", "rows": [], "total_pages": 1}
    rig.portfolio.answers["pnl_report"] = {"state": "ok", "rows": [], "page_size": 5000}
    answer_positions(rig, [position(OPT["token"], "I", 0, realised=100)])
    rig.answer_trades(live_rows())

    async def round_():
        await day.close_day(rig.svc, DAY, CLOSE, False)
        await check.check_day(rig.svc, DAY, CLOSE, False)
        rig.svc.parts.pop("catchup_done", None)
        await catchup.catch_up(rig.svc, DAY, CLOSE, False)

    scenario(rig, round_)  # warm: the store thread, the client, the imports
    watch = FdWatch()
    for _ in range(8):
        scenario(rig, round_)
    watch.assert_clean()
