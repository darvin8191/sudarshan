"""The week in review, the monthly statement and the tax pack: built, rendered, archived and
announced (JB9, M15.3 to M15.5; design 3.5 and 3.6, cross-design Y8, Y22, Y32, Y39).

* **Words** are ``core/words.py``'s (``weekly``, ``monthly``, ``fy``), every figure from ``core/``:
  the metrics of the period's closed live trips, the equity curve and its deepest drawdown on the
  trading capital, the week against NIFTY 50 and beta through the portfolio. One line says what
  the figures leave out when the period has any (review B1): positions the journal did not see
  opened with a fill in the period, and contracts that ended at expiry in it.
* **The file** is the renderer's (JB8) and one per report, as the archive keeps one: the week's is
  the equity-curve PNG (Telegram shows it as a photo), the month's and the year's a PDF. The
  month's trades as CSV (stdlib ``csv``, Y32) are kept in the row's own ``rows`` and answered by
  ``report_file`` with ``format = "csv"``.
* **The keys** of the archive: ``2026W45`` (ISO week, Monday to Sunday), ``202610`` and
  ``FY2526`` (the year starting 1 April 2025). A period still running (built on request) is
  worked out to today and says so in its label; the job builds the whole period, and a rebuild
  of a key replaces its text and file. A build is announced when the report was never announced,
  or was announced as a part period and this build is the whole (review M2).
* **The tax pack is a working paper**: it says to verify it with a CA and that the broker's own
  report is the filing source, and it carries no section number of any Act. Its tax figures are
  the portfolio's (``sudarshan/tax``, asked for as one answer, the pack, through the journal's
  link and only while ``SUDARSHAN_TAX`` is on); ``taxpack.py`` lays them out and this module
  works out none of them. The pack printed is kept whole in the archive row (``rows.tax_pack``,
  with its time and its rules edition), so an archived paper never changes when a rule or a late
  trade does; building the pack again makes a new one. Under it, headed "Trading activity
  recorded by the journal", are the journal's own blocks: month by month, by strategy and by
  kind of trade (the kind by ``sudarshan.tax.classify``, the one rule for it). When the paper
  cannot be had the pack says why in a sentence and holds the journal's blocks alone.
* **The family variant** (``family_variant``) is the counts of the period and nothing else: no
  money, price, quantity, ratio, percentage or report text, as the viewer's mask sees it. A
  family member is never sent the file, which carries amounts.
"""

from __future__ import annotations

import calendar
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from . import serialize, taxpack
from .core import benchmark, equity, kinds, metrics, words
from .core.money import ist_clock, ist_day, to_date, to_day
from .portfolio import PortfolioUnavailable, link_for
from .render import doc as D
from .render import tables
from .render.pdf import render_pdf
from .render.png import render_equity_png
from .reports import OWNER, _thread, announce_once, archive, report_switch
from .schedule import schedule_for

PERIOD_KINDS = ("weekly", "monthly", "fy")
SWITCHES = {"weekly": "weekly_on", "monthly": "monthly_on", "fy": "fy_on"}
BETA_LOOKBACK_DAYS = 110  # calendar days that hold 60 trading days with room to spare
KEY_FORMS = {
    "weekly": re.compile(r"(\d{4})W(\d{2})"),
    "monthly": re.compile(r"(\d{4})(\d{2})"),
    "fy": re.compile(r"FY(\d{2})(\d{2})"),
}
PERIOD_HINT = {
    "weekly": "a week such as 2026W45",
    "monthly": "a month such as 202610",
    "fy": "a financial year such as FY2526",
}
CAPITAL_LINE = "Charges are estimates until Upstox's own figures are read."
FY_TRADES_NOTE = (
    "The trades themselves are listed in each Monthly statement, with a CSV of the month."
)
FY_ACTIVITY = "Trading activity recorded by the journal"
#: Under that heading: what the journal's blocks are, so nobody reads them as the tax figures.
FY_ACTIVITY_NOTE = (
    "These blocks are the journal's own record of the trades it saw close, from the day it was "
    "switched on. They are not the tax figures: those are the working paper's."
)
FY_ESTIMATES = (
    "Where Upstox's own charges for a trade are not read yet, its charges here are the "
    "journal's estimate from Upstox's published rates."
)
MIME = {"pdf": "application/pdf", "png": "image/png", "csv": "text/csv"}


