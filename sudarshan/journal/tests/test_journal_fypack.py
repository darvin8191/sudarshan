"""The year's records pack (M19.10, GA5.2; P8 J4): one zip in the Reports archive each April.

What is pinned here: the files and their order; the year's edges (31 March, 1 April) in every
store, read in India's time; the order of 4 April (the Tax pack, then the pack, both before the
backup that is the year's archive), in a year when 4 April is a Sunday and one when it is not;
that no secret leaves a store; that every store is read and never written; and that nothing
opened is left open. Every date is fixed and the clock is the rig's; every value is synthetic.
"""

from __future__ import annotations

import contextlib
import csv
import dataclasses
import gc
import io
import json
import os
import sqlite3
import zipfile
from datetime import UTC, date, datetime

import pytest
from render_pdfread import read_pdf
from test_journal_analytics import book as book  # noqa: F401  (a fixture)
from test_journal_capture import files as files  # noqa: F401  (a fixture)
from test_journal_capture import ist_ms, placed, scenario
from test_journal_capture import rig as rig  # noqa: F401  (a fixture)
from test_journal_period_reports import at, build, file_of, kept
from test_journal_reports import call

from sudarshan.alerts.market import spec
from sudarshan.alerts.market.engine import Fire, State
from sudarshan.alerts.market.store import DB_FILENAME, MarketStore
from sudarshan.journal import openalgo_ro, periods
from sudarshan.journal.core import kinds
from sudarshan.journal.errors import ApiError
from sudarshan.journal.jobs import fypack
from sudarshan.journal.schedule import FY_AGAIN_AT, once_a_day
from sudarshan.journal.store import Scope
from sudarshan.ops import retention

OWNER = Scope.owner()
#: The year FY 2026-27 and the four instants at its edges, as (India day, clock, inside?).
EDGES = (
    (20260331, "23:59:59", False),
    (20260401, "00:00:00", True),
    (20270331, "23:59:59", True),
    (20270401, "00:00:00", False),
)
SECRET = "secret-test-key"  # the API key the fake order log carries in every request
MEMBERS = [
    "SUDARSHAN_Tax-pack_FY2627.pdf", "trades.csv", "orders.csv", "risk-history.csv",
    "alerts.csv", "assistant-calls.csv", "server-diary.jsonl", "00-read-first.pdf",
]  # fmt: skip


def _text(day: int, clock: str) -> str:
    return f"{day // 10000}-{day // 100 % 100:02d}-{day % 100:02d} {clock}"


def _utc_text(day: int, clock: str) -> str:
    """An India time as the risk store writes it: naive UTC text."""
    from datetime import UTC

    stamp = datetime.fromtimestamp(ist_ms(day, clock) / 1000, UTC)
    return stamp.strftime("%Y-%m-%d %H:%M:%S.%f")


