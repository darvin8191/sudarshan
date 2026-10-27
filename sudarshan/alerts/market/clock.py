"""The exchange clock of market alerts (M10.3): candles, the gate and pauses. Pure.

Which clock a value uses: a new trade its own time (ltt), a quote the moment it arrived.

Candles are anchored at the session's start (09:15 on a usual day): candle k of N minutes covers
[start + kN, start + (k+1)N), as the charts bucket them, and the last one ends at the segment's
session end. So a 15-minute F&O candle from 15:30 is a short one that ends at 15:40, and a cash
one from 15:15 ends at 15:30. "Day" is the whole session.

The gate: a value is judged only when every check passes, and the first that fails is the reason
the page shows (words.py says it in the owner's words):

    the segment trades today                                   CLOSED_TODAY
    inside its session: a trade by its own time, arriving no   OUTSIDE_HOURS
      later than 5 s after the end; a quote by its arrival
    the exchange's status for the segment, when known, is      NOT_OPEN_YET, CLOSED_BY_EXCHANGE
      NORMAL_OPEN (unknown: the calendar decides)
    the feed is connected, its subscriptions are live and its  FEED_DOWN
      health is no older than 12 s
    the value arrived within the last 5 s                      STALE (dropped and counted)

A pause is a stretch longer than GAP_S in which market alerts were not checking: the price feed
reconnecting, or the service not running. A crossing first seen after one may have happened
inside it, so its fire says so; a candle that held one is not judged at its close.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import NamedTuple

from sudarshan.alerts.market.sessions import Session

OK = "ok"
CLOSED_TODAY = "closed_today"
OUTSIDE_HOURS = "outside_hours"
NOT_OPEN_YET = "not_open_yet"
CLOSED_BY_EXCHANGE = "closed_by_exchange"
FEED_DOWN = "feed_down"
STALE = "stale"
#: A trade made before the session's end still counts when it arrives this soon after it.
ARRIVAL_GRACE_MS = 5_000
#: A value older than this when it is judged is dropped.
FRESH_MS = 5_000
#: Feed health older than this means the feed is not being heard.
HEALTH_MAX_AGE_MS = 12_000
#: A pause longer than this marks what is seen after it (as order alerts' GAP_S).
GAP_S = 10
GAP_MS = GAP_S * 1000
NO_STATUS: Mapping[str, str] = MappingProxyType({})


class Feed(NamedTuple):
    """What the feed's last health message said, and when it arrived (epoch ms)."""

    state: str = "unknown"
    live: bool = False
    heard_at: int | None = None


class Pause(NamedTuple):
    """A stretch in which market alerts were not checking; ``end`` None while it lasts."""

    start: int
    end: int | None = None
    #: "feed": the price feed was reconnecting; "service": market alerts were not running.
    cause: str = "feed"


class Market(NamedTuple):
    """What the runtime knows about the market at one moment, as plain values."""

    #: Today's IST date as YYYYMMDD.
    today: int
    #: Segment -> today's session; a segment missing here does not trade today.
    sessions: Mapping[str, Session]
    #: The trading day before today (YYYYMMDD), which an opening gap compares with; 0: unknown.
    previous: int = 0
    #: Segment -> the exchange's status from the feed's health, when it has sent one.
    status: Mapping[str, str] = NO_STATUS
    feed: Feed = Feed()
    #: Today's pauses, oldest first; those of GAP_S or less are ignored.
    pauses: tuple[Pause, ...] = ()
    #: Trust a replay's health (state "replay"): the replay check and its tests only.
    replay: bool = False


def gate(market: Market, segment: str, at: int, recv: int, now: int, *, trade: bool) -> str:
    """The first check a value fails, or OK. ``at`` is a trade's time, ``recv`` its arrival."""
    session = market.sessions.get(segment)
    if session is None:
        return CLOSED_TODAY
    if trade:
        inside = session.start <= at < session.end and recv <= session.end + ARRIVAL_GRACE_MS
    else:
        inside = session.start <= recv < session.end
    if not inside:
        return OUTSIDE_HOURS
    status = market.status.get(segment)
    if status is not None and status != "NORMAL_OPEN":
        return NOT_OPEN_YET if status.startswith("PRE_OPEN") else CLOSED_BY_EXCHANGE
    feed = market.feed
    heard = feed.heard_at is not None and now - feed.heard_at <= HEALTH_MAX_AGE_MS
    up = feed.state == "connected" or (market.replay and feed.state == "replay")
    if not (up and feed.live and heard):
        return FEED_DOWN
    if now - recv > FRESH_MS:
        return STALE
    return OK


def candle(session: Session, minutes: int, at: int) -> tuple[int, int, int]:
    """(index, start, end) of the ``minutes`` candle holding ``at``; 0 minutes is the day."""
    if not minutes:
        return 0, session.start, session.end
    size = minutes * 60_000
    k = (at - session.start) // size
    start = session.start + k * size
    return k, start, min(start + size, session.end)


def paused_between(
    pauses: tuple[Pause, ...], after: int | None, until: int
) -> tuple[int, int] | None:
    """The span of the pauses (over GAP_S) between two observations, or None.

    ``after`` is when the alert last saw a value, ``until`` the value now judged: a crossing
    found now may have happened inside that span.
    """
    if after is None:
        return None
    span = None
    for pause in pauses:
        end = until if pause.end is None else pause.end
        if end - pause.start > GAP_MS and pause.start < until and end > after:
            span = (pause.start if span is None else span[0], end)
    return span


def paused_during(pauses: tuple[Pause, ...], start: int, end: int) -> bool:
    """True when a pause over GAP_S touched [start, end): that candle's close is not judged."""
    for pause in pauses:
        stop = end if pause.end is None else pause.end
        if stop - pause.start > GAP_MS and pause.start < end and stop > start:
            return True
    return False
