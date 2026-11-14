"""The day report and the morning brief: built, archived, and announced to the report desk (JB7,
M15.1, M15.2, M15.10; design 3.5 and 3.6 as amended by cross-design Y8 and Y22).

* **Words** come from ``core/words.py`` (pure, its vectors W): the day report from the day's close
  (``jobs/day.py``), the trips closed that day by strategy and the close check; the brief from the
  day's sessions, Upstox's funds and positions through the portfolio's journal purpose, the
  previous trading day's close and the kill switch.
* **The archive** keeps one row per kind and period (``report``: ``YYYYMMDD`` for these two, the
  form the Reports page reads). A rebuild of a period replaces its text and its build time.
* **Announced once a build** (review M2). A build is announced unless the report was announced
  already, or it was and that was a part period (built on request before its time) and this
  build is the whole: so the Build button never stops the scheduled report from going, and the
  same build never goes twice. The row is claimed in the transaction that decides, and released
  if the line could not be written, so a fault after the build announces on the retry.
* **The announcement** is one line in the journal's spool for the alerts' report desk
  (``ops/events.py``): its id, src ``journal``, kind ``report``, the report's id, its kind and the
  sha256 of its file, never its text (Y8). The desk asks for the text and the file with the
  delivery purpose and tells the outcome back (``delivery``).
* **Telegram Markdown**: the text the desk sends goes through OpenAlgo's /notify in Markdown, so
  under the delivery purpose ``_``, ``*``, a backquote and ``[`` are escaped (a strategy name
  ``my_orb`` would otherwise turn into formatting); the archive and the page keep the plain text.

The report files (PDF, PNG) are the renderer's (JB8): until it is in, a report is text and its
file fields are empty.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from sudarshan.ops.events import JOURNAL_SPOOL, record

from .core import words
from .core.metrics import strategy_of
from .core.money import ist_clock, to_date, to_day
from .openalgo_ro import RecordsUnavailable, records_for
from .portfolio import PortfolioUnavailable, link_for
from .risk_ro import risk_for
from .schedule import register_job, schedule_for
from .store import Scope

log = logging.getLogger("sudarshan.journal")

OWNER = Scope.owner()
QTY_SCALE = 1_000_000
MARKDOWN = str.maketrans({"_": "\\_", "*": "\\*", "`": "\\`", "[": "\\["})
NOT_READ_LINE = (
    "Upstox's positions could not be read at the close: realised is from the journal's own "
    "trades, before charges, and unrealised is not known."
)


def markdown_safe(text: str) -> str:
    """The text as Telegram's Markdown shows it word for word."""
    return text.translate(MARKDOWN)


def clock_text(ms: int | None) -> str | None:
    if ms is None:
        return None
    h, m, _ = ist_clock(ms)
    return f"{h:02d}:{m:02d}"


def spool_of(service: Any) -> Any:
    """The spool the report desk reads: the journal's state folder (JOURNAL_SPOOL's own
    folder on the server)."""
    return service.settings.state_dir / JOURNAL_SPOOL.name


