"""The job for the tax pack, 1 April at 07:00 for the financial year just ended (M15.5) (JB9),
and again 4 April at 07:00, when the year's last charges have arrived (``schedule.FY_AGAIN_AT``):
the same job, the same year, the report built over. That second build never puts a pack without
the tax working paper over one that has it (v0.13.0 verify N3).

The work is ``periods.build_period``; this is the once-a-day run ``schedule.py`` asks for.

**The year's records pack** (M19.10, GA5.2) is built by the same run, straight after the Tax
pack: one zip in the Reports archive (report kind ``records``, key ``FY2627``) holding

    00-read-first.pdf            one page: whose record is whose, what is in the pack, and the
                                 documents the owner adds from Upstox and his bank
    SUDARSHAN_Tax-pack_<key>.pdf the year's Tax pack as the archive keeps it
    trades.csv                   the journal's closed trades of the year
    orders.csv                   every order request and its answer (OpenAlgo's own log)
    risk-history.csv             what the risk layer refused, the kill switch, daily limits
    alerts.csv                   the market alerts that fired
    assistant-calls.csv          the AI assistant's calls: the header alone until it is built
    server-diary.jsonl           every server notice of the year (the watch's diary)

* **The order of the day.** 4 April, 07:00: the Tax pack is rebuilt, then this pack, which
  takes the Tax pack just kept. The year's archive is the first complete backup made from
  4 April 08:00 (``sudarshan/ops/retention.py`` ``FY_FROM``; the nightly run is at 16:30), and
  it takes ``journal.db``, where the Reports archive lives: so the pack is inside the archive
  kept 8 years. The pack is also built when the Tax pack's rebuild had to be put off, so a
  paper that cannot be read never keeps the records out of the archive.
* **Every other store is read, never written**, each a page at a time: OpenAlgo's log
  (``openalgo_ro.order_log``), the risk layer's file (``risk_ro.history``), the alerts'
  ``market.db`` and the watch's diary. A store that is not on this server gives its file with
  the header alone and a line saying so. One that is there and cannot be read just now puts the
  job off (it is asked again in 15 minutes); a build the owner asks for says so and goes on.
* **Each file says what it covers** from its own rows (how many, first day, last day), never
  from a rule: a store that keeps less than a year shows it here.
* **Nothing is sent.** The pack is not announced to the report desk: a zip is not a file
  Telegram's report path takes. It is downloaded from the Reports page.
* **The same bytes for the same records**: members are written in a fixed order with the build
  time the caller gives, never the wall clock.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import zipfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..core import kinds, words
from ..core.money import IST, to_date
from ..openalgo_ro import ORDER_COLUMNS, RecordsUnavailable, _ReadOnly, records_for
from ..periods import (
    SWITCHES,
    Span,
    built_label,
    current_fy_start,
    fy_span,
    keep_file,
    period_job,
    span_for_job,
)
from ..render import doc as D
from ..render import tables
from ..render.pdf import render_pdf
from ..reports import OWNER, _thread, archive, report_switch
from ..risk_ro import HISTORY_COLUMNS, history, risk_for
from ..schedule import FY_AGAIN_AT, JobFailed, register_job

log = logging.getLogger("sudarshan.journal")

RECORDS = "records"
READ_FIRST = "00-read-first.pdf"
MARKET_DB = "market.db"
DIARY_FOLDER = "log"  # sudarshan/ops/watch/diary.py FOLDER
ALERT_COLUMNS = (
    "time_ist", "exchange", "symbol", "kind", "title", "line", "detail", "note", "value",
    "level", "found_later", "at_the_open", "telegram", "telegram_reason", "app",
)  # fmt: skip
#: What the assistant's calls file will carry once its tools exist (P8 U8): the header is fixed
#: now so a year with no calls and a year with calls open the same way.
ASSISTANT_COLUMNS = ("time_ist", "client", "tool", "arguments_sha256", "outcome")
ALERT_PAGE = 2000
#: One diary line is one notice: a longer line is not one, and a file past this is not read on.
DIARY_LINE_MAX, DIARY_BYTES_MAX = 64 * 1024, 64 << 20
#: The Reports page downloads through OpenAlgo's bridge, which passes a file up to about this
#: size (``blueprints/sudarshan_journal.py`` ``DOWNLOAD_MAX_BYTES``, less the wrapping).
PAGE_DOWNLOAD_MAX = 5 * 1024 * 1024
_CELL = 300

WHOSE = (
    "SUDARSHAN's books are your working record. The statements Upstox issues are the record "
    "the tax office and the regulator recognise: keep both, and give your CA Upstox's own "
    "reports."
)
ADD_HEADING = "Add these yourself, from Upstox and your bank"
CHECKLIST = (
    "[ ] Upstox: the contract notes for the year (sent by email, and on its reports pages).",
    "[ ] Upstox: the ledger, the tradebook, the P&L statement and the tax statement for the year.",
    "[ ] Your bank: the statements of the account you fund trading from, with every transfer "
    "to and from Upstox.",
    "[ ] The strategy behind each automated order: the TradingView alert's name and the "
    "script as it was that year.",
)
KEEP_LINE = (
    "Keep this pack with those papers. How many years they must be kept is a question for "
    "your CA; SUDARSHAN keeps one archive of each year for 8."
)
NO_ASSISTANT = (
    "assistant-calls.csv holds its header only: the AI assistant's own tools are not part of "
    "this release, so it made no calls to list."
)
NO_TAX_PACK = (
    "The year's Tax pack is not in the Reports archive, so it is not in this pack. Build the "
    "Tax pack from Reports, then build this pack again."
)
NOT_KEPT = "not kept on this server, so the file holds its header only"
NOT_READ = "could not be read just now, so the file holds its header only; build the pack again"
PUT_OFF = (
    "The year's records pack was not built: {what} could not be read just now. It is tried "
    "again shortly."
)
TOO_LARGE = (
    "This pack is larger than the Reports page can download. It is in the server's backups; "
    "have it copied from the server."
)
WHAT = {
    "trades.csv": "the journal's closed trades",
    "orders.csv": "order requests and their answers",
    "risk-history.csv": "lines of the risk layer's history",
    "alerts.csv": "market alerts that fired",
    "server-diary.jsonl": "server notices",
}
STORE = {
    "orders.csv": "OpenAlgo's order log",
    "risk-history.csv": "the risk layer's history",
    "alerts.csv": "the alerts' history",
    "server-diary.jsonl": "the server diary",
}


class NotKept(Exception):
    """A store that is not on this server: its file is the header alone, and says so."""


@dataclass
class Part:
    """One file of the pack and what it covers, from its own rows."""

    name: str
    rows: int = 0
    first: date | None = None
    last: date | None = None
    note: str = ""

    def saw(self, when: str | date | None) -> None:
        """Count one row; ``when`` is its India date (or an ISO time whose date is taken)."""
        self.rows += 1
        if isinstance(when, str):
            try:
                when = date.fromisoformat(when[:10])
            except ValueError:
                when = None
        if when is not None:
            self.first = when if self.first is None or when < self.first else self.first
            self.last = when if self.last is None or when > self.last else self.last

    def line(self) -> str:
        """``orders.csv: 1,204 order requests and their answers, 3 Apr 2026 to 30 Mar 2027.``"""
        if self.note:
            return f"{self.name}: {self.note}."
        what = WHAT.get(self.name, "rows")
        if not self.rows:
            return f"{self.name}: no {what} in this year."
        count = words.group_en_in(str(self.rows))
        if self.first is None or self.last is None:
            return f"{self.name}: {count} {what}."
        return f"{self.name}: {count} {what}, {_day(self.first)} to {_day(self.last)}."

    def out(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rows": self.rows,
            "from": None if self.first is None else self.first.isoformat(),
            "to": None if self.last is None else self.last.isoformat(),
            "note": self.note or None,
        }


def _day(day: date) -> str:
    return f"{day.day} {day:%b} {day.year}"


def _need(file: _ReadOnly, *tables_needed: str) -> None:
    """``NotKept`` unless the file is there and holds each table: a store that was never used
    (or a release's older file without the table) has no history, which is not a failed read.
    Raises ``RecordsUnavailable`` when the file itself cannot be read."""
    if file.path is None or not Path(file.path).is_file():
        raise NotKept(file.name)
    for table in tables_needed:
        if not file.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = :name", name=table
        ):
            raise NotKept(file.name)


def _bounds(span: Span) -> tuple[int, int]:
    """The span as epoch milliseconds: from its first India midnight to the one after its last."""
    first = datetime.combine(to_date(span.start), datetime.min.time(), IST)
    after = datetime.combine(to_date(span.end) + timedelta(days=1), datetime.min.time(), IST)
    return int(first.timestamp() * 1000), int(after.timestamp() * 1000)


# ---- the readers that live nowhere else --------------------------------------------------------
def alert_rows(folder: Path | None, span: Span, page: int = ALERT_PAGE) -> Iterator[dict[str, str]]:
    """The owner's market alerts that fired in the span, oldest first, with where each went
    (``sudarshan/alerts/market/store.py``: ``alert_event`` and ``alert_delivery``). Test fires
    are left out: they are not alerts. Raises ``NotKept`` when there is no such file here and
    ``RecordsUnavailable`` when it cannot be read."""
    if folder is None:
        raise NotKept(MARKET_DB)
    file, (first, after_last) = _ReadOnly(folder / MARKET_DB, MARKET_DB), _bounds(span)
    _need(file, "alert_event", "alert_delivery")
    seen = 0
    while True:
        rows = file.query(
            "SELECT e.id, e.fired_at, e.exchange, e.symbol, e.kind, e.title, e.line, e.detail, "
            "e.note, e.value, e.level, e.found_later, e.opening, "
            "t.status AS telegram, t.reason AS telegram_reason, a.status AS app "
            "FROM alert_event e "
            "LEFT JOIN alert_delivery t ON t.event_id = e.id AND t.channel = 'telegram' "
            "LEFT JOIN alert_delivery a ON a.event_id = e.id AND a.channel = 'app' "
            "WHERE e.principal = 'owner' AND e.test = 0 AND e.id > :seen "
            "AND e.fired_at >= :first AND e.fired_at < :after ORDER BY e.id LIMIT :page",
            seen=seen,
            first=first,
            after=after_last,
            page=page,
        )
        for row in rows:
            at = datetime.fromtimestamp(int(row["fired_at"]) / 1000, IST)
            yield {
                "time_ist": at.isoformat(),
                **{k: _cell(row[k]) for k in ("exchange", "symbol", "kind", "title", "line",
                                              "detail", "note", "value", "level")},
                "found_later": "yes" if row["found_later"] else "",
                "at_the_open": "yes" if row["opening"] else "",
                "telegram": _cell(row["telegram"]),
                "telegram_reason": _cell(row["telegram_reason"]),
                "app": _cell(row["app"]),
            }  # fmt: skip
        if len(rows) < page:
            return
        seen = int(rows[-1]["id"])


def _cell(value: Any) -> str:
    return "" if value is None else " ".join(str(value).split())[:_CELL]


def diary_lines(folder: Path | None, span: Span) -> Iterator[tuple[bytes, date]]:
    """The server diary's lines of the span, as the watch wrote them, with each one's India
    date. A financial year lies in two of the diary's calendar-year files. Only whole lines
    count: the watch may be appending, so a last line without its end is left for next time
    (CLAUDE.md, "read a file a killed process writes as possibly cut off"). Raises ``NotKept``
    when the server keeps no diary and ``RecordsUnavailable`` when a year's file cannot be read."""
    if folder is None or not (folder / DIARY_FOLDER).is_dir():
        raise NotKept("the server diary")
    first, after_last = (ms // 1000 for ms in _bounds(span))
    for year in range(to_date(span.start).year, to_date(span.end).year + 1):
        path = folder / DIARY_FOLDER / f"{year}.jsonl"
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RecordsUnavailable("the server diary") from exc
        with os.fdopen(fd, "rb") as handle:
            read = 0
            for raw in handle:
                read += len(raw)
                if read > DIARY_BYTES_MAX:
                    break
                if not raw.endswith(b"\n") or len(raw) > DIARY_LINE_MAX:
                    continue
                try:
                    at = json.loads(raw).get("at")
                except (ValueError, AttributeError):
                    continue
                if isinstance(at, int | float) and not isinstance(at, bool):
                    if first <= at < after_last:
                        yield raw, datetime.fromtimestamp(at, IST).date()


# ---- the zip -----------------------------------------------------------------------------------
class _Zip:
    """A zip written in memory, member after member, each stamped with the build's own time."""

    def __init__(self, now: int):
        self.buffer = io.BytesIO()
        self.file = zipfile.ZipFile(self.buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6)
        self.stamp = datetime.fromtimestamp(now / 1000, IST).timetuple()[:6]

    def _info(self, name: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(name, self.stamp)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o600 << 16
        return info

    def put(self, name: str, data: bytes) -> None:
        self.file.writestr(self._info(name), data)

    def table(self, part: Part, columns: tuple[str, ...], rows: Iterable[Mapping[str, str]],
              when: str = "time_ist") -> None:  # fmt: skip
        """A CSV member: its header, then each row as it is read (never the year at once)."""
        with self.file.open(self._info(part.name), "w") as member:
            text = io.TextIOWrapper(member, encoding="utf-8", newline="")
            writer = csv.writer(text, lineterminator="\r\n")
            writer.writerow(columns)
            try:
                for row in rows:
                    writer.writerow([tables.csv_cell(row.get(name, "")) for name in columns])
                    part.saw(row.get(when))
            finally:
                text.flush()
                text.detach()

    def lines(self, part: Part, rows: Iterable[tuple[bytes, date]]) -> None:
        with self.file.open(self._info(part.name), "w") as member:
            for raw, day in rows:
                member.write(raw)
                part.saw(day)

    def close(self) -> bytes:
        self.file.close()
        return self.buffer.getvalue()

    def discard(self) -> None:
        with_open = self.file.fp is not None
        if with_open:
            self.file.close()
        self.buffer.close()


def _guarded(part: Part, strict: bool, fill: Callable[[], None]) -> None:
    """Fill one member from another service's store. A store not on this server leaves the
    header and its line. One that cannot be read puts a scheduled build off (``strict``), and
    leaves a build on request with the header and the line saying so."""
    try:
        fill()
    except NotKept:
        part.rows, part.first, part.last = 0, None, None
        part.note = f"{STORE[part.name]} is {NOT_KEPT}"
    except RecordsUnavailable:
        if strict:
            raise JobFailed(PUT_OFF.format(what=STORE[part.name])) from None
        part.note = (
            f"{STORE[part.name]} could not be read to its end just now: "
            f"{words.group_en_in(str(part.rows))} of its rows are here; build the pack again"
            if part.rows
            else f"{STORE[part.name]} {NOT_READ}"
        )


def _orders(service: Any, span: Span) -> Iterator[dict[str, str]]:
    records = records_for(service)
    _need(records.main, "order_logs")
    yield from records.order_log("live", span.start, span.end)
    try:
        _need(records.main, "analyzer_logs")
    except NotKept:
        return
    yield from records.order_log("sandbox", span.start, span.end)


def _risk(service: Any, span: Span) -> Iterator[dict[str, str]]:
    reader = risk_for(service)
    _need(reader.file, "risk_audit", "daily_locks")
    yield from history(reader, span.start, span.end)


def pack_text(span: Span, parts: list[Part], tax_pack: bool) -> str:
    """The pack's words: the Reports page shows them, and the first page prints them."""
    return "\n".join(
        [
            words.HEADER,
            f"{kinds.REPORT_TITLES[RECORDS]}{words.SEP}{span.label}",
            WHOSE,
            "In this pack:",
            f"{D.pdf_file_name('fy', span.key)}: the year's Tax pack, a working paper."
            if tax_pack
            else NO_TAX_PACK,
            *(part.line() for part in parts),
            NO_ASSISTANT,
            f"{ADD_HEADING}:",
            *CHECKLIST,
            KEEP_LINE,
        ]
    )


def first_page(span: Span, parts: list[Part], tax_pack: bool, now: int) -> bytes:
    """The pack's one page: whose record is whose first, then what is here and what to add."""
    inside = (
        f"{D.pdf_file_name('fy', span.key)}: the year's Tax pack, a working paper."
        if tax_pack
        else NO_TAX_PACK,
        *(part.line() for part in parts),
        NO_ASSISTANT,
    )
    blocks = (
        D.Note(WHOSE),
        D.Heading("In this pack"),
        D.Paragraph(inside),
        D.Heading(ADD_HEADING),
        D.Paragraph((*CHECKLIST, KEEP_LINE)),
    )
    # Dated by the pack, not the clock: the same records make the same bytes.
    return render_pdf(
        D.ReportDoc(
            RECORDS, span.key, kinds.REPORT_TITLES[RECORDS], span.label, built_label(now), blocks
        ),
        created_ms=now,
    )


def _trips(span: Span):
    def read(tx):
        trips = [
            t
            for t in tx.trips_page(
                "live", status="closed", from_day=span.start, to_day=span.end, limit=1_000_000
            )
            if t["net_paise"] is not None
        ]
        tags = {t["id"]: t["name"] for t in tx.rows("tag")}
        books = {b["id"]: b["name"] for b in tx.rows("playbook")}
        have = tx.rows("report", kind="fy", period_key=span.key)
        paper = tx.report_bytes(have[0]["id"]) if have and have[0].get("file_name") else None
        return trips, tx.notes_of(t["id"] for t in trips), tags, books, paper

    return read


def assemble(service: Any, span: Span, now: int, strict: bool) -> tuple[bytes, str, list[Part]]:
    """Read the year and make the zip: its bytes, its words and what each file covers. Blocking;
    the caller runs it off the loop. Everything opened here is closed on every path."""
    from ..ops.analytics_ops import for_core  # the ops package imports the jobs

    trips, notes, tags, books, paper = service.store.read(OWNER, _trips(span))
    rows = [for_core(t, tags, books, notes.get(t["id"])) for t in trips]
    trades = Part("trades.csv")
    for trip in rows:
        trades.saw(to_date(int(trip["close_day"])))
    orders, risk = Part("orders.csv"), Part("risk-history.csv")
    alerts, diary = Part("alerts.csv"), Part("server-diary.jsonl")
    parts = [trades, orders, risk, alerts, diary]
    settings = service.settings
    out = _Zip(now)
    try:
        if paper:
            out.put(D.pdf_file_name("fy", span.key), paper)
        out.put(trades.name, tables.trades_csv(rows).encode("utf-8"))
        _guarded(orders, strict, lambda: out.table(orders, ORDER_COLUMNS, _orders(service, span)))
        _guarded(risk, strict, lambda: out.table(risk, HISTORY_COLUMNS, _risk(service, span)))
        _guarded(
            alerts,
            strict,
            lambda: out.table(
                alerts, ALERT_COLUMNS, alert_rows(getattr(settings, "alerts_dir", None), span)
            ),
        )
        out.table(Part("assistant-calls.csv"), ASSISTANT_COLUMNS, ())
        _guarded(
            diary,
            strict,
            lambda: out.lines(diary, diary_lines(getattr(settings, "watch_dir", None), span)),
        )
        text = pack_text(span, parts, bool(paper))
        out.put(READ_FIRST, first_page(span, parts, bool(paper), now))
        return out.close(), text, parts
    finally:
        out.discard()


async def build_records(service: Any, span: Span, now: int, strict: bool = False) -> int:
    """Build (or rebuild) the records pack of the financial year ``span`` and keep it in the
    Reports archive. Returns the report's id. ``strict`` (the scheduled run): a store that is
    there and cannot be read raises ``JobFailed`` and nothing is kept."""
    data, text, parts = await _thread(lambda: assemble(service, span, now, strict))
    if len(data) > PAGE_DOWNLOAD_MAX:
        text = f"{text}\n{TOO_LARGE}"
    summary = {
        "start": span.start,
        "end": span.end,
        "partial": span.partial,
        "files": [part.out() for part in parts],
        "bytes": len(data),
    }
    report_id, _ = await archive(service, RECORDS, span.key, text, summary, now, announce=False)
    await keep_file(service, report_id, RECORDS, span.key, "zip", data, now)
    return report_id


def records_span(period: str | None, today: int) -> Span:
    """The year a request names (``FY2627``), or the one running now, to today. The periods'
    own rule and words (``BadPeriod``)."""
    from ..periods import resolve_span

    if period is None:
        return fy_span(current_fy_start(to_date(today)), today)
    return resolve_span("fy", period, today)


async def fy_job(service: Any, today: int, now: int, late: bool) -> str:
    day = to_date(today)  # the day the run is for, also when a missed run is built later
    again = (day.month, day.day) == FY_AGAIN_AT[:2]
    put_off: JobFailed | None = None
    try:
        line = await period_job(service, "fy", today, now, keep_paper=again)
    except JobFailed as exc:
        put_off, line = exc, ""
    if not await report_switch(service, SWITCHES["fy"]):
        return line
    # After the Tax pack, so the zip holds the one just kept; and also when that build was put
    # off, so the year's archive never goes without its records.
    report_id = await build_records(service, span_for_job("fy", today), now, strict=True)
    if put_off is not None:
        raise put_off
    return f"{line}; records pack kept as report {report_id}"


def register_jobs() -> None:
    register_job("fy", fy_job)
