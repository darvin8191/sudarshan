"""``analytics``: the metrics block of a filter of closed trips (JB10, M14.3; design 5.2, 5.3, 5.5
and 6.2), every figure from ``core/``.

The filter (lane JD's page sends it as the address keeps it):

    mode       live (default) or sandbox; one journal at a time, never mixed
    period     today, week (Monday on), month (the 1st on), fy (1 April on) or all; month when
               nothing is sent; ``from`` and ``to`` (YYYYMMDD, by close day) take its place
    strategy   the name a trip counts under (the owner's override, else its label), any case
    tag        a mistake tag's id on the trip's note
    playbook   a playbook's id (the trip's setup)
    expiry     expiry: opened on an index expiry day or on the instrument's own; non_expiry:
               neither

The answer (``Analytics``): the metrics, the hold-time buckets, the calendar (day keys 20261109,
week keys 2026W45, month keys 202611), the equity curve and its drawdown over the period's
trading days (on the trading capital when one is set), the heat map (weekday 0 is Monday, the
entry hour in IST), the blocks by strategy, tag and playbook, the expiry split, and beta against
NIFTY 50 over the last 60 trading days (from 20 of them, the closes through the portfolio; null
when the portfolio cannot be asked).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..core import benchmark, equity, kinds, metrics, splits
from ..core.money import ist_day, to_date, to_day
from ..errors import ApiError
from ..portfolio import PortfolioUnavailable, link_for
from ..schedule import schedule_for
from . import op
from .common import OWNER, choice, need, text, whole, work

PERIODS = ("today", "week", "month", "fy", "all")
EXPIRY = ("expiry", "non_expiry")
DAY_LOW, DAY_HIGH = 19_700_101, 29_991_231
BETA_DAYS = kinds.LIMITS["beta_days"]
BETA_MIN = kinds.LIMITS["beta_min_days"]
#: The days scanned back for the trading days of a period with no start of its own.
ALL_DAYS_CAP = 10 * 366


def period_span(period: str, today: int) -> tuple[int | None, int]:
    """The close days a named period covers, up to today."""
    d = to_date(today)
    if period == "today":
        return today, today
    if period == "week":
        return to_day(d - timedelta(days=d.weekday())), today
    if period == "month":
        return to_day(d.replace(day=1)), today
    if period == "fy":
        start = date(d.year if d.month >= 4 else d.year - 1, 4, 1)
        return to_day(start), today
    return None, today


def trading_days(plan: Any, start: int, end: int) -> list[int]:
    out, cursor, last = [], to_date(start), to_date(end)
    while cursor <= last:
        if plan.trading(cursor):
            out.append(to_day(cursor))
        cursor += timedelta(days=1)
    return out


def for_core(trip: dict[str, Any], tags: dict[int, str], books: dict[int, str],
             note: dict[str, Any] | None) -> dict[str, Any]:  # fmt: skip
    """A closed trip as ``core/metrics.py`` reads it: charges actual where known, else est."""
    charges = trip.get("charges_actual_paise")
    charges = trip.get("charges_est_paise") if charges is None else charges
    return {
        **trip,
        "charges_paise": int(charges or 0),
        "tags": [tags[t] for t in (note or {}).get("mistakes") or [] if t in tags],
        "playbook": books.get(trip.get("setup_id")),
    }


async def beta_of(ctx: Any, plan: Any, rows: list[dict[str, Any]], end: int, capital: int | None,
                  first_day: int | None) -> dict[str, Any] | None:  # fmt: skip
    """Beta and correlation against NIFTY 50 over the last 60 trading days up to ``end``, from
    the first day the journal holds a trade; None when the index closes cannot be read."""
    if not capital:
        return benchmark.correlation_beta([], capital)
    window = sorted(to_day(d) for d in plan.previous_trading_days(to_date(end), BETA_DAYS))
    if plan.trading(to_date(end)):
        window = [*window[1:], end] if len(window) >= BETA_DAYS else [*window, end]
    days = [d for d in window if first_day is not None and d >= first_day]
    ready = None
    if first_day is not None and len(days) < BETA_MIN:
        cursor, count = to_date(first_day), 0
        for _ in range(400):
            count += plan.trading(cursor)
            if count >= BETA_MIN:
                ready = to_day(cursor)
                break
            cursor += timedelta(days=1)
    if len(days) < BETA_MIN:  # too few days to ask the index's closes for: core's own words
        placeholder = {"net_paise": 0, "close_u": 1, "prev_close_u": 1}
        return benchmark.correlation_beta([placeholder] * len(days), capital, ready_day=ready)
    before = plan.previous_trading_days(to_date(days[0]), 1)
    start = to_day(before[0]) if before else days[0]
    try:
        answer = await link_for(ctx).benchmark_closes("nifty50", start, days[-1])
    except PortfolioUnavailable:
        return None
    if answer.get("state") != "ok":
        return None
    closes = {int(c["day"]): int(c["close_u"]) for c in answer.get("closes") or []
              if isinstance(c, dict) and isinstance(c.get("close_u"), int)}  # fmt: skip
    net = {}
    for trip in rows:
        net[int(trip["close_day"])] = net.get(int(trip["close_day"]), 0) + int(trip["net_paise"])
    series, previous = [], start
    for day in days:
        if day in closes and previous in closes and closes[previous]:
            series.append({"net_paise": net.get(day, 0), "close_u": closes[day],
                           "prev_close_u": closes[previous]})  # fmt: skip
        previous = day
    return benchmark.correlation_beta(series, capital, ready_day=ready)


@op("analytics")
async def analytics(ctx, args):
    store = need(ctx)
    mode = choice(args, "mode", kinds.MODES, "live")
    strategy = text(args, "strategy", limit=kinds.LIMITS["strategy_name"], default=None)
    tag = whole(args, "tag", low=1, default=None)
    playbook = whole(args, "playbook", low=1, default=None)
    expiry = choice(args, "expiry", EXPIRY, None)
    start = whole(args, "from", low=DAY_LOW, high=DAY_HIGH, default=None)
    end = whole(args, "to", low=DAY_LOW, high=DAY_HIGH, default=None)
    today = ist_day(ctx.clock())
    if start is None and end is None:
        start, end = period_span(choice(args, "period", PERIODS, "month"), today)
    else:
        end = end if end is not None else today
    if start is not None and start > end:
        raise ApiError(400, "bad_request", "The period starts after it ends.", "from")

    def read(tx):
        trips = [t for t in tx.trips_page(mode, status="closed", from_day=start, to_day=end,
                                          setup_id=playbook, limit=1_000_000)
                 if t["net_paise"] is not None]  # fmt: skip
        notes = tx.notes_of(t["id"] for t in trips)
        tags = {t["id"]: t["name"] for t in tx.rows("tag")}
        books = {b["id"]: b["name"] for b in tx.rows("playbook")}
        first = tx.min_of("round_trip", "close_day", mode=mode, status="closed")
        return trips, notes, tags, books, first, tx.settings()

    trips, notes, tags, books, first_day, settings = await work(lambda: store.read(OWNER, read))
    rows = []
    for trip in trips:
        note = notes.get(trip["id"])
        if strategy is not None and metrics.strategy_of(trip).casefold() != strategy.casefold():
            continue
        if tag is not None and tag not in ((note or {}).get("mistakes") or []):
            continue
        on_expiry = bool(trip.get("index_expiry_day") or trip.get("own_expiry_day"))
        if expiry == "expiry" and not on_expiry or expiry == "non_expiry" and on_expiry:
            continue
        rows.append(for_core(trip, tags, books, note))
    plan = schedule_for(ctx)
    capital = settings.get("capital_base_paise")
    span_start = start if start is not None else min((r["close_day"] for r in rows), default=None)
    days = trading_days(plan, span_start, end) if span_start is not None else []
    if span_start is not None and len(days) > ALL_DAYS_CAP:
        days = days[-ALL_DAYS_CAP:]
    return {
        "metrics": metrics.compute(rows),
        "hold": metrics.hold_buckets(rows),
        "calendar": metrics.calendar(rows),
        "equity": equity.equity_curve(rows, days, capital),
        "heat": equity.heat_map(rows),
        "by_strategy": metrics.blocks(rows, "strategy"),
        "by_tag": metrics.blocks(rows, "tag"),
        "by_playbook": metrics.blocks(rows, "playbook"),
        "expiry": splits.expiry_split(rows),
        "beta": await beta_of(ctx, plan, rows, end, capital, first_day),
    }