class BadPeriod(ValueError):
    """A period the owner asked for that cannot be built; the sentence is for the owner."""


@dataclass(frozen=True)
class Span:
    """One report period: its key, the close days it covers (``end`` never past today)."""

    kind: str
    key: str
    start: int
    end: int
    label: str
    partial: bool


# ---- the periods -----------------------------------------------------------------------------
def _to_date_label(day: int) -> str:
    return f"to {words.day_label(day)}"


def week_span(day: date, today: int) -> Span:
    """The ISO week holding ``day``, Monday to Sunday, worked out to ``today``."""
    monday = day - timedelta(days=day.weekday())
    sunday = monday + timedelta(days=6)
    iso = monday.isocalendar()
    end = min(to_day(sunday), today)
    return Span(
        "weekly", f"{iso.year}W{iso.week:02d}", to_day(monday), end, "", end < to_day(sunday)
    )


def month_span(year: int, month: int, today: int) -> Span:
    last = calendar.monthrange(year, month)[1]
    end = min(to_day(date(year, month, last)), today)
    label = f"{calendar.month_name[month]} {year}"
    partial = end < to_day(date(year, month, last))
    return Span("monthly", f"{year}{month:02d}", to_day(date(year, month, 1)), end,
                f"{label}, {_to_date_label(end)}" if partial else label, partial)  # fmt: skip


def fy_span(start_year: int, today: int) -> Span:
    last = to_day(date(start_year + 1, 3, 31))
    end = min(last, today)
    key = f"FY{start_year % 100:02d}{(start_year + 1) % 100:02d}"
    label = f"FY {start_year}-{(start_year + 1) % 100:02d}"
    partial = end < last
    return Span("fy", key, to_day(date(start_year, 4, 1)), end,
                f"{label}, {_to_date_label(end)}" if partial else label, partial)  # fmt: skip


def current_fy_start(day: date) -> int:
    return day.year if day.month >= 4 else day.year - 1


def span_for_job(kind: str, today: int) -> Span:
    """What a job builds on ``today``: this week (Sunday), last month (the 1st), the year just
    ended (1 April)."""
    d = to_date(today)
    if kind == "weekly":
        return week_span(d, today)
    if kind == "monthly":
        last = d.replace(day=1) - timedelta(days=1)
        return month_span(last.year, last.month, today)
    return fy_span(current_fy_start(d) - 1, today)


def resolve_span(kind: str, period: str | None, today: int) -> Span:
    """The period a request names: nothing means the one running now, to today. Raises
    ``BadPeriod`` for a malformed key or one that has not started."""
    d = to_date(today)
    if period is None:
        if kind == "weekly":
            return week_span(d, today)
        if kind == "monthly":
            return month_span(d.year, d.month, today)
        return fy_span(current_fy_start(d), today)
    found = KEY_FORMS[kind].fullmatch(period)
    bad = BadPeriod(f"The period must be {PERIOD_HINT[kind]}.")
    if not found:
        raise bad
    a, b = int(found.group(1)), int(found.group(2))
    try:
        if kind == "weekly":
            span = week_span(date.fromisocalendar(a, b, 1), today)
        elif kind == "monthly":
            if not 1 <= b <= 12 or not 2000 <= a <= 2100:
                raise bad
            span = month_span(a, b, today)
        else:
            if (a + 1) % 100 != b:
                raise bad
            span = fy_span(2000 + a, today)
    except ValueError:
        raise bad from None
    if span.start > today:
        raise BadPeriod("That period has not started yet.")
    label = span.label
    if kind == "weekly":
        label = f"{words.day_label(span.start)} - {words.day_label_year(span.end)}"
    return Span(span.kind, span.key, span.start, span.end, label, span.partial)


# ---- reading what the period holds -----------------------------------------------------------
def _read(span: Span):
    def read(tx):
        trips = [t for t in tx.trips_page("live", status="closed", from_day=span.start,
                                          to_day=span.end, limit=1_000_000)
                 if t["net_paise"] is not None]  # fmt: skip
        beta_from = to_day(to_date(span.end) - timedelta(days=BETA_LOOKBACK_DAYS))
        beta_trips = [t for t in tx.trips_page("live", status="closed", from_day=beta_from,
                                               to_day=span.end, limit=1_000_000)
                      if t["net_paise"] is not None] if span.kind == "weekly" else []  # fmt: skip
        notes = tx.notes_of(t["id"] for t in trips)
        tags = {t["id"]: t["name"] for t in tx.rows("tag")}
        books = {b["id"]: b["name"] for b in tx.rows("playbook")}
        first = tx.min_of("round_trip", "close_day", mode="live", status="closed")
        aside = tx.left_out("live", span.start, span.end)
        return trips, beta_trips, notes, tags, books, first, tx.settings(), aside

    return read


