"""A year with more trades than one read holds (M7.6): its refresh goes on across reads, from the
page it stopped at, and ends when every page is in. It was thrown away at the cap and started
again from page 1, so a segment-year past 10,000 trades (20 pages of 500) kept its first figures
for good, under words that said Upstox could not be read. The stand-in here: a page of 2 trades
and a cap of 2 pages for 500 and 20."""

from __future__ import annotations

import pytest
from test_portfolio_import_ops import OWNER, scenario_of
from test_portfolio_reports import _rows, _wire, fake  # noqa: F401  (fixtures)
from test_portfolio_reports_cache import (
    HOUR,
    MINUTE_OF_TWO,
    WIDE,
    meta_of,
    page,
    prow,
    record_of,
    trow,
)

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
    # The pages moved: Upstox answered every call, so the words must not say it could not be read.
    assert got["note"] == reports.WORDS["short_kept"], got["note"]
    assert "could not be read" not in got["note"]


@scenario_of
async def test_a_refresh_with_no_token_on_the_server_says_so_not_that_upstox_failed(rig):
    fy = await complete_year_of_five(rig)
    six(rig, "ABCDEF")
    rig.svc.upstox.token_file.unlink()  # SUDARSHAN's own token is gone from the server
    rig.now += 7 * HOUR
    got = (await rig.call("report_trades", fy=fy, segment="EQ", force=True))["data"]
    assert got["state"] == "ok" and len(got["rows"]) == 5
    assert got["note"] == reports.WORDS["no_token_kept"], got["note"]
    assert "last complete read" in got["note"] and "could not be read" not in got["note"]


@scenario_of
async def test_a_refresh_refused_for_the_token_keeps_the_figures_and_its_token_warning(rig):
    """A refused token while complete figures are kept: the words keep the warning that the token
    is shared with TRINETRA and must not be made again in the Upstox app, not a bare "could not
    be read" that sends the owner to the Upstox app to make a new one."""
    fy = await complete_year_of_five(rig)
    six(rig, "ABCDEF")
    rig.fake.status["trades"] = 401  # Upstox refuses SUDARSHAN's read-only token
    rig.now += 7 * HOUR
    got = (await rig.call("report_trades", fy=fy, segment="EQ", force=True))["data"]
    assert got["state"] == "ok" and len(got["rows"]) == 5, got
    assert got["note"] == reports.WORDS["token_refused_kept"], got["note"]
    assert "TRINETRA" in got["note"] and "Upstox app" in got["note"]
    assert "last complete read" in got["note"]


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


@scenario_of
async def test_a_pnl_carried_on_after_a_new_closed_trade_is_read_afresh_never_doubled(rig):
    """The current year, a trade a page, the read cut after 2 of 3 pages. Before Read more, one
    more trade closes and Upstox lists it first: every page moves up by one. Page 3 read onto the
    rows held would repeat P2 and the count would still match, so the year was stored complete
    with a doubled row (realised 10,000 against 40,000)."""
    rig.svc.upstox.budget = Budget(limits=WIDE)
    rig.svc.ensure_reports().pages = 2
    fy = reports.financial_year(day_of_instant(rig.now))
    rig.fake.bodies["pnl_meta"] = meta_of(3, 1)
    rig.fake.bodies["pnl"] = [page([prow("P1", 1000, 1100)]), page([prow("P2", 2000, 1900)]),
                              page([prow("P3", 3000, 3300)])]  # fmt: skip
    first = (await rig.call("report_pnl", fy=fy, segment="EQ"))["data"]
    assert first["state"] == "partial" and len(first["rows"]) == 2
    rig.fake.bodies["pnl_meta"] = meta_of(4, 1)
    rig.fake.bodies["pnl"] = [page([prow("P0", 500, 600)]), page([prow("P1", 1000, 1100)]),
                              page([prow("P2", 2000, 1900)]), page([prow("P3", 3000, 3300)])]  # fmt: skip
    rig.now += 60_000
    more = (await rig.call("report_pnl", fy=fy, segment="EQ"))["data"]
    names = [r["scrip_name"] for r in more["rows"]]
    assert len(set(names)) == len(names), names
    assert more["state"] != "ok" or sorted(names) == ["P0", "P1", "P2", "P3"]
    rig.now += 60_000
    done = (await rig.call("report_pnl", fy=fy, segment="EQ"))["data"]
    assert done["state"] == "ok" and sorted(r["scrip_name"] for r in done["rows"]) == [
        "P0",
        "P1",
        "P2",
        "P3",
    ]
    assert done["realised_paise"] == 40_000  # 10,000 + 10,000 - 10,000 + 30,000 paise, by hand


