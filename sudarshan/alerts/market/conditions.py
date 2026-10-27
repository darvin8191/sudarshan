"""Conditions that take their level from the feed (M10.2): % move, day high or low, VWAP. Pure.

Each turns one feed frame into what the engine judges: the value in paise and the edges of the
level at that moment (a fixed price level needs nothing from here; spec compiles its edges once).

% move. The base is the previous close (``cp``, as the books use it) or today's open
(``ohlc.open``). The level for "up X%" is base x (1 + X/100) rounded up to the paisa, for "down
X%" base x (1 - X/100) rounded down, worked out in Decimal once a session and again if the base
changes (Upstox can correct ``cp``). It is a level check, so a gap beyond it fires at the first
trade.

The feed keeps a key's last 1d candle (``ohlc``) across days, so the first frames of a session
can carry yesterday's: its open and high are used only when the candle's own date (``ohlc.ts``,
IST) is today, or yesterday's open (98) makes 100.00 look 2% up from today's open, and
yesterday's high (105) hides every new high of today below it.

Day high or low. A value breaks the high when it is above the day's high as the alert last saw
it (equal is not a break), at or after the start time. The value judged is the higher of the trade
and the feed's own day high, so a break between two ticks, or inside a pause, is still seen: the
high the alert remembers is kept in its state (market.db), and the first value above it after a
pause fires, marked. On a special day the start time moves with the session's open: 09:30 is 15
minutes after it.

VWAP. Upstox's average traded price (``atp``) is the session's VWAP. The side is that of the
trade against it, and starts again each session. The VWAP is not on the paisa grid (100.226), so
its edges are taken as a fixed level's are: rounded up for "above" and down for "below". A trade
below the VWAP is never "at" it, and the VWAP drifting while the price stands still moves no
side. An index has no traded volume, so no VWAP: spec refuses one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple

from sudarshan.alerts.market.clock import Market
from sudarshan.alerts.market.engine import State
from sudarshan.alerts.market.grain import edge_down, edge_up, paise, to_decimal
from sudarshan.alerts.market.spec import DOWN, UP, Alert

WAITING_CLOSE = "waiting_close"
WAITING_OPEN = "waiting_open"
WAITING_VWAP = "waiting_vwap"
#: A fall from today's base would go below 0.01 (down 99.9% on a ₹2 stock): it cannot fire.
UNREACHABLE = "unreachable"
#: IST is UTC+5:30 all year: (epoch ms + this) // a day numbers the IST date.
IST_MS = 19_800_000
DAY_MS = 86_400_000
#: An edge no value reaches: the level is not live yet (before the start time, or unseeded).
NEVER = 1 << 62


class Edges(NamedTuple):
    value: int
    up: int
    down: int
    #: The condition's working values for the session, kept in the alert's state.
    derived: tuple | None = None
    #: For the words: a % move's base, or the day's high or low before this value.
    base: int | None = None
    #: The last trade, when ``value`` is not it (a day high taken from the feed's own high).
    price: int | None = None


def edges(alert: Alert, state: State, frame: Mapping, value: int, at: int, market: Market):
    """The Edges for this frame, or a waiting code when the feed has not sent what it needs."""
    kind = alert.kind
    if kind == "move":
        return _move(alert, state, frame, value, market)
    if kind == "day_break":
        return _break(alert, state, frame, value, at, market)
    atp = frame.get("atp")  # vwap
    if paise(atp) is None:  # not sent yet, 0, or not a number
        return WAITING_VWAP
    vwap = to_decimal(atp)
    return Edges(value, edge_up(vwap), edge_down(vwap))


def todays(frame: Mapping, market: Market, segment: str) -> Mapping | None:
    """The frame's 1d candle when it is today's (its ``ts`` on today's IST date), else None."""
    ohlc = frame.get("ohlc")
    session = market.sessions.get(segment)
    if not isinstance(ohlc, Mapping) or session is None:
        return None
    ts = ohlc.get("ts")
    if ts.__class__ is not int and ts.__class__ is not float:
        return None
    return ohlc if (ts + IST_MS) // DAY_MS == (session.start + IST_MS) // DAY_MS else None


def _move(alert: Alert, state: State, frame: Mapping, value: int, market: Market):
    today = market.today
    if alert.params["base"] == "close":
        raw, waiting = frame.get("cp"), WAITING_CLOSE
    else:
        ohlc = todays(frame, market, alert.segment)
        raw, waiting = (ohlc.get("open") if ohlc is not None else None), WAITING_OPEN
    derived = state.derived
    if derived is not None and derived[0] == today and derived[1] == raw:
        return Edges(value, derived[3], derived[4], derived, derived[2])
    base = paise(raw)
    if base is None:
        return waiting
    exact = to_decimal(raw)
    rise, fall = alert.cond.extra
    up, down = edge_up(exact * rise), edge_down(exact * fall)
    if down < 1 and alert.cond.direction == DOWN:
        return UNREACHABLE
    return Edges(value, up, down, (today, raw, base, up, down), base)


def _break(alert: Alert, state: State, frame: Mapping, value: int, at: int, market: Market):
    ohlc = todays(frame, market, alert.segment)
    high = alert.cond.direction == UP
    day = paise(ohlc.get("high" if high else "low")) if ohlc is not None else None
    remembered = state.ref if state.session == market.today else None
    session = market.sessions[alert.segment]
    live = remembered is not None and at >= session.start + alert.cond.extra[0] * 60_000
    seen = [v for v in (value, day, remembered) if v is not None]
    if high:
        return Edges(max(seen), remembered + 1 if live else NEVER, -NEVER, None, remembered, value)
    return Edges(min(seen), NEVER, remembered - 1 if live else -NEVER, None, remembered, value)
