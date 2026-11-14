"""The journal's account reads (PJ; cross-design Y4): the only ops of the journal purpose, all
reads, all answered with ``read_at``.

One reader serves every ring service, so there is one budget and one set of pauses on Upstox's
APIs. ``trades_day``, ``positions``, ``holdings`` and ``funds`` go through the live reader's parts, so a journal
read and a family or owner read within 5 seconds cost one Upstox call; the order history, the
historical trades and the profit and loss report each have a bucket of their own. A principal
other than the owner leaves the owner's reserve in every bucket (budget.RESERVE).

Nothing here is cached beyond the reader's 5 seconds: the journal keeps what it needs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .. import reports
from ..benchmark import BENCHMARK_OF
from ..core.days import is_day
from ..core.kinds import BENCHMARKS, REPORT_SEGMENTS
from ..errors import ApiError
from ..store import Scope
from ..upstox import MAX_AGE_S, parse_order
from . import op
from .common import caller, need, work

ASK = "The request was incomplete."
ORDER_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
MAX_PAGE = 1000
PNL_PAGE_SIZE = 1000
PNL_PAGE_SIZE_MAX = 5000


def _bad(field: str | None = None) -> ApiError:
    return ApiError(400, "bad_request", ASK, field)


def _day(message: Mapping[str, Any], name: str) -> int:
    value = message.get(name)
    if not is_day(value):
        raise _bad(name)
    return value


def _page(message: Mapping[str, Any]) -> int:
    page = message.get("page", 1)
    if page.__class__ is not int or not 1 <= page <= MAX_PAGE:
        raise _bad("page")
    return page


def _segment(message: Mapping[str, Any], allowed: tuple[str, ...] = REPORT_SEGMENTS) -> str:
    segment = str(message.get("segment") or "").upper()
    if segment not in allowed:
        raise _bad("segment")
    return segment


def _failed(now: int, outcome: str, **more: Any) -> dict[str, Any]:
    """An answer for a read that did not happen: why, in words, and no figures."""
    return {"state": "unavailable", "read_at": None, "note": reports.WORDS[outcome], **more}


async def _part(ctx, name: str) -> dict[str, Any]:
    """One part of the live reader (single flight, 5 s memory): state, read_at, note, data."""
    return (await ctx.ensure_upstox().book([name], MAX_AGE_S, caller().principal))[name]


@op("trades_day")
async def trades_day(ctx, message):
    return await _part(ctx, "trades")


@op("positions")
async def positions(ctx, message):
    return await _part(ctx, "positions")


@op("holdings")
async def holdings(ctx, message):
    """The holdings, read by the journal once per share at first sight: what was held before
    its first fill of it (journal review B1)."""
    return await _part(ctx, "holdings")


@op("funds")
async def funds(ctx, message):
    return await _part(ctx, "funds")


@op("order_history")
async def order_history(ctx, message):
    order_id = message.get("order_id")
    if not isinstance(order_id, str) or not ORDER_ID.fullmatch(order_id):
        raise _bad("order_id")
    reader = ctx.ensure_reports()
    outcome, payload = await reader._call(
        "order_history", {"order_id": order_id}, caller().principal
    )
    if outcome != "ok":
        return _failed(ctx.clock(), outcome, order_id=order_id, rows=[])
    rows = [parse_order(i) for i in payload.get("data") or [] if isinstance(i, Mapping)]
    return {
        "state": "ok",
        "read_at": ctx.clock(),
        "note": None,
        "order_id": order_id,
        "rows": rows,
    }


@op("historical_trades")
async def historical_trades(ctx, message):
    segment, start, end, page = (
        _segment(message),
        _day(message, "from"),
        _day(message, "to"),
        _page(message),
    )
    if start > end:
        raise _bad("from")
    reader = ctx.ensure_reports()
    outcome, payload = await reader._call(
        "trades",
        {
            "segment": segment,
            "start_date": reports._iso(start),
            "end_date": reports._iso(end),
            "page_number": str(page),
            "page_size": str(reports.TRADES_PAGE_SIZE),
        },
        caller().principal,
    )
    base = {"segment": segment, "from": start, "to": end, "page": page}
    if outcome != "ok":
        return _failed(ctx.clock(), outcome, **base, rows=[], total_pages=None, total_records=None)
    try:
        rows = [reports.parse_trade(i) for i in payload.get("data") or [] if isinstance(i, Mapping)]
    except reports.Unreadable:
        return _failed(
            ctx.clock(), "unreadable", **base, rows=[], total_pages=None, total_records=None
        )
    meta = reports._page_meta(payload)
    return {
        "state": "ok",
        "read_at": ctx.clock(),
        "note": None,
        **base,
        "rows": rows,
        "total_pages": reports._whole(meta.get("total_pages")),
        "total_records": reports._whole(meta.get("total_records")),
    }


@op("pnl_report")
async def pnl_report(ctx, message):
    segment = _segment(message, reports.PNL_SEGMENTS)
    fy = message.get("fy")
    if fy.__class__ is not int or not reports.year_ok(fy, _today(ctx)):
        raise _bad("fy")
    part = message.get("part")
    if part not in ("metadata", "data", "charges"):
        raise _bad("part")
    page = _page(message) if part == "data" else 1
    size = message.get("page_size", PNL_PAGE_SIZE)
    if size.__class__ is not int or not 1 <= size <= PNL_PAGE_SIZE_MAX:
        raise _bad("page_size")
    span = _span(message, fy) if part == "charges" else {}
    base = {"segment": segment, "financial_year": reports.fy_code(fy)}
    api = {"metadata": "pnl_meta", "data": "pnl", "charges": "charges"}[part]
    params = {**base, "page_number": str(page), "page_size": str(size)} if part == "data" else base
    if span:  # the charges of some days of the year (M7.7, design p7 4.6): Upstox's dd-mm-yyyy
        params = {**params, "from_date": _dmy(span["from"]), "to_date": _dmy(span["to"])}
    outcome, payload = await ctx.ensure_reports()._call(api, params, caller().principal)
    head = {"segment": segment, "fy": fy, "part": part, "page": page, **span}
    if outcome != "ok":
        return _failed(ctx.clock(), outcome, **head)
    data = payload.get("data") or {}
    try:
        if part == "metadata":
            found: dict[str, Any] = {"meta": reports.parse_meta(data)}
        elif part == "charges":
            found = {"charges": reports.parse_charges(data)}
        else:
            items = data if isinstance(data, list) else []
            found = {
                "rows": [reports.parse_pnl_row(i) for i in items if isinstance(i, Mapping)],
                "page_size": size,
            }
    except reports.Unreadable:
        return _failed(ctx.clock(), "unreadable", **head)
    return {"state": "ok", "read_at": ctx.clock(), "note": None, **head, **found}


def _today(ctx) -> int:
    from ..core.days import day_of_instant

    return day_of_instant(ctx.clock())


def _span(message: Mapping[str, Any], fy: int) -> dict[str, int]:
    """``from`` and ``to``, both days of the financial year, in order; neither is the whole year."""
    if message.get("from") is None and message.get("to") is None:
        return {}
    start, end = _day(message, "from"), _day(message, "to")
    first, last = reports.fy_span(fy)
    if not first <= start <= end <= last:
        raise _bad("from")
    return {"from": start, "to": end}


def _dmy(day: int) -> str:
    return f"{day % 100:02d}-{day // 100 % 100:02d}-{day // 10000:04d}"


@op("benchmark_closes")
async def benchmark_closes(ctx, message):
    index = message.get("index")
    if index not in BENCHMARKS:
        raise _bad("index")
    start, end = _day(message, "from"), _day(message, "to")
    if start > end:
        raise _bad("from")
    store = need(ctx)
    found = await work(lambda: store.read(Scope.owner(), lambda tx: tx.index_closes()))
    closes = [
        {"day": day, "close_u": close}
        for key, series in found.items()
        if BENCHMARK_OF.get(key) == index
        for day, close in series
        if start <= day <= end
    ]
    return {"state": "ok", "read_at": ctx.clock(), "note": None, "index": index, "closes": closes}
