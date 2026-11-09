"""A dashboard card, worked out (M9.16, dashboards design 3.2, 3.6 and 3.8). Pure.

No store, no clock, no feed: every fact arrives as an argument and every decision leaves as a
return value, so the card is the same wherever it is read. ``dashboard_ops.py`` reads the store
and hands the facts here; the sentences are ``words.py``'s.

* How a screen is checked (``mode_of``): at its own candle closes, on the forming day candle at
  each close, or after the close. ``day_only`` says a screen reads nothing but day candles and
  the session's levels, which is when "when it checks" is the owner's to choose.
* What a card shows of a run: ``usable_clauses`` keeps only the value columns the screen still
  has; ``sort_rows`` puts the rows in the widget's own order (ties by symbol); ``thin_series``
  keeps at most 80 points of the day's checks for a count card.
* Why a card is or is not live (``line``): the card's own words for a screen that is stopped,
  paused or checked after the close, the engine line's own sentence for the rest, so a card that
  cannot be live says why (rule 7).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from . import scheduler, words
from . import types as T
from .store import VALUE_CLAUSES_MAX, WIDGET_KINDS, WIDGET_ORDERS, WIDGET_ROWS, WIDGET_SORTS

#: How long the ``dashboard`` read spends working cards out before the rest answer "rows pending".
CARD_BUDGET_S = 1.5
#: A day's checks kept for a count card, and the slot a 1 or 3-minute screen keeps one check of.
SERIES_MAX = 80
SERIES_SLOT_MS = 5 * 60_000
#: Minutes of a trading day, the most candles of any minute size that one day holds.
DAY_MINUTES = 375
VALUE_COLUMNS = VALUE_CLAUSES_MAX
#: The longest clause id a widget may name; the screen's own ids are far shorter.
ID_MAX = 64
#: When a day-only screen is checked (the owner's choice): the run each writes.
WHEN_RUNS = {"during_day": {"on": "close", "tf": 15}, "after_close": {"on": "after_close"}}

#: What the card's line is, for the page to choose its icon (design 3.6).
LINE_KINDS = (
    "stopped",
    "paused",
    "engine",
    "waiting",
    "waits",
    "closed",
    "feed_down",
    "after_close",
    "partial",
    "checking",
    "real_time",
)


def day_only(need: Mapping[Any, int]) -> bool:
    """A screen whose plan reads no candle but the day's: its levels and the forming day candle."""
    return all(tf == T.DAY for tf in need)


def mode_of(run: Mapping[str, Any], only_day: bool) -> tuple[str, str]:
    """(mode, words) of how a screen is checked."""
    after = f"{scheduler.AFTER_CLOSE_MIN // 60:02d}:{scheduler.AFTER_CLOSE_MIN % 60:02d}"
    if run["on"] == scheduler.AFTER_CLOSE:
        mode, tf = words.MODE_AFTER_CLOSE, None
    elif only_day:
        mode, tf = words.MODE_FORMING_DAY, run["tf"]
    else:
        mode, tf = words.MODE_INTRADAY, run["tf"]
    return mode, words.mode_words(mode, tf, after)


def waits_of(need: Mapping[Any, int]) -> str | None:
    """Why the screen cannot judge yet: it needs more candles than today alone gives, so it waits
    for the history download (not built yet). The day candles first, then the minute sizes."""
    if T.WEEK in need:
        return words.waits_words(T.WEEK, need[T.WEEK], 0)
    if need.get(T.DAY, 0) > 1:
        return words.waits_words(T.DAY, need[T.DAY], 1)
    for tf in sorted(k for k in need if isinstance(k, int)):
        most = T.candles_in(tf, DAY_MINUTES * 60_000)
        if need[tf] > most:
            return words.waits_words(tf, need[tf], most)
    return None


def usable_clauses(requested: Sequence[str] | None, clause_ids: Sequence[str]) -> list[str] | None:
    """The value columns the screen still has, in the widget's order; None (the screen's first
    two) when none is left."""
    kept = [c for c in requested or () if c in clause_ids][:VALUE_COLUMNS]
    return kept or None


def usable_sort(
    sort_by: Mapping[str, Any] | None, clause_ids: Sequence[str]
) -> dict[str, Any] | None:
    """The widget's sort, or None (the screen's own rank) when it sorts by a line that is gone."""
    if not sort_by:
        return None
    if sort_by["by"] == "value" and sort_by.get("clause") not in clause_ids:
        return None
    return dict(sort_by)


