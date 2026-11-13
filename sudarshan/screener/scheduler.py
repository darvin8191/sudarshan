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
15:35:05 on a normal day; from 7 Dec 2026, when the auction runs 15:30-15:40 after the cash close
and its print may come until 15:45, 15:45:05).

From 7 Dec 2026 an index's closing value may come after the cash close too (review M2): a screen
whose universe holds an index holds that boundary until the window's end plus the grace (15:45:05),
whatever has come, since a closing value cannot be told from a later one. The values it judges are
the same when none comes.

Both holds are decided by the screen's members today, never by its universe's kind alone (review
N1: a watchlist screen may hold no index): ``due`` takes ``waits(screen) -> Waits``, which the
runtime builds from the members with ``waits_of``. A held boundary holds the screen's later
boundaries too, so its runs are made, recorded and applied to its list in boundary order: a 15:35
or 15:40 run of a short timeframe never comes before the held 15:30 run, and no member is marked
missing and then back because of the hold.

A held run is made when its hold ends, so it is late only when made more than ``LATE_MS`` after
that (review N2), and so is each later boundary the hold kept waiting: the prints' hold ends when
the last print arrived, at the latest at its window's end plus the grace; an index's at its
window's end plus the grace. The 15:30 run of an F&O stocks screen made as the prints arrive at
about 15:40, or of an index screen at 15:45:05, is on time; one a restart delayed past that is
late. A print the screener saw again after a restart counts from that sighting.

