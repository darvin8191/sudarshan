"""The risk layer's state, read-only: the kill switch and today's daily loss lock (JB3', Y30).

``db/sudarshan_risk.db`` is the risk layer's own file (``sudarshan/guard/schema.py``). Its store
(``RiskStore``) makes missing tables and default rows on first use, so the journal never goes
through it: it opens the file the way ``alerts/owner.py`` opens openalgo.db (``mode=ro`` with a
write-ahead log, ``immutable=1`` without) and reads two tables, ``risk_state`` (row 1: whether
the kill switch is on, and since when) and ``daily_locks`` (a pipe's lock for an India date).

What the day report and the brief say from it is one line: "Kill switch off", "Daily loss limit
reached at 13:42; new orders refused for the day". A file that cannot be read says "not read",
never "off": a guess there would read as safety.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .core.money import IST, to_date
from .openalgo_ro import RecordsUnavailable, _ReadOnly


@dataclass(frozen=True)
class RiskState:
    """``kill_switch`` is None when the file could not be read; ``lock_at_ms`` when today's lock
    in the pipe was written (None: not reached, or not read when ``read`` is False)."""

    read: bool
    kill_switch: bool | None
    kill_since_ms: int | None = None
    lock_at_ms: int | None = None

    def lock_clock(self) -> str | None:
        """The lock's time of day in IST, ``13:42``."""
        if self.lock_at_ms is None:
            return None
        at = datetime.fromtimestamp(self.lock_at_ms / 1000, IST)
        return f"{at.hour:02d}:{at.minute:02d}"


NOT_READ = RiskState(read=False, kill_switch=None)
#: The columns of the year's risk history file, in order (``RiskReader.history``).
HISTORY_COLUMNS = ("time_ist", "last_time_ist", "what", "pipe", "where", "by", "times", "summary")
HISTORY_PAGE = 2000
_CELL = 300


def _cell(value: Any) -> str:
    return "" if value is None else " ".join(str(value).split())[:_CELL]


def _ist_text(value: Any) -> str:
    ms = _utc_ms(value)
    return "" if ms is None else datetime.fromtimestamp(ms / 1000, IST).isoformat()


def _utc_ms(value: Any) -> int | None:
    """The risk store's naive UTC time (it writes ``datetime`` without a zone) as epoch ms."""
    if value is None:
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value).strip().replace(" ", "T")[:26])
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return int(stamp.timestamp() * 1000)


class RiskReader:
    """The two tables, read-only, one question each."""

    def __init__(self, path: Path | None, **options: Any):
        self.file = _ReadOnly(path, "sudarshan_risk.db", **options)

    def state(self, day: int, pipe: str = "live") -> RiskState:
        """The kill switch, and the lock of ``pipe`` on the India date ``day``."""
        try:
            switch = self.file.query("SELECT ks_on, ks_since FROM risk_state WHERE id = 1")
            locks = self.file.query(
                "SELECT at FROM daily_locks WHERE pipe = :pipe AND ist_date = :day",
                pipe=pipe,
                day=to_date(day).isoformat(),
            )
        except RecordsUnavailable:
            return NOT_READ
        row = switch[0] if switch else {"ks_on": 0, "ks_since": None}
        on = bool(row["ks_on"])
        return RiskState(
            read=True,
            kill_switch=on,
            kill_since_ms=_utc_ms(row["ks_since"]) if on else None,
            lock_at_ms=_utc_ms(locks[0]["at"]) if locks else None,
        )


def _utc_text(day_start_ist: datetime) -> str:
    """An India midnight as the risk store writes a time: naive UTC text."""
    return day_start_ist.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def history(reader: RiskReader, start_day: int, end_day: int,
            page: int = HISTORY_PAGE) -> Iterator[dict[str, str]]:  # fmt: skip
    """The risk layer's history from ``start_day`` to ``end_day`` (India dates, both included)
    for the year's records pack: the audit (orders refused, the kill switch, settings changed)
    as the risk layer still holds it, then the days a daily loss limit was reached. Oldest
    first, a page at a time. Raises ``RecordsUnavailable``. The audit's ``detail`` is left out:
    the summary is the sentence the owner was shown."""
    first = datetime.combine(to_date(start_day), datetime.min.time(), IST)
    last = datetime.combine(to_date(end_day) + timedelta(days=1), datetime.min.time(), IST)
    after = 0
    while True:
        rows = reader.file.query(
            "SELECT id, at, last_at, kind, pipe, surface, actor, count, summary FROM risk_audit "
            "WHERE id > :after AND last_at >= :start AND last_at < :end ORDER BY id LIMIT :page",
            after=after,
            start=_utc_text(first),
            end=_utc_text(last),
            page=page,
        )
        for row in rows:
            yield {
                "time_ist": _ist_text(row["at"]),
                "last_time_ist": _ist_text(row["last_at"]),
                "what": _cell(row["kind"]),
                "pipe": _cell(row["pipe"]),
                "where": _cell(row["surface"]),
                "by": _cell(row["actor"]),
                "times": _cell(row["count"]),
                "summary": _cell(row["summary"]),
            }
        if len(rows) < page:
            break
        after = int(rows[-1]["id"])
    for row in reader.file.query(
        "SELECT pipe, ist_date, at, figure, loss_limit FROM daily_locks "
        "WHERE ist_date >= :start AND ist_date <= :end ORDER BY ist_date, pipe",
        start=to_date(start_day).isoformat(),
        end=to_date(end_day).isoformat(),
    ):
        yield {
            "time_ist": _ist_text(row["at"]),
            "last_time_ist": "",
            "what": "daily_loss_limit_reached",
            "pipe": _cell(row["pipe"]),
            "where": "",
            "by": "",
            "times": "1",
            "summary": f"Day's P&L {_cell(row['figure'])} against a limit of "
            f"{_cell(row['loss_limit'])} rupees on {_cell(row['ist_date'])}",
        }


def risk_for(service: Any) -> RiskReader:
    """The service's reader of the risk layer's state, built on first use."""
    return service.part("risk", lambda svc: RiskReader(svc.settings.risk_db))