async def read_pack(service: Any, fy_start: int) -> tuple[dict[str, Any] | None, str | None]:
    """The tax working paper of the year starting in ``fy_start``, from the portfolio: the pack,
    or None and why (a key of ``taxpack.NO_PACK`` or of the link's causes). Asked only while
    ``SUDARSHAN_TAX`` is on."""
    if not getattr(service.settings, "tax", False):
        return None, "switched_off"
    try:
        answer = await link_for(service).tax_pack(fy_start)
    except PortfolioUnavailable as exc:
        return None, exc.kind
    return (dict(answer), None) if taxpack.usable(answer) else (None, "unreadable")


def leaves_out(rows: list[dict[str, Any]], aside: Mapping[str, int]) -> tuple[str, ...]:
    """What the journal's own blocks of the tax pack are and leave out (review H1)."""
    lines = [FY_ACTIVITY_NOTE]
    if any(t.get("charges_actual_paise") is None and not tables.unpriced(t) for t in rows):
        lines.append(FY_ESTIMATES)
    unpriced = sum(1 for t in rows if tables.unpriced(t))
    if unpriced:
        lines.append(
            f"Charges for BSE, currency and commodity trades are not estimated: "
            f"{words.plural(unpriced, 'trade')} here {'shows' if unpriced == 1 else 'show'} "
            "none. Upstox's own report has them."
        )
    line = words.left_out_line(int(aside.get("unmatched") or 0), int(aside.get("expired") or 0),
                               unclosed=int(aside.get("unclosed") or 0))  # fmt: skip
    if line:
        lines.append(line)
    return tuple(lines)


async def _index_week(service: Any, plan: Any, span: Span, net: int,
                      capital: int | None) -> dict[str, Any]:  # fmt: skip
    """The account's week and NIFTY 50's: the last close of the week over the close before it."""
    before = plan.previous_trading_days(to_date(span.start), 1)
    last = close = prev = None
    if before:
        try:
            answer = await link_for(service).benchmark_closes(
                "nifty50", to_day(before[0]), span.end
            )
        except PortfolioUnavailable:
            answer = {}
        if answer.get("state") == "ok":
            got = {int(c["day"]): int(c["close_u"]) for c in answer.get("closes") or []
                   if isinstance(c, Mapping) and isinstance(c.get("close_u"), int)}  # fmt: skip
            inside = [d for d in got if span.start <= d <= span.end]
            if inside:
                last = max(inside)
                close, prev = got[last], got.get(to_day(before[0]))
    return benchmark.week_vs_index(net, capital, close, prev)


def _words_inputs(span: Span, rows: list[dict[str, Any]], days: list[int]) -> dict[str, Any]:
    cal = metrics.calendar(rows)["days"]
    out: dict[str, Any] = {"by_strategy": sorted(
        ({"name": b["name"], "net_paise": b["metrics"]["net_paise"], "trades": b["metrics"]["trips"]}
         for b in metrics.blocks(rows, "strategy")), key=lambda r: (-r["net_paise"], r["name"]))}  # fmt: skip
    if cal:
        best = max(cal, key=lambda r: (r["net_paise"], -int(r["key"])))
        worst = min(cal, key=lambda r: (r["net_paise"], int(r["key"])))
        out["best_day"] = (int(best["key"]), best["net_paise"])
        out["worst_day"] = (int(worst["key"]), worst["net_paise"])
    return out


def built_label(now: int) -> str:
    h, m, _ = ist_clock(now)
    return f"Built {words.day_label_year(ist_day(now))}, {h:02d}:{m:02d}"


