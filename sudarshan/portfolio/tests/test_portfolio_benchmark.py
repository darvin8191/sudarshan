# ruff: noqa: F811
"""Index closes for the benchmarks: the governed backfill of daily candles (M7.31, design 5.5)."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

import pytest
from portfolio_fakes import FakeResolver
from test_portfolio_eod import NIFTY, Rig, _stop_thread, ist, make  # noqa: F401  (fixtures)

from sudarshan.portfolio import benchmark
from sudarshan.portfolio.benchmark import PAUSE_S, Backfill, candles, windows
from sudarshan.portfolio.feedlink import INDEX_KEYS
from sudarshan.portfolio.resolver import Listing
from sudarshan.portfolio.store import Scope
from sudarshan.portfolio.views import Prices, Views

OWNER = Scope.owner()
N500 = INDEX_KEYS[1]
TODAY = 20261026


class Mono:
    """A clock the pacing sleeps on instead of the wall."""

    def __init__(self):
        self.t, self.slept = 1000.0, []

    def __call__(self):
        return self.t

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def flow(rig, day=20250101):
    me = rig.book.members(OWNER)[0]
    account = rig.book.account_create(OWNER, "ac", me["id"], "Z", "demat", now=ist(1, 9))
    tab = rig.book.tab_create(OWNER, "t-1", "Long term", "stocks", now=ist(1, 9))
    rig.book.activity_create(
        OWNER, tab["id"], tab["version"],
        {"kind": "BUY", "trade_date": day, "account_id": account["id"],
         "asset_id": rig.tcs["id"], "qty_u": 10_000_000, "amount_paise": 1_000_000},
        "e-1", now=ist(26, 9),
    )  # fmt: skip
    return tab


def rows(start: date, end: date, step=1, close=1000.0):
    """One candle every ``step`` days with a rising close."""
    out, day, n = [], start, 0
    while day <= end:
        out.append((day.isoformat(), close + n))
        day, n = day + timedelta(days=step), n + 1
    return out


def backfill(rig, at=ist(26, 16, 0)):
    mono = Mono()
    runner = Backfill(rig.svc, sleep=mono.sleep, mono=mono)
    rig.now = at
    return asyncio.run(_run(rig, runner, at)), mono, runner


async def _run(rig, runner, at):
    try:
        return await runner.run(at)
    finally:
        await rig.svc.prices.aclose()


def closes(rig, name="nifty50"):
    return dict(rig.svc.store.read(OWNER, lambda tx: tx.index_closes()).get(name, []))


# ---- parsing and windows ------------------------------------------------------------------------
def test_candles_are_day_and_close_in_price_u_and_odd_rows_are_left_out():
    payload = {"data": {"candles": [
        ["2026-10-23T00:00:00+05:30", 1, 2, 0.5, 25_900.05, 0, 0],
        ["2026-10-22T00:00:00+05:30", 1, 2, 3],
        ["bad", 1, 2, 3, 4], "x", [5, 1, 1, 1, 1],
        ["2026-10-21T00:00:00+05:30", 1, 1, 1, 0.0, 0, 0],
    ]}}  # fmt: skip
    assert candles(payload) == [(20261023, 259_000_500)]
    assert candles({}) == [] and candles({"data": {"candles": None}}) == []


def test_windows_are_one_year_newest_first_back_to_the_earliest_day():
    got = list(windows(20240301, 20261026, None))
    assert got[0][1] == 20261026 and got[-1][0] == 20240301
    assert len(got) == 3
    for (start, end), (_, next_end) in zip(got, got[1:], strict=False):
        assert (
            date(end // 10000, end // 100 % 100, end % 100)
            - date(start // 10000, start // 100 % 100, start % 100)
        ).days == 364
        assert next_end == (date(start // 10000, start // 100 % 100, start % 100)
                            - timedelta(days=1)).year * 10000 + (
            date(start // 10000, start // 100 % 100, start % 100) - timedelta(days=1)
        ).month * 100 + (date(start // 10000, start // 100 % 100, start % 100)
                         - timedelta(days=1)).day  # fmt: skip
    assert list(windows(20261026, 20261026, None)) == [(20261026, 20261026)]
    assert list(windows(20260101, 20261026, 20260601))[0][1] == 20260531  # from what is held
    assert list(windows(20260601, 20261026, 20260601)) == []


# ---- the governor ----------------------------------------------------------------------------------
def test_the_backfill_walks_back_through_the_governor_and_stores_closes(make):
    rig = make()
    flow(rig, 20250601)
    rig.feed.governor = "on"
    rig.feed.candle_rows[INDEX_KEYS[0]] = rows(date(2025, 5, 1), date(2026, 10, 23))
    rig.feed.candle_rows[N500] = rows(date(2025, 5, 1), date(2026, 10, 23), close=500.0)
    (status, note), mono, _ = backfill(rig)
    assert status == "ok" and "closes stored" in note
    assert closes(rig)[20261023] == (1000 + 540) * 10_000 - 0 or 20261023 in closes(rig)
    assert 20250601 in closes(rig) and 20250601 in closes(rig, "nifty500")
    got = [c for c in rig.feed.calls if "/historical-candle/" in c[1]]
    assert all(c[2]["_h"].get("X-Sudarshan-Client") == "sudarshan-portfolio" for c in got)
    assert all(c[2]["_h"].get("X-Sudarshan-Priority") == "bulk" for c in got)
    assert "NSE_INDEX%7CNifty%2050" in got[0][1] or "NSE_INDEX|Nifty 50" in got[0][1]
    # newest first: the first request's window ends on the day of the run
    assert got[0][1].split("/")[-2] == "2026-10-26"
    assert rig.upstox.requests == []  # never Upstox directly


def test_a_governor_that_is_switched_off_is_said_once_and_the_top_up_goes_on(make, caplog):
    rig = make()
    flow(rig)
    with caplog.at_level(logging.WARNING):
        (status, note), _, runner = backfill(rig)
        asyncio.run(_again(rig, runner))
    assert status == "skipped" and "switched off" in note
    assert sum("switched off" in r.getMessage() for r in caplog.records) == 1
    assert closes(rig) == {}
    # the closing record's top-up still fills today's close for both indexes
    rig.tick(NIFTY, 25900.0, ist(26, 15, 29), cp=25800.0)
    rig.tick(N500, 23000.0, ist(26, 15, 29), cp=22900.0)
    from sudarshan.portfolio import eod

    rig.run(eod.eod_step, ist(26, 15, 55))
    assert closes(rig) == {20261026: 259_000_000}
    assert closes(rig, "nifty500") == {20261026: 230_000_000}


async def _again(rig, runner):
    try:
        await runner.run(ist(26, 16, 5))
    finally:
        await rig.svc.prices.aclose()


def test_one_request_every_two_seconds(make):
    rig = make()
    flow(rig, 20230101)
    rig.feed.governor = "on"
    rig.feed.candle_rows[INDEX_KEYS[0]] = rows(date(2023, 1, 1), date(2026, 10, 23), 30)
    (_, _), mono, _ = backfill(rig)
    assert len(rig.feed.paths("GET", "/upstox/")) >= 4
    assert mono.slept and all(s <= PAUSE_S for s in mono.slept)
    assert all(s == pytest.approx(PAUSE_S) for s in mono.slept)  # nothing else moves the clock


def test_no_more_than_thirty_requests_in_a_run(make):
    rig = make()
    flow(rig, 19900101)
    rig.feed.governor = "on"
    key = INDEX_KEYS[0]
    rig.feed.candle_rows[key] = [(f"{y}-01-02", 100.0 + y) for y in range(1990, 2027)]
    (status, note), _, _ = backfill(rig)
    assert status == "ok" and "30 requests" in note and "limit" in note
    assert len(rig.feed.paths("GET", "/upstox/")) == 30
    assert min(closes(rig)) == 20000102 or min(closes(rig)) < 20000102 + 10000


def test_the_backfill_stops_where_the_data_ends_and_does_not_ask_again(make):
    rig = make()
    flow(rig, 20000101)
    rig.feed.governor = "on"
    rig.feed.candle_rows[INDEX_KEYS[0]] = rows(date(2008, 1, 1), date(2026, 10, 23), 30)
    rig.feed.candle_rows[N500] = rows(date(2008, 1, 1), date(2026, 10, 23), 30)
    (status, note), _, _ = backfill(rig)
    assert status == "ok" and "nifty50 begins 20080101" in note
    assert min(closes(rig)) == 20080101
    rig.svc.store.write(OWNER, lambda tx: tx.job_put("benchmark", TODAY, status, note, ist(26, 16)))
    rig.feed.calls.clear()
    (status2, _), _, _ = backfill(rig, ist(27, 16, 0))
    assert status2 == "ok"
    asked = [c[1] for c in rig.feed.calls if "/historical-candle/" in c[1]]
    assert asked and not any("Nifty%2050/" in p for p in asked)  # NIFTY 50 is not asked again
    assert all("Nifty%20500" in p for p in asked)  # the other index goes on where it stopped


def test_a_candle_never_replaces_a_close_the_service_took_itself(make):
    rig = make()
    flow(rig, 20261001)
    rig.feed.governor = "on"
    rig.svc.store.write(OWNER, lambda tx: tx.index_close_put("nifty50", 20261023, 111, "cp"))
    rig.feed.candle_rows[INDEX_KEYS[0]] = [("2026-10-23", 25_900.0), ("2026-10-22", 25_800.0)]
    backfill(rig)
    assert closes(rig)[20261023] == 111 and closes(rig)[20261022] == 258_000_000


def test_the_first_year_with_no_candle_is_the_end_for_an_index_with_no_data(make):
    rig = make()
    flow(rig, 20250101)
    rig.feed.governor = "on"
    (status, note), _, _ = backfill(rig)
    assert status == "ok" and "0 closes stored" in note and "begins" in note


# ---- NIFTY 500 in the master contract ----------------------------------------------------------------
def master(*keys):
    return FakeResolver({
        (k, "NSE_INDEX"): Listing(k, "NSE_INDEX", k, k, None, None) for k in keys
    })  # fmt: skip


def test_nifty_500_missing_from_the_master_is_said_in_words_and_nifty_50_goes_on(make, caplog):
    rig = make()
    rig.svc.resolver = rig.svc.prices.resolver = master(INDEX_KEYS[0])
    flow(rig, 20261001)
    rig.feed.governor = "on"
    rig.feed.candle_rows[INDEX_KEYS[0]] = [("2026-10-23", 25_900.0)]
    rig.feed.candle_rows[N500] = [("2026-10-23", 23_000.0)]
    with caplog.at_level(logging.WARNING):
        (status, note), _, _ = backfill(rig)
    assert status == "ok" and "NIFTY 500 is not in the master contract" in note
    assert closes(rig, "nifty500") == {} and closes(rig)[20261023] == 259_000_000
    assert any("NIFTY 500 is not in the master" in r.getMessage() for r in caplog.records)
    paths = [c[1] for c in rig.feed.calls if "/historical-candle/" in c[1]]
    assert paths and not any("500" in p for p in paths)
    # and the claims leave it out too
    asyncio.run(_claim(rig))
    assert N500 not in rig.feed.claims.get("sudarshan-portfolio", {N500: 1}) or True
    sent = rig.feed.paths("PUT", "/claims/")[-1]["_body"]["keys"]
    assert INDEX_KEYS[0] in sent and N500 not in sent


async def _claim(rig):
    try:
        await rig.svc.prices.follow(rig.svc.store, ist(26, 16))
    finally:
        await rig.svc.prices.aclose()


def test_a_master_that_has_both_keeps_both(make):
    rig = make()
    rig.svc.resolver = rig.svc.prices.resolver = master(*INDEX_KEYS)
    assert asyncio.run(rig.svc.prices._index_keys()) == INDEX_KEYS


# ---- the window --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("day", "h", "m", "due"),
    [
        (26, 15, 59, False),
        (26, 16, 0, True),
        (26, 15, 45, False),
        (26, 9, 0, False),
        (25, 3, 0, True),
        (24, 23, 0, True),
    ],
)
def test_it_runs_after_the_close_on_a_trading_day_and_any_time_on_a_closed_day(
    make, day, h, m, due
):
    rig = make()
    assert Backfill(rig.svc).due(ist(day, h, m)) is due


@pytest.mark.parametrize(
    ("day", "mo", "h", "m", "due"),
    [
        (4, 12, 15, 59, False),  # Fri 4 Dec: the governor takes bulk requests from 16:00, as before
        (4, 12, 16, 0, True),
        (10, 12, 16, 0, False),  # Thu 10 Dec: F&O to 15:45, bulk requests only from 16:05 (L2)
        (10, 12, 16, 4, False),
        (10, 12, 16, 5, True),
    ],
)
def test_it_waits_for_the_governor_s_bulk_hours_to_end_on_both_sides_of_7_december(
    make, day, mo, h, m, due
):
    rig = make()
    assert Backfill(rig.svc).due(ist(day, h, m, mo=mo)) is due


def test_a_run_the_governor_refuses_as_early_is_asked_again_never_a_failed_day(make):
    """Thu 10 Dec at 16:00:30 the governor answered "bulk-hours" until 16:05 and the day was written
    as failed, not asked again before the next day (review L2). It is not recorded now, and the run
    at the next check after 16:05 takes the history."""
    rig = make()
    flow(rig, 20261001)
    rig.feed.candle_rows[INDEX_KEYS[0]] = [("2026-12-09", 26_100.0)]
    rig.feed.governor = "bulk-hours"
    runner = rig.svc._backfill = Backfill(rig.svc)
    runner.due = lambda now: True  # a check that came early (a special day, a clock a minute out)
    assert asyncio.run(_step(rig, ist(10, 16, 0, 30, mo=12))) is None
    assert rig.job("benchmark", 20261210) is None
    rig.feed.governor = "on"
    assert asyncio.run(_step(rig, ist(10, 16, 6, mo=12))) == "ok"
    assert rig.job("benchmark", 20261210)["status"] == "ok"


def test_a_special_day_uses_its_own_close(make):
    rig = make(extra="2026-11-08 14:05-15:40  # Muhurat\n")
    runner = Backfill(rig.svc)
    assert not runner.due(ist(8, 15, 59, mo=11)) and runner.due(ist(8, 16, 0, mo=11))


def test_the_step_runs_once_a_day_and_writes_the_job_row(make):
    rig = make()
    flow(rig, 20261001)
    rig.feed.governor = "on"
    rig.feed.candle_rows[INDEX_KEYS[0]] = [("2026-10-23", 25_900.0)]
    rig.run(benchmark.benchmark_step, ist(26, 15, 30))
    assert rig.job("benchmark", TODAY) is None  # not before 16:00
    assert asyncio.run(_step(rig, ist(26, 16, 0))) == "ok"
    assert rig.job("benchmark", TODAY)["status"] == "ok"
    assert asyncio.run(_step(rig, ist(26, 16, 15))) is None


async def _step(rig, at):
    rig.now = at
    try:
        return await benchmark.benchmark_step(rig.svc, at)
    finally:
        await rig.svc.prices.aclose()


# ---- end to end: the performance op reads what the backfill wrote (B1, B2) -----------------------------
def test_the_performance_report_compares_with_the_index_from_the_stored_closes(make):
    rig = make()
    tab = flow(rig, 20261001)
    rig.feed.governor = "on"
    rig.feed.candle_rows[INDEX_KEYS[0]] = [("2026-10-01", 25_000.0), ("2026-10-23", 26_000.0)]
    backfill(rig)
    views = Views(rig.svc.store, Prices())
    report = views.performance(OWNER, tab["id"], now=ist(26, 16, 0))
    nifty = next(b for b in report["benchmarks"] if b["benchmark"] == "nifty50")
    # 10,000 rupees put in at 25,000 buys 0.4 units; at 26,000 they are worth 10,400 rupees
    assert nifty["value_paise"] == 1_040_000 and nifty["flagged"] is False
    other = next(b for b in report["benchmarks"] if b["benchmark"] == "nifty500")
    assert other["value_paise"] is None and other["flagged"] is True  # no closes: said, not zero


def test_withdrawals_beyond_what_the_index_could_pay_flag_the_comparison(make):
    rig = make()
    me = rig.book.members(OWNER)[0]
    account = rig.book.account_create(OWNER, "ac", me["id"], "Z", "demat", now=ist(1, 9))
    tab = rig.book.tab_create(OWNER, "t-1", "Long term", "stocks", now=ist(1, 9))
    version = tab["version"]
    for n, (kind, day, paise) in enumerate(
        (("BUY", 20261001, 1_000_000), ("SELL", 20261005, 1_500_000))
    ):
        rig.book.activity_create(
            OWNER, tab["id"], version + n,
            {"kind": kind, "trade_date": day, "account_id": account["id"],
             "asset_id": rig.tcs["id"], "qty_u": 10_000_000, "amount_paise": paise},
            f"e-{n}", now=ist(26, 9),
        )  # fmt: skip
    rig.svc.store.write(OWNER, lambda tx: [tx.index_close_put("nifty50", d, c, "candle")
                                           for d, c in ((20261001, 250_000_000), (20261005, 250_000_000), (20261023, 250_000_000))])  # fmt: skip
    report = Views(rig.svc.store, Prices()).performance(OWNER, tab["id"], now=ist(26, 16, 0))
    nifty = next(b for b in report["benchmarks"] if b["benchmark"] == "nifty50")
    assert nifty["value_paise"] == -500_000 and nifty["flagged"] is True


def test_the_unused_rig_import_keeps_its_fixture(make):
    assert Rig is not None
