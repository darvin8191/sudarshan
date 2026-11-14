"""Upstox's trade history and trade-wise P&L (M7.6, M7.7; reports.py): the rows as integers
(reports.json, worked out by hand), the cache per financial year and segment, the budget, the
nightly read and the three ops through the sealed API, against a fake Upstox."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from portfolio_fakes import FakeUpstox
from test_portfolio_import_ops import NOW, OWNER, Rig, scenario_of
from test_portfolio_upstox import TOKEN

from sudarshan.portfolio import migrations, reports
from sudarshan.portfolio.budget import Budget
from sudarshan.portfolio.core.days import day_of_instant
from sudarshan.portfolio.store import DB_FILENAME, PortfolioStore
from sudarshan.portfolio.upstox import UpstoxReader

# Read as the reader reads Upstox: every fraction a Decimal, never a float.
FILE = json.loads(
    (Path(__file__).parent / "vectors" / "reports.json").read_text(encoding="utf-8"),
    parse_float=Decimal,
)
TODAY = day_of_instant(NOW)  # 26 Oct 2026: FY 2026-27
FY = 2026
HOUR = 60 * 60 * 1000


# ---- the rows as integers (the vectors) --------------------------------------------------------------
@pytest.mark.parametrize("case", FILE["trades"], ids=lambda c: c["id"])
def test_a_trade_row_becomes_the_vectors_integers(case):
    assert reports.parse_trade(case["raw"]) == case["expect"]


@pytest.mark.parametrize("case", FILE["pnl"], ids=lambda c: c["id"])
def test_a_pnl_row_becomes_the_vectors_integers(case):
    assert reports.parse_pnl_row(case["raw"]) == case["expect"]


@pytest.mark.parametrize("case", FILE["charges"], ids=lambda c: c["id"])
def test_the_charges_become_the_vectors_integers_and_null_stays_null(case):
    assert reports.parse_charges(case["raw"]) == case["expect"]


@pytest.mark.parametrize("case", FILE["meta"], ids=lambda c: c["id"])
def test_the_metadata_is_read_as_whole_numbers(case):
    assert reports.parse_meta(case["raw"]) == case["expect"]


def test_the_vectors_cover_every_segment_shape_and_a_null_charge():
    segments = {c["raw"]["segment"] for c in FILE["trades"]}
    assert {"EQ", "FO", "MF"} <= segments
    assert any(c["expect"]["ipft_paise"] is None for c in FILE["charges"])
    assert any(c["expect"]["pnl_paise"] < 0 for c in FILE["pnl"])
    for case in FILE["trades"] + FILE["pnl"]:
        for key, value in case["expect"].items():
            assert isinstance(value, (int, str)) or value is None, (case["id"], key)  # no fraction


@pytest.mark.parametrize(
    "raw",
    [
        {"transaction_type": "HOLD", "segment": "EQ", "quantity": 1, "price": 1, "amount": 1, "trade_date": "2026-01-01"},
        {"transaction_type": "BUY", "segment": "XX", "quantity": 1, "price": 1, "amount": 1, "trade_date": "2026-01-01"},
        {"transaction_type": "BUY", "segment": "EQ", "quantity": None, "price": 1, "amount": 1, "trade_date": "2026-01-01"},
        {"transaction_type": "BUY", "segment": "EQ", "quantity": 1, "price": 1, "amount": 1, "trade_date": "2026-13-01"},
    ],
    ids=["side", "segment", "quantity", "date shape"],
)  # fmt: skip
def test_a_row_that_is_not_a_trade_is_refused_not_guessed(raw):
    from sudarshan.portfolio.upstox import Unreadable

    with pytest.raises(Unreadable):
        reports.parse_trade(raw)


# ---- the years ---------------------------------------------------------------------------------------
def test_the_last_three_financial_years_newest_first_and_upstox_s_code():
    years = reports.years_of(20261108)
    assert [y["fy"] for y in years] == [2026, 2025, 2024]
    assert years[0] == {"fy": 2026, "label": "FY 2026-27", "current": True}
    assert years[1]["current"] is False
    assert reports.fy_code(2026) == "2627" and reports.fy_code(2099) == "9900"
    assert reports.fy_span(2026) == (20260401, 20270331)
    assert reports.year_ok(2024, 20261108) and not reports.year_ok(2023, 20261108)
    assert not reports.year_ok(2027, 20261108)
    assert reports.years_of(20270115)[0]["fy"] == 2026  # January is still FY 2026-27


# ---- the migration -----------------------------------------------------------------------------------
def test_a_populated_v2_file_gains_the_cache_and_keeps_every_row(tmp_path):
    from sudarshan.portfolio import store as store_module
    from sudarshan.portfolio.book import Book

    path = tmp_path / "s" / DB_FILENAME
    made = PortfolioStore(path, now=NOW)
    Book(made).tab_create(OWNER, "t-1", "Long term", "stocks", now=NOW)
    made.close()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE report_cache")
        conn.execute("PRAGMA user_version = 2")
    with sqlite3.connect(path) as conn:
        before = conn.execute("SELECT id, name FROM tab ORDER BY id").fetchall()
    again = PortfolioStore(path, now=NOW + 1)
    assert again.applied == [3, 4, 5] and migrations.LATEST == 5
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT id, name FROM tab ORDER BY id").fetchall() == before
        assert conn.execute("SELECT count(*) FROM report_cache").fetchone() == (0,)
    again.write(OWNER, lambda tx: tx.report_put("trades", 2026, "EQ", "ok", NOW, None,
                                                {"rows": []}, NOW))  # fmt: skip
    assert again.read(OWNER, lambda tx: tx.report_get("trades", 2026, "EQ"))["payload"] == {
        "rows": []
    }
    again.close()
    store_module.stop()


# ---- the reader through the sealed API ------------------------------------------------------------------
@pytest.fixture
def fake():
    server = FakeUpstox()
    server.start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _wire(fake, tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text(TOKEN)
    orig = Rig.__init__

    def init(self, path):
        orig(self, path)
        self.svc.upstox = UpstoxReader(token, fake.url, clock=lambda: self.now)
        self.fake = fake

    monkeypatch.setattr(Rig, "__init__", init)


def _rows(fake, part):
    return [q for p, q in fake.queries if p == part]


@scenario_of
async def test_the_trade_history_is_read_once_per_year_and_segment_and_answered_as_integers(rig):
    answer = await rig.call("report_trades", fy=FY, segment="EQ")
    data = answer["data"]
    assert data["state"] == "ok" and data["read_at"] == NOW and data["note"] is None
    assert data["fy"] == FY and data["segment"] == "EQ"
    assert [r["trade_id"] for r in data["rows"]] == ["50001", "50002"]
    assert data["rows"][0]["amount_paise"] == 2_587_450 and data["rows"][0]["qty_u"] == 10_000_000
    assert data["bought_paise"] == 2_587_450 and data["sold_paise"] == 812_500
    assert data["total_records"] == 2
    sent = _rows(rig.fake, "trades")
    assert sent == [
        {"segment": "EQ", "start_date": "2026-04-01", "end_date": "2026-10-26",
         "page_number": "1", "page_size": "500"}
    ]  # fmt: skip
    assert TOKEN not in str(answer)
    # Opened again an hour later: the cache answers, Upstox is not asked.
    rig.now = NOW + HOUR
    again = (await rig.call("report_trades", fy=FY, segment="EQ"))["data"]
    assert again["read_at"] == NOW and rig.fake.count("trades") == 1


@scenario_of
async def test_a_past_year_is_kept_once_complete_and_the_current_year_is_read_again_when_old(rig):
    await rig.call("report_trades", fy=2024, segment="EQ")
    assert _rows(rig.fake, "trades")[0]["end_date"] == "2025-03-31"  # a whole past year
    rig.now = NOW + 30 * 24 * HOUR
    await rig.call("report_trades", fy=2024, segment="EQ")
    assert rig.fake.count("trades") == 1  # never again on its own
    await rig.call("report_trades", fy=FY, segment="EQ")
    assert rig.fake.count("trades") == 2
    rig.now += reports.CURRENT_MAX_AGE_MS + 1
    rig.svc.upstox.budget = Budget()  # six hours have passed for the budget too
    data = (await rig.call("report_trades", fy=FY, segment="EQ"))["data"]
    assert rig.fake.count("trades") == 3 and data["read_at"] == rig.now


@scenario_of
async def test_the_pnl_report_reads_metadata_pages_and_charges_and_nets_them(rig):
    data = (await rig.call("report_pnl", fy=FY, segment="EQ"))["data"]
    assert data["state"] == "ok" and data["trades_count"] == 1
    (row,) = data["rows"]
    assert row["pnl_paise"] == 125_000 and row["buy_date"] == 20260914
    assert data["realised_paise"] == 125_000
    assert data["charges"]["total_paise"] == 15_423 and data["charges"]["ipft_paise"] is None
    assert data["net_paise"] == 125_000 - 15_423
    assert [p for p, _ in rig.fake.queries] == ["pnl_meta", "pnl", "charges"]
    meta, pnl, charges = (q for _, q in rig.fake.queries)
    assert meta == {"segment": "EQ", "financial_year": "2627"}
    assert pnl == {
        "segment": "EQ",
        "financial_year": "2627",
        "page_number": "1",
        "page_size": "5000",
    }
    assert charges == meta
    await rig.call("report_pnl", fy=FY, segment="EQ")
    assert rig.fake.count() == 3  # from the cache


@scenario_of
async def test_mutual_funds_have_no_pnl_report_and_nothing_is_asked(rig):
    data = (await rig.call("report_pnl", fy=FY, segment="MF"))["data"]
    assert data["rows"] == [] and data["note"] == reports.WORDS["no_pnl_mf"]
    assert rig.fake.count() == 0


@scenario_of
async def test_a_year_or_segment_upstox_does_not_keep_is_refused_in_words(rig):
    old = await rig.call("report_trades", fy=2022, segment="EQ")
    assert old["code"] == "bad_request" and old["message"] == reports.WORDS["year"]
    ahead = await rig.call("report_trades", fy=2027, segment="EQ")
    assert ahead["code"] == "bad_request"
    bad = await rig.call("report_pnl", fy=FY, segment="GOLD")
    assert bad["code"] == "bad_request" and bad["message"] == reports.WORDS["segment"]
    assert rig.fake.count() == 0


@scenario_of
async def test_many_pages_are_read_in_order_and_a_trade_is_never_doubled(rig):
    page = lambda ids, more: {  # noqa: E731
        "status": "success",
        "data": [
            {**rig.fake.bodies["trades"]["data"][0], "trade_id": i, "trade_date": f"2026-05-0{ord(i) - 64}"}
            for i in ids
        ],
        "metadata": {"page": {"page_number": 1, "page_size": 500, "total_pages": 2 if more else 1}},
    }  # fmt: skip
    monkeypatch_size = 2
    reports.TRADES_PAGE_SIZE, kept = monkeypatch_size, reports.TRADES_PAGE_SIZE
    try:
        rig.fake.bodies["trades"] = [page(["A", "B"], True), page(["B", "C"], False)]
        data = (await rig.call("report_trades", fy=FY, segment="EQ"))["data"]
    finally:
        reports.TRADES_PAGE_SIZE = kept
    assert [q["page_number"] for q in _rows(rig.fake, "trades")] == ["1", "2"]
    assert sorted(r["trade_id"] for r in data["rows"]) == ["A", "B", "C"]
    assert data["state"] == "ok"


@scenario_of
async def test_the_budget_stops_a_read_where_it_is_and_the_next_open_continues(rig):
    """The reader shares the live book's budget: one read a second and no more. With a budget of
    one call, the second page is not sent; the record is partial and says so; the next open
    starts from the page it stopped at."""
    rig.svc.upstox.budget = Budget(limits=((1, 1_000_000.0),))
    page = lambda ids, more: {  # noqa: E731
        "status": "success",
        "data": [{**rig.fake.bodies["trades"]["data"][0], "trade_id": i} for i in ids],
        "metadata": {"page": {"page_number": 1, "page_size": 1, "total_pages": 2}},
    }
    kept, reports.TRADES_PAGE_SIZE = reports.TRADES_PAGE_SIZE, 1
    try:
        rig.fake.bodies["trades"] = [page(["A"], True), page(["B"], False)]
        data = (await rig.call("report_trades", fy=FY, segment="EQ"))["data"]
        assert data["state"] == "partial" and data["note"] == reports.WORDS["busy"]
        assert [r["trade_id"] for r in data["rows"]] == ["A"] and data["read_at"] is None
        assert rig.fake.count("trades") == 1
        rig.svc.upstox.budget = Budget()  # the minute has passed
        rig.now += 61_000
        data = (await rig.call("report_trades", fy=FY, segment="EQ"))["data"]
    finally:
        reports.TRADES_PAGE_SIZE = kept
    assert [q["page_number"] for q in _rows(rig.fake, "trades")] == ["1", "2"]
    assert data["state"] == "ok" and [r["trade_id"] for r in data["rows"]] == ["A", "B"]


@scenario_of
async def test_a_slow_down_from_upstox_pauses_the_api_and_the_figures_say_so(rig):
    rig.fake.status["trades"] = 429
    data = (await rig.call("report_trades", fy=FY, segment="EQ"))["data"]
    assert data["state"] == "unavailable" and data["rows"] == []
    assert data["note"] == reports.WORDS["slow"]
    assert rig.svc.upstox.budget.paused_for("trades") > 0
    assert rig.svc.upstox.budget.paused_for("holdings") == 0  # its own API only
    rig.fake.status.pop("trades")
    again = (await rig.call("report_trades", fy=FY, segment="EQ"))["data"]
    assert again["state"] == "unavailable" and rig.fake.count("trades") == 1  # still paused


@scenario_of
async def test_a_refused_token_pauses_every_report_until_the_token_file_changes(rig):
    rig.fake.status["pnl_meta"] = 401
    data = (await rig.call("report_pnl", fy=FY, segment="EQ"))["data"]
    assert data["state"] == "unavailable" and data["note"] == reports.WORDS["token_refused"]
    trades = (await rig.call("report_trades", fy=FY, segment="EQ"))["data"]
    assert trades["note"] == reports.WORDS["token_refused"] and rig.fake.count("trades") == 0
    assert "Upstox app" in trades["note"] and "TRINETRA" in trades["note"]
    rig.fake.status.pop("pnl_meta")
    rig.svc.upstox.token_file.write_text(TOKEN + "x")  # the file changed: try again
    data = (await rig.call("report_trades", fy=FY, segment="EQ"))["data"]
    assert data["state"] == "ok"


@scenario_of
async def test_a_slow_upstox_answers_reading_and_the_figures_follow(rig, monkeypatch=None):
    kept, reports.ANSWER_WITHIN_S = reports.ANSWER_WITHIN_S, 0.2
    rig.fake.delay = 0.6
    try:
        data = (await rig.call("report_trades", fy=FY, segment="EQ"))["data"]
        assert data["state"] == "reading" and data["note"] == reports.WORDS["reading"]
        assert data["rows"] == []
        await asyncio.sleep(0.8)
        rig.fake.delay = 0.0
        data = (await rig.call("report_trades", fy=FY, segment="EQ"))["data"]
    finally:
        reports.ANSWER_WITHIN_S = kept
    assert data["state"] == "ok" and len(data["rows"]) == 2
    assert rig.fake.count("trades") == 1  # one read in flight was shared, not repeated


@scenario_of
async def test_the_nightly_read_waits_for_the_closing_record_and_runs_once(rig):
    rig.now = NOW - 24 * HOUR
    await rig.call("report_pnl", fy=FY, segment="FO")  # a record the owner opened yesterday
    calls = rig.fake.count()
    rig.svc.upstox.budget = Budget()
    rig.now = NOW + 6 * HOUR  # this evening, no closing record yet
    assert await reports.reports_step(rig.svc, rig.now) is None
    assert rig.fake.count() == calls
    rig.svc.store.write(OWNER, lambda tx: tx.job_put("eod", TODAY, "ok", "taken", rig.now))
    assert await reports.reports_step(rig.svc, rig.now) == "ok"
    await asyncio.gather(*rig.svc.reports._tasks.values())
    # The history of EQ (always) and the P&L of FO (kept) were read; nothing else.
    read = sorted({p for p, _ in rig.fake.queries[calls:]})
    assert read == ["charges", "pnl", "pnl_meta", "trades"]
    assert {q["segment"] for p, q in rig.fake.queries[calls:] if p == "trades"} == {"EQ"}
    assert {q["segment"] for p, q in rig.fake.queries[calls:] if p == "pnl"} == {"FO"}
    assert await reports.reports_step(rig.svc, rig.now + 1) is None  # once a night
    job = rig.svc.store.read(OWNER, lambda tx: tx.job_get("reports", TODAY))
    assert job["status"] == "ok" and "trades EQ" in job["detail"]


@scenario_of
async def test_the_years_op_lists_the_three_years_with_the_current_one_marked(rig):
    data = (await rig.call("report_years"))["data"]
    assert data["today"] == TODAY
    assert [y["fy"] for y in data["years"]] == [2026, 2025, 2024]
    assert data["years"][0]["current"] and data["years"][0]["label"] == "FY 2026-27"


def test_the_words_are_for_a_trader():
    import re

    for text in reports.WORDS.values():
        assert not re.search(r"HTTP|\b[45]\d\d\b|JSON|API|token file|None|null", text), text