def _paragraph(text: str, kind: str, also: tuple[str, ...] = ()) -> D.Paragraph:
    """The words as a PDF paragraph: without the brand line and the title line (the page has
    both), the pointer to this very report and the working-paper line (its own note); the tax
    pack's line of what its figures leave out is in its own block."""
    drop = {words.OPEN_FULL_REPORT, words.FY_NOTE, *also}
    title = f"{kinds.REPORT_TITLES[kind]}{words.SEP}"
    aside = "Not in these figures:" if kind == "fy" else None
    para = D.Paragraph.from_words(text)
    return D.Paragraph(
        tuple(
            ln for ln in para.lines
            if ln not in drop and not ln.startswith(title)
            and not (aside and ln.startswith(aside))
        )
    )  # fmt: skip


def monthly_pdf(span: Span, text: str, rows: list[dict[str, Any]], now: int) -> bytes:
    m = metrics.compute(rows)
    cal = metrics.calendar(rows)
    blocks = (
        tables.summary_figures(m),
        D.Heading("Summary"),
        _paragraph(text, "monthly"),
        D.Heading("By strategy"),
        tables.strategy_table(metrics.blocks(rows, "strategy")),
        D.Heading("Day by day"),
        tables.days_table(cal["days"]),
        D.Heading("Trades"),
        tables.trades_table(sorted(rows, key=lambda t: (t["close_day"], t.get("closed_ms") or 0))),
    )
    return render_pdf(D.ReportDoc("monthly", span.key, kinds.REPORT_TITLES["monthly"], span.label,
                                  built_label(now), blocks, CAPITAL_LINE))  # fmt: skip


def fy_pdf(span: Span, text: str, rows: list[dict[str, Any]], now: int,
           pack: Mapping[str, Any] | None = None, no_pack: str | None = None,
           left_out: tuple[str, ...] = ()) -> bytes:  # fmt: skip
    """The tax pack: the working paper as the portfolio computed it (``pack``), or the sentence
    saying why it is not here (``no_pack``), then the journal's own blocks."""
    m = metrics.compute(rows)
    cal = metrics.calendar(rows)
    paper: tuple[D.Block, ...]
    if pack is not None:
        paper, said = taxpack.pack_blocks(pack), tuple(taxpack.pack_lines(pack))
    else:
        said = (no_pack or taxpack.no_pack_words(None),)
        paper = (D.Heading("Tax working paper"), D.Paragraph(said))
    blocks = (
        D.Note(words.FY_NOTE),
        *paper,
        D.Heading(FY_ACTIVITY),
        D.Paragraph(left_out or (FY_ACTIVITY_NOTE,)),
        tables.summary_figures(m),
        D.Heading("Summary"),
        _paragraph(text, "fy", said),
        D.Heading("Month by month"),
        tables.months_table(cal["months"]),
        D.Heading("By strategy"),
        tables.strategy_table(metrics.blocks(rows, "strategy")),
        D.Heading("By kind of trade"),
        tables.kinds_table(rows),
        D.Paragraph((FY_TRADES_NOTE,)),
    )
    return render_pdf(D.ReportDoc("fy", span.key, kinds.REPORT_TITLES["fy"], span.label,
                                  built_label(now), blocks, words.FY_NOTE))  # fmt: skip


