"""When a screen runs (M9.14, design 3.6): the day's boundaries, the runs due now, the next one.

Pure. The sessions come only from ``sudarshan.alerts.market.sessions`` (cross-design X11): the
runtime builds ``Market.sessions`` once a day and hands it here with the screens and the clock.

A screen that runs ``on == "close"`` with timeframe N has a boundary at every N-minute candle end
of every segment its universe trades in (the union, sorted, each numbered ``k`` from 0), and runs
at the end plus the arrival grace the market alerts use (S11: ``clock.ARRIVAL_GRACE_MS``, 5 s, the
same moment the 1-minute bar is sealed). A screen that runs ``after_close`` has one boundary a
trading day, at 16:05 IST (R5), whose candle is the whole day. A run has the key
``<screen>:v<version>:<session>:<tf>:<k>``; what is recorded under that key is never run again,
and a boundary whose run is more than ``LATE_MS`` old is made late, marked so (3.6, "checked late
at 10:16 after a restart").

A stock that closes by auction has no continuous trade after the cash close until its print
(X6): a screen whose universe holds such stocks holds its last cash boundary of the day until
every one has printed, or at the latest until the auction window's end plus the grace (S12:
15:35:05 on a normal day). ``due`` takes ``printed(screen) -> bool`` for that.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sudarshan.alerts.market.clock import ARRIVAL_GRACE_MS
from sudarshan.alerts.market.sessions import AUCTION, Session, epoch_ms, key_day
from sudarshan.autologin.schedule import IST

from .types import RunKey

GRACE_MS = ARRIVAL_GRACE_MS
#: A boundary whose run starts this long after its due moment is a late run (a restart).
LATE_MS = 60_000
#: The after-close run (R5): 16:05 IST, after the 16:02 close snapshot.
AFTER_CLOSE_MIN = 16 * 60 + 5
AFTER_CLOSE = "after_close"
MINUTE_MS = 60_000


@dataclass(frozen=True, slots=True)
class Screen:
    """What the scheduler needs to know about one active screen."""

    id: int
    version: int
    #: "close" or "after_close" (the plan's ``on``).
    on: str
    #: Minutes for "close"; ignored for "after_close".
    tf: int
    #: The session keys its universe trades under (``NSE_EQ``, ``NSE_FO``, ``NSE_EQ+auction``...).
    segments: frozenset[str]


@dataclass(frozen=True, slots=True)
class Boundary:
    k: int
    #: The candle judged: its start and end (epoch ms). For after_close, the day's sessions.
    start: int
    end: int
    #: When the run is made (epoch ms): the end plus the grace, or 16:05.
    at: int
    #: The run's timeframe label: "15" or "after_close".
    tf: str


@dataclass(frozen=True, slots=True)
class Due:
    """A run to make now."""

    key: RunKey
    screen: Screen
    boundary: Boundary
    #: True when the run is more than LATE_MS after its moment (a restart): marked on the run.
    late: bool
    #: The screen's last boundary of the day: a day-end grace ends with it.
    last: bool = False


def _spans(screen: Screen, sessions: Mapping[str, Session]) -> list[Session]:
    return [sessions[name] for name in sorted(screen.segments) if name in sessions]


def boundaries(screen: Screen, sessions: Mapping[str, Session]) -> list[Boundary]:
    """The screen's boundaries today, in order; empty when none of its segments trades."""
    spans = _spans(screen, sessions)
    if not spans:
        return []
    if screen.on == AFTER_CLOSE:
        day = key_day(spans[0].key)
        at = epoch_ms(day, AFTER_CLOSE_MIN)
        return [
            Boundary(0, min(s.start for s in spans), max(s.end for s in spans), at, AFTER_CLOSE)
        ]
    size = screen.tf * MINUTE_MS
    ends: dict[int, int] = {}  # end -> earliest start of a candle ending there
    for span in spans:
        k = 0
        while span.start + k * size < span.end:
            start = span.start + k * size
            end = min(start + size, span.end)
            ends[end] = min(ends.get(end, start), start)
            k += 1
    return [
        Boundary(k, start, end, end + GRACE_MS, str(screen.tf))
        for k, (end, start) in enumerate(sorted(ends.items()))
    ]


def _held_until(screen: Screen, sessions: Mapping[str, Session], boundary: Boundary) -> int | None:
    """For a screen with stocks that close by auction: the latest moment its last cash boundary
    waits for their prints (the auction window's end plus the grace), else None."""
    if screen.on == AFTER_CLOSE:
        return None
    for name in screen.segments:
        span = sessions.get(name)
        if name.endswith(AUCTION) and span is not None and span.auction_until is not None:
            # the print lands in the candle ending at the cash close (bars.py), not at 15:15
            if boundary.end == span.auction_close:
                return span.auction_until + GRACE_MS
    return None


def due(
    screens: Iterable[Screen],
    sessions: Mapping[str, Session],
    now: int,
    recorded: Callable[[str], bool],
    *,
    printed: Callable[[Screen], bool] = lambda screen: True,
    session_key: int | None = None,
) -> list[Due]:
    """Every run to make now, oldest boundary first: its moment has come, it is not recorded, and
    (for the last cash candle of a screen with auction stocks) the prints are in or the window has
    ended. ``recorded(run_key)`` answers from the store."""
    out: list[Due] = []
    for screen in screens:
        spans = _spans(screen, sessions)
        if not spans:
            continue
        session = session_key if session_key is not None else spans[0].key
        today = boundaries(screen, sessions)
        for boundary in today:
            if now < boundary.at:
                break
            hold = _held_until(screen, sessions, boundary)
            if hold is not None and now < hold and not printed(screen):
                continue
            key = RunKey(screen.id, screen.version, session, boundary.tf, boundary.k)
            if recorded(str(key)):
                continue
            late = now - boundary.at > LATE_MS
            out.append(Due(key, screen, boundary, late, boundary is today[-1]))
    out.sort(key=lambda item: (item.boundary.at, item.screen.id))
    return out


def next_at(screens: Iterable[Screen], sessions: Mapping[str, Session], now: int) -> int | None:
    """The next moment any screen runs today, or None when today has no more."""
    best: int | None = None
    for screen in screens:
        for boundary in boundaries(screen, sessions):
            if boundary.at > now and (best is None or boundary.at < best):
                best = boundary.at
                break
    return best


def next_trading_boundary(
    screens: Sequence[Screen],
    now: int,
    *,
    sessions_of: Callable[[date], Mapping[str, Session]],
    horizon: int = 15,
) -> int | None:
    """The first run after ``now`` within ``horizon`` days, for "Market closed, next check Mon
    09:30": today's sessions first, then each following day's."""
    first = datetime.fromtimestamp(now / 1000, IST).date()
    for offset in range(horizon + 1):
        found = next_at(screens, sessions_of(first + timedelta(days=offset)), now)
        if found is not None:
            return found
    return None


__all__ = [
    "AFTER_CLOSE",
    "AFTER_CLOSE_MIN",
    "GRACE_MS",
    "LATE_MS",
    "Boundary",
    "Due",
    "Screen",
    "boundaries",
    "due",
    "next_at",
    "next_trading_boundary",
]