A screen made, resumed or edited during the session starts a new version (``Screen.since``, the
moment it began), and its run key is new for every boundary of the day. It gets one run, on the
latest candle closed by then, on time (not marked late unless a restart delayed it past
``LATE_MS`` from the later of its moment and the version's start), and then follows its schedule: the day's
earlier boundaries are not its missed checks and are never run at once (round-3 review MEDIUM-B6:
95 runs of a 3-minute screen made at 14:00, with their list changes and alerts for old candles).
A boundary after the version began that its run missed (a restart) is still run late, marked so.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sudarshan.alerts.market.clock import ARRIVAL_GRACE_MS
from sudarshan.alerts.market.sessions import (
    AUCTION,
    INDEX_SEGMENTS,
    Session,
    epoch_ms,
    key_day,
)
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
    #: When its current version began (epoch ms: made, resumed or edited); 0 when not known.
    since: int = 0


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
    #: True when the run is more than LATE_MS after its moment (a restart), or after the end of
    #: the hold that kept it waiting: marked on the run.
    late: bool
    #: The screen's last boundary of the day: a day-end grace ends with it.
    last: bool = False


@dataclass(frozen=True, slots=True)
class Waits:
    """What a screen's members make its last cash boundary of the day wait for (review N1). The
    runtime asks the screen's universe (``waits_of``); the scheduler stays pure."""

    #: Its stocks that close by auction: None while one has not printed; else when the last of
    #: their prints arrived (epoch ms), 0 when it holds none or no arrival is known.
    printed: int | None = 0
    #: Whether it holds an index, whose closing value may come after the cash close (review M2).
    index: bool = False


#: A screen whose members make nothing wait.
NO_WAITS = Waits()


def waits_of(
    members: Iterable[Any],
    printed_at: Callable[[Any], int | None],
    followed: Callable[[Any], bool] | None = None,
) -> Waits:
    """A screen's ``Waits`` from its members today (each with ``cas`` and ``segment``, as
    ``runs.Member``): ``printed_at(member)`` is when a stock's closing auction print arrived (0
    when not known), or None while it has not come. ``followed(member)`` says whether the
    screener follows the instrument (review N9): one it does not follow can never print here, so
    its arrival is unknown and nothing waits for it. None counts every member as followed."""
    found = tuple(members)
    last: int | None = 0
    for member in found:
        if member.cas and last is not None and (followed is None or followed(member)):
            arrived = printed_at(member)
            last = None if arrived is None else max(last, arrived)
    return Waits(last, any(member.segment in INDEX_SEGMENTS for member in found))


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
            # The print lands in the candle ending at the cash close (bars.py), not at 15:15. The
            # cash close is the plain segment's end: the auction's price comes at it until 6 Dec
            # 2026 and 10 minutes after it from 7 Dec, so its expected moment cannot stand in.
            cash = sessions.get(name.removesuffix(AUCTION))
            closes = cash.end if cash is not None else span.auction_close
            if boundary.end == closes:
                return span.auction_until + GRACE_MS
    return None


def _index_hold(screen: Screen, sessions: Mapping[str, Session], boundary: Boundary) -> int | None:
    """For a screen whose universe may hold indices, from 7 Dec 2026: the moment its last cash
    boundary waits until for their closing value (the window's end plus the grace, review M2),
    else None. Whether it holds one today is its members' (``Waits.index``, review N1)."""
    if screen.on == AFTER_CLOSE:
        return None
    for name in screen.segments:
        span = sessions.get(name)
        if span is not None and span.close_until is not None and boundary.end == span.end:
            return span.close_until + GRACE_MS
    return None


def _released(prints: int | None, index: int | None, waits: Waits, now: int) -> int | None:
    """Whether a boundary that may be held (``prints``: the latest moment it waits for the
    auction prints; ``index``: for an index's closing value) is free at ``now``: None while it is
    held, else the moment its hold ended (0 when nothing held it). The prints end their hold when
    the last one arrived, at the latest at its window's end; an index's, at its window's end."""
    ended = 0
    if prints is not None:
        if waits.printed is not None:
            ended = min(waits.printed, prints)
        elif now < prints:
            return None
        else:
            ended = prints
    if index is not None and waits.index:
        if now < index:
            return None
        ended = max(ended, index)
    return ended


def _first(today: Sequence[Boundary], since: int) -> int:
    """The index of the screen's first run today: the latest boundary whose candle had closed when
    its version began (its run moment, end plus the grace, may still be seconds away: the loop
    waits for it, so an edit just after a close judges that candle once and no older one), else
    the day's first."""
    first = 0
    for k, boundary in enumerate(today):
        if boundary.end > since:
            break
        first = k
    return first


def due(
    screens: Iterable[Screen],
    sessions: Mapping[str, Session],
    now: int,
    recorded: Callable[[str], bool],
    *,
    waits: Callable[[Screen], Waits] = lambda screen: NO_WAITS,
    session_key: int | None = None,
) -> list[Due]:
    """Every run to make now, oldest boundary first: its moment has come, it is not recorded, and
    (for the last cash candle of a screen with auction stocks or an index) its hold has ended: the
    prints are in or their window has ended, and an index's window has ended. A held boundary holds
    the screen's later ones (review N1), and lateness counts from the hold's end (review N2).
    ``recorded(run_key)`` answers from the store and ``waits(screen)`` from the screen's members.
    A version that began during the day starts from the latest boundary closed by then
    (``_first``), not late."""
    out: list[Due] = []
    for screen in screens:
        spans = _spans(screen, sessions)
        if not spans or now < screen.since:  # a version not begun yet (the replay walks the day)
            continue
        session = session_key if session_key is not None else spans[0].key
        today = boundaries(screen, sessions)
        found: Waits | None = None  # the members' word, asked once and only when a hold applies
        held_to = 0  # when the hold that kept this boundary (or an earlier one) waiting ended
        for boundary in today[_first(today, screen.since) :]:
            if now < boundary.at:
                break
            key = RunKey(screen.id, screen.version, session, boundary.tf, boundary.k)
            if recorded(str(key)):
                continue
            prints = _held_until(screen, sessions, boundary)
            index = _index_hold(screen, sessions, boundary)
            if prints is not None or index is not None:
                found = waits(screen) if found is None else found
                ended = _released(prints, index, found, now)
                if ended is None:
                    break  # held: its later boundaries wait for it, in order
                held_to = max(held_to, ended)
            late = now - max(boundary.at, screen.since, held_to) > LATE_MS
            out.append(Due(key, screen, boundary, late, boundary is today[-1]))
    out.sort(key=lambda item: (item.boundary.at, item.screen.id))
    return out


def _hold_end(prints: int | None, index: int | None, waits: Waits, now: int) -> int | None:
    """When the hold on a boundary ends if it is held at ``now`` (the later of the holds that
    apply, as ``_released`` reads them), else None."""
    ends: list[int] = []
    if prints is not None and waits.printed is None and now < prints:
        ends.append(prints)
    if index is not None and waits.index and now < index:
        ends.append(index)
    return max(ends) if ends else None


def next_at(
    screens: Iterable[Screen],
    sessions: Mapping[str, Session],
    now: int,
    waits: Callable[[Screen], Waits] | None = None,
) -> int | None:
    """The next moment any screen runs today, or None when today has no more. With ``waits`` (the
    same members' word ``due`` takes), a boundary whose moment has come but which is held reads as
    the moment its hold ends (review N8): the prints' window end plus the grace, or an index's.
    A held boundary holds the later ones, so none of them is named before it."""
    best: int | None = None
    for screen in screens:
        found: Waits | None = None
        for boundary in boundaries(screen, sessions):
            if boundary.at <= now:
                if waits is None:
                    continue
                prints = _held_until(screen, sessions, boundary)
                index = _index_hold(screen, sessions, boundary)
                if prints is None and index is None:
                    continue
                found = waits(screen) if found is None else found
                until = _hold_end(prints, index, found, now)
                if until is None:
                    continue
                at = until
            else:
                at = boundary.at
            if best is None or at < best:
                best = at
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
    "NO_WAITS",
    "Boundary",
    "Due",
    "Screen",
    "Waits",
    "boundaries",
    "due",
    "next_at",
    "next_trading_boundary",
    "waits_of",
]
