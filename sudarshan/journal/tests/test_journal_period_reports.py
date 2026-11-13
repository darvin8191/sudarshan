"""The week in review, the monthly statement and the tax pack (JB9): their periods, the words
from core/words.py, the file the renderer makes for each (PNG, PDF, and the month's CSV), the
archive row that keeps it, the line for the report desk, the three jobs and the family's
variant. Against the journal's own rig; every value is synthetic."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import re

import pytest
from PIL import Image
from render_pdfread import read_pdf
from test_journal_analytics import book as book  # noqa: F401  (a fixture)
from test_journal_analytics import trade
from test_journal_capture import DAY, ist_ms, scenario
from test_journal_capture import files as files  # noqa: F401  (a fixture)
from test_journal_capture import rig as rig  # noqa: F401  (a fixture)
from test_journal_reports import call, spool

from sudarshan.alerts import report_files
from sudarshan.journal import periods, reports, serialize
from sudarshan.journal.core import metrics, words
from sudarshan.journal.errors import ApiError
from sudarshan.journal.jobs import digest, fypack, statement
from sudarshan.journal.render import tables
from sudarshan.journal.schedule import once_a_day
from sudarshan.journal.store import Scope

OWNER = Scope.owner()
SUNDAY = 20261108


def kept(rig, kind):
    rows = rig.svc.store.read(OWNER, lambda tx: tx.rows("report", kind=kind, order="id"))
    return rows[-1] if rows else None


def file_of(rig, row):
    return rig.svc.store.read(OWNER, lambda tx: tx.report_bytes(row["id"]))


def build(rig, kind, **args):
    return call(rig, "report_build", kind=kind, **args)


def pdf_lines(blob):
    return [ln for page in read_pdf(blob).pages for ln in page.lines]


# ---- the periods -----------------------------------------------------------------------------
def test_a_week_runs_monday_to_sunday_and_is_keyed_by_its_iso_week():
    full = periods.week_span(periods.to_date(20261104), 20261114)
    assert (full.key, full.start, full.end, full.partial) == ("2026W45", 20261102, 20261108, False)
    running = periods.week_span(periods.to_date(20261110), 20261111)
    assert (running.key, running.start, running.end, running.partial) == (
        "2026W46", 20261109, 20261111, True,
    )  # fmt: skip
    assert periods.week_span(periods.to_date(20270103), 20270110).key == "2026W53"  # an ISO year


def test_a_month_and_a_financial_year_have_their_own_keys_and_labels():
    month = periods.month_span(2026, 10, 20261114)
    assert (month.key, month.start, month.end, month.label) == (
        "202610", 20261001, 20261031, "October 2026",
    )  # fmt: skip
    now = periods.month_span(2026, 11, 20261114)
    assert now.partial and now.label == "November 2026, to Sat 14 Nov"
    fy = periods.fy_span(2025, 20261114)
    assert (fy.key, fy.start, fy.end, fy.label) == ("FY2526", 20250401, 20260331, "FY 2025-26")
    running = periods.fy_span(2026, 20261114)
    assert running.key == "FY2627" and running.end == 20261114 and running.partial


def test_each_job_builds_the_period_that_just_ended():
    assert periods.span_for_job("weekly", SUNDAY).key == "2026W45"
    assert periods.span_for_job("weekly", SUNDAY).end == SUNDAY  # the whole week, not a part
    assert periods.span_for_job("monthly", 20261201).key == "202611"
    assert periods.span_for_job("monthly", 20270101).key == "202612"  # across the year
    assert periods.span_for_job("fy", 20270401).key == "FY2627"
    assert periods.span_for_job("fy", 20270401).label == "FY 2026-27"


def test_a_request_names_a_period_or_gets_the_one_running_now():
    assert periods.resolve_span("weekly", None, 20261111).key == "2026W46"
    assert periods.resolve_span("weekly", "2026W45", 20261114).start == 20261102
    assert periods.resolve_span("weekly", "2026W45", 20261114).label == "Mon 2 Nov - 8 Nov 2026"
    assert periods.resolve_span("monthly", "202610", 20261114).end == 20261031
    assert periods.resolve_span("fy", "FY2526", 20261114).end == 20260331
    for kind, bad in (("weekly", "2026-W45"), ("weekly", "2026W99"), ("monthly", "2026-10"),
                      ("monthly", "202613"), ("fy", "FY2528"), ("fy", "2526"), ("fy", "FY26")):  # fmt: skip
        with pytest.raises(periods.BadPeriod):
            periods.resolve_span(kind, bad, 20261114)
    for kind, future in (("weekly", "2027W01"), ("monthly", "202701"), ("fy", "FY2728")):
        with pytest.raises(periods.BadPeriod, match="not started"):
            periods.resolve_span(kind, future, 20261114)


# ---- the week in review ----------------------------------------------------------------------
def test_the_week_in_review_is_words_and_a_png_with_its_labels(rig, book):  # noqa: F811
    built = build(rig, "weekly", period="2026W45")
    assert built["words"] == "The week in review is in the archive."
    row = kept(rig, "weekly")
    assert row["period_key"] == "2026W45" and row["id"] == built["id"]
    trips = book.read(OWNER, lambda tx: tx.rows("round_trip", status="closed", close_day=20261102))
    trips += book.read(OWNER, lambda tx: tx.rows("round_trip", status="closed", close_day=20261103))
    net = sum(t["net_paise"] for t in trips)
    assert row["text"].splitlines()[0] == words.HEADER
    assert row["text"].splitlines()[1] == "Week in review · Mon 2 Nov - 8 Nov 2026"
    assert f"Net {words.rupees(net, True)}" in row["text"]
    assert "Trading days 5 · trades 2 · won 1, lost 1" in row["text"]
    # the file is the PNG, kept as the archive keeps one file
    blob = file_of(rig, row)
    assert row["file_name"] == "SUDARSHAN_Week-in-review_2026W45.png"
    assert row["file_mime"] == "image/png"
    assert row["file_sha256"] == hashlib.sha256(blob).hexdigest()
    image = Image.open(io.BytesIO(blob))
    assert image.size == (1080, 600)
    assert report_files.kind_of(row["file_name"]).extension == "png"
    served = call(rig, "report_file", id=row["id"])
    assert base64.b64decode(served["file_b64"]) == blob and served["file_name"] == row["file_name"]


def test_the_week_is_announced_once_with_the_files_hash_and_never_its_text(rig, book):  # noqa: F811
    build(rig, "weekly", period="2026W45")
    row = kept(rig, "weekly")
    lines = spool(rig)
    assert len(lines) == 1
    assert (
        lines[0].fact("report_kind") == "weekly" and lines[0].fact("sha256") == row["file_sha256"]
    )
    assert "Net" not in str(lines[0])
    build(rig, "weekly", period="2026W45")  # a rebuild replaces, and tells no one again
    assert len(spool(rig)) == 1


def test_a_week_with_no_trading_day_says_so_and_still_has_a_picture(rig, book):  # noqa: F811
    build(rig, "weekly", period="2026W44")  # Monday 26 Oct to Sunday 1 Nov: no trade, trading days
    row = kept(rig, "weekly")
    assert "Net" not in row["text"] or "Trading days" in row["text"]
    assert Image.open(io.BytesIO(file_of(rig, row))).size == (1080, 600)


def test_the_week_against_the_index_comes_from_the_portfolios_closes(rig, book):  # noqa: F811
    rig.portfolio.answers["benchmark_closes"] = {
        "state": "ok", "index": "nifty50",
        "closes": [{"day": 20261030, "close_u": 250_000_000},
                   {"day": 20261106, "close_u": 252_500_000}],
    }  # fmt: skip
    build(rig, "weekly", period="2026W45")
    text = kept(rig, "weekly")["text"]
    assert "Against NIFTY 50:" in text and "index +1.00%" in text
    ask = [c for c in rig.portfolio.calls if c[0] == "benchmark_closes"][0]
    assert ask[1]["from"] == 20261030 and ask[1]["to"] == SUNDAY


def test_a_portfolio_that_cannot_be_asked_leaves_the_index_out_never_guessed(rig, book):  # noqa: F811
    rig.portfolio.down = True
    build(rig, "weekly", period="2026W45")
    text = kept(rig, "weekly")["text"]
    assert "index" not in text.lower().replace("set your trading capital", "")


# ---- the monthly statement -------------------------------------------------------------------
def test_the_monthly_statement_is_a_pdf_of_the_months_trades(rig, book):  # noqa: F811
    built = build(rig, "monthly", period="202611")
    row = kept(rig, "monthly")
    assert built["id"] == row["id"] and row["period_key"] == "202611"
    assert row["file_name"] == "SUDARSHAN_Monthly-statement_202611.pdf"
    assert row["file_mime"] == "application/pdf"
    blob = file_of(rig, row)
    assert blob.startswith(b"%PDF-") and row["file_sha256"] == hashlib.sha256(blob).hexdigest()
    pdf = read_pdf(blob)
    lines = [ln for page in pdf.pages for ln in page.lines]
    text = "\n".join(lines)
    assert "SUDARSHAN" in text and "Monthly statement" in text
    assert "November 2026, to Mon 9 Nov" in text
    assert "By strategy" in text and "Day by day" in text and "Trades" in text
    assert text.count("LONG") == 4  # the four trips, each with its side in a box
    assert "Open the full report" not in text and words.HEADER not in text
    # every trade's net is a signed money cell
    trips = book.read(OWNER, lambda tx: tx.rows("round_trip", status="closed"))
    for t in trips:
        assert words.rupees(t["net_paise"], True) in text or t["net_paise"] == 0
    assert read_pdf(blob).info["Title"].startswith("SUDARSHAN - Monthly statement")
    assert report_files.kind_of(row["file_name"]).extension == "pdf"
    assert len(spool(rig)) == 1 and spool(rig)[0].fact("sha256") == row["file_sha256"]


def test_the_months_csv_is_answered_by_the_same_op_and_is_safe_to_open(rig, book):  # noqa: F811
    first = book.read(OWNER, lambda tx: tx.rows("round_trip", close_day=20261102))[0]
    book.write(OWNER, lambda tx: tx.touch(
        "round_trip", first["id"], {"strategy_override": "=HYPERLINK(1)"}, 1))  # fmt: skip
    build(rig, "monthly", period="202611")
    row = kept(rig, "monthly")
    served = call(rig, "report_file", id=row["id"], format="csv")
    assert served["file_name"] == "SUDARSHAN_Monthly-statement_202611.csv"
    assert served["file_mime"] == "text/csv"
    data = base64.b64decode(served["file_b64"])
    assert served["file_sha256"] == hashlib.sha256(data).hexdigest()
    assert report_files.kind_of(served["file_name"]).extension == "csv"
    rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))
    assert tuple(rows[0]) == tables.CSV_HEADER and len(rows) == 5
    assert rows[1][0] == "2026-11-02" and rows[1][12] == "'=HYPERLINK(1)"  # Y32
    assert [r[0] for r in rows[1:]] == sorted(r[0] for r in rows[1:])
    nets = [r[11] for r in rows[1:]]
    stored = book.read(
        OWNER, lambda tx: tx.rows("round_trip", status="closed", order="close_day,id")
    )
    assert nets == [tables.paise_text(t["net_paise"]) for t in stored]
    assert b"\x00" not in data
    # without format the same op still answers the PDF
    assert call(rig, "report_file", id=row["id"])["file_name"].endswith(".pdf")
    # a report that has no CSV (the week) answers empty, never an error
    build(rig, "weekly", period="2026W45")
    week = kept(rig, "weekly")
    assert call(rig, "report_file", id=week["id"], format="csv")["file_name"] is None


def test_a_bad_format_is_refused(rig, book):  # noqa: F811
    build(rig, "monthly", period="202611")
    with pytest.raises(ApiError):
        call(rig, "report_file", id=kept(rig, "monthly")["id"], format="xlsx")


def test_the_csv_apostrophe_rule_is_exactly_y32():
    cell = tables.csv_cell
    assert [cell(v) for v in ("=1+1", "+cmd", "@SUM", "-cmd", "-", "--1")] == [
        "'=1+1", "'+cmd", "'@SUM", "'-cmd", "'-", "'--1"]  # fmt: skip
    assert [cell(v) for v in ("-12.50", "-1", "ORB", "my-strategy", "12", "")] == [
        "-12.50", "-1", "ORB", "my-strategy", "12", ""]  # fmt: skip
    assert tables.paise_text(-123456) == "-1234.56" and tables.paise_text(5) == "0.05"
    assert tables.price_text(1_005_000) == "100.50" and tables.price_text(25) == "0.0025"
    assert tables.paise_text(None) == "" and tables.price_text(None) == ""


# ---- the tax pack ----------------------------------------------------------------------------
def test_the_tax_pack_is_a_labelled_working_paper_with_no_section_numbers(rig, book):  # noqa: F811
    built = build(rig, "fy", period="FY2627")
    assert built["words"] == "The tax pack is in the archive."
    row = kept(rig, "fy")
    assert row["period_key"] == "FY2627" and row["file_name"] == "SUDARSHAN_Tax-pack_FY2627.pdf"
    lines = pdf_lines(file_of(rig, row))
    text = "\n".join(lines)
    assert (
        "A working paper: verify with your CA. The broker's own report is the filing source."
        in text
    )
    assert "Month by month" in text and "Nov 2026" in text
    assert "By kind of trade" in text and "Futures and options (NFO, BFO)" in text
    assert "Upstox's own charges for this financial year have not been read yet" in text
    both = text + row["text"]
    assert not re.search(
        r"(?i)\bsection\b|§|\bsec\.|\b4[34]A[DB]\b|\b11[12]A\b|\b43\(5\)|income tax act", both
    )
    assert words.FY_NOTE in row["text"]


def test_the_brokers_own_charges_are_listed_when_they_have_been_read(rig, book):  # noqa: F811
    book.write(OWNER, lambda tx: tx.upstox_charges_put({
        "segment": "FO", "financial_year": 2026, "period_from": 20260401, "period_to": 20270331,
        "total_paise": 123_456, "brokerage_paise": 40_000, "gst_paise": 9_000,
        "stt_paise": 50_000, "stamp_paise": 2_000, "transaction_paise": 20_000,
        "clearing_paise": 0, "ipft_paise": 100, "others_paise": 2_356, "read_at": 1}, 1))  # fmt: skip
    build(rig, "fy", period="FY2627")
    text = "\n".join(pdf_lines(file_of(rig, kept(rig, "fy"))))
    assert "Charges as Upstox reports them" in text
    assert "Futures and options" in text and "−₹1,234.56" in text
    assert "have not been read yet" not in text


def test_a_year_with_no_trade_is_still_a_complete_labelled_paper(rig, book):  # noqa: F811
    build(rig, "fy", period="FY2526")
    text = "\n".join(pdf_lines(file_of(rig, kept(rig, "fy"))))
    assert "verify with your CA" in text and "No trades closed in this" in text


def test_trades_are_grouped_by_kind_and_holding_period_never_by_a_legal_term():
    day = 86_400
    base = {"exchange": "NFO", "net_paise": 100, "gross_paise": 150, "charges_paise": 50}
    assert tables.kind_of_trade(base) == "Futures and options (NFO, BFO)"
    assert tables.kind_of_trade({**base, "exchange": "MCX"}) == "Currency and commodity derivatives"
    eq = {**base, "exchange": "NSE", "product_class": "intraday"}
    assert tables.kind_of_trade(eq) == "Equity, bought and sold the same day"
    carry = {**base, "exchange": "NSE", "product_class": "carry"}
    assert tables.kind_of_trade({**carry, "hold_s": 365 * day}).endswith("up to 12 months")
    assert tables.kind_of_trade({**carry, "hold_s": 365 * day + 1}).endswith("over 12 months")


# ---- the archive and the ops -----------------------------------------------------------------
def test_a_rebuild_replaces_the_text_and_the_file_in_one_row(rig, book):  # noqa: F811
    build(rig, "monthly", period="202611")
    first = kept(rig, "monthly")
    trade(book, 9, 20261110, 100, 130)
    rig.now = ist_ms(20261110, "16:30:00")
    build(rig, "monthly", period="202611")
    second = kept(rig, "monthly")
    assert second["id"] == first["id"]
    assert len(rig.svc.store.read(OWNER, lambda tx: tx.rows("report", kind="monthly"))) == 1
    assert second["file_sha256"] != first["file_sha256"]
    assert "trades 5" in second["text"]


def test_the_period_a_request_names_must_make_sense(rig, book):  # noqa: F811
    for kind, bad in (("weekly", "20261109"), ("monthly", "2026-11"), ("fy", "FY9")):
        with pytest.raises(ApiError) as caught:
            build(rig, kind, period=bad)
        assert (caught.value.status, caught.value.code) == (400, "bad_request")
        assert str(caught.value.message).startswith("The period must be")
    with pytest.raises(ApiError) as caught:
        build(rig, "monthly", period="202701")
    assert caught.value.message == "That period has not started yet."


def test_with_no_period_a_request_builds_the_one_running_now(rig, book):  # noqa: F811
    build(rig, "weekly")
    build(rig, "monthly")
    build(rig, "fy")
    assert [kept(rig, k)["period_key"] for k in ("weekly", "monthly", "fy")] == [
        "2026W46", "202611", "FY2627"]  # fmt: skip


def test_the_desk_gets_the_text_markdown_safe_and_the_file_under_its_own_purpose(rig, book):  # noqa: F811
    first = book.read(OWNER, lambda tx: tx.rows("round_trip", close_day=20261102))[0]
    book.write(
        OWNER, lambda tx: tx.touch("round_trip", first["id"], {"strategy_override": "my_orb"}, 1)
    )
    build(rig, "monthly", period="202611")
    row = kept(rig, "monthly")
    got = call(rig, "report_get", "delivery", id=row["id"])
    assert "my\\_orb" in got["text"]
    assert call(rig, "report_file", "delivery", id=row["id"])["file_name"] == row["file_name"]


# ---- the jobs --------------------------------------------------------------------------------
def at(day, clock):
    return ist_ms(day, clock + ":00")


def run(rig, job, module, day, clock):
    return asyncio.run(once_a_day(rig.svc, job, at(day, clock), module))


import asyncio  # noqa: E402


def test_the_week_is_built_on_sunday_evening_and_not_on_another_day(rig, book):  # noqa: F811
    async def go():
        wrong = await once_a_day(rig.svc, "weekly", at(20261107, "18:05"), digest.weekly_job)
        right = await once_a_day(rig.svc, "weekly", at(SUNDAY, "18:05"), digest.weekly_job)
        again = await once_a_day(rig.svc, "weekly", at(SUNDAY, "18:20"), digest.weekly_job)
        return wrong, right, again

    assert scenario(rig, go) == (None, "ok", None)
    assert kept(rig, "weekly")["period_key"] == "2026W45"
    assert rig.svc.store.read(OWNER, lambda tx: tx.job_get("weekly", SUNDAY))["status"] == "ok"


def test_a_job_missed_by_a_service_that_was_down_runs_late(rig, book):  # noqa: F811
    async def go():
        return await once_a_day(rig.svc, "weekly", at(SUNDAY, "21:00"), digest.weekly_job)

    assert scenario(rig, go) == "late"
    assert kept(rig, "weekly") is not None


def test_the_month_and_the_year_are_built_on_their_days(rig, book):  # noqa: F811
    async def go():
        month = await once_a_day(rig.svc, "monthly", at(20261201, "07:05"), statement.monthly_job)
        year = await once_a_day(rig.svc, "fy", at(20270401, "07:05"), fypack.fy_job)
        none = await once_a_day(rig.svc, "fy", at(20270402, "07:05"), fypack.fy_job)
        return month, year, none

    assert scenario(rig, go) == ("ok", "ok", None)
    assert kept(rig, "monthly")["period_key"] == "202611"
    assert kept(rig, "fy")["period_key"] == "FY2627"
    assert kept(rig, "monthly")["file_name"].endswith(".pdf")


def test_each_job_has_its_own_switch(rig, book):  # noqa: F811
    def off(tx):
        s = {k: v for k, v in tx.settings().items() if k != "version"}
        tx.settings_save({**s, "weekly_on": False, "monthly_on": False, "fy_on": False},
                         tx.settings()["version"], 1)  # fmt: skip

    book.write(OWNER, off)

    async def go():
        a = await once_a_day(rig.svc, "weekly", at(SUNDAY, "18:05"), digest.weekly_job)
        b = await once_a_day(rig.svc, "monthly", at(20261201, "07:05"), statement.monthly_job)
        c = await once_a_day(rig.svc, "fy", at(20270401, "07:05"), fypack.fy_job)
        return a, b, c

    assert scenario(rig, go) == ("ok", "ok", "ok")  # the job ran, and built nothing
    assert [kept(rig, k) for k in ("weekly", "monthly", "fy")] == [None, None, None]
    detail = rig.svc.store.read(OWNER, lambda tx: tx.job_get("weekly", SUNDAY))["detail"]
    assert "switched off" in detail


def test_the_jobs_are_registered_with_the_service():
    from sudarshan.journal import service
    from sudarshan.journal.jobs import register_all

    register_all()
    for name in ("weekly", "monthly", "fy"):
        assert name in service.JOBS


# ---- the family's variant --------------------------------------------------------------------
def test_the_family_variant_is_counts_only_whatever_the_rows_hold():
    rows = {"trading_days": 5, "trades": 7, "wins": 4, "losses": 3, "flats": 0,
            "net_paise": 123_456, "charges_paise": 4_000, "csv": "a,b\r\n1,2", "text": "Net +₹1,234.56",
            "avg_u": 5, "qty": 65, "win_rate": "57.14", "r_total_pct": "1.2"}  # fmt: skip
    out = periods.family_variant("weekly", "2026W45", rows)
    assert out == {"kind": "weekly", "period_key": "2026W45",
                   "line": "Trades this week: 7",
                   "counts": {"trading_days": 5, "trades": 7, "wins": 4, "losses": 3, "flats": 0}}  # fmt: skip
    blob = str(out)
    assert "₹" not in out["line"] and not re.search(r"paise|_u|qty|csv|text|pct|rate", blob)
    # the mask itself takes a money, price or quantity field out whatever it is called
    leaky = serialize.apply({"trades": 7, "net_paise": 1, "entry_avg_u": 2, "qty_max": 3,
                             "win_rate_pct": "5", "text": "x"}, serialize.viewer())  # fmt: skip
    assert leaky == {"trades": 7}
    assert periods.family_variant("monthly", "202610", {})["line"] is None


def test_a_viewer_never_sees_a_period_reports_text(rig, book):  # noqa: F811
    build(rig, "monthly", period="202611")
    row = kept(rig, "monthly")
    from sudarshan.journal.ops.report_ops import report_out

    masked = serialize.apply(report_out(row), serialize.viewer())
    assert "text" not in masked and "₹" not in str(masked)


def test_the_period_words_are_cores_over_the_closed_trips(rig, book):  # noqa: F811
    build(rig, "monthly", period="202611")
    trips = book.read(OWNER, lambda tx: tx.rows("round_trip", status="closed"))
    m = metrics.compute([{**t, "charges_paise": t["charges_est_paise"]} for t in trips])
    text = kept(rig, "monthly")["text"]
    assert f"Net {words.rupees(m['net_paise'], True)}" in text
    assert f"trades {m['trips']}" in text
