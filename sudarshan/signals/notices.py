"""The TRINETRA view's notices on Telegram (T9, design 3.6): decided here, told by the alerts'
notice desk.

``due`` is pure: the clock, the trading calendar's answers and what was told before are its
arguments. ``tell`` records what is due as events in the service's spool
(``/var/lib/sudarshan-signals/events.jsonl``, ``ops/events.py``, src ``signals``), from which
``sudarshan-alerts`` words and sends each once, in the notices lane (``alerts/notices.py``). The
words are ``contract.WORDS``. Nothing here sends anything, and nothing names a path, unit or port
of TRINETRA's: SUDARSHAN can only say that the letterbox stayed empty.

    signals_quiet   a trading day, a document seen before, and none since 08:30 by 09:20:
                    "TRINETRA has not published today." (at once)
    signals_stale   stale throughout the last 10 minutes of market hours, the last document not a
                    planned stop: "TRINETRA stopped publishing at 10:42. SUDARSHAN cannot restart
                    it; see TRINETRA's own runbook." (at once; once a quiet day or a stale spell)
    signals_back    a document again after one of those two: "TRINETRA is publishing again since
                    11:05." (at once)
    signals_format  a document of a newer format today: the newer-format sentence (once a day,
                    after the close)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from sudarshan.ops import events
from sudarshan.signals import contract as c

IST = timezone(timedelta(seconds=c.IST_OFFSET_S), "IST")
SRC = "signals"
KINDS = ("signals_quiet", "signals_stale", "signals_back", "signals_format")


@dataclass(frozen=True)
class Memory:
    """What was told: the published time of the document whose silence was told (a quiet day or
    a stale spell; None once its return was told), and the days of the last quiet and format
    notices."""

    silent_since: int | None = None
    quiet_day: int | None = None
    format_day: int | None = None

    @classmethod
    def of(cls, raw: Any) -> Memory:
        if not isinstance(raw, dict):
            return cls()
        return cls(**{k: raw.get(k) for k in ("silent_since", "quiet_day", "format_day")})

    def as_dict(self) -> dict[str, Any]:
        return {"silent_since": self.silent_since, "quiet_day": self.quiet_day,
                "format_day": self.format_day}  # fmt: skip


@dataclass(frozen=True)
class Notice:
    kind: str
    ident: str
    facts: dict[str, str]


def _clock(hhmm: str) -> time:
    hours, minutes = hhmm.split(":")
    return time(int(hours), int(minutes))


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def due(
    memory: Memory,
    *,
    now_ms: int,
    last: dict[str, Any] | None,
    newer_format_ms: int | None,
    is_trading_day: Callable[[Any], bool],
    in_market_hours: Callable[[datetime], bool],
) -> tuple[list[Notice], Memory]:
    """The notices due now, and the memory after telling them. ``last`` is the stored note of the
    last accepted document (``published_ms``, ``fresh_until_ms``, ``received_ms``, ``stopping``)."""
    now = datetime.fromtimestamp(now_ms / 1000, IST)
    day = int(now.strftime("%Y%m%d"))
    out: list[Notice] = []
    published = int(last["published_ms"]) if last else None
    received = int(last.get("received_ms") or 0) if last else 0
    # back: a document after a told silence
    if (
        memory.silent_since is not None
        and published is not None
        and published != memory.silent_since
    ):
        if now_ms < int(last["fresh_until_ms"]):
            out.append(Notice("signals_back", f"signals-back-{published}",
                              {"since": str(published // 1000)}))  # fmt: skip
            memory = replace(memory, silent_since=None)
    trading = is_trading_day(now.date())
    since = _ms(datetime.combine(now.date(), _clock(c.QUIET["since"]), IST))
    check = _ms(datetime.combine(now.date(), _clock(c.QUIET["check"]), IST))
    # quiet: a trading day, a document seen before, none since 08:30 by 09:20
    if (
        last is not None
        and trading
        and now_ms >= check
        and received < since
        and memory.quiet_day != day
    ):
        out.append(Notice("signals_quiet", f"signals-quiet-{day}", {}))
        memory = replace(memory, quiet_day=day, silent_since=published)
    # stale: throughout the last 10 minutes of market hours, not a planned stop
    if (
        last is not None
        and not last.get("stopping")
        and memory.silent_since != published
        and now_ms - int(last["fresh_until_ms"]) >= c.STALE_NOTICE_MS
        and in_market_hours(now)
        and in_market_hours(now - timedelta(milliseconds=c.STALE_NOTICE_MS))
    ):
        out.append(Notice("signals_stale", f"signals-stale-{published}",
                          {"since": str(published // 1000)}))  # fmt: skip
        memory = replace(memory, silent_since=published)
    # format: a newer format seen today, once a day
    if newer_format_ms is not None and memory.format_day != day:
        seen = datetime.fromtimestamp(newer_format_ms / 1000, IST)
        if int(seen.strftime("%Y%m%d")) == day:
            out.append(Notice("signals_format", f"signals-format-{day}", {}))
            memory = replace(memory, format_day=day)
    return out, memory


def tell(spool: Path, notices: list[Notice], now_ms: int) -> list[Notice]:
    """Record each notice in the spool; the ones that are there (a notice already in the file
    counts as told). Never raises."""
    told = []
    for notice in notices:
        if events.record(spool, kind=notice.kind, event_id=notice.ident, at=now_ms / 1000,
                         run=notice.ident, src=SRC, **notice.facts):  # fmt: skip
            told.append(notice)
    return told