async def _thread(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


# ---- the archive and the announcement ------------------------------------------------------
async def archive(service: Any, kind: str, period_key: str, text: str, rows: Mapping[str, Any],
                  now: int, announce: bool = True, create: bool = True,
                  partial: bool = False) -> tuple[int | None, bool]:  # fmt: skip
    """Keep the report of ``kind`` for ``period_key`` (a rebuild replaces the text and the build
    time) and, with ``announce``, announce this build to the desk when it is due
    (``announce_once``; ``partial`` says it is a part period). ``create`` False only rewrites a
    row already kept. Returns the report's id (None when nothing was kept) and whether it was
    new."""
    values = {"kind": kind, "period_key": period_key, "built_ms": now, "text": text,
              "rows": dict(rows)}  # fmt: skip

    def write(tx):
        have = tx.rows("report", kind=kind, period_key=period_key)
        if have:
            tx.touch("report", have[0]["id"], {"text": text, "rows": dict(rows), "built_ms": now},
                     now)  # fmt: skip
            return have[0]["id"], False
        if not create:
            return None, False
        made = tx.insert("report", values, now)
        return made["id"], True

    report_id, new = await _thread(lambda: service.store.write(OWNER, write))
    if report_id is not None and announce:
        await announce_once(service, report_id, kind, None, partial, now)
    return report_id, new


async def announce_once(service: Any, report_id: int, kind: str, sha256: str | None,
                        partial: bool, now: int) -> bool:  # fmt: skip
    """Announce this build of a report to the desk when it is due (review M2): never announced
    yet, or last announced as a part period and this build is the whole one. The row is marked
    in the transaction that decides (two builds at once announce once) and the mark is put back
    if the spool line could not be written (the next build or retry announces). True when a line
    was written."""

    def claim(tx):
        row = tx.get("report", report_id)
        if row is None:
            return None
        before = (row.get("announced_ms"), bool(row.get("announced_partial")))
        if before[0] is not None and not (before[1] and not partial):
            return None
        tx.touch("report", report_id, {"announced_ms": now, "announced_partial": partial}, now)
        return row["built_ms"], before

    claimed = await _thread(lambda: service.store.write(OWNER, claim))
    if claimed is None:
        return False
    built_ms, (was_ms, was_partial) = claimed
    done = await announce_report(service, report_id, kind, built_ms, sha256)
    if not done:
        await _thread(lambda: service.store.write(OWNER, lambda tx: tx.touch(
            "report", report_id, {"announced_ms": was_ms, "announced_partial": was_partial},
            now)))  # fmt: skip
    return done


async def announce_report(service: Any, report_id: int, kind: str, built_ms: int,
                          sha256: str | None) -> bool:  # fmt: skip
    """One line in the spool for the report desk: ids only, never the text (Y8)."""
    event_id = f"report-{report_id}-{built_ms}"
    done = await _thread(
        lambda: record(
            spool_of(service),
            kind="report",
            event_id=event_id,
            at=built_ms / 1000,
            run=event_id,
            src="journal",
            report=str(report_id),
            report_kind=kind,
            sha256=sha256 or "",
        )  # fmt: skip
    )
    if not done:
        log.warning("a %s report was kept but its line for the report desk could not be written",
                    kind)  # fmt: skip
    return done


# ---- the day report --------------------------------------------------------------------------
def _by_strategy(trips: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for trip in trips:
        name = strategy_of(trip)
        row = found.setdefault(name, {"name": name, "net_paise": 0, "trades": 0})
        row["net_paise"] += int(trip["net_paise"] or 0)
        row["trades"] += 1
    return sorted(found.values(), key=lambda r: (-r["net_paise"], r["name"]))


def _close_check(close: Mapping[str, Any], more: int) -> tuple[dict | None, str | None]:
    """The words' close check, and a line of its own when the check did more than add fills."""
    if close.get("reconciled_at") is None:
        return None, None
    note = close.get("reconcile_note") or ""
    if note.startswith("The close check agreed"):
        return {"state": "agreed"}, None
    if more and note.endswith(f"found {words.plural(more, 'more fill', 'more fills')}; the "
                              "journal is corrected."):  # fmt: skip
        return {"state": "found", "more": more}, None
    return None, note or None


def eod_text(day: int, close: Mapping[str, Any], trips: list[Mapping[str, Any]], more: int,
             check_at: str | None, left_out: Mapping[str, int] | None = None) -> str:  # fmt: skip
    """The day report's words from the day's close and the trips closed that day; ``left_out``
    what the trade count leaves out (positions not seen opened, contracts ended at expiry)."""
    check, extra = _close_check(close, more)
    kill = close.get("kill_switch")
    text = words.eod(
        day,
        int(close.get("realised_paise") or 0),
        int(close.get("unrealised_paise") or 0),
        int(close.get("charges_est_paise") or 0),
        int(close.get("trades_closed") or 0),
        int(close.get("wins") or 0),
        int(close.get("losses") or 0),
        int(close.get("flats") or 0),
        by_strategy=_by_strategy(trips),
        carried=[
            {"label": f"{p['symbol']} {p['product']}".strip(), "qty": p["qty"]}
            for p in close.get("positions_overnight") or []
        ],  # fmt: skip
        kill_switch=None if kill is None else kill == "on",
        daily_lock_at=close.get("daily_lock"),
        fills_recorded=int(close.get("fills_live") or 0),
        check_at=None if check or extra else check_at,
        close_check=check,
        left_out=left_out,
    )
    lines = [text]
    if close.get("read_at") is None:
        lines.append(NOT_READ_LINE)
    if extra:
        lines.append(extra)
    return "\n".join(lines)


async def build_eod(service: Any, day: int, now: int, announce: bool = True,
                    create: bool = True, partial: bool = False) -> int | None:  # fmt: skip
    """Build (or rebuild) the day report of ``day`` from its close; None when there is none.
    ``create`` False (the close check) only rewrites a day report already kept, so one that is
    yet to be built is still announced when it is. ``partial``: built on request before the
    day's close job, so the job's build is still announced."""

    def read(tx):
        close = tx.day_close_get("live", day)
        if close is None:
            return None
        trips = tx.trips_page("live", status="closed", from_day=day, to_day=day, limit=100_000)
        more = sum(1 for f in tx.fills_of("live", day=day) if f["source"] == "close_reconcile")
        return close, trips, more, tx.left_out("live", day, day)

    found = await _thread(lambda: service.store.read(OWNER, read))
    if found is None:
        return None
    close, trips, more, aside = found
    check_at = clock_text(schedule_for(service).check_at(to_date(day)))
    text = eod_text(day, close, trips, more, check_at, aside)
    rows = {"trades": len(trips), "fills_live": close.get("fills_live"), "more": more}
    report_id, _ = await archive(service, "eod", str(day), text, rows, now, announce, create,
                                 partial)  # fmt: skip
    if report_id is not None and close.get("reconciled_at") is not None:
        await _thread(lambda: service.store.write(OWNER, lambda tx: tx.touch(
            "report", report_id, {"reconciled_at": close["reconciled_at"]}, now)))  # fmt: skip
    return report_id


async def report_switch(service: Any, name: str) -> bool:
    settings = await _thread(lambda: service.store.read(OWNER, lambda tx: tx.settings()))
    return bool(settings.get(name, True))


# ---- the morning brief -----------------------------------------------------------------------
def _span(plan: Any, day: date, segments: tuple[str, ...]) -> tuple[int, int] | None:
    from sudarshan.alerts.market import sessions

    found = sessions.sessions_of(day, trading_day=plan.trading(day),
                                 window=plan.calendar.day_window(day))  # fmt: skip
    spans = [s for key, s in found.items() if key in segments]
    return (min(s.start for s in spans), max(s.end for s in spans)) if spans else None


def _holiday_ahead(plan: Any, day: date) -> int | None:
    after = day + timedelta(days=1)
    return to_day(after) if after.weekday() < 5 and not plan.trading(after) else None


async def brief_inputs(service: Any, day: int, now: int) -> dict[str, Any]:
    """What the brief says, gathered: sessions, expiry, funds, overnight positions, yesterday,
    the kill switch. A part that cannot be read is said as not read, never guessed."""
    plan = schedule_for(service)
    d = to_date(day)
    cash, fno = _span(plan, d, ("NSE_EQ", "NSE_INDEX")), _span(plan, d, ("NSE_FO",))
    special = plan.calendar.day_window(d) is not None and not (
        d.weekday() < 5 and d not in plan.calendar.holidays()
    )
    out: dict[str, Any] = {"day": day}
    if special:
        start = min(x[0] for x in (cash, fno) if x)
        end = max(x[1] for x in (cash, fno) if x)
        out.update(kind="muhurat", window=f"{clock_text(start)}-{clock_text(end)}")
    else:
        out.update(kind="normal", hours=f"{clock_text(cash[0])}-{clock_text(cash[1])}"
                   if cash else None, fno_close=clock_text(fno[1]) if fno else None)  # fmt: skip
    try:
        names = await _thread(lambda: records_for(service).index_expiry_names(day))
    except RecordsUnavailable:
        names = []
    if names:
        out["expiry_label"] = " and ".join(names)
    out["holiday_ahead_day"] = _holiday_ahead(plan, d)
    link = link_for(service)
    try:
        funds = await link.funds()
        if funds.get("state") == "ok" and isinstance(funds.get("data"), Mapping):
            out["funds_available_paise"] = funds["data"].get("available_paise")
    except PortfolioUnavailable:
        pass
    overnight = None
    try:
        answer = await link.positions()
        if answer.get("state") == "ok" and isinstance(answer.get("data"), list):
            overnight = answer["data"]
    except PortfolioUnavailable:
        pass
    previous = plan.base.previous_trading_day(d)

    def read(tx):
        close = tx.day_close_get("live", to_day(previous)) if previous else None
        return close

    close = await _thread(lambda: service.store.read(OWNER, read))
    if overnight is not None:
        keys = [r.get("instrument_key") for r in overnight if isinstance(r, Mapping)]
        try:
            listings = await _thread(lambda: records_for(service).instruments(keys))
        except RecordsUnavailable:
            listings = {}
        out["overnight"] = [
            {"label": (listings[r["instrument_key"]].symbol if r.get("instrument_key") in
                       listings else str(r.get("name") or r.get("instrument_key") or "")),
             "qty": int(r["overnight_qty_u"]) // QTY_SCALE}
            for r in overnight
            if isinstance(r, Mapping) and isinstance(r.get("overnight_qty_u"), int)
            and r["overnight_qty_u"]
        ]  # fmt: skip
    elif close is not None:
        out["overnight"] = [{"label": p["symbol"], "qty": p["qty"]}
                            for p in close.get("positions_overnight") or []]  # fmt: skip
    if close is not None:
        out["yesterday"] = {
            "trades": int(close.get("trades_closed") or 0),
            "net_paise": int(close.get("realised_paise") or 0)
            + int(close.get("unrealised_paise") or 0),
        }
    risk = await _thread(lambda: risk_for(service).state(day))
    out["kill_switch"] = risk.kill_switch if risk.read else None
    return out


async def build_brief(service: Any, day: int, now: int, announce: bool = True,
                      partial: bool = False) -> int:  # fmt: skip
    """Build (or rebuild) the morning brief of ``day``; ``partial``: built on request before its
    time, so the scheduled brief is still announced."""
    inputs = await brief_inputs(service, day, now)
    text = words.brief(**inputs)
    report_id, _ = await archive(service, "brief", str(day), text, {"inputs": inputs}, now,
                                 announce, partial=partial)  # fmt: skip
    return report_id


# ---- the jobs --------------------------------------------------------------------------------
async def brief_job(service: Any, today: int, now: int, late: bool) -> str:
    if not await report_switch(service, "brief_on"):
        return "the morning brief is switched off"
    report_id = await build_brief(service, today, now)
    return f"brief {report_id} kept"


def register_jobs() -> None:
    register_job("brief", brief_job)