@pytest.fixture
def year(rig, files, tmp_path):  # noqa: F811
    """Every store the pack reads, each with a row at the four edges of FY 2026-27."""
    with contextlib.closing(sqlite3.connect(files / "openalgo.db")) as conn, conn:
        for n, (day, clock, _) in enumerate(EDGES):
            row = placed(f"edge{n}", strategy=f"Edge {n}", at=_text(day, clock))
            conn.execute(
                "INSERT INTO order_logs (api_type, request_data, response_data, created_at) "
                "VALUES (?, ?, ?, ?)",
                (row["api_type"], json.dumps(row["request_data"]),
                 json.dumps(row["response_data"]), row["created_at"]),
            )  # fmt: skip
        conn.execute(
            "INSERT INTO order_logs (api_type, request_data, response_data, created_at) "
            "VALUES ('orderstatus', ?, '{}', '2026-06-01 10:00:00')",
            (json.dumps({"apikey": SECRET, "orderid": "edge1"}),),
        )
    with contextlib.closing(sqlite3.connect(files / "sudarshan_risk.db")) as conn, conn:
        conn.execute(
            "CREATE TABLE risk_audit (id INTEGER PRIMARY KEY, at DATETIME, last_at DATETIME, "
            "kind TEXT, pipe TEXT, surface TEXT, actor TEXT, activation_id TEXT, count INTEGER, "
            "merge_key TEXT, summary TEXT, detail TEXT)"
        )
        for n, (day, clock, _) in enumerate(EDGES):
            stamp = _utc_text(day, clock)
            conn.execute(
                "INSERT INTO risk_audit (at, last_at, kind, pipe, surface, actor, count, "
                "summary, detail) VALUES (?, ?, 'refused', 'live', 'app', 'Account owner', 1, ?, ?)",
                (stamp, stamp, f"Refused {n}", json.dumps({"apikey": SECRET})),
            )
        conn.execute(
            "INSERT INTO daily_locks VALUES ('live', '2026-03-31', '2026-03-31 08:00:00', -5, 5)"
        )
        conn.execute(
            "INSERT INTO daily_locks VALUES ('live', '2027-03-31', '2027-03-31 08:00:00', -5, 5)"
        )
    alerts_dir, watch_dir = tmp_path / "alerts", tmp_path / "watch"
    made = ist_ms(20260301, "10:00:00")
    store = MarketStore(alerts_dir / DB_FILENAME)
    try:
        fields = spec.check(
            {"symbol": "SBIN", "exchange": "NSE", "instrument_key": "NSE_EQ|INE062A01020",
             "tick_size": "0.05", "kind": "price",
             "params": {"op": "crosses_above", "level": "100"}}, made,
        )  # fmt: skip
        alert = store.create("owner", fields, made)
        for n, (day, clock, _) in enumerate(EDGES):
            fired = ist_ms(day, clock)
            store.record_fire(
                "owner", Fire(alert["id"], f"1:v1:{n}", day, 0, fired, fired, 10005, 10000, 1, 1,
                              version=1),
                State(), title=f"SBIN crossed {n}", line="l", value="100.05", level="100",
                channels={"telegram": True, "app": True},
            )  # fmt: skip
        store.record_test("owner", "A test", "l", ist_ms(20260601, "10:00:00"),
                          {"telegram": True, "app": True})  # fmt: skip
    finally:
        store.close()
    (watch_dir / "log").mkdir(parents=True)
    by_year: dict[int, list[str]] = {}
    for n, (day, clock, _) in enumerate(EDGES):
        line = json.dumps({"id": f"watch-disk-{n}", "src": "watch", "kind": "watch_warning",
                           "at": ist_ms(day, clock) // 1000, "run": "r", "check": "disk"})  # fmt: skip
        by_year.setdefault(day // 10000, []).append(line + "\n")
    # a line the watch was still writing when the pack was built: never part of the record
    by_year[2027].append('{"id":"cut-off","src":"watch","kind":"watch_fine","at":17900')
    by_year[2026].insert(0, "not json at all\n")
    for calendar_year, lines in by_year.items():
        (watch_dir / "log" / f"{calendar_year}.jsonl").write_text("".join(lines))
    rig.svc.settings = dataclasses.replace(
        rig.svc.settings, alerts_dir=alerts_dir, watch_dir=watch_dir
    )
    return rig


def _zip(rig):  # noqa: F811
    return zipfile.ZipFile(io.BytesIO(file_of(rig, kept(rig, "records"))))


def _rows(pack: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(pack.read(name).decode("utf-8"))))


def _job(rig, day: int, clock: str = "07:05"):  # noqa: F811
    async def go():
        return await once_a_day(rig.svc, "fy", at(day, clock), fypack.fy_job)

    return scenario(rig, go)


# ---- the order log's lines, pure ---------------------------------------------------------------
def test_an_order_request_becomes_a_line_and_the_api_key_is_never_copied():
    (line,) = openalgo_ro.order_lines(placed("o9", at="2026-11-09 10:00:59"), "live")
    assert line == {
        "time_ist": "2026-11-09T10:00:59+05:30", "mode": "live", "request": "placeorder",
        "strategy": "ORB", "exchange": "NFO", "symbol": "NIFTY25NOV2625000CE", "action": "BUY",
        "quantity": "65", "pricetype": "LIMIT", "product": "MIS", "price": "100.05",
        "trigger_price": "0", "order_id": "", "answer": "success", "answer_order_ids": "o9",
        "answer_message": "",
    }  # fmt: skip
    assert set(line) == set(openalgo_ro.ORDER_COLUMNS) and SECRET not in str(line)


def test_a_basket_is_a_line_per_leg_a_refusal_keeps_its_words_and_a_read_is_no_order():
    basket = {
        "api_type": "basketorder",
        "request_data": {"apikey": SECRET, "strategy": "Pair",
                         "orders": [{"symbol": "ACME", "exchange": "NSE", "action": "BUY",
                                     "quantity": 1},
                                    {"symbol": "BETA", "exchange": "NSE", "action": "SELL",
                                     "quantity": 2}]},
        "response_data": {"status": "success",
                          "results": [{"orderid": "b1"}, {"orderid": "b2"}]},
        "created_at": "2027-03-31 23:59:59.123456",
    }  # fmt: skip
    first, second = openalgo_ro.order_lines(basket, "live")
    assert (first["symbol"], first["answer_order_ids"]) == ("ACME", "b1")
    assert (second["symbol"], second["action"], second["answer_order_ids"]) == (
        "BETA",
        "SELL",
        "b2",
    )
    assert first["strategy"] == second["strategy"] == "Pair"
    refused = placed("x", api_type="cancelorder", orderid="261109000000001")
    refused["response_data"] = {"status": "error", "message": "Order is\nalready complete"}
    (line,) = openalgo_ro.order_lines(refused, "sandbox")
    assert (line["order_id"], line["answer"], line["mode"]) == (
        "261109000000001", "error", "sandbox",
    )  # fmt: skip
    assert line["answer_message"] == "Order is already complete"  # one line, as a cell must be
    for read in sorted(openalgo_ro.READS):
        assert openalgo_ro.order_lines(placed("r", api_type=read), "live") == []
    odd = {"api_type": "placeorder", "request_data": "not json", "response_data": None,
           "created_at": "soon"}  # fmt: skip
    assert openalgo_ro.order_lines(odd, "live")[0]["time_ist"] == ""


def test_the_order_log_leaves_reads_and_mode_switches_out_in_its_query(rig, files, monkeypatch):  # noqa: F811
    """v0.16.0 review L2: the reads (which can far outnumber the orders) are left out by the
    query, so their request and answer text is never fetched; a switch between Live and Analyze
    mode is not an order request either. Pages stay whole across the rows left out."""
    assert "analyzer_toggle" in openalgo_ro.READS
    kinds_logged = ("orderstatus", "analyzer_toggle", "placeorder", "openposition",
                    "analyzer_status", "placeorder", "margin", "cancelorder")  # fmt: skip
    with contextlib.closing(sqlite3.connect(files / "openalgo.db")) as conn, conn:
        for n, api_type in enumerate(kinds_logged):
            row = placed(f"m{n}", strategy=f"Row {n}", api_type=api_type,
                         at=f"2026-11-1{n} 10:00:00")  # fmt: skip
            conn.execute(
                "INSERT INTO order_logs (api_type, request_data, response_data, created_at) "
                "VALUES (?, ?, ?, ?)",
                (row["api_type"], json.dumps(row["request_data"]),
                 json.dumps(row["response_data"]), row["created_at"]),
            )  # fmt: skip
    fetched: list[str] = []
    real = openalgo_ro._ReadOnly.query

    def query(self, sql, **params):
        rows = real(self, sql, **params)
        fetched.extend(str(r["api_type"]) for r in rows if "api_type" in r)
        return rows

    monkeypatch.setattr(openalgo_ro._ReadOnly, "query", query)
    lines = list(openalgo_ro.records_for(rig.svc).order_log("live", 20261101, 20261130, page=2))
    assert not set(fetched) & openalgo_ro.READS
    assert [(line["request"], line["strategy"]) for line in lines] == [
        ("placeorder", "ORB"), ("placeorder", "Breakout"), ("placeorder", "Row 2"),
        ("placeorder", "Row 5"), ("cancelorder", "Row 7"),
    ]  # fmt: skip


# ---- the pack ----------------------------------------------------------------------------------
def test_the_pack_of_4_april_holds_the_year_from_1_april_to_31_march_and_no_more(year, book):  # noqa: F811
    assert date(2027, 4, 4).weekday() == 6, "4 April 2027 is a Sunday"
    assert _job(year, 20270404) == "ok"
    row = kept(year, "records")
    assert (row["period_key"], row["file_name"], row["file_mime"]) == (
        "FY2627", "SUDARSHAN_Records-pack_FY2627.zip", "application/zip",
    )  # fmt: skip
    with _zip(year) as pack:
        assert pack.testzip() is None and pack.namelist() == MEMBERS
        orders, risk = _rows(pack, "orders.csv"), _rows(pack, "risk-history.csv")
        alerts, trades = _rows(pack, "alerts.csv"), _rows(pack, "trades.csv")
        diary = [json.loads(ln) for ln in pack.read("server-diary.jsonl").splitlines()]
        assistant = pack.read("assistant-calls.csv").decode()
        everything = b"".join(pack.read(name) for name in pack.namelist())
        stamps = {info.date_time for info in pack.infolist()}
    # the year's edges, in India's time, in every store
    assert [o["strategy"] for o in orders if o["strategy"].startswith("Edge")] == [
        "Edge 1", "Edge 2",
    ]  # fmt: skip
    edge = next(o for o in orders if o["strategy"] == "Edge 1")
    assert edge["time_ist"] == "2026-04-01T00:00:00+05:30"
    assert {o["mode"] for o in orders} == {"live", "sandbox"} and len(orders) == 5
    assert all(o["request"] != "orderstatus" for o in orders)
    assert [r["summary"] for r in risk] == [
        "Refused 1", "Refused 2",
        "Day's P&L -1000.0 against a limit of 1000.0 rupees on 2026-11-09",
        "Day's P&L -5.0 against a limit of 5.0 rupees on 2027-03-31",
    ]  # fmt: skip
    assert risk[2]["what"] == "daily_loss_limit_reached" and risk[2]["time_ist"].startswith(
        "2026-11-09T13:42"
    )
    assert risk[1]["time_ist"] == "2027-03-31T23:59:59+05:30" and risk[0]["by"] == "Account owner"
    assert [a["title"] for a in alerts] == ["SBIN crossed 1", "SBIN crossed 2"]
    assert alerts[0]["time_ist"] == "2026-04-01T00:00:00+05:30" and alerts[0]["telegram"]
    assert [d["id"] for d in diary] == ["watch-disk-1", "watch-disk-2"]
    assert len(trades) == 4 and trades[0]["Close day"] == "2026-11-02"
    assert assistant == ",".join(fypack.ASSISTANT_COLUMNS) + "\r\n"  # the header alone
    # nothing secret leaves a store: not the API key, not the audit's detail
    assert SECRET.encode() not in everything and b"apikey" not in everything
    assert stamps == {(2027, 4, 4, 7, 5, 0)}  # the build's own time, never the wall clock
    assert row["rows"]["files"] == [
        {"name": "trades.csv", "rows": 4, "from": "2026-11-02", "to": "2026-11-09", "note": None},
        {"name": "orders.csv", "rows": 5, "from": "2026-04-01", "to": "2027-03-31", "note": None},
        {"name": "risk-history.csv", "rows": 4, "from": "2026-04-01", "to": "2027-03-31",
         "note": None},
        {"name": "alerts.csv", "rows": 2, "from": "2026-04-01", "to": "2027-03-31", "note": None},
        {"name": "server-diary.jsonl", "rows": 2, "from": "2026-04-01", "to": "2027-03-31",
         "note": None},
    ]  # fmt: skip
    assert row["announced_ms"] is None and row["status"] == "ready"  # kept, never sent


def test_the_first_page_is_one_page_and_says_whose_record_is_whose(year, book):  # noqa: F811
    assert _job(year, 20270404) == "ok"
    row = kept(year, "records")
    with _zip(year) as pack:
        page = read_pdf(pack.read(fypack.READ_FIRST))
    assert len(page.pages) == 1
    lines = [ln for ln in page.pages[0].lines if ln.strip()]
    text = " ".join(lines)
    assert "Upstox issues are the record the tax office and the regulator recognise" in text
    assert text.index("working record") < text.index("In this pack") < text.index("Add these")
    for item in ("contract notes", "ledger", "bank", "TradingView"):
        assert item in text
    assert "orders.csv: 5 order requests and their answers, 1 Apr 2026 to 31 Mar 2027." in text
    assert "assistant-calls.csv holds its header only" in text and "not part of this" in text
    said = row["text"].split("\n")
    assert said[0] == "सुदर्शन · SUDARSHAN" and said[1] == "Records pack · FY 2026-27"
    assert said[2] == fypack.WHOSE and fypack.NO_ASSISTANT in said
    assert [ln for ln in said if ln.startswith("[ ]")] == list(fypack.CHECKLIST)
    assert "SUDARSHAN_Tax-pack_FY2627.pdf: the year's Tax pack, a working paper." in said
    assert fypack.TOO_LARGE not in row["text"]


@pytest.mark.parametrize(("day", "weekday"), [(20270404, 6), (20280404, 1)])
def test_on_4_april_the_tax_pack_is_rebuilt_first_and_both_are_in_the_years_archive(
    year,
    book,
    day,
    weekday,  # noqa: F811
):
    """The order of the day, on a Sunday (2027) and on a Tuesday (2028): 07:00 the Tax pack,
    then the pack with that very file in it, both before 08:00, from when a backup is the year's
    archive (``retention.FY_FROM``); the 16:30 run of that day is the first after it."""
    opening = day - 3  # 1 April
    assert date(day // 10000, 4, 4).weekday() == weekday
    assert _job(year, opening) == "ok"
    first_paper, first_pack = kept(year, "fy"), kept(year, "records")
    assert _job(year, opening + 1) is None and _job(year, opening + 2) is None  # 2 and 3 April
    assert _job(year, day, "06:55") is None
    assert _job(year, day) == "ok"
    paper, pack = kept(year, "fy"), kept(year, "records")
    key = f"FY{(day // 10000 - 1) % 100}{day // 10000 % 100}"
    assert paper["period_key"] == pack["period_key"] == key
    assert (paper["id"], pack["id"]) == (first_paper["id"], first_pack["id"])  # built over
    assert paper["built_ms"] == pack["built_ms"] == at(day, "07:05") > first_pack["built_ms"]
    with _zip(year) as inside:
        assert inside.read(f"SUDARSHAN_Tax-pack_{key}.pdf") == file_of(year, paper)
    month, mday, hour = retention.FY_FROM
    assert (month, mday) == FY_AGAIN_AT[:2] == (4, 4)
    whole_from = ist_ms(day, f"{hour:02d}:00:00")
    assert pack["built_ms"] < whole_from <= ist_ms(day, "16:30:00")
    label = f"{day // 10000 - 1}-{day // 10000 % 100}"
    assert retention.fy_label(datetime(day // 10000, 4, 4, 16, 30)) == label
    assert retention.fy_label(datetime(day // 10000, 4, 4, 7, 59)) != label
    detail = year.svc.store.read(OWNER, lambda tx: tx.job_get("fy", day))["detail"]
    assert detail == f"{key} kept as report {paper['id']}; records pack kept as report {pack['id']}"
    assert _job(year, day, "09:00") is None and _job(year, day + 1) is None  # once, that day


def test_the_backup_takes_the_journals_database_where_the_pack_is_kept():
    """The pack lives in journal.db (the Reports archive); the nightly archive takes every
    database in the ring's state folders, so the year's archive holds it."""
    from sudarshan.journal import config, store
    from sudarshan.ops import backup

    assert backup.RING_VAR == "/var/lib" and config.STATE_DIR == "/var/lib/sudarshan-journal"
    assert store.DB_FILENAME.endswith(".db")


def test_a_tax_pack_that_had_to_wait_never_keeps_the_records_out_of_the_archive(year, book):  # noqa: F811
    """4 April, the paper cannot be read: the Tax pack of 1 April stays and the job is asked
    again, but the records pack is built all the same, with the Tax pack that is kept."""
    year.svc.settings = dataclasses.replace(year.svc.settings, tax=True)
    assert _job(year, 20270401) == "ok"
    paper = kept(year, "fy")
    year.portfolio.down = True
    assert _job(year, 20270404) == "failed"
    job = year.svc.store.read(OWNER, lambda tx: tx.job_get("fy", 20270404))
    assert job["detail"] == periods.PAPER_KEPT
    pack = kept(year, "records")
    assert (
        pack["built_ms"] == at(20270404, "07:05")
        and kept(year, "fy")["built_ms"] == (paper["built_ms"])
    )
    with _zip(year) as inside:
        assert inside.read("SUDARSHAN_Tax-pack_FY2627.pdf") == file_of(year, paper)


def test_a_store_that_cannot_be_read_puts_the_job_off_and_a_build_on_request_says_so(year, book):  # noqa: F811
    (year.svc.settings.alerts_dir / DB_FILENAME).write_bytes(b"not a database at all")
    for side in ("-wal", "-shm"):
        (year.svc.settings.alerts_dir / (DB_FILENAME + side)).unlink(missing_ok=True)
    assert _job(year, 20270404) == "failed"
    assert kept(year, "records") is None
    job = year.svc.store.read(OWNER, lambda tx: tx.job_get("fy", 20270404))
    assert job["detail"] == (
        "The year's records pack was not built: the alerts' history could not be read just "
        "now. It is tried again shortly."
    )
    year.now = at(20270404, "07:10")
    assert build(year, "records", period="FY2627")["words"] == "The records pack is in the archive."
    row = kept(year, "records")
    assert "alerts.csv: the alerts' history could not be read just now" in row["text"]
    with _zip(year) as pack:
        assert _rows(pack, "alerts.csv") == [] and len(_rows(pack, "orders.csv")) == 5


def test_a_server_without_the_other_stores_still_gets_its_pack(rig, book):  # noqa: F811
    """A laptop, or a server where alerts and the watch never ran: each file is there with its
    header, and the page says which stores are not kept. No Tax pack: the pack says how to get
    one in."""
    rig.now = at(20270331, "22:00")
    built = build(rig, "records")
    row = kept(rig, "records")
    assert built["id"] == row["id"] and row["period_key"] == "FY2627"
    assert row["rows"]["partial"] is False  # 31 March is the year's last day: the whole year
    with _zip(rig) as pack:
        assert pack.namelist() == MEMBERS[1:]
        assert pack.read("alerts.csv").decode() == ",".join(fypack.ALERT_COLUMNS) + "\r\n"
        assert pack.read("server-diary.jsonl") == b""
    for line in (
        "alerts.csv: the alerts' history is not kept on this server, so the file holds its "
        "header only.",
        "server-diary.jsonl: the server diary is not kept on this server, so the file holds "
        "its header only.",
        "risk-history.csv: the risk layer's history is not kept on this server, so the file "
        "holds its header only.",
        fypack.NO_TAX_PACK,
    ):
        assert line in row["text"].split("\n")


def test_a_request_builds_the_running_year_to_today_or_the_year_it_names(year, book):  # noqa: F811
    year.now = at(20270401, "00:30")  # the new year's first half hour
    build(year, "records")
    running = kept(year, "records")
    assert (running["period_key"], running["rows"]["partial"]) == ("FY2728", True)
    assert "Records pack · FY 2027-28, to Thu 1 Apr" in running["text"]
    with _zip(year) as pack:  # only what is past the edge
        assert [o["strategy"] for o in _rows(pack, "orders.csv")] == ["Edge 3"]
        assert [a["title"] for a in _rows(pack, "alerts.csv")] == ["SBIN crossed 3"]
    build(year, "records", period="FY2627")
    rows = year.svc.store.read(OWNER, lambda tx: tx.rows("report", kind="records", order="id"))
    assert [r["period_key"] for r in rows] == ["FY2728", "FY2627"]
    for bad in ("2026", "FY2628", "202611"):
        with pytest.raises(ApiError):
            build(year, "records", period=bad)
    with pytest.raises(ApiError, match="not started"):
        build(year, "records", period="FY2829")


def test_the_switch_of_the_tax_pack_is_the_packs_too(year, book):  # noqa: F811
    def off(tx):
        s = {k: v for k, v in tx.settings().items() if k != "version"}
        tx.settings_save({**s, "fy_on": False}, tx.settings()["version"], 1)

    book.write(OWNER, off)
    assert _job(year, 20270404) == "ok"
    assert kept(year, "fy") is None and kept(year, "records") is None


def test_the_same_records_make_the_same_bytes_and_nothing_is_written_or_left_open(year, book):  # noqa: F811
    span, now = periods.fy_span(2026, 20270404), at(20270404, "07:05")
    settings = year.svc.settings
    folders = (settings.openalgo_db.parent, settings.alerts_dir, settings.watch_dir / "log")
    seen = {str(f): {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in f.iterdir()}
            for f in folders}  # fmt: skip
    fypack.assemble(year.svc, span, now, True)  # the store's own handles open on first use
    open_before = len(os.listdir("/proc/self/fd"))
    first = fypack.assemble(year.svc, span, now, True)
    again = fypack.assemble(year.svc, span, now, True)
    (settings.alerts_dir / DB_FILENAME).write_bytes(b"not a database at all")
    for side in ("-wal", "-shm"):
        (settings.alerts_dir / (DB_FILENAME + side)).unlink(missing_ok=True)
    from sudarshan.journal.schedule import JobFailed

    with pytest.raises(JobFailed):
        fypack.assemble(year.svc, span, now, True)  # the failing path closes what it opened
    # A file SQLite refuses is let go by the garbage collector, not at once: the driver's own
    # error holds its connection in a cycle (measured: two descriptors, gone after a collect).
    # That is OpenAlgo's reader as it was; what is checked here is that nothing stays for good.
    gc.collect()
    assert len(os.listdir("/proc/self/fd")) == open_before
    assert first[0] == again[0] and first[1] == again[1]
    del seen[str(settings.alerts_dir)]  # this test broke that one itself
    for folder in (settings.openalgo_db.parent, settings.watch_dir / "log"):
        now_there = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()}
        assert now_there == seen[str(folder)]  # read, never written; no side file made


def test_the_first_page_is_dated_by_the_pack_not_the_clock(year, book):  # noqa: F811
    # fpdf2 writes the clock's second as a PDF's creation date unless told one, so two packs of
    # the same records built either side of a second were other bytes: the test above failed in
    # CI when its two builds did that.
    span, now = periods.fy_span(2026, 20270404), at(20270404, "07:05")
    with zipfile.ZipFile(io.BytesIO(fypack.assemble(year.svc, span, now, True)[0])) as pack:
        page = pack.read(fypack.READ_FIRST)
    stamp = datetime.fromtimestamp(now / 1000, tz=UTC).strftime("%Y%m%d%H%M%S")
    assert f"/CreationDate (D:{stamp}Z)".encode() in page


def test_a_pack_too_large_for_the_page_says_where_it_is_and_is_refused_in_the_same_words(
    year,
    book,  # noqa: F811
    monkeypatch,
):
    """v0.16.0 review L1: one rule, kinds.FILE_MAX_BYTES, for the pack's text and the download.
    A pack past it is refused before it is read out, in the pack's own words (it is in the
    backups); retrying never helps, so nothing says to try again. One within it downloads."""
    assert kinds.FILE_MAX_BYTES * 4 // 3 + 64 * 1024 <= kinds.ANSWER_MAX_BYTES
    assert kinds.FILE_MAX_BYTES > 5 * 1024 * 1024  # realistic packs are 1 to 2 MB
    year.now = at(20270404, "07:10")
    build(year, "records", period="FY2627")
    whole = kept(year, "records")
    assert fypack.TOO_LARGE not in whole["text"]
    assert call(year, "report_file", id=whole["id"])["file_name"].endswith(".zip")

    monkeypatch.setattr(kinds, "FILE_MAX_BYTES", 100)
    build(year, "records", period="FY2627")
    row = kept(year, "records")
    assert row["text"].endswith(fypack.TOO_LARGE)
    with pytest.raises(ApiError) as refused:
        call(year, "report_file", id=row["id"])
    assert (refused.value.status, refused.value.code) == (422, "refused")
    assert refused.value.message == fypack.TOO_LARGE
    assert "again" not in refused.value.message


def test_any_report_file_too_large_for_the_page_is_refused_with_where_it_is(
    year,
    book,  # noqa: F811
    monkeypatch,
):
    from sudarshan.journal.ops import report_ops

    build(year, "weekly", period="2026W45")
    week = kept(year, "weekly")
    monkeypatch.setattr(kinds, "FILE_MAX_BYTES", 100)
    with pytest.raises(ApiError) as refused:
        call(year, "report_file", id=week["id"])
    assert (refused.value.code, refused.value.message) == ("refused", report_ops.TOO_LARGE)


def test_the_kind_has_its_title_and_its_file_name():
    assert "records" in kinds.REPORT_KINDS and kinds.REPORT_TITLES["records"] == "Records pack"
    assert periods.MIME["zip"] == "application/zip"
