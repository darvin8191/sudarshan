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