# ---- the build -------------------------------------------------------------------------------
async def build_period(service: Any, kind: str, span: Span, now: int, announce: bool = True) -> int:
    """Build (or rebuild) the report of ``kind`` for ``span``: words, file, archive row, and one
    line for the report desk when this build is due one (``reports.announce_once``: never
    announced, or announced as a part period and this is the whole). Returns the report's id."""
    # The analytics op's own helpers; imported here because the ops package imports this module.
    from .ops.analytics_ops import beta_of, for_core, trading_days

    trips, beta_trips, notes, tags, books, first, settings, aside = await _thread(
        lambda: service.store.read(OWNER, _read(span))
    )
    pack = no_pack = None
    rows = [for_core(t, tags, books, notes.get(t["id"])) for t in trips]
    plan = schedule_for(service)
    capital = settings.get("capital_base_paise")
    days = trading_days(plan, span.start, span.end)
    block = metrics.compute(rows)
    curve = equity.equity_curve(rows, days, capital)
    extra = _words_inputs(span, rows, days)
    extra["drawdown"] = curve["drawdown"]
    extra["left_out"] = aside
    if kind == "weekly":
        extra["benchmark"] = await _index_week(service, plan, span, block["net_paise"], capital)
        beta_rows = [for_core(t, tags, books, None) for t in beta_trips]
        extra["beta"] = await beta_of(service, plan, beta_rows, span.end, capital, first)
        text = words.weekly(span.start, span.end, len(days), block, **extra)
        label = span.label or f"{words.day_label(span.start)} - {words.day_label_year(span.end)}"
        file = (
            "png",
            await _thread(lambda: render_equity_png(curve, kinds.REPORT_TITLES[kind], label)),
        )
    elif kind == "monthly":
        text = words.monthly(span.label, len(days), block, **extra)
        file = ("pdf", await _thread(lambda: monthly_pdf(span, text, rows, now)))
    else:
        pack, why = await read_pack(service, 2000 + int(span.key[2:4]))
        no_pack = None if pack is not None else taxpack.no_pack_words(why)
        paper = taxpack.pack_lines(pack) if pack is not None else [no_pack]
        text = words.fy(span.label, len(days), block, paper=paper, **extra)
        aside_lines = leaves_out(rows, aside)
        file = ("pdf", await _thread(lambda: fy_pdf(span, text, rows, now, pack, no_pack,
                                                    aside_lines)))  # fmt: skip
    summary = {"start": span.start, "end": span.end, "trading_days": len(days),
               "trades": block["trips"], "wins": block["wins"], "losses": block["losses"],
               "flats": block["flats"], "net_paise": block["net_paise"],
               "charges_paise": block["charges_paise"], "partial": span.partial,
               "left_out": aside}  # fmt: skip
    if kind == "monthly":
        summary["csv"] = tables.trades_csv(rows)
    if kind == "fy":
        # The paper as printed, frozen: its time and rules edition are inside it.
        summary["tax_pack"], summary["tax_note"] = pack, no_pack
    report_id, _ = await archive(service, kind, span.key, text, summary, now, announce=False)
    sha = await keep_file(service, report_id, kind, span.key, file[0], file[1], now)
    if announce:  # after the file is kept, so a fault before here announces on the retry (M2)
        await announce_once(service, report_id, kind, sha, span.partial, now)
    return report_id


async def keep_file(service: Any, report_id: int, kind: str, key: str, ext: str, data: bytes,
                    now: int) -> str:  # fmt: skip
    """Store a report's file as the archive keeps it: name, type, sha256 and bytes, one file."""
    sha = hashlib.sha256(data).hexdigest()
    values = {"file_name": D.pdf_file_name(kind, key, ext), "file_mime": MIME[ext],
              "file_sha256": sha, "file_bytes": data}  # fmt: skip
    await _thread(
        lambda: service.store.write(OWNER, lambda tx: tx.touch("report", report_id, values, now))
    )
    return sha


def csv_file(kind: str, key: str, row: Mapping[str, Any]) -> tuple[str, str, str, bytes] | None:
    """The month's CSV as ``report_file`` answers it: name, type, sha256 and bytes."""
    text = (row.get("rows") or {}).get("csv")
    if not isinstance(text, str):
        return None
    data = text.encode("utf-8")
    return D.pdf_file_name(kind, key, "csv"), MIME["csv"], hashlib.sha256(data).hexdigest(), data


# ---- the family's variant --------------------------------------------------------------------
COUNT_KEYS = ("trading_days", "trades", "wins", "losses", "flats")
PERIOD_WORD = {"weekly": "this week", "monthly": "this month", "fy": "this year"}


def family_variant(kind: str, period_key: str, rows: Mapping[str, Any]) -> dict[str, Any]:
    """A period report as the family sees it: its counts only. Built from a whitelist of counts
    and then passed through the viewer's mask, so a money, price or quantity field added to
    ``rows`` later still cannot leave. The line says how many trades and nothing else: won and
    lost need the P&L, which the mask takes out."""
    counts = {k: rows[k] for k in COUNT_KEYS if k in rows}
    masked = serialize.apply(counts, serialize.viewer())
    line = None
    if "trades" in masked:
        line = f"Trades {PERIOD_WORD[kind]}: {int(masked['trades'])}"
    return {"kind": kind, "period_key": period_key, "line": line, "counts": masked}


# ---- the jobs --------------------------------------------------------------------------------
async def period_job(service: Any, kind: str, today: int, now: int) -> str:
    if not await report_switch(service, SWITCHES[kind]):
        return f"the {kinds.REPORT_TITLES[kind].lower()} is switched off"
    span = span_for_job(kind, today)
    report_id = await build_period(service, kind, span, now)
    return f"{span.key} kept as report {report_id}"
