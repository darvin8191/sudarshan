"""The report archive's ops (JB7, M15.7, M15.10; design 7.2 and lane JC's contract):
``reports_list``, ``report_get``, ``report_file``, ``report_build`` and ``delivery``.

* ``report_get`` and ``report_file`` take ``{"id"}``; under the delivery purpose (the alerts'
  report desk) the text comes Markdown-safe, as Telegram shows it through OpenAlgo's /notify.
* ``report_file`` answers ``{file_name, file_mime, file_sha256, file_b64}``: all empty until the
  renderer keeps a file (the bridge then says the report has no file to download). The week's
  file is its PNG, the month's and the year's a PDF; with ``format = "csv"`` a monthly statement
  answers its CSV in the same four fields (JB9).
* A report that is not there is a 404 with a sentence (a bare 404 reads as an op not ready).
* ``delivery`` is the desk's outcome, ``{report_id, channel, status, reason}``: the report's
  status, and the reason in the owner's words when it was not sent whole.
"""

from __future__ import annotations

import base64
from typing import Any

from ..core import kinds
from ..core.money import ist_day
from ..errors import ApiError
from ..periods import PERIOD_KINDS, BadPeriod, build_period, csv_file, resolve_span
from ..reports import build_brief, build_eod, markdown_safe
from ..views import done, fields_of
from . import op
from .common import OWNER, caller, choice, need, text, whole, work

REPORT = fields_of("Report")
LIMIT_DEFAULT, LIMIT_MAX = 50, 200
GONE = "This report is not in the journal any more. Open the Reports page again."
BUILT = {
    "eod": "The day report is in the archive.",
    "brief": "The morning brief is in the archive.",
    "weekly": "The week in review is in the archive.",
    "monthly": "The monthly statement is in the archive.",
    "fy": "The tax pack is in the archive.",
}
NO_CLOSE = (
    "There is no close for that day in the journal yet: the day report is built at the close."
)
DELIVERY_STATUSES = ("sent", "text_only", "not_sent")
REASON_TEXT = 300


def report_out(row: dict[str, Any]) -> dict[str, Any]:
    """A kept report as the ``Report`` record: its last delivery's reason beside it."""
    last = (row.get("delivery") or [{}])[-1] if row.get("delivery") else {}
    out = {name: row.get(name) for name in REPORT if name != "reason"}
    out["reason"] = last.get("reason") if row.get("status") != "sent" else None
    if caller().purpose == "delivery":
        out["text"] = markdown_safe(out["text"] or "")
    return out


def _gone() -> ApiError:
    return ApiError(404, "not_found", GONE)


@op("reports_list")
async def reports_list(ctx, args):
    store = need(ctx)
    kind = choice(args, "kind", kinds.REPORT_KINDS, None)
    limit = whole(args, "limit", low=1, high=LIMIT_MAX, default=LIMIT_DEFAULT)

    def read(tx):
        rows = tx.rows("report", order="built_ms,id", **({"kind": kind} if kind else {}))
        return [report_out(r) for r in reversed(rows)][:limit]

    return await work(lambda: store.read(OWNER, read))


@op("report_get")
async def report_get(ctx, args):
    store = need(ctx)
    report_id = whole(args, "id", low=1)
    row = await work(lambda: store.read(OWNER, lambda tx: tx.get("report", report_id)))
    if row is None:
        raise _gone()
    return report_out(row)


@op("report_file")
async def report_file(ctx, args):
    store = need(ctx)
    report_id = whole(args, "id", low=1)
    form = choice(args, "format", ("csv",), None)

    def read(tx):
        row = tx.get("report", report_id)
        return None if row is None else (row, tx.report_bytes(report_id))

    found = await work(lambda: store.read(OWNER, read))
    if found is None:
        raise _gone()
    row, blob = found
    if form == "csv":
        kept = csv_file(row["kind"], row["period_key"], row)
        if kept is None:
            return {"file_name": None, "file_mime": None, "file_sha256": None, "file_b64": None}
        name, mime, sha, data = kept
        return {"file_name": name, "file_mime": mime, "file_sha256": sha,
                "file_b64": base64.b64encode(data).decode("ascii")}  # fmt: skip
    has = blob is not None and row.get("file_name")
    return {
        "file_name": row.get("file_name") if has else None,
        "file_mime": row.get("file_mime") if has else None,
        "file_sha256": row.get("file_sha256") if has else None,
        "file_b64": base64.b64encode(blob).decode("ascii") if has else None,
    }


@op("report_build")
async def report_build(ctx, args):
    store = need(ctx)
    kind = choice(args, "kind", kinds.REPORT_KINDS)
    now = ctx.clock()
    today = ist_day(now)
    period = text(args, "period", limit=8, default=None)
    if kind in PERIOD_KINDS:
        try:
            span = resolve_span(kind, period, today)
        except BadPeriod as exc:
            raise ApiError(400, "bad_request", str(exc), "period") from None
        report_id = await build_period(ctx, kind, span, now)
    else:
        if period is not None and not (period.isdecimal() and len(period) == 8):
            raise ApiError(400, "bad_request", "The period must be a day such as 20261109.",
                           "period")  # fmt: skip
        day = int(period) if period else today
        if day > today or (kind == "brief" and day != today):
            raise ApiError(422, "refused", "That report can be built for today or an earlier day "
                           "only; the morning brief for today only.", "period")  # fmt: skip
        if kind == "eod":
            report_id = await build_eod(ctx, day, now)
            if report_id is None:
                raise ApiError(422, "refused", NO_CLOSE, "period")
        else:
            report_id = await build_brief(ctx, day, now)
    version = await work(lambda: store.read(OWNER, lambda tx: tx.get("report", report_id)))
    return done(report_id, version["version"] if version else None, BUILT[kind])


@op("delivery")
async def delivery(ctx, args):
    store = need(ctx)
    report_id = whole(args, "report_id", low=1)
    channel = choice(args, "channel", ("telegram",))
    status = choice(args, "status", DELIVERY_STATUSES)
    reason = text(args, "reason", limit=REASON_TEXT, default=None) or None
    now = ctx.clock()

    def write(tx):
        row = tx.get("report", report_id)
        if row is None:
            return None
        told = [*(row.get("delivery") or []),
                {"channel": channel, "status": status, "reason": reason, "at": now}][-10:]  # fmt: skip
        tx.touch("report", report_id, {"status": status, "delivery": told, "told_ms": now}, now)
        return row["version"]

    version = await work(lambda: store.write(OWNER, write))
    if version is None:
        raise _gone()
    return done(report_id, version)
