"""The reports cache under failure (M7.6, M7.7; reports.py): what a failed charges call, a stopped
refresh, a year read before it closed and a budget stop may and may not do to a kept record.

Money is integer paise. A wrong number saved as complete is the worst outcome for tax work, so each
test here pins a "not read" or "partly read" over a figure that cannot be stood behind. Hand-worked
figures: a P&L row bought for 1,000 and sold for 1,100 is 10,000 paise; the fixture's charges total
154.23 is 15,423 paise; its one closed trade is 125,000 paise (1,250.00)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from test_portfolio_import_ops import OWNER, scenario_of
from test_portfolio_reports import _rows, _wire, fake  # noqa: F401  (fixtures)

from sudarshan.portfolio import reports
from sudarshan.portfolio.budget import Budget
from sudarshan.portfolio.core.days import day_of_instant
from sudarshan.portfolio.upstox import Unreadable

IST = timezone(timedelta(hours=5, minutes=30))
HOUR = 3_600_000
#: A budget that never gets in the way.
WIDE = ((1000, 1.0), (10_000, 60.0), (100_000, 1800.0))
#: Per-second and 30-minute buckets wide open, the minute bucket two calls: a stop, not a wait.
MINUTE_OF_TWO = ((100, 1.0), (2, 60.0), (200, 1800.0))
CHARGES_PAISE = 15_423
ONE_TRADE_PAISE = 125_000


def ist(y, m, d, hh=10, mm=0) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=IST).timestamp() * 1000)


BASE = {
    "exchange": "NSE", "segment": "EQ", "option_type": None, "quantity": 10, "amount": 1000.0,
    "trade_id": "X", "trade_date": "2026-05-04", "transaction_type": "BUY",
    "scrip_name": "ALPHA LTD", "strike_price": None, "expiry": None, "price": 100.0,
    "isin": "INE000A01011", "symbol": "ALPHA", "instrument_token": "NSE_EQ|INE000A01011",
}  # fmt: skip


def trow(tid, day="2026-05-04", **more):
    return {**BASE, "trade_id": tid, "trade_date": day, **more}


def page(rows, total_pages=None, total_records=None, block="metadata"):
    meta = {"page": {"page_number": 1, "page_size": len(rows)}}
    if total_pages is not None:
        meta["page"]["total_pages"] = total_pages
    if total_records is not None:
        meta["page"]["total_records"] = total_records
    return {"status": "success", "data": rows, block: meta}


def prow(name, buy, sell, qty=10):
    return {
        "quantity": qty, "isin": "INE000A01011", "scrip_name": name, "trade_type": "EQ",
        "buy_date": "04-05-2026", "buy_average": buy // qty, "sell_date": "05-05-2026",
        "sell_average": sell // qty, "buy_amount": buy, "sell_amount": sell,
    }  # fmt: skip


def meta_of(count, size):
    return {"status": "success", "data": {"trades_count": count, "page_size_limit": size}}


@pytest.fixture
def small_pages(monkeypatch):
    monkeypatch.setattr(reports, "TRADES_PAGE_SIZE", 2)


def record_of(rig, kind, fy, segment):
    return rig.svc.store.read(OWNER, lambda tx: tx.report_get(kind, fy, segment))


async def settle(rig):
    if rig.svc.reports is not None:
        await asyncio.gather(*rig.svc.reports._tasks.values(), return_exceptions=True)


def close_of_day(rig, at):
    day = day_of_instant(at)
    rig.svc.store.write(OWNER, lambda tx: tx.job_put("eod", day, "ok", "taken", at))


# ---- HIGH-1: a failed charges call never doubles the realised P&L ------------------------------------
@scenario_of
async def test_a_failed_charges_call_is_followed_by_the_charges_alone_never_page_one_again(rig):
    rig.svc.upstox.budget = Budget(limits=WIDE)
    rig.fake.status["charges"] = 503
    first = (await rig.call("report_pnl", fy=2024, segment="EQ"))["data"]
    assert first["state"] == "partial" and first["note"] == reports.WORDS["silent"]
    assert len(first["rows"]) == 1 and first["net_paise"] is None  # no net without the charges
    rig.fake.status.pop("charges")
    second = (await rig.call("report_pnl", fy=2024, segment="EQ"))["data"]
    assert second["state"] == "ok" and second["note"] is None
    assert len(second["rows"]) == 1 and second["realised_paise"] == ONE_TRADE_PAISE
    assert second["net_paise"] == ONE_TRADE_PAISE - CHARGES_PAISE
    assert [q["page_number"] for q in _rows(rig.fake, "pnl")] == ["1"]  # page 1 once
    assert rig.fake.count("charges") == 2 and rig.fake.count("pnl_meta") == 1
    rig.now += 100 * 24 * HOUR  # a year read complete after it settled is final
    later = (await rig.call("report_pnl", fy=2024, segment="EQ"))["data"]
    assert later["realised_paise"] == ONE_TRADE_PAISE and rig.fake.count() == 4


@scenario_of
async def test_two_nightly_reads_never_double_the_current_year_after_one_failed_charges_call(rig):
    rig.svc.upstox.budget = Budget(limits=WIDE)
    rig.now = ist(2026, 10, 22, 12, 0)
    first = (await rig.call("report_pnl", fy=2026, segment="EQ"))["data"]
    assert first["state"] == "ok" and first["realised_paise"] == ONE_TRADE_PAISE
    for day, fail in ((23, True), (26, False)):  # Friday's charges fail; Monday's are fine
        rig.now = ist(2026, 10, day, 16, 0)
        close_of_day(rig, rig.now)
        if fail:
            rig.fake.status["charges"] = 503
        else:
            rig.fake.status.pop("charges", None)
        await reports.reports_step(rig.svc, rig.now)
        await settle(rig)
        rec = record_of(rig, "pnl", 2026, "EQ")
        assert len(rec["payload"]["rows"]) == 1  # never two
    data = (await rig.call("report_pnl", fy=2026, segment="EQ"))["data"]
    assert data["state"] == "ok" and data["realised_paise"] == ONE_TRADE_PAISE
    assert data["net_paise"] == ONE_TRADE_PAISE - CHARGES_PAISE


@scenario_of
async def test_a_pnl_cut_by_the_budget_carries_on_page_by_page_and_reads_the_charges_last(rig):
    t = [1000.0]
    rig.svc.upstox.budget = Budget(limits=((1, 1.0), (30, 60.0), (200, 1800.0)), clock=lambda: t[0])
    rig.fake.bodies["pnl_meta"] = meta_of(3, 1)
    rig.fake.bodies["pnl"] = [
        page([prow("P1", 1000, 1100)]),
        page([prow("P2", 2000, 2100)]),
        page([prow("P3", 3000, 3100)]),
    ]
    seen = []
    for _ in range(6):
        d = (await rig.call("report_pnl", fy=2025, segment="FO"))["data"]
        seen.append((d["state"], len(d["rows"])))
        if d["state"] != "ok":
            assert rig.fake.count("charges") == 0  # no charges while pages are missing
        t[0] += 1.0
        if d["state"] == "ok":
            break
    assert seen == [("partial", 1), ("partial", 2), ("ok", 3)]
    assert [q["page_number"] for q in _rows(rig.fake, "pnl")] == ["1", "2", "3"]
    assert rig.fake.count("charges") == 1 and rig.fake.count("pnl_meta") == 1
    assert d["realised_paise"] == 30_000  # 3 x 10,000 paise, worked by hand


@scenario_of
async def test_a_pnl_of_three_trades_at_two_a_page_is_two_pages_not_one(rig):
    rig.svc.upstox.budget = Budget(limits=WIDE)
    rig.fake.bodies["pnl_meta"] = meta_of(3, 2)
    rig.fake.bodies["pnl"] = [
        page([prow("P1", 1000, 1100), prow("P2", 2000, 2100)]),
        page([prow("P3", 3000, 2900)]),
    ]
    d = (await rig.call("report_pnl", fy=2025, segment="FO"))["data"]
    assert d["state"] == "ok" and len(d["rows"]) == 3
    assert [q["page_number"] for q in _rows(rig.fake, "pnl")] == ["1", "2"]
    assert d["realised_paise"] == 10_000 + 10_000 - 10_000  # the last trade lost 100.00


@scenario_of
async def test_a_two_page_pnl_adds_up_and_nets_the_charges(rig):
    rig.svc.upstox.budget = Budget(limits=WIDE)
    rig.fake.bodies["pnl_meta"] = meta_of(2, 1)
    rig.fake.bodies["pnl"] = [page([prow("P1", 1000, 1100)]), page([prow("P2", 2000, 2100)])]
    d = (await rig.call("report_pnl", fy=2025, segment="EQ"))["data"]
    assert d["state"] == "ok" and d["trades_count"] == 2
    assert d["realised_paise"] == 20_000 and d["net_paise"] == 20_000 - CHARGES_PAISE == 4_577


@scenario_of
async def test_rows_that_do_not_match_the_count_are_never_saved_as_complete(rig):
    rig.svc.upstox.budget = Budget(limits=WIDE)
    rig.fake.bodies["pnl_meta"] = meta_of(1, 5000)
    rig.fake.bodies["pnl"] = page([prow("P1", 1000, 1100), prow("P2", 2000, 2100)])
    d = (await rig.call("report_pnl", fy=2025, segment="EQ"))["data"]
    assert d["state"] == "partial" and d["note"] == reports.WORDS["count"]
    assert rig.fake.count("charges") == 0 and d["net_paise"] is None
    rec = record_of(rig, "pnl", 2025, "EQ")
    assert rec["payload"]["next_page"] is None and rec["payload"]["pages_done"] is False


@scenario_of
async def test_a_partial_pnl_from_the_old_reader_is_read_afresh_not_onto_its_rows(rig):
    """A record 8d75b1c wrote (no pages_done, every row in, next_page none) is read from page 1
    with no rows kept: its rows cannot be told from a second copy."""
    rig.svc.upstox.budget = Budget(limits=WIDE)
    old_row = reports.parse_pnl_row(prow("P1", 1000, 1100))
    rig.svc.store.write(
        OWNER,
        lambda tx: tx.report_put(
            "pnl", 2025, "EQ", "partial", None, "x",
            {"rows": [old_row], "trades_count": 1, "page_size": 5000, "charges": None,
             "next_page": None},
            rig.now,
        ),
    )  # fmt: skip
    rig.fake.bodies["pnl_meta"] = meta_of(1, 5000)
    rig.fake.bodies["pnl"] = page([prow("P1", 1000, 1100)])
    d = (await rig.call("report_pnl", fy=2025, segment="EQ"))["data"]
    assert d["state"] == "ok" and len(d["rows"]) == 1 and d["realised_paise"] == 10_000


# ---- MEDIUM-1: a refresh that stops never shrinks a complete year -------------------------------------
@scenario_of
async def test_a_refresh_that_fails_part_way_keeps_the_complete_year_and_says_so(rig):
    reports.TRADES_PAGE_SIZE, kept = 2, reports.TRADES_PAGE_SIZE
    try:
        rig.svc.upstox.budget = Budget(limits=WIDE)
        p1, p2, p3 = (
            page([trow("A"), trow("B")], 3, 6),
            page([trow("C"), trow("D")], 3, 6),
            page([trow("E"), trow("F")], 3, 6),
        )
        rig.fake.bodies["trades"] = [p1, p2, p3]
        full = (await rig.call("report_trades", fy=2026, segment="FO"))["data"]
        assert full["state"] == "ok" and len(full["rows"]) == 6
        rig.now += reports.CURRENT_MAX_AGE_MS + 60_000
        rig.fake.bodies["trades"] = [p1, {"status": "error", "errors": [{"message": "x"}]}, p3]
        again = (await rig.call("report_trades", fy=2026, segment="FO"))["data"]
    finally:
        reports.TRADES_PAGE_SIZE = kept
    assert again["state"] == "ok" and len(again["rows"]) == 6
    assert again["note"] == reports.WORDS["kept"] and again["read_at"] == full["read_at"]
    assert again["bought_paise"] == full["bought_paise"] == 6 * 100_000


@scenario_of
async def test_a_refresh_stopped_by_the_minute_budget_keeps_the_complete_year(rig):
    reports.TRADES_PAGE_SIZE, kept = 2, reports.TRADES_PAGE_SIZE
    try:
        rig.svc.upstox.budget = Budget(limits=WIDE)
        rig.fake.bodies["trades"] = [
            page([trow("A"), trow("B")], 3, 6),
            page([trow("C"), trow("D")], 3, 6),
            page([trow("E"), trow("F")], 3, 6),
        ]
        full = (await rig.call("report_trades", fy=2026, segment="FO"))["data"]
        rig.now += reports.CURRENT_MAX_AGE_MS + 60_000
        rig.svc.upstox.budget = Budget(limits=MINUTE_OF_TWO)  # page 3 finds the minute empty
        again = (await rig.call("report_trades", fy=2026, segment="FO"))["data"]
    finally:
        reports.TRADES_PAGE_SIZE = kept
    assert [q["page_number"] for q in _rows(rig.fake, "trades")][3:] == ["1", "2"]
    assert again["state"] == "ok" and len(again["rows"]) == 6
    assert again["read_at"] == full["read_at"] and again["note"] == reports.WORDS["kept"]


@scenario_of
async def test_a_pnl_refresh_whose_metadata_fails_keeps_the_complete_year(rig):
    rig.svc.upstox.budget = Budget(limits=WIDE)
    full = (await rig.call("report_pnl", fy=2026, segment="EQ"))["data"]
    assert full["state"] == "ok"
    rig.now += reports.CURRENT_MAX_AGE_MS + 60_000
    rig.fake.status["pnl_meta"] = 503
    again = (await rig.call("report_pnl", fy=2026, segment="EQ"))["data"]
    assert again["state"] == "ok" and len(again["rows"]) == 1
    assert again["realised_paise"] == ONE_TRADE_PAISE and again["charges"] == full["charges"]
    assert again["note"] == reports.WORDS["kept"] and again["read_at"] == full["read_at"]


@scenario_of
async def test_a_refresh_that_completes_replaces_the_year_and_clears_the_note(rig):
    rig.svc.upstox.budget = Budget(limits=WIDE)
    rig.fake.bodies["trades"] = page([trow("A")], 1, 1)
    await rig.call("report_trades", fy=2026, segment="EQ")
    rig.now += reports.CURRENT_MAX_AGE_MS + 60_000
    rig.fake.status["trades"] = 503
    kept = (await rig.call("report_trades", fy=2026, segment="EQ"))["data"]
    assert kept["note"] == reports.WORDS["kept"]
    rig.fake.status.pop("trades")
    rig.fake.bodies["trades"] = page([trow("A"), trow("B")], 1, 2)
    now = (await rig.call("report_trades", fy=2026, segment="EQ"))["data"]
    assert now["note"] is None and len(now["rows"]) == 2 and now["read_at"] == rig.now


# ---- HIGH-2: a year read before it closed is read again, then final -----------------------------------
@scenario_of
async def test_a_year_read_on_its_last_day_is_read_again_after_it_settles_and_then_is_final(rig):
    rig.svc.upstox.budget = Budget(limits=WIDE)
    rig.now = ist(2026, 3, 31, 11, 0)
    rig.fake.bodies["trades"] = page([trow("A", "2026-03-30"), trow("B", "2026-03-31")], 1, 2)
    first = (await rig.call("report_trades", fy=2025, segment="EQ"))["data"]
    assert first["state"] == "ok" and len(first["rows"]) == 2
    rig.fake.bodies["trades"] = page(
        [trow("A", "2026-03-30"), trow("B", "2026-03-31"), trow("C", "2026-03-31")], 1, 3
    )
    rig.now = ist(2026, 4, 4, 10, 0)  # past the 3rd of April: settled
    later = (await rig.call("report_trades", fy=2025, segment="EQ"))["data"]
    assert later["state"] == "ok" and len(later["rows"]) == 3 and later["read_at"] == rig.now
    assert later["note"] is None
    rig.now = ist(2027, 1, 15, 10, 0)
    again = (await rig.call("report_trades", fy=2025, segment="EQ"))["data"]
    assert len(again["rows"]) == 3 and rig.fake.count("trades") == 2  # final: not asked again


@scenario_of
async def test_a_year_read_before_it_settles_says_so_until_it_is_read_again(rig):
    rig.svc.upstox.budget = Budget(limits=WIDE)
    rig.now = ist(2026, 3, 31, 23, 0)
    await rig.call("report_trades", fy=2025, segment="EQ")
    rig.now = ist(2026, 4, 1, 1, 0)  # two hours on: not due by age, not settled yet
    early = (await rig.call("report_trades", fy=2025, segment="EQ"))["data"]
    assert early["state"] == "ok" and early["note"] == reports.WORDS["early"]
    assert "Read again" in early["note"] and rig.fake.count("trades") == 1


@scenario_of
async def test_the_last_nightly_read_of_a_year_is_not_its_last_the_evening_trades_follow(rig):
    rig.svc.upstox.budget = Budget(limits=WIDE)
    mcx = lambda *ids: [  # noqa: E731
        trow(i, "2026-03-3" + i[-1], segment="COM", exchange="MCX") for i in ids
    ]
    rig.now = ist(2026, 3, 30, 18, 0)
    rig.fake.bodies["trades"] = page(mcx("M0"), 1, 1)
    await rig.call("report_trades", fy=2025, segment="COM")
    rig.now = ist(2026, 3, 31, 16, 0)  # the year's last nightly, before the 23:30 MCX close
    close_of_day(rig, rig.now)
    await reports.reports_step(rig.svc, rig.now)
    await settle(rig)
    rig.fake.bodies["trades"] = page(mcx("M0", "M1"), 1, 2)
    asked = rig.fake.count("trades")
    rig.now = ist(2026, 4, 2, 16, 0)  # 2 April: not settled, so tonight leaves the old year alone
    close_of_day(rig, rig.now)
    await reports.reports_step(rig.svc, rig.now)
    await settle(rig)
    assert {q["start_date"] for _, q in rig.fake.queries[asked:]} <= {"2026-04-01"}
    rig.now = ist(2026, 4, 4, 16, 0)  # settled: the nightly step reads FY 2025-26 again, unasked
    close_of_day(rig, rig.now)
    await reports.reports_step(rig.svc, rig.now)
    await settle(rig)
    rec = record_of(rig, "trades", 2025, "COM")
    assert [r["trade_id"] for r in rec["payload"]["rows"]] == ["M0", "M1"]
    assert day_of_instant(rec["read_at"]) == 20260404


@scenario_of
async def test_read_again_reads_any_year_inside_the_budget_and_never_loses_the_old_figures(rig):
    rig.svc.upstox.budget = Budget(limits=WIDE)
    rig.fake.bodies["trades"] = page([trow("A")], 1, 1)
    await rig.call("report_trades", fy=2024, segment="EQ")  # final: read after it settled
    await rig.call("report_trades", fy=2024, segment="EQ")
    assert rig.fake.count("trades") == 1
    rig.fake.bodies["trades"] = page([trow("A"), trow("B")], 1, 2)
    again = (await rig.call("report_trades", fy=2024, segment="EQ", force=True))["data"]
    assert rig.fake.count("trades") == 2 and len(again["rows"]) == 2
    pnl = (await rig.call("report_pnl", fy=2024, segment="EQ", force=True))["data"]
    assert pnl["state"] == "ok" and rig.fake.count("pnl") == 1
    # The budget still limits it: with the minute spent, nothing is sent and the old stays.
    spent = Budget(limits=((100, 1.0), (1, 60.0), (200, 1800.0)))
    assert spent.take("trades")
    rig.svc.upstox.budget = spent
    rig.fake.bodies["trades"] = page([trow("Z")], 1, 1)
    kept = (await rig.call("report_trades", fy=2024, segment="EQ", force=True))["data"]
    assert rig.fake.count("trades") == 2 and len(kept["rows"]) == 2
    assert kept["state"] == "ok" and kept["note"] == reports.WORDS["kept"]


@scenario_of
async def test_a_read_in_flight_is_waited_for_by_a_plain_open_even_when_the_year_is_complete(rig):
    rig.svc.upstox.budget = Budget(limits=WIDE)
    await rig.call("report_trades", fy=2024, segment="EQ")
    kept, reports.ANSWER_WITHIN_S = reports.ANSWER_WITHIN_S, 0.2
    try:
        rig.fake.delay = 0.7
        forced = (await rig.call("report_trades", fy=2024, segment="EQ", force=True))["data"]
        plain = (await rig.call("report_trades", fy=2024, segment="EQ"))["data"]
        assert forced["state"] == "reading" and plain["state"] == "reading"
        await asyncio.sleep(1.0)
        rig.fake.delay = 0.0
        done = (await rig.call("report_trades", fy=2024, segment="EQ"))["data"]
    finally:
        reports.ANSWER_WITHIN_S = kept
    assert done["state"] == "ok" and rig.fake.count("trades") == 2  # one forced read, shared


# ---- the nightly step: only a record read after the closing record is left alone ---------------------
@scenario_of
async def test_the_nightly_step_rereads_a_record_read_before_the_close_and_skips_one_read_after(
    rig,
):
    rig.svc.upstox.budget = Budget(limits=WIDE)
    rig.now = ist(2026, 10, 26, 15, 0)
    await rig.call("report_trades", fy=2026, segment="EQ")  # read before tonight's close
    close_of_day(rig, ist(2026, 10, 26, 15, 56))
    rig.now = ist(2026, 10, 26, 16, 10)
    await rig.call("report_trades", fy=2026, segment="FO")  # read after it
    asked = rig.fake.count("trades")
    rig.now = ist(2026, 10, 26, 16, 20)
    assert await reports.reports_step(rig.svc, rig.now) == "ok"
    await settle(rig)
    assert {q["segment"] for p, q in rig.fake.queries[2:] if p == "trades"} == {"EQ"}
    assert rig.fake.count("trades") == asked + 1


# ---- MEDIUM-2: the per-second budget is waited for; the minute one is a stop in words -----------------
@scenario_of
async def test_quick_pages_are_read_in_one_go_waiting_out_the_per_second_budget(rig):
    reports.TRADES_PAGE_SIZE, kept = 2, reports.TRADES_PAGE_SIZE
    try:
        rig.fake.bodies["trades"] = [
            page([trow("A"), trow("B")], 3, 5),
            page([trow("C"), trow("D")], 3, 5),
            page([trow("E")], 3, 5),
        ]
        data = (await rig.call("report_trades", fy=2026, segment="FO"))["data"]
    finally:
        reports.TRADES_PAGE_SIZE = kept
    assert data["state"] == "ok" and len(data["rows"]) == 5 and data["note"] is None
    assert [q["page_number"] for q in _rows(rig.fake, "trades")] == ["1", "2", "3"]


@scenario_of
async def test_a_minute_budget_stop_says_what_happens_next_and_the_next_read_carries_on(rig):
    reports.TRADES_PAGE_SIZE, kept = 2, reports.TRADES_PAGE_SIZE
    try:
        rig.fake.bodies["trades"] = [
            page([trow("A"), trow("B")], 3, 6),
            page([trow("C"), trow("D")], 3, 6),
            page([trow("E"), trow("F")], 3, 6),
        ]
        rig.svc.upstox.budget = Budget(limits=MINUTE_OF_TWO)
        cut = (await rig.call("report_trades", fy=2026, segment="FO"))["data"]
        assert cut["state"] == "partial" and len(cut["rows"]) == 4
        assert cut["note"] == reports.WORDS["busy"]
        rig.svc.upstox.budget = Budget(limits=WIDE)  # the minute has passed
        done = (await rig.call("report_trades", fy=2026, segment="FO"))["data"]
    finally:
        reports.TRADES_PAGE_SIZE = kept
    assert done["state"] == "ok" and [r["trade_id"] for r in done["rows"]] == list("ABCDEF")
    assert [q["page_number"] for q in _rows(rig.fake, "trades")] == ["1", "2", "3"]


def test_no_word_promises_a_follow_up_that_does_not_come():
    for key in ("slow", "busy", "more", "short", "count"):
        text = reports.WORDS[key]
        assert "follows in a minute" not in text and "button below" in text, key
    assert "Wait a minute" in reports.WORDS["busy"] and "Wait a minute" in reports.WORDS["slow"]
    assert reports.WORDS["no_pnl_mf"].endswith("in Trades.")


def test_the_budget_waits_only_for_the_per_second_bucket():
    t = [100.0]
    budget = Budget(limits=((2, 1.0), (30, 60.0), (200, 1800.0)), clock=lambda: t[0])
    assert budget.take("trades") and budget.take("trades") and not budget.take("trades")
    wait = budget.wait_s("trades")
    assert wait is not None and 0.5 <= wait <= 0.6  # half a second for one token at 2 a second
    t[0] += wait
    assert budget.take("trades")
    longer = Budget(limits=((2, 1.0), (2, 60.0), (200, 1800.0)), clock=lambda: t[0])
    assert longer.take("x") and longer.take("x") and longer.wait_s("x") is None  # a minute: stop
    longer.pause("y")
    assert longer.wait_s("y") is None
    assert Budget(limits=((0, 1.0),), clock=lambda: t[0]).wait_s("z") is None  # never refills


# ---- MEDIUM-3: a short page does not end the history while Upstox says more follow ----------------------
@scenario_of
async def test_a_short_page_is_not_the_end_while_total_pages_says_more(rig):
    rig.fake.bodies["trades"] = [
        page([trow("A"), trow("B")], 3, 6),
        page([trow("C"), trow("D")], 3, 6),
        page([trow("E"), trow("F")], 3, 6),
    ]
    data = (await rig.call("report_trades", fy=2026, segment="EQ"))["data"]
    assert data["state"] == "ok" and len(data["rows"]) == 6 and data["total_records"] == 6
    assert [q["page_number"] for q in _rows(rig.fake, "trades")] == ["1", "2", "3"]


@scenario_of
async def test_fewer_trades_than_upstox_counted_is_partly_read_not_complete(rig):
    rig.fake.bodies["trades"] = page([trow("A"), trow("B")], 1, 5)
    data = (await rig.call("report_trades", fy=2026, segment="EQ"))["data"]
    assert data["state"] == "partial" and data["note"] == reports.WORDS["short"]
    assert data["total_records"] == 5 and len(data["rows"]) == 2


@scenario_of
async def test_the_meta_data_block_of_the_examples_gives_the_total_and_is_checked(rig):
    rig.fake.bodies["trades"] = page([trow("A"), trow("B")], 1, 2, block="meta_data")
    ok = (await rig.call("report_trades", fy=2026, segment="EQ"))["data"]
    assert ok["state"] == "ok" and ok["total_records"] == 2
    rig.fake.bodies["trades"] = page([trow("A"), trow("B")], 1, 3, block="meta_data")
    short = (await rig.call("report_trades", fy=2026, segment="FO"))["data"]
    assert short["state"] == "partial" and short["total_records"] == 3


@scenario_of
async def test_a_read_cut_at_the_page_cap_is_partial_never_complete(rig):
    reports.TRADES_PAGE_SIZE, kept = 2, reports.TRADES_PAGE_SIZE
    try:
        rig.svc.upstox.budget = Budget(limits=WIDE)
        rig.fake.bodies["trades"] = [
            page([trow("A"), trow("B")], 3),
            page([trow("C"), trow("D")], 3),
            page([trow("E"), trow("F")], 3),
        ]  # no total_records: only the page cap can tell the read is not over
        rig.svc.ensure_reports().pages = 2
        cut = (await rig.call("report_trades", fy=2026, segment="EQ"))["data"]
        assert cut["state"] == "partial" and cut["note"] == reports.WORDS["more"]
        assert len(cut["rows"]) == 4
        rig.svc.ensure_reports().pages = 20
        done = (await rig.call("report_trades", fy=2026, segment="EQ"))["data"]
    finally:
        reports.TRADES_PAGE_SIZE = kept
    assert done["state"] == "ok" and len(done["rows"]) == 6
    assert [q["page_number"] for q in _rows(rig.fake, "trades")] == ["1", "2", "3"]


# ---- the rest: keys, single flight, shapes -------------------------------------------------------------
@scenario_of
async def test_one_trade_number_on_two_exchanges_keeps_both_trades(rig):
    rig.fake.bodies["trades"] = page(
        [trow("1001", exchange="NSE"), trow("1001", exchange="BSE", transaction_type="SELL")], 1, 2
    )
    data = (await rig.call("report_trades", fy=2026, segment="EQ"))["data"]
    assert [(r["exchange"], r["transaction_type"]) for r in data["rows"]] == [
        ("BSE", "SELL"),
        ("NSE", "BUY"),
    ]
    assert data["state"] == "ok"


@scenario_of
async def test_two_opens_at_once_make_one_request(rig):
    rig.fake.delay = 0.5
    a, b = await asyncio.gather(
        rig.call("report_trades", fy=2026, segment="EQ"),
        rig.call("report_trades", fy=2026, segment="EQ"),
    )
    assert rig.fake.count("trades") == 1
    assert a["data"]["state"] == b["data"]["state"] == "ok"
    assert a["data"]["rows"] == b["data"]["rows"]


@scenario_of
async def test_a_refused_token_that_changed_is_told_again_when_it_is_refused_again(rig):
    upstox = rig.svc.upstox
    upstox._refused, upstox._refused_at = True, ("stale",)
    upstox._told.add("401")
    await rig.call("report_trades", fy=2026, segment="EQ")
    assert "401" not in upstox._told and upstox._refused is False


def test_a_date_in_either_of_upstox_s_shapes_is_read_in_both_rows():
    trade = reports.parse_trade(
        {"transaction_type": "BUY", "segment": "EQ", "quantity": 1, "price": 1, "amount": 1,
         "trade_date": "28-03-2023", "expiry": "2023-04-27"}
    )  # fmt: skip
    assert trade["trade_date"] == 20230328 and trade["expiry"] == 20230427
    row = reports.parse_pnl_row(
        {"quantity": 1, "buy_amount": 1, "sell_amount": 2, "buy_average": 1, "sell_average": 2,
         "buy_date": "2023-03-28", "sell_date": "29-03-2023"}
    )  # fmt: skip
    assert row["buy_date"] == 20230328 and row["sell_date"] == 20230329
    with pytest.raises(Unreadable):
        reports.parse_trade(
            {"transaction_type": "BUY", "segment": "EQ", "quantity": 1, "price": 1, "amount": 1,
             "trade_date": "28/03/2023"}
        )  # fmt: skip
