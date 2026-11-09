"""A year with more trades than one read holds (M7.6): its refresh goes on across reads, from the
page it stopped at, and ends when every page is in. It was thrown away at the cap and started
again from page 1, so a segment-year past 10,000 trades (20 pages of 500) kept its first figures
for good, under words that said Upstox could not be read. The stand-in here: a page of 2 trades
and a cap of 2 pages for 500 and 20."""

from __future__ import annotations

import pytest
from test_portfolio_import_ops import scenario_of
from test_portfolio_reports import _rows, _wire, fake  # noqa: F401  (fixtures)
from test_portfolio_reports_cache import HOUR, MINUTE_OF_TWO, WIDE, page, record_of, trow

from sudarshan.portfolio import reports
from sudarshan.portfolio.budget import Budget
from sudarshan.portfolio.core.days import day_of_instant


@pytest.fixture(autouse=True)
def small(monkeypatch):
    monkeypatch.setattr(reports, "TRADES_PAGE_SIZE", 2)


def ids(data):
    return [r["trade_id"] for r in data["rows"]]


def six(rig, rows="ABCDEF", total=None):
    n = total or len(rows)
    pairs = [rows[i : i + 2] for i in range(0, len(rows), 2)]
    rig.fake.bodies["trades"] = [page([trow(t) for t in pair], len(pairs), n) for pair in pairs]


async def complete_year_of_five(rig):
    """The current year read to completion over two reads (cap 2 pages): A..E."""
    rig.svc.upstox.budget = Budget(limits=WIDE)
    rig.svc.ensure_reports().pages = 2
    fy = reports.financial_year(day_of_instant(rig.now))
    six(rig, "ABCDE")
    await rig.call("report_trades", fy=fy, segment="EQ")
    second = (await rig.call("report_trades", fy=fy, segment="EQ"))["data"]  # Read more
    assert second["state"] == "ok" and len(second["rows"]) == 5, second
    return fy


@scenario_of
async def test_a_complete_year_past_the_page_cap_is_refreshed_across_reads(rig):
    fy = await complete_year_of_five(rig)
    six(rig, "ABCDEF")  # the owner traded again
    seen = []
    for _ in range(2):
        rig.now += 7 * HOUR
        got = (await rig.call("report_trades", fy=fy, segment="EQ"))["data"]
        seen.append((got["state"], len(got["rows"]), got["note"]))
    assert seen[0] == ("ok", 5, reports.WORDS["stepwise"]), seen  # cut at the cap, old figures
    assert seen[1][:2] == ("ok", 6) and seen[1][2] is None, seen  # carried on and swapped in
    assert "refresh" not in record_of(rig, "trades", fy, "EQ")["payload"]


@scenario_of
async def test_the_continuation_reads_only_the_pages_not_yet_read(rig):
    fy = await complete_year_of_five(rig)
    six(rig, "ABCDEF")
    before = len(_rows(rig.fake, "trades"))
    rig.now += 7 * HOUR
    cut = (await rig.call("report_trades", fy=fy, segment="EQ"))["data"]
    assert [q["page_number"] for q in _rows(rig.fake, "trades")][before:] == ["1", "2"]
    assert cut["note"] == reports.WORDS["stepwise"] and len(cut["rows"]) == 5
    rig.now += 7 * HOUR
    done = (await rig.call("report_trades", fy=fy, segment="EQ"))["data"]
    assert [q["page_number"] for q in _rows(rig.fake, "trades")][before + 2 :] == ["3"]
    assert ids(done) == list("ABCDEF") and done["note"] is None


@scenario_of
async def test_the_stop_is_worded_as_sudarshans_own_never_as_upstox_failing(rig):
    fy = await complete_year_of_five(rig)
    six(rig, "ABCDEF")
    rig.now += 7 * HOUR
    cut = (await rig.call("report_trades", fy=fy, segment="EQ"))["data"]
    assert cut["note"] == reports.WORDS["stepwise"]
    assert "Upstox" not in cut["note"]
    assert "Read again" in cut["note"]
    # the minute budget spent: a forced read says the same, and sends nothing
    spent = Budget(limits=MINUTE_OF_TWO)
    assert spent.take("trades") and spent.take("trades")
    rig.svc.upstox.budget = spent
    asked = len(_rows(rig.fake, "trades"))
    forced = (await rig.call("report_trades", fy=fy, segment="EQ", force=True))["data"]
    assert len(_rows(rig.fake, "trades")) == asked
    assert forced["note"] == reports.WORDS["held_back"] and len(forced["rows"]) == 5
    assert "Upstox could not" not in forced["note"]


@scenario_of
async def test_an_upstox_failure_in_a_refresh_is_still_worded_as_upstox_s(rig):
    rig.svc.upstox.budget = Budget(limits=WIDE)
    fy = reports.financial_year(day_of_instant(rig.now))
    six(rig, "ABCDEF")
    full = (await rig.call("report_trades", fy=fy, segment="EQ"))["data"]
    assert len(full["rows"]) == 6
    rig.now += 7 * HOUR
    rig.fake.bodies["trades"] = [page([trow("A"), trow("B")], 3, 6),
                                 {"status": "error", "errors": [{"message": "x"}]}]  # fmt: skip
    again = (await rig.call("report_trades", fy=fy, segment="EQ"))["data"]
    assert again["note"] == reports.WORDS["kept"] and len(again["rows"]) == 6


@scenario_of
async def test_a_stale_continuation_starts_again_from_page_one(rig):
    fy = await complete_year_of_five(rig)
    six(rig, "ABCDEF")
    rig.now += 7 * HOUR
    await rig.call("report_trades", fy=fy, segment="EQ")  # cut at the cap, continuation kept
    assert "refresh" in record_of(rig, "trades", fy, "EQ")["payload"]
    rig.now += reports.REFRESH_KEPT_MS + HOUR  # the pages may have moved by now
    before = len(_rows(rig.fake, "trades"))
    await rig.call("report_trades", fy=fy, segment="EQ")
    assert [q["page_number"] for q in _rows(rig.fake, "trades")][before:][0] == "1"


@scenario_of
async def test_a_continuation_that_comes_up_short_is_dropped_and_the_old_figures_stay(rig):
    fy = await complete_year_of_five(rig)
    six(rig, "ABCDEF")
    rig.now += 7 * HOUR
    await rig.call("report_trades", fy=fy, segment="EQ")
    # a trade is listed ahead of the rest: every page moves up, and the last trade is never reached
    rig.fake.bodies["trades"] = [
        page([trow("A"), trow("B")], 4, 7),
        page([trow("C"), trow("D")], 4, 7),
        page([trow("F"), trow("F")], 4, 7),  # page 3 now repeats a trade
        page([trow("G")], 4, 7),
    ]
    rig.now += 7 * HOUR
    got = (await rig.call("report_trades", fy=fy, segment="EQ"))["data"]
    record = record_of(rig, "trades", fy, "EQ")
    assert (
        got["state"] == "ok" and len(got["rows"]) == 5
    )  # never a doubled or short year as complete
    assert "refresh" not in record["payload"]
    assert got["note"] == reports.WORDS["kept"]


@scenario_of
async def test_a_year_past_the_cap_is_never_frozen_however_many_times_it_is_opened(rig):
    fy = await complete_year_of_five(rig)
    six(rig, "ABCDEF")
    last = 0
    for _ in range(4):  # four opens, each past the age limit, or four nightly reads
        rig.now += 7 * HOUR
        last = len((await rig.call("report_trades", fy=fy, segment="EQ"))["data"]["rows"])
        if last == 6:
            break
    assert last == 6, "a complete year past the page cap is never refreshed"