def _number(text: Any) -> Decimal | None:
    try:
        value = Decimal(str(text).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def sort_rows(
    rows: Sequence[Mapping[str, Any]],
    sort_by: Mapping[str, Any] | None,
    *,
    entered: Mapping[str, int],
    found: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The rows in the widget's order: by the run's % change, its price, the time the instrument
    entered (``entered``, by key) or a clause's value (``found``, the run's values by key). A row
    with no figure goes last; ties by symbol. None keeps the screen's own rank. Ranks are renumbered
    to the order shown. Rows only move here, at a check or a settings change, never on a tick."""
    if not sort_by:
        return [dict(row) for row in rows]
    by, descending = sort_by["by"], sort_by["order"] == "desc"

    def figure(row: Mapping[str, Any]) -> Decimal | None:
        key = row.get("key", "")
        if by == "entered":
            return Decimal(entered[key]) if key in entered else None
        if by == "value":
            return _number(((found.get(key) or {}).get(sort_by.get("clause")) or {}).get("l"))
        return _number(row.get(by)) if row.get(by) is not None else None

    def tie(row: Mapping[str, Any]) -> tuple[str, str]:
        return (row["symbol"], row.get("key", ""))

    have = sorted((r for r in rows if figure(r) is not None), key=tie)
    have.sort(key=lambda r: figure(r), reverse=descending)  # type: ignore[arg-type,return-value]
    lacking = sorted((r for r in rows if figure(r) is None), key=tie)
    return [{**row, "rank": place} for place, row in enumerate([*have, *lacking], start=1)]


def newest_per_boundary(runs: Sequence[Mapping[str, Any]]) -> list[tuple[int, int]]:
    """One (candle_end, matches) for each boundary, the newest run's: a changed screen runs its
    latest boundary again, and that check is the one the card means."""
    newest: dict[int, Mapping[str, Any]] = {}
    for run in runs:
        if run["candle_end"] not in newest or run["id"] > newest[run["candle_end"]]["id"]:
            newest[run["candle_end"]] = run
    return [(end, run["matched"]) for end, run in sorted(newest.items())]


def thin_series(
    points: Sequence[tuple[int, int]], *, slot_ms: int = SERIES_SLOT_MS, limit: int = SERIES_MAX
) -> list[tuple[int, int]]:
    """A day's checks as (at, matches), oldest first, kept to ``limit``: the last check of each
    ``slot_ms`` slot (a 1 or 3-minute screen), then the newest ``limit`` if still more. A slot is
    the five minutes that end on a 5-minute mark, so a check at 15:30 closes the 15:26 slot."""
    last: dict[int, tuple[int, int]] = {}
    for at, matches in sorted(points):
        last[(at - 1) // slot_ms] = (at, matches)
    return [last[slot] for slot in sorted(last)][-limit:]


def line(
    *,
    status: str,
    reason: str | None,
    mode: str,
    waits: str | None,
    engine_state: str,
    engine_line: str,
    run_at: int | None,
    now: int,
    next_at: int | None = None,
    count: bool = False,
) -> dict[str, Any]:
    """The card's line, the first that applies: ``kind`` (for the icon), ``words`` (the sentence),
    ``live`` (ticks and checks are arriving) and the engine ``state`` it was worked out from. The
    page overrides it only for what it alone knows: not in the owner's room, or ticks that stopped."""
    after = f"{scheduler.AFTER_CLOSE_MIN // 60:02d}:{scheduler.AFTER_CLOSE_MIN % 60:02d}"
    if status == "stopped":
        kind = "stopped"
    elif status == "paused":
        kind = "paused"
    elif engine_state in (words.NOT_ANSWERING, words.SWITCHED_OFF):
        kind = "engine"
    elif engine_state == words.WAITING_MASTER:
        kind = "waiting"
    elif waits:
        kind, engine_line = "waits", waits
    elif engine_state == words.CLOSED:
        kind = "closed"
    elif engine_state == words.FEED_DOWN:
        kind = "feed_down"
    elif mode == words.MODE_AFTER_CLOSE:
        kind = "after_close"
    elif engine_state == words.REAL_TIME:
        kind = "checking" if count else "real_time"  # a count card shows no prices
    else:
        kind = "partial"
    text = words.card_line_words(
        kind,
        reason=reason,
        engine_line=engine_line,
        run_at=run_at,
        now=now,
        after_close=after,
        next_at=next_at,
    )
    return {
        "kind": kind,
        "words": text,
        "live": kind in ("real_time", "checking"),
        "state": engine_state,
    }


# ---- what a write may say (the owner's words for each refusal) ---------------------------------------------
class WidgetError(ValueError):
    """A widget the owner asked for that cannot be; ``str()`` is his sentence."""


def check_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The widget fields named in ``raw`` (kind, rows, sort_by, value_clauses), each checked and
    worded; a field that is not named is not in the answer."""
    if "kind" in raw and raw["kind"] not in WIDGET_KINDS:
        raise WidgetError(words.DASH_KIND)
    if "rows" in raw and (isinstance(raw["rows"], bool) or raw["rows"] not in WIDGET_ROWS):
        raise WidgetError(words.DASH_ROWS)
    sort = raw.get("sort_by")
    if sort is not None:
        if (
            not isinstance(sort, Mapping)
            or set(sort) - {"by", "order", "clause"}
            or sort.get("by") not in WIDGET_SORTS
            or sort.get("order") not in WIDGET_ORDERS
        ):
            raise WidgetError(words.DASH_SORT)
        clause = sort.get("clause")
        if (sort["by"] == "value") != (clause is not None) or (
            clause is not None and not (isinstance(clause, str) and 0 < len(clause) <= ID_MAX)
        ):
            raise WidgetError(words.DASH_SORT)
        sort = dict(sort)
    clauses = raw.get("value_clauses")
    if clauses is not None and (
        not isinstance(clauses, (list, tuple))
        or len(clauses) > VALUE_COLUMNS
        or len(set(clauses)) != len(clauses)
        or not all(isinstance(c, str) and 0 < len(c) <= ID_MAX for c in clauses)
    ):
        raise WidgetError(words.DASH_VALUES)
    out = {n: raw[n] for n in ("kind", "rows", "sort_by", "value_clauses") if n in raw}
    if sort is not None:
        out["sort_by"] = sort
    if clauses is not None:
        out["value_clauses"] = list(clauses)
    return out


def check_order(raw: Any) -> list[tuple[int, str]]:
    """The whole order as (screen_id, kind) pairs."""
    if not isinstance(raw, (list, tuple)) or not 0 < len(raw) <= 20:
        raise WidgetError(words.DASH_ORDER)
    pairs = []
    for item in raw:
        if (
            not isinstance(item, (list, tuple))
            or len(item) != 2
            or isinstance(item[0], bool)
            or not isinstance(item[0], int)
            or item[1] not in WIDGET_KINDS
        ):
            raise WidgetError(words.DASH_ORDER)
        pairs.append((item[0], item[1]))
    return pairs
