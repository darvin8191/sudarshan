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

from dataclasses import dataclass
from datetime import UTC, datetime
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


def risk_for(service: Any) -> RiskReader:
    """The service's reader of the risk layer's state, built on first use."""
    return service.part("risk", lambda svc: RiskReader(svc.settings.risk_db))