async def complete_pnl_year(rig):
    rig.svc.upstox.budget = Budget(limits=WIDE)
    fy = reports.financial_year(day_of_instant(rig.now))
    rig.fake.bodies["pnl_meta"] = meta_of(1, 100)
    rig.fake.bodies["pnl"] = [page([prow("P1", 1000, 1100)])]
    full = (await rig.call("report_pnl", fy=fy, segment="EQ"))["data"]
    assert full["state"] == "ok", full
    return fy


@scenario_of
async def test_a_pnl_refresh_stopped_by_sudarshans_own_minute_budget_is_worded_as_sudarshans(rig):
    """Upstox was never asked, so the words must not say it could not be read."""
    fy = await complete_pnl_year(rig)
    spent = Budget(limits=MINUTE_OF_TWO)
    assert spent.take("pnl_meta") and spent.take("pnl_meta")
    rig.svc.upstox.budget = spent
    asked = len(_rows(rig.fake, "pnl_meta"))
    again = (await rig.call("report_pnl", fy=fy, segment="EQ", force=True))["data"]
    assert len(_rows(rig.fake, "pnl_meta")) == asked
    assert again["state"] == "ok" and len(again["rows"]) == 1
    assert again["note"] == reports.WORDS["held_back"], again["note"]
    assert "Upstox could not" not in again["note"]


@scenario_of
async def test_a_pnl_refresh_whose_rows_do_not_match_the_count_says_so_and_keeps_the_figures(rig):
    """The count note the changelog asks the owner to report must not be hidden under the words for
    Upstox failing."""
    fy = await complete_pnl_year(rig)
    rig.fake.bodies["pnl_meta"] = meta_of(2, 100)  # Upstox counts 2 trades and sends 1 row
    rig.now += 7 * HOUR
    again = (await rig.call("report_pnl", fy=fy, segment="EQ", force=True))["data"]
    assert again["state"] == "ok" and len(again["rows"]) == 1  # the complete figures stay
    assert again["note"] == reports.WORDS["count_kept"], again["note"]
    assert "does not match" in again["note"] and "Upstox could not" not in again["note"]


async def nightly(rig, now):
    """The nightly job at ``now``: the closing record is in, so the reports step reads."""
    rig.svc.store.write(
        OWNER, lambda tx: tx.job_put("eod", day_of_instant(now), "ok", "", now - 60_000)
    )
    rig.now = now
    return await reports.reports_step(rig.svc, now)


@scenario_of
async def test_the_nightly_read_alone_carries_a_large_year_on_when_the_next_night_starts_later(rig):
    """Two nightly reads are about 24 hours apart and land on either side of that at random. The
    next night 30 seconds later in the day used to be past the window: page 1 again, every night,
    and the year stayed on the last complete read for good."""
    fy = await complete_year_of_five(rig)
    six(rig, "ABCDEF")  # the owner traded again: three pages, the cap is two
    night = rig.now + 7 * HOUR
    await nightly(rig, night)
    first = record_of(rig, "trades", fy, "EQ")
    assert first["note"] == reports.WORDS["stepwise"] and "refresh" in first["payload"]
    before = len(_rows(rig.fake, "trades"))
    await nightly(rig, night + 24 * HOUR + 30_000)
    got = record_of(rig, "trades", fy, "EQ")
    assert [q["page_number"] for q in _rows(rig.fake, "trades")][before:] == ["3"]
    assert ids(got["payload"]) == list("ABCDEF") and "refresh" not in got["payload"]


@scenario_of
async def test_a_friday_step_is_carried_on_by_the_monday_nightly(rig):
    fy = await complete_year_of_five(rig)
    six(rig, "ABCDEF")
    friday = rig.now + 7 * HOUR
    await nightly(rig, friday)
    await nightly(rig, friday + 72 * HOUR - 60_000)  # Monday's, a minute earlier in the day
    got = record_of(rig, "trades", fy, "EQ")
    assert ids(got["payload"]) == list("ABCDEF"), "Monday started again from page 1"


@scenario_of
async def test_a_continuation_older_than_the_window_still_starts_again(rig):
    """The short check stays: past four days the pages may have moved."""
    assert reports.REFRESH_KEPT_MS >= 4 * 24 * HOUR
    fy = await complete_year_of_five(rig)
    six(rig, "ABCDEF")
    night = rig.now + 7 * HOUR
    await nightly(rig, night)
    before = len(_rows(rig.fake, "trades"))
    await nightly(rig, night + reports.REFRESH_KEPT_MS + HOUR)
    assert [q["page_number"] for q in _rows(rig.fake, "trades")][before:][0] == "1"
    assert "refresh" in record_of(rig, "trades", fy, "EQ")["payload"]  # a new one, from page 1
